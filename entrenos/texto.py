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
    """0-100: quanto dois textos curtos (nomes, alvos de gosto) se parecem, sem acento nem caixa.

    É o `token_set_ratio` (o do rapidfuzz/fuzzywuzzy) escrito em Python puro, para dar o mesmo
    número no computador e no celular (o Pyodide não tem rapidfuzz; antes o celular caía no
    difflib, que mede outra coisa: "Ana" x "Ana Paula" dava 100 aqui e 50 lá). Um texto contido
    no outro, palavra por palavra, vale 100.
    """
    return token_set_ratio(normalizar(a), normalizar(b))


def token_set_ratio(a: str, b: str) -> float:
    """O `fuzz.token_set_ratio` do rapidfuzz 3 (sem pré-processamento), em Python puro.

    Compara os conjuntos de palavras: o que os dois têm em comum (ordenado) mais o que sobra de
    cada lado, pela distância Indel (só inserir e apagar). Nenhuma palavra = 0.
    """
    palavras_a, palavras_b = set(a.split()), set(b.split())
    if not palavras_a or not palavras_b:
        return 0.0
    comuns = palavras_a & palavras_b
    so_a, so_b = palavras_a - palavras_b, palavras_b - palavras_a
    if comuns and (not so_a or not so_b):
        return 100.0                  # um texto está contido no outro
    resto_a, resto_b = " ".join(sorted(so_a)), " ".join(sorted(so_b))
    tamanho_comum = len(" ".join(sorted(comuns)))
    separador = 1 if tamanho_comum else 0
    com_a = tamanho_comum + separador + len(resto_a)
    com_b = tamanho_comum + separador + len(resto_b)
    # "comuns + resto_a" x "comuns + resto_b": a parte comum é igual, só os restos diferem.
    resultado = _nota(_distancia_indel(resto_a, resto_b), com_a + com_b)
    if not tamanho_comum:
        return resultado
    # "comuns" x "comuns + resto": a distância é só o que sobra (com o espaço).
    return max(resultado,
               _nota(separador + len(resto_a), tamanho_comum + com_a),
               _nota(separador + len(resto_b), tamanho_comum + com_b))


def _nota(distancia: int, soma_dos_tamanhos: int) -> float:
    return 100.0 - 100.0 * distancia / soma_dos_tamanhos if soma_dos_tamanhos else 100.0


def _distancia_indel(a: str, b: str) -> int:
    """Quantas letras inserir e apagar para ir de `a` a `b`: len(a) + len(b) - 2 × maior
    subsequência comum. A subsequência sai pelo método de bits de Hyyrö (um inteiro do Python
    faz o papel do vetor de bits), rápido mesmo em Python puro."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    mascaras: dict[str, int] = {}
    for posicao, letra in enumerate(b):
        mascaras[letra] = mascaras.get(letra, 0) | (1 << posicao)
    todos = (1 << len(b)) - 1
    v = todos
    for letra in a:
        u = v & mascaras.get(letra, 0)
        v = ((v + u) | (v - u)) & todos
    comum = len(b) - bin(v).count("1")
    return len(a) + len(b) - 2 * comum


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
