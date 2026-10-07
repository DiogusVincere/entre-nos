"""Área 🪞 Eu (DESIGN.md, seção 20.9): como estou sendo com ela.

Sem nota, sem placar, sem ranking; comemora mais do que cobra; tudo cita a origem (o registro, a
promessa, a pergunta) e **nada daqui vai para a IA**: registro com uma atitude, um aprendizado, um
desentendimento ou uma admiração guardados aqui fica 🔒 (`marca_privado` e `fora_da_ia`).

O que faz:
1. **Check-in semanal** (2 ou 3 perguntas escolhidas pela semana real: semana difícil dela,
   promessa vencendo, pergunta profunda feita, um dia importante dela, um desentendimento; sem
   gancho, "uma coisa boa que você fez por ela" e "algo que faria diferente") + escala de presença
   em 5 emojis (só sua) e **reflexão mensal**. Tabela `checkins`.
2. **Espelho** por semana e por mês: promessas feitas × cumpridas, acompanhamento das pendências
   dela, equilíbrio (preocupações × alegrias registradas), antecedência nas datas, perguntas
   profundas, atitudes que combinam com o que ela disse que ajuda; tendência em texto.
3. **Minhas atitudes** (regras em primeira pessoa: "levei café pra ela", "fiquei com ela no
   hospital", "escutei sem dar conselho"), cruzadas com o que ela disse que ajuda (itens APOIO) e,
   com cuidado, o descompasso como pergunta. Tabela `atitudes`.
4. **Aprendizados** com gatilhos (estados, termos, categorias) que voltam na revisão (extra
   `aprendizado_lembrete`) e no Hoje quando um registro novo casa com eles. Tabela `aprendizados`.
5. **O que ela diz**: as respostas da categoria "Nós dois" do recurso perguntas, lidas pela tabela
   `perguntas_feitas` (colunas do contrato; tolera a tabela não existir), lado a lado com o seu
   check-in no mês.
6. **Intenção do mês** sugerida pelos dados (com o porquê), escolhida ou escrita por você; lembrete
   gentil no meio do mês e revisão no fim. Tabela `intencoes`.
7. **Desentendimentos e reparos**, só a sua parte (não existe campo "o que ela errou"), com
   acompanhamento depois de 2-3 dias, tema repetido e sugestão cuidadosa de ajuda. Tabela `reparos`.
8. **Admirações** (tabela `admiracoes`), que alimentam cartas e a reflexão.
9. **Ajuda para cartas**: blocos tirados dos registros (marcos, frases dela 🟢, do que ela gosta,
   momentos felizes, admirações), esqueleto só com o que foi escolhido. Tabela `cartas`.
10. **Cuidar de você**: presença caindo (ou baixa) por 3 semanas seguidas → "Como você está?".

O ciclo (seção 20.8) nunca explica nada aqui: registros que outros recursos deixam fora da IA (menos
as respostas "Nós dois", que já são da área Eu) não entram como "semana difícil", gatilho nem bloco
de carta. Roda no navegador (Pyodide): sem thread, sem sleep, sem rede. As datas são do Python.
"""
from __future__ import annotations

import calendar
import json
import re
import weakref
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..banco import NaoEncontrado
from ..modelos import Certeza
from ..regras.categorias import classificar
from ..regras.certeza import classificar_certeza
from ..regras.pessoas import plano
from ..texto import STOPWORDS, frases, normalizar, palavras, semelhanca
from . import cartao, com_gancho, falhas, recurso, registrar_erro, rota

NOME = "eu"
ROTULO = "Área Eu"
TIPOS_EXTRAS = ("atitude", "aprendizado", "reparo", "admiracao", "aprendizado_lembrete")
# Sem confirmar na revisão, só o desentendimento deixa o TEXTO fora da IA (seção 20.10): uma atitude ou
# um aprendizado não confirmados podem ser só um acontecimento; uma briga é sempre da área Eu.
TIPOS_FORA_PELO_TEXTO = ("reparo",)
# O que é da área Eu deixa o registro 🔒. O lembrete de um aprendizado só aponta para algo seu que já
# existe: o texto do registro continua sendo sobre ela, e nada da ligação fica no registro.
_TIPOS_PRIVADOS = frozenset({"atitude", "aprendizado", "reparo", "admiracao"})

ESQUEMA = [
    """
    CREATE TABLE checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK (tipo IN ('semanal', 'mensal')),
        periodo TEXT NOT NULL,
        respostas TEXT NOT NULL DEFAULT '{}',
        presenca INTEGER CHECK (presenca IS NULL OR presenca BETWEEN 1 AND 5),
        criado_em TEXT NOT NULL,
        atualizado_em TEXT
    );
    CREATE UNIQUE INDEX checkins_periodo ON checkins(tipo, periodo);
    CREATE TABLE atitudes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        data TEXT NOT NULL,
        registro_id INTEGER,
        trecho TEXT,
        jeito TEXT NOT NULL DEFAULT 'outro',
        criado_em TEXT NOT NULL
    );
    CREATE INDEX atitudes_registro ON atitudes(registro_id);
    CREATE INDEX atitudes_data ON atitudes(data);
    CREATE TABLE aprendizados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        gatilhos TEXT NOT NULL DEFAULT '{}',
        criado_em TEXT NOT NULL,
        mostrado_em TEXT,
        data TEXT NOT NULL,
        registro_id INTEGER,
        trecho TEXT
    );
    CREATE INDEX aprendizados_registro ON aprendizados(registro_id);
    CREATE TABLE reparos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT NOT NULL,
        aconteceu TEXT NOT NULL,
        eu_senti TEXT,
        ela_pode_ter_sentido TEXT,
        ela_certeza TEXT NOT NULL DEFAULT 'inferencia'
            CHECK (ela_certeza IN ('confirmado', 'observado', 'inferencia')),
        faria_diferente TEXT,
        conversamos INTEGER CHECK (conversamos IS NULL OR conversamos IN (0, 1)),
        reparo TEXT,
        tema TEXT,
        intensidade INTEGER NOT NULL DEFAULT 1 CHECK (intensidade BETWEEN 1 AND 3),
        criado_em TEXT NOT NULL,
        acompanhado_em TEXT,
        registro_id INTEGER,
        trecho TEXT
    );
    CREATE INDEX reparos_registro ON reparos(registro_id);
    CREATE INDEX reparos_data ON reparos(data);
    CREATE TABLE admiracoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        data TEXT NOT NULL,
        registro_id INTEGER,
        trecho TEXT,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX admiracoes_registro ON admiracoes(registro_id);
    CREATE TABLE cartas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ocasiao TEXT NOT NULL,
        titulo TEXT,
        texto TEXT NOT NULL,
        blocos TEXT NOT NULL DEFAULT '[]',
        criado_em TEXT NOT NULL,
        atualizado_em TEXT
    );
    CREATE TABLE intencoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mes TEXT NOT NULL,
        texto TEXT NOT NULL,
        origem TEXT NOT NULL DEFAULT 'escrita',
        sugestao TEXT,
        criado_em TEXT NOT NULL,
        revisao TEXT,
        revisada_em TEXT
    );
    CREATE UNIQUE INDEX intencoes_mes ON intencoes(mes)
    """,
]
ESQUEMA_VERSAO = len(ESQUEMA)

TABELAS = ("checkins", "atitudes", "aprendizados", "reparos", "admiracoes", "cartas", "intencoes")
# Tabelas cujas linhas podem vir de um registro (e por isso o deixam fora da IA).
_TABELAS_DE_REGISTRO = ("atitudes", "aprendizados", "reparos", "admiracoes")

# ---------------------------------------------------------------- limites e textos

MAX_TEXTO = 1000
MAX_RESPOSTA = 2000
MAX_CARTA = 20000
MAX_LINHAS = 5000                # por tabela (protege a tela e o Hoje; ninguém escreve tanto)
MAX_IMPORTAR = 20000
DIAS_ATRAS_MAX = 3650            # datas aceitas: até 10 anos para trás
SEMANAS_CHECKIN = 12             # check-in de até 12 semanas atrás ainda pode ser feito ou editado
MESES_REFLEXAO = 6
DIAS_ACOMPANHAR = (2, 7)         # "vocês conversaram sobre aquilo?" de 2 a 7 dias depois
DIAS_TEMA = 180                  # tema que voltou 3 vezes nesse tempo
VEZES_TEMA = 3
DIAS_INTENSOS = 30               # 3 de intensidade 3 em 30 dias
VEZES_INTENSOS = 3
DIAS_DESCOMPASSO = 60
VEZES_DESCOMPASSO = 2
DIAS_SENSIVEL = 3                # estado difícil dela nos últimos 3 dias: o Eu fala com mais cuidado
DIAS_ACOMPANHAMENTO = 45         # pendência dela: procura o "perguntei" até 45 dias depois
LIMIAR_GATILHO = 3               # pontos para um aprendizado voltar (estado 3, termo 2, categoria 1)

RELEVANCIA_CHECKIN = 34
RELEVANCIA_MES = 33
RELEVANCIA_INTENCAO_MEIO = 40
RELEVANCIA_INTENCAO_ESCOLHER = 36
RELEVANCIA_REPARO = 58
RELEVANCIA_TEMA = 52
RELEVANCIA_INTENSOS = 64
RELEVANCIA_APRENDIZADO = 60
RELEVANCIA_CUIDAR = 62
RELEVANCIA_CARTA = 38

CHAVE_LEMBRETES = "eu_lembretes"   # Ajustes → 🪞 Área Eu: check-in, reflexão e intenção no Hoje

PERIODO_INVALIDO = "O período precisa ser uma semana (AAAA-Www, como 2026-W40) ou um mês (AAAA-MM)."
PERIODO_FUTURO = "Esse período ainda não chegou."
PERIODO_ANTIGO = "Esse período é antigo demais para um check-in."
TEXTO_INVALIDO = f"Escreva o texto (até {MAX_TEXTO} caracteres)."
DATA_INVALIDA = "A data precisa ser AAAA-MM-DD, de hoje ou de antes."
CHECKIN_VAZIO = "Responda pelo menos uma pergunta, ou escolha como você se sentiu na semana."
PERGUNTA_DESCONHECIDA = "Essa pergunta não é deste check-in."
PRESENCA_INVALIDA = "A presença vai de 1 a 5 (os cinco emojis)."
SO_A_SUA_PARTE = ("Aqui entra só a sua parte: o que aconteceu, o que você sentiu, o que ela pode ter sentido, "
                  "o que faria diferente, se vocês conversaram, o reparo, o tema e a intensidade.")
INTENSIDADE_INVALIDA = "A intensidade vai de 1 (leve) a 3 (forte)."
OCASIAO_INVALIDA = "Escolha uma das ocasiões da lista."
BLOCO_INVALIDO = "Algum bloco escolhido não existe mais. Abra a lista de novo."
CAMPOS_INVALIDOS = "Há campos que não podem ser mudados aqui."
DEMAIS = "Já há coisas demais guardadas aqui. Apague as antigas que não fazem mais sentido."
NAO_ENCONTREI = "Não encontrei isso. Talvez já tenha sido apagado."
ACONTECEU_DO_REGISTRO = "O que aconteceu vem do registro: para mudar, edite o registro."

AVISO_ESPELHO_VAZIO = "Ainda não há registros nestes dias para olhar."
POSSIBILIDADE = "É só uma possibilidade: quem conhece vocês é você."
CUIDAR_TEXTO = "As últimas semanas parecem mais pesadas para você. Como você está?"

_DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro")

# A escala de presença: só sua, nunca vira nota dela (e nunca aparece como número na tela).
PRESENCA = ((1, "😶‍🌫️", "Bem longe"), (2, "😕", "Meio distante"), (3, "🙂", "Mais ou menos"),
            (4, "😊", "Presente"), (5, "🥰", "Muito presente"))
_EMOJI = {v: e for v, e, _ in PRESENCA}

JEITOS: dict[str, str] = {
    "cuidado": "Cuidado", "presenca": "Presença", "escuta": "Escuta", "espaco": "Espaço", "carinho": "Carinho",
    "ajuda": "Ajuda", "interesse": "Interesse", "desculpas": "Desculpas", "gratidao": "Gratidão",
    "conselho": "Conselho", "outro": "Outro",
}
# Jeitos que não se comemoram sozinhos (o "conselho" só serve para perceber um descompasso).
_JEITOS_NEUTROS = frozenset({"conselho", "outro"})

OCASIOES: dict[str, str] = {
    "aniversario_dela": "Aniversário dela", "namoro": "Aniversário de namoro", "mesversario": "Mesversário",
    "namorados": "Dia dos Namorados", "natal": "Natal", "desculpas": "Um pedido de desculpas",
    "sem_motivo": "Só porque sim",
}

_TEMAS = {"cuidado": "Cuidado", "melhorar": "O que pode melhorar", "momentos": "Momentos",
          "mes": "Como andam as coisas", "intencao": "Intenção do mês", "promessa": "Promessas",
          "conversa": "Conversas", "outros": "Outros"}


# ================================================================ ajudantes

_STOP = frozenset(normalizar(w) for w in STOPWORDS)


def _termos(s: str) -> list[str]:
    """O mesmo que `texto.termos` (palavras normalizadas, sem stopwords), com as stopwords
    normalizadas uma vez só: o espelho e os aprendizados chamam isto milhares de vezes."""
    return [w for w in (normalizar(p) for p in palavras(s)) if len(w) > 1 and w not in _STOP]


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _d(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if not valor or not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _dia(d: date) -> str:
    return f"{d:%d/%m}"


def _quando(d: date, ref: date) -> str:
    """"hoje", "ontem", "na terça (29/09)", "em 12/09" (com o ano quando não é o da referência)."""
    dias = (ref - d).days
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    if 2 <= dias <= 6:
        nome = _DIAS_SEMANA[d.weekday()]
        return f"{'no' if nome in ('sábado', 'domingo') else 'na'} {nome} ({_dia(d)})"
    return f"em {_dia(d)}" + (f"/{d.year}" if d.year != ref.year else "")


def _de_quando(d: date, ref: date) -> str:
    """"de terça (29/09)", "de ontem", "do dia 23/09" (para "aquilo de ...")."""
    dias = (ref - d).days
    if dias == 0:
        return "de hoje"
    if dias == 1:
        return "de ontem"
    if 2 <= dias <= 6:
        return f"de {_DIAS_SEMANA[d.weekday()]} ({_dia(d)})"
    return f"do dia {_dia(d)}"


def _json(valor: Any, padrao: Any) -> Any:
    if isinstance(valor, (dict, list)):
        return valor
    try:
        lido = json.loads(valor) if isinstance(valor, str) else padrao
    except ValueError:
        return padrao
    return lido if isinstance(lido, type(padrao)) else padrao


def _texto(valor: Any, maximo: int = MAX_TEXTO, erro: str = TEXTO_INVALIDO, obrigatorio: bool = True) -> str | None:
    if valor is None and not obrigatorio:
        return None
    if not isinstance(valor, str):
        raise ValueError(erro)
    limpo = re.sub(r"[ \t]+", " ", valor).strip()
    if not limpo:
        if obrigatorio:
            raise ValueError(erro)
        return None
    if len(limpo) > maximo:
        raise ValueError(erro)
    return limpo


def _data_pedida(valor: Any, ref: date, padrao: date | None = None) -> date:
    if valor in (None, ""):
        if padrao is None:
            raise ValueError(DATA_INVALIDA)
        return padrao
    d = _d(valor) if isinstance(valor, str) and len(valor) == 10 else None
    if d is None or d > ref or (ref - d).days > DIAS_ATRAS_MAX:
        raise ValueError(DATA_INVALIDA)
    return d


def _id(valor: Any) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else None


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


# ---------------------------------------------------------------- semanas e meses

_RE_SEMANA = re.compile(r"(\d{4})-W(\d{2})")
_RE_MES = re.compile(r"(\d{4})-(\d{2})")


def semana(d: date) -> str:
    """date -> "2026-W40" (semana ISO, de segunda a domingo)."""
    ano, numero, _ = d.isocalendar()
    return f"{ano}-W{numero:02d}"


def mes(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def limites(periodo: Any) -> tuple[str, date, date]:
    """"2026-W40" -> ("semana", segunda, domingo); "2026-09" -> ("mes", 1º, último dia). ValueError."""
    if isinstance(periodo, str):
        achado = _RE_SEMANA.fullmatch(periodo)
        if achado:
            ano, numero = int(achado.group(1)), int(achado.group(2))
            if 2000 <= ano <= 2100:
                try:
                    inicio = date.fromisocalendar(ano, numero, 1)
                except ValueError:
                    raise ValueError(PERIODO_INVALIDO) from None
                return "semana", inicio, inicio + timedelta(days=6)
        achado = _RE_MES.fullmatch(periodo)
        if achado:
            ano, numero = int(achado.group(1)), int(achado.group(2))
            if 2000 <= ano <= 2100 and 1 <= numero <= 12:
                return "mes", date(ano, numero, 1), date(ano, numero, calendar.monthrange(ano, numero)[1])
    raise ValueError(PERIODO_INVALIDO)


def semana_do_checkin(ref: date) -> str:
    """Sexta, sábado e domingo: a semana que está acabando; de segunda a quinta: a que passou."""
    return semana(ref if ref.weekday() >= 4 else ref - timedelta(days=7))


def mes_da_reflexao(ref: date) -> str:
    """Do dia 25 em diante, o mês que está acabando; antes, o mês que passou."""
    if ref.day >= 25:
        return mes(ref)
    return mes(ref.replace(day=1) - timedelta(days=1))


def _mes_anterior(m: str) -> str:
    return mes(limites(m)[1] - timedelta(days=1))


def _mes_seguinte(m: str) -> str:
    return mes(limites(m)[2] + timedelta(days=1))


def rotulo_periodo(periodo: str) -> str:
    tipo, inicio, fim = limites(periodo)
    if tipo == "semana":
        return f"semana de {_dia(inicio)} a {_dia(fim)}"
    return f"{_MESES[inicio.month - 1]} de {inicio.year}"


def _nome_mes(m: str) -> str:
    return _MESES[limites(m)[1].month - 1]


def _semanas_do_mes(m: str) -> list[str]:
    """As semanas ISO cuja quinta-feira cai no mês (cada semana conta para um mês só)."""
    _, inicio, fim = limites(m)
    saida = []
    d = inicio - timedelta(days=inicio.weekday())
    while d <= fim:
        if inicio <= d + timedelta(days=3) <= fim:
            saida.append(semana(d))
        d += timedelta(days=7)
    return saida


# ================================================================ leitura do diário

def _existe(banco: Any, tabela: str) -> bool:
    try:
        return bool(banco.consultar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabela,)))
    except Exception:  # noqa: BLE001
        return False


def _colunas(banco: Any, tabela: str) -> set[str]:
    try:
        return {l["name"] for l in banco.consultar(f'PRAGMA table_info("{tabela}")')}
    except Exception:  # noqa: BLE001 - tabela de outro recurso com outra forma: sem colunas
        return set()


def _dela(item: dict) -> bool:
    return item.get("pessoa_id") is None and not (item.get("pessoa") or "").strip()


def _categorias(item: dict) -> list[str]:
    return [c for c in _json(item.get("categorias"), []) if isinstance(c, str)]


def _itens(banco: Any, desde: date | None = None, ate: date | None = None, tipos: Iterable[str] = ()) -> list[dict]:
    """Itens ativos com a data do registro, do mais recente para o mais antigo."""
    onde, parametros = ["i.status IN ('ativo', 'resolvido')"], []
    if desde is not None:
        onde.append("r.data_registro >= ?")
        parametros.append(desde.isoformat())
    if ate is not None:
        onde.append("r.data_registro <= ?")
        parametros.append(ate.isoformat())
    tipos = list(tipos)
    if tipos:
        onde.append(f"i.tipo IN ({', '.join('?' for _ in tipos)})")
        parametros.extend(tipos)
    linhas = banco.consultar(
        "SELECT i.id, i.registro_id, i.tipo, i.resumo, i.trecho, i.certeza, i.categorias, i.importancia,"
        " i.data_evento, i.precisao, i.pessoa, i.pessoa_id, i.alvo, i.polaridade, i.status, i.atualizado_em,"
        " r.data_registro FROM itens i JOIN registros r ON r.id = i.registro_id"
        f" WHERE {' AND '.join(onde)} ORDER BY r.data_registro DESC, i.id DESC LIMIT 5000", tuple(parametros))
    for l in linhas:
        l["_data"] = _d(l["data_registro"])
        l["_evento"] = _d(l["data_evento"])
    return linhas


def _ativos(linhas: list[dict]) -> list[dict]:
    return [l for l in linhas if l.get("status") == "ativo"]


def _fora_outros(banco: Any) -> set[int] | None:
    """Registros que outros recursos deixam fora da IA (o ciclo...): aqui eles não explicam nada nem
    viram citação. As respostas "Nós dois" (perguntas) já são da área Eu e ficam. None = não deu
    para saber (aí o Eu não usa os estados dela para escolher pergunta nem gatilho)."""
    if falhas():
        return None
    fora: set[int] = set()
    for nome, funcao in com_gancho("fora_da_ia"):
        if nome in (NOME, "perguntas"):
            continue
        try:
            fora |= {i for i in (funcao(banco) or ()) if isinstance(i, int) and not isinstance(i, bool)}
        except Exception as erro:  # noqa: BLE001 - na dúvida, nada de citar
            registrar_erro(erro, NOME, "fora_da_ia de outro recurso")
            return None
    return fora


def _estados_dela(banco: Any, desde: date, ate: date, fora: set[int]) -> list[dict]:
    """ESTADOs dela no período (pela data do estado, senão a do registro), fora o que outro recurso
    deixa de fora. Inferência ("ela parecia magoada" 🔵) não conta: não é fato registrado."""
    saida = []
    for it in _ativos(_itens(banco, desde - timedelta(days=1), ate + timedelta(days=1), ("estado",))):
        quando = it["_evento"] or it["_data"]
        if it.get("certeza") == Certeza.INFERENCIA.value:
            continue
        if _dela(it) and it["registro_id"] not in fora and quando and desde <= quando <= ate:
            it["_quando"] = quando
            saida.append(it)
    return saida


def _alegria(it: dict) -> bool:
    return (it.get("polaridade") or 0) > 0 or "Coisas que deixam feliz" in _categorias(it)


def _preocupacao(it: dict) -> bool:
    return (it.get("polaridade") or 0) < 0 or "Preocupações" in _categorias(it)


def _momento_dela(banco: Any, ref: date, fora: set[int] | None) -> dict | None:
    """O estado mais recente dela, se difícil e dos últimos 3 dias (como o resto do app: se o mais
    recente é bom, não é um momento difícil)."""
    if fora is None:
        return None
    estados = _estados_dela(banco, ref - timedelta(days=DIAS_SENSIVEL), ref, fora)
    if not estados:
        return None
    it = max(estados, key=lambda x: (x["_quando"], x["id"]))
    if (it.get("polaridade") or 0) >= 0:
        return None
    return {"texto": f"{_cap(_quando(it['_quando'], ref))} você registrou: “{it['trecho']}”",
            "trecho": it["trecho"], "data": it["_quando"], "registro_id": it["registro_id"]}


def _promessas(banco: Any) -> list[dict]:
    """A tabela do recurso promessas (seção 20.3), pelas colunas do contrato. Sem ela: lista vazia."""
    if not _existe(banco, "promessas"):
        return []
    cols = _colunas(banco, "promessas")
    if not {"id", "texto", "status"} <= cols:
        return []
    pedir = [c for c in ("id", "texto", "prazo", "status", "registro_id", "criado_em", "concluida_em", "feita_em")
             if c in cols]
    try:
        linhas = banco.consultar(f"SELECT {', '.join(pedir)} FROM promessas ORDER BY id LIMIT 5000")
    except Exception:  # noqa: BLE001
        return []
    for l in linhas:
        l["_feita"] = _d(l.get("feita_em")) or _d(l.get("criado_em"))
        l["_prazo"] = _d(l.get("prazo"))
        l["_concluida"] = _d(l.get("concluida_em"))
    return linhas


