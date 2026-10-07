"""Diário de conversa e resumo da semana (DESIGN.md, seção 20.7).

O que este recurso faz, sempre com o que foi registrado (nunca inventa):

- **Depois de uma conversa** (a tela é `web/telas/semana.js`, rota `#conversa`): um formulário curto
  (onde foi, o que ela contou, como ela estava, assuntos importantes, o que preciso lembrar) que
  `montar_conversa` transforma em UM texto + `contexto` e `humor`. O texto vai para o Registrar e
  passa pela revisão normal, analisado pelas regras como sempre: nada é gravado antes de revisar.
  O que ela contou entra como foi escrito; "como ela estava" vira "Ela estava cansada."; os
  assuntos viram "Assuntos importantes: ..."; cada coisa a lembrar vira "Lembrar: ...".
- **Lê o texto** (`extrair`), em qualquer registro, dois extras para a revisão (trecho literal):
  * `conversa_assuntos`: "Assuntos importantes: trabalho, a viagem e a irmã dela." e frases como
    "conversamos sobre a viagem e a mudança" -> os assuntos, cada um escrito no trecho;
  * `conversa_lembrete`: "Lembrar: perguntar do exame na sexta.", "preciso lembrar de comprar a
    ração", "não posso esquecer do aniversário da mãe dela dia 12", "ela pediu pra eu lembrar ela
    de tomar o remédio" -> um lembrete SEU, com a data que o Python achou no trecho. Fato sobre ela
    ("lembrar que ela não gosta de cebola") não vira lembrete: as regras do app já guardam como
    preferência. Negação ("não preciso lembrar"), pergunta e ironia ficam de fora.
- **Resumo da semana** (`resumo`), calculado na hora e nunca guardado (apagar um registro tira
  tudo dele do resumo): a semana termina no dia escolhido em Ajustes (padrão domingo) e se chama
  pela semana ISO desse dia (`2026-W40`). Traz o que ela contou por categoria (citando cada
  trecho), destaques com pontos explicáveis (o que ela mesma disse, o que é importante, o que tem
  data, o que ficou em aberto, o assunto que voltou), como ela estava (as palavras registradas,
  sem diagnóstico), as conversas e os assuntos (e em quantas semanas antes eles já tinham
  aparecido), o que ficou em aberto (pendências, promessas e lembretes), os próximos 7 dias, até
  três "Talvez..." tirados do que está registrado e a pergunta sugerida (do recurso Perguntas, sem
  gravar nada). **Sensível ao momento dela:** com um estado difícil dela registrado nos últimos 3
  dias, o primeiro "Talvez" é perguntar como ela está, com o que você registrou que a ajuda.
- **Hoje** (`cartoes_hoje`): o cartão do resumo no dia do resumo e nos dois seguintes (só se a
  semana tem pelo menos 2 registros: nada genérico, e um registro só já está em "Últimos
  registros"); os seus lembretes de hoje e de amanhã; e, com carinho, um lembrete que passou há
  poucos dias e continua em aberto.

Privacidade: nada daqui vai para a IA (a IA só recebe itens e registros; os extras nunca). O resumo
e o Hoje deixam de fora o que fala do ciclo dela (palavras como menstruação, TPM e cólica, e os
trechos que o recurso Ciclo guardou): o Hoje nunca explica nada pelo ciclo. Log só com o tipo do erro.

Tabelas (as colunas são contrato; outros recursos, como a área Eu, podem ler):
- `semana_lembretes(id, texto, trecho, data, precisao, data_expressao, status: aberto|feito|
  descartado, registro_id, criado_em, concluido_em)`;
- `semana_assuntos(id, assunto, chave, trecho, registro_id, criado_em)` (`chave`: o assunto
  normalizado, sem artigo e sem "dela", para juntar "o trabalho dela" com "trabalho").

Ajustes (na config do diário, Ajustes → 🗓️ Resumo da semana): `semana_dia` ("domingo"...) e
`semana_no_hoje` (o cartão do resumo no Hoje; os lembretes aparecem sempre).

Roda no navegador também (Pyodide): nada de thread, sleep, rede ou Windows; as datas saem de
regras/datas.py e do Python.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass

from datetime import date, datetime, timedelta
from typing import Any

from ..banco import NaoEncontrado
from ..modelos import CATEGORIAS
from ..regras.datas import extrair_datas
from ..regras.pessoas import plano
from ..texto import frases, normalizar
from . import cartao, registrar_erro, rota
from . import discretos as _discretos_dos_recursos
from . import recurso as recurso_por_nome
from . import texto_reservado as _reservado


NOME = "semana"
ROTULO = "Resumo da semana"
TIPOS_EXTRAS = ("conversa_assuntos", "conversa_lembrete")

T_LEMBRETES = "semana_lembretes"
T_ASSUNTOS = "semana_assuntos"

ESQUEMA = [
    f"""
    CREATE TABLE {T_LEMBRETES} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        texto TEXT NOT NULL,
        trecho TEXT NOT NULL,
        data TEXT,
        precisao TEXT,
        data_expressao TEXT,
        status TEXT NOT NULL DEFAULT 'aberto' CHECK (status IN ('aberto', 'feito', 'descartado')),
        registro_id INTEGER,
        criado_em TEXT NOT NULL,
        concluido_em TEXT
    );
    CREATE INDEX {T_LEMBRETES}_status ON {T_LEMBRETES}(status);
    CREATE INDEX {T_LEMBRETES}_registro ON {T_LEMBRETES}(registro_id);
    CREATE TABLE {T_ASSUNTOS} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assunto TEXT NOT NULL,
        chave TEXT NOT NULL,
        trecho TEXT NOT NULL,
        registro_id INTEGER NOT NULL,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX {T_ASSUNTOS}_registro ON {T_ASSUNTOS}(registro_id);
    CREATE INDEX {T_ASSUNTOS}_chave ON {T_ASSUNTOS}(chave)
    """,
]
ESQUEMA_VERSAO = len(ESQUEMA)

STATUS_LEMBRETE = ("aberto", "feito", "descartado")
PRECISOES = ("dia", "semana", "mes", "ano")

# Ajustes (config do diário).
CHAVE_DIA = "semana_dia"
CHAVE_NO_HOJE = "semana_no_hoje"
DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
DIA_PADRAO = 6                       # domingo (date.weekday())

DIAS_CARTAO = 2                      # o cartão do resumo fica do dia do resumo até 2 dias depois
MIN_REGISTROS_CARTAO = 2             # um registro só não é resumo: ele já está em "Últimos registros"
DIAS_PROXIMOS = 7
DIAS_PENDENCIA = 90                  # pendência mais velha que isso sai de "em aberto" (como no Hoje)
DIAS_SENSIVEL = 3                    # estado difícil dela nos últimos 3 dias: cuidado primeiro
DIAS_ATRASO = 7                      # lembrete que passou: lembra com carinho até aqui
SEMANAS_ANTES = 8                    # "o assunto já tinha aparecido em 3 das 8 semanas antes"
MAX_ASSUNTOS = 8                     # por frase
MAX_ASSUNTO = 60                     # letras de um assunto
MAX_TEXTO = 300                      # letras de um lembrete
MAX_CONTOU = 20000
MAX_CAMPO = 2000
MAX_ONDE = 200
MAX_HUMOR = 80
MAX_POR_CATEGORIA = 12
MAX_DESTAQUES = 5
MAX_SUGESTOES = 3
MAX_HISTORICO = 104

RELEVANCIA_RESUMO = 72               # no dia do resumo; 6 a menos a cada dia depois
RELEVANCIA_HOJE = 82
RELEVANCIA_AMANHA = 76
RELEVANCIA_ATRASADO = 48

SEMANA_INVALIDA = "A semana precisa vir como AAAA-Www (por exemplo, 2026-W40)."
DATA_INVALIDA = "A data precisa estar no formato AAAA-MM-DD (ou ficar vazia)."
CONVERSA_VAZIA = "Conte pelo menos um pouco da conversa antes de organizar."
CAMPO_INVALIDO = "Cada campo da conversa precisa ser um texto."
CAMPO_GRANDE = "O campo “{campo}” ficou grande demais (até {limite} letras)."
TEXTO_INVALIDO = f"O lembrete precisa de um texto (até {MAX_TEXTO} letras)."
STATUS_INVALIDO = "A situação do lembrete precisa ser “aberto”, “feito” ou “descartado”."
PRECISAO_INVALIDA = "A precisão da data precisa ser dia, semana, mês ou ano."
CAMPO_DESCONHECIDO = "O lembrete não tem esse campo: {campo}."
NADA_PARA_MUDAR = "Nada para mudar no lembrete."
LIMITE_INVALIDO = "O limite precisa ser um número inteiro."
FORMATO_IMPORTAR = "Os dados do resumo da semana vieram num formato estranho."
LEMBRETE_SUMIU = "Não encontrei esse lembrete. Talvez já tenha sido apagado."

AVISO_SENSIVEL_HOJE = "um lembrete que você anotou."

_DIAS_NORMAIS = {normalizar(d): i for i, d in enumerate(DIAS)}
_CATEGORIA_ORDEM = {c: i for i, c in enumerate(CATEGORIAS)}
# Categorias que dizem "que tipo de coisa" e não "sobre o quê": o assunto vem da outra.
_GENERICAS = frozenset({"Eventos futuros", "Situações pendentes", "Conversas importantes", "Preferências",
                        "Datas importantes", "Coisas que deixam feliz", "Coisas que incomodam", "Preocupações"})
OUTRAS = "Outras coisas"

# O que fala do ciclo dela não entra no resumo nem no Hoje (seção 20.8: o Hoje nunca usa essas
# palavras e nada explica humor pelo ciclo). Conferido no texto normalizado.
_RE_SENSIVEL = re.compile(r"menstru|\bciclo\b|\btpm\b|\bcolicas?\b|absorvente|coletor\s+menstrual|ovula|"
                          r"periodo\s+fertil|anticoncepcion|\bpilula\b|\bsangr")


# ================================================================ pequenos ajudantes

def _d(valor: Any) -> date | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _json_lista(valor: Any) -> list[str]:
    try:
        lista = json.loads(valor or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in lista if isinstance(c, str)] if isinstance(lista, list) else []


def _encurtar(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def citar(trecho: str, n: int = 140) -> str:
    """'“Ela estava cansada.”' (o trecho literal, encurtado se for longo)."""
    return f"“{_encurtar(trecho, n)}”"


def frase(s: str) -> str:
    """Fecha a frase com ponto, a não ser que ela já termine numa citação com pontuação."""
    s = s.rstrip()
    return s if re.search(r"[.!?…]”?$", s) else s + "."


def data_dita(d: date, precisao: str | None) -> str:
    """A data com a precisão com que foi dita, sem ano: "06/10", "semana de 05/10", "maio de 2027"."""
    if precisao == "mes":
        return f"{_MESES[d.month - 1]} de {d.year}"
    if precisao == "semana":
        return f"semana de {d:%d/%m}"
    if precisao == "ano":
        return str(d.year)
    return f"{d:%d/%m}"


def quando(d: date, referencia: date) -> str:
    """'hoje', 'ontem', 'anteontem', 'na terça (29/09)', 'amanhã', 'sexta (02/10)', 'em 12/10'."""
    dias = (d - referencia).days
    if dias == 0:
        return "hoje"
    if dias == -1:
        return "ontem"
    if dias == 1:
        return "amanhã"
    if dias == -2:
        return "anteontem"
    nome = DIAS[d.weekday()]
    if -6 <= dias < 0:
        return f"{'no' if d.weekday() >= 5 else 'na'} {nome} ({d:%d/%m})"
    if 0 < dias <= 6:
        return f"{nome} ({d:%d/%m})"
    return f"em {d:%d/%m}" if d.year == referencia.year else f"em {d:%d/%m/%Y}"


_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro")


def quando_data(d: date, precisao: str | None, referencia: date) -> str:
    """A data com a precisão que foi dita: "na semana de 05/10", "em dezembro", "em 2027"."""
    if precisao == "semana":
        return f"na semana de {d:%d/%m}"
    if precisao == "mes":
        return f"em {_MESES[d.month - 1]}" + (f" de {d.year}" if d.year != referencia.year else "")
    if precisao == "ano":
        return f"em {d.year}"
    return quando(d, referencia)


def chave_assunto(assunto: str) -> str:
    """'o trabalho dela' -> 'trabalho'; 'a viagem pra praia' -> 'viagem pra praia'."""
    s = normalizar(assunto)
    s = re.sub(r"^(?:(?:sobre|de|do|da|dos|das|no|na|nos|nas|o|a|os|as|um|uma|uns|umas)\s+)+", "", s)
    s = re.sub(r"\s+(?:dela|dele|delas|deles)$", "", s)
    return re.sub(r"[^\w\s-]", "", s).strip()


def sensivel(*textos: str | None) -> bool:
    """O texto fala do ciclo dela? (fica fora do resumo e do Hoje)."""
    return any(t and _RE_SENSIVEL.search(normalizar(t)) for t in textos)


def _ironia(frase: str) -> bool:
    return bool(re.search(r"\bso\s+que\s+nao\b|\bsqn\b", normalizar(frase)))


def _limpar_fim(s: str) -> str:
    """Tira do fim pontuação, risada e espaço ("kkk", "rs", "haha")."""
    anterior = None
    while anterior != s:
        anterior = s
        s = s.rstrip(" \t\n.,;:!…-–—")
        s = re.sub(r"(?i)\s+(?:k{2,}|rs+|ha(?:ha)+h?|he(?:he)+|hu(?:hu)+)$", "", s)
    return s


# ================================================================ a semana

@dataclass(frozen=True)
class Periodo:
    """Os 7 dias que terminam no dia do resumo. O nome é a semana ISO do último dia."""

    inicio: date
    fim: date

    @property
    def id(self) -> str:
        ano, semana, _ = self.fim.isocalendar()
        return f"{ano}-W{semana:02d}"

    def contem(self, d: date | None) -> bool:
        return d is not None and self.inicio <= d <= self.fim

    def anterior(self) -> Periodo:
        return Periodo(self.inicio - timedelta(days=7), self.fim - timedelta(days=7))

    def seguinte(self) -> Periodo:
        return Periodo(self.inicio + timedelta(days=7), self.fim + timedelta(days=7))


def dia_do_resumo(config: dict | None) -> int:
    """O dia da semana do resumo (0 = segunda ... 6 = domingo). Ajuste estranho = domingo."""
    valor = (config or {}).get(CHAVE_DIA)
    if isinstance(valor, str):
        return _DIAS_NORMAIS.get(normalizar(valor).replace("-feira", ""), DIA_PADRAO)
    if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= 6:
        return valor
    return DIA_PADRAO


def no_hoje(config: dict | None) -> bool:
    valor = (config or {}).get(CHAVE_NO_HOJE)
    return valor if isinstance(valor, bool) else True


def periodo_de(d: date, dia: int = DIA_PADRAO) -> Periodo:
    """A semana que contém `d` (termina no próximo dia do resumo, ou no próprio `d`)."""
    fim = d + timedelta(days=(dia - d.weekday()) % 7)
    return Periodo(fim - timedelta(days=6), fim)


def periodo_por_id(texto: Any, dia: int = DIA_PADRAO) -> Periodo:
    """'2026-W40' -> a semana que termina no dia do resumo dessa semana ISO. ValueError se não der."""
    m = re.fullmatch(r"\s*(\d{4})-[wW](\d{1,2})\s*", texto) if isinstance(texto, str) else None
    if not m:
        raise ValueError(SEMANA_INVALIDA)
    try:
        fim = date.fromisocalendar(int(m.group(1)), int(m.group(2)), dia + 1)
    except ValueError:
        raise ValueError(SEMANA_INVALIDA) from None
    return Periodo(fim - timedelta(days=6), fim)


def periodo_do_cartao(referencia: date, dia: int = DIA_PADRAO) -> Periodo | None:
    """A semana cujo resumo o Hoje mostra: a que terminou hoje ou até 2 dias atrás."""
    fim = referencia - timedelta(days=(referencia.weekday() - dia) % 7)
    if (referencia - fim).days > DIAS_CARTAO:
        return None
    return Periodo(fim - timedelta(days=6), fim)


def periodo_padrao(referencia: date, dia: int = DIA_PADRAO) -> Periodo:
    """O que a tela abre: o resumo que acabou de fechar (até 2 dias depois), senão a semana de agora."""
    return periodo_do_cartao(referencia, dia) or periodo_de(referencia, dia)


# ================================================================ depois de uma conversa

# Primeira palavra de "como ela estava" que já é verbo (a frase vira "Ela <isso>.").
_RE_HUMOR_VERBO = re.compile(
    r"ela|ta|esta|estava|tava|anda|andava|parecia|parece|fica|ficou|ficava|chegou|chegava|disse|dizia|falou|"
    r"falava|contou|comentou|me|se|sentia|sente|achou|acordou|veio|voltou|saiu|chorou|chorava|riu|sorriu|"
    r"reclamou|reclamava|desabafou|brincou|dormiu|gostou|amou|adorou|odiou|curtiu|passou|teve|tinha|queria|"
    r"quis|pediu|mandou|ligou|perguntou|respondeu|reagiu|continua|continuava|seguia|segue|[^\W\d_]{2,}ou")
_RE_BULLET = re.compile(r"^\s*(?:[-*•·]+|\d{1,2}[.)-])\s*")
_RE_LEMBRAR_INICIO = re.compile(r"^(?:lembrar|lembrete|preciso|precisava|tenho\s+(?:que|q|de)|nao\s+esquecer|"
                                r"n\s+esquecer|nao\s+posso\s+esquecer|nao\s+esquece|devo|vou\s+ter\s+que)\b")


def _texto_do_campo(campos: dict, nome: str, limite: int, rotulo: str) -> str:
    valor = campos.get(nome)
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ValueError(CAMPO_INVALIDO)
    valor = valor.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(valor) > limite:
        raise ValueError(CAMPO_GRANDE.format(campo=rotulo, limite=limite))
    return valor


def frase_do_humor(humor: str) -> str:
    """'cansada' -> 'Ela estava cansada.'; 'tava feliz' -> 'Ela tava feliz.'; 'ela chorou' -> 'Ela chorou.'"""
    h = _limpar_fim(re.sub(r"\s+", " ", humor or "").strip())
    if not h:
        return ""
    primeira = plano(h).split(" ", 1)[0]
    if primeira == "ela":
        return _cap(h) + "."
    if _RE_HUMOR_VERBO.fullmatch(primeira):
        return f"Ela {h}."
    return f"Ela estava {h}."


def lista_de_assuntos(assuntos: str) -> str:
    """Uma linha por assunto (ou separados por ";") -> "a, b e c"; com vírgulas, fica como foi escrito."""
    partes = [_limpar_fim(_RE_BULLET.sub("", p)) for p in re.split(r"\s*(?:\n|;)\s*", assuntos or "")]
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return partes[0] if partes else ""
    return ", ".join(partes[:-1]) + " e " + partes[-1]


def frases_de_lembrar(lembrar: str) -> list[str]:
    """Cada linha vira "Lembrar: <linha>." (fica como está se já diz "preciso lembrar...")."""
    saida = []
    for linha in re.split(r"\s*(?:\n|;)\s*", lembrar or ""):
        linha = _limpar_fim(_RE_BULLET.sub("", linha).strip())
        if not linha:
            continue
        if _RE_LEMBRAR_INICIO.match(plano(linha)):
            saida.append(_cap(linha) + ".")
        else:
            saida.append(f"Lembrar: {linha}.")
    return saida


def montar_conversa(campos: Any) -> dict:
    """O formulário "Depois de uma conversa" -> {texto, contexto, humor}.

    `campos`: {contou, humor, assuntos, lembrar, onde} (textos; todos opcionais, mas algum precisa
    ter conteúdo). Nada é inventado: o texto é o que foi escrito, em frases que as regras entendem.
    ValueError (400) com a mensagem para a tela."""
    if not isinstance(campos, dict):
        raise ValueError(CAMPO_INVALIDO)
    contou = _texto_do_campo(campos, "contou", MAX_CONTOU, "O que ela contou")
    humor = re.sub(r"\s+", " ", _texto_do_campo(campos, "humor", MAX_CAMPO, "Como ela estava"))
    assuntos = _texto_do_campo(campos, "assuntos", MAX_CAMPO, "Assuntos importantes")
    lembrar = _texto_do_campo(campos, "lembrar", MAX_CAMPO, "O que preciso lembrar")
    onde = re.sub(r"\s+", " ", _texto_do_campo(campos, "onde", MAX_ONDE, "Onde ou como foi"))
    blocos: list[str] = []
    if contou:
        blocos.append(contou)
    linhas = []
    frase = frase_do_humor(humor) if humor else ""
    if frase:
        linhas.append(frase)
    lista = lista_de_assuntos(assuntos) if assuntos else ""
    if lista:
        linhas.append(f"Assuntos importantes: {lista}.")
    linhas.extend(frases_de_lembrar(lembrar) if lembrar else [])
    if linhas:
        blocos.append("\n".join(linhas))
    if not blocos:
        raise ValueError(CONVERSA_VAZIA)
    humor_curto = _limpar_fim(humor)
    if len(humor_curto) > MAX_HUMOR:
        humor_curto = humor_curto[: MAX_HUMOR - 1].rsplit(" ", 1)[0].rstrip(" ,;") + "…"
    return {"texto": "\n\n".join(blocos), "contexto": onde or None, "humor": humor_curto or None}


# ================================================================ extração (revisão)

_PREP = r"(?:de|do|da|dos|das|que|q)"
_RE_MARCA_ASSUNTOS = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:os\s+)?assuntos?(?:\s+(?:importantes?|principais|da\s+conversa|do\s+papo))?\s*:\s*")
_RE_MARCA_LEMBRAR = re.compile(r"^\s*(?:[-*•]\s*)?(?:lembrar|lembrete|lembretes)\s*:")
_RE_CONVERSAMOS = re.compile(
    r"(?<![\w])(?P<sujeito>(?:a\s+gente|nos|eu\s+e\s+ela|ela\s+e\s+eu|ela)\s+(?:(?:ja|tambem|so|ainda)\s+)?)?"
    r"(?P<verbo>conversamos|conversou|conversaram|falamos|falou|falaram|batemos\s+(?:um\s+)?papo|bateu\s+(?:um\s+)?papo|"
    r"papeamos|discutimos|trocamos\s+(?:uma\s+)?ideia)"
    r"(?:\s+(?:muito|mt|mto|mta|mo|bastante|demais|um\s+pouco|um\s+monte|um\s+tempao|pra\s+caramba|horas|de\s+novo|"
    r"tambem|mais|longamente|ontem|hoje))*"
    r"\s+(?:sobre|a\s+respeito\s+d[eoa]s?|de|do|da|dos|das)\s+")
_RE_ASSUNTO_FOI = re.compile(r"(?<![\w])o\s+(?:assunto|papo|tema)(?:\s+principal)?(?:\s+da\s+conversa)?\s+(?:foi|era)"
                             r"(?:\s+sobre)?\s+")
_RE_NEGACAO_ANTES = re.compile(r"(?:\bnao|\bn|\bnem|\bnunca|\bjamais)(?:\s+\w+){0,2}\s*$")
# Onde a lista de assuntos de uma frase acaba (outra oração começa).
_RE_FIM_LISTA = re.compile(
    r"\s*[,;]\s*(?:que|q|mas|porque|pq|pois|quando|e\s+(?:ela|eu|ele|a\s+gente|depois|entao|ai))\b"
    r"|\s+(?:mas|porem|porque|pq|pois|quando|enquanto|ate\s+que|que|q)\b"
    r"|\s+e\s+(?:ela|eu|ele|a\s+gente|depois|entao|no\s+fim|ai|preciso|tenho|vou|quero|combinamos|fiquei|prometi)\b"
    r"|\s+e\s+(?:foi|era|ficou|ficamos|deu|acabou|terminou|rolou|fomos|estava|tava)\b"
    r"|\s*;|[.!?…]|\s*\(|\s+-\s")
# Pedaço que é outra oração, não um assunto ("ela disse que...", "foi tenso").
_RE_NAO_ASSUNTO = re.compile(r"^(?:(?:ela|eu|ele|a\s+gente|nos)\s+(?:disse|falou|contou|achou|acha|ficou|estava|tava|ta|"
                             r"esta|pediu|quer|queria)|foi|era|ficou)(?![\w])")
_RE_SEPARA = re.compile(r"\s*[,;]\s*(?:e\s+)?(?:sobre\s+)?|\s+e\s+(?:tambem\s+)?(?:sobre\s+)?|\s+alem\s+d[eoa]s?\s+")
_RE_PREP_INICIO = re.compile(r"^(?:sobre|de|do|da|dos|das)\s+")
_VAZIOS = frozenset(normalizar(x) for x in (
    "tudo", "nada", "isso", "isto", "aquilo", "coisas", "coisa", "varias coisas", "vários assuntos", "a vida",
    "vida", "bobeira", "bobeiras", "besteira", "besteiras", "tudo um pouco", "um pouco de tudo", "assunto",
    "assuntos", "muita coisa", "muitas coisas", "tanta coisa", "manha", "tarde", "noite", "madrugada", "novo",
    "hoje", "ontem", "amanha", "ela", "eu", "nos", "a gente", "mim", "voce", "tudo e nada", "nada demais",
    "nada de mais", "qualquer coisa", "o de sempre", "de sempre", "repente", "longe", "perto", "boa", "bem"))


def _itens_da_lista(frase: str, ini: int, fim: int) -> list[str]:
    """Os assuntos escritos em frase[ini:fim], cada um um pedaço literal da frase."""
    p = plano(frase)
    saida: list[str] = []
    vistos: set[str] = set()
    pos = ini
    for m in [*_RE_SEPARA.finditer(p, ini, fim), None]:
        a, b = pos, (m.start() if m else fim)
        pos = m.end() if m else fim
        pedaco = frase[a:b]
        pref = _RE_PREP_INICIO.match(plano(pedaco))
        if pref:
            pedaco = pedaco[pref.end():]
        pedaco = _limpar_fim(pedaco.strip(" \"'“”‘’")).strip(" \"'“”‘’")
        chave = chave_assunto(pedaco)
        if (not pedaco or len(pedaco) > MAX_ASSUNTO or not re.search(r"[^\W\d_]", pedaco)
                or normalizar(pedaco) in _VAZIOS or not chave or chave in _VAZIOS or chave in vistos
                or _RE_NAO_ASSUNTO.match(plano(pedaco))):
            continue

        vistos.add(chave)
        saida.append(pedaco)
        if len(saida) >= MAX_ASSUNTOS:
            break
    return saida


def assuntos_da_frase(frase: str) -> list[str]:
    """Os assuntos de uma frase ("Assuntos importantes: a, b e c" ou "conversamos sobre a e b")."""
    if _ironia(frase) or frase.rstrip().endswith("?"):
        return []
    p = plano(frase)
    m = _RE_MARCA_ASSUNTOS.match(p)
    if m:
        return _itens_da_lista(frase, m.end(), max(len(_limpar_fim(frase)), m.end()))
    for m in [*_RE_CONVERSAMOS.finditer(p), *_RE_ASSUNTO_FOI.finditer(p)]:
        grupos = m.groupdict()
        verbo = grupos.get("verbo") or ""
        sujeito = re.sub(r"\s+", " ", (grupos.get("sujeito") or "").strip())
        sujeito = re.sub(r"\s+(?:ja|tambem|so|ainda)$", "", sujeito)
        if verbo.startswith(("falou", "conversou", "bateu")) and not sujeito:
            continue                      # "a mãe dela falou sobre...": só vale "ela"/"a gente"
        if verbo.startswith(("falaram", "conversaram")) and sujeito not in ("ela e eu", "eu e ela"):
            continue
        inicio = m.start("sujeito") if grupos.get("sujeito") else (m.start("verbo") if verbo else m.start())
        if _RE_NEGACAO_ANTES.search(p[:inicio]):
            continue                      # "não conversamos sobre...", "nem falamos de..."
        fim_lista = _RE_FIM_LISTA.search(p, m.end())
        itens = _itens_da_lista(frase, m.end(), fim_lista.start() if fim_lista else len(frase))
        if itens:
            return itens
    return []


_RE_LEMBRAR = re.compile(
    r"(?P<gatilho>"
    r"^\s*(?:[-*•]\s*)?(?:obs\s*:\s*)?(?:lembrar|lembrete|lembretes|nao\s+esquecer|n\s+esquecer)\s*(?::|-|–)"
    r"|(?:^|(?<=[\s,;(:]))(?:eu\s+)?(?:preciso|precisava|tenho\s+(?:que|q|de)|vou\s+ter\s+(?:que|q|de)|devo)\s+"
    r"(?:me\s+)?lembrar\b(?:\s+(?P<p1>" + _PREP + r"))?(?=[\s:,-])"
    r"|(?:^|(?<=[\s,;(:]))(?:eu\s+)?(?:nao|n)\s+(?:posso|vou|pode|devo)\s+(?:me\s+)?esquecer\b"
    r"(?:\s+(?P<p2>" + _PREP + r"))?(?=[\s:,-])"
    r"|^\s*(?:[-*•]\s*)?(?:e\s+|ah,?\s+|obs\s*:\s*)?(?:nao|n)\s+(?:esquecer|esquece|esqueca)\b"
    r"(?:\s+(?P<p3>" + _PREP + r"))?(?=[\s:,-])"
    r"|^\s*(?:[-*•]\s*)?(?:me\s+)?lembrar\s+(?P<p4>" + _PREP + r")(?=\s)"
    r"|(?:^|(?<=[\s,;(:]))pediu\s+(?:pra|para|p/)\s+(?:eu\s+)?(?:te\s+|lhe\s+)?lembrar(?P<ela>\s+ela)?\b"
    r"(?:\s+(?P<p5>" + _PREP + r"))?(?=[\s:,-])"
    r")[\s:,–-]*(?P<resto>[^\s:,–-].*)$")
_RE_FATO = re.compile(r"^(?:ela|ele|dela|dele|elas|eles)\b")
_RE_QUE_TAREFA = re.compile(r"^(?:eu\s+)?(?:tenho\s+(?:que|q|de)|preciso|vou|devo)\s+")
_RE_PREP_DATA = re.compile(r"(?:\s*,)?\s*\b(?:na|no|nessa|nesse|nesta|neste|em|dia|ate|pra|para|la\s+pra|"
                           r"antes\s+d[eoa])\s*$")


def _datas_da_frase(datas: list, ini: int, fim: int) -> list:
    saida = []
    for d in datas or []:
        inicio, final = getattr(d, "inicio", None), getattr(d, "fim", None)
        if isinstance(inicio, int) and isinstance(final, int) and ini <= inicio and final <= fim:
            saida.append(d)
    return saida


def _valor(v: Any) -> Any:
    return getattr(v, "value", v)


_RE_DIA_DA_SEMANA = re.compile(r"(?<![\w])(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?![\w])")
_RE_SEMANA_PASSADA = re.compile(r"(?<![\w])(?:passad[ao]|retrasad[ao]|ultim[ao])(?![\w])")


def _proximo_dia_da_semana(d: Any, referencia: date) -> date | None:
    """"sexta" lida no passado -> a próxima sexta (hoje conta). Só dia da semana sem "passada"."""
    expressao = plano(str(getattr(d, "expressao", "") or ""))
    data = getattr(d, "data", None)
    if (not isinstance(data, date) or _valor(getattr(d, "precisao", "dia")) not in (None, "dia")
            or not _RE_DIA_DA_SEMANA.search(expressao) or _RE_SEMANA_PASSADA.search(expressao)):
        return None
    while data < referencia:
        data += timedelta(days=7)
    return data


def lembrete_da_frase(frase: str, ini: int, datas: list, referencia: date) -> dict | None:
    """Um lembrete seu na frase ("Lembrar: ...", "preciso lembrar de ...", "não posso esquecer ...").
    `ini`: a posição da frase no texto (as datas vêm com as posições do texto inteiro)."""
    if _ironia(frase) or frase.rstrip().endswith("?"):
        return None
    p = plano(frase)
    m = _RE_LEMBRAR.search(p)
    if not m:
        return None
    gatilho = p[m.start("gatilho"):m.end("gatilho")]
    if _RE_NEGACAO_ANTES.search(p[:m.start("gatilho")]) and not re.match(r"\s*(?:[-*•]\s*)?(?:\w+\s+)?(?:nao|n)\b",
                                                                         gatilho):
        return None                       # "não preciso lembrar de...", "ela não pediu pra eu lembrar"
    prep = next((m.group(g) for g in ("p1", "p2", "p3", "p4", "p5") if m.group(g)), None)
    r_ini, r_fim = m.start("resto"), len(_limpar_fim(frase))
    if r_fim <= r_ini:
        return None
    que = re.match(r"(?:que|q)\s+", p[r_ini:r_fim])
    if prep in ("que", "q") or que:
        base = r_ini + (que.end() if que else 0)
        tarefa = _RE_QUE_TAREFA.match(p[base:r_fim])
        if not tarefa:
            return None                   # "lembrar que ela não gosta de cebola": é fato, não tarefa
        r_ini = base + tarefa.end()
    resto_p = p[r_ini:r_fim]
    if _RE_FATO.match(resto_p):
        return None                       # "Lembrar: ela não gosta de cebola" (as regras já guardam)
    if not re.search(r"[^\W\d_]{2,}", resto_p):
        return None
    bruto = frase[r_ini:r_fim]
    texto = bruto.strip()
    # A data: a primeira que o Python achou na frase e que ainda não passou. Lembrete é sempre para a
    # frente: um dia da semana que as regras leram no passado por causa de um "como foi" depois
    # ("perguntar na sexta como foi a conversa") é a próxima sexta (revisão de 07/10).
    escolhida = None
    for d in _datas_da_frase(datas, ini, ini + len(frase)):
        data = getattr(d, "data", None)
        if not isinstance(data, date):
            continue
        if data < referencia or getattr(d, "passado", False):
            proxima = _proximo_dia_da_semana(d, referencia)
            if proxima is None:
                continue
            d = dataclasses.replace(d, data=proxima, passado=False, ambigua=False, alternativas=[proxima])
        escolhida = d
        break
    data_iso = precisao = expressao = None
    alternativas: list[str] = []
    if escolhida is not None:
        data_iso = escolhida.data.isoformat()
        precisao = _valor(getattr(escolhida, "precisao", "dia")) or "dia"
        expressao = getattr(escolhida, "expressao", None)
        if getattr(escolhida, "ambigua", False):
            alternativas = [a.isoformat() for a in getattr(escolhida, "alternativas", []) if isinstance(a, date)]
        # Tira do texto a expressão da data (e o "na"/"no"/"dia" antes dela): "perguntar do exame".
        a, b = escolhida.inicio - ini - r_ini, escolhida.fim - ini - r_ini
        if 0 <= a < b <= len(bruto):
            antes = bruto[:a]
            pm = _RE_PREP_DATA.search(plano(antes))
            if pm:
                antes = antes[:pm.start()]
            sem_data = _limpar_fim(re.sub(r"\s+", " ", (antes.rstrip(" ,") + " " + bruto[b:].lstrip(" ,")).strip()))
            if re.search(r"[^\W\d_]{2,}", sem_data):
                texto = sem_data
    texto = _limpar_fim(texto)
    if m.group("ela"):
        texto = f"lembrar ela {prep} {texto}" if prep and prep not in ("que", "q") else f"lembrar ela: {texto}"
    texto = _cap(_encurtar(texto, MAX_TEXTO))
    resumo = f"{texto} — {quando_data(_d(data_iso), precisao, referencia)}" if data_iso else texto
    extra = {"tipo": "conversa_lembrete", "resumo": resumo, "texto": texto, "data": data_iso, "precisao": precisao,
             "data_expressao": expressao, "pedido_dela": "pediu" in gatilho}
    if len(alternativas) > 1:
        extra["data_alternativas"] = alternativas
    return extra


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Os extras da revisão: assuntos da conversa e lembretes seus (cada um com o trecho literal)."""
    saida: list[dict] = []
    for ini, _fim, frase in frases(texto or ""):
        assuntos = assuntos_da_frase(frase)
        if assuntos:
            saida.append({"tipo": "conversa_assuntos", "resumo": "Assuntos da conversa: " + ", ".join(assuntos),
                          "trecho": frase, "assuntos": assuntos})
        lembrete = lembrete_da_frase(frase, ini, datas, referencia)
        if lembrete:
            lembrete["trecho"] = frase
            saida.append(lembrete)
    return saida


