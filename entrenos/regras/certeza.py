"""Certeza de uma frase: fala dela (confirmado), coisa vista (observado) ou hipótese (inferência).

INCERTO nunca sai daqui: é calculado depois, por idade ou contradição (perfil.py).

A fala dela só confirma o que ela disse: `trechos_confirmados` devolve a parte da frase que
vem depois do verbo de fala ("Ela disse que está cansada | e eu percebi que..."). Fala negada
("ela não disse que...") e dúvida ("não sei se...") não afirmam nada: `trechos_nao_afirmados`
devolve esses pedaços, para que não virem item.
"""
from __future__ import annotations

import re

from ..modelos import Certeza
from ..texto import normalizar
from .pessoas import RELACOES, _NAO_NOMES, plano

_INFERENCIA = re.compile(
    r"\b(?:talvez|acho|achamos|acredito que|provavelmente|possivelmente|pode ser que|podia ser que"
    r"|poderia ser que|deve(?:m|ria)? (?:ser|estar|gostar|ter|achar|preferir|querer|ficar|sentir"
    r"|odiar|adorar|amar|curtir|detestar|sentir-se)|imagino|suponho|parece que"
    r"|parece (?:gostar|estar|ser|ter|preferir|curtir|odiar|querer|sentir)|sera que"
    r"|tenho a impressao|tive a impressao|me parece|me pareceu|pelo jeito|aparentemente|quem sabe"
    r"|desconfio|sinto que|meu palpite|chuto que|aposto que|nao tenho certeza|sem certeza"
    r"|nao tenho ctz|nao sei se|nao sei bem se|sei la|nao faco ideia|nao lembro se|nao lembro bem se"
    r"|achei que|eu (?:que )?achei|fui eu que achei)\b"
)

_VERBOS_FALA = (
    r"disse|contou|falou|comentou|mencionou|explicou|confirmou|avisou|respondeu|garantiu|relatou"
    r"|revelou|admitiu|confessou|afirmou|jurou|desabafou|reclamou|escreveu|repetiu|contou pra mim"
    r"|diz|fala|conta|comenta|garante|afirma|confessa|repete|vive dizendo|vive falando"
    r"|mandou mensagem dizendo|mandou mensagem falando"
)
_VERBOS_ME = (r"disse|contou|falou|explicou|confirmou|avisou|garantiu|respondeu|confessou"
              r"|mandou mensagem|escreveu|confidenciou")
_NEGACAO = r"(?:nao|nunca|nem|jamais)"

# "ela [até 3 palavras] (me) disse": o meio não pode ter negação ("ela não disse").
_RE_FALA_DELA = re.compile(
    r"\bela\b(?P<meio>(?:\s+[\w-]+){0,3}?)\s+(?:me\s+|nos\s+|pra\s+mim\s+|para\s+mim\s+)?"
    r"(?:" + _VERBOS_FALA + r")\b")
_RE_ME_DISSE = re.compile(r"\bme\s+(?:" + _VERBOS_ME + r")\b")
# Marcas que valem para a frase toda.
_RE_FALA_FRASE = re.compile(
    r"\bsegundo ela\b|\bnas palavras dela\b|\bde acordo com ela\b|\bnas proprias palavras\b"
    r"|\bpalavras dela\b|\bela\s*:\s*[\"“]"
    r"|[\"“][^\"”]{3,}[\"”]\s*,?\s*(?:" + _VERBOS_FALA + r")\s+ela\b")
_RE_FALA_NEGADA = re.compile(
    r"\b" + _NEGACAO + r"\s+(?:me\s+|nos\s+|pra\s+mim\s+|para\s+mim\s+)?(?:disse|contou|falou|comentou"
    r"|mencionou|explicou|confirmou|avisou|respondeu|garantiu|relatou|revelou|admitiu|confessou|afirmou"
    r"|jurou|escreveu)\b")
_RE_DUVIDA = re.compile(
    r"\b(?:nao\s+sei|nao\s+sei\s+bem|nao\s+lembro|nao\s+lembro\s+bem|nao\s+faco\s+ideia|sei\s+la)\s+se\b")

# Onde termina o que ela disse: outra oração, com outro sujeito ou outra fonte.
_RE_FIM_FALA = re.compile(
    r"[.;!?]|\s[-—]\s|,?\s+(?:mas|porem|entretanto|contudo|so\s+que)\b"
    r"|,?\s+e\s+(?:eu|a\s+gente|nos)\b|,\s*(?:eu|a\s+gente)\b"
    r"|\beu\s+(?:percebi|reparei|notei|vi|senti|achei|acho|imagino|desconfio|sei)\b")
_RE_FIM_NAO_AFIRMADO = re.compile(r"[.;!?,]|\s[-—]\s|\s+(?:mas|porem|so\s+que)\b")
# Depois do verbo de fala só isto: "Ela adora tulipas, ela disse." (o que ela disse veio antes).
_RE_SO_FECHO = re.compile(r"(?:\s+(?:isso|me|pra\s+mim|para\s+mim|hoje|ontem|uma\s+vez|de\s+novo))*\s*[.!?…]*\s*$")

