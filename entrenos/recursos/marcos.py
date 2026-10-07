"""Marcos, linha do tempo e "Neste dia" (DESIGN.md, seção 20.6).

O que este recurso faz, sempre com o que foi registrado (nunca inventa):

- **Lê o texto** (`extrair`): "nosso primeiro encontro foi ontem", "pedi ela em namoro dia 20",
  "nossa primeira viagem juntos foi pra Paraty", "conheci a mãe dela no domingo", "dissemos eu te
  amo pela primeira vez", "nosso primeiro beijo", "fomos morar juntos", "hoje faz 1 ano do nosso
  primeiro encontro"... viram o extra "marco", com o `trecho` literal e a data do texto (ou a do
  registro, avisando). Datas sempre por regras/datas.py; quando o texto conta algo que JÁ aconteceu
  e a data sem ano caiu no futuro ("pedi ela em namoro dia 20", dito em 30/09), vale a última vez que
  aquela data passou (e a revisão diz isso). Negação ("ainda não conheci a família dela"), plano
  ("vamos fazer nossa primeira viagem em dezembro", "nosso primeiro natal juntos vai ser..."),
  pergunta, dúvida ("não sei se..."), ironia e ex ("minha ex e eu nos conhecemos...") ficam de fora;
  "acho que" vira proposta fraca.
- **Grava** o que você confirmou (`gravar_extras`) na tabela `marcos` (o contrato da seção 20.6 +
  colunas a mais). Marco igual (mesmo tipo, título e data) não duplica.
- **Marcos de tempo juntos** (calculados de `inicio_namoro`, em Ajustes): 100 dias, 6 meses, 1 ano,
  500 dias, 1.000 dias, anos seguintes (e cada mil dias). O Hoje do núcleo já avisa mesversários e
  aniversários de namoro (7 dias antes e no dia); aqui entram os marcos em DIAS (100, 500, 1.000...)
  com o mesmo aviso, e, no dia de 6 meses ou de um ano, uma sugestão de relembrar os momentos que
  você guardou (só se houver algum). Sem data em Ajustes, vale o marco de namoro registrado (e a
  tela oferece guardá-lo em Ajustes); aí os avisos de 6 meses e de cada ano também saem daqui.
- **"Neste dia"** no Hoje: um marco ou um registro de anos anteriores no mesmo dia e mês (e "há 6
  meses"), escolhido por uma pontuação explicável (uma memória de vocês, um momento feliz dela, um
  acontecimento, palavras dela...). Sensível ao momento dela: com um estado difícil registrado nos
  últimos 3 dias, a lembrança fica só como lembrança (sem convite). Briga e momento difícil não
  voltam sozinhos; o aniversário de uma perda dela vira um cuidado ("talvez hoje seja um dia mais
  sensível para ela"). Nada do que os recursos deixam fora da IA (o ciclo, a área Eu) aparece aqui.
- **Linha do tempo** (`#tempo`): marcos, marcos de tempo, eventos passados e registros importantes,
  por ano, cada ponto com o trecho e o registro de onde veio.

Apagar é apagar: o marco sai do banco, das cópias automáticas e das chaves de aviso dispensado;
apagar o registro de onde ele veio o apaga junto. Nada daqui vai para a IA (a IA só recebe itens e
registros, e os extras nunca). Tudo roda no navegador também: nada de thread, sleep, rede ou Windows.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..alertas import somar_meses, tempo_juntos
from ..banco import NaoEncontrado
from ..regras.datas import extrair_datas
from ..regras.pessoas import e_nome_dela, plano
from ..texto import data_br, frases, normalizar, semelhanca, tempo_atras
from . import RecursoIndisponivel, cartao, rota
from . import fora_da_ia as _fora_da_ia_dos_recursos

NOME = "marcos"
ROTULO = "Marcos"
TIPOS_EXTRAS = ("marco",)

TIPOS = ("primeiro_encontro", "namoro", "viagem", "familia", "eu_te_amo", "outro")
PRECISOES = ("dia", "semana", "mes", "ano")
ORIGENS = ("texto", "registro", "ajustada", "manual")

ESQUEMA = [
    """
    CREATE TABLE marcos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titulo TEXT NOT NULL,
        data TEXT NOT NULL,
        precisao TEXT NOT NULL DEFAULT 'dia' CHECK (precisao IN ('dia', 'semana', 'mes', 'ano')),
        tipo TEXT NOT NULL DEFAULT 'outro'
            CHECK (tipo IN ('primeiro_encontro', 'namoro', 'viagem', 'familia', 'eu_te_amo', 'outro')),
        descricao TEXT,
        pessoa TEXT,
        trecho TEXT,
        data_expressao TEXT,
        data_origem TEXT NOT NULL DEFAULT 'texto',
        registro_id INTEGER,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX marcos_registro ON marcos(registro_id);
    CREATE INDEX marcos_data ON marcos(data)
    """,
]
ESQUEMA_VERSAO = 1

ROTULOS_TIPO = {
    "primeiro_encontro": ("☕", "Primeiro encontro"),
    "namoro": ("💑", "Namoro"),
    "viagem": ("✈️", "Viagem"),
    "familia": ("👪", "Família"),
    "eu_te_amo": ("❤️", "Eu te amo"),
    "outro": ("⭐", "Marco"),
}
# Aniversários "de casal": lembrete 7 dias antes e no dia (como o núcleo faz com o namoro).
ANIVERSARIOS_DE_CASAL = frozenset({"primeiro_encontro", "namoro", "eu_te_amo"})

MAX_TITULO = 120
MAX_DESCRICAO = 300
MAX_MARCOS = 3000
MAX_IMPORTAR = 20000
MAX_PONTOS = 2000                 # pontos da linha do tempo numa resposta (os mais recentes)
MAX_MOMENTOS = 5                  # momentos lembrados no dia de um marco de tempo
MAX_LEMBRANCAS = 8                # "Neste dia" na tela
DIAS_AVISO = (7, 0)               # marcos de tempo e aniversários de casal: 7 dias antes e no dia
DIAS_MOMENTO = 3                  # um estado difícil dela nesses dias muda o tom
MESES_MARCANTES_ATE = 1200        # 100 anos (as contas param aí)
LIMIAR_LEMBRANCA = 15             # pontos para um registro voltar no "Neste dia" (anos atrás)
LIMIAR_SEIS_MESES = 25            # e no "há 6 meses" (só lembranças fortes e felizes)
LIMIAR_IMPORTANTE = 20            # registro importante na linha do tempo ("o essencial")
LIMIAR_TUDO = 10                  # ... e em "tudo"

TITULO_INVALIDO = f"Escreva o nome do marco (até {MAX_TITULO} caracteres)."
DESCRICAO_INVALIDA = f"A descrição pode ter até {MAX_DESCRICAO} caracteres."
DATA_INVALIDA = "A data precisa ser um dia (AAAA-MM-DD)."
DATA_FUTURA = "Um marco é algo que já aconteceu: a data não pode ser depois de hoje (ou do dia do registro)."
DATA_ANTIGA = "Essa data é antiga demais para um marco de vocês."
TIPO_INVALIDO = "O tipo do marco precisa ser um destes: " + ", ".join(TIPOS) + "."
PRECISAO_INVALIDA = "A precisão da data precisa ser dia, semana, mês ou ano."
CAMPOS_INVALIDOS = "Só dá para mudar o título, a data, o tipo e a descrição do marco."
DEMAIS = "Já há marcos demais guardados. Apague os que não fazem mais sentido."
SEM_MARCO = "Não encontrei esse marco. Talvez ele já tenha sido apagado."

_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro")


# ================================================================ ajudantes

def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _campo(obj: Any, nome: str, padrao: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nome, padrao)
    return getattr(obj, nome, padrao)


def _valor(v: Any) -> Any:
    return getattr(v, "value", v)


def _data(v: Any) -> date | None:
    """'AAAA-MM-DD' (ou date) -> date; vazio -> None; o resto -> ValueError(DATA_INVALIDA)."""
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


def _data_ou_none(v: Any) -> date | None:
    try:
        return _data(v)
    except ValueError:
        return None


def _id_valido(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _espacos(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _texto(v: Any, maximo: int, erro: str) -> str:
    if not isinstance(v, str):
        raise ValueError(erro)
    limpo = _espacos(v)
    if not limpo or len(limpo) > maximo:
        raise ValueError(erro)
    return limpo


def _encurtar(s: str, maximo: int) -> str:
    s = _espacos(s)
    if len(s) <= maximo:
        return s
    return s[:maximo].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _anos(n: int) -> str:
    return "1 ano" if n == 1 else f"{n} anos"


def quando_texto(d: date | None, precisao: str | None) -> str:
    """A data com a precisão que foi dita: '29/09/2026', 'julho de 2026', 'semana de 21/09/2026', '2024'."""
    if d is None:
        return ""
    if precisao == "ano":
        return str(d.year)
    if precisao == "mes":
        return f"{_MESES[d.month - 1]} de {d.year}"
    if precisao == "semana":
        return f"semana de {data_br(d)}"
    return data_br(d)


def _dia(d: date, referencia: date) -> str:
    return d.strftime("%d/%m") if d.year == referencia.year else data_br(d)


def _aniversario_em(d: date, ano: int) -> date:
    """O mesmo dia e mês em outro ano (29/02 cai em 28/02 nos anos que não são bissextos)."""
    try:
        return date(ano, d.month, d.day)
    except ValueError:
        return date(ano, 2, 28)


def _mesmo_dia_mes(d: date, alvo: date) -> bool:
    return _aniversario_em(d, alvo.year) == alvo


def _json_lista(v: Any) -> list:
    if isinstance(v, list):
        return v
    try:
        valor = json.loads(v or "[]")
    except (TypeError, ValueError):
        return []
    return valor if isinstance(valor, list) else []


def _marcas(n: int) -> str:
    return ", ".join("?" for _ in range(n))


# ================================================================ extração (regras)

@dataclass
class _Frase:
    ini: int          # posição da frase no texto do registro
    texto: str        # a frase literal
    n: str            # a mesma, minúscula e sem acento, do mesmo tamanho
    b: str = ""       # a mesma, minúscula COM acento, do mesmo tamanho (para achar as orações)

    def __post_init__(self) -> None:
        if not self.b:
            self.b = "".join(c.lower()[:1] or c for c in self.texto)


@dataclass
class _Achado:
    ini: int
    fim: int
    tipo: str
    titulo: str
    gatilho: str
    descricao: str | None = None
    pessoa: str | None = None
    casal: bool = True                 # o gatilho já diz que é de vocês ("nosso", "pedi", "dissemos")
    confira: str | None = None         # por que a proposta é fraca (ex.: "é ela?")


_RE_IRONIA = re.compile(r"(?<!\w)(?:sqn|so\s+que\s+nao)(?!\w)")
# "minha ex", "a ex-namorada" (mas não "a ex-chefe dela", que é outra pessoa).
_RE_EX = re.compile(r"(?<!\w)(?:(?:minha|meu|a|da|com\s+a)\s+ex(?![\w-])|ex[\s-]+(?:namorad[ao]|mulher|esposa|noiva"
                    r"|ficante)(?!\w))")
_RE_CASAL = re.compile(r"(?<!\w)(?:nosso|nossa|nossos|nossas|a\s+gente|nos|eu\s+e\s+ela|ela\s+e\s+eu|com\s+ela|juntos|junto"
                       r"|fomos|saimos|tivemos|fizemos|demos|viajamos|passamos|ficamos)(?!\w)")
_RE_NEGACAO_ANTES = re.compile(r"(?:^|\s)(?:nao|n|nunca|jamais|nem|ainda\s+nao)\s+(?:[a-z]+\s+){0,2}$")
# "se nosso primeiro encontro for bom": condição. ("quando pedi ela em namoro" é passado: vale.)
_RE_CONDICAO_ANTES = re.compile(r"(?:^|\s)(?:se|caso)\s+(?:(?:o|a|eu|ela|a\s+gente|nos)\s+)?$")
_RE_PLANO_ANTES = re.compile(
    r"(?<!\w)(?:vou|vamos|vai|vao|quero|queria|queremos|pretendo|pretendemos|planejo|planejamos|planejando"
    r"|pensando\s+em|penso\s+em|sonho|sonhamos|tomara|espero|esperamos|preciso|precisamos"
    r"|tenho\s+(?:que|q|de)|temos\s+(?:que|q|de)|devia|deveria|gostaria|imagina|imagino|antes\s+d[eo])(?!\w)")
_RE_PLANO_DEPOIS = re.compile(
    r"^\s*(?:(?:juntos|junto|com\s+ela|de\s+voces|a\s+dois)\s+)?(?:vai|vao|sera|serao|vamos|deve|devera|tem\s+que"
    r"|ta\s+marcad[oa]|esta\s+marcad[oa]|fica\s+(?:pra|para)|for|forem|fosse)(?!\w)")
_RE_DUVIDA = re.compile(r"(?<!\w)(?:sera\s+que|nao\s+sei\s+(?:se|quando)|n\s+sei\s+(?:se|quando)"
                        r"|nao\s+lembro\s+(?:se|quando|o\s+dia|a\s+data|direito)|sei\s+la|nao\s+faco\s+ideia"
                        r"|esqueci\s+quando)(?!\w)")
_RE_ACHO = re.compile(r"(?<!\w)(?:acho\s+que|acho\s+q|acredito\s+que|talvez|provavelmente|se\s+nao\s+me\s+engano"
                      r"|se\s+nao\s+me\s+falha)(?!\w)")
_RE_PASSADO = re.compile(
    r"(?<!\w)(?:foi|fomos|fui|foram|era|eramos|tivemos|teve|tive|fizemos|fez|fiz|demos|deu|dei|rolou|aconteceu"
    r"|passamos|saimos|pedi|conheci|conheceu|dissemos|falamos|disse|falei|viajamos|casamos|mudamos|adotamos"
    r"|beijamos|beijei|aceitou|pediu|comecamos|comecou|ficamos|vimos|curtimos|jantamos|almocamos|comemoramos"
    r"|trocamos|declarei|oficializamos|noivamos|assumimos|ontem|anteontem|passad[oa]|atras|ha)(?!\w)")
# Onde começa a oração do gatilho (o "vamos" de outra oração não faz dele um plano).
# (Rodam no texto em minúsculas COM acento, `_Frase.b`: "é" é verbo, não "e".)
_RE_ORACAO = re.compile(r"[,;:()]|\s(?:e|mas|por[eé]m|s[oó]\s+que|ent[aã]o|porque|pq)\s")
_RE_FIM_ORACAO = re.compile(r"[,.;:!?…()]|\s(?:e|mas|porque|pq|por[eé]m|quando|enquanto|s[oó]\s+que|ent[aã]o)\s")
_NUM = r"(?P<n>\d{1,3}|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|quinze|vinte|trinta|cem)"
_UNIDADE = r"(?P<u>anos?|mes|meses|semanas?|dias?)"
# "hoje faz 1 ano do nosso primeiro encontro" / "nosso primeiro encontro fez 1 ano hoje".
# Também "faz 2 anos hoje que..." e "amanhã faz 1 ano que..." (a âncora é o dia dito: revisão de 07/10).
_ANCORA = r"(?P<ancora>hoje|amanha|ontem)"
_RE_FAZ_ANTES = re.compile(r"(?<!\w)(?P<expr>(?:" + _ANCORA + r"\s+)?(?:faz|fazem|fez|completou|completa|completamos"
                           r"|completam|completaram|vai\s+fazer|fara)\s+" + _NUM + r"\s+" + _UNIDADE
                           + r"(?:\s+(?P<ancora2>hoje|amanha|ontem))?)\s+(?:d[aoe]s?|que|q|desde)\s+$")
_RE_FAZ_DEPOIS = re.compile(r"^\s*(?:,\s*)?(?P<expr>(?:" + _ANCORA + r"\s+)?(?:fez|faz|completou|completa|completaram"
                            r"|completam|vai\s+fazer|fara)\s+" + _NUM + r"\s+" + _UNIDADE
                            + r"(?:\s+(?P<ancora2>hoje|amanha|ontem))?)(?!\w)")
# "como se fosse ontem" é jeito de falar, não a data do marco.
_RE_FIGURA_DATA = re.compile(r"(?<!\w)como\s+se\s+(?:fosse|tivesse\s+sido)\s+$")
_RE_SEM_TEMPO_EXATO = re.compile(r"^(?:ha|faz|fazem)\s+\S+\s+(?P<u>anos?|mes|meses)(?:\s+atras)?$"
                                 r"|^\S+\s+(?P<u2>anos?|mes|meses)\s+atras$")
_RE_RELATIVO_FUTURO = re.compile(r"amanha|que\s+vem|q\s+vem|proxim|daqui|dentro\s+de|^em\s+\S+\s+(?:dias?|semanas?|mes"
                                 r"|meses|anos?)$")
_RE_DIA_SEMANA = re.compile(r"(?<!\w)(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?!\w)")
_RE_DIA_SOLTO = re.compile(r"^(?:(?:no\s+|este\s+|esse\s+)?dia\s+\S+|\S+\s*,?\s*dia\s+\S+)$")

_PARENTES = (r"(?:familia|mae|pai|pais|irma|irmao|irmas|irmaos|avo|avos|vo|tia|tio|tias|tios|prima|primo|primas"
             r"|primos|sogra|sogro|sogros|cunhada|cunhado|cunhadas|cunhados|madrasta|padrasto|filha|filho|filhos"
             r"|madrinha|padrinho|amigas|amigos|melhor\s+amiga|melhores\s+amigas)")
_SOGROS = re.compile(r"(?<!\w)(?:sogra|sogro|sogros|cunhad[oa]s?)(?!\w)")
_PETS = frozenset("gato gata gatinho gatinha cachorro cachorra cachorrinho cachorrinha cao filhote dog doguinho pet "
                  "coelho coelha coelhinho hamster passarinho calopsita peixinho tartaruga".split())
_NEGATIVOS = frozenset("briga brigas discussao discussoes dr crise termino separacao desentendimento problema "
                       "problemas ciume ciumes mentira traicao decepcao".split())
# Já têm regra própria, ou são tempo juntos (calculado de Ajustes, não um marco a guardar).
_GENERICOS_JA = frozenset("encontro date role rolezinho viagem beijo vez mes ano aniversario mesversario".split())
_NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30,
            "cem": 100}
_FIM_SOLTO = frozenset("e de do da no na em a o que pra para com ao os as".split())


def _original(fr: _Frase, ini: int, fim: int) -> str:
    return fr.texto[ini:fim]


def _e_ela(quem_n: str, quem: str, nome_dela: str | None) -> bool | None:
    """'ela' / 'a ela' / 'minha namorada' / o nome dela -> True; outro nome -> False; sem saber -> None."""
    q = quem_n.strip()
    if q in ("ela", "a ela") or re.fullmatch(r"(?:a\s+)?minha\s+(?:namorada|gata|mina|princesa|linda|amada|menina)", q):
        return True
    nome = re.sub(r"^(?:a|o)\s+", "", quem.strip())
    if nome[:1].isupper():
        if nome_dela:
            return e_nome_dela(nome, nome_dela)
        return None
    return False


def _alvo_da_familia(texto: str) -> str:
    """'a minha mãe' -> 'sua mãe' (a mesma relação, dita do lado de quem lê)."""
    trocas = {"minha": "sua", "meu": "seu", "minhas": "suas", "meus": "seus"}
    partes = texto.split()
    if len(partes) > 1 and plano(partes[0]) in ("a", "o", "as", "os") and plano(partes[1]) in trocas:
        partes = partes[1:]
    return " ".join(trocas.get(plano(p), p) for p in partes)


def _ate_oracao(fr: _Frase, ini: int, maximo_palavras: int = 9) -> int:
    """Onde termina o pedaço de frase que começa em `ini` (vírgula, ponto, "e ela", "mas"...)."""
    m = _RE_FIM_ORACAO.search(fr.b, ini)
    fim = m.start() if m else len(fr.texto)
    palavras = list(re.finditer(r"\S+", fr.texto[ini:fim]))
    if len(palavras) > maximo_palavras:
        fim = ini + palavras[maximo_palavras - 1].end()
    return fim


def _sem_fim_solto(texto: str) -> str:
    palavras = texto.split()
    while palavras and plano(palavras[-1]).strip(",.;:!?") in _FIM_SOLTO:
        palavras.pop()
    return " ".join(palavras).rstrip(",.;:!?")


def _destino(fr: _Frase, pos: int) -> str | None:
    """O destino escrito depois de "primeira viagem" ("foi pra Paraty", "para Ouro Preto")."""
    resto = fr.texto[pos:pos + 80]
    m = re.match(r"\s*(?:(?:juntos|junto|a\s+dois|de\s+casal|foi|nossa|com\s+ela)[\s,]+){0,3}"
                 r"(?:pra|para|pro|a|ao|à|em|no|na)\s+(?P<d>[A-ZÀ-Ý][\wÀ-ÿ'-]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][\wÀ-ÿ'-]+){0,3})",
                 resto)
    return m.group("d") if m else None


def _pessoa_na_posicao(pessoas: list, fr: _Frase, pos: int) -> Any:
    for p in pessoas:
        ini = _campo(p, "inicio", -1) - fr.ini
        if pos - 1 <= ini <= pos + 4:
            return p
    return None


# Cada regra: (nome, regex no texto sem acento, montar(m, fr, ctx) -> _Achado | None). Ordem: do mais
# específico para o mais genérico (um pedaço da frase vira um marco só).

def _r_encontro(m, fr, ctx):
    casal = re.search(r"nosso|a\s+gente|saimos|(?<!\w)nos(?!\w)|eu\s+e\s+ela|com\s+ela", m.group(0)) is not None
    return _Achado(m.start(), m.end(), "primeiro_encontro", "Primeiro encontro", fr.texto[m.start():m.end()], casal=casal)


def _r_conhecemos(m, fr, ctx):
    quem = m.groupdict().get("quem")
    confira = None
    if quem:
        resto = fr.n[m.end():m.end() + 25]
        if re.match(r"\s+(?:melhor|mais|direito|de\s+verdade|bem|um\s+pouco)(?!\w)", resto):
            return None                                   # "conheci ela melhor": não é o começo
        dela = _e_ela(quem, _original(fr, m.start("quem"), m.end("quem")), ctx["nome_dela"])
        if dela is False or (dela is None and not quem.startswith("a ")):
            return None                                   # "conheci Paris": só com o nome dela, sem artigo
        if dela is None:
            confira = "Confira se é ela."
    return _Achado(m.start(), m.end(), "primeiro_encontro", "Vocês se conheceram", fr.texto[m.start():m.end()],
                   confira=confira)


def _r_pedido_namoro(m, fr, ctx):
    quem = m.groupdict().get("quem")
    confira = None
    if quem is not None:
        dela = _e_ela(quem, _original(fr, m.start("quem"), m.end("quem")), ctx["nome_dela"])
        if dela is False:
            return None
        if dela is None:
            confira = "Confira se é ela."
    return _Achado(m.start(), m.end(), "namoro", "Pedido de namoro", fr.texto[m.start():m.end()], confira=confira)


def _r_comeco_namoro(m, fr, ctx):
    return _Achado(m.start(), m.end(), "namoro", "Começo do namoro", fr.texto[m.start():m.end()])


def _r_namoro_oficial(m, fr, ctx):
    return _Achado(m.start(), m.end(), "namoro", "Namoro oficial", fr.texto[m.start():m.end()])


def _r_viagem(m, fr, ctx):
    depois = fr.n[m.end():m.end() + 12]
    if re.match(r"\s+(?:dela|dele|d[aeo]\s+(?!nos\b|voces\b))", depois) and "junt" not in m.group(0):
        return None                                      # "a primeira viagem dela": não é de vocês
    casal = re.search(r"nossa|viajamos|viajou|junt", m.group(0)) is not None
    return _Achado(m.start(), m.end(), "viagem", "Primeira viagem juntos", fr.texto[m.start():m.end()],
                   descricao=_destino(fr, m.end()), casal=casal)


def _r_familia(m, fr, ctx):
    alvo_ini, alvo_fim = m.start("alvo"), m.end("alvo")
    alvo_n = fr.n[alvo_ini:alvo_fim]
    dela = alvo_n.endswith(" dela") or _SOGROS.search(alvo_n) is not None
    nome_dela = ctx["nome_dela"]
    if not dela:
        de = re.match(r"\s+d[aeo]\s+([A-ZÀ-Ý][\wÀ-ÿ]+)", fr.texto[alvo_fim:alvo_fim + 30])
        if de and nome_dela and e_nome_dela(de.group(1), nome_dela):
            alvo_fim += de.end()
            dela = True
    if not dela or re.match(r"\s+(?:melhor|mais|direito|de\s+verdade)(?!\w)", fr.n[alvo_fim:alvo_fim + 30]):
        return None
    alvo = _original(fr, alvo_ini, alvo_fim)
    alvo_p = plano(alvo)
    if not re.match(r"(?:a|o|as|os)\s", alvo_p):
        artigo = "os" if re.match(r"(?:pais|sogros|avos|tios|primos|irmaos|cunhados|amigos|filhos)\b", alvo_p) else \
            "as" if re.match(r"(?:amigas|tias|primas|irmas|cunhadas|melhores)\b", alvo_p) else \
            "o" if re.match(r"(?:pai|sogro|avo|vo|tio|primo|irmao|cunhado|padrasto|padrinho|filho)\b", alvo_p) else "a"
        alvo = f"{artigo} {alvo}"
    pessoa = alvo.split(" ", 1)[1] if " " in alvo else alvo
    p = _pessoa_na_posicao(ctx["pessoas"], fr, alvo_ini)
    nome = _campo(p, "nome") if p is not None else None
    if not nome:
        # "conheci a mãe dela, a Sandra": o nome logo depois, se o texto disse.
        n2 = re.match(r",\s*(?:a\s+|o\s+|dona\s+|seu\s+)?([A-ZÀ-Ý][\wÀ-ÿ]+)", fr.texto[alvo_fim:alvo_fim + 30])
        if n2 and not _RE_DIA_SEMANA.match(plano(n2.group(1))):
            nome = n2.group(1)
    titulo = f"Você conheceu {alvo}" + (f" ({nome})" if nome and plano(nome) not in plano(alvo) else "")
    tipo = "outro" if re.search(r"amig", alvo_p) else "familia"
    return _Achado(m.start(), m.end(), tipo, titulo, fr.texto[m.start():m.end()], pessoa=pessoa)


def _r_ela_conheceu(m, fr, ctx):
    alvo = _original(fr, m.start("alvo"), m.end("alvo"))
    return _Achado(m.start(), m.end(), "familia", f"Ela conheceu {_alvo_da_familia(alvo)}", fr.texto[m.start():m.end()])


def _r_apresentado(m, fr, ctx):
    alvo = _original(fr, m.start("alvo"), m.end("alvo"))
    artigo = "os" if plano(alvo).startswith("pais") else "o" if plano(alvo).startswith("pai ") else "a"
    return _Achado(m.start(), m.end(), "familia", f"Você conheceu {artigo} {alvo}", fr.texto[m.start():m.end()],
                   pessoa=alvo)


def _r_literal(tipo: str) -> Callable:
    def montar(m, fr, ctx):
        titulo = _cap(_sem_fim_solto(_espacos(fr.texto[m.start():m.end()])))
        return _Achado(m.start(), m.end(), tipo, titulo, fr.texto[m.start():m.end()])
    return montar


def _r_eu_te_amo(m, fr, ctx):
    plural = re.match(r"(?:dissemos|falamos|trocamos)", m.group(0)) is not None
    if not (plural or "primeir" in fr.n):
        return None                                   # "ela disse que me ama" num dia qualquer não é marco
    return _Achado(m.start(), m.end(), "eu_te_amo", "Primeiro “eu te amo”", fr.texto[m.start():m.end()])


def _r_beijo(m, fr, ctx):
    depois = fr.n[m.end():m.end() + 10]
    casal = re.search(r"nosso|demos|dei|beijamos|beijei|com\s+ela", m.group(0)) is not None
    if re.match(r"\s+(?:dela|dele)(?!\w)", depois) and not casal:
        return None                                  # "o primeiro beijo dela foi aos 15": não é de vocês
    return _Achado(m.start(), m.end(), "outro", "Primeiro beijo", fr.texto[m.start():m.end()], casal=casal)


def _r_pedido_casamento(m, fr, ctx):
    quem = m.groupdict().get("quem")
    if quem is not None and _e_ela(quem, _original(fr, m.start("quem"), m.end("quem")), ctx["nome_dela"]) is False:
        return None
    titulo = "Noivado" if re.search(r"noiv", m.group(0)) else "Pedido de casamento"
    return _Achado(m.start(), m.end(), "outro", titulo, fr.texto[m.start():m.end()])


def _r_casamento(m, fr, ctx):
    return _Achado(m.start(), m.end(), "outro", "Casamento", fr.texto[m.start():m.end()])


def _r_morar(m, fr, ctx):
    return _Achado(m.start(), m.end(), "outro", "Morar juntos", fr.texto[m.start():m.end()])


def _r_adocao(m, fr, ctx):
    if plano(m.group("pet")) not in _PETS:
        return None
    obj = _original(fr, m.start("obj"), m.end("obj"))
    return _Achado(m.start(), m.end(), "outro", f"Adoção: {obj}", fr.texto[m.start():m.end()])


def _r_nosso_primeiro(m, fr, ctx):
    objeto = m.group("obj").split()[0]
    if objeto in _GENERICOS_JA or objeto in _NEGATIVOS:
        return None
    titulo = _sem_fim_solto(_espacos(fr.texto[m.start("ord"):m.end()]))
    if len(titulo.split()) < 2:
        return None
    return _Achado(m.start(), m.end(), "outro", _cap(titulo), fr.texto[m.start():m.end()])


def _r_primeira_vez(m, fr, ctx):
    fim = _ate_oracao(fr, m.end())
    resto = fr.n[m.end():fim].split()
    if not resto or re.match(r"brig|discut|termin|separ|chor|grit", resto[0]):
        return None                                  # "primeira vez que brigamos" não é para comemorar
    titulo = _sem_fim_solto(_espacos(fr.texto[m.start("pv"):fim]))
    if len(titulo.split()) < 5:
        return None
    return _Achado(m.start(), fim, "outro", _cap(titulo), fr.texto[m.start():fim])


_REGRAS: tuple[tuple[str, re.Pattern, Callable], ...] = (
    ("eu_te_amo", re.compile(
        r"(?<!\w)(?:(?:dissemos|falamos|trocamos|eu\s+disse|disse|falei|declarei|ela\s+(?:me\s+)?(?:disse|falou|declarou)"
        r"|me\s+disse|me\s+falou)\s+(?:(?:o|um|pela\s+primeira\s+vez|pra\s+ela|para\s+ela|nosso)\s+)*(?:primeiro\s+)?[\"“']?"
        r"(?:eu\s+te\s+amo|te\s+amo|que\s+me\s+ama(?:va)?|que\s+(?:a\s+|te\s+)?amava|que\s+amo\s+ela|que\s+a\s+amo)"
        r"|(?:o\s+)?(?:nosso\s+)?primeiro\s+[\"“']?(?:eu\s+)?te\s+amo)(?!\w)"), _r_eu_te_amo),
    ("pedido_casamento", re.compile(
        r"(?<!\w)(?:pedi\s+(?P<quem>(?:[a-z]+\s+){0,2}?[a-z]+)\s+em\s+casamento|pedi\s+a\s+mao\s+dela"
        r"|ficamos\s+noivos|(?:nos\s+)?noivamos|ela\s+aceitou\s+(?:se\s+)?casar\s+comigo"
        r"|ela\s+disse\s+sim\s+(?:pro|para\s+o|ao)\s+(?:meu\s+)?pedido(?:\s+de\s+casamento)?)(?!\w)"), _r_pedido_casamento),
    ("pedido_namoro", re.compile(
        r"(?<!\w)(?:pedi\s+(?P<quem>(?:[a-z]+\s+){0,2}?[a-z]+)\s+(?:em\s+namoro|pra\s+namorar|para\s+namorar)"
        r"|(?:ela\s+)?me\s+pediu\s+em\s+namoro|(?:ela\s+)?aceitou\s+(?:namorar\s+comigo|(?:o\s+)?meu\s+pedido(?:\s+de\s+namoro)?"
        r"|ser\s+minha\s+namorada))(?!\w)"), _r_pedido_namoro),
    ("comeco_namoro", re.compile(
        r"(?<!\w)(?:comecamos|(?:a\s+gente|nos)\s+comecou)\s+a\s+namorar(?!\w)"), _r_comeco_namoro),
    ("namoro_oficial", re.compile(
        r"(?<!\w)(?:oficializamos(?:\s+o\s+namoro)?|(?:viramos|agora\s+somos)\s+namorados|namoro\s+(?:oficial|oficializado)"
        r"|(?:a\s+gente\s+)?assumimos\s+(?:o\s+)?namoro|estamos\s+namorando\s+oficialmente)(?!\w)"), _r_namoro_oficial),
    ("casamento", re.compile(
        r"(?<!\w)(?:nos\s+casamos|casamos|a\s+gente\s+(?:se\s+)?casou|nosso\s+casamento\s+foi"
        r"|dissemos\s+sim\s+no\s+altar)(?!\w)(?!\s+(?:as|os)\s)"), _r_casamento),
    ("morar", re.compile(
        r"(?<!\w)(?:(?:fomos|viemos)\s+morar\s+junt[oa]s?|(?:comecamos|a\s+gente\s+comecou)\s+a\s+morar\s+junt[oa]s?"
        r"|nos\s+mudamos\s+junt[oa]s?|mudamos\s+(?:pra|para)\s+(?:a\s+)?nossa\s+(?:casa|casinha|ap|apartamento)"
        r"|(?:alugamos|compramos)\s+(?:o\s+|a\s+)?(?:nosso|nossa)\s+(?:ap|apto|apartamento|casa|casinha|cantinho))(?!\w)"),
     _r_morar),
    ("adocao", re.compile(r"(?<!\w)adotamos\s+(?P<obj>(?:um|uma|o|a|nosso|nossa)\s+(?P<pet>[a-z]+))(?!\w)"), _r_adocao),
    ("viagem", re.compile(
        r"(?<!\w)(?:(?:a\s+)?(?:nossa\s+)?primeira\s+viagem(?:\s+(?:juntos|junto|a\s+dois|de\s+casal))?"
        r"|viajamos\s+junt[oa]s\s+pela\s+primeira\s+vez|primeira\s+vez\s+que\s+(?:a\s+gente\s+viajou|viajamos)"
        r"(?:\s+junt[oa]s)?)(?!\w)"), _r_viagem),
    ("beijo", re.compile(
        r"(?<!\w)(?:(?:o\s+)?(?:nosso\s+)?primeiro\s+beijo(?:\s+com\s+ela)?|nos\s+beijamos\s+pela\s+primeira\s+vez"
        r"|beijei\s+ela\s+pela\s+primeira\s+vez|(?:demos|dei)\s+(?:o\s+)?(?:nosso\s+)?primeiro\s+beijo)(?!\w)"), _r_beijo),
    ("familia", re.compile(
        r"(?<!\w)conheci\s+(?P<alvo>(?:(?:a|o|as|os)\s+)?" + _PARENTES + r"(?:\s+dela)?)(?!\w)"), _r_familia),
    ("ela_conheceu", re.compile(
        r"(?<!\w)ela\s+conheceu\s+(?P<alvo>(?:(?:a|o|as|os)\s+)?(?:minha|meu|meus|minhas)\s+"
        r"(?:familia|mae|pai|pais|irma|irmao|irmas|irmaos|avo|avos|tia|tio|filha|filho|filhos))(?!\w)"), _r_ela_conheceu),
    ("apresentado", re.compile(
        r"(?<!\w)fui\s+apresentad[oa]\s+(?:a|aos|as|ao)\s+(?P<alvo>(?:familia|pais|mae|pai)\s+dela)(?!\w)"), _r_apresentado),
    ("casa_dela", re.compile(
        r"(?<!\w)(?:(?:a\s+)?primeira\s+vez\s+(?:na|em)\s+casa\s+d(?:os|as|a|o)\s+(?:pais|mae|pai|familia|avos?|sogros|sogra)"
        r"\s+dela|(?:o\s+)?primeiro\s+(?:almoco|jantar|natal|domingo)\s+(?:com|na\s+casa\s+d[aoe]s?)\s+(?:a\s+)?"
        r"(?:familia|pais|mae|pai|sogros)\s+dela)(?!\w)"), _r_literal("familia")),
    ("encontro", re.compile(
        r"(?<!\w)(?:(?:o\s+)?nosso\s+primeiro\s+(?:encontro|date|role|rolezinho)|o\s+primeiro\s+(?:encontro|date)"
        r"|primeiro\s+(?:encontro|date)\s+(?:com\s+ela|nosso)"
        r"|primeira\s+vez\s+(?:que|q)\s+(?:a\s+gente|nos|eu\s+e\s+ela)\s+(?:saiu|saimos|se\s+viu|nos\s+vimos|se\s+encontrou"
        r"|nos\s+encontramos)|(?:a\s+gente\s+)?saimos\s+pela\s+primeira\s+vez)(?!\w)"), _r_encontro),
    ("conhecemos", re.compile(
        r"(?<!\w)(?:(?:a\s+gente|nos)\s+se\s+conheceu|nos\s+conhecemos|(?<!se\s)conheci\s+(?P<quem>ela|a\s+[a-z]+|[a-z]+))(?!\w)"),
     _r_conhecemos),
    ("nosso_primeiro", re.compile(
        r"(?<!\w)(?:(?:o|a)\s+)?(?:nosso|nossa)\s+(?P<ord>primeir[oa])\s+(?P<obj>[a-z]+(?:\s+(?:de|do|da|no|na)\s+[a-z]+)?)"
        r"(?:\s+junt[oa]s)?(?!\w)"), _r_nosso_primeiro),
    ("primeira_vez", re.compile(
        r"(?<!\w)(?:a\s+)?(?P<pv>primeira\s+vez\s+(?:que|q)\s+(?:a\s+gente|nos|eu\s+e\s+ela))(?!\w)"), _r_primeira_vez),
)
_MOTIVO = "Você escreveu “{g}”."


def _inicio_oracao(fr: _Frase, pos: int) -> int:
    inicio = 0
    for m in _RE_ORACAO.finditer(fr.b, 0, pos):
        inicio = m.end()
    return inicio


def _faz_tempo(fr: _Frase, a: _Achado) -> re.Match | None:
    """"Hoje faz 1 ano do nosso primeiro encontro" / "nosso primeiro encontro fez 1 ano hoje"."""
    antes = _RE_FAZ_ANTES.search(fr.n[max(0, a.ini - 60):a.ini])
    if antes:
        return antes
    return _RE_FAZ_DEPOIS.match(fr.n[a.fim:a.fim + 40])


def _data_do_marco(fr: _Frase, achado: _Achado, datas: list, referencia: date) -> tuple[dict | None, bool]:
    """({data, precisao, expressao, origem, nota}, é plano?) do marco. O Python calcula (regras/datas.py).
    `é plano` = a data é de algo que ainda vai acontecer (o marco fica de fora)."""
    faz = _faz_tempo(fr, achado)
    if faz:
        n_txt, unidade = faz.group("n"), faz.group("u")
        n = int(n_txt) if n_txt.isdigit() else _NUMEROS.get(n_txt)
        ancora_txt = faz.groupdict().get("ancora") or faz.groupdict().get("ancora2")
        # A âncora: "amanhã faz 1 ano" conta de amanhã; "ontem fez 2 anos", de ontem; senão, de hoje.
        ancora = referencia + timedelta(days={"amanha": 1, "ontem": -1}.get(ancora_txt or "", 0))
        calculada = [d for d in extrair_datas(f"há {n} {unidade}", ancora) if d.data] if n else []
        if calculada:
            d = calculada[0].data
            hoje_dito = bool(ancora_txt) or re.search(r"(?<!\w)hoje(?!\w)", fr.n) is not None
            precisao = "dia"
            if not hoje_dito and unidade.startswith("ano"):
                precisao, d = "ano", date(d.year, 1, 1)
            elif not hoje_dito and unidade.startswith("mes"):
                precisao, d = "mes", d.replace(day=1)
            # A expressão literal, achada de novo no texto original (as posições de `faz` são de um pedaço).
            expr_n = faz.group("expr")
            pos = fr.n.find(expr_n)
            expr = _espacos(fr.texto[pos:pos + len(expr_n)]) if pos >= 0 else None
            return {"data": d, "precisao": precisao, "expressao": expr, "origem": "texto", "nota": None}, False
    candidatas = []
    for d in datas:
        a = _campo(d, "inicio", -1) - fr.ini
        b = _campo(d, "fim", -1) - fr.ini
        if a < 0 or b > len(fr.texto) or _campo(d, "data") is None:
            continue
        if _RE_FIGURA_DATA.search(fr.n[max(0, a - 30):a]):
            continue                                # "lembro como se fosse ontem": não é a data

        # Data no futuro de outra oração ("foi ótimo e vamos repetir sábado") é de outra coisa.
        if _campo(d, "data") > referencia and _RE_FIM_ORACAO.search(fr.b[min(b, achado.fim):max(a, achado.ini)]):
            continue
        candidatas.append((a, b, d))
    if not candidatas:
        return {"data": referencia, "precisao": "dia", "expressao": None, "origem": "registro",
                "nota": "O texto não diz quando: usei o dia do registro. Se foi em outro dia, troque."}, False
    # A data mais perto do gatilho (depois dele vale um pouco mais: "foi ontem").
    a, b, escolhida = min(candidatas, key=lambda c: min(abs(c[0] - achado.fim), abs(achado.ini - c[1]))
                          + (0 if c[0] >= achado.ini else 3))
    d = _campo(escolhida, "data")
    precisao = _valor(_campo(escolhida, "precisao")) or "dia"
    if precisao not in PRECISOES:
        precisao = "dia"
    expr = fr.texto[a:b]
    expr_n = plano(expr).strip()
    sem_exato = _RE_SEM_TEMPO_EXATO.match(expr_n)
    if sem_exato:                                   # "há 2 anos": o ano, não um dia que ninguém disse
        u = sem_exato.group("u") or sem_exato.group("u2") or ""
        if u.startswith("ano"):
            precisao, d = "ano", date(d.year, 1, 1)
        else:
            precisao, d = "mes", d.replace(day=1)
    nota = None
    origem = "texto"
    if d > referencia:
        # O marco já aconteceu; a data caiu no futuro. Data relativa ("amanhã", "sábado que vem") ou
        # frase sem nada no passado: é um plano. Data de calendário sem ano: a última vez que passou.
        if _RE_RELATIVO_FUTURO.search(expr_n) or not _RE_PASSADO.search(fr.n):
            return None, True
        if _RE_DIA_SEMANA.search(expr_n) and not re.search(r"\d", expr_n):
            novo = d - timedelta(days=7)
        elif _RE_DIA_SOLTO.match(expr_n):
            novo = somar_meses(d, -1)
        else:
            novo = somar_meses(d, -12)
        while novo > referencia:
            novo = somar_meses(novo, -12)
        nota = (f"Pelo texto, isso já aconteceu: usei {quando_texto(novo, precisao)}, a última vez que "
                f"“{expr}” passou. Se não foi, troque a data.")
        d, origem = novo, "ajustada"
    return {"data": d, "precisao": precisao, "expressao": expr, "origem": origem, "nota": nota}, False


def _marcos_da_frase(fr: _Frase, datas: list, referencia: date, ctx: dict) -> list[dict]:
    if fr.texto.rstrip().endswith("?") or _RE_IRONIA.search(fr.n) or _RE_EX.search(fr.n):
        return []
    achados: list[_Achado] = []
    for _nome, rx, montar in _REGRAS:
        for m in rx.finditer(fr.n):
            if any(m.start() < a.fim and a.ini < m.end() for a in achados):
                continue
            achado = montar(m, fr, ctx)
            if achado is not None:
                achados.append(achado)
    saida = []
    duvida = _RE_DUVIDA.search(fr.n)
    for a in sorted(achados, key=lambda x: x.ini):
        antes = fr.n[max(0, a.ini - 40):a.ini]
        # Um gatilho no passado ("pedi", "conheci", "dissemos") já é o que aconteceu: o "vai" de antes é de
        # outra coisa ("ela vai lembrar do dia em que pedi..."). Um gatilho que é só nome ("nossa primeira
        # viagem") com plano logo antes ("vamos fazer nossa primeira viagem") é plano.
        no_passado = _RE_PASSADO.search(plano(a.gatilho)) is not None
        ultimas = " ".join(fr.n[_inicio_oracao(fr, a.ini):a.ini].split()[-6:])
        if _RE_NEGACAO_ANTES.search(antes) or _RE_CONDICAO_ANTES.search(antes) \
                or (not no_passado and _RE_PLANO_ANTES.search(ultimas)):
            continue
        if _RE_PLANO_DEPOIS.match(fr.n[a.fim:]) or (duvida and duvida.start() < a.ini):
            continue
        if not a.casal and not _RE_CASAL.search(fr.n):
            continue                                  # "o primeiro encontro" sem nada que diga que é de vocês
        quando, e_plano = _data_do_marco(fr, a, datas, referencia)
        if e_plano or quando is None:
            continue
        fraca = bool(_RE_ACHO.search(fr.n[:a.ini])) or a.confira is not None
        motivo = _MOTIVO.format(g=_espacos(a.gatilho))
        if _RE_ACHO.search(fr.n[:a.ini]):
            motivo += " Com “acho que”: confira se foi isso mesmo."
        if a.confira:
            motivo += " " + a.confira
        saida.append({
            "tipo": "marco",
            "marco_tipo": a.tipo,
            "titulo": a.titulo,
            "resumo": f"{a.titulo} ({quando_texto(quando['data'], quando['precisao'])})",
            "trecho": fr.texto,
            "data": quando["data"],
            "precisao": quando["precisao"],
            "data_expressao": quando["expressao"],
            "data_origem": quando["origem"],
            "nota_data": quando["nota"],
            "descricao": a.descricao,
            "pessoa": a.pessoa,
            "motivo": motivo,
            "forca": "fraca" if fraca else "forte",
        })
    return saida


# O mesmo acontecimento dito de dois jeitos no mesmo texto ("Ficamos noivos! Pedi ela em casamento").
_MESMO_ACONTECIMENTO = {"noivado": "casamento_pedido", "pedido de casamento": "casamento_pedido",
                        "pedido de namoro": "namoro", "comeco do namoro": "namoro", "namoro oficial": "namoro"}


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Marcos do texto, cada um com a frase literal e a data calculada pelo Python."""
    ctx = {"nome_dela": _campo(conhecimento, "nome_dela"), "pessoas": list(pessoas or [])}
    saida: list[dict] = []
    vistos: set[tuple] = set()
    for ini, _fim, frase in frases(texto):
        fr = _Frase(ini, frase, plano(frase))
        for e in _marcos_da_frase(fr, datas or [], referencia, ctx):
            titulo = normalizar(e["titulo"])
            chave = (e["marco_tipo"], _MESMO_ACONTECIMENTO.get(titulo, titulo), e["data"])
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append(e)
    return saida


