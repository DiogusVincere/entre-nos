"""Promessas (DESIGN.md, seção 20.3): o que você prometeu, ficou de fazer ou combinou com ela.

Tudo sai dos registros (ou do que você mesmo anotou na tela) e cita o trecho de onde veio. Sem placar
nem nota: a lista existe para nada ficar esquecido, e comemora mais do que cobra.

Tabela `promessas` (contrato da seção 20.3 + colunas a mais, que os outros recursos podem ler):
- id, texto (o que foi prometido, curto: "Levar ela ao cinema"), prazo (AAAA-MM-DD ou NULL),
  status ("aberta" | "cumprida" | "desisti"), registro_id (de onde veio; NULL = anotada à mão na
  tela), criado_em (data e hora em que entrou no diário), concluida_em (AAAA-MM-DD; NULL enquanto
  aberta, e também numa cumprida cujo registro de prova foi apagado);
- trecho (a frase literal do registro; à mão: o próprio texto), prazo_expressao ("na sexta", como foi
  dito), precisao ("dia" | "semana" | "mes" | "ano": a do prazo), pessoa (para quem, quando não é
  ela: o pedaço literal, "a mãe dela"), forma (prometi | fiquei_de | combinei | vou | disse_que |
  manual), feita_em (AAAA-MM-DD, o dia em que foi prometida: a data do registro, ou a véspera em
  "ontem prometi"), concluida_registro_id (o registro que mostrou que foi cumprida, quando houver).

Regras (primeira pessoa, `extrair`): "prometi (pra ela) (que) X", "fiquei de X", "combinei (com ela)
de X", "vou/tenho que/preciso levar|buscar|ligar... ela X", "falei pra ela que ia X", "jurei que ia
X" viram o extra "promessa" (prazo pelas datas do texto, que regras/datas.py calcula). "Como
prometi", "cumpri a promessa", "o que eu tinha prometido" viram "promessa_cumprida": a revisão mostra
as promessas em aberto parecidas e você escolhe qual (nada é ligado sozinho). Negação ("não
prometi"), dúvida ("não sei se prometi", "será que"), pergunta, ironia ("sqn"), promessa para si
mesmo e para gente sua ("pro meu chefe") ficam de fora.

Hoje: prazo chegando (lembrete); prazo que passou há pouco e promessa aberta há mais de 14 dias sem
prazo (sugestão gentil, que respeita o momento dela); "parece que você cumpriu" quando um registro
depois da promessa conta a mesma ação no passado ("levei ela no cinema") — sempre citando o trecho.

Apagar é apagar: a promessa sai do banco, das cópias automáticas e das chaves de aviso dispensado;
apagar o registro de onde ela veio a apaga junto, e apagar o registro que mostrou o cumprimento tira
a ligação e a data que vieram dele (a sua decisão de que foi cumprida fica). Nada daqui vai para a
IA: a IA só recebe itens e registros, e o contexto das sugestões não lê esta tabela.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..banco import NaoEncontrado
from ..regras.pessoas import e_nome_dela, plano
from ..texto import data_br, frases, normalizar, semelhanca, tempo_atras, termos
from . import cartao, rota
from . import discretos as _discretos_dos_recursos
from . import texto_reservado as _reservado

NOME = "promessas"
ROTULO = "Promessas"
TIPOS_EXTRAS = ("promessa", "promessa_cumprida")

ESQUEMA = [
    """
    CREATE TABLE promessas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        trecho TEXT,
        prazo TEXT,
        prazo_expressao TEXT,
        precisao TEXT,
        pessoa TEXT,
        forma TEXT NOT NULL DEFAULT 'prometi',
        status TEXT NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta', 'cumprida', 'desisti')),
        registro_id INTEGER,
        feita_em TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        concluida_em TEXT,
        concluida_registro_id INTEGER
    );
    CREATE INDEX promessas_registro ON promessas(registro_id);
    CREATE INDEX promessas_status ON promessas(status)
    """,
]
ESQUEMA_VERSAO = 1

STATUS = ("aberta", "cumprida", "desisti")
PRECISOES = ("dia", "semana", "mes", "ano")
FORMAS = ("prometi", "fiquei_de", "combinei", "vou", "disse_que", "manual")

MAX_TEXTO = 300
MAX_PROMESSAS = 5000                  # no diário (protege a tela e o Hoje; ninguém faz tantas)
MAX_IMPORTAR = 20000
DIAS_ANTIGA = 14                      # aberta e sem prazo: depois disso, uma sugestão gentil
DIAS_ATRASO = 30                      # prazo que passou: lembra com carinho até aqui, depois deixa quieto
DIAS_MOMENTO = 3                      # um estado difícil dela nesses dias muda o tom das sugestões
LIMIAR_PARECIDA = 88                  # semelhança para dizer "já existe uma igual em aberto"
LIMIAR_CANDIDATA = 35                 # pontos para uma promessa aparecer como "a que foi cumprida"
MAX_REGISTROS_INDICIO = 3000          # registros lidos para achar "parece que você cumpriu" (os mais recentes)
DIAS_INDICIO_HOJE = 90                # no Hoje, o "parece que você cumpriu" olha só estes dias para trás

TEXTO_INVALIDO = f"Escreva o que foi prometido (até {MAX_TEXTO} caracteres)."
PRAZO_INVALIDO = "O prazo precisa ser uma data (AAAA-MM-DD) ou ficar vazio."
STATUS_INVALIDO = "A situação precisa ser “aberta”, “cumprida” ou “desisti”."
CAMPOS_INVALIDOS = "Só dá para mudar o texto, o prazo e a situação da promessa."
REGISTRO_INVALIDO = "Esse registro não existe (talvez tenha sido apagado)."
DEMAIS = "Já há promessas demais guardadas. Apague as antigas que não fazem mais sentido."
SEM_PROMESSA = "Não encontrei essa promessa. Talvez ela já tenha sido apagada."
CUMPRIDA_INVALIDA = "A promessa cumprida precisa ser uma das promessas em aberto."

_DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro")


# ================================================================ ajudantes

def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _data(v: Any) -> date | None:
    """'AAAA-MM-DD' (ou date) -> date; vazio -> None; o resto -> ValueError."""
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
    raise ValueError(PRAZO_INVALIDO)


def _data_ou_none(v: Any) -> date | None:
    try:
        return _data(v)
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _texto(v: Any, maximo: int = MAX_TEXTO, erro: str = TEXTO_INVALIDO) -> str:
    if not isinstance(v, str):
        raise ValueError(erro)
    limpo = re.sub(r"\s+", " ", v).strip()
    if not limpo or len(limpo) > maximo:
        raise ValueError(erro)
    return limpo


def _id_valido(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _dia(d: date, referencia: date) -> str:
    """'02/10' (com o ano quando não é o da referência)."""
    return d.strftime("%d/%m") if d.year == referencia.year else data_br(d)


def _ultimo_dia(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def periodo(prazo: date, precisao: str | None) -> tuple[date, date]:
    """O começo e o fim do prazo, pela precisão com que ele foi dito ("em dezembro" vale o mês)."""
    if precisao == "semana":
        return prazo, prazo + timedelta(days=6)
    if precisao == "mes":
        return prazo, _ultimo_dia(prazo)
    if precisao == "ano":
        return prazo, date(prazo.year, 12, 31)
    return prazo, prazo


def quando_prazo(prazo: date, precisao: str | None, referencia: date) -> str:
    """O prazo em palavras, com a precisão que foi dita: "amanhã", "sexta (02/10)", "na semana que
    vem", "em dezembro". Nunca um dia que ninguém disse."""
    if precisao == "semana":
        if prazo <= referencia <= prazo + timedelta(days=6):
            return "esta semana"
        if 1 <= (prazo - referencia).days <= 7:
            return "na semana que vem"
        return f"na semana de {_dia(prazo, referencia)}"
    if precisao == "mes":
        if (prazo.year, prazo.month) == (referencia.year, referencia.month):
            return "este mês"
        return f"em {_MESES[prazo.month - 1]}" + ("" if prazo.year == referencia.year else f" de {prazo.year}")
    if precisao == "ano":
        return "este ano" if prazo.year == referencia.year else f"em {prazo.year}"
    dias = (prazo - referencia).days
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "amanhã"
    if dias == -1:
        return "ontem"
    if 2 <= dias <= 6:
        return f"{_DIAS_SEMANA[prazo.weekday()]} ({_dia(prazo, referencia)})"
    return f"{_DIAS_SEMANA[prazo.weekday()]}, {_dia(prazo, referencia)}"


_MINUSCULAS = frozenset("um uma o a os as nunca nao sempre mais menos todo toda todos todas ir ser".split())


def acao(texto: str, trecho: str | None = None) -> str:
    """O texto da promessa para entrar no meio de uma frase: "Levar ela ao cinema" -> "levar ela ao
    cinema". Nome próprio no começo ("Mariana...") continua com maiúscula."""
    if not texto:
        return texto
    primeira = texto.split(" ", 1)[0]
    p = plano(primeira)
    minuscula = (re.fullmatch(r"[a-z]+(?:r|-la|-lo|-las|-los)", p) is not None or p in _MINUSCULAS
                 or (bool(trecho) and re.search(r"(?<![\w])" + re.escape(primeira.lower()) + r"(?![\w])", trecho) is not None))
    return (texto[:1].lower() + texto[1:]) if minuscula else texto