def _e_nos_dois(linha: dict, cols: set[str]) -> bool:
    if "trilha" in cols and linha.get("trilha"):
        return linha.get("trilha") == "nos_dois"
    if "categoria" in cols and linha.get("categoria"):
        return normalizar(str(linha.get("categoria"))).replace("_", " ") == "nos dois"
    pid = linha.get("pergunta_id")
    modulo = recurso("perguntas")
    if modulo is not None and callable(getattr(modulo, "pergunta", None)) and callable(getattr(modulo, "trilha", None)):
        try:
            p = modulo.pergunta(pid)
            if p is not None:
                return modulo.trilha(p) == "nos_dois"
        except Exception:  # noqa: BLE001
            pass
    return isinstance(pid, int) and 200 <= pid < 250       # a faixa reservada de "Nós dois" (perguntas.py)


def _texto_da_pergunta(linha: dict) -> str | None:
    if isinstance(linha.get("pergunta"), str) and linha["pergunta"].strip():
        return linha["pergunta"].strip()
    modulo = recurso("perguntas")
    if modulo is not None and callable(getattr(modulo, "pergunta", None)):
        try:
            return getattr(modulo.pergunta(linha.get("pergunta_id")), "texto", None)
        except Exception:  # noqa: BLE001
            return None
    return None


def _textos_de_registros(banco: Any, ids: Iterable[Any]) -> dict[int, str]:
    ids = sorted({i for i in ids if isinstance(i, int) and not isinstance(i, bool)})
    textos: dict[int, str] = {}
    for parte in range(0, len(ids), 500):
        bloco = ids[parte:parte + 500]
        for r in banco.consultar(f"SELECT id, texto FROM registros WHERE id IN ({', '.join('?' for _ in bloco)})",
                                 tuple(bloco)):
            textos[r["id"]] = r["texto"]
    return textos


def _perguntas_feitas(banco: Any, desde: date | None = None, ate: date | None = None) -> list[dict]:
    """As perguntas feitas (recurso perguntas, seção 20.1), pelas colunas do contrato: pergunta_id,
    status, feita_em, registro_id. Cada uma ganha `nos_dois`, `pergunta` (texto do banco de perguntas)
    e, quando a tabela tiver, `resposta` (só se for literal do registro) e `certeza`."""
    if not _existe(banco, "perguntas_feitas"):
        return []
    cols = _colunas(banco, "perguntas_feitas")
    if not {"id", "pergunta_id", "status", "feita_em", "registro_id"} <= cols:
        return []
    try:
        linhas = banco.consultar("SELECT * FROM perguntas_feitas WHERE status = 'feita' ORDER BY id LIMIT 5000")
    except Exception:  # noqa: BLE001
        return []
    textos = _textos_de_registros(banco, (l.get("registro_id") for l in linhas))
    saida = []
    for l in linhas:
        feita = _d(l.get("feita_em"))
        if feita is None or (desde and feita < desde) or (ate and feita > ate):
            continue
        resposta = l.get("resposta") if isinstance(l.get("resposta"), str) else None
        texto_registro = textos.get(l.get("registro_id"))
        if resposta and (texto_registro is None or resposta.strip() not in texto_registro):
            resposta = None                     # nunca inventar: só o que está escrito no registro
        certeza = l.get("certeza") if l.get("certeza") in {c.value for c in Certeza} else None
        if resposta and certeza is None:
            certeza = classificar_certeza(resposta).value
        saida.append({"id": l["id"], "pergunta_id": l.get("pergunta_id"), "pergunta": _texto_da_pergunta(l),
                      "nos_dois": _e_nos_dois(l, cols), "feita_em": feita,
                      "registro_id": l.get("registro_id") if l.get("registro_id") in textos else None,
                      "resposta": resposta.strip() if resposta else None, "certeza": certeza,
                      "recusou": bool(l.get("recusou"))})
    return saida


def _linhas(banco: Any, tabela: str, onde: str = "", parametros: tuple = (), ordem: str = "id") -> list[dict]:
    return banco.consultar(f"SELECT * FROM {tabela} {('WHERE ' + onde) if onde else ''} ORDER BY {ordem} LIMIT {MAX_LINHAS}",
                           parametros)


def _uma(banco: Any, tabela: str, linha_id: int) -> dict:
    linhas = banco.consultar(f"SELECT * FROM {tabela} WHERE id = ?", (linha_id,))
    if not linhas:
        raise NaoEncontrado(NAO_ENCONTREI)
    return linhas[0]


def _texto_registro(banco: Any, registro_id: int) -> str | None:
    linhas = banco.consultar("SELECT texto FROM registros WHERE id = ?", (registro_id,))
    return linhas[0]["texto"] if linhas else None


def _lotado(banco: Any, tabela: str) -> None:
    if banco.consultar(f"SELECT COUNT(*) AS n FROM {tabela}")[0]["n"] >= MAX_LINHAS:
        raise ValueError(DEMAIS)


# ================================================================ o diário aberto (para a revisão)
#
# O aprendizado precisa voltar NA REVISÃO quando o texto novo casa com ele (20.9.4). Desde a
# integração, o `extrair` recebe a leitura do diário em `conhecimento.consultar` (20.0.1). A referência
# FRACA abaixo ficou só como reserva (quem chama o motor sem diário): nenhum conteúdo fica guardado no
# módulo, e ao bloquear o diário é fechado (a consulta falha e a revisão segue sem o lembrete).

_diario: Any = None


def _lembrar_diario(banco: Any) -> None:
    global _diario
    try:
        _diario = weakref.ref(banco)
    except TypeError:
        _diario = None


def _diario_aberto() -> Any:
    ref = _diario
    return ref() if ref is not None else None


def esquecer_diario() -> None:
    """Para os testes: a revisão deixa de olhar os aprendizados até o Hoje ou uma rota daqui rodar."""
    global _diario
    _diario = None


# ================================================================ extração por regras

_RE_IRONIA = re.compile(r"(?<!\w)(?:sqn|so\s+que\s+nao)(?!\w)")
_RE_NEGACAO_ANTES = re.compile(r"(?:^|[\s,;(])(?:nao|n|nem|nunca|jamais|esqueci\s+de|deixei\s+de|sem)\s+"
                               r"(?:(?:eu|te|lhe|mais|ainda|tinha|tenho)\s+)?$")
_RE_DUVIDA_ANTES = re.compile(r"(?:^|\s)(?:se|sera\s+que|nao\s+sei\s+se|n\s+sei\s+se|nao\s+lembro\s+se)\s+(?:eu\s+)?$")
_RE_FIM_ORACAO = re.compile(
    r"\s*[,;:(]|\s+(?:porque|pq|pois|mas|so\s+que|quando|enquanto|e\s+depois|depois\s+disso|e\s+ai|e\s+ela|e\s+eu|"
    r"e\s+a\s+gente|ate\s+que|so\s+pra|para\s+que|pra\s+que|e\s+no\s+fim|e\s+fomos|e\s+voltei|e\s+dormimos)\s")
_RE_PONTA = re.compile(r"[\s.!…]+$")


@dataclass
class _Frase:
    ini: int
    fim: int
    texto: str
    plano: str


def _frases(texto: str) -> list[_Frase]:
    return [_Frase(a, b, f, plano(f)) for a, b, f in frases(texto)]


def _sem_ponta(texto: str) -> int:
    return len(_RE_PONTA.sub("", texto)) if _RE_PONTA.search(texto) else len(texto)


def _alvo_ela(nome_dela: str | None) -> str:
    """"ela", "minha namorada"... e o nome dela (sem acento), para os padrões."""
    opcoes = [r"ela", r"minha\s+(?:namorada|noiva|esposa|mulher|mina|gata|princesa|vida)"]
    nome = plano(nome_dela or "").strip()
    if nome and re.fullmatch(r"[a-z][a-z\s'-]{0,40}", nome):
        opcoes.append(re.escape(nome))
    return r"(?<![\w])(?:a\s+)?(?:" + "|".join(opcoes) + r")(?![\w])"


_ALVO_PADRAO = _alvo_ela(None)



def _fim_oracao(fr: _Frase, depois: int) -> int:
    """Onde termina a oração que começou antes de `depois` (vírgula, "porque", "mas"...)."""
    achado = _RE_FIM_ORACAO.search(fr.plano, depois)
    fim = achado.start() if achado else len(fr.texto)
    return _sem_ponta(fr.texto[:fim])


def _pedaco(fr: _Frase, ini: int, fim: int, maximo: int = 160) -> str:
    """O pedaço literal da frase (cortado numa palavra se for comprido demais)."""
    pedaco = fr.texto[ini:fim].strip()
    if len(pedaco) > maximo:
        corte = pedaco.rfind(" ", 0, maximo)
        pedaco = pedaco[:corte if corte > 20 else maximo].rstrip(" ,;")
    return pedaco


def _negado(fr: _Frase, pos: int) -> bool:
    antes = fr.plano[max(0, pos - 30):pos]
    return bool(_RE_NEGACAO_ANTES.search(antes) or _RE_DUVIDA_ANTES.search(antes))


def _frase_valida(fr: _Frase) -> bool:
    """Pergunta e ironia não viram nada (não foram afirmadas)."""
    return not fr.texto.rstrip().endswith("?") and not _RE_IRONIA.search(fr.plano)


def _data_da_frase(fr: _Frase, datas: list, referencia: date) -> tuple[date, str | None]:
    """A data dita na frase ("ontem levei..."), se for de hoje ou de antes; senão, a do registro."""
    for d in datas or ():
        ini, fim, valor = getattr(d, "inicio", None), getattr(d, "fim", None), getattr(d, "data", None)
        if not isinstance(ini, int) or not isinstance(fim, int) or not isinstance(valor, date) or \
                not (fr.ini <= ini and fim <= fr.fim):
            continue
        precisao = getattr(getattr(d, "precisao", None), "value", getattr(d, "precisao", "dia"))
        if precisao == "dia" and valor <= referencia and (referencia - valor).days <= 60:
            return valor, getattr(d, "expressao", None)
    return referencia, None


# ---------------------------------------------------------------- atitudes

_CUIDADO_OBJ = ("cafe", "cha", "sopa", "comida", "jantar", "almoco", "lanche", "remedio", "agua", "chocolate",
                "doce", "sorvete", "brigadeiro", "bolo", "pipoca", "pao", "fruta", "suco", "vitamina", "cobertor",
                "bolsa de agua", "bolsa termica", "compressa", "marmita", "macarrao", "pizza", "acai", "torrada",
                "mingau", "caldo", "cuscuz", "tapioca", "omelete", "panqueca", "cafezinho", "chazinho", "cafe da manha")
_OBJ_FORA = frozenset("pergunta perguntas comentario critica cobranca reclamacao pix transferencia sinal careta questao "
                      "prova".split())
_LUGARES_CUIDADO = (r"medic[oa]s?|hospital|pronto[\s-]socorro|ps|upa|consulta|exames?|dentista|clinica|laboratorio|"
                    r"terapia|psicolog[oa]|fisioterapia|farmacia|aeroporto|rodoviaria|faculdade|trabalho|prova|"
                    r"entrevista|velorio|enterro|cirurgia|vacina")


def _padroes_atitude(alvo: str) -> list[tuple[str, re.Pattern[str]]]:
    """(jeito, padrão) em primeira pessoa e no passado, sobre o texto plano (sem acento)."""
    a = alvo
    art = r"(?:(?:um|uma|uns|umas|o|a|os|as|meu|minha)\s+)?"
    return [
        ("cuidado", re.compile(rf"(?<![\w])(?P<v>levei|trouxe|fiz|preparei|cozinhei|esquentei|comprei|pedi)\s+{art}"
                               rf"(?P<obj>[a-z][a-z\s-]{{1,40}}?)\s+(?:pra|para|pro)\s+{a}")),
        ("cuidado", re.compile(rf"(?<![\w])(?P<v>levei|trouxe|fiz|preparei|cozinhei|esquentei|comprei|pedi)\s+"
                               rf"(?:pra|para)\s+{a}\s+{art}(?P<obj>[a-z][a-z-]{{1,25}}(?:\s+de\s+[a-z]{{2,15}})?)")),
        ("presenca", re.compile(rf"(?<![\w])(?P<v>levei|acompanhei)\s+{a}\s+(?:no|na|nos|nas|ao|a|pro|pra|para\s+o|"
                                rf"para\s+a|ate\s+o|ate\s+a|em)\s+(?:\w+\s+)?(?P<obj>{_LUGARES_CUIDADO})(?![\w])")),
        ("presenca", re.compile(rf"(?<![\w])(?P<v>acompanhei)\s+{a}")),
        ("ajuda", re.compile(rf"(?<![\w])(?P<v>busquei)\s+{a}")),
        ("presenca", re.compile(rf"(?<![\w])(?P<v>fiquei|estive|permaneci|dormi|passei\s+(?:a\s+noite|o\s+dia|a\s+tarde|"
                                rf"a\s+manha|o\s+fim\s+de\s+semana|o\s+fds|um\s+tempo|horas|o\s+domingo|o\s+sabado))\s+"
                                rf"(?:com|junto\s+(?:com|de)|do\s+lado\s+de|ao\s+lado\s+de|perto\s+de|la\s+com)\s+{a}")),
        ("presenca", re.compile(r"(?<![\w])(?P<v>fiquei|estive|permaneci)\s+(?:do\s+lado|ao\s+lado|perto|juntinho|junto)\s+dela")),
        ("escuta", re.compile(rf"(?<![\w])(?P<v>escutei)\s+(?:{a}|tudo|o\s+que\s+ela|com\s+calma|sem)")),
        ("escuta", re.compile(rf"(?<![\w])(?P<v>ouvi)\s+(?:{a}\s+|tudo\s+|o\s+que\s+ela\s+\w+\s+)?(?:sem\s+(?:dar|julgar|"
                              r"interromper|opinar)|com\s+calma|com\s+atencao|de\s+verdade|desabafar)")),
        ("escuta", re.compile(rf"(?<![\w])(?P<v>deixei)\s+{a}\s+(?:desabafar|falar|chorar|botar\s+pra\s+fora|por\s+pra\s+fora)")),
        ("espaco", re.compile(r"(?<![\w])(?P<v>dei|respeitei)\s+(?:o\s+|um\s+|um\s+pouco\s+de\s+)?(?:espaco(?:\s+(?:dela|pra\s+ela|"
                              r"que\s+ela\s+\w+))?|(?:tempo|silencio)\s+(?:dela|pra\s+ela|que\s+ela\s+(?:pediu|precisava|queria)))")),
        ("espaco", re.compile(rf"(?<![\w])(?P<v>deixei)\s+{a}\s+(?:sozinha|quieta|no\s+canto\s+dela|descansar|dormir|em\s+paz|"
                              r"respirar)")),
        ("espaco", re.compile(r"(?<![\w])(?P<v>nao\s+(?:insisti|pressionei|cobrei|forcei))(?![\w])")),
        ("carinho", re.compile(rf"(?<![\w])(?P<v>abracei)\s+(?:{a}|forte|bem)")),
        ("carinho", re.compile(r"(?<![\w])(?P<v>dei)\s+(?:um\s+)?(?:abraco|colo|beijo|cafune|cheiro)(?![\w])")),
        ("carinho", re.compile(r"(?<![\w])(?P<v>fiz)\s+(?:um\s+|uma\s+)?(?:carinho|cafune|massagem|massagenzinha|surpresa)(?![\w])")),
        ("carinho", re.compile(rf"(?<![\w])(?P<v>elogiei)\s+(?:{a}|o\s+|a\s+|muito)")),
        ("carinho", re.compile(r"(?<![\w])(?P<v>deixei|escrevi|mandei)\s+(?:um\s+|uma\s+)?(?:bilhete|bilhetinho|recadinho|"
                               r"cartinha|carta)(?![\w])")),
        ("carinho", re.compile(r"(?<![\w])(?P<v>mandei)\s+(?:uma\s+)?mensagem\s+(?:de\s+carinho|fofa|carinhosa|de\s+bom\s+dia|"
                               r"de\s+boa\s+noite|de\s+boa\s+sorte|de\s+apoio)")),
        ("ajuda", re.compile(rf"(?<![\w])(?P<v>ajudei)\s+{a}")),
        ("ajuda", re.compile(r"(?<![\w])(?P<v>ajudei)\s+(?:com|na|no|nas|nos|a)\s+[a-z\s]{2,40}?\s+dela(?![\w])")),
        ("cuidado", re.compile(r"(?<![\w])(?P<v>cuidei)\s+(?:dela|d[oa]s?\s+[a-z]+\s+dela)(?![\w])")),
        ("ajuda", re.compile(r"(?<![\w])(?P<v>lavei\s+a\s+louca|arrumei\s+a\s+casa|fiz\s+as\s+compras|fiz\s+o\s+mercado|"
                             r"lavei\s+a\s+roupa|fiz\s+a\s+faxina|limpei\s+a\s+casa)(?![\w])")),
        ("interesse", re.compile(rf"(?<![\w])(?P<v>perguntei)\s+(?:(?:pra|para|a)\s+{a}\s+)?(?:como\s+(?:foi|ela\s+(?:estava|"
                                 r"esta|ta|tava|se\s+sentia|ficou)|tinha\s+sido|ta\s+indo|esta\s+indo)|se\s+ela\s+(?:estava|"
                                 r"esta|ta|tava)\s+bem|(?:sobre|da|do)\s+(?:a\s+|o\s+)?\w+)")),
        ("interesse", re.compile(r"(?<![\w])(?P<v>mandei)\s+(?:uma\s+)?mensagem\s+(?:perguntando|pra\s+saber|para\s+saber)")),
        ("interesse", re.compile(rf"(?<![\w])(?P<v>liguei)\s+(?:(?:pra|para)\s+{a}\s+)?(?:pra\s+saber|para\s+saber|perguntando)")),
        ("desculpas", re.compile(r"(?<![\w])(?P<v>pedi)\s+(?:(?:uma\s+)?desculpas?|perdao)(?![\w])")),
        ("gratidao", re.compile(rf"(?<![\w])(?P<v>agradeci)\s+(?:{a}|a\s+ela|por|pelo|pela|muito)")),
        ("conselho", re.compile(r"(?<![\w])(?P<v>dei)\s+(?:um\s+|uns\s+|o\s+meu\s+|meu\s+|minha\s+)?(?:conselhos?|sermao|"
                                r"palpites?|bronca|opiniao|licao\s+de\s+moral)(?![\w])")),
        ("conselho", re.compile(r"(?<![\w])(?P<v>tentei|quis)\s+(?:logo\s+)?resolver(?![\w])")),
        ("conselho", re.compile(r"(?<![\w])(?P<v>aconselhei)(?![\w])")),
        ("conselho", re.compile(r"(?<![\w])(?P<v>fiquei)\s+(?:dando|falando)\s+(?:conselho|palpite|opiniao|o\s+que\s+ela\s+devia)")),
        ("conselho", re.compile(r"(?<![\w])(?P<v>falei|disse)\s+(?:pra\s+ela\s+)?o\s+que\s+ela\s+(?:devia|deveria|tinha\s+que|"
                                r"precisava)")),
    ]


def _cuidado_obj(obj: str) -> bool:
    n = " " + obj.strip() + " "
    return any(f" {o} " in n for o in _CUIDADO_OBJ)


def _atitudes_da_frase(fr: _Frase, alvo: str, padroes: list[tuple[str, re.Pattern[str]]]) -> list[tuple[int, int, str, str]]:
    """[(ini, fim, jeito, objeto)] das atitudes da frase (uma por oração, sem sobrepor)."""
    if not _frase_valida(fr):
        return []
    achados: list[tuple[int, int, str, str]] = []
    menciona_ela = bool(re.search(alvo, fr.plano)) or bool(re.search(r"(?<![\w])(?:dela|nela|a\s+gente)(?![\w])", fr.plano))
    for jeito, padrao in padroes:
        for m in padrao.finditer(fr.plano):
            ini = m.start("v")
            verbo = m.group("v")
            if not verbo.startswith("nao") and _negado(fr, ini):
                continue
            obj = (m.groupdict().get("obj") or "").strip()
            if jeito == "cuidado" and verbo in ("comprei", "pedi") and not _cuidado_obj(obj):
                continue                 # presente comprado é do recurso presentes
            if obj and obj.split()[0] in _OBJ_FORA:
                continue
            jeito_final = jeito
            if jeito == "cuidado" and verbo in ("levei", "trouxe", "fiz", "preparei") and obj and not _cuidado_obj(obj):
                jeito_final = "carinho"  # "levei flores pra ela": um carinho (o presente é do recurso presentes)
            if jeito_final == "ajuda" and verbo.startswith(("lavei", "arrumei", "fiz", "limpei")) and not menciona_ela:
                continue                 # tarefa de casa só conta quando a frase fala dela
            fim = _fim_oracao(fr, m.end())
            if fim <= ini:
                fim = _sem_ponta(fr.texto[:m.end()])
            if any(not (fim <= a or ini >= b) for a, b, _, _ in achados):
                continue
            achados.append((ini, fim, jeito_final, obj))
    return sorted(achados)


# ---------------------------------------------------------------- aprendizados

_RE_APRENDI = re.compile(r"(?<![\w])(?P<v>aprendi|descobri|percebi|entendi|notei|reparei)(?:\s+com\s+ela)?\s+(?:que|q)\s+"
                         r"(?P<c>\S)")
_RE_APRENDI_A = re.compile(r"(?<![\w])(?P<v>aprendi)(?:\s+com\s+ela)?\s+a\s+(?P<c>[a-z])")
_ACAO_PROXIMA = (r"(?:vou|quero|preciso|devo|tento|nao\s+vou|vou\s+tentar|tenho\s+que|so\s+vou|posso|melhor|"
                 r"nao\s+posso|nao\s+devo)\s+\S")
_RE_PROXIMA = re.compile(r"(?<![\w])(?P<v>(?:(?:da|na)\s+)?proxima\s+vez)\s*,?\s*(?:eu\s+)?(?P<c>" + _ACAO_PROXIMA + ")")
# "Da próxima vez que ela estiver nervosa, vou só abraçar ela": a condição vira gatilho (`_RE_CONDICAO`).
_RE_PROXIMA_QUE = re.compile(r"(?<![\w])(?P<v>(?:(?:da|na)\s+)?proxima\s+vez\s+(?:que|q|em\s+que)\s+[^,;.]+?)"
                             r"\s*[,;]?\s+(?:eu\s+)?(?P<c>" + _ACAO_PROXIMA + ")")
# "Aprendi a ..." só é aprendizado sobre ela (não "aprendi a fazer lasanha").
_RE_SOBRE_ELA = re.compile(r"(?<![\w])(?:ela|dela|nela|com\s+ela|pra\s+ela|para\s+ela|a\s+gente|nos|nosso|nossa)(?![\w])")
_RE_NOTA_MENTAL = re.compile(r"(?<![\w])(?P<v>nota\s+mental|lembrete\s+pra\s+mim|licao|aprendizado|lembrar\s+pra\s+mim)"
                             r"\s*[:\-–]\s*(?P<c>\S)")
_RE_MARCA_APRENDIZADO = re.compile(
    r"(?<![\w])(?:o\s+melhor\s+e|e\s+melhor|melhor\s+(?:e|nao|eu|deixar|so|ficar|esperar|ouvir|escutar|dar)|funciona|"
    r"nao\s+adianta|ajuda\s+(?:mais|muito)|o\s+que\s+ajuda|eu\s+(?:devo|preciso|tenho\s+que|nao\s+devo|deveria|posso)|"
    r"preciso|nao\s+posso|vale\s+(?:mais|a\s+pena)|(?:da|na)\s+proxima\s+vez|quando\s+ela|se\s+ela|sempre\s+que\s+ela|"
    r"toda\s+vez\s+que|quando\s+eu|me\s+\w+)")
