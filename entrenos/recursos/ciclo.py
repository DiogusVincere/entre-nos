"""Ciclo (DESIGN.md, seção 20.8) — ela sabe e concorda.

Um espaço para cuidar melhor dela, com o conhecimento dela: quando o período começou e terminou,
o que ela sente e o que ajuda. Tudo sai do que você registrou e cita o trecho de onde veio; a
previsão é só uma faixa, calculada pelo Python a partir das datas anotadas, e nunca vira ordem nem
explicação de humor ou comportamento. Nada de diagnóstico.

Tabelas (contrato da seção 20.8 + colunas a mais, que outros recursos podem ler):
- `ciclos(id, inicio, fim, criado_em)` + `exatidao` ("dia": a data exata foi dita; "aproximada": só
  a semana ou o mês, ou "ela já estava menstruada" sem saber o começo; "talvez": você achou, não
  sabe), `origem` ("registro" | "manual"), `registro_id` (o registro que trouxe o início: apagá-lo
  apaga o ciclo, a não ser que outro registro também conte esse início), `fim_registro_id` (o que
  trouxe o fim) e `intervalo_confirmado` (você disse que o intervalo longo antes deste início foi
  assim mesmo, e não um mês sem registro);
- `ciclo_eventos(id, data, tipo: inicio|durante|fim|fluxo|sintoma, valor, trecho, registro_id,
  criado_em)` + os tipos `ajuda` (o que ajuda ou não ajuda, com `polaridade` +1/-1) e `mencao`
  (comentário sobre o ciclo sem data, ou de outra pessoa: guardado para o registro ficar fora da IA),
  e as colunas `ciclo_id`, `palavras` (as palavras do registro: "cólica", "bolsa de água quente"),
  `polaridade`, `certeza` (confirmado | observado | inferencia) e `de_quem` ("ela" | "outra").
  `valor` por tipo: inicio = a exatidão da data; durante = o início que o registro disse (AAAA-MM-DD)
  ou vazio; fim = vazio; fluxo = leve | moderado | intenso; sintoma = a chave (colica, dor_de_cabeca...);
  ajuda = o que ajuda (literal); mencao = tpm | atraso | habito | duvida | futuro | outra | geral;
- `ciclo_ajustes(chave, valor)`: lembrete no Hoje e período fértil (ligados por padrão).

Regras (`extrair`, frase a frase, sem acento e com gírias): início ("menstruou", "ficou menstruada",
"desceu pra ela", "veio a menstruação", "começou a menstruar"), durante ("tá menstruada", "tá de
chico", "tá naqueles dias", "segundo dia" — que já diz quando começou; "desde segunda" e "há 3 dias"
também), fim ("parou", "acabou a menstruação", "não tá mais menstruada"), fluxo (só com a palavra
"fluxo"), sintomas com as palavras do registro (cólica, dor de cabeça, enxaqueca, inchaço, cansaço,
dor nas costas, seios sensíveis, náusea, espinhas, desejo de doce...) e o que ajuda ("quando tá com
cólica, bolsa de água quente ajuda"; "o que ajuda é chá"; "ela odeia que eu fique perguntando" = não
ajuda). Humor (irritada, triste, sensível) nunca vira sintoma: o app não liga humor ao ciclo.
Negação ("ainda não desceu"), dúvida ("não sei se"), hábito ("sempre fica menstruada no começo do
mês"), futuro e outra pessoa ("a irmã dela", "minha menstruação") viram `ciclo_mencao`: sem data,
mas o registro fica 🔒. Datas pelo `regras/datas.py`; data que caiu no futuro ("desde segunda" dito
na quarta) é lida de novo no passado ("foi segunda"). Os extras têm tipo com prefixo
(`ciclo_inicio`...) para não disputar o desenho da revisão com outros recursos.

Limites (com testes):
- todo extra pede 🔒 (`marca_privado`); `fora_da_ia` (= `discretos`) = registros com dado do ciclo
  guardado e também qualquer registro em que o `extrair` acha algo (`texto_fala_do_ciclo`: o mesmo
  vocabulário da revisão; mesmo sem nada confirmado, com "Não guardar" ou com a caixinha desmarcada);
- os itens das regras tirados da MESMA frase de um dado do ciclo confirmado (um "Está irritada"
  numa frase que diz "menstruou") ficam "descartados" ao gravar: não entram em "Como ela está", no
  Hoje nem nas ligações com outros registros; continuam no registro (dá para reativar lá). A nota da
  frase (o texto) fica, e um acontecimento de resumo neutro ("Consulta") também. Sem nada confirmado,
  as telas centrais fazem o mesmo pelo `reservado` (20.10);
- o Hoje recebe no máximo um lembrete NEUTRO, 1-2 dias antes da faixa prevista, sem as palavras
  menstruação, ciclo, TPM (nem cólica, fluxo...), e só sugere o que ela disse que ajuda (🟢); sem
  fala dela, sem sugestão;
- previsão só com 3 ciclos completos (3 intervalos entre inícios com a data exata); faixa = o que os
  últimos ciclos variaram em volta da mediana (no mínimo ±2 dias); "irregular" acima de 7 dias de
  variação; intervalo ≈ 2× a mediana pergunta "faltou registrar um mês?" e fica fora; intervalo
  longe da mediana (mais que 9 dias ou 30%) fica fora quando sobram ciclos suficientes;
- período fértil só com previsão regular, só no cartão (nunca no Hoje), 🔵 e com o aviso fixo;
- planejamento (evento futuro registrado nos dias previstos) só quando há algo que ela disse (🟢) ou um
  padrão dos registros, e só no cartão; "no momento" lembra, no cartão, o padrão que toca o dia de hoje;
- apagar é apagar: um ciclo, uma anotação ou tudo (também nas cópias automáticas e nas chaves de
  aviso dispensado); apagar o registro tira o que veio dele (o início passa para outro registro que
  também o contava; o fim volta ao de outro registro ou some).

Tudo roda igual no navegador (Pyodide): sem thread, sleep, rede ou Windows.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..banco import NaoEncontrado
from ..regras.certeza import classificar_certeza, trechos_confirmados, trechos_nao_afirmados
from ..regras.datas import extrair_datas
from ..regras.pessoas import plano
from ..texto import data_br, frases, normalizar, tempo_atras
from . import cartao, registrar_erro, rota

NOME = "ciclo"
ROTULO = "Ciclo"

# Tipos dos extras (com prefixo: o desenho da revisão é escolhido pelo tipo, que é global na tela).
EXTRA_INICIO, EXTRA_DURANTE, EXTRA_FIM = "ciclo_inicio", "ciclo_durante", "ciclo_fim"
EXTRA_FLUXO, EXTRA_SINTOMA, EXTRA_AJUDA, EXTRA_MENCAO = "ciclo_fluxo", "ciclo_sintoma", "ciclo_ajuda", "ciclo_mencao"
_TIPOS_DO_PERIODO = (EXTRA_INICIO, EXTRA_DURANTE, EXTRA_FIM, EXTRA_FLUXO, EXTRA_SINTOMA, EXTRA_AJUDA, EXTRA_MENCAO)
TIPOS_EVENTO = ("inicio", "durante", "fim", "fluxo", "sintoma", "ajuda", "mencao")
_EVENTO_DO_EXTRA = dict(zip(_TIPOS_DO_PERIODO, TIPOS_EVENTO))
# Integração (20.10): o aviso de planejamento NA REVISÃO de um acontecimento que cai nos dias previstos
# (20.8). É só informação: não é dado do ciclo, não deixa o registro 🔒 e não grava nada.
EXTRA_PLANEJAMENTO = "ciclo_planejamento"
TIPOS_EXTRAS = (*_TIPOS_DO_PERIODO, EXTRA_PLANEJAMENTO)
# Todo dado do ciclo deixa o TEXTO fora da IA, confirmado ou não (seção 20.10.1).
TIPOS_FORA_PELO_TEXTO = _TIPOS_DO_PERIODO


ESQUEMA = [
    """
    CREATE TABLE ciclos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inicio TEXT NOT NULL,
        fim TEXT,
        criado_em TEXT NOT NULL,
        exatidao TEXT NOT NULL DEFAULT 'dia' CHECK (exatidao IN ('dia', 'aproximada', 'talvez')),
        origem TEXT NOT NULL DEFAULT 'registro' CHECK (origem IN ('registro', 'manual')),
        registro_id INTEGER,
        fim_registro_id INTEGER,
        intervalo_confirmado INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX ciclos_inicio ON ciclos(inicio);
    CREATE INDEX ciclos_registro ON ciclos(registro_id);
    CREATE TABLE ciclo_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('inicio', 'durante', 'fim', 'fluxo', 'sintoma', 'ajuda', 'mencao')),
        valor TEXT,
        trecho TEXT NOT NULL,
        registro_id INTEGER,
        criado_em TEXT NOT NULL,
        ciclo_id INTEGER,
        palavras TEXT,
        polaridade INTEGER,
        certeza TEXT NOT NULL DEFAULT 'observado',
        de_quem TEXT NOT NULL DEFAULT 'ela'
    );
    CREATE INDEX ciclo_eventos_registro ON ciclo_eventos(registro_id);
    CREATE INDEX ciclo_eventos_ciclo ON ciclo_eventos(ciclo_id);
    CREATE INDEX ciclo_eventos_data ON ciclo_eventos(data);
    CREATE TABLE ciclo_ajustes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chave TEXT NOT NULL UNIQUE,
        valor TEXT NOT NULL
    )
    """,
]
ESQUEMA_VERSAO = 1

EXATIDOES = ("dia", "aproximada", "talvez")
CERTEZAS = ("confirmado", "observado", "inferencia")
FLUXOS = ("leve", "moderado", "intenso")
MENCOES = ("atraso", "tpm", "habito", "duvida", "futuro", "outra", "geral")
AJUSTES_PADRAO = {"lembrete_hoje": True, "mostrar_fertil": True}

CICLOS_MINIMOS = 3            # ciclos completos (intervalos entre dois inícios exatos) para prever
ULTIMOS_CICLOS = 6            # a previsão olha os últimos N ciclos que valem
INTERVALO_MINIMO = 15         # menos que isso entre dois inícios não entra no cálculo
INTERVALO_MAXIMO = 120        # mais que isso é pausa nos registros
MESMO_CICLO = 10              # dois inícios a até 10 dias um do outro são o mesmo ciclo
DURACAO_MAXIMA = 15           # um fim até 15 dias depois do início
DURACAO_PADRAO = 5            # sem fins registrados (o "em andamento" e o planejamento)
FAIXA_MINIMA = 2              # a faixa prevista tem pelo menos ±2 dias
VARIACAO_IRREGULAR = 7        # variação dos últimos ciclos acima disso = irregular
ESQUECIDO = (1.7, 2.3)        # intervalo ≈ 2× a mediana: faltou registrar um mês?
FORA_DO_PADRAO_DIAS = 9       # longe da mediana mais que max(9 dias, 30%) = fora do padrão
DIAS_LEMBRETE = (1, 2)        # o lembrete do Hoje: 1-2 dias antes da faixa prevista
OVULACAO_ANTES, OVULACAO_FOLGA = 14, 2
FERTIL_ANTES, FERTIL_DEPOIS = 5, 1
CICLOS_PADRAO = 4             # padrões de sintomas: os últimos 4 ciclos
DIAS_ANTES_SINTOMA = 5        # sintoma até 5 dias antes de um início conta como "antes de começar"
MESES_AJUDA_ANTIGA = 12
MAX_PALAVRAS = 80
MAX_AJUDA = 200
MAX_SINTOMAS_POR_EXTRA = 12
MAX_ANOS_ATRAS = 5
MAX_IMPORTAR = 20000

AVISO_FERTIL = ("Estimativa aproximada, feita a partir de anotações. Não serve como método para evitar ou planejar "
                "gravidez; para isso, o ideal é um ginecologista.")
AVISO_CONSENTIMENTO = ("Use este espaço só se ela sabe e concorda. Nada daqui vai para a IA nem aparece em "
                       "“Como ela está”; no Hoje, no máximo um lembrete discreto de cuidado.")
TEXTO_HOJE = "💙 Um lembrete de cuidado para os próximos dias."
PORQUE_HOJE = "Pelas suas anotações, os próximos dias podem pedir um cuidado a mais. Os detalhes ficam em Conhecendo Ela."

DATA_INVALIDA = "A data precisa estar no formato AAAA-MM-DD."
DATA_NO_FUTURO = "Essa data ainda não chegou: anote o que já aconteceu."
DATA_ANTIGA = f"Essa data é antiga demais (mais de {MAX_ANOS_ATRAS} anos)."
DATA_DEPOIS_DO_REGISTRO = "A data não pode ser depois do dia do registro."
DATA_LONGE_DO_REGISTRO = "A data ficou longe demais do dia do registro (mais de um ano antes)."
FIM_ANTES = "O fim não pode ser antes do início."
FIM_LONGE = f"O fim precisa estar até {DURACAO_MAXIMA} dias depois do início."
CICLO_REPETIDO = "Já existe um ciclo começando perto dessa data ({data}). Edite esse em vez de criar outro."
CAMPOS_CICLO = "Só dá para mudar o início e o fim."
CAMPOS_AJUSTES = "Os ajustes do ciclo são “lembrete_hoje” e “mostrar_fertil” (sim ou não)."
FLUXO_INVALIDO = "O fluxo precisa ser leve, moderado ou intenso."
SINTOMAS_INVALIDOS = "Os sintomas vieram num formato que não dá para guardar."
AJUDA_INVALIDA = f"Escreva o que ajuda (até {MAX_AJUDA} caracteres)."
CERTEZA_INVALIDA = "A certeza precisa ser confirmado, observado ou inferência."
TIPO_INVALIDO = "Tipo de informação do ciclo desconhecido."
DIA_INVALIDO = f"O dia do período precisa ser um número de 1 a {DURACAO_MAXIMA}."
POLARIDADE_INVALIDA = "A ajuda precisa dizer se ajuda (1) ou não ajuda (-1)."
CONFIRMAR_TUDO = "Para apagar tudo, confirme (confirmar: true)."
SEM_CICLO = "Não encontrei esse ciclo. Talvez ele já tenha sido apagado."
SEM_ANOTACAO = "Não encontrei essa anotação. Talvez ela já tenha sido apagada."
REAL_INVALIDO = "Diga se o intervalo foi assim mesmo (real: true ou false)."
SEM_INICIO = "Informe a data do início."

# Palavras que nunca aparecem no Hoje (o texto do Hoje é neutro).
_PALAVRAS_DO_HOJE_PROIBIDAS = re.compile(
    r"menstru|ciclo|\btpm\b|colica|fluxo|absorvente|sangr|chico|ovula|fertil|gravid|periodo|naqueles\s+dias"
    r"|pre[\s-]?menstru|\bregra\b|\bdesceu\b")


# ================================================================ ajudantes

def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _data(v: Any) -> date | None:
    """'AAAA-MM-DD' (ou date) -> date; vazio -> None; o resto -> ValueError (DATA_INVALIDA)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            pass
    raise ValueError(DATA_INVALIDA)


