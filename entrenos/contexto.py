"""Detector de contexto: liga o que acabou de ser escrito ao que já está gravado (DESIGN.md, seção 9).

Três tipos de relação, todas só com o que está no banco e citando a origem:
- contradicao: preferência/restrição nova com o mesmo alvo e polaridade oposta a uma ativa
  (ou uma ação que a contraria: "pediu sushi" contra "não gosta de sushi"). A antiga nunca
  muda sozinha: quem decide é o usuário.
- contexto: evento novo e um padrão/estado/restrição antigo do mesmo assunto (e o contrário:
  padrão novo e evento antigo que ainda vai acontecer). A sugestão é sempre "Talvez...".
- mesma_pessoa: pessoa que já apareceu nos últimos 90 dias.

Os candidatos saem da busca (FTS5) com os termos do texto novo. No máximo 5 relações, as mais
fortes; sem relação, lista vazia (nada é forçado).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .banco import Banco
from .modelos import Certeza, Item, PessoaEncontrada, Precisao, Proposta, Relacao, TipoItem
from .regras.pessoas import RELACOES, casar_pessoa, e_dela, genero_relacao, plano, relacao_base
from .texto import normalizar, semelhanca, termos

LIMITE_RELACOES = 5
SEMELHANCA_MINIMA = 88
DIAS_MESMA_PESSOA = 90
DIAS_ESTADO = 60            # um estado antigo só serve de contexto se for recente

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro"]
_GOSTOS = (TipoItem.PREFERENCIA, TipoItem.RESTRICAO)


# ---------------------------------------------------------------- assuntos

@dataclass(frozen=True)
class _Assunto:
    nome: str
    evento: re.Pattern[str]         # como o evento aparece no resumo
    ligado: re.Pattern[str]         # como o assunto aparece num padrão/estado/restrição
    consulta: str                   # termos para a busca de candidatos
    destino: bool = False           # viagem: o lugar citado também liga


def _rx(raizes: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + raizes + r")")


_ASSUNTOS = [
    _Assunto("apresentacao", _rx(r"apresent|palestra|seminario|falar em publico"),
             _rx(r"apresent|falar em publico|em publico|palestra|seminario|plateia"),
             "apresentação apresentações público palestra seminário"),
    _Assunto("entrevista", _rx(r"entrevista|processo seletivo"),
             _rx(r"entrevista|emprego|trabalh|vaga|processo seletivo|desempreg"),
             "entrevista emprego trabalho vaga"),
    _Assunto("saude", _rx(r"cirurgi|exame|consulta|operac|operar|dentista|biopsia|ressonancia|tratamento|internac"),
             _rx(r"saude|cirurgi|exame|consulta|medic|hospital|doen|dor\b|remedio|tratamento|agulha|dentista"
                 r"|internad|operac"),
             "saúde cirurgia exame consulta médico hospital doença remédio agulha"),
    _Assunto("prova", _rx(r"prova|provao|vestibular|concurso|enem|simulado|tcc"),
             _rx(r"prova|estud|faculdade|vestibular|concurso|enem|reprov|materia|tcc"),
             "prova estudo estudar faculdade vestibular concurso"),
    _Assunto("viagem", _rx(r"viag|viaj|voo\b|aeroporto"),
             _rx(r"viag|viaj|aviao|avioes|voo\b|voar|aeroporto|onibus|estrada"),
             "viagem viajar avião voo voar aeroporto", destino=True),
]
_RE_DESTINO = re.compile(r"\b(?:para|pra|pro|em)\s+([A-ZÀ-Þ][\wÀ-ÿ]+(?:\s+(?:de\s+)?[A-ZÀ-Þ][\wÀ-ÿ]+)*)")

# Ações que contrariam um gosto antigo sem dizer "gosta"/"não gosta".
_RE_A_FAVOR = r"pediu|comeu|escolheu|quis|provou|tomou|bebeu|experimentou|repetiu|devorou|adorou|amou|curtiu"
_RE_CONTRA = r"recusou|rejeitou|dispensou|devolveu|nao\s+quis|nao\s+comeu|nao\s+tomou|nao\s+bebeu"
_ENCHIMENTO = r"(?:\s+(?:um|uma|uns|umas|o|a|os|as|de|do|da|dos|das|mais|muito|todo|toda|outro|outra|pouco))*"

# Começos de resumo que viram "você registrou que ela ...".
_INICIOS_VERBO = frozenset(normalizar(p) for p in """
não nao gosta gostava prefere fica ficou tem tinha está esta estava costuma morre quer queria gostaria
sonha ama adora odeia detesta sempre precisa vai anda se pode come bebe acha é
""".split())
_INICIOS_ADJETIVO = frozenset({"alergica", "alergico", "intolerante", "vegetariana", "vegana", "celiaca",
                               "diabetica"})


# ---------------------------------------------------------------- textos

def _minuscula(s: str) -> str:
    return s[:1].lower() + s[1:] if s[1:2] and not s[1:2].isupper() else s


def _data_curta(d: date, referencia: date) -> str:
    return d.strftime("%d/%m") if d.year == referencia.year else d.strftime("%d/%m/%Y")


def _quando_evento(item: Item, referencia: date) -> str | None:
    d = item.data_evento
    if d is None:
        return None
    if item.precisao == Precisao.MES:
        return _MESES[d.month - 1] + ("" if d.year == referencia.year else f" de {d.year}")
    if item.precisao == Precisao.ANO:
        return str(d.year)
    if item.precisao == Precisao.SEMANA:
        return f"a semana de {_data_curta(d, referencia)}"
    return _data_curta(d, referencia)


def _citacao(s: str, maximo: int = 30) -> str:
    palavras = s.split()
    curto = " ".join(palavras[:maximo]) + ("…" if len(palavras) > maximo else "")
    return f"“{curto}”"


def _registrou(item: Item) -> str:
    """'que ela não gosta de sushi' ou ': “Mãe: cirurgia em novembro/2026”'."""
    resumo = item.resumo.strip().rstrip(".")
    if item.pessoa is None and item.pessoa_id is None and resumo:
        primeira = normalizar(resumo.split()[0])
        if primeira in _INICIOS_ADJETIVO:
            return f" que ela é {_minuscula(resumo)}"
        if primeira in _INICIOS_VERBO:
            return f" que ela {_minuscula(resumo)}"
    return f": {_citacao(resumo)}"


def _fim_com_ponto(s: str) -> str:
    """Termina a frase com ponto; citação que já fecha com pontuação ("...jantar.”") fica igual."""
    fim = s[:-1] if s.endswith("”") else s
    return s if fim.endswith((".", "!", "?", "…")) else s + "."


def _antes_de(item: Item, referencia: date) -> str:
    """' alguns dias antes de 20/10', ' quando dezembro estiver chegando'..."""
    quando = _quando_evento(item, referencia)
    if quando is None or item.precisao == Precisao.ANO:
        return " nos dias antes"
    if item.precisao == Precisao.MES:
        return f" quando {quando} estiver chegando"
    if item.precisao == Precisao.SEMANA:
        return f" alguns dias antes da {quando[2:]}"
    return f" alguns dias antes de {quando}"


def _rotulo_pessoa(pe: PessoaEncontrada | None, cadastro: dict | None) -> str:
    """'Mariana', 'A mãe dela', 'Sua mãe'."""
    nome = (pe.nome if pe else None) or (cadastro or {}).get("nome")
    if nome:
        return nome
    relacao = (pe.relacao if pe else None) or (cadastro or {}).get("relacao") or ""
    base = relacao_base(relacao) or relacao
    if relacao.endswith("(sua)"):
        return f"Sua {base}"
    if relacao.endswith("(seu)"):
        return f"Seu {base}"
    if pe and pe.trecho and len(pe.trecho.split()) <= 4 and base and normalizar(base) in normalizar(pe.trecho):
        return pe.trecho[:1].upper() + pe.trecho[1:]
    genero = genero_relacao(relacao)
    if e_dela(relacao):
        artigo = "A " if genero == "f" else "O " if genero == "m" else ""
        return f"{artigo}{base if artigo else _maiuscula(base)} dela"
    return _maiuscula(relacao)


def _maiuscula(s: str) -> str:
    return s[:1].upper() + s[1:]


def _rotulo_do_item(item: Item, ctx: "_Contexto") -> str:
    """De quem é o item, para usar no meio da frase: 'a mãe dela', 'Mariana'."""
    pid = item.pessoa_id if ctx.pessoa(item.pessoa_id) else None
    if pid is None and item.pessoa:
        pid = casar_pessoa(item.pessoa, ctx.pessoas)
    cadastro = ctx.pessoa(pid)
    if cadastro is not None:
        rotulo = _rotulo_pessoa(None, cadastro)
    elif item.pessoa and normalizar(relacao_base(item.pessoa)) in RELACOES:
        rotulo = _rotulo_pessoa(PessoaEncontrada(trecho="", inicio=0, fim=0, relacao=item.pessoa), None)
    else:
        rotulo = item.pessoa or ""
    return _minuscula(rotulo) if rotulo[:2] in ("A ", "O ") or rotulo[:4] in ("Sua ", "Seu ") else rotulo


# ---------------------------------------------------------------- consultas ao banco

class _Contexto:
    def __init__(self, banco: Banco, referencia: date):
        self.banco = banco
        self.referencia = referencia
        self.pessoas = banco.pessoas()
        self._itens: dict[int, Item | None] = {}
        self._datas: dict[int, date | None] = {}

    def item(self, iid: int) -> Item | None:
        if iid not in self._itens:
            self._itens[iid] = self.banco.item(iid)
        return self._itens[iid]

    def data_registro(self, rid: int | None) -> date | None:
        if rid is None:
            return None
        if rid not in self._datas:
            r = self.banco.registro(rid)
            self._datas[rid] = r["data_registro"] if r else None
        return self._datas[rid]

    def candidatos(self, consulta: str, limite: int = 100) -> list[Item]:
        """Itens ativos que a busca de texto acha para a consulta."""
        saida: list[Item] = []
        vistos: set[int] = set()
        for achado in self.banco.buscar_texto(consulta, limite=limite):
            if achado["tipo"] != "item" or achado["ref_id"] in vistos:
                continue
            vistos.add(achado["ref_id"])
            item = self.item(achado["ref_id"])
            if item is not None and item.status == "ativo":
                saida.append(item)
        return saida

    def pessoa(self, pid: int | None) -> dict | None:
        return next((p for p in self.pessoas if p["id"] == pid), None)

    def dono(self, item: Item) -> set[str]:
        """Chaves de quem é o item ({'ela'} para ela). Dois itens são da mesma pessoa se cruzam."""
        if item.pessoa_id is None and not item.pessoa:
            return {"ela"}
        chaves: set[str] = set()
        pid = item.pessoa_id
        if pid is None and item.pessoa:
            pid = casar_pessoa(item.pessoa, self.pessoas)
        if pid is not None:
            chaves.add(f"id:{pid}")
        if item.pessoa:
            chaves.add("txt:" + _chave_pessoa(item.pessoa))
        return chaves

    def assuntos_do_destino(self, item: Item) -> list[str]:
        return [normalizar(m.group(1)) for m in _RE_DESTINO.finditer(item.trecho or "")]


def _chave_pessoa(s: str) -> str:
    k = normalizar(s)
    k = re.sub(r"^(?:a|o|as|os)\s+", "", k)
    return re.sub(r"\s+dela$", "", k).strip()


def _texto_do_item(item: Item) -> str:
    return plano(" ".join(x for x in (item.resumo, item.alvo) if x))


# ---------------------------------------------------------------- contradições

def _contradicoes(proposta: Proposta, ctx: _Contexto) -> list[tuple[float, Relacao]]:
    saida: list[tuple[float, Relacao]] = []
    for i, novo in enumerate(proposta.itens):
        if novo.tipo in _GOSTOS and novo.alvo and novo.polaridade in (1, -1):
            for antigo in ctx.candidatos(novo.alvo):
                if antigo.tipo not in _GOSTOS or not antigo.alvo or antigo.polaridade != -novo.polaridade:
                    continue
                if not (ctx.dono(novo) & ctx.dono(antigo)):
                    continue
                sim = semelhanca(novo.alvo, antigo.alvo)
                if sim >= SEMELHANCA_MINIMA:
                    saida.append((90 + (sim - SEMELHANCA_MINIMA) / 12 * 10, _contradicao(i, novo, antigo, ctx)))
        elif novo.pessoa is None and novo.pessoa_id is None and novo.trecho:
            for antigo in ctx.candidatos(" ".join(termos(novo.trecho))):
                if (antigo.tipo in _GOSTOS and antigo.alvo and antigo.polaridade in (1, -1)
                        and ctx.dono(antigo) == {"ela"} and _acao_contraria(novo.trecho, antigo)):
                    saida.append((85.0, _contradicao(i, novo, antigo, ctx)))
    return saida


def _acao_contraria(trecho: str, antigo: Item) -> bool:
    """'Ela pediu sushi' contraria 'não gosta de sushi'; 'recusou o sushi' contraria 'gosta'."""
    palavras = normalizar(antigo.alvo or "").split()
    if not palavras or len(palavras) > 4:
        return False
    alvo = r"\s+".join(map(re.escape, palavras))
    verbos = _RE_A_FAVOR if antigo.polaridade == -1 else _RE_CONTRA
    rx = r"(?<![a-z])(?<!nao )(?<!nunca )(?:" + verbos + r")" + _ENCHIMENTO + r"\s+" + alvo + r"(?![a-z])"
    return re.search(rx, normalizar(trecho)) is not None


def _contradicao(i: int, novo: Item, antigo: Item, ctx: _Contexto) -> Relacao:
    quando = ctx.data_registro(antigo.registro_id)
    inicio = f"Em {_data_curta(quando, ctx.referencia)} você registrou" if quando else "Você registrou"
    coisa = "a restrição" if antigo.tipo == TipoItem.RESTRICAO else "a preferência"
    motivo = (f"{_fim_com_ponto(inicio + _registrou(antigo))} Agora: {_fim_com_ponto(_citacao(novo.trecho, 40))}"
              f" Existe uma possível mudança ou contradição. Deseja atualizar {coisa}?")
    return Relacao(tipo="contradicao", motivo=motivo, item_novo=i, item_antigo_id=antigo.id,
                   registro_antigo_id=antigo.registro_id)


# ---------------------------------------------------------------- contexto (assunto)

def _assuntos_de_evento(item: Item) -> list[_Assunto]:
    texto = _texto_do_item(item)
    return [a for a in _ASSUNTOS if a.evento.search(texto)]


def _assuntos_ligados(item: Item) -> list[_Assunto]:
    texto = _texto_do_item(item)
    return [a for a in _ASSUNTOS if a.ligado.search(texto)]


def _recente(antigo: Item, ctx: _Contexto) -> bool:
    quando = antigo.data_evento or ctx.data_registro(antigo.registro_id)
    return quando is not None and quando >= ctx.referencia - timedelta(days=DIAS_ESTADO)


def _contextos(proposta: Proposta, ctx: _Contexto) -> list[tuple[float, Relacao]]:
    saida: list[tuple[float, Relacao]] = []
    for i, novo in enumerate(proposta.itens):
        if novo.tipo == TipoItem.EVENTO:
            for assunto in _assuntos_de_evento(novo):
                destinos = ctx.assuntos_do_destino(novo) if assunto.destino else []
                consulta = " ".join([assunto.consulta, *destinos])
                for antigo in ctx.candidatos(consulta):
                    if antigo.tipo not in (TipoItem.PADRAO, TipoItem.ESTADO, TipoItem.RESTRICAO):
                        continue
                    if antigo.tipo == TipoItem.ESTADO and not _recente(antigo, ctx):
                        continue
                    texto = _texto_do_item(antigo)
                    if not (assunto.ligado.search(texto) or any(d and d in texto for d in destinos)):
                        continue
                    if not (ctx.dono(novo) & ctx.dono(antigo)):
                        continue
                    saida.append((_forca_contexto(antigo), _relacao_contexto(i, novo, antigo, ctx)))
        elif novo.tipo in (TipoItem.PADRAO, TipoItem.ESTADO, TipoItem.RESTRICAO):
            for assunto in _assuntos_ligados(novo):
                for antigo in ctx.candidatos(assunto.consulta):
                    if (antigo.tipo != TipoItem.EVENTO or antigo.data_evento is None
                            or antigo.data_evento < ctx.referencia or not assunto.evento.search(_texto_do_item(antigo))):
                        continue
                    if not (ctx.dono(novo) & ctx.dono(antigo)):
                        continue
                    saida.append((65.0, _relacao_evento_antigo(i, novo, antigo, ctx)))
    return saida


def _forca_contexto(antigo: Item) -> float:
    forca = 70.0
    forca += {TipoItem.PADRAO: 8, TipoItem.RESTRICAO: 6, TipoItem.ESTADO: 4}.get(antigo.tipo, 0)
    if antigo.certeza == Certeza.CONFIRMADO:
        forca += 3
    if antigo.polaridade == -1:
        forca += 2
    return forca


def _sugestao(evento: Item, sentimento: Item, ctx: _Contexto) -> str:
    """Sempre possibilidade. `evento` traz a data; `sentimento` diz se é preocupação."""
    quando = _quando_evento(evento, ctx.referencia)
    if evento.data_evento is not None and evento.data_evento < ctx.referencia:
        return "Talvez seja uma boa perguntar como foi."
    antes = _antes_de(evento, ctx.referencia)
    if ctx.dono(evento) != {"ela"}:
        return f"Talvez seja uma boa perguntar como {_rotulo_do_item(evento, ctx)} está{antes}."
    if sentimento.tipo == TipoItem.RESTRICAO:
        return f"Talvez valha a pena levar isso em conta nos planos{' para ' + quando if quando else ''}."
    if sentimento.polaridade == 1:
        return f"Talvez seja uma boa conversar com ela sobre isso{' antes de ' + quando if quando else ''}."
    return f"Talvez seja interessante perguntar como ela está{antes}."


def _relacao_contexto(i: int, novo: Item, antigo: Item, ctx: _Contexto) -> Relacao:
    quando = ctx.data_registro(antigo.registro_id)
    inicio = f"Em {_data_curta(quando, ctx.referencia)} você registrou" if quando else "Você registrou"
    motivo = f"{_fim_com_ponto(inicio + _registrou(antigo))} Agora: {_fim_com_ponto(_citacao(novo.resumo))}"
    return Relacao(tipo="contexto", motivo=motivo, item_novo=i, item_antigo_id=antigo.id,
                   registro_antigo_id=antigo.registro_id, sugestao=_sugestao(novo, antigo, ctx))


def _relacao_evento_antigo(i: int, novo: Item, evento: Item, ctx: _Contexto) -> Relacao:
    quando = ctx.data_registro(evento.registro_id)
    inicio = f"Em {_data_curta(quando, ctx.referencia)} você registrou" if quando else "Você registrou"
    motivo = f"{inicio}: {_fim_com_ponto(_citacao(evento.resumo))} Agora: {_fim_com_ponto(_citacao(novo.resumo))}"
    return Relacao(tipo="contexto", motivo=motivo, item_novo=i, item_antigo_id=evento.id,
                   registro_antigo_id=evento.registro_id, sugestao=_sugestao(evento, novo, ctx))


# ---------------------------------------------------------------- mesma pessoa

def _mesmas_pessoas(proposta: Proposta, ctx: _Contexto, usados: set[int]) -> list[tuple[float, Relacao]]:
    saida: list[tuple[float, Relacao]] = []
    vistos: set[int] = set()
    inicio_janela = ctx.referencia - timedelta(days=DIAS_MESMA_PESSOA)
    for pe in proposta.pessoas:
        pid = pe.pessoa_id if ctx.pessoa(pe.pessoa_id) else None
        if pid is None:
            pid = casar_pessoa(pe.nome or pe.relacao or "", ctx.pessoas)
        if pid is None or pid in vistos:
            continue
        vistos.add(pid)
        mencoes = [m for m in ctx.banco.mencoes(pid, limite=10)
                   if m["data_registro"] and inicio_janela <= m["data_registro"] <= ctx.referencia
                   and m["registro_id"] not in usados]
        if not mencoes:
            continue
        m = mencoes[0]
        rotulo = _rotulo_pessoa(pe, ctx.pessoa(pid))
        motivo = (f"{rotulo} também apareceu em {_data_curta(m['data_registro'], ctx.referencia)}: "
                  f"{_citacao(m['trecho'])}")
        item_novo = next((n for n, it in enumerate(proposta.itens) if f"id:{pid}" in ctx.dono(it)), None)
        dias = (ctx.referencia - m["data_registro"]).days
        saida.append((50 + (DIAS_MESMA_PESSOA - dias) / DIAS_MESMA_PESSOA * 10,
                      Relacao(tipo="mesma_pessoa", motivo=motivo, item_novo=item_novo,
                              registro_antigo_id=m["registro_id"])))
        usados.add(m["registro_id"])
    return saida


# ---------------------------------------------------------------- entrada

def relacionar(proposta: Proposta, banco: Banco, referencia: date) -> list[Relacao]:
    """Relações entre a proposta (ainda não gravada) e o que já está no banco. No máximo 5."""
    if proposta is None or not (proposta.itens or proposta.pessoas):
        return []
    ctx = _Contexto(banco, referencia)
    candidatas = _contradicoes(proposta, ctx) + _contextos(proposta, ctx)

    def recencia(rel: Relacao) -> int:
        d = ctx.data_registro(rel.registro_antigo_id)
        return d.toordinal() if d else 0

    candidatas.sort(key=lambda c: (-c[0], -recencia(c[1])))
    escolhidas: list[tuple[float, Relacao]] = []
    antigos_usados: set[int] = set()
    chaves_usadas: set[tuple] = set()
    for forca, rel in candidatas:
        # Uma relação por item novo e tipo (a mais forte e, no empate, a mais recente): o mesmo
        # "não gosta de sushi" registrado dez vezes não ocupa as cinco vagas.
        chave = (rel.tipo, rel.item_novo)
        if rel.item_antigo_id in antigos_usados or chave in chaves_usadas:
            continue
        antigos_usados.add(rel.item_antigo_id)
        chaves_usadas.add(chave)
        escolhidas.append((forca, rel))
    registros_usados = {r.registro_antigo_id for _, r in escolhidas if r.registro_antigo_id is not None}
    escolhidas += _mesmas_pessoas(proposta, ctx, registros_usados)
    escolhidas.sort(key=lambda c: -c[0])
    return [rel for _, rel in escolhidas[:LIMITE_RELACOES]]


__all__ = ["LIMITE_RELACOES", "relacionar"]