_RE_SO_SOBRE_ELA = re.compile(r"^(?:ela\s+)?(?:gosta|odeia|ama|adora|prefere|detesta|curte|nao\s+gosta|tem\s+medo)(?![\w])")


def _aprendizado_da_frase(fr: _Frase) -> tuple[int, int] | None:
    if not _frase_valida(fr):
        return None
    for padrao, precisa_marca, desde_gatilho in ((_RE_NOTA_MENTAL, False, False), (_RE_PROXIMA_QUE, False, True),
                                                 (_RE_PROXIMA, False, True), (_RE_APRENDI_A, False, True),
                                                 (_RE_APRENDI, True, False)):
        m = padrao.search(fr.plano)
        if not m or _negado(fr, m.start("v")):
            continue
        conteudo = fr.plano[m.start("c"):]
        if padrao is _RE_APRENDI_A and not (_RE_SOBRE_ELA.search(conteudo) or _RE_MARCA_APRENDIZADO.search(conteudo)):
            continue                         # "aprendi a fazer lasanha" não é sobre ela
        if precisa_marca:
            explicito = m.group("v") == "aprendi"
            if _RE_SO_SOBRE_ELA.search(conteudo) and not _RE_MARCA_APRENDIZADO.search(conteudo):
                continue                     # "aprendi que ela gosta de X" é sobre ela (as regras do app cuidam)
            if not explicito and not _RE_MARCA_APRENDIZADO.search(conteudo):
                continue                     # "percebi que ela odeia o chefe" é uma observação dela
        ini = m.start("v") if desde_gatilho else m.start("c")
        fim = _sem_ponta(fr.texto)
        if fim - ini < 6:
            continue
        return ini, fim
    return None


ESTADOS: dict[str, str] = {
    "cansad": "cansada", "exaust": "exausta", "estress": "estressada", "trist": "triste", "nervos": "nervosa",
    "ansios": "ansiosa", "brav": "brava", "irritad": "irritada", "chatead": "chateada", "preocupad": "preocupada",
    "magoad": "magoada", "insegur": "insegura", "doente": "doente", "passando mal": "passando mal",
    "sobrecarregad": "sobrecarregada", "desanimad": "desanimada", "sozinh": "sozinha", "com medo": "com medo",
    "mal humorad": "mal-humorada", "frustrad": "frustrada", "chorando": "chorando", "carente": "carente",
    "feliz": "feliz", "animad": "animada", "empolgad": "empolgada", "esgotad": "esgotada",
    "morta de cansaco": "morta de cansaço",
}
# Estados que dizem a mesma coisa: "ela chegou exausta" faz voltar o "quando ela chega cansada".
_SINONIMOS_ESTADO: dict[str, tuple[str, ...]] = {
    "cansad": ("exaust", "esgotad", "morta de cansaco"), "exaust": ("cansad", "esgotad", "morta de cansaco"),
    "esgotad": ("cansad", "exaust"), "morta de cansaco": ("cansad", "exaust"),
    "trist": ("chatead", "desanimad", "magoad"), "chatead": ("trist", "magoad"), "magoad": ("chatead", "trist"),
    "nervos": ("ansios", "estress"), "ansios": ("nervos", "preocupad"), "preocupad": ("ansios",),
    "estress": ("sobrecarregad", "nervos", "irritad"), "sobrecarregad": ("estress",),
    "irritad": ("brav", "mal humorad", "estress"), "brav": ("irritad", "mal humorad"), "mal humorad": ("irritad", "brav"),
}
_GENERICOS = frozenset(normalizar(p) for p in (
    "ela chega fica esta ta tá anda andar chegar ficar estar dia dias vez vezes coisa coisas assunto melhor pior "
    "deixar dar ajuda ajudar funciona preciso devo tenho quero vou eu me dela dele hora momento sempre quando "
    "tudo nada gente fazer faço faz fiz ouvir escutar só so mais menos muito pouco bem mal aprendi percebi "
    "descobri entendi notei proxima próxima nota mental lembrete licao lição aprendizado chegou ficou estava tava "
    "levei trouxe preparei dei pra para pro tipo meio super besteira bobeira historia negocio "
    "for foi era seja fosse ser estiver tiver vier tava tiver kkk kkkk kkkkk rs rsrs haha hahaha ne ai "
    "tem tinha teve vai ia vem veio pode podia").split())
# Risada e interjeição nunca viram gatilho ("kkk", "rsrs", "hahaha").
_RE_RISO = re.compile(r"^(?:k+|(?:rs)+|(?:ha)+h?|(?:he)+|(?:hu)+e?|aff+|ops)$")
_RE_CONDICAO = re.compile(r"(?<![\w])(?:quando|se|sempre\s+que|toda\s+vez\s+que|nos\s+dias\s+(?:em\s+)?que|depois\s+que|"
                          r"antes\s+de|(?:(?:da|na)\s+)?proxima\s+vez\s+(?:que|q|em\s+que))\s+(?P<c>[^,;.]+?)"
                          r"(?=\s*[,;.]|\s+(?:o\s+melhor|e\s+melhor|melhor|funciona|ajuda|eu|"
                          r"preciso|devo|nao|vale|da\s+pra|e\s+bom|vou|quero|so\s+vou)\s|$)")
# Ela como sujeito do estado: "ela (tá|chegou...) cansada", sem negação nem outra pessoa no meio
# ("ela não tá cansada", "ela disse que a mãe tá cansada" não contam).
_RE_ESTADO_COM_ELA = "(?<![\\w])ela\\s+(?P<meio>(?:\\S+\\s+){{0,4}}?){rotulo}(?![\\w])"
# Sem sujeito escrito, só no começo da frase (com no máximo um "hoje", "e", "ontem"...): "chegou cansada
# do trabalho". "Mãe dela chegou cansada" tem sujeito (não é ela).
_RE_ESTADO_SEM_SUJEITO = ("^(?P<meio>(?:(?:hoje|hj|ontem|anteontem|e|ai|entao|so|ja|que|nossa|cara|de\\s+novo)\\s+){{0,2}}"
                          "(?:chegou|ficou|tava|estava|ta|esta|anda|acordou|voltou|parecia)\\s+(?:\\S+\\s+){{0,3}}?)"
                          "{rotulo}(?![\\w])")
_RE_MEIO_NEGADO = re.compile(r"(?<![\w])(?:nao|n|nem|nunca|jamais|ja\s+nao|nada)(?![\w])")
_RE_MEIO_OUTRA_PESSOA = re.compile(
    r"(?<![\w])(?:mae|pai|irma|irmao|amiga|amigo|chefe|avo|avoh|tia|tio|prima|primo|filha|filho|sogra|sogro|"
    r"cunhada|cunhado|colega|vizinha|vizinho|namorado|ex|gente|eu|voce|ele|eles|elas)(?![\w])")


def _estados_no_texto(p: str) -> list[str]:
    return [raiz for raiz in ESTADOS if re.search(rf"(?<![\w]){re.escape(raiz)}", p)]


def gatilhos(texto: str) -> dict[str, list[str]]:
    """O que faz um aprendizado voltar: estados ("quando ela chega cansada" -> cansad), termos da
    condição ("quando o assunto é a mãe dela" -> mae) e a categoria da condição (Família)."""
    p = plano(texto or "")
    condicoes = [m.group("c") for m in _RE_CONDICAO.finditer(p)]
    base = " ".join(condicoes) if condicoes else p
    estados = _estados_no_texto(base) or ([] if condicoes else _estados_no_texto(p))
    estados_palavras = {normalizar(ESTADOS[e]) for e in estados}
    lista_termos: list[str] = []
    for t in _termos(base):
        if (t in _GENERICOS or _RE_RISO.match(t) or t in estados_palavras or any(t.startswith(e) for e in estados)
                or len(t) < 3):
            continue
        if t not in lista_termos:
            lista_termos.append(t)
    categorias = [c for c, pontos in classificar(base) if pontos >= 0.4][:2] if condicoes else []
    return {"estados": estados[:4], "termos": lista_termos[:6], "categorias": categorias}


def _mesmo_termo(a: str, b: str) -> bool:
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def _campo(it: Any, nome: str) -> Any:
    valor = it.get(nome) if isinstance(it, dict) else getattr(it, nome, None)
    return getattr(valor, "value", valor)


# Os dois padrões de cada estado ("ela ... cansada", "chegou cansada"), compilados uma vez só.
_PADROES_ESTADO: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    raiz: (re.compile(_RE_ESTADO_COM_ELA.format(rotulo=re.escape(plano(rotulo)))),
           re.compile(_RE_ESTADO_SEM_SUJEITO.format(rotulo=re.escape(plano(rotulo)))))
    for raiz, rotulo in ESTADOS.items()
}


def _estado_dela_na_frase(raiz: str, fr: _Frase) -> bool:
    """Ela está assim nesta frase? Sem negação ("ela não tá cansada") nem outra pessoa no meio."""
    com_ela, sem_sujeito = _PADROES_ESTADO[raiz]
    for padrao in (com_ela, sem_sujeito):
        for m in padrao.finditer(fr.plano):
            meio = m.group("meio") or ""
            antes = fr.plano[max(0, m.start() - 12):m.start()]
            if _RE_MEIO_NEGADO.search(meio) or _RE_MEIO_OUTRA_PESSOA.search(meio) \
                    or re.search(r"(?<![\w])(?:nao|n|nem)\s+$", antes):
                continue
            return True
    return False


@dataclass
class _Analisado:
    """Um texto dividido uma vez só (frases, termos de cada frase e o que os itens dele dizem), para
    casar com vários aprendizados sem refazer o trabalho (o Hoje e a tela olham muitos registros)."""
    frases: list[_Frase]
    termos: list[list[str]]
    estados_itens: dict[str, str] | None = None     # estado dela (raiz) -> trecho do item
    categorias_itens: set[str] | None = None


def _do_que_os_itens_dizem(itens: Iterable[Any]) -> tuple[dict[str, str], set[str]]:
    estados: dict[str, str] = {}
    categorias: set[str] = set()
    for it in itens:
        cats = _campo(it, "categorias")
        categorias |= set(cats if isinstance(cats, list) else _json(cats, []))
        if _campo(it, "tipo") == "estado" and not (_campo(it, "pessoa") or "").strip() and _campo(it, "pessoa_id") is None:
            resumo = plano(f"{_campo(it, 'resumo') or ''} {_campo(it, 'alvo') or ''}")
            if _RE_MEIO_NEGADO.search(resumo):
                continue                                   # "Não está cansada" não é ela cansada
            for raiz in _estados_no_texto(resumo):
                estados.setdefault(raiz, _campo(it, "trecho") or "")
    return estados, categorias


def _analisar(texto: str, itens: Iterable[Any] | None = None) -> _Analisado:
    frases_ = _frases(texto or "")
    analisado = _Analisado(frases_, [_termos(f.texto) for f in frases_])
    if itens is not None:
        analisado.estados_itens, analisado.categorias_itens = _do_que_os_itens_dizem(itens)
    return analisado


def casar_gatilhos(g: dict, texto: str | _Analisado, itens: Iterable[Any] = ()) -> tuple[int, list[str], int | None]:
    """Pontos explicáveis de um texto novo para os gatilhos de um aprendizado: estado dela +3,
    termo +2, categoria +1. Devolve (pontos, motivos, posição da primeira frase que casou)."""
    pontos, motivos, pos = 0, [], None
    analisado = texto if isinstance(texto, _Analisado) else _analisar(texto)
    frases_ = analisado.frases
    if analisado.estados_itens is not None and analisado.categorias_itens is not None:
        estados_itens, categorias_itens = analisado.estados_itens, analisado.categorias_itens
    else:
        estados_itens, categorias_itens = _do_que_os_itens_dizem(itens)

    def marcar(fr: _Frase | None) -> None:
        nonlocal pos
        if fr is not None:
            pos = fr.ini if pos is None else min(pos, fr.ini)

    estados_do_aprendizado = [r for r in g.get("estados", []) if r in ESTADOS]
    algum_estado = False
    for raiz in estados_do_aprendizado:
        for parecido in (raiz, *_SINONIMOS_ESTADO.get(raiz, ())):
            if parecido not in ESTADOS:
                continue
            trecho = estados_itens.get(parecido)
            fr = next((f for f in frases_ if trecho and (trecho in f.texto or f.texto in trecho)), None) if trecho else None
            fr = fr or next((f for f in frases_ if _estado_dela_na_frase(parecido, f)), None)
            if parecido in estados_itens or fr is not None:
                pontos += 3
                motivos.append(f"ela está {ESTADOS[parecido]}")
                marcar(fr)
                algum_estado = True
                break
    if estados_do_aprendizado and not algum_estado:
        # O estado é a condição do aprendizado ("quando ela chega cansada"): sem ele, o assunto e a
        # categoria sozinhos não o trazem de volta ("Eu cheguei cansado do trabalho" não é ela).
        return 0, [], None
    for t in g.get("termos", []):
        fr = next((f for f, termos_frase in zip(frases_, analisado.termos)
                   if any(_mesmo_termo(t, x) for x in termos_frase)), None)
        if fr is not None:
            pontos += 2
            motivos.append(f"fala de “{t}”")
            marcar(fr)
    for c in g.get("categorias", []):
        if c in categorias_itens:
            pontos += 1
            motivos.append(f"assunto: {c}")
    return pontos, motivos, pos


# ---------------------------------------------------------------- desentendimentos

_VERBO_NOS = (r"(?:brigamos|discutimos|nos\s+desentendemos|nos\s+estranhamos|tivemos\s+(?:uma\s+|um\s+)?(?:\w+\s+)?"
              r"(?:briga|discussao|discussaozinha|briguinha|desentendimento|treta|dr))")
_VERBO_A_GENTE = (r"(?:brigou|discutiu|se\s+desentendeu|se\s+estranhou|teve\s+(?:uma\s+|um\s+)?(?:\w+\s+)?"
                  r"(?:briga|discussao|discussaozinha|briguinha|desentendimento|treta|dr))")
_ENTRE = r"(?:(?:meio|tipo|ate|ja|tambem|de\s+novo|feio)\s+)?"
_RE_CONFLITO = re.compile(
    r"(?<![\w])(?P<v>"
    rf"(?:a\s+gente|eu\s+e\s+ela|ela\s+e\s+eu)\s+{_ENTRE}{_VERBO_A_GENTE}"
    rf"|(?:(?:nos|eu\s+e\s+ela|ela\s+e\s+eu)\s+)?{_ENTRE}{_VERBO_NOS}"
    r"|(?:briguei|discuti|me\s+desentendi)\s+com\s+(?:a\s+)?ela|ela\s+(?:brigou|discutiu)\s+comigo"
    r"|(?:rolou|teve|deu)\s+(?:uma\s+|um\s+)?(?:\w+\s+)?(?:briga|briguinha|discussao|discussaozinha|treta|dr|climao|"
    r"desentendimento)(?=.*(?:entre\s+nos|"
    r"a\s+gente|comigo|com\s+ela|eu\s+e\s+ela))"
    r"|ficou\s+(?:um\s+)?clim(?:a|ao)(?:\s+(?:ruim|estranho|chato|pesado|tenso))?(?=.*(?:entre\s+nos|entre\s+a\s+gente|"
    r"a\s+gente|com\s+ela)))(?![\w])")
_RE_OBJETO_DIRETO = re.compile(r"^\s+(?:o|a|os|as|um|uma|uns|umas)\s+(?!briga|discussao)")
_RE_TEMA = re.compile(r"(?<![\w])(?:por\s+(?:causa|conta)\s+(?:de\s+|d(?=[ao]s?\s))|sobre\s+|de\s+novo\s+por\s+)(?P<t>\S)")
_RE_INTENSO = re.compile(r"(?<![\w])(?:feio|feia|grave|pesad[oa]|horrivel|gritei|gritamos|gritou|gritaria|xing\w*|"
                         r"bateu\s+a\s+porta|terminar|terminamos|chorou\s+muito|muito\s+feio|enorme|daquelas)(?![\w])")
_RE_LEVE = re.compile(r"(?<![\w])(?:briguinha|discussaozinha|bobeira|besteira|leve|rapidinh[oa]|rapida|clima|desentendimento|"
                      r"estranhamos|estranhou|desentendeu|desentendemos|bobo|boba)(?![\w])")
_SENTIMENTOS = (r"magoad[oa]|chatead[oa]|triste|irritad[oa]|frustrad[oa]|culpad[oa]|arrependid[oa]|ansios[oa]|nervos[oa]|"
                r"brav[oa]|com\s+raiva|sem\s+paciencia|incompreendid[oa]|sozinh[oa]|mal|pra\s+baixo|abalad[oa]|confus[oa]|"
                r"injusticad[oa]|envergonhad[oa]|com\s+vergonha|perdid[oa]|desrespeitad[oa]")
_RE_EU_SENTI = re.compile(rf"(?<![\w])(?:(?:eu\s+)?(?P<v>fiquei|me\s+senti)|eu\s+(?P<v2>to|estou|tava|estava|fico|me\s+sinto))\s+"
                          rf"(?:muito\s+|bem\s+|meio\s+|um\s+pouco\s+|super\s+|mt\s+|mto\s+|tao\s+)?(?:{_SENTIMENTOS})(?![\w])")
_RE_FARIA = re.compile(r"(?<![\w])(?P<v>(?:eu\s+)?(?:devia|deveria|podia|poderia|queria)\s+ter\s+)\w")
_RE_REPARO = re.compile(r"(?<![\w])(?P<v>pedi\s+(?:(?:uma\s+)?desculpas?|perdao)|fizemos\s+as\s+pazes|fiz\s+as\s+pazes|"
                        r"a\s+gente\s+(?:conversou|se\s+acertou|fez\s+as\s+pazes)|conversamos(?:\s+(?:depois|com\s+calma|melhor))?|"
                        r"nos\s+acertamos|ja\s+(?:ta|esta)\s+tudo\s+bem)(?![\w])")
_RE_CONVERSAMOS = re.compile(r"(?<![\w])(?:conversou|conversamos|fizemos\s+as\s+pazes|fiz\s+as\s+pazes|se\s+acertou|"
                             r"nos\s+acertamos|fez\s+as\s+pazes)(?![\w])")
_RE_ELA_DISSE_SENTIU = re.compile(r"(?<![\w])(?P<v>ela\s+(?:me\s+)?(?:disse|falou|contou|explicou))\s+que\s+(?:se\s+sentiu|ficou|"
                                  r"estava|tava)\s+(?P<s>[a-z][a-z\s]{1,40}?)(?=\s*[,;.!]|\s+(?:porque|pq|com|quando|e)\s|$)")
_RE_ELA_FICOU = re.compile(r"(?<![\w])(?P<v>ela\s+(?P<verbo>ficou|tava|estava|parecia|pareceu))\s+(?:muito\s+|bem\s+|meio\s+|"
                           r"super\s+)?(?P<s>magoada|chateada|triste|brava|irritada|decepcionada|frustrada|sentida|chorando|"
                           r"abalada|desapontada|com\s+raiva)(?![\w])")


def _reparo_da_frase(fr: _Frase, texto: str, todas: list[_Frase]) -> dict | None:
    if not _frase_valida(fr):
        return None
    m = _RE_CONFLITO.search(fr.plano)
    if not m or _negado(fr, m.start("v")):
        return None
    if re.search(r"(?:discutimos|discutiu)$", m.group("v")) and _RE_OBJETO_DIRETO.match(fr.plano[m.end():]):
        return None                      # "discutimos o orçamento" é conversar sobre, não brigar
    saida: dict[str, Any] = {"aconteceu": _pedaco(fr, 0, _sem_ponta(fr.texto), 400)}
    tema = _RE_TEMA.search(fr.plano, m.start())
    if tema:
        ini = tema.start("t")
        palavras = fr.texto[ini:_fim_oracao(fr, ini)].split()
        if palavras:
            saida["tema"] = " ".join(palavras[:7]).strip(" ,;.")
    if _RE_INTENSO.search(fr.plano) or _RE_INTENSO.search(plano(texto)):
        saida["intensidade"] = 3
    elif _RE_LEVE.search(fr.plano):
        saida["intensidade"] = 1
    else:
        saida["intensidade"] = 2
    for outra in todas:
        if not _frase_valida(outra):
            continue
        if "eu_senti" not in saida:
            s = _RE_EU_SENTI.search(outra.plano)
            if s and not _negado(outra, s.start()):
                saida["eu_senti"] = _cap(_pedaco(outra, s.start("v") if s.group("v") else s.start("v2"), s.end()))
        if "faria_diferente" not in saida:
            f = _RE_FARIA.search(outra.plano)
            if f:
                saida["faria_diferente"] = _cap(_pedaco(outra, f.start("v"), _fim_oracao(outra, f.end())))
        if "reparo" not in saida:
            r = _RE_REPARO.search(outra.plano)
            if r and not _negado(outra, r.start("v")):
                saida["reparo"] = _cap(_pedaco(outra, r.start("v"), _fim_oracao(outra, r.end())))
                if _RE_CONVERSAMOS.search(outra.plano[r.start("v"):]):
                    saida["conversamos"] = True
        if "ela_pode_ter_sentido" not in saida:
            e = _RE_ELA_DISSE_SENTIU.search(outra.plano)
            if e and not _negado(outra, e.start("v")):
                saida["ela_pode_ter_sentido"] = _pedaco(outra, e.start("v"), e.end("s"))
                saida["ela_certeza"] = Certeza.CONFIRMADO.value
                continue
            e = _RE_ELA_FICOU.search(outra.plano)
            if e and not _negado(outra, e.start("v")):
                saida["ela_pode_ter_sentido"] = _pedaco(outra, e.start("v"), e.end("s"))
                saida["ela_certeza"] = (Certeza.INFERENCIA.value if e.group("verbo").startswith("parec")
                                        else Certeza.OBSERVADO.value)
    return saida


# ---------------------------------------------------------------- admirações

_TRACOS = (r"forte|corajosa|generosa|dedicada|inteligente|carinhosa|paciente|gentil|batalhadora|esforcada|sensivel|"
           r"determinada|madura|criativa|talentosa|leal|honesta|bondosa|atenciosa|resiliente|guerreira|focada|"
           r"organizada|engracada|divertida|parceira|companheira|amorosa|incrivel|especial|sabia|admiravel")
# O que se admira tem de ser ela: "admiro ela", "admiro a coragem dela", "admiro como ela...". Uma
# pessoa ou a relação ("meu pai", "a mãe dela") não é admiração por ela (vira bloco de carta para ela).
_RE_ADMIRA_OUTRA = re.compile(
    r"^(?:muito\s+|demais\s+|tanto\s+)?(?:(?:o|a|os|as)\s+)?(?:meu|minha|meus|minhas|seu|sua|nosso|nossa)\s+"
    r"|^(?:muito\s+|demais\s+|tanto\s+)?(?:(?:o|a|os|as)\s+)?(?:pai|mae|irma|irmao|amiga|amigo|chefe|avo|tia|tio|prima|"
    r"primo|filha|filho|sogra|sogro|cunhada|cunhado|familia|colega|professora?|vizinh[ao])(?![\w])")
_RE_ADMIRA = re.compile(
    r"(?<![\w])(?P<v>(?:eu\s+)?admiro\s+(?:muito\s+|demais\s+|tanto\s+)?(?=\S)"
    r"|(?:(?:tenho|sinto)\s+(?:muito\s+|tanto\s+|um\s+)?orgulho\s+dela"
    r"|(?:fico|fiquei|to|estou|sou)\s+(?:muito\s+|tao\s+|super\s+)?orgulhos[oa]\s+dela"
    r"|que\s+orgulho\s+dela"
    r"|sou\s+(?:muito\s+|tao\s+)?grat[oa]\s+(?:por\s+(?:ela|ter\s+ela)|a\s+ela|pela\s+\w+\s+dela)"
    rf"|ela\s+e\s+(?:tao|muito|super|incrivelmente|mt|mto)\s+(?:{_TRACOS}))(?![\w]))")


