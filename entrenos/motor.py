"""Motores de análise (DESIGN.md, seções 8 e 17).

O motor transforma o texto numa `Proposta` para o usuário revisar; nada é gravado aqui.

- `MotorRegras` é SEMPRE o motor (`obter_motor`): datas + pessoas + itens + categorias, sem rede.
- A IA só roda quando o usuário aperta um botão ✨ (seção 17). `CadeiaIA` tenta a IA principal,
  depois a de reserva e, se nenhuma responder, fica com as regras e diz por quê.
- `MotorIA` conversa com um serviço de `provedores.py`, no formato do Ollama (/api/chat) ou no
  formato comum (/chat/completions). O formato da resposta desce em cascata até um que o modelo
  aceite (esquema estrito → JSON → texto com JSON) e o que funcionou fica lembrado enquanto o app
  está aberto.

Tudo o que volta da IA é conferido no Python: o trecho tem de estar no texto, as categorias têm
de existir, as datas são recalculadas por `regras/datas.py`, a certeza nunca sobe, as citações
têm de apontar para registros enviados e sugestão com linguagem de controle ou manipulação sai.

Nada daqui escreve em log: textos, nomes, chaves e respostas da IA não saem deste módulo. As
mensagens de erro para a tela não trazem texto do usuário, chave nem resposta do serviço.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable, Protocol, runtime_checkable

try:
    import ssl
except ImportError:          # navegador sem o módulo ssl (seção 19): a IA ali não funciona mesmo
    ssl = None  # type: ignore[assignment]

from . import config, provedores
from .modelos import (CATEGORIAS, Certeza, DataEncontrada, Item, PessoaEncontrada, Precisao,
                      Proposta, Resposta, TipoItem, para_json)
from .regras.categorias import categorias_de, classificar
from .regras.certeza import classificar_certeza
from .regras.datas import extrair_datas
from .regras.itens import extrair_itens
from .regras.pessoas import casar_pessoa, extrair_pessoas, plano
from .texto import frases, normalizar, termos

AVISO_IA = "A IA não respondeu; usei as regras."
NAO_ENCONTREI = "Não encontrei essa informação."
SEM_SUGESTAO = "Não tenho informação suficiente para sugerir algo específico."
OLLAMA_PADRAO = "http://127.0.0.1:11434"
TIPOS_IA = provedores.TIPOS
TEMPO_LIMITE_PADRAO = 120.0

# Frase INVENTADA do "Testar conexão": nada do diário vai junto.
FRASE_TESTE = "Ela me contou que adora girassóis e que detesta filas."
_DATA_TESTE = date(2026, 1, 15)

_MAX_RESPOSTA = 2 * 1024 * 1024     # bytes aceitos da IA
_MAX_ITENS_IA = 60
_MAX_CATEGORIAS_PROPOSTA = 8
_MAX_SUGESTOES = 3
_MAX_CONTEXTO = 40                  # registros mandados para a IA pensar numa sugestão

_DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira",
                "sábado", "domingo"]
_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro"]


# ---------------------------------------------------------------- contrato

@dataclass
class Conhecimento:
    """O que o motor pode consultar do banco (fica no computador: não vai para a IA)."""

    pessoas: list[dict] = field(default_factory=list)   # como em extrair_pessoas
    nome_dela: str | None = None


def conhecimento_do_banco(banco) -> Conhecimento:
    """Monta o Conhecimento a partir do Banco (pessoas cadastradas e nome dela)."""
    pessoas = [{"id": p["id"], "nome": p["nome"], "relacao": p["relacao"],
                "apelidos": list(p.get("apelidos") or [])} for p in banco.pessoas()]
    return Conhecimento(pessoas=pessoas, nome_dela=banco.config().get("nome_dela"))


@runtime_checkable
class Motor(Protocol):
    nome: str

    def disponivel(self) -> bool: ...

    def analisar(self, texto: str, referencia: date, conhecimento: Conhecimento) -> Proposta: ...

    def responder(self, pergunta: str, trechos: list[dict]) -> Resposta: ...


# ---------------------------------------------------------------- utilidades

def data_extenso(d: date, referencia: date | None = None) -> str:
    """date -> 'sexta-feira, 2 de outubro' (com o ano quando não é o da referência)."""
    dia = "1º" if d.day == 1 else str(d.day)
    texto = f"{_DIAS_SEMANA[d.weekday()]}, {dia} de {_MESES[d.month - 1]}"
    if referencia is None or d.year != referencia.year:
        texto += f" de {d.year}"
    return texto


def juntar(partes: list[str], conjuncao: str = "e") -> str:
    """['a', 'b', 'c'] -> 'a, b e c'."""
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + f" {conjuncao} " + partes[-1]


def _categorias_da_proposta(itens: list[Item], texto: str) -> list[str]:
    """Categorias do registro: as dos itens (a principal vale mais) + as do texto inteiro."""
    pontos: dict[str, float] = {}
    for item in itens:
        for i, c in enumerate(item.categorias):
            if c in CATEGORIAS:
                pontos[c] = pontos.get(c, 0.0) + (2.0 if i == 0 else 1.0)
    for c, p in classificar(texto):
        pontos[c] = pontos.get(c, 0.0) + p
    ordem = sorted(pontos, key=lambda c: (-pontos[c], CATEGORIAS.index(c)))
    return ordem[:_MAX_CATEGORIAS_PROPOSTA]


def _rotulo_pessoa(p: PessoaEncontrada) -> str:
    if p.nome and p.relacao:
        return f"{p.nome} ({p.relacao})"
    return p.nome or p.relacao or p.trecho


def _avisos(datas: list[DataEncontrada], pessoas: list[PessoaEncontrada], referencia: date) -> list[str]:
    avisos: list[str] = []
    for d in datas:
        if d.ambigua and len(d.alternativas) > 1:
            opcoes = juntar([data_extenso(a, referencia) for a in d.alternativas], "ou")
            avisos.append(f"“{d.expressao}” pode ser {opcoes}. Confira a data antes de salvar.")
        elif d.data is None:
            avisos.append(f"Não consegui calcular a data de “{d.expressao}”. Confira antes de salvar.")
    vistos: set[str] = set()
    for p in pessoas:
        if not p.nova or not (p.nome or p.relacao):
            continue
        rotulo = _rotulo_pessoa(p)
        if normalizar(rotulo) in vistos:
            continue
        vistos.add(normalizar(rotulo))
        avisos.append(f"Pessoa nova: {rotulo}. Confira o nome e a relação antes de salvar.")
    return avisos


def _registros(n: int) -> str:
    return "1 registro" if n == 1 else f"{n} registros"


# ---------------------------------------------------------------- regras

class MotorRegras:
    """O motor do app: só regras locais, sem rede."""

    nome = "regras"

    def disponivel(self) -> bool:
        return True

    def analisar(self, texto: str, referencia: date, conhecimento: Conhecimento | None = None) -> Proposta:
        texto = texto if isinstance(texto, str) else ""
        conhecimento = conhecimento or Conhecimento()
        if not texto.strip():
            return Proposta(texto=texto, referencia=referencia, motor=self.nome)
        datas = extrair_datas(texto, referencia)
        pessoas = extrair_pessoas(texto, list(conhecimento.pessoas or []), nome_dela=conhecimento.nome_dela)
        itens = extrair_itens(texto, referencia, pessoas, datas)
        return Proposta(
            texto=texto, referencia=referencia, itens=itens, pessoas=pessoas, datas=datas,
            categorias=_categorias_da_proposta(itens, texto),
            avisos=_avisos(datas, pessoas, referencia), motor=self.nome,
        )

    def responder(self, pergunta: str, trechos: list[dict]) -> Resposta:
        """Resposta por modelo de frase: só conta o que foi encontrado."""
        trechos = [t for t in (trechos or []) if isinstance(t, dict)]
        if not trechos:
            return Resposta(pergunta=pergunta, texto=NAO_ENCONTREI, motor=self.nome)
        qtd = len({t.get("registro_id") for t in trechos})
        return Resposta(pergunta=pergunta, texto=f"Encontrei {_registros(qtd)} sobre isso:",
                        encontrados=list(trechos), motor=self.nome)


def _base_das_regras(texto: Any, referencia: date, conhecimento: Conhecimento,
                     base: Proposta | None) -> Proposta:
    """A proposta das regras para este texto: a recebida, se for dele, ou uma nova."""
    texto = texto if isinstance(texto, str) else ""
    if isinstance(base, Proposta) and base.texto == texto and base.referencia == referencia:
        return base
    return MotorRegras().analisar(texto, referencia, conhecimento)


# ---------------------------------------------------------------- IA: esquemas e instruções

def _nulo(tipo: str) -> dict:
    return {"type": [tipo, "null"]}


_ESQUEMA_ANALISE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": [t.value for t in TipoItem]},
                    "resumo": {"type": "string"},
                    "trecho": {"type": "string"},
                    "certeza": {"type": "string", "enum": [Certeza.CONFIRMADO.value, Certeza.OBSERVADO.value,
                                                           Certeza.INFERENCIA.value]},
                    "categorias": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIAS)}},
                    "alvo": _nulo("string"),
                    "polaridade": _nulo("integer"),
                    "pessoa": _nulo("string"),
                    "data_expressao": _nulo("string"),
                },
                "required": ["tipo", "resumo", "trecho", "certeza", "categorias", "alvo", "polaridade",
                             "pessoa", "data_expressao"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["itens"],
    "additionalProperties": False,
}

_ESQUEMA_RESPOSTA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "texto": {"type": "string"},
        "citacoes": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["texto", "citacoes"],
    "additionalProperties": False,
}

_ESQUEMA_SUGESTAO: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sugestoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string"},
                    "porque": {"type": "string"},
                    "registro_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["texto", "porque", "registro_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sugestoes"],
    "additionalProperties": False,
}

_SISTEMA_ANALISE = """Você organiza anotações de um diário sobre a namorada do usuário ("ela").
Receberá o texto, a data do registro e uma proposta feita por regras. Devolva a lista
completa de itens, melhorando a proposta. Regras obrigatórias:
- "trecho" é uma cópia LITERAL de uma frase (ou parte) do texto, sem mudar nenhuma letra.
- Não invente nada que não esteja no texto. "resumo" é curto, em terceira pessoa, com as
  palavras do texto ("Gosta de tulipas", "Mãe: cirurgia em novembro").