# ================================================================ extração (regras)

# Verbos de compromisso com ela ("vou levar ela", "tenho que buscar ela", "preciso ligar pra ela").
# Sem "dar"/"comprar" (presentes é de outro recurso) e sem "ver"/"encontrar" (encontro não é promessa).
_VERBOS_COMPROMISSO = ("levar", "buscar", "pegar", "acompanhar", "ajudar", "visitar", "ligar", "cozinhar",
                       "fazer", "consertar", "arrumar", "montar", "ensinar", "mostrar", "apresentar",
                       "surpreender", "chamar", "convidar", "resolver", "cuidar")
_RE_AUX = r"(?:vou|vamos|vou\s+ter\s+(?:que|q|de)|tenho\s+(?:que|q|de)|temos\s+(?:que|q|de)|preciso|precisamos)"

_GATILHOS: tuple[tuple[str, re.Pattern], ...] = (
    ("prometi", re.compile(
        r"(?<![\w-])(?:(?:eu|ja|eu\s+ja|tambem)\s+)?(?:prometi|prometemos|a\s+gente\s+(?:ja\s+)?prometeu"
        r"|(?:ela\s+)?me\s+fez\s+prometer|fiz\s+(?:uma\s+)?promessa)(?![\w-])")),
    ("fiquei_de", re.compile(r"(?<![\w-])(?:(?:eu|ja)\s+)?(?:fiquei|ficamos|a\s+gente\s+ficou)\s+de(?![\w-])")),
    ("combinei", re.compile(
        r"(?<![\w-])(?:(?:eu|ja)\s+)?(?:combinei|combinamos|a\s+gente\s+(?:ja\s+)?combinou)(?![\w-])")),
    ("vou", re.compile(
        r"(?<![\w-])(?:(?:eu|ainda|tambem)\s+)?" + _RE_AUX + r"\s+(?:(?:mesmo|ainda|tambem|sim|logo)\s+)?"
        r"(?P<verbo>" + "|".join(_VERBOS_COMPROMISSO) + r")(?:-la|-lo)?(?![\w-])")),
    ("disse_que", re.compile(
        r"(?<![\w-])(?:eu\s+(?:disse|falei|garanti|jurei)|falei|garanti|jurei|disse(?=\s+(?:pra|para|a)\s+ela\b))"
        r"(?:\s+(?:pra|para|a)\s+ela)?\s+que\s+(?:eu\s+)?(?:ia|iria|vou|vamos|a\s+gente\s+ia)(?![\w-])")),
)
_MOTIVOS = {
    "prometi": "Você escreveu “{g}”.",
    "fiquei_de": "Você escreveu “{g}”: um compromisso seu.",
    "combinei": "Você escreveu “{g}”: algo combinado.",
    "vou": "Você escreveu “{g}”: parece um compromisso seu com ela.",
    "disse_que": "Você escreveu “{g}”: você disse a ela que ia fazer.",
}
_FORCA = {"prometi": "forte", "fiquei_de": "forte", "combinei": "forte", "vou": "media", "disse_que": "media"}

# Promessa cumprida, dita com todas as letras.
_RE_CUMPRIDA = re.compile(
    r"(?<![\w-])(?:cumpri|cumprimos|a\s+gente\s+cumpriu)\s+(?:(?:a|minha|nossa|aquela|essa|esta|o)\s+)?"
    r"(?:promessa|prometido|combinado|que\s+(?:eu\s+)?(?:tinha\s+|havia\s+)?(?:prometi|prometido|combinei|combinamos))"
    r"(?![\w-])"
    r"|(?<![\w-])como\s+(?:eu\s+)?(?:tinha\s+|havia\s+|ja\s+)?(?:prometi|prometido|combinado|combinei|combinamos"
    r"|a\s+gente\s+(?:tinha\s+)?combinado)(?![\w-])"
    r"|(?<![\w-])promessa\s+cumprida(?![\w-])"
    r"|(?<![\w-])(?:o|aquilo)\s+que\s+(?:eu\s+)?(?:tinha\s+|havia\s+)?(?:prometi|prometido)(?![\w-])")

_RE_NEGACAO_ANTES = re.compile(r"(?:^|\s)(?:nao|n|nunca|jamais|nem|ainda\s+nao)\s+(?:(?:eu|a\s+gente|te|lhe)\s+)?"
                               r"(?:\w+\s+)?$")
_RE_QUE_ANTES = re.compile(r"(?:^|\s)(?:(\w+)\s+)?que\s+(?:eu\s+)?(?:tinha\s+|havia\s+|ja\s+)?$")
_RE_COMO_ANTES = re.compile(r"(?:^|\s)como\s+(?:eu\s+)?(?:tinha\s+|havia\s+|ja\s+)?$")
# "acho que prometi", "ela lembrou que eu prometi": o "que" introduz o que foi dito (é promessa).
_COMPLEMENTO = frozenset("acho achei acredito lembrei lembrou lembro lembra lembrar disse falei falou sei sabe sabia "
                         "esqueci esquece esquecer percebi contei contou jurei garanti admito confesso pensei reconheco "
                         "avisei avisou verdade certeza".split())
_RE_SE_ANTES = re.compile(r"(?:^|\s)se\s+(?:eu\s+)?$")
_RE_DUVIDA = re.compile(r"(?<!\w)(?:sera\s+que|nao\s+sei\s+se|n\s+sei\s+se|nao\s+lembro\s+se|sei\s+la\s+se"
                        r"|nao\s+faco\s+ideia\s+se)(?!\w)")
_RE_ACHO = re.compile(r"(?<!\w)(?:acho\s+que|acredito\s+que|talvez|provavelmente)(?!\w)")
_RE_IRONIA = re.compile(r"(?<!\w)(?:sqn|so\s+que\s+nao)(?!\w)")
# Onde termina o que foi prometido.
_RE_FIM_CONTEUDO = re.compile(
    r"[.;!?…:()\[\]]|\s[-–—]\s|,\s*(?:mas|e|so\s+que|porque|pq|porem|entao|ai|dai|enquanto)(?![\w-])"
    r"|\s(?:mas|porque|pq|porem|so\s+que|kkk+|rs+|haha\w*|sqn)(?![\w-])"
    r"|\s+e\s+(?:ela|eu|ele|a\s+gente|nos)(?![\w-])")
_RE_DOIS_PONTOS = re.compile(r"\s*(?:(?:uma|duas|tres)\s+coisas?\s*)?:\s*")
# Começo do conteúdo que não faz parte do que foi prometido ("que eu ia ...").
_RE_LIXO_INICIO = re.compile(r"^(?:\s*,?\s*(?:que|q|eu|a\s+gente|nos|de|sim|mesmo|tambem|ainda)(?![\w-]))+")
# "que eu ia levar ela": o "ia" é auxiliar e sai; "que eu ia no médico": é o verbo, e vira "ir".
_RE_AUX_INICIO = re.compile(r"^(?:ia|iria|iriamos|vou|vamos|vai|vao|irei|iremos)\s+")
_RE_INFINITIVO = re.compile(r"[a-z]+(?:r|-la|-lo|-las|-los)(?![\w])")
_RE_AUX_DEPOIS_NEGACAO = re.compile(r"^((?:nunca\s+mais|nao|nunca|sempre)\s+)(?:ia|vou|iria|vamos)\s+")
_PREPS_DATA = frozenset("na no nas nos em dia ate pra para pro de do da nesse neste nessa nesta esse este essa "
                        "esta agora la ao".split())
_FIM_SOLTO = frozenset("e ou mas que pra para pro de do da na no em a o ao as os com pela pelo".split())
_VAGOS = frozenset("ela dela ele dele nela isso isto aquilo algo coisa coisas nada tudo ver fazer la lo ai aqui "
                   "depois mesmo sim nao promessa".split())
_DESTINO_DELA = frozenset("namorada mina gata amor princesa mulher noiva esposa vida linda".split())
_PARENTES_DELA = frozenset("sogra sogro sogros cunhada cunhado cunhados".split())
_RE_DESTINO = re.compile(r"\s*(?:a|pra|para|pro|pros|pras|com)\s+")
_RE_MEU = re.compile(r"(?:(?:o|a|os|as)\s+)?(?:meu|minha|meus|minhas)\s+([a-z]+)")
_RE_MIM = re.compile(r"mim(?:\s+mesm[oa])?(?![\w-])")
_RE_PARENTE_DELA = re.compile(r"(?:(?:a|o|os|as)\s+)?[a-z]+(?:\s+[a-z]+)?\s+dela(?![\w-])")
_RE_GENTE_SUA = re.compile(r"(?<!\w)(?:meu|minha|meus|minhas)\s+(?!(?:" + "|".join(sorted(_PARENTES_DELA | _DESTINO_DELA))
                           + r")(?!\w))")


@dataclass
class _Frase:
    ini: int          # posição da frase no texto do registro
    texto: str        # a frase literal
    n: str            # a mesma, minúscula e sem acento, do mesmo tamanho


def _campo(obj: Any, nome: str, padrao: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nome, padrao)
    return getattr(obj, nome, padrao)


def _valor(v: Any) -> Any:
    return getattr(v, "value", v)


def _antes(fr: _Frase, pos: int, largura: int = 40) -> str:
    return fr.n[max(0, pos - largura):pos]