# ================================================================ gravação

def _texto_registro(banco: Any, registro_id: int) -> tuple[str, date | None] | None:
    linhas = banco.consultar("SELECT texto, data_registro FROM registros WHERE id = ?", (registro_id,))
    return (linhas[0]["texto"], _d(linhas[0]["data_registro"])) if linhas else None


def _expressao_da_data(trecho: str, expressao: Any, data: date | None, referencia: date | None) -> str | None:
    """A expressão escrita ("na sexta") só fica se o Python, lendo o trecho, chega à mesma data (a data
    mudada na revisão deixa de ser "sexta"). Nunca uma expressão que não está escrita."""
    if data is None or referencia is None or not isinstance(expressao, str) or not expressao.strip()             or expressao not in trecho:
        return None
    for d in extrair_datas(trecho, referencia):
        if d.expressao == expressao and (d.data == data or data in d.alternativas):
            return expressao
    return None


def _data_valida(valor: Any) -> date | None:
    if valor in (None, ""):
        return None
    if not isinstance(valor, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        raise ValueError(DATA_INVALIDA)
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValueError(DATA_INVALIDA) from None


def _texto_lembrete(valor: Any) -> str:
    if not isinstance(valor, str):
        raise ValueError(TEXTO_INVALIDO)
    valor = re.sub(r"\s+", " ", valor).strip()
    if not valor or len(valor) > MAX_TEXTO:
        raise ValueError(TEXTO_INVALIDO)
    return _cap(valor)


def _gravar_assuntos(banco: Any, registro_id: int, extra: dict) -> None:
    trecho = extra["trecho"]
    assuntos = extra.get("assuntos")
    if not isinstance(assuntos, list):
        assuntos = assuntos_da_frase(trecho)        # a tela não mandou a lista: lê de novo do trecho
    ja = {l["chave"] for l in banco.consultar(f"SELECT chave FROM {T_ASSUNTOS} WHERE registro_id = ?", (registro_id,))}
    for assunto in assuntos:
        if not isinstance(assunto, str):
            continue
        assunto = assunto.strip()
        chave = chave_assunto(assunto)
        # Nunca inventar: só assunto escrito no trecho.
        if not assunto or len(assunto) > MAX_ASSUNTO or assunto not in trecho or not chave or chave in ja:
            continue
        ja.add(chave)
        banco.inserir(T_ASSUNTOS, {"assunto": assunto, "chave": chave, "trecho": trecho, "registro_id": registro_id,
                                   "criado_em": _agora()})


def _gravar_lembrete(banco: Any, registro_id: int, extra: dict, referencia: date) -> None:
    trecho = extra["trecho"]
    texto = extra.get("texto")
    texto = _texto_lembrete(texto if isinstance(texto, str) and texto.strip() else extra.get("resumo") or trecho)
    data = _data_valida(extra.get("data"))
    precisao = extra.get("precisao") if extra.get("precisao") in PRECISOES else "dia"
    expressao = _expressao_da_data(trecho, extra.get("data_expressao"), data, referencia)
    chave = normalizar(texto)
    for l in banco.consultar(f"SELECT texto, data FROM {T_LEMBRETES} WHERE status = 'aberto'"):
        if normalizar(l["texto"]) == chave and _d(l["data"]) == data:
            return                                  # o mesmo lembrete contado de novo
    banco.inserir(T_LEMBRETES, {"texto": texto, "trecho": trecho, "data": data, "precisao": precisao if data else None,
                                "data_expressao": expressao, "status": "aberto", "registro_id": registro_id,
                                "criado_em": _agora()})
    if data is not None:
        _afastar_evento_da_mesma_frase(banco, registro_id, trecho, data)


def _afastar_evento_da_mesma_frase(banco: Any, registro_id: int, trecho: str, data: date) -> None:
    """Integração (20.10): você confirmou que a frase é um lembrete SEU ("Lembrar: perguntar dos exames da
    mãe dela na sexta"); o acontecimento que as regras tiraram da mesma frase, com a mesma data, fica
    "descartado" (continua no registro e dá para reativar). Sem isso, o Hoje avisava duas vezes da mesma
    coisa, uma delas como se fosse um acontecimento dela."""
    for it in banco.consultar("SELECT id, trecho, data_evento FROM itens WHERE registro_id = ? AND status = 'ativo'"
                              " AND tipo IN ('evento', 'pendencia')", (registro_id,)):
        if (it.get("trecho") or "").strip() == trecho.strip() and _d(it.get("data_evento")) == data:
            banco.atualizar_item(it["id"], status="descartado")


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava o que o usuário confirmou. Data ou texto inválido no lembrete: ValueError (400, nada gravado)."""
    for extra in extras:
        if extra.get("tipo") == "conversa_assuntos":
            _gravar_assuntos(banco, registro_id, extra)
        elif extra.get("tipo") == "conversa_lembrete":
            _gravar_lembrete(banco, registro_id, extra, referencia)


# ================================================================ o que está registrado

def _tabela_existe(banco: Any, nome: str) -> bool:
    return bool(banco.consultar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (nome,)))


def _trechos_do_ciclo(banco: Any) -> dict[int, list[str]]:
    """{registro_id: [trechos normalizados]} que o recurso Ciclo guardou (se ele existe aqui)."""
    try:
        if not _tabela_existe(banco, "ciclo_eventos"):
            return {}
        saida: dict[int, list[str]] = {}
        for l in banco.consultar("SELECT registro_id, trecho FROM ciclo_eventos WHERE registro_id IS NOT NULL"):
            if isinstance(l.get("trecho"), str) and l["trecho"].strip():
                saida.setdefault(l["registro_id"], []).append(normalizar(l["trecho"]))
        return saida
    except Exception as erro:  # noqa: BLE001 - a tabela é de outro recurso: forma diferente = só as palavras
        registrar_erro(erro, NOME, "ler ciclo_eventos")
        return {}


class _Filtro:
    """O que fica fora do resumo e do Hoje: o que fala do ciclo dela (palavras e trechos do Ciclo, e
    a frase com palavras reservadas de um registro discreto, mesmo sem nada confirmado; 20.10)."""

    def __init__(self, banco: Any):
        self.ciclo = _trechos_do_ciclo(banco)
        self.discretos = _discretos_dos_recursos(banco)

    def fora(self, registro_id: Any, *textos: str | None) -> bool:
        if sensivel(*textos):
            return True
        if registro_id in self.discretos and any(_reservado(t or "") for t in textos):
            return True
        trechos = self.ciclo.get(registro_id)
        if not trechos:
            return False
        for t in textos:
            n = normalizar(t or "")
            if n and any(n in c or c in n for c in trechos):
                return True
        return False


def _dela(item: dict) -> bool:
    return item.get("pessoa_id") is None and not (item.get("pessoa") or "").strip()


def _assunto_do_item(item: dict) -> str:
    cats = item["_categorias"]
    for c in cats:
        if c not in _GENERICAS:
            return c
    return cats[0] if cats else OUTRAS


def _marcador(trecho: str) -> bool:
    """Frase montada pelo formulário da conversa (assuntos, lembrar): não é algo que ela contou."""
    p = plano(trecho or "")
    return bool(_RE_MARCA_ASSUNTOS.match(p) or _RE_MARCA_LEMBRAR.match(p))


# Frase sua, não dela: promessa ou desentendimento contado em primeira pessoa (revisão de 07/10:
# "Prometi levar ela no cinema" e "A gente discutiu..., eu fui grosso" apareciam em "O que ela contou").
_RE_FRASE_SUA = re.compile(
    r"(?<![\w])(?:prometi|combinei\s+(?:com\s+ela\s+)?de|fiquei\s+de|jurei)(?![\w])"
    r"|(?<![\w])(?:a\s+gente|nos|eu\s+e\s+ela|ela\s+e\s+eu)\s+(?:\w+\s+)?(?:brig\w+|discut\w+|desentend\w+|estranh\w+)"
    r"|(?<![\w])(?:brigamos|discutimos|nos\s+desentendemos|nos\s+estranhamos)(?![\w])")


def _frases_suas(banco: Any, ids: list[int]) -> set[tuple[Any, str]]:
    """(registro, trecho) que outros recursos guardaram como algo SEU: promessas e a área Eu."""
    if not ids:
        return set()
    marcas = ",".join("?" * len(ids))
    saida: set[tuple[Any, str]] = set()
    for tabela in ("promessas", "reparos", "atitudes", "aprendizados", "admiracoes"):
        try:
            if not _tabela_existe(banco, tabela):
                continue
            for l in banco.consultar(f"SELECT registro_id, trecho FROM {tabela} WHERE registro_id IN ({marcas})", tuple(ids)):
                if isinstance(l.get("trecho"), str) and l["trecho"].strip():
                    saida.add((l["registro_id"], normalizar(l["trecho"])))
        except Exception as erro:  # noqa: BLE001 - tabela de outro recurso com outra forma: segue sem ela
            registrar_erro(erro, NOME, f"ler {tabela}")
    return saida


def _itens_do_periodo(banco: Any, inicio: date, fim: date) -> list[dict]:
    linhas = banco.consultar(
        "SELECT i.id, i.registro_id, i.tipo, i.resumo, i.trecho, i.certeza, i.categorias, i.importancia,"
        " i.data_evento, i.precisao, i.pessoa, i.pessoa_id, i.alvo, i.polaridade, i.lembrar_em, i.status,"
        " r.data_registro FROM itens i JOIN registros r ON r.id = i.registro_id"
        " WHERE r.data_registro >= ? AND r.data_registro <= ? AND i.status != 'descartado'"
        " ORDER BY r.data_registro, i.id", (inicio.isoformat(), fim.isoformat()))
    for l in linhas:
        l["_data"] = _d(l["data_registro"])
        l["_categorias"] = _json_lista(l["categorias"])
        l["_evento"] = _d(l["data_evento"])
    return linhas


def _pessoas(banco: Any) -> dict[int, str]:
    return {l["id"]: (l["nome"] or l["relacao"] or "") for l in banco.consultar("SELECT id, nome, relacao FROM pessoas")}


def _rotulo_pessoa(item: dict, pessoas: dict[int, str]) -> str | None:
    if item.get("pessoa_id") is not None:
        return pessoas.get(item["pessoa_id"]) or item.get("pessoa") or None
    return (item.get("pessoa") or "").strip() or None


# ---------------------------------------------------------------- destaques (pontos explicáveis)

_PESO_TIPO = {"restricao": 3, "data_importante": 3, "pendencia": 3, "apoio": 3, "desejo": 2, "preferencia": 2,
              "padrao": 2, "estado": 2, "pessoa_info": 1, "fato": 1, "nota": 0}
_MOTIVO_TIPO = {"restricao": "é um cuidado (restrição)", "data_importante": "é uma data que se repete",
                "pendencia": "ficou em aberto", "apoio": "diz o que a ajuda", "desejo": "é algo que ela quer"}


def pontuar(item: dict, fim: date, repeticoes: int) -> tuple[int, list[str]]:
    """Pontos de um item para os destaques e o porquê de cada um (o que aparece em "Por quê?").

    Tipo (restrição, data que se repete, pendência, o que ajuda e evento com data depois da semana: 3;
    desejo, preferência, padrão: 2; fato: 1; nota solta: 0) + ela mesma disse (+2) ou você viu (+1)
    + importante (+2; comentário solto -1) + o assunto voltou na semana (+1 por registro a mais, até +2)."""
    tipo = item["tipo"]
    pontos, motivos = 0, []
    if tipo == "evento":
        evento = item.get("_evento")
        if evento and evento > fim:
            pontos += 3
            motivos.append(f"tem data marcada ({data_dita(evento, item.get('precisao'))})")
        else:
            pontos += 2
    else:
        pontos += _PESO_TIPO.get(tipo, 1)
        if tipo in _MOTIVO_TIPO:
            motivos.append(_MOTIVO_TIPO[tipo])
    if item.get("certeza") == "confirmado":
        pontos += 2
        motivos.insert(0, "ela mesma disse")
    elif item.get("certeza") == "observado":
        pontos += 1
    importancia = item.get("importancia") or 2
    if importancia >= 3:
        pontos += 2
        motivos.append("é importante")
    elif importancia <= 1:
        pontos -= 1
    if repeticoes >= 2:
        pontos += min(repeticoes - 1, 2)
        motivos.append(f"o assunto voltou em {repeticoes} registros")
    return pontos, motivos


def _entrada(item: dict, pessoas: dict[int, str], pontos: int = 0, motivos: list[str] | None = None) -> dict:
    return {"item_id": item["id"], "registro_id": item["registro_id"], "tipo": item["tipo"],
            "resumo": item["resumo"], "trecho": item["trecho"], "certeza": item["certeza"],
            "categoria": _assunto_do_item(item), "categorias": item["_categorias"],
            "data_registro": item["data_registro"], "data_evento": item["data_evento"], "precisao": item["precisao"],
            "polaridade": item.get("polaridade"), "pessoa": _rotulo_pessoa(item, pessoas),
            "importancia": item.get("importancia") or 2, "pontos": pontos, "motivos": list(motivos or [])}


# ---------------------------------------------------------------- o que ficou em aberto

def _pendencias(banco: Any, inicio: date, ate: date, so_do_periodo: bool, filtro: _Filtro) -> list[dict]:
    desde = inicio if so_do_periodo else ate - timedelta(days=DIAS_PENDENCIA)
    saida = []
    for l in banco.consultar(
            "SELECT i.id, i.registro_id, i.resumo, i.trecho, i.certeza, i.lembrar_em, i.pessoa, r.data_registro"
            " FROM itens i JOIN registros r ON r.id = i.registro_id WHERE i.tipo = 'pendencia' AND i.status = 'ativo'"
            " AND r.data_registro >= ? AND r.data_registro <= ? ORDER BY r.data_registro DESC, i.id DESC",
            (desde.isoformat(), ate.isoformat())):
        if filtro.fora(l["registro_id"], l["trecho"], l["resumo"]):
            continue
        saida.append({"item_id": l["id"], "registro_id": l["registro_id"], "texto": l["resumo"], "trecho": l["trecho"],
                      "certeza": l["certeza"], "desde": l["data_registro"], "lembrar_em": l["lembrar_em"],
                      "da_semana": inicio.isoformat() <= l["data_registro"]})
    saida.sort(key=lambda x: (not x["da_semana"], x["lembrar_em"] or "9999", -(x["item_id"] or 0)))
    return saida


def _promessas(banco: Any, periodo: Periodo, so_do_periodo: bool, filtro: _Filtro) -> list[dict]:
    """As promessas em aberto (tabela do recurso Promessas, seção 20.3), se ele existe aqui."""
    try:
        if not _tabela_existe(banco, "promessas"):
            return []
        linhas = banco.consultar("SELECT * FROM promessas WHERE status = 'aberta' ORDER BY id")
        ids = [l.get("registro_id") for l in linhas if isinstance(l.get("registro_id"), int)]
        datas_registro = {r["id"]: r["data_registro"] for r in banco.consultar(
            f"SELECT id, data_registro FROM registros WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
    except Exception as erro:  # noqa: BLE001 - tabela de outro recurso: forma diferente = sem promessas aqui
        registrar_erro(erro, NOME, "ler promessas")
        return []
    saida = []
    for l in linhas:
        texto = l.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            continue
        rid = l.get("registro_id") if isinstance(l.get("registro_id"), int) else None
        quando_feita = datas_registro.get(rid) or (str(l.get("criado_em") or "")[:10] or None)
        if so_do_periodo and not periodo.contem(_d(quando_feita)):
            continue
        trecho = l.get("trecho") if isinstance(l.get("trecho"), str) else None
        if filtro.fora(rid, texto, trecho):
            continue
        prazo = _d(l.get("prazo"))
        precisao = l.get("precisao") if l.get("precisao") in PRECISOES else ("dia" if prazo else None)
        saida.append({"id": l.get("id"), "texto": texto, "trecho": trecho, "prazo": prazo,
                      "precisao": precisao if prazo else None, "registro_id": rid, "desde": quando_feita})
    saida.sort(key=lambda x: (x["prazo"] is None, x["prazo"] or date.max, x["id"] or 0))
    return saida


def _lembretes(banco: Any, status: tuple[str, ...] = ("aberto",)) -> list[dict]:
    return banco.consultar(
        f"SELECT l.*, r.data_registro FROM {T_LEMBRETES} l LEFT JOIN registros r ON r.id = l.registro_id"
        f" WHERE l.status IN ({','.join('?' * len(status))}) ORDER BY l.data IS NULL, l.data, l.id", status)


def _vista_lembrete(l: dict, referencia: date, filtro: _Filtro) -> dict:
    data = _d(l.get("data"))
    precisao = l.get("precisao") or ("dia" if data else None)
    return {"id": l["id"], "texto": l["texto"], "trecho": l["trecho"], "data": data, "precisao": precisao,
            "data_expressao": l.get("data_expressao"), "status": l["status"], "registro_id": l.get("registro_id"),
            "desde": l.get("data_registro"), "concluido_em": l.get("concluido_em"),
            "quando": quando_data(data, precisao, referencia) if data else None,
            "atrasado": bool(data and l["status"] == "aberto" and precisao == "dia" and data < referencia),
            "sensivel": filtro.fora(l.get("registro_id"), l["texto"], l["trecho"])}


# ---------------------------------------------------------------- assuntos das conversas

def _assuntos(banco: Any, periodo: Periodo, dia: int) -> list[dict]:
    linhas = banco.consultar(
        f"SELECT a.id, a.assunto, a.chave, a.registro_id, r.data_registro FROM {T_ASSUNTOS} a"
        " JOIN registros r ON r.id = a.registro_id WHERE r.data_registro >= ? AND r.data_registro <= ?"
        " ORDER BY r.data_registro, a.id", (periodo.inicio.isoformat(), periodo.fim.isoformat()))
    if not linhas:
        return []
    antes_ini = periodo.inicio - timedelta(days=7 * SEMANAS_ANTES)
    semanas_antes: dict[str, set[str]] = {}
    for l in banco.consultar(
            f"SELECT a.chave, r.data_registro FROM {T_ASSUNTOS} a JOIN registros r ON r.id = a.registro_id"
            " WHERE r.data_registro >= ? AND r.data_registro < ?",
            (antes_ini.isoformat(), periodo.inicio.isoformat())):
        d = _d(l["data_registro"])
        if d:
            semanas_antes.setdefault(l["chave"], set()).add(periodo_de(d, dia).id)
    juntos: dict[str, dict] = {}
    for l in linhas:
        if sensivel(l["assunto"]):
            continue
        j = juntos.setdefault(l["chave"], {"assunto": l["assunto"], "chave": l["chave"], "registro_ids": [],
                                           "ids": [], "semanas_antes": len(semanas_antes.get(l["chave"], ()))})
        if l["registro_id"] not in j["registro_ids"]:
            j["registro_ids"].append(l["registro_id"])
        j["ids"].append(l["id"])
    saida = [{**j, "vezes": len(j["registro_ids"])} for j in juntos.values()]
    saida.sort(key=lambda x: (-x["vezes"], -x["semanas_antes"], x["chave"]))
    return saida


# ---------------------------------------------------------------- o momento dela

def _sem_momento() -> dict:
    return {"sensivel": False, "texto": None, "registro_ids": [], "apoio": []}


def _momento(estados: list[dict], ate: date, apoios: list[dict]) -> dict:
    """Sensível: o estado mais recente dela, dos últimos 3 dias, é difícil. Só cita o que foi escrito."""
    recentes = [e for e in estados if e["_quando"] and e["_quando"] <= ate and (ate - e["_quando"]).days <= DIAS_SENSIVEL]
    if not recentes:
        return _sem_momento()
    ultimo = max(recentes, key=lambda e: (e["_quando"], e["id"]))
    if (ultimo.get("polaridade") or 0) >= 0:
        return _sem_momento()
    texto = frase(f"{_cap(quando(ultimo['_quando'], ate))} você registrou: {citar(ultimo['trecho'])}")
    return {"sensivel": True, "texto": texto, "registro_ids": [ultimo["registro_id"]],
            "apoio": [{"texto": a["resumo"], "trecho": a["trecho"], "registro_id": a["registro_id"]} for a in apoios[:2]]}


def _apoios(banco: Any, filtro: _Filtro) -> list[dict]:
    return [l for l in banco.consultar(
        "SELECT id, registro_id, resumo, trecho FROM itens WHERE tipo = 'apoio' AND status = 'ativo'"
        " AND COALESCE(polaridade, 1) > 0 AND pessoa_id IS NULL AND (pessoa IS NULL OR pessoa = '')"
        " ORDER BY id DESC LIMIT 10") if not filtro.fora(l["registro_id"], l["trecho"], l["resumo"])]


# ---------------------------------------------------------------- próximos 7 dias

def _proximos(banco: Any, referencia: date, promessas: list[dict], lembretes: list[dict], filtro: _Filtro) -> list[dict]:
    from ..alertas import proximos_eventos          # na hora do uso (o banco carrega os recursos ao abrir)
    fim = referencia + timedelta(days=DIAS_PROXIMOS)
    saida = []
    ocorrencias = proximos_eventos(banco, referencia, DIAS_PROXIMOS)
    # A mesma frase que virou lembrete (ou promessa) e evento ("Lembrar: perguntar da entrevista na
    # segunda" não quer dizer que a entrevista é segunda): fica só o lembrete, com as palavras dele.
    ids = [oc["item_id"] for oc in ocorrencias if isinstance(oc.get("item_id"), int)]
    trechos = {l["id"]: l["trecho"] for l in banco.consultar(
        f"SELECT id, trecho FROM itens WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
    seus = {(x["registro_id"], x[chave], x["trecho"]) for x, chave in
            [(p, "prazo") for p in promessas] + [(l, "data") for l in lembretes] if x.get(chave) and x.get("trecho")}
    for oc in ocorrencias:
        data = _d(oc.get("data"))
        if data is None or data < referencia or filtro.fora(oc.get("registro_id"), oc.get("texto")):
            continue
        if (oc.get("registro_id"), data, trechos.get(oc.get("item_id"))) in seus:
            continue
        saida.append({"data": data, "texto": oc.get("texto") or "", "tipo": oc.get("tipo"), "origem": "registro",
                      "registro_id": oc.get("registro_id"), "item_id": oc.get("item_id"),
                      "precisao": oc.get("precisao") or "dia", "dias": (data - referencia).days})
    for p in promessas:
        if p["prazo"] and referencia <= p["prazo"] <= fim and (p.get("precisao") or "dia") == "dia":
            saida.append({"data": p["prazo"], "texto": f"Promessa: {p['texto']}", "tipo": "promessa", "origem": "promessa",
                          "registro_id": p["registro_id"], "item_id": None, "precisao": "dia",
                          "dias": (p["prazo"] - referencia).days})
    for l in lembretes:
        if l["data"] and referencia <= l["data"] <= fim and l["precisao"] == "dia" and not l["sensivel"]:
            saida.append({"data": l["data"], "texto": f"Lembrar: {l['texto']}", "tipo": "lembrete", "origem": "lembrete",
                          "registro_id": l["registro_id"], "item_id": None, "lembrete_id": l["id"], "precisao": "dia",
                          "dias": (l["data"] - referencia).days})
    ordem = {"registro": 0, "promessa": 1, "lembrete": 2}
    saida.sort(key=lambda x: (x["data"], ordem[x["origem"]], normalizar(x["texto"])))
    return saida


# ---------------------------------------------------------------- a pergunta (recurso Perguntas)

def _pergunta(banco: Any, referencia: date) -> dict | None:
    """A pergunta sugerida agora, pelo recurso Perguntas, sem gravar nada (se ele existe aqui)."""
    modulo = recurso_por_nome("perguntas")
    funcao = getattr(modulo, "sugestao_atual", None) if modulo is not None else None
    if not callable(funcao):
        return None
    try:
        dados = funcao(banco, referencia)
    except Exception as erro:  # noqa: BLE001 - o resumo aparece sem a pergunta
        registrar_erro(erro, NOME, "pergunta do recurso perguntas")
        return None
    if not isinstance(dados, dict) or not isinstance(dados.get("pergunta"), str) or not dados["pergunta"].strip():
        return None
    motivos = [m for m in (dados.get("motivos") or []) if isinstance(m, dict) and isinstance(m.get("texto"), str)]

    def texto_ou_nada(chave: str) -> str | None:
        return dados.get(chave) if isinstance(dados.get(chave), str) else None

    return {"pergunta": dados["pergunta"].strip(), "titulo": texto_ou_nada("titulo"),
            "categoria": texto_ou_nada("categoria_nome"), "estado": texto_ou_nada("estado"),
            "motivos": [{"texto": m["texto"], "registro_ids": [r for r in (m.get("registro_ids") or [])
                                                                if isinstance(r, int) and not isinstance(r, bool)]}
                        for m in motivos]}


# ---------------------------------------------------------------- "Talvez..." (só do que está registrado)

def _sugestoes(momento: dict, proximos: list[dict], pendencias: list[dict], voltaram: list[dict], banco: Any,
               referencia: date) -> list[dict]:
    saida: list[dict] = []
    if momento["sensivel"]:
        porque = [momento["texto"]]
        ids = list(momento["registro_ids"])
        for a in momento["apoio"]:
            porque.append(frase(f"Você também registrou o que costuma ajudar: {citar(a['trecho'], 120)}"))
            ids.append(a["registro_id"])
        texto = ("Talvez, nos próximos dias, valha mais estar perto do jeito que ela disse que ajuda."
                 if momento["apoio"] else
                 "Talvez, nos próximos dias, valha mais perguntar como ela está do que puxar assuntos novos.")
        saida.append({"texto": texto, "porque": " ".join(porque), "registro_ids": list(dict.fromkeys(ids))})
    ids_eventos = [p["item_id"] for p in proximos if p["origem"] == "registro" and p["tipo"] == "evento" and p["item_id"]]
    if ids_eventos:
        importantes = {l["id"]: l for l in banco.consultar(
            f"SELECT id, registro_id, trecho FROM itens WHERE id IN ({','.join('?' * len(ids_eventos))})"
            " AND importancia >= 3", tuple(ids_eventos))}
        for p in proximos:
            item = importantes.get(p.get("item_id"))
            if item is None or p["dias"] < 1:
                continue                  # hoje: o Hoje já avisa como Importante
            saida.append({"texto": f"{_cap(quando(p['data'], referencia))}: {p['texto'].rstrip('.')}. Talvez uma "
                                   "mensagem de carinho perto do dia faça diferença.",
                          "porque": frase(f"Ficou marcado como importante. Você registrou: {citar(item['trecho'])}"),
                          "registro_ids": [item["registro_id"]]})
            break
    for pend in pendencias:
        if not pend["da_semana"]:
            continue
        lembrar = _d(pend["lembrar_em"])
        if lembrar is None or lembrar <= referencia + timedelta(days=DIAS_PROXIMOS):
            desde = _d(pend["desde"])
            saida.append({"texto": f"Talvez perguntar como ficou: “{pend['texto'].rstrip('.')}”.",
                          "porque": frase(f"Você registrou {quando(desde, referencia) if desde else ''}: "
                                          f"{citar(pend['trecho'])}"),
                          "registro_ids": [pend["registro_id"]]})
            break
    for v in voltaram:
        if v["tipo"] == "categoria" and v["registros"] >= 3 and v["rotulo"] != OUTRAS:
            saida.append({"texto": f"Talvez valha perguntar como ela está com o assunto “{v['rotulo']}”.",
                          "porque": f"Ele apareceu em {v['registros']} registros nesta semana.",
                          "registro_ids": list(v["registro_ids"])})
            break
    return saida[:MAX_SUGESTOES]


# ---------------------------------------------------------------- o resumo

def resumo(banco: Any, periodo: Periodo, referencia: date, dia: int | None = None) -> dict:
    """Tudo o que a tela #semana mostra (nada é gravado). Ver a docstring do módulo."""
    dia = dia_do_resumo(banco.config()) if dia is None else dia
    filtro = _Filtro(banco)
    pessoas = _pessoas(banco)
    atual = periodo.inicio <= referencia and periodo.fim >= referencia - timedelta(days=DIAS_CARTAO)
    registros = banco.consultar(
        "SELECT id, data_registro, contexto, humor FROM registros WHERE data_registro >= ? AND data_registro <= ?"
        " ORDER BY data_registro, id", (periodo.inicio.isoformat(), periodo.fim.isoformat()))
    ids_registros = [r["id"] for r in registros]
    # Frases que são um lembrete SEU ("Lembrar: ...", "preciso lembrar de ..."): não são algo que ela contou.
    de_lembrete = {(l["registro_id"], l["trecho"]) for l in banco.consultar(
        f"SELECT registro_id, trecho FROM {T_LEMBRETES} WHERE registro_id IN ({','.join('?' * len(ids_registros))})",
        tuple(ids_registros))} if ids_registros else set()
    itens = [i for i in _itens_do_periodo(banco, periodo.inicio, periodo.fim)
             if not filtro.fora(i["registro_id"], i["trecho"], i["resumo"])]
    suas = _frases_suas(banco, ids_registros)

    def _e_seu(i: dict) -> bool:
        trecho = i["trecho"] or ""
        n = normalizar(trecho)
        return (_marcador(trecho) or (i["registro_id"], trecho) in de_lembrete
                or any(rid == i["registro_id"] and (n in t or t in n) for rid, t in suas)
                or bool(_RE_FRASE_SUA.search(plano(trecho))))

    # O que voltou na semana: assunto (categoria) e pessoa em 2 ou mais registros.
    por_assunto: dict[str, list[int]] = {}
    por_pessoa: dict[str, tuple[str, list[int]]] = {}
    for i in itens:
        i["_seu"] = _e_seu(i)
        if i["_seu"]:
            continue
        assunto = _assunto_do_item(i)
        lista = por_assunto.setdefault(assunto, [])
        if i["registro_id"] not in lista:
            lista.append(i["registro_id"])
        rotulo = _rotulo_pessoa(i, pessoas)
        if rotulo:
            chave = f"id:{i['pessoa_id']}" if i.get("pessoa_id") is not None else normalizar(rotulo)
            _nome, ids = por_pessoa.setdefault(chave, (rotulo, []))
            if i["registro_id"] not in ids:
                ids.append(i["registro_id"])
    voltaram = ([{"rotulo": a, "tipo": "categoria", "registros": len(ids), "registro_ids": ids}
                 for a, ids in por_assunto.items() if len(ids) >= 2 and a != OUTRAS]
                + [{"rotulo": nome, "tipo": "pessoa", "registros": len(ids), "registro_ids": ids}
                   for nome, ids in por_pessoa.values() if len(ids) >= 2])
    voltaram.sort(key=lambda v: (-v["registros"], v["tipo"], _CATEGORIA_ORDEM.get(v["rotulo"], 99), v["rotulo"]))

    # O que ela contou, por categoria, e os destaques.
    grupos: dict[str, list[dict]] = {}
    estados: list[dict] = []
    todos: list[dict] = []
    for i in itens:
        if i["tipo"] == "estado":
            if _dela(i):
                i["_quando"] = _d(i["data_evento"]) or i["_data"]
                estados.append(i)
            continue
        if i["_seu"]:
            continue                      # "Assuntos importantes: ..." / "Lembrar: ...": não é algo que ela contou
        assunto = _assunto_do_item(i)
        pontos, motivos = pontuar(i, periodo.fim, len(por_assunto.get(assunto, ())) if assunto != OUTRAS else 0)
        entrada = _entrada(i, pessoas, pontos, motivos)
        grupos.setdefault(assunto, []).append(entrada)
        todos.append(entrada)
    contou = []
    for assunto, entradas in grupos.items():
        entradas.sort(key=lambda e: (-e["pontos"], e["data_registro"], e["item_id"]))
        contou.append({"categoria": assunto, "total": len(entradas), "registros": len({e["registro_id"] for e in entradas}),
                       "itens": entradas[:MAX_POR_CATEGORIA], "mais": max(0, len(entradas) - MAX_POR_CATEGORIA)})
    contou.sort(key=lambda g: (g["categoria"] == OUTRAS, -g["total"], _CATEGORIA_ORDEM.get(g["categoria"], 99)))
    destaques = sorted((e for e in todos if e["pontos"] >= 3),
                       key=lambda e: (-e["pontos"], e["data_registro"], e["item_id"]))[:MAX_DESTAQUES]

    estados.sort(key=lambda e: (e["_quando"] or date.min, e["id"]))
    lista_estados = [{"data": e["_quando"], "texto": (e.get("alvo") or e["resumo"]), "resumo": e["resumo"],
                      "trecho": e["trecho"], "certeza": e["certeza"], "polaridade": e.get("polaridade"),
                      "registro_id": e["registro_id"], "item_id": e["id"]} for e in estados]
    ate = min(periodo.fim, referencia)
    momento = _momento(estados, ate, _apoios(banco, filtro)) if atual else _sem_momento()

    # Conversas: registros com "onde/como" ou com assuntos guardados.
    assuntos = _assuntos(banco, periodo, dia)
    assuntos_por_registro: dict[int, list[str]] = {}
    for l in banco.consultar(
            f"SELECT registro_id, assunto FROM {T_ASSUNTOS} WHERE registro_id IN ({','.join('?' * len(ids_registros))})"
            " ORDER BY id", tuple(ids_registros)) if ids_registros else []:
        if not sensivel(l["assunto"]):
            assuntos_por_registro.setdefault(l["registro_id"], []).append(l["assunto"])
    conversas = [{"registro_id": r["id"], "data": r["data_registro"], "contexto": r["contexto"], "humor": r["humor"],
                  "assuntos": assuntos_por_registro.get(r["id"], [])}
                 for r in registros if (r["contexto"] or "").strip() or r["id"] in assuntos_por_registro]

    # Em aberto: agora (na semana atual) ou o que veio daquela semana e continua em aberto.
    so_do_periodo = not atual
    pendencias = _pendencias(banco, periodo.inicio, periodo.fim if so_do_periodo else max(ate, periodo.inicio),
                             so_do_periodo, filtro)
    promessas = _promessas(banco, periodo, so_do_periodo, filtro)
    lembretes = [_vista_lembrete(l, referencia, filtro) for l in _lembretes(banco)]
    lembretes = [l for l in lembretes if not l["sensivel"] and (not so_do_periodo or periodo.contem(_d(l["desde"])))]

    proximos = _proximos(banco, referencia, promessas, lembretes, filtro) if atual else []
    sugestoes = _sugestoes(momento, proximos, pendencias, voltaram, banco, referencia) if atual and registros else []
    pergunta = _pergunta(banco, referencia) if atual else None

    seguinte = periodo.seguinte()
    return {
        "semana": periodo.id, "inicio": periodo.inicio, "fim": periodo.fim, "referencia": referencia,
        "dia_resumo": DIAS[dia], "atual": atual, "futura": periodo.inicio > referencia,
        "em_andamento": periodo.inicio <= referencia < periodo.fim,
        "registros": len(registros), "registro_ids": [r["id"] for r in registros],
        "itens": len(todos) + len(lista_estados), "vazia": not registros,
        "contou": contou, "destaques": destaques, "estados": lista_estados, "momento": momento,
        "voltaram": voltaram[:8], "conversas": conversas, "assuntos": assuntos,
        "abertos": {"pendencias": pendencias, "promessas": promessas, "lembretes": lembretes,
                    "total": len(pendencias) + len(promessas) + len(lembretes)},
        "proximos": proximos, "sugestoes": sugestoes, "pergunta": pergunta,
        "anterior": periodo.anterior().id, "seguinte": seguinte.id if seguinte.inicio <= referencia else None,
    }


def historico(banco: Any, referencia: date, dia: int | None = None, limite: int = 26) -> list[dict]:
    """As semanas com registro (mais nova primeiro): {semana, inicio, fim, registros, conversas}."""
    dia = dia_do_resumo(banco.config()) if dia is None else dia
    conversas = {l["id"] for l in banco.consultar(
        f"SELECT r.id FROM registros r WHERE TRIM(COALESCE(r.contexto, '')) != ''"
        f" OR EXISTS (SELECT 1 FROM {T_ASSUNTOS} a WHERE a.registro_id = r.id)")}
    semanas: dict[str, dict] = {}
    for l in banco.consultar("SELECT id, data_registro FROM registros WHERE data_registro <= ? ORDER BY data_registro DESC",
                             (periodo_de(referencia, dia).fim.isoformat(),)):
        d = _d(l["data_registro"])
        if d is None:
            continue
        p = periodo_de(d, dia)
        s = semanas.setdefault(p.id, {"semana": p.id, "inicio": p.inicio, "fim": p.fim, "registros": 0, "conversas": 0})
        s["registros"] += 1
        s["conversas"] += 1 if l["id"] in conversas else 0
    return sorted(semanas.values(), key=lambda s: s["fim"], reverse=True)[:limite]


# ================================================================ Hoje

def _cartao_resumo(banco: Any, referencia: date, dia: int) -> dict | None:
    periodo = periodo_do_cartao(referencia, dia)
    if periodo is None:
        return None
    r = resumo(banco, periodo, referencia, dia)
    if r["registros"] < MIN_REGISTROS_CARTAO:
        return None                       # semana sem registro (ou com um só): nada de cartão genérico
    dias_depois = (referencia - periodo.fim).days
    abertos = r["abertos"]
    primeiros = ([p["texto"] for p in abertos["pendencias"]] + [f"Promessa: {p['texto']}" for p in abertos["promessas"]]
                 + [f"Lembrar: {l['texto']}" for l in abertos["lembretes"]])
    dados = {
        "semana": r["semana"], "inicio": r["inicio"], "fim": r["fim"], "registros": r["registros"],
        "categorias": [{"nome": g["categoria"], "total": g["total"]} for g in r["contou"][:4]],
        "destaques": [{"resumo": e["resumo"], "trecho": e["trecho"], "certeza": e["certeza"],
                       "registro_id": e["registro_id"], "motivos": e["motivos"]} for e in r["destaques"][:2]],
        "estados": [{"texto": e["texto"], "data": e["data"], "polaridade": e["polaridade"]} for e in r["estados"][-3:]],
        "abertos": {"total": abertos["total"], "primeiros": primeiros[:2]},
        "proximos": [{"data": p["data"], "texto": p["texto"], "dias": p["dias"]} for p in r["proximos"][:3]],
        "pergunta": {"pergunta": r["pergunta"]["pergunta"], "titulo": r["pergunta"]["titulo"]} if r["pergunta"] else None,
        "momento": r["momento"]["texto"] if r["momento"]["sensivel"] else None,
        "sugestao": r["sugestoes"][0]["texto"] if r["sugestoes"] else None,
    }
    texto = (f"Resumo da semana ({periodo.inicio:%d/%m} a {periodo.fim:%d/%m}): o que ela contou, "
             "o que ficou em aberto e o que vem por aí.")
    porque = (f"Toda semana, no dia do resumo ({DIAS[dia]}), o Entre Nós junta o que você registrou nos 7 dias. "
              "Só entra o que está nos registros; dá para trocar o dia em Ajustes → Resumo da semana.")
    return cartao(f"resumo:{r['semana']}", "lembrete", texto, porque,
                  relevancia=RELEVANCIA_RESUMO - 6 * dias_depois, data=periodo.fim,
                  registro_ids=r["registro_ids"][-10:], tipo="semana_resumo", dados=dados)


def _cartoes_lembretes(banco: Any, referencia: date) -> list[dict]:
    filtro = _Filtro(banco)
    saida: list[dict] = []
    atrasados = []
    for l in _lembretes(banco):
        v = _vista_lembrete(l, referencia, filtro)
        data = v["data"]
        if data is None or v["precisao"] != "dia":
            continue
        dias = (data - referencia).days
        desde = _d(v["desde"])
        origem = f"Você anotou em {desde:%d/%m}" if desde else "Você anotou"
        ids = [v["registro_id"]] if v["registro_id"] else []
        dia_txt = "Hoje" if dias == 0 else "Amanhã"
        if dias in (0, 1):
            if v["sensivel"]:
                # O resumo da semana deixa os sensíveis de fora: o caminho é o próprio registro (revisão de 07/10).
                texto = f"{dia_txt}: {AVISO_SENSIVEL_HOJE}"
                porque = "Abra o registro para ver." if ids else "O texto fica reservado."

                dados = {"id": v["id"], "sensivel": True}
            else:
                texto = f"{dia_txt}: {v['texto'].rstrip('.')}."
                porque = frase(f"{origem}: {citar(v['trecho'], 160)}")
                dados = {"id": v["id"], "texto": v["texto"], "data": data, "sensivel": False}
            saida.append(cartao(f"l{v['id']}:{data.isoformat()}:d{dias}", "lembrete", texto, porque,
                                relevancia=RELEVANCIA_HOJE if dias == 0 else RELEVANCIA_AMANHA, data=data,
                                registro_ids=ids, tipo="semana_lembrete", dados=dados))
        elif -DIAS_ATRASO <= dias < 0 and not v["sensivel"]:
            atrasados.append((data, v, origem, ids))
    if atrasados:
        data, v, origem, ids = max(atrasados, key=lambda x: (x[0], x[1]["id"]))
        saida.append(cartao(
            f"l{v['id']}:{data.isoformat()}:atrasado", "sugestao",
            f"Ficou para trás um lembrete seu: “{v['texto'].rstrip('.')}” (era para {quando(data, referencia)}). "
            "Talvez ainda dê tempo; se já resolveu, é só marcar como feito.",
            frase(f"{origem}: {citar(v['trecho'], 160)}"), relevancia=RELEVANCIA_ATRASADO, data=data,
            registro_ids=ids, tipo="semana_lembrete", dados={"id": v["id"], "texto": v["texto"], "data": data,
                                                             "atrasado": True, "sensivel": False}))
    return saida


def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """O resumo da semana (no dia do resumo e nos 2 seguintes) e os seus lembretes de hoje e amanhã."""
    config = banco.config()
    saida = []
    if no_hoje(config):
        resumo_cartao = _cartao_resumo(banco, referencia, dia_do_resumo(config))
        if resumo_cartao:
            saida.append(resumo_cartao)
    saida.extend(_cartoes_lembretes(banco, referencia))
    return saida


# ================================================================ rotas (/api/semana/...)

def _hoje_do_pedido(nucleo: Any, pedido: Any) -> date:
    bruto = pedido.parametro("data") if hasattr(pedido, "parametro") else None
    if bruto:
        try:
            return _data_valida(bruto.strip()) or date.today()
        except ValueError:
            raise ValueError(DATA_INVALIDA) from None
    hoje = getattr(nucleo, "hoje", None)
    try:
        valor = hoje() if callable(hoje) else None
    except Exception:  # noqa: BLE001
        valor = None
    return valor if isinstance(valor, date) else date.today()


def _rota_resumo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    referencia = _hoje_do_pedido(nucleo, pedido)
    dia = dia_do_resumo(banco.config())
    pedida = pedido.parametro("semana")
    periodo = periodo_por_id(pedida, dia) if pedida else periodo_padrao(referencia, dia)
    return resumo(banco, periodo, referencia, dia)


def _rota_historico(nucleo: Any, pedido: Any, banco: Any) -> dict:
    referencia = _hoje_do_pedido(nucleo, pedido)
    bruto = pedido.parametro("limite")
    limite = 26
    if bruto:
        if not re.fullmatch(r"\s*\d{1,4}\s*", bruto):
            raise ValueError(LIMITE_INVALIDO)
        limite = max(1, min(MAX_HISTORICO, int(bruto)))
    dia = dia_do_resumo(banco.config())
    return {"semanas": historico(banco, referencia, dia, limite), "dia_resumo": DIAS[dia],
            "atual": periodo_de(referencia, dia).id}


def _rota_conversa(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return montar_conversa(pedido.corpo)


def _rota_lembretes(nucleo: Any, pedido: Any, banco: Any) -> dict:
    referencia = _hoje_do_pedido(nucleo, pedido)
    filtro = _Filtro(banco)
    desde = (referencia - timedelta(days=30)).isoformat()
    abertos = [_vista_lembrete(l, referencia, filtro) for l in _lembretes(banco)]
    feitos = [_vista_lembrete(l, referencia, filtro) for l in _lembretes(banco, ("feito",))
              if (l.get("concluido_em") or "") >= desde]
    feitos.sort(key=lambda v: (v["concluido_em"] or "", v["id"]), reverse=True)
    return {"abertos": abertos, "feitos": feitos}


_CAMPOS_PATCH = frozenset({"status", "texto", "data", "precisao"})


def _rota_mudar_lembrete(nucleo: Any, pedido: Any, banco: Any) -> dict:
    corpo = pedido.corpo
    for campo in corpo:
        if campo not in _CAMPOS_PATCH:
            raise ValueError(CAMPO_DESCONHECIDO.format(campo=campo))
    linhas = banco.consultar(f"SELECT * FROM {T_LEMBRETES} WHERE id = ?", (pedido.id,))
    if not linhas:
        raise NaoEncontrado(LEMBRETE_SUMIU)
    referencia = _hoje_do_pedido(nucleo, pedido)
    mudar: dict[str, Any] = {}
    if "status" in corpo:
        if corpo["status"] not in STATUS_LEMBRETE:
            raise ValueError(STATUS_INVALIDO)
        mudar["status"] = corpo["status"]
        mudar["concluido_em"] = referencia if corpo["status"] == "feito" else None
    if "texto" in corpo:
        mudar["texto"] = _texto_lembrete(corpo["texto"])
    if "data" in corpo:
        data = _data_valida(corpo["data"])
        precisao = corpo.get("precisao", "dia")
        if data is not None and precisao not in PRECISOES:
            raise ValueError(PRECISAO_INVALIDA)
        mudar["data"] = data
        mudar["precisao"] = precisao if data is not None else None
        if data is None or _d(linhas[0]["data"]) != data:
            mudar["data_expressao"] = None          # a expressão escrita era da data antiga
    elif "precisao" in corpo:
        if corpo["precisao"] not in PRECISOES or not linhas[0]["data"]:
            raise ValueError(PRECISAO_INVALIDA)
        mudar["precisao"] = corpo["precisao"]
    if not mudar:
        raise ValueError(NADA_PARA_MUDAR)
    banco.atualizar(T_LEMBRETES, pedido.id, mudar)
    l = banco.consultar(f"SELECT l.*, r.data_registro FROM {T_LEMBRETES} l LEFT JOIN registros r"
                        " ON r.id = l.registro_id WHERE l.id = ?", (pedido.id,))[0]
    return _vista_lembrete(l, referencia, _Filtro(banco))


def _apagar_na_copia(copia: Any, tabela: str, linha_id: int) -> bool:
    copia.apagar(tabela, linha_id)
    if tabela == T_LEMBRETES:
        copia.esquecer_alertas(f"{NOME}:l{linha_id}:")
    return True


def _rota_apagar_lembrete(nucleo: Any, pedido: Any, banco: Any) -> dict:
    lid = pedido.id
    with banco.transacao():
        banco.apagar(T_LEMBRETES, lid)
        banco.esquecer_alertas(f"{NOME}:l{lid}:")
    nucleo.limpar_copias(lambda copia: _apagar_na_copia(copia, T_LEMBRETES, lid))
    return {}


def _rota_apagar_assunto(nucleo: Any, pedido: Any, banco: Any) -> dict:
    aid = pedido.id
    banco.apagar(T_ASSUNTOS, aid)
    nucleo.limpar_copias(lambda copia: _apagar_na_copia(copia, T_ASSUNTOS, aid))
    return {}


def rotas() -> list:
    return [
        rota("GET", "", _rota_resumo),
        rota("GET", "historico", _rota_historico),
        rota("POST", "conversa", _rota_conversa),
        rota("GET", "lembretes", _rota_lembretes),
        rota("PATCH", "lembretes/{id}", _rota_mudar_lembrete),
        rota("DELETE", "lembretes/{id}", _rota_apagar_lembrete),
        rota("DELETE", "assuntos/{id}", _rota_apagar_assunto),
    ]


# ================================================================ exportar, importar, apagar

_COLUNAS_LEMBRETE = ("texto", "trecho", "data", "precisao", "data_expressao", "status", "registro_id", "criado_em",
                     "concluido_em")


def exportar(banco: Any) -> dict:
    return {
        "lembretes": banco.consultar(f"SELECT {', '.join(_COLUNAS_LEMBRETE)} FROM {T_LEMBRETES} ORDER BY id"),
        "assuntos": banco.consultar(f"SELECT assunto, trecho, registro_id, criado_em FROM {T_ASSUNTOS} ORDER BY id"),
    }


def _lembrete_importado(l: Any, rid: int | None, texto_reg: str | None, data_reg: date | None) -> dict | None:
    """Um lembrete do arquivo conferido (None = fica de fora)."""
    trecho = l.get("trecho") if isinstance(l, dict) else None
    if not texto_reg or not isinstance(trecho, str) or not trecho.strip() or trecho not in texto_reg:
        return None
    try:
        texto = _texto_lembrete(l.get("texto"))
        data = _data_valida(l.get("data"))
        status = l.get("status") if l.get("status") in STATUS_LEMBRETE else "aberto"
        concluido = _data_valida(l.get("concluido_em")) if status == "feito" else None
    except ValueError:
        return None
    precisao = l.get("precisao") if l.get("precisao") in PRECISOES else "dia"
    criado = l.get("criado_em") if isinstance(l.get("criado_em"), str) and l.get("criado_em").strip() else _agora()
    return {"texto": texto, "trecho": trecho, "data": data, "precisao": precisao if data else None,
            "data_expressao": _expressao_da_data(trecho, l.get("data_expressao"), data, data_reg),
            "status": status, "registro_id": rid, "criado_em": criado[:32], "concluido_em": concluido}


def importar(banco: Any, dados: Any, mapa_registros: dict) -> dict:
    """Junta sem duplicar. Só entra o que aponta para um registro que veio junto e cujo trecho está
    no texto dele (nunca inventar); o resto fica de fora, contado no texto."""
    if not isinstance(dados, dict):
        raise ValueError(FORMATO_IMPORTAR)
    lembretes = dados.get("lembretes") or []
    assuntos = dados.get("assuntos") or []
    if not isinstance(lembretes, list) or not isinstance(assuntos, list):
        raise ValueError(FORMATO_IMPORTAR)
    registros: dict[int, tuple[str, date | None] | None] = {}

    def registro_de(bruto: Any) -> tuple[int | None, str | None, date | None]:
        rid = mapa_registros.get(bruto) if isinstance(bruto, int) and not isinstance(bruto, bool) else None
        if rid is None:
            return None, None, None
        if rid not in registros:
            registros[rid] = _texto_registro(banco, rid)
        achado = registros[rid]
        return (rid, achado[0], achado[1]) if achado else (None, None, None)

    novos = fora = 0
    for l in lembretes:
        rid, texto_reg, data_reg = registro_de(l.get("registro_id")) if isinstance(l, dict) else (None, None, None)
        valores = _lembrete_importado(l, rid, texto_reg, data_reg)
        if valores is None:
            fora += 1
            continue
        if banco.consultar(f"SELECT 1 FROM {T_LEMBRETES} WHERE registro_id = ? AND trecho = ? AND texto = ?",
                           (rid, valores["trecho"], valores["texto"])):
            continue
        banco.inserir(T_LEMBRETES, valores, historico=False)
        novos += 1
    for a in assuntos:
        rid, texto_reg, _data_reg = registro_de(a.get("registro_id")) if isinstance(a, dict) else (None, None, None)
        assunto = a.get("assunto") if isinstance(a, dict) else None
        trecho = a.get("trecho") if isinstance(a, dict) else None
        assunto = assunto.strip() if isinstance(assunto, str) else ""
        if (not texto_reg or not isinstance(trecho, str) or not trecho.strip() or trecho not in texto_reg
                or not assunto or assunto not in trecho or len(assunto) > MAX_ASSUNTO or not chave_assunto(assunto)):
            fora += 1
            continue
        chave = chave_assunto(assunto)
        if banco.consultar(f"SELECT 1 FROM {T_ASSUNTOS} WHERE registro_id = ? AND chave = ?", (rid, chave)):
            continue
        criado = a.get("criado_em") if isinstance(a.get("criado_em"), str) and a.get("criado_em").strip() else _agora()
        banco.inserir(T_ASSUNTOS, {"assunto": assunto, "chave": chave, "trecho": trecho, "registro_id": rid,
                                   "criado_em": criado[:32]}, historico=False)
        novos += 1
    if novos == 0:
        texto = "Nenhum lembrete ou assunto de conversa novo"
    else:
        texto = f"{novos} {'lembrete ou assunto de conversa' if novos == 1 else 'lembretes e assuntos de conversa'}"
    if fora:
        texto += (f" ({fora} {'ficou' if fora == 1 else 'ficaram'} de fora: sem o registro ou o trecho de onde "
                  f"{'veio' if fora == 1 else 'vieram'})")
    return {"novos": novos, "texto": texto + "."}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """Apagar é apagar: os lembretes e assuntos do registro saem (o banco apaga as linhas com
    `registro_id` logo depois) e as chaves de aviso dispensado deles também (revelariam a data)."""
    for l in banco.consultar(f"SELECT id FROM {T_LEMBRETES} WHERE registro_id = ?", (registro_id,)):
        banco.esquecer_alertas(f"{NOME}:l{l['id']}:")


__all__ = [
    "CHAVE_DIA", "CHAVE_NO_HOJE", "DIAS", "ESQUEMA", "ESQUEMA_VERSAO", "NOME", "Periodo", "ROTULO", "T_ASSUNTOS",
    "T_LEMBRETES", "TIPOS_EXTRAS", "ao_apagar_registro", "assuntos_da_frase", "cartoes_hoje", "chave_assunto",
    "dia_do_resumo", "exportar", "extrair", "frase_do_humor", "gravar_extras", "historico", "importar",
    "lembrete_da_frase", "montar_conversa", "periodo_de", "periodo_do_cartao", "periodo_padrao", "periodo_por_id",
    "pontuar", "quando", "quando_data", "resumo", "rotas", "sensivel",
]