- Não calcule datas. Se houver data, copie em "data_expressao" a expressão exata do texto
  ("dia 20", "sexta que vem"); senão use null.
- "certeza": "confirmado" só quando o texto diz que ela falou ("ela disse", "me contou");
  "inferencia" para hipóteses ("acho que", "talvez"); "observado" para o resto.
- "pessoa": null quando o item é sobre ela; senão o nome ou a relação escritos no texto.
- "polaridade": 1 (gosta/ajuda), -1 (não gosta/não ajuda) ou null.
- "categorias": só da lista permitida. Tipos: fato, preferencia, restricao, evento,
  data_importante, estado, padrao, apoio, pendencia, desejo, pessoa_info, nota."""

_SISTEMA_RESPOSTA = """Você responde perguntas sobre o que o usuário registrou a respeito da
namorada. Use SOMENTE os trechos fornecidos; nunca invente nem complete com suposições.
Responda em português, em poucas frases, e cite em "citacoes" o registro_id de cada trecho
usado. Se os trechos não respondem à pergunta, responda exatamente "Não encontrei essa
informação." e deixe "citacoes" vazia. Sugestões só como possibilidade ("Talvez...")."""

_SISTEMA_SUGESTAO = """Você ajuda o usuário a cuidar do namoro usando SOMENTE os registros
fornecidos sobre a namorada ("ela"). Sugira no máximo 3 gestos pequenos, gentis e genuínos
(perguntar como ela está, lembrar de algo que ela contou, um cuidado prático). Regras obrigatórias:
- Cada sugestão é só uma possibilidade, nunca uma ordem: comece com "Talvez" ou "Pode ser uma boa".
- Nada de manipular, testar, provocar ciúme, convencer, controlar ou influenciar decisões dela,
  nem explorar inseguranças ou medos dela.
- Não invente nada que não esteja nos registros. "porque" diz, em uma frase, de onde veio a ideia.
- "registro_ids" traz o registro_id de cada registro usado (só os fornecidos).
- Se os registros não permitem sugerir algo específico, devolva "sugestoes": []."""

_PEDIR_JSON = "\n\nResponda apenas com um objeto JSON, sem nenhum texto antes ou depois, neste formato:\n"
_EXEMPLO_ANALISE = ('{"itens": [{"tipo": "preferencia", "resumo": "Gosta de ...", '
                    '"trecho": "<cópia literal de uma frase do texto>", "certeza": "observado", '
                    '"categorias": ["Presentes"], "alvo": "...", "polaridade": 1, "pessoa": null, '
                    '"data_expressao": null}]}')
_EXEMPLO_RESPOSTA = '{"texto": "...", "citacoes": [12]}'
_EXEMPLO_SUGESTAO = '{"sugestoes": [{"texto": "Talvez ...", "porque": "Você registrou que ...", "registro_ids": [12]}]}'


# ---------------------------------------------------------------- IA: validação da análise

_NIVEL = {Certeza.INFERENCIA: 0, Certeza.OBSERVADO: 1, Certeza.CONFIRMADO: 2}
_ELA = frozenset({"ela", "dela", "namorada", "a namorada", "minha namorada", "ela mesma"})
# Palavras de modelo de frase que o resumo pode usar mesmo sem estarem no texto.
_PALAVRAS_MODELO = frozenset(normalizar(p) for p in """
gosta gostar nao prefere fica ficou esta estava tem quer queria gostaria sonha pode come bebe
alergica alergico alergia intolerante aniversario evento data importante padrao apoio pendencia
desejo nota fato restricao preferencia costuma sempre ajuda precisa vai sera foi antes depois
quando dia mes semana ano proxima proximo mae pai irma irmao amiga amigo pessoa ela sobre
""".split())


def _texto_curto(v: Any, maximo: int) -> str | None:
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v).strip()
    if not v:
        return None
    return v if len(v) <= maximo else v[:maximo - 1].rstrip() + "…"


def _raiz(p: str) -> str:
    return p[:5] if len(p) > 5 else p


def _cobertura(frase: str, texto: str) -> float:
    """Quanto das palavras de `frase` (fora as de modelo) aparece em `texto` (0-1)."""
    palavras = [t for t in termos(frase) if t not in _PALAVRAS_MODELO and not t.isdigit()]
    if not palavras:
        return 1.0
    raizes = {_raiz(t) for t in termos(texto)}
    return sum(1 for t in palavras if _raiz(t) in raizes) / len(palavras)


def _limitar_certeza(valor: Any, *tetos: Certeza) -> Certeza:
    teto = min(tetos, key=lambda c: _NIVEL[c])
    try:
        pedida = Certeza(valor)
    except ValueError:
        return teto
    if pedida == Certeza.INCERTO:
        pedida = Certeza.INFERENCIA   # incerto nunca vem do texto
    return pedida if _NIVEL[pedida] <= _NIVEL[teto] else teto


