"""Utilidades de texto em português usadas por todos os módulos."""
from __future__ import annotations

import re
import unicodedata

# Palavras que não ajudam a buscar nem a relacionar registros.
STOPWORDS: frozenset[str] = frozenset("""
a o as os um uma uns umas de do da dos das no na nos nas em num numa ao aos à às
pelo pela pelos pelas por para pra pro pras pros com sem sob sobre entre ate até
e ou mas nem que se como quando onde porque pois ja já nao não sim tambem também
mais menos muito muita muitos muitas pouco pouca bem mal so só ainda sempre nunca
ela ele elas eles eu voce você me mim te ti se si lhe lhes nos nós vos meu minha
meus minhas seu sua seus suas dela dele delas deles nosso nossa isso isto aquilo
esse essa esses essas este esta estes estas aquele aquela aqueles aquelas
é e ser foi era sao são estar esta está estava estao estão ter tem tinha teve
vai vou ia ir fazer faz fez hoje ontem amanha amanhã dia dias
qual quais quem quando onde oque o que sei sabe falou disse contou comentou
coisa coisas algo alguma algum alguns algumas tudo todo toda todos todas
""".split())

_RE_PALAVRA = re.compile(r"[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*", re.UNICODE)
# Fim de frase: . ! ? … seguidos de espaço/fim, ou quebra de linha.
_RE_FIM_FRASE = re.compile(r"[.!?…]+(?=\s|$)|\n+")
# Abreviações que terminam em ponto mas não encerram a frase ("a Dra. Ana").
_ABREVIACOES = frozenset({"dr", "dra", "sr", "sra", "srta", "prof", "profa", "sto", "sta", "av", "vs", "etc"})
_RE_ULTIMA_PALAVRA = re.compile(r"(\w+)$")


def sem_acento(s: str) -> str:
    """'Apresentação' -> 'Apresentacao' (mantém maiúsculas)."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalizar(s: str) -> str:
    """Minúsculas, sem acento, espaços simples. Use para comparar, nunca para mostrar."""
    return re.sub(r"\s+", " ", sem_acento(s).lower()).strip()


def palavras(s: str) -> list[str]:
    """Palavras do texto original, na ordem, sem pontuação."""
    return _RE_PALAVRA.findall(s)


def termos(s: str) -> list[str]:
    """Palavras normalizadas e sem stopwords, para busca e relação entre registros."""
    stop = {normalizar(w) for w in STOPWORDS}
    return [w for w in (normalizar(p) for p in palavras(s)) if len(w) > 1 and w not in stop]


def frases(texto: str) -> list[tuple[int, int, str]]:
    """Divide o texto em frases. Devolve (inicio, fim, frase) com posições no texto original.

    A frase devolvida é exatamente texto[inicio:fim] (sem espaços nas pontas), para que
    o `trecho` dos itens seja sempre literal.
    """
    resultado: list[tuple[int, int, str]] = []
    pos = 0
    for m in _RE_FIM_FRASE.finditer(texto):
        if m.group() == ".":
            antes = _RE_ULTIMA_PALAVRA.search(texto[pos:m.start()])
            if antes and antes.group(1).lower() in _ABREVIACOES:
                continue
        fim = m.end() if m.group().strip() else m.start()
        _acrescentar(texto, pos, fim, resultado)
        pos = m.end()
    _acrescentar(texto, pos, len(texto), resultado)
    return resultado


def _acrescentar(texto: str, ini: int, fim: int, saida: list[tuple[int, int, str]]) -> None:
    trecho = texto[ini:fim]
    esq = len(trecho) - len(trecho.lstrip())
    dir_ = len(trecho.rstrip())
    if dir_ > esq:
        saida.append((ini + esq, ini + dir_, texto[ini + esq: ini + dir_]))


def semelhanca(a: str, b: str) -> float:
    """0-100. Usa rapidfuzz se houver; senão difflib. Compara textos normalizados."""
    a, b = normalizar(a), normalizar(b)
    try:
        from rapidfuzz import fuzz
        return float(fuzz.token_set_ratio(a, b))
    except ImportError:  # rapidfuzz é opcional
        from difflib import SequenceMatcher
        return 100.0 * SequenceMatcher(None, a, b).ratio()


def data_br(d) -> str:
    """date -> '20/10/2026'."""
    return d.strftime("%d/%m/%Y") if d else ""


def tempo_atras(dias: int) -> str:
    """Quantos dias atrás, em português ('hoje', 'há 3 dias', 'há 8 meses', 'há 1 ano')."""
    if dias <= 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    if dias < 30:
        return f"há {dias} dias"
    meses = round(dias / 30.44)
    if meses < 12:
        return "há 1 mês" if meses <= 1 else f"há {meses} meses"
    anos = round(dias / 365.25)
    return "há 1 ano" if anos <= 1 else f"há {anos} anos"