def _destino(fr: _Frase, pos: int, pessoas: list, nome_dela: str | None) -> tuple[str, str | None, int]:
    """Para quem foi a promessa, olhando logo depois do gatilho ("prometi pra ela", "pra mãe dela",
    "pro meu chefe", "pra mim mesmo"). Devolve (situação, pessoa literal, onde o resto começa):
    situação "ela", "pessoa", "minha" (gente do usuário: fica de fora), "mim" (para si mesmo) ou ""."""
    m = _RE_DESTINO.match(fr.n, pos)
    if not m:
        return "", None, pos
    k = m.end()
    resto = fr.n[k:]
    if re.match(r"ela(?![\w-])", resto):
        return "ela", None, k + 3
    mim = _RE_MIM.match(resto)
    if mim:
        return "mim", None, k + mim.end()
    meu = _RE_MEU.match(resto)
    if meu:
        palavra = meu.group(1)
        if palavra in _DESTINO_DELA:
            return "ela", None, k + meu.end()
        if palavra in _PARENTES_DELA:
            return "pessoa", fr.texto[k:k + meu.end()], k + meu.end()
        return "minha", None, k + meu.end()
    for p in pessoas:
        ini = _campo(p, "inicio", -1) - fr.ini
        fim = _campo(p, "fim", -1) - fr.ini
        if k - 1 <= ini <= k + 3 and fim > ini:
            trecho = fr.texto[ini:fim]
            if nome_dela and e_nome_dela(trecho, nome_dela):
                return "ela", None, fim
            return "pessoa", trecho, fim
    parente = _RE_PARENTE_DELA.match(resto)
    if parente and not re.match(r"(?:(?:a|o)\s+)?(?:casa|carro|vida|mae\s+e|amiga\s+e)\b", resto):
        return "pessoa", fr.texto[k:k + parente.end()], k + parente.end()
    nome = re.match(r"(?:(?:a|o)\s+)?([A-ZÀ-Ý][\wÀ-ÿ]+)", fr.texto[k:])
    if nome:
        if nome_dela and e_nome_dela(nome.group(0), nome_dela):
            return "ela", None, k + nome.end()
        if re.match(r"\s+(?:que|de)(?![\w-])", fr.n[k + nome.end():]):
            return "pessoa", fr.texto[k:k + nome.end()], k + nome.end()
    return "", None, pos


def _fim_conteudo(fr: _Frase, ini: int, outros: list[int]) -> int:
    fim = len(fr.texto)
    m = _RE_FIM_CONTEUDO.search(fr.n, ini)
    if m:
        fim = min(fim, m.start())
    for o in outros:
        if ini < o < fim:
            fim = o
    return fim


def _sem_datas(fr: _Frase, ini: int, fim: int, datas: list) -> list[tuple[int, int]]:
    """Os pedaços de [ini, fim) que sobram tirando as expressões de data (e a preposição solta antes
    delas: "levar ela ao cinema na sexta" -> "levar ela ao cinema")."""
    cortes = []
    for d in datas:
        a, b = _campo(d, "inicio", -1) - fr.ini, _campo(d, "fim", -1) - fr.ini
        if a < ini or a >= fim:
            continue
        b = min(b, fim)
        antes = re.search(r"(?:^|\s)([a-z]+)\s+$", fr.n[ini:a])
        if antes and antes.group(1) in _PREPS_DATA:
            a = ini + antes.start(1)
        cortes.append((a, b))
    pedacos, pos = [], ini
    for a, b in sorted(cortes):
        if a > pos:
            pedacos.append((pos, a))
        pos = max(pos, b)
    if pos < fim:
        pedacos.append((pos, fim))
    return pedacos


def _infinitivo_do_condicional(palavra: str) -> str:
    """'levaria' -> 'levar', 'faria' -> 'fazer' (a mesma palavra que a pessoa escreveu, no infinitivo)."""
    p = plano(palavra)
    irregulares = {"faria": "fazer", "diria": "dizer", "traria": "trazer"}
    if p in irregulares:
        novo = irregulares[p]
    elif re.fullmatch(r"[a-z]{2,}(?:aria|eria|iria)", p):
        novo = palavra[:-4] + palavra[-4] + "r"
    else:
        return palavra
    return novo[:1].upper() + novo[1:] if palavra[:1].isupper() else novo


def _sem_inicio_solto(texto: str) -> str:
    """Tira do começo o "que eu ia" (auxiliar: "ia levar" -> "levar"; verbo: "ia no médico" ->
    "ir no médico") e o que não faz parte do que foi prometido."""
    n = plano(texto)
    for _ in range(4):
        lixo = _RE_LIXO_INICIO.match(n)
        if lixo and lixo.end():
            texto, n = texto[lixo.end():], n[lixo.end():]
            corte = len(texto) - len(texto.lstrip(" ,:"))
            texto, n = texto[corte:], n[corte:]
        aux = _RE_AUX_INICIO.match(n)
        if not aux:
            break
        seguinte = n[aux.end():]
        if _RE_INFINITIVO.match(seguinte) or _RE_AUX_INICIO.match(seguinte) or _RE_LIXO_INICIO.match(seguinte):
            texto, n = texto[aux.end():], seguinte
        else:
            texto, n = "ir " + texto[aux.end():], "ir " + seguinte
            break
    aux = _RE_AUX_DEPOIS_NEGACAO.match(n)
    if aux:
        texto = texto[:aux.end(1)] + texto[aux.end():]
    primeira, _espaco, resto = texto.partition(" ")
    return (_infinitivo_do_condicional(primeira) + (" " + resto if resto else "")) if primeira else texto


# "no aniversário dela eu ia fazer um jantar": o quando vai para o fim, entre parênteses.
_RE_ADVERBIAL = re.compile(r"^((?:no|na|nos|nas|em|quando|depois|antes|ate|durante|assim\s+que)\s+(?:\S+\s+){0,6}?)"
                           r"(?:eu\s+|a\s+gente\s+)?(?:ia|iria|vou|vamos|vai)\s+(?=[a-z]+(?:r|-la|-lo)(?![\w]))")


def _cortar(palavras: list[str], maximo: int) -> tuple[list[str], bool]:
    cortou = len(palavras) > maximo
    palavras = palavras[:maximo]
    while palavras and plano(palavras[-1]).strip(",.;:") in _FIM_SOLTO:
        palavras.pop()
    return palavras, cortou


def _limpar_pedacos(fr: _Frase, pedacos: list[tuple[int, int]]) -> str:
    """Junta os pedaços (no texto original), tira o "que eu ia" do começo e o que ficou solto no fim.
    Só palavras que estão no trecho (o infinitivo de "ia levar"/"levaria" é a mesma palavra)."""
    texto = " ".join(fr.texto[a:b] for a, b in pedacos)
    texto = re.sub(r"\s+", " ", texto).strip(" ,;:-–—\"“”'")
    texto = _sem_inicio_solto(texto)
    quando = ""
    adverbial = _RE_ADVERBIAL.match(plano(texto))
    if adverbial:
        quando = texto[:adverbial.end(1)].strip(" ,")
        texto = texto[adverbial.end():]
    palavras, cortou = _cortar(texto.split(), 12 if quando else 14)
    saida = " ".join(palavras).rstrip(",;:") + ("…" if cortou else "")
    if quando and saida:
        saida += f" ({quando})"
    return saida


def _conteudo_util(texto: str) -> bool:
    return any(t not in _VAGOS and len(t) > 2 for t in termos(texto))


def _menciona_ela(n: str, texto: str, nome_dela: str | None) -> bool:
    if re.search(r"(?<![\w])(?:ela|dela|nela)(?![\w])|-la(?![\w])", n):
        return True
    if nome_dela:
        for m in re.finditer(r"[A-ZÀ-Ý][\wÀ-ÿ]+", texto):
            if e_nome_dela(m.group(0), nome_dela):
                return True
    return False


def _prazo(fr: _Frase, gatilho: int, limite: int, datas: list, referencia: date) -> tuple[Any, Any]:
    """(data passada antes do gatilho = quando foi prometida, primeira data futura entre ele e o
    próximo gatilho = prazo: "prometi o cinema e fiquei de ligar amanhã" não põe "amanhã" no cinema)."""
    feita, prazo = None, None
    for d in sorted(datas, key=lambda x: _campo(x, "inicio", 0)):
        a = _campo(d, "inicio", -1) - fr.ini
        if a < 0 or a >= limite or _campo(d, "data") is None:
            continue
        if _campo(d, "passado", False):
            if a < gatilho and feita is None and _campo(d, "data") <= referencia:
                feita = d
            continue
        if a >= gatilho and prazo is None:
            prazo = d
    return feita, prazo


def _relativa(antes: str) -> bool:
    """Fala de uma promessa que já existia ("como prometi", "o jantar que prometi"): não é nova.
    "Acho que prometi", "ela lembrou que eu prometi" continuam sendo promessa."""
    if _RE_COMO_ANTES.search(antes):
        return True
    m = _RE_QUE_ANTES.search(antes)
    return bool(m) and (m.group(1) or "") not in _COMPLEMENTO