def _categorias_validas(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    por_nome = {normalizar(c): c for c in CATEGORIAS}
    saida: list[str] = []
    for c in v:
        certa = por_nome.get(normalizar(c)) if isinstance(c, str) else None
        if certa and certa not in saida:
            saida.append(certa)
    return saida[:5]


def _polaridade(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return 1 if v > 0 else -1 if v < 0 else None


def _par_das_regras(do_tipo: list[Item], resumo: str | None) -> Item | None:
    """O item das regras que corresponde ao da IA: o único da frase, ou o que tem o mesmo alvo."""
    if len(do_tipo) == 1:
        return do_tipo[0]
    r = normalizar(resumo or "")
    return next((i for i in do_tipo if i.alvo and normalizar(i.alvo) in r), None)


def _posicoes(expressao: str, texto: str) -> list[int]:
    alvo, base = plano(expressao), plano(texto)
    saida, pos = [], base.find(alvo)
    while pos >= 0 and alvo:
        saida.append(pos)
        pos = base.find(alvo, pos + 1)
    return saida


def _data_da_expressao(expressao: Any, trecho: str, texto: str, datas: list[DataEncontrada],
                       referencia: date) -> DataEncontrada | None:
    """A data que o PYTHON calcula para a expressão indicada pela IA (ou None)."""
    expressao = _texto_curto(expressao, 80)
    if not expressao:
        return None
    ini_trecho = texto.find(trecho)
    fim_trecho = ini_trecho + len(trecho)
    ocorrencias = _posicoes(expressao, texto)
    if not ocorrencias:
        return None
    dentro = [p for p in ocorrencias if ini_trecho <= p < fim_trecho] or ocorrencias
    for p in dentro:
        for d in datas:
            if d.data is not None and d.inicio < p + len(expressao) and p < d.fim:
                return d
    # Sem data do texto inteiro ali: tenta a frase sozinha (mesmo contexto verbal).
    if ini_trecho >= 0:
        for d in extrair_datas(trecho, referencia):
            if d.data is not None and any(d.inicio < p - ini_trecho + len(expressao) and p - ini_trecho < d.fim
                                          for p in dentro):
                return d
    return None


def _no_texto(valor: Any, texto_normalizado: str) -> str | None:
    """`valor` se ele aparece escrito no texto; senão None (o cadastro não vai para a IA)."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    procurado = normalizar(re.sub(r"\s*\(sua\)$", "", valor))
    if procurado and re.search(r"(?<!\w)" + re.escape(procurado) + r"(?!\w)", texto_normalizado):
        return valor
    return None


def _pedido_analise(base: Proposta) -> str:
    """O que vai para a IA: o texto e o que as regras tiraram DELE. Nome que veio do cadastro
    ("a mãe dela" casou com "Sandra") vira a relação ou o trecho escrito no texto."""
    n = normalizar(base.texto)
    trocas: dict[str, str] = {}
    for p in base.pessoas:
        if p.nome and not _no_texto(p.nome, n):
            trocas[p.nome] = _no_texto(p.relacao, n) or p.trecho

    def sem_cadastro(valor: Any) -> Any:
        if not isinstance(valor, str):
            return valor
        for nome, por in trocas.items():
            comeca = valor.startswith(nome)
            valor = re.sub(r"(?<!\w)" + re.escape(nome) + r"(?!\w)", por, valor)
            if comeca:
                valor = valor[:1].upper() + valor[1:]
        return valor

    def pessoa(valor: Any) -> str | None:
        return trocas.get(valor) or _no_texto(valor, n)

    proposta = [{"tipo": i.tipo.value, "resumo": sem_cadastro(i.resumo), "trecho": i.trecho,
                 "certeza": i.certeza.value, "categorias": i.categorias, "alvo": sem_cadastro(i.alvo),
                 "polaridade": i.polaridade, "pessoa": pessoa(i.pessoa), "data_expressao": i.data_expressao}
                for i in base.itens]
    pedido = {
        "texto": base.texto,
        "data_do_registro": base.referencia.isoformat(),
        "pessoas_citadas": [{"trecho": p.trecho, "nome": _no_texto(p.nome, n), "relacao": _no_texto(p.relacao, n)}
                            for p in base.pessoas],
        "proposta_das_regras": proposta,
    }
    return json.dumps(pedido, ensure_ascii=False)


# ---------------------------------------------------------------- IA: só o que está escrito no registro

_RE_PARENTESE_FINAL = re.compile(r"\s*\([^)]*\)\s*$")
_STOP_NOMES = frozenset(normalizar(p) for p in ("da", "de", "do", "das", "dos", "e", "del", "van", "von"))


def _limite(termo: str) -> str:
    return r"(?<!\w)" + re.escape(termo) + r"(?!\w)"


def _escrito(termo: Any, texto_normalizado: str) -> bool:
    """O termo aparece (como palavra) no texto já normalizado?"""
    procurado = normalizar(termo) if isinstance(termo, str) else ""
    return bool(procurado) and re.search(_limite(procurado), texto_normalizado) is not None


def _cadastro_para_limpar(pessoas: Any) -> list[tuple[list[str], str | None]]:
    """(nomes, relação) de cada pessoa do cadastro. `nomes` traz nome, apelidos e as partes de
    nome composto ("Sandra Regina" → "Sandra", "Regina"), do mais longo para o mais curto."""
    saida: list[tuple[list[str], str | None]] = []
    for p in pessoas if isinstance(pessoas, (list, tuple)) else []:
        if not isinstance(p, dict):
            continue
        apelidos = p.get("apelidos") if isinstance(p.get("apelidos"), (list, tuple)) else []
        nomes: list[str] = []
        for x in (p.get("nome"), *apelidos):
            if not isinstance(x, str) or not x.strip():
                continue
            nomes.append(x.strip())
            partes = [w for w in x.split() if len(normalizar(w).strip(".")) >= 3 and normalizar(w) not in _STOP_NOMES]
            if len(partes) > 1:
                nomes.extend(partes)
        relacao = p.get("relacao")
        relacao = _RE_PARENTESE_FINAL.sub("", relacao).strip() if isinstance(relacao, str) else ""
        nomes = sorted(dict.fromkeys(nomes), key=len, reverse=True)
        if nomes or relacao:
            saida.append((nomes, relacao or None))
    return saida


def _sem_o_cadastro(valor: Any, n: str, cadastro: list[tuple[list[str], str | None]],
                    capitalizar: bool = True) -> str | None:
    """`valor` sem nome, apelido ou relação do cadastro que não esteja escrito no texto do
    registro (`n`, normalizado). Nome que falta vira a relação escrita (ou outro nome escrito);
    relação que falta sai do rótulo "Nome (relação)" ou vira o nome escrito. Sem troca: None."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    maiuscula = capitalizar and valor[:1].isupper()
    for nomes, relacao in cadastro:
        rel_ok = relacao if relacao and _escrito(relacao, n) else None
        nome_ok = next((x for x in nomes if _escrito(x, n)), None)
        if relacao:          # rótulo "Nome (relação)", como a busca mostra uma pessoa
            for nome in nomes:
                rotulo = re.compile(_limite(nome) + r"\s*\(\s*" + re.escape(relacao) + r"(?:\s*\([^)]*\))?\s*\)",
                                    re.IGNORECASE)
                if not rotulo.search(valor):
                    continue
                este = nome if _escrito(nome, n) else nome_ok
                troca = f"{este} ({rel_ok})" if este and rel_ok else (este or rel_ok)
                if not troca:
                    return None
                valor = rotulo.sub(lambda _m, t=troca: t, valor)
        for nome in nomes:
            padrao = re.compile(_limite(nome), re.IGNORECASE)
            if _escrito(nome, n) or not padrao.search(valor):
                continue
            troca = rel_ok or nome_ok
            if not troca:
                return None
            valor = padrao.sub(lambda _m, t=troca: t, valor)
        if relacao and not rel_ok:
            padrao = re.compile(_limite(relacao), re.IGNORECASE)
            if padrao.search(valor):
                if not nome_ok:
                    return None
                valor = padrao.sub(lambda _m, t=nome_ok: t, valor)
    # Conferência final (acento escrito de outro jeito, por exemplo): nada do cadastro fora do texto.
    nv = normalizar(valor)
    for nomes, relacao in cadastro:
        for termo in (*nomes, *((relacao,) if relacao else ())):
            if not _escrito(termo, n) and _escrito(termo, nv):
                return None
    valor = re.sub(r"\s{2,}", " ", valor).strip()
    if maiuscula:
        valor = valor[:1].upper() + valor[1:]
    return valor or None


def so_o_escrito(entrada: dict, texto: str, pessoas: list[dict]) -> dict:
    """Cópia de um achado da busca (ou de um item do contexto da sugestão) só com o que está
    escrito no texto do PRÓPRIO registro, como `_pedido_analise` faz na análise (seções 17.1.5
    e 17.8). Nome, apelido ou relação que vieram do cadastro, às vezes aprendidos num registro
    🔒, saem de `resumo`, `pessoa` e `alvo` (viram o que está escrito); o resumo que não dá
    para limpar vira o próprio trecho. Os outros campos ficam como estão."""
    copia = dict(entrada) if isinstance(entrada, dict) else {}
    texto = texto if isinstance(texto, str) else ""
    n = normalizar(texto)
    cadastro = _cadastro_para_limpar(pessoas)
    trecho = copia.get("trecho") if isinstance(copia.get("trecho"), str) else None
    if trecho is not None and trecho.strip() and trecho.rstrip("…").strip() not in texto:
        trecho = copia["trecho"] = None          # só vai o que é literal do registro
    resumo = _sem_o_cadastro(copia.get("resumo"), n, cadastro)
    copia["resumo"] = resumo if resumo is not None else (_texto_curto(trecho, 200) if trecho else None)
    if "pessoa" in copia:
        copia["pessoa"] = _sem_o_cadastro(copia.get("pessoa"), n, cadastro, capitalizar=False)
    if "alvo" in copia:
        copia["alvo"] = _sem_o_cadastro(copia.get("alvo"), n, cadastro)
    return copia


# ---------------------------------------------------------------- IA: sugestões

# Linguagem de controle ou manipulação (texto normalizado: minúsculo e sem acento).
_MANIPULACAO = re.compile(r"(?<![a-z0-9])(?:" + "|".join([
    r"fa(?:zer|ca) (?:com que )?ela", r"faze-la",
    r"convenc(?:er|e-la|endo|a)", r"persuadi\w*", r"manipul\w*", r"chantag\w*",
    r"para que ela", r"pra que ela", r"(?:para|pra) ela (?:ceder|aceitar|mudar de ideia|desistir)",
    r"ciume\w*", r"enciumad\w*",
    r"test(?:ar|e|a) (?:ela|se ela|a fidelidade|o amor)", r"testa-la", r"teste de fidelidade",
    r"controlar (?:ela|o que ela|com quem|onde ela)", r"controla-la",
    r"vigi(?:ar|a-la)", r"espi(?:ar|onar)", r"monitor(?:ar|e) (?:ela|o que)", r"rastre(?:ar|e)",
    r"(?:olhar|mexer|fucar|checar|ver|ler) (?:no |o |as |os |nas |nos )?(?:celular|mensagens|conversas|redes) dela",
    r"senha dela",
    r"obrig(?:ar|ue) ela", r"obriga-la", r"forc(?:ar|e) ela", r"forca-la", r"pression(?:ar|e)\w*",
    r"proib(?:ir|a)\w*", r"impedi(?:r ela|-la)",
    r"nao deix(?:ar|e) ela (?:sair|ir|ver|falar|usar|trabalhar|estudar|viajar)",
    r"isol(?:ar|e) ela", r"isola-la", r"afast(?:ar|e) ela", r"afasta-la", r"longe d[ao]s? amig\w*",
    r"puni(?:r|-la)", r"castig\w*", r"vinga\w*",
    r"dar (?:um )?gelo", r"gelo nela", r"tratamento de silencio",
    r"sentir culpad\w*", r"culpa-la", r"jogar na cara",
    r"explorar (?:a |as |o |os )?(?:inseguranc|medo|fraquez|carenc)\w*",
    r"usar (?:isso|o medo|a inseguranca)\w* contra", r"contra ela", r"tirar vantagem",
    r"aproveitar que ela (?:esta|ta|anda) (?:fragil|insegura|carente|triste|vulneravel)",
    r"ment(?:ir|ira)\w*", r"joguinho\w*", r"jogo psicologico",
    r"depend(?:er|ente) de voce",
    # Afastamento e silêncio de propósito ("sumir uns dias", "ignorar as mensagens dela").
    r"ignor\w* (?:a )?ela", r"ignora-la", r"ignor\w* o que ela",
    r"ignor\w* (?:as |os |a |o )?(?:mensage\w*|ligac\w*|chamad\w*|audios?|recados?) dela",
    r"no vacuo", r"visualiz\w* e nao respond\w*",
    r"nao respond\w* (?:(?:a|as) )?(?:ela|mensage\w* dela)",
    r"nao respond\w* (?:logo|na hora|rapido|de proposito|por (?:um|uns|alguns|uma))",
    r"demor\w* (?:pra|para|a) respond\w*",
    r"sum(?:ir|a|irem|iu) (?:por (?:uns|umas|alguns|algumas|um|uma|dois|duas|tres|\d+)|um tempo|uns dias"
    r"|do mapa|de proposito)", r"sumic\w*",
    # Fazer falta, correr atrás, "ver o quanto precisa de você".
    r"sent(?:ir|e|a) (?:a )?sua falta", r"sint(?:a|am) (?:a )?sua falta", r"sent(?:ir|e|a) falta de voce",
    r"sint(?:a|am) falta de voce", r"corr(?:er|a|e) atras de voce", r"(?:para|pra|ate) ela corr(?:er|a|e) atras",
    r"o quanto (?:ela )?(?:precisa|depende|sente falta) de voce",
    r"ate ela (?:implorar|ceder|vir atras|pedir desculpas?)",
    # Fingir, deixar insegura, provocar com a ex, influenciar.
    r"fing\w*", r"fazer de conta",
    r"deix(?:ar|e|a)(?:-la| ela) (?:mais |bem |um pouco )?(?:insegur\w*|culpad\w*|com ciume\w*|na duvida|com medo"
    r"|carente|mal)",
    r"(?:sua|tua) ex", r"ex-?namorad\w*",
    r"influenci\w*", r"induz\w*",
    r"provoc\w* (?:ela|nela|uma reacao|reacao)", r"provoca-la",
]) + r")(?![a-z0-9])")

# Sugestão é possibilidade, nunca ordem (princípio 5): começa como possibilidade.
_POSSIBILIDADE = re.compile(
    r"^(?:talvez|pode ser|quem sabe|que tal|e se|vale (?:a pena|tentar)|uma (?:ideia|possibilidade|opcao|sugestao)"
    r"|seria (?:legal|bom|bonito|gentil|uma boa)|se (?:fizer sentido|ela quiser|voce quiser|quiser|der|for possivel"
    r"|ela topar|rolar))(?![a-z0-9])")


def _manipuladora(texto: str) -> bool:
    return bool(_MANIPULACAO.search(normalizar(texto or "")))


def _e_possibilidade(texto: str) -> bool:
    """A sugestão começa como possibilidade ("Talvez...", "Pode ser uma boa...")?"""
    return bool(_POSSIBILIDADE.match(re.sub(r"^[^a-z0-9]+", "", normalizar(texto or ""))))


def _id(valor: Any) -> int | None:
    """registro_id que veio da IA: inteiro (ou texto só com dígitos)."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def _contexto_para_ia(contexto: Any) -> list[dict]:
    """Só os campos que a IA precisa para sugerir; nada de ids de item, config ou cadastro."""
    saida: list[dict] = []
    for bruto in contexto if isinstance(contexto, (list, tuple)) else []:
        if isinstance(bruto, Item):
            bruto = para_json(bruto)
        if not isinstance(bruto, dict):
            continue
        bruto = para_json(bruto)
        rid = _id(bruto.get("registro_id"))
        if rid is None:
            continue
        registro: dict[str, Any] = {"registro_id": rid}
        for campo in ("data_registro", "tipo", "resumo", "trecho", "certeza", "pessoa", "data_evento", "quando"):
            valor = _texto_curto(bruto.get(campo), 300)
            if valor:
                registro[campo] = valor
        categorias = bruto.get("categorias") or ([bruto["categoria"]] if bruto.get("categoria") else [])
        if isinstance(categorias, list):
            validas = [c for c in categorias if isinstance(c, str) and c in CATEGORIAS][:3]
            if validas:
                registro["categorias"] = validas
        if "resumo" in registro or "trecho" in registro:
            saida.append(registro)
        if len(saida) >= _MAX_CONTEXTO:
            break
    return saida


def _validar_sugestoes(brutas: list, registros: list[dict]) -> tuple[list[dict], int]:
    """Fica só o que cita registros enviados e não tem linguagem de controle. (sugestões, descartadas)"""
    por_id = {r["registro_id"]: r for r in registros}
    saida: list[dict] = []
    vistos: set[str] = set()
    descartadas = 0
    for bruta in brutas[:10]:
        if not isinstance(bruta, dict):
            descartadas += 1
            continue
        texto = _texto_curto(bruta.get("texto"), 300)
        porque = _texto_curto(bruta.get("porque"), 400)
        ids = bruta.get("registro_ids")
        convertidos = [_id(x) for x in ids] if isinstance(ids, list) else []
        if (not texto or not convertidos or any(x not in por_id for x in convertidos)
                or not _e_possibilidade(texto) or _manipuladora(texto) or _manipuladora(porque or "")):
            descartadas += 1
            continue
        if normalizar(texto) in vistos:
            continue
        vistos.add(normalizar(texto))
        if not porque:
            origem = por_id[convertidos[0]]
            porque = f"Você registrou: “{origem.get('trecho') or origem.get('resumo')}”"
        if len(saida) < _MAX_SUGESTOES:
            saida.append({"texto": texto, "porque": porque, "registro_ids": list(dict.fromkeys(convertidos))})
    return saida, descartadas


# ---------------------------------------------------------------- IA: rede e erros

class ErroIA(ValueError):
    """A IA não respondeu (ou respondeu algo que não se aproveita).

    `mensagem` vai para a tela: em português, sem texto do usuário, chave, URL nem o que o
    serviço respondeu. `causa` é só o nome do tipo de erro (para teste e diagnóstico).
    """

    def __init__(self, mensagem: str, tipo: str = "falha", causa: str | None = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.tipo = tipo
        self.causa = causa or "ErroIA"


class _FalhaHttp(Exception):
    """Resposta HTTP de erro, com um pedaço do corpo em minúsculas (só para decidir; nunca vai à tela)."""

    def __init__(self, status: int, detalhe: str):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.detalhe = detalhe


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    """Redirecionamento poderia mandar o texto (e a chave) para outro endereço."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


# Sem proxy do sistema e sem seguir redirecionamento.
_ABRIDOR = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SemRedirecionar())
_AGENTE = f"EntreNos/{config.VERSAO}"

_MSG = {
    "chave": "A chave foi recusada pelo serviço. Confira a chave em Ajustes → IA.",
    "limite": "O serviço chegou ao limite de pedidos ou da cota. Espere um pouco e tente de novo.",
    "nao_encontrado": "O serviço não achou o modelo ou o endereço. Confira a URL e o nome do modelo.",
    "recusado": "O serviço recusou o pedido. Talvez este modelo não saiba responder em JSON; "
                "experimente outro modelo.",
    "grande": "O texto ficou grande demais para este serviço.",
    "redirecionamento": "O serviço tentou mandar o pedido para outro endereço; por segurança, não segui.",
    "servico": "O serviço da IA está com problema agora (erro {status}). Tente de novo mais tarde.",
    "http": "O serviço da IA respondeu com erro {status}.",
    "tempo": "A IA demorou mais que o tempo limite ({segundos} s).",
    "conexao": "Não consegui falar com a IA. Confira se ela está ligada, a internet e o endereço.",
    "dns": "Não encontrei o endereço da IA. Confira a internet e a URL.",
    "seguranca": "Não consegui uma conexão segura com o serviço da IA.",
    "incompativel": "O endereço respondeu, mas não parece ser uma IA compatível. "
                    "Confira a URL e o serviço escolhido.",
    "formato": "A resposta da IA veio fora do formato esperado.",
    "grande_resposta": "A resposta da IA veio grande demais.",
    "recusa": "A IA se recusou a responder.",
    "vazio": "Nada do que a IA devolveu conferiu com o texto.",
    "sem_citacao": "A resposta da IA não citou nenhum dos registros encontrados.",
    "sugestoes": "Nenhuma sugestão da IA passou na conferência (citar um registro e respeitar as regras).",
    "falha": "A IA não respondeu direito.",
}
_STATUS_FORMATO = (400, 404, 422)     # "não aceito este formato": tenta o próximo da cascata
# Pistas no corpo de um 400/422 (em minúsculas). Erro que fala do formato desce a cascata; texto
# grande demais e modelo que não existe não mudam com outro formato: vão direto para a tela.
_RE_ERRO_FORMATO = re.compile(r"response_format|json_schema|json_object|schema|(?<![a-z])format")
_RE_ERRO_GRANDE = re.compile(r"context.{0,20}(?:length|window|limit|size)|maximum context|too many tokens"
                             r"|tokens? limit|too long|reduce the length|prompt is too large|request too large")
_RE_ERRO_MODELO = re.compile(r"(?:invalid|unknown|no such|unsupported) model|model[_ ]not[_ ]found"
                             r"|model.{0,80}(?:not found|does not exist|not exist|is not available|decommissioned)")


def _segundos(t: float) -> str:
    return str(int(t)) if t >= 1 and float(t).is_integer() else f"{t:.1f}".replace(".", ",")


def _erro_http(status: int) -> ErroIA:
    if status in (401, 403):
        return ErroIA(_MSG["chave"], "chave", "HTTPError")
    if status == 429:
        return ErroIA(_MSG["limite"], "limite", "HTTPError")
    if status == 404:
        return ErroIA(_MSG["nao_encontrado"], "nao_encontrado", "HTTPError")
    if status in (400, 422):
        return ErroIA(_MSG["recusado"], "recusado", "HTTPError")
    if status == 413:
        return ErroIA(_MSG["grande"], "grande", "HTTPError")
    if 300 <= status < 400:
        return ErroIA(_MSG["redirecionamento"], "redirecionamento", "HTTPError")
    if status >= 500:
        return ErroIA(_MSG["servico"].format(status=status), "servico", "HTTPError")
    return ErroIA(_MSG["http"].format(status=status), "http", "HTTPError")


def _falha_de_nome(motivo: Any) -> bool:
    """O endereço não existe (socket.gaierror, falha de DNS)? Sem importar `socket`: o motor
    também roda no navegador (seção 19). Se um gaierror aconteceu, quem o levantou (o urllib)
    já carregou o módulo `socket`."""
    gaierror = getattr(sys.modules.get("socket"), "gaierror", None)
    return isinstance(gaierror, type) and isinstance(motivo, gaierror)


def _erro_ia(erro: BaseException, tempo: float) -> ErroIA:
    """Qualquer falha de rede/HTTP/formato -> ErroIA com mensagem para a tela."""
    if isinstance(erro, ErroIA):
        return erro
    causa = type(erro).__name__
    if isinstance(erro, _FalhaHttp):
        return _erro_http(erro.status)
    motivo = erro.reason if isinstance(erro, urllib.error.URLError) else erro
    if isinstance(motivo, TimeoutError):
        return ErroIA(_MSG["tempo"].format(segundos=_segundos(tempo)), "tempo", "TimeoutError")
    if ssl is not None and isinstance(motivo, ssl.SSLError):
        return ErroIA(_MSG["seguranca"], "seguranca", causa)
    if _falha_de_nome(motivo):
        return ErroIA(_MSG["dns"], "conexao", causa)
    if isinstance(erro, (urllib.error.URLError, OSError, http.client.HTTPException)):
        return ErroIA(_MSG["conexao"], "conexao", causa)
    return ErroIA(_MSG["falha"], "falha", causa)


def _limitar_leitura(resposta: Any, segundos: float) -> None:
    """Cada leitura do corpo espera no máximo até o prazo. O `timeout` do urllib vale por
    leitura, contado de quando o pedido abriu; aqui ele encolhe junto com o prazo."""
    try:
        resposta.fp.raw._sock.settimeout(max(0.05, segundos))   # http.client: BufferedReader → SocketIO → socket
    except Exception:  # noqa: BLE001 - sem acesso ao socket, fica o timeout da abertura
        pass


def _requisitar(endereco: str, corpo: dict | None, prazo: float, chave: str | None) -> dict:
    """GET (corpo None) ou POST JSON até o `prazo` (time.monotonic). Devolve o JSON da resposta."""
    cabecalhos = {"Accept": "application/json", "User-Agent": _AGENTE}
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"
    if chave:
        cabecalhos["Authorization"] = f"Bearer {chave}"
    pedido = urllib.request.Request(endereco, data=dados, headers=cabecalhos,
                                    method="POST" if corpo is not None else "GET")
    restante = prazo - time.monotonic()
    if restante <= 0:
        raise TimeoutError("tempo esgotado")
    try:
        aberta = _ABRIDOR.open(pedido, timeout=restante)
    except urllib.error.HTTPError as erro:
        detalhe = ""
        try:
            detalhe = (erro.read(16384) or b"").decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001 - o corpo do erro é só uma pista
            pass
        finally:
            erro.close()     # erro HTTP também traz uma conexão aberta
        raise _FalhaHttp(erro.code, detalhe) from None
    partes: list[bytes] = []
    tamanho = 0
    with aberta as resposta:
        while True:
            restante = prazo - time.monotonic()
            if restante <= 0:
                raise TimeoutError("tempo esgotado")
            _limitar_leitura(resposta, restante)
            # read1: devolve o que já chegou (uma leitura só). read(n) ficaria esperando os n
            # bytes enquanto o corpo pinga devagar, e o prazo só seria conferido no fim.
            bloco = resposta.read1(65536)
            if not bloco:
                break
            tamanho += len(bloco)
            if tamanho > _MAX_RESPOSTA:
                raise ErroIA(_MSG["grande_resposta"], "grande_resposta", "RespostaGrande")
            partes.append(bloco)
            if getattr(resposta, "length", None) == 0:
                break            # o corpo chegou inteiro: vale, mesmo que em cima do prazo
    try:
        resultado = json.loads(b"".join(partes).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ErroIA(_MSG["incompativel"], "incompativel", type(erro).__name__) from None
    if not isinstance(resultado, dict):
        raise ErroIA(_MSG["incompativel"], "incompativel", "ForaDoFormato")
    return resultado


def _rota(url: str, tipo: str, qual: str) -> str:
    if tipo == "ollama":
        return url + ("/api/chat" if qual == "chat" else "/api/tags")
    return url + ("/chat/completions" if qual == "chat" else "/models")


def _rede_protegida(host: str) -> bool:
    """Loopback ou rede de casa (IP privado): a chave pode ir sem https."""
    if provedores.endereco_local(f"http://{host}"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _conexao(provedor_id: Any, url: Any, tipo: Any, chave: Any,
             url_exata: bool = False) -> tuple[provedores.Provedor, str, str, str | None]:
    """Confere e limpa (serviço, url, tipo, chave). ValueError com mensagem para a tela.

    `url_exata` (só em "Outro"): o endereço fica como foi digitado, sem ganhar /v1."""
    prov = provedores.provedor(provedor_id)
    if prov is None:
        raise ValueError("Serviço de IA desconhecido.")
    tipo = tipo or prov.tipo
    if tipo not in TIPOS_IA:
        raise ValueError("Tipo de IA desconhecido.")
    bruta = url if isinstance(url, str) and url.strip() else prov.url
    url = provedores.normalizar_url(bruta, tipo, versao=not (url_exata is True and prov.id == "outro"))
    try:
        partes = urllib.parse.urlsplit(url)
        partes.port  # noqa: B018 - porta inválida levanta ValueError
    except ValueError:
        raise ValueError("Endereço da IA inválido.") from None
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ValueError("Endereço da IA inválido.")
    chave = provedores.limpar_chave(chave) or None
    if chave:
        motivo = provedores.chave_nao_suportada(chave)
        if motivo:
            raise ValueError(motivo)
        if not provedores.chave_valida(chave):
            raise ValueError("A chave tem caracteres que não podem ser enviados. Copie a chave de novo.")
        if partes.scheme != "https" and not _rede_protegida(partes.hostname):
            raise ValueError("Para enviar a chave pela internet, o endereço precisa começar com https://.")
    elif prov.precisa_chave:
        raise ValueError("Este serviço precisa de uma chave.")
    return prov, url, tipo, chave


def _tempo_valido(valor: Any) -> float:
    if isinstance(valor, bool):
        raise ValueError("O tempo limite deve ser um número de segundos.")
    try:
        tempo = float(valor)
    except (TypeError, ValueError):
        raise ValueError("O tempo limite deve ser um número de segundos.") from None
    if not (tempo > 0 and math.isfinite(tempo)):
        raise ValueError("O tempo limite deve ser positivo.")
    return tempo


# ---------------------------------------------------------------- IA: formato da resposta

# Cascata por tipo: do mais rígido ao mais solto.
_FORMATOS: dict[str, tuple[str, ...]] = {
    "openai": ("json_schema", "json_object", "texto"),
    "ollama": ("esquema", "json"),
}
_MEMORIA_FORMATO: dict[tuple[str, str, str], str] = {}     # (tipo, url, modelo) -> formato que funcionou
_SEM_TEMPERATURA: set[tuple[str, str, str]] = set()        # modelos que recusam temperature=0
_TRAVA_MEMORIA = threading.Lock()

_RE_PENSAMENTO = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_RE_CERCA = re.compile(r"```[a-zA-Z]*\s*(.*?)\s*```", re.DOTALL)


def esquecer_formatos() -> None:
    """Esquece o que cada modelo aceitou (usado nos testes e ao trocar de configuração)."""
    with _TRAVA_MEMORIA:
        _MEMORIA_FORMATO.clear()
        _SEM_TEMPERATURA.clear()


def _procurar_objeto(texto: str) -> dict | None:
    """O primeiro objeto JSON dentro de um texto (em cerca ``` ou solto entre frases)."""
    for cerca in _RE_CERCA.finditer(texto):
        try:
            dados = json.loads(cerca.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(dados, dict):
            return dados
    decodificador = json.JSONDecoder()
    inicio, tentativas = texto.find("{"), 0
    while inicio >= 0 and tentativas < 50:
        try:
            dados, _ = decodificador.raw_decode(texto, inicio)
            if isinstance(dados, dict):
                return dados
        except json.JSONDecodeError:
            pass
        inicio = texto.find("{", inicio + 1)
        tentativas += 1
    return None


def _json_do_modelo(conteudo: Any, lista_vira: str | None = None, _nivel: int = 0) -> dict:
    """O JSON que o modelo escreveu. Aceita cercas ```json```, texto antes/depois do objeto,
    bloco <think> de modelos que "pensam alto" e JSON dentro de uma string."""
    if isinstance(conteudo, dict):
        return conteudo
    if not isinstance(conteudo, str):
        raise ValueError("Resposta da IA sem conteúdo.")
    bruto = _RE_PENSAMENTO.sub("", conteudo).strip()
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError as primeiro:
        dados = _procurar_objeto(bruto)
        if dados is None:
            raise primeiro
    if isinstance(dados, str) and _nivel == 0:
        return _json_do_modelo(dados, lista_vira, 1)
    if isinstance(dados, list) and lista_vira:
        return {lista_vira: dados}
    if not isinstance(dados, dict):
        raise ValueError("Resposta da IA fora do formato.")
    return dados


def _conteudo(resposta: dict, tipo: str) -> Any:
    """O texto que o modelo escreveu, nos dois formatos de resposta."""
    if tipo == "ollama":
        mensagem = resposta.get("message")
    else:
        escolhas = resposta.get("choices")
        primeira = escolhas[0] if isinstance(escolhas, list) and escolhas else None
        mensagem = primeira.get("message") if isinstance(primeira, dict) else None
    if not isinstance(mensagem, dict):
        raise ErroIA(_MSG["incompativel"], "incompativel", "ForaDoFormato")
    conteudo = mensagem.get("content")
    if isinstance(conteudo, list):      # conteúdo em partes: [{"type": "text", "text": "..."}]
        conteudo = "".join(p["text"] for p in conteudo if isinstance(p, dict) and isinstance(p.get("text"), str))
    recusa = mensagem.get("refusal")
    if (not isinstance(conteudo, (str, dict)) or not conteudo) and isinstance(recusa, str) and recusa.strip():
        raise ErroIA(_MSG["recusa"], "recusa", "Recusa")
    if conteudo is None or (isinstance(conteudo, str) and not conteudo.strip()):
        raise ErroIA(_MSG["formato"], "formato", "RespostaVazia")
    return conteudo


# ---------------------------------------------------------------- IA

class MotorIA:
    """Uma IA configurada (principal ou reserva). Só roda quando o usuário aperta ✨.

    Os métodos `tentar_*` levantam `ErroIA` quando a IA não serve (a `CadeiaIA` passa para a
    próxima). `analisar`, `responder` e `sugerir` nunca levantam: sem IA, ficam as regras.
    """

    def __init__(self, provedor: str, url: str | None, modelo: str, tipo: str | None = None,
                 chave: str | None = None, tempo_limite: float = TEMPO_LIMITE_PADRAO, url_exata: bool = False):
        prov, url, tipo, chave = _conexao(provedor, url, tipo, chave, url_exata)
        modelo = modelo.strip() if isinstance(modelo, str) else ""
        if not modelo:
            raise ValueError("Informe o modelo da IA.")
        self.provedor = prov.id
        self.nome_provedor = prov.nome
        self.url = url
        self.modelo = modelo
        self.tipo = tipo
        self.chave = chave
        self.tempo_limite = _tempo_valido(tempo_limite)
        self.nome = f"ia:{prov.id}/{modelo}"
        self.ultimo_erro: str | None = None     # só o nome do tipo de erro, nunca conteúdo
        self.formato: str | None = None         # último formato de resposta que funcionou
        self._regras = MotorRegras()

    def __repr__(self) -> str:                  # sem a chave
        return f"MotorIA({self.nome!r}, local={self.local})"

    @property
    def local(self) -> bool:
        return provedores.e_local(self.url, self.modelo)

    @property
    def _memoria(self) -> tuple[str, str, str]:
        return self.tipo, self.url, self.modelo

    def esquecer_formato(self) -> None:
        with _TRAVA_MEMORIA:
            _MEMORIA_FORMATO.pop(self._memoria, None)
            _SEM_TEMPERATURA.discard(self._memoria)
        self.formato = None

    # ------------------------------------------------------------ HTTP

    def _corpo(self, formato: str, sistema: str, usuario: str, esquema: dict, nome_esquema: str,
               exemplo: str, sem_temperatura: bool) -> dict:
        mensagens = [{"role": "system", "content": sistema + _PEDIR_JSON + exemplo},
                     {"role": "user", "content": usuario}]
        if self.tipo == "ollama":
            return {"model": self.modelo, "messages": mensagens, "format": esquema if formato == "esquema" else "json",
                    "stream": False, "options": {"temperature": 0}}
        corpo: dict[str, Any] = {"model": self.modelo, "messages": mensagens}
        if not sem_temperatura:
            corpo["temperature"] = 0
        if formato == "json_schema":
            corpo["response_format"] = {"type": "json_schema",
                                        "json_schema": {"name": nome_esquema, "strict": True, "schema": esquema}}
        elif formato == "json_object":
            corpo["response_format"] = {"type": "json_object"}
        return corpo

    def _chat(self, sistema: str, usuario: str, esquema: dict, nome_esquema: str, exemplo: str,
              lista_vira: str | None = None) -> dict:
        """Pede e devolve o JSON do modelo, descendo a cascata de formatos quando preciso."""
        prazo = time.monotonic() + self.tempo_limite
        formatos = _FORMATOS[self.tipo]
        with _TRAVA_MEMORIA:
            lembrado = _MEMORIA_FORMATO.get(self._memoria)
            sem_temperatura = self._memoria in _SEM_TEMPERATURA
        i = formatos.index(lembrado) if lembrado in formatos else 0
        recusa: _FalhaHttp | None = None
        while i < len(formatos):
            formato = formatos[i]
            corpo = self._corpo(formato, sistema, usuario, esquema, nome_esquema, exemplo, sem_temperatura)
            try:
                resposta = _requisitar(_rota(self.url, self.tipo, "chat"), corpo, prazo, self.chave)
            except _FalhaHttp as falha:
                if (self.tipo == "openai" and not sem_temperatura and falha.status in (400, 422)
                        and "temperature" in falha.detalhe):
                    sem_temperatura = True      # modelos que só aceitam a temperatura padrão
                    with _TRAVA_MEMORIA:
                        _SEM_TEMPERATURA.add(self._memoria)
                    continue
                if falha.status in (400, 422) and not _RE_ERRO_FORMATO.search(falha.detalhe):
                    if _RE_ERRO_GRANDE.search(falha.detalhe):
                        raise ErroIA(_MSG["grande"], "grande", "HTTPError") from None
                    if _RE_ERRO_MODELO.search(falha.detalhe):
                        raise ErroIA(_MSG["nao_encontrado"], "nao_encontrado", "HTTPError") from None
                if falha.status in _STATUS_FORMATO:
                    recusa = falha
                    i += 1
                    continue
                raise _erro_ia(falha, self.tempo_limite) from None
            except Exception as erro:  # noqa: BLE001 - rede, tempo, TLS...
                raise _erro_ia(erro, self.tempo_limite) from None
            conteudo = _conteudo(resposta, self.tipo)
            try:
                dados = _json_do_modelo(conteudo, lista_vira)
            except ValueError as erro:
                raise ErroIA(_MSG["formato"], "formato", type(erro).__name__) from None
            with _TRAVA_MEMORIA:
                _MEMORIA_FORMATO[self._memoria] = formato
            self.formato = formato
            return dados
        raise _erro_ia(recusa, self.tempo_limite) if recusa else ErroIA(_MSG["falha"])

    def _registrar_falha(self, erro: BaseException) -> ErroIA:
        erro_ia = erro if isinstance(erro, ErroIA) else ErroIA(_MSG["falha"], "falha", type(erro).__name__)
        self.ultimo_erro = erro_ia.causa
        return erro_ia

    def disponivel(self) -> bool:
        try:
            _requisitar(_rota(self.url, self.tipo, "modelos"), None,
                        time.monotonic() + min(5.0, self.tempo_limite), self.chave)
            return True
        except Exception:  # noqa: BLE001 - qualquer falha = indisponível
            return False

    # ------------------------------------------------------------ análise

    def tentar_analisar(self, texto: str, referencia: date, conhecimento: Conhecimento | None = None,
                        base: Proposta | None = None) -> Proposta:
        """Proposta da IA, conferida. Levanta ErroIA se a IA não responder ou nada conferir."""
        conhecimento = conhecimento or Conhecimento()
        base = _base_das_regras(texto, referencia, conhecimento, base)
        if not base.texto.strip():
            return base
        try:
            resposta = self._chat(_SISTEMA_ANALISE, _pedido_analise(base), _ESQUEMA_ANALISE, "itens",
                                  _EXEMPLO_ANALISE, lista_vira="itens")
            brutos = resposta.get("itens")
            if not isinstance(brutos, list):
                raise ErroIA(_MSG["formato"], "formato", "SemItens")
            itens, descartados = self._validar(brutos, base, base.referencia, conhecimento)
            if not itens:
                raise ErroIA(_MSG["vazio"], "vazio", "NadaConferiu")
            itens = self._completar(itens, base)
        except Exception as erro:  # noqa: BLE001 - tudo vira ErroIA
            raise self._registrar_falha(erro) from None
        avisos = list(base.avisos)
        if descartados:
            avisos.append(f"Descartei {descartados} {'item sugerido' if descartados == 1 else 'itens sugeridos'}"
                          f" pela IA que não {'conferia' if descartados == 1 else 'conferiam'} com o texto.")
        self.ultimo_erro = None
        return Proposta(texto=base.texto, referencia=base.referencia, itens=itens, pessoas=list(base.pessoas),
                        datas=list(base.datas), categorias=_categorias_da_proposta(itens, base.texto),
                        relacoes=list(base.relacoes), avisos=avisos, motor=self.nome)

    def analisar(self, texto: str, referencia: date, conhecimento: Conhecimento | None = None,
                 base: Proposta | None = None) -> Proposta:
        """Como `tentar_analisar`, mas sem IA fica a proposta das regras com um aviso."""
        conhecimento = conhecimento or Conhecimento()
        base = _base_das_regras(texto, referencia, conhecimento, base)
        try:
            return self.tentar_analisar(base.texto, base.referencia, conhecimento, base)
        except Exception:  # noqa: BLE001 - volta para as regras
            return replace(base, avisos=[*base.avisos, AVISO_IA])

    def _validar(self, brutos: list, base: Proposta, referencia: date,
                 conhecimento: Conhecimento) -> tuple[list[Item], int]:
        itens: list[Item] = []
        descartados = 0
        for bruto in brutos[:_MAX_ITENS_IA]:
            item = self._item(bruto, base, referencia, conhecimento)
            if item is None:
                descartados += 1
            else:
                itens.append(item)
        return itens, descartados + max(0, len(brutos) - _MAX_ITENS_IA)

    def _item(self, bruto: Any, base: Proposta, referencia: date, conhecimento: Conhecimento) -> Item | None:
        if not isinstance(bruto, dict):
            return None
        try:
            tipo = TipoItem(bruto.get("tipo"))
        except ValueError:
            return None
        trecho = bruto.get("trecho").strip() if isinstance(bruto.get("trecho"), str) else ""
        if not termos(trecho) or trecho not in base.texto:
            return None                                   # trecho inventado, alterado ou vazio ("Ela")
        dono = self._pessoa(bruto.get("pessoa"), base, conhecimento)
        if dono is None:
            return None                                   # pessoa que não está no texto
        pessoa, pessoa_id = dono
        # Itens das regras na mesma frase e do mesmo tipo: limitam a certeza e cobrem o que faltar.
        do_tipo = [i for i in base.itens if i.tipo == tipo and (i.trecho in trecho or trecho in i.trecho)]
        resumo = _texto_curto(bruto.get("resumo"), 200)
        par = _par_das_regras(do_tipo, resumo)

        tetos = [classificar_certeza(trecho)] + [i.certeza for i in do_tipo if i.certeza in _NIVEL]
        certeza = _limitar_certeza(bruto.get("certeza"), *tetos)

        if not resumo or _cobertura(resumo, base.texto) < 0.6:
            resumo = par.resumo if par else _texto_curto(trecho, 120)
        alvo = _texto_curto(bruto.get("alvo"), 120)
        if not alvo or _cobertura(alvo, trecho) < 1.0:
            alvo = par.alvo if par else None
        polaridade = _polaridade(bruto.get("polaridade"))
        if polaridade is None and par is not None:
            polaridade = par.polaridade

        categorias = _categorias_validas(bruto.get("categorias"))
        if not categorias:
            categorias = list(par.categorias) if par else categorias_de(trecho, 3)

        item = Item(tipo=tipo, resumo=resumo, trecho=trecho, certeza=certeza, categorias=categorias,
                    pessoa=pessoa, pessoa_id=pessoa_id, alvo=alvo, polaridade=polaridade)
        d = _data_da_expressao(bruto.get("data_expressao"), trecho, base.texto, base.datas, referencia)
        if d is not None:
            item.data_evento, item.data_expressao, item.precisao = d.data, d.expressao, d.precisao
            item.recorrente = d.recorrente or tipo == TipoItem.DATA_IMPORTANTE
        if tipo == TipoItem.ESTADO and item.data_evento is None:
            item.data_evento, item.precisao = referencia, Precisao.DIA
        if tipo == TipoItem.PENDENCIA:
            futura = item.data_evento if item.data_evento and item.data_evento > referencia else None
            item.lembrar_em = futura + timedelta(days=1) if futura else referencia + timedelta(days=3)

        if par is not None:
            item.importancia = par.importancia
        elif tipo in (TipoItem.RESTRICAO, TipoItem.DATA_IMPORTANTE):
            item.importancia = 3
        elif tipo == TipoItem.NOTA:
            item.importancia = 1
        if item.data_evento and item.data_evento > referencia and tipo not in (
                TipoItem.DATA_IMPORTANTE, TipoItem.ESTADO) and "Eventos futuros" not in item.categorias:
            item.categorias = (item.categorias[:1] + ["Eventos futuros"] + item.categorias[1:])[:5]
        return item

    @staticmethod
    def _pessoa(valor: Any, base: Proposta, conhecimento: Conhecimento) -> tuple[str | None, int | None] | None:
        """(pessoa, pessoa_id) do item; None quando a IA citou alguém que não está no texto."""
        if valor is None:
            return None, None
        if not isinstance(valor, str):
            return None
        chave = normalizar(valor)
        chave = re.sub(r"^(?:a|o|as|os)\s+", "", chave)
        chave = re.sub(r"\s+dela$", "", chave).strip()
        nome_dela = normalizar(conhecimento.nome_dela or "")
        if not chave or chave in _ELA or normalizar(valor) in _ELA or (nome_dela and chave in (
                nome_dela, nome_dela.split()[0])):
            return None, None
        for p in base.pessoas:
            chaves = {normalizar(x) for x in (p.nome, p.relacao, p.trecho) if x}
            chaves |= {re.sub(r"\s+dela$", "", re.sub(r"^(?:a|o|as|os)\s+", "", c)) for c in chaves}
            if chave in chaves:
                return (p.nome or p.relacao or valor.strip()), p.pessoa_id
        if not re.search(r"(?<!\w)" + re.escape(chave) + r"(?!\w)", normalizar(base.texto)):
            return None
        return valor.strip(), casar_pessoa(valor, list(conhecimento.pessoas or []))

    @staticmethod
    def _completar(itens_ia: list[Item], base: Proposta) -> list[Item]:
        """Frase que a IA deixou de fora fica com os itens das regras (nada se perde)."""
        texto = base.texto
        lista_frases = frases(texto)

        def frase_de(trecho: str) -> int:
            pos = texto.find(trecho)
            for n, (ini, fim, _) in enumerate(lista_frases):
                if ini <= pos < fim:
                    return n
            return -1

        cobertas = {frase_de(i.trecho) for i in itens_ia}
        extras = [i for i in base.itens if frase_de(i.trecho) not in cobertas]
        todos = [(texto.find(i.trecho), n, i) for n, i in enumerate(itens_ia + extras)]
        todos.sort(key=lambda x: (x[0], x[1]))
        return [i for _, _, i in todos]

    # ------------------------------------------------------------ resposta da busca

    def tentar_responder(self, pergunta: str, trechos: list[dict]) -> Resposta:
        """Resposta da IA citando só os trechos enviados. Levanta ErroIA quando não serve."""
        trechos = [t for t in (trechos or []) if isinstance(t, dict)]
        if not trechos:
            return self._regras.responder(pergunta, trechos)
        validos = {t.get("registro_id") for t in trechos
                   if isinstance(t.get("registro_id"), int) and not isinstance(t.get("registro_id"), bool)}
        enviados = [{"registro_id": t.get("registro_id"), "data_registro": t.get("data_registro"),
                     "resumo": t.get("resumo"), "trecho": t.get("trecho"), "certeza": t.get("certeza")}
                    for t in trechos]
        pedido = json.dumps(para_json({"pergunta": pergunta, "trechos": enviados}), ensure_ascii=False)
        try:
            resposta = self._chat(_SISTEMA_RESPOSTA, pedido, _ESQUEMA_RESPOSTA, "resposta", _EXEMPLO_RESPOSTA)
            texto = _texto_curto(resposta.get("texto"), 1500)
            citacoes = resposta.get("citacoes")
            if not texto or not isinstance(citacoes, list):
                raise ErroIA(_MSG["formato"], "formato", "ForaDoFormato")
            citadas = {_id(c) for c in citacoes} & validos
            if not citadas:
                if normalizar(texto).rstrip(".") == normalizar(NAO_ENCONTREI).rstrip(".") and not citacoes:
                    self.ultimo_erro = None     # a IA leu e disse, com honestidade, que não está ali
                    return Resposta(pergunta=pergunta, texto=NAO_ENCONTREI, motor=self.nome)
                raise ErroIA(_MSG["sem_citacao"], "sem_citacao", "SemCitacoesValidas")
        except Exception as erro:  # noqa: BLE001 - tudo vira ErroIA
            raise self._registrar_falha(erro) from None
        self.ultimo_erro = None
        usados = [t for t in trechos if t.get("registro_id") in citadas]
        return Resposta(pergunta=pergunta, texto=texto, encontrados=usados, motor=self.nome)

    def responder(self, pergunta: str, trechos: list[dict]) -> Resposta:
        """Como `tentar_responder`, mas sem IA fica a resposta das regras."""
        try:
            return self.tentar_responder(pergunta, trechos)
        except Exception:  # noqa: BLE001 - volta para o modelo de frase
            return self._regras.responder(pergunta, trechos)

    # ------------------------------------------------------------ sugestões

    def tentar_sugerir(self, contexto: list[dict], referencia: date) -> tuple[list[dict], list[str]]:
        """(sugestões, avisos). Até 3 gestos pequenos, cada um com `porque` e `registro_ids`
        que existem no contexto. Levanta ErroIA quando a IA não responde ou nada passa."""
        registros = _contexto_para_ia(contexto)
        if not registros:
            return [], []
        pedido = json.dumps(para_json({"data_de_hoje": referencia, "registros": registros}), ensure_ascii=False)
        try:
            resposta = self._chat(_SISTEMA_SUGESTAO, pedido, _ESQUEMA_SUGESTAO, "sugestoes", _EXEMPLO_SUGESTAO,
                                  lista_vira="sugestoes")
            brutas = resposta.get("sugestoes")
            if not isinstance(brutas, list):
                raise ErroIA(_MSG["formato"], "formato", "SemSugestoes")
            sugestoes, descartadas = _validar_sugestoes(brutas, registros)
            if brutas and not sugestoes:
                erro = ErroIA(_MSG["sugestoes"], "sugestoes", "SugestoesDescartadas")
                erro.descartadas = max(1, descartadas)      # a IA respondeu; nada passou na conferência
                raise erro
        except Exception as erro:  # noqa: BLE001 - tudo vira ErroIA
            raise self._registrar_falha(erro) from None
        self.ultimo_erro = None
        avisos = []
        if descartadas:
            avisos.append(f"Descartei {descartadas} {'sugestão' if descartadas == 1 else 'sugestões'} da IA "
                          f"que não {'passou' if descartadas == 1 else 'passaram'} na conferência.")
        return sugestoes, avisos

    def sugerir(self, contexto: list[dict], referencia: date) -> list[dict]:
        """[{texto, porque, registro_ids}]; lista vazia quando não há nada válido (ou sem IA)."""
        try:
            return self.tentar_sugerir(contexto, referencia)[0]
        except Exception:  # noqa: BLE001 - sem sugestão inventada
            return []


# ---------------------------------------------------------------- cadeia principal -> reserva -> regras

_ROTULO = {"principal": "A IA principal", "reserva": "A IA de reserva"}
_NINGUEM_ANALISOU = "Nenhuma IA respondeu; ficou o que as regras entenderam."
_NINGUEM_RESPONDEU = "Nenhuma IA respondeu; ficou a resposta das regras."
_NINGUEM_SUGERIU = "Nenhuma IA respondeu agora. Tente de novo mais tarde."
_RESPONDEU_RESERVA = "Quem respondeu foi a IA de reserva."


class CadeiaIA:
    """Principal → reserva → regras. Cada método devolve (resultado, quem, avisos):
    quem = "principal" | "reserva" | None (nenhuma IA respondeu; o resultado é o das regras)."""

    def __init__(self, principal: MotorIA | None, reserva: MotorIA | None):
        self.principal = principal
        self.reserva = reserva
        self.avisos_config: list[str] = []     # IA configurada que não deu para montar

    def _motores(self) -> list[tuple[str, MotorIA]]:
        return [(q, m) for q, m in (("principal", self.principal), ("reserva", self.reserva)) if m is not None]

    @property
    def envia_para_fora(self) -> bool:
        return any(not m.local for _, m in self._motores())

    def _tentar(self, chamar: Callable[[MotorIA], Any],
                erros: list[tuple[str, BaseException]] | None = None) -> tuple[Any, str | None, list[str]]:
        avisos = list(self.avisos_config)
        for qual, motor in self._motores():
            try:
                resultado = chamar(motor)
            except Exception as erro:  # noqa: BLE001 - passa para a próxima
                if erros is not None:
                    erros.append((qual, erro))
                if isinstance(erro, ErroIA) and erro.tipo == "sugestoes":
                    n = getattr(erro, "descartadas", 1)
                    avisos.append(f"{_ROTULO[qual]} respondeu, mas descartei {n} "
                                  f"{'sugestão que não passou' if n == 1 else 'sugestões que não passaram'} "
                                  "na conferência (citar um registro e respeitar as regras).")
                    continue
                mensagem = erro.mensagem if isinstance(erro, ErroIA) else _MSG["falha"]
                avisos.append(f"{_ROTULO[qual]} não respondeu. {mensagem}")
                continue
            if qual == "reserva":
                avisos.append(_RESPONDEU_RESERVA)
            return resultado, qual, avisos
        return None, None, avisos

    def analisar(self, texto: str, referencia: date, conhecimento: Conhecimento | None = None,
                 base: Proposta | None = None) -> tuple[Proposta, str | None, list[str]]:
        conhecimento = conhecimento or Conhecimento()
        base = _base_das_regras(texto, referencia, conhecimento, base)
        if not base.texto.strip():
            return base, None, []
        proposta, qual, avisos = self._tentar(
            lambda m: m.tentar_analisar(base.texto, base.referencia, conhecimento, base))
        if qual is None:
            return base, None, [*avisos, _NINGUEM_ANALISOU]
        return proposta, qual, avisos

    def responder(self, pergunta: str, trechos: list[dict]) -> tuple[Resposta, str | None, list[str]]:
        validos = [t for t in (trechos or []) if isinstance(t, dict)]
        regras = MotorRegras().responder(pergunta, validos)
        if not validos:
            return regras, None, []
        resposta, qual, avisos = self._tentar(lambda m: m.tentar_responder(pergunta, validos))
        if qual is None:
            return regras, None, [*avisos, _NINGUEM_RESPONDEU]
        return resposta, qual, avisos

    def sugerir(self, contexto: list[dict], referencia: date) -> tuple[list[dict], str | None, list[str]]:
        if not _contexto_para_ia(contexto):
            return [], None, []
        erros: list[tuple[str, BaseException]] = []
        resultado, qual, avisos = self._tentar(lambda m: m.tentar_sugerir(contexto, referencia), erros)
        if qual is None:
            # A IA respondeu, mas nada passou na conferência: não é "ninguém respondeu". Fica a
            # lista vazia com quem respondeu, e a tela diz que não há informação suficiente.
            responderam = [q for q, e in erros if isinstance(e, ErroIA) and e.tipo == "sugestoes"]
            if responderam:
                return [], responderam[-1], avisos
            return [], None, [*avisos, _NINGUEM_SUGERIU]
        sugestoes, extras = resultado
        return sugestoes, qual, [*avisos, *extras]


# ---------------------------------------------------------------- montagem a partir da configuração

def _motor_de_config(cfg: Any, tempo: float) -> MotorIA:
    """MotorIA de um `{provedor, url, modelo, tipo, chave}`. ValueError se faltar algo."""
    if not isinstance(cfg, dict):
        raise ValueError("Configuração da IA inválida.")
    provedor_id = cfg.get("provedor")
    if not provedor_id:
        achado = provedores.detectar_por_url(cfg.get("url") or "")
        provedor_id = achado.id if achado else ("ollama" if cfg.get("tipo") == "ollama" else "outro")
    return MotorIA(provedor_id, cfg.get("url"), cfg.get("modelo"), cfg.get("tipo"), cfg.get("chave"), tempo,
                   url_exata=cfg.get("url_exata") is True)


def obter_cadeia_ia(config: dict | None) -> CadeiaIA | None:
    """A cadeia de `ia_principal`/`ia_reserva` (tempo em `ia_tempo_limite`). None sem IA configurada."""
    if not isinstance(config, dict):
        return None
    try:
        tempo = _tempo_valido(config.get("ia_tempo_limite") or TEMPO_LIMITE_PADRAO)
    except ValueError:
        tempo = TEMPO_LIMITE_PADRAO
    motores: dict[str, MotorIA] = {}
    avisos: list[str] = []
    for qual in ("principal", "reserva"):
        cfg = config.get(f"ia_{qual}")
        if not cfg:
            continue
        try:
            motores[qual] = _motor_de_config(cfg, tempo)
        except Exception:  # noqa: BLE001 - configuração estranha nunca derruba o app
            avisos.append(f"{_ROTULO[qual]} está com a configuração incompleta; confira em Ajustes → IA.")
    if not motores:
        return None
    cadeia = CadeiaIA(motores.get("principal"), motores.get("reserva"))
    cadeia.avisos_config = avisos
    return cadeia


def problemas_da_config_ia(config: dict | None) -> dict[str, str]:
    """{"principal"|"reserva": por que a IA salva não dá para usar}, só das configuradas que não
    montam (um motor antigo migrado sem chave, chave indo por http:// para a internet...).
    As mensagens são as de `_conexao`: sem chave, URL nem modelo."""
    if not isinstance(config, dict):
        return {}
    try:
        tempo = _tempo_valido(config.get("ia_tempo_limite") or TEMPO_LIMITE_PADRAO)
    except ValueError:
        tempo = TEMPO_LIMITE_PADRAO
    problemas: dict[str, str] = {}
    for qual in ("principal", "reserva"):
        cfg = config.get(f"ia_{qual}")
        if not cfg:
            continue
        try:
            _motor_de_config(cfg, tempo)
        except ValueError as erro:
            mensagem = str(erro).strip()
            problemas[qual] = mensagem if mensagem[:1].isupper() else "Configuração da IA inválida."
        except Exception:  # noqa: BLE001 - configuração estranha nunca derruba o app
            problemas[qual] = "Configuração da IA inválida."
    return problemas


_RE_NAO_CONVERSA = re.compile(r"embed|whisper|tts|dall-e|moderation|transcri|image|rerank", re.IGNORECASE)


def listar_modelos(provedor: str, url: str | None, tipo: str | None, chave: str | None,
                   tempo: float = 15, url_exata: bool = False) -> list[str]:
    """Modelos de conversa que o serviço oferece, em ordem alfabética. ErroIA/ValueError com
    mensagem para a tela quando não dá."""
    prov, url, tipo, chave = _conexao(provedor, url, tipo, chave, url_exata)
    tempo = _tempo_valido(tempo)
    try:
        dados = _requisitar(_rota(url, tipo, "modelos"), None, time.monotonic() + tempo, chave)
    except Exception as erro:  # noqa: BLE001 - vira mensagem para a tela
        raise _erro_ia(erro, tempo) from None
    if tipo == "ollama":
        lista = dados.get("models")
        nomes = [m.get("name") or m.get("model") for m in lista if isinstance(m, dict)] if isinstance(lista, list) else None
    else:
        lista = dados.get("data") if isinstance(dados.get("data"), list) else dados.get("models")
        nomes = [m.get("id") for m in lista if isinstance(m, dict)] if isinstance(lista, list) else None
    if nomes is None:
        raise ErroIA(_MSG["incompativel"], "incompativel", "ForaDoFormato")
    limpos: set[str] = set()
    for nome in nomes:
        if not isinstance(nome, str) or not nome.strip():
            continue
        nome = nome.strip()
        if prov.id == "google" and nome.startswith("models/"):
            nome = nome[len("models/"):]
        if not _RE_NAO_CONVERSA.search(nome):
            limpos.add(nome)
    return sorted(limpos, key=lambda n: (n.lower(), n))


def testar_ia(cfg_ia: dict, tempo: float | None = None) -> dict:
    """Testar conexão: manda só a FRASE_TESTE (inventada) e devolve {ok, segundos, formato, erro}.

    `formato` é o formato de resposta que o modelo aceitou ("json_schema", "json_object", "texto",
    "esquema" ou "json"). `erro` é a mensagem para a tela quando não deu certo."""
    inicio = time.monotonic()

    def resultado(ok: bool, formato: str | None = None, erro: str | None = None) -> dict:
        return {"ok": ok, "segundos": round(time.monotonic() - inicio, 2), "formato": formato, "erro": erro}

    try:
        if tempo is None and isinstance(cfg_ia, dict):
            tempo = cfg_ia.get("tempo_limite")
        motor = _motor_de_config(cfg_ia, _tempo_valido(tempo if tempo is not None else TEMPO_LIMITE_PADRAO))
    except ValueError as erro:
        return resultado(False, erro=str(erro))
    motor.esquecer_formato()        # testar é descobrir de novo o que o modelo aceita
    try:
        motor.tentar_analisar(FRASE_TESTE, _DATA_TESTE, Conhecimento())
    except ErroIA as erro:
        return resultado(False, erro=erro.mensagem)
    except Exception:  # noqa: BLE001
        return resultado(False, erro=_MSG["falha"])
    return resultado(True, formato=motor.formato)


# ---------------------------------------------------------------- comparação regras x IA

_CAMPOS_COMPARADOS = ("tipo", "resumo", "certeza", "categorias", "data_evento", "precisao", "pessoa", "alvo",
                      "polaridade", "importancia", "recorrente")


def comparar_propostas(regras: Proposta, ia: Proposta) -> list[dict]:
    """O que a IA mudou em relação às regras, para marcar os cartões da revisão.

    Uma entrada por item da IA, na ordem de `ia.itens`:
      {indice, indice_regras, situacao: "igual"|"mudou"|"novo", campos: [nomes dos campos que mudaram]}
    seguida de uma entrada por item das regras que a IA tirou:
      {indice: None, indice_regras, situacao: "removido", campos: [], resumo, tipo}
    """
    itens_r = list(regras.itens) if isinstance(regras, Proposta) else []
    itens_ia = list(ia.itens) if isinstance(ia, Proposta) else []

    def mesmo_alvo(a: Item, b: Item) -> bool:
        return normalizar(a.alvo or "") == normalizar(b.alvo or "")

    def sobrepoe(a: Item, b: Item) -> bool:
        return bool(a.trecho and b.trecho) and (a.trecho in b.trecho or b.trecho in a.trecho)

    criterios: list[Callable[[Item, Item], bool]] = [
        lambda a, b: a.trecho == b.trecho and a.tipo == b.tipo and mesmo_alvo(a, b),
        lambda a, b: a.trecho == b.trecho and a.tipo == b.tipo,
        lambda a, b: sobrepoe(a, b) and a.tipo == b.tipo,
        lambda a, b: a.trecho == b.trecho,
    ]
    pares: dict[int, int] = {}
    usados: set[int] = set()
    for criterio in criterios:
        for i, novo in enumerate(itens_ia):
            if i in pares:
                continue
            for j, velho in enumerate(itens_r):
                if j not in usados and criterio(novo, velho):
                    pares[i] = j
                    usados.add(j)
                    break
    saida: list[dict] = []
    for i, novo in enumerate(itens_ia):
        if i not in pares:
            saida.append({"indice": i, "indice_regras": None, "situacao": "novo", "campos": []})
            continue
        velho = itens_r[pares[i]]
        campos = [c for c in _CAMPOS_COMPARADOS if para_json(getattr(novo, c)) != para_json(getattr(velho, c))]
        saida.append({"indice": i, "indice_regras": pares[i], "situacao": "mudou" if campos else "igual",
                      "campos": campos})
    for j, velho in enumerate(itens_r):
        if j not in usados:
            saida.append({"indice": None, "indice_regras": j, "situacao": "removido", "campos": [],
                          "resumo": velho.resumo, "tipo": velho.tipo.value})
    return saida


# ---------------------------------------------------------------- escolha do motor e privacidade

def obter_motor(config: dict | None = None) -> MotorRegras:
    """O motor do app: SEMPRE as regras (seção 17). A IA só roda por botão, via `obter_cadeia_ia`."""
    return MotorRegras()


def ia_envia_para_fora(cfg_ia: dict | None) -> bool:
    """True se essa IA manda o texto para fora deste computador: endereço que não é local, ou
    modelo "-cloud" (o Ollama local repassa para ollama.com). Sem IA: False.

    Aceita uma IA (`{provedor, url, modelo, ...}`) ou a configuração inteira (`ia_principal`
    e `ia_reserva`: True se qualquer uma sair). Configuração que não dá para entender conta como
    "sai" (é o lado seguro para avisar).
    """
    if not isinstance(cfg_ia, dict) or not cfg_ia:
        return False
    if "ia_principal" in cfg_ia or "ia_reserva" in cfg_ia:
        return any(ia_envia_para_fora(cfg_ia.get(k)) for k in ("ia_principal", "ia_reserva"))
    prov = provedores.provedor(cfg_ia.get("provedor"))
    url = cfg_ia.get("url") if isinstance(cfg_ia.get("url"), str) and cfg_ia.get("url").strip() else (
        prov.url if prov else "")
    if not url:
        return True
    return not provedores.e_local(url, cfg_ia.get("modelo"))


def envia_para_fora(config: dict | None) -> bool:
    """Compatibilidade: o motor do app (as regras) nunca manda nada para fora. Para saber se
    uma IA configurada sai do computador, use `ia_envia_para_fora`."""
    return False


__all__ = ["AVISO_IA", "FRASE_TESTE", "NAO_ENCONTREI", "OLLAMA_PADRAO", "SEM_SUGESTAO", "TEMPO_LIMITE_PADRAO",
           "CadeiaIA", "Conhecimento", "ErroIA", "Motor", "MotorIA", "MotorRegras", "comparar_propostas",
           "conhecimento_do_banco", "data_extenso", "envia_para_fora", "esquecer_formatos", "ia_envia_para_fora",
           "juntar", "listar_modelos", "obter_cadeia_ia", "obter_motor", "problemas_da_config_ia", "so_o_escrito",
           "testar_ia"]