def _admiracao_da_frase(fr: _Frase, alvo: str = _ALVO_PADRAO) -> tuple[int, int] | None:
    if not _frase_valida(fr):
        return None
    m = _RE_ADMIRA.search(fr.plano)
    if not m or _negado(fr, m.start("v")):
        return None
    ini = m.start("v") + (3 if fr.plano[m.start("v"):].startswith("eu ") else 0)
    fim = _fim_oracao(fr, m.end())
    if fim <= ini + 4:
        fim = _sem_ponta(fr.texto)
    if re.match(r"(?:eu\s+)?admiro", fr.plano[m.start("v"):]):
        objeto = fr.plano[m.end():max(fim, m.end())]
        # "admiro meu pai", "admiro a mãe dela": não é ela. Precisa ser ela ("ela", "dela", o nome dela).
        if _RE_ADMIRA_OUTRA.search(objeto) or not (re.search(alvo, objeto) or re.search(r"(?<![\w])(?:dela|nela)(?![\w])",
                                                                                          objeto)):
            return None
    return ini, fim


# ---------------------------------------------------------------- o gancho extrair

def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Atitudes, aprendizados, desentendimentos e admirações em primeira pessoa (cada um com o
    trecho literal), mais o lembrete de um aprendizado seu que casa com este texto."""
    alvo = _alvo_ela(getattr(conhecimento, "nome_dela", None))
    padroes = _padroes_atitude(alvo)
    todas = _frases(texto)
    saida: list[dict] = []
    frases_de_aprendizado: set[int] = set()
    reparo_feito = False
    for fr in todas:
        for ini, fim, jeito, obj in _atitudes_da_frase(fr, alvo, padroes):
            texto_atitude = _cap(_pedaco(fr, ini, fim))
            quando, expressao = _data_da_frase(fr, datas, referencia)
            saida.append({"tipo": "atitude", "resumo": texto_atitude, "trecho": fr.texto, "texto": texto_atitude,
                          "jeito": jeito, "jeito_nome": JEITOS[jeito], "objeto": obj or None, "data": quando,
                          "data_expressao": expressao})
        aprendizado = _aprendizado_da_frase(fr)
        if aprendizado:
            texto_ap = _cap(_pedaco(fr, *aprendizado, maximo=400))
            frases_de_aprendizado.add(fr.ini)
            g = gatilhos(texto_ap)
            if not any(g.values()):
                g = gatilhos(fr.texto)        # "Quando o assunto for a mãe dela, aprendi que...": a condição veio antes
            saida.append({"tipo": "aprendizado", "resumo": texto_ap, "trecho": fr.texto, "texto": texto_ap,
                          "gatilhos": g, "gatilhos_rotulos": rotulos_gatilhos(g)})
        if not reparo_feito:
            reparo = _reparo_da_frase(fr, texto, todas)
            if reparo:
                reparo_feito = True
                quando, expressao = _data_da_frase(fr, datas, referencia)
                certeza = reparo.pop("ela_certeza", None)
                resumo = "Um desentendimento" + (f" (tema: {reparo['tema']})" if reparo.get("tema") else "")
                saida.append({"tipo": "reparo", "resumo": resumo, "trecho": fr.texto, "data": quando,
                              "data_expressao": expressao, "ela_certeza": certeza, **reparo})
        admiracao = _admiracao_da_frase(fr, alvo)

        if admiracao:
            texto_ad = _cap(_pedaco(fr, *admiracao))
            saida.append({"tipo": "admiracao", "resumo": texto_ad, "trecho": fr.texto, "texto": texto_ad})
    saida.extend(_lembretes(texto, todas, itens, frases_de_aprendizado, getattr(conhecimento, "consultar", None)))
    return saida


def _lembretes(texto: str, todas: list[_Frase], itens: list, ignorar: set[int],
               consultar: Any = None) -> list[dict]:
    """Aprendizados seus cujo gatilho casa com o texto novo, lidos do diário aberto: pela leitura
    que o núcleo passa no `conhecimento` (20.0.1) ou, sem ela, pela referência fraca; sem nenhuma, nada."""
    if not callable(consultar):
        banco = _diario_aberto()
        if banco is None:
            return []
        consultar = banco.consultar
    try:
        aprendizados = consultar("SELECT id, texto, gatilhos, data FROM aprendizados ORDER BY id LIMIT 500")
    except Exception:  # noqa: BLE001 - diário fechado ou sem a tabela: segue sem o lembrete
        return []
    candidatos = []
    analisado = _Analisado(todas, [_termos(f.texto) for f in todas], *_do_que_os_itens_dizem(itens))
    for ap in aprendizados:
        pontos, motivos, pos = casar_gatilhos(_json(ap.get("gatilhos"), {}), analisado, itens)
        if pontos < LIMIAR_GATILHO or pos is None:
            continue
        fr = next((f for f in todas if f.ini == pos), None)
        if fr is None or fr.ini in ignorar:
            continue
        candidatos.append((pontos, ap, motivos, fr))
    candidatos.sort(key=lambda c: (-c[0], c[1]["id"]))
    return [{"tipo": "aprendizado_lembrete", "resumo": f"Um aprendizado seu: “{ap['texto']}”", "trecho": fr.texto,
             "aprendizado_id": ap["id"], "aprendizado": ap["texto"], "aprendizado_data": ap.get("data"),
             "motivos": motivos, "pontos": pontos}
            for pontos, ap, motivos, fr in candidatos[:2]]


def marca_privado(extras: list[dict]) -> bool:
    """Atitude, aprendizado, desentendimento e admiração são da área Eu: o registro fica 🔒 (tipo
    desconhecido também: na dúvida, fora da IA)."""
    return any(e.get("tipo") in _TIPOS_PRIVADOS or e.get("tipo") not in TIPOS_EXTRAS for e in extras)


# ================================================================ gravar o que foi confirmado

def _no_trecho(valor: Any, trecho: str) -> str | None:
    """Um pedaço que está no trecho (a primeira letra pode ter virado maiúscula)."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    v = valor.strip()
    if v in trecho or (v[:1].lower() + v[1:]) in trecho:
        return v
    return None


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava o que o usuário confirmou. Tudo é conferido de novo: o que vem do registro tem de
    estar escrito nele; o que é reflexão sua (o que sentiu, o que faria diferente, o tema) pode ser
    escrito por você; o que ela pode ter sentido fica 🔵 até estar escrito que ela disse."""
    _lembrar_diario(banco)
    linha = banco.consultar("SELECT texto, data_registro FROM registros WHERE id = ?", (registro_id,))
    texto = linha[0]["texto"] if linha else ""
    data_registro = (_d(linha[0]["data_registro"]) if linha else None) or referencia
    nome_dela = banco.config().get("nome_dela")
    for e in extras:
        tipo = e.get("tipo")
        trecho = e.get("trecho") or ""
        if tipo == "atitude":
            _gravar_atitude(banco, registro_id, e, trecho, data_registro, nome_dela)
        elif tipo == "aprendizado":
            _gravar_aprendizado(banco, registro_id, e, trecho, data_registro)
        elif tipo == "reparo":
            _gravar_reparo(banco, registro_id, e, trecho, texto, data_registro)
        elif tipo == "admiracao":
            _gravar_admiracao(banco, registro_id, e, trecho, data_registro)
        elif tipo == "aprendizado_lembrete":
            aid = _id(e.get("aprendizado_id"))
            if aid is not None and banco.consultar("SELECT 1 FROM aprendizados WHERE id = ?", (aid,)):
                banco.atualizar("aprendizados", aid, {"mostrado_em": data_registro}, historico=False)


def _data_extra(e: dict, padrao: date) -> date:
    d = _d(e.get("data")) if isinstance(e.get("data"), str) else None
    if d is None or d > padrao or (padrao - d).days > 60:
        return padrao
    return d


def _gravar_atitude(banco: Any, rid: int, e: dict, trecho: str, data_registro: date, nome_dela: str | None) -> None:
    texto = _no_trecho(e.get("texto"), trecho) or _no_trecho(e.get("resumo"), trecho)
    alvo = _alvo_ela(nome_dela)
    achadas = [(f, a) for f in _frases(trecho) for a in _atitudes_da_frase(f, alvo, _padroes_atitude(alvo))]
    if texto is None:
        texto = _pedaco(achadas[0][0], achadas[0][1][0], achadas[0][1][1]) if achadas else trecho
    jeito = e.get("jeito") if e.get("jeito") in JEITOS else (achadas[0][1][2] if achadas else "outro")
    texto = _cap(texto)
    if banco.consultar("SELECT 1 FROM atitudes WHERE registro_id = ? AND texto = ?", (rid, texto)):
        return
    _lotado(banco, "atitudes")
    banco.inserir("atitudes", {"texto": texto, "data": _data_extra(e, data_registro), "registro_id": rid,
                               "trecho": trecho, "jeito": jeito, "criado_em": _agora()})


def _gravar_aprendizado(banco: Any, rid: int, e: dict, trecho: str, data_registro: date) -> None:
    bruto = e.get("texto") if isinstance(e.get("texto"), str) and e["texto"].strip() else e.get("resumo")
    texto = _cap(_texto(bruto if isinstance(bruto, str) and bruto.strip() else trecho))
    if any(normalizar(a["texto"]) == normalizar(texto) for a in banco.consultar("SELECT texto FROM aprendizados")):
        return
    _lotado(banco, "aprendizados")
    g = gatilhos(texto)
    if not any(g.values()):
        g = gatilhos(trecho)                  # a condição pode ter vindo antes do "aprendi que"
    banco.inserir("aprendizados", {"texto": texto, "gatilhos": g, "criado_em": _agora(),
                                   "data": data_registro, "registro_id": rid, "trecho": trecho})



_CHAVES_EXTRA_REPARO = frozenset({"tipo", "resumo", "trecho", "data", "data_expressao", "ela_certeza", "aconteceu", "tema",
                                  "intensidade", "eu_senti", "faria_diferente", "reparo", "conversamos",
                                  "ela_pode_ter_sentido"})


def _gravar_reparo(banco: Any, rid: int, e: dict, trecho: str, texto: str, data_registro: date) -> None:
    if any(k not in _CHAVES_EXTRA_REPARO and not str(k).startswith("_") for k in e):
        raise ValueError(SO_A_SUA_PARTE)          # não existe campo "o que ela errou": nada é gravado
    valores = _campos_reparo({k: v for k, v in e.items() if k in _CAMPOS_REPARO and k not in ("data", "ela_disse")},
                             data_registro, parcial=True)
    aconteceu = _no_trecho(e.get("aconteceu"), trecho) or _pedaco(_frases(trecho)[0], 0, _sem_ponta(trecho), 400) \
        if trecho.strip() else trecho
    ela = valores.get("ela_pode_ter_sentido")
    certeza = Certeza.INFERENCIA.value
    if ela:
        # O que ela pode ter sentido só passa de 🔵 se estiver escrito no registro, e com a certeza que
        # a própria frase permite (ela disse = 🟢; você viu = 🟡; "parecia" continua 🔵).
        frase = next((f.texto for f in _frases(texto) if _no_trecho(ela, f.texto)), None)
        if frase is not None and not re.search(r"(?<![\w])parec", plano(ela)):
            lida = classificar_certeza(frase).value
            certeza = lida if lida in (Certeza.CONFIRMADO.value, Certeza.OBSERVADO.value) else Certeza.INFERENCIA.value
    if banco.consultar("SELECT 1 FROM reparos WHERE registro_id = ?", (rid,)):
        return
    _lotado(banco, "reparos")
    banco.inserir("reparos", {**valores, "aconteceu": aconteceu, "ela_certeza": certeza,
                              "data": _data_extra(e, data_registro), "criado_em": _agora(), "registro_id": rid,
                              "trecho": trecho,
                              "acompanhado_em": data_registro if valores.get("conversamos") is not None else None})


def _gravar_admiracao(banco: Any, rid: int, e: dict, trecho: str, data_registro: date) -> None:
    texto = _cap(_no_trecho(e.get("texto"), trecho) or _no_trecho(e.get("resumo"), trecho) or trecho)
    if banco.consultar("SELECT 1 FROM admiracoes WHERE registro_id = ? AND texto = ?", (rid, texto)):
        return
    _lotado(banco, "admiracoes")
    banco.inserir("admiracoes", {"texto": texto, "data": data_registro, "registro_id": rid, "trecho": trecho,
                                 "criado_em": _agora()})


# ================================================================ atitudes × o que ela disse que ajuda

_JEITOS_NO_TEXTO: dict[str, re.Pattern[str]] = {j: re.compile(r"(?<![\w])(?:" + p + ")") for j, p in {
    "presenca": r"perto|junt[oa]s?(?![\w])|do\s+lado|ao\s+lado|companhia|presen|fique\s+com|ficar\s+com|estar\s+com|acompanh",
    "escuta": r"escut|ouv[ie]|ouc[ao]|desabaf|prestar\s+atencao|me\s+ouvir",
    "espaco": r"espaco|sozinha|quieta|tempo\s+dela|no\s+tempo\s+dela|silencio|deixar\s+ela\s+(?:em\s+paz|descansar|quieta)",
    "carinho": r"abrac|carinho|colo(?![\w])|cafune|massag|beij|aconcheg|bilhete|elogi",
    "cuidado": r"cha(?![\w])|chazinho|cafe(?![\w])|cafezinho|comida|sopa|jantar|almoco|lanche|chocolate|doce|remedio|cobert",
    "ajuda": r"busc|carona|louca|arrum|tarefa|ajudar\s+(?:com|na|no|nas|nos)\s",
    "interesse": r"pergunt|saber\s+como",
    "conselho": r"conselho|aconselh|resolv|soluc|opini|sermao|palpite|julg|dizer\s+o\s+que\s+fazer",
}.items()}
_RE_NEGACAO_CLAUSULA = re.compile(r"(?<![\w])(?:nao|n|nunca|odeia|detesta|irrita|incomoda|atrapalha|pior)(?![\w])")
_RE_DIVIDE = re.compile(r"\s*[,;]\s*|\s+(?:mas|so\s+que|e\s+sim|prefere\s+que|quer\s+que)\s+")


def jeitos_do_apoio(texto: str, polaridade: int | None) -> tuple[set[str], set[str]]:
    """O que ela disse que ajuda (positivos) e o que ela prefere que não (negativos), por jeito.
    "prefere que eu não tente resolver, só que eu escute" -> ({escuta}, {conselho})."""
    positivos: set[str] = set()
    negativos: set[str] = set()
    for clausula in _RE_DIVIDE.split(" " + plano(texto or "") + " "):
        c = clausula.strip()
        if not c:
            continue
        jeitos = {j for j, padrao in _JEITOS_NO_TEXTO.items() if padrao.search(c)}
        if not jeitos:
            continue
        sem_mais_nada = not positivos and not negativos
        so = re.search(r"(?<![\w])(?:so|apenas)(?![\w])", c)
        if _RE_NEGACAO_CLAUSULA.search(c) or ((polaridade or 0) < 0 and sem_mais_nada and not so):
            negativos |= jeitos
        else:
            positivos |= jeitos
    return positivos, negativos


def _apoios(banco: Any, fora: set[int] | None) -> list[dict]:
    """APOIOs dela (o que ajuda e o que não), com os jeitos de cada um."""
    saida = []
    for it in _ativos(_itens(banco, tipos=("apoio",))):
        if not _dela(it) or (fora is not None and it["registro_id"] in fora):
            continue
        it["_pos"], it["_neg"] = jeitos_do_apoio(it["trecho"], it.get("polaridade"))
        it["_termos"] = set(_termos(it.get("alvo") or "")) - _GENERICOS
        saida.append(it)
    return saida


def combinacoes(atitude: dict, apoios: list[dict]) -> tuple[list[dict], list[dict]]:
    """(combina, descompasso) de uma atitude com o que ela disse. Combina: o jeito bate com algo
    que ela disse que ajuda, ou o objeto da atitude ("chá") está no que ela disse. Descompasso: o
    jeito é algo que ela disse que prefere que não. Só o que ela disse ANTES da atitude conta."""
    jeito = atitude.get("jeito") or "outro"
    objeto = set(_termos(atitude.get("texto") or "")) - _GENERICOS
    combina, descompasso = [], []
    data = _d(atitude.get("data"))
    for ap in apoios:
        if data is not None and ap["_data"] is not None and ap["_data"] > data:
            continue
        if jeito in ap["_neg"]:
            descompasso.append(ap)
        elif jeito not in _JEITOS_NEUTROS and (jeito in ap["_pos"] or (objeto & ap["_termos"] and ap["_pos"])):
            combina.append(ap)
    return combina, descompasso


def _citacao_apoio(ap: dict) -> dict:
    return {"item_id": ap["id"], "registro_id": ap["registro_id"], "trecho": ap["trecho"], "resumo": ap["resumo"],
            "certeza": ap["certeza"], "data": ap["_data"]}


def atitudes_cruzadas(banco: Any, ref: date, desde: date | None = None, ate: date | None = None,
                      fora: set[int] | None = None) -> list[dict]:
    """As atitudes (do período) com o que combina e o descompasso."""
    apoios = _apoios(banco, fora)
    onde, parametros = [], []
    if desde:
        onde.append("data >= ?")
        parametros.append(desde.isoformat())
    if ate:
        onde.append("data <= ?")
        parametros.append(ate.isoformat())
    saida = []
    for a in _linhas(banco, "atitudes", " AND ".join(onde), tuple(parametros), ordem="data DESC, id DESC"):
        combina, descompasso = combinacoes(a, apoios)
        saida.append({**a, "data": _d(a["data"]), "jeito_nome": JEITOS.get(a["jeito"], "Outro"),
                      "combina": [_citacao_apoio(c) for c in combina[:2]],
                      "descompasso": [_citacao_apoio(c) for c in descompasso[:1]]})
    return saida


def descompassos(banco: Any, ref: date, fora: set[int] | None) -> list[dict]:
    """Uma pergunta, nunca uma acusação: "Em 30/09 você registrou: “Ela disse que prefere que eu não
    tente resolver...” Depois disso, em 2 registros, você anotou que deu conselho. Como foi?"."""
    apoios = [a for a in _apoios(banco, fora) if a["_neg"]]
    if not apoios:
        return []
    atitudes = _linhas(banco, "atitudes", "data >= ? AND data <= ?",
                       ((ref - timedelta(days=DIAS_DESCOMPASSO)).isoformat(), ref.isoformat()), ordem="data, id")
    saida = []
    for ap in apoios:
        depois = [a for a in atitudes if a["jeito"] in ap["_neg"] and (_d(a["data"]) or ref) >= (ap["_data"] or ref)]
        if len({a["registro_id"] or -a["id"] for a in depois}) < VEZES_DESCOMPASSO:
            continue
        jeito = depois[0]["jeito"]
        o_que = "deu conselho ou tentou resolver" if jeito == "conselho" else f"fez algo de {JEITOS[jeito].lower()}"
        quando = f"Em {_dia(ap['_data'])} você" if ap["_data"] else "Você"
        saida.append({
            "texto": f"{quando} registrou: “{ap['trecho']}” Depois disso, em {len(depois)} registros, você anotou que "
                     f"{o_que}. Como foi?",
            "apoio": _citacao_apoio(ap),
            "atitudes": [{"id": a["id"], "texto": a["texto"], "data": _d(a["data"]), "registro_id": a["registro_id"]}
                         for a in depois[-3:]],
            "registro_ids": sorted({x for x in [ap["registro_id"], *(a["registro_id"] for a in depois[-3:])] if x}),
        })
    return saida[:3]


# ================================================================ check-in

@dataclass
class PerguntaCheckin:
    id: str
    texto: str                      # o que a tela pergunta (com o detalhe de agora)
    generico: str                   # o que fica guardado (sem conteúdo de registro: apagar é apagar)
    tema: str
    porque: str = ""
    registro_ids: list[int] = field(default_factory=list)
    refs: dict = field(default_factory=dict)
    pontos: int = 0

    def para_dict(self) -> dict:
        return {"id": self.id, "texto": self.texto, "tema": self.tema, "porque": self.porque,
                "registro_ids": list(self.registro_ids), "refs": dict(self.refs), "resposta": ""}


_FIXAS_SEMANA = {
    "coisa_boa": ("Uma coisa boa que você fez por ela nesta semana", "cuidado"),
    "faria_diferente": ("Algo que você faria diferente", "melhorar"),
}
# A data que as regras põem no fim do resumo ("Entrevista de emprego — 30/09/2026"): a pergunta já diz o dia.
_RE_DATA_NO_RESUMO = re.compile(r"\s*—\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$")
_FIXAS_MES = {

    "mes_momento": ("Que momento com ela você quer guardar deste mês?", "momentos"),
    "mes_cuidado": ("Em que você sente que cuidou bem dela este mês?", "cuidado"),
    "mes_melhorar": ("O que você quer cuidar melhor no mês que vem?", "melhorar"),
    "mes_voce": ("E você, como chegou ao fim do mês?", "mes"),
}


def _ganchos_da_semana(banco: Any, inicio: date, fim: date, ref: date, fora: set[int] | None) -> list[PerguntaCheckin]:
    ganchos: list[PerguntaCheckin] = []
    if fora is not None:
        # Semana difícil dela (estados difíceis registrados; nada do ciclo).
        dificeis = [e for e in _estados_dela(banco, inicio, fim, fora) if _preocupacao(e)]
        if len(dificeis) >= 2 or any((e.get("importancia") or 0) >= 3 for e in dificeis):
            dificeis.sort(key=lambda e: (e["_quando"], e["id"]))
            citados = dificeis[-2:]
            ids = sorted({e["registro_id"] for e in citados})
            porque = " ".join(f"{_cap(_quando(e['_quando'], ref))} você registrou: “{e['trecho']}”" for e in citados)
            ganchos.append(PerguntaCheckin("semana_dificil", "Como você a apoiou na semana difícil dela?",
                                           "Como você a apoiou na semana difícil dela?", "cuidado", porque, ids,
                                           {"registro_ids": ids}, 90))
        # Um dia importante dela na semana (evento de importância alta).
        eventos = [it for it in _ativos(_itens(banco, inicio - timedelta(days=120), fim, ("evento",)))
                   if _dela(it) and it["registro_id"] not in fora and (it.get("importancia") or 0) >= 3
                   and it["_evento"] and inicio <= it["_evento"] <= fim and it.get("precisao") in (None, "dia")]
        if eventos:
            ev = min(eventos, key=lambda x: (x["_evento"], x["id"]))
            resumo = _RE_DATA_NO_RESUMO.sub("", ev["resumo"] or "").strip() or ev["resumo"]
            ganchos.append(PerguntaCheckin(
                "evento_dela", f"Ela tinha “{resumo}” ({_dia(ev['_evento'])}). Como você esteve com ela nisso?",
                "Ela tinha um dia importante nesta semana. Como você esteve com ela nisso?", "cuidado",
                f"Você registrou {_quando(ev['_data'], ref)}: “{ev['trecho']}”", [ev["registro_id"]],
                {"item_id": ev["id"], "registro_ids": [ev["registro_id"]]}, 75))
    # Promessa vencendo (aberta, prazo na semana ou até 3 dias depois).
    abertas = [p for p in _promessas(banco) if p.get("status") == "aberta" and p["_prazo"]
               and inicio <= p["_prazo"] <= fim + timedelta(days=3)]
    if abertas:
        p = min(abertas, key=lambda x: (x["_prazo"], x["id"]))
        ids = [p["registro_id"]] if p.get("registro_id") else []
        faltam = (p["_prazo"] - ref).days
        if faltam > 0:
            # O prazo ainda não chegou: a pergunta não é no passado (revisão de 07/10).
            pergunta = f"Já deu para cumprir “{p['texto']}”?"
            porque = f"O prazo é {'amanhã' if faltam == 1 else 'daqui a ' + str(faltam) + ' dias'} ({_dia(p['_prazo'])})."
        elif faltam == 0:
            pergunta, porque = f"Já deu para cumprir “{p['texto']}”?", f"O prazo é hoje ({_dia(p['_prazo'])})."
        else:
            pergunta, porque = f"Conseguiu cumprir “{p['texto']}”?", f"O prazo era {_dia(p['_prazo'])}."
        ganchos.append(PerguntaCheckin("promessa", pergunta,
                                       "Conseguiu cumprir a promessa que vencia nesta semana?", "promessa",
                                       porque, ids, {"promessa_id": p["id"], "registro_ids": ids}, 80))
    # Pergunta profunda (ou "Nós dois") feita na semana.
    feitas = [f for f in _perguntas_feitas(banco, inicio, fim) if f.get("pergunta")]
    if feitas:
        f = max(feitas, key=lambda x: (x["feita_em"], x["id"]))
        ids = [f["registro_id"]] if f.get("registro_id") else []
        texto = f"Como foi a conversa sobre “{f['pergunta']}”?"
        ganchos.append(PerguntaCheckin("pergunta_feita", texto, texto, "conversa",
                                       f"Você perguntou {_quando(f['feita_em'], ref)}.", ids,
                                       {"pergunta_feita_id": f["id"], "registro_ids": ids}, 70))
    # Um desentendimento na semana.
    reparos = _linhas(banco, "reparos", "data >= ? AND data <= ?", (inicio.isoformat(), fim.isoformat()),
                      ordem="data DESC, id DESC")
    if reparos:
        r = reparos[0]
        d = _d(r["data"]) or inicio
        ids = [r["registro_id"]] if r.get("registro_id") else []
        texto = f"Depois do desentendimento de {_dia(d)}, como vocês ficaram?"
        ganchos.append(PerguntaCheckin("reparo", texto, texto, "melhorar", "Você anotou um desentendimento nesta semana.",
                                       ids, {"reparo_id": r["id"], "registro_ids": ids}, 85))
    ganchos.sort(key=lambda g: -g.pontos)
    return ganchos