def _promessas_da_frase(fr: _Frase, datas: list, referencia: date, pessoas: list, nome_dela: str | None) -> list[dict]:
    if fr.texto.rstrip().endswith("?") or _RE_IRONIA.search(fr.n):
        return []
    achados = []
    for forma, rx in _GATILHOS:
        for m in rx.finditer(fr.n):
            achados.append((m.start(), m.end(), forma, m))
    achados.sort(key=lambda x: (x[0], -x[1]))
    # Gatilhos que se sobrepõem ("eu disse que ia levar ela"): fica o que começa antes (e o maior).
    escolhidos: list[tuple[int, int, str, re.Match]] = []
    for a in achados:
        if escolhidos and a[0] < escolhidos[-1][1]:
            continue
        # "combinamos que vou buscar ela", "prometi pra ela que vou levar ela": o segundo gatilho é o
        # conteúdo do primeiro (fica um só, com a forma e a força do primeiro).
        if escolhidos and re.fullmatch(r"\s*(?:(?:pra|para|a|pro|com)\s+(?:ela|(?:a|o)\s+[\w-]+(?:\s+dela)?|[\w-]+)\s*)?"
                                       r",?\s*(?:que|q|de)?\s*(?:eu\s+|a\s+gente\s+)?", fr.n[escolhidos[-1][1]:a[0]]):
            continue
        escolhidos.append(a)
    inicios = [a[0] for a in escolhidos]
    duvida = _RE_DUVIDA.search(fr.n)
    saida = []
    for ini, fim, forma, m in escolhidos:
        antes = _antes(fr, ini)
        if _RE_NEGACAO_ANTES.search(antes) or _RE_SE_ANTES.search(antes):
            continue
        if forma == "prometi" and _relativa(antes):
            continue                                  # "como prometi": é cumprimento, não promessa nova
        if duvida and duvida.start() < ini:
            continue
        pessoa, comeco = None, fim
        if forma in ("prometi", "combinei"):
            situacao, pessoa, comeco = _destino(fr, fim, pessoas, nome_dela)
            if situacao in ("mim", "minha"):
                continue
        if forma == "vou":
            comeco = m.start("verbo")
        if forma == "combinei":
            de = re.match(r"\s*,?\s*(?:de|que)(?![\w-])", fr.n[comeco:])
            if de:
                comeco += de.end()
        dois_pontos = _RE_DOIS_PONTOS.match(fr.n, comeco)
        if dois_pontos:
            comeco = dois_pontos.end()
        conteudo_fim = _fim_conteudo(fr, comeco, inicios)
        if conteudo_fim <= comeco:
            continue
        if forma == "fiquei_de" and not re.match(r"\s*[a-z]+(?:r|-la|-lo)(?![\w])", fr.n[comeco:]):
            continue                                  # "fiquei de cama", "fiquei de boa": não é compromisso
        miolo_n, miolo = fr.n[ini:conteudo_fim], fr.texto[ini:conteudo_fim]
        if forma == "vou" and not _menciona_ela(miolo_n, miolo, nome_dela):
            continue                                  # "vou levar o carro na oficina": não é com ela
        if (forma == "combinei" and pessoa and not re.search(r"(?<![\w-])dela(?![\w-])", plano(pessoa))
                and plano(pessoa).split()[-1] not in _PARENTES_DELA
                and not _menciona_ela(fr.n[comeco:conteudo_fim], fr.texto[comeco:conteudo_fim], nome_dela)):
            continue                                  # "combinei com o Pedro de jogar bola": combinado com um amigo seu

        if _RE_GENTE_SUA.search(fr.n[comeco:conteudo_fim]) and not _menciona_ela(fr.n[comeco:conteudo_fim], "", None):
            continue                                  # "fiquei de mandar o relatório pro meu chefe"
        texto = _limpar_pedacos(fr, _sem_datas(fr, comeco, conteudo_fim, datas))
        if not texto or not _conteudo_util(texto):
            continue
        limite = min([o for o in inicios if o > ini] or [len(fr.texto)])
        feita, prazo = _prazo(fr, ini, limite, datas, referencia)
        gatilho = fr.texto[ini:fim].strip()
        fraca = bool(_RE_ACHO.search(fr.n[:ini]))
        prazo_data = _campo(prazo, "data") if prazo is not None else None
        alternativas = []
        if prazo is not None and _campo(prazo, "ambigua", False):
            alternativas = [x for x in (_campo(prazo, "alternativas") or []) if x != prazo_data]
        saida.append({
            "tipo": "promessa",
            "texto": _cap(texto),
            "resumo": _cap(texto),
            "trecho": fr.texto,
            "forma": forma,
            "forca": "fraca" if fraca else _FORCA[forma],
            "motivo": _MOTIVOS[forma].format(g=gatilho) + (" Com “acho que”: confira se foi isso mesmo." if fraca else ""),
            "pessoa": pessoa,
            "feita_em": _campo(feita, "data") if feita is not None else referencia,
            "prazo": prazo_data,
            "prazo_expressao": _campo(prazo, "expressao") if prazo is not None else None,
            "precisao": _valor(_campo(prazo, "precisao")) if prazo is not None else None,
            "prazo_alternativas": alternativas,
        })
    return saida


def _cumpridas_da_frase(fr: _Frase) -> list[dict]:
    if fr.texto.rstrip().endswith("?") or _RE_IRONIA.search(fr.n):
        return []
    m = _RE_CUMPRIDA.search(fr.n)
    if not m:
        return []
    antes = fr.n[:m.start()]
    # "Ainda não cumpri a promessa", "não fiz o que prometi", "não sei se cumpri": não foi cumprida.
    if _RE_NEGACAO_ANTES.search(antes[-40:]) or _RE_DUVIDA.search(antes) \
            or re.search(r"(?<!\w)(?:nao|n|nunca|ainda\s+nao)\s+(?:\w+\s+){0,2}$", antes):
        return []
    return [{
        "tipo": "promessa_cumprida",
        "resumo": "Uma promessa cumprida",
        "trecho": fr.texto,
        "motivo": f"Você escreveu “{fr.texto[m.start():m.end()].strip()}”.",
    }]


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Promessas e promessas cumpridas do texto (primeira pessoa), cada uma com a frase literal."""
    nome_dela = _campo(conhecimento, "nome_dela")
    saida: list[dict] = []
    for ini, _fim, frase in frases(texto):
        fr = _Frase(ini, frase, plano(frase))
        saida.extend(_promessas_da_frase(fr, datas or [], referencia, pessoas or [], nome_dela))
        saida.extend(_cumpridas_da_frase(fr))
    return saida


# ================================================================ gravar o que foi confirmado

def _linhas(banco: Any, onde: str = "", parametros: tuple = ()) -> list[dict]:
    return banco.consultar(f"SELECT * FROM promessas {onde}", parametros)


def _uma(banco: Any, pid: int) -> dict:
    linhas = banco.consultar("SELECT * FROM promessas WHERE id = ?", (pid,))
    if not linhas:
        raise NaoEncontrado(SEM_PROMESSA)
    return linhas[0]


def _mesma(a: str | None, b: str | None) -> bool:
    return bool(a and b) and (normalizar(a) == normalizar(b) or semelhanca(a, b) >= LIMIAR_PARECIDA)


def _literal(v: Any, trecho: str, maximo: int = 120) -> str | None:
    """Um pedaço que tem de estar escrito no trecho (nunca inventar); senão None."""
    if not isinstance(v, str) or not v.strip() or len(v) > maximo:
        return None
    return v.strip() if plano(v.strip()) in plano(trecho) else None


def _conferir_lotacao(banco: Any, novas: int = 1) -> None:
    total = banco.consultar("SELECT count(*) AS n FROM promessas")[0]["n"]
    if total + novas > MAX_PROMESSAS:
        raise ValueError(DEMAIS)


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava as promessas confirmadas e marca as cumpridas que você escolheu na revisão."""
    for e in extras:
        if e.get("tipo") == "promessa":
            _gravar_promessa(banco, registro_id, e, referencia)
        elif e.get("tipo") == "promessa_cumprida":
            _gravar_cumprida(banco, registro_id, e, referencia)


def _gravar_promessa(banco: Any, registro_id: int, e: dict, referencia: date) -> None:
    trecho = e["trecho"]
    bruto = e.get("texto") if isinstance(e.get("texto"), str) and e.get("texto").strip() else e.get("resumo")
    texto = _cap(_texto(bruto))
    prazo = _data(e.get("prazo"))
    precisao = (e.get("precisao") if e.get("precisao") in PRECISOES else "dia") if prazo else None
    expressao = _literal(e.get("prazo_expressao"), trecho, 80) if prazo else None
    feita = _data_ou_none(e.get("feita_em")) or referencia
    if feita > referencia or (referencia - feita).days > 366:
        feita = referencia
    forma = e.get("forma") if e.get("forma") in FORMAS and e.get("forma") != "manual" else "prometi"
    # Já existe igual em aberto (o mesmo assunto contado de novo): não duplica.
    for antiga in _linhas(banco, "WHERE status = 'aberta'"):
        if _mesma(antiga["texto"], texto) and (prazo is None or antiga["prazo"] == _iso(prazo)):
            return
    _conferir_lotacao(banco)
    banco.inserir("promessas", {
        "texto": texto, "trecho": trecho, "prazo": prazo, "prazo_expressao": expressao, "precisao": precisao,
        "pessoa": _literal(e.get("pessoa"), trecho), "forma": forma, "status": "aberta",
        "registro_id": registro_id, "feita_em": feita, "criado_em": _agora(),
    })