def _data_tolerante(v: Any) -> date | None:
    try:
        return _data(v)
    except ValueError:
        return None


def _dia(d: date | None, referencia: date) -> str:
    """'28/09' no ano da referência; '28/09/2025' em outro ano."""
    if d is None:
        return ""
    return d.strftime("%d/%m") if d.year == referencia.year else data_br(d)


def _mediana(valores: list[float]) -> float:
    s = sorted(valores)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _arredondar(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _numero(v: float) -> str:
    """28.0 -> '28'; 28.5 -> '28,5'."""
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}".replace(".", ",")


def _juntar(partes: list[str]) -> str:
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " e " + partes[-1]


def sem_palavras_do_ciclo(texto: str) -> bool:
    """O texto pode aparecer no Hoje? (sem menstruação, ciclo, TPM, cólica, fluxo...)"""
    return not _PALAVRAS_DO_HOJE_PROIBIDAS.search(plano(texto or ""))


# ================================================================ dados

@dataclass
class Ciclo:
    inicio: date
    fim: date | None = None
    exatidao: str = "dia"
    id: int | None = None
    origem: str = "registro"
    registro_id: int | None = None
    fim_registro_id: int | None = None
    intervalo_confirmado: bool = False


@dataclass
class Evento:
    data: date
    tipo: str
    valor: str | None = None
    trecho: str = ""
    id: int | None = None
    registro_id: int | None = None
    ciclo_id: int | None = None
    palavras: str | None = None
    polaridade: int | None = None
    certeza: str = "observado"
    de_quem: str = "ela"


def _ciclos(banco: Any) -> list[Ciclo]:
    saida = []
    for l in banco.consultar("SELECT * FROM ciclos ORDER BY inicio, id"):
        inicio = _data_tolerante(l.get("inicio"))
        if inicio is None:
            continue
        saida.append(Ciclo(inicio=inicio, fim=_data_tolerante(l.get("fim")),
                           exatidao=l.get("exatidao") if l.get("exatidao") in EXATIDOES else "dia", id=l["id"],
                           origem=l.get("origem") or "registro", registro_id=l.get("registro_id"),
                           fim_registro_id=l.get("fim_registro_id"),
                           intervalo_confirmado=bool(l.get("intervalo_confirmado"))))
    return saida


def _eventos(banco: Any) -> list[Evento]:
    saida = []
    for l in banco.consultar("SELECT * FROM ciclo_eventos ORDER BY data, id"):
        d = _data_tolerante(l.get("data"))
        if d is None or l.get("tipo") not in TIPOS_EVENTO:
            continue
        pol = l.get("polaridade")
        saida.append(Evento(data=d, tipo=l["tipo"], valor=l.get("valor"), trecho=l.get("trecho") or "", id=l["id"],
                            registro_id=l.get("registro_id"), ciclo_id=l.get("ciclo_id"), palavras=l.get("palavras"),
                            polaridade=pol if pol in (1, -1) and not isinstance(pol, bool) else None,
                            certeza=l.get("certeza") if l.get("certeza") in CERTEZAS else "observado",
                            de_quem="outra" if l.get("de_quem") == "outra" else "ela"))
    return saida


def ajustes(banco: Any) -> dict:
    saida = dict(AJUSTES_PADRAO)
    for l in banco.consultar("SELECT chave, valor FROM ciclo_ajustes"):
        if l["chave"] in AJUSTES_PADRAO:
            saida[l["chave"]] = l["valor"] == "1"
    return saida


# ================================================================ extração (regras)
# Tudo sobre o texto em minúsculas e sem acento com o MESMO tamanho (`plano`): as posições valem
# no texto original, e o que vai para os extras é sempre um pedaço literal dele.

# Palavras que fazem uma frase falar do ciclo.
_MARCADOR = re.compile(
    r"\bmenstru\w*|\btpm\b|\bpre[\s-]?menstru\w*|\babsorventes?\b|\bcoletor\s+menstrual\b"
    r"|\bciclo\s+(?:menstrual|dela)\b|\bde\s+chico\b|\b(?:ta|esta|tava|estava|anda|andava)\s+naqueles\s+dias\b"
    r"|\bperiodo\s+(?:menstrual|dela)\b|\bovulac\w*|\bovulando\b|\bperiodo\s+fertil\b|\bregra\s+dela\b")
# Palavras fortes num trecho solto (o aviso de planejamento não fala de um acontecimento do ciclo). O
# que deixa um registro fora da IA é `texto_fala_do_ciclo` (o mesmo `extrair` da revisão).
_TEXTO_DO_CICLO = re.compile(
    r"\bmenstru\w*|\btpm\b|\bpre[\s-]?menstru\w*|\babsorventes?\b|\bcoletor\s+menstrual\b|\bciclo\s+menstrual\b"
    r"|\bovulac\w*|\bovulando\b|\bperiodo\s+fertil\b|\b(?:ta|esta|tava|estava|anda|andava)\s+(?:de\s+chico"
    r"|naqueles\s+dias)\b")
# Marcadores fracos: sozinhos falam do ciclo quase sempre ("tá com cólica", "fluxo intenso"). Como
# qualquer dado do ciclo que a revisão acha, deixam o registro fora da IA mesmo sem confirmar
# (`texto_fala_do_ciclo`: um vocabulário só, o da revisão).
_COLICA = re.compile(r"\bcolicas?\b(?!\s+(?:renal|renais|intestina\w*|de\s+rim|no\s+rim|de\s+bebe|do\s+bebe|biliar"
                     r"|de\s+figado|nefretica|de\s+gases))")
_FLUXO_MARCADOR = re.compile(r"\bfluxo\s+(?:(?:ta|esta|tava|estava|bem|muito|mais|meio)\s+)*(?:leve|fraco|intenso"
                             r"|forte|moderado|abundante|pesado|grande)\b")
# Pré-filtro do `fora_da_ia` (SQL): tudo o que pode fazer o `extrair` achar algo, com e sem acento
# composto ("_" é uma letra no LIKE do SQLite). O que passa daqui é lido pelo próprio `extrair`.
_LIKE_DO_CICLO = ("%menstru%", "%tpm%", "%absorvente%", "%ovula%", "%f_rtil%", "%fe_rtil%", "%chico%",
                  "%naqueles dias%", "%coletor%", "%ciclo%", "%per_odo%", "%peri_odo%", "%desceu%", "%regra%",
                  "%c_lica%", "%co_lica%", "%fluxo%")

_VERBO_FIM = r"(?:parou|acabou|terminou|passou|cessou|foi\s+embora|se\s+foi)"
_VERBO_INICIO = r"(?:veio|chegou|desceu|comecou|apareceu)"
_DONO_NOME = r"(?:\s+(?:dela|da\s+\w+))?"

# (tipo, expressão). A ordem decide quem fica com um pedaço da frase (o primeiro que casa).
_PERIODO: tuple[tuple[str, re.Pattern], ...] = tuple((t, re.compile(r)) for t, r in (
    ("eu", r"\bmenstruei\b|\bfiquei\s+menstruada\b|\b(?:to|tou|estou)\s+(?:menstruada|de\s+chico|naqueles\s+dias)\b"
           r"|\bminha\s+(?:menstruacao|tpm|regra)\b|\bmeu\s+ciclo\b"),
    ("durante_fim_negado", r"\bmenstruacao" + _DONO_NOME + r"\s+(?:ainda\s+)?nao\s+" + _VERBO_FIM + r"\b"),
    ("atraso", r"\bmenstruacao" + _DONO_NOME + r"\s+(?:ainda\s+)?nao\s+" + _VERBO_INICIO + r"\b"),
    ("fim", r"\b" + _VERBO_FIM + r"\s+(?:a\s+)?menstruacao\b"),
    ("fim", r"\bmenstruacao" + _DONO_NOME + r"\s+(?:ja\s+)?" + _VERBO_FIM + r"\b"),
    ("fim", r"\b(?:parou|terminou|acabou)\s+de\s+menstruar\b"),
    ("fim", r"\bnao\s+(?:esta|ta|tava|estava)\s+mais\s+menstruada\b"),
    ("fim", r"\bsaiu\s+(?:da|de)\s+(?:menstruacao|chico)\b"),
    ("fim", r"\b(?:acabou|terminou|parou|passou)\s+o\s+(?:chico|periodo)\b"),
    ("inicio", r"\b" + _VERBO_INICIO + r"\s+(?:a\s+)?menstruacao\b"),
    ("inicio", r"\bmenstruacao" + _DONO_NOME + r"\s+(?:ja\s+|finalmente\s+|enfim\s+)?" + _VERBO_INICIO + r"\b"),
    ("inicio", r"\bcomecou\s+a\s+menstruar\b"),
    ("inicio", r"\bfic(?:ou|aram)\s+menstruadas?\b"),
    ("inicio", r"\bmenstruou\b"),
    ("desceu", r"\bdesceu\b"),
    ("durante", r"\b(?:esta|ta|tava|estava|anda|andava|segue|continua|seguia|continuava)\s+(?:(?:bem|muito|toda|ainda"
                r"|meio)\s+)?(?:menstruada|de\s+chico|naqueles\s+dias|no\s+periodo|com\s+a\s+menstruacao)\b"),
    ("durante", r"\b(?:primeiro|segundo|terceiro|quarto|quinto|sexto|setimo|oitavo|\d{1,2}\s*[oº°]?)\s+dia\s+(?:da|de"
                r"|do)\s+(?:menstruacao|chico|periodo|ciclo)\b"),
    ("durante_n", r"\b(?:esta|ta|tava|estava)\s+no\s+(?:primeiro|segundo|terceiro|quarto|quinto|sexto|setimo|oitavo"
                  r"|\d{1,2}\s*[oº°]?)\s+dia\b"),
    ("tpm", r"\btpm\b|\bpre[\s-]?menstru\w*"),
))
# "desceu" é gíria de início só sem ir a lugar nenhum: "desceu pra ela", "desceu hoje", "desceu.".
_DESCEU_DEPOIS = re.compile(
    r"\s*(?:$|[,.;!?)]|(?:pra|para)\s+ela\b|nela\b|(?:hoje|hj|ontem|anteontem|agora|cedo|ja|finalmente|enfim)\b"
    r"|de\s+(?:manha|madrugada|noite|tarde)\b|a\s+noite\b|dia\s+\d|\d|(?:na|no|nessa|nesse)\s+(?:segunda|terca|quarta"
    r"|quinta|sexta|sabado|domingo)|(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|(?:semana|mes)\s+passad"
    r"|faz\s|ha\s)")
_PREENCHE = (r"(?:(?:hoje|hj|ontem|anteontem|ja|finalmente|enfim|e|que|ai|entao|so|agora|cedo|ufa|acho\s+que"
             r"|a\s+noite|de\s+noite|de\s+manha|de\s+madrugada|a\s+tarde|de\s+tarde|ela|ela\s+ja)\s*)*")
_ANTES_DE_DESCEU = re.compile(_PREENCHE.replace("|ela|ela\\s+ja", "|ainda|nao|n"))
_ANTES_DE_FIM_SOLTO = re.compile(_PREENCHE)
_FIM_SOLTO = re.compile(r"\b(?:parou|acabou|terminou)\b(?!\s+(?:de|o|a|os|as|um|uma|com|no|na|em|pra|para|por"
                        r"|tudo|isso)\b)")
_DIA_N = re.compile(
    r"\b(?:(?P<n>\d{1,2})\s*(?:o|º|°)?|(?P<o>primeiro|segundo|terceiro|quarto|quinto|sexto|setimo|oitavo))\s+dia\b")
_ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6, "setimo": 7, "oitavo": 8}

_SEPARADOR = re.compile(r"[,;:()]|\s(?:e|mas|porém|porem|só\s+que|so\s+que|então|entao|aí|ai|daí|dai|enquanto)\s")
_NEGACAO_ANTES = re.compile(r"\b(?:nao|nem|nunca|n)\s+(?:[\w-]+\s+)?$")
_HIPOTESE_ANTES = re.compile(r"\b(?:vai|vao|ia|iria|deve|devia|deveria|pode|podia|poderia|tomara\s+que|era\s+pra"
                             r"|era\s+para|tinha\s+que|tem\s+que|antes\s+de|esperando|espera|esperar|caso)\s+"
                             r"(?:[\w-]+\s+){0,2}$")
_HABITO = re.compile(r"\b(?:sempre|todo\s+mes|todos\s+os\s+meses|toda\s+vez|costuma|costumava|geralmente|normalmente"
                     r"|todo\s+(?:comeco|inicio|final|fim)\s+de\s+mes)\b")
_OBJETO_ANTES = re.compile(r"\b(?:com|pra|para|de|da|do|sobre|por|ao|levar|levei|levou|ver|vi|deixar|deixei)\s+$")
_CAUSA_DEPOIS = re.compile(r"^\s*(?:por\s+causa|por\s+conta|devido|porque|pq\b|do\s+trabalho|da\s+faculdade"
                           r"|com\s+o\s+trabalho|da\s+academia|do\s+treino|da\s+viagem)")
_OUTRA_CAUSA = re.compile(r"\b(?:por\s+causa|por\s+conta|devido|porque|pq|do\s+trabalho|da\s+faculdade|da\s+academia"
                          r"|do\s+treino|da\s+viagem|da\s+prova|do\s+chefe)\b")

# Sintomas: (chave, rótulo, expressão). Humor (irritada, triste, sensível...) fica de fora de propósito.
_SINTOMAS: tuple[tuple[str, str, re.Pattern], ...] = tuple((k, r, re.compile(r"\b(?:" + x + r")\b")) for k, r, x in (
    ("colica", "cólica", r"colicas?"),
    ("dor_de_cabeca", "dor de cabeça", r"dor(?:es)?\s+de\s+cabeca|cabeca\s+(?:doendo|explodindo)"),
    ("enxaqueca", "enxaqueca", r"enxaquecas?"),
    ("inchaco", "inchaço", r"inchac[oa]s?|inchad[ao]s?|estufad[ao]"),
    ("cansaco", "cansaço", r"cansac[oa]|cansad[ao]|exaust[ao]|sem\s+energia|indispost[ao]|moid[ao]"),
    ("dor_nas_costas", "dor nas costas", r"dor(?:es)?\s+(?:nas|na)\s+(?:costas|lombar)|dor\s+lombar|costas\s+doendo"),
    ("seios_sensiveis", "seios sensíveis", r"(?:seios?|peitos?)\s+(?:sensive(?:l|is)|doloridos?|inchados?|doendo)"
                                         r"|dor\s+nos\s+(?:seios|peitos)"),
    ("nausea", "náusea", r"nauseas?|enjoos?|enjoad[ao]|ansia\s+de\s+vomito"),
    ("espinhas", "espinhas", r"espinhas?|acnes?"),
    ("desejo_de_doce", "desejo de doce", r"(?:desejo|vontade|loucura|fissura)\s+(?:de|por)\s+(?:comer\s+)?"
                                       r"(?:doces?|chocolates?|acucar|brigadeiros?)"),
    ("dor_nas_pernas", "dor nas pernas", r"dor(?:es)?\s+nas\s+pernas|pernas\s+doendo"),
    ("dor_de_barriga", "dor de barriga", r"dor(?:es)?\s+(?:de|na)\s+barriga|barriga\s+doendo"),
    ("tontura", "tontura", r"tonturas?"),
    ("insonia", "insônia", r"insonia|sem\s+conseguir\s+dormir|nao\s+(?:conseguiu|consegue)\s+dormir"),
))
ROTULOS_SINTOMAS = {k: r for k, r, _ in _SINTOMAS}
_NEGACAO_SINTOMA = re.compile(r"\b(?:sem|nao|nem|nenhuma|nenhum|zero)\s+(?:[\w-]+\s+){0,2}$")
_CONDICAO_ANTES = re.compile(r"\b(?:quando|se|sempre\s+que|toda\s+vez\s+que|caso)\b")


