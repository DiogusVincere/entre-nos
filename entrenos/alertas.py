"""Página "Hoje" (DESIGN.md, seção 11): alertas, próximos acontecimentos e coisas para lembrar.

Tudo sai do que está gravado. Cada alerta diz de onde veio (`porque` cita o trecho e a data
do registro, `registro_ids` abre o contexto); sem dado, sem alerta: nada genérico é inventado.
Sugestão é sempre possibilidade ("Talvez..."). As datas passam por regras/datas.py.

Níveis:
- importante: evento (ou aniversário) hoje ou amanhã;
- lembrete: evento ou data recorrente a N dias, com N em config["antecedencias"];
  mesversário e aniversário de namoro (config["inicio_namoro"]) 7 dias antes e no dia;
- sugestao: pendência vencida, evento que passou há 1-2 dias e preocupava, estado negativo
  recente. O APOIO registrado entra no `porque`.

Anti-excesso: todos os importantes, até 3 lembretes e 1 sugestão. Dispensados não voltam; a
chave junta a origem, a data e a antecedência ("evento:12:2026-10-20:d7").

Recursos (seção 20.0): os `cartoes_hoje` de cada recurso entram no mesmo anti-excesso, que continua
central: todos os importantes, até 3 lembretes somando todo mundo, e no máximo 1 sugestão por
fornecedor (as do Hoje e as de cada recurso) e 2 no total. Cartão de recurso vem como
`CartaoRecurso` (um Alerta com `recurso`, `tipo` e `dados`), com a chave "<NOME>:...".
Desde a integração (20.10): a sugestão do núcleo vem primeiro, as dos recursos com relevância ≥ 70
pela relevância e as outras num rodízio pelo dia; cada recurso fica sabendo quais cartões dele
apareceram (`cartoes_mostrados`); e o registro discreto (o ciclo) não mostra o começo do texto em
"Últimos registros".
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any

from . import config as cfg
from . import recursos
from .modelos import Alerta, Certeza, Item, Precisao, Relacao, TipoItem, para_json
from .regras.datas import extrair_datas, proxima_ocorrencia
from .regras.pessoas import RELACOES, e_dela, genero_relacao, relacao_base
from .texto import data_br, normalizar, tempo_atras

if TYPE_CHECKING:
    from .banco import Banco

MAX_LEMBRETES = 3
MAX_SUGESTOES = 1               # por fornecedor (o Hoje e cada recurso)
MAX_SUGESTOES_TOTAL = 2
SUGESTAO_URGENTE = 70           # sugestão de recurso a partir disto disputa só pela relevância (sem rodízio)
MAX_CARTOES_POR_RECURSO = 20    # o que um recurso pode mandar (antes do anti-excesso)
_RE_TIPO_CARTAO = re.compile(r"[a-z][a-z0-9_-]{0,39}")
DIAS_PROXIMOS = 30
DIAS_NAMORO = (7, 0)            # mesversário e aniversário de namoro: 7 dias antes e no dia
DIAS_ESTADO_RECENTE = 7         # estado negativo que ainda vale uma sugestão
DIAS_DEPOIS_EVENTO = (1, 2)     # "talvez perguntar como foi"
DIAS_PENDENCIA_ABERTA = 90      # pendência mais velha que isso sai de "Coisas para lembrar"
MAX_LEMBRAR = 10
MAX_ULTIMOS = 5
_PAGINA = 1000                  # limite de banco.registros()

_TIPOS_COM_DATA = (TipoItem.EVENTO, TipoItem.DATA_IMPORTANTE, TipoItem.PENDENCIA)
RESUMO_DISCRETO = "🔒 Um registro reservado (abra para ver)."
CARTAO_RESERVADO = "🔒 Um aviso reservado (abra o registro para ver)."

_ORDEM_TIPO = {"aniversario_dela": 0, "namoro": 1, "mesversario": 1, "aniversario": 2, "evento": 3,
               "data_importante": 4, "pendencia": 5}

_MESES = "janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
# A data que itens.py acrescenta no fim do resumo ("— 20/10/2026", "em novembro/2026").
_RE_DATA_FINAL = re.compile(
    r"\s*(?:—\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?|\b(?:em|no\s+dia|dia)\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|\bem\s+(?:" + _MESES + r")(?:/\d{4})?|\bem\s+\d{4}|\bna\s+semana\s+de\s+\d{1,2}/\d{1,2}/\d{4}"
    r"|\([^()]*\))\s*$", re.IGNORECASE)
_RE_PREFIXO_PESSOA = re.compile(r"^([^:]{1,60}):\s+(\S.*)$")
_RE_PREOCUPACAO = re.compile(
    r"\b(?:preocupad[ao]s?|preocupa\w*|nervos[ao]s?|ansios[ao]s?|ansiedade|apreensiv[ao]s?|tens[ao]s?"
    r"|medo|estressad[ao]s?|angustiad[ao]s?|aflit[ao]s?|insegur[ao]s?)\b")
_RE_ANIVERSARIO = re.compile(r"\b(?:aniversario|niver|faz(?:er)?\s+anos|completa\s+(?:\d+\s+)?anos)\b")

# Artigo dos nomes de evento que itens.py usa no começo do resumo ("a apresentação no trabalho").
_ARTIGOS = {
    "apresentacao": "a", "prova": "a", "provao": "o", "entrevista": "a", "cirurgia": "a",
    "operacao": "a", "consulta": "a", "exame": "o", "exames": "os", "viagem": "a", "show": "o",
    "casamento": "o", "formatura": "a", "reuniao": "a", "festa": "a", "festinha": "a", "mudanca": "a",
    "defesa": "a", "tcc": "o", "vestibular": "o", "concurso": "o", "voo": "o", "audiencia": "a",
    "curso": "o", "aula": "a", "jogo": "o", "palestra": "a", "dentista": "o", "batizado": "o",
    "plantao": "o", "primeiro": "o", "primeira": "a", "nossa": "a", "nosso": "o", "aniversario": "o",
    "mesversario": "o", "cha": "o", "recital": "o", "espetaculo": "o", "peca": "a", "encontro": "o",
    "jantar": "o", "almoco": "o", "passeio": "o", "treino": "o", "competicao": "a", "estreia": "a",
    "inauguracao": "a", "missa": "a", "resultado": "o", "retorno": "o", "natal": "o", "bodas": "as",
}
_VERBOS_DIRETOS = frozenset("vai ainda ficou precisa tem nao esta ta aguarda espera quer".split())


# ------------------------------------------------------------------ utilidades de data

def somar_meses(d: date, meses: int) -> date:
    """07/03 + 7 meses = 07/10; 31/01 + 1 mês = 28/02 (o dia vai para o fim do mês)."""
    total = d.year * 12 + d.month - 1 + meses
    ano, mes = divmod(total, 12)
    mes += 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def meses_completos(inicio: date, referencia: date) -> int:
    """Meses inteiros entre as duas datas (0 se a referência vier antes)."""
    if referencia < inicio:
        return 0
    meses = (referencia.year - inicio.year) * 12 + referencia.month - inicio.month
    return meses if somar_meses(inicio, meses) <= referencia else meses - 1


def duracao(meses: int) -> str:
    """19 -> '1 ano e 7 meses'."""
    anos, resto = divmod(meses, 12)
    partes = []
    if anos:
        partes.append("1 ano" if anos == 1 else f"{anos} anos")
    if resto or not anos:
        partes.append("1 mês" if resto == 1 else f"{resto} meses")
    return " e ".join(partes)


def tempo_juntos(inicio: date | None, referencia: date) -> str | None:
    """'6 meses', '1 ano e 7 meses', '3 semanas'. None sem data de início (ou no futuro)."""
    if inicio is None or inicio > referencia:
        return None
    meses = meses_completos(inicio, referencia)
    if meses:
        return duracao(meses)
    dias = (referencia - inicio).days
    if dias == 0:
        return "desde hoje"
    if dias < 7:
        return "1 dia" if dias == 1 else f"{dias} dias"
    semanas = dias // 7
    return "1 semana" if semanas == 1 else f"{semanas} semanas"


def _dia(d: date, referencia: date) -> str:
    """'12/09' no mesmo ano da referência; senão '12/09/2025'."""
    return d.strftime("%d/%m") if d.year == referencia.year else data_br(d)


def _ha(d: date, referencia: date) -> str:
    """'Hoje', 'Ontem', 'Há 3 dias', 'Há 2 meses'."""
    t = tempo_atras((referencia - d).days)
    return t[:1].upper() + t[1:]


# ------------------------------------------------------------------ texto

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _minuscula(s: str) -> str:
    """Primeira letra minúscula, menos em sigla ("TCC")."""
    primeira = s.split(" ", 1)[0]
    if len(primeira) > 1 and primeira.isupper():
        return s
    return s[:1].lower() + s[1:]


def _com_artigo(rotulo: str) -> str | None:
    """'Apresentação no trabalho' -> 'a apresentação no trabalho'; None se não souber o artigo."""
    m = re.match(r"[\wÀ-ÿ]+", rotulo)
    artigo = _ARTIGOS.get(normalizar(m.group())) if m else None
    return f"{artigo} {_minuscula(rotulo)}" if artigo else None


def _sem_data(resumo: str) -> str:
    return _RE_DATA_FINAL.sub("", resumo).strip() or resumo.strip()


def _encurtar(s: str, maximo: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= maximo:
        return s
    corte = s[:maximo].rsplit(" ", 1)[0]
    return corte.rstrip(" ,;:") + "…"


def de_quem(nome: str | None, relacao: str | None) -> str:
    """'da mãe dela', 'da mãe dela, Sandra', 'de Mariana', 'da sua mãe', 'dela'."""
    if relacao:
        base = relacao_base(relacao)
        artigo = {"f": "da", "m": "do"}.get(genero_relacao(relacao) or "", "do(a)")
        if normalizar(base) == "pais":
            artigo = "dos"
        if relacao.endswith("(sua)"):
            texto = f"da sua {base}"
        elif relacao.endswith("(seu)"):
            texto = f"do seu {base}"
        elif relacao.endswith("(de vocês)"):
            texto = f"{artigo} {base} de vocês"
        elif e_dela(relacao):
            texto = f"{artigo} {base} dela"
        else:
            texto = f"{artigo} {relacao}"
        return f"{texto}, {nome}" if nome else texto
    return f"de {nome}" if nome else "dela"


def _sujeito(dono: tuple[str | None, str | None] | None) -> str:
    """Quem faz a ação numa frase: 'ela', 'Mariana', 'a mãe dela', 'sua mãe'."""
    if dono is None:
        return "ela"
    nome, relacao = dono
    if nome:
        return nome
    base = relacao_base(relacao)
    if relacao.endswith("(sua)"):
        return f"sua {base}"
    if relacao.endswith("(seu)"):
        return f"seu {base}"
    artigo = {"f": "a", "m": "o"}.get(genero_relacao(relacao) or "", "o(a)")
    return f"{artigo} {base} dela" if e_dela(relacao) else f"{artigo} {relacao}"


def _de_ela(config: dict) -> str:
    nome = config.get("nome_dela")
    return f"da {nome}" if nome else "dela"


# ------------------------------------------------------------------ o que se lê do banco

@dataclass
class Dados:
    """O que alertas e perfil consultam do banco, lido uma vez só."""

    referencia: date
    config: dict
    registros: dict[int, dict]          # id -> resumo de banco.registros()
    itens: list[Item]                   # todos, em qualquer situação, mais recentes primeiro
    pessoas: dict[int, dict]
    relacoes: list[Relacao]
    # Registros cujo texto fica só na tela do recurso (o ciclo, `recursos.discretos`): o Hoje não
    # mostra o começo do texto e Pessoas não cita o trecho. Os itens deles já chegam aqui sem o que é
    # reservado (`sem_o_reservado`, seção 20.10), com ou sem o dado do ciclo confirmado.
    discretos: frozenset[int] = frozenset()

    @cached_property
    def ativos(self) -> list[Item]:
        return [i for i in self.itens if (i.status or "ativo") == "ativo"]

    @cached_property
    def por_id(self) -> dict[int, Item]:
        return {i.id: i for i in self.itens if i.id is not None}

    def data_registro(self, item: Item) -> date | None:
        r = self.registros.get(item.registro_id)
        return r["data_registro"] if r else None

    def quando(self, item: Item) -> date | None:
        """Data do que aconteceu (estado, evento); sem ela, a do registro."""
        return item.data_evento or self.data_registro(item)

    def dono(self, item: Item) -> tuple[str | None, str | None] | None:
        """(nome, relação) de quem é o item; None quando é dela."""
        if item.pessoa_id is not None and item.pessoa_id in self.pessoas:
            p = self.pessoas[item.pessoa_id]
            return p["nome"], p["relacao"]
        texto = (item.pessoa or "").strip()
        if not texto:
            return None
        if normalizar(relacao_base(texto)) in RELACOES:
            return None, texto
        return texto, None

    def rotulo(self, item: Item, sem_data: bool = True) -> str:
        """Resumo sem a data e com o dono no fim: 'Mãe: cirurgia — 05/11' -> 'Cirurgia da mãe dela'."""
        r = _sem_data(item.resumo) if sem_data else item.resumo.strip()
        dono = self.dono(item)
        if dono and sem_data:
            m = _RE_PREFIXO_PESSOA.match(r)
            if m:
                r = f"{_cap(m.group(2))} {de_quem(*dono)}"
        return r or item.resumo

    def trecho(self, item: Item) -> str:
        """O trecho que as telas centrais podem citar: "" para a frase com palavras reservadas de um
        registro discreto (o ciclo, seção 20.10); aí a tela cita o resumo."""
        t = item.trecho or ""
        if t and item.registro_id in self.discretos and recursos.texto_reservado(t):
            return ""
        return t

    def citacao(self, item: Item) -> str:
        """'Você registrou em 28/09: “...”' (o trecho é literal)."""
        dr = self.data_registro(item)
        quando = f" em {_dia(dr, self.referencia)}" if dr else ""
        trecho = self.trecho(item)
        if trecho:
            return f"Você registrou{quando}: “{trecho.strip()}”"
        return f"Você registrou{quando}: {item.resumo.strip().rstrip('.')}."


# Itens que explicariam humor ou comportamento: de uma frase do ciclo, nunca (seção 20.8).
_TIPOS_DE_HUMOR = (TipoItem.ESTADO, TipoItem.PADRAO, TipoItem.APOIO)


def sem_o_reservado(itens: list[Item], discretos: frozenset[int] | set[int]) -> list[Item]:
    """Os itens que as telas centrais podem usar (seção 20.10). De um registro discreto (o ciclo):
    - resumo, alvo ou pessoa com palavras reservadas (`recursos.texto_reservado`): o item sai;
    - trecho com essas palavras: humor, padrão e o que ajuda saem (o ciclo nunca explica humor nem
      entra em "Como ela está"); o resto fica, e as telas não citam a frase (`Dados.trecho`).
    Vale com ou sem o dado do ciclo confirmado na revisão, e para registros antigos."""
    saida: list[Item] = []
    for item in itens:
        if item.registro_id in discretos and (
                recursos.texto_reservado(" ".join(x for x in (item.resumo, item.alvo, item.pessoa) if x))
                or (item.tipo in _TIPOS_DE_HUMOR and recursos.texto_reservado(item.trecho or ""))):
            continue
        saida.append(item)
    return saida


def carregar(banco: Banco, referencia: date) -> Dados:
    """Lê config, registros, itens, pessoas e relações (também usado por perfil.py e pelos recursos).
    Os itens já vêm sem o que é reservado de um registro discreto (`sem_o_reservado`)."""
    registros: dict[int, dict] = {}
    antes = None
    while True:
        pagina = banco.registros(limite=_PAGINA, antes_de=antes)
        registros.update((r["id"], r) for r in pagina)
        if len(pagina) < _PAGINA:
            break
        antes = pagina[-1]["id"]
    discretos = frozenset(recursos.discretos(banco))
    return Dados(referencia=referencia, config=banco.config(), registros=registros,
                 itens=sem_o_reservado(banco.itens(status=None), discretos),
                 pessoas={p["id"]: p for p in banco.pessoas()},
                 relacoes=banco.relacoes(status=None), discretos=discretos)


# ------------------------------------------------------------------ preocupação e apoio

def _preocupacao(d: Dados, item: Item) -> tuple[bool, list[str], list[int]]:
    """O evento vinha com preocupação? Devolve (sim?, frases que citam a origem, registros)."""
    frases: list[str] = []
    registros: list[int] = []
    sim = "Preocupações" in (item.categorias or []) or bool(_RE_PREOCUPACAO.search(normalizar(item.trecho)))
    dono = d.dono(item)
    citados = {item.trecho}

    def mesmo_registro(outro: Item) -> None:
        nonlocal sim
        sim = True
        if d.trecho(outro) and outro.trecho not in citados:
            citados.add(outro.trecho)
            frases.append(f"No mesmo registro: “{outro.trecho.strip()}”")

    for outro in d.ativos:
        # "A mãe dela opera amanhã. Ela está preocupada.": a preocupação dela conta também.
        if (outro.tipo == TipoItem.ESTADO and outro.registro_id == item.registro_id
                and (outro.polaridade or 0) < 0 and d.dono(outro) in (dono, None)):
            mesmo_registro(outro)
            break
    for outro in d.ativos:
        # "...vai apresentar dia 20. Ela fica muito nervosa.": o padrão contado junto também.
        if (outro.tipo == TipoItem.PADRAO and outro.registro_id == item.registro_id
                and (outro.polaridade or 0) < 0 and d.dono(outro) in (dono, None)):
            mesmo_registro(outro)
    for r in d.relacoes:
        if r.tipo != "contexto" or r.status == "descartada" or r.item_novo != item.id:
            continue
        antigo = d.por_id.get(r.item_antigo_id)
        if antigo and antigo.status == "ativo" and antigo.tipo in (TipoItem.PADRAO, TipoItem.ESTADO) \
                and (antigo.polaridade or 0) < 0:
            sim = True
            if r.motivo:
                frases.append(r.motivo.strip())
            if antigo.registro_id is not None:
                registros.append(antigo.registro_id)
    return sim, frases, registros


def _frase_apoio(item: Item) -> str:
    """'Prefere que eu fique perto' -> 'Você registrou que ela prefere que você fique perto.'"""
    r = item.resumo.strip().rstrip(".")
    n = normalizar(r)
    for inicio, frase in (("o que ajuda:", "o que ajuda é"), ("o que nao ajuda:", "o que não ajuda é")):
        if n.startswith(inicio):
            return f"Você registrou que {frase} {r.split(':', 1)[1].strip()}."
    r = re.sub(r"\bque eu\b", "que você", r, flags=re.IGNORECASE)   # subjuntivo: "que eu fique"
    if re.search(r"\b(?:eu|mim|comigo)\b", r, flags=re.IGNORECASE):
        return f"Você registrou: “{_cap(r)}”."
    return f"Você registrou que ela {_minuscula(r)}."


def _apoio(d: Dados) -> tuple[list[str], list[int]]:
    """O que ajuda (e o que não ajuda) nela, para o `porque`: o mais certo e mais recente de cada."""
    dela = [i for i in d.ativos if i.tipo == TipoItem.APOIO and d.dono(i) is None]
    dela.sort(key=lambda i: (i.certeza == Certeza.CONFIRMADO, d.data_registro(i) or date.min, i.id or 0),
              reverse=True)
    escolhidos = [next((i for i in dela if (i.polaridade or 1) > 0), None),
                  next((i for i in dela if (i.polaridade or 1) < 0), None)]
    escolhidos = [i for i in escolhidos if i is not None]
    return [_frase_apoio(i) for i in escolhidos], [i.registro_id for i in escolhidos if i.registro_id]


# ------------------------------------------------------------------ datas que vêm por aí

@dataclass
class Ocorrencia:
    """Uma data da lista de próximos (e a origem de importantes e lembretes)."""

    data: date
    tipo: str                           # evento | pendencia | aniversario | aniversario_dela
                                        # | data_importante | mesversario | namoro
    texto: str                          # para a lista: "Apresentação no trabalho"
    origem: str                         # parte estável da chave: "evento:12", "pessoa:3", "dela"
    porque: list[str]
    frase: str | None = None            # com artigo, para frases: "a apresentação no trabalho"
    item: Item | None = None
    pessoa_id: int | None = None
    registro_ids: list[int] = field(default_factory=list)
    precisao: Precisao | None = Precisao.DIA
    avisa: bool = True                  # False: só aparece na lista (data imprecisa, pendência)
    so_namoro: bool = False             # avisa só 7 dias antes e no dia
    meses: int | None = None            # tempo juntos, para "Vocês completam 7 meses juntos"
    preocupacao: bool = False
    importancia: int = 2

    def dias(self, referencia: date) -> int:
        return (self.data - referencia).days

    def para_dict(self, referencia: date) -> dict:
        return {"data": self.data, "texto": self.texto, "dias": self.dias(referencia), "tipo": self.tipo,
                "item_id": self.item.id if self.item else None,
                "registro_id": self.registro_ids[0] if self.registro_ids else None,
                "pessoa_id": self.pessoa_id,
                "precisao": self.precisao.value if self.precisao else None}


def _tema(item: Item) -> str | None:
    """'mesversario' | 'namoro' | 'aniversario' | None, pelo resumo (ou pelo trecho)."""
    for texto in (item.resumo, item.trecho if item.tipo == TipoItem.DATA_IMPORTANTE else ""):
        n = normalizar(texto or "")
        if "mesversario" in n:
            return "mesversario"
        if "namoro" in n or "bodas" in n:
            return "namoro"
        if _RE_ANIVERSARIO.search(n):
            return "aniversario"
    return None


def _proxima_recorrente(item: Item, referencia: date) -> date | None:
    """Próxima vez da data que se repete. 'Dia das mães' muda de dia: recalcula pela expressão."""
    expr = item.data_expressao or ""
    if expr and not re.search(r"\d", expr):
        for achada in extrair_datas(expr, referencia):
            if achada.data and achada.recorrente and achada.data >= referencia:
                return achada.data
    try:
        return proxima_ocorrencia(item.data_evento.day, item.data_evento.month, referencia)
    except ValueError:
        return None


def _mesmo_dia(a: date | None, b: date) -> bool:
    """Mesmo dia e mês (o ano não importa numa data que se repete)."""
    return a is not None and (a.month, a.day) == (b.month, b.day)


def _mensais(dia: int, referencia: date, fim: date) -> list[date]:
    datas, ano, mes = [], referencia.year, referencia.month
    while True:
        d = date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))
        if d > fim:
            return datas
        if d >= referencia:
            datas.append(d)
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def _recorrentes(d: Dados, item: Item, fim: date, cobertos: set[tuple[int, int, int]]) -> list[Ocorrencia]:
    ref, base = d.referencia, item.data_evento
    inicio_namoro, aniv_dela = d.config.get("inicio_namoro"), d.config.get("aniversario_dela")
    tema = _tema(item)
    comum = {"item": item, "registro_ids": [item.registro_id] if item.registro_id else [],
             "porque": [d.citacao(item)], "importancia": item.importancia, "precisao": item.precisao}
    if tema == "mesversario":
        if inicio_namoro:
            return []      # quem manda é a data de início do namoro, em Ajustes
        return [Ocorrencia(data=x, tipo="mesversario", texto="Mesversário", origem=f"data:{item.id}",
                           frase="o mesversário de vocês", so_namoro=True, **comum)
                for x in _mensais(base.day, ref, fim)]
    dono = d.dono(item)
    if tema == "namoro" and _mesmo_dia(inicio_namoro, base):
        return []
    if tema == "aniversario" and dono is None and _mesmo_dia(aniv_dela, base):
        return []
    data = _proxima_recorrente(item, ref)
    if data is None or data > fim:
        return []
    dia_certo = (item.precisao or Precisao.DIA) == Precisao.DIA
    if tema == "aniversario":
        de = de_quem(*dono) if dono else _de_ela(d.config)
        if item.pessoa_id is not None:
            cobertos.add((item.pessoa_id, base.month, base.day))
        return [Ocorrencia(data=data, tipo="aniversario" if dono else "aniversario_dela",
                           texto=f"Aniversário {de}", frase=f"o aniversário {de}", origem=f"data:{item.id}",
                           pessoa_id=item.pessoa_id, avisa=dia_certo, **comum)]
    rotulo = d.rotulo(item)
    return [Ocorrencia(data=data, tipo="namoro" if tema == "namoro" else "data_importante", texto=rotulo,
                       frase=_com_artigo(rotulo), origem=f"data:{item.id}", pessoa_id=item.pessoa_id,
                       avisa=dia_certo, so_namoro=tema == "namoro", **comum)]


def _pontual(d: Dados, item: Item, fim: date) -> Ocorrencia | None:
    """Evento ou pendência com data: entra se começa na janela (semana/mês: se ainda está valendo)."""
    ref, inicio = d.referencia, item.data_evento
    precisao = item.precisao or Precisao.DIA
    if precisao == Precisao.SEMANA:
        ultimo = inicio + timedelta(days=6)
    elif precisao == Precisao.MES:
        ultimo = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
    else:
        ultimo = inicio
    if inicio > fim or ultimo < ref or (inicio < ref and precisao not in (Precisao.SEMANA, Precisao.MES)):
        return None
    dia_certo = precisao == Precisao.DIA
    evento = item.tipo != TipoItem.PENDENCIA
    rotulo = d.rotulo(item, sem_data=dia_certo)
    preocupa, frases, outros = _preocupacao(d, item) if evento else (False, [], [])
    return Ocorrencia(data=inicio, tipo="evento" if evento else "pendencia", texto=rotulo,
                      frase=_com_artigo(rotulo) if dia_certo else None, origem=f"evento:{item.id}",
                      porque=[d.citacao(item), *frases], item=item, pessoa_id=item.pessoa_id,
                      registro_ids=[r for r in dict.fromkeys([item.registro_id, *outros]) if r],
                      precisao=precisao, avisa=dia_certo and evento, preocupacao=preocupa,
                      importancia=item.importancia)


def _namoro(d: Dados, inicio: date, fim: date) -> list[Ocorrencia]:
    ref = d.referencia
    if inicio > ref:
        return []
    saida = []
    n = max(1, meses_completos(inicio, ref))
    data = somar_meses(inicio, n)
    while data < ref:
        n += 1
        data = somar_meses(inicio, n)
    while data <= fim:
        texto = duracao(n)
        saida.append(Ocorrencia(
            data=data, tipo="namoro" if n % 12 == 0 else "mesversario",
            texto=f"{texto} de namoro" if n % 12 == 0 else f"{texto} juntos",
            origem="namoro", porque=[f"Vocês estão juntos desde {data_br(inicio)} (informado em Ajustes)."],
            so_namoro=True, meses=n))
        n += 1
        data = somar_meses(inicio, n)
    return saida


def ocorrencias(d: Dados, dias: int) -> list[Ocorrencia]:
    """Datas entre a referência e referência + dias, sem repetição, em ordem."""
    ref = d.referencia
    fim = ref + timedelta(days=max(0, int(dias)))
    saida: list[Ocorrencia] = []
    cobertos: set[tuple[int, int, int]] = set()   # (pessoa_id, mês, dia) já vindos de um item
    for item in d.ativos:
        if item.data_evento is None or item.tipo not in _TIPOS_COM_DATA:
            continue
        if item.recorrente:
            saida += _recorrentes(d, item, fim, cobertos)
        else:
            oc = _pontual(d, item, fim)
            if oc is not None:
                saida.append(oc)
    for pid, p in d.pessoas.items():
        aniv = p.get("aniversario")
        if aniv is None or (pid, aniv.month, aniv.day) in cobertos:
            continue
        data = proxima_ocorrencia(aniv.day, aniv.month, ref)
        if data <= fim:
            de = de_quem(p["nome"], p["relacao"])
            saida.append(Ocorrencia(
                data=data, tipo="aniversario", texto=f"Aniversário {de}", frase=f"o aniversário {de}",
                origem=f"pessoa:{pid}", pessoa_id=pid,
                porque=[f"No cadastro de pessoas, o aniversário está em {aniv.strftime('%d/%m')}."]))
    aniv_dela = d.config.get("aniversario_dela")
    if aniv_dela is not None:
        data = proxima_ocorrencia(aniv_dela.day, aniv_dela.month, ref)
        if data <= fim:
            de = _de_ela(d.config)
            saida.append(Ocorrencia(
                data=data, tipo="aniversario_dela", texto=f"Aniversário {de}", frase=f"o aniversário {de}",
                origem="dela", importancia=3,
                porque=[f"O aniversário {de} é em {aniv_dela.strftime('%d/%m')} (informado em Ajustes)."]))
    if d.config.get("inicio_namoro") is not None:
        saida += _namoro(d, d.config["inicio_namoro"], fim)
    return _sem_repetidas(saida)


def _sem_repetidas(lista: list[Ocorrencia]) -> list[Ocorrencia]:
    """O mesmo evento registrado duas vezes vira um só (fica o registro mais recente)."""
    unicas: dict[tuple, Ocorrencia] = {}
    for oc in lista:
        chave = (oc.data, normalizar(oc.texto))
        if chave not in unicas:
            unicas[chave] = oc
            continue
        guardada = unicas[chave]
        guardada.registro_ids = list(dict.fromkeys([*guardada.registro_ids, *oc.registro_ids]))
        guardada.preocupacao = guardada.preocupacao or oc.preocupacao
        guardada.importancia = max(guardada.importancia, oc.importancia)
    return sorted(unicas.values(), key=lambda o: (o.data, _ORDEM_TIPO.get(o.tipo, 9), normalizar(o.texto)))


def proximos_eventos(banco: Banco, referencia: date, dias: int = DIAS_PROXIMOS) -> list[dict]:
    """[{data, texto, dias, tipo, item_id, registro_id, pessoa_id, precisao}] dos próximos `dias`.

    Inclui eventos e pendências com data, aniversários (itens, pessoas e o dela),
    mesversários e aniversários de namoro. Evento de semana ou mês ainda em curso também
    entra (com `dias` negativo).
    """
    d = carregar(banco, referencia)
    return [oc.para_dict(referencia) for oc in ocorrencias(d, dias)]


# ------------------------------------------------------------------ alertas

def _quando_futuro(dias: int) -> str:
    return "Hoje" if dias == 0 else "Amanhã" if dias == 1 else f"Daqui a {dias} dias"


def _texto_data(oc: Ocorrencia, dias: int) -> str:
    if oc.meses is not None:
        tempo = duracao(oc.meses)
        if dias == 0:
            return f"Hoje vocês completam {tempo} juntos."
        return f"Vocês completam {tempo} juntos daqui a {dias} {'dia' if dias == 1 else 'dias'}."
    if oc.frase:
        return f"{_quando_futuro(dias)} é {oc.frase}."
    return f"{_quando_futuro(dias)}: {oc.texto.rstrip('.')}."


def _avisos_de_data(d: Dados, lista: list[Ocorrencia], antecedencias: set[int],
                    apoio: tuple[list[str], list[int]]) -> tuple[list[Alerta], list[Alerta]]:
    importantes, lembretes = [], []
    for oc in lista:
        dias = oc.dias(d.referencia)
        if not oc.avisa or dias < 0:
            continue
        if oc.so_namoro:
            nivel = "lembrete" if dias in DIAS_NAMORO else None
        elif dias <= 1:
            nivel = "importante"
        else:
            nivel = "lembrete" if dias in antecedencias else None
        if nivel is None:
            continue
        porque = list(oc.porque)
        registros = list(oc.registro_ids)
        if oc.preocupacao:
            porque += apoio[0]
            registros += apoio[1]
        if nivel == "importante":
            relevancia = 90 + (5 if dias == 0 else 0) + (5 if oc.preocupacao else 0)
        else:
            relevancia = max(40, 80 - dias) + (5 if oc.tipo in ("aniversario_dela", "namoro", "mesversario") else 0) \
                + (3 if oc.preocupacao or oc.importancia >= 3 else 0)
        alerta = Alerta(chave=f"{oc.origem}:{oc.data.isoformat()}:d{dias}", nivel=nivel,
                        texto=_texto_data(oc, dias), porque=" ".join(porque),
                        relevancia=min(100 if nivel == "importante" else 89, relevancia), data=oc.data,
                        registro_ids=list(dict.fromkeys(r for r in registros if r)))
        (importantes if nivel == "importante" else lembretes).append(alerta)
    return importantes, lembretes


def _oracao_pendencia(d: Dados, item: Item) -> str | None:
    """'Esperando o resultado' -> 'ela estava esperando o resultado'; None se não der frase boa."""
    r = _sem_data(item.resumo)
    if d.dono(item) is not None:
        m = _RE_PREFIXO_PESSOA.match(r)
        r = m.group(2) if m else r
    r = r.strip().rstrip(".")
    if not r:
        return None
    primeira = normalizar(r.split()[0])
    sujeito = _sujeito(d.dono(item))
    if primeira.endswith("ndo") or primeira == "na":
        return f"{sujeito} estava {_minuscula(r)}"
    if primeira in _VERBOS_DIRETOS:
        return f"{sujeito} {_minuscula(r)}"
    return None


def _sugestao_pendencia(d: Dados, item: Item, apoio) -> Alerta:
    ref = d.referencia
    dr = d.data_registro(item) or item.lembrar_em
    quando = _ha(dr, ref)
    oracao = _oracao_pendencia(d, item)
    if oracao:
        texto = f"{quando} você registrou que {oracao}."
    else:
        texto = f"{quando} ficou um assunto em aberto: “{item.resumo.strip().rstrip('.')}”."
    texto += " Talvez seja um bom momento para perguntar como ficou."
    preocupa = bool(_RE_PREOCUPACAO.search(normalizar(item.trecho))) or "Preocupações" in item.categorias
    porque = [d.citacao(item), f"O lembrete era para {_dia(item.lembrar_em, ref)}."]
    registros = [item.registro_id]
    if preocupa or item.importancia >= 3:
        porque += apoio[0]
        registros += apoio[1]
    atraso = (ref - item.lembrar_em).days
    relevancia = 62 + (4 if item.importancia >= 3 else 0) + (4 if preocupa else 0) - min(atraso // 7, 6)
    return Alerta(chave=f"pendencia:{item.id}:{item.lembrar_em.isoformat()}", nivel="sugestao", texto=texto,
                  porque=" ".join(porque), relevancia=relevancia,
                  registro_ids=list(dict.fromkeys(r for r in registros if r)))


def _sugestao_depois(d: Dados, item: Item, apoio) -> Alerta | None:
    """Evento marcado com antecedência, que passou há 1-2 dias e preocupava."""
    ref, data = d.referencia, item.data_evento
    dias = (ref - data).days
    dr = d.data_registro(item)
    if dias not in DIAS_DEPOIS_EVENTO or dr is None or dr >= data:
        return None
    preocupa, frases, outros = _preocupacao(d, item)
    if not preocupa:
        return None
    rotulo = d.rotulo(item)
    frase = _com_artigo(rotulo)
    quando = "Ontem" if dias == 1 else "Anteontem"
    texto = f"{quando} foi {frase}." if frase else f"{quando}: {rotulo.rstrip('.')}."
    texto += " Talvez seja um bom momento para perguntar como foi."
    relevancia = (70 if dias == 1 else 66) + (4 if item.importancia >= 3 else 0)
    return Alerta(chave=f"depois:{item.id}:{data.isoformat()}", nivel="sugestao", texto=texto,
                  porque=" ".join([d.citacao(item), *frases, *apoio[0]]), relevancia=relevancia, data=data,
                  registro_ids=list(dict.fromkeys(r for r in [item.registro_id, *outros, *apoio[1]] if r)))


def _sugestao_estado(d: Dados, apoio, evitar: set[int]) -> Alerta | None:
    """O estado negativo mais recente dela (últimos 7 dias), se nada melhor veio depois."""
    ref = d.referencia
    estados = []
    for item in d.ativos:
        quando = d.quando(item)
        if item.tipo != TipoItem.ESTADO or d.dono(item) is not None or quando is None or quando > ref:
            continue
        estados.append((quando, item.id or 0, item))
    estados.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for quando, _, item in estados:
        if (ref - quando).days > DIAS_ESTADO_RECENTE:
            break
        if (item.polaridade or 0) >= 0:
            return None          # o registro mais recente é bom: nada a sugerir
        if item.registro_id in evitar:
            return None          # já aparece num alerta importante
        dias = (ref - quando).days
        prefixo = "Hoje" if dias == 0 else "Ontem" if dias == 1 else f"Há {dias} dias"
        alvo = item.alvo if item.alvo and len(item.alvo.split()) <= 4 else None
        if alvo:
            verbo = "ela comentou que estava" if item.certeza == Certeza.CONFIRMADO else "você registrou que ela estava"
            texto = f"{prefixo} {verbo} {alvo}."
        else:
            texto = f"{prefixo} você registrou: “{item.resumo.strip().rstrip('.')}”."
        texto += (" Talvez perguntar como foi o dia dela seja uma boa ideia." if dias == 0
                  else " Talvez perguntar como ela está agora seja uma boa ideia.")
        relevancia = 50 + (DIAS_ESTADO_RECENTE - dias) + (3 if item.importancia >= 3 else 0)
        return Alerta(chave=f"estado:{item.id}", nivel="sugestao", texto=texto,
                      porque=" ".join([d.citacao(item), *apoio[0]]), relevancia=relevancia, data=quando,
                      registro_ids=list(dict.fromkeys(r for r in [item.registro_id, *apoio[1]] if r)))
    return None


def _sugestoes(d: Dados, apoio, evitar: set[int]) -> list[Alerta]:
    ref = d.referencia
    saida: list[Alerta] = []
    for item in d.ativos:
        if item.tipo == TipoItem.PENDENCIA and item.lembrar_em is not None and item.lembrar_em <= ref:
            saida.append(_sugestao_pendencia(d, item, apoio))
        elif (item.tipo == TipoItem.EVENTO and item.data_evento is not None and not item.recorrente
              and (item.precisao or Precisao.DIA) == Precisao.DIA):
            alerta = _sugestao_depois(d, item, apoio)
            if alerta is not None:
                saida.append(alerta)
    estado = _sugestao_estado(d, apoio, evitar)
    if estado is not None:
        saida.append(estado)
    return saida


def _ordenar(alertas: list[Alerta]) -> list[Alerta]:
    return sorted(alertas, key=lambda a: (-a.relevancia, a.data or date.max, a.chave))


def _lembrar(d: Dados) -> list[dict]:
    """Pendências abertas (as vencidas primeiro) e estados dos últimos 7 dias."""
    ref = d.referencia
    pendencias, estados = [], []
    for item in d.ativos:
        dr = d.data_registro(item)
        if item.tipo == TipoItem.PENDENCIA and dr is not None and 0 <= (ref - dr).days <= DIAS_PENDENCIA_ABERTA:
            pendencias.append((item.lembrar_em or date.max, -(item.id or 0), item, dr))
        elif item.tipo == TipoItem.ESTADO:
            quando = d.quando(item)
            if quando is not None and 0 <= (ref - quando).days <= DIAS_ESTADO_RECENTE:
                estados.append((quando, item.id or 0, item, quando))
    pendencias.sort(key=lambda x: (x[0], x[1]))
    estados.sort(key=lambda x: (x[0], x[1]), reverse=True)
    saida = []
    for *_, item, quando in pendencias + estados:
        saida.append({"texto": f"{item.resumo.strip().rstrip('.')} ({tempo_atras((ref - quando).days)})",
                      "item_id": item.id, "registro_id": item.registro_id, "tipo": item.tipo.value,
                      "data": quando, "certeza": Certeza(item.certeza).value, "trecho": d.trecho(item)})

    return saida[:MAX_LEMBRAR]


def _ultimos(d: Dados) -> list[dict]:
    recentes = sorted(d.registros.values(), key=lambda r: (r["data_registro"], r["id"]), reverse=True)
    saida = []
    for r in recentes[:MAX_ULTIMOS]:
        if r["id"] in d.discretos:
            # Seção 20.8: o Hoje nunca mostra o que fala do ciclo (nem o começo do texto, nem quem).
            saida.append({"id": r["id"], "registro_id": r["id"], "data_registro": r["data_registro"],
                          "resumo": RESUMO_DISCRETO, "qtd_itens": 0, "categorias": [], "pessoas": [],
                          "discreto": True})
            continue
        saida.append({"id": r["id"], "registro_id": r["id"], "data_registro": r["data_registro"],
                      "resumo": _encurtar(r["texto"], 140), "qtd_itens": r.get("qtd_itens", 0),
                      "categorias": list(r.get("categorias") or []), "pessoas": list(r.get("pessoas") or [])})
    return saida


@dataclass
class CartaoRecurso(Alerta):
    """Um cartão do Hoje vindo de um recurso: `recurso` (NOME), `tipo` (o desenho da tela,
    `registrarCartaoHoje`) e `dados` (JSON para a tela)."""

    recurso: str = ""
    tipo: str = ""
    dados: dict = field(default_factory=dict)


def _data_do_cartao(valor: Any) -> date | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _cartao_de_recurso(nome: str, bruto: Any) -> CartaoRecurso | None:
    """Alerta ou dict do recurso -> CartaoRecurso conferido; None (com log) se não serve."""
    try:
        if isinstance(bruto, Alerta):
            bruto = {"chave": bruto.chave, "nivel": bruto.nivel, "texto": bruto.texto, "porque": bruto.porque,
                     "relevancia": bruto.relevancia, "data": bruto.data, "registro_ids": bruto.registro_ids,
                     "tipo": getattr(bruto, "tipo", None), "dados": getattr(bruto, "dados", None)}
        if not isinstance(bruto, dict):
            raise TypeError("cartão precisa ser um Alerta ou um dict")
        chave, nivel, texto = bruto.get("chave"), bruto.get("nivel"), bruto.get("texto")
        porque, relevancia = bruto.get("porque") or "", bruto.get("relevancia", 50)
        if not isinstance(chave, str) or not chave.strip() or len(chave) > 200:
            raise ValueError("chave")
        if nivel not in recursos.NIVEIS or not isinstance(texto, str) or not texto.strip():
            raise ValueError("nível ou texto")
        if not isinstance(porque, str) or isinstance(relevancia, bool) or not isinstance(relevancia, (int, float)):
            raise ValueError("porque ou relevância")
        ids = bruto.get("registro_ids") or []
        if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ValueError("registro_ids")
        tipo = bruto.get("tipo") or nome
        dados = bruto.get("dados") or {}
        # O tipo escolhe o desenho na tela (registrarCartaoHoje), que aceita só estes nomes.
        if not isinstance(tipo, str) or not _RE_TIPO_CARTAO.fullmatch(tipo) or not isinstance(dados, dict):
            raise ValueError("tipo ou dados")
        dados = para_json(dados)
        json.dumps(dados, ensure_ascii=False)
        chave = chave.strip()
        if not chave.startswith(f"{nome}:"):
            chave = f"{nome}:{chave}"
        return CartaoRecurso(chave=chave, nivel=nivel, texto=texto.strip(), porque=porque,
                             relevancia=int(min(100, max(0, relevancia))), data=_data_do_cartao(bruto.get("data")),
                             registro_ids=list(ids), recurso=nome, tipo=tipo, dados=dados)
    except Exception as erro:  # noqa: BLE001 - cartão fora do contrato não aparece
        recursos.registrar_erro(erro, nome, "cartoes_hoje (cartão)")
        return None


def cartoes_dos_recursos(banco: Banco, referencia: date, dispensados: set[str] | None = None) -> list[CartaoRecurso]:
    """Os cartões de todos os recursos, conferidos e sem os dispensados (ainda sem o anti-excesso)."""
    dispensados = banco.alertas_dispensados() if dispensados is None else dispensados
    saida: list[CartaoRecurso] = []
    for nome, brutos in recursos.cartoes_brutos(banco, referencia):
        for bruto in brutos[:MAX_CARTOES_POR_RECURSO]:
            cartao = _cartao_de_recurso(nome, bruto)
            if cartao is not None and cartao.chave not in dispensados:
                saida.append(cartao)
    return saida


def _escolher_sugestoes(do_nucleo: list[Alerta], dos_recursos: list[CartaoRecurso],
                        referencia: date) -> list[Alerta]:
    """As (no máximo) 2 sugestões do Hoje, uma por fornecedor (integração, seção 20.10).

    A do núcleo vem primeiro (é sobre ela, agora: uma pendência vencida, um dia difícil). Depois as
    dos recursos que pedem passagem (relevância ≥ SUGESTAO_URGENTE), pela relevância. As outras
    entram num rodízio pelo dia: sem ele, quem tem a relevância um pouco maior (as ideias de presente
    perto de uma data, por 45 dias) ocupava o Hoje todos os dias, e a pergunta da semana nunca
    aparecia. O mesmo dia dá sempre a mesma escolha."""
    urgentes = _ordenar([c for c in dos_recursos if c.relevancia >= SUGESTAO_URGENTE])
    demais = sorted((c for c in dos_recursos if c.relevancia < SUGESTAO_URGENTE), key=lambda c: c.recurso)
    if demais:
        k = referencia.toordinal() % len(demais)
        demais = demais[k:] + demais[:k]
    return _ordenar((_ordenar(list(do_nucleo))[:MAX_SUGESTOES] + urgentes + demais)[:MAX_SUGESTOES_TOTAL])


def _avisar_mostrados(banco: Banco, referencia: date, mostrados: list[CartaoRecurso]) -> None:
    """Diz a cada recurso quais cartões dele passaram pelo anti-excesso (o gancho `cartoes_mostrados`):
    quem grava "já sugeri" (as perguntas) grava só o que a pessoa viu de verdade."""
    por_recurso: dict[str, list[dict]] = {}
    for c in mostrados:
        chave = c.chave[len(c.recurso) + 1:] if c.chave.startswith(f"{c.recurso}:") else c.chave
        por_recurso.setdefault(c.recurso, []).append(
            {"chave": chave, "nivel": c.nivel, "tipo": c.tipo, "dados": dict(c.dados or {})})
    recursos.cartoes_mostrados(banco, referencia, por_recurso)


def _sem_palavras_reservadas(alerta: Alerta, discretos: frozenset[int]) -> Alerta:
    """Garantia final do Hoje (seção 20.10): cartão de recurso ligado a um registro discreto (o ciclo)
    com palavras reservadas no texto, no porquê ou nos dados vira um aviso neutro, com a mesma chave e
    os mesmos registros (abrir o registro mostra tudo). O texto do Hoje nunca usa essas palavras."""
    if not isinstance(alerta, CartaoRecurso) or not discretos.intersection(alerta.registro_ids or ()):
        return alerta
    tudo = " ".join([alerta.texto or "", alerta.porque or "", json.dumps(alerta.dados or {}, ensure_ascii=False)])
    if not recursos.texto_reservado(tudo):
        return alerta
    return Alerta(chave=alerta.chave, nivel=alerta.nivel, texto=CARTAO_RESERVADO, porque="",
                  relevancia=alerta.relevancia, data=alerta.data, registro_ids=list(alerta.registro_ids))


def hoje(banco: Banco, referencia: date) -> dict:
    """Tudo o que a página "Hoje" mostra.

    {"alertas": [Alerta] (filtrados e em ordem: importantes, lembretes, sugestões),
     "proximos": [{data, texto, dias, tipo, item_id, registro_id, ...}] (próximos 30 dias),
     "lembrar": [{texto, item_id, registro_id, tipo, data}],
     "ultimos": [{id, data_registro, resumo, qtd_itens, categorias, pessoas}]}
    Datas saem como `date` e alertas como `Alerta` (os dos recursos, `CartaoRecurso`): use
    modelos.para_json antes do json.dumps.
    """
    d = carregar(banco, referencia)
    antecedencias = d.config.get("antecedencias")
    if antecedencias is None:
        antecedencias = cfg.ANTECEDENCIAS_PADRAO
    antecedencias = {int(x) for x in antecedencias}
    janela = max([DIAS_PROXIMOS, 1, *DIAS_NAMORO, *antecedencias])
    lista = ocorrencias(d, janela)
    dispensados = banco.alertas_dispensados()
    apoio = _apoio(d)

    importantes, lembretes = _avisos_de_data(d, lista, antecedencias, apoio)
    importantes = [a for a in importantes if a.chave not in dispensados]
    evitar = {r for a in importantes for r in a.registro_ids}
    sugestoes = _ordenar([a for a in _sugestoes(d, apoio, evitar) if a.chave not in dispensados])[:MAX_SUGESTOES]

    # Anti-excesso central, com os cartões dos recursos (seção 20.0).
    cartoes = cartoes_dos_recursos(banco, referencia, dispensados)
    importantes = _ordenar(importantes + [c for c in cartoes if c.nivel == "importante"])
    lembretes = _ordenar([a for a in lembretes if a.chave not in dispensados]
                         + [c for c in cartoes if c.nivel == "lembrete"])[:MAX_LEMBRETES]
    dos_recursos: list[CartaoRecurso] = []
    for nome in dict.fromkeys(c.recurso for c in cartoes):
        dos_recursos += _ordenar([c for c in cartoes if c.recurso == nome and c.nivel == "sugestao"])[:MAX_SUGESTOES]
    sugestoes = _escolher_sugestoes(sugestoes, dos_recursos, referencia)

    alertas_ = importantes + lembretes + sugestoes
    _avisar_mostrados(banco, referencia, [a for a in alertas_ if isinstance(a, CartaoRecurso)])
    return {
        "alertas": [_sem_palavras_reservadas(a, d.discretos) for a in alertas_],
        "proximos": [oc.para_dict(referencia) for oc in lista if oc.dias(referencia) <= DIAS_PROXIMOS],
        "lembrar": _lembrar(d),
        "ultimos": _ultimos(d),
    }


__all__ = ["CartaoRecurso", "Dados", "Ocorrencia", "carregar", "cartoes_dos_recursos", "de_quem", "duracao", "hoje",
           "meses_completos", "ocorrencias", "proximos_eventos", "sem_o_reservado", "somar_meses", "tempo_juntos"]