def _gravar_cumprida(banco: Any, registro_id: int, e: dict, referencia: date) -> None:
    bruto = e.get("promessa_id")
    if bruto is None:
        return                                       # nenhuma escolhida na revisão: nada muda
    pid = _id_valido(bruto)
    if pid is None:
        raise ValueError(CUMPRIDA_INVALIDA)
    linhas = banco.consultar("SELECT id, status FROM promessas WHERE id = ?", (pid,))
    if not linhas or linhas[0]["status"] != "aberta":
        return                                       # apagada ou já concluída nesse meio-tempo
    banco.atualizar("promessas", pid, {"status": "cumprida", "concluida_em": referencia,
                                       "concluida_registro_id": registro_id})
    _esquecer(banco, pid)


def _esquecer(banco: Any, pid: int) -> None:
    """As chaves de aviso dispensado desta promessa (podem revelar o prazo de algo apagado)."""
    banco.esquecer_alertas(f"{NOME}:p{pid}:")


# ================================================================ leitura: momento dela, eventos, indícios

def _registros_datas(banco: Any, ids: set[int]) -> dict[int, date]:
    if not ids:
        return {}
    marcas = ", ".join("?" for _ in ids)
    return {l["id"]: _data_ou_none(l["data_registro"])
            for l in banco.consultar(f"SELECT id, data_registro FROM registros WHERE id IN ({marcas})", tuple(ids))}


def _dela(linha: dict) -> bool:
    return linha.get("pessoa_id") is None and not (linha.get("pessoa") or "").strip()


def _da_frase_reservada(linha: dict, discretos: set[int]) -> bool:
    """Item de um registro discreto (o ciclo) com palavras reservadas no resumo ou no trecho: o humor
    dele nunca explica nada aqui, e a frase não é citada (seção 20.10)."""
    return linha.get("registro_id") in discretos and (
        _reservado(linha.get("resumo") or "") or _reservado(linha.get("trecho") or ""))


def momento(banco: Any, referencia: date) -> dict | None:
    """O estado mais recente dela nos últimos dias, se for difícil (cansada, triste, estressada...):
    {resumo, trecho, registro_id, data}. Se o mais recente é bom, None (ela está bem). O humor tirado
    de uma frase do ciclo não conta (o ciclo nunca explica humor, seção 20.8)."""
    linhas = banco.consultar(
        "SELECT i.resumo, i.trecho, i.polaridade, i.registro_id, i.pessoa, i.pessoa_id, r.data_registro"
        " FROM itens i JOIN registros r ON r.id = i.registro_id"
        " WHERE i.tipo = 'estado' AND i.status = 'ativo' AND r.data_registro BETWEEN ? AND ?"
        " ORDER BY r.data_registro DESC, i.id DESC",
        ((referencia - timedelta(days=DIAS_MOMENTO)).isoformat(), referencia.isoformat()))
    discretos = _discretos_dos_recursos(banco) if linhas else set()
    for l in linhas:
        if not _dela(l) or _da_frase_reservada(l, discretos):
            continue
        if (l["polaridade"] or 0) >= 0:
            return None
        return {"resumo": l["resumo"], "trecho": l["trecho"], "registro_id": l["registro_id"],
                "data": _data_ou_none(l["data_registro"])}
    return None


def _apoios(banco: Any) -> list[dict]:
    """O que ela disse que ajuda (APOIO +1, dela, ativo)."""
    linhas = banco.consultar(
        "SELECT i.alvo, i.resumo, i.trecho, i.registro_id, i.pessoa, i.pessoa_id, r.data_registro FROM itens i"
        " JOIN registros r ON r.id = i.registro_id"
        " WHERE i.tipo = 'apoio' AND i.status = 'ativo' AND COALESCE(i.polaridade, 1) > 0"
        " ORDER BY r.data_registro DESC, i.id DESC LIMIT 50")
    discretos = _discretos_dos_recursos(banco) if linhas else set()
    return [l for l in linhas if _dela(l) and not _da_frase_reservada(l, discretos)]


def _eventos_no_dia(banco: Any, dia: date) -> list[dict]:
    linhas = banco.consultar(
        "SELECT i.id, i.resumo, i.trecho, i.registro_id, i.pessoa, i.pessoa_id, r.data_registro"
        " FROM itens i JOIN registros r ON r.id = i.registro_id"
        " WHERE i.tipo = 'evento' AND i.status = 'ativo' AND i.data_evento = ?"
        " AND COALESCE(i.precisao, 'dia') = 'dia' ORDER BY i.id", (dia.isoformat(),))
    discretos = _discretos_dos_recursos(banco) if linhas else set()
    saida = []
    for l in linhas:
        if not _dela(l) or (l.get("registro_id") in discretos and _reservado(l.get("resumo") or "")):
            continue
        if l.get("registro_id") in discretos and _reservado(l.get("trecho") or ""):
            l = {**l, "trecho": ""}                      # o acontecimento fica, sem citar a frase do ciclo
        saida.append(l)
    return saida


def _mesma_frase(a: str | None, b: str | None) -> bool:
    return bool(a and b) and (plano(a.strip()) in plano(b) or plano(b.strip()) in plano(a))


def _conflitos(banco: Any, p: dict, prazo: date | None, referencia: date) -> list[dict]:
    """Algo dela no mesmo dia do prazo (uma prova, uma viagem): vale combinar se o dia ainda serve."""
    if prazo is None or (p.get("precisao") or "dia") != "dia":
        return []
    saida = []
    for ev in _eventos_no_dia(banco, prazo):
        if _mesma_frase(ev["trecho"], p.get("trecho")):
            continue                                  # é a própria promessa vista como evento
        dr = _data_ou_none(ev["data_registro"])
        saida.append({"resumo": ev["resumo"], "trecho": ev["trecho"], "registro_id": ev["registro_id"],
                      "registrado_em": dr, "texto": f"No mesmo dia ela tem: {ev['resumo'].rstrip('.')}"
                      + (f" (você registrou em {_dia(dr, referencia)})." if dr else ".")})
    return saida[:3]


# Formas no passado ("levei", "fomos") de um verbo no infinitivo, para achar o cumprimento.
_IRREGULARES = {
    "fazer": ("fiz", "fizemos"), "ir": ("fui", "fomos"), "dar": ("dei", "demos"), "ver": ("vi", "vimos"),
    "ter": ("tive", "tivemos"), "trazer": ("trouxe", "trouxemos"), "por": ("pus", "pusemos"),
    "dizer": ("disse", "dissemos"), "vir": ("vim", "viemos"), "poder": ("pude", "pudemos"),
    "saber": ("soube", "soubemos"), "querer": ("quis", "quisemos"), "estar": ("estive", "estivemos"),
    "ser": ("fui", "fomos"), "ler": ("li", "lemos"), "pedir": ("pedi", "pedimos"), "ouvir": ("ouvi", "ouvimos"),
}
# Quem leva ela a algum lugar também "foi com ela": "fomos ao cinema" cumpre "levar ela ao cinema".
_SINONIMOS_PASSADO = {"levar": ("fomos",), "acompanhar": ("fomos",), "visitar": ("fomos",),
                      "assistir": ("vimos",), "ver": ("assistimos", "assisti")}


def formas_passado(verbo: str) -> set[str]:
    """'levar' -> {'levei', 'levamos'}; 'começar' -> {'comecei', 'comecamos'} (sem acento, minúsculas)."""
    v = verbo.lower().strip()
    clitico = re.fullmatch(r"([^\W\d_]+)-(?:la|lo|las|los)", v)
    if clitico:
        v = clitico.group(1)
        v = plano(v) + ("" if plano(v).endswith("r") else "r")
    n = plano(v)
    if n in _IRREGULARES:
        formas = set(_IRREGULARES[n])
    elif v.endswith("çar"):
        formas = {n[:-3] + "cei", n[:-3] + "camos"}
    elif n.endswith("car"):
        formas = {n[:-3] + "quei", n[:-3] + "camos"}
    elif n.endswith("gar"):
        formas = {n[:-3] + "guei", n[:-3] + "gamos"}
    elif n.endswith("ar"):
        formas = {n[:-2] + "ei", n[:-2] + "amos"}
    elif n.endswith("er"):
        formas = {n[:-2] + "i", n[:-2] + "emos"}
    elif n.endswith("ir"):
        formas = {n[:-2] + "i", n[:-2] + "imos"}
    else:
        return set()
    return formas | set(_SINONIMOS_PASSADO.get(n, ()))


def _verbo_e_assunto(texto: str) -> tuple[str | None, set[str]]:
    """'Levar ela ao cinema' -> ('levar', {'cinema'}). Sem verbo no infinitivo no começo: (None, ...)."""
    palavras = [plano(p).strip(",.;:!?…") for p in texto.split()]
    palavras = [p for p in palavras if p]
    if not palavras:
        return None, set()
    primeiro = palavras[0]
    verbo = primeiro if re.fullmatch(r"[a-z]+(?:r|-la|-lo|-las|-los)", primeiro) else None
    assunto = {t for t in termos(" ".join(palavras[1:] if verbo else palavras)) if t not in _VAGOS and len(t) > 2}
    return verbo, assunto


def _mesmo_termo(a: str, b: str) -> bool:
    """A mesma palavra, ou a mesma raiz: as duas com 5 letras ou mais e as 5 (até 6) primeiras iguais
    ("cinema"/"cinemas", "massagem"/"massagens"). Palavra curta só igual: "para" não é "paraty"."""
    if a == b:
        return True
    if a in _VAZIAS or b in _VAZIAS:
        return False
    if a + "s" == b or b + "s" == a:
        return True                                  # "bolo" / "bolos"
    if len(a) < 5 or len(b) < 5:
        return False

    raiz = min(len(a), len(b), 6)
    return a[:raiz] == b[:raiz]