_FLUXO = re.compile(r"\bfluxo\b(?P<meio>[^.;!?]{0,25}?)\b(?P<n>leve|fraco|fraquinho|pouco|pouquinho|baixo|moderado"
                    r"|medio|normal|intenso|forte|fortissimo|grande|alto|abundante|pesado)\b")
_NIVEL_FLUXO = {"leve": "leve", "fraco": "leve", "fraquinho": "leve", "pouco": "leve", "pouquinho": "leve",
                "baixo": "leve", "moderado": "moderado", "medio": "moderado", "normal": "moderado"}

# O que ajuda (ou não ajuda), só numa frase sobre o ciclo.
_VERBO_AJUDA = (r"(?:ajuda|ajudam|ajudou|ajudaram|alivia|aliviam|aliviou|melhora|melhoram|melhorou|faz\s+bem"
                r"|fazem\s+bem|fez\s+bem|acalma|acalmam|acalmou|resolve|resolveu)")
_VERBO_ATRAPALHA = r"(?:atrapalha|atrapalham|atrapalhou|piora|pioram|piorou)"
_RE_O_QUE_AJUDA = re.compile(r"\bo\s+que\s+(?:mais\s+)?(?P<neg>nao\s+)?" + _VERBO_AJUDA
                             + r"(?:\s+(?:ela|muito|mais|bastante|de\s+verdade))*\s+(?:e|eh|sao|seria|foi)\s+")
_RE_VERBO_AJUDA = re.compile(r"\b(?P<neg>(?:nao|n)\s+)?" + _VERBO_AJUDA + r"\b|\b(?P<ruim>" + _VERBO_ATRAPALHA + r")\b")
_RE_GOSTO = re.compile(r"\b(?P<neg>(?:nao|n)\s+)?(?P<v>gosta|curte|adora|ama|prefere|pede|quer|odeia|detesta|suporta)\s+"
                       r"(?:(?:muito|mais|bastante)\s+)?(?:de\s+|(?P<eu>(?:que|quando)\s+eu\s+))?")
_NEGATIVOS_GOSTO = frozenset({"odeia", "detesta", "suporta"})
_AJUDA_VAZIA = frozenset({"isso", "nada", "tudo", "ela", "so", "isso ai", "o que", "muito", "bastante", "mais",
                          "eu", "ele", "a gente", "nao", "sim", "que"})
_FIM_AJUDA = re.compile(r"\s+(?:quando|se|enquanto|porque|pq|nos\s+dias|na\s+hora|durante)\b|[,.;!?:]")
_INICIO_AJUDA = re.compile(r"\s+|(?:e|que(?!\s+eu\b)|entao|ai|tipo|so|ela\s+disse\s+que|disse\s+que|ela\s+falou\s+que"
                           r"|falou\s+que|ela\s+contou\s+que)\s+")


@dataclass
class _Frase:
    ini: int
    fim: int
    texto: str
    p: str = ""
    b: str = ""
    marcador: bool = False      # fala do ciclo (marcador forte, fraco ou um acontecimento)
    forte: bool = False         # tem palavra forte (menstruação, TPM...): sem nada achado, vira comentário
    dela: bool = False

    def __post_init__(self) -> None:
        self.p = plano(self.texto)
        self.b = "".join(c.lower()[:1] or c for c in self.texto)


def _clausulas(fr: _Frase) -> list[tuple[int, int]]:
    """Pedaços da frase (posições na frase), separados por vírgula, "e", "mas"... (como em datas.py:
    sobre o texto COM acento, para "é" não virar "e")."""
    cortes = [0]
    for m in _SEPARADOR.finditer(fr.b):
        cortes.extend((m.start(), m.end()))
    cortes.append(len(fr.texto))
    pares = [(cortes[i], cortes[i + 1]) for i in range(0, len(cortes) - 1, 2)]
    return [(a, b) for a, b in pares if b > a] or [(0, len(fr.texto))]


