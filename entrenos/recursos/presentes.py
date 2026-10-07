"""Ideias de presentes (DESIGN.md, seção 20.2).

O que este recurso faz, sempre com o que foi registrado (nunca inventa):

- **Lê o texto** (`extrair`): presentes dados ou recebidos ("dei um colar pra ela e ela amou", "ela
  ganhou um perfume da mãe dela e não curtiu muito", "a Mariana deu um caderno pra ela"), presentes
  separados para dar ("comprei um anel pra dar no aniversário dela") e desejos de objeto que as
  regras do núcleo não pegam ("ela tá doida por um kindle", "precisa de uma mochila nova", "adoraria
  ganhar uma câmera"). O "queria um fone novo" o núcleo já guarda como DESEJO (Presentes): este
  recurso lê esse item direto do banco, sem duplicar.
- **Grava** o que o usuário confirmou na revisão (`gravar_extras`), conferindo tudo de novo.
- **Monta a tela** `#presentes` (`painel`): "Ela comentou que gostaria" (agrupado, com "há 8
  meses" e "talvez já dado"), "Ideias possíveis" 🔵 (só de preferências fortes e hobbies, com a
  pontuação explicada e a origem citada), "Já separado", "Já recebeu" (de você / de outros),
  "Gostou", "Não gostou" e "Não recomendados" (não gosta, restrições, não quer, já dado).
- **Cartão no Hoje** (`cartoes_hoje`): ocasião em até 45 dias (aniversário dela, de namoro, Natal,
  Dia dos Namorados) com algo registrado: "Ideias para o aniversário dela (em 30 dias): 3 coisas
  que ela mencionou".
- **Sensível ao momento dela**: com um estado difícil nos últimos 7 dias e algo que ela disse que
  ajuda, a tela lembra disso (sem diagnóstico, citando os registros).

Tabelas (contrato da seção 20.2 mais colunas próprias): `presentes`, `presentes_desejos` e
`presentes_ocultos`. Nada daqui vai para a IA (os extras nunca vão; o recurso não manda nada).
Tudo roda no navegador também (Pyodide): nada de thread, sleep ou rede.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import alertas
from ..banco import NaoEncontrado, OperacaoProibida
from ..modelos import Certeza, Item, TipoItem
from ..perfil import forca_preferencia
from ..regras.certeza import classificar_certeza, trechos_nao_afirmados
from ..regras.datas import extrair_datas, proxima_ocorrencia
from ..regras.pessoas import RELACOES, e_nome_dela, genero_relacao, plano
from ..texto import frases, normalizar, semelhanca, tempo_atras, termos
from . import cartao, rota, sem_frases_reservadas

NOME = "presentes"
ROTULO = "Ideias de presentes"
TIPOS_EXTRAS = ("presente", "presente_desejo")

ESQUEMA = [
    """
    CREATE TABLE presentes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        descricao TEXT NOT NULL,
        data TEXT,
        ocasiao TEXT,
        de_quem TEXT NOT NULL DEFAULT 'eu' CHECK (de_quem IN ('eu', 'outro')),
        reacao TEXT NOT NULL DEFAULT 'nao_sei' CHECK (reacao IN ('gostou', 'nao_gostou', 'neutra', 'nao_sei')),
        registro_id INTEGER,
        criado_em TEXT NOT NULL,
        trecho TEXT,
        quem TEXT,
        certeza TEXT NOT NULL DEFAULT 'observado',
        planejado INTEGER NOT NULL DEFAULT 0,
        desejo_item_id INTEGER,
        desejo_id INTEGER,
        atualizado_em TEXT
    );
    CREATE INDEX presentes_registro ON presentes(registro_id);
    CREATE TABLE presentes_desejos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        descricao TEXT NOT NULL,
        trecho TEXT NOT NULL,
        certeza TEXT NOT NULL DEFAULT 'observado',
        pista TEXT,
        registro_id INTEGER,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX presentes_desejos_registro ON presentes_desejos(registro_id);
    CREATE TABLE presentes_ocultos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL CHECK (tipo IN ('desejo', 'desejo_dado', 'ideia')),
        ref TEXT NOT NULL,
        presente_id INTEGER,
        registro_id INTEGER,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX presentes_ocultos_registro ON presentes_ocultos(registro_id)
    """,
]
ESQUEMA_VERSAO = 1

REACOES: dict[str, str] = {"gostou": "Gostou", "nao_gostou": "Não gostou", "neutra": "Achou normal",
                           "nao_sei": "Ainda não sei"}
DE_QUEM = ("eu", "outro")
OCASIOES: dict[str, str] = {
    "aniversario_dela": "Aniversário dela",
    "namoro": "Aniversário de namoro",
    "natal": "Natal",
    "dia_dos_namorados": "Dia dos Namorados",
    "pascoa": "Páscoa",
    "formatura": "Formatura",
    "dia_da_mulher": "Dia da Mulher",
    "sem_motivo": "Sem data especial",
    "outra": "Outra ocasião",
}
CERTEZAS_ACEITAS = ("confirmado", "observado", "inferencia")
_ORDEM_CERTEZA = {"inferencia": 1, "observado": 2, "confirmado": 3}

DIAS_OCASIAO = 45              # ocasião em até 45 dias -> cartão no Hoje
DIAS_OCASIOES_TELA = 365
DIAS_MOMENTO = 7               # estado difícil recente que muda o que a tela lembra
MESES_JA_DADO = 12             # presente seu mais novo que isso entra em "não recomendados (já dado)"
MESES_JA_GANHOU = 6            # presente de outra pessoa mais novo que isso também
MAX_IDEIAS = 8
MAX_NO_CARTAO = 3
MAX_DESCRICAO = 120
MAX_QUEM = 80
MAX_PALAVRAS_OBJETO = 8
SEMELHANCA_MESMA_COISA = 85

# Categorias de onde uma preferência pode virar ideia de presente (dormir até tarde não vira).
PRESENTEAVEIS = frozenset({"Presentes", "Comidas", "Bebidas", "Filmes", "Séries", "Música", "Livros", "Hobbies",
                           "Lugares", "Viagens", "Coisas que deixam feliz"})

ERRO_DESCRICAO = "Diga qual é o presente (até 120 letras)."
ERRO_REACAO = "Reação desconhecida."
ERRO_OCASIAO = "Ocasião desconhecida."
ERRO_DE_QUEM = "Diga se o presente foi seu ou de outra pessoa."
ERRO_DATA = "A data precisa estar no formato AAAA-MM-DD."
ERRO_QUEM = "O nome de quem deu é longo demais."
ERRO_CAMPO = "Este campo não pode ser mudado aqui."


# ======================================================================== utilidades

def _campo(obj: Any, nome: str, padrao: Any = None) -> Any:
    """Atributo de um objeto (Item, PessoaEncontrada...) ou chave de um dict."""
    if isinstance(obj, dict):
        return obj.get(nome, padrao)
    return getattr(obj, nome, padrao)


def _valor(v: Any) -> Any:
    return getattr(v, "value", v)


def _data(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _dia(d: date | None, referencia: date) -> str:
    if d is None:
        return ""
    return d.strftime("%d/%m") if d.year == referencia.year else d.strftime("%d/%m/%Y")


def _quando(d: date | None, referencia: date) -> str | None:
    if d is None or d > referencia:
        return None
    return tempo_atras((referencia - d).days)


def _somar_meses(d: date, meses: int) -> date:
    return alertas.somar_meses(d, meses)


_RE_INICIO_CHAVE = re.compile(
    r"^(?:(?:de|do|da|dos|das|o|a|os|as|um|uma|uns|umas|aquele|aquela|aqueles|aquelas|daquele|daquela|daqueles"
    r"|daquelas|esse|essa|esses|essas|desse|dessa|desses|dessas|este|esta|deste|desta|seu|sua|ganhar|receber|ter"
    r"|comprar|novo|nova)\s+)+")


def chave(texto: str | None) -> str:
    """'um fone novo' e 'Fones novos' caem na mesma chave (para agrupar e comparar)."""
    n = _RE_INICIO_CHAVE.sub("", normalizar(texto or ""))
    n = re.sub(r"[^\w\s-]", " ", n)
    return " ".join(p[:-1] if len(p) > 3 and p.endswith("s") else p for p in n.split())


def mesma_coisa(a: str | None, b: str | None) -> bool:
    ca, cb = chave(a), chave(b)
    return bool(ca and cb) and (ca == cb or semelhanca(ca, cb) >= SEMELHANCA_MESMA_COISA)


_RE_INICIO_OBJETO = re.compile(
    r"^(?:(?:um|uma|uns|umas|o|a|os|as|aquele|aquela|aqueles|aquelas|daquele|daquela|daqueles|daquelas|esse|essa"
    r"|esses|essas|desse|dessa|desses|dessas|este|esta|deste|desta|ganhar|receber|ter|de\s+presente)\s+)+",
    re.IGNORECASE)


def _limpar_objeto(texto: str) -> str:
    """'um fone novo' -> 'fone novo'; 'ganhar um perfume da Natura' -> 'perfume da Natura'.
    Só tira palavras do começo e pontuação das pontas: o resto é o que está escrito."""
    s = re.sub(r"\s+", " ", texto or "").strip(" \t,;:.!?…\"“”'()")
    s = _RE_INICIO_OBJETO.sub("", s).strip()
    return s


# ======================================================================== extração (regras)

# Palavras que, como objeto de "dar"/"ganhar", não são presente ("dei uma volta", "ganhou o jogo").
_NAO_PRESENTE = frozenset("""
volta voltinha risada risadas gargalhada tempo tempinho bola passada passadinha pulo pulinho jeito jeitinho carona
beijo beijinho beijao beijos abraco abracinho abracos atencao razao conselho conselhos olhada olhadinha forca forcinha
ligada ligadinha toque oi ola bom boa parabens desculpa desculpas fora bronca sorte azar mole bobeira sinal noticia
noticias recado resposta aula aulas palestra curso treinamento carinho cafune colo apoio ouvido ouvidos espaco tchau
susto presenca mao ajuda help vacilo perdido satisfacao gelo match like cochilo cochilada banho conta certo errado
problema problemas trabalho trabalheira ideia sugestao palpite opiniao feedback retorno alo grito tapa tapinha empurrao
chute mordida beliscao cutucada piscada piscadinha sorriso sorrisinho nota notas entrevista depoimento entrada lance
migue mancada trela credito valor importancia chance oportunidade prazo ultimato folga ferias permissao autorizacao
liberdade bencao descarga partida re seta play pause ok print zoom unfollow bloqueio cano remedio remedios agua janta
jantar almoco cafe-da-manha jogo partidas aposta discussao promocao aumento bonus peso quilos kilos kg eleicao causa
processo corrida campeonato torneio disputa concurso premio premiacao medalha trofeu vaga cargo emprego elogio
elogios dia apelido cicatriz ruga rugas espinha espinhas febre gripe virose ponto pontos desconto cupom cashback
milhas briga fama seguidores likes curtidas confianca coragem experiencia nada nenhum nenhuma tudo isso isto aquilo
coisa coisas presente presentes presentinho presentinhos mimo mimos lembrancinha lembrancinhas surpresa surpresinha
algo alguma algum mais menos outro outra bebe bebezinho xingamento bolada direito dinheiro grana pix
""".split())
# Ajuda que não é presente ("precisa de um tempo", "precisa de ajuda").
_NAO_DESEJO = _NAO_PRESENTE | frozenset("""
descanso pausa respiro tratamento consulta terapia conversa medico medica solucao luz help abraco colo
""".split())
# Parentes ganhando ("ganhou um sobrinho") não são presente; bicho de estimação pode ser.
_PARENTES = frozenset(k for k in RELACOES if k not in {"pet", "cachorro", "cachorra", "gato", "gata", "cadela",
                                                         "cachorrinho", "cachorrinha", "gatinho", "gatinha"})

_RE_IRONIA = re.compile(r"\b(?:so\s+que\s+nao|sqn)\b")
_NEGACOES = frozenset({"nao", "nunca", "n", "jamais", "nem", "sem"})
_CONDICIONAIS = frozenset({"se", "caso"})

def _alternativas(chaves: Any) -> str:
    """'melhor amiga' -> 'melhor\\s+amiga' (as relações viram alternativas de um regex)."""
    return "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(chaves, key=len, reverse=True))


_RELACOES_RX = _alternativas(RELACOES)
# Quem recebe, logo depois de "pra/para/a/pro": ela, outra pessoa, ou "nela" (expressão: "dei um bolo nela").
_RE_PREP_DESTINO = re.compile(r"\s*\b(?:pra|para|a|pro|pros|pras|ao|aos)\s+")
_RE_NELA = re.compile(r"\s*\bnela\b")
_ELA_DESTINO = re.compile(
    r"(?:ela|mozao|mozinho|minha\s+(?:namorada|noiva|esposa|mulher|mina|gata|princesa|amada|linda|pequena|morena"
    r"|loira|menina)|meu\s+(?:amor|bem|bebe|xodo|chuchu)|minha\s+amor)\b")
_OUTRO_DESTINO = re.compile(
    r"(?:(?:a|o|as|os|minha|meu|minhas|meus|sua|seu|suas|seus)\s+)?(?:" + _RELACOES_RX + r")(?:\s+dela|\s+dele)?\b"
    r"|(?:ele|eles|elas|mim|voce|voces|gente|todo\s+mundo|todos|todas|alguem|eles\s+dois)\b")

_RE_FIM_OBJETO = re.compile(
    r"\s+(?:pra|para|pro|pros|pras|a|ao|aos|nela|nele|pela|pelo)\s"
    r"|\s+de\s+(?:presente|presentinho|aniversario|niver|natal|pascoa|formatura|mesversario|namorados|lembranca|mim"
    r"|surpresa|\d+\s+(?:anos?|meses|mes)\b|um\s+ano)\b"
    r"|\s+(?:no|na|nos|nas|num|numa|em|pelo|pela)\s"
    r"|\s+(?:e|mas|que|porque|pq|quando|so\s+que|enquanto|pois|entao|ai|dai|hoje|ontem|anteontem|agora|semana|mes"
    r"|ano|sabado|domingo|segunda|terca|quarta|quinta|sexta|ha|faz|depois|antes|sem|do\s+nada|kk+|rs+|haha+|hj"
    r"|esse|essa|este|esta|nesse|nessa|neste|nesta|uns\s+dias|dias|logo|ja|mesmo|tambem|tb|tbm)\b"
    r"|\s*[,;:.!?…()\"“”]"
    r"|$")

_OCASIOES_RE = [
    ("namoro", re.compile(r"\b(?:(?:aniversario|niver)\s+de\s+namoro|mesversario|bodas|(?:\d+|um|dois|tres)\s+"
                          r"(?:anos?|meses)\s+(?:de\s+namoro|juntos))\b")),
    ("dia_dos_namorados", re.compile(r"\bdia\s+dos\s+namorados\b")),
    ("aniversario_dela", re.compile(r"\b(?:aniversario|niver)\b")),
    ("natal", re.compile(r"\bnatal\b")),
    ("pascoa", re.compile(r"\bpascoa\b")),
    ("formatura", re.compile(r"\bformatura\b")),
    ("dia_da_mulher", re.compile(r"\bdia\s+(?:da|das)\s+mulher(?:es)?\b")),
    ("sem_motivo", re.compile(r"\b(?:sem\s+motivo|do\s+nada|so\s+porque\s+sim|sem\s+data\s+especial)\b")),
]
# Aniversário de outra pessoa ("aniversário da mãe dela") não é ocasião dela.
_RE_ANIVERSARIO_DE_OUTRO = re.compile(r"\b(?:aniversario|niver)\s+d[aeo]s?\s+(?!ela\b|namoro\b)[\w-]+")

# Reação: neutra (mais específica) > negativa > positiva.
_RE_REACAO_NEUTRA = re.compile(
    r"\b(?:achou\s+(?:ok|legal|bonitinh[oa]s?|normal|razoavel|so\s+ok)|gostou\s+mais\s+ou\s+menos"
    r"|curtiu\s+mais\s+ou\s+menos|mais\s+ou\s+menos|ficou\s+(?:indiferente|na\s+dela)|nao\s+(?:demonstrou|disse|falou)"
    r"\s+nada|nao\s+amou|nem\s+tanto|reacao\s+morna|meio\s+morna)\b")
_RE_REACAO_NEG = re.compile(
    r"\b(?:odiou|detestou|(?:nao|n)\s+(?:gostou|curtiu|usou|usa|quis)|torceu\s+o\s+nariz|fez\s+(?:uma\s+)?cara\s+feia"
    r"|ficou\s+(?:meio\s+|bem\s+|muito\s+)?(?:decepcionada|chateada|sem\s+graca)|trocou|devolveu|achou\s+(?:muito\s+"
    r"|meio\s+|bem\s+|super\s+|um\s+pouco\s+|mo\s+)?(?:feio|feia|feios|feias|brega|estranho|estranha|horrivel|ruim"
    r"|cafona|exagerado|exagerada)|nem\s+(?:usou|abriu|olhou|ligou))\b")
_RE_REACAO_POS = re.compile(
    r"\b(?:amou|adorou|gostou|curtiu|amando|adorando|curtindo|ficou\s+(?:muito\s+|super\s+|mt\s+|mto\s+|bem\s+|toda\s+"
    r"|tao\s+)?(?:feliz|felizona|felizinha|contente|emocionada|radiante|encantada|animada|boba|bobinha|alegre)"
    r"|(?:ta|esta|ficou)\s+(?:toda\s+|super\s+)?apaixonada|se\s+emocionou|chorou\s+de\s+(?:emocao|alegria)|vibrou"
    r"|pulou\s+de\s+alegria|deu\s+pulos|usa\s+todo\s+dia"
    r"|nao\s+tira|vive\s+usando|ja\s+(?:esta\s+|ta\s+)?usando|achou\s+(?:muito\s+|super\s+)?(?:lind[oa]s?|perfeit[oa]s?"
    r"|incrivel|incriveis|maravilhos[oa]s?|o\s+maximo|top|fof[oa]s?))\b")

# Eu dando (passado) — e os verbos que só valem com ela como destino ou marca de presente.
_RE_EU_DEI = re.compile(r"\b(?P<v>dei|demos|presenteei|presenteamos)\b")
_RE_EU_OUTROS = re.compile(
    r"\b(?P<v>comprei|compramos|trouxe|trouxemos|levei|levamos|mandei|mandamos|enviei|fiz|fizemos|encomendei"
    r"|preparei|montei|escrevi|bordei|pintei|costurei|tricotei|gravei|separei|guardei|escondi)\b")
# Eu pensando em dar (separado, ainda não dado).
_RE_EU_VOU = re.compile(
    r"\b(?:vou|vamos|quero|queria|pretendo|planejo|penso\s+em|pensei\s+em|(?:to|estou|tava|estava)\s+pensando\s+em"
    r"|decidi|resolvi|tenho\s+que|preciso|vou\s+ver\s+se)\s+(?P<v>dar|presentear|comprar|levar|fazer|mandar"
    r"|encomendar|dou|compro)\b")
# Outra pessoa dando para ela.
_RE_OUTRO_DEU = re.compile(
    r"\b(?P<v>deu|presenteou|comprou|levou|mandou|enviou|fez|encomendou|deram|compraram|trouxeram|mandaram"
    r"|presentearam|trouxe)\b")
# Ela recebendo.
_RE_ELA_GANHOU = re.compile(r"\b(?P<v>ganhou|recebeu|ganhara|ganhou\s+de\s+presente|recebeu\s+de\s+presente)\b")
_RE_PLANO_MARCA = re.compile(
    r"\b(?:pra|para)\s+(?:dar|entregar)\b|\bainda\s+nao\s+(?:dei|entreguei)\b|\bvou\s+(?:dar|entregar)\b"
    r"|\bescondid|\bguardad|\bsurpresa\s+(?:pro|pra|para)\b")
_RE_MEU_PRESENTE = re.compile(
    r"\b(?:(?:o\s+)?meu|(?:o\s+)?nosso)\s+presente\s+(?:(?:pra|para)\s+(?:ela|minha\s+namorada)\s+)?(?:de\s+[\w\s]{2,30}?\s+)?"
    r"(?:foi|(?P<futuro>vai\s+ser|sera))\s+|\bo\s+presente\s+que\s+(?:eu\s+)?dei\s+(?:(?:pra|para)\s+ela\s+)?foi\s+")
_RE_MARCA_PRESENTE = re.compile(r"\b(?:de\s+presente|presentinho|presente|mimo|lembrancinha|surpresa)\b")
_RE_ELA_SUJEITO = re.compile(r"\b(?:ela|minha\s+(?:namorada|noiva|esposa|mulher|mina|gata)|a\s+gata|mozao)\b")
_RE_DE_MIM = re.compile(r"^\s+de\s+mim\b")
# Quem deu, logo depois do "ganhou" ("ganhou da mãe dela um vestido") ou depois do objeto.
_DOADOR_RX = r"(?P<q>(?:minha|meu|minhas|meus|sua|seu)\s+(?:" + _RELACOES_RX + r")|mim|(?:" + _RELACOES_RX + r")(?:\s+dela)?)\b"
_RE_DOADOR = re.compile(r"^\s+(?P<prep>de|da|do|das|dos)\s+" + _DOADOR_RX)
_RE_DOADOR_DEPOIS = re.compile(r"\s+(?P<prep>de|da|do|das|dos)\s+" + _DOADOR_RX)
_ARTIGO_DA_PREPOSICAO = {"da": "a", "do": "o", "das": "as", "dos": "os", "de": ""}


def _com_artigo(quem: str, preposicao: str | None) -> str:
    """'mãe dela' + 'da' -> 'a mãe dela' (guardado assim, a tela diz "da mãe dela")."""
    artigo = _ARTIGO_DA_PREPOSICAO.get((preposicao or "").lower(), "")
    if not artigo or re.match(r"(?:a|o|as|os|minha|meu|minhas|meus|sua|seu|suas|seus)\s", normalizar(quem)):
        return quem
    return f"{artigo} {quem}"

# Desejos que o núcleo não pega (o "queria um X"/"quer ganhar um X" já vem como DESEJO, Presentes).
_DESEJOS = [
    ("doida", re.compile(
        r"\b(?:ta|tava|esta|estava|anda|andava|vive|fica|ficou)\s+(?:doida|louca|maluca|doidinha|alucinada|doidona|loucona"
        r"|morrendo\s+de\s+vontade)\s+(?:por|pra\s+ter|para\s+ter|pra\s+ganhar|para\s+ganhar|de\s+ter|de\s+ganhar"
        r"|num|numa)\s+"), None),
    ("de_olho", re.compile(
        r"\b(?:ta|esta|anda|vive|ficou)\s+de\s+olho\s+(?=(?:num|numa|nuns|numas|em\s+um|em\s+uma|naquele|naquela"
        r"|naqueles|naquelas|nesse|nessa)\s)(?:num|numa|nuns|numas|em\s+um|em\s+uma|naquele|naquela|naqueles"
        r"|naquelas|nesse|nessa)\s+"), None),
    ("vive_falando", re.compile(
        r"\b(?:vive|fica|sempre)\s+(?:falando|comentando|fala|comenta)\s+(?=(?:daquele|daquela|daqueles|daquelas"
        r"|desse|dessa|deste|desta)\s)"), Certeza.INFERENCIA),
    ("precisa", re.compile(
        r"\b(?:precisa|(?:ta|esta|anda)\s+precisando)\s+(?:muito\s+|tanto\s+)?de\s+(?=(?:um|uma|uns|umas)\s)"), None),
    ("adoraria", re.compile(
        r"\b(?:adoraria|amaria|ia\s+amar|ia\s+adorar|iria\s+amar|iria\s+adorar|ficaria\s+feliz\s+(?:em|de)"
        r"|seria\s+um\s+sonho)\s+(?:ganhar|receber|ter)\s+"), None),
    ("pediu", re.compile(r"\bpediu\s+(?:de\s+presente\s+)?(?=(?:um|uma|uns|umas|o|a|os|as)\s)"), None),
    ("olhando", re.compile(
        r"\b(?:ficou|fica|ficava|vive)\s+(?:babando|namorando|suspirando)\s+(?:por\s+|em\s+|no\s+|na\s+|num\s+"
        r"|numa\s+)?"), Certeza.INFERENCIA),
    ("olhando", re.compile(
        r"\b(?:ficou|fica|ficava)\s+(?:olhando|admirando|encarando)\s+(?=(?:um|uma|uns|umas|o|a|os|as|aquele"
        r"|aquela)\s.*\b(?:vitrine|loja|site|instagram|insta|anuncio|shopping|feira)\b)"), Certeza.INFERENCIA),
]
# "Na lista de desejos dela tem uma câmera instantânea" (o objeto vem depois; revisão de 07/10).
_RE_LISTA_DESEJOS_TEM = re.compile(
    r"\b(?:lista\s+de\s+desejos|wishlist)(?:\s+dela)?\s+(?:tem|ta|esta|inclui|entrou|agora\s+tem|ja\s+tem)\s+")
_RE_PRESENTEEI_COM = re.compile(r"\s+(?P<quem>ela|a\s+minha\s+\w+|minha\s+\w+|[\w'-]+)\s+com\s+")

_RE_DE_PRESENTE_DE = re.compile(
    r"\s+de\s+presente\s+(?P<prep>d[aeo]s?)\s+(?:(?:[A-ZÀ-Ý][\wÀ-ÿ'-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'-]+)?)"
    r"|(?:(?:minha|meu|sua|seu|a|o)\s+)?(?:" + _RELACOES_RX + r")(?:\s+dela)?)(?![\w])")
_RE_LISTA_DESEJOS = re.compile(
    r"\b(?:colocou|botou|pos|adicionou|salvou)\s+(?P<obj>.{2,80}?)\s+(?:na|no|nos|nas)\s+(?:lista\s+de\s+desejos"
    r"|wishlist|carrinho|favoritos)\b")
_RE_SERIA_PRESENTE = re.compile(
    r"\b(?:um|uma)\s+(?P<obj>.{2,60}?)\s+seria\s+(?:um\s+)?(?:otimo|bom|lindo|perfeito|o\s+melhor)\s+presente\b")


@dataclass
class _Frase:
    """Uma frase do texto: o original, o plano (minúsculas sem acento, mesmo tamanho) e onde está."""

    texto: str
    p: str
    ini: int
    fim: int
    nao_afirmado: list[tuple[int, int]] = field(default_factory=list)

    def negado(self, pos: int, palavras: int = 3) -> bool:
        antes = re.findall(r"[\w-]+", self.p[max(0, pos - 30):pos])[-palavras:]
        return any(w in _NEGACOES for w in antes)

    def condicional(self, pos: int) -> bool:
        antes = re.findall(r"[\w-]+", self.p[max(0, pos - 25):pos])[-3:]
        return any(w in _CONDICIONAIS for w in antes)

    def afirmado(self, ini: int, fim: int) -> bool:
        return not any(a < fim and ini < b for a, b in self.nao_afirmado)


# "um ingresso pro show do Coldplay": "pro show" é parte do presente, não o destino (revisão de 07/10).
_RE_COMPLEMENTO_DO_OBJETO = re.compile(
    r"\s+(?:pro|pra|para|pros|pras|ao|a)\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:show|shows|jogo|festival|teatro|cinema|musical"
    r"|concerto|peca|espetaculo|evento|parque|museu|exposicao|stand[\s-]?up|cruzeiro|spa|massagem)\b")


def _objeto(fr: _Frase, pos: int) -> tuple[int, int] | None:
    """O objeto que começa em `pos`: até a próxima fronteira (destino, ocasião, outra oração).
    Fronteira logo no começo ("dei de aniversário", "dei pra ela") = não há objeto."""
    m = _RE_FIM_OBJETO.search(fr.p, pos)
    while m and m.start() > pos and _RE_COMPLEMENTO_DO_OBJETO.match(fr.p, m.start()):
        m = _RE_FIM_OBJETO.search(fr.p, _RE_COMPLEMENTO_DO_OBJETO.match(fr.p, m.start()).end())
    fim = m.start() if m else len(fr.p)
    inicio = pos
    while inicio < len(fr.p) and fr.p[inicio].isspace():
        inicio += 1
    if fim <= inicio:
        return None
    pedaco = fr.p[inicio:fim]
    if len(pedaco.split()) > MAX_PALAVRAS_OBJETO or re.match(r"(?:de|do|da|dos|das|no|na|em|com|pra|para|pro|por)\s",
                                                              pedaco):
        return None
    return inicio, fim


_RE_E_OUTRO_OBJETO = re.compile(r"\s+e\s+(?=(?:um|uma|uns|umas)\s)")


def _objetos(fr: _Frase, pos: int) -> list[tuple[int, int]]:
    """Um objeto ou uma lista curta: "um colar e um brinco" -> dois (só com artigo, para não cortar
    "pão de mel e chocolate" errado)."""
    primeiro = _objeto(fr, pos)
    if primeiro is None:
        return []
    saida = [primeiro]
    while len(saida) < 4:
        m = _RE_E_OUTRO_OBJETO.match(fr.p, saida[-1][1])
        proximo = _objeto(fr, m.end()) if m else None
        if proximo is None:
            break
        saida.append(proximo)
    return saida


def _objeto_valido(fr: _Frase, ini: int, fim: int, proibidas: frozenset[str]) -> str | None:
    descricao = _limpar_objeto(fr.texto[ini:fim])
    palavras = chave(descricao).split()
    if not palavras or not re.search(r"[A-Za-zÀ-ÿ]", descricao):
        return None
    if palavras[0] in proibidas or normalizar(descricao) in proibidas:
        return None
    if " ".join(palavras[:2]) in ("bom dia", "boa noite", "boa tarde"):
        return None
    if palavras[0] in _PARENTES or normalizar(descricao) in _PARENTES:
        return None
    if palavras[0] in ("ela", "ele", "eu", "voce", "mim", "si", "gente", "me", "te", "lhe", "nos"):
        return None
    return descricao


def _destino(fr: _Frase, pos: int, nome_dela: str | None) -> tuple[str | None, int]:
    """Quem recebe, logo em `pos`: ("ela" | "outro" | "idioma" | None, onde acaba).

    "pra ela", "pra minha namorada", "pra Ju" (nome ou apelido dela) -> ela; "pra mãe dela", "pra
    Mariana", "pra mim" -> outro; "nela" -> idioma ("dei um bolo nela"); "pra dar", "pra comemorar"
    (não é gente) -> None, e a posição não anda."""
    n = _RE_NELA.match(fr.p, pos)
    if n:
        return "idioma", n.end()
    m = _RE_PREP_DESTINO.match(fr.p, pos)
    if not m:
        return None, pos
    depois = m.end()
    e = _ELA_DESTINO.match(fr.p, depois)
    if e:
        return "ela", e.end()
    o = _OUTRO_DESTINO.match(fr.p, depois)
    if o:
        return "outro", o.end()
    palavra = re.match(r"[\wÀ-ÿ'-]+", fr.texto[depois:])
    if palavra and nome_dela and e_nome_dela(palavra.group()[:1].upper() + palavra.group()[1:], nome_dela):
        return "ela", depois + palavra.end()            # "pra Ju", "pra ju" (o apelido dela)
    if palavra and palavra.group()[:1].isupper() and palavra.group().lower() not in ("ela", "ele"):
        return "outro", depois + palavra.end()          # "pra Mariana"
    return None, pos


_RE_DE_PRESENTE = re.compile(r"\s+de\s+(?:presente|presentinho|surpresa)\b")


def _antes_do_objeto(fr: _Frase, pos: int, nome_dela: str | None) -> tuple[int, str | None]:
    """Pula o que pode vir entre o verbo e o objeto, em qualquer ordem: "pra ela" e "de presente"
    ("dei de presente pra ela um caderno"). Devolve (onde começa o objeto, destino achado)."""
    destino = None
    for _ in range(3):
        d, novo = _destino(fr, pos, nome_dela)
        if d is not None:
            destino = d
            pos = novo
            if d in ("outro", "idioma"):
                break
            continue
        m = _RE_DE_PRESENTE.match(fr.p, pos)
        if m:
            pos = m.end()
            continue
        break
    return pos, destino


def _ocasiao(p: str) -> str | None:
    for codigo, rx in _OCASIOES_RE:
        m = rx.search(p)
        if not m:
            continue
        if codigo == "aniversario_dela" and _RE_ANIVERSARIO_DE_OUTRO.search(p) and not re.search(
                r"\b(?:aniversario|niver)\s+dela\b", p):
            continue
        return codigo
    return None


def _reacao(p: str) -> str | None:
    if _RE_REACAO_NEUTRA.search(p):
        return "neutra"
    if _RE_REACAO_NEG.search(p):
        return "nao_gostou"
    if _RE_REACAO_POS.search(p):
        return "gostou"
    return None


def _sem_trecho(p: str, ini: int, fim: int) -> str:
    """O plano da frase sem o pedaço do objeto (a reação não sai do nome do presente: "bolsa adorável")."""
    return p[:ini] + " " * (fim - ini) + p[fim:]


def _data_do_presente(fr: _Frase, datas: list, referencia: date, planejado: bool) -> tuple[date | None, str | None]:
    """A data dita na frase (precisão de dia), ajustada ao tempo do verbo: presente dado "no natal"
    é o do ano passado; separado "pro natal" é o que vem. O Python calcula (regras/datas.py)."""
    for d in datas:
        inicio, fim = _campo(d, "inicio"), _campo(d, "fim")
        if inicio is None or fim is None or not (fr.ini <= inicio and fim <= fr.fim):
            continue
        valor = _campo(d, "data")
        if not isinstance(valor, date) or _valor(_campo(d, "precisao")) not in (None, "dia"):
            continue
        expressao = _campo(d, "expressao")
        if planejado:
            if valor >= referencia:
                return valor, expressao
            continue
        if valor <= referencia:
            return valor, expressao
        if _campo(d, "recorrente"):
            try:
                um_ano_antes = date(referencia.year - 1, referencia.month, referencia.day)
            except ValueError:
                um_ano_antes = date(referencia.year - 1, referencia.month, 28)
            for outra in extrair_datas(expressao or "", um_ano_antes):
                if outra.data is not None and outra.data <= referencia:
                    return outra.data, expressao
    return None, None


def _pessoa_antes(fr: _Frase, pos: int, pessoas: list) -> str | None:
    """Quem vem logo antes de `pos` (sujeito do "deu"): uma pessoa achada pelas regras."""
    melhor = None
    for pe in pessoas:
        fim = _campo(pe, "fim")
        ini = _campo(pe, "inicio")
        if ini is None or fim is None:
            continue
        rel_ini, rel_fim = ini - fr.ini, fim - fr.ini
        if 0 <= pos - rel_fim <= 3 and ini >= fr.ini:
            artigo = re.search(r"(?<![\w-])(?:a|o|as|os)\s+$", fr.p[:rel_ini])
            melhor = fr.texto[rel_ini:rel_fim]
            if artigo:                        # "A Mariana deu..." -> "a Mariana" (a tela diz "da Mariana")
                melhor = f"{artigo.group().strip().lower()} {melhor}"
    return melhor


def _sujeito_texto(fr: _Frase, pos: int) -> str:
    """O pedaço da oração antes de `pos` (para ver se o sujeito é ela, eu ou outra pessoa)."""
    inicio = max(fr.p.rfind(",", 0, pos), fr.p.rfind(";", 0, pos))
    pedaco = fr.p[inicio + 1:pos]
    partes = re.split(r"\s+(?:e|mas|que|quando|porque)\s+", pedaco)
    return partes[-1].strip()


def de_quem_texto(quem: str | None) -> str:
    """'a mãe dela' -> 'da mãe dela'; 'o chefe' -> 'do chefe'; 'meu pai' -> 'do meu pai';
    'mãe dela' -> 'da mãe dela'; 'Mariana' -> 'de Mariana'."""
    if not quem:
        return ""
    n = normalizar(quem)
    primeira = n.split()[0] if n.split() else ""
    contracao = {"a": "da", "o": "do", "as": "das", "os": "dos"}.get(primeira)
    if contracao and len(quem.split()) > 1:
        return f"{contracao} {quem.split(' ', 1)[1]}"
    if primeira in ("minha", "sua", "minhas", "suas"):
        return f"da {quem}" if primeira in ("minha", "sua") else f"das {quem}"
    if primeira in ("meu", "seu", "meus", "seus"):
        return f"do {quem}" if primeira in ("meu", "seu") else f"dos {quem}"
    base = re.sub(r"\s+(?:dela|dele)$", "", n)
    if base in RELACOES:
        genero = genero_relacao(RELACOES[base])
        if base == "pais":
            return f"dos {quem}"
        return f"{'da' if genero == 'f' else 'do' if genero == 'm' else 'de'} {quem}"
    return f"de {quem}"


def _resumo_presente(e: dict) -> str:
    desc = e["descricao"]
    if e.get("planejado"):
        base = f"Para dar a ela: {desc}"
    elif e["de_quem"] == "eu":
        base = f"Você deu a ela: {desc}"
    else:
        base = f"Ela ganhou{(' ' + de_quem_texto(e['quem'])) if e.get('quem') else ''}: {desc}"
    if e.get("ocasiao") and e["ocasiao"] not in ("sem_motivo", "outra"):
        base += f" ({OCASIOES[e['ocasiao']].lower()})"
    if e.get("reacao") and e["reacao"] != "nao_sei":
        base += f" — {REACOES[e['reacao']].lower()}"
    return base


def _presentes_da_frase(fr: _Frase, proxima: _Frase | None, texto: str, referencia: date, pessoas: list,
                        datas: list, nome_dela: str | None) -> list[dict]:
    """Presentes desta frase (a reação pode vir na frase seguinte, curta: "Dei um colar. Ela amou!")."""
    achados: list[dict] = []
    ocupado: list[tuple[int, int]] = []

    def livre(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in ocupado)

    def montar(ini_obj: int, fim_obj: int, de_quem: str, quem: str | None, planejado: bool, inicio_verbo: int) -> None:
        proibidas = _NAO_PRESENTE
        descricao = _objeto_valido(fr, ini_obj, fim_obj, proibidas)
        if descricao is None or not livre(ini_obj, fim_obj):
            return
        ocupado.append((min(inicio_verbo, ini_obj), fim_obj))
        resto = _sem_trecho(fr.p, ini_obj, fim_obj)
        planejado_final = planejado or bool(_RE_PLANO_MARCA.search(fr.p))
        data, expressao = _data_do_presente(fr, datas, referencia, planejado_final)
        if not planejado_final and data is None:
            futura, _ = _data_do_presente(fr, datas, referencia, True)
            if futura is not None and futura > referencia and de_quem == "eu" and re.search(
                    r"\b(?:comprei|encomendei|separei|guardei|escondi)\b", fr.p):
                planejado_final, data = True, futura
        reacao = None if planejado_final else _reacao(resto)
        trecho_fim = fr.fim
        usou_proxima = False
        if reacao is None and not planejado_final and proxima is not None:
            palavras = proxima.p.split()
            if len(palavras) <= 8 and _reacao(proxima.p) and not (_RE_EU_DEI.search(proxima.p)
                                                                   or _RE_ELA_GANHOU.search(proxima.p)):
                reacao = _reacao(proxima.p)
                trecho_fim = proxima.fim
                usou_proxima = True
        trecho = texto[fr.ini:trecho_fim]
        extra = {
            "tipo": "presente",
            "trecho": trecho,
            "descricao": descricao,
            "de_quem": de_quem,
            "quem": quem,
            "reacao": reacao or "nao_sei",
            "ocasiao": _ocasiao(fr.p),
            "data": data,
            "data_expressao": expressao,
            "planejado": planejado_final,
            "certeza": classificar_certeza(trecho).value,
            "_usou_proxima": usou_proxima,
        }
        extra["resumo"] = _resumo_presente(extra)
        achados.append(extra)

    p = fr.p
    # 1) Eu pensando em dar / já separado: "vou dar um perfume pra ela no natal".
    for m in _RE_EU_VOU.finditer(p):
        if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        pos, destino = _antes_do_objeto(fr, m.end(), nome_dela)
        if destino in ("outro", "idioma"):
            continue
        objs = _objetos(fr, pos)
        if not objs:
            continue
        destino_depois, _ = _destino(fr, objs[-1][1], nome_dela)
        if destino_depois in ("outro", "idioma"):
            continue
        if destino is None and destino_depois is None and not (_RE_MARCA_PRESENTE.search(p) or _ocasiao(p)):
            continue
        for obj in objs:
            montar(*obj, "eu", None, True, m.start())

    # 2) Eu dei: "dei um colar pra ela", "dei pra ela um colar", "dei de presente um livro".
    for m in _RE_EU_DEI.finditer(p):
        if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        com = _RE_PRESENTEEI_COM.match(p, m.end()) if m.group("v").startswith("presente") else None
        if com is not None:
            # "Presenteei ela com um livro": quem recebe vem antes, o presente depois do "com".
            quem_recebe = com.group("quem")
            palavra = re.match(r"[\wÀ-ÿ'-]+", fr.texto[com.start("quem"):]) if quem_recebe else None
            e_ela = bool(_ELA_DESTINO.fullmatch(quem_recebe) or (palavra and nome_dela and e_nome_dela(
                palavra.group()[:1].upper() + palavra.group()[1:], nome_dela)))
            if not e_ela:
                continue
            for obj in _objetos(fr, com.end()):
                montar(*obj, "eu", None, False, m.start())
            continue
        pos, destino = _antes_do_objeto(fr, m.end(), nome_dela)
        if destino in ("outro", "idioma"):
            continue
        objs = _objetos(fr, pos)
        if not objs:
            continue
        destino_depois, _ = _destino(fr, objs[-1][1], nome_dela)
        if destino_depois in ("outro", "idioma"):
            continue
        for obj in objs:
            montar(*obj, "eu", None, False, m.start())

    # 3) Eu comprei/trouxe/fiz... só com ela como destino ou com marca de presente.
    for m in _RE_EU_OUTROS.finditer(p):
        if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        sujeito = _sujeito_texto(fr, m.start())
        if re.search(r"\bela\b", sujeito) or _pessoa_antes(fr, m.start(), pessoas):
            continue                          # "ela trouxe", "a Mariana trouxe": não sou eu
        pos, destino = _antes_do_objeto(fr, m.end(), nome_dela)
        if destino in ("outro", "idioma"):
            continue
        objs = _objetos(fr, pos)
        if not objs:
            continue
        destino_depois, _ = _destino(fr, objs[-1][1], nome_dela)
        if destino_depois in ("outro", "idioma"):
            continue
        guardar = m.group("v") in ("separei", "guardei", "escondi", "encomendei")
        marca = bool(_RE_MARCA_PRESENTE.search(p) or _ocasiao(p) or _RE_PLANO_MARCA.search(p))
        if destino is None and destino_depois is None and not marca:
            continue
        for obj in objs:
            montar(*obj, "eu", None, guardar, m.start())

    # 4) Outra pessoa deu para ela: "a mãe dela deu um vestido pra ela", "a Mariana deu um caderno pra ela".
    for m in _RE_OUTRO_DEU.finditer(p):
        if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        sujeito = _sujeito_texto(fr, m.start())
        quem = _pessoa_antes(fr, m.start(), pessoas)
        if not quem and re.search(r"\b(?:ela|ele|voce|eu)\s*$", sujeito):
            continue                          # "ela deu pra mãe", "eu trouxe" (esse é o passo 3)
        if re.search(r"\b(?:me|mim)\s*$", sujeito) or re.match(r"\s+(?:me|pra\s+mim|para\s+mim)\b", p[m.end():]):
            continue                          # "ela me deu": quem ganhou fui eu
        if m.group("v") == "trouxe" and not quem:
            continue                          # "trouxe" sem sujeito é o "eu trouxe" do passo 3
        de_quem = "eu" if re.search(r"\ba\s+gente\s*$", sujeito) else "outro"
        pos, destino = _antes_do_objeto(fr, m.end(), nome_dela)
        if destino in ("outro", "idioma"):
            continue
        obj = _objeto(fr, pos)
        if obj is None:
            continue
        destino_depois, _ = _destino(fr, obj[1], nome_dela)
        if "ela" not in (destino, destino_depois):
            continue                          # "deu um bolo" sem "pra ela": não dá para saber
        if de_quem == "outro" and not quem:
            palavras_sujeito = sujeito.split()
            if 0 < len(palavras_sujeito) <= 4:
                inicio_sujeito = fr.p.rfind(sujeito, 0, m.start())
                if inicio_sujeito >= 0:
                    quem = fr.texto[inicio_sujeito:inicio_sujeito + len(sujeito)].strip(" ,")
                    quem = re.sub(r"^(?:(?:hoje|ontem|anteontem|ai|dai|entao|e|no|na)\s+)+", "", quem,
                                  flags=re.I) or None
        montar(*obj, de_quem, quem if de_quem == "outro" else None, False, m.start())

    # 5) Ela ganhou/recebeu: "ela ganhou um perfume da mãe dela", "ganhou de mim um livro".
    for m in _RE_ELA_GANHOU.finditer(p):
        if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        sujeito = _sujeito_texto(fr, m.start())
        palavras_sujeito = re.findall(r"[\w-]+", sujeito)
        ultima_original = re.findall(r"[\wÀ-ÿ'-]+", fr.texto[:m.start()])[-1:] if palavras_sujeito else []
        e_ela = (not palavras_sujeito                                       # "Ganhou um perfume da mãe."
                 or bool(re.search(r"\b(?:ela|gata|namorada|noiva|esposa|mozao)\s*(?:ja|tambem|tb|ate)?\s*$", sujeito))
                 or bool(nome_dela and ultima_original and e_nome_dela(ultima_original[0], nome_dela)))
        if not e_ela or _pessoa_antes(fr, m.start(), pessoas):
            continue
        if re.search(r"\b(?:sorteio|rifa|concurso|competicao|jogo|aposta)\b", p):
            continue
        pos = m.end()
        de_quem, quem = "outro", None
        for _ in range(2):                    # "ganhou de presente da mãe dela uma bolsa", em qualquer ordem
            md = _RE_DE_MIM.match(p[pos:])
            dd = None if md else _RE_DOADOR.match(p[pos:])
            mp = _RE_DE_PRESENTE.match(p, pos)
            if md:
                de_quem, pos = "eu", pos + md.end()
            elif dd and normalizar(dd.group("q")) != "mim":
                quem = _com_artigo(fr.texto[pos + dd.start("q"):pos + dd.end("q")], dd.group("prep"))
                pos += dd.end()
            elif mp:
                pos = mp.end()
            else:
                break
        obj = _objeto(fr, pos)
        if obj is None:
            continue
        ini_obj, fim_obj = obj
        if quem is None and de_quem == "outro":
            # O doador depois do objeto: "um perfume da mãe dela", "um vestido da Mariana", "um livro de mim".
            corte = None
            for pe in pessoas:
                pi, pf = _campo(pe, "inicio"), _campo(pe, "fim")
                if pi is None or pf is None:
                    continue
                ri, rf = pi - fr.ini, pf - fr.ini
                if ini_obj < ri <= fim_obj + 12:
                    mm = re.search(r"\s+(?P<prep>de|da|do|das|dos)\s+(?:(?:minha|meu|sua|seu|a|o)\s+)?$", p[ini_obj:ri])
                    if mm and mm.start() > 0:
                        corte = (ini_obj + mm.start(), rf, mm.group("prep"))
                        break
            if corte is None:
                dd = _RE_DOADOR_DEPOIS.search(p, ini_obj, min(len(p), fim_obj + 40))
                if dd and ini_obj < dd.start() <= fim_obj:
                    corte = (dd.start(), dd.end(), dd.group("prep"))
            # (Um nome com maiúscula logo depois, "um perfume da Natura", pode ser a marca: só a pessoa
            # que as regras reconhecem, acima, vira quem deu.)
            if corte is None:
                # Depois do objeto: "um livro de presente da Mariana", "... de presente da mãe dela".
                dp = _RE_DE_PRESENTE_DE.match(fr.texto, fim_obj) or _RE_DE_PRESENTE_DE.match(p, fim_obj)
                if dp:
                    corte = (dp.start("prep"), dp.end(), dp.group("prep").lower())
                    corte_depois = True
                else:
                    corte_depois = False
            else:
                corte_depois = False
            if corte is not None:
                q = re.sub(r"^\s*(?:de|da|do|das|dos)\s+", "", fr.texto[corte[0]:corte[1]], flags=re.I).strip()
                if normalizar(q) == "mim":
                    de_quem, quem = "eu", None
                else:
                    quem = _com_artigo(q, corte[2])
                if not corte_depois:
                    fim_obj = min(fim_obj, corte[0])
        # "flores minhas", "um livro meu": quem deu fui eu.
        meu = re.search(r"\s+(?:minhas?|meus?)\s*$", p[ini_obj:fim_obj])
        if meu and de_quem == "outro" and quem is None:
            de_quem, fim_obj = "eu", ini_obj + meu.start()
        montar(ini_obj, fim_obj, de_quem, quem if de_quem == "outro" else None, False, m.start())

    # 6) "Meu presente pra ela foi um kit de skincare" / "o presente que eu dei foi..." / "vai ser...".
    for m in _RE_MEU_PRESENTE.finditer(p):
        if fr.negado(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        obj = _objeto(fr, m.end())
        if obj is not None:
            montar(*obj, "eu", None, bool(m.group("futuro")), m.start())

    # 7) Reação a um presente meu, sem "dei" antes: "ela amou o livro que eu dei".
    m = re.search(r"\b(?:o|a|os|as|do|da|dos|das)\s+(?P<obj>[\w\s-]{2,60}?)\s+que\s+(?:eu\s+)?(?:(?:tinha|havia)\s+)?"
                  r"(?:dei|comprei|trouxe|fiz|mandei|dei\s+pra\s+ela|dei\s+a\s+ela)\b", p)
    if m and not fr.negado(m.start("obj"), 1) and _reacao(_sem_trecho(p, m.start("obj"), m.end("obj"))):
        montar(m.start("obj"), m.end("obj"), "eu", None, False, m.start())

    for e in achados:
        e.pop("_usou_proxima", None)
    return achados


def _desejos_da_frase(fr: _Frase, texto: str, pessoas: list, itens: list, nome_dela: str | None) -> list[dict]:
    """Desejos de objeto que as regras do núcleo não pegam (e só os dela)."""
    achados: list[dict] = []
    p = fr.p
    # O núcleo já guardou um DESEJO (Presentes) desta frase? Então o item dele vale.
    do_nucleo = [i for i in itens if _valor(_campo(i, "tipo")) == TipoItem.DESEJO.value
                 and "Presentes" in (_campo(i, "categorias") or [])
                 and (_campo(i, "trecho") or "") and ((_campo(i, "trecho") in fr.texto) or (fr.texto in _campo(i, "trecho")))]

    def sujeito_e_ela(pos: int) -> bool:
        sujeito = _sujeito_texto(fr, pos)
        if _pessoa_antes(fr, pos, pessoas):
            return False
        if re.search(r"\b(?:eu|ele|voce|a\s+gente|minha\s+mae|meu\s+pai)\b", sujeito) and not _RE_ELA_SUJEITO.search(sujeito):
            return False
        return True

    def montar(ini_obj: int, fim_obj: int, pista: str, certeza_max: Certeza | None) -> None:
        descricao = _objeto_valido(fr, ini_obj, fim_obj, _NAO_DESEJO)
        if descricao is None:
            return
        if any(mesma_coisa(descricao, _campo(i, "alvo") or _campo(i, "resumo")) for i in do_nucleo):
            return
        if any(mesma_coisa(descricao, a["descricao"]) for a in achados):
            return
        certeza = classificar_certeza(fr.texto)
        if certeza_max is not None and _ORDEM_CERTEZA[certeza.value] > _ORDEM_CERTEZA[certeza_max.value]:
            certeza = certeza_max
        achados.append({"tipo": "presente_desejo", "trecho": fr.texto, "descricao": descricao, "pista": pista,
                        "certeza": certeza.value, "resumo": f"Ela gostaria de ganhar: {descricao}"})

    for pista, rx, certeza_max in _DESEJOS:
        for m in rx.finditer(p):
            if fr.negado(m.start()) or fr.condicional(m.start()) or not fr.afirmado(m.start(), m.end()):
                continue
            if not sujeito_e_ela(m.start()):
                continue
            if pista == "pediu" and not re.search(r"\bde\s+presente\b", p):
                continue
            obj = _objeto(fr, m.end())
            if obj is None:
                continue
            montar(*obj, pista, certeza_max)
    for rx, pista in ((_RE_LISTA_DESEJOS, "lista"), (_RE_SERIA_PRESENTE, "seria_presente")):
        for m in rx.finditer(p):
            if fr.negado(m.start()) or not fr.afirmado(m.start(), m.end()) or not sujeito_e_ela(m.start()):
                continue
            montar(m.start("obj"), m.end("obj"), pista, None)
    for m in _RE_LISTA_DESEJOS_TEM.finditer(p):
        if fr.negado(m.start()) or not fr.afirmado(m.start(), m.end()):
            continue
        for obj in _objetos(fr, m.end()):
            montar(*obj, "lista", None)

    return achados


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Presentes e desejos de presente no texto (cada um com o `trecho` literal de onde saiu).

    Nada aqui é gravado: a revisão mostra e o usuário confirma. Frase com negação ("não dei nada"),
    condição ("se eu der"), pergunta, ironia ("sqn") ou fala negada não vira presente nem desejo."""
    nome_dela = _campo(conhecimento, "nome_dela") if conhecimento is not None else None
    lista = []
    for ini, fim, frase in frases(texto):
        p = plano(frase)
        lista.append(_Frase(frase, p, ini, fim, trechos_nao_afirmados(frase)))
    saida: list[dict] = []
    for n, fr in enumerate(lista):
        if fr.texto.rstrip().endswith("?") or _RE_IRONIA.search(fr.p):
            continue
        proxima = lista[n + 1] if n + 1 < len(lista) else None
        if proxima is not None and (proxima.texto.rstrip().endswith("?") or _RE_IRONIA.search(proxima.p)):
            proxima = None
        desta = _presentes_da_frase(fr, proxima, texto, referencia, list(pessoas or []), list(datas or []), nome_dela)
        desejos = _desejos_da_frase(fr, texto, list(pessoas or []), list(itens or []), nome_dela)
        # "Dei um kindle pra ela, ela tava doida por um kindle": o desejo já foi realizado ali mesmo.
        saida += desta + [d for d in desejos if not any(mesma_coisa(d["descricao"], p["descricao"]) for p in desta)]
    return saida[:50]