# Preposições e palavras que não são assunto (nunca contam como "o mesmo assunto").
_VAZIAS = frozenset({"para", "pela", "pelo", "pelas", "pelos", "com", "sem", "sobre", "entre", "desde", "depois",
                     "antes", "quando", "porque", "dela", "dele", "nela", "nele", "essa", "esse", "isso", "aquele",
                     "aquela", "minha", "meu", "nossa", "nosso", "mais", "muito", "muita", "ainda", "tambem",
                     "agora", "hoje", "ontem", "amanha", "semana", "dia"})


def pontuar(promessa_texto: str, texto: str) -> tuple[int, list[str]]:
    """Quanto um texto parece contar o cumprimento da promessa, com o porquê (pontos explicáveis):
    a mesma ação no passado (+45), cada palavra do assunto em comum (+25, até 50)."""
    verbo, assunto = _verbo_e_assunto(promessa_texto)
    palavras = re.findall(r"[a-z]+", plano(texto))
    pontos, motivos = 0, []
    if verbo:
        formas = formas_passado(verbo)
        achada = next((p for p in palavras if p in formas), None)
        if achada:
            pontos += 45
            motivos.append(f"a mesma ação (“{achada}”)")
    comuns = sorted({a for a in assunto for p in palavras if len(p) > 2 and _mesmo_termo(a, p)})
    if comuns:
        pontos += min(50, 25 * len(comuns))
        motivos.append("o mesmo assunto (" + ", ".join(f"“{c}”" for c in comuns[:3]) + ")")
    return pontos, motivos


# Minúsculas sem acento, rápido (para procurar palavras; a posição não precisa bater com o original).
_SEM_ACENTO = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
_RE_PALAVRA = re.compile(r"[a-z]+")
_RE_FRASE_DE_PROMESSA = re.compile(r"(?<!\w)(?:prometi|promessa|fiquei\s+de|combinei|combinamos|vou|vamos)(?!\w)")


def _baixo(s: str) -> str:
    return s.lower().translate(_SEM_ACENTO)


@dataclass
class _FraseRegistro:
    registro_id: int
    data: date | None
    texto: str
    n: str
    palavras: frozenset[str]
    radicais: frozenset[str]         # as 4 primeiras letras de cada palavra (filtro rápido do assunto)
    reservada: bool = False          # frase do ciclo num registro discreto: não é citada (20.10)


def frases_dos_registros(registros: list[dict]) -> list[_FraseRegistro]:
    """As frases dos registros, prontas para procurar cumprimentos (cada uma lida uma vez só). Pergunta
    e frase de promessa ("prometi levar ela de novo") não contam como cumprimento."""
    saida = []
    for r in registros:
        dr = _data_ou_none(r.get("data_registro"))
        for _ini, _fim, frase in frases(r.get("texto") or ""):
            if frase.rstrip().endswith("?"):
                continue
            n = _baixo(frase)
            if _RE_FRASE_DE_PROMESSA.search(n):
                continue
            palavras = frozenset(_RE_PALAVRA.findall(n))
            saida.append(_FraseRegistro(r["id"], dr, frase, n, palavras,
                                        frozenset(w[:4] for w in palavras if len(w) >= 4)))
    return saida


def _negado_antes(n: str, pos: int) -> bool:
    return bool(re.search(r"(?<!\w)(?:nao|n|nunca|nem|ainda\s+nao|sem)\s+(?:\w+\s+)?$", n[max(0, pos - 25):pos]))


def indicio(p: dict, frases_registros: list[_FraseRegistro]) -> dict | None:
    """Um registro DEPOIS da promessa que conta a mesma ação no passado ("Levei ela no cinema") e,
    se a promessa tem assunto, o mesmo assunto. Só promessas que começam com um verbo; negação e
    pergunta não contam; frase com "prometi" também não (é promessa, não cumprimento)."""
    verbo, assunto = _verbo_e_assunto(p["texto"])
    formas = formas_passado(verbo) if verbo else set()
    if not formas:
        return None
    radicais = frozenset(a[:4] for a in assunto if len(a) >= 4)
    desde = _data_ou_none(p.get("feita_em"))
    for fr in frases_registros:
        if fr.registro_id == p.get("registro_id") or not (formas & fr.palavras):
            continue
        if assunto and not (assunto & fr.palavras or radicais & fr.radicais):
            continue                                  # filtro rápido: nenhuma palavra do assunto aqui
        if desde and fr.data and fr.data < desde:
            continue
        for m in _RE_PALAVRA.finditer(fr.n):
            if m.group(0) not in formas or _negado_antes(fr.n, m.start()):
                continue
            resto = _RE_PALAVRA.findall(fr.n[m.end():m.end() + 80])
            if assunto:
                comuns = sorted({a for a in assunto for w in resto if len(w) > 2 and _mesmo_termo(a, w)})
                if not comuns:
                    continue
                motivo = ("a mesma ação (“" + m.group(0) + "”) e o mesmo assunto ("
                          + ", ".join(f"“{c}”" for c in comuns[:3]) + ")")
            elif any(w in ("ela", "dela", "nela") for w in resto[:4]):
                motivo = f"a mesma ação (“{m.group(0)}”) com ela"
            else:
                continue
            if fr.reservada:
                return {"registro_id": fr.registro_id, "data_registro": fr.data, "trecho": "", "motivo": motivo,
                        "discreto": True}
            return {"registro_id": fr.registro_id, "data_registro": fr.data, "trecho": fr.texto, "motivo": motivo}
    return None


def _frases_desde(banco: Any, desde: date | None) -> list[_FraseRegistro]:
    """As frases dos registros de `desde` em diante (os mais recentes, se forem muitos). Frase de um
    registro discreto (o ciclo) com palavras reservadas fica marcada: o indício não a cita."""
    if desde is None:
        return []
    registros = banco.consultar("SELECT id, data_registro, texto FROM registros WHERE data_registro >= ?"
                                f" ORDER BY data_registro DESC, id DESC LIMIT {MAX_REGISTROS_INDICIO}", (desde.isoformat(),))
    registros.reverse()
    lista = frases_dos_registros(registros)
    discretos = _discretos_dos_recursos(banco) if lista else set()
    for fr in lista:
        if fr.registro_id in discretos and _reservado(fr.texto):
            fr.reservada = True
    return lista


# ================================================================ a lista (tela) e os cartões do Hoje

def _situacao(p: dict, referencia: date) -> tuple[str, int | None]:
    """('sem_prazo' | 'no_prazo' | 'chegando' | 'hoje' | 'atrasada', dias até o começo do prazo)."""
    prazo = _data_ou_none(p.get("prazo"))
    if prazo is None:
        return "sem_prazo", None
    inicio, fim = periodo(prazo, p.get("precisao"))
    dias = (inicio - referencia).days
    if referencia > fim:
        return "atrasada", dias
    if inicio <= referencia <= fim:
        return ("hoje" if (p.get("precisao") or "dia") == "dia" else "chegando"), dias
    if dias <= 3:
        return "chegando", dias
    return "no_prazo", dias


def _vista(banco: Any, p: dict, referencia: date, frases_registros: list[_FraseRegistro] | None = None) -> dict:
    prazo = _data_ou_none(p.get("prazo"))
    feita = _data_ou_none(p.get("feita_em"))
    situacao, dias = _situacao(p, referencia) if p["status"] == "aberta" else (p["status"], None)
    vista = {
        "id": p["id"], "texto": p["texto"], "acao": acao(p["texto"], p.get("trecho")), "trecho": p.get("trecho"),
        "pessoa": p.get("pessoa"), "status": p["status"], "forma": p.get("forma"), "registro_id": p.get("registro_id"),
        "prazo": prazo, "precisao": p.get("precisao"), "prazo_expressao": p.get("prazo_expressao"),
        "prazo_texto": quando_prazo(prazo, p.get("precisao"), referencia) if prazo else None,
        "feita_em": feita, "feita_quando": tempo_atras((referencia - feita).days) if feita else None,
        "concluida_em": _data_ou_none(p.get("concluida_em")),
        "concluida_registro_id": p.get("concluida_registro_id"),
        "situacao": situacao, "dias": dias, "conflitos": [], "indicio": None,
    }
    if p["status"] == "aberta":
        vista["conflitos"] = _conflitos(banco, p, prazo, referencia)
        if frases_registros is not None:
            vista["indicio"] = indicio(p, frases_registros)
    return vista


def painel(banco: Any, referencia: date) -> dict:
    """Tudo o que a tela #promessas mostra: em aberto (as mais urgentes primeiro), cumpridas e
    deixadas de lado, e o momento dela (para a tela falar com cuidado)."""
    todas = _linhas(banco, "ORDER BY id")
    abertas = [p for p in todas if p["status"] == "aberta"]
    inicio = min((_data_ou_none(p["feita_em"]) or referencia for p in abertas), default=None)
    frases_registros = _frases_desde(banco, inicio)
    ordem = {"atrasada": 0, "hoje": 1, "chegando": 2, "no_prazo": 3, "sem_prazo": 4}
    vistas_abertas = [_vista(banco, p, referencia, frases_registros) for p in abertas]
    vistas_abertas.sort(key=lambda v: (ordem.get(v["situacao"], 9), v["prazo"] or date.max,
                                       v["feita_em"] or date.min, v["id"]))

    def concluidas(status: str) -> list[dict]:
        vs = [_vista(banco, p, referencia) for p in todas if p["status"] == status]
        vs.sort(key=lambda v: (v["concluida_em"] or date.min, v["id"]), reverse=True)
        return vs

    return {"abertas": vistas_abertas, "cumpridas": concluidas("cumprida"), "deixadas": concluidas("desisti"),
            "momento": momento(banco, referencia)}