def _clausula_de(clausulas: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for a, b in clausulas:
        if a <= pos < b:
            return a, b
    anteriores = [c for c in clausulas if c[0] <= pos]
    return anteriores[-1] if anteriores else clausulas[0]


def _dono(fr: _Frase, cl_ini: int, pos: int, pessoas: list[tuple[int, int]], fim: int | None = None) -> str:
    """Quem é o dono do que começa em `pos`: "ela" (também implícita), "eu" ou "outra" (sujeito mais
    perto antes, na oração; "minha menstruação" é sua; "a menstruação da Mariana" é da Mariana)."""
    if re.search(r"\b(?:minha|meu|minhas|meus)\s+$", fr.p[max(0, pos - 8):pos]):
        return "eu"
    if fim is not None:
        for ini, _f in pessoas:
            if pos < ini < fim or (fim <= ini <= fim + 6 and re.fullmatch(r"\s+(?:da|do)\s+", fr.p[fim:ini])):
                return "outra"
    # "... quando tá com cólica": depois de "quando" o sujeito é o de antes, quase sempre ela (implícita).
    quando = [m.end() for m in re.finditer(r"\bquando\b", fr.p[cl_ini:pos])]
    inicio_oracao = cl_ini + quando[-1] if quando else cl_ini
    antes = fr.p[inicio_oracao:pos]
    candidatos: list[tuple[int, str]] = []
    for m in re.finditer(r"\bela\b", antes):
        if not _OBJETO_ANTES.search(antes[:m.start()]):
            candidatos.append((m.start(), "ela"))
    for m in re.finditer(r"\beu\b", antes):
        candidatos.append((m.start(), "eu"))
    for ini, fim_p in pessoas:
        # A pessoa dentro da oração, ou um aposto que termina onde a oração começa ("Mariana, a amiga dela, ...").
        if (inicio_oracao <= ini < pos or ini < inicio_oracao <= fim_p + 2) \
                and not _OBJETO_ANTES.search(fr.p[max(0, min(ini, inicio_oracao) - 12):ini]):
            candidatos.append((max(ini - inicio_oracao, 0), "outra"))
    if candidatos:
        return max(candidatos)[1]
    # Sem sujeito na oração: se a frase começa por outra pessoa ("A irmã dela, que mora longe, menstruou"), é dela.
    for ini, _f in pessoas:
        if ini < inicio_oracao and re.fullmatch(r"(?:(?:a|o|e|que|so|ja|hoje|ontem)\s+)*", fr.p[:ini]):
            return "outra"
    return "ela"


def _dentro(trechos: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in trechos)


def _certeza(pos: int, confirmados: list[tuple[int, int]], base: str) -> str:
    if base == "inferencia":
        return "inferencia"
    return "confirmado" if _dentro(confirmados, pos) else "observado"


def _resolver_data(d: Any, referencia: date) -> tuple[date, str, list[date]] | None:
    """A data de um acontecimento que já passou: (data, precisão, alternativas). Data que caiu no
    futuro ("desde segunda" dito na quarta) é lida de novo no passado ("foi segunda"), pelo
    regras/datas.py; se mesmo assim fica no futuro, não é um acontecimento (None)."""
    precisao = getattr(getattr(d, "precisao", None), "value", None) or "dia"
    data = getattr(d, "data", None)
    if not isinstance(data, date):
        return None
    alternativas = [a for a in (getattr(d, "alternativas", None) or []) if isinstance(a, date) and a <= referencia]
    if data > referencia:
        if alternativas:
            data = max(alternativas)
        else:
            refeito = [x for x in extrair_datas("foi " + str(getattr(d, "expressao", "")), referencia)
                       if isinstance(x.data, date) and x.data <= referencia]
            if not refeito:
                return None
            data, precisao = refeito[0].data, refeito[0].precisao.value
            alternativas = [a for a in refeito[0].alternativas if a <= referencia]
    return data, precisao, sorted(set(alternativas) - {data}, reverse=True)


def _datas_da_frase(fr: _Frase, datas: list) -> list[tuple[int, int, Any]]:
    """[(início na frase, fim na frase, DataEncontrada)]."""
    saida = []
    for d in datas or []:
        ini, fim = getattr(d, "inicio", None), getattr(d, "fim", None)
        if isinstance(ini, int) and isinstance(fim, int) and fr.ini <= ini and fim <= fr.fim:
            saida.append((ini - fr.ini, fim - fr.ini, d))
    return saida


def _data_perto(fr: _Frase, clausulas: list[tuple[int, int]], datas_fr: list[tuple[int, int, Any]],
                cl: tuple[int, int], pos: int, sozinha: bool) -> tuple[int, Any] | None:
    """A data da mesma oração mais perto de `pos`. Sem nenhuma, e se `sozinha` (um acontecimento só na
    frase): a única data da frase, quando ela está sozinha na oração dela ("Ontem, ela menstruou")."""
    na_clausula = [(abs(a - pos), a, d) for a, _b, d in datas_fr if cl[0] <= a < cl[1]]
    if na_clausula:
        _, a, d = min(na_clausula, key=lambda x: x[0])
        return a, d
    if sozinha and len(datas_fr) == 1:
        a, b, d = datas_fr[0]
        outra = _clausula_de(clausulas, a)
        resto = (fr.p[outra[0]:a] + " " + fr.p[b:outra[1]]).strip()
        if re.fullmatch(r"(?:(?:foi|desde|e|ja|so|isso|la|tipo)\s*)*", resto):
            return a, d
    return None


def _sintomas_da_frase(fr: _Frase, clausulas: list[tuple[int, int]], pessoas: list[tuple[int, int]],
                       datas_fr: list[tuple[int, int, Any]], referencia: date) -> list[dict]:
    """Sintomas com as palavras do registro (e a data da oração deles, se houver)."""
    achados: list[dict] = []
    usados: list[tuple[int, int]] = []
    for chave, rotulo, rx in _SINTOMAS:
        for m in rx.finditer(fr.p):
            if any(m.start() < b and a < m.end() for a, b in usados):
                continue
            cl = _clausula_de(clausulas, m.start())
            if _NEGACAO_SINTOMA.search(fr.p[cl[0]:m.start()]) or _CAUSA_DEPOIS.search(fr.p[m.end():m.end() + 30]):
                continue
            if _dono(fr, cl[0], m.start(), pessoas) != "ela":
                continue
            # Hábito ou condição não é sintoma de um dia: "ela sempre fica com enxaqueca quando menstrua",
            # "quando está com cólica, chá ajuda" (fica o comentário, ou só o que ajuda).
            if (_HABITO.search(fr.p[cl[0]:cl[1]]) or _CONDICAO_ANTES.search(fr.p[cl[0]:m.start()])
                    or re.search(r"\bquando\b", fr.p[m.end():cl[1]])):
                continue
            usados.append((m.start(), m.end()))
            data = None
            perto = _data_perto(fr, clausulas, datas_fr, cl, m.start(), sozinha=False)
            if perto is not None:
                resolvida = _resolver_data(perto[1], referencia)
                if resolvida is not None:
                    data = resolvida[0]
            achados.append({"chave": chave, "rotulo": rotulo, "palavras": fr.texto[m.start():m.end()],
                            "data": data, "_pos": m.start()})
    achados.sort(key=lambda s: s["_pos"])
    return achados


def _fluxo_da_frase(fr: _Frase) -> tuple[str, str] | None:
    m = _FLUXO.search(fr.p)
    if not m or re.search(r"\bnao\b", m.group("meio")) or _NEGACAO_SINTOMA.search(fr.p[:m.start()]):
        return None
    return _NIVEL_FLUXO.get(m.group("n"), "intenso"), fr.texto[m.start():m.end()]


def _limpar_ajuda(fr: _Frase, ini: int, fim: int) -> tuple[int, int] | None:
    """Recorta "o que ajuda": até o fim da oração, sem "e"/"que"/"ela disse que" no começo. Literal."""
    corte = _FIM_AJUDA.search(fr.p[ini:fim])
    if corte:
        fim = ini + corte.start()
    while ini < fim:
        m = _INICIO_AJUDA.match(fr.p, ini, fim)
        if not m or m.end() == ini:
            break
        ini = m.end()
    while fim > ini and fr.p[fim - 1] in " ,.;:!?":
        fim -= 1
    valor = normalizar(fr.p[ini:fim])
    if not valor or valor in _AJUDA_VAZIA or len(valor) > MAX_PALAVRAS or not re.search(r"[a-z]{2}", valor):
        return None
    if _MARCADOR.fullmatch(valor) or any(rx.fullmatch(valor) for _, _, rx in _SINTOMAS):
        return None
    return ini, fim


def _tem_quando_do_ciclo(texto: str) -> bool:
    """"... quando tá com cólica / de TPM / menstruada": a preferência é ligada ao ciclo."""
    for q in re.finditer(r"\bquando\b", texto):
        resto = texto[q.end():q.end() + 60]
        if _MARCADOR.search(resto) or any(rx.search(resto) for _, _, rx in _SINTOMAS) \
                or re.search(r"\b(?:ta|esta|fica)\s+(?:menstruada|naqueles\s+dias|de\s+chico)\b", resto):
            return True
    return False


def _ajudas_da_frase(fr: _Frase, clausulas: list[tuple[int, int]],
                     pessoas: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """[(início do valor, fim do valor, polaridade, posição do verbo)] na frase."""
    saida: list[tuple[int, int, int, int]] = []
    vistos: list[tuple[int, int]] = []

    def guardar(ini: int, fim: int, polaridade: int, pos: int) -> None:
        limpo = _limpar_ajuda(fr, ini, fim)
        if limpo is None or any(limpo[0] < b and a < limpo[1] for a, b in vistos):
            return
        vistos.append(limpo)
        saida.append((limpo[0], limpo[1], polaridade, pos))

    for m in _RE_O_QUE_AJUDA.finditer(fr.p):
        guardar(m.end(), len(fr.p), -1 if m.group("neg") else 1, m.start())
    for m in _RE_VERBO_AJUDA.finditer(fr.p):
        if re.search(r"\bo\s+que\s+(?:mais\s+)?(?:nao\s+)?$", fr.p[:m.start()]):
            continue
        cl = _clausula_de(clausulas, m.start())
        if _dono(fr, cl[0], m.start(), pessoas) == "outra":
            continue
        ini = cl[0]
        pedaco = fr.p[ini:m.start()]
        if re.search(r"\bquando\b", pedaco):
            # "quando ela tá com cólica a bolsa ajuda": o que ajuda vem depois do sintoma/da palavra do ciclo.
            fim_contexto = 0
            for rx in [_MARCADOR] + [s[2] for s in _SINTOMAS]:
                for x in rx.finditer(pedaco):
                    fim_contexto = max(fim_contexto, x.end())
            if not fim_contexto:
                continue
            ini += fim_contexto
        guardar(ini, m.start(), -1 if (m.group("neg") or m.group("ruim")) else 1, m.start())
    for m in _RE_GOSTO.finditer(fr.p):
        verbo = m.group("v")
        if verbo == "suporta" and not m.group("neg"):
            continue
        cl = _clausula_de(clausulas, m.start())
        if _dono(fr, cl[0], m.start(), pessoas) != "ela" or not _tem_quando_do_ciclo(fr.p):
            continue
        negativo = (verbo in _NEGATIVOS_GOSTO) != bool(m.group("neg")) if verbo != "suporta" else True
        ini = m.start("eu") if m.group("eu") else m.end()   # "odeia que eu fique perguntando": o "que eu" fica
        guardar(ini, len(fr.p), -1 if negativo else 1, m.start())
    return saida


def _evento_do_periodo(fr: _Frase) -> list[tuple[str, int, int]]:
    """Os pedaços da frase que falam do período: [(tipo, início, fim)], sem sobreposição."""
    achados: list[tuple[str, int, int]] = []
    tem_marcador = bool(_MARCADOR.search(fr.p))
    for tipo, rx in _PERIODO:
        for m in rx.finditer(fr.p):
            if any(m.start() < b and a < m.end() for _, a, b in achados):
                continue
            if tipo == "durante_n":
                if not tem_marcador:
                    continue              # "tá no segundo dia" só vale numa frase sobre o ciclo
                tipo = "durante"
            if tipo == "desceu":
                if not _DESCEU_DEPOIS.match(fr.p, m.end()):
                    continue
                cl_ini = max([0] + [x.end() for x in _SEPARADOR.finditer(fr.b, 0, m.start())])
                antes = fr.p[cl_ini:m.start()].strip()
                if not tem_marcador and not _ANTES_DE_DESCEU.fullmatch(antes):
                    continue              # "ela desceu.", "o elevador desceu": foi alguém que desceu
                tipo = "inicio"
            achados.append((tipo, m.start(), m.end()))
    if tem_marcador or achados:
        for m in _FIM_SOLTO.finditer(fr.p):
            if any(m.start() < b and a < m.end() for _, a, b in achados):
                continue
            cl_ini = max([0] + [x.end() for x in _SEPARADOR.finditer(fr.b, 0, m.start())])
            antes = fr.p[cl_ini:m.start()].strip()
            antes = re.sub(r"^(?:ainda\s+)?(?:nao|n)\s*", "", antes)
            if _ANTES_DE_FIM_SOLTO.fullmatch(antes):
                achados.append(("fim", m.start(), m.end()))   # "e já parou ontem" (sem outro sujeito)
    return sorted(achados, key=lambda x: x[1])


def _frase_e_dela(fr: _Frase, pessoas: list[tuple[int, int]]) -> bool:
    """A frase fala do ciclo DELA (e não de outra pessoa ou do seu)?"""
    clausulas = _clausulas(fr)
    for tipo, ini, fim in _evento_do_periodo(fr):
        if tipo != "eu" and _dono(fr, _clausula_de(clausulas, ini)[0], ini, pessoas, fim) == "ela":
            return True
    for m in [*_MARCADOR.finditer(fr.p), *_COLICA.finditer(fr.p), *_FLUXO_MARCADOR.finditer(fr.p)]:
        if any(a <= m.start() < b for t, a, b in _evento_do_periodo(fr) if t == "eu"):
            continue
        if _dono(fr, _clausula_de(clausulas, m.start())[0], m.start(), pessoas, m.end()) == "ela":
            return True
    return False


def _extra(tipo: str, fr: _Frase, resumo: str, certeza: str, **campos: Any) -> dict:
    extra = {"tipo": tipo, "resumo": resumo, "trecho": fr.texto, "certeza": certeza, "de_quem": "ela"}
    extra.update(campos)
    return extra


_RESUMO_MENCAO = {
    "tpm": "Comentário sobre TPM", "atraso": "Comentário sobre o ciclo", "habito": "Como costuma ser",
    "duvida": "Comentário sobre o ciclo (sem certeza)", "futuro": "Comentário sobre o ciclo (sem data)",
    "geral": "Comentário sobre o ciclo", "outra": "Sobre o ciclo de outra pessoa (não entra no 🌙 dela)",
}


def _periodos_da_frase(fr: _Frase, referencia: date, pessoas: list[tuple[int, int]],
                       datas_fr: list[tuple[int, int, Any]], clausulas: list[tuple[int, int]],
                       base: str, confirmados: list[tuple[int, int]]) -> tuple[list[dict], list[tuple[str, int, int]]]:
    """(acontecimentos com data: início, durante, fim) e (comentários sem data)."""
    nao_afirmados = trechos_nao_afirmados(fr.texto)
    periodo = _evento_do_periodo(fr)
    eventos: list[dict] = []
    mencoes: list[tuple[str, int, int]] = []
    com_data = sum(1 for t, _, _ in periodo if t in ("inicio", "fim", "durante", "durante_fim_negado"))
    for tipo, ini, fim in periodo:
        cl = _clausula_de(clausulas, ini)
        antes = fr.p[cl[0]:ini]
        if tipo == "eu":
            mencoes.append(("outra", ini, fim))
            continue
        dono = _dono(fr, cl[0], ini, pessoas, fim)
        if dono != "ela":
            mencoes.append(("outra", ini, fim))
            continue
        if tipo in ("tpm", "atraso"):
            mencoes.append((tipo, ini, fim))
            continue
        if _dentro(nao_afirmados, ini):
            mencoes.append(("duvida", ini, fim))
            continue
        if _HABITO.search(fr.p[cl[0]:cl[1]]):
            mencoes.append(("habito", ini, fim))
            continue
        if _HIPOTESE_ANTES.search(antes):
            mencoes.append(("futuro", ini, fim))
            continue
        if tipo == "durante_fim_negado":
            tipo = "durante"                       # "a menstruação ainda não acabou": continua
        elif _NEGACAO_ANTES.search(antes):
            if tipo == "inicio":
                mencoes.append(("atraso", ini, fim))   # "ainda não desceu": sem data, sem dizer "atraso"
                continue
            if tipo == "fim":
                tipo = "durante"                   # "ainda não acabou a menstruação"
            else:
                mencoes.append(("geral", ini, fim))    # "não tá menstruada"
                continue
        perto = _data_perto(fr, clausulas, datas_fr, cl, ini, sozinha=com_data == 1)
        resolvida = _resolver_data(perto[1], referencia) if perto is not None else None
        if perto is not None and resolvida is None:
            mencoes.append(("futuro", ini, fim))   # a data ainda não chegou: não aconteceu
            continue
        data, precisao, alternativas = resolvida if resolvida else (referencia, "dia", [])
        evento = {"tipo": tipo, "ini": ini, "fim": fim, "data": data, "precisao": precisao,
                  "alternativas": alternativas, "certeza": _certeza(ini, confirmados, base),
                  "expressao": str(getattr(perto[1], "expressao", "")) if perto is not None else None,
                  "sem_data": perto is None, "inicio": None, "dia_do_ciclo": None}
        if tipo == "durante":
            if perto is not None and (re.search(r"\bdesde\s+(?:a\s+|o\s+)?$", fr.p[max(0, perto[0] - 9):perto[0]])
                                      or re.match(r"(?:ha|faz)\s", fr.p[perto[0]:])):
                evento["inicio"], evento["data"] = data, referencia     # "desde segunda", "há 3 dias"
            n = _DIA_N.search(fr.p)
            if n and evento["inicio"] is None:
                dia_n = int(n.group("n")) if n.group("n") else _ORDINAIS[n.group("o")]
                if 1 <= dia_n <= DURACAO_MAXIMA:
                    evento["dia_do_ciclo"] = dia_n
                    evento["inicio"] = evento["data"] - timedelta(days=dia_n - 1)
        eventos.append(evento)
    # Um início e um "tá menstruada" na mesma frase contam a mesma coisa: fica o início.
    if any(e["tipo"] == "inicio" for e in eventos):
        eventos = [e for e in eventos if e["tipo"] != "durante"]
    unicos: list[dict] = []
    for e in eventos:
        if all(u["tipo"] != e["tipo"] for u in unicos):
            unicos.append(e)
    return unicos, mencoes


def _extras_da_frase(fr: _Frase, referencia: date, pessoas: list[tuple[int, int]],
                     datas_fr: list[tuple[int, int, Any]], vizinha: bool) -> list[dict]:
    clausulas = _clausulas(fr)
    base = classificar_certeza(fr.texto).value
    confirmados = trechos_confirmados(fr.texto)
    eventos, mencoes = _periodos_da_frase(fr, referencia, pessoas, datas_fr, clausulas, base, confirmados)
    tipos_mencao = {t for t, _, _ in mencoes}
    so_de_outra = bool(mencoes) and tipos_mencao == {"outra"} and not eventos
    dela = bool(eventos) or bool(tipos_mencao - {"outra"}) or (fr.marcador and fr.dela) \
        or (vizinha and not fr.marcador and not _OUTRA_CAUSA.search(fr.p))
    saida: list[dict] = []

    sintomas: list[dict] = []
    fluxo = None
    if dela and not so_de_outra:
        sintomas = _sintomas_da_frase(fr, clausulas, pessoas, datas_fr, referencia)
        fluxo = _fluxo_da_frase(fr)
        for s in sintomas:
            if s["data"] is None:
                # Na mesma oração do acontecimento ("menstruou ontem com cólica"), a data dele; senão, o dia do registro.
                junto = next((e for e in eventos if not e["sem_data"] and e["tipo"] != "durante"
                              and _clausula_de(clausulas, e["ini"]) == _clausula_de(clausulas, s["_pos"])), None)
                s["data"] = junto["data"] if junto else referencia
            s["data"] = s["data"].isoformat()
            del s["_pos"]

    for e in eventos:
        exatidao = "talvez" if e["certeza"] == "inferencia" else ("dia" if e["precisao"] == "dia" else "aproximada")
        campos: dict[str, Any] = {
            "data": e["data"].isoformat(), "precisao": e["precisao"], "exatidao": exatidao,
            "data_expressao": e["expressao"], "alternativas": [a.isoformat() for a in e["alternativas"]],
            "sem_data": e["sem_data"], "motivo": f"Você escreveu “{fr.texto[e['ini']:e['fim']].strip()}”.",
        }
        aproximado = " (aproximado)" if exatidao == "aproximada" else ""
        if e["tipo"] == "inicio":
            tipo, resumo = EXTRA_INICIO, f"Início: {data_br(e['data'])}{aproximado}"
        elif e["tipo"] == "fim":
            tipo, resumo = EXTRA_FIM, f"Fim: {data_br(e['data'])}{aproximado}"
        else:
            tipo = EXTRA_DURANTE
            campos["inicio"] = e["inicio"].isoformat() if e["inicio"] else None
            if e["dia_do_ciclo"]:
                campos["dia_do_ciclo"] = e["dia_do_ciclo"]
            resumo = (f"Em andamento em {data_br(e['data'])} (começou em {data_br(e['inicio'])})" if e["inicio"]
                      else f"Em andamento em {data_br(e['data'])} (início não informado)")
        if not saida and sintomas:
            campos["sintomas"] = sintomas
            resumo += " · " + _juntar([s["palavras"] for s in sintomas])
        if not saida and fluxo:
            campos["fluxo"] = fluxo[0]
            resumo += f" · fluxo {fluxo[0]}"
        saida.append(_extra(tipo, fr, resumo, e["certeza"], **campos))

    if not saida and sintomas:
        pos = fr.p.find(plano(sintomas[0]["palavras"]))
        extra = _extra(EXTRA_SINTOMA, fr, "Sintomas: " + _juntar([s["palavras"] for s in sintomas]),
                       _certeza(pos, confirmados, base), data=sintomas[0]["data"], sintomas=sintomas,
                       motivo=f"Você escreveu “{sintomas[0]['palavras']}”.")
        if fluxo:
            extra["fluxo"] = fluxo[0]
            extra["resumo"] += f" · fluxo {fluxo[0]}"
        saida.append(extra)
    elif not saida and fluxo:
        saida.append(_extra(EXTRA_FLUXO, fr, f"Fluxo {fluxo[0]}", _certeza(fr.p.find("fluxo"), confirmados, base),
                            data=referencia.isoformat(), fluxo=fluxo[0], motivo=f"Você escreveu “{fluxo[1]}”."))

    if dela and not so_de_outra:
        for ini, fim, polaridade, pos in _ajudas_da_frase(fr, clausulas, pessoas):
            valor = fr.texto[ini:fim].strip()
            saida.append(_extra(EXTRA_AJUDA, fr, ("Ajuda: " if polaridade > 0 else "Não ajuda: ") + valor,
                                _certeza(pos, confirmados, base), data=referencia.isoformat(), valor=valor,
                                polaridade=polaridade,
                                motivo=f"Você escreveu “{fr.texto[min(ini, pos):max(fim, pos)].strip()}”."))

    # Comentário sem data (ou de outra pessoa): o registro fica 🔒 do mesmo jeito.
    if "tpm" in tipos_mencao or (not saida and (mencoes or (fr.forte and fr.dela))):
        ordem = ("tpm", "atraso", "habito", "duvida", "futuro", "geral")
        valor = next((t for t in ordem if t in tipos_mencao), "outra" if mencoes else "geral")
        if valor == "geral" and not mencoes:
            valor = "habito" if _HABITO.search(fr.p) else "futuro" if re.search(
                r"\b(?:vai|vao|deve|deveria|pode)\s+(?:\w+\s+)?(?:menstruar|descer|vir|chegar)\b", fr.p) else "geral"
        if not mencoes or (valor == "geral" and "geral" not in tipos_mencao):
            m = _MARCADOR.search(fr.p)
            ini, fim = (m.start(), m.end()) if m else (0, len(fr.texto))
        else:
            ini, fim = next((a, b) for t, a, b in mencoes if t == valor)
        extra = _extra(EXTRA_MENCAO, fr, _RESUMO_MENCAO[valor], _certeza(ini, confirmados, base),
                       data=referencia.isoformat(), valor=valor, motivo=f"Você escreveu “{fr.texto[ini:fim].strip()}”.")
        if valor == "outra":
            extra["de_quem"] = "outra"
        saida.append(extra)
    return saida


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Extras do ciclo para a revisão: um por acontecimento (início, durante, fim) com os sintomas e o
    fluxo da frase; o que ajuda; sintomas numa frase vizinha; comentários sem data (`ciclo_mencao`)."""
    if not isinstance(texto, str) or not texto.strip() or not isinstance(referencia, date):
        return []
    spans = []
    for pe in pessoas or []:
        ini, fim = getattr(pe, "inicio", None), getattr(pe, "fim", None)
        if isinstance(ini, int) and isinstance(fim, int):
            spans.append((ini, fim))
    lista = [_Frase(i, f, t) for i, f, t in frases(texto)]
    pessoas_de: list[list[tuple[int, int]]] = []
    for fr in lista:
        pessoas_fr = [(a - fr.ini, b - fr.ini) for a, b in spans if fr.ini <= a < fr.fim]
        pessoas_de.append(pessoas_fr)
        fr.forte = bool(_MARCADOR.search(fr.p))
        fr.marcador = (fr.forte or bool(_COLICA.search(fr.p) or _FLUXO_MARCADOR.search(fr.p))
                       or any(t in ("inicio", "eu") for t, _, _ in _evento_do_periodo(fr)))
        fr.dela = fr.marcador and _frase_e_dela(fr, pessoas_fr)
    saida: list[dict] = []
    for n, fr in enumerate(lista):
        vizinha = any(lista[k].marcador and lista[k].dela for k in (n - 1, n + 1) if 0 <= k < len(lista))
        if fr.marcador or vizinha:
            saida.extend(_extras_da_frase(fr, referencia, pessoas_de[n], _datas_da_frase(fr, datas), vizinha))
    if not saida:
        # Um registro que não fala do ciclo pode marcar algo para os dias previstos: o aviso de
        # planejamento entra na revisão dele (20.8). Com dado do ciclo no texto, não (já é desse assunto).
        saida.extend(_planejamento_na_revisao(itens, referencia, getattr(conhecimento, "consultar", None)))
    return saida[:50]


class _SoLeitura:
    """O pedaço do Banco que a previsão usa (`consultar`), a partir da leitura que o `extrair` recebe."""

    def __init__(self, consultar: Callable[..., list[dict]]):
        self.consultar = consultar


def _planejamento_na_revisao(itens: list, referencia: date, consultar: Any) -> list[dict]:
    """Acontecimento do texto que cai nos dias previstos, quando há algo que ela disse (🟢) ou um padrão
    nos registros: o mesmo aviso do cartão 🌙, na revisão (nunca no Hoje). Só informação: não é dado do
    ciclo, não deixa o registro 🔒 e não grava nada. Sem a leitura do diário (o motor sozinho), nada."""
    if not callable(consultar):
        return []
    try:
        leitor = _SoLeitura(consultar)
        ciclos, eventos = _ciclos(leitor), _eventos(leitor)
        calc = calcular(ciclos, referencia)
        previsao, dur = calc["previsao"], calc["duracao"]
        padroes = padroes_sintomas(ciclos, eventos, referencia)
    except Exception as erro:  # noqa: BLE001 - sem a previsão, a revisão segue sem o aviso
        registrar_erro(erro, NOME, "planejamento na revisão")
        return []
    if not previsao or previsao.get("passou"):
        return []
    disse = [e for e in eventos if e.de_quem == "ela" and e.certeza == "confirmado"
             and e.tipo in ("sintoma", "ajuda", "fluxo", "mencao")]
    if not disse and not padroes:
        return []
    inicio = max(previsao["inicio"], referencia)
    fim = previsao["fim"] + timedelta(days=(dur["media"] if dur else DURACAO_PADRAO) - 1)
    saida = []
    for it in itens or []:
        tipo = getattr(getattr(it, "tipo", None), "value", getattr(it, "tipo", None))
        quando = getattr(it, "data_evento", None)
        precisao = getattr(getattr(it, "precisao", None), "value", getattr(it, "precisao", None))
        trecho = getattr(it, "trecho", None) or ""
        if (tipo != "evento" or not isinstance(quando, date) or not inicio <= quando <= fim
                or precisao not in (None, "dia") or getattr(it, "pessoa", None) or not trecho.strip()
                or _TEXTO_DO_CICLO.search(plano(trecho))):
            continue
        lembrar = [p["texto"] for p in padroes[:2]]
        for e in sorted(disse, key=lambda e: (e.data, e.id or 0), reverse=True)[:2]:
            if e.tipo == "ajuda":
                lembrar.append(f"Ela disse em {_dia(e.data, referencia)} que "
                               f"{'ajuda' if (e.polaridade or 1) > 0 else 'não ajuda'}: {e.valor}.")
            elif e.tipo == "sintoma":
                lembrar.append(f"Ela comentou em {_dia(e.data, referencia)}: "
                               f"{e.palavras or ROTULOS_SINTOMAS.get(e.valor or '', '')}.")
        texto = (f"Cai perto dos dias previstos do 🌙 ({_dia(previsao['inicio'], referencia)} a "
                 f"{_dia(previsao['fim'], referencia)}). Talvez valha ter isso em mente ao planejar.")
        saida.append({"tipo": EXTRA_PLANEJAMENTO, "resumo": texto, "trecho": trecho, "data": quando,
                      "lembrar": lembrar, "informativo": True})
        break                                        # um aviso basta
    return saida


def marca_privado(extras: list[dict]) -> bool:
    """Todo dado do ciclo deixa o registro 🔒 (fora da IA). O aviso de planejamento não é dado do ciclo."""
    return any(e.get("tipo") != EXTRA_PLANEJAMENTO for e in extras)


# ================================================================ gravar

@dataclass
class _Extra:
    tipo: str                      # tipo do evento ("inicio"...)
    trecho: str
    data: date
    certeza: str = "observado"
    exatidao: str = "dia"
    de_quem: str = "ela"
    valor: str | None = None
    palavras: str | None = None
    polaridade: int | None = None
    inicio: date | None = None
    fluxo: str | None = None
    sintomas: list[dict] = field(default_factory=list)


def _data_do_extra(v: Any, limite: date, padrao: date) -> date:
    d = _data(v) if v not in (None, "") else padrao
    if d > limite:
        raise ValueError(DATA_DEPOIS_DO_REGISTRO)
    if (limite - d).days > 400:
        raise ValueError(DATA_LONGE_DO_REGISTRO)
    return d


def _texto_curto(v: Any, maximo: int, erro: str) -> str:
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > maximo:
        raise ValueError(erro)
    return re.sub(r"\s+", " ", v.strip())


def _validar_extra(e: dict, referencia: date) -> _Extra:
    """O extra confirmado, conferido de novo (a tela pode ter mudado a data). ValueError = 400."""
    tipo = _EVENTO_DO_EXTRA.get(e.get("tipo"))
    if tipo is None:
        raise ValueError(TIPO_INVALIDO)
    certeza = e.get("certeza") or "observado"
    if certeza not in CERTEZAS:
        raise ValueError(CERTEZA_INVALIDA)
    data = _data_do_extra(e.get("data"), referencia, referencia)
    precisao = e.get("precisao") or "dia"
    exatidao = "talvez" if certeza == "inferencia" else ("dia" if precisao == "dia" else "aproximada")
    v = _Extra(tipo=tipo, trecho=str(e.get("trecho") or ""), data=data, certeza=certeza, exatidao=exatidao,
               de_quem="outra" if e.get("de_quem") == "outra" else "ela")
    if tipo == "mencao":
        v.valor = e.get("valor") if e.get("valor") in MENCOES else "geral"
        if v.valor == "outra":
            v.de_quem = "outra"
        return v
    if tipo == "durante":
        if e.get("inicio") not in (None, ""):
            v.inicio = _data_do_extra(e.get("inicio"), data, data)
            if (data - v.inicio).days > DURACAO_MAXIMA:
                raise ValueError(FIM_LONGE)
        elif e.get("dia_do_ciclo") not in (None, ""):
            n = e.get("dia_do_ciclo")
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= DURACAO_MAXIMA:
                raise ValueError(DIA_INVALIDO)
            v.inicio = data - timedelta(days=n - 1)
    fluxo = e.get("fluxo")
    if fluxo not in (None, ""):
        if fluxo not in FLUXOS:
            raise ValueError(FLUXO_INVALIDO)
        v.fluxo = fluxo
    if tipo == "fluxo":
        if v.fluxo is None:
            raise ValueError(FLUXO_INVALIDO)
        v.valor = v.fluxo
    sintomas = e.get("sintomas")
    if sintomas not in (None, []):
        if not isinstance(sintomas, list) or len(sintomas) > MAX_SINTOMAS_POR_EXTRA:
            raise ValueError(SINTOMAS_INVALIDOS)
        for s in sintomas:
            if not isinstance(s, dict) or s.get("chave") not in ROTULOS_SINTOMAS:
                raise ValueError(SINTOMAS_INVALIDOS)
            palavras = s.get("palavras") or ROTULOS_SINTOMAS[s["chave"]]
            v.sintomas.append({"chave": s["chave"], "palavras": _texto_curto(palavras, MAX_PALAVRAS, SINTOMAS_INVALIDOS),
                               "data": _data_do_extra(s.get("data"), referencia, data)})
    if tipo == "sintoma" and not v.sintomas:
        raise ValueError(SINTOMAS_INVALIDOS)
    if tipo == "ajuda":
        v.valor = _texto_curto(e.get("valor"), MAX_AJUDA, AJUDA_INVALIDA)
        v.palavras = v.valor
        pol = e.get("polaridade", 1)
        if isinstance(pol, bool) or pol not in (1, -1):
            raise ValueError(POLARIDADE_INVALIDA)
        v.polaridade = pol
    return v


def _inserir_evento(banco: Any, rid: int | None, v: _Extra, tipo: str, data: date, agora: str,
                    ciclo_id: int | None, valor: str | None = None, palavras: str | None = None,
                    polaridade: int | None = None) -> int:
    return banco.inserir("ciclo_eventos", {
        "data": data, "tipo": tipo, "valor": valor, "trecho": v.trecho, "registro_id": rid, "criado_em": agora,
        "ciclo_id": ciclo_id, "palavras": palavras, "polaridade": polaridade, "certeza": v.certeza,
        "de_quem": v.de_quem}, historico=False)


_ORDEM_EXATIDAO = {"dia": 2, "aproximada": 1, "talvez": 0}


def _registrar_inicio(banco: Any, data: date, exatidao: str, rid: int | None, agora: str) -> int:
    """O ciclo deste início: o mesmo se já há um a até MESMO_CICLO dias (fica a data mais certa e,
    entre duas exatas, a mais cedo; ciclo anotado à mão no cartão não muda); senão, um novo."""
    perto = [c for c in _ciclos(banco) if abs((c.inicio - data).days) <= MESMO_CICLO]
    if perto:
        c = min(perto, key=lambda c: (abs((c.inicio - data).days), -(c.id or 0)))
        melhor = _ORDEM_EXATIDAO[exatidao] > _ORDEM_EXATIDAO[c.exatidao]
        mais_cedo = _ORDEM_EXATIDAO[exatidao] == _ORDEM_EXATIDAO[c.exatidao] and data < c.inicio
        if (melhor or mais_cedo) and c.origem != "manual":
            banco.atualizar("ciclos", c.id, {"inicio": data, "exatidao": exatidao}, historico=False)
        return c.id
    return banco.inserir("ciclos", {"inicio": data, "criado_em": agora, "exatidao": exatidao, "origem": "registro",
                                    "registro_id": rid}, historico=False)


def _ciclo_em(banco: Any, data: date) -> int | None:
    """O ciclo em andamento na data (início até ela; até o fim, ou sem fim e até 12 dias)."""
    candidatos = [c for c in _ciclos(banco) if c.inicio <= data
                  and (data - c.inicio).days <= ((c.fim - c.inicio).days if c.fim else DURACAO_MAXIMA - 3)]
    return max(candidatos, key=lambda c: c.inicio).id if candidatos else None


def _registrar_fim(banco: Any, data: date, rid: int | None) -> int | None:
    candidatos = [c for c in _ciclos(banco) if c.inicio <= data and (data - c.inicio).days <= DURACAO_MAXIMA]
    if not candidatos:
        return None
    c = max(candidatos, key=lambda c: c.inicio)
    if c.fim is None or data >= c.fim:
        banco.atualizar("ciclos", c.id, {"fim": data, "fim_registro_id": rid}, historico=False)
    return c.id


def _gravar_um(banco: Any, rid: int | None, v: _Extra, agora: str) -> None:
    if v.de_quem == "outra" or v.tipo == "mencao":
        _inserir_evento(banco, rid, v, "mencao", v.data, agora, None, valor=v.valor or "geral")
        return
    if v.tipo == "inicio":
        ciclo_id = _registrar_inicio(banco, v.data, v.exatidao, rid, agora)
    elif v.tipo == "durante":
        if v.inicio is not None:
            ciclo_id = _registrar_inicio(banco, v.inicio, "talvez" if v.exatidao == "talvez" else "dia", rid, agora)
        else:
            ciclo_id = _ciclo_em(banco, v.data) or _registrar_inicio(
                banco, v.data, "talvez" if v.exatidao == "talvez" else "aproximada", rid, agora)
    elif v.tipo == "fim":
        ciclo_id = _registrar_fim(banco, v.data, rid)
    else:
        ciclo_id = _ciclo_em(banco, v.data)
    if v.tipo != "sintoma":
        # valor: no início, a exatidão da data; no "durante", o início que o registro disse (se disse).
        valor = {"fluxo": v.valor, "ajuda": v.valor, "inicio": v.exatidao,
                 "durante": v.inicio.isoformat() if v.inicio is not None else None}.get(v.tipo)
        _inserir_evento(banco, rid, v, v.tipo, v.data, agora, ciclo_id, valor=valor, palavras=v.palavras,
                        polaridade=v.polaridade)
    for s in v.sintomas:
        _inserir_evento(banco, rid, v, "sintoma", s["data"], agora, _ciclo_em(banco, s["data"]) or ciclo_id,
                        valor=s["chave"], palavras=s["palavras"])
    if v.fluxo and v.tipo != "fluxo":
        _inserir_evento(banco, rid, v, "fluxo", v.data, agora, ciclo_id, valor=v.fluxo)


# Itens que explicariam humor ou comportamento pelo ciclo (20.8): saem sempre da frase do ciclo.
_TIPOS_DE_HUMOR = ("estado", "padrao", "apoio")


def _afastar_itens(banco: Any, rid: int, trechos: list[str]) -> None:
    """Os itens das regras tirados da mesma frase de um dado do ciclo ficam "descartados": assim não
    entram em "Como ela está", no Hoje nem nas ligações com outros registros (continuam no registro,
    e dá para reativá-los nele). A nota da frase fica como está, e um acontecimento (ou pendência,
    gosto...) com o resumo neutro também: "Consulta — 01/10" continua lembrada no Hoje, sem citar a
    frase (o registro é discreto). Humor, padrão e o que ajuda saem sempre: o ciclo não explica nada."""
    trechos = [t for t in trechos if t]
    if not trechos:
        return
    for it in banco.consultar("SELECT id, tipo, resumo, alvo, trecho FROM itens WHERE registro_id = ? "
                              "AND status = 'ativo'", (rid,)):
        trecho = it.get("trecho") or ""
        if it.get("tipo") == "nota" or not trecho:
            continue
        if not any(t in trecho or trecho in t for t in trechos):
            continue
        neutro = sem_palavras_do_ciclo(" ".join(x for x in (it.get("resumo"), it.get("alvo")) if x))
        if it.get("tipo") in _TIPOS_DE_HUMOR or not neutro:
            banco.atualizar_item(it["id"], status="descartado")


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava o que foi confirmado na revisão (na transação do registro). ValueError = 400, nada gravado.
    O aviso de planejamento é só informação: confirmado ou não, não grava nada."""
    extras = [e for e in extras if e.get("tipo") != EXTRA_PLANEJAMENTO]
    if not extras:
        return
    conferidos = [_validar_extra(e, referencia) for e in extras]
    agora = _agora()
    for v in conferidos:
        _gravar_um(banco, registro_id, v, agora)
    _afastar_itens(banco, registro_id, [v.trecho for v in conferidos])
    banco.esquecer_alertas(f"{NOME}:")


# ================================================================ previsão

def intervalos(ciclos: list[Ciclo], referencia: date) -> list[dict]:
    """Cada intervalo entre dois inícios seguidos, com a situação no cálculo: usado | aproximado (um
    dos inícios não tem a data exata) | curto | pausa | esquecido (≈ 2× a mediana: faltou registrar um
    mês?) | longo_confirmado (você disse que foi assim) | fora_do_padrao."""
    lista = sorted([c for c in ciclos if c.inicio <= referencia], key=lambda c: (c.inicio, c.id or 0))
    saida = []
    for a, b in zip(lista, lista[1:]):
        dias = (b.inicio - a.inicio).days
        situacao = "usado"
        if a.exatidao != "dia" or b.exatidao != "dia":
            situacao = "aproximado"
        elif dias < INTERVALO_MINIMO:
            situacao = "curto"
        elif dias > INTERVALO_MAXIMO:
            situacao = "pausa"
        saida.append({"de": a.inicio, "ate": b.inicio, "dias": dias, "de_id": a.id, "ate_id": b.id,
                      "situacao": situacao, "confirmado": b.intervalo_confirmado})
    validos = [i for i in saida if i["situacao"] == "usado"]
    if len(validos) >= 3:
        m = _mediana([i["dias"] for i in validos])
        for i in validos:
            if m > 0 and ESQUECIDO[0] <= i["dias"] / m <= ESQUECIDO[1]:
                i["situacao"] = "longo_confirmado" if i["confirmado"] else "esquecido"
    restantes = [i for i in saida if i["situacao"] == "usado"]
    if len(restantes) > CICLOS_MINIMOS:
        m = _mediana([i["dias"] for i in restantes])
        limite = max(FORA_DO_PADRAO_DIAS, 0.3 * m)
        fora = [i for i in restantes if abs(i["dias"] - m) > limite]
        if fora and len(restantes) - len(fora) >= CICLOS_MINIMOS and len(fora) * 3 <= len(restantes):
            for i in fora:
                i["situacao"] = "fora_do_padrao"
    return saida


def duracao(ciclos: list[Ciclo], referencia: date) -> dict | None:
    """Duração pelos fins registrados (inícios exatos): média, menor, maior e em quantos ciclos."""
    duracoes = [(c.fim - c.inicio).days + 1 for c in sorted(ciclos, key=lambda c: c.inicio)
                if c.exatidao == "dia" and c.fim and c.inicio <= referencia
                and 0 <= (c.fim - c.inicio).days <= DURACAO_MAXIMA][-ULTIMOS_CICLOS:]
    if not duracoes:
        return None
    media = _arredondar(sum(duracoes) / len(duracoes))
    if len(duracoes) == 1:
        texto = f"Só um fim registrado até agora: {duracoes[0]} dias."
    elif min(duracoes) == max(duracoes):
        texto = f"Duração média: {media} dias (sempre {media}, em {len(duracoes)} ciclos com fim registrado)."
    else:
        texto = (f"Duração média: {media} dias (de {min(duracoes)} a {max(duracoes)}, em {len(duracoes)} ciclos "
                 f"com fim registrado).")
    return {"media": media, "minimo": min(duracoes), "maximo": max(duracoes), "ciclos": len(duracoes), "texto": texto}


def calcular(ciclos: list[Ciclo], referencia: date) -> dict:
    """A previsão (só com CICLOS_MINIMOS ciclos completos), o período fértil quando é permitido, o que
    está acontecendo agora e as perguntas ("faltou registrar um mês?"). Tudo explicado."""
    lista = sorted([c for c in ciclos if c.inicio <= referencia], key=lambda c: (c.inicio, c.id or 0))
    ints = intervalos(lista, referencia)
    usados = [i for i in ints if i["situacao"] == "usado"][-ULTIMOS_CICLOS:]
    dur = duracao(lista, referencia)
    saida: dict[str, Any] = {"intervalos": ints, "duracao": dur, "previsao": None, "sem_previsao": None,
                             "fertil": None, "fertil_motivo": None, "perguntas": [], "atual": None}
    comum = _mediana([x["dias"] for x in ints if x["situacao"] == "usado"])
    for i in ints:
        if i["situacao"] == "esquecido":
            saida["perguntas"].append({
                "ciclo_id": i["ate_id"], "de": i["de"], "ate": i["ate"], "dias": i["dias"],
                "texto": (f"Entre {_dia(i['de'], referencia)} e {_dia(i['ate'], referencia)} passaram {i['dias']} dias, "
                          f"quase o dobro do comum ({_numero(comum)}). Faltou registrar um mês?")})

    ultimo = next((c for c in reversed(lista) if c.exatidao != "talvez"), None)
    if ultimo is not None:
        dias = (referencia - ultimo.inicio).days
        tipica = dur["media"] if dur else DURACAO_PADRAO
        andamento = (ultimo.fim is not None and referencia <= ultimo.fim) or (ultimo.fim is None and dias < tipica)
        saida["atual"] = {"inicio": ultimo.inicio, "dia": dias + 1, "em_andamento": andamento,
                          "aproximado": ultimo.exatidao != "dia", "fim": ultimo.fim, "ciclo_id": ultimo.id}

    if len(usados) < CICLOS_MINIMOS or ultimo is None:
        faltam = CICLOS_MINIMOS - len(usados)
        saida["sem_previsao"] = (
            f"Ainda não dá para prever: preciso de pelo menos {CICLOS_MINIMOS} ciclos completos registrados (o tempo "
            f"entre dois inícios com a data certa); por enquanto, {len(usados)}. "
            + ("Falta registrar mais 1 início." if faltam == 1 else f"Faltam mais {max(faltam, 1)} inícios."))
        saida["fertil_motivo"] = "O período fértil só aparece quando já há previsão, com ciclos regulares."
        return saida

    dias_lista = [i["dias"] for i in usados]
    m = _mediana(dias_lista)
    mr = _arredondar(m)
    menor, maior = min(dias_lista), max(dias_lista)
    # Regular ou não: os últimos ciclos de verdade, também os que ficaram fora da faixa (bem diferentes,
    # ou o intervalo longo que você disse que foi assim mesmo). Só o "faltou registrar um mês?" ainda sem
    # resposta fica de fora (revisão de 07/10: o período fértil aparecia com ciclos de 18 e 40 dias).
    reais = [i["dias"] for i in ints if i["situacao"] in ("usado", "fora_do_padrao", "longo_confirmado")]
    reais = reais[-ULTIMOS_CICLOS:] or dias_lista
    menor_real, maior_real = min(reais), max(reais)
    variacao = maior_real - menor_real
    irregular = variacao > VARIACAO_IRREGULAR
    lo, hi = min(menor, mr - FAIXA_MINIMA), max(maior, mr + FAIXA_MINIMA)
    base_aproximada = ultimo.exatidao != "dia"
    if base_aproximada:
        lo -= 3
    inicio = ultimo.inicio + timedelta(days=lo)
    fim = ultimo.inicio + timedelta(days=hi)
    provavel = ultimo.inicio + timedelta(days=mr)
    fora = Counter(i["situacao"] for i in ints)
    partes_fora = []
    if fora.get("esquecido"):
        n = fora["esquecido"]
        partes_fora.append(f"{n} intervalo{'s' if n > 1 else ''} que parece{'m' if n > 1 else ''} ter um mês sem registro")
    n_fora = fora.get("fora_do_padrao", 0) + fora.get("longo_confirmado", 0)
    if n_fora:
        partes_fora.append(f"{n_fora} fora do padrão")
    passou = referencia > fim
    previsao = {
        "inicio": inicio, "fim": fim, "provavel": provavel, "certeza": "inferencia",
        "mediana": m, "menor": menor, "maior": maior, "variacao": variacao, "irregular": irregular,
        "ciclos": len(dias_lista), "base": ultimo.inicio, "base_aproximada": base_aproximada, "passou": passou,
        "texto": (f"Próximo início: entre {_dia(inicio, referencia)} e {_dia(fim, referencia)} "
                  f"(mais provável {_dia(provavel, referencia)})."),
        "porque": (f"Calculado a partir dos últimos {len(dias_lista)} ciclos registrados (de {menor} a {maior} dias; "
                   f"mediana de {_numero(m)}), contando do início de {_dia(ultimo.inicio, referencia)}."
                   + (" Esse início foi anotado sem a data exata, então a faixa começa um pouco antes."
                      if base_aproximada else "")
                   + (f" Fora do cálculo: {'; '.join(partes_fora)}." if partes_fora else "")),
        "aviso": (f"Os últimos ciclos variaram {variacao} dias (de {menor_real} a {maior_real}): a previsão pode errar."
                  if irregular else None),

        "texto_passou": (f"A faixa prevista ({_dia(inicio, referencia)} a {_dia(fim, referencia)}) já passou e não há "
                         f"registro de um novo início. Talvez ele só não tenha sido anotado.") if passou else None,
    }
    saida["previsao"] = previsao

    if irregular:
        saida["fertil_motivo"] = "Com ciclos irregulares, o período fértil não é estimado."
    elif base_aproximada:
        saida["fertil_motivo"] = "O último início foi anotado sem a data exata: o período fértil não é estimado."
    elif passou:
        saida["fertil_motivo"] = "A faixa prevista já passou sem um novo início registrado."
    else:
        ovul_ini = inicio - timedelta(days=OVULACAO_ANTES + OVULACAO_FOLGA)
        ovul_fim = fim - timedelta(days=OVULACAO_ANTES - OVULACAO_FOLGA)
        f_ini = max(ovul_ini - timedelta(days=FERTIL_ANTES), ultimo.inicio + timedelta(days=1))
        f_fim = ovul_fim + timedelta(days=FERTIL_DEPOIS)
        saida["fertil"] = {
            "inicio": f_ini, "fim": f_fim, "ovulacao_inicio": ovul_ini, "ovulacao_fim": ovul_fim,
            "certeza": "inferencia", "aviso": AVISO_FERTIL, "passou": f_fim < referencia,
            "texto": f"Período fértil estimado: de {_dia(f_ini, referencia)} a {_dia(f_fim, referencia)}.",
            "porque": ("Contado de trás para frente a partir da faixa prevista: ovulação perto de 14 dias antes do "
                       "próximo início (±2 dias), e a janela de 5 dias antes a 1 dia depois dela."),
        }
    return saida


# ================================================================ sintomas, ajudas e planejamento

def _posicao_no_ciclo(d: date, ciclos: list[Ciclo]) -> tuple[Ciclo, int] | None:
    """(ciclo, dia) da data: 1 = o dia do início; negativo = dias antes de um início."""
    dentro = [c for c in ciclos if c.inicio <= d <= (c.fim or c.inicio + timedelta(days=DURACAO_PADRAO + 2))]
    if dentro:
        c = max(dentro, key=lambda c: c.inicio)
        return c, (d - c.inicio).days + 1
    antes = [c for c in ciclos if 0 < (c.inicio - d).days <= DIAS_ANTES_SINTOMA]
    if antes:
        c = min(antes, key=lambda c: c.inicio)
        return c, -(c.inicio - d).days
    return None


def _texto_dia(n: int) -> str:
    if n < 0:
        return "1 dia antes de começar" if n == -1 else f"{-n} dias antes de começar"
    return f"no {n}º dia"


def padroes_sintomas(ciclos: list[Ciclo], eventos: list[Evento], referencia: date) -> list[dict]:
    """"Cólica em 3 dos últimos 4 ciclos, quase sempre no 1º dia": a contagem dos registros, sem
    diagnóstico. Precisa de pelo menos 2 ciclos com o sintoma."""
    base = sorted([c for c in ciclos if c.inicio <= referencia and c.exatidao in ("dia", "aproximada")],
                  key=lambda c: c.inicio)
    ultimos = base[-CICLOS_PADRAO:]
    if len(ultimos) < 2:
        return []
    ids_ultimos = {c.id for c in ultimos}
    por_chave: dict[str, dict[Any, int]] = {}
    palavras: dict[str, Counter] = {}
    for e in eventos:
        if e.tipo != "sintoma" or e.de_quem != "ela" or e.data > referencia or e.valor not in ROTULOS_SINTOMAS:
            continue
        pos = _posicao_no_ciclo(e.data, base)
        if pos is None or pos[0].id not in ids_ultimos:
            continue
        c, dia = pos
        dias = por_chave.setdefault(e.valor, {})
        dias[c.id] = min(dias.get(c.id, dia), dia)           # o primeiro dia em que apareceu naquele ciclo
        palavras.setdefault(e.valor, Counter())[(e.palavras or ROTULOS_SINTOMAS[e.valor]).strip().lower()] += 1
    saida = []
    for chave, dias in por_chave.items():
        if len(dias) < 2:
            continue
        rotulo = palavras[chave].most_common(1)[0][0] if palavras.get(chave) else ROTULOS_SINTOMAS[chave]
        lista_dias = list(dias.values())
        comum, vezes = Counter(lista_dias).most_common(1)[0]
        if vezes == len(lista_dias):
            quando = f", sempre {_texto_dia(comum)}"
        elif vezes >= 2 and vezes * 3 >= len(lista_dias) * 2:
            quando = f", quase sempre {_texto_dia(comum)}"
        elif all(d < 0 for d in lista_dias):
            quando = ", nos dias antes de começar"
        elif all(d > 0 for d in lista_dias):
            quando = f", entre o {min(lista_dias)}º e o {max(lista_dias)}º dia"
        else:
            quando = ", perto do começo"
        saida.append({"chave": chave, "rotulo": rotulo, "ciclos": len(dias), "de": len(ultimos),
                      "dias": sorted(lista_dias),
                      "texto": f"{rotulo[:1].upper()}{rotulo[1:]} em {len(dias)} dos últimos {len(ultimos)} ciclos{quando}."})
    saida.sort(key=lambda s: (-s["ciclos"], s["chave"]))
    return saida


def no_momento(calc: dict, padroes: list[dict], referencia: date) -> list[str]:
    """Os padrões dos registros que tocam o dia de hoje (só no cartão, nunca no Hoje): "Hoje é o 1º
    dia. Cólica em 3 dos últimos 4 ciclos, quase sempre no 1º dia." Sensibilidade, não previsão nova."""
    saida = []
    atual, prev = calc.get("atual"), calc.get("previsao")
    for p in padroes:
        if atual and atual.get("em_andamento") and atual["dia"] in p["dias"]:
            saida.append(f"Hoje é o {atual['dia']}º dia. {p['texto']}")
        elif prev and not prev["passou"] and not (atual and atual.get("em_andamento")):
            faltam = (prev["provavel"] - referencia).days
            if faltam > 0 and -faltam in p["dias"]:
                saida.append(f"Faltam {faltam} {'dia' if faltam == 1 else 'dias'} para a data mais provável. {p['texto']}")
    return saida


_ORDEM_CERTEZA = {"confirmado": 3, "observado": 2, "inferencia": 1, "incerto": 0}


def _ajudas(eventos: list[Evento], datas_registro: dict[int, date], referencia: date) -> list[dict]:
    """O que ajuda e o que não ajuda, um por valor: o mais recente representa; 🔴 incerto quando há
    registro dos dois lados ou quando é antigo (mais de 12 meses)."""
    grupos: dict[str, list[Evento]] = {}
    for e in eventos:
        if e.tipo == "ajuda" and e.de_quem == "ela" and e.valor and e.data <= referencia:
            grupos.setdefault(normalizar(e.valor), []).append(e)
    saida = []
    for grupo in grupos.values():
        recente = max(grupo, key=lambda e: (e.data, e.id or 0))
        certeza = max((e.certeza for e in grupo), key=lambda c: _ORDEM_CERTEZA.get(c, 0))
        motivo = None
        if len({e.polaridade or 1 for e in grupo}) > 1:
            certeza, motivo = "incerto", "Há registro de que ajuda e de que não ajuda. Pode ter mudado."
        elif (referencia - recente.data).days > MESES_AJUDA_ANTIGA * 30.44:
            certeza, motivo = "incerto", "Faz mais de 12 meses que isso não aparece. Pode ter mudado."
        dr = datas_registro.get(recente.registro_id) if recente.registro_id is not None else None
        saida.append({"id": recente.id, "valor": recente.valor, "polaridade": recente.polaridade or 1,
                      "certeza": certeza, "motivo": motivo, "data": recente.data, "trecho": recente.trecho,
                      "registro_id": recente.registro_id, "vezes": len(grupo),
                      "quando": tempo_atras((referencia - (dr or recente.data)).days),
                      "ids": [e.id for e in grupo]})
    saida.sort(key=lambda a: (a["polaridade"] < 0, -_ORDEM_CERTEZA.get(a["certeza"], 0), -a["data"].toordinal()))
    return saida


def planejamento(banco: Any, eventos: list[Evento], previsao: dict | None, dur: dict | None,
                 padroes: list[dict], referencia: date) -> list[dict]:
    """Evento futuro registrado que cai nos dias previstos, quando há algo que ela já disse (🟢) ou um
    padrão nos registros para lembrar. Só no cartão do ciclo (nunca no Hoje)."""
    if not previsao or previsao.get("passou"):
        return []
    disse = [e for e in eventos if e.de_quem == "ela" and e.certeza == "confirmado"
             and e.tipo in ("sintoma", "ajuda", "fluxo", "mencao")]
    if not disse and not padroes:
        return []
    janela_fim = previsao["fim"] + timedelta(days=(dur["media"] if dur else DURACAO_PADRAO) - 1)
    linhas = banco.consultar(
        "SELECT id, registro_id, resumo, trecho, data_evento FROM itens WHERE tipo = 'evento' AND status = 'ativo'"
        " AND data_evento IS NOT NULL AND data_evento >= ? AND data_evento <= ? AND (precisao IS NULL OR precisao = 'dia')"
        " AND pessoa_id IS NULL AND (pessoa IS NULL OR pessoa = '') ORDER BY data_evento, id",
        (max(previsao["inicio"], referencia).isoformat(), janela_fim.isoformat()))
    lembrar = [p["texto"] for p in padroes[:2]]
    for e in sorted(disse, key=lambda e: (e.data, e.id or 0), reverse=True)[:2]:
        if e.tipo == "ajuda":
            lembrar.append(f"Ela disse em {_dia(e.data, referencia)} que "
                           f"{'ajuda' if (e.polaridade or 1) > 0 else 'não ajuda'}: {e.valor}.")
        elif e.tipo == "sintoma":
            lembrar.append(f"Ela comentou em {_dia(e.data, referencia)}: "
                           f"{e.palavras or ROTULOS_SINTOMAS.get(e.valor or '', '')}.")
    do_ciclo = {e.registro_id for e in eventos if e.registro_id is not None}
    saida = []
    for l in linhas:
        quando = _data_tolerante(l.get("data_evento"))
        if quando is None or l.get("registro_id") in do_ciclo:
            continue
        saida.append({
            "item_id": l["id"], "registro_id": l["registro_id"], "resumo": l["resumo"], "trecho": l["trecho"],
            "data": quando, "lembrar": lembrar,
            "texto": (f"“{l['resumo']}” cai perto dos dias previstos ({_dia(previsao['inicio'], referencia)} a "
                      f"{_dia(previsao['fim'], referencia)}). Talvez valha ter isso em mente ao planejar."),
        })
    return saida


# ================================================================ o cartão (Conhecendo Ela) e o Hoje

def _datas_registros(banco: Any, ids: set[int]) -> dict[int, date]:
    saida: dict[int, date] = {}
    lista = sorted(i for i in ids if isinstance(i, int))
    for n in range(0, len(lista), 500):
        parte = lista[n:n + 500]
        for l in banco.consultar(f"SELECT id, data_registro FROM registros WHERE id IN ({', '.join('?' * len(parte))})",
                                 tuple(parte)):
            d = _data_tolerante(l.get("data_registro"))
            if d is not None:
                saida[l["id"]] = d
    return saida


def painel(banco: Any, referencia: date) -> dict:
    """Tudo o que o cartão "🌙 Ciclo" mostra (GET /api/ciclo)."""
    ciclos = _ciclos(banco)
    eventos = _eventos(banco)
    conf = ajustes(banco)
    calc = calcular(ciclos, referencia)
    padroes = padroes_sintomas(ciclos, eventos, referencia)
    regs = _datas_registros(banco, {e.registro_id for e in eventos if e.registro_id is not None})
    dela = [e for e in eventos if e.de_quem == "ela"]

    por_ciclo: dict[int, list[Evento]] = {}
    for e in dela:
        if e.ciclo_id is not None:
            por_ciclo.setdefault(e.ciclo_id, []).append(e)
    intervalo_antes = {i["ate_id"]: i for i in calc["intervalos"]}
    historico = []
    for c in sorted(ciclos, key=lambda c: (c.inicio, c.id or 0), reverse=True):
        evs = por_ciclo.get(c.id, [])
        i = intervalo_antes.get(c.id)
        historico.append({
            "id": c.id, "inicio": c.inicio, "fim": c.fim, "exatidao": c.exatidao, "origem": c.origem,
            "registro_id": c.registro_id, "fim_registro_id": c.fim_registro_id,
            "duracao": (c.fim - c.inicio).days + 1 if c.fim else None,
            "intervalo": i["dias"] if i else None, "situacao_intervalo": i["situacao"] if i else None,
            "intervalo_confirmado": c.intervalo_confirmado,
            "sintomas": sorted({(e.palavras or ROTULOS_SINTOMAS.get(e.valor or "", "")).strip() for e in evs
                                if e.tipo == "sintoma"} - {""}),
            "fluxos": [e.valor for e in evs if e.tipo == "fluxo"],
            "registros": sorted({e.registro_id for e in evs if e.registro_id is not None}),
        })
    sintomas_recentes = []
    vistos: set[str] = set()
    for e in sorted(dela, key=lambda e: (e.data, e.id or 0), reverse=True):
        if e.tipo != "sintoma" or (e.valor or "") in vistos:
            continue
        vistos.add(e.valor or "")
        sintomas_recentes.append({"id": e.id, "chave": e.valor, "palavras": e.palavras, "data": e.data,
                                  "certeza": e.certeza, "trecho": e.trecho, "registro_id": e.registro_id})
    anotacoes = [{"id": e.id, "tipo": e.tipo, "valor": e.valor, "data": e.data, "trecho": e.trecho,
                  "registro_id": e.registro_id, "certeza": e.certeza}
                 for e in sorted(dela, key=lambda e: (e.data, e.id or 0), reverse=True)
                 if e.tipo == "mencao" or (e.tipo in ("fim", "durante") and e.ciclo_id is None)][:30]
    fertil = calc["fertil"] if conf["mostrar_fertil"] else None
    return {
        "tem_dados": bool(ciclos or dela),
        "aviso_consentimento": AVISO_CONSENTIMENTO,
        "atual": calc["atual"],
        "previsao": calc["previsao"],
        "sem_previsao": calc["sem_previsao"],
        "perguntas": calc["perguntas"],
        "duracao": calc["duracao"],
        "padroes": padroes,
        "no_momento": no_momento(calc, padroes, referencia),
        "sintomas": sintomas_recentes,
        "ajudas": _ajudas(eventos, regs, referencia),
        "fertil": fertil,
        "fertil_motivo": None if fertil else (calc["fertil_motivo"] if conf["mostrar_fertil"]
                                              else "Você escolheu não mostrar o período fértil."),
        "planejamento": planejamento(banco, eventos, calc["previsao"], calc["duracao"], padroes, referencia),
        "historico": historico,
        "anotacoes": anotacoes,
        "ajustes": conf,
        "minimo_ciclos": CICLOS_MINIMOS,
    }


def _sugestoes_do_hoje(eventos: list[Evento], referencia: date) -> list[dict]:
    """Só o que ELA disse que ajuda (🟢), sem contradição nem antigo; sem palavras do ciclo."""
    return [{"valor": a["valor"], "data": a["data"]} for a in _ajudas(eventos, {}, referencia)
            if a["polaridade"] > 0 and a["certeza"] == "confirmado" and sem_palavras_do_ciclo(a["valor"])][:2]


def cartoes_hoje(banco: Any, referencia: date) -> list:
    """No máximo um lembrete NEUTRO, 1-2 dias antes da faixa prevista, sem as palavras do ciclo."""
    if not ajustes(banco)["lembrete_hoje"]:
        return []
    prev = calcular(_ciclos(banco), referencia)["previsao"]
    if not prev or prev["passou"] or (prev["inicio"] - referencia).days not in DIAS_LEMBRETE:
        return []
    sugestoes = _sugestoes_do_hoje(_eventos(banco), referencia)
    texto = TEXTO_HOJE
    if sugestoes:
        texto += f" Talvez ter por perto o que ela disse que ajuda: {_juntar([s['valor'] for s in sugestoes])}."
    if not sem_palavras_do_ciclo(texto):          # garantia: nada do ciclo escapa para o Hoje
        texto, sugestoes = TEXTO_HOJE, []
    return [cartao(f"cuidado:{prev['inicio'].isoformat()}", "lembrete", texto, porque=PORQUE_HOJE, relevancia=55,
                   tipo="ciclo_cuidado", dados={"sugestoes": sugestoes})]


# ================================================================ IA

# A resposta de `texto_fala_do_ciclo` por texto (o texto de um registro não muda). Só sim/não, com a
# chave pelo resumo (hash) do texto; esquecida inteira quando passa do tamanho.
_MEMO_FALA: dict[str, bool] = {}
_MAX_MEMO_FALA = 4096
# Sem datas, o `extrair` não calcula nenhuma: a data só preenche o que ele devolveria, e não muda a resposta.
_DATA_QUALQUER = date(2000, 1, 1)


def texto_fala_do_ciclo(texto: str) -> bool:
    """O texto tem algum dado do ciclo pelo MESMO critério da revisão (um vocabulário só): o `extrair`
    acha um início, um comentário ("o ciclo dela atrasou", "tá no período dela", "a regra dela"), um
    sintoma, o que ajuda... dela ou de outra pessoa. É o que deixa o registro fora da IA e discreto,
    mesmo sem nada confirmado na revisão (o ✨ Analisar já recusa o mesmo texto com 403)."""
    if not isinstance(texto, str) or not texto.strip():
        return False
    chave = hashlib.sha256(texto.encode("utf-8", "surrogatepass")).hexdigest()
    achado = _MEMO_FALA.get(chave)
    if achado is None:
        achado = any(e.get("tipo") != EXTRA_PLANEJAMENTO
                     for e in extrair(texto, _DATA_QUALQUER, [], [], [], None))
        if len(_MEMO_FALA) >= _MAX_MEMO_FALA:
            _MEMO_FALA.clear()
        _MEMO_FALA[chave] = achado
    return achado


def _registros_que_falam_do_ciclo(banco: Any) -> set[int]:
    """A varredura (LIKE em todos os registros) guardada até a próxima escrita (`Banco.lembrar`, seção
    22.2): o Hoje a pedia 7 vezes, 1,3 s com 4.000 registros longos. Sempre uma cópia."""
    def varrer() -> frozenset[int]:
        condicao = " OR ".join("texto LIKE ?" for _ in _LIKE_DO_CICLO)
        return frozenset(l["id"] for l in banco.consultar(f"SELECT id, texto FROM registros WHERE {condicao}",
                                                           _LIKE_DO_CICLO)
                         if texto_fala_do_ciclo(l.get("texto") or ""))
    lembrar = getattr(banco, "lembrar", None)
    if not callable(lembrar):
        return set(varrer())
    return set(lembrar(f"ciclo.falam_do_ciclo:{id(texto_fala_do_ciclo)}", varrer))


def fora_da_ia(banco: Any) -> set[int]:
    """Registros que nunca vão para a IA: os com dado do ciclo guardado e os que falam do ciclo no
    texto pelo critério da revisão (`texto_fala_do_ciclo`; mesmo sem nada confirmado, mesmo com
    "Não guardar", mesmo com a caixinha 🔒 desmarcada depois)."""
    ids = {l["r"] for l in banco.consultar(
        "SELECT registro_id AS r FROM ciclo_eventos WHERE registro_id IS NOT NULL"
        " UNION SELECT registro_id FROM ciclos WHERE registro_id IS NOT NULL"
        " UNION SELECT fim_registro_id FROM ciclos WHERE fim_registro_id IS NOT NULL")}
    return {i for i in ids if isinstance(i, int) and not isinstance(i, bool)} | _registros_que_falam_do_ciclo(banco)


def discretos(banco: Any) -> set[int]:
    """Os mesmos registros ficam só aqui (seção 20.8): o Hoje não mostra o começo do texto, Pessoas
    não cita a frase e as ligações "mesma pessoa" não os usam. Os itens desses registros passam pelo
    `reservado` nas telas centrais (sem humor explicado pelo ciclo, sem as palavras dele)."""
    return fora_da_ia(banco)


def reservado(texto: str) -> bool:
    """O texto tem palavras do ciclo (as que nunca aparecem no Hoje): um item de registro discreto
    com elas no resumo não aparece fora do 🌙, e com elas no trecho não é citado (20.10)."""
    return not sem_palavras_do_ciclo(texto)


# ================================================================ apagar

def _apoio_do_inicio(banco: Any, ciclo_id: int, sem_registro: int) -> tuple[date, str, int] | None:
    """O que OUTROS registros dizem do início deste ciclo: (data, exatidão, registro). A data mais
    certa ganha; entre duas iguais, a mais cedo (como ao juntar). None = só o registro apagado dizia."""
    candidatos = []
    for a in banco.consultar(
            "SELECT registro_id, data, tipo, valor, certeza FROM ciclo_eventos WHERE ciclo_id = ? AND tipo IN"
            " ('inicio', 'durante') AND registro_id IS NOT NULL AND registro_id != ? AND de_quem = 'ela'",
            (ciclo_id, sem_registro)):
        talvez = a.get("certeza") == "inferencia"
        if a["tipo"] == "inicio":
            data = _data_tolerante(a.get("data"))
            exatidao = "talvez" if talvez else (a.get("valor") if a.get("valor") in EXATIDOES else "dia")
        else:
            data = _data_tolerante(a.get("valor"))
            exatidao = "talvez" if talvez else "dia"
            if data is None:
                data, exatidao = _data_tolerante(a.get("data")), "talvez" if talvez else "aproximada"
        if data is not None:
            candidatos.append((-_ORDEM_EXATIDAO[exatidao], data, exatidao, a["registro_id"]))
    if not candidatos:
        return None
    _, data, exatidao, rid = min(candidatos)
    return data, exatidao, rid


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """O que veio do registro sai junto: o início passa para outro registro que também o contava (com
    a data que ELE disse), senão o ciclo sai (o banco apaga as linhas com este registro_id); o fim volta
    ao de outro registro, ou some. As chaves de aviso dispensado do ciclo são esquecidas."""
    for c in _ciclos(banco):
        if c.registro_id == registro_id:
            apoio = _apoio_do_inicio(banco, c.id, registro_id)
            if apoio is not None:
                data, exatidao, rid = apoio
                banco.atualizar("ciclos", c.id, {"registro_id": rid, "inicio": data, "exatidao": exatidao}, historico=False)
            else:
                banco.executar("UPDATE ciclo_eventos SET ciclo_id = NULL WHERE ciclo_id = ? AND "
                               "(registro_id IS NULL OR registro_id != ?)", (c.id, registro_id))
        if c.fim_registro_id == registro_id:
            outros = banco.consultar(
                "SELECT registro_id, data FROM ciclo_eventos WHERE ciclo_id = ? AND tipo = 'fim' AND registro_id IS NOT NULL"
                " AND registro_id != ? ORDER BY data DESC, id DESC", (c.id, registro_id))
            novo = {"fim": outros[0]["data"], "fim_registro_id": outros[0]["registro_id"]} if outros \
                else {"fim": None, "fim_registro_id": None}
            banco.atualizar("ciclos", c.id, novo, historico=False)
    banco.esquecer_alertas(f"{NOME}:")


def _apagar_ciclo_em(banco: Any, ciclo_id: int) -> bool:
    """Apaga o ciclo e as anotações ligadas a ele (na rota e nas cópias). False = não havia."""
    with banco.transacao():
        if not banco.consultar("SELECT 1 FROM ciclos WHERE id = ?", (ciclo_id,)):
            return False
        banco.executar("DELETE FROM ciclo_eventos WHERE ciclo_id = ?", (ciclo_id,))
        banco.apagar("ciclos", ciclo_id)
        banco.esquecer_alertas(f"{NOME}:")
    return True


def _apagar_tudo_em(banco: Any) -> bool:
    with banco.transacao():
        n = banco.executar("DELETE FROM ciclo_eventos") + banco.executar("DELETE FROM ciclos") \
            + banco.executar("DELETE FROM ciclo_ajustes") + banco.esquecer_alertas(f"{NOME}:")
    return bool(n)


# ================================================================ rotas

def _corpo(pedido: Any) -> dict:
    c = pedido.corpo
    if not isinstance(c, dict):
        raise ValueError("Pedido sem dados.")
    return c


def _hoje(nucleo: Any, pedido: Any) -> date:
    return _data(pedido.parametro("data")) or nucleo.hoje()


def _conferir_datas(inicio: date, fim: date | None, hoje: date) -> None:
    if inicio > hoje or (fim is not None and fim > hoje):
        raise ValueError(DATA_NO_FUTURO)
    if (hoje - inicio).days > MAX_ANOS_ATRAS * 366:
        raise ValueError(DATA_ANTIGA)
    if fim is not None:
        if fim < inicio:
            raise ValueError(FIM_ANTES)
        if (fim - inicio).days > DURACAO_MAXIMA:
            raise ValueError(FIM_LONGE)


def _rota_painel(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return painel(banco, _hoje(nucleo, pedido))


def _rota_criar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _corpo(pedido)
    if set(c) - {"inicio", "fim"}:
        raise ValueError(CAMPOS_CICLO)
    inicio, fim = _data(c.get("inicio")), _data(c.get("fim"))
    if inicio is None:
        raise ValueError(SEM_INICIO)
    hoje = nucleo.hoje()
    _conferir_datas(inicio, fim, hoje)
    perto = [x for x in _ciclos(banco) if abs((x.inicio - inicio).days) <= MESMO_CICLO]
    if perto:
        raise ValueError(CICLO_REPETIDO.format(data=_dia(perto[0].inicio, hoje)))
    with banco.transacao():
        novo = banco.inserir("ciclos", {"inicio": inicio, "fim": fim, "criado_em": _agora(), "exatidao": "dia",
                                        "origem": "manual"})
        banco.esquecer_alertas(f"{NOME}:")
    return {"id": novo}


def _um_ciclo(banco: Any, ciclo_id: int) -> Ciclo:
    c = next((x for x in _ciclos(banco) if x.id == ciclo_id), None)
    if c is None:
        raise NaoEncontrado(SEM_CICLO)
    return c


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _corpo(pedido)
    if not c or set(c) - {"inicio", "fim"}:
        raise ValueError(CAMPOS_CICLO)
    atual = _um_ciclo(banco, pedido.id)
    inicio = _data(c["inicio"]) if "inicio" in c else atual.inicio
    if inicio is None:
        raise ValueError(SEM_INICIO)
    fim = _data(c["fim"]) if "fim" in c else atual.fim
    hoje = nucleo.hoje()
    _conferir_datas(inicio, fim, hoje)
    outro = [x for x in _ciclos(banco) if x.id != atual.id and abs((x.inicio - inicio).days) <= MESMO_CICLO]
    if outro:
        raise ValueError(CICLO_REPETIDO.format(data=_dia(outro[0].inicio, hoje)))
    mudar: dict[str, Any] = {}
    if "inicio" in c and (inicio != atual.inicio or atual.exatidao != "dia"):
        # A data que você escreveu no cartão é sua: o ciclo deixa de depender do registro.
        mudar.update(inicio=inicio, exatidao="dia", origem="manual", registro_id=None)
    if "fim" in c and fim != atual.fim:
        mudar.update(fim=fim, fim_registro_id=None)
    if mudar:
        with banco.transacao():
            banco.atualizar("ciclos", atual.id, mudar)
            banco.esquecer_alertas(f"{NOME}:")
    return {}


def _rota_intervalo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _corpo(pedido)
    if not isinstance(c.get("real"), bool) or set(c) - {"real"}:
        raise ValueError(REAL_INVALIDO)
    banco.atualizar("ciclos", _um_ciclo(banco, pedido.id).id, {"intervalo_confirmado": c["real"]})
    return {}


def _rota_apagar_ciclo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ciclo_id = pedido.id
    if not _apagar_ciclo_em(banco, ciclo_id):
        raise NaoEncontrado(SEM_CICLO)
    nucleo.limpar_copias(lambda copia: _apagar_ciclo_em(copia, ciclo_id))
    return {}


def _rota_apagar_evento(nucleo: Any, pedido: Any, banco: Any) -> dict:
    evento_id = pedido.id

    def apagar(b: Any) -> None:
        with b.transacao():
            b.apagar("ciclo_eventos", evento_id)
            b.esquecer_alertas(f"{NOME}:")

    try:
        apagar(banco)
    except NaoEncontrado:
        raise NaoEncontrado(SEM_ANOTACAO) from None
    nucleo.limpar_copias(apagar)
    return {}


def _rota_ajustes(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = _corpo(pedido)
    if not c or set(c) - set(AJUSTES_PADRAO) or not all(isinstance(v, bool) for v in c.values()):
        raise ValueError(CAMPOS_AJUSTES)
    with banco.transacao():
        for chave, valor in c.items():
            banco.executar("INSERT INTO ciclo_ajustes (chave, valor) VALUES (?, ?) "
                           "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, "1" if valor else "0"))
        banco.esquecer_alertas(f"{NOME}:")
    return ajustes(banco)


def _rota_apagar_tudo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    if _corpo(pedido).get("confirmar") is not True:
        raise ValueError(CONFIRMAR_TUDO)
    _apagar_tudo_em(banco)
    nucleo.limpar_copias(_apagar_tudo_em)
    return {}


def rotas() -> list:
    return [
        rota("GET", "", _rota_painel),
        rota("POST", "ciclos", _rota_criar),
        rota("PATCH", "ciclos/{id}", _rota_editar),
        rota("DELETE", "ciclos/{id}", _rota_apagar_ciclo),
        rota("POST", "ciclos/{id}/intervalo", _rota_intervalo),
        rota("DELETE", "eventos/{id}", _rota_apagar_evento),
        rota("PATCH", "ajustes", _rota_ajustes),
        rota("POST", "apagar-tudo", _rota_apagar_tudo),
    ]


# ================================================================ exportar e importar

def exportar(banco: Any) -> dict:
    return {
        "versao": 1,
        "ciclos": banco.consultar("SELECT id, inicio, fim, exatidao, origem, registro_id, fim_registro_id, "
                                  "intervalo_confirmado, criado_em FROM ciclos ORDER BY inicio, id"),
        "eventos": banco.consultar("SELECT id, data, tipo, valor, palavras, polaridade, certeza, de_quem, trecho, "
                                   "registro_id, ciclo_id, criado_em FROM ciclo_eventos ORDER BY data, id"),
        "ajustes": ajustes(banco),
    }


def _id_do_arquivo(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta sem duplicar: ciclo a até MESMO_CICLO dias de um daqui é o mesmo (o fim que faltava entra);
    anotação igual (data, tipo, valor, trecho e registro) não entra de novo; anotação de um registro
    que não veio não fica solta. Linha estranha é pulada. Os ajustes daqui ficam."""
    if not isinstance(dados, dict):
        raise ValueError("Os dados do ciclo no arquivo vieram num formato estranho.")
    agora = _agora()
    novos_ciclos = novos_eventos = 0
    mapa_ciclos: dict[int, int] = {}
    brutos_c = dados.get("ciclos") if isinstance(dados.get("ciclos"), list) else []
    brutos_e = dados.get("eventos") if isinstance(dados.get("eventos"), list) else []
    for b in brutos_c[:MAX_IMPORTAR]:
        if not isinstance(b, dict):
            continue
        inicio, fim = _data_tolerante(b.get("inicio")), _data_tolerante(b.get("fim"))
        if inicio is None or (fim is not None and not 0 <= (fim - inicio).days <= DURACAO_MAXIMA):
            continue
        rid = mapa_registros.get(_id_do_arquivo(b.get("registro_id")))
        fim_rid = mapa_registros.get(_id_do_arquivo(b.get("fim_registro_id")))
        existente = next((c for c in _ciclos(banco) if abs((c.inicio - inicio).days) <= MESMO_CICLO), None)
        if existente is not None:
            if existente.fim is None and fim is not None:
                banco.atualizar("ciclos", existente.id, {"fim": fim, "fim_registro_id": fim_rid}, historico=False)
            novo_id = existente.id
        else:
            novo_id = banco.inserir("ciclos", {
                "inicio": inicio, "fim": fim, "criado_em": agora,
                "exatidao": b.get("exatidao") if b.get("exatidao") in EXATIDOES else "dia",
                "origem": "registro" if rid is not None else "manual", "registro_id": rid,
                "fim_registro_id": fim_rid if fim is not None else None,
                "intervalo_confirmado": b.get("intervalo_confirmado") in (1, True)}, historico=False)
            novos_ciclos += 1
        if _id_do_arquivo(b.get("id")) is not None:
            mapa_ciclos[b["id"]] = novo_id
    existentes = {(l["data"], l["tipo"], l["valor"], l["trecho"], l["registro_id"])
                  for l in banco.consultar("SELECT data, tipo, valor, trecho, registro_id FROM ciclo_eventos")}
    for b in brutos_e[:MAX_IMPORTAR]:
        if not isinstance(b, dict) or b.get("tipo") not in TIPOS_EVENTO:
            continue
        d = _data_tolerante(b.get("data"))
        trecho = b.get("trecho")
        if d is None or not isinstance(trecho, str) or not trecho.strip() or len(trecho) > 5000:
            continue
        rid_arquivo = _id_do_arquivo(b.get("registro_id"))
        rid = mapa_registros.get(rid_arquivo) if rid_arquivo is not None else None
        if rid_arquivo is not None and rid is None:
            continue                 # o registro de onde veio não está aqui: o trecho não fica solto
        valor = b.get("valor") if isinstance(b.get("valor"), str) and len(b["valor"]) <= MAX_AJUDA else None
        chave = (d.isoformat(), b["tipo"], valor, trecho, rid)
        if chave in existentes:
            continue
        existentes.add(chave)
        palavras = b.get("palavras") if isinstance(b.get("palavras"), str) and len(b["palavras"]) <= MAX_AJUDA else None
        pol = b.get("polaridade")
        banco.inserir("ciclo_eventos", {
            "data": d, "tipo": b["tipo"], "valor": valor, "trecho": trecho, "registro_id": rid, "criado_em": agora,
            "ciclo_id": mapa_ciclos.get(_id_do_arquivo(b.get("ciclo_id"))), "palavras": palavras,
            "polaridade": pol if pol in (1, -1) and not isinstance(pol, bool) else None,
            "certeza": b.get("certeza") if b.get("certeza") in CERTEZAS else "observado",
            "de_quem": "outra" if b.get("de_quem") == "outra" else "ela"}, historico=False)
        novos_eventos += 1
    if novos_ciclos or novos_eventos:
        banco.esquecer_alertas(f"{NOME}:")
    partes = []
    if novos_ciclos:
        partes.append(f"{novos_ciclos} {'ciclo' if novos_ciclos == 1 else 'ciclos'}")
    if novos_eventos:
        partes.append(f"{novos_eventos} {'anotação' if novos_eventos == 1 else 'anotações'}")
    texto = ("Ciclo: " + " e ".join(partes) + ".") if partes else "Ciclo: nada novo para juntar."
    return {"novos": novos_ciclos + novos_eventos, "ciclos": novos_ciclos, "eventos": novos_eventos, "texto": texto}


__all__ = [
    "AVISO_CONSENTIMENTO", "AVISO_FERTIL", "CICLOS_MINIMOS", "Ciclo", "ESQUEMA", "ESQUEMA_VERSAO", "Evento", "NOME",
    "PORQUE_HOJE", "ROTULO", "ROTULOS_SINTOMAS", "TEXTO_HOJE", "TIPOS_EXTRAS", "ajustes", "ao_apagar_registro",
    "calcular", "cartoes_hoje", "duracao", "exportar", "extrair", "fora_da_ia", "gravar_extras", "importar",
    "intervalos", "marca_privado", "no_momento", "padroes_sintomas", "painel", "planejamento", "reservado", "rotas",
    "sem_palavras_do_ciclo", "texto_fala_do_ciclo", "discretos",
]