# ======================================================================== gravar (o que foi confirmado)

def _texto_curto(v: Any, maximo: int, erro: str, obrigatorio: bool = True) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if obrigatorio:
            raise ValueError(erro)
        return None
    if not isinstance(v, str):
        raise ValueError(erro)
    v = re.sub(r"\s+", " ", v).strip()
    if len(v) > maximo:
        raise ValueError(erro)
    return v


def _data_campo(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError(ERRO_DATA)
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError(ERRO_DATA) from None


def _certeza_do_trecho(pedida: Any, trecho: str) -> str:
    """A certeza nunca sobe acima do que o trecho permite (princípio 3)."""
    permitida = classificar_certeza(trecho).value
    if permitida not in _ORDEM_CERTEZA:
        permitida = "observado"
    if not isinstance(pedida, str) or pedida not in _ORDEM_CERTEZA:
        return permitida
    return pedida if _ORDEM_CERTEZA[pedida] <= _ORDEM_CERTEZA[permitida] else permitida


def _ocasiao_campo(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or v not in OCASIOES:
        raise ValueError(ERRO_OCASIAO)
    return v


def _reacao_campo(v: Any) -> str:
    if v in (None, ""):
        return "nao_sei"
    if not isinstance(v, str) or v not in REACOES:
        raise ValueError(ERRO_REACAO)
    return v


def _linha_presente(e: dict, registro_id: int, criado: date) -> dict:
    de_quem = e.get("de_quem") or "eu"
    if de_quem not in DE_QUEM:
        raise ValueError(ERRO_DE_QUEM)
    planejado = e.get("planejado", False)
    if not isinstance(planejado, bool):
        raise ValueError("“planejado” precisa ser verdadeiro ou falso.")
    return {
        "descricao": _texto_curto(e.get("descricao"), MAX_DESCRICAO, ERRO_DESCRICAO),
        "data": _data_campo(e.get("data")),
        "ocasiao": _ocasiao_campo(e.get("ocasiao")),
        "de_quem": de_quem,
        "quem": _texto_curto(e.get("quem"), MAX_QUEM, ERRO_QUEM, obrigatorio=False) if de_quem == "outro" else None,
        "reacao": "nao_sei" if planejado else _reacao_campo(e.get("reacao")),
        "planejado": planejado and de_quem == "eu",
        "registro_id": registro_id,
        "criado_em": criado,
        "trecho": e["trecho"],
        "certeza": _certeza_do_trecho(e.get("certeza"), e["trecho"]),
    }


def _linha_desejo(e: dict, registro_id: int, criado: date) -> dict:
    pista = e.get("pista")
    return {
        "descricao": _texto_curto(e.get("descricao"), MAX_DESCRICAO, ERRO_DESCRICAO),
        "trecho": e["trecho"],
        "certeza": _certeza_do_trecho(e.get("certeza"), e["trecho"]),
        "pista": pista if isinstance(pista, str) and re.fullmatch(r"[a-z_]{1,30}", pista) else None,
        "registro_id": registro_id,
        "criado_em": criado,
    }


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava os presentes e desejos que o usuário confirmou (o servidor já conferiu tipo e trecho;
    aqui confere o resto e a certeza nunca sobe). ValueError = 400 e nada é gravado."""
    linhas = []
    for e in extras:
        if e.get("tipo") == "presente":
            linhas.append(("presentes", _linha_presente(e, registro_id, referencia)))
        elif e.get("tipo") == "presente_desejo":
            linhas.append(("presentes_desejos", _linha_desejo(e, registro_id, referencia)))
    for tabela, valores in linhas:
        banco.inserir(tabela, valores)


# ======================================================================== leitura (a tela e o Hoje)

@dataclass
class _Base:
    """O que a tela e o Hoje consultam, lido uma vez só."""

    d: alertas.Dados
    presentes: list[dict]
    desejos: list[dict]
    ocultos: list[dict]

    @property
    def ref(self) -> date:
        return self.d.referencia

    def data_registro(self, rid: int | None) -> date | None:
        r = self.d.registros.get(rid) if rid is not None else None
        return r["data_registro"] if r else None


def _carregar(banco: Any, referencia: date) -> _Base:
    return _Base(
        d=alertas.carregar(banco, referencia),
        presentes=banco.consultar("SELECT * FROM presentes ORDER BY id"),
        desejos=banco.consultar("SELECT * FROM presentes_desejos ORDER BY id"),
        ocultos=banco.consultar("SELECT * FROM presentes_ocultos ORDER BY id"),
    )


def _citacao(b: _Base, rid: int | None, trecho: str | None, certeza: str | None) -> dict:
    dr = b.data_registro(rid)
    return {"registro_id": rid, "trecho": trecho, "certeza": certeza or "observado", "data_registro": dr,
            "quando": _quando(dr, b.ref)}


def _presente(b: _Base, linha: dict) -> dict:
    dr = b.data_registro(linha["registro_id"])
    data = _data(linha["data"])
    base = data or dr
    return {
        "id": linha["id"],
        "descricao": linha["descricao"],
        "data": data,
        "data_registro": dr,
        "quando": _quando(base, b.ref),
        "ocasiao": linha["ocasiao"],
        "ocasiao_rotulo": OCASIOES.get(linha["ocasiao"] or ""),
        "de_quem": linha["de_quem"],
        "quem": linha["quem"],
        "quem_texto": de_quem_texto(linha["quem"]) if linha["quem"] else None,     # "da mãe dela"
        "reacao": linha["reacao"],
        "reacao_rotulo": REACOES.get(linha["reacao"], REACOES["nao_sei"]),
        "planejado": bool(linha["planejado"]),
        "trecho": linha["trecho"],
        "certeza": linha["certeza"],
        "registro_id": linha["registro_id"],
        "ligado_a_desejo": linha["desejo_item_id"] is not None or linha["desejo_id"] is not None,
        "_base": base,
    }


def _sem_internos(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _sem_internos(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [_sem_internos(v) for v in x]
    return x


# ---------------------------------------------------------------- desejos (o que ela comentou)

@dataclass
class _Membro:
    origem: str               # "item" (DESEJO do núcleo) | "desejo" (deste recurso)
    id: int
    descricao: str
    trecho: str
    certeza: str
    registro_id: int | None
    data: date | None

    @property
    def ref(self) -> str:
        return f"{self.origem}:{self.id}"


def _membros_desejo(b: _Base) -> list[_Membro]:
    membros = []
    for i in b.d.ativos:
        if i.tipo != TipoItem.DESEJO or b.d.dono(i) is not None or (i.polaridade or 1) < 0:
            continue
        if "Presentes" not in (i.categorias or []):
            continue
        descricao = _limpar_objeto(i.alvo or i.resumo)
        if not descricao:
            continue
        membros.append(_Membro("item", i.id, descricao, i.trecho, Certeza(i.certeza).value, i.registro_id,
                               b.d.data_registro(i)))
    for l in b.desejos:
        membros.append(_Membro("desejo", l["id"], l["descricao"], l["trecho"], l["certeza"], l["registro_id"],
                               b.data_registro(l["registro_id"])))
    return membros


def _grupos_de_desejo(b: _Base) -> list[list[_Membro]]:
    """Os desejos agrupados pela coisa desejada ("fone novo" em março e em maio: um só)."""
    grupos: list[list[_Membro]] = []
    for m in sorted(_membros_desejo(b), key=lambda x: (x.data or date.min, x.id), reverse=True):
        for g in grupos:
            if mesma_coisa(g[0].descricao, m.descricao):
                g.append(m)
                break
        else:
            grupos.append([m])
    return grupos


def _situacao_dos_membros(b: _Base) -> tuple[dict[str, int], dict[str, int]]:
    """({ref: id do presente que o realizou}, {ref: id da linha que o escondeu})."""
    presentes = {l["id"] for l in b.presentes}
    dados: dict[str, int] = {}
    for l in b.presentes:
        if l["desejo_item_id"] is not None:
            dados[f"item:{l['desejo_item_id']}"] = l["id"]
        if l["desejo_id"] is not None:
            dados[f"desejo:{l['desejo_id']}"] = l["id"]
    escondidos: dict[str, int] = {}
    for o in b.ocultos:
        if o["tipo"] == "desejo_dado" and o["presente_id"] in presentes:
            dados.setdefault(o["ref"], o["presente_id"])
        elif o["tipo"] == "desejo":
            escondidos[o["ref"]] = o["id"]
    return dados, escondidos


def _desejos_abertos(b: _Base) -> list[dict]:
    dados, escondidos = _situacao_dos_membros(b)
    saida = []
    for grupo in _grupos_de_desejo(b):
        abertos = [m for m in grupo if m.ref not in dados and m.ref not in escondidos]
        if not abertos:
            continue
        rep = abertos[0]
        primeiro = min((m.data for m in abertos if m.data), default=None)
        entrada = {
            "origem": rep.origem,
            "id": rep.id,
            "chave": chave(rep.descricao),
            "descricao": rep.descricao,
            "trecho": rep.trecho,
            "certeza": max((m.certeza for m in abertos), key=lambda c: _ORDEM_CERTEZA.get(c, 0)),
            "registro_id": rep.registro_id,
            "data_registro": rep.data,
            "quando": _quando(rep.data, b.ref),
            "vezes": len({m.registro_id for m in abertos}),
            "origens": [_citacao(b, m.registro_id, m.trecho, m.certeza) for m in abertos[:3]],
            "talvez_dado": None,
            "ja_separado": None,
            "_primeiro": primeiro,
        }
        # Talvez já dado: um presente (seu ou de outra pessoa) parecido, depois do desejo.
        for linha in sorted(b.presentes, key=lambda l: l["id"], reverse=True):
            if linha["desejo_item_id"] is not None or linha["desejo_id"] is not None:
                continue
            if not mesma_coisa(rep.descricao, linha["descricao"]):
                continue
            p = _presente(b, linha)
            if p["planejado"]:
                if entrada["ja_separado"] is None:
                    entrada["ja_separado"] = _sem_internos(p)
                continue
            if primeiro is not None and p["_base"] is not None and p["_base"] < primeiro:
                continue
            if entrada["talvez_dado"] is None:
                entrada["talvez_dado"] = _sem_internos(p)
        saida.append(entrada)
    saida.sort(key=lambda e: (e["data_registro"] or date.min, e["id"]), reverse=True)
    return saida


# ---------------------------------------------------------------- ideias possíveis (🔵)

def _chave_alvo(item: Item) -> str:
    return chave(item.alvo or item.resumo)


def _alvo_util(texto: str | None) -> bool:
    """Alvo que diz alguma coisa ("muito", "isso" não dizem)."""
    return bool(texto) and bool(termos(texto))


def _texto_alvo(item: Item) -> str:
    return _limpar_objeto(re.sub(r"^(?:de|do|da|dos|das)\s+", "", (item.alvo or item.resumo or "").strip(), flags=re.I))


# Grupos de coisas: "não gosta de flores de presente" vale para girassóis e rosas; "não bebe mais
# vinho" e "não bebe álcool" valem para "vinho tinto" (revisão de 07/10). Pelo começo das palavras.
_GRUPOS_DE_COISAS: dict[str, tuple[str, ...]] = {
    "flor": ("flor", "flores", "girass", "rosa", "rosas", "tulip", "orquid", "lirio", "margarid", "buque", "ramalhete",
             "astromel", "peonia", "lavanda", "hortensi", "cravo", "gerbera", "suculenta"),
    "alcool": ("alcool", "alcoolic", "bebida alcoolica", "vinho", "cerveja", "cachaca", "whisky", "uisque", "vodka",
               "gin", "espumante", "champanhe", "champagne", "drink", "drinque", "licor", "tequila", "caipirinha",
               "rum", "chopp", "chope"),
}
_PALAVRA_DO_GRUPO = {"flor": ("flor", "flores", "buque", "buques"), "alcool": ("alcool", "alcoolica", "alcoolicas",
                                                                            "alcoolico", "bebida alcoolica")}


_RE_FALA_DE_PRESENTE = re.compile(r"(?<![\w])(?:presente\w*|presenteei|presenteou|ganhar|ganhou|ganhei|dar|dei|deu"
                                  r"|receber|recebeu|mimo|lembrancinha)(?![\w])")


def _membro(palavras: list[str], grupo: str) -> bool:

    return any(p.startswith(r) for p in palavras for r in _GRUPOS_DE_COISAS[grupo])


def _contradito(alvo: str, negativos: list[str]) -> bool:
    """A ideia bate com algo que ela não quer: a mesma coisa ("vinho" e "vinho tinto"), ou o grupo
    inteiro dito ("não gosta de flores" e "girassóis")."""
    a = normalizar(alvo)
    pa = a.split()
    for neg in negativos:
        n = normalizar(neg)
        if not n:
            continue
        if mesma_coisa(alvo, neg) or re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", a):
            return True
        pn = n.split()
        for grupo, nomes in _PALAVRA_DO_GRUPO.items():
            # O que ela não quer nomeia o grupo ("flores de presente", "álcool") e a ideia é dele.
            if any(p in nomes for p in pn) and _membro(pa, grupo):
                return True
    return False


def _frase_ideia(alvo: str) -> str:
    primeira = normalizar(alvo.split()[0]) if alvo.split() else ""
    if re.fullmatch(r"[a-z]+(?:ar|er|ir)", primeira) and primeira not in ("bar", "colar", "par", "lugar", "mar"):
        return f"Talvez algo para {alvo}"
    return f"Talvez algo com {alvo}"


_ALVO_RUIM = re.compile(r"^(?:eu|que|quando|se|ele|ela|a\s+gente|voce|nos|mim|isso|quem)\b")


def _ideias_e_recusas(b: _Base, momento: dict | None) -> tuple[list[dict], list[dict]]:
    d = b.d
    ref = b.ref
    datas = {i.id: d.data_registro(i) for i in d.itens}
    grupos: dict[str, list[Item]] = defaultdict(list)
    for i in d.ativos:
        if i.tipo in (TipoItem.PREFERENCIA, TipoItem.RESTRICAO) and d.dono(i) is None:
            grupos[_chave_alvo(i)].append(i)
    escondidas = {o["ref"] for o in b.ocultos if o["tipo"] == "ideia"}
    presentes = [_presente(b, l) for l in b.presentes]
    nao_quer = [i for i in d.ativos if i.tipo == TipoItem.DESEJO and d.dono(i) is None and (i.polaridade or 1) < 0]
    restricoes = [i for i in d.ativos if i.tipo == TipoItem.RESTRICAO and d.dono(i) is None]
    # Tudo o que ela disse que não gosta, não quer (de presente) ou não pode: a ideia não contradiz isso.
    negativos_todos = ([_texto_alvo(i) for i in d.ativos if i.tipo == TipoItem.PREFERENCIA and d.dono(i) is None
                        and (i.polaridade or 1) < 0]
                       + [_limpar_objeto(i.alvo or "") for i in nao_quer] + [_texto_alvo(i) for i in restricoes])
    apoios = [a for a in (momento or {}).get("apoios", [])]

    ideias: list[dict] = []
    recusas: list[dict] = []
    for chave_, grupo in grupos.items():
        if not chave_:
            continue
        positivos = [i for i in grupo if i.tipo == TipoItem.PREFERENCIA and (i.polaridade or 1) > 0]
        negativos = [i for i in grupo if i.tipo == TipoItem.PREFERENCIA and (i.polaridade or 1) < 0]
        cats = {c for i in grupo for c in (i.categorias or [])}
        forca = forca_preferencia(grupo, ref, datas)
        if forca == "nao_gosta" and negativos and not [i for i in grupo if i.tipo == TipoItem.RESTRICAO] \
                and cats & PRESENTEAVEIS:
            rep = max(negativos, key=lambda i: (_ORDEM_CERTEZA.get(Certeza(i.certeza).value, 0), datas.get(i.id) or date.min))
            if _alvo_util(_texto_alvo(rep)):
                recusas.append({"tipo": "nao_gosta", "texto": _texto_alvo(rep), "motivo": "Ela não gosta",
                                **_citacao(b, rep.registro_id, rep.trecho, Certeza(rep.certeza).value)})
        if not positivos:
            continue
        hobby = "Hobbies" in cats
        if forca == "gosta":
            pass
        elif forca == "comentou_uma_vez" and hobby and any(Certeza(i.certeza) != Certeza.INFERENCIA for i in positivos):
            pass
        else:
            continue
        rep = max(positivos, key=lambda i: (_ORDEM_CERTEZA.get(Certeza(i.certeza).value, 0),
                                             datas.get(i.id) or date.min, i.id or 0))
        alvo = _texto_alvo(rep)
        if not _alvo_util(alvo) or _ALVO_RUIM.match(normalizar(alvo)) or len(alvo.split()) > 6:
            continue
        # "Ela ama Harry Potter": um nome próprio (obra, artista, marca) de que ela gosta com força vale
        # como pista de presente mesmo sem categoria de presente ("acordar cedo" não vale).
        nome_proprio = forca == "gosta" and alvo[:1].isupper() and not cats & PRESENTEAVEIS
        if not cats & PRESENTEAVEIS and not nome_proprio:
            continue
        if any(f"item:{i.id}" in escondidas for i in positivos):
            continue
        if any(mesma_coisa(alvo, _limpar_objeto(i.alvo or "")) for i in nao_quer):
            continue
        if _contradito(alvo, negativos_todos):
            continue
        # Presente parecido que ela não curtiu, ou que você deu há pouco: vai para "não recomendados".
        ruim = next((p for p in presentes if not p["planejado"] and p["reacao"] == "nao_gostou"
                     and mesma_coisa(alvo, p["descricao"])), None)
        if ruim is not None:
            continue
        recente = next((p for p in presentes if not p["planejado"] and p["de_quem"] == "eu" and p["_base"]
                        and _somar_meses(p["_base"], MESES_JA_DADO) >= ref and mesma_coisa(alvo, p["descricao"])), None)
        if recente is not None:
            continue
        pontos = 0
        motivos: list[str] = []
        confirmados = [i for i in positivos if Certeza(i.certeza) == Certeza.CONFIRMADO]
        if confirmados:
            pontos += 3
            motivos.append("ela mesma disse que gosta 🟢")
        registros = {i.registro_id for i in positivos if i.registro_id is not None}
        if len(registros) >= 2:
            pontos += min(3, len(registros))
            motivos.append(f"apareceu em {len(registros)} registros")
        ultima = max((datas.get(i.id) for i in positivos if datas.get(i.id)), default=None)
        if ultima is not None:
            dias = (ref - ultima).days
            if dias <= 30:
                pontos += 2
            elif dias <= 180:
                pontos += 1
            motivos.append(f"última menção {tempo_atras(max(0, dias))}")
        # "Já apareceu ligado a presentes" só quando um registro liga isso a presente; a categoria vem
        # do dicionário de palavras e não diz nada sobre ela (revisão de 07/10: era uma afirmação inventada).
        if any(_RE_FALA_DE_PRESENTE.search(normalizar(i.trecho or "")) for i in positivos):
            pontos += 2
            motivos.append("já apareceu ligado a presentes")
        elif "Presentes" in cats:
            pontos += 1
            motivos.append("costuma ser um bom presente")
        if "Coisas que deixam feliz" in cats:
            pontos += 1
            motivos.append("está entre as coisas que a deixam feliz")
        if hobby:
            pontos += 1
            motivos.append("é um hobby dela")
        if nome_proprio:
            motivos.append("é um nome (obra, artista ou marca) de que ela gosta")
        gostou = next((p for p in presentes if p["reacao"] == "gostou" and mesma_coisa(alvo, p["descricao"])), None)
        if gostou is not None:
            pontos += 2
            motivos.append(f"ela gostou quando ganhou {gostou['descricao']}"
                           + (f" ({_dia(gostou['_base'], ref)})" if gostou["_base"] else ""))
        if apoios and any(mesma_coisa(alvo, a.get("alvo") or "") for a in apoios):
            pontos += 2
            motivos.append("combina com o que ajuda quando ela não está bem")
        avisos = []
        if cats & {"Comidas", "Bebidas"}:
            for r in restricoes[:2]:
                dr = d.data_registro(r)
                avisos.append(f"Atenção ao que você registrou{(' em ' + _dia(dr, ref)) if dr else ''}: “{r.trecho}”")
        antigos = [p for p in presentes if not p["planejado"] and p["de_quem"] == "eu" and mesma_coisa(alvo, p["descricao"])]
        if antigos:
            avisos.append(f"Você já deu algo assim {antigos[0]['quando'] or 'antes'}.")
        de_outros = [p for p in presentes if not p["planejado"] and p["de_quem"] == "outro" and p["_base"]
                     and _somar_meses(p["_base"], MESES_JA_GANHOU) >= ref and mesma_coisa(alvo, p["descricao"])]
        if de_outros:
            q = de_outros[0]
            avisos.append(f"Ela ganhou algo assim {q['quando'] or 'há pouco'}"
                          + (f" {de_quem_texto(q['quem'])}" if q["quem"] else "") + ".")
        origens = sorted(positivos, key=lambda i: (datas.get(i.id) or date.min, i.id or 0), reverse=True)[:3]
        ideias.append({
            "chave": chave_,
            "item_id": rep.id,
            "item_ids": [i.id for i in positivos],
            "texto": alvo,
            "frase": _frase_ideia(alvo),
            "categoria": next((c for c in (rep.categorias or []) if c in PRESENTEAVEIS and c != "Coisas que deixam feliz"),
                              next(iter(sorted(cats & PRESENTEAVEIS)), "Preferências")),
            "certeza": Certeza.INFERENCIA.value,
            "pontos": pontos,
            "motivos": motivos,
            "avisos": avisos,
            "origens": [_citacao(b, i.registro_id, i.trecho, Certeza(i.certeza).value) for i in origens],
            "registro_ids": sorted(registros),
        })
    ideias.sort(key=lambda x: (-x["pontos"], x["texto"].lower()))
    return ideias[:MAX_IDEIAS], recusas


def _nao_recomendados(b: _Base, recusas: list[dict]) -> list[dict]:
    d, ref = b.d, b.ref
    saida = list(recusas)
    for i in d.ativos:
        if i.tipo == TipoItem.RESTRICAO and d.dono(i) is None:
            saida.append({"tipo": "restricao", "texto": i.resumo, "motivo": "Restrição",
                          **_citacao(b, i.registro_id, i.trecho, Certeza(i.certeza).value)})
        elif i.tipo == TipoItem.DESEJO and d.dono(i) is None and (i.polaridade or 1) < 0 and re.search(
                r"\b(?:ganhar|presente|receber|dar|darem)\b", normalizar(i.trecho)) \
                and _alvo_util(_limpar_objeto(i.alvo or i.resumo)):
            saida.append({"tipo": "nao_quer", "texto": _limpar_objeto(i.alvo or i.resumo), "motivo": "Ela disse que não quer",
                          **_citacao(b, i.registro_id, i.trecho, Certeza(i.certeza).value)})
    for linha in b.presentes:
        p = _presente(b, linha)
        if p["planejado"]:
            continue
        cit = _citacao(b, p["registro_id"], p["trecho"], p["certeza"])
        if p["reacao"] == "nao_gostou":
            saida.append({"tipo": "nao_gostou", "texto": p["descricao"],
                          "motivo": "Não gostou quando ganhou" + (f" ({p['quem']})" if p["quem"] else ""),
                          **cit, "presente_id": p["id"]})
        elif p["de_quem"] == "eu" and p["_base"] and _somar_meses(p["_base"], MESES_JA_DADO) >= ref:
            saida.append({"tipo": "ja_dado", "texto": p["descricao"],
                          "motivo": f"Você já deu {p['quando'] or 'há pouco'}", **cit, "presente_id": p["id"]})
        elif p["de_quem"] == "outro" and p["reacao"] != "gostou" and p["_base"] \
                and _somar_meses(p["_base"], MESES_JA_GANHOU) >= ref:
            # Ela acabou de ganhar isso de outra pessoa: dar de novo seria repetir. (Se ela adorou, é
            # uma pista boa, e aparece nas ideias com o aviso.)
            saida.append({"tipo": "ja_ganhou", "texto": p["descricao"],
                          "motivo": f"Ela ganhou {p['quando'] or 'há pouco'}"
                          + (f" {de_quem_texto(p['quem'])}" if p["quem"] else ""),
                          **cit, "presente_id": p["id"]})
    ordem = {"restricao": 0, "nao_gosta": 1, "nao_quer": 2, "nao_gostou": 3, "ja_dado": 4, "ja_ganhou": 5}
    vistos: set[tuple[str, str]] = set()
    unicos = []
    for e in sorted(saida, key=lambda e: (ordem.get(e["tipo"], 9), -(e["data_registro"] or date.min).toordinal())):
        k = (e["tipo"], chave(e["texto"]))
        if k in vistos:
            continue
        vistos.add(k)
        unicos.append(e)
    return unicos


# ---------------------------------------------------------------- momento dela (sensibilidade)

def _momento(b: _Base) -> dict | None:
    """Estado difícil dela nos últimos 7 dias (e mais recente que qualquer estado bom), com o que ela
    disse que ajuda. Sem o que ajuda registrado, nada (não inventar)."""
    d, ref = b.d, b.ref
    estados = []
    for i in d.ativos:
        q = d.quando(i)
        if i.tipo == TipoItem.ESTADO and d.dono(i) is None and q is not None and q <= ref:
            estados.append((q, i.id or 0, i))
    if not estados:
        return None
    q, _, ultimo = max(estados, key=lambda x: (x[0], x[1]))
    if (ref - q).days > DIAS_MOMENTO or (ultimo.polaridade or 0) >= 0:
        return None
    apoios = [i for i in d.ativos if i.tipo == TipoItem.APOIO and d.dono(i) is None and (i.polaridade or 1) > 0]
    if not apoios:
        return None
    apoios.sort(key=lambda i: (Certeza(i.certeza) == Certeza.CONFIRMADO, d.data_registro(i) or date.min, i.id or 0),
                reverse=True)
    return {
        "estado": {"texto": ultimo.alvo or ultimo.resumo, "resumo": ultimo.resumo, "data": q, "quando": _quando(q, ref),
                   **_citacao(b, ultimo.registro_id, ultimo.trecho, Certeza(ultimo.certeza).value)},
        "apoios": [{"texto": i.resumo, "alvo": i.alvo, **_citacao(b, i.registro_id, i.trecho, Certeza(i.certeza).value)}
                   for i in apoios[:3]],
    }


# ---------------------------------------------------------------- ocasiões

@dataclass
class _Ocasiao:
    codigo: str
    rotulo: str                  # "o aniversário dela", "o Natal"
    titulo: str                  # "Aniversário dela"
    data: date
    origem: str                  # parte estável da chave: "dela", "data:12", "namoro", "natal"
    porque: str
    registro_ids: list[int] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)

    def dias(self, ref: date) -> int:
        return (self.data - ref).days


def _ocasioes(b: _Base, dias: int) -> list[_Ocasiao]:
    d, ref = b.d, b.ref
    saida: list[_Ocasiao] = []
    for oc in alertas.ocorrencias(d, dias):
        if oc.tipo == "aniversario_dela":
            nome = d.config.get("nome_dela")
            de = f"da {nome}" if nome else "dela"
            saida.append(_Ocasiao("aniversario_dela", f"o aniversário {de}", f"Aniversário {de}", oc.data, oc.origem,
                                  " ".join(oc.porque), list(oc.registro_ids),
                                  [oc.item.id] if oc.item is not None and oc.item.id is not None else []))
        elif oc.tipo == "namoro":
            titulo = oc.texto if oc.meses else "Aniversário de namoro"
            saida.append(_Ocasiao("namoro", "o aniversário de namoro", titulo, oc.data, oc.origem, " ".join(oc.porque),
                                  list(oc.registro_ids),
                                  [oc.item.id] if oc.item is not None and oc.item.id is not None else []))
    for codigo, (dia, mes), rotulo, titulo in (("natal", (25, 12), "o Natal", "Natal"),
                                               ("dia_dos_namorados", (12, 6), "o Dia dos Namorados", "Dia dos Namorados")):
        data = proxima_ocorrencia(dia, mes, ref)
        if (data - ref).days <= dias:
            saida.append(_Ocasiao(codigo, rotulo, titulo, data, codigo, f"{titulo}: {data.strftime('%d/%m')}."))
    vistas: set[tuple[str, date]] = set()
    unicas = []
    for oc in sorted(saida, key=lambda o: (o.data, o.codigo)):
        if (oc.codigo, oc.data) in vistas:
            continue
        vistas.add((oc.codigo, oc.data))
        unicas.append(oc)
    return unicas


def _quando_futuro(dias: int) -> str:
    return "hoje" if dias == 0 else "amanhã" if dias == 1 else f"em {dias} dias"


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


# ---------------------------------------------------------------- a tela

def painel(banco: Any, referencia: date) -> dict:
    """Tudo o que a tela #presentes mostra (datas como `date`: o núcleo aplica para_json). A frase do
    ciclo de um registro discreto não é citada (`sem_frases_reservadas`, seção 20.10)."""
    b = _carregar(banco, referencia)
    return sem_frases_reservadas(_painel(b), b.d.discretos)


def _painel(b: _Base) -> dict:
    ref = b.ref
    momento = _momento(b)
    desejos = _desejos_abertos(b)
    ideias, recusas = _ideias_e_recusas(b, momento)
    presentes = [_presente(b, l) for l in b.presentes]
    presentes.sort(key=lambda p: (p["_base"] or date.min, p["id"]), reverse=True)
    dados = [p for p in presentes if not p["planejado"]]
    ocasioes = _ocasioes(b, DIAS_OCASIOES_TELA)
    ocultos = _ocultos_para_tela(b)
    resposta = {
        "referencia": ref,
        "desejos": desejos,
        "ideias": ideias,
        "separados": [p for p in presentes if p["planejado"]],
        "recebidos": {"de_voce": [p for p in dados if p["de_quem"] == "eu"],
                      "de_outros": [p for p in dados if p["de_quem"] == "outro"]},
        "gostou": [p for p in dados if p["reacao"] == "gostou"],
        "nao_gostou": [p for p in dados if p["reacao"] == "nao_gostou"],
        "nao_recomendados": _nao_recomendados(b, recusas),
        "ocasioes": [{"codigo": o.codigo, "titulo": o.titulo, "data": o.data, "dias": o.dias(ref), "porque": o.porque,
                      "perto": o.dias(ref) <= DIAS_OCASIAO} for o in ocasioes],
        "momento": momento,
        "ocultos": ocultos,
        "reacoes": REACOES,
        "ocasioes_rotulos": OCASIOES,
    }
    resposta["vazio"] = not any((desejos, ideias, presentes, resposta["nao_recomendados"]))
    return _sem_internos(resposta)


def _ocultos_para_tela(b: _Base) -> list[dict]:
    """O que o usuário escondeu (para "mostrar de novo"), agrupado pelo que aparece na tela."""
    membros = {m.ref: m for m in _membros_desejo(b)}
    itens = {i.id: i for i in b.d.itens}
    grupos: dict[tuple[str, str], dict] = {}
    for o in b.ocultos:
        if o["tipo"] == "desejo_dado":
            continue
        texto = None
        if o["tipo"] == "desejo" and o["ref"] in membros:
            texto = membros[o["ref"]].descricao
        elif o["tipo"] == "ideia" and o["ref"].startswith("item:"):
            item = itens.get(int(o["ref"].split(":", 1)[1]))
            texto = _texto_alvo(item) if item is not None else None
        if not texto:
            continue
        k = (o["tipo"], chave(texto))
        entrada = grupos.setdefault(k, {"tipo": o["tipo"], "texto": texto, "ids": []})
        entrada["ids"].append(o["id"])
    return list(grupos.values())


# ======================================================================== Hoje

def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """Ocasião em até 45 dias com algo registrado: um cartão com o que ela mencionou e as ideias.
    Sem nada registrado, nenhum cartão (o Hoje já lembra da data em si)."""
    b = _carregar(banco, referencia)
    ocasioes = [o for o in _ocasioes(b, DIAS_OCASIAO) if 0 <= o.dias(referencia) <= DIAS_OCASIAO]
    if not ocasioes:
        return []
    momento = _momento(b)
    # O que talvez já foi realizado (ela ganhou, você deu) não volta como ideia (revisão de 07/10).
    desejos = sem_frases_reservadas([e for e in _desejos_abertos(b) if not e["talvez_dado"]], b.d.discretos)
    ideias, _ = _ideias_e_recusas(b, momento)
    ideias = sem_frases_reservadas(ideias, b.d.discretos)
    presentes = [_presente(b, l) for l in b.presentes]
    cartoes = []
    for oc in ocasioes:
        dias = oc.dias(referencia)
        separados = [p for p in presentes if p["planejado"] and (
            p["ocasiao"] == oc.codigo or (p["data"] is not None and abs((p["data"] - oc.data).days) <= 3))]
        if not (desejos or ideias or separados):
            continue
        partes = []
        if desejos:
            partes.append(_plural(len(desejos), "coisa que ela mencionou", "coisas que ela mencionou"))
        if ideias:
            partes.append(_plural(len(ideias), "ideia a partir do que ela gosta", "ideias a partir do que ela gosta"))
        texto = f"Ideias para {oc.rotulo} ({_quando_futuro(dias)})"
        texto += (": " + " e ".join(partes) + ".") if partes else "."
        if separados:
            texto += f" Você já separou: {', '.join(p['descricao'] for p in separados[:2])}."
        porque = [oc.porque]
        registro_ids: list[int] = list(oc.registro_ids)
        for e in desejos[:MAX_NO_CARTAO]:
            dr = e["data_registro"]
            porque.append(f"Você registrou{(' em ' + _dia(dr, referencia)) if dr else ''}: "
                          + (f"“{e['trecho'].strip()}”." if e["trecho"] else f"{e['descricao']} (um registro reservado)."))
            if e["registro_id"] is not None:
                registro_ids.append(e["registro_id"])
        for i in ideias[:max(0, MAX_NO_CARTAO - len(desejos))]:
            o = i["origens"][0] if i["origens"] else None
            if o:
                porque.append(f"Ideia (🔵): {i['frase'][:1].lower()}{i['frase'][1:]} — você registrou"
                              f"{(' em ' + _dia(o['data_registro'], referencia)) if o['data_registro'] else ''}"
                              + (f": “{o['trecho'].strip()}”." if o["trecho"] else " (um registro reservado)."))

                if o["registro_id"] is not None:
                    registro_ids.append(o["registro_id"])
        relevancia = 55 + min(15, (DIAS_OCASIAO - dias) // 3) + (5 if oc.codigo in ("aniversario_dela", "namoro") else 0)
        if separados:
            relevancia -= 10
        cartoes.append(cartao(
            chave=f"ocasiao:{oc.origem}:{oc.data.isoformat()}", nivel="sugestao", texto=texto,
            porque=" ".join(porque), relevancia=relevancia, data=oc.data,
            registro_ids=list(dict.fromkeys(r for r in registro_ids if r is not None)), tipo="presentes_ocasiao",
            dados={
                "ocasiao": oc.codigo, "titulo": oc.titulo, "data": oc.data, "dias": dias,
                "desejos": [{"descricao": e["descricao"], "certeza": e["certeza"], "quando": e["quando"],
                             "registro_id": e["registro_id"], "trecho": e["trecho"]} for e in desejos[:MAX_NO_CARTAO]],
                "ideias": [{"frase": i["frase"], "texto": i["texto"], "certeza": i["certeza"], "motivos": i["motivos"][:2],
                            "registro_id": i["origens"][0]["registro_id"] if i["origens"] else None}
                           for i in ideias[:max(0, MAX_NO_CARTAO - len(desejos))]],
                "separados": [{"descricao": p["descricao"], "id": p["id"]} for p in separados[:2]],
                "total_desejos": len(desejos), "total_ideias": len(ideias),
            }))
    return cartoes


# ======================================================================== rotas

def _hoje_do_pedido(nucleo: Any, pedido: Any) -> date:
    bruto = pedido.parametro("data") if hasattr(pedido, "parametro") else None
    if bruto:
        try:
            return date.fromisoformat(bruto)
        except ValueError:
            raise ValueError("O parâmetro “data” precisa ser uma data (AAAA-MM-DD).") from None
    return nucleo.hoje()


def _corpo(pedido: Any) -> dict:
    corpo = pedido.corpo
    if not isinstance(corpo, dict):
        raise ValueError("O pedido precisa ser um objeto.")
    return corpo


def _apagar_nas_copias(nucleo: Any, tabela: str, id_: int, extra: Any = None) -> None:
    def apagar(copia: Any) -> bool | None:
        try:
            copia.apagar(tabela, id_)
        except (NaoEncontrado, OperacaoProibida):
            return False
        if extra is not None:
            try:
                extra(copia)
            except (NaoEncontrado, OperacaoProibida):
                pass
        return None
    nucleo.limpar_copias(apagar)


def _rota_painel(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return painel(banco, _hoje_do_pedido(nucleo, pedido))


_EDITAVEIS = frozenset({"reacao", "ocasiao", "data", "descricao", "quem", "de_quem", "planejado"})


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    corpo = _corpo(pedido)
    if not corpo or set(corpo) - _EDITAVEIS:
        raise ValueError(ERRO_CAMPO)
    linhas = banco.consultar("SELECT * FROM presentes WHERE id = ?", (pedido.id,))
    if not linhas:
        raise NaoEncontrado("Não encontrei esse presente. Talvez já tenha sido apagado.")
    atual = linhas[0]
    mudancas: dict[str, Any] = {}
    if "reacao" in corpo:
        mudancas["reacao"] = _reacao_campo(corpo["reacao"])
    if "ocasiao" in corpo:
        mudancas["ocasiao"] = _ocasiao_campo(corpo["ocasiao"])
    if "data" in corpo:
        mudancas["data"] = _data_campo(corpo["data"])
    if "descricao" in corpo:
        mudancas["descricao"] = _texto_curto(corpo["descricao"], MAX_DESCRICAO, ERRO_DESCRICAO)
    if "de_quem" in corpo:
        if corpo["de_quem"] not in DE_QUEM:
            raise ValueError(ERRO_DE_QUEM)
        mudancas["de_quem"] = corpo["de_quem"]
    if "quem" in corpo:
        mudancas["quem"] = _texto_curto(corpo["quem"], MAX_QUEM, ERRO_QUEM, obrigatorio=False)
    hoje = _hoje_do_pedido(nucleo, pedido)
    if "planejado" in corpo:
        if corpo["planejado"] is not False:
            raise ValueError("Um presente só deixa de estar “separado” (quando você dá).")
        mudancas["planejado"] = False
        data_antes = _data(atual["data"])
        if "data" not in mudancas and (data_antes is None or data_antes > hoje):
            mudancas["data"] = hoje.isoformat()      # deu agora (a data de "separado" era a da ocasião)
    if mudancas.get("de_quem", atual["de_quem"]) == "eu":
        if atual["quem"] is not None or "quem" in mudancas:
            mudancas["quem"] = None
    elif atual["planejado"]:
        mudancas["planejado"] = False                # presente de outra pessoa nunca fica "separado"
    continua_separado = bool(atual["planejado"]) and mudancas.get("planejado", True) is not False
    if continua_separado and mudancas.get("reacao", "nao_sei") != "nao_sei":
        raise ValueError("Ela ainda não recebeu este presente: marque “Já dei” antes da reação.")
    mudancas["atualizado_em"] = hoje
    banco.atualizar("presentes", pedido.id, mudancas)
    return {}


def _rota_apagar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    with banco.transacao():
        banco.apagar("presentes", pedido.id)
        banco.executar("DELETE FROM presentes_ocultos WHERE presente_id = ?", (pedido.id,))
    _apagar_nas_copias(nucleo, "presentes", pedido.id,
                       lambda c: c.executar("DELETE FROM presentes_ocultos WHERE presente_id = ?", (pedido.id,)))
    return {}


def _grupo_do_membro(b: _Base, origem: str, id_: int) -> list[_Membro]:
    for grupo in _grupos_de_desejo(b):
        if any(m.origem == origem and m.id == id_ for m in grupo):
            return grupo
    raise NaoEncontrado("Não encontrei esse desejo. Talvez já tenha sido apagado.")


def _dei(origem: str):
    def fn(nucleo: Any, pedido: Any, banco: Any) -> dict:
        corpo = _corpo(pedido)
        if set(corpo) - {"presente_id", "data", "ocasiao"}:
            raise ValueError(ERRO_CAMPO)
        hoje = _hoje_do_pedido(nucleo, pedido)
        b = _carregar(banco, hoje)
        grupo = _grupo_do_membro(b, origem, pedido.id)
        dados, _ = _situacao_dos_membros(b)
        abertos = [m for m in grupo if m.ref not in dados] or grupo
        rep = next((m for m in abertos if m.origem == origem and m.id == pedido.id), abertos[0])
        coluna = "desejo_item_id" if rep.origem == "item" else "desejo_id"
        with banco.transacao():
            presente_id = corpo.get("presente_id")
            if presente_id is not None:
                if isinstance(presente_id, bool) or not isinstance(presente_id, int):
                    raise ValueError("Diga qual presente realizou este desejo.")
                linhas = banco.consultar("SELECT * FROM presentes WHERE id = ?", (presente_id,))
                if not linhas:
                    raise NaoEncontrado("Não encontrei esse presente. Talvez já tenha sido apagado.")
                if linhas[0]["desejo_item_id"] is not None or linhas[0]["desejo_id"] is not None:
                    raise ValueError("Esse presente já está ligado a outro desejo.")
                banco.atualizar("presentes", presente_id, {coluna: rep.id, "atualizado_em": hoje})
            else:
                data = _data_campo(corpo.get("data")) or hoje.isoformat()
                if date.fromisoformat(data) > hoje:
                    raise ValueError("A data em que você deu não pode ser no futuro.")
                presente_id = banco.inserir("presentes", {
                    "descricao": rep.descricao[:MAX_DESCRICAO], "data": data,
                    "ocasiao": _ocasiao_campo(corpo.get("ocasiao")), "de_quem": "eu", "reacao": "nao_sei",
                    "registro_id": rep.registro_id, "criado_em": hoje, "trecho": rep.trecho,
                    "certeza": Certeza.OBSERVADO.value, "planejado": False, coluna: rep.id})
            for m in abertos:
                if m is rep:
                    continue
                banco.inserir("presentes_ocultos", {"tipo": "desejo_dado", "ref": m.ref, "presente_id": presente_id,
                                                    "registro_id": m.registro_id, "criado_em": hoje})
        return {"presente_id": presente_id}
    return fn


def _ocultar_desejo(origem: str):
    def fn(nucleo: Any, pedido: Any, banco: Any) -> dict:
        hoje = _hoje_do_pedido(nucleo, pedido)
        b = _carregar(banco, hoje)
        grupo = _grupo_do_membro(b, origem, pedido.id)
        _, escondidos = _situacao_dos_membros(b)
        with banco.transacao():
            for m in grupo:
                if m.ref not in escondidos:
                    banco.inserir("presentes_ocultos", {"tipo": "desejo", "ref": m.ref, "registro_id": m.registro_id,
                                                        "criado_em": hoje})
        return {}
    return fn


def _rota_ocultar_ideia(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """Esconde a ideia que tem este item (preferência) como representante: todos os itens dela."""
    hoje = _hoje_do_pedido(nucleo, pedido)
    b = _carregar(banco, hoje)
    ideias, _ = _ideias_e_recusas(b, _momento(b))
    ideia = next((i for i in ideias if pedido.id in i["item_ids"]), None)
    if ideia is None:
        raise NaoEncontrado("Não encontrei essa ideia. Talvez ela já não apareça mais.")
    registros = {i.id: i.registro_id for i in b.d.itens}
    ja = {o["ref"] for o in b.ocultos if o["tipo"] == "ideia"}
    with banco.transacao():
        for iid in ideia["item_ids"]:
            if f"item:{iid}" not in ja:
                banco.inserir("presentes_ocultos", {"tipo": "ideia", "ref": f"item:{iid}",
                                                    "registro_id": registros.get(iid), "criado_em": hoje})
    return {}


def _rota_mostrar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    linhas = banco.consultar("SELECT tipo FROM presentes_ocultos WHERE id = ?", (pedido.id,))
    if not linhas or linhas[0]["tipo"] == "desejo_dado":
        raise NaoEncontrado("Não encontrei isso. Talvez já esteja aparecendo de novo.")
    banco.apagar("presentes_ocultos", pedido.id)
    return {}


def _rota_apagar_desejo(nucleo: Any, pedido: Any, banco: Any) -> dict:
    with banco.transacao():
        banco.apagar("presentes_desejos", pedido.id)
        banco.executar("DELETE FROM presentes_ocultos WHERE ref = ?", (f"desejo:{pedido.id}",))
        banco.executar("UPDATE presentes SET desejo_id = NULL WHERE desejo_id = ?", (pedido.id,))
    _apagar_nas_copias(nucleo, "presentes_desejos", pedido.id)
    return {}


def rotas() -> list:
    return [
        rota("GET", "", _rota_painel),
        rota("PATCH", "{id}", _rota_editar),
        rota("DELETE", "{id}", _rota_apagar),
        rota("POST", "itens/{id}/dei", _dei("item")),
        rota("POST", "desejos/{id}/dei", _dei("desejo")),
        rota("POST", "itens/{id}/ocultar", _ocultar_desejo("item")),
        rota("POST", "desejos/{id}/ocultar", _ocultar_desejo("desejo")),
        rota("DELETE", "desejos/{id}", _rota_apagar_desejo),
        rota("POST", "ideias/{id}/ocultar", _rota_ocultar_ideia),
        rota("DELETE", "ocultos/{id}", _rota_mostrar),
    ]


# ======================================================================== exportar, importar, apagar

def _ref_item(banco: Any, item_id: int | None) -> dict | None:
    """Um item central como o arquivo leva: o registro e o trecho (os ids mudam ao importar)."""
    if item_id is None:
        return None
    linhas = banco.consultar("SELECT registro_id, tipo, trecho FROM itens WHERE id = ?", (item_id,))
    return dict(linhas[0]) if linhas else None


def _achar_item(banco: Any, ref: Any, mapa: dict[int, int]) -> int | None:
    if not isinstance(ref, dict):
        return None
    rid = mapa.get(ref.get("registro_id")) if isinstance(ref.get("registro_id"), int) else None
    if rid is None or not isinstance(ref.get("trecho"), str):
        return None
    linhas = banco.consultar("SELECT id FROM itens WHERE registro_id = ? AND tipo = ? AND trecho = ? ORDER BY id",
                             (rid, ref.get("tipo") or "desejo", ref["trecho"]))
    return linhas[0]["id"] if linhas else None


def exportar(banco: Any) -> dict:
    presentes = []
    for l in banco.consultar("SELECT * FROM presentes ORDER BY id"):
        presentes.append({k: l[k] for k in ("id", "descricao", "data", "ocasiao", "de_quem", "quem", "reacao", "planejado",
                                             "trecho", "certeza", "registro_id", "criado_em", "desejo_id")}
                         | {"planejado": bool(l["planejado"]), "desejo_item": _ref_item(banco, l["desejo_item_id"])})
    desejos = [dict(l) for l in banco.consultar(
        "SELECT id, descricao, trecho, certeza, pista, registro_id, criado_em FROM presentes_desejos ORDER BY id")]
    ocultos = []
    for l in banco.consultar("SELECT * FROM presentes_ocultos ORDER BY id"):
        origem, _, numero = l["ref"].partition(":")
        alvo = _ref_item(banco, int(numero)) if origem == "item" and numero.isdigit() else None
        ocultos.append({"tipo": l["tipo"], "origem": origem, "desejo_id": int(numero) if origem == "desejo" and numero.isdigit()
                        else None, "item": alvo, "presente_id": l["presente_id"], "registro_id": l["registro_id"],
                        "criado_em": l["criado_em"]})
    return {"versao": 1, "presentes": presentes, "desejos": desejos, "ocultos": ocultos}


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta sem duplicar: só o que pertence a um registro que veio (ou já estava) no diário; o
    mesmo registro + trecho + descrição não entra duas vezes. Linha estranha é ignorada."""
    if not isinstance(dados, dict):
        raise ValueError("Os dados de presentes no arquivo não estão no formato certo.")
    mapa = {int(k): v for k, v in mapa_registros.items()}
    novos = ignorados = 0
    ids_desejo: dict[int, int] = {}
    ids_presente: dict[int, int] = {}

    def texto_ok(v: Any, maximo: int) -> bool:
        return isinstance(v, str) and 0 < len(v.strip()) <= maximo

    for d in dados.get("desejos") or []:
        try:
            rid = mapa.get(d.get("registro_id")) if isinstance(d, dict) else None
            if rid is None or not texto_ok(d.get("descricao"), MAX_DESCRICAO) or not texto_ok(d.get("trecho"), 100_000):
                ignorados += 1
                continue
            texto = banco.consultar("SELECT texto FROM registros WHERE id = ?", (rid,))
            if not texto or d["trecho"] not in texto[0]["texto"]:
                ignorados += 1
                continue
            ja = banco.consultar("SELECT id FROM presentes_desejos WHERE registro_id = ? AND trecho = ? AND descricao = ?",
                                 (rid, d["trecho"], d["descricao"]))
            if ja:
                if isinstance(d.get("id"), int):
                    ids_desejo[d["id"]] = ja[0]["id"]
                continue
            novo = banco.inserir("presentes_desejos", {
                "descricao": d["descricao"].strip(), "trecho": d["trecho"],
                "certeza": _certeza_do_trecho(d.get("certeza"), d["trecho"]),
                "pista": d.get("pista") if isinstance(d.get("pista"), str) and re.fullmatch(r"[a-z_]{1,30}", d["pista"])
                else None,
                "registro_id": rid, "criado_em": _data(d.get("criado_em")) or date.today()})
            if isinstance(d.get("id"), int):
                ids_desejo[d["id"]] = novo
            novos += 1
        except (TypeError, ValueError, AttributeError):
            ignorados += 1

    for p in dados.get("presentes") or []:
        try:
            rid = mapa.get(p.get("registro_id")) if isinstance(p, dict) else None
            if rid is None or not texto_ok(p.get("trecho"), 100_000):
                ignorados += 1
                continue
            texto = banco.consultar("SELECT texto FROM registros WHERE id = ?", (rid,))
            if not texto or p["trecho"] not in texto[0]["texto"]:
                ignorados += 1
                continue
            linha = _linha_presente({**p, "planejado": bool(p.get("planejado"))}, rid,
                                    _data(p.get("criado_em")) or date.today())
            ja = banco.consultar("SELECT id FROM presentes WHERE registro_id = ? AND trecho = ? AND descricao = ? "
                                 "AND de_quem = ?", (rid, linha["trecho"], linha["descricao"], linha["de_quem"]))
            if ja:
                if isinstance(p.get("id"), int):
                    ids_presente[p["id"]] = ja[0]["id"]
                continue
            if isinstance(p.get("desejo_id"), int) and p["desejo_id"] in ids_desejo:
                linha["desejo_id"] = ids_desejo[p["desejo_id"]]
            item = _achar_item(banco, p.get("desejo_item"), mapa)
            if item is not None:
                linha["desejo_item_id"] = item
            novo = banco.inserir("presentes", linha)
            if isinstance(p.get("id"), int):
                ids_presente[p["id"]] = novo
            novos += 1
        except (TypeError, ValueError, AttributeError):
            ignorados += 1

    ocultos = 0
    for o in dados.get("ocultos") or []:
        try:
            if not isinstance(o, dict) or o.get("tipo") not in ("desejo", "desejo_dado", "ideia"):
                ignorados += 1
                continue
            rid = mapa.get(o.get("registro_id"))
            if rid is None:
                ignorados += 1
                continue
            if o.get("origem") == "desejo" and isinstance(o.get("desejo_id"), int) and o["desejo_id"] in ids_desejo:
                ref = f"desejo:{ids_desejo[o['desejo_id']]}"
            elif o.get("origem") == "item":
                item = _achar_item(banco, o.get("item"), mapa)
                if item is None:
                    ignorados += 1
                    continue
                ref = f"item:{item}"
            else:
                ignorados += 1
                continue
            presente = ids_presente.get(o.get("presente_id")) if isinstance(o.get("presente_id"), int) else None
            if o["tipo"] == "desejo_dado" and presente is None:
                ignorados += 1
                continue
            if banco.consultar("SELECT 1 FROM presentes_ocultos WHERE tipo = ? AND ref = ?", (o["tipo"], ref)):
                continue
            banco.inserir("presentes_ocultos", {"tipo": o["tipo"], "ref": ref, "presente_id": presente,
                                                "registro_id": rid, "criado_em": _data(o.get("criado_em")) or date.today()})
            ocultos += 1
        except (TypeError, ValueError, AttributeError):
            ignorados += 1

    partes = [_plural(novos, "presente ou desejo", "presentes ou desejos")]
    texto = f"{partes[0]} de Ideias de presentes" if novos else "Nada novo em Ideias de presentes"
    if ignorados:
        texto += f" ({_plural(ignorados, 'linha ignorada', 'linhas ignoradas')})"
    return {"novos": novos, "texto": texto + ".", "ocultos": ocultos, "ignorados": ignorados}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """O registro ainda existe: tira as ligações que apontam para ele e as chaves de cartão que
    revelariam a data de um aniversário tirado dele. As linhas com este registro_id saem depois,
    pela fundação (apagar é apagar)."""
    itens = [l["id"] for l in banco.consultar("SELECT id FROM itens WHERE registro_id = ?", (registro_id,))]
    desejos = [l["id"] for l in banco.consultar("SELECT id FROM presentes_desejos WHERE registro_id = ?", (registro_id,))]
    presentes = [l["id"] for l in banco.consultar("SELECT id FROM presentes WHERE registro_id = ?", (registro_id,))]
    for iid in itens:
        banco.executar("UPDATE presentes SET desejo_item_id = NULL WHERE desejo_item_id = ?", (iid,))
        banco.executar("DELETE FROM presentes_ocultos WHERE ref = ?", (f"item:{iid}",))
        banco.esquecer_alertas(f"{NOME}:ocasiao:data:{iid}:")
    for did in desejos:
        banco.executar("UPDATE presentes SET desejo_id = NULL WHERE desejo_id = ?", (did,))
        banco.executar("DELETE FROM presentes_ocultos WHERE ref = ?", (f"desejo:{did}",))
    for pid in presentes:
        banco.executar("DELETE FROM presentes_ocultos WHERE presente_id = ?", (pid,))


__all__ = ["ESQUEMA", "ESQUEMA_VERSAO", "NOME", "OCASIOES", "REACOES", "ROTULO", "TIPOS_EXTRAS", "ao_apagar_registro",
           "cartoes_hoje", "chave", "exportar", "extrair", "gravar_extras", "importar", "mesma_coisa", "painel", "rotas"]
