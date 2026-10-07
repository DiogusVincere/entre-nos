"""Conhecendo Ela (DESIGN.md, seção 12): pessoas, mapa de preferências e "Como ela está".

Só organiza o que foi registrado. Cada entrada leva `certeza`, `quando` ("há 8 meses") e
`registro_id` para abrir o contexto. A única conta é a força de uma preferência
(`forca_preferencia`); "Como ela está" não diagnostica nada: lista estados, apoios e
acontecimentos com as palavras registradas.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .alertas import Dados, carregar, ocorrencias, somar_meses, tempo_juntos
from .modelos import CATEGORIAS, Certeza, Item, TipoItem
from .regras.pessoas import e_dela, relacao_base
from .texto import normalizar, tempo_atras

if TYPE_CHECKING:
    from .banco import Banco

MESES_SEM_MENCAO = 12           # mais que isso sem nova menção: incerto
DIAS_COMO_ESTA = 14             # janela de "Como ela está"
DIAS_DATAS_PESSOA = 365
MAX_INFOS = 20
MAX_LISTA = 10

_ORDEM_CERTEZA = {Certeza.CONFIRMADO: 3, Certeza.OBSERVADO: 2, Certeza.INFERENCIA: 1, Certeza.INCERTO: 0}
# Categorias que dizem "como" e não "sobre o quê": a preferência vai para a mais específica.
_GENERICAS = frozenset({"Preferências", "Coisas que deixam feliz", "Coisas que incomodam", "Eventos futuros"})
_GRUPOS = ("gosta", "nao_gosta", "restricoes", "comentou_uma_vez", "incertos")
_PROXIMAS = frozenset(normalizar(r) for r in (
    "mãe", "pai", "pais", "irmã", "irmão", "avó", "avô", "filha", "filho", "madrasta", "padrasto",
    "melhor amiga", "melhor amigo"))
_IMPORTANCIAS = ("muito importante", "importante", "apareceu pouco")
_RE_INICIO_ALVO = re.compile(r"^(?:(?:de|do|da|dos|das|o|a|os|as|um|uma|uns|umas)\s+)+")


# ------------------------------------------------------------------ força de uma preferência

def _polaridade(item: Item) -> int:
    if item.tipo == TipoItem.RESTRICAO:
        return -1
    if item.polaridade is not None and int(item.polaridade) < 0:
        return -1
    return 1


def _data_mencao(item: Item, datas_mencao: dict[int, date] | None) -> date | None:
    if datas_mencao and item.id in datas_mencao and datas_mencao[item.id] is not None:
        return datas_mencao[item.id]
    return item.criado_em.date() if item.criado_em else None


def forca_preferencia(itens_mesmo_alvo: list[Item], referencia: date,
                      datas_mencao: dict[int, date] | None = None) -> str:
    """"gosta" | "nao_gosta" | "comentou_uma_vez" | "incerto" para os itens de um mesmo alvo.

    - gosta: ao menos 1 CONFIRMADO positivo, ou OBSERVADOS positivos em 2+ registros;
    - comentou_uma_vez: um só OBSERVADO (ou inferências) positivo; nunca vira "gosta" sozinho;
    - nao_gosta: só negativos (restrição conta como negativo);
    - incerto: positivo e negativo ativos ao mesmo tempo, ou mais de 12 meses sem nova menção.
    Só contam itens ativos. A data da menção é `datas_mencao[item.id]` (o dia do registro,
    que conhecendo_ela passa) ou, na falta dela, `item.criado_em`.
    """
    ativos = [i for i in itens_mesmo_alvo if (i.status or "ativo") == "ativo"]
    if not ativos:
        return "incerto"
    positivos = [i for i in ativos if _polaridade(i) > 0]
    negativos = [i for i in ativos if _polaridade(i) < 0]
    if positivos and negativos:
        return "incerto"
    datas = [x for x in (_data_mencao(i, datas_mencao) for i in ativos) if x is not None]
    if datas and somar_meses(max(datas), MESES_SEM_MENCAO) < referencia:
        return "incerto"
    if negativos:
        return "nao_gosta"
    if any(i.certeza == Certeza.CONFIRMADO for i in positivos):
        return "gosta"
    ocasioes = {i.registro_id if i.registro_id is not None else ("item", n)
                for n, i in enumerate(positivos) if i.certeza == Certeza.OBSERVADO}
    return "gosta" if len(ocasioes) >= 2 else "comentou_uma_vez"


# ------------------------------------------------------------------ entradas

def _entrada(d: Dados, item: Item) -> dict:
    """Um item do jeito que a tela mostra: resumo, trecho literal, selo, quando e registro."""
    dr = d.data_registro(item)
    return {
        "item_id": item.id,
        "registro_id": item.registro_id,
        "tipo": item.tipo.value,
        "resumo": item.resumo,
        "trecho": d.trecho(item),           # frase do ciclo de um registro discreto não é citada (20.10)
        "certeza": item.certeza.value,
        "categorias": list(item.categorias or []),
        "importancia": item.importancia,
        "data_registro": dr,
        "quando": tempo_atras((d.referencia - dr).days) if dr else None,
        "data_evento": item.data_evento,
        "precisao": item.precisao.value if item.precisao else None,
        "alvo": item.alvo,
        "polaridade": item.polaridade,
        "pessoa": item.pessoa,
        "pessoa_id": item.pessoa_id,
    }


def _mais_recentes(d: Dados, itens: list[Item]) -> list[Item]:
    return sorted(itens, key=lambda i: (d.quando(i) or date.min, i.id or 0), reverse=True)


# ------------------------------------------------------------------ ela

def _ela(d: Dados) -> dict:
    c = d.config
    return {"nome": c.get("nome_dela"), "aniversario": c.get("aniversario_dela"),
            "inicio_namoro": c.get("inicio_namoro"),
            "tempo_juntos": tempo_juntos(c.get("inicio_namoro"), d.referencia)}


# ------------------------------------------------------------------ pessoas

def _importancia(relacao: str | None, mencoes: int, itens: list[Item], aniversario: date | None) -> str:
    """Pelo que foi registrado: relação próxima dela, quantas vezes aparece e itens de peso."""
    proxima = bool(relacao) and e_dela(relacao) and normalizar(relacao_base(relacao)) in _PROXIMAS
    alta = any(i.importancia >= 3 for i in itens)
    if mencoes >= 5 or (proxima and mencoes >= 2) or (alta and mencoes >= 3):
        return "muito importante"
    if mencoes >= 2 or proxima or alta or aniversario is not None:
        return "importante"
    return "apareceu pouco"


def _pessoas(banco: Banco, d: Dados) -> list[dict]:
    ref = d.referencia
    registros_de: dict[int, set[int]] = defaultdict(set)
    for rid, r in d.registros.items():
        for p in r.get("pessoas") or []:
            registros_de[p["id"]].add(rid)
    for i in d.itens:
        if i.pessoa_id is not None and i.registro_id is not None:
            registros_de[i.pessoa_id].add(i.registro_id)
    datas_por_pessoa: dict[int, list] = defaultdict(list)
    for oc in ocorrencias(d, DIAS_DATAS_PESSOA):
        if oc.pessoa_id is not None:
            datas_por_pessoa[oc.pessoa_id].append(oc)

    saida = []
    for pid, p in d.pessoas.items():
        # A anotação solta de um registro discreto é a própria frase (o ciclo, seção 20.8): não vira
        # informação da pessoa aqui. Os itens com assunto (evento, gosto...) continuam.
        itens = _mais_recentes(d, [i for i in d.ativos if i.pessoa_id == pid
                                   and not (i.tipo == TipoItem.NOTA and i.registro_id in d.discretos)])
        datas = datas_por_pessoa.get(pid, [])
        nas_datas = {oc.item.id for oc in datas if oc.item is not None}
        mencoes = len(registros_de.get(pid, ()))
        # Registro discreto (o ciclo, seção 20.8) não é citado aqui: "a irmã dela menstruou" fica só
        # no cartão 🌙 (a contagem de menções continua contando, sem o trecho).
        ultimas = [{"registro_id": m["registro_id"], "data": m["data_registro"],
                    "data_registro": m["data_registro"], "trecho": m["trecho"]}
                   for m in banco.mencoes(pid, 5, excluir=d.discretos)]
        saida.append({
            "id": pid,
            "nome": p["nome"],
            "relacao": p["relacao"],
            "apelidos": list(p.get("apelidos") or []),
            "aniversario": p.get("aniversario"),
            "observacoes": p.get("observacoes") or "",
            "infos": [_entrada(d, i) for i in itens if i.id not in nas_datas][:MAX_INFOS],
            "datas": [_entrada_data(d, oc) for oc in datas],
            "ultimas_mencoes": ultimas,
            "mencoes": mencoes,
            "importancia": _importancia(p["relacao"], mencoes, itens, p.get("aniversario")),
            "ultima_mencao": ultimas[0]["data"] if ultimas else None,
        })
    saida.sort(key=lambda x: (_IMPORTANCIAS.index(x["importancia"]), -x["mencoes"],
                              -(x["ultima_mencao"] or date.min).toordinal(), x["id"]))
    return saida


def _entrada_data(d: Dados, oc) -> dict:
    e = oc.para_dict(d.referencia)
    if oc.item is not None:
        e.update(certeza=oc.item.certeza.value, trecho=d.trecho(oc.item))

    return e


# ------------------------------------------------------------------ preferências

def _chave_alvo(item: Item) -> str:
    """'as tulipas' e 'tulipa' caem no mesmo alvo; sem alvo, vale o resumo."""
    n = _RE_INICIO_ALVO.sub("", normalizar(item.alvo or item.resumo or ""))
    return " ".join(p[:-1] if len(p) > 3 and p.endswith("s") else p for p in n.split())


def _categoria(itens: list[Item]) -> str:
    """A categoria mais específica do item mais recente do grupo."""
    for i in itens:
        for c in i.categorias or []:
            if c in CATEGORIAS and c not in _GENERICAS:
                return c
    for i in itens:
        for c in i.categorias or []:
            if c in CATEGORIAS:
                return c
    return "Preferências"


def _entrada_grupo(d: Dados, itens: list[Item], forca: str, datas: dict[int, date]) -> dict:
    """Uma linha por alvo: o registro mais certo (e mais recente) representa o grupo."""
    ref = d.referencia
    representante = max(itens, key=lambda i: (_ORDEM_CERTEZA.get(i.certeza, 0), datas.get(i.id) or date.min,
                                              i.id or 0))
    ultima = max((datas.get(i.id) for i in itens if datas.get(i.id)), default=None)
    e = _entrada(d, representante)
    e.update({
        "forca": forca,
        "quando": tempo_atras((ref - ultima).days) if ultima else e["quando"],
        "ultima_mencao": ultima,
        "item_ids": [i.id for i in itens],
        "registro_ids": sorted({i.registro_id for i in itens if i.registro_id is not None}),
        "mencoes": len({i.registro_id for i in itens if i.registro_id is not None}),
    })
    return e


def _entrada_incerta(d: Dados, itens: list[Item], datas: dict[int, date]) -> dict:
    """Contradição ou menção antiga: o mais recente representa, com selo incerto e o motivo."""
    recente = max(itens, key=lambda i: (datas.get(i.id) or date.min, i.id or 0))
    e = _entrada_grupo(d, itens, "incerto", datas)
    e.update({k: v for k, v in _entrada(d, recente).items() if k in ("item_id", "registro_id", "resumo", "trecho")})
    e["certeza"] = Certeza.INCERTO.value
    polaridades = {_polaridade(i) for i in itens}
    if len(polaridades) > 1:
        e["motivo"] = "Há registros que dizem que gosta e registros que dizem que não gosta. Pode ter mudado."
        e["resumo"] = f"{recente.resumo.rstrip('.')} (também há registro do contrário)"
        e["registros"] = [_entrada(d, i) for i in itens]
    else:
        e["motivo"] = "Faz mais de 12 meses que isso não aparece nos registros. Pode ter mudado."
        e["resumo"] = f"{recente.resumo.rstrip('.')} (sem nova menção há mais de um ano)"
    return e


def _preferencias(d: Dados) -> dict:
    datas = {i.id: d.data_registro(i) for i in d.itens}
    grupos: dict[str, list[Item]] = {}
    for i in d.ativos:
        if i.tipo in (TipoItem.PREFERENCIA, TipoItem.RESTRICAO) and d.dono(i) is None:
            grupos.setdefault(_chave_alvo(i), []).append(i)

    mapa: dict[str, dict[str, list]] = {}
    for grupo in grupos.values():
        forca = forca_preferencia(grupo, d.referencia, datas)
        restricoes = [i for i in grupo if i.tipo == TipoItem.RESTRICAO]
        preferencias = [i for i in grupo if i.tipo == TipoItem.PREFERENCIA]
        destino = mapa.setdefault(_categoria(grupo), {g: [] for g in _GRUPOS})
        if restricoes:
            destino["restricoes"].append(_entrada_grupo(d, restricoes, "nao_gosta", datas))
        if not preferencias:
            continue
        if forca == "incerto":
            destino["incertos"].append(_entrada_incerta(d, grupo, datas))
        elif forca == "nao_gosta":
            if not restricoes:          # a restrição já diz isso, e com mais peso
                destino["nao_gosta"].append(_entrada_grupo(d, preferencias, forca, datas))
        else:
            destino["gosta" if forca == "gosta" else "comentou_uma_vez"].append(
                _entrada_grupo(d, preferencias, forca, datas))

    for grupos_cat in mapa.values():
        for lista in grupos_cat.values():
            lista.sort(key=lambda e: (-_ORDEM_CERTEZA.get(Certeza(e["certeza"]), 0), -e["mencoes"],
                                      -(e["ultima_mencao"] or date.min).toordinal()))
    ordem = {c: n for n, c in enumerate(CATEGORIAS)}
    return {c: mapa[c] for c in sorted(mapa, key=lambda c: ordem.get(c, len(ordem)))}


# ------------------------------------------------------------------ como ela está

def _como_ela_esta(d: Dados) -> dict:
    """Só o que foi registrado: estados dela nos últimos 14 dias, apoios e acontecimentos recentes."""
    ref = d.referencia
    desde = ref - timedelta(days=DIAS_COMO_ESTA)

    def recente(i: Item) -> bool:
        q = d.quando(i)
        return q is not None and desde <= q <= ref

    dela = [i for i in d.ativos if d.dono(i) is None]
    estados = [i for i in dela if i.tipo == TipoItem.ESTADO and recente(i)]
    apoios = sorted((i for i in dela if i.tipo == TipoItem.APOIO),
                    key=lambda i: (i.certeza == Certeza.CONFIRMADO, d.data_registro(i) or date.min, i.id or 0),
                    reverse=True)
    eventos = [i for i in d.ativos if i.tipo == TipoItem.EVENTO and not i.recorrente
               and i.data_evento is not None and desde <= i.data_evento <= ref]
    return {
        "situacao_atual": [_entrada(d, i) for i in _mais_recentes(d, estados)][:MAX_LISTA],
        "o_que_ajuda": [_entrada(d, i) for i in apoios if (i.polaridade or 1) > 0][:MAX_LISTA],
        "o_que_nao_gosta": [_entrada(d, i) for i in apoios if (i.polaridade or 1) < 0][:MAX_LISTA],
        "ultimos_acontecimentos": [_entrada(d, i) for i in _mais_recentes(d, eventos)][:MAX_LISTA],
    }


# ------------------------------------------------------------------ página

def conhecendo_ela(banco: Banco, referencia: date) -> dict:
    """{"ela": {nome, aniversario, inicio_namoro, tempo_juntos},
        "pessoas": [{id, nome, relacao, infos, ultimas_mencoes, datas, observacoes, mencoes,
                     importancia, ...}],
        "preferencias": {categoria: {gosta, nao_gosta, restricoes, comentou_uma_vez, incertos}},
        "como_ela_esta": {situacao_atual, o_que_ajuda, o_que_nao_gosta, ultimos_acontecimentos}}
    Datas saem como `date`: use modelos.para_json antes do json.dumps.
    """
    d = carregar(banco, referencia)
    return {
        "ela": _ela(d),
        "pessoas": _pessoas(banco, d),
        "preferencias": _preferencias(d),
        "como_ela_esta": _como_ela_esta(d),
    }


__all__ = ["conhecendo_ela", "forca_preferencia"]