def perguntas_da_semana(banco: Any, periodo: str, ref: date) -> list[PerguntaCheckin]:
    """2 ou 3 perguntas escolhidas pela semana real: até 2 ganchos (os de maior peso) + "uma coisa
    boa que você fez" (comemora mais do que cobra); sem gancho, a coisa boa e "algo que faria
    diferente"."""
    _, inicio, fim = limites(periodo)
    ganchos = _ganchos_da_semana(banco, inicio, fim, ref, _fora_outros(banco))[:2]
    saida = list(ganchos)
    for chave in (["coisa_boa"] if ganchos else ["coisa_boa", "faria_diferente"]):
        texto, tema = _FIXAS_SEMANA[chave]
        saida.append(PerguntaCheckin(chave, texto, texto, tema))
    return saida


def perguntas_do_mes(banco: Any, m: str) -> list[PerguntaCheckin]:
    saida = [PerguntaCheckin(k, t, t, tema) for k, (t, tema) in _FIXAS_MES.items()]
    intencao = _intencao(banco, m)
    if intencao:
        saida.insert(2, PerguntaCheckin("mes_intencao", f"Como foi a sua intenção “{intencao['texto']}”?",
                                        "Como foi a sua intenção do mês?", "intencao",
                                        f"Você escolheu essa intenção para {_nome_mes(m)}.", [],
                                        {"intencao_id": intencao["id"]}))
    return saida


def _checkin(banco: Any, tipo: str, periodo: str) -> dict | None:
    linhas = banco.consultar("SELECT * FROM checkins WHERE tipo = ? AND periodo = ?", (tipo, periodo))
    if not linhas:
        return None
    l = linhas[0]
    l["respostas"] = _json(l["respostas"], {})
    return l


def _enriquecer(banco: Any, pergunta: dict) -> dict:
    """A pergunta guardada (genérica) ganha de novo o detalhe de agora, se ele ainda existe."""
    refs = pergunta.get("refs") if isinstance(pergunta.get("refs"), dict) else {}
    texto = pergunta.get("pergunta") or ""
    existentes = set(_textos_de_registros(banco, refs.get("registro_ids") or []))
    if pergunta.get("id") == "promessa" and _id(refs.get("promessa_id")):
        p = next((x for x in _promessas(banco) if x["id"] == refs["promessa_id"]), None)
        if p:
            texto = f"Conseguiu cumprir “{p['texto']}”?"
    if pergunta.get("id") == "evento_dela" and _id(refs.get("item_id")):
        linhas = banco.consultar("SELECT resumo, data_evento FROM itens WHERE id = ?", (refs["item_id"],))
        if linhas and _d(linhas[0]["data_evento"]):
            texto = f"Ela tinha “{linhas[0]['resumo']}” ({_dia(_d(linhas[0]['data_evento']))}). Como você esteve com ela nisso?"
    if pergunta.get("id") == "mes_intencao" and _id(refs.get("intencao_id")):
        linhas = banco.consultar("SELECT texto FROM intencoes WHERE id = ?", (refs["intencao_id"],))
        if linhas:
            texto = f"Como foi a sua intenção “{linhas[0]['texto']}”?"
    return {"id": pergunta.get("id"), "texto": texto, "tema": pergunta.get("tema") or "outros", "porque": "",
            "resposta": pergunta.get("resposta") or "", "registro_ids": sorted(existentes), "refs": {}}


def _vista_checkin(banco: Any, tipo: str, periodo: str, ref: date) -> dict:
    perguntas = perguntas_da_semana(banco, periodo, ref) if tipo == "semanal" else perguntas_do_mes(banco, periodo)
    feito = _checkin(banco, tipo, periodo)
    _, inicio, fim = limites(periodo)
    vista = {"tipo": tipo, "periodo": periodo, "rotulo": rotulo_periodo(periodo), "inicio": inicio, "fim": fim,
             "perguntas": [p.para_dict() for p in perguntas],
             "escala": [{"valor": v, "emoji": e, "rotulo": r} for v, e, r in PRESENCA] if tipo == "semanal" else [],
             "feito": None}
    if feito:
        guardadas = [_enriquecer(banco, p) for p in feito["respostas"].get("perguntas", []) if isinstance(p, dict)]
        ids = {p["id"] for p in guardadas}
        # As perguntas de agora que não foram respondidas continuam aparecendo (dá para completar).
        vista["perguntas"] = guardadas + [p.para_dict() for p in perguntas if p.id not in ids]
        vista["feito"] = {"id": feito["id"], "presenca": feito["presenca"], "emoji": _EMOJI.get(feito["presenca"]),
                          "criado_em": feito["criado_em"], "atualizado_em": feito["atualizado_em"]}
    return vista


def _conferir_periodo(tipo: str, periodo: Any, ref: date, para_ver: bool = False) -> str:
    if tipo not in ("semanal", "mensal"):
        raise ValueError("O check-in é semanal ou mensal.")
    tipo_periodo, inicio, fim = limites(periodo)
    if (tipo == "semanal") != (tipo_periodo == "semana"):
        raise ValueError(PERIODO_INVALIDO)
    if inicio > ref:
        raise ValueError(PERIODO_FUTURO)
    if not para_ver and tipo == "semanal" and (ref - inicio).days > 7 * SEMANAS_CHECKIN:
        raise ValueError(PERIODO_ANTIGO)
    if not para_ver and tipo == "mensal" and (ref - fim).days > 31 * MESES_REFLEXAO:
        raise ValueError(PERIODO_ANTIGO)
    return periodo


def salvar_checkin(banco: Any, corpo: dict, ref: date) -> dict:
    """Grava (ou completa) o check-in do período. As perguntas aceitas são as que o app escolheu
    para aquele período (ou as já guardadas); o que fica guardado é a forma genérica da pergunta
    (o detalhe de agora volta na hora de mostrar, se ainda existir: apagar é apagar)."""
    estranhos = set(corpo) - {"tipo", "periodo", "respostas", "presenca"}
    if estranhos:
        raise ValueError(CAMPOS_INVALIDOS)
    tipo = corpo.get("tipo", "semanal")
    periodo = _conferir_periodo(tipo, corpo.get("periodo"), ref)
    respostas = corpo.get("respostas") if corpo.get("respostas") is not None else {}
    if not isinstance(respostas, dict):
        raise ValueError(CHECKIN_VAZIO)
    presenca = corpo.get("presenca")
    if tipo == "mensal":
        presenca = None
    elif presenca is not None and (isinstance(presenca, bool) or not isinstance(presenca, int) or presenca not in _EMOJI):
        raise ValueError(PRESENCA_INVALIDA)
    validas = {p.id: p for p in (perguntas_da_semana(banco, periodo, ref) if tipo == "semanal"
                                 else perguntas_do_mes(banco, periodo))}
    anterior = _checkin(banco, tipo, periodo)
    guardadas = {p["id"]: p for p in (anterior["respostas"].get("perguntas", []) if anterior else [])
                 if isinstance(p, dict) and isinstance(p.get("id"), str)}
    perguntas = []
    for pid, resposta in respostas.items():
        if pid not in validas and pid not in guardadas:
            raise ValueError(PERGUNTA_DESCONHECIDA)
        resposta = _texto(resposta, MAX_RESPOSTA, f"Cada resposta tem até {MAX_RESPOSTA} caracteres.", obrigatorio=False)
        if not resposta:
            continue
        base = validas.get(pid)
        if base is not None:
            perguntas.append({"id": pid, "pergunta": base.generico, "tema": base.tema, "refs": base.refs, "resposta": resposta})
        else:
            perguntas.append({**guardadas[pid], "resposta": resposta})
    # O que já estava respondido e não veio de novo continua (completar não apaga sem querer).
    perguntas.extend(p for pid, p in guardadas.items() if pid not in respostas)
    if not perguntas and presenca is None:
        raise ValueError(CHECKIN_VAZIO)
    if anterior and "presenca" not in corpo:
        presenca = anterior["presenca"]
    valores = {"respostas": {"perguntas": perguntas}, "presenca": presenca}
    if anterior:
        banco.atualizar("checkins", anterior["id"], {**valores, "atualizado_em": _agora()})
        cid = anterior["id"]
    else:
        _lotado(banco, "checkins")
        cid = banco.inserir("checkins", {"tipo": tipo, "periodo": periodo, **valores, "criado_em": _agora()})
    if tipo == "mensal" and "mes_intencao" in respostas:
        intencao = _intencao(banco, periodo)
        resposta = next((p["resposta"] for p in perguntas if p["id"] == "mes_intencao"), None)
        if intencao and resposta:
            banco.atualizar("intencoes", intencao["id"], {"revisao": resposta, "revisada_em": ref}, historico=False)
    return {"id": cid, **_vista_checkin(banco, tipo, periodo, ref)}


# ================================================================ cuidar de você

def _presencas(banco: Any) -> list[tuple[str, int]]:
    return [(l["periodo"], l["presenca"]) for l in
            banco.consultar("SELECT periodo, presenca FROM checkins WHERE tipo = 'semanal' AND presenca IS NOT NULL"
                            " ORDER BY periodo")]


def cuidar_de_voce(banco: Any, ref: date) -> dict | None:
    """Presença baixa (😕 ou menos) por 3 semanas seguidas, ou caindo 3 semanas seguidas: "As
    últimas semanas parecem mais pesadas para você. Como você está?" (sem diagnóstico)."""
    pontos = [(p, v) for p, v in _presencas(banco) if limites(p)[1] <= ref]
    if len(pontos) < 3:
        return None
    ultima = pontos[-1][0]
    if (ref - limites(ultima)[1]).days > 14:
        return None                       # faz tempo: não dá para dizer "as últimas semanas"
    seguidas = [pontos[-1]]
    for periodo, valor in reversed(pontos[:-1]):
        if (limites(seguidas[0][0])[1] - limites(periodo)[1]).days != 7:
            break
        seguidas.insert(0, (periodo, valor))
    baixa = len(seguidas) >= 3 and all(v <= 2 for _, v in seguidas[-3:])
    caindo = len(seguidas) >= 4 and all(seguidas[i][1] > seguidas[i + 1][1] for i in range(len(seguidas) - 4, len(seguidas) - 1))
    if not (baixa or caindo):
        return None
    mostradas = seguidas[-4:] if caindo and not baixa else seguidas[-3:]
    lista = ", ".join(f"{_dia(limites(p)[1])} {_EMOJI[v]}" for p, v in mostradas)
    return {"texto": CUIDAR_TEXTO,
            "porque": f"Nos seus check-ins (semanas de {lista}). Cuidar de você também faz parte. Se fizer sentido, "
                      "conversar com alguém de confiança pode ajudar.",
            "periodo": ultima, "semanas": [{"periodo": p, "emoji": _EMOJI[v]} for p, v in mostradas]}


# ================================================================ desentendimentos

_CAMPOS_REPARO = ("aconteceu", "eu_senti", "ela_pode_ter_sentido", "ela_disse", "faria_diferente", "conversamos",
                  "reparo", "tema", "intensidade", "data")


def _campos_reparo(corpo: dict, ref: date, parcial: bool = False) -> dict:
    """Só a sua parte. Campo que não está na lista (como "o que ela errou") = 400."""
    if not isinstance(corpo, dict) or any(k not in _CAMPOS_REPARO for k in corpo):
        raise ValueError(SO_A_SUA_PARTE)
    valores: dict[str, Any] = {}
    if "aconteceu" in corpo or not parcial:
        valores["aconteceu"] = _texto(corpo.get("aconteceu"), erro="Conte em poucas palavras o que aconteceu (até 1000 caracteres).")
    for nome in ("eu_senti", "ela_pode_ter_sentido", "faria_diferente", "reparo", "tema"):
        if nome in corpo:
            valores[nome] = _texto(corpo[nome], obrigatorio=False) if corpo[nome] is not None else None
    if "ela_disse" in corpo:
        if not isinstance(corpo["ela_disse"], bool):
            raise ValueError("“ela_disse” é sim ou não.")
        valores["ela_certeza"] = Certeza.CONFIRMADO.value if corpo["ela_disse"] else Certeza.INFERENCIA.value
    if "conversamos" in corpo:
        v = corpo["conversamos"]
        if v is not None and not isinstance(v, bool):
            raise ValueError("“conversamos” é sim, não ou ainda não sei.")
        valores["conversamos"] = None if v is None else (1 if v else 0)
    if "intensidade" in corpo or not parcial:
        i = corpo.get("intensidade", 1)
        if isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= 3:
            raise ValueError(INTENSIDADE_INVALIDA)
        valores["intensidade"] = i
    if "data" in corpo or not parcial:
        valores["data"] = _data_pedida(corpo.get("data"), ref, padrao=ref)
    return valores


def _tema_termos(tema: str | None) -> set[str]:
    return {t for t in _termos(tema or "") if t not in _GENERICOS and len(t) >= 3}


