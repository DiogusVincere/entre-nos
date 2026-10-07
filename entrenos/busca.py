"""Busca em linguagem natural (DESIGN.md, seção 10).

Entende algumas perguntas comuns ("o que eu sei sobre a família dela", "qual a comida que ela
mais gosta", "quando ela comentou sobre o trabalho"...), expande sinônimos e filtra por
categoria, tipo ou pessoa; depois completa com a busca de texto (FTS5). Só devolve o que está
gravado, sempre com o trecho e a data de onde saiu. Sem nada: "Não encontrei essa informação."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .banco import Banco
from .modelos import Certeza, Item, Resposta, TipoItem
from .motor import NAO_ENCONTREI, Motor, juntar
from .regras.categorias import categorias_de
from .regras.pessoas import (casar_pessoa, e_dela, e_nome_dela, extrair_pessoas, genero_relacao, grupo_relacao,
                             mesma_relacao, plano, relacao_base)
from .texto import normalizar, semelhanca, tempo_atras, termos

MAX_ENCONTRADOS = 10
_STATUS_VALIDOS = ("ativo", "resolvido")
_PESO_CERTEZA = {Certeza.CONFIRMADO: 2.0, Certeza.OBSERVADO: 1.0, Certeza.INFERENCIA: 0.5, Certeza.INCERTO: 0.25}


# ---------------------------------------------------------------- vocabulário

@dataclass(frozen=True)
class _Topico:
    chave: re.Pattern[str]          # como o assunto aparece na pergunta
    categoria: str | None           # None: só pelos sinônimos (a categoria inteira seria larga demais)
    raizes: tuple[str, ...]         # sinônimos para a busca de texto
    grupo_pessoas: str | None = None    # pessoas desse grupo também contam ("pet": a gata dela, Mel)


def _topico(chave: str, categoria: str | None, raizes: str, grupo_pessoas: str | None = None) -> _Topico:
    return _Topico(re.compile(r"(?<![a-z])(?:" + chave + r")(?![a-z])"), categoria, tuple(raizes.split()),
                   grupo_pessoas)


# Raiz com "*" vale como começo de palavra ("trabalh*" pega trabalho/trabalhar); sem "*",
# só a palavra inteira, com plural ("mae" não pega "maestro", "prima" não pega "primavera").
_TOPICOS = [
    _topico(r"familia|familiares|parentes", "Família",
            "familia* parente mae pai irma irmao avo tia tio prima primo sobrinha sobrinho cunhada cunhado"
            " sogra sogro madrinha padrinho"),
    _topico(r"trabalho|emprego|servico|carreira|firma|empresa|escritorio", "Trabalho",
            "trabalh* emprego* chefe escritorio empresa* reuniao reunioes colega* entrevista* salario*"),
    _topico(r"estudos?|faculdade|facul|curso|escola|provas?|tcc|mestrado", "Estudos",
            "estud* faculdade* prova aula curso tcc vestibular*"),
    _topico(r"saude|medicos?|doencas?|hospital|remedios?", "Saúde",
            "saude medic* consulta exame cirurgia* doente hospital* remedio*"),
    _topico(r"viagem|viagens|viajar|ferias|passeios?", "Viagens",
            "viag* viaj* ferias passagem passagens voo aeroporto* hotel hoteis"),
    _topico(r"presentes?|mimos?|surpresas?", "Presentes",
            "presente* ganhou ganhar gostaria queria recebeu surpresa*"),
    _topico(r"comidas?|comer|pratos?|restaurantes?|culinaria", "Comidas",
            "comida* comer restaurante* jantar almoco prato"),
    _topico(r"bebidas?|beber|drinks?", "Bebidas", "bebida* beber drink cerveja* vinho* cafe"),
    _topico(r"filmes?|cinema", "Filmes", "filme cinema*"),
    _topico(r"series?|seriados?|novelas?", "Séries", "serie seriado* temporada* novela*"),
    _topico(r"musicas?|bandas?|shows?|cantor\w*", "Música", "music* banda show cantor*"),
    _topico(r"livros?|leitura", "Livros", "livro* leitura*"),
    _topico(r"amig\w*|amizades?", "Amigos", "amig* amizade*"),
    _topico(r"medos?|fobias?", "Medos", "medo pavor fobia*"),
    _topico(r"sonhos?|desejos?|vontades?", "Sonhos", "sonho* sonha* vontade*"),
    _topico(r"objetivos?|metas?|planos?", "Objetivos", "objetivo* meta plano pretende planeja*"),
    _topico(r"preocupac\w*|problemas?", "Preocupações", "preocup* nervos* ansios* problema*"),
    _topico(r"aniversarios?|niver", "Aniversários", "aniversario* niver"),
    _topico(r"relacionamento|namoro|nos\s+dois", "Relacionamento", "namor* relacionamento* casal"),
    _topico(r"lugar(?:es)?|cidades?", "Lugares", "lugar lugares cidade*"),
    _topico(r"hobb\w*|passatempos?|lazer", "Hobbies", "hobby hobbies passatempo*"),
    _topico(r"infancia", "Infância", "infancia crianca*"),
    _topico(r"flor(?:es)?", None, "flor flores tulipa* rosa rosas girass* orquidea* lirio* margarida* buque* cravo*"
                                  " lavanda peonia* hortensia*"),
    _topico(r"pets?|bichos?|bichinhos?|animal|animais|estimacao", None,
            "pet pets gato gata gatos gatas gatinh* cachorr* cadela* bicho* animal animais", "pet"),
]
_ATIVIDADES = frozenset("""sair ir cozinhar comer beber jantar almocar viajar ver assistir ouvir ler dancar
cantar tomar passear fazer ficar dormir conversar andar correr nadar jogar experimentar provar pedir""".split())
# Palavras de conversa que não dizem nada sobre o assunto ("ela tá grávida?").
_COLOQUIAIS = frozenset("ta tava tou to pra pro pq q vc voce hj mt mto tb tbm ne entao ai la aqui ok".split())

# (como aparece na pergunta, categoria, como chamar no texto, filtro do alvo)
_FAVORITOS: list[tuple[str, str, str, str | None]] = [
    (r"comidas?|pratos?|doces?|sobremesas?|restaurantes?|lanches?", "Comidas", "comidas", None),
    (r"bebidas?|drinks?|drinques?", "Bebidas", "bebidas", None),
    (r"filmes?", "Filmes", "filmes", None),
    (r"series?|seriados?", "Séries", "séries", None),
    (r"musicas?|bandas?|cantor(?:a|es|as)?|artistas?", "Música", "músicas", None),
    (r"livros?|autor(?:a|es|as)?", "Livros", "livros", None),
    (r"flor(?:es)?", "Presentes", "flores",
     r"flor|tulipa|rosa|girass|orquidea|lirio|margarida|lavanda|peonia|hortensia|buque|cravo"),
    (r"lugar(?:es)?|passeios?", "Lugares", "lugares", None),
    (r"presentes?", "Presentes", "presentes", None),
    (r"hobb(?:y|ies)|passatempos?", "Hobbies", "hobbies", None),
]
_RE_FAVORITO = re.compile(
    r"\b(?:qual|quais|que)\b.*?(?<![a-z])(?P<cat>" + "|".join(f[0] for f in _FAVORITOS) + r")(?![a-z])"
    r".*\b(?:mais\s+gosta|gosta\s+mais|favorit\w*|preferid\w*|predilet\w*|mais\s+ama|ama\s+mais"
    r"|mais\s+curte|curte\s+mais|ela\s+gosta|ela\s+ama|ela\s+adora|ela\s+curte|(?:ela\s+)?prefere)")
# "o que ela não gosta de comer?", "do que ela não gosta?", "o que ela odeia?"
_RE_NAO_GOSTA_DE = re.compile(
    r"^\s*(?:e\s+)?(?:o\s+que|do\s+que|que\s+coisas?|quais\s+(?:as\s+)?coisas?|que\s+tipo\s+de\s+\w+)\s+"
    r"(?:que\s+)?ela\s+(?:(?:nao|n)\s+(?:gosta|curte|suporta|aguenta|come|bebe|pode)|odeia|detesta)"
    r"(?:\s+(?:de\s+)?(?P<x>[a-z]+))?" + r"\s*[?.!]*\s*$")
_CATEGORIA_DO_VERBO = {"comer": "Comidas", "come": "Comidas", "beber": "Bebidas", "bebe": "Bebidas",
                       "assistir": "Filmes", "ver": "Filmes", "ouvir": "Música", "escutar": "Música",
                       "ler": "Livros", "ganhar": "Presentes", "receber": "Presentes"}
# "quando é o aniversário dela?", "quando a mãe dela faz aniversário?"
_RE_ANIVERSARIO_DE = re.compile(
    r"^\s*(?:e\s+)?(?:quando\s+(?:e|eh|sera|cai|vai\s+ser|foi)\s+o\s+|qual\s+(?:e\s+)?(?:a\s+data\s+d[oe]\s+)?o?\s*)?"
    r"(?:aniversario|niver)\s+(?P<x>.+?)\s*[?.!]*\s*$"
    r"|^\s*(?:e\s+)?quando\s+(?P<y>.+?)\s+faz\s+(?:aniversario|anos)\s*[?.!]*\s*$")

_TENSAO = ("estress* nervos* ansios* ansiedade tensa tenso preocupad* sobrecarreg* cansad* irritad* pressao "
           "medo").split()
_TRISTEZA = "triste tristeza chatead* magoad* desanimad* carente sozinh* chor*".split()
_DOENCA = "doente gripad* resfriad* enjoad* colica".split()
_EMOCOES: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"estress|nervos|ansios|tens[ao]|preocupad|sobrecarreg|cansad|irritad|brav|medo"), _TENSAO),
    (re.compile(r"trist|chatead|magoad|desanimad|carente|sozinh|chorand|pra\s+baixo"), _TRISTEZA),
    (re.compile(r"doente|gripad|resfriad|enjoad|mal\b|com\s+dor|colica"), _DOENCA),
]
_RE_AJUDA = re.compile(r"(?<![a-z])(?:ajuda|acalma|conforta|melhora|relaxa|distrai|gosta|adora|prefere|curte)")

_PREP = r"(?:sobre|de|do|da|dos|das|a\s+respeito\s+d[eoa]s?|em|no|na|nos|nas)"
_FIM = r"\s*[?.!]*\s*$"
_RE_PESSOAS = re.compile(
    r"\bpessoas?\b.*\b(?:importantes?|proximas?|principais|queridas?|especiais)\b"
    r"|\bquem\s+(?:e|sao)\s+(?:as?\s+)?(?:pessoas?\s+)?(?:mais\s+)?(?:importantes?|proximas?)\b"
    r"|\bquem\s+(?:ela\s+)?mais\s+(?:ama|gosta|confia)\b")
_RE_QUANDO = re.compile(
    r"^\s*(?:e\s+)?quando\s+(?:(?:e\s+)?(?:foi\s+)?que\s+)?ela\s+(?:me\s+)?(?:ja\s+)?"
    r"(?:comentou|falou|disse|contou|mencionou|citou|tocou\s+no\s+assunto)\s+"
    r"(?:(?:alguma\s+)?(?:coisa|algo)\s+)?(?:" + _PREP + r"\s+)?(?P<x>.+?)" + _FIM)
_RE_ESTADO = re.compile(
    r"\bquando\s+(?:ela\s+)?(?:esta|ta|fica|estiver|ficar|anda|se\s+sente|se\s+sentir)\s+"
    r"(?:(?:muito|meio|mais|bem)\s+)?(?P<e>(?:com\s+|pra\s+)?[a-z]+)")
_RE_O_QUE_AJUDA = re.compile(r"\bo\s+que\s+(?:mais\s+)?(?:ajuda|acalma|conforta|anima|alegra)\b")
_RE_SEI = re.compile(
    r"^\s*(?:e\s+)?(?:o\s+que\s+(?:(?:eu|a\s+gente|voce)\s+)?(?:ja\s+)?(?:sei|sabe|sabemos|tenho|registrei|anotei"
    r"|temos|tem)|(?:me\s+)?(?:fala|fale|conta|conte|diga|diz|mostra|mostre)(?:\s+(?:tudo|mais))?|tudo"
    r"|informac\w+|resumo)\s+" + _PREP + r"\s+(?P<x>.+?)" + _FIM)
_RE_FALOU = re.compile(
    r"^\s*(?:e\s+)?(?P<oque>o\s+que\s+|que\s+)?ela\s+(?:ja\s+)?(?:me\s+)?(?:falou|disse|contou|comentou|mencionou"
    r"|fala|diz|conta|comenta|citou)\s+(?:(?:alguma\s+)?(?:coisa|algo|nada)\s+)?" + _PREP + r"\s+(?P<x>.+?)" + _FIM)
_RE_NAO_GOSTA = re.compile(r"\b(?:nao|nunca)\s+(?:gosta|quer|suporta|ajuda|curte)|\bpiora|\batrapalha|\bodeia")
_PALAVRAS_PERGUNTA = frozenset("qual quais quando onde como porque sei sabe voce falou disse contou comentou "
                               "mencionou registrei anotei ja alguma coisa algo tudo".split())
# Verbos que quase todo registro tem: só entram na busca livre se não sobrar outra palavra.
_PALAVRAS_FRACAS = frozenset("gosta gostar gostou gostava adora ama acha achou fica ficou quer queria pode "
                             "tem teve fazer fez sente sentiu prefere preferir preferia comer beber gosto "
                             "odeia detesta curte".split())
_RE_SO_ELA = re.compile(r"(?:ela|ela\s+mesma|a\s+namorada|minha\s+namorada)")


# ---------------------------------------------------------------- apoio

def _raiz(p: str) -> str:
    """Raiz simples para busca por prefixo ('viagens' -> 'viage*', 'presentes' -> 'present*')."""
    if len(p) >= 7:
        return p[:-2] + "*"
    if len(p) >= 5 and p.endswith("s"):
        return p[:-1] + "*"
    return p


def _rx_raizes(raizes) -> re.Pattern[str] | None:
    """Regex das raízes: com '*' casa o começo da palavra; sem, a palavra inteira (com plural)."""
    partes = []
    for r in raizes:
        base = normalizar(r.rstrip("*"))
        if not base:
            continue
        corpo = r"\s+".join(map(re.escape, base.split()))
        partes.append(corpo if r.endswith("*") else corpo + r"(?:s|es)?(?![a-z0-9])")
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(partes) + r")") if partes else None


def _registros(n: int) -> str:
    return "1 registro" if n == 1 else f"{n} registros"


def _texto_item(item: Item) -> str:
    return plano(" ".join(x for x in (item.resumo, item.trecho, item.alvo or "", item.pessoa or "") if x))


def _rotulo_cadastro(p: dict) -> str:
    nome, relacao = p.get("nome"), p.get("relacao")
    if nome and relacao:
        return f"{nome} ({relacao})"
    if nome:
        return nome
    base = relacao_base(relacao) or relacao or ""
    genero = genero_relacao(relacao)
    artigo = "a " if genero == "f" else "o " if genero == "m" else ""
    return f"{artigo}{base} dela"


@dataclass
class _Coleta:
    itens: dict[int, Item] = field(default_factory=dict)
    pontos: dict[int, float] = field(default_factory=dict)
    privados: frozenset[int] = frozenset()      # registros que ficam de fora (busca para a IA)

    def somar(self, item: Item | None, pontos: float) -> None:
        if item is None or item.id is None or item.status not in _STATUS_VALIDOS or item.registro_id in self.privados:
            return
        self.itens[item.id] = item
        self.pontos[item.id] = self.pontos.get(item.id, 0.0) + pontos


class _Busca:
    """Consultas ao banco com cache (uma pergunta = um objeto)."""

    def __init__(self, banco: Banco, referencia: date, privados: frozenset[int] = frozenset()):
        self.banco = banco
        self.referencia = referencia
        self.privados = privados
        self.pessoas = banco.pessoas()
        self.nome_dela = normalizar(banco.config().get("nome_dela") or "")
        self._registros: dict[int, dict | None] = {}
        self._itens: dict[int, Item | None] = {}
        self.nao_dela = {p["id"] for p in self.pessoas if p["relacao"] and not e_dela(p["relacao"])}

    def coleta(self) -> _Coleta:
        return _Coleta(privados=self.privados)

    def registro(self, rid: int) -> dict | None:
        if rid not in self._registros:
            self._registros[rid] = self.banco.registro(rid)
        return self._registros[rid]

    def data_registro(self, rid: int | None) -> date | None:
        r = self.registro(rid) if rid is not None else None
        return r["data_registro"] if r else None

    def item(self, iid: int) -> Item | None:
        if iid not in self._itens:
            self._itens[iid] = self.banco.item(iid)
        return self._itens[iid]

    def ordinal(self, item: Item) -> int:
        d = self.data_registro(item.registro_id)
        return d.toordinal() if d else 0

    def por_texto(self, raizes, coleta: _Coleta, peso: float = 2.0, aceitar=None) -> None:
        """Busca de texto (FTS5). Registro achado entra pelos itens cuja frase cita a raiz."""
        rx = _rx_raizes(raizes)
        if rx is None:
            return
        consulta = " ".join(r.rstrip("*") for r in raizes)
        for n, achado in enumerate(self.banco.buscar_texto(consulta, limite=200)):
            fator = peso * (1 - min(n, 100) / 200)
            if achado["tipo"] == "item":
                candidatos, fator_item = [self.item(achado["ref_id"])], 1.0
            else:
                registro = self.registro(achado["ref_id"]) or {}
                candidatos, fator_item = registro.get("itens", []), 0.5
            for item in candidatos:
                if item is not None and rx.search(_texto_item(item)) and (aceitar is None or aceitar(item)):
                    coleta.somar(item, fator * fator_item)

    def mencoes_da_pessoa(self, pessoa: dict, coleta: _Coleta, peso: float) -> None:
        """Itens dos registros que citam a pessoa e cuja frase fala dela."""
        chaves = [pessoa.get("nome"), *(pessoa.get("apelidos") or [])]
        if not pessoa.get("nome"):
            chaves.append(relacao_base(pessoa.get("relacao")))
        rx = _rx_raizes([c for c in chaves if c])
        if rx is None:
            return
        for m in self.banco.mencoes(pessoa["id"], limite=50):
            for item in (self.registro(m["registro_id"]) or {}).get("itens", []):
                if rx.search(plano(item.trecho)) and item.pessoa_id not in self.nao_dela:
                    coleta.somar(item, peso)


def _ajustar_pontos(coleta: _Coleta) -> None:
    for iid, item in coleta.itens.items():
        extra = 0.3 if item.certeza == Certeza.CONFIRMADO else 0.0
        extra += 0.2 if item.importancia >= 3 else 0.0
        extra -= 0.5 if item.tipo == TipoItem.NOTA else 0.0
        coleta.pontos[iid] += extra


def _escolher(coleta: _Coleta, ctx: _Busca, recentes: bool = False) -> list[Item]:
    """Os mais relevantes (ou os mais recentes) até 10, mostrados do mais novo para o mais antigo."""
    _ajustar_pontos(coleta)
    if recentes:
        ordem = sorted(coleta.itens.values(), key=lambda i: (-ctx.ordinal(i), -coleta.pontos[i.id], -i.id))
    else:
        ordem = sorted(coleta.itens.values(), key=lambda i: (-coleta.pontos[i.id], -ctx.ordinal(i), -i.id))
    escolhidos = ordem[:MAX_ENCONTRADOS]
    escolhidos.sort(key=lambda i: (-ctx.ordinal(i), -i.id))
    return escolhidos


def _encontrado(item: Item, ctx: _Busca) -> dict:
    data = ctx.data_registro(item.registro_id)
    return {
        "item_id": item.id,
        "registro_id": item.registro_id,
        "resumo": item.resumo,
        "trecho": item.trecho,
        "certeza": Certeza(item.certeza).value,
        "categoria": item.categorias[0] if item.categorias else None,
        "data_registro": data,
        "quando": tempo_atras((ctx.referencia - data).days) if data else None,
    }


def _frase_encontrei(coleta: _Coleta, escolhidos: list[Item], sobre: str, prefixo: str = "") -> str:
    total = len({i.registro_id for i in coleta.itens.values()})
    mostrados = len({i.registro_id for i in escolhidos})
    frase = f"{prefixo}Encontrei {_registros(total)} sobre {sobre}"
    return frase + (". Estes são os mais relevantes:" if mostrados < total else ":")


# ---------------------------------------------------------------- de que se fala

@dataclass
class _Assunto:
    rotulo: str
    pessoas: list[dict] = field(default_factory=list)
    categoria: str | None = None
    raizes: list[str] = field(default_factory=list)
    familia: bool = False
    dela: bool = False              # "o que eu sei sobre ela": o que é dela e mais importante


def _limpar_x(x: str) -> str:
    return re.sub(r"[\s?.!,;:]+$", "", x.strip())


def _palavras_livres(texto: str, ctx: _Busca) -> list[str]:
    """Palavras que valem para a busca de texto (sem as da pergunta nem o nome dela)."""
    nome = set(ctx.nome_dela.split())
    palavras = [t for t in termos(texto) if t not in _PALAVRAS_PERGUNTA and t not in nome and t not in _COLOQUIAIS]
    fortes = [t for t in palavras if t not in _PALAVRAS_FRACAS]
    return fortes or palavras


def _entender_assunto(x: str, ctx: _Busca) -> _Assunto:
    rotulo = _limpar_x(x)
    sem_artigo = re.sub(r"^(?:a|o|as|os|um|uma)\s+", "", normalizar(rotulo))
    nucleo = re.sub(r"\s+(?:dela|dele|deles|delas|de voces|da gente)$", "", sem_artigo)
    if _RE_SO_ELA.fullmatch(sem_artigo) or (ctx.nome_dela and (
            nucleo in (ctx.nome_dela, ctx.nome_dela.split()[0])
            or (e_nome_dela(nucleo, ctx.nome_dela) and casar_pessoa(nucleo, ctx.pessoas) is None))):
        return _Assunto(rotulo=rotulo, dela=True)       # "a Ju", "a Julinha" também são ela
    pid = casar_pessoa(rotulo, ctx.pessoas) if ctx.pessoas else None
    if pid is not None:
        pessoa = next(p for p in ctx.pessoas if p["id"] == pid)
        chaves = [pessoa.get("nome"), *(pessoa.get("apelidos") or [])]
        return _Assunto(rotulo=rotulo, pessoas=[pessoa], raizes=[normalizar(c) for c in chaves if c])
    for topico in _TOPICOS:
        if topico.chave.fullmatch(nucleo):     # "comida japonesa" não é "comida": vai pela busca livre
            if topico.categoria == "Família":
                familia = [p for p in ctx.pessoas if grupo_relacao(p["relacao"]) == "familia" and e_dela(p["relacao"])]
                return _Assunto(rotulo=rotulo, pessoas=familia, categoria="Família",
                                raizes=list(topico.raizes), familia=True)
            grupo = [p for p in ctx.pessoas if topico.grupo_pessoas and grupo_relacao(p["relacao"]) == topico.grupo_pessoas]
            return _Assunto(rotulo=rotulo, pessoas=grupo, categoria=topico.categoria, raizes=list(topico.raizes))
    livres = _palavras_livres(rotulo, ctx)
    return _Assunto(rotulo=rotulo, raizes=list(dict.fromkeys(_raiz(t) for t in livres)))


def _coletar_assunto(assunto: _Assunto, ctx: _Busca) -> _Coleta:
    coleta = ctx.coleta()
    if assunto.dela:
        for item in ctx.banco.itens(pessoa_id=None):
            if item.tipo != TipoItem.NOTA:
                # Alergia e saúde sempre entram no resumo sobre ela: é informação de segurança.
                seguranca = 3.0 if item.tipo == TipoItem.RESTRICAO else 1.0 if "Saúde" in item.categorias else 0.0
                coleta.somar(item, item.importancia + _PESO_CERTEZA[item.certeza] + seguranca)
        return coleta

    def da_familia_dela(item: Item) -> bool:
        if item.pessoa_id in ctx.nao_dela:
            return False
        return not re.search(r"\((?:sua|seu|de voc[eê]s)\)", item.pessoa or "")

    aceitar = da_familia_dela if assunto.familia else None
    for pessoa in assunto.pessoas:
        for item in ctx.banco.itens(pessoa_id=pessoa["id"]):
            coleta.somar(item, 3.0)
        ctx.mencoes_da_pessoa(pessoa, coleta, 2.0)
    if assunto.categoria:
        for item in ctx.banco.itens(categoria=assunto.categoria):
            if aceitar is None or aceitar(item):
                coleta.somar(item, 3.0 if item.categorias[:1] == [assunto.categoria] else 2.0)
    if assunto.raizes:
        peso = 1.0 if (assunto.categoria or assunto.pessoas) else 2.0
        ctx.por_texto(assunto.raizes, coleta, peso, aceitar)
    return coleta


# ---------------------------------------------------------------- intenções

def _pessoas_importantes(p: str, n: str, ctx: _Busca):
    if not _RE_PESSOAS.search(n):
        return None
    ranking = []
    for pessoa in ctx.pessoas:
        if pessoa["id"] in ctx.nao_dela:
            continue
        # Na busca para a IA, registro 🔒 não conta menção nem entra com item.
        mencoes = [m for m in ctx.banco.mencoes(pessoa["id"], limite=1000) if m["registro_id"] not in ctx.privados]
        itens = [i for i in ctx.banco.itens(pessoa_id=pessoa["id"]) if i.registro_id not in ctx.privados]
        if not mencoes and not itens:
            continue
        pontos = len(mencoes) + 0.5 * len(itens)
        pontos += {"importante": 3, "familia": 2}.get(grupo_relacao(pessoa["relacao"]) or "", 0)
        pontos += 2 * sum(1 for i in itens if "Pessoas importantes" in i.categorias)
        ranking.append((pontos, pessoa, mencoes, itens))
    ranking.sort(key=lambda r: (-r[0], r[1]["id"]))
    ranking = ranking[:5]
    if not ranking:
        return [], NAO_ENCONTREI
    encontrados = []
    for _, pessoa, mencoes, itens in ranking:
        info = [i for i in itens if i.tipo == TipoItem.PESSOA_INFO] or \
               [i for i in itens if "Pessoas importantes" in i.categorias] or itens
        rotulo = _rotulo_cadastro(pessoa)
        resumo = f"{rotulo[:1].upper() + rotulo[1:]} — aparece em {_registros(max(len(mencoes), 1))}"
        if info:
            e = _encontrado(info[0], ctx)
        else:
            m = mencoes[0]
            e = {"item_id": None, "registro_id": m["registro_id"], "trecho": m["trecho"],
                 "certeza": Certeza.OBSERVADO.value, "data_registro": m["data_registro"],
                 "quando": tempo_atras((ctx.referencia - m["data_registro"]).days)}
        e.update({"resumo": resumo, "categoria": "Pessoas importantes", "pessoa_id": pessoa["id"]})
        encontrados.append(e)
    nomes = [_rotulo_cadastro(r[1]) for r in ranking]
    if len(nomes) == 1:
        texto = f"A pessoa que mais aparece nos seus registros é {nomes[0]}:"
    else:
        texto = f"As pessoas que mais aparecem nos seus registros são: {juntar(nomes)}."
    return encontrados, texto


def _favorito(p: str, n: str, ctx: _Busca):
    m = _RE_FAVORITO.search(n)
    if not m:
        return None
    palavra = m.group("cat")
    _, categoria, nome, filtro = next(f for f in _FAVORITOS if re.fullmatch(f[0], palavra))
    rx_filtro = re.compile(filtro) if filtro else None
    positivos, negativos = [], []
    for item in ctx.banco.itens(tipo=TipoItem.PREFERENCIA, categoria=categoria, pessoa_id=None):
        if not item.alvo or (rx_filtro and not rx_filtro.search(plano(item.alvo))):
            continue
        # "Qual a comida favorita": "sair pra jantar" e "cozinhar" são programas, não comidas.
        if categoria not in ("Presentes", "Lugares", "Hobbies") and plano(item.alvo).split()[0] in _ATIVIDADES:
            continue
        if item.polaridade == 1 and item.registro_id in ctx.privados:
            continue          # busca para a IA: o gosto registrado só em 🔒 não aparece
        (positivos if item.polaridade == 1 else negativos).append(item)
    grupos: list[list[Item]] = []
    for item in sorted(positivos, key=lambda i: (-ctx.ordinal(i), -i.id)):
        if any(semelhanca(item.alvo, neg.alvo) >= 88 for neg in negativos):
            continue   # há registro dizendo o contrário: fica de fora (é incerto)
        grupo = next((g for g in grupos if semelhanca(item.alvo, g[0].alvo) >= 88), None)
        if grupo is None:
            grupos.append([item])
        else:
            grupo.append(item)
    if not grupos:
        return [], NAO_ENCONTREI

    def peso(g: list[Item]) -> tuple:
        return (-len({i.registro_id for i in g}), -sum(_PESO_CERTEZA[i.certeza] for i in g), -ctx.ordinal(g[0]))

    grupos.sort(key=peso)
    coleta = ctx.coleta()
    escolhidos: list[Item] = []
    for g in grupos:
        for item in g:
            coleta.somar(item, 1.0)
            if len(escolhidos) < MAX_ENCONTRADOS:
                escolhidos.append(item)
    registros_do_primeiro = len({i.registro_id for i in grupos[0]})
    segundo = len({i.registro_id for i in grupos[1]}) if len(grupos) > 1 else 0
    total = len({i.registro_id for i in coleta.itens.values()})
    texto = f"Encontrei {_registros(total)} sobre {nome} de que ela gosta"
    cortou = len({i.registro_id for i in escolhidos}) < total
    if registros_do_primeiro >= 2 and registros_do_primeiro > segundo:
        alvo = min((i.alvo for i in grupos[0]), key=len)
        texto += f". O que mais aparece: {alvo} ({_registros(registros_do_primeiro)})."
        texto += " Estes são os mais relevantes:" if cortou else ""
    else:
        texto += ". Estes são os mais relevantes:" if cortou else ":"
    return [_encontrado(i, ctx) for i in escolhidos], texto


def _quando_comentou(p: str, n: str, ctx: _Busca):
    m = _RE_QUANDO.search(n)
    if not m:
        return None
    assunto = _entender_assunto(p[m.start("x"):m.end("x")], ctx)
    coleta = _coletar_assunto(assunto, ctx)
    if not coleta.itens:
        return [], NAO_ENCONTREI
    escolhidos = _escolher(coleta, ctx, recentes=True)
    datas = []
    for item in escolhidos:
        d = ctx.data_registro(item.registro_id)
        if d and d.strftime("%d/%m/%Y") not in datas:
            datas.append(d.strftime("%d/%m/%Y"))
    total = len({i.registro_id for i in coleta.itens.values()})
    texto = f"Encontrei {_registros(total)} sobre {assunto.rotulo}"
    if len({i.registro_id for i in escolhidos}) < total:
        texto += f". Os mais recentes são de {juntar(datas)}:"
    else:
        texto += f", em {juntar(datas)}:"
    return [_encontrado(i, ctx) for i in escolhidos], texto


def _o_que_ajuda(p: str, n: str, ctx: _Busca):
    m = _RE_ESTADO.search(n)
    familia = None
    emocao = None
    if m:
        palavra = m.group("e")
        for rx, lista in _EMOCOES:
            if rx.search(palavra):
                familia, emocao = lista, p[m.start("e"):m.end("e")]
                break
    if familia is None:
        if not _RE_O_QUE_AJUDA.search(n):
            return None
        familia = _TENSAO + _TRISTEZA
    rx_emocao = _rx_raizes(familia)
    polaridade = -1 if _RE_NAO_GOSTA.search(n) else 1    # "o que ela NÃO gosta quando está..."
    coleta = ctx.coleta()

    def aceitar(item: Item) -> bool:
        return item.pessoa_id is None and not item.pessoa

    for item in ctx.banco.itens(tipo=TipoItem.APOIO, pessoa_id=None):
        if item.polaridade == polaridade:
            coleta.somar(item, 3.0 + (1.0 if rx_emocao.search(_texto_item(item)) else 0.0))
    busca = ctx.coleta()
    ctx.por_texto(familia, busca, 1.0, aceitar)
    for item in busca.itens.values():
        if item.tipo in (TipoItem.ESTADO, TipoItem.EVENTO, TipoItem.DATA_IMPORTANTE, TipoItem.PENDENCIA):
            continue
        if not rx_emocao.search(plano(item.trecho)):
            continue
        if polaridade < 0 and (item.polaridade != -1 or item.tipo != TipoItem.PREFERENCIA):
            continue     # padrão negativo ("fica nervosa antes de...") é o que causa, não o que atrapalha
        if polaridade > 0 and (item.polaridade == -1 or (item.tipo in (TipoItem.NOTA, TipoItem.FATO)
                                                         and not _RE_AJUDA.search(plano(item.trecho)))):
            continue
        coleta.somar(item, 1.0 if item.tipo == TipoItem.NOTA else 2.0)
    if not coleta.itens:
        return [], NAO_ENCONTREI
    escolhidos = _escolher(coleta, ctx)
    o_que = "o que ajuda" if polaridade > 0 else "o que não ajuda"
    sobre = f"{o_que} quando ela está {emocao}" if emocao else f"{o_que} nos momentos difíceis"
    return [_encontrado(i, ctx) for i in escolhidos], _frase_encontrei(coleta, escolhidos, sobre)


def _sobre(regex: re.Pattern[str], p: str, n: str, ctx: _Busca):
    m = regex.search(n)
    if not m:
        return None
    assunto = _entender_assunto(p[m.start("x"):m.end("x")], ctx)
    coleta = _coletar_assunto(assunto, ctx)
    if not coleta.itens:
        return [], NAO_ENCONTREI
    escolhidos = _escolher(coleta, ctx)
    sim_nao = "oque" in regex.groupindex and not m.group("oque")
    texto = _frase_encontrei(coleta, escolhidos, assunto.rotulo, "Sim. " if sim_nao else "")
    return [_encontrado(i, ctx) for i in escolhidos], texto


def _o_que_sei(p: str, n: str, ctx: _Busca):
    return _sobre(_RE_SEI, p, n, ctx)


def _falou_sobre(p: str, n: str, ctx: _Busca):
    return _sobre(_RE_FALOU, p, n, ctx)


def _de_quem(p: str, n: str, ctx: _Busca):
    """Filtro de dono pela pergunta: outra pessoa citada ("a mãe dela gosta de sushi?") ou ela
    ("ela gosta de sushi?"). None quando a pergunta não diz de quem é."""
    citadas = extrair_pessoas(p, ctx.pessoas, ctx.nome_dela or None)
    pids = {pe.pessoa_id for pe in citadas if pe.pessoa_id is not None}
    relacoes = [pe.relacao for pe in citadas if pe.pessoa_id is None and pe.relacao]
    nomes = [pe.nome for pe in citadas if pe.pessoa_id is None and pe.nome]
    if pids or relacoes or nomes:
        def de_outra(item: Item) -> bool:
            if item.pessoa_id in pids:
                return True
            rotulo = item.pessoa or ""
            return any(mesma_relacao(relacao_base(rotulo), relacao_base(r)) for r in relacoes) or \
                any(normalizar(rotulo) == normalizar(x) for x in nomes)
        return de_outra, citadas
    if re.search(r"\b(?:ela|dela)\b", n) or (ctx.nome_dela and re.search(r"\b" + re.escape(ctx.nome_dela.split()[0])
                                                                       + r"\b", n)):
        return (lambda item: item.pessoa_id is None and not item.pessoa), []
    return None, []


def _aniversario(p: str, n: str, ctx: _Busca):
    m = _RE_ANIVERSARIO_DE.search(n)
    if not m:
        return None
    grupo = "x" if m.group("x") else "y"
    bruto = p[m.start(grupo):m.end(grupo)].strip()
    quem = re.sub(r"^(?:d[eoa]s?)\s+", "", bruto, flags=re.IGNORECASE)
    quem_n = normalizar(quem)
    dela = bool(re.fullmatch(r"(?:ela|dela|a\s+namorada|minha\s+namorada)", quem_n)) or \
        (ctx.nome_dela and e_nome_dela(quem_n, ctx.nome_dela) and casar_pessoa(quem, ctx.pessoas) is None)
    pid = None if dela else casar_pessoa(quem, ctx.pessoas)
    if dela:
        rotulo = "dela"
    elif quem != bruto:
        rotulo = bruto                                   # "da mãe dela", "do pai dela"
    elif re.match(r"(?:a|o)\s", quem_n):
        rotulo = ("da " if quem_n.startswith("a ") else "do ") + quem[2:].strip()
    else:
        rotulo = "de " + quem
    candidatos = []
    for item in ctx.banco.itens(tipo=TipoItem.DATA_IMPORTANTE):
        if "anivers" not in normalizar(item.resumo) or "namoro" in normalizar(item.resumo):
            continue
        if item.registro_id in ctx.privados:
            continue
        if dela and item.pessoa_id is None and not item.pessoa:
            candidatos.append(item)
        elif not dela and pid is not None and item.pessoa_id == pid:
            candidatos.append(item)
        elif not dela and pid is None and item.pessoa and (
                mesma_relacao(relacao_base(item.pessoa), relacao_base(quem_n)) or normalizar(item.pessoa) == quem_n):
            candidatos.append(item)
    if candidatos:
        coleta = ctx.coleta()
        for item in candidatos:
            coleta.somar(item, 1.0)
        escolhidos = _escolher(coleta, ctx, recentes=True)
        primeiro = next((i for i in escolhidos if i.data_evento), None)
        texto = (f"O aniversário {rotulo} é em {primeiro.data_evento:%d/%m}:" if primeiro
                 else _frase_encontrei(coleta, escolhidos, f"o aniversário {rotulo}"))
        return [_encontrado(i, ctx) for i in escolhidos], texto
    # Sem registro: vale a data informada em Ajustes ou no cadastro da pessoa.
    data = None
    if dela:
        data = ctx.banco.config().get("aniversario_dela")
    elif pid is not None:
        data = next((pp.get("aniversario") for pp in ctx.pessoas if pp["id"] == pid), None)
    if data:
        origem = "em Ajustes" if dela else "no cadastro da pessoa"
        return [], f"O aniversário {rotulo} é em {data:%d/%m} (você informou {origem})."
    return [], NAO_ENCONTREI


def _nao_gosta(p: str, n: str, ctx: _Busca):
    m = _RE_NAO_GOSTA_DE.search(n)
    if not m:
        return None
    x = m.group("x")
    verbo = re.search(r"\b(come|bebe)\b", n[:m.start("x")] if x else n)
    categoria = _CATEGORIA_DO_VERBO.get(x or "") or (_CATEGORIA_DO_VERBO.get(verbo.group(1)) if verbo else None)
    coleta = ctx.coleta()
    for item in ctx.banco.itens(pessoa_id=None):
        if item.tipo not in (TipoItem.PREFERENCIA, TipoItem.RESTRICAO) or item.polaridade != -1:
            continue
        if categoria and categoria not in item.categorias and categoria not in categorias_de(item.alvo or "", 3):
            continue
        coleta.somar(item, 3.0 if item.tipo == TipoItem.RESTRICAO else 2.0)
    if not coleta.itens:
        return [], NAO_ENCONTREI
    escolhidos = _escolher(coleta, ctx)
    sobre = "o que ela não gosta" + (f" de {x}" if x and categoria else "")
    return [_encontrado(i, ctx) for i in escolhidos], _frase_encontrei(coleta, escolhidos, sobre)


def _geral(p: str, n: str, ctx: _Busca):
    palavras = _palavras_livres(p, ctx)
    if not palavras:
        return [], NAO_ENCONTREI
    aceitar, citadas = _de_quem(p, n, ctx)
    coleta = ctx.coleta()
    for pe in citadas:
        if pe.pessoa_id is not None:
            for item in ctx.banco.itens(pessoa_id=pe.pessoa_id):
                coleta.somar(item, 1.0)
    raizes: list[str] = []
    for t in palavras:
        topico = next((tp for tp in _TOPICOS if tp.chave.fullmatch(t)), None)
        if topico:
            if topico.categoria:
                for item in ctx.banco.itens(categoria=topico.categoria):
                    if aceitar is None or aceitar(item):
                        coleta.somar(item, 2.0)
            if topico.grupo_pessoas:
                for pessoa in ctx.pessoas:
                    if grupo_relacao(pessoa["relacao"]) == topico.grupo_pessoas:
                        for item in ctx.banco.itens(pessoa_id=pessoa["id"]):
                            coleta.somar(item, 2.0)
            raizes += topico.raizes
        else:
            raizes.append(_raiz(t))
    ctx.por_texto(list(dict.fromkeys(raizes)), coleta, 2.0, aceitar)
    if not coleta.itens:
        return [], NAO_ENCONTREI
    escolhidos = _escolher(coleta, ctx)
    return [_encontrado(i, ctx) for i in escolhidos], _frase_encontrei(coleta, escolhidos, "isso")


_INTENCOES = (_pessoas_importantes, _aniversario, _favorito, _quando_comentou, _o_que_ajuda, _nao_gosta,
              _o_que_sei, _falou_sobre)


# ---------------------------------------------------------------- entrada

def _usa_ia(motor) -> bool:
    return motor is not None and getattr(motor, "nome", "regras") != "regras"


def buscar(pergunta: str, banco: Banco, motor: Motor | None, referencia: date,
           privados: set[int] | frozenset[int] | None = None) -> Resposta:
    """Responde só com o que está gravado. Sem nada: 'Não encontrei essa informação.'

    `privados`: registros "não enviar para a IA" que ficam de fora (a busca que vai para a IA,
    seção 17). Eles não entram nos achados nem contam como menção de uma pessoa.
    """
    original = pergunta if isinstance(pergunta, str) else ""
    p = original.strip()[:500]
    n = plano(p)
    if not n:
        return Resposta(pergunta=original, texto=NAO_ENCONTREI)
    ctx = _Busca(banco, referencia, frozenset(privados or ()))
    resultado = None
    for intencao in _INTENCOES:
        resultado = intencao(p, n, ctx)
        if resultado is not None:
            break
    encontrados, texto = resultado if resultado is not None else _geral(p, n, ctx)
    if not encontrados:
        # Só a data guardada em Ajustes ou no cadastro ("O aniversário dela é em 14/03") vem sem registro.
        return Resposta(pergunta=original, texto=texto or NAO_ENCONTREI)
    if _usa_ia(motor):
        try:
            r = motor.responder(original, encontrados)
            validos = {e["registro_id"] for e in encontrados}
            usados = [e for e in (r.encontrados or []) if isinstance(e, dict) and e.get("registro_id") in validos]
            if r.motor != "regras" and r.texto and usados:
                return Resposta(pergunta=original, texto=r.texto, encontrados=usados, motor=r.motor)
        except Exception:  # noqa: BLE001 - a IA nunca derruba a busca: fica o modelo de frase
            pass
    return Resposta(pergunta=original, texto=texto, encontrados=encontrados)


__all__ = ["MAX_ENCONTRADOS", "buscar"]