# ================================================================ gravar o que foi confirmado

def _literal(v: Any, trecho: str | None, maximo: int = 120) -> str | None:
    """Um pedaço que tem de estar escrito no trecho (nunca inventar); senão None."""
    if not isinstance(v, str) or not v.strip() or len(v) > maximo or not trecho:
        return None
    return v.strip() if plano(v.strip()) in plano(trecho) else None


def _tipo(v: Any, padrao: str | None = "outro") -> str:
    if v in TIPOS:
        return v
    if padrao is None:
        raise ValueError(TIPO_INVALIDO)
    return padrao


def _data_valida(v: Any, limite: date) -> date:
    d = _data(v)
    if d is None:
        raise ValueError(DATA_INVALIDA)
    if d > limite:
        raise ValueError(DATA_FUTURA)
    if d.year < 1900:
        raise ValueError(DATA_ANTIGA)
    return d


def _descricao(v: Any) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if not isinstance(v, str) or len(_espacos(v)) > MAX_DESCRICAO:
        raise ValueError(DESCRICAO_INVALIDA)
    return _espacos(v)


def _titulo_do_extra(e: dict) -> str:
    for chave in ("titulo", "resumo"):
        v = e.get(chave)
        if isinstance(v, str) and v.strip():
            v = _espacos(v)
            if chave == "resumo" and v == _espacos(e.get("trecho") or ""):
                v = _encurtar(v, MAX_TITULO)        # sem título: a fundação pôs o trecho no resumo
            return _texto(v, MAX_TITULO, TITULO_INVALIDO)
    raise ValueError(TITULO_INVALIDO)