def _citacao(p: dict, datas_registros: dict[int, date], referencia: date) -> str:
    rid = p.get("registro_id")
    dr = datas_registros.get(rid) if rid else None
    if rid and p.get("trecho"):
        return f"Você registrou{f' em {_dia(dr, referencia)}' if dr else ''}: “{p['trecho'].strip()}”"
    feita = _data_ou_none(p.get("feita_em"))
    return f"Você anotou esta promessa{f' em {_dia(feita, referencia)}' if feita else ''}"


def _para_quem(p: dict) -> str:
    return f" (para {p['pessoa']})" if p.get("pessoa") else ""


def _evento_do_proprio_registro(banco: Any, p: dict, prazo: date) -> bool:
    """O próprio registro já virou um EVENTO na mesma data: o Hoje do núcleo já lembra dele. (Menos
    quando o núcleo não o mostra: um acontecimento com palavras do ciclo num registro discreto.)"""
    if not p.get("registro_id"):
        return False
    linhas = banco.consultar("SELECT resumo, trecho FROM itens WHERE registro_id = ? AND tipo = 'evento'"
                             " AND status = 'ativo' AND data_evento = ?", (p["registro_id"], prazo.isoformat()))
    linhas = [l for l in linhas if _mesma_frase(l["trecho"], p.get("trecho"))]
    if linhas and p["registro_id"] in _discretos_dos_recursos(banco):
        linhas = [l for l in linhas if not _reservado(l.get("resumo") or "")]
    return bool(linhas)



def _dados_cartao(v: dict) -> dict:
    return {k: v[k] for k in ("id", "texto", "acao", "trecho", "pessoa", "prazo", "precisao", "prazo_texto",
                              "registro_id", "feita_em", "situacao", "conflitos", "indicio")}


def _apoio_que_combina(p: dict, apoios: list[dict]) -> dict | None:
    """O que ela disse que ajuda e tem a ver com a promessa ("massagem" e "fazer uma massagem nela")."""
    _verbo, assunto = _verbo_e_assunto(p["texto"])
    for a in apoios:
        termos_apoio = set(termos(" ".join(x for x in (a.get("alvo"), a.get("resumo")) if x)))
        if any(_mesmo_termo(x, y) for x in assunto for y in termos_apoio if len(x) > 3 and len(y) > 3):
            return a
    return None


