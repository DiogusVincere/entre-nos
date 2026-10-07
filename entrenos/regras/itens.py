"""Extração de itens: cada frase vira preferência, evento, estado, apoio... ou nota.

Nada se perde: frase sem item reconhecido vira NOTA. O `trecho` é sempre a frase literal
(substring do texto) e o `resumo` sai por modelo de frase, só com palavras do próprio texto.
As datas chegam prontas (regras/datas.py); aqui só se escolhe qual data vai com qual item.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..modelos import CATEGORIAS, Certeza, DataEncontrada, Item, PessoaEncontrada, Precisao, TipoItem
from ..texto import data_br, frases, normalizar
from .categorias import categorias_de
from .certeza import classificar_certeza, trechos_confirmados, trechos_nao_afirmados
from .pessoas import RELACOES, e_dela, genero_relacao, grupo_relacao, plano, relacao_base  # noqa: F401

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro"]


@dataclass
class _Frase:
    ini: int                                   # posição da frase no texto
    texto: str                                 # frase literal
    p: str                                     # a mesma frase em minúsculas e sem acento (mesmo tamanho)
    certeza: Certeza
    datas: list[tuple[int, int, DataEncontrada]]         # posições relativas à frase
    pessoas: list[tuple[int, int, PessoaEncontrada]]     # idem
    ele: PessoaEncontrada | None = None        # último homem citado antes (para "ele")
    anterior: "_Frase | None" = None
    preocupada: bool = False                   # a frase ou uma vizinha fala de preocupação
    ocupado: list[tuple[int, int]] = field(default_factory=list)
    datas_usadas: set[int] = field(default_factory=set)
    confirmado: list[tuple[int, int]] = field(default_factory=list)   # o que ela disse (certeza.py)
    nao_afirmado: list[tuple[int, int]] = field(default_factory=list)  # fala negada, dúvida, ironia

    def livre(self, a: int, b: int) -> bool:
        return not any(a < y and x < b for x, y in self.ocupado)

    def ocupar(self, a: int, b: int) -> None:
        self.ocupado.append((a, b))


# ------------------------------------------------------------------ texto

_RE_INTENS = (r"(?:(?:muito|muuito|mt|mto|mo|super|hiper|tao|bem|meio|um\s+pouco|bastante|mega|extremamente"
             r"|mais|meio\s+que|toda|todo|totalmente|completamente)\s+)*")
# Negação antes de um verbo: "não", "nunca", "nem" e o "n" abreviado ("ela n gosta").
_SEM_NEGACAO = r"(?<!nao )(?<!nunca )(?<!nem )(?<!\bn )(?<!jamais )"
# Onde termina o objeto de um verbo: pontuação ou conjunção que abre outra oração.
_RE_PARADA = re.compile(
    r"[.;:!?()\"“”…]|\s[-—]\s"
    r"|,?\s+(?:e\s+|mas\s+)?(?:que|mas|porem|porque|pois|quando|enquanto|embora|entao|so\s+que"
    r"|ja\s+que|do\s+que|apesar|senao|desde)\b"
)
# Parada mais larga (para apoio, pendência, padrão): deixa passar "que", "quando", "se".
_RE_PARADA_LONGA = re.compile(
    r"[.;!?()\"“”…]|\s[-—]\s|,?\s+(?:mas|porem|porque|pois|embora|entao)\b"
    r"|,?\s+e\s+(?=(?:ela|eu|ele)\b)|,\s*(?=(?:ela|eu|ele)\b)"
)
_RE_INICIO_ALVO = re.compile(
    r"(?:(?:de|do|da|dos|das|o|a|os|as|um|uma|uns|umas|mais|muito|tambem|so|apenas|bastante)\s+)+")
_RE_FIM_ALVO = re.compile(
    r"(?:\s+(?:tambem|demais|muito|mesmo|bastante|hoje|agora|ultimamente|sempre|de\s+verdade"
    r"|pra\s+caramba|ne|dela\s+mesma|de\s+jeito\s+nenhum|nenhum|k{2,}|(?:rs){1,}s*|(?:ha){2,}h?|(?:he){2,}h?"
    r"|(?:hua){2,}|tipo|sqn))+\s*$")
_NAO_INICIA_ALVO = frozenset("""
e ou ela ele eu elas eles nao tambem principalmente especialmente sobretudo mas so muito sempre
nunca ja ainda hoje ontem agora fica ficou esta estava odeia gosta adora ama detesta tem tinha
foi vai quer prefere acha disse falou contou comentou normalmente geralmente costuma depois entao
mesmo por isso ai la tudo nada quando se me te nos
""".split())
_ALVOS_VAZIOS = frozenset("""mim voce ti nos ninguem nada isso isto aquilo tudo ela ele eles elas gente
dela dele delas deles o a os as um uma que de do da dos das e ou com em no na
muito muita tanto tanta mais menos bem pouco demais assim so nao mt mto mta muitao bastante""".split())
# (as palavras da última linha: "não curtiu muito" não é um gosto por "muito")
# Palavras que só aparecem como verbo: um pedaço da lista que tem uma delas já é outra oração
# ("ela ama gatos, o irmão dela é alérgico").
_VERBOS_DE_ORACAO = frozenset("""
esta estao ta tao estava estavam tava tavam foi foram era eram sao vai vao tem tinha fica ficou ficam
anda andou parece parecia virou continua mora morava trabalha estuda quer queria acha achou sabe
pode deve precisa gosta ama adora odeia detesta curte prefere faz fez disse falou contou""".split())
_NAO_VERBO_EM_OU = frozenset("sou vou dou estou show".split())


def _cap(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" ,;:-—")
    return s[:1].upper() + s[1:] if s else s


def _encurtar(s: str, maximo: int) -> str:
    ws = s.split()
    return " ".join(ws[:maximo]) + ("…" if len(ws) > maximo else "")


def _sem_ponto(s: str) -> str:
    return s.rstrip(" .!?…;:")


def _corte_datas(fr: _Frase, de: int, ate: int) -> int:
    for a, _, _ in fr.datas:
        if de < a < ate:
            ate = a
    return ate


def _segmento(fr: _Frase, ini: int, longo: bool = False, max_palavras: int = 12) -> int:
    """Fim (posição na frase) do objeto que começa em `ini`. A primeira palavra sempre entra."""
    m1 = re.compile(r"\s*[\w'-]+").match(fr.p, ini)
    if not m1:
        return ini
    fim = len(fr.p)
    m = (_RE_PARADA_LONGA if longo else _RE_PARADA).search(fr.p, m1.end())
    if m:
        fim = m.start()
    if not longo:
        cortado = _corte_datas(fr, m1.end(), fim)
        if cortado < fim:
            # "com a entrevista de | amanhã": a preposição que ficou antes da data sai junto.
            fim = cortado
            sobra = re.search(r"(?:\s+(?:de|do|da|dos|das|no|na|nos|nas|em|para|pra|pro|com|ate|a|o|as|os))+\s*$",
                              fr.p[m1.end():fim])
            if sobra:
                fim = m1.end() + sobra.start()
    palavras = list(re.finditer(r"[\w'-]+", fr.p[ini:fim]))
    if len(palavras) > max_palavras:
        fim = ini + palavras[max_palavras - 1].end()
    while fim > ini and fr.p[fim - 1] in " ,;:-—":
        fim -= 1
    return fim


def _aparar(fr: _Frase, a: int, b: int) -> tuple[int, int]:
    s = fr.p
    while a < b and (s[a].isspace() or s[a] in ",;:"):
        a += 1
    while b > a and (s[b - 1].isspace() or s[b - 1] in ",;:-—"):
        b -= 1
    m = _RE_INICIO_ALVO.match(s, a, b)
    if m and m.end() < b:
        a = m.end()
    m = _RE_FIM_ALVO.search(s, a, b)
    if m and m.start() > a:
        b = m.start()
    return a, b


def _ate(partes: list[tuple[int, int]], padrao: int) -> int:
    """Até onde um achado ocupa a frase: o fim do último alvo aceito (o resto fica livre)."""
    return max(partes[-1][1], padrao) if partes else padrao


def _e_verbo(fr: _Frase, a: int, b: int) -> bool:
    """O pedaço fr[a:b] tem um verbo? ("é" com acento, formas comuns, pretérito em -ou/-iu)."""
    if re.search(r"(?<![\wÀ-ÿ])[éÉ](?![\wÀ-ÿ])", fr.texto[a:b]):
        return True
    for w in re.findall(r"[\w'-]+", fr.p[a:b]):
        if w in _VERBOS_DE_ORACAO or (len(w) >= 4 and w.endswith(("ou", "iu")) and w not in _NAO_VERBO_EM_OU):
            return True
    return False


def _partes(fr: _Frase, ini: int, fim: int, max_palavras: int = 8) -> list[tuple[int, int]]:
    """Divide "sushi e comida japonesa" / "A, B e C" / "glúten nem lactose" em alvos.

    Para no que parece outra oração: pedaço com verbo ("o irmão dela é alérgico"), trecho já
    usado por outra regra, "nem tanto" ("a Camila nem tanto" não é gosto).
    """
    nem_tanto = re.search(r"\s+nem\s+(?:tanto|tao\b|muito|um\s+pouco|sempre)\b", fr.p[ini:fim])
    if nem_tanto:
        virgula = [m.start() for m in re.finditer(r",", fr.p[ini:ini + nem_tanto.start()])]
        fim = ini + virgula[-1] if virgula else ini
    limite = fim
    pedacos, pos, sujeito = [], ini, False
    for m in re.finditer(r"\s*,\s*|\s+e\s+|\s+ou\s+|\s+nem\s+", fr.p[ini:fim]):
        if m.group().strip() == "e" and fr.texto[ini + m.start():ini + m.end()].strip() in ("é", "É"):
            sujeito = True                    # "é" é verbo: o pedaço antes dele é sujeito de outra oração
            break
        pedacos.append((pos, ini + m.start()))
        pos = ini + m.end()
    if not sujeito or not pedacos:
        pedacos.append((pos, ini + m.start() if sujeito else fim))
    saida: list[tuple[int, int]] = []
    for i, (a, b) in enumerate(pedacos):
        palavras_brutas = fr.p[a:b].split()
        if i > 0:
            virgula = fr.p.find(",", b, limite)
            resto = virgula if virgula >= 0 else limite
            if (not palavras_brutas or palavras_brutas[0] in _NAO_INICIA_ALVO or len(palavras_brutas) > 4
                    or _e_verbo(fr, a, max(b, resto)) or not fr.livre(a, b)):
                break
        a, b = _aparar(fr, a, b)
        if a >= b:
            if i == 0:
                break
            continue
        palavras = list(re.finditer(r"\S+", fr.p[a:b]))
        if len(palavras) > max_palavras:
            b = a + palavras[max_palavras - 1].end()
        if normalizar(fr.p[a:b]) in _ALVOS_VAZIOS:
            if i == 0:
                break
            continue
        saida.append((a, b))
    return saida


# ------------------------------------------------------------------ de quem é

_RE_ORACAO = re.compile(
    r"[;:]|,?\s+(?:e\s+)?que\s+|\s+mas\s+|\s+porque\s+|\s+pois\s+|\s+quando\s+|\s+enquanto\s+"
    r"|,?\s+e\s+(?=ela\b)"
    # ", a mãe dela odeia rosas" / "e a defesa é": outro sujeito começa outra oração
    # (mas "ela e a Mariana adoram" é um sujeito só).
    r"|,\s*(?=(?:o|a|os|as)\s)|(?<!\bela)\s+e\s+(?=(?:o|a|os|as)\s+\w)")
_RE_ELA = re.compile(r"\bela\b")
_PREPOSICOES = frozenset("com para pra de sobre por sem a contra entre ate perto".split())
# "pedir ela em casamento", "levou ela", "convidou ela": aqui "ela" é objeto, não sujeito.
_VERBOS_COM_OBJETO = frozenset("""
pedir pediu pede levar levou leva chamar chamou chama convidar convidou convida ajudar ajudou ajuda
buscar buscou busca pegar pegou deixar deixou ver viu vi beijar beijou abracar abracou encontrar encontrou
conhecer conheceu cobrar cobrou cobrando magoar magoou xingar xingou trair traiu largar largou visitar
visitou elogiar elogiou esperar esperou esperando tratar tratou tratando levando chamando ajudando
""".split())


def _ela_sujeito(p: str, ini: int = 0, fim: int | None = None) -> bool:
    """Há um "ela" que não é objeto ("com ela", "para ela", "pedir ela") em p[ini:fim]?"""
    for m in _RE_ELA.finditer(p, ini, len(p) if fim is None else fim):
        antes = re.search(r"(\w+)\s+$", p[max(0, m.start() - 16):m.start()])
        if not antes or (antes.group(1) not in _PREPOSICOES and antes.group(1) not in _VERBOS_COM_OBJETO):
            return True
    return False


def _oracao(fr: _Frase, pos: int) -> tuple[int, int]:
    ini, fim = 0, len(fr.p)
    for m in _RE_ORACAO.finditer(fr.p):
        # Vírgula de aposto ("o chefe dela, o Marcos, ...") fica dentro da pessoa: não separa.
        if any(a <= m.start() < b for a, b, _ in fr.pessoas):
            continue
        if m.end() <= pos:
            ini = m.end()
        elif m.start() >= pos:
            fim = m.start()
            break
    return ini, fim


def _dono(fr: _Frase, a: int, b: int) -> PessoaEncontrada | None:
    """De quem é o que está em [a, b): None = dela; senão a pessoa citada como sujeito."""
    ci, _ = _oracao(fr, a)
    if _ela_sujeito(fr.p, ci, a):
        return None
    antes = [x for x in fr.pessoas if ci <= x[0] and x[1] <= a]
    if antes:
        return antes[-1][2]
    for x in fr.pessoas:  # "cirurgia da mãe dela", "aniversário da Mariana"
        if b <= x[0] and re.fullmatch(r"\s+(?:d[eoa]s?|com|para|pra)\s+(?:(?:a|o)\s+)?", fr.p[b:x[0]]):
            return x[2]
    if re.search(r"\bele\b", fr.p[ci:a]):
        homens = [x[2] for x in fr.pessoas if x[1] <= ci and genero_relacao(x[2].relacao) == "m"]
        return homens[-1] if homens else fr.ele
    return None


def _nome_pessoa(pe: PessoaEncontrada) -> str:
    return pe.nome or pe.relacao or pe.trecho


def _rotulo(pe: PessoaEncontrada) -> str:
    return _cap(_nome_pessoa(pe))


def _de_pessoa(pe: PessoaEncontrada | None) -> str:
    """'dela', 'de Mariana', 'da mãe', 'do pai', 'da sua mãe'."""
    if pe is None:
        return "dela"
    if pe.nome:
        return f"de {pe.nome}"
    rel = pe.relacao or ""
    base = relacao_base(rel)
    genero = genero_relacao(rel)
    if rel.endswith("(sua)") or rel.endswith("(seu)"):
        return f"da sua {base}" if genero == "f" or rel.endswith("(sua)") else f"do seu {base}"
    if genero == "f":
        return f"da {rel}"
    if genero == "m":
        return f"do {rel}"
    return f"de {rel}"


_CAT_GRUPO = {
    "familia": ["Família"], "importante": ["Pessoas importantes", "Amigos"], "amigos": ["Amigos"],
    "trabalho": ["Trabalho"], "estudos": ["Estudos"], "saude": ["Saúde"], "ex": ["Relacionamento"],
    "pet": ["Família"],
}


def _cats_pessoa(pe: PessoaEncontrada | None) -> list[str]:
    if pe is None:
        return []
    return list(_CAT_GRUPO.get(grupo_relacao(pe.relacao) or "", []))


def _filtrar(cats: list[str], limite: int = 5) -> list[str]:
    saida: list[str] = []
    for c in cats:
        if c in CATEGORIAS and c not in saida:
            saida.append(c)
    return saida[:limite]


_SEM_DONO = object()


def _item(fr: _Frase, tipo: TipoItem, resumo: str, a: int, b: int, categorias: list[str],
          pessoa=_SEM_DONO, certeza: Certeza | None = None, frase_cats: bool = True, **campos) -> Item:
    """Monta o item. `frase_cats=False` quando a frase tem vários alvos e as categorias da
    frase inteira misturariam um com o outro."""
    if pessoa is _SEM_DONO:
        pessoa = _dono(fr, a, b)
    if certeza is None:
        certeza = fr.certeza
        # A fala dela confirma só o que ela disse: "Ela disse que está cansada e eu percebi que...".
        if certeza == Certeza.CONFIRMADO and not any(x <= a < y for x, y in fr.confirmado):
            certeza = Certeza.OBSERVADO
    cats = list(categorias) + _cats_pessoa(pessoa) + (categorias_de(fr.texto, 3) if frase_cats else [])
    if pessoa is not None and tipo != TipoItem.DESEJO:
        # "a Mariana vai casar", "o irmão dela vai se formar": os sonhos e objetivos são dela.
        cats = [c for c in cats if c not in ("Sonhos", "Objetivos")]
    if pessoa is not None and not resumo.startswith(_rotulo(pessoa)) and tipo not in (
            TipoItem.DATA_IMPORTANTE, TipoItem.NOTA):
        resumo = f"{_rotulo(pessoa)}: {resumo[:1].lower() + resumo[1:] if not resumo[:2].isupper() else resumo}"
    return Item(tipo=tipo, resumo=resumo, trecho=fr.texto, certeza=certeza,
                categorias=_filtrar(cats),
                pessoa=_nome_pessoa(pessoa) if pessoa is not None else None,
                pessoa_id=pessoa.pessoa_id if pessoa is not None else None, **campos)


# ------------------------------------------------------------------ datas

def _quando(d: DataEncontrada, recorrente: bool = False) -> tuple[str, str]:
    """(conector, texto): ("—", "20/10/2026"), ("em", "novembro/2026"), ("", "(sexta que vem)")."""
    if d.data is None:
        return "", f"({d.expressao})"
    mes = _MESES[d.data.month - 1]
    if d.precisao == Precisao.MES:
        return "em", mes if recorrente else f"{mes}/{d.data.year}"
    if d.precisao == Precisao.ANO:
        return "em", str(d.data.year)
    if d.precisao == Precisao.SEMANA:
        return "na", f"semana de {data_br(d.data)}"
    if recorrente:
        return "—", f"{d.data.day:02d}/{d.data.month:02d}"
    return "—", data_br(d.data)


def _com_data(base: str, d: DataEncontrada, recorrente: bool = False) -> str:
    conector, quando = _quando(d, recorrente)
    return f"{base} {conector} {quando}" if conector else f"{base} {quando}"


def _campos_data(d: DataEncontrada) -> dict:
    return {"data_evento": d.data, "data_expressao": d.expressao, "precisao": d.precisao,
            "recorrente": d.recorrente}


def _fim_periodo(d: DataEncontrada) -> date:
    """Último dia do período da data (o próprio dia, o domingo da semana, o fim do mês ou do ano)."""
    if d.precisao == Precisao.SEMANA:
        return d.data + timedelta(days=6)
    if d.precisao == Precisao.MES:
        return (d.data.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if d.precisao == Precisao.ANO:
        return date(d.data.year, 12, 31)
    return d.data


def _passou(d: DataEncontrada, referencia: date) -> bool:
    """O período inteiro já terminou ("semana de 28/09" ainda não passou no dia 30/09)."""
    if d.passado or d.data is None:
        return d.passado
    return _fim_periodo(d) < referencia


def _por_vir(d: DataEncontrada, referencia: date) -> bool:
    """Ainda vai acontecer: dia depois de hoje, ou período que não terminou e não é só hoje."""
    if d.data is None or _passou(d, referencia):
        return False
    return d.data > referencia or (d.precisao not in (None, Precisao.DIA) and _fim_periodo(d) > referencia)


def _data_mais_perto(fr: _Frase, a: int, b: int, referencia: date,
                     livres: bool = True) -> int | None:
    """Índice (em fr.datas) da data mais próxima de [a, b); prefere as que vêm depois."""
    melhor, escolhida = None, None
    for i, (da, db, d) in enumerate(fr.datas):
        if livres and i in fr.datas_usadas:
            continue
        nota = _distancia(fr, a, b, da, db, d, referencia)
        if nota is not None and (melhor is None or nota < melhor):
            melhor, escolhida = nota, i
    return escolhida


def _distancia(fr: _Frase, a: int, b: int, da: int, db: int, d: DataEncontrada,
               referencia: date) -> float | None:
    nota = (da - b) if da >= b else (a - db) * 1.5 + 5
    if nota > 150:
        return None
    # "Hoje ela me contou que..." é a data do relato, não a do evento.
    if normalizar(d.expressao) == "hoje" and d.data == referencia and len(fr.datas) > 1:
        nota += 200
    return nota


# ------------------------------------------------------------------ apoio

_QUEM = r"(?:eu|a\s+gente|alguem|as\s+pessoas|os\s+outros)"
_RE_PREFERE_QUE = re.compile(r"\bprefere\s+que\s+(?P<q>eu|a\s+gente)\s+")
_APOIOS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b(?:(?:nao|n)\s+gosta|odeia|detesta|(?:nao|n)\s+suporta|(?:nao|n)\s+aguenta|se\s+irrita"
                r"|fica\s+(?:chateada|irritada|brava|pior))\s+(?P<c>que|quando)\s+" + _QUEM + r"\s+"), -1),
    (re.compile(r"\b(?:nao|n)\s+quer\s+(?P<c>que)\s+(?:eu|a\s+gente)\s+"), -1),
    (re.compile(_SEM_NEGACAO + r"\b(?:gosta|adora|ama|curte)\s+(?P<c>que|quando)\s+" + _QUEM + r"\s+"), 1),
    (re.compile(r"\b(?:ajuda|acalma|conforta)\s+(?:muito\s+|bastante\s+)?(?P<c>quando)\s+" + _QUEM + r"\s+"), 1),
    (re.compile(r"\b(?:se\s+sente|fica)\s+melhor\s+(?P<c>quando|com|se)\s+"), 1),
    (re.compile(r"\bo\s+que\s+(?:mais\s+)?(?:a\s+)?(?:ajuda|acalma)(?:\s+ela)?\s+(?:e|sao)\s+"), 1),
    (re.compile(r"\bo\s+que\s+(?:nao\s+ajuda|atrapalha|piora)\s+(?:e|sao)\s+"), -1),
    (re.compile(r"\bprecisa\s+de\s+(?=espaco|um\s+tempo|tempo|colo|carinho|um\s+abraco|abraco|atencao"
                r"|silencio|ficar\s+sozinha|desabafar|conversar|um\s+ombro)"), 1),
    (re.compile(r"\b(?:quer|prefere|gosta\s+de|precisa|costuma)\s+(?=ficar\s+sozinha\b)"), 1),
]
_RE_ENFEITE = re.compile(r"\b(?:apenas|s[oó]|somente|simplesmente)\s+", re.IGNORECASE)
_RE_FIM_APOIO = re.compile(r"\s+(?:dela|com\s+ela|de\s+mim|pra\s+ela|para\s+ela)$", re.IGNORECASE)


def _limpar_apoio(s: str) -> str:
    return _RE_FIM_APOIO.sub("", _RE_ENFEITE.sub("", re.sub(r"\s+", " ", s).strip()))


def _apoios(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    cats = ["Relacionamento"]
    for m in _RE_PREFERE_QUE.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end(), longo=True, max_palavras=16)
        quem = fr.texto[m.start("q"):m.end("q")]
        sep = re.compile(r"\s+e\s+(?:que\s+)?(?:eu\s+|a\s+gente\s+)?(?:nao|nunca)\s+").search(fr.p, m.end(), fim)
        partes = [(m.end(), sep.start() if sep else fim, 1)]
        if sep:
            partes.append((sep.end(), fim, -1))
        for a, b, pol in partes:
            neg = re.match(r"(?:nao|nunca)\s+", fr.p[a:b])
            if neg:
                a, pol = a + neg.end(), -1
            acao = _limpar_apoio(fr.texto[a:b])
            if not acao:
                continue
            resumo = f"Prefere que {quem} {acao}" if pol > 0 else f"Prefere que {quem} não {acao}"
            itens.append(_item(fr, TipoItem.APOIO, resumo, m.start(), b, cats,
                               alvo=f"que {quem} {acao}", polaridade=pol))
        fr.ocupar(m.start(), fim)
    for rx, pol in _APOIOS:
        for m in rx.finditer(fr.p):
            if not fr.livre(m.start(), m.end()):
                continue
            fim = _segmento(fr, m.end(), longo=True, max_palavras=12)
            if fim <= m.end():
                continue
            ini_alvo = m.start("c") if "c" in rx.groupindex and m.group("c") else m.end()
            alvo = _limpar_apoio(fr.texto[ini_alvo:fim])
            resumo = _cap(fr.texto[m.start():fim])
            if rx.pattern.startswith(r"\bo\s+que"):
                resumo = f"{'O que ajuda' if pol > 0 else 'O que não ajuda'}: {alvo}"
            itens.append(_item(fr, TipoItem.APOIO, resumo, m.start(), fim, cats,
                               alvo=alvo or None, polaridade=pol))
            fr.ocupar(m.start(), fim)
    return itens


# ------------------------------------------------------------------ restrição

_RE_RESTRICAO = re.compile(
    r"\b(?:(?P<alergica>e\s+alergic[ao])|(?P<alergia>tem\s+alergia)|(?P<intolerante>e\s+intolerante)"
    r"|(?P<intolerancia>tem\s+intolerancia))\s+(?:a|ao|aos|as|de|d[oa]s?)\s+"
    r"|\b(?:nao|n)\s+(?:pode|deve|consegue)\s+(?P<vp>comer|beber|tomar|consumir|ingerir)\s+"
    r"|\b(?:nao|n)\s+(?P<vc>come|bebe)\s+(?!mais\s+nada\b|nada\b)"
    # "parou de beber vinho" é como "não bebe mais vinho" (revisão de 07/10).
    r"|\b(?<!se\s)(?:parou|deixou)\s+de\s+(?P<vparou>comer|beber)\s+(?!nada\b)"
)
_RESTRICOES_FIXAS = [
    (re.compile(r"\be\s+vegetarian[ao]\b"), "É vegetariana", "carne"),
    (re.compile(r"\be\s+vegan[ao]\b"), "É vegana", "alimentos de origem animal"),
    (re.compile(r"\be\s+celiac[ao]\b"), "É celíaca", "glúten"),
    (re.compile(r"\be\s+diabetic[ao]\b"), "É diabética", "açúcar"),
]


def _restricoes(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_RESTRICAO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        if m.group("alergica"):
            modelo = "Alérgica a" if fr.p[m.end("alergica") - 1] == "a" else "Alérgico a"
        elif m.group("alergia"):
            modelo = "Alergia a"
        elif m.group("intolerante") or m.group("intolerancia"):
            modelo = "Intolerante a"
        elif m.group("vp"):
            modelo = f"Não pode {m.group('vp')}"
        elif m.group("vparou"):
            modelo = "Não come" if m.group("vparou") == "comer" else "Não bebe"
        else:
            modelo = f"Não {m.group('vc')}"
        comida = bool(m.group("vp") or m.group("vc") or m.group("vparou"))
        fim = _segmento(fr, m.end())
        partes = _partes(fr, m.end(), fim)
        for a, b in partes:
            alvo = fr.texto[a:b]
            cats_alvo = categorias_de(alvo, 2)
            cats = ["Saúde"]
            if comida or "Comidas" in cats_alvo or "Bebidas" in cats_alvo:
                cats += ["Bebidas"] if (m.group("vp") in ("beber",) or m.group("vc") == "bebe" or m.group("vparou") == "beber"

                                        or ("Bebidas" in cats_alvo and "Comidas" not in cats_alvo)) else ["Comidas"]
            itens.append(_item(fr, TipoItem.RESTRICAO, f"{modelo} {alvo}", m.start(), b, cats + cats_alvo,
                               frase_cats=False, importancia=3, alvo=alvo, polaridade=-1))
        fr.ocupar(m.start(), _ate(partes, m.end()))
    for rx, resumo, alvo in _RESTRICOES_FIXAS:
        for m in rx.finditer(fr.p):
            if fr.livre(m.start(), m.end()):
                itens.append(_item(fr, TipoItem.RESTRICAO, resumo, m.start(), m.end(), ["Saúde", "Comidas"],
                                   importancia=3, alvo=alvo, polaridade=-1))
                fr.ocupar(m.start(), m.end())
    return itens


# ------------------------------------------------------------------ preferências

_ADJ_BOM = r"feliz|contente|alegre|radiante|emocionada|encantada|animada|empolgada|grata"
_ADJ_RUIM = (r"chateada|irritada|incomodada|brava|triste|magoada|chocada|decepcionada|frustrada"
             r"|ofendida|com\s+raiva|puta|p\s+da\s+vida")
_RE_REACAO = re.compile(
    r"\b(?:ficou|fica|ficava)\s+" + _RE_INTENS + r"(?P<bom>" + _ADJ_BOM + r")\s+(?:quando|com|ao|por)\s+"
    r"|\b(?:ficou|fica|ficava)\s+" + _RE_INTENS + r"(?P<ruim>" + _ADJ_RUIM
    + r")\s+(?:quando|com|porque|por\s+causa\s+d[eoa]s?|ao)\s+"
    r"|\b(?P<amou>amou|adorou|curtiu\s+muito)\s+(?:quando\s+)?"
    r"|\bse\s+(?P<emocionou>emocionou)\s+(?:quando|com|ao)\s+"
)
_RE_VERBO_GANHO = re.compile(
    r"(?:eu\s+|a\s+gente\s+)?(?:ganhou|recebeu|ganha|recebe|ganhava|recebia|dei|levei|comprei|mandei"
    r"|fomos|a\s+gente\s+foi|vimos|comeu|comemos|assistimos|ouviu|viu|fui)\s+(?:de\s+presente\s+)?")
_RE_GOSTA = re.compile(
    _SEM_NEGACAO + r"\b(?P<v>"
    r"(?:gosta(?:va|m)?|gostou|(?:comecou|passou|aprendeu)\s+a\s+gostar)"
    r"(?:\s+(?:muito|bastante|demais|tanto|mais))?\s+(?:de|d[oa]s?)"
    r"|adora(?:va|m)?|adorando|ama(?:va|m)?|amando|curte|curtem|curtia|curtindo|prefere|preferia"
    r"|e\s+(?:apaixonada|louca|doida|tarada)\s+(?:por|pel[oa]s?)|e\s+fa\s+(?:de|d[oa]s?)"
    r"|e\s+viciada\s+em|tem\s+paixao\s+(?:por|pel[oa]s?)"
    r")\s+(?!que\b|quando\b|se\b)"
)
_RE_NAO_GOSTA = re.compile(
    r"\b(?P<v>(?:nao|nunca|nem|n)\s+(?:gosta(?:va|m)?|gostou|curte|curtia|curtiu)"
    r"(?:\s+(?:muito|tanto|mais|nada))?(?:\s+(?:de|d[oa]s?))?"
    r"|(?:nao|nunca|n)\s+(?:suporta|aguenta|tolera)"
    r"|" + _SEM_NEGACAO + r"(?:odeia|odeiam|odiava|detesta|detestam|detestava|tem\s+(?:nojo|ranco|birra)\s+(?:de|d[oa]s?)"
    r"|abomina)"
    r")\s+(?!que\b|quando\b|se\b)"
)
# "Ela odeia quando o Uber cancela": o que incomoda (ou agrada) é a situação inteira.
_RE_QUANDO_ALGUEM = re.compile(
    _SEM_NEGACAO + r"\b(?P<v>odeia|detesta|(?:nao|n)\s+gosta|(?:nao|n)\s+suporta|(?:nao|n)\s+aguenta"
    r"|ama|adora|gosta)\s+(?P<q>quando)\s+(?!eu\b|a\s+gente\b)")
# "Ela adora o trabalho novo, só que não": ironia, nada de gosto ou desgosto.
_RE_IRONIA = re.compile(r"\bso\s+que\s+nao\s*[.!?…)]*\s*$|\b#?sqn\b")
_RE_TUDO_MENOS = re.compile(r"(?:de\s+)?tudo\s+(?:menos|exceto|fora)\s+(?:(?:de|d[oa]s?)\s+)?")
_RE_INFANCIA = re.compile(
    r"\bquando\s+(?:era|ela\s+era)\s+(?:crianca|pequena|menina|mais\s+nova|adolescente)\b|\bna\s+infancia\b"
    r"|\bde\s+pequena\b|\bquando\s+pequena\b|\bantigamente\b|\bna\s+adolescencia\b")
# Reação a algo assim é um momento (ESTADO), não um gosto.
_OBJETOS_VAGOS = frozenset("""noticia noticias novidade novidades coisa coisas situacao historia resultado
resposta conversa mensagem ligacao isso aquilo tudo dia semana""".split())
_RE_ESPECIAL = re.compile(
    r"\b(?:tem|tenha|tenham|tinha|tinham)\s+(?:um\s+)?(?:significado|valor)\s+(?:muito\s+)?"
    r"(?:especial|sentimental|enorme|grande)|\bsignificam?\s+muito|\be\s+(?:muito\s+)?especial\s+(?:pra|para)\s+ela")


def _reacoes(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    certeza = Certeza.OBSERVADO if fr.certeza == Certeza.CONFIRMADO else fr.certeza
    for m in _RE_REACAO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        ruim = bool(m.group("ruim"))
        fim = _segmento(fr, m.end())
        ini_alvo = m.end()
        g = _RE_VERBO_GANHO.match(fr.p, ini_alvo, fim)
        if g and g.end() < fim:
            ini_alvo = g.end()
        partes = _partes(fr, ini_alvo, fim)
        if not partes or (len(partes) == 1 and normalizar(fr.p[partes[0][0]:partes[0][1]]) in _OBJETOS_VAGOS):
            continue  # "ficou irritada com isso", "ficou triste com a notícia": fica para o ESTADO
        for a, b in partes:
            alvo = fr.texto[a:b]
            resumo = _cap(fr.texto[m.start():ini_alvo] + " " + fr.texto[a:b]) if len(partes) > 1 \
                else _cap(fr.texto[m.start():b])
            principal = ["Coisas que incomodam"] if ruim else ["Coisas que deixam feliz"]
            itens.append(_item(fr, TipoItem.PREFERENCIA, resumo, m.start(), b,
                               principal + categorias_de(alvo, 2) + ["Preferências"], frase_cats=len(partes) == 1,
                               certeza=certeza, alvo=alvo, polaridade=-1 if ruim else 1))
        fr.ocupar(m.start(), _ate(partes, m.end()))
    return itens


_RE_CORTE_PREFERE = re.compile(r"\s+(?:a|ao|a\s+o|do\s+que|em\s+vez\s+de|ao\s+inves\s+de)\s+")
# "feito à mão", "à noite": a crase aqui não é o "a" de "prefere X a Y".
_RE_LOCUCAO_CRASE = re.compile(r"(?:mao|noite|tarde|vista|toa|vontade|beca|moda|parte|distancia|milanesa|luz)\b")


def _corte_prefere(fr: _Frase, ini: int, fim: int) -> int:
    for c in _RE_CORTE_PREFERE.finditer(fr.p, ini, fim):
        palavra = c.group().strip()
        crase = palavra == "a" and fr.texto[c.start():c.end()].strip() in ("à", "À")
        if crase and _RE_LOCUCAO_CRASE.match(fr.p, c.end()):
            continue
        return c.start()
    return fim


def _preferencias(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_QUANDO_ALGUEM.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end(), longo=True, max_palavras=10)
        if fim <= m.end():
            continue
        pol = -1 if re.match(r"ode|odi|detest|nao|n\s", m.group("v")) else 1
        situacao = fr.texto[m.start("q"):fim].strip()
        resumo = f"{'Gosta' if pol > 0 else 'Não gosta'} {situacao}"
        principal = ["Coisas que deixam feliz"] if pol > 0 else ["Coisas que incomodam"]
        itens.append(_item(fr, TipoItem.PREFERENCIA, resumo, m.start(), fim, principal + ["Preferências"],
                           importancia=3 if pol < 0 and re.match(r"ode|odi|detest", m.group("v")) else 2,
                           alvo=situacao, polaridade=pol))
        fr.ocupar(m.start(), fim)
    achados = sorted([(m, -1) for m in _RE_NAO_GOSTA.finditer(fr.p)] + [(m, 1) for m in _RE_GOSTA.finditer(fr.p)],
                     key=lambda x: (x[0].start(), x[1]))
    achados = [(m, pol) for m, pol in achados if fr.livre(m.start(), m.end())]
    sozinho = len(achados) == 1 and not fr.ocupado   # só então as categorias da frase valem para o alvo
    for m, pol in achados:  # na ordem do texto
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end())
        v = m.group("v")
        if v.startswith("prefer"):
            fim = _corte_prefere(fr, m.end(), fim)
        ini = m.end()
        tudo_menos = _RE_TUDO_MENOS.match(fr.p, ini, fim)
        if tudo_menos:                     # "gosta de tudo menos de peixe": não gosta de peixe
            ini, pol = tudo_menos.end(), -pol
        forte = pol < 0 and re.match(r"ode|odi|detest|nunca|abomin", v)
        # "Adorava ir no sítio do avô quando era criança": lembrança, não gosto de agora.
        lembranca = pol > 0 and re.match(r"\w+(?:ava|ia)\b", v) and _RE_INFANCIA.search(fr.p)
        partes = _partes(fr, ini, fim)
        for a, b in partes:
            alvo = fr.texto[a:b]
            if lembranca:
                verbo = fr.texto[m.start("v"):m.end("v")]
                itens.append(_item(fr, TipoItem.FATO, _cap(f"{verbo} {alvo}"), m.start(), b,
                                   ["Infância", "Experiências importantes"] + categorias_de(alvo, 2),
                                   frase_cats=False, alvo=alvo, polaridade=pol))
                continue
            resumo = f"Gosta de {alvo}" if pol > 0 else f"Não gosta de {alvo}"
            extra = ["Preferências"] + (["Coisas que incomodam"] if pol < 0 else [])
            itens.append(_item(fr, TipoItem.PREFERENCIA, resumo, m.start(), b,
                               categorias_de(alvo, 2) + extra, importancia=3 if forte else 2,
                               frase_cats=sozinho and len(partes) == 1, alvo=alvo, polaridade=pol))
        fr.ocupar(m.start(), _ate(partes, m.end()))
    return itens


def _especiais(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_ESPECIAL.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        ci, _ = _oracao(fr, m.start())
        antes = fr.p[ci:m.start()]
        lixo = re.match(r"\s*(?:(?:talvez|acho\s+que|eu\s+acho\s+que|acredito\s+que|provavelmente|parece\s+que"
                        r"|que|ela\s+acha\s+que|pra\s+ela|para\s+ela)\s+)*", antes)
        a = ci + (lixo.end() if lixo else 0)
        a, b = _aparar(fr, a, m.start())
        if a >= b or len(fr.p[a:b].split()) > 6:
            continue
        alvo = fr.texto[a:b]
        itens.append(_item(fr, TipoItem.PREFERENCIA, f"Significado especial: {alvo}", a, m.end(),
                           ["Preferências"] + categorias_de(alvo, 2), pessoa=None,
                           alvo=alvo, polaridade=1))
        fr.ocupar(a, m.end())
    return itens


# ------------------------------------------------------------------ aniversários e eventos

_RE_ANIVERSARIO = re.compile(
    r"\b(?:aniversario(?P<namoro>\s+de\s+namoro)?|niver|faz\s+anos|fazer\s+anos|completa\s+(?:\d+\s+)?anos"
    r"|bodas|mesversario)\b")
# Logo antes da data: ela é o prazo de outra coisa ("até dia 25", "antes do dia 25").
_RE_PRAZO_ANTES = re.compile(r"\b(?:ate|antes\s+d[eoa]s?)\s+(?:o\s+|a\s+)?$")


def _aniversarios(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_ANIVERSARIO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        i = _data_mais_perto(fr, m.start(), m.end(), referencia)
        if i is None:
            continue
        # "comprar o presente de aniversário dela até dia 25": a data é um prazo, não o aniversário.
        if _RE_PRAZO_ANTES.search(fr.p, 0, fr.datas[i][0]):
            continue
        d = fr.datas[i][2]
        fr.datas_usadas.add(i)
        if m.group("namoro") or "mesversario" in m.group():
            pessoa, base, cats = None, _cap(fr.texto[m.start():m.end()]), ["Datas importantes", "Relacionamento"]
        else:
            pessoa = None if re.match(r"\s+dela\b", fr.p[m.end():]) else _dono(fr, m.start(), m.end())
            base, cats = f"Aniversário {_de_pessoa(pessoa)}", ["Aniversários", "Datas importantes"]
        campos = _campos_data(d)
        campos["recorrente"] = True
        itens.append(_item(fr, TipoItem.DATA_IMPORTANTE, _com_data(base, d, recorrente=True),
                           m.start(), m.end(), cats, pessoa=pessoa, importancia=3, **campos))
        fr.ocupar(m.start(), m.end())
    return itens


# (padrão, nome no resumo, plural para "antes de ...", memória compartilhada?)
_EVENTOS: list[tuple[re.Pattern[str], str | None, str, bool]] = [
    (re.compile(p), nome, plural, memoria) for p, nome, plural, memoria in [
        (r"\bprimeiro\s+encontro\b", "Primeiro encontro", "encontros", True),
        (r"\bprimeiro\s+beijo\b", "Primeiro beijo", "encontros", True),
        (r"\bnossa\s+viagem\b", "Nossa viagem", "viagens", True),
        (r"\bconheci\s+(?:a|o|os|as)\s+(?:familia|pais|mae|pai|irma|irmao|amiga|amigas|amigos|avo|avos)\s+dela\b",
         None, "encontros", True),
        (r"\b(?:fomos|a\s+gente\s+foi|nos\s+fomos|viajamos|saimos|jantamos|assistimos)\b", None, "passeios", True),
        (r"\bapresent\w*", "Apresentação", "apresentações", False),
        (r"\b(?:provas?|provao)\b", "Prova", "provas", False),
        (r"\bentrevistas?\b", "Entrevista", "entrevistas", False),
        (r"\b(?:cirurgias?|operacao|operar|se\s+opera)\b", "Cirurgia", "cirurgias", False),
        (r"\bconsultas?\b", "Consulta", "consultas", False),
        (r"\bexames?\b", "Exame", "exames", False),
        (r"\b(?:viagem|viagens|viajar|viaja|viajam|viajou)\b", "Viagem", "viagens", False),
        (r"\bshows?\b", "Show", "shows", False),
        (r"\b(?:casamento|casar|se\s+casa|casam)\b", "Casamento", "casamentos", False),
        (r"\b(?:formatura|se\s+formar|se\s+forma)\b", "Formatura", "formaturas", False),
        (r"\b(?:reuniao|reunioes)\b", "Reunião", "reuniões", False),
        (r"\b(?:festas?|festinha)\b", "Festa", "festas", False),
        (r"\b(?:mudanca|se\s+mudar|se\s+muda|vai\s+mudar)\b", "Mudança", "mudanças", False),
        (r"\b(?:defesa|defender)\b", "Defesa", "defesas", False),
        (r"\btcc\b", "TCC", "o TCC", False),
        (r"\bvestibular\b", "Vestibular", "vestibulares", False),
        (r"\bconcursos?\b", "Concurso", "concursos", False),
        (r"\b(?:voo|voos|voar)\b", "Voo", "voos", False),
        (r"\baudiencia\b", "Audiência", "audiências", False),
        (r"\bcursos?\b", "Curso", "cursos", False),
        (r"\baulas?\b", "Aula", "aulas", False),
        (r"\bjogos?\b", "Jogo", "jogos", False),
        (r"\bpalestras?\b", "Palestra", "palestras", False),
        (r"\bdentista\b", "Dentista", "dentista", False),
        (r"\bbatizado\b", "Batizado", "batizados", False),
        (r"\bcha\s+(?:de\s+bebe|de\s+panela|revelacao|de\s+cozinha)\b", None, "festas", False),
        (r"\b(?:recital|espetaculo|peca\s+de\s+teatro)\b", None, "apresentações", False),
        (r"\bplantao\b", "Plantão", "plantões", False),
    ]
]
_EVENTOS_SAUDE = {"Cirurgia", "Consulta", "Exame", "Dentista"}
_RE_QUALIFICADOR = re.compile(
    r"\s+(?:no|na|em|do|da)\s+(?:trabalho|faculdade|facul|escola|empresa|hospital|clinica|academia|igreja"
    r"|colegio|curso|banco|escritorio)\b")
_RE_DE_ASSUNTO = re.compile(r"\s+(?:de|do|da)\s+(?!ela\b|dela\b|mae\b|pai\b|irma\b|irmao\b|namoro\b)([a-z]{3,})\b")
_RE_DESTINO = re.compile(r"\s+(?:para|pra|pro|em)\s+([A-ZÀ-Þ][\wÀ-ÿ]+(?:\s+(?:de\s+)?[A-ZÀ-Þ][\wÀ-ÿ]+)*)")


def _palavras_chave(fr: _Frase) -> list[tuple[int, int, str | None, str, bool]]:
    achadas: list[tuple[int, int, str | None, str, bool]] = []
    for rx, nome, plural, memoria in _EVENTOS:
        for m in rx.finditer(fr.p):
            if any(m.start() < y and x < m.end() for x, y, *_ in achadas):
                continue
            achadas.append((m.start(), m.end(), nome, plural, memoria))
    achadas.sort()
    return achadas


def _base_evento(fr: _Frase, a: int, b: int, nome: str | None, memoria: bool) -> str:
    if nome is None:
        fim = _segmento(fr, a, max_palavras=7) if memoria else b
        return _cap(fr.texto[a:max(fim, b)])
    base = nome
    if not re.search(r"(?:ar|er|ir)$", fr.p[a:b]):
        assunto = _RE_DE_ASSUNTO.match(fr.p, b)
        if assunto and not any(assunto.start() <= x < assunto.end() for x, _, _ in (*fr.pessoas, *fr.datas)):
            base += fr.texto[assunto.start():assunto.end()].rstrip()
            b = assunto.end()
    q = _RE_QUALIFICADOR.search(fr.p, b, min(len(fr.p), b + 35))
    if q:
        base += " " + fr.texto[q.start():q.end()].strip()
    if nome in ("Viagem", "Voo", "Mudança"):
        destino = _RE_DESTINO.search(fr.texto, b, min(len(fr.texto), b + 40))
        if destino:
            base += " " + fr.texto[destino.start():destino.end()].strip()
    return base


# "o resultado do exame (sai semana que vem)": o exame já foi; a data é do resultado.
_RE_RESULTADO_DE = re.compile(r"\bresultados?\s+d[eoa]s?\s+(?:\w+\s+)?$")
# "perguntar dos exames da mãe dela na sexta": a sexta é de perguntar, não a do exame (integração, 20.10).
_RE_PERGUNTAR_DE = re.compile(r"\bpergunt(?:ar|ei|o|e|amos)\s+(?:sobre|d[eoa]s?|como\s+(?:foi|foram|vai|vao))\s+"
                              r"(?:[oa]s?\s+)?$")
# "semana passada brigou por causa do casamento": a data passada é da briga, não do casamento.
_RE_POR_CAUSA_DE = re.compile(r"\bpor\s+causa\s+d[eoa]s?\s+(?:\w+\s+)?$")
_RE_RECORRENTE_EVENTO = re.compile(r"\b(?:todo|todos\s+os|toda)\s+anos?\b|\banualmente\b|\baniversario\b")


def _campos_evento(fr: _Frase, d: DataEncontrada) -> dict:
    """Datas comemorativas são recorrentes, mas o evento que cai nelas não ("casamento no natal")."""
    campos = _campos_data(d)
    campos["recorrente"] = bool(d.recorrente and _RE_RECORRENTE_EVENTO.search(fr.p))
    return campos


def _eventos(fr: _Frase, referencia: date) -> list[Item]:
    chaves = [k for k in _palavras_chave(fr) if fr.livre(k[0], k[1])
              and not _RE_RESULTADO_DE.search(fr.p, 0, k[0]) and not _RE_PERGUNTAR_DE.search(fr.p, 0, k[0])]
    pares = []
    for ki, (a, b, *_resto) in enumerate(chaves):
        por_causa = _RE_POR_CAUSA_DE.search(fr.p, 0, a) is not None
        for di, (da, db, d) in enumerate(fr.datas):
            if di in fr.datas_usadas or (por_causa and _passou(d, referencia)):
                continue
            nota = _distancia(fr, a, b, da, db, d, referencia)
            if nota is not None:
                pares.append((nota, ki, di))
    pares.sort()
    usadas_k: set[int] = set()
    itens: list[Item] = []
    for _nota, ki, di in pares:
        if ki in usadas_k or di in fr.datas_usadas:
            continue
        usadas_k.add(ki)
        fr.datas_usadas.add(di)
        a, b, nome, _plural, memoria = chaves[ki]
        d = fr.datas[di][2]
        passado = _passou(d, referencia)
        pessoa = None if memoria else _dono(fr, a, b)
        if pessoa is not None and nome in _EVENTOS_SAUDE and grupo_relacao(pessoa.relacao) == "saude":
            pessoa = None     # "a médica dela pediu exames": os exames são dela, não da médica
        base = _base_evento(fr, a, b, nome, memoria)
        if memoria:
            cats = (["Memórias compartilhadas"] if passado else ["Eventos futuros", "Memórias compartilhadas"])
        else:
            cats = ["Experiências importantes"] if passado else ["Eventos futuros"]
        importancia = 3 if (fr.preocupada or nome in _EVENTOS_SAUDE) else 2
        itens.append(_item(fr, TipoItem.EVENTO, _com_data(base, d), a, b, cats, pessoa=pessoa,
                           importancia=importancia, **_campos_evento(fr, d)))
        fr.ocupar(a, b)
    return itens


def _evento_generico(fr: _Frase, referencia: date) -> list[Item]:
    """Frase sem nada reconhecido mas com data futura: vira evento com as palavras dela."""
    for i, (da, db, d) in enumerate(fr.datas):
        if i in fr.datas_usadas or not _por_vir(d, referencia) or not fr.livre(da, db):
            continue
        fr.datas_usadas.add(i)
        pessoa = None
        if fr.pessoas and not _ela_sujeito(fr.p):
            pessoa = fr.pessoas[0][2]
        base = _cap(_encurtar(_sem_ponto(fr.texto), 12))
        return [_item(fr, TipoItem.EVENTO, _com_data(base, d), da, db, ["Eventos futuros"],
                      pessoa=pessoa, **_campos_evento(fr, d))]
    return []


# ------------------------------------------------------------------ estados e padrões

_ADJ_NEG = (r"preocupad[ao]s?|nervos[ao]s?|ansios[ao]s?|triste|tristonh[ao]|chatead[ao]s?|cansad[ao]s?"
            r"|estressad[ao]s?|insegur[ao]s?|sobrecarregad[ao]s?|desanimad[ao]s?|frustrad[ao]s?|magoad[ao]s?"
            r"|irritad[ao]s?|tens[ao]|apreensiv[ao]s?|exaust[ao]s?|esgotad[ao]s?|abalad[ao]s?|deprimid[ao]s?"
            r"|doente|mal|sensivel|carente|sozinh[ao]|brav[ao]|angustiad[ao]|aflit[ao]|confus[ao]|perdid[ao]"
            r"|assustad[ao]|enjoad[ao]|gripad[ao]|resfriad[ao]|com\s+medo|com\s+raiva|com\s+saudades?"
            r"|com\s+dor(?:\s+de\s+(?:cabeca|barriga|garganta|dente))?|com\s+ciumes?|sem\s+paciencia"
            r"|pra\s+baixo|para\s+baixo|de\s+mau\s+humor|mal\s+humorad[ao]|no\s+limite")
_ADJ_POS = (r"animad[ao]s?|empolgad[ao]s?|feliz(?:es)?|content[ae]s?|aliviad[ao]s?|orgulhos[ao]s?"
            r"|tranquil[ao]s?|calm[ao]s?|radiante|realizad[ao]s?|grat[ao]s?|motivad[ao]s?|confiante"
            r"|apaixonad[ao]|de\s+bom\s+humor|bem\s+humorad[ao]|em\s+paz|otim[ao]s?|melhor")
# "a avó que tá doente": o estado é da pessoa da relação, não dela (revisão de 07/10).
_RE_RELACAO_QUE = re.compile(r"(?:^|\s)(?P<r>[a-z]+)(?P<dela>\s+dela)?\s+que\s+$")
# "cansada e triste": o segundo estado da mesma frase também conta.
_RE_ESTADO_E = re.compile(r"\s*(?:,\s*|\s+e\s+)" + _RE_INTENS + r"(?:(?P<neg>" + _ADJ_NEG + r")|(?P<pos>"
                          + _ADJ_POS + r"))\b")
_ADJ_SAUDE = ("doente", "com dor", "gripad", "resfriad", "enjoad")
_RE_ESTADO = re.compile(
    _SEM_NEGACAO + r"\b(?P<v>esta\s+se\s+sentindo|ta\s+se\s+sentindo|estava\s+se\s+sentindo|tava\s+se\s+sentindo"
    r"|tem\s+estado|tem\s+andado|se\s+sente|se\s+sentia|se\s+sentiu|esta|ta|anda|andava|andou|estava|tava|esteve"
    r"|ficou|continua|segue|parece|parecia|acordou|chegou|voltou)\s+" + _RE_INTENS
    + r"(?:(?P<neg>" + _ADJ_NEG + r")|(?P<pos>" + _ADJ_POS + r"))\b")
_RE_CAUSA = re.compile(r"\s+(?:com|por\s+causa\s+d[eoa]s?|pel[oa]s?|sobre|por|de)\s+")
_VERBO_PRESENTE = {"esta", "ta", "anda", "continua", "segue", "tem estado", "tem andado", "se sente",
                   "esta se sentindo", "ta se sentindo", "parece"}
_VERBO_PASSADO = {"estava", "tava", "esteve", "andava", "andou", "se sentia", "estava se sentindo",
                  "tava se sentindo", "parecia"}
# "quando ela tá estressada ela prefere...": condição ou hábito, não como ela está agora.
_RE_CONDICAO = re.compile(
    r"\b(?:quando|qdo|qnd|se|sempre\s+que|toda\s+vez\s+que|cada\s+vez\s+que|caso)\s+(?:ela\s+)?$")
_RE_PREOCUPACAO = re.compile(
    r"\b(?:preocupad[ao]|preocupa|preocupacao|nervos[ao]|ansios[ao]|ansiedade|apreensiv[ao]|tens[ao]"
    r"|com\s+medo|estressad[ao]|angustiad[ao]|aflit[ao])\b")


def _estados(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_ESTADO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        neg = bool(m.group("neg"))
        g = "neg" if neg else "pos"
        adj = fr.texto[m.start(g):m.end(g)]
        v = re.sub(r"\s+", " ", m.group("v"))
        if v in _VERBO_PRESENTE and _RE_CONDICAO.search(fr.p, 0, m.start()):
            continue
        verbo = "Está" if v in _VERBO_PRESENTE else "Estava" if v in _VERBO_PASSADO else _cap(fr.texto[m.start("v"):m.end("v")])
        resumo = f"{verbo} {adj}"
        depois = m.end()
        for da, db, _d in fr.datas:     # "ansiosa ontem por causa da prova": a data no meio não corta
            if da >= depois and re.fullmatch(r"\s*", fr.p[depois:da]):
                depois = db
        causa = _RE_CAUSA.match(fr.p, depois)
        fim = m.end()
        if causa:
            fim_causa = _segmento(fr, causa.end(), max_palavras=6)
            if normalizar(fr.p[causa.end():fim_causa]) in _ALVOS_VAZIOS:     # "com o que eu falei"
                fim_causa = _segmento(fr, causa.end(), longo=True, max_palavras=6)
            if fim_causa > causa.end():
                resumo += " " + fr.texto[causa.start():fim_causa].strip()
                fim = fim_causa
        adj_n = normalizar(adj)
        if neg:
            cats = ["Preocupações"] + (["Medos"] if "medo" in adj_n else []) \
                + (["Saúde"] if adj_n.startswith(_ADJ_SAUDE) else [])
        else:
            cats = ["Coisas que deixam feliz"]
        # A data da mesma oração ("triste ontem mas hoje tá ótima": o "ontem" não é do segundo estado).
        validas = [(da, db, d) for da, db, d in fr.datas if d.data and _passou(d, referencia)]

        def separado(x0: int, x1: int) -> bool:
            return bool(re.search(r"\b(?:mas|porem|so\s+que)\b", fr.p[x0:x1]))
        perto = [d for da, _db, d in validas if da >= m.end() and not separado(m.end(), da)]
        antes = [d for _da, db, d in validas if db <= m.start() and not separado(db, m.start())]
        quando = perto[0] if perto else (antes[-1] if antes else None)

        # "a avó que tá doente": o estado é da pessoa logo antes do "que" (revisão de 07/10).
        dono_estado = _SEM_DONO
        rel = _RE_RELACAO_QUE.search(fr.p, 0, m.start())
        if rel:
            antes_que = next((x[2] for x in fr.pessoas if re.fullmatch(r"\s+que\s+", fr.p[x[1]:m.start()])), None)
            if antes_que is not None:
                dono_estado = antes_que
            elif normalizar(relacao_base(rel.group("r"))) in RELACOES:
                fr.ocupar(m.start(), fim)
                continue                     # é de alguém que as regras não acharam: não vira estado dela

        campos_estado = {"data_evento": quando.data if quando else referencia,
                         "data_expressao": quando.expressao if quando else None, "precisao": Precisao.DIA}
        itens.append(_item(fr, TipoItem.ESTADO, resumo, m.start(), m.end(), cats, pessoa=dono_estado,
                           alvo=adj, polaridade=-1 if neg else 1, **campos_estado))
        e2 = _RE_ESTADO_E.match(fr.p, fim if fim > m.end() else m.end())
        if e2 and fim == m.end():
            neg2 = bool(e2.group("neg"))
            g2 = "neg" if neg2 else "pos"
            adj2 = fr.texto[e2.start(g2):e2.end(g2)]
            adj2_n = normalizar(adj2)
            cats2 = (["Preocupações"] + (["Medos"] if "medo" in adj2_n else [])
                     + (["Saúde"] if adj2_n.startswith(_ADJ_SAUDE) else [])) if neg2 else ["Coisas que deixam feliz"]
            itens.append(_item(fr, TipoItem.ESTADO, f"{verbo} {adj2}", m.start(), e2.end(), cats2, pessoa=dono_estado,
                               alvo=adj2, polaridade=-1 if neg2 else 1, **campos_estado))
            fim = e2.end()
        fr.ocupar(m.start(), fim)
    return itens



_RE_PADRAO_ADJ = re.compile(
    r"\b(?:(?:sempre|normalmente|geralmente|quase\s+sempre|frequentemente|muitas\s+vezes)\s+)?"
    r"(?:fica|ficava|costuma\s+ficar|costumava\s+ficar|tende\s+a\s+ficar|acaba\s+ficando)\s+" + _RE_INTENS
    + r"(?:(?P<neg>" + _ADJ_NEG + r")|(?P<pos>" + _ADJ_POS + r"))\b")
_RE_CONTEXTO_PADRAO = re.compile(
    r"\s+(?:antes\s+d[eoa]s?|quando|com|em|durante|depois\s+d[eoa]s?|se|perto\s+d[eoa]s?|na\s+hora\s+d[eoa]s?)\s+")
_RE_MEDO = re.compile(
    r"\b(?:tem|tinha|sente)\s+(?:muito\s+|um\s+pouco\s+de\s+|um\s+)?(?P<n>medo|pavor|fobia|panico|horror|trauma)"
    r"\s+(?:de|d[oa]s?|a|com)\s+|\b(?P<morre>morre\s+de\s+medo)\s+(?:de|d[oa]s?)\s+")
_RE_COSTUMA = re.compile(r"\bcostuma(?:va)?\s+(?!ficar\b)")
_RE_TODA_VEZ = re.compile(r"\b(?:toda\s+vez\s+que|sempre\s+que)\s+")


def _contexto_de_evento(fr: _Frase, fora: tuple[int, int]) -> str | None:
    for alvo in (fr, fr.anterior):
        if alvo is None:
            continue
        for a, b, _nome, plural, memoria in _palavras_chave(alvo):
            if not memoria and (alvo is not fr or not (fora[0] <= a < fora[1])):
                return plural
    return None


def _padroes(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_PADRAO_ADJ.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        neg = bool(m.group("neg"))
        g = "neg" if neg else "pos"
        adj = fr.texto[m.start(g):m.end(g)]
        fim, alvo = m.end(), None
        ctx = _RE_CONTEXTO_PADRAO.match(fr.p, m.end())
        if ctx:
            fim = _segmento(fr, ctx.end(), longo=True, max_palavras=8)
            resumo = f"Fica {adj} " + fr.texto[ctx.start():fim].strip()
            alvo = fr.texto[ctx.end():fim].strip() or None
        elif (tv := _RE_TODA_VEZ.search(fr.p, 0, m.start())) is not None:
            # "Toda vez que chove ela fica triste": o contexto veio antes.
            resumo = _cap(fr.texto[tv.start():m.end()])
            alvo = re.sub(r"[\s,]+(?:ela)?[\s,]*$", "", fr.texto[tv.end():m.start()],
                          flags=re.IGNORECASE).strip() or None
        else:
            plural = _contexto_de_evento(fr, (m.start(), m.end()))
            resumo = f"Fica {adj} antes de {plural}" if plural else f"Fica {adj}"
            alvo = plural
        adj_n = normalizar(adj)
        cats = (["Medos"] if "medo" in adj_n else []) + (["Preocupações"] if neg else ["Coisas que deixam feliz"])
        itens.append(_item(fr, TipoItem.PADRAO, resumo, m.start(), m.end(), cats,
                           alvo=alvo, polaridade=-1 if neg else 1))
        fr.ocupar(m.start(), fim)
    for m in _RE_MEDO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end())
        modelo = "Morre de medo de" if m.group("morre") else f"Tem {fr.texto[m.start('n'):m.end('n')]} de"
        partes = _partes(fr, m.end(), fim)
        for a, b in partes:
            alvo = fr.texto[a:b]
            itens.append(_item(fr, TipoItem.PADRAO, f"{modelo} {alvo}", m.start(), b,
                               ["Medos"] + categorias_de(alvo, 2), frase_cats=False,
                               alvo=alvo, polaridade=-1))
        fr.ocupar(m.start(), _ate(partes, m.end()))
    for m in _RE_COSTUMA.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end(), longo=True, max_palavras=10)
        if fim <= m.end():
            continue
        itens.append(_item(fr, TipoItem.PADRAO, _cap(fr.texto[m.start():fim]), m.start(), fim,
                           categorias_de(fr.texto[m.end():fim], 2) or ["Preferências"],
                           alvo=fr.texto[m.end():fim].strip()))
        fr.ocupar(m.start(), fim)
    if not any(i.tipo == TipoItem.PADRAO for i in itens):
        for m in _RE_TODA_VEZ.finditer(fr.p):
            if fr.livre(m.start(), m.end()):
                resumo = _cap(_encurtar(_sem_ponto(fr.texto[m.start():]), 14))
                itens.append(_item(fr, TipoItem.PADRAO, resumo, m.start(), m.end(),
                                   categorias_de(fr.texto, 2) or ["Preferências"]))
                fr.ocupar(m.start(), len(fr.p))
                break
    return itens


# ------------------------------------------------------------------ pendências, desejos, pessoas

_RE_PENDENCIA = re.compile(
    r"\b(?:esperando|aguardando|na\s+expectativa\s+d[eoa]s?|ainda\s+nao\s+sabe|nao\s+sabe\s+ainda"
    r"|ainda\s+nao\s+decidiu|vai\s+saber|vai\s+descobrir|vai\s+decidir|precisa\s+decidir|tem\s+que\s+decidir"
    r"|esta\s+decidindo|ta\s+decidindo|ficou\s+de|vai\s+receber\s+(?:a\s+|o\s+)?(?:resposta|resultado|retorno)"
    r"|esta\s+vendo\s+se|ta\s+vendo\s+se|aguarda|espera\s+(?:o|a|os|as)\s+(?:resultado|resposta|retorno))\b")


def _pendencias(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_PENDENCIA.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        fim = _segmento(fr, m.end(), longo=True, max_palavras=12)
        fim = max(fim, m.end())
        resumo = _cap(fr.texto[m.start():fim])
        a, b = _aparar(fr, m.end(), fim)
        futura = next((d for _, _, d in fr.datas if _por_vir(d, referencia)), None)
        campos = _campos_data(futura) if futura else {}
        lembrar = (max(futura.data, referencia) + timedelta(days=1)) if futura else referencia + timedelta(days=3)
        itens.append(_item(fr, TipoItem.PENDENCIA, resumo, m.start(), fim, ["Situações pendentes"],
                           importancia=3 if fr.preocupada else 2, alvo=fr.texto[a:b] if a < b else None,
                           lembrar_em=lembrar, **campos))
        fr.ocupar(m.start(), fim)
    return itens


_RE_DESEJO = re.compile(
    _SEM_NEGACAO + r"\b(?P<v>(?:um\s+dia\s+|ainda\s+)?(?:quer|queria|gostaria\s+de|sonha\s+(?:em|com)"
    r"|sonhava\s+(?:em|com)|tem\s+vontade\s+de|tinha\s+vontade\s+de|morre\s+de\s+vontade\s+de|pretende|planeja"
    r"|esta\s+louca\s+para|ta\s+louca\s+pra|sempre\s+quis|tem\s+o\s+sonho\s+de|o\s+sonho\s+dela\s+e)"
    r"(?:\s+muito)?)\s+(?!que\b|saber\b|dizer\b)")
_RE_NAO_QUER = re.compile(r"\b(?P<v>(?:nao|nunca|n)\s+(?:quer|queria)(?:\s+mais)?)\s+(?!que\b|saber\b|dizer\b)")
_VERBOS_OBJETIVO = frozenset("""conseguir passar terminar aprender juntar comprar abrir fazer mudar tirar formar
emagrecer voltar estudar trabalhar economizar entrar comecar trocar ganhar montar criar melhorar crescer ser
largar pedir fazer""".split())
_ARTIGOS_OBJETO = frozenset("um uma uns umas o a os as".split())


def _desejos(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for rx, pol in ((_RE_NAO_QUER, -1), (_RE_DESEJO, 1)):
        for m in rx.finditer(fr.p):
            if not fr.livre(m.start(), m.end()):
                continue
            fim = _segmento(fr, m.end())
            v = normalizar(m.group("v"))
            verbo = _cap(fr.texto[m.start("v"):m.end("v")])
            partes = _partes(fr, m.end(), fim)
            for n, (a, b) in enumerate(partes):
                bruto = fr.p[m.end():b].split() if n == 0 else fr.p[a:b].split()
                primeira = bruto[0] if bruto else ""
                if re.search(r"sonh|um dia|sempre quis|vontade", v):
                    cat = "Sonhos"
                elif re.search(r"pretende|planeja", v):
                    cat = "Objetivos"
                elif primeira in _ARTIGOS_OBJETO or re.match(r"(?:ganhar|receber)\s+(?:um|uma|uns|umas|de\s+presente)\b",
                                                             " ".join(bruto)):
                    # "queria um fone novo", "quer ganhar um perfume": presente
                    cat = "Presentes"
                elif re.sub(r"-se$", "", primeira) in _VERBOS_OBJETIVO:
                    cat = "Objetivos"
                else:
                    cat = "Sonhos"
                if n == 0:
                    resumo = _cap(fr.texto[m.start():b])
                else:
                    resumo = f"{verbo} {fr.texto[a:b]}"
                if pol < 0:
                    cat = "Coisas que incomodam"
                alvo = fr.texto[a:b]
                itens.append(_item(fr, TipoItem.DESEJO, resumo, m.start(), b,
                                   [cat] + categorias_de(alvo, 2), frase_cats=len(partes) == 1,
                                   alvo=alvo, polaridade=pol))
            fr.ocupar(m.start(), _ate(partes, m.end()))
    return itens


_RE_INFO = re.compile(
    r"\b(?:(?P<juntas>se\s+conheceram|se\s+conhecem|trabalham\s+juntas|estudam\s+juntas|moram\s+juntas"
    r"|cresceram\s+juntas|estudaram\s+juntas)|conheceu\s+ela|a\s+conheceu|trabalha\s+com\s+ela"
    r"|estuda\s+com\s+ela|mora\s+com\s+ela|e\s+casad[ao]\s+com|esta\s+gravida|ta\s+gravida"
    r"|mora\s+(?:em|no|na)|morava\s+(?:em|no|na)|trabalha\s+(?:em|no|na|como|de)|estuda\s+(?:em|no|na)"
    r"|tem\s+\w+\s+filh[oa]s?|se\s+formou\s+em|namora(?:\s+com)?|e\s+(?:a|o)\s+(?:melhor\s+)?amig[ao])\b")


def _infos_pessoa(fr: _Frase, referencia: date) -> list[Item]:
    itens: list[Item] = []
    for m in _RE_INFO.finditer(fr.p):
        if not fr.livre(m.start(), m.end()):
            continue
        if m.group("juntas"):
            pessoa = next((x[2] for x in fr.pessoas if e_dela(x[2].relacao)), None)
        else:
            pessoa = _dono(fr, m.start(), m.end())
        if pessoa is None or not e_dela(pessoa.relacao):
            continue
        fim = max(_segmento(fr, m.end(), longo=True, max_palavras=10), m.end())
        predicado = re.sub(r"\s+", " ", fr.texto[m.start():fim]).strip()
        resumo = f"{_rotulo(pessoa)}: {predicado}"
        itens.append(_item(fr, TipoItem.PESSOA_INFO, resumo, m.start(), fim, ["Pessoas importantes"],
                           pessoa=pessoa))
        fr.ocupar(m.start(), fim)
    if not itens:
        for _a, _b, pe in fr.pessoas:  # "Mariana é a melhor amiga dela"
            if pe.nome and pe.relacao and e_dela(pe.relacao) and re.search(r"\s(?:e|era)\s", plano(pe.trecho)):
                itens.append(_item(fr, TipoItem.PESSOA_INFO, f"{pe.nome}: {pe.relacao} dela", _a, _b,
                                   ["Pessoas importantes"], pessoa=pe))
    return itens


def _nota(fr: _Frase) -> Item:
    pessoa = fr.pessoas[0][2] if fr.pessoas else None
    return _item(fr, TipoItem.NOTA, _cap(_encurtar(_sem_ponto(fr.texto), 14)), 0, 0, [],
                 pessoa=pessoa, certeza=fr.certeza, importancia=1)


# ------------------------------------------------------------------ montagem

def _termos_busca(pe: PessoaEncontrada) -> list[tuple[str, bool]]:
    """Como achar de novo a mesma pessoa em outra frase: (termo, só se for dela)."""
    termos: list[tuple[str, bool]] = []
    if pe.nome:
        termos.append((pe.nome, False))
    if pe.relacao:
        base = relacao_base(pe.relacao)
        if pe.relacao.endswith("(sua)"):
            termos.append((f"minha {base}", False))
        elif pe.relacao.endswith("(seu)"):
            termos.append((f"meu {base}", False))
        elif e_dela(pe.relacao):
            termos.append((base, True))
    return termos


def _pessoas_da_frase(frase: str, p: str, ini: int, fim: int,
                      pessoas: list[PessoaEncontrada]) -> list[tuple[int, int, PessoaEncontrada]]:
    achadas = [(pe.inicio - ini, pe.fim - ini, pe) for pe in pessoas if ini <= pe.inicio and pe.fim <= fim]
    for pe in pessoas:
        if any(x[2] is pe for x in achadas):
            continue
        for termo, so_dela in _termos_busca(pe):
            rx = r"(?<![\w-])" + r"\s+".join(map(re.escape, plano(termo).split())) + r"(?![\w-])"
            for m in re.finditer(rx, p):
                a, b = m.start(), m.end()
                if any(a < y and x < b for x, y, _ in achadas):
                    continue
                if so_dela and (re.search(r"\b(?:minha|meu|minhas|meus|nossa|nosso)\s+$", p[:a])
                                or re.match(r"\s+(?:dele|d[aoe]\s+[A-ZÀ-Þ])", frase[b:])):
                    continue
                achadas.append((a, b, pe))
    achadas.sort(key=lambda x: (x[0], x[1]))
    return achadas


_ETAPAS = (_apoios, _restricoes, _reacoes, _preferencias, _especiais, _aniversarios, _eventos,
           _estados, _padroes, _pendencias, _desejos, _infos_pessoa)
_RE_IMPORTANTE = re.compile(r"\b(?:muito|super|mega|extremamente)\s+importante\b")
_RE_EVENTO_IMPORTANTE = re.compile(r"(?<!nao e )(?<!nada )(?<!pouco )\bimportante\b")
# "nunca esquecer" (lembrete de quem escreve) não é o "nunca" dela; "não odeia" não é "odeia".
_RE_NUNCA_ODEIA = re.compile(r"\b(?:nunca(?!\s+(?:me\s+|se\s+)?esquec)|" + _SEM_NEGACAO + r"(?:odeia|odiava))\b")


def extrair_itens(texto: str, referencia: date, pessoas: list[PessoaEncontrada],
                  datas: list[DataEncontrada]) -> list[Item]:
    """Itens de todas as frases do texto, na ordem. Frase sem item estruturado vira NOTA."""
    texto = texto or ""
    pessoas = list(pessoas or [])
    datas = sorted(datas or [], key=lambda d: (d.inicio, d.fim))
    lista: list[_Frase] = []
    ultimo_homem: PessoaEncontrada | None = None
    for ini, fim, frase in frases(texto):
        p = plano(frase)
        fr = _Frase(ini=ini, texto=frase, p=p, certeza=classificar_certeza(frase),
                    datas=[(d.inicio - ini, d.fim - ini, d) for d in datas if ini <= d.inicio and d.fim <= fim],
                    pessoas=_pessoas_da_frase(frase, p, ini, fim, pessoas),
                    ele=ultimo_homem, anterior=lista[-1] if lista else None,
                    confirmado=trechos_confirmados(frase))
        # "Ela nunca disse que odeia rosas", "não sei se ela gosta de gato": nada afirmado.
        fr.nao_afirmado = trechos_nao_afirmados(frase)
        if _RE_IRONIA.search(p):          # "adora o trabalho novo, só que não": fica como nota
            fr.nao_afirmado.append((0, len(p)))
        for a, b in fr.nao_afirmado:
            fr.ocupar(a, b)
        lista.append(fr)
        for _, _, pe in fr.pessoas:
            if genero_relacao(pe.relacao) == "m":
                ultimo_homem = pe
    for i, fr in enumerate(lista):
        fr.preocupada = any(_RE_PREOCUPACAO.search(x.p) for x in lista[max(0, i - 1):i + 2])

    itens: list[Item] = []
    for fr in lista:
        novos: list[Item] = []
        for etapa in _ETAPAS:
            novos += etapa(fr, referencia)
        if not novos:
            novos = _evento_generico(fr, referencia) or [_nota(fr)]
        importante = bool(_RE_IMPORTANTE.search(fr.p))
        nunca_odeia = any(not any(a <= m.start() < b for a, b in fr.nao_afirmado)
                          for m in _RE_NUNCA_ODEIA.finditer(fr.p))
        vistos: set[tuple] = set()
        for item in novos:
            chave = (item.tipo, normalizar(item.alvo or item.resumo), item.polaridade, item.pessoa,
                     item.data_evento)
            if chave in vistos:
                continue
            vistos.add(chave)
            if importante or (nunca_odeia and (item.polaridade == -1 or item.tipo == TipoItem.NOTA)):
                item.importancia = 3
            elif item.tipo in (TipoItem.EVENTO, TipoItem.PENDENCIA) and _RE_EVENTO_IMPORTANTE.search(fr.p):
                item.importancia = 3              # "uma reunião importante"
            futuro = item.data_evento is not None and item.data_evento > referencia
            if futuro and item.tipo not in (TipoItem.DATA_IMPORTANTE, TipoItem.ESTADO) \
                    and "Eventos futuros" not in item.categorias:
                item.categorias = _filtrar(item.categorias[:1] + ["Eventos futuros"] + item.categorias[1:])
            itens.append(item)
    return itens


__all__ = ["extrair_itens"]
