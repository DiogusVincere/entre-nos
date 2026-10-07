"""Anotação rápida (DESIGN.md, seção 20.4): guardar agora, organizar depois.

Um campo e um botão (no celular, também pelo "+" de Registrar; no computador, Alt+N abre a tela). A
anotação fica no diário, criptografada como o resto, até você organizar: aí ela abre na revisão
normal **com a data de referência guardada** (a do momento da anotação), para "amanhã" e "sexta"
continuarem certos mesmo que você organize dias depois. Organizada ou apagada, ela some (do banco e
das cópias automáticas).

Tabela `rascunhos` (contrato da seção 20.4 + colunas a mais):
- id, texto, criado_em (data e hora em que foi anotada), referencia (AAAA-MM-DD: "quando foi");
- privado (1 = organizar já com a caixinha 🔒 "Não enviar para a IA" marcada), contexto e humor (os
  "mais detalhes" do Registrar, quando a anotação veio de lá).
Não tem `registro_id`: a anotação ainda não é um registro (e por isso nunca vai para a IA).

Como a anotação sai da lista ao ser organizada: a tela abre Registrar já preenchido e, quando o
registro é salvo, chama `POST /api/rapida/rascunhos/{id}/organizado {registro_id}`. O servidor confere
que aquele registro é mesmo esta anotação (o texto está nele, ou quase igual, e ele é de depois da
anotação) antes de apagá-la. Se a tela não conseguiu avisar (você saiu no meio), a lista percebe sozinha
("parece que já virou o registro de 05/10") e oferece tirar da lista; o Hoje não conta essas.

Hoje: "📝 3 anotações para organizar", mais urgente quando uma delas fala de hoje ou de amanhã (pela
data de quando foi anotada).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from ..banco import NaoEncontrado
from ..regras.datas import extrair_datas
from ..texto import data_br, normalizar, semelhanca, tempo_atras
from . import cartao, rota
from . import texto_reservado as _reservado

NOME = "rapida"
ROTULO = "Anotação rápida"

ESQUEMA = [
    """
    CREATE TABLE rascunhos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        referencia TEXT NOT NULL,
        privado INTEGER NOT NULL DEFAULT 0,
        contexto TEXT,
        humor TEXT
    )
    """,
]
ESQUEMA_VERSAO = 1

MAX_TEXTO = 100_000
MAX_RASCUNHOS = 1000
MAX_IMPORTAR = 5000
LIMIAR_ORGANIZADA = 80        # semelhança mínima entre a anotação e o registro em que ela virou
LIMIAR_PARECE = 92            # para a lista dizer sozinha "parece que já foi organizada"

TEXTO_VAZIO = "Escreva alguma coisa antes de guardar."
TEXTO_GRANDE = "A anotação ficou grande demais. Que tal organizar em mais de um registro?"
DATA_INVALIDA = "A data precisa ser um dia válido (AAAA-MM-DD), de hoje para trás."
CAMPOS_INVALIDOS = "Só dá para mudar o texto, a data e a caixinha 🔒 da anotação."
DEMAIS = (f"Já são {MAX_RASCUNHOS} anotações esperando. Organize ou apague algumas antes de guardar mais.")
SEM_RASCUNHO = "Não encontrei essa anotação. Talvez ela já tenha sido organizada ou apagada."
REGISTRO_INVALIDO = "Esse registro não existe (talvez tenha sido apagado)."
NAO_E_ESTA = ("A anotação continua na lista: o registro salvo ficou bem diferente dela. Se ela já foi "
              "organizada, toque em “Tirar da lista”.")
ANTES_DA_ANOTACAO = "A anotação continua na lista: esse registro é de antes dela."


# ================================================================ ajudantes

INICIO_RESERVADO = "🔒 Uma anotação reservada (abra para ver)."


def _agora() -> str:

    return datetime.now().isoformat(timespec="seconds")


def _data_ou_none(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def _texto(v: Any) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(TEXTO_VAZIO)
    if len(v) > MAX_TEXTO:
        raise ValueError(TEXTO_GRANDE)
    return v.strip()


def _referencia(v: Any, hoje: date) -> date:
    """A data da anotação: a que a tela mandou (o "Quando foi?"), ou hoje. Nada de futuro (um dia de
    folga, por causa do fuso do aparelho)."""
    if v is None or v == "":
        return hoje
    d = _data_ou_none(v) if isinstance(v, str) else None
    if d is None or d > hoje + timedelta(days=1) or d.year < 1900:
        raise ValueError(DATA_INVALIDA)
    return d


def _opcional(v: Any, maximo: int, campo: str) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if not isinstance(v, str) or len(v) > maximo:
        raise ValueError(f"“{campo}” precisa ser um texto de até {maximo} caracteres.")
    return v.strip()


def _booleano(v: Any) -> bool:
    if v is None:
        return False
    if not isinstance(v, bool):
        raise ValueError("“privado” precisa ser verdadeiro ou falso.")
    return v


def _dia(d: date, hoje: date) -> str:
    return d.strftime("%d/%m") if d.year == hoje.year else data_br(d)


def _inicio(texto: str, n: int = 90) -> str:
    t = re.sub(r"\s+", " ", texto).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _contido(curto: str, longo: str) -> bool:
    a, b = normalizar(curto), normalizar(longo)
    return bool(a) and a in b


# ================================================================ leitura

def datas_do_texto(texto: str, referencia: date, hoje: date) -> list[dict]:
    """As datas que a anotação cita, calculadas pela data da anotação (não pela de hoje): é por isso
    que ela guarda a referência. `dias` = quanto falta, contando de hoje."""
    saida = []
    for d in extrair_datas(texto, referencia):
        if d.data is None:
            continue
        saida.append({"expressao": d.expressao, "data": d.data, "precisao": d.precisao.value,
                      "passado": d.data < hoje, "dias": (d.data - hoje).days})
    return saida[:10]


def _urgente(datas: list[dict]) -> str | None:
    """'hoje' ou 'amanha' quando a anotação fala de um dia que é hoje ou amanhã."""
    dias = {d["dias"] for d in datas if d["precisao"] == "dia" and d["dias"] in (0, 1)}
    if 0 in dias:
        return "hoje"
    if 1 in dias:
        return "amanha"
    return None


def parece_organizada(banco: Any, r: dict) -> dict | None:
    """Um registro de depois da anotação, com a mesma data, que tem o texto dela (ou quase): ela já
    foi organizada e só faltou tirar da lista."""
    linhas = banco.consultar("SELECT id, data_registro, texto, criado_em FROM registros WHERE data_registro = ?"
                             " ORDER BY id", (r["referencia"],))
    for reg in linhas:
        if (reg["criado_em"] or "") < (r["criado_em"] or ""):
            continue
        if _contido(r["texto"], reg["texto"]) or semelhanca(r["texto"], reg["texto"]) >= LIMIAR_PARECE:
            return {"registro_id": reg["id"], "data_registro": _data_ou_none(reg["data_registro"])}
    return None


def _vista(banco: Any, r: dict, hoje: date) -> dict:
    referencia = _data_ou_none(r["referencia"]) or hoje
    datas = datas_do_texto(r["texto"], referencia, hoje)
    return {
        "id": r["id"], "texto": r["texto"], "inicio": _inicio(r["texto"]), "referencia": referencia,
        "criado_em": r["criado_em"], "privado": bool(r["privado"]), "contexto": r.get("contexto"),
        "humor": r.get("humor"), "dias": (hoje - referencia).days, "quando": tempo_atras((hoje - referencia).days),
        "datas": datas, "urgente": _urgente(datas), "organizada": parece_organizada(banco, r),
    }


def _todos(banco: Any) -> list[dict]:
    return banco.consultar("SELECT * FROM rascunhos ORDER BY referencia, id")


def lista(banco: Any, hoje: date) -> dict:
    """{"rascunhos": [mais antigos primeiro], "total", "para_organizar" (sem os que já parecem organizados)}."""
    vistas = [_vista(banco, r, hoje) for r in _todos(banco)]
    return {"rascunhos": vistas, "total": len(vistas),
            "para_organizar": sum(1 for v in vistas if not v["organizada"])}


def _um(banco: Any, rid: int) -> dict:
    linhas = banco.consultar("SELECT * FROM rascunhos WHERE id = ?", (rid,))
    if not linhas:
        raise NaoEncontrado(SEM_RASCUNHO)
    return linhas[0]


# ================================================================ Hoje

def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """Um cartão só: "📝 N anotações para organizar" (as que já parecem organizadas não contam)."""
    pendentes = [v for v in (_vista(banco, r, referencia) for r in _todos(banco)) if not v["organizada"]]
    if not pendentes:
        return []
    n = len(pendentes)
    mais_antiga = min(pendentes, key=lambda v: (v["referencia"], v["id"]))
    urgentes = [v for v in pendentes if v["urgente"]]
    texto = f"📝 {n} {'anotação' if n == 1 else 'anotações'} para organizar"
    porque = [f"A mais antiga é de {_dia(mais_antiga['referencia'], referencia)} ({mais_antiga['quando']})."
              if mais_antiga["dias"] > 0 else "Você anotou hoje."]
    relevancia = 40 + min(20, mais_antiga["dias"] * 3) + min(6, n)
    if urgentes:
        quando = "hoje" if any(v["urgente"] == "hoje" for v in urgentes) else "amanhã"
        texto += f" ({'ela fala' if n == 1 else 'uma fala'} de {quando})"
        u = next(v for v in urgentes if (v["urgente"] == "hoje") == (quando == "hoje"))
        expressao = next((d["expressao"] for d in u["datas"] if d["dias"] in (0, 1) and d["precisao"] == "dia"), None)
        if expressao:
            porque.append(f"Na anotação de {_dia(u['referencia'], referencia)}, “{expressao}” é {quando}.")
        relevancia = 82 if quando == "hoje" else 76
    porque.append("Ao organizar, cada uma vira um registro com a data de quando você anotou, "
                  "para “amanhã” e “sexta” continuarem certos.")
    escolhidas = sorted(pendentes, key=lambda v: (v["urgente"] is None, v["referencia"], v["id"]))[:3]
    maior = max(v["id"] for v in pendentes)
    # O Hoje nunca mostra palavras do ciclo (20.8): a anotação que as tem aparece reservada (o texto
    # continua inteiro na tela da anotação rápida, e ao organizar).
    rascunhos = []
    for v in escolhidas:
        discreto = _reservado(v["texto"])
        rascunhos.append({"id": v["id"], "inicio": INICIO_RESERVADO if discreto else v["inicio"],
                          "referencia": v["referencia"], "urgente": v["urgente"], "discreto": discreto})
    return [cartao(f"n{maior}:pendentes", "lembrete", texto, porque=" ".join(porque), relevancia=relevancia,
                   tipo="rapida", dados={"total": n, "rascunhos": rascunhos})]


# ================================================================ rotas

def _rota_listar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return lista(banco, nucleo.hoje())


def _rota_criar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if set(c) - {"texto", "referencia", "privado", "contexto", "humor"}:
        raise ValueError("Uma anotação tem só o texto, a data, a caixinha 🔒 e os detalhes do Registrar.")
    hoje = nucleo.hoje()
    linha = {"texto": _texto(c.get("texto")), "referencia": _referencia(c.get("referencia"), hoje),
             "privado": _booleano(c.get("privado")), "contexto": _opcional(c.get("contexto"), 200, "contexto"),
             "humor": _opcional(c.get("humor"), 80, "humor"), "criado_em": _agora()}
    if banco.consultar("SELECT count(*) AS n FROM rascunhos")[0]["n"] >= MAX_RASCUNHOS:
        raise ValueError(DEMAIS)
    rid = banco.inserir("rascunhos", linha)
    return {"id": rid, "rascunho": _vista(banco, _um(banco, rid), hoje)}


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if not c or set(c) - {"texto", "referencia", "privado"}:
        raise ValueError(CAMPOS_INVALIDOS)
    r = _um(banco, pedido.id)
    hoje = nucleo.hoje()
    mudar: dict[str, Any] = {}
    if "texto" in c:
        mudar["texto"] = _texto(c.get("texto"))
    if "referencia" in c:
        mudar["referencia"] = _referencia(c.get("referencia"), hoje)
    if "privado" in c:
        mudar["privado"] = _booleano(c.get("privado"))
    banco.atualizar("rascunhos", r["id"], mudar)
    return {"rascunho": _vista(banco, _um(banco, r["id"]), hoje)}


def _apagar(nucleo: Any, banco: Any, rid: int) -> None:
    """Apaga a anotação aqui e nas cópias automáticas (apagar é apagar), com as chaves de aviso."""
    with banco.transacao():
        banco.apagar("rascunhos", rid)
        banco.esquecer_alertas(f"{NOME}:n{rid}:")

    def na_copia(copia: Any) -> None:
        copia.apagar("rascunhos", rid)              # NaoEncontrado: a cópia não tinha, fica como está
        copia.esquecer_alertas(f"{NOME}:n{rid}:")

    nucleo.limpar_copias(na_copia)


def _rota_apagar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    _um(banco, pedido.id)
    _apagar(nucleo, banco, pedido.id)
    return {}


def _rota_organizado(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """A anotação virou o registro `registro_id`: confere e tira da lista. Confere de verdade (o
    texto da anotação está no registro, ou quase igual, e o registro é de depois dela): um registro
    qualquer não apaga anotação nenhuma."""
    c = pedido.corpo
    if set(c) - {"registro_id"}:
        raise ValueError("Mande só o registro em que a anotação virou.")
    r = _um(banco, pedido.id)
    rid = c.get("registro_id")
    if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
        raise ValueError(REGISTRO_INVALIDO)
    linhas = banco.consultar("SELECT id, texto, criado_em FROM registros WHERE id = ?", (rid,))
    if not linhas:
        raise ValueError(REGISTRO_INVALIDO)
    reg = linhas[0]
    if (reg["criado_em"] or "") < (r["criado_em"] or ""):
        return {"organizado": False, "motivo": ANTES_DA_ANOTACAO}
    if not (_contido(r["texto"], reg["texto"]) or semelhanca(r["texto"], reg["texto"]) >= LIMIAR_ORGANIZADA):
        return {"organizado": False, "motivo": NAO_E_ESTA}
    _apagar(nucleo, banco, r["id"])
    return {"organizado": True, "registro_id": rid}


def rotas() -> list:
    return [
        rota("GET", "rascunhos", _rota_listar),
        rota("POST", "rascunhos", _rota_criar),
        rota("PATCH", "rascunhos/{id}", _rota_editar),
        rota("DELETE", "rascunhos/{id}", _rota_apagar),
        rota("POST", "rascunhos/{id}/organizado", _rota_organizado),
    ]


# ================================================================ exportar e importar

def exportar(banco: Any) -> dict:
    return {"rascunhos": [{k: r.get(k) for k in ("id", "texto", "criado_em", "referencia", "privado", "contexto",
                                                  "humor")} for r in banco.consultar("SELECT * FROM rascunhos ORDER BY id")]}


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta as anotações de um arquivo exportado: a mesma anotação (mesmo texto e mesma data) não
    entra duas vezes."""
    if not isinstance(dados, dict) or not isinstance(dados.get("rascunhos", []), list):
        raise ValueError("formato")
    existentes = {(normalizar(r["texto"]), r["referencia"]) for r in banco.consultar("SELECT texto, referencia FROM rascunhos")}
    total = len(existentes)
    novos = fora = 0
    for bruto in dados.get("rascunhos", [])[:MAX_IMPORTAR]:
        if not isinstance(bruto, dict) or not isinstance(bruto.get("texto"), str) or not bruto["texto"].strip() \
                or len(bruto["texto"]) > MAX_TEXTO:
            fora += 1
            continue
        referencia = _data_ou_none(bruto.get("referencia")) if isinstance(bruto.get("referencia"), str) else None
        if referencia is None:
            fora += 1
            continue
        chave = (normalizar(bruto["texto"]), referencia.isoformat())
        if chave in existentes:
            continue
        if total >= MAX_RASCUNHOS:
            fora += 1
            continue
        criado = bruto.get("criado_em")
        try:
            criado = datetime.fromisoformat(criado).isoformat(timespec="seconds") if isinstance(criado, str) else _agora()
        except ValueError:
            criado = _agora()

        def curto(v: Any, maximo: int) -> str | None:
            return v.strip() if isinstance(v, str) and v.strip() and len(v) <= maximo else None

        banco.inserir("rascunhos", {"texto": bruto["texto"].strip(), "referencia": referencia, "criado_em": criado,
                                    "privado": bruto.get("privado") in (True, 1),
                                    "contexto": curto(bruto.get("contexto"), 200), "humor": curto(bruto.get("humor"), 80)})
        existentes.add(chave)
        total += 1
        novos += 1
    texto = ("Nenhuma anotação nova." if not novos else
             "1 anotação para organizar." if novos == 1 else f"{novos} anotações para organizar.")
    if fora:
        texto += f" {fora} ficaram de fora (dados que não dá para ler)."
    return {"novos": novos, "texto": texto}