def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    abertas = _linhas(banco, "WHERE status = 'aberta' ORDER BY id")
    if not abertas:
        return []
    datas_registros = _registros_datas(banco, {p["registro_id"] for p in abertas if p.get("registro_id")})
    inicio = min(_data_ou_none(p["feita_em"]) or referencia for p in abertas)
    # O Hoje abre toda hora: procura cumprimento só nos registros dos últimos DIAS_INDICIO_HOJE dias
    # (a tela Promessas, aberta por toque, procura em todos).
    frases_registros = _frases_desde(banco, max(inicio, referencia - timedelta(days=DIAS_INDICIO_HOJE)))
    dificil = momento(banco, referencia)
    apoios = _apoios(banco) if dificil else []
    saida: list[dict] = []
    for p in abertas:
        v = _vista(banco, p, referencia, frases_registros)
        citacao = _citacao(p, datas_registros, referencia)
        ids = [p["registro_id"]] if p.get("registro_id") else []
        prazo = v["prazo"]
        situacao = v["situacao"]
        if v["indicio"]:
            ind = v["indicio"]
            quando = f"Em {_dia(ind['data_registro'], referencia)} você" if ind.get("data_registro") else "Você"
            registrou = (f"{quando} registrou algo com {ind['motivo']} (um registro reservado: abra para ver)"
                         if ind.get("discreto") else f"{quando} registrou: “{ind['trecho'].strip()}” ({ind['motivo']})")
            saida.append(cartao(
                f"p{p['id']}:cumpriu:r{ind['registro_id']}:", "sugestao",
                f"Parece que você cumpriu “{v['acao']}” 💙",
                porque=(f"{registrou}. {citacao}. "
                        "Se foi isso mesmo, dá para marcar como cumprida."),

                relevancia=62, data=ind.get("data_registro"), registro_ids=list(dict.fromkeys([ind["registro_id"], *ids])),
                tipo="promessa_cumprida_talvez", dados=_dados_cartao(v)))
            continue
        if prazo is not None and situacao in ("hoje", "chegando"):
            precisao = p.get("precisao") or "dia"
            inicio_prazo, fim_prazo = periodo(prazo, precisao)
            dias = (inicio_prazo - referencia).days
            if precisao == "dia":
                fase = "hoje" if dias == 0 else "amanha" if dias == 1 else "perto" if 2 <= dias <= 3 else None
                relevancia = {"hoje": 86, "amanha": 80, "perto": 70}.get(fase or "", 0)
            elif precisao in ("semana", "mes"):
                fase = precisao if (-1 <= dias <= 3 or inicio_prazo <= referencia <= fim_prazo) else None
                relevancia = 62 if precisao == "semana" else 58
            else:
                fase = None
            if fase is None or _evento_do_proprio_registro(banco, p, prazo):
                continue
            porque = [citacao + "."]
            for c in v["conflitos"]:
                porque.append(c["texto"] + " Talvez valha combinar com ela se o dia ainda funciona.")
            if dificil and dias <= 1:
                quando = _data_ou_none(dificil.get("data"))
                porque.append(f"Em {_dia(quando, referencia) if quando else 'dias recentes'} você registrou: "
                              f"“{dificil['trecho'].strip()}”. Talvez valha conferir com ela se ainda é um bom dia.")
            saida.append(cartao(
                f"p{p['id']}:prazo:{prazo.isoformat()}:{fase}", "lembrete",
                f"{_cap(v['prazo_texto'])}: {v['acao']}{_para_quem(p)}.",
                porque=" ".join(porque), relevancia=relevancia, data=prazo if precisao == "dia" else None,
                registro_ids=list(dict.fromkeys(ids + [c["registro_id"] for c in v["conflitos"] if c.get("registro_id")])),
                tipo="promessa_prazo", dados=_dados_cartao(v)))
            continue
        # Sugestões gentis: o prazo passou há pouco, ou está aberta há tempo sem prazo.
        if situacao == "atrasada":
            _inicio, fim = periodo(prazo, p.get("precisao"))
            atraso = (referencia - fim).days
            if atraso > DIAS_ATRASO:
                continue
            chave = f"p{p['id']}:atrasada:{prazo.isoformat()}"
            texto = (f"Talvez ainda dê tempo para “{v['acao']}”{_para_quem(p)}: "
                     f"o combinado era {quando_prazo(prazo, p.get('precisao'), referencia)}.")
            relevancia = 56 if atraso <= 7 else 46
        elif situacao == "sem_prazo":
            feita = v["feita_em"]
            dias = (referencia - feita).days if feita else 0
            if dias <= DIAS_ANTIGA:
                continue
            chave = f"p{p['id']}:antiga"
            texto = (f"Há {dias} dias você prometeu “{v['acao']}”{_para_quem(p)}. Talvez seja um bom momento; "
                     "e se você mudou de ideia, tudo bem deixar de lado.")
            relevancia = 44 + min(12, (dias - DIAS_ANTIGA) // 7 * 2)
        else:
            continue
        porque = [citacao + "."]
        if dificil:
            apoio = _apoio_que_combina(p, apoios)
            quando = _data_ou_none(dificil.get("data"))
            estado = (f"Em {_dia(quando, referencia) if quando else 'dias recentes'} você registrou: "
                      f"“{dificil['trecho'].strip()}”.")
            if apoio:
                relevancia += 12
                porque.append(f"{estado} E ela já disse o que ajuda: “{apoio['trecho'].strip()}”. "
                              "Talvez cumprir isso agora seja um carinho.")
                ids = ids + [r for r in (dificil.get("registro_id"), apoio.get("registro_id")) if r]
            else:
                relevancia -= 12              # ela anda num momento difícil: não é hora de lembrar disso
        saida.append(cartao(chave, "sugestao", texto, porque=" ".join(porque), relevancia=relevancia,
                            data=prazo, registro_ids=list(dict.fromkeys(ids)), tipo="promessa_gentil",
                            dados=_dados_cartao(v)))
    saida.sort(key=lambda c: -c["relevancia"])
    return saida[:20]


# ================================================================ rotas

def _rota_listar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return painel(banco, nucleo.hoje())


def _rota_criar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if set(c) - {"texto", "prazo", "pessoa"}:
        raise ValueError(CAMPOS_INVALIDOS)
    texto = _cap(_texto(c.get("texto")))
    prazo = _data(c.get("prazo"))
    pessoa = None
    if c.get("pessoa") not in (None, ""):
        pessoa = _texto(c.get("pessoa"), 80, "“Para quem” precisa ser um texto curto.")
    _conferir_lotacao(banco)
    hoje = nucleo.hoje()
    pid = banco.inserir("promessas", {
        "texto": texto, "trecho": texto, "prazo": prazo, "precisao": "dia" if prazo else None,
        "pessoa": pessoa, "forma": "manual", "status": "aberta", "registro_id": None, "feita_em": hoje,
        "criado_em": _agora()})
    return {"id": pid, "promessa": _vista(banco, _uma(banco, pid), hoje)}


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if not c or set(c) - {"texto", "prazo", "status", "registro_id"}:
        raise ValueError(CAMPOS_INVALIDOS)
    if "registro_id" in c and c.get("status") != "cumprida":
        raise ValueError(CAMPOS_INVALIDOS)
    p = _uma(banco, pedido.id)
    hoje = nucleo.hoje()
    mudar: dict[str, Any] = {}
    if "texto" in c:
        mudar["texto"] = _cap(_texto(c.get("texto")))
        if p.get("forma") == "manual":
            mudar["trecho"] = mudar["texto"]
    if "prazo" in c:
        prazo = _data(c.get("prazo"))
        mudar.update(prazo=prazo, precisao="dia" if prazo else None, prazo_expressao=None)
    if "status" in c:
        status = c.get("status")
        if status not in STATUS:
            raise ValueError(STATUS_INVALIDO)
        mudar["status"] = status
        if status == "aberta":
            mudar.update(concluida_em=None, concluida_registro_id=None)
        else:
            quando, prova = hoje, None
            if c.get("registro_id") is not None:
                rid = _id_valido(c.get("registro_id"))
                linhas = banco.consultar("SELECT id, data_registro FROM registros WHERE id = ?", (rid,)) if rid else []
                if not linhas:
                    raise ValueError(REGISTRO_INVALIDO)
                prova, quando = rid, _data_ou_none(linhas[0]["data_registro"]) or hoje
            mudar.update(concluida_em=quando, concluida_registro_id=prova)
    with banco.transacao():
        banco.atualizar("promessas", p["id"], mudar)
        _esquecer(banco, p["id"])
    return {"promessa": _vista(banco, _uma(banco, p["id"]), hoje)}


def _rota_apagar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    pid = pedido.id
    with banco.transacao():
        banco.apagar("promessas", pid)
        _esquecer(banco, pid)

    def na_copia(copia: Any) -> None:
        copia.apagar("promessas", pid)              # NaoEncontrado: a cópia não tinha, fica como está
        copia.esquecer_alertas(f"{NOME}:p{pid}:")

    nucleo.limpar_copias(na_copia)
    return {}


def _texto_do_pedido(pedido: Any) -> str:
    c = pedido.corpo
    if set(c) - {"texto"}:
        raise ValueError("Mande só o texto para comparar.")
    return _texto(c.get("texto"), 5000, "Mande o texto para comparar.")


def _rota_parecidas(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    """Promessas em aberto parecidas com um texto (a revisão avisa antes de criar outra igual)."""
    texto = _texto_do_pedido(pedido)
    hoje = nucleo.hoje()
    saida = []
    for p in _linhas(banco, "WHERE status = 'aberta' ORDER BY id"):
        nota = semelhanca(p["texto"], texto)
        if nota >= LIMIAR_PARECIDA - 18:
            saida.append({**_vista(banco, p, hoje), "semelhanca": round(nota), "igual": nota >= LIMIAR_PARECIDA})
    saida.sort(key=lambda v: (-v["semelhanca"], v["id"]))
    return saida[:5]


def _rota_candidatas(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    """Promessas em aberto que o texto pode estar cumprindo, com os pontos e o porquê."""
    texto = _texto_do_pedido(pedido)
    hoje = nucleo.hoje()
    saida = []
    for p in _linhas(banco, "WHERE status = 'aberta' ORDER BY id"):
        pontos, motivos = pontuar(p["texto"], texto)
        if pontos >= LIMIAR_CANDIDATA:
            saida.append({**_vista(banco, p, hoje), "pontos": pontos, "motivos": motivos})
    saida.sort(key=lambda v: (-v["pontos"], v["id"]))
    return saida[:5]


def rotas() -> list:
    return [
        rota("GET", "", _rota_listar),
        rota("POST", "", _rota_criar),
        rota("PATCH", "{id}", _rota_editar),
        rota("DELETE", "{id}", _rota_apagar),
        rota("POST", "parecidas", _rota_parecidas),
        rota("POST", "candidatas", _rota_candidatas),
    ]


# ================================================================ exportar, importar, apagar registro

_COLUNAS = ("texto", "trecho", "prazo", "prazo_expressao", "precisao", "pessoa", "forma", "status", "registro_id",
            "feita_em", "criado_em", "concluida_em", "concluida_registro_id")


def exportar(banco: Any) -> dict:
    return {"promessas": [{"id": l["id"], **{c: l.get(c) for c in _COLUNAS}} for l in _linhas(banco, "ORDER BY id")]}


def _texto_import(v: Any, maximo: int) -> str | None:
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v).strip()
    return v if v and len(v) <= maximo else None


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta as promessas de um arquivo exportado sem duplicar. A que vinha de um registro que não
    entrou fica de fora (não teria de onde citar); a anotada à mão entra."""
    if not isinstance(dados, dict) or not isinstance(dados.get("promessas", []), list):
        raise ValueError("formato")
    existentes = _linhas(banco)
    novos = fora = 0
    total = len(existentes)
    for bruto in dados.get("promessas", [])[:MAX_IMPORTAR]:
        if not isinstance(bruto, dict):
            fora += 1
            continue
        texto = _texto_import(bruto.get("texto"), MAX_TEXTO)
        rid_arquivo = bruto.get("registro_id")
        rid = mapa_registros.get(rid_arquivo) if _id_valido(rid_arquivo) else None
        if texto is None or (rid_arquivo is not None and rid is None):
            fora += 1
            continue
        trecho = _texto_import(bruto.get("trecho"), 20000) or texto
        data_registro = None
        if rid is not None:
            registro = banco.consultar("SELECT texto, data_registro FROM registros WHERE id = ?", (rid,))
            if not registro or plano(trecho) not in plano(registro[0]["texto"]):
                fora += 1                             # o trecho tem de estar no registro (nunca inventar)
                continue
            data_registro = _data_ou_none(registro[0]["data_registro"])
        else:
            trecho = texto
        prazo = _data_ou_none(bruto.get("prazo"))
        feita = _data_ou_none(bruto.get("feita_em")) or data_registro or date.today()
        status = bruto.get("status") if bruto.get("status") in STATUS else "aberta"
        concluida = _data_ou_none(bruto.get("concluida_em")) if status != "aberta" else None
        prova_arquivo = bruto.get("concluida_registro_id")
        prova = mapa_registros.get(prova_arquivo) if _id_valido(prova_arquivo) and status == "cumprida" else None
        if prova_arquivo is not None and prova is None:
            concluida = None                          # a prova não veio: a data que saía dela também não
        if any(e.get("registro_id") == rid and _mesma(e["texto"], texto)
               and (rid is not None or e.get("feita_em") == feita.isoformat()) for e in existentes):
            continue
        if total >= MAX_PROMESSAS:
            fora += 1
            continue
        criado = bruto.get("criado_em")
        try:
            criado = datetime.fromisoformat(criado).isoformat(timespec="seconds") if isinstance(criado, str) else _agora()
        except ValueError:
            criado = _agora()
        linha = {
            "texto": texto, "trecho": trecho, "prazo": prazo,
            "prazo_expressao": _literal(bruto.get("prazo_expressao"), trecho, 80) if prazo else None,
            "precisao": (bruto.get("precisao") if bruto.get("precisao") in PRECISOES else "dia") if prazo else None,
            "pessoa": _literal(bruto.get("pessoa"), trecho) if rid is not None else _texto_import(bruto.get("pessoa"), 80),
            "forma": bruto.get("forma") if bruto.get("forma") in FORMAS else ("manual" if rid is None else "prometi"),
            "status": status, "registro_id": rid, "feita_em": feita, "criado_em": criado,
            "concluida_em": concluida, "concluida_registro_id": prova,
        }
        banco.inserir("promessas", linha)
        existentes.append({"texto": texto, "registro_id": rid, "feita_em": feita.isoformat()})
        total += 1
        novos += 1
    texto = ("Nenhuma promessa nova." if not novos else
             "1 promessa nova." if novos == 1 else f"{novos} promessas novas.")
    if fora:
        texto += f" {fora} ficaram de fora (sem o registro de onde vieram, ou com dados que não dá para ler)."
    return {"novos": novos, "texto": texto}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """Antes de o registro sair: as promessas que vieram dele saem junto (a fundação apaga as linhas
    com esse registro_id); o cumprimento que ele mostrava perde a ligação e a data que vieram dele (a
    sua decisão de que foi cumprida fica); e as chaves de aviso que citam o registro somem."""
    for l in banco.consultar("SELECT id FROM promessas WHERE registro_id = ?", (registro_id,)):
        _esquecer(banco, l["id"])
    for l in banco.consultar("SELECT id FROM promessas WHERE concluida_registro_id = ?", (registro_id,)):
        banco.atualizar("promessas", l["id"], {"concluida_registro_id": None, "concluida_em": None})
    for l in banco.consultar("SELECT id FROM promessas"):
        banco.esquecer_alertas(f"{NOME}:p{l['id']}:cumpriu:r{registro_id}:")