def _todos(banco: Any) -> list[dict]:
    return banco.consultar("SELECT * FROM marcos ORDER BY data, id")


def _um(banco: Any, mid: int) -> dict:
    linhas = banco.consultar("SELECT * FROM marcos WHERE id = ?", (mid,))
    if not linhas:
        raise NaoEncontrado(SEM_MARCO)
    return linhas[0]


def _igual(a: dict, tipo: str, titulo: str, data: date) -> bool:
    return (a["tipo"] == tipo and a["data"] == data.isoformat()
            and (normalizar(a["titulo"]) == normalizar(titulo) or semelhanca(a["titulo"], titulo) >= 90))


def _conferir_lotacao(banco: Any, novos: int = 1) -> None:
    if banco.consultar("SELECT count(*) AS n FROM marcos")[0]["n"] + novos > MAX_MARCOS:
        raise ValueError(DEMAIS)


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava os marcos confirmados (dentro da transação do registro). ValueError = 400, nada gravado."""
    existentes = _todos(banco)
    for e in extras:
        if e.get("tipo") != "marco":
            continue
        trecho = e["trecho"]
        tipo = _tipo(e.get("marco_tipo"))
        titulo = _titulo_do_extra(e)
        d = _data_valida(e.get("data") or referencia, referencia)
        precisao = e.get("precisao") if e.get("precisao") in PRECISOES else "dia"
        origem = e.get("data_origem") if e.get("data_origem") in ORIGENS else "texto"
        if any(_igual(a, tipo, titulo, d) for a in existentes):
            continue                                   # o mesmo marco contado de novo: não duplica
        _conferir_lotacao(banco)
        linha = {
            "titulo": titulo, "data": d, "precisao": precisao, "tipo": tipo, "descricao": _descricao(e.get("descricao")),
            "pessoa": _literal(e.get("pessoa"), trecho), "trecho": trecho,
            "data_expressao": _literal(e.get("data_expressao"), trecho, 80), "data_origem": origem,
            "registro_id": registro_id, "criado_em": _agora(),
        }
        novo = banco.inserir("marcos", linha)
        existentes.append({**linha, "id": novo, "data": d.isoformat()})


def _esquecer_marco(banco: Any, mid: int) -> None:
    """As chaves de aviso dispensado deste marco (podem revelar a data de algo apagado)."""
    banco.esquecer_alertas(f"{NOME}:m{mid}:")


# ================================================================ o que se lê do banco

class _Sensiveis:
    """Os registros que nunca aparecem aqui, lidos uma vez só e só quando preciso (o gancho
    `fora_da_ia` de cada recurso roda a cada leitura)."""

    def __init__(self, banco: Any):
        self._banco = banco
        self._valor: set[int] | None = None

    def __call__(self) -> set[int]:
        if self._valor is None:
            self._valor = _sensiveis(self._banco)
        return self._valor


def _sensiveis(banco: Any) -> set[int]:
    """Registros que nunca aparecem aqui sozinhos: os que os recursos deixam fora da IA (o ciclo, a
    área Eu). Sem saber com segurança (um recurso não abriu), todos os 🔒 ficam de fora."""
    try:
        return set(_fora_da_ia_dos_recursos(banco))
    except RecursoIndisponivel:
        return set(banco.registros_privados())


# Palavras que o Hoje nunca mostra (seção 20.8) e que, numa lembrança, seriam do ciclo.
_RE_CICLO = re.compile(r"menstru|(?<!\w)tpm(?!\w)|colica|absorvente|ovula|fertil|ciclo\s+menstrual|anticoncepcion"
                       r"|(?<!\w)pilula(?!\w)|(?<!\w)regras?\s+dela")
_RE_LUTO = re.compile(r"(?<!\w)(?:faleceu|falecimento|faleceram|velorio|enterro|sepultamento|luto|missa\s+de\s+setimo"
                      r"|morreu(?!\s+de\s+(?:rir|vergonha|medo|fome|sono|calor|frio|saudade|cansaco|tedio|inveja|amores"
                      r"|ciume))|perdeu\s+(?:a|o|os|as)\s+(?:avo|avos|vo|mae|pai|tia|tio|irma|irmao|prima|primo|amiga|amigo"
                      r"|gato|gata|cachorro|cachorra|cachorrinho|cachorrinha|madrinha|padrinho|bebe|pet))(?!\w)")
_RE_DELA = re.compile(r"(?<!\w)(?:dela|ela\s+perdeu|sogr[oa]s?|cunhad[oa]s?)(?!\w)")
_RE_BRIGA = re.compile(r"(?<!\w)(?:brig\w*|discut\w*|discussao|terminamos|termino|demos\s+um\s+tempo|dar\s+um\s+tempo"
                       r"|separ\w*|traicao|traiu|ciume\w*|magoe\w*|magoad\w*|grosseri\w*|gritei|gritou)(?!\w)")
_CAT_NEGATIVAS = frozenset({"Preocupações", "Coisas que incomodam", "Medos"})
_RE_ALEGRIA = re.compile(r"(?<!\w)(?:emoc\w*|alegria|feliz|felizes|amou|adorou|maravilh\w*|incrivel|melhor\s+dia"
                         r"|perfeit[oa]|rimos|riu\s+muito|divertid\w*|especial|inesquecivel|lind[oa])(?!\w)")
_RE_NAO_ANTES = re.compile(r"(?<!\w)(?:nao|n|nem|nada|nunca)\s+(?:\w+\s+){0,2}$")


def _alegre(trecho: str) -> bool:
    """O trecho fala de alegria (sem negar: "não foi especial" não conta)."""
    n = plano(trecho)
    return any(not _RE_NAO_ANTES.search(n[max(0, m.start() - 20):m.start()]) for m in _RE_ALEGRIA.finditer(n))


def _resolver(sensiveis: Any) -> set[int]:
    if sensiveis is None:
        return set()
    return sensiveis() if callable(sensiveis) else sensiveis


def _pode_citar(trecho: str | None, registro_id: int | None, sensiveis: Any) -> bool:
    """Um trecho pode aparecer no Hoje e na linha do tempo? Nunca com palavras do ciclo, nem de um
    registro que os recursos deixam fora da IA (o marco continua; só o trecho não é citado)."""
    if not trecho or _RE_CICLO.search(plano(trecho)):
        return False
    return not (registro_id and sensiveis is not None and registro_id in _resolver(sensiveis))


def _dela(linha: dict) -> bool:
    return linha.get("pessoa_id") is None and not (linha.get("pessoa") or "").strip()


def momento(banco: Any, referencia: date, sensiveis: Any = None) -> dict | None:
    """O estado mais recente dela nos últimos dias, se for difícil: {resumo, trecho, registro_id,
    data}. Se o mais recente é bom (ou não há), None. O que é do ciclo (ou de um registro que os
    recursos deixam fora da IA) não entra: ele nunca explica humor (seção 20.8)."""
    linhas = banco.consultar(
        "SELECT i.resumo, i.trecho, i.polaridade, i.registro_id, i.pessoa, i.pessoa_id, r.data_registro"
        " FROM itens i JOIN registros r ON r.id = i.registro_id"
        " WHERE i.tipo = 'estado' AND i.status = 'ativo' AND r.data_registro BETWEEN ? AND ?"
        " ORDER BY r.data_registro DESC, i.id DESC",
        ((referencia - timedelta(days=DIAS_MOMENTO)).isoformat(), referencia.isoformat()))
    for l in linhas:
        if not _dela(l) or not _pode_citar(l["trecho"], l["registro_id"], sensiveis):
            continue
        if (l["polaridade"] or 0) >= 0:
            return None
        return {"resumo": l["resumo"], "trecho": l["trecho"], "registro_id": l["registro_id"],
                "data": _data_ou_none(l["data_registro"])}
    return None


def _apoio(banco: Any, sensiveis: set[int]) -> dict | None:
    """O que ela disse que ajuda (APOIO +1, dela, ativo), o mais recente."""
    for l in banco.consultar(
            "SELECT i.alvo, i.resumo, i.trecho, i.registro_id, i.pessoa, i.pessoa_id FROM itens i"
            " JOIN registros r ON r.id = i.registro_id"
            " WHERE i.tipo = 'apoio' AND i.status = 'ativo' AND COALESCE(i.polaridade, 1) > 0"
            " ORDER BY r.data_registro DESC, i.id DESC LIMIT 30"):
        if _dela(l) and l["registro_id"] not in sensiveis and not _RE_CICLO.search(plano(l["trecho"] or "")):
            return l
    return None


def _itens_de(banco: Any, ids: Any) -> dict[int, list[dict]]:
    ids = sorted(set(ids))
    saida: dict[int, list[dict]] = {i: [] for i in ids}
    for parte in range(0, len(ids), 500):
        lote = ids[parte:parte + 500]
        for l in banco.consultar(
                "SELECT id, registro_id, tipo, resumo, trecho, certeza, categorias, importancia, data_evento, precisao,"
                " recorrente, pessoa, pessoa_id, polaridade FROM itens WHERE status = 'ativo'"
                f" AND registro_id IN ({_marcas(len(lote))}) ORDER BY id", tuple(lote)):
            l["categorias"] = _json_lista(l["categorias"])
            saida.setdefault(l["registro_id"], []).append(l)
    return saida


@dataclass
class Avaliacao:
    """Por que um registro é uma boa lembrança (pontos explicáveis) e o tom dele."""

    pontos: int = 0
    motivos: list[str] = field(default_factory=list)
    tom: str = "neutro"               # feliz | neutro | dificil | luto
    frase: str | None = None          # a frase literal que representa o registro


_PESOS = {"memoria": (15, "uma memória de vocês"), "feliz": (12, "um momento feliz dela"),
          "evento": (10, "um acontecimento"), "importante": (8, "algo que você marcou como importante"),
          "confirmado": (5, "palavras dela 🟢")}


def avaliar(texto: str, itens: list[dict], frase_preferida: str | None = None) -> Avaliacao:
    """Pontos: uma memória de vocês (+15), um momento feliz dela (+12), um acontecimento (+10),
    algo marcado como importante (+8), palavras dela 🟢 (+5). Tom: luto (uma perda dela) > difícil
    (estado ruim, preocupação, briga) > feliz > neutro."""
    av = Avaliacao()
    sinais: dict[str, bool] = {k: False for k in _PESOS}
    negativo = False
    melhor: tuple[int, str] | None = None
    for it in itens:
        cats = set(it.get("categorias") or [])
        pol = it.get("polaridade") or 0
        tipo = it.get("tipo")
        alegre = _alegre(it.get("trecho") or "")
        # "chorou de emoção" cai em Preocupações pelas regras; com alegria no trecho, não é momento difícil.
        neg = (tipo == "estado" and pol < 0) or (bool(cats & _CAT_NEGATIVAS) and not alegre) \
            or (tipo in ("preferencia", "apoio") and pol < 0 and "Coisas que incomodam" in cats)
        negativo = negativo or neg
        peso = 0
        achados = {
            "memoria": "Memórias compartilhadas" in cats,
            "feliz": not neg and ((tipo == "estado" and pol > 0) or "Coisas que deixam feliz" in cats or alegre),
            "evento": tipo == "evento",
            "importante": not neg and (it.get("importancia") or 2) >= 3,
            "confirmado": it.get("certeza") == "confirmado",
        }
        for chave, sim in achados.items():
            if sim:
                sinais[chave] = True
                peso += _PESOS[chave][0]
        trecho = it.get("trecho") or ""
        if peso and not neg and trecho and trecho in (texto or "") and (melhor is None or peso > melhor[0]):
            melhor = (peso, trecho)
    for chave, ligado in sinais.items():
        if ligado:
            av.pontos += _PESOS[chave][0]
            av.motivos.append(_PESOS[chave][1])
    frases_texto = [f for _a, _b, f in frases(texto or "")]
    luto = next((f for f in frases_texto if _RE_LUTO.search(plano(f)) and _RE_DELA.search(plano(f))), None)
    if luto:
        av.tom, av.frase = "luto", luto
        return av
    if negativo or _RE_BRIGA.search(plano(texto or "")):
        av.tom = "dificil"
    elif sinais["feliz"] or sinais["memoria"]:
        av.tom = "feliz"
    if frase_preferida and frase_preferida in (texto or ""):
        av.frase = frase_preferida
    elif melhor:
        av.frase = melhor[1]
    else:
        av.frase = frases_texto[0] if frases_texto else None
    return av


# ================================================================ tempo juntos

def dias_marcantes(limite: int = 40000) -> list[int]:
    """100, 500, 1.000, 2.000, 3.000... dias."""
    return [100, 500] + list(range(1000, limite + 1, 1000))


def _meses_marcantes() -> list[int]:
    return [6] + list(range(12, MESES_MARCANTES_ATE + 1, 12))


def titulo_tempo(unidade: str, n: int) -> str:
    if unidade == "d":
        return f"{_milhar(n)} dias juntos"
    if n % 12 == 0:
        return f"{_anos(n // 12)} juntos"
    return f"{n} meses juntos"


def marcos_de_tempo(inicio: date, ate: date, desde: date | None = None) -> list[dict]:
    """Os marcos de tempo juntos entre `desde` (padrão: o início) e `ate`, em ordem de data:
    [{unidade: "d"|"m", n, data, titulo, chave}]. As datas em meses são as do mesversário do núcleo."""
    desde = desde or inicio
    saida = []
    for n in dias_marcantes():
        d = inicio + timedelta(days=n)
        if d > ate:
            break
        if d >= desde:
            saida.append({"unidade": "d", "n": n, "data": d, "titulo": titulo_tempo("d", n), "chave": f"d{n}"})
    for n in _meses_marcantes():
        d = somar_meses(inicio, n)
        if d > ate:
            break
        if d >= desde:
            saida.append({"unidade": "m", "n": n, "data": d, "titulo": titulo_tempo("m", n), "chave": f"m{n}"})
    saida.sort(key=lambda x: (x["data"], x["unidade"] != "m"))
    return saida


def _marco_de_namoro(marcos: list[dict]) -> dict | None:
    """O marco de namoro mais antigo com dia certo (o "Pedido de namoro", o "Começo do namoro")."""
    candidatos = [m for m in marcos if m["tipo"] == "namoro" and m["precisao"] == "dia" and _data_ou_none(m["data"])]
    candidatos.sort(key=lambda m: (m["data"], m["id"]))
    return candidatos[0] if candidatos else None


def tempo_info(banco: Any, referencia: date, marcos: list[dict] | None = None) -> dict:
    """De onde sai o tempo juntos (Ajustes ou o marco de namoro), quanto tempo e os marcos de tempo
    que já passaram e os próximos."""
    marcos = _todos(banco) if marcos is None else marcos
    inicio = banco.config().get("inicio_namoro")
    namoro = _marco_de_namoro(marcos)
    base, base_marco = None, None
    if inicio is not None:
        base = "ajustes"
    elif namoro is not None:
        inicio, base, base_marco = _data(namoro["data"]), "marco", namoro
    info: dict[str, Any] = {"inicio": inicio, "base": base, "marco": None, "dias": None, "tempo_juntos": None,
                            "passados": [], "proximos": [], "diferente": None}
    if base_marco is not None:
        info["marco"] = {"id": base_marco["id"], "titulo": base_marco["titulo"], "registro_id": base_marco["registro_id"],
                         "trecho": base_marco["trecho"]}
    if base == "ajustes" and namoro is not None and _data(namoro["data"]) != inicio:
        info["diferente"] = {"id": namoro["id"], "titulo": namoro["titulo"], "data": _data(namoro["data"]),
                             "registro_id": namoro["registro_id"]}
    if inicio is None or inicio > referencia:
        return info
    info["dias"] = (referencia - inicio).days
    info["tempo_juntos"] = tempo_juntos(inicio, referencia)
    todos = marcos_de_tempo(inicio, referencia + timedelta(days=3700))
    info["passados"] = [dict(x, dias=(x["data"] - referencia).days) for x in todos if x["data"] <= referencia]
    info["proximos"] = [dict(x, dias=(x["data"] - referencia).days) for x in todos if x["data"] > referencia][:3]
    return info


def _de_onde_vem(info: dict) -> str:
    if info["base"] == "ajustes":
        return f"Vocês estão juntos desde {data_br(info['inicio'])} (informado em Ajustes)."
    m = info["marco"] or {}
    if m.get("trecho") and m.get("registro_id"):
        return (f"Contei a partir de {data_br(info['inicio'])}, do marco “{m['titulo']}” que você registrou: "
                f"“{_encurtar(m['trecho'], 160)}”. Dá para guardar essa data em Ajustes.")
    return f"Contei a partir de {data_br(info['inicio'])}, do marco “{m.get('titulo', '')}”."


# ================================================================ "Neste dia"

def _vista_marco(m: dict, referencia: date) -> dict:
    d = _data(m["data"])
    emoji, rotulo = ROTULOS_TIPO.get(m["tipo"], ROTULOS_TIPO["outro"])
    vista = {
        "id": m["id"], "titulo": m["titulo"], "data": d, "precisao": m["precisao"],
        "quando": quando_texto(d, m["precisao"]), "tipo": m["tipo"], "icone": emoji, "rotulo_tipo": rotulo,
        "descricao": m.get("descricao"), "pessoa": m.get("pessoa"), "registro_id": m.get("registro_id"),
        "trecho": m.get("trecho"), "data_expressao": m.get("data_expressao"), "data_origem": m.get("data_origem"),
        "criado_em": m.get("criado_em"),
        "ha": tempo_atras((referencia - d).days) if d <= referencia and m["precisao"] == "dia" else None,
        "aniversario": None,
    }
    if m["precisao"] == "dia" and d < referencia:
        proximo = _aniversario_em(d, referencia.year)
        if proximo < referencia:
            proximo = _aniversario_em(d, referencia.year + 1)
        vista["aniversario"] = {"data": proximo, "anos": proximo.year - d.year, "dias": (proximo - referencia).days}
    return vista


def _ha_anos(n: int) -> str:
    return f"há {_anos(n)}"


def _mmdd(d: date) -> list[str]:
    """O dia e o mês para comparar; em 28/02 de ano não bissexto, também o 29/02 (que não existe)."""
    saida = [d.strftime("%m-%d")]
    if d.month == 2 and d.day == 28:
        try:
            date(d.year, 2, 29)
        except ValueError:
            saida.append("02-29")
    return saida


def lembrancas(banco: Any, referencia: date, sensiveis: Any = None, marcos: list[dict] | None = None,
               info: dict | None = None) -> list[dict]:
    """O "Neste dia" de `referencia`: marcos e registros de anos anteriores no mesmo dia e mês, e o
    que foi registrado há exatamente 6 meses; cada um com os pontos e o porquê. Mais fortes primeiro.

    [{fonte: "marco"|"registro", marco_id, registro_id, data, anos (ou meses=6), ha, titulo, trecho,
      tom, pontos, motivos, descricao, marco_tipo}]
    """
    marcos = _todos(banco) if marcos is None else marcos
    info = tempo_info(banco, referencia, marcos) if info is None else info
    if sensiveis is None:
        sensiveis = _Sensiveis(banco)
    saida: list[dict] = []
    datas_marco: dict[int, set[str]] = {}
    for m in marcos:
        if m.get("registro_id") is not None:
            datas_marco.setdefault(m["registro_id"], set()).add(m["data"])
    seis = somar_meses(referencia, -6)
    for m in marcos:
        d = _data_ou_none(m["data"])
        if d is None or m["precisao"] != "dia":
            continue
        if d == seis:
            anos, meses, ha = None, 6, "há 6 meses"
        elif d.year < referencia.year and _mesmo_dia_mes(d, referencia):
            anos, meses, ha = referencia.year - d.year, None, _ha_anos(referencia.year - d.year)
        else:
            continue
        if m["tipo"] in ("namoro", "primeiro_encontro") and info.get("inicio") is not None and d == info["inicio"]:
            continue                                   # o aniversário de namoro o núcleo (ou o tempo juntos) já lembra
        rotulo = ROTULOS_TIPO.get(m["tipo"], ROTULOS_TIPO["outro"])[1].lower()
        saida.append({"fonte": "marco", "marco_id": m["id"], "registro_id": m.get("registro_id"), "data": d,
                      "anos": anos, "meses": meses, "ha": ha, "titulo": m["titulo"],
                      "trecho": m.get("trecho") if _pode_citar(m.get("trecho"), m.get("registro_id"), sensiveis) else None,
                      "descricao": m.get("descricao"), "marco_tipo": m["tipo"], "tom": "marco",
                      "pontos": (40 if anos else 30) + (10 if m["tipo"] in ANIVERSARIOS_DE_CASAL else 0),
                      "motivos": [f"um marco de vocês ({rotulo})"]})
    # Registros (e acontecimentos registrados) no mesmo dia e mês em anos anteriores, e há 6 meses.
    mmdd = _mmdd(referencia)
    antes_deste_ano = f"{referencia.year}-01-01"
    candidatos: dict[tuple[int, str], dict] = {}
    for l in banco.consultar(
            f"SELECT id, data_registro FROM registros WHERE (substr(data_registro, 6, 5) IN ({_marcas(len(mmdd))})"
            " AND data_registro < ?) OR data_registro = ?", (*mmdd, antes_deste_ano, seis.isoformat())):
        dia = l["data_registro"][:10]
        candidatos[(l["id"], dia)] = {"registro_id": l["id"], "data": dia, "frase": None}
    for l in banco.consultar(
            "SELECT registro_id, data_evento, trecho FROM itens WHERE tipo = 'evento' AND status = 'ativo'"
            " AND COALESCE(recorrente, 0) = 0 AND COALESCE(precisao, 'dia') = 'dia' AND data_evento IS NOT NULL"
            f" AND ((substr(data_evento, 6, 5) IN ({_marcas(len(mmdd))}) AND data_evento < ?) OR data_evento = ?)",
            (*mmdd, antes_deste_ano, seis.isoformat())):
        dia = l["data_evento"][:10]
        chave = (l["registro_id"], dia)
        candidatos.setdefault(chave, {"registro_id": l["registro_id"], "data": dia, "frase": None})
        candidatos[chave]["frase"] = l["trecho"]
    if candidatos:
        sensiveis = _resolver(sensiveis)
        ids = sorted({c["registro_id"] for c in candidatos.values()})
        textos = {l["id"]: l for l in banco.consultar(
            f"SELECT id, texto, data_registro FROM registros WHERE id IN ({_marcas(len(ids))})", tuple(ids))}
        itens = _itens_de(banco, ids)
        for c in candidatos.values():
            rid, dia = c["registro_id"], c["data"]
            reg = textos.get(rid)
            d = _data_ou_none(dia)
            if reg is None or d is None or rid in sensiveis or dia in datas_marco.get(rid, set()):
                continue
            todos_itens = itens.get(rid, [])
            if c["frase"] is not None:
                itens_c = [i for i in todos_itens if i.get("trecho") == c["frase"]]
            else:   # o dia do registro: sem os acontecimentos de outros dias
                itens_c = [i for i in todos_itens if i["tipo"] != "evento" or (i.get("data_evento") or "")[:10] == dia]
            av = avaliar(reg["texto"], itens_c, c["frase"])
            if av.tom == "dificil" or not av.frase or _RE_CICLO.search(plano(av.frase)):
                continue
            if av.tom != "luto" and _RE_CICLO.search(plano(reg["texto"] or "")):
                continue
            if d == seis:
                if av.tom != "feliz" or av.pontos < LIMIAR_SEIS_MESES:
                    continue
                anos, meses, ha = None, 6, "há 6 meses"
            else:
                anos = referencia.year - d.year
                if anos < 1 or (av.tom != "luto" and av.pontos < LIMIAR_LEMBRANCA):
                    continue
                meses, ha = None, _ha_anos(anos)
            saida.append({"fonte": "registro", "marco_id": None, "registro_id": rid, "data": d, "anos": anos,
                          "meses": meses, "ha": ha, "titulo": None, "trecho": av.frase, "descricao": None,
                          "marco_tipo": None, "tom": av.tom, "pontos": av.pontos + (8 if (anos or 0) >= 2 else 0),
                          "motivos": av.motivos if av.tom != "luto" else ["o aniversário de uma perda dela"]})
    # Um registro só uma vez (o dia do registro e o do evento podem cair no mesmo dia).
    vistos: set[tuple] = set()
    unicas = []
    for l in sorted(saida, key=lambda x: (-x["pontos"], x["data"], x["registro_id"] or 0, x["marco_id"] or 0)):
        chave = (l["fonte"], l["marco_id"] or l["registro_id"], l["data"])
        if chave in vistos or (l["fonte"] == "registro" and ("registro", l["registro_id"]) in vistos):
            continue
        vistos.add(chave)
        if l["fonte"] == "registro":
            vistos.add(("registro", l["registro_id"]))
        unicas.append(l)
    return unicas


# ================================================================ cartões do Hoje

def _citacao_marco(m: dict, referencia: date, datas_registros: dict[int, date], sensiveis: Any = None) -> str:
    rid = m.get("registro_id")
    dr = datas_registros.get(rid) if rid else None
    quando = f" em {_dia(dr, referencia)}" if dr else ""
    if rid and _pode_citar(m.get("trecho"), rid, sensiveis):
        return f"Você registrou{quando}: “{_encurtar(m['trecho'], 220)}”"
    if rid:
        return f"Você guardou este marco num registro{quando}"
    criado = _data_ou_none((m.get("criado_em") or "")[:10])
    return f"Você guardou este marco{f' em {_dia(criado, referencia)}' if criado else ''}"


def _datas_registros(banco: Any, ids: set[int]) -> dict[int, date]:
    ids_ = sorted(i for i in ids if i)
    if not ids_:
        return {}
    return {l["id"]: _data_ou_none(l["data_registro"]) for l in banco.consultar(
        f"SELECT id, data_registro FROM registros WHERE id IN ({_marcas(len(ids_))})", tuple(ids_))}


def momentos_ate(marcos: list[dict], ate: date) -> list[dict]:
    """Os primeiros marcos guardados da história de vocês até uma data, para relembrar."""
    lista = [m for m in marcos if _data_ou_none(m["data"]) and _data(m["data"]) <= ate]
    lista.sort(key=lambda m: (m["data"], m["id"]))
    return [{"marco_id": m["id"], "titulo": m["titulo"], "data": _data(m["data"]), "precisao": m["precisao"],
             "quando": quando_texto(_data(m["data"]), m["precisao"]), "registro_id": m.get("registro_id")}
            for m in lista[:MAX_MOMENTOS]]


def _frase_momentos(momentos: list[dict]) -> str:
    partes = [f"{m['titulo']} ({m['quando']})" for m in momentos]
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + " e " + partes[-1]


def _frase_dificil(dificil: dict, referencia: date) -> str:
    quando = dificil.get("data")
    return (f"Em {_dia(quando, referencia) if quando else 'dias recentes'} você registrou: "
            f"“{_encurtar(dificil.get('trecho') or dificil.get('resumo') or '', 160)}”.")


def _cartoes_tempo(info: dict, marcos: list[dict], dificil: dict | None, referencia: date) -> list[dict]:
    saida: list[dict] = []
    if info["inicio"] is None or info["inicio"] > referencia:
        return saida
    origem = _de_onde_vem(info)
    ids_base = [info["marco"]["registro_id"]] if info["base"] == "marco" and info["marco"]["registro_id"] else []
    for x in marcos_de_tempo(info["inicio"], referencia + timedelta(days=max(DIAS_AVISO)), referencia):
        dias = (x["data"] - referencia).days
        if dias not in DIAS_AVISO:
            continue
        momentos = momentos_ate(marcos, x["data"]) if dias == 0 else []
        ids_momentos = [m["registro_id"] for m in momentos if m.get("registro_id")]
        if not (info["base"] == "ajustes" and x["unidade"] == "m"):
            texto = (f"Hoje vocês completam {x['titulo']} 💙" if dias == 0
                     else f"Daqui a {dias} dias vocês completam {x['titulo']} 💙")
            porque = [origem]
            if momentos:
                porque.append(f"Alguns momentos de vocês que você guardou: {_frase_momentos(momentos)}.")
            saida.append(cartao(
                f"tempo:{x['chave']}:{x['data'].isoformat()}:d{dias}", "lembrete", texto, porque=" ".join(porque),
                relevancia=max(40, 80 - dias) + 5, data=x["data"],
                registro_ids=list(dict.fromkeys(ids_base + ids_momentos)), tipo="marco_tempo",
                dados={"titulo": x["titulo"], "data": x["data"], "dias": dias, "unidade": x["unidade"], "n": x["n"],
                       "inicio": info["inicio"], "base": info["base"], "momentos": momentos}))
        elif dias == 0 and momentos:
            # O núcleo já avisa "Hoje vocês completam 1 ano juntos": aqui, só relembrar o que foi guardado.
            if dificil:
                texto = (f"Hoje vocês completam {x['titulo']}. Se for um bom momento para ela, talvez seja bonito "
                         "relembrar juntos alguns momentos de vocês 💙")
            else:
                texto = f"Hoje vocês completam {x['titulo']}. Talvez seja bonito relembrar com ela alguns momentos de vocês 💙"
            porque = [origem, f"Você guardou: {_frase_momentos(momentos)}."]
            ids = list(ids_momentos)
            if dificil:
                porque.append(_frase_dificil(dificil, referencia))
                if dificil.get("registro_id"):
                    ids.append(dificil["registro_id"])
            saida.append(cartao(
                f"relembrar:{x['chave']}:{x['data'].isoformat()}", "sugestao", texto, porque=" ".join(porque),
                relevancia=48 if dificil else 60, data=x["data"], registro_ids=list(dict.fromkeys(ids)),
                tipo="marco_relembrar", dados={"titulo": x["titulo"], "data": x["data"], "momentos": momentos}))
    return saida


def _cartoes_aniversarios(info: dict, marcos: list[dict], referencia: date, datas_registros: dict[int, date],
                          sensiveis: Any = None) -> list[dict]:
    """Aniversário de um marco de casal (primeiro encontro, "eu te amo", namoro): 7 dias antes e no dia."""
    saida = []
    for m in marcos:
        d = _data_ou_none(m["data"])
        if d is None or m["precisao"] != "dia" or m["tipo"] not in ANIVERSARIOS_DE_CASAL:
            continue
        if info.get("inicio") is not None and d == info["inicio"]:
            continue                                   # é o início do namoro: quem lembra é o tempo juntos
        for dias in DIAS_AVISO:
            alvo = referencia + timedelta(days=dias)
            if not _mesmo_dia_mes(d, alvo) or alvo.year <= d.year:
                continue
            anos = alvo.year - d.year
            texto = (f"Hoje faz {_anos(anos)}: “{m['titulo']}” 💙" if dias == 0
                     else f"Daqui a {dias} dias faz {_anos(anos)}: “{m['titulo']}”.")
            porque = f"{_citacao_marco(m, referencia, datas_registros, sensiveis)}. Talvez seja gostoso relembrar com ela."
            citavel = _pode_citar(m.get("trecho"), m.get("registro_id"), sensiveis)
            saida.append(cartao(
                f"m{m['id']}:aniv:{alvo.isoformat()}:d{dias}", "lembrete", texto, porque=porque,
                relevancia=82 if dias == 0 else 74, data=alvo,
                registro_ids=[m["registro_id"]] if m.get("registro_id") else [], tipo="marco_aniversario",
                dados={"marco": {**_vista_marco(m, referencia), "trecho": m.get("trecho") if citavel else None},
                       "anos": anos, "dias": dias, "trecho": m.get("trecho") if citavel else None}))
    return saida


def texto_lembranca(l: dict, dificil: dict | None, amanha: bool = False) -> str:
    """A frase do "Neste dia" (cita o trecho; o convite some quando ela anda num momento difícil)."""
    if l["tom"] == "luto":
        if amanha:
            return (f"Amanhã faz {_anos(l['anos'])}: “{_encurtar(l['trecho'], 200)}” Talvez seja um dia mais "
                    "sensível para ela, e estar por perto ajude.")
        return (f"Neste dia, {l['ha']}, você registrou: “{_encurtar(l['trecho'], 200)}” Talvez hoje seja um dia mais "
                "sensível para ela, e estar por perto ajude.")
    convite = "" if dificil else " Talvez seja gostoso relembrar com ela."
    if l["fonte"] == "marco":
        extra = f" ({l['descricao']})" if l.get("descricao") else ""
        inicio = "Há 6 meses" if l["meses"] else f"Neste dia, {l['ha']}"
        return f"{inicio}: “{l['titulo']}”{extra} 💙{convite}"
    if l["meses"]:
        return f"Há 6 meses, você registrou: “{_encurtar(l['trecho'], 200)}”{convite}"
    return f"Neste dia, {l['ha']}, você registrou: “{_encurtar(l['trecho'], 200)}”{convite}"


def _cartoes_neste_dia(banco: Any, info: dict, marcos: list[dict], dificil: dict | None, referencia: date,
                       datas_registros: dict[int, date], sensiveis: Any) -> list[dict]:
    saida = []
    apoio: dict | None | bool = False                    # lido só se precisar
    for dias in (0, 1):
        alvo = referencia + timedelta(days=dias)
        for l in lembrancas(banco, alvo, sensiveis, marcos, info):
            if dias == 1 and l["tom"] != "luto":
                continue                               # na véspera, só o cuidado com uma perda dela
            if l["fonte"] == "marco" and l["anos"] and l["marco_tipo"] in ANIVERSARIOS_DE_CASAL:
                continue                               # já é um lembrete de aniversário
            porque = []
            ids = [l["registro_id"]] if l.get("registro_id") else []
            if l["fonte"] == "marco":
                m = next((x for x in marcos if x["id"] == l["marco_id"]), None)
                if m is not None:
                    porque.append(_citacao_marco(m, referencia, datas_registros, sensiveis) + ".")
                relevancia = 66 if l["anos"] else 58
            else:
                porque.append(f"Você registrou em {data_br(l['data'])}: “{_encurtar(l['trecho'], 220)}”.")
                if l["motivos"]:
                    porque.append("Volta porque é " + " e ".join(l["motivos"][:3]) + ".")
                relevancia = (44 + min(l["pontos"], 40) // 2) if l["anos"] else (42 + min(l["pontos"], 40) // 3)
            if l["tom"] == "luto":
                relevancia = 70 if dias == 0 else 64
                if apoio is False:
                    apoio = _apoio(banco, sensiveis())
                if apoio:
                    porque.append(f"Ela já disse o que ajuda: “{_encurtar(apoio['trecho'] or apoio['resumo'] or '', 160)}”.")
                    ids.append(apoio["registro_id"])
                chave = f"r{l['registro_id']}:luto:{alvo.isoformat()}:d{dias}"
                tipo = "neste_dia_luto"
            else:
                if dificil:
                    relevancia -= 15
                    porque.append(_frase_dificil(dificil, referencia) + " Por isso fica só como lembrança, sem convite.")
                    if dificil.get("registro_id"):
                        ids.append(dificil["registro_id"])
                fonte = f"m{l['marco_id']}" if l["fonte"] == "marco" else f"r{l['registro_id']}"
                chave = f"{fonte}:{'seis' if l['meses'] else 'neste'}:{alvo.isoformat()}"
                tipo = "neste_dia"
            saida.append(cartao(
                chave, "sugestao", texto_lembranca(l, dificil, amanha=dias == 1), porque=" ".join(porque),
                relevancia=relevancia, data=alvo, registro_ids=list(dict.fromkeys(i for i in ids if i)), tipo=tipo,
                dados={"fonte": l["fonte"], "marco_id": l["marco_id"], "registro_id": l["registro_id"], "data": l["data"],
                       "ha": l["ha"], "titulo": l["titulo"], "trecho": l["trecho"], "tom": l["tom"],
                       "motivos": l["motivos"], "descricao": l["descricao"]}))
    return saida


def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    marcos = _todos(banco)
    info = tempo_info(banco, referencia, marcos)
    sensiveis = _Sensiveis(banco)                      # lido só se algum trecho for aparecer
    dificil = momento(banco, referencia, sensiveis)
    datas_registros = _datas_registros(banco, {m["registro_id"] for m in marcos if m.get("registro_id")})
    saida = (_cartoes_tempo(info, marcos, dificil, referencia)
             + _cartoes_aniversarios(info, marcos, referencia, datas_registros, sensiveis)
             + _cartoes_neste_dia(banco, info, marcos, dificil, referencia, datas_registros, sensiveis))
    saida.sort(key=lambda c: -c["relevancia"])
    return saida[:20]


# ================================================================ linha do tempo

_RE_DATA_NO_FIM = re.compile(
    r"\s*(?:—\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?|\b(?:em|no\s+dia|dia)\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|\bem\s+(?:" + "|".join(_MESES) + r"|marco)(?:/\d{4})?|\bem\s+\d{4}|\bna\s+semana\s+de\s+\d{1,2}/\d{1,2}/\d{4})\s*$",
    re.IGNORECASE)


def _sem_data(resumo: str) -> str:
    return _RE_DATA_NO_FIM.sub("", resumo or "").strip() or (resumo or "").strip()


def _pessoas_dos_registros(banco: Any, ids: set[int]) -> dict[int, list[str]]:
    saida: dict[int, list[str]] = {}
    ids_ = sorted(i for i in ids if i)
    for parte in range(0, len(ids_), 500):
        lote = ids_[parte:parte + 500]
        for l in banco.consultar(
                "SELECT rp.registro_id, p.nome, p.relacao FROM registro_pessoas rp JOIN pessoas p ON p.id = rp.pessoa_id"
                f" WHERE rp.registro_id IN ({_marcas(len(lote))}) ORDER BY p.id", tuple(lote)):
            if l["nome"] and l["relacao"]:
                rotulo = f"{l['nome']} ({l['relacao']})"
            else:
                rotulo = l["nome"] or l["relacao"]
            if rotulo:
                saida.setdefault(l["registro_id"], []).append(rotulo)
    return saida


def _ponto(tipo: str, d: date, precisao: str, titulo: str, icone: str, rotulo: str, **mais: Any) -> dict:
    p = {"tipo": tipo, "data": d, "precisao": precisao, "quando": quando_texto(d, precisao), "titulo": titulo,
         "trecho": None, "registro_id": None, "marco_id": None, "marco_tipo": None, "icone": icone, "rotulo": rotulo,
         "descricao": None, "pessoas": [], "motivos": [], "tom": "marco", "data_origem": None}
    p.update(mais)
    return p


def linha_do_tempo(banco: Any, referencia: date, tudo: bool = False) -> dict:
    """Marcos, marcos de tempo, o início do namoro, eventos passados e registros importantes, por ano
    (o mais recente primeiro). `tudo`: também os acontecimentos de outras pessoas, os momentos
    difíceis e os registros menos marcantes."""
    marcos = _todos(banco)
    info = tempo_info(banco, referencia, marcos)
    sensiveis = _sensiveis(banco)
    pontos: list[dict] = []
    usados: set[tuple[int, str]] = set()        # (registro_id, trecho) já representados por um ponto
    for m in marcos:
        d = _data(m["data"])
        if d > referencia:
            continue
        emoji, rotulo = ROTULOS_TIPO.get(m["tipo"], ROTULOS_TIPO["outro"])
        citavel = _pode_citar(m.get("trecho"), m.get("registro_id"), sensiveis)
        pontos.append(_ponto("marco", d, m["precisao"], m["titulo"], emoji, rotulo,
                             trecho=m.get("trecho") if citavel else None,
                             registro_id=m.get("registro_id"), marco_id=m["id"], marco_tipo=m["tipo"],
                             descricao=m.get("descricao"), data_origem=m.get("data_origem")))
        if m.get("registro_id"):
            usados.add((m["registro_id"], m.get("trecho") or ""))
    if info["base"] == "ajustes" and info["inicio"] <= referencia and not any(
            p["data"] == info["inicio"] and p["marco_tipo"] in ("namoro", "primeiro_encontro") for p in pontos):
        pontos.append(_ponto("inicio", info["inicio"], "dia", "Começo do namoro", "💑", "Início do namoro",
                             descricao="A data que está em Ajustes."))
    for x in info["passados"]:
        pontos.append(_ponto("tempo", x["data"], "dia", x["titulo"], "⏳", "Tempo juntos"))
    # Acontecimentos passados (com a data que foi dita).
    for l in banco.consultar(
            "SELECT i.id, i.registro_id, i.resumo, i.trecho, i.categorias, i.importancia, i.data_evento, i.precisao,"
            " i.pessoa, i.pessoa_id, i.certeza FROM itens i"
            " WHERE i.tipo = 'evento' AND i.status = 'ativo' AND COALESCE(i.recorrente, 0) = 0"
            " AND i.data_evento IS NOT NULL AND i.data_evento <= ? ORDER BY i.data_evento DESC, i.id DESC",
            (referencia.isoformat(),)):
        trecho = l["trecho"] or ""
        if l["registro_id"] in sensiveis or (l["registro_id"], trecho) in usados or _RE_CICLO.search(plano(trecho)):
            continue
        d = _data_ou_none(l["data_evento"])
        if d is None:
            continue
        precisao = l["precisao"] if l["precisao"] in PRECISOES else "dia"
        cats = set(_json_lista(l["categorias"]))
        dificil = bool(cats & _CAT_NEGATIVAS) or _RE_BRIGA.search(plano(trecho)) is not None
        luto = _RE_LUTO.search(plano(trecho)) is not None
        essencial = ("Memórias compartilhadas" in cats or (l["importancia"] or 2) >= 3 or luto
                     or (_dela(l) and bool(cats & {"Viagens", "Experiências importantes", "Relacionamento"})))
        if not tudo and (not essencial or (dificil and not luto)):
            continue
        pontos.append(_ponto("evento", d, precisao, _sem_data(l["resumo"]), "🕊️" if luto else "📌", "Acontecimento",
                             trecho=l["trecho"], registro_id=l["registro_id"], certeza=l["certeza"],
                             tom="luto" if luto else ("dificil" if dificil else "neutro")))
        usados.add((l["registro_id"], trecho))
    # Registros importantes (pontos explicáveis).
    registros = banco.consultar("SELECT id, data_registro, texto FROM registros WHERE data_registro <= ?"
                                " ORDER BY data_registro DESC, id DESC", (referencia.isoformat(),))
    representados = {rid for rid, _t in usados}
    validos = [r for r in registros if r["id"] not in sensiveis and r["id"] not in representados
               and not _RE_CICLO.search(plano(r["texto"] or ""))]
    itens = _itens_de(banco, [r["id"] for r in validos])
    limiar = LIMIAR_TUDO if tudo else LIMIAR_IMPORTANTE
    for r in validos:
        av = avaliar(r["texto"], [i for i in itens.get(r["id"], []) if i["tipo"] != "evento"])
        if av.tom == "luto":
            av.pontos = max(av.pontos, limiar)
        elif av.tom == "dificil" and tudo:
            # Em "tudo", um momento difícil também é parte da história (no essencial, não aparece).
            av.pontos = max(av.pontos, LIMIAR_TUDO)
            av.motivos = av.motivos + ["um momento difícil"]
        if av.pontos < limiar or not av.frase or (av.tom == "dificil" and not tudo):
            continue
        d = _data(r["data_registro"])
        icone = {"feliz": "💛", "luto": "🕊️", "dificil": "🫂"}.get(av.tom, "📖")
        pontos.append(_ponto("registro", d, "dia", _encurtar(av.frase, 160), icone, "Registro", trecho=av.frase,
                             registro_id=r["id"], motivos=av.motivos, tom=av.tom))
    ordem = {"inicio": 0, "marco": 1, "tempo": 2, "evento": 3, "registro": 4}
    pontos.sort(key=lambda p: (p["data"], -ordem.get(p["tipo"], 9)), reverse=True)
    pontos = pontos[:MAX_PONTOS]
    pessoas = _pessoas_dos_registros(banco, {p["registro_id"] for p in pontos if p.get("registro_id")})
    for p in pontos:
        if p.get("registro_id"):
            p["pessoas"] = pessoas.get(p["registro_id"], [])[:6]
    anos: dict[int, list[dict]] = {}
    for p in pontos:
        anos.setdefault(p["data"].year, []).append(p)
    contagem = {t: sum(1 for p in pontos if p["tipo"] == t) for t in ("marco", "tempo", "evento", "registro")}
    return {"tempo": vista_tempo(info), "anos": [{"ano": a, "pontos": ps} for a, ps in anos.items()],
            "contagem": contagem, "tudo": tudo}


def vista_tempo(info: dict) -> dict:
    return {
        "inicio": info["inicio"], "base": info["base"], "marco": info["marco"], "dias": info["dias"],
        "tempo_juntos": info["tempo_juntos"], "diferente": info["diferente"],
        "texto": _de_onde_vem(info) if info["inicio"] else None,
        "proximos": [{"titulo": x["titulo"], "data": x["data"], "dias": x["dias"]} for x in info["proximos"]],
        "ultimo": ({"titulo": info["passados"][-1]["titulo"], "data": info["passados"][-1]["data"]}
                   if info["passados"] else None),
    }


def painel(banco: Any, referencia: date) -> dict:
    """GET /api/marcos: os marcos guardados (mais novos primeiro) e o tempo juntos."""
    marcos = _todos(banco)
    info = tempo_info(banco, referencia, marcos)
    vistas = [_vista_marco(m, referencia) for m in marcos]
    vistas.sort(key=lambda v: (v["data"], v["id"]), reverse=True)
    return {"marcos": vistas, "tempo": vista_tempo(info)}


def neste_dia(banco: Any, referencia: date) -> dict:
    """GET /api/marcos/neste-dia: as lembranças do dia (para a tela), com o momento dela."""
    marcos = _todos(banco)
    info = tempo_info(banco, referencia, marcos)
    sensiveis = _Sensiveis(banco)
    dificil = momento(banco, referencia, sensiveis)
    saida = [{**l, "texto": texto_lembranca(l, dificil)} for l in lembrancas(banco, referencia, sensiveis, marcos, info)]
    return {"data": referencia, "lembrancas": saida[:MAX_LEMBRANCAS], "momento": dificil,
            "tempo_hoje": [x["titulo"] for x in info["passados"] if x["data"] == referencia]}


# ================================================================ rotas

def _rota_listar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return painel(banco, nucleo.hoje())


def _rota_linha(nucleo: Any, pedido: Any, banco: Any) -> dict:
    tudo = (pedido.parametro("tudo") or "").strip().lower() in ("1", "sim", "true")
    return linha_do_tempo(banco, nucleo.hoje(), tudo)


def _rota_neste_dia(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return neste_dia(banco, _data(pedido.parametro("data")) or nucleo.hoje())


def _rota_criar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if set(c) - {"titulo", "data", "tipo", "descricao", "precisao"}:
        raise ValueError(CAMPOS_INVALIDOS)
    hoje = nucleo.hoje()
    titulo = _texto(c.get("titulo"), MAX_TITULO, TITULO_INVALIDO)
    d = _data_valida(c.get("data"), hoje)
    tipo = _tipo(c.get("tipo", "outro"), None)
    precisao = c.get("precisao", "dia")
    if precisao not in PRECISOES:
        raise ValueError(PRECISAO_INVALIDA)
    descricao = _descricao(c.get("descricao"))
    _conferir_lotacao(banco)
    mid = banco.inserir("marcos", {
        "titulo": titulo, "data": d, "precisao": precisao, "tipo": tipo, "descricao": descricao, "pessoa": None,
        "trecho": None, "data_expressao": None, "data_origem": "manual", "registro_id": None, "criado_em": _agora()})
    return {"id": mid, "marco": _vista_marco(_um(banco, mid), hoje)}


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    c = pedido.corpo
    if not c or set(c) - {"titulo", "data", "tipo", "descricao", "precisao"}:
        raise ValueError(CAMPOS_INVALIDOS)
    m = _um(banco, pedido.id)
    hoje = nucleo.hoje()
    mudar: dict[str, Any] = {}
    if "titulo" in c:
        mudar["titulo"] = _texto(c.get("titulo"), MAX_TITULO, TITULO_INVALIDO)
    if "tipo" in c:
        mudar["tipo"] = _tipo(c.get("tipo"), None)
    if "descricao" in c:
        mudar["descricao"] = _descricao(c.get("descricao"))
    if "data" in c:
        d = _data_valida(c.get("data"), hoje)
        if d.isoformat() != m["data"]:
            mudar.update(data=d, data_expressao=None, data_origem="manual", precisao="dia")
    if "precisao" in c:
        if c.get("precisao") not in PRECISOES:
            raise ValueError(PRECISAO_INVALIDA)
        mudar["precisao"] = c.get("precisao")
    if mudar:
        with banco.transacao():
            banco.atualizar("marcos", m["id"], mudar)
            _esquecer_marco(banco, m["id"])
    return {"marco": _vista_marco(_um(banco, m["id"]), hoje)}


def _rota_apagar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    mid = pedido.id
    with banco.transacao():
        banco.apagar("marcos", mid)
        _esquecer_marco(banco, mid)

    def na_copia(copia: Any) -> None:
        copia.apagar("marcos", mid)                    # NaoEncontrado: a cópia não tinha, fica como está
        copia.esquecer_alertas(f"{NOME}:m{mid}:")

    nucleo.limpar_copias(na_copia)
    return {}


def _rota_parecidos(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    """Marcos já guardados parecidos com um que vai ser confirmado (a revisão avisa antes)."""
    c = pedido.corpo
    if set(c) - {"tipo", "titulo", "data"}:
        raise ValueError("Mande só o tipo, o título e a data para comparar.")
    tipo = c.get("tipo") if c.get("tipo") in TIPOS else None
    titulo = c.get("titulo") if isinstance(c.get("titulo"), str) and len(c.get("titulo")) <= 500 else ""
    if not tipo and not titulo.strip():
        raise ValueError("Mande o tipo ou o título do marco para comparar.")
    d = _data_ou_none(c.get("data"))
    hoje = nucleo.hoje()
    saida = []
    for m in _todos(banco):
        nota = semelhanca(m["titulo"], titulo) if titulo.strip() else 0
        mesmo_tipo = tipo is not None and tipo != "outro" and m["tipo"] == tipo
        if nota < 70 and not mesmo_tipo:
            continue
        igual = d is not None and bool(titulo.strip()) and _igual(m, tipo or m["tipo"], titulo, d)
        saida.append({**_vista_marco(m, hoje), "semelhanca": round(nota), "igual": igual})
    saida.sort(key=lambda v: (not v["igual"], -v["semelhanca"], v["id"]))
    return saida[:5]


def rotas() -> list:
    return [
        rota("GET", "", _rota_listar),
        rota("POST", "", _rota_criar),
        rota("GET", "linha", _rota_linha),
        rota("GET", "neste-dia", _rota_neste_dia),
        rota("POST", "parecidos", _rota_parecidos),
        rota("PATCH", "{id}", _rota_editar),
        rota("DELETE", "{id}", _rota_apagar),
    ]


# ================================================================ exportar, importar, apagar registro

_COLUNAS = ("titulo", "data", "precisao", "tipo", "descricao", "pessoa", "trecho", "data_expressao", "data_origem",
            "registro_id", "criado_em")


def exportar(banco: Any) -> dict:
    return {"marcos": [{"id": l["id"], **{c: l.get(c) for c in _COLUNAS}}
                       for l in banco.consultar("SELECT * FROM marcos ORDER BY id")]}


def _texto_import(v: Any, maximo: int) -> str | None:
    if not isinstance(v, str):
        return None
    v = _espacos(v)
    return v if v and len(v) <= maximo else None


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta os marcos de um arquivo exportado sem duplicar. O que vinha de um registro que não entrou
    fica de fora (não teria de onde citar); o guardado à mão entra; o trecho tem de estar no registro."""
    if not isinstance(dados, dict) or not isinstance(dados.get("marcos", []), list):
        raise ValueError("formato")
    existentes = _todos(banco)
    novos = fora = 0
    total = len(existentes)
    for bruto in dados.get("marcos", [])[:MAX_IMPORTAR]:
        if not isinstance(bruto, dict):
            fora += 1
            continue
        titulo = _texto_import(bruto.get("titulo"), MAX_TITULO)
        d = _data_ou_none(bruto.get("data"))
        tipo = bruto.get("tipo") if bruto.get("tipo") in TIPOS else "outro"
        rid_arquivo = bruto.get("registro_id")
        rid = mapa_registros.get(rid_arquivo) if _id_valido(rid_arquivo) else None
        if titulo is None or d is None or d.year < 1900 or (rid_arquivo is not None and rid is None):
            fora += 1
            continue
        trecho = None
        if rid is not None:
            trecho = _texto_import(bruto.get("trecho"), 20000)
            registro = banco.consultar("SELECT texto FROM registros WHERE id = ?", (rid,))
            if not registro or not trecho or plano(trecho) not in plano(registro[0]["texto"]):
                fora += 1                              # o trecho tem de estar no registro (nunca inventar)
                continue
        if any(_igual(e, tipo, titulo, d) and e.get("registro_id") == rid for e in existentes):
            continue
        if total >= MAX_MARCOS:
            fora += 1
            continue
        try:
            descricao = _descricao(bruto.get("descricao"))
        except ValueError:
            descricao = None
        criado = bruto.get("criado_em")
        try:
            criado = datetime.fromisoformat(criado).isoformat(timespec="seconds") if isinstance(criado, str) else _agora()
        except ValueError:
            criado = _agora()
        linha = {
            "titulo": titulo, "data": d, "tipo": tipo,
            "precisao": bruto.get("precisao") if bruto.get("precisao") in PRECISOES else "dia",
            "descricao": descricao, "pessoa": _literal(bruto.get("pessoa"), trecho), "trecho": trecho,
            "data_expressao": _literal(bruto.get("data_expressao"), trecho, 80),
            "data_origem": (bruto.get("data_origem") if bruto.get("data_origem") in ORIGENS else "texto")
            if rid is not None else "manual",
            "registro_id": rid, "criado_em": criado,
        }
        novo = banco.inserir("marcos", linha)
        existentes.append({**linha, "id": novo, "data": d.isoformat()})
        total += 1
        novos += 1
    texto = "Nenhum marco novo." if not novos else "1 marco novo." if novos == 1 else f"{novos} marcos novos."
    if fora:
        texto += f" {fora} ficaram de fora (sem o registro de onde vieram, ou com dados que não dá para ler)."
    return {"novos": novos, "texto": texto}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """Antes de o registro sair: as chaves de aviso que citam ele ou os marcos que vieram dele somem
    (a fundação apaga as linhas com esse registro_id: o marco sai junto com o texto de onde veio)."""
    for l in banco.consultar("SELECT id FROM marcos WHERE registro_id = ?", (registro_id,)):
        _esquecer_marco(banco, l["id"])
    banco.esquecer_alertas(f"{NOME}:r{registro_id}:")