# Quem falou antes do "me disse": pronome ou relação = outra pessoa ("a mãe dela me disse").
_RE_OUTRO_SUJEITO = re.compile(
    r"\b(?:ele|eles|elas|alguem|minha|meu|minhas|meus|a\s+gente|voce|todo\s+mundo|o\s+pessoal|"
    + "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(RELACOES, key=len, reverse=True))
    + r")\b")
_RE_PALAVRA_MAIUSCULA = re.compile(r"(?<![\wÀ-ÿ])[A-ZÀ-Þ][\wÀ-ÿ'-]*")
_COMUNS_NO_INICIO = frozenset(normalizar(w) for w in """
no na nos nas de do da dos das em ao aos pelo pela mais tambem ja ate sobre durante logo cedo tarde
hj entao ai depois antes hoje ontem agora quando que e so la aqui ali me
""".split()) | _NAO_NOMES


def _ela_como_objeto(p: str, inicio: int) -> bool:
    antes = re.search(r"(\w+)\s+$", p[max(0, inicio - 12):inicio])
    return bool(antes and antes.group(1) in ("com", "pra", "para", "de", "sobre", "por", "a", "sem"))


def _sujeito_e_ela(frase: str, p: str, pos: int) -> bool:
    """Antes de "me disse", na mesma oração: é ela (ou ninguém dito) quem fala?"""
    ini = max((m.end() for m in re.finditer(r"[,;:]", p[:pos])), default=0)
    antes = p[ini:pos]
    if re.search(r"\b" + _NEGACAO + r"\s*$", antes):
        return False          # "não me disse"
    if re.search(r"\bela\b", antes):
        return True
    if _RE_OUTRO_SUJEITO.search(antes):
        return False
    for m in _RE_PALAVRA_MAIUSCULA.finditer(frase, ini, pos):
        if normalizar(m.group()) not in _COMUNS_NO_INICIO:
            return False      # um nome: "A Mariana me contou"
    return True


def _fim(p: str, depois: int, rx: re.Pattern[str]) -> int:
    m = rx.search(p, depois)
    return m.start() if m else len(p)


def _falas_dela(frase: str, p: str) -> list[tuple[int, int]]:
    """Posições dos verbos de fala dela (não negados) na frase `p` (plano)."""
    achados = []
    for m in _RE_FALA_DELA.finditer(p):
        if re.search(r"\b" + _NEGACAO + r"\b", m.group("meio")) or _ela_como_objeto(p, m.start()):
            continue
        achados.append((m.start(), m.end()))
    for m in _RE_ME_DISSE.finditer(p):
        if any(a <= m.start() < b for a, b in achados):
            continue
        if _sujeito_e_ela(frase, p, m.start()):
            achados.append((m.start(), m.end()))
    return sorted(achados)


def trechos_confirmados(frase: str) -> list[tuple[int, int]]:
    """Partes da frase (posições no texto original) que relatam o que ela disse.

    Vazio quando não há fala dela. A inferência vence: com "acho que", nada é confirmado.
    """
    p = plano(frase or "")
    if not p or _INFERENCIA.search(p):
        return []
    if _RE_FALA_FRASE.search(p):
        return [(0, len(p))]
    trechos = []
    for ini, fim in _falas_dela(frase, p):
        if _RE_SO_FECHO.fullmatch(p, fim):
            trechos.append((0, fim))
        else:
            trechos.append((ini, _fim(p, fim, _RE_FIM_FALA)))
    return trechos


def trechos_nao_afirmados(frase: str) -> list[tuple[int, int]]:
    """O que vem depois de fala negada ("ela nunca disse que odeia rosas") ou de dúvida
    ("não sei se ela gosta de gato"): nada disso foi afirmado e não deve virar item."""
    p = plano(frase or "")
    trechos = []
    for rx in (_RE_FALA_NEGADA, _RE_DUVIDA):
        for m in rx.finditer(p):
            trechos.append((m.start(), _fim(p, m.end(), _RE_FIM_NAO_AFIRMADO)))
    return sorted(trechos)


def classificar_certeza(frase: str) -> Certeza:
    """CONFIRMADO para fala dela relatada, INFERENCIA para hipótese, OBSERVADO para o resto.

    Inferência vence confirmado: "acho que ela disse" é hipótese. Fala negada ("ela não
    disse") e fala de outra pessoa ("a Mariana me contou") não confirmam nada.
    """
    n = normalizar(frase or "")
    if _INFERENCIA.search(n):
        return Certeza.INFERENCIA
    if trechos_confirmados(frase or ""):
        return Certeza.CONFIRMADO
    return Certeza.OBSERVADO


__all__ = ["classificar_certeza", "trechos_confirmados", "trechos_nao_afirmados"]
