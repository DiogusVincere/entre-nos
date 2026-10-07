"""Datas em português: acha expressões de data no texto e calcula a data certa.

Só o Python calcula datas (DESIGN.md, seções 1 e 3). Tudo é resolvido em relação à
`referencia`, a data do registro.

Como funciona:
1. O texto ganha duas cópias do mesmo tamanho: minúsculas (`baixo`) e minúsculas sem
   acento (`norm`). As expressões regulares rodam em `norm`, então as posições valem
   para o texto original.
2. Cada padrão acha candidatos e um "resolvedor" calcula a data. O resolvedor pode
   recusar (não é data: "a segunda vez") ou marcar como inválida ("31/02"). A inválida
   ainda ocupa o lugar, para que um pedaço dela ("dia 31") não vire data.
3. Nada se sobrepõe: a expressão mais longa ganha.
4. Datas sem ano ("20/10", "dia 20", "em março", "sexta") olham o tempo verbal perto
   delas: no passado ("fomos", "ficou", "ontem") ficam com a data que já passou; no
   resto, com a próxima.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from ..modelos import DataEncontrada, Precisao
from ..texto import frases

_PASSADO = "passado"
_FUTURO = "futuro"


# ------------------------------------------------------------------ vocabulário

_NUMEROS: dict[str, int] = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
    "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16,
    "dezasseis": 16, "dezessete": 17, "dezassete": 17, "dezoito": 18,
    "dezenove": 19, "dezanove": 19, "vinte": 20, "trinta": 30,
}
for _nome, _valor in list(_NUMEROS.items()):
    if _valor < 10:
        _NUMEROS[f"vinte e {_nome}"] = 20 + _valor
_NUMEROS["trinta e um"] = _NUMEROS["trinta e uma"] = 31

_MESES: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MESES_ABREV: dict[str, int] = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
_DIAS_SEMANA: dict[str, int] = {
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6,
}

# "segunda" a "sexta" também são ordinais ("a segunda vez"). Palavras que, logo depois,
# indicam o ordinal:
_ORDINAL_ANTES = frozenset("a uma cada sua minha tua outra mesma nossa".split())
_ORDINAL_DEPOIS = frozenset("""
vez vezes opcao opcoes chance filha filho filhas filhos neta neto serie etapa fase parte
colocada colocado lugar pessoa maior melhor pior menor mais tentativa rodada temporada
edicao dose semana classe turma versao via prova aula guerra mao ordem linha pagina
questao fileira geracao gravidez graduacao lingua instancia chamada vaga casa porta rua
sessao consulta cirurgia reuniao entrevista viagem
""".split())

# Um número seguido destas palavras é quantidade, não dia nem ano.
_UNIDADES = frozenset("""
anos ano vezes vez horas hora h min minuto minutos segundos reais real litros litro kg
quilos km metros filhos filhas pessoas graus copos dolares euros mil pontos calorias
passos gramas ml
""".split())

# Palavra antes de um mês sozinho ("até novembro", "fim de novembro").
_PREP_MES = frozenset("de ate desde para pra inicio comeco fim final meados durante".split())
# Palavra antes de "3/4" que faz dele data, e não fração.
_PREP_DATA = frozenset("dia em ate desde para pra no".split())

# Logo antes da data, um prazo ("até dia 25", "antes do dia 12"): a data não é a do aniversário.
_RE_PRAZO_ANTES = re.compile(r"(?<!\w)(?:ate|antes\s+d[eoa]s?)\s+(?:o\s+|a\s+)?$")
# Recorrência anual: a frase fala de aniversário ou de "todo ano".
_RE_RECORRENTE = re.compile(
    r"(?<!\w)(?:aniversario|aniver|niver|faz\s+anos|fara\s+anos|fez\s+anos|completa\s+anos|"
    r"todo\s+ano|todos\s+os\s+anos|anualmente|nasceu|nascimento|bodas)(?!\w)"
)

# ------------------------------------------------------------ tempo verbal

# Verbos de fala não dizem nada sobre a data ("ela contou que a prova é dia 20").
_FALA = frozenset("""
disse dizia contou falou comentou mencionou explicou avisou lembrou respondeu perguntou
confirmou garantiu mandou escreveu pediu achou pensou desabafou reclamou decidiu
disseram contaram falaram comentaram avisaram contei falei
""".split())
# Presente e futuro contam juntos como "não passado".
_FUTURO_PALAVRAS = frozenset("""
vai vao vamos vou ira irao iremos irei sera serao seremos serei tera terao teremos terei
fara farao faremos farei havera estara estarao estaremos pretende pretendem pretendo
pretendemos planeja planejam planejo planejamos quer querem quero queremos queria queriam
gostaria gostariamos marcou marcaram marcamos marquei marcado marcada marcados marcadas
agendou agendaram agendamos agendei agendado agendada agendados agendadas remarcou
remarcaram remarcamos remarquei remarcado remarcada adiou adiaram adiamos adiei adiado
adiada antecipou anteciparam antecipado antecipada combinou combinaram combinamos combinei
combinado combinada prometeu prometeram prometi proximo proxima proximos proximas amanha
daqui esperando aguardando aguarda espera precisa precisam precisamos deve devem devemos
tem sao esta estao fica ficam comeca comecam termina terminam acontece acontecem cai caem
chega chegam volta voltam viaja viajam faz fazem completa completam
""".split())
_PASSADO_PALAVRAS = frozenset("""
foi fomos fui foram era eram eramos estava estavam estavamos estive esteve estivemos
estiveram teve tive tivemos tiveram tinha tinham tinhamos houve fez fiz fizemos fizeram
deu dei demos deram viu vi vimos viram veio vim viemos vieram ontem anteontem atras
passado passada passados passadas retrasado retrasada ultimo ultima saimos jantamos
almocamos assistimos conhecemos comemoramos passamos voltamos chegamos ficamos brigamos
conversamos terminamos comecamos ganhamos compramos viajamos
conheci comi bebi perdi recebi escolhi senti dormi abri assisti consegui descobri subi corri
entendi aprendi esqueci apareci vendi devolvi nasci cresci vivi trouxe trouxemos trouxeram
quis quisemos pude pudemos soube pus pusemos
""".split())
# (Integração de 07/10/2026: a 1ª pessoa do pretérito em -i não termina em -ou/-eu/-iu nem em -ei;
# "conheci os pais dela em 12/03" era lido como 12/03 do ano que vem. "pedi", "falei" e "contei" são
# fala, como "pediu": não dizem o tempo da data.)
_SUFIXOS_FUTURO = ("arei", "erei", "irei", "aremos", "eremos", "iremos", "arao", "erao", "irao")
_SUFIXOS_PASSADO = ("ou", "eu", "iu", "ava", "avam", "aram", "eram", "iram")
_NAO_SAO_PASSADO = frozenset("""
estou sou vou dou tou museu chapeu europeu judeu trofeu plebeu hebreu liceu ilheu pigmeu
camafeu apogeu coliseu mausoleu fariseu ateu breu pneu brava bravas oitava escrava grava
trava crava agrava lava cava clava fava java nava
""".split())
# Locuções que valem como futuro ("ficou de responder", "sexta que vem").
_RE_LOCUCAO_FUTURO = re.compile(
    r"(?<!\w)(?:(?:que|q)\s+vem|fic(?:ou|a|am|amos|aram)\s+de|fiquei\s+de|"
    r"t(?:em|emos|enho)\s+que|vai\s+ter)(?!\w)"
)
# Onde termina uma oração (no texto em minúsculas COM acento: "é" não é "e").
_RE_ORACAO = re.compile(
    r"[;:()\[\]]|\s(?:e|mas|porém|porem|só\s+que|so\s+que|enquanto|então|entao|aí|ai|daí|dai)\s"
)
_RE_LETRAS = re.compile(r"[^\W\d_]+")


def _classe_palavra(palavra: str) -> str | None:
    """'passado', 'futuro' (inclui presente) ou None para uma palavra em minúsculas."""
    if palavra == "é":
        return _FUTURO
    p = _sem_acento(palavra)
    if p in _FALA:
        return None
    if p in _FUTURO_PALAVRAS:
        return _FUTURO
    if p in _PASSADO_PALAVRAS:
        return _PASSADO
    if len(p) >= 5 and p.endswith(_SUFIXOS_FUTURO):
        return _FUTURO
    if len(p) >= 4 and p not in _NAO_SAO_PASSADO:
        if p.endswith(_SUFIXOS_PASSADO) or (len(p) >= 5 and p.endswith("ei")):
            return _PASSADO
    return None


# ------------------------------------------------------------ texto de mesmo tamanho

def _minusculas(texto: str) -> str:
    """lower() caractere a caractere, sem mudar o tamanho (posições continuam valendo)."""
    return "".join(b if len(b := c.lower()) == 1 else c for c in texto)


def _sem_acento(texto: str) -> str:
    """Tira o acento caractere a caractere, sem mudar o tamanho."""
    saida = []
    for c in texto:
        base = "".join(x for x in unicodedata.normalize("NFD", c) if unicodedata.category(x) != "Mn")
        saida.append(base if len(base) == 1 else c)
    return "".join(saida)


def _palavra_anterior(norm: str, pos: int) -> str | None:
    m = re.search(r"([^\W\d_]+)\s+$", norm[max(0, pos - 40):pos])
    return m.group(1) if m else None


def _palavra_seguinte(norm: str, pos: int) -> str | None:
    m = re.match(r"\s*([^\W\d_]+|%)", norm[pos:pos + 40])
    return m.group(1) if m else None


# ------------------------------------------------------------ contas com datas

def _ultimo_dia(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _data_segura(ano: int, mes: int, dia: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _existe(dia: int, mes: int) -> bool:
    """O dia existe nesse mês em algum ano (29/02 existe)."""
    return 1 <= mes <= 12 and 1 <= dia <= _ultimo_dia(2000, mes)


def _somar_meses(d: date, meses: int) -> date:
    """31/01 + 1 mês = 28/02 (ou 29/02): o dia é ajustado ao fim do mês."""
    total = d.year * 12 + d.month - 1 + meses
    ano, mes = divmod(total, 12)
    mes += 1
    return date(ano, mes, min(d.day, _ultimo_dia(ano, mes)))


def _inicio_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _proximo_dia_semana(ref: date, dia_semana: int, a_partir_de: int = 1) -> date:
    """Primeiro `dia_semana` a partir de ref + `a_partir_de` dias."""
    inicio = ref + timedelta(days=a_partir_de)
    return inicio + timedelta(days=(dia_semana - inicio.weekday()) % 7)


def _ultimo_dia_semana(ref: date, dia_semana: int) -> date:
    """Último `dia_semana` antes de ref (nunca o próprio dia)."""
    fim = ref - timedelta(days=1)
    return fim - timedelta(days=(fim.weekday() - dia_semana) % 7)


def _enesimo_domingo(ano: int, mes: int, n: int) -> date:
    primeiro = date(ano, mes, 1)
    return primeiro + timedelta(days=(6 - primeiro.weekday()) % 7 + 7 * (n - 1))


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (calendário gregoriano, algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def proxima_ocorrencia(dia: int, mes: int, referencia: date) -> date:
    """Próxima vez, a partir da referência (inclusive), em que cai esse dia e mês.

    Para aniversários. 29/02 cai em 28/02 nos anos que não são bissextos.
    """
    if not _existe(dia, mes):
        raise ValueError(f"dia {dia} não existe no mês {mes}")
    for ano in (referencia.year, referencia.year + 1):
        d = date(ano, mes, min(dia, _ultimo_dia(ano, mes)))
        if d >= referencia:
            return d
    raise AssertionError("inalcançável")


def _ano_completo(texto: str, ref: date) -> int:
    """'26' -> 2026, '98' -> 1998 (até 20 anos à frente da referência é 20xx)."""
    ano = int(texto)
    if len(texto) > 2:
        return ano
    return 2000 + ano if 2000 + ano <= ref.year + 20 else 1900 + ano


def _numero(texto: str) -> int | None:
    texto = re.sub(r"\s+", " ", texto.strip())
    digitos = re.match(r"\d+", texto)
    if digitos:
        return int(digitos.group())
    if texto == "primeiro":
        return 1
    return _NUMEROS.get(texto)


def _mes(texto: str) -> int:
    texto = texto.rstrip(".")
    return _MESES.get(texto) or _MESES_ABREV[texto]


# ------------------------------------------------------------ contexto de uma busca

@dataclass
class _Achado:
    data: date
    precisao: Precisao = Precisao.DIA
    alternativas: tuple[date, ...] = ()
    recorrente: bool = False


_INVALIDA = object()   # parece data, mas não existe ("31/02"): ocupa o lugar e some no fim


class _Contexto:
    def __init__(self, texto: str, referencia: date):
        self.texto = texto
        self.ref = referencia
        self.baixo = _minusculas(texto)
        self.norm = _sem_acento(self.baixo)
        self._frases = [(i, f) for i, f, _ in frases(texto)]
        self._marcas: dict[tuple[int, int], list[tuple[int, int, str]]] = {}

    def frase(self, pos: int) -> tuple[int, int]:
        for ini, fim in self._frases:
            if ini <= pos < fim:
                return ini, fim
        return 0, len(self.texto)

    def recorrente(self, pos: int) -> bool:
        ini, fim = self.frase(pos)
        return _RE_RECORRENTE.search(self.norm, ini, fim) is not None

    def tempo(self, inicio: int, fim: int) -> str | None:
        """Tempo verbal que governa a expressão: a marca mais perto ANTES dela na mesma
        oração; sem nenhuma, a mais perto depois. None quando não há pista."""
        f_ini, f_fim = self.frase(inicio)
        o_ini, o_fim = f_ini, f_fim
        for b in _RE_ORACAO.finditer(self.baixo, f_ini, f_fim):
            if b.end() <= inicio:
                o_ini = b.end()
            elif b.start() >= fim:
                o_fim = b.start()
                break
        marcas = self._marcas_da_frase(f_ini, f_fim)
        antes = [c for i, f, c in marcas if o_ini <= i and f <= inicio]
        if antes:
            return antes[-1]
        depois = [c for i, f, c in marcas if i >= fim and f <= o_fim]
        return depois[0] if depois else None

    def _marcas_da_frase(self, ini: int, fim: int) -> list[tuple[int, int, str]]:
        if (ini, fim) not in self._marcas:
            marcas = [(m.start(), m.end(), _FUTURO) for m in _RE_LOCUCAO_FUTURO.finditer(self.norm, ini, fim)]
            cobertos = list(marcas)
            for m in _RE_LETRAS.finditer(self.baixo, ini, fim):
                if any(a <= m.start() < b for a, b, _ in cobertos):
                    continue
                classe = _classe_palavra(m.group())
                if classe:
                    marcas.append((m.start(), m.end(), classe))
            self._marcas[(ini, fim)] = sorted(marcas)
        return self._marcas[(ini, fim)]


# ------------------------------------------------------------ padrões

_Resolvedor = Callable[[re.Match, _Contexto], "_Achado | object | None"]
_PADROES: list[tuple[re.Pattern, _Resolvedor]] = []

_ESQ = r"(?<![\w-])"             # início de palavra ("pré-natal" não é Natal)
_ESQ_NUM = r"(?<![\w/.])"        # início de número (não pega o meio de "1.20.10")
_DIR = r"(?!\w)"
_DIR_NUM = r"(?!\w)(?![/.]\d)"


def _alternancia(palavras) -> str:
    return "(?:" + "|".join(p.replace(" ", r"\s+") for p in sorted(palavras, key=len, reverse=True)) + ")"


_RE_NUM = r"(?:\d{1,3}|" + _alternancia(_NUMEROS)[3:]
_RE_DIA = (r"(?:\d{1,2}(?:[º°]|o(?=\s))?|primeiro|"
           + _alternancia(k for k in _NUMEROS if k not in ("um", "uma"))[3:])
_RE_MES = _alternancia(_MESES)
_RE_MES_OU_ABREV = r"(?:" + _alternancia(_MESES)[3:-1] + "|" + _alternancia(_MESES_ABREV) + r"\.?)"
_RE_DS = r"(?:(?:segunda|terca|quarta|quinta|sexta)(?:(?:-|\s+)feira)?|sabado|domingo)"
_RE_NOME_DS = re.compile(r"segunda|terca|quarta|quinta|sexta|sabado|domingo")
_RE_UNIDADE = r"(?:dias?|semanas?|mes|meses|anos?)"
_RE_ESTE = r"(?:n?est[ae]|n?ess[ae])"
_RE_FDS = r"(?:(?:fim|final)\s+de\s+semana|fds)"
_QUE_VEM = r"(?:que|q)\s+vem"            # "semana q vem"


def _registrar(regex: str, resolvedor: _Resolvedor, esq: str = _ESQ, dir_: str = _DIR) -> None:
    _PADROES.append((re.compile(esq + "(?:" + regex + ")" + dir_), resolvedor))


def _dia_semana(m: re.Match) -> int:
    return _DIAS_SEMANA[_RE_NOME_DS.search(m.group()).group()]


# --- hoje, amanhã, ontem...

def _deslocamento(dias: int) -> _Resolvedor:
    return lambda m, ctx: _Achado(ctx.ref + timedelta(days=dias))


_registrar(r"depois\s+de\s+amanha", _deslocamento(2))
_registrar(r"amanha", _deslocamento(1))
_registrar(r"hoje|hj", _deslocamento(0))
_registrar(r"anteontem|antes\s+de\s+ontem", _deslocamento(-2))
_registrar(r"ontem", _deslocamento(-1))


# --- dias da semana

def _ds_semana_seguinte(m, ctx):
    return _Achado(_inicio_semana(ctx.ref) + timedelta(days=7 + _dia_semana(m)))


def _ds_semana_anterior(m, ctx):
    return _Achado(_inicio_semana(ctx.ref) + timedelta(days=-7 + _dia_semana(m)))


def _ds_proximo(m, ctx):
    d = _proximo_dia_semana(ctx.ref, _dia_semana(m))
    return _Achado(d, alternativas=(d, d + timedelta(days=7)))


def _ds_passado(m, ctx):
    d = _ultimo_dia_semana(ctx.ref, _dia_semana(m))
    if d == ctx.ref - timedelta(days=1):
        # "terça passada" dita na quarta: muita gente quer dizer a da semana anterior.
        return _Achado(d, alternativas=(d, d - timedelta(days=7)))
    return _Achado(d)


def _ds_retrasado(m, ctx):
    return _Achado(_ultimo_dia_semana(ctx.ref, _dia_semana(m)) - timedelta(days=7))


def _ds_este(m, ctx):
    if ctx.tempo(m.start(), m.end()) == _PASSADO:
        return _Achado(_ultimo_dia_semana(ctx.ref, _dia_semana(m)))
    return _Achado(_proximo_dia_semana(ctx.ref, _dia_semana(m), a_partir_de=0))


def _ds_simples(m, ctx):
    nome = m.group()
    if "feira" not in nome and not nome.startswith(("sabado", "domingo")):
        if (_palavra_anterior(ctx.norm, m.start()) in _ORDINAL_ANTES
                or _palavra_seguinte(ctx.norm, m.end()) in _ORDINAL_DEPOIS):
            return None
    if ctx.tempo(m.start(), m.end()) == _PASSADO:
        return _Achado(_ultimo_dia_semana(ctx.ref, _dia_semana(m)))
    return _Achado(_proximo_dia_semana(ctx.ref, _dia_semana(m)))


_SEMANA_QUE_VEM = r"(?:semana\s+" + _QUE_VEM + r"|proxima\s+semana)"
_registrar(_RE_DS + r"\s+(?:da|na)\s+" + _SEMANA_QUE_VEM, _ds_semana_seguinte)
_registrar(_SEMANA_QUE_VEM + r"\s*,?\s*(?:na|no)\s+" + _RE_DS, _ds_semana_seguinte)
_registrar(_RE_DS + r"\s+(?:da|na)\s+semana\s+passada", _ds_semana_anterior)
_registrar(r"proxim[oa]\s+" + _RE_DS + "|" + _RE_DS + r"\s+" + _QUE_VEM, _ds_proximo)
_registrar(r"ultim[oa]\s+" + _RE_DS + "|" + _RE_DS + r"\s+passad[oa]", _ds_passado)
_registrar(r"penultim[oa]\s+" + _RE_DS + "|" + _RE_DS + r"\s+retrasad[oa]", _ds_retrasado)
_registrar(_RE_ESTE + r"\s+" + _RE_DS, _ds_este)
_registrar(_RE_DS, _ds_simples)


# --- semanas, fins de semana, meses e anos relativos

def _semana(deslocamento: int) -> _Resolvedor:
    return lambda m, ctx: _Achado(_inicio_semana(ctx.ref) + timedelta(days=7 * deslocamento), Precisao.SEMANA)


def _mes_relativo(deslocamento: int) -> _Resolvedor:
    return lambda m, ctx: _Achado(_somar_meses(ctx.ref.replace(day=1), deslocamento), Precisao.MES)


def _ano_relativo(deslocamento: int) -> _Resolvedor:
    return lambda m, ctx: _Achado(date(ctx.ref.year + deslocamento, 1, 1), Precisao.ANO)


def _sabado_desta_semana(ref: date) -> date:
    return _inicio_semana(ref) + timedelta(days=5)


def _fds_proximo(m, ctx):
    sabado = _sabado_desta_semana(ctx.ref) + timedelta(days=7 if ctx.ref.weekday() >= 5 else 0)
    return _Achado(sabado, alternativas=(sabado, sabado + timedelta(days=7)))


def _fds_simples(m, ctx):
    if ctx.ref.weekday() >= 5:
        return _Achado(ctx.ref)
    sabado = _sabado_desta_semana(ctx.ref)
    if ctx.tempo(m.start(), m.end()) == _PASSADO:
        sabado -= timedelta(days=7)
    return _Achado(sabado)


_registrar(_SEMANA_QUE_VEM, _semana(1))
_registrar(r"semana\s+passada", _semana(-1))
_registrar(r"semana\s+retrasada", _semana(-2))
_registrar(_RE_ESTE + r"\s+semana", _semana(0))

_registrar(r"proximo\s+" + _RE_FDS + "|" + _RE_FDS + r"\s+" + _QUE_VEM, _fds_proximo)
_registrar(r"ultimo\s+" + _RE_FDS + "|" + _RE_FDS + r"\s+passado",
           lambda m, ctx: _Achado(_sabado_desta_semana(ctx.ref) - timedelta(days=7)))
_registrar(_RE_ESTE + r"\s+" + _RE_FDS, lambda m, ctx: _Achado(_sabado_desta_semana(ctx.ref)))
_registrar(_RE_FDS, _fds_simples)

_registrar(r"mes\s+" + _QUE_VEM + r"|proximo\s+mes", _mes_relativo(1))
_registrar(r"mes\s+passado", _mes_relativo(-1))
_registrar(r"mes\s+retrasado", _mes_relativo(-2))
_registrar(_RE_ESTE + r"\s+mes", _mes_relativo(0))


def _fim_do_mes(deslocamento: int) -> _Resolvedor:
    """'no fim do mês': a última semana do mês (precisão semana, a partir da segunda-feira)."""
    def resolver(m, ctx):
        mes = _somar_meses(ctx.ref.replace(day=1), deslocamento)
        ultimo = mes.replace(day=_ultimo_dia(mes.year, mes.month))
        return _Achado(_inicio_semana(ultimo), Precisao.SEMANA)
    return resolver


_FIM_DO = r"(?:(?:no|ate\s+o|pro|para\s+o)\s+)?(?:fim|final)\s+(?:d[oe]|dest[ea]|dess[ea])\s+"
_registrar(_FIM_DO + r"(?:mes\s+" + _QUE_VEM + r"|proximo\s+mes)", _fim_do_mes(1))
_registrar(_FIM_DO + r"mes", _fim_do_mes(0))
_registrar(_FIM_DO + r"ano", lambda m, ctx: _Achado(date(ctx.ref.year, 12, 1), Precisao.MES))

_registrar(r"ano\s+" + _QUE_VEM + r"|proximo\s+ano", _ano_relativo(1))
_registrar(r"ano\s+passado", _ano_relativo(-1))
_registrar(r"ano\s+retrasado", _ano_relativo(-2))
_registrar(_RE_ESTE + r"\s+ano", _ano_relativo(0))


# --- daqui a N dias, há N semanas, N meses atrás

def _somar(ref: date, n: int, unidade: str) -> date:
    unidade = unidade[:3]
    if unidade == "dia":
        return ref + timedelta(days=n)
    if unidade == "sem":
        return ref + timedelta(days=7 * n)
    if unidade == "mes":
        return _somar_meses(ref, n)
    return _somar_meses(ref, 12 * n)


def _duracao(sinal: int) -> _Resolvedor:
    def resolver(m, ctx):
        n = _numero(m.group("n"))
        if n is None:
            return None
        # "ela faz 20 anos amanhã" é idade; "faz 2 anos que" e "faz 2 anos atrás" são tempo.
        prefixo = m.groupdict().get("p")
        if prefixo == "faz" and m.group("u").startswith("ano") and "atras" not in m.group():
            if not re.match(r"\s+que(?!\w)", ctx.norm[m.end():]):
                return None
        try:
            return _Achado(_somar(ctx.ref, sinal * n, m.group("u")))
        except (ValueError, OverflowError):
            return None
    return resolver


_N_UNIDADE = r"(?P<n>" + _RE_NUM + r")\s+(?P<u>" + _RE_UNIDADE + ")"
_registrar(r"(?:daqui\s+(?:a\s+)?|dentro\s+de\s+)" + _N_UNIDADE, _duracao(+1))
_registrar(r"em\s+" + _N_UNIDADE + r"(?!\s+de\s)", _duracao(+1))
_registrar(r"(?P<p>ha|faz)\s+" + _N_UNIDADE + r"(?:\s+atras)?", _duracao(-1))
_registrar(_N_UNIDADE + r"\s+atras", _duracao(-1), esq=_ESQ_NUM)


# --- datas de calendário: 20/10, 20 de outubro, dia 20, novembro, 2019

def _dia_e_mes(m: re.Match, ctx: _Contexto, dia: int | None, mes: int, ano: int | None):
    """Resolve dia + mês (+ ano). Sem ano: no passado fica com a data deste ano que já
    passou; senão, a próxima. Frase de aniversário/"todo ano" marca recorrente."""
    if dia is None or not _existe(dia, mes):
        return _INVALIDA
    recorrente = ctx.recorrente(m.start())
    if ano is not None:
        d = _data_segura(ano, mes, dia)
        return _Achado(d, recorrente=recorrente) if d else _INVALIDA
    ref = ctx.ref
    deste_ano = _data_segura(ref.year, mes, dia)
    if deste_ano and deste_ano >= ref:
        return _Achado(deste_ano, recorrente=recorrente)
    if ctx.tempo(m.start(), m.end()) == _PASSADO:
        anos = range(ref.year, ref.year - 9, -1)
    elif recorrente:
        return _Achado(proxima_ocorrencia(dia, mes, ref), recorrente=True)
    else:
        anos = range(ref.year + 1, ref.year + 9)
    for ano in anos:
        d = _data_segura(ano, mes, dia)
        if d:
            return _Achado(d, recorrente=recorrente)
    return _INVALIDA


def _dd_mm(m, ctx):
    texto_dia, texto_mes, separador, texto_ano = m.group("d"), m.group("m"), m.group("s"), m.group("a")
    if not texto_ano:
        # "7.5" e "5-10" costumam ser número e intervalo; "1/2" costuma ser fração.
        if separador in ".-" and (len(texto_dia) != 2 or len(texto_mes) != 2):
            return None
        if (separador == "/" and len(texto_dia) == 1 and len(texto_mes) == 1 and not m.group("pre")
                and _palavra_anterior(ctx.norm, m.start()) not in _PREP_DATA):
            return None
    ano = _ano_completo(texto_ano, ctx.ref) if texto_ano else None
    return _dia_e_mes(m, ctx, int(texto_dia), int(texto_mes), ano)


def _iso(m, ctx):
    return _dia_e_mes(m, ctx, int(m.group("d")), int(m.group("m")), int(m.group("a")))


def _dia_de_mes(m, ctx):
    ano = int(m.group("a")) if m.group("a") else None
    return _dia_e_mes(m, ctx, _numero(m.group("d")), _mes(m.group("m")), ano)


def _dia_solto(m, ctx):
    """'dia 20': dia ≥ hoje fica neste mês; dia que já passou vai para o mês seguinte."""
    depois = ctx.norm[m.end():]
    if re.match(r"\s*[/.\-]\s*\d", depois) or re.match(r"\s+de\s+" + _RE_MES_OU_ABREV + _DIR, depois):
        return None
    if _palavra_seguinte(ctx.norm, m.end()) in _UNIDADES:
        return None
    if _palavra_anterior(ctx.norm, m.start()) in ("por", "cada", "ao"):
        return None
    dia = _numero(m.group("d"))
    if dia is None or not 1 <= dia <= 31:
        return _INVALIDA
    ref = ctx.ref
    recorrente = ctx.recorrente(m.start())
    deste_mes = _data_segura(ref.year, ref.month, dia)
    if deste_mes and deste_mes >= ref:
        return _Achado(deste_mes, recorrente=recorrente)
    passo = -1 if ctx.tempo(m.start(), m.end()) == _PASSADO else 1
    if passo == -1 and deste_mes:
        return _Achado(deste_mes, recorrente=recorrente)
    primeiro = ref.replace(day=1)
    for i in range(1, 13):
        mes = _somar_meses(primeiro, passo * i)
        d = _data_segura(mes.year, mes.month, dia)
        if d:
            return _Achado(d, recorrente=recorrente)
    return _INVALIDA


def _mes_por_nome(m, ctx):
    nome = m.group("m")
    ano = m.group("a") or m.group("a2")
    if not ano and not m.group("pre"):
        # Mês sozinho só com preposição antes ("até novembro"). "o Marco ligou" é nome.
        if nome == "marco" and "ç" not in ctx.baixo[m.start("m"):m.end("m")]:
            return None
        if _palavra_anterior(ctx.norm, m.start()) not in _PREP_MES:
            return None
    mes = _MESES[nome]
    if ano:
        return _Achado(date(int(ano), mes, 1), Precisao.MES)
    ref = ctx.ref
    if mes >= ref.month or ctx.tempo(m.start(), m.end()) == _PASSADO:
        return _Achado(date(ref.year, mes, 1), Precisao.MES)
    return _Achado(date(ref.year + 1, mes, 1), Precisao.MES)


def _mes_barra_ano(m, ctx):
    mes = int(m.group("m"))
    return _Achado(date(int(m.group("a")), mes, 1), Precisao.MES) if 1 <= mes <= 12 else None


def _ano(m, ctx):
    if _palavra_seguinte(ctx.norm, m.end()) in _UNIDADES:
        return None
    return _Achado(date(int(m.group("a")), 1, 1), Precisao.ANO)


_DD_MM = (r"(?P<pre>dia\s+)?(?P<d>\d{1,2})(?P<s>[/.\-])(?P<m>\d{1,2})"
          r"(?:(?P=s)(?P<a>\d{4}|\d{2}))?")
_DIA_DE_MES = (r"(?:dia\s+)?(?P<d>" + _RE_DIA + r")\s+de\s+(?P<m>" + _RE_MES_OU_ABREV + r")"
               r"(?:\s+de\s+(?P<a>\d{4}))?")
# Com o dia da semana na frente ("sexta, 20/10") vira uma expressão só; sem ele também
# vale, para que "próxima sexta, 20/10" não perca a data.
_COM_DS = _RE_DS + r"\s*,?\s+"

_registrar(_DD_MM, _dd_mm, esq=_ESQ_NUM, dir_=_DIR_NUM)
_registrar(_COM_DS + _DD_MM, _dd_mm, dir_=_DIR_NUM)
_registrar(r"(?P<a>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})", _iso, esq=_ESQ_NUM, dir_=_DIR_NUM)
_registrar(_DIA_DE_MES, _dia_de_mes, esq=_ESQ_NUM)
_registrar(_COM_DS + _DIA_DE_MES, _dia_de_mes)
_registrar(r"(?:(?:proximo|" + _RE_ESTE + r")\s+)?dia\s+(?P<d>" + _RE_DIA + ")", _dia_solto)
_registrar(_COM_DS + r"dia\s+(?P<d>" + _RE_DIA + ")", _dia_solto)
_registrar(r"(?P<pre>(?:em|n?o\s+mes\s+de|mes\s+de)\s+)?(?P<m>" + _RE_MES + r")"
           r"(?:\s+de\s+(?P<a>\d{4})|\s*/\s*(?P<a2>\d{4}))?", _mes_por_nome)
_registrar(r"(?P<m>\d{1,2})/(?P<a>\d{4})", _mes_barra_ano, esq=_ESQ_NUM, dir_=_DIR_NUM)
_registrar(r"(?:em|desde|ate|n?o\s+ano\s+de|ano\s+de)\s+(?P<a>(?:19|20)\d{2})", _ano, dir_=_DIR_NUM)


# --- datas comemorativas

def _dia_fixo(dia: int, mes: int) -> Callable[[int], date]:
    return lambda ano: date(ano, mes, dia)


def _feriado(calcular: Callable[[int], date], recorrente: bool,
             nao_depois_de: frozenset[str] = frozenset()) -> _Resolvedor:
    def resolver(m, ctx):
        ref = ctx.ref
        if m.group("a"):
            return _Achado(calcular(int(m.group("a"))), recorrente=recorrente)
        qualificador = m.group("q1") or m.group("q2") or ""
        este_ano = calcular(ref.year)
        if qualificador.startswith(("proxim", "que", "q ")):
            d = este_ano if este_ano > ref else calcular(ref.year + 1)
        elif qualificador.startswith(("ultim", "passad")):
            d = este_ano if este_ano < ref else calcular(ref.year - 1)
        else:
            if _palavra_anterior(ctx.norm, m.start()) in nao_depois_de:
                return None
            if este_ano >= ref or ctx.tempo(m.start(), m.end()) == _PASSADO:
                d = este_ano
            else:
                d = calcular(ref.year + 1)
        return _Achado(d, recorrente=recorrente)
    return resolver


_FERIADOS: list[tuple[str, Callable[[int], date], bool, frozenset[str]]] = [
    (r"vespera\s+de\s+natal|noite\s+de\s+natal", _dia_fixo(24, 12), True, frozenset()),
    # "para Natal", "em Natal": a cidade.
    (r"natal", _dia_fixo(25, 12), True, frozenset({"em", "para", "pra", "a"})),
    (r"ano\s+novo", _dia_fixo(1, 1), True, frozenset({"um", "uma"})),
    (r"reveillon|virada\s+d[eo]\s+ano", _dia_fixo(31, 12), True, frozenset()),
    (r"dia\s+dos\s+namorados", _dia_fixo(12, 6), True, frozenset()),
    (r"dia\s+das\s+maes", lambda ano: _enesimo_domingo(ano, 5, 2), True, frozenset()),
    (r"dia\s+dos\s+pais", lambda ano: _enesimo_domingo(ano, 8, 2), True, frozenset()),
    (r"dia\s+das\s+criancas", _dia_fixo(12, 10), True, frozenset()),
    (r"dia\s+(?:internacional\s+)?das?\s+mulher(?:es)?", _dia_fixo(8, 3), True, frozenset()),
    (r"dia\s+dos\s+avos", _dia_fixo(26, 7), True, frozenset()),
    (r"dia\s+do\s+professor", _dia_fixo(15, 10), True, frozenset()),
    (r"dia\s+das\s+bruxas|halloween", _dia_fixo(31, 10), True, frozenset()),
    (r"(?:dia\s+de\s+)?finados", _dia_fixo(2, 11), True, frozenset()),
    (r"dia\s+do\s+trabalh(?:o|ador)", _dia_fixo(1, 5), True, frozenset()),
    # Datas móveis: dependem da Páscoa; não são "mesmo dia todo ano".
    (r"(?:domingo\s+de\s+)?pascoa", pascoa, False, frozenset()),
    (r"(?:terca(?:(?:-|\s+)feira)?\s+de\s+)?carnaval", lambda ano: pascoa(ano) - timedelta(days=47),
     False, frozenset()),
    (r"quarta(?:(?:-|\s+)feira)?\s+de\s+cinzas", lambda ano: pascoa(ano) - timedelta(days=46),
     False, frozenset()),
    (r"sexta(?:(?:-|\s+)feira)?\s+santa", lambda ano: pascoa(ano) - timedelta(days=2), False, frozenset()),
    (r"corpus\s+christi", lambda ano: pascoa(ano) + timedelta(days=60), False, frozenset()),
]
for _regex, _calcular, _recorrente, _nao_depois in _FERIADOS:
    _registrar(r"(?:(?P<q1>proxim[oa]|ultim[oa])\s+)?(?:" + _regex + r")"
               r"(?:\s+(?P<q2>passad[oa]|(?:que|q)\s+vem)|\s+de\s+(?P<a>\d{4}))?",
               _feriado(_calcular, _recorrente, _nao_depois), dir_=_DIR_NUM)


# --- o que parece data mas não é (apaga candidatos que encostam aqui)

_MASCARAS = [re.compile(x) for x in (
    r"(?:r\$|us\$|\$|€|£)\s*\d[\d.,]*",                                 # R$ 20.10
    r"\d+(?:[.,]\d+)?\s*%",
    r"(?<![\w/.])\d{1,2}\s*h(?:\s*\d{2})?(?!\w)",                       # 10h, 10h30
    r"(?<![\w/.])\d{1,2}:\d{2}(?!\d)",                                  # 15:30
    r"(?<!\w)(?:as|das|ate\s+as)\s+\d{1,2}[.:h]\d{2}(?!\d)",            # às 10.12
    r"(?<!\w)(?:nota|notas|media|peso|versao|numero|n[º°]|capitulo|pagina|pag|placar|"
    r"tamanho|altura|temporada|episodio|ep)\s*:?\s*\d+(?:[.,/]\d+)*",  # nota 8/10
    r"(?<!\w)de\s+" + _RE_NUM + r"\s+em\s+" + _RE_NUM + r"\s+" + _RE_UNIDADE + r"(?!\w)",
    r"(?<![\w/])24\s*/\s*7(?!\d)",
    r"(?<!\w)de\s+" + _RE_DS + r"\s+(?:a|ate)\s+" + _RE_DS + r"(?!\w)",   # de segunda a sexta
    r"(?<!\w)tod[oa]s?\s+(?:as\s+|os\s+|o\s+|a\s+)?" + _RE_DS + r"s?(?!\w)",  # toda sexta
    r"(?<!\w)hoje\s+em\s+dia(?!\w)",
)]


# ------------------------------------------------------------ função principal

def _passou(data: date, precisao: Precisao, ref: date) -> bool:
    """O período inteiro já terminou antes da referência."""
    if precisao is Precisao.SEMANA:
        fim = data + timedelta(days=6)
    elif precisao is Precisao.MES:
        fim = data.replace(day=_ultimo_dia(data.year, data.month))
    elif precisao is Precisao.ANO:
        fim = date(data.year, 12, 31)
    else:
        fim = data
    return fim < ref


def extrair_datas(texto: str, referencia: date) -> list[DataEncontrada]:
    """Todas as datas do texto, em ordem, sem sobreposição (a expressão mais longa ganha).

    `texto[d.inicio:d.fim] == d.expressao` sempre. Números que não são datas ("20 anos",
    "R$ 20,50", "10h") e datas que não existem ("31/02") ficam de fora.
    """
    if not texto or not texto.strip():
        return []
    ctx = _Contexto(texto, referencia)
    mascaras = [(m.start(), m.end()) for r in _MASCARAS for m in r.finditer(ctx.norm)]

    candidatos: list[tuple[int, int, object]] = []
    for regex, resolver in _PADROES:
        for m in regex.finditer(ctx.norm):
            if any(m.start() < fim and ini < m.end() for ini, fim in mascaras):
                continue
            achado = resolver(m, ctx)
            if achado is not None:
                candidatos.append((m.start(), m.end(), achado))

    escolhidos: list[tuple[int, int, object]] = []
    for ini, fim, achado in sorted(candidatos, key=lambda c: (c[0] - c[1], c[0])):
        if all(fim <= e_ini or ini >= e_fim for e_ini, e_fim, _ in escolhidos):
            escolhidos.append((ini, fim, achado))

    resultado = []
    for ini, fim, achado in sorted(escolhidos, key=lambda c: c[0]):
        if achado is _INVALIDA:
            continue
        if achado.recorrente and _RE_PRAZO_ANTES.search(ctx.norm, 0, ini):
            # "comprar o presente de aniversário dela até dia 25": o dia 25 é um prazo, não se repete.
            achado.recorrente = False
        resultado.append(DataEncontrada(
            expressao=texto[ini:fim],
            inicio=ini,
            fim=fim,
            data=achado.data,
            precisao=achado.precisao,
            ambigua=len(achado.alternativas) > 1,
            alternativas=list(achado.alternativas),
            recorrente=achado.recorrente,
            passado=_passou(achado.data, achado.precisao, referencia),
        ))
    return resultado


__all__ = ["extrair_datas", "proxima_ocorrencia", "pascoa"]