def _mesmo_tema(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    if any(_mesmo_termo(x, y) for x in _tema_termos(a) for y in _tema_termos(b)):
        return True
    return semelhanca(a, b) >= 85


def temas_repetidos(banco: Any, ref: date) -> list[dict]:
    """Assunto que voltou 3 vezes (em 180 dias): "talvez valha uma conversa calma"."""
    reparos = [r for r in _linhas(banco, "reparos", "data >= ? AND data <= ? AND tema IS NOT NULL",
                                  ((ref - timedelta(days=DIAS_TEMA)).isoformat(), ref.isoformat()), ordem="data DESC, id DESC")
               if (r.get("tema") or "").strip()]
    usados: set[int] = set()
    saida = []
    for r in reparos:
        if r["id"] in usados:
            continue
        grupo = [x for x in reparos if x["id"] not in usados and _mesmo_tema(r["tema"], x["tema"])]
        if len(grupo) < VEZES_TEMA:
            continue
        usados |= {x["id"] for x in grupo}
        datas = sorted(_d(x["data"]) for x in grupo)
        saida.append({"tema": r["tema"], "vezes": len(grupo), "ultimo_id": r["id"], "datas": datas,
                      "registro_ids": [x["registro_id"] for x in grupo if x.get("registro_id")],
                      "texto": f"Esse assunto (“{r['tema']}”) voltou {len(grupo)} vezes; talvez valha uma conversa calma, "
                               "num momento tranquilo para os dois.",
                      "porque": "Desentendimentos anotados em " + ", ".join(_dia(d) for d in datas) + "."})
    return saida


def intensos(banco: Any, ref: date) -> dict | None:
    """Frequentes e intensos (3 de intensidade 3 em 30 dias): sugestão cuidadosa de conversar com
    ela ou com um profissional. Nada de diagnóstico."""
    linhas = _linhas(banco, "reparos", "intensidade = 3 AND data >= ? AND data <= ?",
                     ((ref - timedelta(days=DIAS_INTENSOS)).isoformat(), ref.isoformat()), ordem="data DESC, id DESC")
    if len(linhas) < VEZES_INTENSOS:
        return None
    datas = sorted(_d(l["data"]) for l in linhas)
    return {"ultimo_id": linhas[0]["id"], "vezes": len(linhas), "datas": datas,
            "registro_ids": [l["registro_id"] for l in linhas if l.get("registro_id")],
            "texto": "Os desentendimentos andam frequentes e intensos. Talvez valha conversar com ela num momento calmo "
                     "e, se fizer sentido para vocês, buscar a ajuda de um profissional (terapia de casal ou individual).",
            "porque": f"Você anotou {len(linhas)} desentendimentos fortes nos últimos {DIAS_INTENSOS} dias ("
                      + ", ".join(_dia(d) for d in datas) + ")."}


def _vista_reparo(r: dict, ref: date) -> dict:
    d = _d(r["data"])
    return {**r, "data": d, "conversamos": None if r["conversamos"] is None else bool(r["conversamos"]),
            "ela_certeza": r.get("ela_certeza") or Certeza.INFERENCIA.value, "quando": _quando(d, ref) if d else "",
            "de_quando": _de_quando(d, ref) if d else "",
            "acompanhar": bool(d and r["conversamos"] is None and r["acompanhado_em"] is None
                               and DIAS_ACOMPANHAR[0] <= (ref - d).days <= DIAS_ACOMPANHAR[1])}


# ================================================================ aprendizados

def _registros_recentes(banco: Any, desde: date, ate: date, fora: set[int] | None) -> list[dict]:
    regs = banco.consultar("SELECT id, data_registro, texto FROM registros WHERE data_registro >= ? AND data_registro <= ?"
                           " ORDER BY data_registro DESC, id DESC LIMIT 120", (desde.isoformat(), ate.isoformat()))
    regs = [r for r in regs if fora is None or r["id"] not in fora]
    if not regs:
        return []
    itens = banco.consultar(
        "SELECT registro_id, tipo, resumo, trecho, alvo, pessoa, pessoa_id, categorias FROM itens WHERE status = 'ativo'"
        f" AND registro_id IN ({', '.join('?' for _ in regs)})", tuple(r["id"] for r in regs))
    por_registro: dict[int, list[dict]] = {}
    for i in itens:
        por_registro.setdefault(i["registro_id"], []).append(i)
    for r in regs:
        r["_data"] = _d(r["data_registro"])
        r["_itens"] = por_registro.get(r["id"], [])
        r["_analisado"] = _analisar(r["texto"], r["_itens"])   # dividido uma vez, usado por todos os aprendizados
    return regs


def rotulos_gatilhos(g: dict) -> list[str]:
    """Os gatilhos como a tela mostra: "ela cansada", "“trabalho”", "Família" (nunca a raiz "cansad")."""
    return ([f"ela {ESTADOS.get(e, e)}" for e in g.get("estados", [])] + [f"“{t}”" for t in g.get("termos", [])]
            + list(g.get("categorias", [])))


def _vista_aprendizado(ap: dict, ref: date, recentes: list[dict] | None = None) -> dict:
    g = _json(ap["gatilhos"], {})
    rotulos = rotulos_gatilhos(g)

    voltou = []
    data_ap = _d(ap.get("data")) or ref
    for r in recentes or []:
        if r["id"] == ap.get("registro_id") or (r["_data"] or ref) < data_ap:
            continue
        pontos, motivos, pos = casar_gatilhos(g, r["_analisado"], r["_itens"])
        if pontos >= LIMIAR_GATILHO and pos is not None:
            fr = next((f for f in r["_analisado"].frases if f.ini == pos), None)
            voltou.append({"registro_id": r["id"], "data": r["_data"], "trecho": fr.texto if fr else None,
                           "motivos": motivos})
    return {"id": ap["id"], "texto": ap["texto"], "gatilhos": rotulos, "data": _d(ap.get("data")),
            "mostrado_em": _d(ap.get("mostrado_em")), "registro_id": ap.get("registro_id"), "trecho": ap.get("trecho"),
            "voltou": voltou[:5]}


# ================================================================ intenção do mês

def _intencao(banco: Any, m: str) -> dict | None:
    linhas = banco.consultar("SELECT * FROM intencoes WHERE mes = ?", (m,))
    return linhas[0] if linhas else None


def sugestoes_de_intencao(banco: Any, ref: date) -> list[dict]:
    """Intenções sugeridas pelos dados dos últimos 30 dias, cada uma com o porquê (citando). Sem
    dados, ideias gerais marcadas como tais (não vêm dos seus registros)."""
    fora = _fora_outros(banco)
    desde = ref - timedelta(days=30)
    saida: list[dict] = []
    if fora is not None:
        estados = _estados_dela(banco, desde, ref, fora)
        preocupacoes = [e for e in estados if _preocupacao(e)]
        alegrias = [e for e in estados if _alegria(e) and not _preocupacao(e)]
        if len(preocupacoes) >= 2 and len(alegrias) * 2 < len(preocupacoes):
            saida.append({"id": "alegrias", "texto": "Notar o que a deixa feliz",
                          "porque": f"Nos últimos 30 dias você registrou {_plural(len(preocupacoes), 'preocupação', 'preocupações')}"
                                    f" e {_plural(len(alegrias), 'alegria', 'alegrias')} dela.",
                          "registro_ids": sorted({e["registro_id"] for e in preocupacoes})[:5]})
        recentes = [e for e in preocupacoes if (ref - e["_quando"]).days <= 7]
        if recentes:
            e = max(recentes, key=lambda x: (x["_quando"], x["id"]))
            saida.append({"id": "momento", "texto": "Estar perto nos dias difíceis dela",
                          "porque": f"{_cap(_quando(e['_quando'], ref))} você registrou: “{e['trecho']}”",
                          "registro_ids": [e["registro_id"]]})
        apoios = [a for a in _apoios(banco, fora) if a["_pos"] and (a.get("polaridade") or 0) > 0]
        if apoios:
            ap = max(apoios, key=lambda a: (a["certeza"] == "confirmado", a["_data"] or ref, a["id"]))
            combinou = any(a["combina"] for a in atitudes_cruzadas(banco, ref, desde, ref, fora))
            if not combinou:
                saida.append({"id": "apoio", "texto": f"Praticar o que ela disse que ajuda: “{ap['resumo']}”",
                              "porque": (f"Você registrou {_quando(ap['_data'], ref)}: “{ap['trecho']}”" if ap["_data"]
                                         else f"Você registrou: “{ap['trecho']}”"),
                              "registro_ids": [ap["registro_id"]]})
    antigas = [p for p in _promessas(banco) if p.get("status") == "aberta" and p["_feita"] and (ref - p["_feita"]).days > 14]
    if antigas:
        p = min(antigas, key=lambda x: (x["_feita"], x["id"]))
        saida.append({"id": "promessa", "texto": "Tirar do papel uma promessa que está esperando",
                      "porque": f"“{p['texto']}” está em aberto desde {_dia(p['_feita'])}.",
                      "registro_ids": [p["registro_id"]] if p.get("registro_id") else []})
    pendentes = [r for r in _linhas(banco, "reparos", "data >= ? AND (conversamos IS NULL OR conversamos = 0)",
                                    ((ref - timedelta(days=60)).isoformat(),), ordem="data DESC")
                 if (ref - (_d(r["data"]) or ref)).days >= DIAS_ACOMPANHAR[0]]
    if pendentes:
        r = pendentes[0]
        saida.append({"id": "reparo", "texto": "Conversar com calma sobre o que ficou pendente",
                      "porque": f"O desentendimento de {_dia(_d(r['data']) or ref)} ainda está sem conversa anotada.",
                      "registro_ids": [r["registro_id"]] if r.get("registro_id") else []})
    pres = _presencas(banco)[-2:]
    if len(pres) == 2 and all(v <= 2 for _, v in pres):
        saida.append({"id": "presenca", "texto": "Estar presente de verdade nos momentos juntos",
                      "porque": f"Nos seus últimos check-ins você marcou {_EMOJI[pres[0][1]]} e {_EMOJI[pres[1][1]]}.",
                      "registro_ids": []})
    if _existe(banco, "perguntas_feitas") and not _perguntas_feitas(banco, desde, ref) and \
            banco.consultar("SELECT COUNT(*) AS n FROM registros")[0]["n"] >= 5:
        saida.append({"id": "perguntas", "texto": "Conhecer um lado novo dela, com uma pergunta profunda",
                      "porque": "Nos últimos 30 dias não há pergunta profunda registrada.", "registro_ids": []})
    for s in saida:
        s["de_dados"] = True
    if not saida:
        saida = [{"id": f"geral_{i}", "texto": t, "porque": "Uma ideia geral: não vem dos seus registros.",
                  "registro_ids": [], "de_dados": False}
                 for i, t in enumerate(("Escutar mais do que falar", "Um gesto pequeno por semana",
                                        "Notar o que a deixa feliz"), 1)]
    return saida[:4]


# ================================================================ espelho

def _linha(chave: str, emoji: str, texto: str, detalhes: list[dict] | None = None, registro_ids: Iterable[Any] = (),
           pergunta: bool = False) -> dict:
    ids = sorted({i for i in registro_ids if isinstance(i, int) and not isinstance(i, bool)})
    return {"chave": chave, "emoji": emoji, "texto": texto, "detalhes": detalhes or [], "registro_ids": ids,
            "pergunta": pergunta}


_RE_VOLTOU_AO_ASSUNTO = re.compile(r"(?<![\w])(?:perguntei|pergunto|mandei\s+mensagem|liguei|lembrei\s+de\s+perguntar)(?![\w])")


def _acompanhamento(banco: Any, inicio: date, fim: date, ref: date, fora: set[int] | None) -> list[dict]:
    """Pendências dela (e dias importantes dela que passaram) e se você voltou ao assunto: marcou
    como resolvido, ou registrou depois que perguntou ("ela contou da entrevista no dia 12; você
    anotou no dia 15 que perguntou")."""
    if fora is None:
        return []
    alvos = [it for it in _itens(banco, inicio, fim, ("pendencia",)) if it["registro_id"] not in fora]
    alvos += [it for it in _itens(banco, inicio - timedelta(days=90), fim, ("evento",))
              if it["registro_id"] not in fora and _dela(it) and (it.get("importancia") or 0) >= 3 and it["_evento"]
              and inicio <= it["_evento"] <= fim and it["_evento"] < ref]
    vistos: set[int] = set()
    saida = []
    divididos: dict[int, _Analisado] = {}           # cada registro é dividido uma vez só
    for it in sorted(alvos, key=lambda x: (x["_evento"] or x["_data"], x["id"])):
        if it["id"] in vistos or len(saida) >= 8:
            continue
        vistos.add(it["id"])
        base = it["_evento"] if it["tipo"] == "evento" else it["_data"]
        chaves = {t for t in _termos(f"{it['resumo']} {it.get('alvo') or ''}") if t not in _GENERICOS and len(t) >= 4}
        retorno = None
        if it.get("status") == "resolvido" and _d(it.get("atualizado_em")):
            retorno = {"tipo": "resolvido", "data": _d(it["atualizado_em"]), "registro_id": None, "trecho": None}
        if retorno is None and chaves:
            depois = banco.consultar(
                "SELECT id, data_registro, texto FROM registros WHERE data_registro >= ? AND data_registro <= ? AND id != ?"
                " ORDER BY data_registro, id LIMIT 300",
                (base.isoformat(), (base + timedelta(days=DIAS_ACOMPANHAMENTO)).isoformat(), it["registro_id"]))
            for r in depois:
                if r["id"] not in divididos:
                    divididos[r["id"]] = _analisar(r["texto"])
                analisado = divididos[r["id"]]
                fr = None
                for f, termos_frase in zip(analisado.frases, analisado.termos):
                    m = _RE_VOLTOU_AO_ASSUNTO.search(f.plano)
                    if m and _frase_valida(f) and not _negado(f, m.start()) and \
                            any(_mesmo_termo(c, t) for c in chaves for t in termos_frase):
                        fr = f
                        break
                if fr is not None:
                    retorno = {"tipo": "perguntou", "data": _d(r["data_registro"]), "registro_id": r["id"], "trecho": fr.texto}
                    break
        contou = f"Em {_dia(it['_data'])} você registrou: “{it['trecho']}”"
        if retorno and retorno["tipo"] == "perguntou":
            texto = f"{contou}; em {_dia(retorno['data'])} você anotou que voltou ao assunto."
        elif retorno:
            texto = f"{contou}; você marcou como resolvido em {_dia(retorno['data'])}."
        else:
            texto = f"{contou}; ainda não há registro de que vocês voltaram ao assunto."
        saida.append({"texto": texto, "item_id": it["id"], "registro_id": it["registro_id"], "retorno": retorno,
                      "acompanhou": retorno is not None})
    return saida[:8]


def espelho(banco: Any, periodo: str, ref: date) -> dict:
    """Observações do período, cada uma citando a origem. Nada de nota: são fatos dos registros."""
    tipo, inicio, fim = limites(periodo)
    fora = _fora_outros(banco)
    linhas: list[dict] = []
    # Promessas feitas × cumpridas.
    promessas = _promessas(banco)
    novas = [p for p in promessas if p["_feita"] and inicio <= p["_feita"] <= fim]
    cumpridas = [p for p in promessas if p.get("status") == "cumprida" and p["_concluida"] and inicio <= p["_concluida"] <= fim]
    if novas or cumpridas:
        partes = []
        if novas:
            partes.append(_plural(len(novas), "promessa nova", "promessas novas"))
        if cumpridas:
            partes.append(_plural(len(cumpridas), "cumprida", "cumpridas") + " 💙")
        ids_cumpridas = {p["id"] for p in cumpridas}
        linhas.append(_linha("promessas", "🤝", "Promessas: " + "; ".join(partes) + ".",
                             [{"texto": p["texto"], "situacao": "cumprida" if p["id"] in ids_cumpridas else p.get("status"),
                               "registro_id": p.get("registro_id"),
                               "data": p["_concluida"] if p["id"] in ids_cumpridas else p["_feita"]}
                              for p in (cumpridas + [n for n in novas if n["id"] not in ids_cumpridas])[:8]],
                             [p.get("registro_id") for p in novas + cumpridas]))
    # Acompanhamento das pendências dela.
    acomp = _acompanhamento(banco, inicio, fim, ref, fora)
    if acomp:
        voltou = sum(1 for a in acomp if a["acompanhou"])
        linhas.append(_linha("acompanhamento", "🔁",
                             f"Assuntos dela para acompanhar: {len(acomp)}" + (f"; você voltou a {voltou} 💙." if voltou else "."),
                             [{"texto": a["texto"], "registro_id": a["registro_id"],
                               "retorno_registro_id": (a["retorno"] or {}).get("registro_id")} for a in acomp],
                             [a["registro_id"] for a in acomp] + [(a["retorno"] or {}).get("registro_id") for a in acomp]))
    # Equilíbrio: preocupações × alegrias registradas.
    if fora is not None:
        estados = _estados_dela(banco, inicio, fim, fora)
        preoc = [e for e in estados if _preocupacao(e)]
        alegr = [e for e in estados if _alegria(e) and not _preocupacao(e)]
        if preoc or alegr:
            texto = (f"Você registrou {_plural(len(preoc), 'preocupação', 'preocupações')} e "
                     f"{_plural(len(alegr), 'alegria', 'alegrias')} dela.")
            if len(preoc) >= 2 and not alegr:
                texto += " Talvez valha notar também o que a deixa feliz."
            ids_alegres = {e["id"] for e in alegr}
            linhas.append(_linha("equilibrio", "⚖️", texto,
                                 [{"texto": e["trecho"], "registro_id": e["registro_id"], "data": e["_quando"],
                                   "lado": "alegria" if e["id"] in ids_alegres else "preocupacao", "certeza": e["certeza"]}
                                  for e in sorted(estados, key=lambda x: (x["_quando"], x["id"]))[:10]],
                                 [e["registro_id"] for e in estados]))
    # Antecedência nas datas (o que aconteceu no período e quando foi anotado).
    if fora is not None:
        datas = [it for it in _ativos(_itens(banco, inicio - timedelta(days=365), fim, ("evento", "data_importante")))
                 if it["registro_id"] not in fora and it["_evento"] and inicio <= it["_evento"] <= fim
                 and it.get("precisao") in (None, "dia") and it["_data"] and it["_data"] <= it["_evento"]]
        if datas:
            vistos, detalhes = set(), []
            for it in sorted(datas, key=lambda x: (x["_evento"], x["id"])):
                chave = (normalizar(it["resumo"]), it["_evento"])
                if chave in vistos:
                    continue
                vistos.add(chave)
                dias = (it["_evento"] - it["_data"]).days
                antes = "no próprio dia" if dias == 0 else f"{_plural(dias, 'dia', 'dias')} antes"
                detalhes.append({"texto": f"“{it['resumo']}” ({_dia(it['_evento'])}) estava anotado desde "
                                          f"{_dia(it['_data'])} ({antes}).", "registro_id": it["registro_id"], "dias": dias})
            antes = sum(1 for d in detalhes if d["dias"] > 0)
            # "período" é palavra do 🌙 no app: aqui é "desta semana" / "deste mês" (revisão de 07/10).
            deste = "desta semana" if tipo == "semana" else "deste mês"
            if len(detalhes) == 1:
                texto = ("Esta data estava anotada com antecedência." if antes
                         else "Esta data foi anotada no próprio dia.")
            else:
                texto = f"{antes} de {len(detalhes)} datas {deste} estavam anotadas com antecedência."
            linhas.append(_linha("antecedencia", "📅", texto, detalhes[:8], [d["registro_id"] for d in detalhes]))
    # Perguntas profundas feitas.
    feitas = _perguntas_feitas(banco, inicio, fim)
    if feitas:
        profundas = [f for f in feitas if not f["nos_dois"]]
        nos_dois = [f for f in feitas if f["nos_dois"]]
        partes = []
        if profundas:
            partes.append(_plural(len(profundas), "pergunta profunda", "perguntas profundas"))
        if nos_dois:
            partes.append(_plural(len(nos_dois), "pergunta sobre vocês dois", "perguntas sobre vocês dois"))
        linhas.append(_linha("perguntas", "💬", "Você fez " + " e ".join(partes) + ".",
                             [{"texto": f["pergunta"], "registro_id": f["registro_id"], "data": f["feita_em"]} for f in feitas[:8]],
                             [f["registro_id"] for f in feitas]))
    # Atitudes (e as que combinam com o que ela disse que ajuda).
    atitudes = atitudes_cruzadas(banco, ref, inicio, fim, fora)
    if atitudes:
        celebrar = [a for a in atitudes if a["jeito"] not in _JEITOS_NEUTROS]
        combinam = [a for a in atitudes if a["combina"]]
        texto = (f"Você anotou {_plural(len(celebrar), 'atitude de cuidado', 'atitudes de cuidado')}" if celebrar
                 else f"Você anotou {_plural(len(atitudes), 'atitude', 'atitudes')}")
        if combinam:
            texto += f"; {len(combinam)} {'combina' if len(combinam) == 1 else 'combinam'} com o que ela disse que ajuda 💙"
        linhas.append(_linha("atitudes", "🫶", texto + ".",
                             [{"texto": a["texto"], "registro_id": a["registro_id"], "data": a["data"],
                               "jeito": a["jeito_nome"], "combina": a["combina"][:1]} for a in atitudes[:10]],
                             [a["registro_id"] for a in atitudes] + [c["registro_id"] for a in combinam for c in a["combina"]]))
    # Descompassos (como pergunta).
    for d in descompassos(banco, min(fim, ref), fora):
        linhas.append(_linha("descompasso", "🤔", d["texto"],
                             [{"texto": a["texto"], "registro_id": a["registro_id"], "data": a["data"]} for a in d["atitudes"]],
                             d["registro_ids"], pergunta=True))
    # Desentendimentos e admirações.
    reparos = _linhas(banco, "reparos", "data >= ? AND data <= ?", (inicio.isoformat(), fim.isoformat()), ordem="data, id")
    if reparos:
        conversaram = sum(1 for r in reparos if r["conversamos"] == 1)
        linhas.append(_linha("reparos", "🌧️", _plural(len(reparos), "desentendimento anotado", "desentendimentos anotados")
                             + (f"; vocês conversaram depois em {conversaram}." if conversaram else "."),
                             [{"texto": r["aconteceu"], "registro_id": r.get("registro_id"), "data": _d(r["data"])} for r in reparos],
                             [r.get("registro_id") for r in reparos]))
    admiracoes = _linhas(banco, "admiracoes", "data >= ? AND data <= ?", (inicio.isoformat(), fim.isoformat()), ordem="data, id")
    if admiracoes:
        linhas.append(_linha("admiracoes", "✨", f"{_plural(len(admiracoes), 'coisa', 'coisas')} que você admira nela.",
                             [{"texto": a["texto"], "registro_id": a.get("registro_id"), "data": _d(a["data"])} for a in admiracoes],
                             [a.get("registro_id") for a in admiracoes]))
    # Check-ins.
    semanas = [semana(inicio)] if tipo == "semana" else _semanas_do_mes(periodo)
    feitos = [c for c in banco.consultar("SELECT periodo, presenca FROM checkins WHERE tipo = 'semanal'") if c["periodo"] in semanas]
    if feitos:
        linhas.append(_linha("checkins", "🪞", "Check-in feito." if tipo == "semana" else
                             f"Check-ins: {len(feitos)} de {len(semanas)} semanas.",
                             [{"texto": rotulo_periodo(c["periodo"]), "emoji": _EMOJI.get(c["presenca"])}
                              for c in sorted(feitos, key=lambda x: x["periodo"])]))
    return {"periodo": periodo, "tipo": tipo, "rotulo": rotulo_periodo(periodo), "inicio": inicio, "fim": fim,
            "linhas": linhas, "vazio": AVISO_ESPELHO_VAZIO if not linhas else None,
            "tendencia": tendencia(banco, periodo, ref) if tipo == "mes" else []}


def tendencia(banco: Any, m: str, ref: date, meses: int = 3) -> list[str]:
    """"Atitudes de cuidado anotadas: em agosto, 2; em setembro, 5; em outubro, 3." Só fatos."""
    fora = _fora_outros(banco)
    lista = [m]
    for _ in range(meses - 1):
        lista.insert(0, _mes_anterior(lista[0]))
    lista = [mm for mm in lista if limites(mm)[1] <= ref]
    medidas: dict[str, list[int]] = {"Atitudes de cuidado anotadas": [], "Alegrias dela registradas": [],
                                     "Preocupações dela registradas": [], "Promessas cumpridas": [],
                                     "Perguntas profundas feitas": []}
    promessas = _promessas(banco)
    for mm in lista:
        _, ini, fim = limites(mm)
        atit = banco.consultar("SELECT jeito FROM atitudes WHERE data >= ? AND data <= ?", (ini.isoformat(), fim.isoformat()))
        medidas["Atitudes de cuidado anotadas"].append(sum(1 for a in atit if a["jeito"] not in _JEITOS_NEUTROS))
        estados = _estados_dela(banco, ini, fim, fora) if fora is not None else []
        medidas["Alegrias dela registradas"].append(sum(1 for e in estados if _alegria(e) and not _preocupacao(e)))
        medidas["Preocupações dela registradas"].append(sum(1 for e in estados if _preocupacao(e)))
        medidas["Promessas cumpridas"].append(sum(1 for p in promessas if p.get("status") == "cumprida" and p["_concluida"]
                                                  and ini <= p["_concluida"] <= fim))
        medidas["Perguntas profundas feitas"].append(len(_perguntas_feitas(banco, ini, fim)))
    if len(lista) < 2:
        return []
    return [f"{nome}: " + "; ".join(f"em {_nome_mes(mm)}, {v}" for mm, v in zip(lista, valores)) + "."
            for nome, valores in medidas.items() if any(valores)]


# ================================================================ o que ela diz (lado a lado)

_RE_TEMA_ELA = (
    ("cuidado", re.compile(r"cuidad|gesto|diferenca|contar comigo|amada|apoio")),
    ("melhorar", re.compile(r"fazer melhor|diferente|incomoda|precisa de mim|sente falta|tirando o seu sono|discorda|"
                            r"nao tem pedido|melhor por voce")),
    ("momentos", re.compile(r"momento|caixinha|rir|favorita|historia|tradic|time|melhor conversa|fazemos bem|mais vezes|"
                            r"sonho|guardaria")),
    ("mes", re.compile(r"semanas|entre a gente|te escuto")),
)


def _tema_da_pergunta_dela(texto: str | None) -> str:
    p = plano(texto or "")
    for tema, padrao in _RE_TEMA_ELA:
        if padrao.search(p):
            return tema
    return "outros"


def lado_a_lado(banco: Any, m: str) -> dict:
    """"O que eu achei" (seus check-ins do mês) × "o que ela disse" (as respostas "Nós dois" do mês,
    🟢 quando ela disse). Pares pelo tema; o resto fica em cada lado."""
    _, inicio, fim = limites(m)
    eu_lado = []
    semanas = set(_semanas_do_mes(m))
    for c in banco.consultar("SELECT * FROM checkins ORDER BY periodo, id"):
        if not ((c["tipo"] == "semanal" and c["periodo"] in semanas) or (c["tipo"] == "mensal" and c["periodo"] == m)):
            continue
        for p in _json(c["respostas"], {}).get("perguntas", []):
            if isinstance(p, dict) and p.get("resposta"):
                eu_lado.append({**_enriquecer(banco, p), "periodo": c["periodo"], "rotulo": rotulo_periodo(c["periodo"])})
    ela_lado = [{"pergunta": f["pergunta"], "resposta": f["resposta"], "certeza": f["certeza"], "data": f["feita_em"],
                 "registro_id": f["registro_id"], "recusou": f["recusou"], "tema": _tema_da_pergunta_dela(f["pergunta"])}
                for f in _perguntas_feitas(banco, inicio, fim) if f["nos_dois"]]
    pares = []
    for tema in ("cuidado", "melhorar", "momentos", "mes"):
        meus = [x for x in eu_lado if x["tema"] == tema]
        dela = [x for x in ela_lado if x["tema"] == tema]
        if meus and dela:
            pares.append({"tema": tema, "titulo": _TEMAS[tema], "eu": meus, "ela": dela})
    temas_pares = {p["tema"] for p in pares}
    return {"pares": pares, "eu": [x for x in eu_lado if x["tema"] not in temas_pares],
            "ela": [x for x in ela_lado if x["tema"] not in temas_pares],
            "sugestao_pergunta": None if ela_lado else _pergunta_nos_dois_sugerida()}


def _pergunta_nos_dois_sugerida() -> dict:
    """Uma pergunta "Nós dois" para fazer a ela (do banco do recurso perguntas, se ele estiver aqui)."""
    modulo = recurso("perguntas")
    textos: list[str] = []
    if modulo is not None:
        try:
            textos = [p.texto for p in getattr(modulo, "PERGUNTAS", ()) if modulo.trilha(p) == "nos_dois"]
        except Exception:  # noqa: BLE001
            textos = []
    texto = next((t for t in textos if "cuidada" in t), None) or (textos[0] if textos else
                                                                   "O que te fez se sentir cuidada este mês?")
    return {"texto": texto, "porque": "Ainda não há resposta dela sobre vocês dois neste mês. Talvez perguntar, com calma, "
                                      "num momento bom.", "possibilidade": POSSIBILIDADE}


# ================================================================ cartas

def _forte(itens_alvo: list[dict]) -> bool:
    return any(i["certeza"] == "confirmado" for i in itens_alvo) or sum(1 for i in itens_alvo if i["certeza"] == "observado") >= 2


def blocos_de_carta(banco: Any, ocasiao: Any, ref: date) -> list[dict]:
    """O que dá para lembrar numa carta, só do que foi registrado: marcos, frases dela 🟢, do que ela
    gosta (gostos fortes), momentos felizes, admirações e, no pedido de desculpas, o que você anotou
    que faria diferente. Nada do ciclo."""
    if ocasiao not in OCASIOES:
        raise ValueError(OCASIAO_INVALIDA)
    fora = _fora_outros(banco)
    blocos: list[dict] = []
    if fora is not None:
        cols = _colunas(banco, "marcos") if _existe(banco, "marcos") else set()
        if {"id", "titulo", "data"} <= cols:
            pedir = [c for c in ("id", "titulo", "data", "tipo", "descricao", "registro_id") if c in cols]
            for mc in banco.consultar(f"SELECT {', '.join(pedir)} FROM marcos ORDER BY data DESC LIMIT 30"):
                if mc.get("registro_id") in fora:
                    continue
                blocos.append({"id": f"marco:{mc['id']}", "tipo": "marco", "titulo": "Um marco de vocês",
                               "texto": mc["titulo"], "data": _d(mc["data"]), "registro_id": mc.get("registro_id")})
        itens = [i for i in _ativos(_itens(banco)) if _dela(i) and i["registro_id"] not in fora]
        frases_dela = [i for i in itens if i["certeza"] == "confirmado"][:15]
        for i in frases_dela:
            blocos.append({"id": f"frase:{i['id']}", "tipo": "frase", "titulo": "Algo que ela disse", "texto": i["resumo"],
                           "trecho": i["trecho"], "certeza": "confirmado", "data": i["_data"], "registro_id": i["registro_id"]})
        # Gostos parecidos ("tulipas" e "tulipas amarelas") contam juntos, como no resto do app (≥ 88).
        grupos: list[list[dict]] = []
        for i in itens:
            if i["tipo"] == "preferencia" and (i.get("polaridade") or 0) > 0 and i.get("alvo"):
                grupo = next((g for g in grupos if semelhanca(g[0]["alvo"], i["alvo"]) >= 88), None)
                if grupo is None:
                    grupos.append([i])
                else:
                    grupo.append(i)
        for grupo in grupos[:30]:
            if _forte(grupo):
                i = grupo[0]
                blocos.append({"id": f"gosta:{i['id']}", "tipo": "gosta", "titulo": "Do que ela gosta", "texto": i["alvo"],
                               "trecho": i["trecho"],
                               "certeza": "confirmado" if any(x["certeza"] == "confirmado" for x in grupo) else "observado",
                               "data": i["_data"], "registro_id": i["registro_id"]})
        usados = {(b.get("registro_id"), b.get("trecho")) for b in blocos if b["tipo"] == "frase"}
        felizes = [i for i in itens if (i["tipo"] == "estado" and (i.get("polaridade") or 0) > 0)
                   or "Memórias compartilhadas" in _categorias(i)
                   or (i["tipo"] == "preferencia" and "Coisas que deixam feliz" in _categorias(i))][:12]
        for i in felizes:
            if (i["registro_id"], i["trecho"]) in usados:
                continue
            blocos.append({"id": f"momento:{i['id']}", "tipo": "momento", "titulo": "Um momento feliz", "texto": i["resumo"],
                           "trecho": i["trecho"], "certeza": i["certeza"], "data": i["_evento"] or i["_data"],
                           "registro_id": i["registro_id"]})
    for a in _linhas(banco, "admiracoes", ordem="data DESC, id DESC")[:20]:
        if fora is None and a.get("registro_id"):
            continue
        blocos.append({"id": f"admiracao:{a['id']}", "tipo": "admiracao", "titulo": "O que você admira nela",
                       "texto": a["texto"], "data": _d(a["data"]), "registro_id": a.get("registro_id")})
    if ocasiao == "desculpas":
        for r in _linhas(banco, "reparos", "data >= ?", ((ref - timedelta(days=60)).isoformat(),), ordem="data DESC, id DESC")[:5]:
            blocos.append({"id": f"reparo:{r['id']}", "tipo": "reparo", "titulo": "O que você anotou que faria diferente",
                           "texto": r["faria_diferente"] or r["aconteceu"], "data": _d(r["data"]),
                           "registro_id": r.get("registro_id")})
    ordem = {"aniversario_dela": ("admiracao", "gosta", "frase", "momento", "marco"),
             "namoro": ("marco", "momento", "frase", "admiracao", "gosta"),
             "mesversario": ("momento", "marco", "frase", "admiracao", "gosta"),
             "desculpas": ("reparo", "admiracao", "frase", "momento", "marco", "gosta")}.get(
        ocasiao, ("momento", "admiracao", "frase", "gosta", "marco"))
    blocos.sort(key=lambda b: ordem.index(b["tipo"]) if b["tipo"] in ordem else 9)
    return blocos


_ABERTURAS = {
    "aniversario_dela": "Feliz aniversário, {nome}!", "namoro": "Feliz aniversário de namoro, {nome}!",
    "mesversario": "Mais um mês com você, {nome}.", "namorados": "Feliz Dia dos Namorados, {nome}!",
    "natal": "Feliz Natal, {nome}!", "desculpas": "{nome}, queria te dizer uma coisa com calma.",
    "sem_motivo": "{nome}, só passei para te dizer uma coisa.",
}
_MODELOS = {
    "marco": "Lembro de quando… ({texto}, {data})",
    "frase": "Lembro de quando você me disse… (você anotou: “{trecho}”, {data})",
    "gosta": "Sei o quanto você gosta de {texto}.",
    "momento": "Lembro de quando… ({texto}, {data})",
    "admiracao": "Uma coisa que eu admiro em você… (você anotou: “{texto}”)",
    "reparo": "Sobre o que aconteceu em {data}… (você anotou: “{texto}”)",
}


def esqueleto(banco: Any, ocasiao: Any, escolhidos: Any, ref: date) -> dict:
    """O esqueleto só com os blocos escolhidos, relidos do diário (nunca o texto que a tela mandou):
    frases-modelo ("Lembro de quando…") com o que você registrou entre parênteses, para reescrever."""
    if not isinstance(escolhidos, list) or not all(isinstance(x, str) for x in escolhidos) or len(escolhidos) > 40:
        raise ValueError(BLOCO_INVALIDO)
    disponiveis = {b["id"]: b for b in blocos_de_carta(banco, ocasiao, ref)}
    if any(x not in disponiveis for x in escolhidos):
        raise ValueError(BLOCO_INVALIDO)
    nome = (banco.config().get("nome_dela") or "").strip() or "meu amor"
    linhas = [_ABERTURAS[ocasiao].format(nome=nome), ""]
    usados = []
    for bid in dict.fromkeys(escolhidos):
        b = disponiveis[bid]
        data = f"{b['data']:%d/%m/%Y}" if isinstance(b.get("data"), date) else "sem data"
        linhas.extend([_MODELOS[b["tipo"]].format(texto=b["texto"], trecho=b.get("trecho") or b["texto"], data=data), ""])
        usados.append({"id": bid, "registro_id": b.get("registro_id")})
    linhas.append("Com carinho, e querendo fazer melhor," if ocasiao == "desculpas" else "Com todo o meu carinho,")
    return {"ocasiao": ocasiao, "texto": "\n".join(linhas), "blocos": usados}


def _vista_carta(c: dict) -> dict:
    return {**c, "blocos": _json(c["blocos"], []), "ocasiao_nome": OCASIOES.get(c["ocasiao"], c["ocasiao"])}


# ================================================================ visão geral

def visao_geral(banco: Any, ref: date) -> dict:
    fora = _fora_outros(banco)
    periodo = semana_do_checkin(ref)
    m_reflexao = mes_da_reflexao(ref)
    m_atual = mes(ref)
    feito = _checkin(banco, "semanal", periodo)
    intencao = _intencao(banco, m_atual)
    reparos = [_vista_reparo(r, ref) for r in _linhas(banco, "reparos", ordem="data DESC, id DESC")[:30]]
    aprendizados = _linhas(banco, "aprendizados", ordem="id DESC")
    recentes = _registros_recentes(banco, ref - timedelta(days=30), ref, fora) if aprendizados else []
    return {
        "hoje": ref,
        "semana": {"periodo": periodo, "rotulo": rotulo_periodo(periodo), "feito": bool(feito),
                   "emoji": _EMOJI.get(feito["presenca"]) if feito else None,
                   "perguntas": [p.para_dict() for p in perguntas_da_semana(banco, periodo, ref)]},
        "mes": {"mes": m_reflexao, "rotulo": rotulo_periodo(m_reflexao), "feito": bool(_checkin(banco, "mensal", m_reflexao))},
        "intencao": {"mes": m_atual, "rotulo": rotulo_periodo(m_atual), "atual": intencao,
                     "sugestoes": [] if intencao else sugestoes_de_intencao(banco, ref)},
        "momento_dela": _momento_dela(banco, ref, fora),
        "espelho_semana": espelho(banco, semana(ref), ref)["linhas"][:6],
        "atitudes": atitudes_cruzadas(banco, ref, ref - timedelta(days=60), ref, fora)[:10],
        "descompassos": descompassos(banco, ref, fora),
        "reparos": {"acompanhar": [r for r in reparos if r["acompanhar"]], "temas": temas_repetidos(banco, ref),
                    "intensos": intensos(banco, ref), "total": len(reparos)},
        "aprendizados": [_vista_aprendizado(a, ref, recentes) for a in aprendizados[:5]],
        "total_aprendizados": len(aprendizados),
        "admiracoes": [{**a, "data": _d(a["data"])} for a in _linhas(banco, "admiracoes", ordem="data DESC, id DESC")[:5]],
        "cuidar": cuidar_de_voce(banco, ref),
        "presencas": [{"periodo": p, "rotulo": rotulo_periodo(p), "emoji": _EMOJI[v]} for p, v in _presencas(banco)[-8:]],
    }


def vista_mes(banco: Any, m: str, ref: date) -> dict:
    _conferir_periodo("mensal", m, ref, para_ver=True)
    seguinte = _mes_seguinte(m)
    return {"mes": m, "rotulo": rotulo_periodo(m), "anterior": _mes_anterior(m),
            "seguinte": seguinte if limites(seguinte)[1] <= ref else None,
            "reflexao": _vista_checkin(banco, "mensal", m, ref), "espelho": espelho(banco, m, ref),
            "lado_a_lado": lado_a_lado(banco, m), "intencao": _intencao(banco, m),
            "pode_responder": (ref - limites(m)[2]).days <= 31 * MESES_REFLEXAO}


# ================================================================ cartões do Hoje

def _lembretes_ligados(banco: Any) -> bool:
    valor = banco.config().get(CHAVE_LEMBRETES)
    return valor if isinstance(valor, bool) else True


def _usa_o_app(banco: Any) -> bool:
    return banco.consultar("SELECT COUNT(*) AS n FROM registros")[0]["n"] >= 3


def _usa_a_area(banco: Any) -> bool:
    return any(banco.consultar(f"SELECT 1 FROM {t} LIMIT 1") for t in TABELAS)


def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """Check-in e reflexão (lembretes discretos), intenção, acompanhamento de um desentendimento,
    tema repetido, desentendimentos intensos, aprendizado que casa com hoje, cuidar de você e carta
    para uma ocasião próxima. O centro mostra no máximo 1 sugestão daqui."""
    _lembrar_diario(banco)
    ref = referencia
    saida: list[dict] = []
    lembretes = _lembretes_ligados(banco)
    fora = _fora_outros(banco)
    if lembretes and _usa_o_app(banco):
        # Check-in da semana: de sexta a terça.
        periodo = semana_do_checkin(ref)
        if (ref.weekday() >= 4 or ref.weekday() <= 1) and not _checkin(banco, "semanal", periodo):
            perguntas = perguntas_da_semana(banco, periodo, ref)
            ganchos = [p for p in perguntas if p.id not in _FIXAS_SEMANA]
            saida.append(cartao(f"checkin:{periodo}:", "lembrete",
                                f"Que tal um minuto para você? O check-in da semana tem {len(perguntas)} perguntas curtas.",
                                ("Escolhidas pela sua semana: " + " ".join(p.texto for p in ganchos)) if ganchos
                                else "Uma coisa boa que você fez e algo que faria diferente. Só seu: nada daqui vai para a IA.",
                                RELEVANCIA_CHECKIN, tipo="eu_checkin", dados={"periodo": periodo, "rotulo": rotulo_periodo(periodo)}))
        # Reflexão do mês: nos últimos 3 dias do mês e nos 5 primeiros do seguinte.
        m = mes_da_reflexao(ref)
        _, _, fim = limites(m)
        ultimo_dia = calendar.monthrange(ref.year, ref.month)[1]
        if (ref.day >= ultimo_dia - 2 or ref.day <= 5) and fim - timedelta(days=2) <= ref and not _checkin(banco, "mensal", m):
            saida.append(cartao(f"mes:{m}:", "lembrete",
                                f"{_cap(_nome_mes(m))} {'está terminando' if mes(ref) == m else 'terminou'}: que tal a reflexão do mês?",
                                "O espelho do mês e, lado a lado, o que ela disse sobre vocês.",
                                RELEVANCIA_MES, tipo="eu_mes", dados={"mes": m, "rotulo": rotulo_periodo(m)}))
    # Intenção do mês: lembrete gentil no meio; no começo, um convite (só para quem já usa a área).
    m_atual = mes(ref)
    intencao = _intencao(banco, m_atual)
    if lembretes and intencao and 14 <= ref.day <= 17 and not intencao.get("revisao"):
        escolhida = _d(intencao.get("criado_em"))
        saida.append(cartao(f"intencao:{m_atual}:meio:", "sugestao", f"Sua intenção deste mês: “{intencao['texto']}”. Como está indo?",
                            (f"Você escolheu essa intenção em {_dia(escolhida)}. " if escolhida else "") + POSSIBILIDADE,
                            RELEVANCIA_INTENCAO_MEIO, tipo="eu_intencao", dados={"id": intencao["id"], "mes": m_atual}))
    elif lembretes and not intencao and ref.day <= 5 and _usa_a_area(banco):
        sugestoes = [s for s in sugestoes_de_intencao(banco, ref) if s.get("de_dados")]
        if sugestoes:
            s = sugestoes[0]
            saida.append(cartao(f"intencao:{m_atual}:escolher:", "sugestao",
                                f"Talvez escolher uma intenção para {_nome_mes(m_atual)}? Uma ideia: “{s['texto']}”.",
                                s["porque"], RELEVANCIA_INTENCAO_ESCOLHER, registro_ids=s["registro_ids"], tipo="eu_intencao",
                                dados={"mes": m_atual, "sugestao": s["id"], "texto": s["texto"]}))
    # Desentendimento: "vocês conversaram sobre aquilo?", tema repetido e intensos.
    for r in _linhas(banco, "reparos", "conversamos IS NULL AND acompanhado_em IS NULL AND data >= ? AND data <= ?",
                     ((ref - timedelta(days=DIAS_ACOMPANHAR[1])).isoformat(),
                      (ref - timedelta(days=DIAS_ACOMPANHAR[0])).isoformat()), ordem="data DESC, id DESC")[:1]:
        d = _d(r["data"]) or ref
        saida.append(cartao(f"reparo:p{r['id']}:conversaram:", "sugestao", f"Vocês conversaram sobre aquilo {_de_quando(d, ref)}?",
                            f"Você anotou um desentendimento {_quando(d, ref)}"
                            + (f" (tema: {r['tema']})" if r.get("tema") else "")
                            + ". Se ainda não conversaram, talvez um momento calmo ajude.",
                            RELEVANCIA_REPARO, registro_ids=[r["registro_id"]] if r.get("registro_id") else [],
                            tipo="eu_reparo", dados={"id": r["id"]}))
    for t in temas_repetidos(banco, ref)[:1]:
        saida.append(cartao(f"tema:p{t['ultimo_id']}:n{t['vezes']}:", "sugestao", t["texto"], t["porque"], RELEVANCIA_TEMA,
                            registro_ids=t["registro_ids"][:5], tipo="eu_tema", dados={"tema": t["tema"], "vezes": t["vezes"]}))
    forte = intensos(banco, ref)
    if forte:
        saida.append(cartao(f"intensos:p{forte['ultimo_id']}:", "sugestao", forte["texto"], forte["porque"],
                            RELEVANCIA_INTENSOS, registro_ids=forte["registro_ids"][:5], tipo="eu_cuidado_relacao",
                            dados={"vezes": forte["vezes"]}))
    # Aprendizado que casa com o que foi registrado hoje ou ontem.
    saida.extend(_cartoes_aprendizado(banco, ref, fora))
    # Cuidar de você.
    cuidar = cuidar_de_voce(banco, ref)
    if cuidar:
        saida.append(cartao(f"cuidar:{cuidar['periodo']}:", "sugestao", cuidar["texto"], cuidar["porque"], RELEVANCIA_CUIDAR,
                            tipo="eu_cuidar", dados={"semanas": cuidar["semanas"]}))
    # Uma carta para uma ocasião que está chegando.
    saida.extend(_cartao_carta(banco, ref))
    return saida


def _cartoes_aprendizado(banco: Any, ref: date, fora: set[int] | None) -> list[dict]:
    if fora is None:
        return []
    aprendizados = _linhas(banco, "aprendizados", ordem="id")
    if not aprendizados:
        return []
    melhor = None
    for r in _registros_recentes(banco, ref - timedelta(days=1), ref, fora):
        for ap in aprendizados:
            if r["id"] == ap.get("registro_id") or (r["_data"] or ref) < (_d(ap.get("data")) or ref):
                continue
            pontos, motivos, pos = casar_gatilhos(_json(ap["gatilhos"], {}), r["_analisado"], r["_itens"])
            if pontos < LIMIAR_GATILHO or pos is None:
                continue
            fr = next((f for f in r["_analisado"].frases if f.ini == pos), None)
            chave = (pontos, r["_data"], r["id"])
            if melhor is None or chave > melhor[0]:
                melhor = (chave, ap, r, fr, motivos)
    if melhor is None:
        return []
    _, ap, r, fr, motivos = melhor
    citado = fr.texto if fr else r["texto"][:160]
    return [cartao(f"r{r['id']}:ap{ap['id']}:", "sugestao", f"Um aprendizado seu combina com hoje: “{ap['texto']}”",
                   f"{_cap(_quando(r['_data'], ref))} você registrou: “{citado}” ({', '.join(motivos)}). "
                   f"Você anotou esse aprendizado em {_dia(_d(ap['data']) or ref)}.",
                   RELEVANCIA_APRENDIZADO, registro_ids=[r["id"]], tipo="eu_aprendizado",
                   dados={"id": ap["id"], "texto": ap["texto"]})]


def _ocasioes_proximas(banco: Any, ref: date) -> list[tuple[str, date]]:
    config = banco.config()

    def proxima(dia: int, mes_: int) -> date | None:
        for ano in (ref.year, ref.year + 1):
            try:
                d = date(ano, mes_, dia)
            except ValueError:
                continue
            if d >= ref:
                return d
        return None

    saida = []
    aniversario = _d(config.get("aniversario_dela"))
    if aniversario:
        d = proxima(aniversario.day, aniversario.month)
        if d:
            saida.append(("aniversario_dela", d))
    inicio = _d(config.get("inicio_namoro"))
    if inicio:
        d = proxima(inicio.day, inicio.month)
        if d and d.year > inicio.year:
            saida.append(("namoro", d))
    for chave, (dia, mes_) in (("namorados", (12, 6)), ("natal", (25, 12))):
        d = proxima(dia, mes_)
        if d:
            saida.append((chave, d))
    return saida


def _cartao_carta(banco: Any, ref: date) -> list[dict]:
    nomes = {"aniversario_dela": "O aniversário dela", "namoro": "O aniversário de namoro",
             "namorados": "O Dia dos Namorados", "natal": "O Natal"}
    for ocasiao, d in sorted(_ocasioes_proximas(banco, ref), key=lambda x: x[1]):
        dias = (d - ref).days
        if not 3 <= dias <= 14:
            continue
        blocos = blocos_de_carta(banco, ocasiao, ref)
        if len(blocos) < 3:
            continue
        return [cartao(f"carta:{ocasiao}:{d.isoformat()}:", "sugestao", f"{nomes[ocasiao]} está chegando (em {dias} dias). "
                       "Talvez uma carta?", f"Já há {len(blocos)} lembranças registradas para escolher; o esqueleto usa só "
                                            "o que você marcar.", RELEVANCIA_CARTA, data=d, tipo="eu_carta",
                       dados={"ocasiao": ocasiao})]
    return []


# ================================================================ rotas

def _ref(nucleo: Any) -> date:
    try:
        valor = nucleo.hoje()
    except Exception:  # noqa: BLE001
        valor = None
    return valor if isinstance(valor, date) else date.today()


def _com_diario(funcao: Any) -> Any:
    """Toda rota daqui lembra do diário aberto (para a revisão achar os aprendizados)."""
    def chamar(nucleo: Any, pedido: Any, banco: Any) -> Any:
        _lembrar_diario(banco)
        return funcao(nucleo, pedido, banco)
    chamar.__name__ = getattr(funcao, "__name__", "rota")
    return chamar


def _corpo(pedido: Any) -> dict:
    corpo = pedido.corpo
    if not isinstance(corpo, dict):
        raise ValueError("O pedido precisa ser um objeto.")
    return corpo


def _esquecer_chaves(banco: Any, *prefixos: str) -> None:
    for prefixo in prefixos:
        try:
            banco.esquecer_alertas(prefixo)
        except Exception as erro:  # noqa: BLE001
            registrar_erro(erro, NOME, "esquecer alertas")


def _esquecer_chaves_com(banco: Any, pedaco: str) -> None:
    """Chaves dispensadas daqui que contêm `pedaco` (":ap12:"): cada uma sai pelo próprio nome."""
    for l in banco.consultar("SELECT chave FROM alertas_dispensados WHERE substr(chave, 1, 3) = 'eu:'"):
        if pedaco in l["chave"]:
            _esquecer_chaves(banco, l["chave"])


def _apagar(nucleo: Any, banco: Any, tabela: str, linha_id: int, prefixos: Iterable[str] = (),
            pedaco: str | None = None) -> dict:
    banco.apagar(tabela, linha_id)
    prefixos = tuple(prefixos)
    _esquecer_chaves(banco, *prefixos)
    if pedaco:
        _esquecer_chaves_com(banco, pedaco)

    def nas_copias(copia: Any) -> bool:
        try:
            copia.apagar(tabela, linha_id)
        except NaoEncontrado:
            return False
        _esquecer_chaves(copia, *prefixos)
        if pedaco:
            _esquecer_chaves_com(copia, pedaco)
        return True

    nucleo.limpar_copias(nas_copias)
    return {}


def _rota_visao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return visao_geral(banco, _ref(nucleo))


def _rota_checkin(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    periodo = pedido.parametro("periodo") or semana_do_checkin(ref)
    tipo = "mensal" if _RE_MES.fullmatch(periodo) else "semanal"
    _conferir_periodo(tipo, periodo, ref, para_ver=True)
    vista = _vista_checkin(banco, tipo, periodo, ref)
    try:
        _conferir_periodo(tipo, periodo, ref)
        vista["pode_responder"] = True
    except ValueError:
        vista["pode_responder"] = False
    if tipo == "semanal":
        inicio = limites(periodo)[1]
        vista["anterior"] = semana(inicio - timedelta(days=7))
        vista["seguinte"] = semana(inicio + timedelta(days=7)) if inicio + timedelta(days=7) <= ref else None
    return vista


def _rota_salvar_checkin(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return salvar_checkin(banco, _corpo(pedido), _ref(nucleo))


def _rota_checkins(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    saida = []
    for c in banco.consultar("SELECT id, tipo, periodo, presenca, criado_em FROM checkins ORDER BY periodo DESC, id DESC LIMIT 200"):
        try:
            rotulo = rotulo_periodo(c["periodo"])
        except ValueError:
            continue
        saida.append({**c, "rotulo": rotulo, "emoji": _EMOJI.get(c["presenca"])})
    return saida


def _rota_apagar_checkin(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _uma(banco, "checkins", pedido.id)
    prefixo = f"eu:checkin:{c['periodo']}:" if c["tipo"] == "semanal" else f"eu:mes:{c['periodo']}:"
    return _apagar(nucleo, banco, "checkins", pedido.id, (prefixo, f"eu:cuidar:{c['periodo']}:"))


def _rota_mes(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    return vista_mes(banco, pedido.parametro("mes") or mes_da_reflexao(ref), ref)


def _rota_espelho(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    periodo = pedido.parametro("periodo") or semana(ref)
    if limites(periodo)[1] > ref:
        raise ValueError(PERIODO_FUTURO)
    return espelho(banco, periodo, ref)


def _rota_atitudes(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    fora = _fora_outros(banco)
    return {"atitudes": atitudes_cruzadas(banco, ref, fora=fora), "descompassos": descompassos(banco, ref, fora),
            "jeitos": JEITOS}


def _rota_nova_atitude(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) - {"texto", "data", "jeito"}:
        raise ValueError(CAMPOS_INVALIDOS)
    texto = _cap(_texto(c.get("texto"), maximo=300, erro="Escreva o que você fez (até 300 caracteres)."))
    jeito = c.get("jeito")
    if jeito is None:
        alvo = _alvo_ela(banco.config().get("nome_dela"))
        achadas = [a for f in _frases(texto) for a in _atitudes_da_frase(f, alvo, _padroes_atitude(alvo))]
        jeito = achadas[0][2] if achadas else "outro"
    if jeito not in JEITOS:
        raise ValueError("Esse jeito de cuidar não está na lista.")
    _lotado(banco, "atitudes")
    aid = banco.inserir("atitudes", {"texto": texto, "data": _data_pedida(c.get("data"), ref, padrao=ref),
                                     "registro_id": None, "trecho": None, "jeito": jeito, "criado_em": _agora()})
    return {"id": aid, "jeito": jeito}


def _rota_apagar_atitude(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return _apagar(nucleo, banco, "atitudes", pedido.id)


def _rota_aprendizados(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    ref = _ref(nucleo)
    aprendizados = _linhas(banco, "aprendizados", ordem="id DESC")
    recentes = _registros_recentes(banco, ref - timedelta(days=30), ref, _fora_outros(banco)) if aprendizados else []
    return [_vista_aprendizado(a, ref, recentes) for a in aprendizados]


def _rota_aprendizado(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return _vista_aprendizado(_uma(banco, "aprendizados", pedido.id), _ref(nucleo))


def _rota_novo_aprendizado(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) - {"texto"}:
        raise ValueError(CAMPOS_INVALIDOS)
    texto = _cap(_texto(c.get("texto")))
    if any(normalizar(a["texto"]) == normalizar(texto) for a in banco.consultar("SELECT texto FROM aprendizados")):
        raise ValueError("Esse aprendizado já está guardado.")
    _lotado(banco, "aprendizados")
    aid = banco.inserir("aprendizados", {"texto": texto, "gatilhos": gatilhos(texto), "criado_em": _agora(), "data": ref,
                                         "registro_id": None, "trecho": None})
    return _vista_aprendizado(_uma(banco, "aprendizados", aid), ref)


def _rota_editar_aprendizado(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) != {"texto"}:
        raise ValueError(CAMPOS_INVALIDOS)
    _uma(banco, "aprendizados", pedido.id)
    texto = _cap(_texto(c["texto"]))
    banco.atualizar("aprendizados", pedido.id, {"texto": texto, "gatilhos": gatilhos(texto)})
    return _vista_aprendizado(_uma(banco, "aprendizados", pedido.id), ref)


def _rota_lembrei(nucleo: Any, pedido: Any, banco: Any) -> dict:
    _uma(banco, "aprendizados", pedido.id)
    banco.atualizar("aprendizados", pedido.id, {"mostrado_em": _ref(nucleo)}, historico=False)
    return {}


def _rota_apagar_aprendizado(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return _apagar(nucleo, banco, "aprendizados", pedido.id, pedaco=f":ap{pedido.id}:")


def _rota_reparos(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    reparos = [_vista_reparo(r, ref) for r in _linhas(banco, "reparos", ordem="data DESC, id DESC")]
    return {"reparos": reparos, "acompanhar": [r for r in reparos if r["acompanhar"]], "temas": temas_repetidos(banco, ref),
            "intensos": intensos(banco, ref)}


def _rota_novo_reparo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    valores = _campos_reparo(_corpo(pedido), ref)
    valores.setdefault("ela_certeza", Certeza.INFERENCIA.value)
    if valores.get("conversamos") is not None:
        valores["acompanhado_em"] = ref
    _lotado(banco, "reparos")
    rid = banco.inserir("reparos", {**valores, "criado_em": _agora(), "registro_id": None, "trecho": None})
    return _vista_reparo(_uma(banco, "reparos", rid), ref)


def _rota_editar_reparo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    atual = _uma(banco, "reparos", pedido.id)
    valores = _campos_reparo(_corpo(pedido), ref, parcial=True)
    if not valores:
        raise ValueError(CAMPOS_INVALIDOS)
    if atual.get("registro_id") and "aconteceu" in valores and valores["aconteceu"] not in (
            _texto_registro(banco, atual["registro_id"]) or ""):
        raise ValueError(ACONTECEU_DO_REGISTRO)
    if "conversamos" in valores:
        valores["acompanhado_em"] = ref
    banco.atualizar("reparos", pedido.id, valores)
    return _vista_reparo(_uma(banco, "reparos", pedido.id), ref)


def _rota_apagar_reparo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    rid = pedido.id
    return _apagar(nucleo, banco, "reparos", rid, (f"eu:reparo:p{rid}:", f"eu:tema:p{rid}:", f"eu:intensos:p{rid}:"))


def _rota_admiracoes(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    return [{**a, "data": _d(a["data"])} for a in _linhas(banco, "admiracoes", ordem="data DESC, id DESC")]


def _rota_nova_admiracao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) - {"texto", "data"}:
        raise ValueError(CAMPOS_INVALIDOS)
    texto = _cap(_texto(c.get("texto"), maximo=300, erro="Escreva o que você admira nela (uma linha, até 300 caracteres)."))
    _lotado(banco, "admiracoes")
    aid = banco.inserir("admiracoes", {"texto": texto, "data": _data_pedida(c.get("data"), ref, padrao=ref),
                                       "registro_id": None, "trecho": None, "criado_em": _agora()})
    return {"id": aid}


def _rota_apagar_admiracao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return _apagar(nucleo, banco, "admiracoes", pedido.id)


def _rota_intencao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    m = pedido.parametro("mes") or mes(ref)
    _conferir_periodo("mensal", m, ref, para_ver=True)
    return {"mes": m, "rotulo": rotulo_periodo(m), "atual": _intencao(banco, m),
            "sugestoes": sugestoes_de_intencao(banco, ref) if m == mes(ref) else []}


def _rota_escolher_intencao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) - {"mes", "texto", "sugestao"}:
        raise ValueError(CAMPOS_INVALIDOS)
    m = c.get("mes") or mes(ref)
    _conferir_periodo("mensal", m, ref)
    texto = _cap(_texto(c.get("texto"), maximo=200, erro="Escreva a intenção em poucas palavras (até 200 caracteres)."))
    sugestao = c.get("sugestao")
    if sugestao is not None and (not isinstance(sugestao, str) or not re.fullmatch(r"[a-z_0-9]{1,30}", sugestao)):
        raise ValueError("Sugestão desconhecida.")
    atual = _intencao(banco, m)
    valores = {"texto": texto, "origem": "sugerida" if sugestao else "escrita", "sugestao": sugestao}
    if atual:
        banco.atualizar("intencoes", atual["id"], valores)
        iid = atual["id"]
    else:
        iid = banco.inserir("intencoes", {"mes": m, **valores, "criado_em": ref.isoformat()})
    return {"id": iid, "atual": _intencao(banco, m)}


def _rota_editar_intencao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    _uma(banco, "intencoes", pedido.id)
    c = _corpo(pedido)
    if not c or set(c) - {"texto", "revisao"}:
        raise ValueError(CAMPOS_INVALIDOS)
    valores: dict[str, Any] = {}
    if "texto" in c:
        valores["texto"] = _cap(_texto(c["texto"], maximo=200))
    if "revisao" in c:
        valores["revisao"] = _texto(c["revisao"], maximo=MAX_RESPOSTA, obrigatorio=False) if c["revisao"] is not None else None
        valores["revisada_em"] = ref if valores["revisao"] else None
    banco.atualizar("intencoes", pedido.id, valores)
    return {"atual": _uma(banco, "intencoes", pedido.id)}


def _rota_apagar_intencao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    i = _uma(banco, "intencoes", pedido.id)
    return _apagar(nucleo, banco, "intencoes", pedido.id, (f"eu:intencao:{i['mes']}:",))


def _rota_cartas(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return {"cartas": [_vista_carta(c) for c in _linhas(banco, "cartas", ordem="id DESC")],
            "ocasioes": [{"id": k, "nome": v} for k, v in OCASIOES.items()]}


def _rota_blocos(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ocasiao = pedido.parametro("ocasiao") or "sem_motivo"
    return {"ocasiao": ocasiao, "blocos": blocos_de_carta(banco, ocasiao, _ref(nucleo))}


def _rota_esqueleto(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _corpo(pedido)
    return esqueleto(banco, c.get("ocasiao"), c.get("blocos", []), _ref(nucleo))


def _blocos_conferidos(banco: Any, ocasiao: str, blocos: Any, ref: date) -> list[dict]:
    if blocos in (None, []):
        return []
    disponiveis = {b["id"]: b for b in blocos_de_carta(banco, ocasiao, ref)}
    if not isinstance(blocos, list) or len(blocos) > 40 or any(not isinstance(b, str) or b not in disponiveis for b in blocos):
        raise ValueError(BLOCO_INVALIDO)
    return [{"id": b, "registro_id": disponiveis[b].get("registro_id")} for b in dict.fromkeys(blocos)]


def _texto_carta(valor: Any) -> str:
    _texto(valor, MAX_CARTA, f"Escreva a carta (até {MAX_CARTA} caracteres).")
    return valor.strip()


def _rota_nova_carta(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref = _ref(nucleo)
    c = _corpo(pedido)
    if set(c) - {"ocasiao", "titulo", "texto", "blocos"}:
        raise ValueError(CAMPOS_INVALIDOS)
    if c.get("ocasiao") not in OCASIOES:
        raise ValueError(OCASIAO_INVALIDA)
    texto = _texto_carta(c.get("texto"))
    _lotado(banco, "cartas")
    cid = banco.inserir("cartas", {"ocasiao": c["ocasiao"], "titulo": _texto(c.get("titulo"), 120, obrigatorio=False),
                                   "texto": texto, "blocos": _blocos_conferidos(banco, c["ocasiao"], c.get("blocos"), ref),
                                   "criado_em": _agora()})
    return _vista_carta(_uma(banco, "cartas", cid))


def _rota_editar_carta(nucleo: Any, pedido: Any, banco: Any) -> dict:
    _uma(banco, "cartas", pedido.id)
    c = _corpo(pedido)
    if not c or set(c) - {"titulo", "texto"}:
        raise ValueError(CAMPOS_INVALIDOS)
    valores: dict[str, Any] = {"atualizado_em": _agora()}
    if "texto" in c:
        valores["texto"] = _texto_carta(c["texto"])
    if "titulo" in c:
        valores["titulo"] = _texto(c["titulo"], 120, obrigatorio=False) if c["titulo"] is not None else None
    banco.atualizar("cartas", pedido.id, valores)
    return _vista_carta(_uma(banco, "cartas", pedido.id))


def _rota_apagar_carta(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return _apagar(nucleo, banco, "cartas", pedido.id)


def rotas() -> list:
    lista = [
        ("GET", "", _rota_visao), ("GET", "checkin", _rota_checkin), ("POST", "checkin", _rota_salvar_checkin),
        ("GET", "checkins", _rota_checkins), ("DELETE", "checkins/{id}", _rota_apagar_checkin),
        ("GET", "mes", _rota_mes), ("GET", "espelho", _rota_espelho),
        ("GET", "atitudes", _rota_atitudes), ("POST", "atitudes", _rota_nova_atitude),
        ("DELETE", "atitudes/{id}", _rota_apagar_atitude),
        ("GET", "aprendizados", _rota_aprendizados), ("POST", "aprendizados", _rota_novo_aprendizado),
        ("GET", "aprendizados/{id}", _rota_aprendizado), ("PATCH", "aprendizados/{id}", _rota_editar_aprendizado),
        ("POST", "aprendizados/{id}/lembrei", _rota_lembrei), ("DELETE", "aprendizados/{id}", _rota_apagar_aprendizado),
        ("GET", "reparos", _rota_reparos), ("POST", "reparos", _rota_novo_reparo),
        ("PATCH", "reparos/{id}", _rota_editar_reparo), ("DELETE", "reparos/{id}", _rota_apagar_reparo),
        ("GET", "admiracoes", _rota_admiracoes), ("POST", "admiracoes", _rota_nova_admiracao),
        ("DELETE", "admiracoes/{id}", _rota_apagar_admiracao),
        ("GET", "intencao", _rota_intencao), ("POST", "intencao", _rota_escolher_intencao),
        ("PATCH", "intencao/{id}", _rota_editar_intencao), ("DELETE", "intencao/{id}", _rota_apagar_intencao),
        ("GET", "cartas", _rota_cartas), ("GET", "cartas/blocos", _rota_blocos),
        ("POST", "cartas/esqueleto", _rota_esqueleto), ("POST", "cartas", _rota_nova_carta),
        ("PATCH", "cartas/{id}", _rota_editar_carta), ("DELETE", "cartas/{id}", _rota_apagar_carta),
    ]
    return [rota(metodo, caminho, _com_diario(funcao)) for metodo, caminho, funcao in lista]


# ================================================================ exportar, importar, apagar, IA

def exportar(banco: Any) -> dict:
    saida = {}
    for tabela in TABELAS:
        linhas = _linhas(banco, tabela)
        for l in linhas:
            for chave, padrao in (("respostas", {}), ("gatilhos", {}), ("blocos", [])):
                if chave in l:
                    l[chave] = _json(l[chave], padrao)
        saida[tabela] = linhas
    return saida


def _str(v: Any, maximo: int) -> str | None:
    return v.strip()[:maximo] if isinstance(v, str) and v.strip() else None


def importar(banco: Any, dados: Any, mapa_registros: dict) -> dict:
    """Junta sem duplicar. Linha que dependia de um registro que não veio (ou cujo trecho não está
    no texto dele) fica de fora: nunca inventar, e apagar é apagar."""
    formato = "Os dados da área Eu no arquivo não estão no formato certo."
    if not isinstance(dados, dict) or any(not isinstance(dados.get(t, []), list) for t in TABELAS):
        raise ValueError(formato)
    if sum(len(dados.get(t, [])) for t in TABELAS) > MAX_IMPORTAR:
        raise ValueError("Dados demais da área Eu no arquivo.")
    novos, de_fora = 0, 0
    textos: dict[int, str] = {}

    def registro_de(linha: dict) -> tuple[bool, int | None]:
        rid = linha.get("registro_id")
        if rid is None:
            return True, None
        novo = mapa_registros.get(rid) if isinstance(rid, int) and not isinstance(rid, bool) else None
        if novo is None:
            return False, None
        if novo not in textos:
            textos[novo] = _texto_registro(banco, novo) or ""
        trecho = linha.get("trecho")
        if not isinstance(trecho, str) or not trecho.strip() or trecho not in textos[novo]:
            return False, None
        return True, novo

    def data_de(l: dict) -> date | None:
        return _d(l.get("data")) if isinstance(l.get("data"), str) else None

    for l in dados.get("checkins", []):
        try:
            tipo_periodo = limites(l.get("periodo"))[0] if isinstance(l, dict) else None
        except ValueError:
            tipo_periodo = None
        if tipo_periodo is None or l.get("tipo") not in ("semanal", "mensal") or (l["tipo"] == "semanal") != (tipo_periodo == "semana"):
            de_fora += 1
            continue
        if _checkin(banco, l["tipo"], l["periodo"]):
            continue
        respostas = l.get("respostas") if isinstance(l.get("respostas"), dict) else {}
        perguntas = []
        for p in respostas.get("perguntas", []) if isinstance(respostas.get("perguntas"), list) else []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not _str(p.get("resposta"), MAX_RESPOSTA):
                continue
            refs = p.get("refs") if isinstance(p.get("refs"), dict) else {}
            refs = {**refs, "registro_ids": [mapa_registros[i] for i in refs.get("registro_ids", [])
                                             if isinstance(i, int) and i in mapa_registros]}
            perguntas.append({"id": p["id"][:40], "pergunta": _str(p.get("pergunta"), 400) or "", "tema": _str(p.get("tema"), 20),
                              "refs": refs, "resposta": _str(p["resposta"], MAX_RESPOSTA)})
        presenca = l.get("presenca") if l["tipo"] == "semanal" and l.get("presenca") in _EMOJI else None
        banco.inserir("checkins", {"tipo": l["tipo"], "periodo": l["periodo"], "respostas": {"perguntas": perguntas},
                                   "presenca": presenca, "criado_em": _str(l.get("criado_em"), 40) or _agora(),
                                   "atualizado_em": _str(l.get("atualizado_em"), 40)})
        novos += 1
    for l in dados.get("atitudes", []):
        if not isinstance(l, dict) or not _str(l.get("texto"), 300) or data_de(l) is None:
            de_fora += 1
            continue
        ok, rid = registro_de(l)
        if not ok:
            de_fora += 1
            continue
        texto = _str(l["texto"], 300)
        if banco.consultar("SELECT 1 FROM atitudes WHERE texto = ? AND data = ? AND registro_id IS ?",
                           (texto, data_de(l).isoformat(), rid)):
            continue
        banco.inserir("atitudes", {"texto": texto, "data": data_de(l), "registro_id": rid,
                                   "trecho": l.get("trecho") if rid else None,
                                   "jeito": l.get("jeito") if l.get("jeito") in JEITOS else "outro",
                                   "criado_em": _str(l.get("criado_em"), 40) or _agora()})
        novos += 1
    existentes = {normalizar(a["texto"]) for a in banco.consultar("SELECT texto FROM aprendizados")}
    for l in dados.get("aprendizados", []):
        texto = _str(l.get("texto"), MAX_TEXTO) if isinstance(l, dict) else None
        if not texto or data_de(l) is None:
            de_fora += 1
            continue
        ok, rid = registro_de(l)
        if not ok:
            de_fora += 1
            continue
        if normalizar(texto) in existentes:
            continue
        existentes.add(normalizar(texto))
        banco.inserir("aprendizados", {"texto": texto, "gatilhos": gatilhos(texto),
                                       "criado_em": _str(l.get("criado_em"), 40) or _agora(),
                                       "mostrado_em": _iso(_d(l.get("mostrado_em"))), "data": data_de(l),
                                       "registro_id": rid, "trecho": l.get("trecho") if rid else None})
        novos += 1
    for l in dados.get("reparos", []):
        if not isinstance(l, dict) or not _str(l.get("aconteceu"), MAX_TEXTO) or data_de(l) is None:
            de_fora += 1
            continue
        ok, rid = registro_de(l)
        if not ok:
            de_fora += 1
            continue
        aconteceu = _str(l["aconteceu"], MAX_TEXTO)
        if any(normalizar(r["aconteceu"]) == normalizar(aconteceu)
               for r in banco.consultar("SELECT aconteceu FROM reparos WHERE data = ?", (data_de(l).isoformat(),))):
            continue
        certeza = l.get("ela_certeza") if l.get("ela_certeza") in ("confirmado", "observado", "inferencia") else "inferencia"
        banco.inserir("reparos", {
            "data": data_de(l), "aconteceu": aconteceu, "eu_senti": _str(l.get("eu_senti"), MAX_TEXTO),
            "ela_pode_ter_sentido": _str(l.get("ela_pode_ter_sentido"), MAX_TEXTO),
            "ela_certeza": certeza if rid or certeza != "observado" else "inferencia",
            "faria_diferente": _str(l.get("faria_diferente"), MAX_TEXTO),
            "conversamos": l.get("conversamos") if l.get("conversamos") in (0, 1) and not isinstance(l.get("conversamos"), bool) else None,
            "reparo": _str(l.get("reparo"), MAX_TEXTO), "tema": _str(l.get("tema"), MAX_TEXTO),
            "intensidade": l.get("intensidade") if l.get("intensidade") in (1, 2, 3) and not isinstance(l.get("intensidade"), bool) else 1,
            "criado_em": _str(l.get("criado_em"), 40) or _agora(), "acompanhado_em": _iso(_d(l.get("acompanhado_em"))),
            "registro_id": rid, "trecho": l.get("trecho") if rid else None})
        novos += 1
    for l in dados.get("admiracoes", []):
        if not isinstance(l, dict) or not _str(l.get("texto"), 300) or data_de(l) is None:
            de_fora += 1
            continue
        ok, rid = registro_de(l)
        if not ok:
            de_fora += 1
            continue
        texto = _str(l["texto"], 300)
        if any(normalizar(a["texto"]) == normalizar(texto) for a in banco.consultar("SELECT texto FROM admiracoes")):
            continue
        banco.inserir("admiracoes", {"texto": texto, "data": data_de(l), "registro_id": rid,
                                     "trecho": l.get("trecho") if rid else None,
                                     "criado_em": _str(l.get("criado_em"), 40) or _agora()})
        novos += 1
    for l in dados.get("cartas", []):
        if not isinstance(l, dict) or l.get("ocasiao") not in OCASIOES or not _str(l.get("texto"), MAX_CARTA):
            de_fora += 1
            continue
        texto = l["texto"].strip()[:MAX_CARTA]
        if banco.consultar("SELECT 1 FROM cartas WHERE ocasiao = ? AND texto = ?", (l["ocasiao"], texto)):
            continue
        blocos = []
        for b in l.get("blocos") if isinstance(l.get("blocos"), list) else []:
            if not isinstance(b, dict) or not isinstance(b.get("id"), str):
                continue
            rid = b.get("registro_id")
            if rid is None:
                blocos.append({"id": b["id"][:40], "registro_id": None})
            elif isinstance(rid, int) and rid in mapa_registros:
                blocos.append({"id": b["id"][:40], "registro_id": mapa_registros[rid]})
        banco.inserir("cartas", {"ocasiao": l["ocasiao"], "titulo": _str(l.get("titulo"), 120), "texto": texto,
                                 "blocos": blocos, "criado_em": _str(l.get("criado_em"), 40) or _agora(),
                                 "atualizado_em": _str(l.get("atualizado_em"), 40)})
        novos += 1
    for l in dados.get("intencoes", []):
        try:
            tipo_periodo = limites(l.get("mes"))[0] if isinstance(l, dict) else None
        except ValueError:
            tipo_periodo = None
        if tipo_periodo != "mes" or not _str(l.get("texto"), 200):
            de_fora += 1
            continue
        if _intencao(banco, l["mes"]):
            continue
        banco.inserir("intencoes", {"mes": l["mes"], "texto": _str(l["texto"], 200),
                                    "origem": l.get("origem") if l.get("origem") in ("sugerida", "escrita") else "escrita",
                                    "sugestao": _str(l.get("sugestao"), 30), "criado_em": _str(l.get("criado_em"), 40) or _agora(),
                                    "revisao": _str(l.get("revisao"), MAX_RESPOSTA), "revisada_em": _iso(_d(l.get("revisada_em")))})
        novos += 1
    texto = f"{_plural(novos, 'item novo', 'itens novos')} da área Eu."
    if de_fora:
        texto += (f" {de_fora} {'ficou' if de_fora == 1 else 'ficaram'} de fora (dependiam de registros que não vieram "
                  "ou estavam incompletos).")
    return {"novos": novos, "texto": texto}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """O que dependia do registro some junto: as linhas com registro_id saem pela fundação; aqui
    saem as referências dele nos check-ins e nas cartas, e as chaves de aviso que o citam."""
    for c in banco.consultar("SELECT id, respostas FROM checkins"):
        respostas = _json(c["respostas"], {})
        mudou = False
        for p in respostas.get("perguntas", []):
            refs = p.get("refs") if isinstance(p, dict) and isinstance(p.get("refs"), dict) else None
            if refs and registro_id in (refs.get("registro_ids") or []):
                refs["registro_ids"] = [i for i in refs["registro_ids"] if i != registro_id]
                mudou = True
        if mudou:
            banco.atualizar("checkins", c["id"], {"respostas": respostas}, historico=False)
    for c in banco.consultar("SELECT id, blocos FROM cartas"):
        blocos = _json(c["blocos"], [])
        restantes = [b for b in blocos if not (isinstance(b, dict) and b.get("registro_id") == registro_id)]
        if len(restantes) != len(blocos):
            banco.atualizar("cartas", c["id"], {"blocos": restantes}, historico=False)
    for r in banco.consultar("SELECT id FROM reparos WHERE registro_id = ?", (registro_id,)):
        _esquecer_chaves(banco, f"eu:reparo:p{r['id']}:", f"eu:tema:p{r['id']}:", f"eu:intensos:p{r['id']}:")
    for a in banco.consultar("SELECT id FROM aprendizados WHERE registro_id = ?", (registro_id,)):
        _esquecer_chaves_com(banco, f":ap{a['id']}:")
    _esquecer_chaves(banco, f"eu:r{registro_id}:")


def fora_da_ia(banco: Any) -> set[int]:
    """Registros com algo da área Eu (atitude, aprendizado, desentendimento, admiração): nada da
    área Eu vai para a IA, nem com a caixinha 🔒 desmarcada."""
    ids: set[int] = set()
    for tabela in _TABELAS_DE_REGISTRO:
        ids |= {l["registro_id"] for l in banco.consultar(
            f"SELECT DISTINCT registro_id FROM {tabela} WHERE registro_id IS NOT NULL")}
    return ids


__all__ = [
    "ESQUEMA", "ESQUEMA_VERSAO", "JEITOS", "NOME", "OCASIOES", "PRESENCA", "ROTULO", "TIPOS_EXTRAS", "ao_apagar_registro",
    "atitudes_cruzadas", "blocos_de_carta", "cartoes_hoje", "casar_gatilhos", "combinacoes", "cuidar_de_voce",
    "descompassos", "espelho", "esqueleto", "esquecer_diario", "exportar", "extrair", "fora_da_ia", "gatilhos",
    "gravar_extras", "importar", "intensos", "jeitos_do_apoio", "lado_a_lado", "limites", "marca_privado", "mes",
    "mes_da_reflexao", "perguntas_da_semana", "perguntas_do_mes", "rotas", "rotulo_periodo", "salvar_checkin", "semana",
    "semana_do_checkin", "sugestoes_de_intencao", "temas_repetidos", "tendencia", "visao_geral", "vista_mes",
]
