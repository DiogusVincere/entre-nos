"""Lugares (DESIGN.md, seção 20.5).

O que este recurso faz, sempre com o que foi registrado (nunca inventa):

- **Lê o texto** (`extrair`): lugares onde vocês foram ("fomos no Coco Bambu ontem e ela amou o
  camarão", "jantamos no restaurante Mangiare sábado passado, ela achou meio caro", "nossa viagem
  para Paraty foi incrível"), onde ela foi ("ela foi no bar do Zé com as amigas e amou"), o que
  ela quer conhecer ("ela quer conhecer o Beco do Batman", "ela sempre quis ir pra Gramado"), o
  que vocês combinaram ("vamos no Coco Bambu sábado"), a sua ideia ("quero levar ela no Jardim
  Botânico") e a opinião dela sobre um lugar ("ela adora o Parque Ibirapuera", "o restaurante
  favorito dela é o Mangiare", "ela odiou o bar Pinguim, muito barulhento"). Cada proposta leva o
  `trecho` literal; o nome do lugar é o que está escrito.
- **Grava** o que o usuário confirmou na revisão (`gravar_extras`): um lugar é uma linha de
  `lugares` (o contrato da seção 20.5) e cada vez que ele aparece num registro é uma menção, com o
  trecho, em `lugares_mencoes`. A situação do lugar (fomos / queremos ir), a opinião dela, a cidade
  e os registros saem das menções, a não ser o que o usuário corrigiu à mão.
- **Monta a aba "Lugares"** de Conhecendo Ela (`painel`): o que ela quer conhecer, em ordem de uma
  pontuação explicável (quem quer, se ela disse com as palavras dela, quantas vezes, há quanto
  tempo, se combina com o que ela gosta, se há uma viagem ou uma data chegando); os lugares de
  vocês com a opinião dela e as ressalvas; as pistas do que ela curte e do que é melhor evitar
  (das preferências e restrições já registradas).
- **Sugere no Hoje** (`cartoes_hoje`) no máximo um lugar por semana: "Talvez o <Lugar>, que ela quer
  conhecer, seja uma boa para o próximo encontro". Sensível ao momento dela: com um estado difícil
  registrado nos últimos 3 dias não sugere sair; sem nada que ela queira conhecer (ou um lugar que
  ela amou para voltar), nenhum cartão (nada genérico inventado).

Nada daqui precisa ficar fora da IA (o recurso não tem `marca_privado` nem `fora_da_ia`); os extras
nunca vão para a IA (a fundação garante). Tudo roda no navegador também (Pyodide): nada de thread,
sleep, rede ou Windows; as datas saem de regras/datas.py.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .. import alertas
from ..banco import NaoEncontrado, OperacaoProibida
from ..modelos import Certeza, Item, TipoItem
from ..perfil import forca_preferencia
from ..regras.certeza import classificar_certeza, trechos_nao_afirmados
from ..regras.pessoas import RELACOES, e_nome_dela, plano
from ..texto import frases, normalizar, semelhanca, tempo_atras, termos
from . import cartao, rota, sem_frases_reservadas
from . import texto_reservado as _reservado

NOME = "lugares"
ROTULO = "Lugares"
TIPOS_EXTRAS = ("lugar",)

ESQUEMA = [
    """
    CREATE TABLE lugares (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        tipo TEXT,
        status TEXT NOT NULL DEFAULT 'queremos_ir' CHECK (status IN ('fomos', 'queremos_ir')),
        opiniao INTEGER CHECK (opiniao IN (-1, 1)),
        cidade TEXT,
        registro_ids TEXT NOT NULL DEFAULT '[]',
        criado_em TEXT NOT NULL,
        chave TEXT NOT NULL,
        generico INTEGER NOT NULL DEFAULT 0,
        juntos INTEGER NOT NULL DEFAULT 0,
        quem_quer TEXT,
        editado TEXT NOT NULL DEFAULT '[]',
        visitado_em TEXT,
        atualizado_em TEXT
    );
    CREATE INDEX lugares_chave ON lugares(chave);
    CREATE TABLE lugares_mencoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lugar_id INTEGER NOT NULL,
        registro_id INTEGER NOT NULL,
        mencao TEXT NOT NULL CHECK (mencao IN ('visita', 'vontade', 'opiniao')),
        quem TEXT NOT NULL CHECK (quem IN ('nos', 'ela', 'eu')),
        opiniao INTEGER CHECK (opiniao IN (-1, 1)),
        reacao TEXT,
        ressalvas TEXT NOT NULL DEFAULT '[]',
        voltar INTEGER NOT NULL DEFAULT 0,
        data TEXT,
        data_expressao TEXT,
        nome TEXT NOT NULL,
        tipo_lugar TEXT,
        palavra_tipo TEXT,
        cidade TEXT,
        trecho TEXT NOT NULL,
        certeza TEXT NOT NULL DEFAULT 'observado',
        criado_em TEXT NOT NULL
    );
    CREATE INDEX lugares_mencoes_lugar ON lugares_mencoes(lugar_id);
    CREATE INDEX lugares_mencoes_registro ON lugares_mencoes(registro_id)
    """,
]
ESQUEMA_VERSAO = 1

STATUS: dict[str, str] = {"fomos": "Já foram", "queremos_ir": "Querem ir"}
MENCOES = ("visita", "vontade", "opiniao")
QUEM = ("nos", "ela", "eu")

# Código do tipo -> (rótulo, emoji). O código é o que vai na coluna `tipo` (filtros da tela).
TIPOS: dict[str, tuple[str, str]] = {
    "restaurante": ("Restaurante", "🍽️"),
    "bar": ("Bar e bebidas", "🍻"),
    "cafe": ("Café e padaria", "☕"),
    "doces": ("Doces e sorvete", "🍦"),
    "praia": ("Praia", "🏖️"),
    "natureza": ("Natureza", "🌳"),
    "cultura": ("Cultura", "🖼️"),
    "cinema": ("Cinema", "🎬"),
    "show": ("Show e festa", "🎶"),
    "compras": ("Compras e feiras", "🛍️"),
    "hospedagem": ("Hospedagem", "🏨"),
    "cidade": ("Cidade ou viagem", "🧳"),
    "passeio": ("Passeio", "📍"),
    "outro": ("Outro", "📍"),
}
_COMIDA = frozenset({"restaurante", "bar", "cafe", "doces"})
_CALMOS = frozenset({"natureza", "praia", "cafe", "cultura", "doces", "cinema"})
_AGITADOS = frozenset({"show", "bar"})

MAX_NOME = 80
MAX_CIDADE = 60
MAX_REACAO = 80
MAX_RESSALVAS = 5
MAX_EXPRESSAO = 60
MAX_ORIGENS = 3

DIAS_MOMENTO_BLOQUEIA = 3     # estado difícil dela nos últimos 3 dias: sem sugestão de sair
DIAS_MOMENTO = 7              # até 7 dias: lugares tranquilos ganham ponto
DIAS_OCASIAO = 21             # aniversário dela / de namoro / mesversário em até 21 dias
DIAS_VIAGEM = 60              # viagem registrada nos próximos 60 dias
DIAS_PLANO = 30               # combinado com data nos próximos 30 dias: já está planejado
DIAS_VOLTAR = 60              # lugar que ela amou e não voltaram há 60+ dias
SEMANAS_DESCANSO = 4          # "agora não": o lugar descansa 4 semanas
SEMELHANCA_MESMO_LUGAR = 95
DIAS_DA_SUGESTAO = (3, 4)     # o cartão do Hoje só na quinta e na sexta (planejar o fim de semana)...
DIAS_DATA_ESPECIAL = 3        # ... ou com mesversário/aniversário dela/de namoro em até 3 dias

ERRO_NOME = f"Diga o nome do lugar (até {MAX_NOME} letras)."
ERRO_TIPO = "Tipo de lugar desconhecido."
ERRO_CIDADE = f"O nome da cidade é longo demais (até {MAX_CIDADE} letras)."
ERRO_STATUS = "Diga se vocês já foram ou se querem ir."
ERRO_OPINIAO = "A opinião precisa ser gostou (1), não gostou (-1) ou não sei (vazio)."
ERRO_MENCAO = "Tipo de menção desconhecido."
ERRO_QUEM = "Diga quem quer ir ou quem foi."
ERRO_DATA = "A data precisa estar no formato AAAA-MM-DD."
ERRO_DATA_FUTURA = "A data de uma ida não pode ser no futuro."
ERRO_CAMPO = "Este campo não pode ser mudado aqui."
ERRO_REPETIDO = "Já existe um lugar com esse nome na sua lista."
NAO_ACHEI = "Não encontrei esse lugar. Talvez já tenha sido apagado."
NAO_ACHEI_MENCAO = "Não encontrei essa menção. Talvez já tenha sido apagada."


# ======================================================================== vocabulário

def _lista(texto: str) -> list[str]:
    return [" ".join(normalizar(x).split()) for x in re.split(r"[|\n]", texto) if x.strip()]


# Palavra de tipo (normalizada) -> (código, gênero do artigo).
_TIPO_PALAVRAS: dict[str, tuple[str, str]] = {}
for _grupo, _genero, _palavras in (
    ("restaurante", "m", "restaurante|rodizio|bistro|self service|buffet|quiosque|food truck|trailer"),
    ("restaurante", "f", "churrascaria|pizzaria|hamburgueria|lanchonete|temakeria|cantina|trattoria|creperia"
                         "|esfiharia|pastelaria|tapiocaria|peixaria|galeteria"),
    ("bar", "m", "bar|barzinho|boteco|botequim|pub|rooftop|bar de vinho"),
    ("bar", "f", "cervejaria|choperia|adega|vinicola|destilaria|cachacaria"),
    ("cafe", "m", "cafe|cafezinho|cafe colonial"),
    ("cafe", "f", "cafeteria|padaria|casa de cha"),
    ("doces", "f", "confeitaria|doceria|sorveteria|gelateria|acaiteria|chocolateria|brigaderia|bomboniere"),
    ("praia", "f", "praia|prainha"),
    ("natureza", "m", "parque|parque estadual|parque nacional|jardim botanico|jardim|bosque|mirante|lago|pico|morro"
                      "|horto|vale|canion|camping"),
    ("natureza", "f", "trilha|cachoeira|lagoa|serra|represa|gruta|caverna|reserva|chapada"),
    ("cultura", "m", "museu|teatro|centro cultural|planetario|observatorio|instituto"),
    ("cultura", "f", "galeria|exposicao|livraria|biblioteca|pinacoteca|bienal"),
    ("cinema", "m", "cinema|drive-in|cine"),
    ("show", "m", "show|festival|karaoke|estadio|clube"),
    ("show", "f", "balada|boate|casa de show|roda de samba|festa junina"),
    ("compras", "m", "shopping|mercadao|mercado municipal|brecho|outlet|bazar"),
    ("compras", "f", "feira|feirinha|feira de artesanato"),
    ("hospedagem", "m", "hotel|resort|chale|hostel|airbnb"),
    ("hospedagem", "f", "pousada|cabana"),
    ("passeio", "m", "zoologico|aquario|parque de diversoes|parque aquatico|boliche|beco|bairro|calcadao|spa"
                     "|kart|escape room|mirante"),
    ("passeio", "f", "rua|orla|praca|ilha|fazendinha|roda gigante"),
):
    for _p in _lista(_palavras):
        _TIPO_PALAVRAS.setdefault(_p, (_grupo, _genero))


def _alternativas(chaves: Any) -> str:
    return "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(chaves, key=len, reverse=True))


_RE_TIPO = re.compile(r"(?:" + _alternativas(_TIPO_PALAVRAS) + r")(?![\w-])")
_GENERO_PADRAO = {"restaurante": "m", "bar": "m", "cafe": "m", "doces": "f", "praia": "f", "natureza": "m",
                  "cultura": "m", "cinema": "m", "show": "m", "compras": "m", "hospedagem": "m"}

# Cozinhas e estilos que, logo depois do tipo, já dizem que lugar é ("um restaurante mexicano").
_COZINHAS = frozenset(_lista("""
japones|japonesa|italiano|italiana|arabe|mexicano|mexicana|chines|chinesa|tailandes|tailandesa|indiano|indiana
|coreano|coreana|peruano|peruana|frances|francesa|portugues|portuguesa|vegano|vegana|vegetariano|vegetariana
|nordestino|nordestina|mineiro|mineira|grego|grega|alemao|alema|espanhol|espanhola|havaiano|havaiana|argentino
|argentina|baiano|baiana|libanes|libanesa|turco|turca|asiatico|asiatica|contemporaneo|contemporanea|natural
|saudavel|sertanejo|sertaneja|irlandes|irlandesa|ingles|inglesa|colonial|caicara|praiano|uruguaio|uruguaia
"""))
# Palavras que fecham o nome de um lugar escrito em minúsculas ("fomos no parque perto de casa").
_FIM_NOME = frozenset(_lista("""
e|com|que|pra|para|pro|no|na|nos|nas|num|numa|em|ao|aos|a|o|as|os|ontem|hoje|hj|anteontem|amanha|depois|antes
|agora|logo|cedo|tarde|noite|manha|madrugada|sabado|domingo|segunda|terca|quarta|quinta|sexta|semana|mes|ano
|fds|feriado|ferias|aniversario|niver|natal|mas|porque|pq|onde|quando|la|aqui|ali|perto|longe|junto|juntos
|juntinhos|novo|nova|de|do|da|dos|das|so|ja|tambem|tb|tbm|ate|pela|pelo|kk|kkk|rs|rsrs|haha|ela|eu|gente|nos
|voce|mesmo|mesma|inteiro|inteira|todo|toda|bom|boa|otimo|otima|lindo|linda|maravilhoso|maravilhosa|incrivel
|caro|cara|barato|barata|legal|top|chique|simples|pequeno|pequena|grande|gostoso|gostosa|famoso|famosa|lotado
|lotada|cheio|cheia|vazio|vazia|lindissimo|lindissima|perfeito|perfeita|delicia|demais|muito|pouco|bem|mal
|casa|carro|uber|onibus|metro|trem|bike|bicicleta|pe|moto|taxi|aviao|graca|repente|surpresa|volta|ida
|primeira|primeiro|vez|vezes|dia|dias|hora|horas|minutos|tempo|lado|frente|fundo|centro|caminho|vista
|e|eh|era|foi|tava|estava|ficou|ficamos|fomos|vamos|vai|vou|quer|queria|amou|adorou|gostou|curtiu|odiou
"""))
_NOVO = frozenset(_lista("novo|nova|novinho|novinha|recem-aberto|recem aberto"))
_RE_MINUSCULA = re.compile(r"[a-z0-9][a-z0-9'-]*")

# Cidades, destinos e lugares conhecidos (escritos em minúsculas também valem).
_CONHECIDOS: dict[str, str] = {}
for _tipo, _nomes in (
    ("cidade", """
    sao paulo|sampa|rio de janeiro|belo horizonte|curitiba|porto alegre|florianopolis|floripa|salvador|recife
    |fortaleza|joao pessoa|maceio|aracaju|teresina|sao luis|belem|manaus|brasilia|goiania|campo grande|cuiaba
    |vitoria|campinas|santos|guaruja|ubatuba|ilhabela|caraguatatuba|sao sebastiao|maresias|juquehy|bertioga
    |paraty|parati|angra dos reis|ilha grande|buzios|cabo frio|arraial do cabo|petropolis|teresopolis|gramado
    |canela|bento goncalves|campos do jordao|monte verde|holambra|atibaia|sao roque|brotas|socorro
    |aguas de lindoia|serra negra|aguas de sao pedro|pirenopolis|bonito|chapada dos veadeiros|chapada diamantina
    |lencois maranhenses|jericoacoara|jeri|fernando de noronha|noronha|porto de galinhas|maragogi|porto seguro
    |trancoso|itacare|morro de sao paulo|foz do iguacu|ouro preto|tiradentes|sao joao del rei|diamantina
    |capitolio|olimpia|blumenau|pomerode|balneario camboriu|bombinhas|garopaba|praia do rosa|urubici|joinville
    |londrina|maringa|ribeirao preto|sorocaba|jundiai|valinhos|vinhedo|indaiatuba|piracicaba|itu|niteroi
    |paris|londres|roma|veneza|florenca|milao|barcelona|madri|lisboa|amsterda|berlim|praga|viena|budapeste
    |atenas|santorini|dubai|toquio|seul|nova york|nova iorque|new york|miami|orlando|las vegas|los angeles
    |chicago|boston|toronto|vancouver|montreal|cancun|punta cana|buenos aires|santiago|bariloche|mendoza
    |montevideu|punta del este|cartagena|lima|cusco|machu picchu|bali|maldivas|tailandia|italia|franca|portugal
    |espanha|grecia|japao|chile|argentina|uruguai|peru|mexico|canada|eua|estados unidos|europa|irlanda
    |inglaterra|escocia|holanda|suica|alemanha|egito|marrocos|turquia|colombia|nova zelandia|australia
    |africa do sul|caribe|patagonia|disney|nordeste|serra gaucha|amazonia|pantanal
    """),
    ("restaurante", """
    outback|madero|coco bambu|spoleto|habibs|mcdonalds|mc donalds|mequi|burger king|subway|giraffas|applebees
    |abbraccio|paris 6|fogo de chao|olive garden|china in box|ragazzo|vivenda do camarao|bobs|kfc|pizza hut
    |dominos|camarada camarao|bullguer|jeronimo|mania de churrasco|tgi fridays
    """),
    ("cafe", "starbucks|santo grao|the coffee|coffee lab|havanna"),
    ("doces", "kopenhagen|cacau show|bacio di latte|chiquinho sorvetes|milky moo|amorino"),
    ("compras", "ikea|25 de marco|oscar freire|iguatemi|jk iguatemi"),
    ("cultura", """masp|pinacoteca|museu do ipiranga|inhotim|museu do amanha|museu da lingua portuguesa|ccbb
    |instituto tomie ohtake|sesc pompeia|teatro municipal"""),
    ("natureza", "ibirapuera|horto florestal|parque villa-lobos|villa-lobos|serra do mar"),
    ("passeio", """beco do batman|avenida paulista|av paulista|vila madalena|cristo redentor|hopi hari
    |beto carrero|wet n wild|thermas dos laranjais|cidade da crianca|parque da monica"""),
    ("show", "allianz parque|lollapalooza|rock in rio|the town|espaco unimed"),
):
    for _n in _lista(_nomes):
        _CONHECIDOS.setdefault(_n, _tipo)
# Escritos em minúsculas, estes podem ser outra coisa ("rio", "porto"): só valem com maiúscula.
_SO_MAIUSCULA = frozenset(_lista("rio|porto|natal|campos|sul|centro|lapa|liberdade|bonito|socorro|vitoria|lima"
                                 "|santiago|santos|franca|jeri|olimpia|itu|mequi|disney|nordeste|pantanal"
                                 "|amazonia|caribe|europa|paris 6"))
for _n in _lista("rio|porto|natal|lapa|liberdade|centro"):
    _CONHECIDOS.setdefault(_n, "cidade" if _n in ("rio", "porto", "natal") else "passeio")
_RE_CONHECIDO = re.compile(r"(?:" + _alternativas(_CONHECIDOS) + r")(?![\w-])")

# Palavras com maiúscula que não são lugar.
_NAO_LUGAR = frozenset(_lista("""
janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|domingo|segunda
|terca|quarta|quinta|sexta|sabado|segunda-feira|terca-feira|quarta-feira|quinta-feira|sexta-feira|pascoa
|carnaval|reveillon|ano novo|dia dos namorados|dia das maes|dia dos pais|deus|jesus|eu|ela|ele|elas|eles|voce
|nossa|nosso|meu|minha|seu|sua|a gente|gente|ok|oi|ah|tipo|enfim|hoje|ontem|amanha|nada|tudo|sim|nao
"""))
_FERIADOS = frozenset(_lista("natal|pascoa|carnaval|reveillon|ano novo"))

# Termos que ajudam a ligar um lugar ao que ela gosta (sem inventar: só para explicar o porquê).
_SINONIMOS_TIPO: dict[str, frozenset[str]] = {k: frozenset(_lista(v)) for k, v in {
    "restaurante": "restaurante|rodizio|jantar|jantarzinho|almoco|almocar|jantar fora",
    "bar": "bar|barzinho|boteco|drink|drinks|cerveja|chopp|chope|happy hour|vinho|caipirinha",
    "cafe": "cafe|cafeteria|cappuccino|cafezinho|padaria|cafe da manha|brunch",
    "doces": "doce|doces|sorvete|chocolate|bolo|acai|confeitaria|gelato",
    "praia": "praia|mar|litoral|areia|sol|pe na areia",
    "natureza": "natureza|parque|trilha|cachoeira|verde|ar livre|mato|montanha|piquenique|caminhada|caminhar",
    "cultura": "museu|museus|arte|exposicao|historia|teatro|livro|livros|livraria|galeria|peca|cultura|ler",
    "cinema": "cinema|filme|filmes|pipoca",
    "show": "show|shows|musica|festival|balada|dancar|danca|festa|karaoke|samba|pagode",
    "compras": "shopping|compras|feira|brecho|loja|lojas|comprar",
    "hospedagem": "hotel|pousada|viagem|viajar|descansar",
    "cidade": "viagem|viagens|viajar|conhecer lugares|conhecer lugares novos",
    "passeio": "passeio|passear|passeios",
}.items()}
_ATRIBUTOS_TIPO: dict[str, frozenset[str]] = {
    "show": frozenset({"barulho", "barulhento", "lotado", "cheio", "agito", "agitado", "multidao", "aglomeracao"}),
    "bar": frozenset({"barulho", "barulhento", "lotado", "cheio", "agito", "agitado"}),
    "natureza": frozenset({"tranquilo", "calmo", "sossegado", "silencio", "paz"}),
    "praia": frozenset({"tranquilo", "calmo", "sossegado"}),
    "cultura": frozenset({"tranquilo", "calmo", "silencio"}),
    "cafe": frozenset({"tranquilo", "calmo", "aconchegante"}),
}
_COZINHA_TERMOS: dict[str, frozenset[str]] = {k: frozenset(_lista(v)) for k, v in {
    "japones": "japonesa|japones|sushi|temaki|sashimi|comida japonesa|yakisoba",
    "italiano": "italiana|italiano|massa|massas|macarrao|pizza|lasanha|risoto|comida italiana",
    "mexicano": "mexicana|mexicano|taco|tacos|burrito|nachos|guacamole|comida mexicana",
    "arabe": "arabe|esfiha|kibe|quibe|homus|comida arabe",
    "chines": "chinesa|chines|yakisoba|comida chinesa",
    "tailandes": "tailandesa|tailandes|pad thai|comida tailandesa",
    "indiano": "indiana|indiano|curry|comida indiana",
    "coreano": "coreana|coreano|kimchi|comida coreana",
    "peruano": "peruana|peruano|ceviche|comida peruana",
    "vegano": "vegano|vegana|vegetariano|vegetariana",
    "frutos do mar": "frutos do mar|camarao|peixe|marisco|mariscos|lagosta|polvo",
    "pizza": "pizza|pizzas|pizzaria",
    "hamburguer": "hamburguer|burger|lanche|hamburgueria",
    "churrasco": "churrasco|carne|carnes|picanha|churrascaria",
    "sushi": "sushi|japonesa|temaki",
}.items()}
_COZINHA_PALAVRA = {
    "japonesa": "japones", "italiana": "italiano", "mexicana": "mexicano", "chinesa": "chines",
    "tailandesa": "tailandes", "indiana": "indiano", "coreana": "coreano", "peruana": "peruano", "vegana": "vegano",
    "vegetariano": "vegano", "vegetariana": "vegano", "pizzaria": "pizza", "hamburgueria": "hamburguer",
    "churrascaria": "churrasco", "temakeria": "japones", "peixaria": "frutos do mar",
}
# Restrição -> o que pode não combinar (só para avisar e explicar, citando o registro).
_CONFLITOS: dict[str, frozenset[str]] = {k: frozenset(_lista(v)) for k, v in {
    "vegetarian": "churrasco|carne|carnes|picanha|churrascaria|hamburguer|frutos do mar|camarao|peixe|sushi",
    "vegan": "churrasco|carne|carnes|picanha|churrascaria|hamburguer|frutos do mar|camarao|peixe|sushi|queijo"
             "|sorvete|fondue|doce",
    "lactose": "queijo|pizza|sorvete|fondue|leite|gelato|doce",
    "gluten": "pizza|massa|massas|pao|padaria|hamburguer|cerveja|confeitaria",
    "camarao": "camarao|frutos do mar",
    "frutos do mar": "frutos do mar|camarao|peixe|marisco",
    "peixe": "peixe|frutos do mar|sushi|japonesa",
    "amendoim": "doce|doces|confeitaria",
    "alcool": "bar|cerveja|vinho|drink|drinks|chopp",
}.items()}
_GENERICOS = frozenset(_lista("lugar|lugares|lugarzinho|coisa|coisas|comida|comidas|ir|sair|ficar|conhecer|novo|nova"
                              "|bom|boa|muito|tudo|algum|alguma|ela|gosta|ama|adora"))

# Ressalvas que ela fez de um lugar (a palavra é a que está escrita).
_RESSALVAS = frozenset(_lista("""
caro|cara|carissimo|carissima|salgado|salgada|barulhento|barulhenta|barulho|cheio|cheia|lotado|lotada|fila
|longe|demorado|demorada|demora|apertado|apertada|abafado|abafada|sujo|suja|apimentado|apimentada|frio|fria
|gelado|gelada|quente|escuro|escura|perigoso|perigosa|atendimento ruim|mal atendida|mal atendidos
"""))
_RE_RESSALVA = re.compile(r"(?<![\w-])(?:" + _alternativas(_RESSALVAS) + r")(?![\w-])")


# ======================================================================== utilidades

def _campo(obj: Any, nome: str, padrao: Any = None) -> Any:
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


def _quando_futuro(dias: int) -> str:
    return "hoje" if dias == 0 else "amanhã" if dias == 1 else f"em {dias} dias"


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _json_lista(v: Any) -> list:
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v or "[]")
    except (TypeError, ValueError):
        return []
    return x if isinstance(x, list) else []


def _semana(d: date) -> str:
    ano, semana, _ = d.isocalendar()
    return f"{ano}-W{semana:02d}"


def _segunda(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _espacos(s: str) -> str:
    return " ".join(s.split())


_RE_INICIO_CHAVE = re.compile(
    r"^(?:(?:o|a|os|as|um|uma|uns|umas|aquele|aquela|esse|essa|este|esta|nosso|nossa|no|na|ao|do|da)\s+)+")


def chave_lugar(nome: str | None) -> str:
    """'Restaurante Mangiare', 'restaurante mangiare' e 'Mangiare' caem na mesma chave; 'Museu do
    Ipiranga' fica inteiro (o "do" faz parte do nome); 'restaurante mexicano' também."""
    n = normalizar(nome or "")
    n = re.sub(r"[^\w\s&-]", " ", n)
    n = _espacos(_RE_INICIO_CHAVE.sub("", _espacos(n)))
    m = _RE_TIPO.match(n)
    if m:
        resto = n[m.end():].strip()
        primeira = resto.split()[0] if resto.split() else ""
        if resto and primeira not in ("de", "do", "da", "dos", "das", "di", "del") and primeira not in _COZINHAS \
                and primeira not in _NOVO:
            n = resto
    return n


def mesmo_lugar(a: str | None, b: str | None, generico: bool = False) -> bool:
    """Mesma chave; ou (nomes próprios) quase iguais e com no máximo uma palavra de diferença
    ("Coco Bambu" e "Coco Bambu Campinas"). Lugar genérico ("restaurante mexicano") só pela chave."""
    ca, cb = chave_lugar(a), chave_lugar(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if generico:
        return False
    pa, pb = ca.split(), cb.split()
    if abs(len(pa) - len(pb)) > 1 or min(len(ca), len(cb)) < 5:
        return False
    return semelhanca(ca, cb) >= SEMELHANCA_MESMO_LUGAR


def _raiz(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _conjunto(texto: str | None) -> set[str]:
    """Palavras que importam de um texto (sem acento, no singular, sem as genéricas)."""
    return {_raiz(w) for w in termos(texto or "") if w not in _GENERICOS}


def _contem(texto_norm: str, expressoes: frozenset[str] | set[str]) -> set[str]:
    """Quais expressões (normalizadas, de uma ou mais palavras) aparecem no texto, palavra inteira."""
    achadas = set()
    for e in expressoes:
        if re.search(r"(?<![\w-])" + re.escape(e) + r"s?(?![\w-])", texto_norm):
            achadas.add(e)
    return achadas


# ======================================================================== extração (regras)

_NEGACOES = frozenset({"nao", "nunca", "n", "jamais", "nem", "sem"})
_CONDICIONAIS = frozenset({"se", "caso"})
_RE_IRONIA = re.compile(r"\b(?:so\s+que\s+nao|sqn)\b")


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

    def valido(self, ini: int, fim: int) -> bool:
        return not self.negado(ini) and not self.condicional(ini) and self.afirmado(ini, fim)


@dataclass
class _Lugar:
    ini: int                  # posições na frase
    fim: int
    nome: str                 # o que está escrito
    tipo: str                 # código de TIPOS
    genero: str | None = None
    generico: bool = False    # "restaurante mexicano": um tipo de lugar, sem nome próprio
    palavra: str | None = None  # a palavra do tipo, normalizada ("churrascaria"), mesmo fora do nome


@dataclass
class _Ctx:
    nome_dela: str | None
    pessoas: frozenset[str]   # nomes (normalizados) de gente: cadastro e pessoas com relação no texto


@dataclass
class _Achado:
    lugar: _Lugar
    mencao: str
    quem: str
    verbo: int                # onde começa o que disparou (para a ordem e a data)
    opiniao: int | None = None
    reacao: str | None = None
    voltar: bool = False
    futuro: bool = False      # vontade com data por vir ("vamos no X sábado")
    certeza_max: Certeza | None = None


# ---------------------------------------------------------------- reconhecer um lugar numa posição

_RE_ARTIGO = re.compile(
    r"(?:o|a|os|as|um|uma|uns|umas|aquele|aquela|aqueles|aquelas|esse|essa|esses|essas|este|esta|nosso|nossa"
    r"|daquele|daquela|naquele|naquela|nesse|nessa|neste|nesta|pelo|pela)\s+")
_RE_CAP = re.compile(r"[A-ZÀ-Þ][\wÀ-ÿ'’&-]*|[0-9]+")
_RE_CONECTOR_CAP = re.compile(r"(?:\s+(?:de|do|da|dos|das|di|del|d'|&)\s*|\s+)(?=[A-ZÀ-Þ0-9])")
_INDEFINIDOS = frozenset({"um", "uma", "uns", "umas", "num", "numa", "nuns", "numas"})


def _capitalizado(t: str, pos: int) -> tuple[int, int] | None:
    """Um nome com maiúscula a partir de `pos` ("Coco Bambu", "Beco do Batman", "Paris 6")."""
    m = _RE_CAP.match(t, pos)
    if not m or not t[pos].isalpha():
        return None
    fim = m.end()
    n = 1
    while n < 6:
        c = _RE_CONECTOR_CAP.match(t, fim)
        if not c:
            break
        prox = _RE_CAP.match(t, c.end())
        if not prox:
            break
        fim = prox.end()
        n += 1
    while fim > pos and t[fim - 1] in "'’&-":
        fim -= 1
    return pos, fim


def _nome_de_data(nome: str) -> bool:
    """Mês, dia da semana, feriado, pronome: com maiúscula, mas não é nome de lugar."""
    n = _espacos(normalizar(nome))
    primeira = n.split()[0] if n.split() else ""
    return not n or n in _NAO_LUGAR or primeira in _NAO_LUGAR


def _nome_excluido(nome: str, ctx: _Ctx, prep: str | None = None) -> bool:
    """Um nome com maiúscula sem tipo que não é lugar: data, gente do cadastro, o nome dela."""
    n = _espacos(normalizar(nome))
    primeira = n.split()[0] if n.split() else ""
    if _nome_de_data(nome):
        return True
    if n in _FERIADOS and prep in (None, "no", "na", "nesse", "nessa", "neste", "nesta"):
        return True
    if ctx.nome_dela and any(e_nome_dela(p, ctx.nome_dela) for p in nome.split()[:1]):
        return True
    if n in RELACOES or primeira in RELACOES:
        return True
    return n in ctx.pessoas or (primeira in ctx.pessoas and len(n.split()) <= 2 and n not in _CONHECIDOS)


def _minusculas_ok(p: str, pos: int, maximo: int) -> int:
    """Até `maximo` palavras em minúsculas que podem ser nome ("shopping iguatemi", "bar do ze")."""
    fim = atual = pos
    for n in range(maximo):
        if n:
            espaco = re.match(r"\s+", p[atual:])
            if not espaco:
                break
            atual += espaco.end()
        w = _RE_MINUSCULA.match(p, atual)
        if not w:
            break
        palavra = w.group()
        if palavra in _FIM_NOME or palavra in RELACOES or (n == 0 and palavra.isdigit()):
            break
        if len(palavra) >= 5 and re.search(r"(?:ar|er|ir)$", palavra):
            break                              # verbo no infinitivo: "no cinema assistir"
        fim = atual = w.end()
    return fim


def _com_tipo(fr: _Frase, mt: re.Match, ctx: _Ctx, indefinido: bool) -> _Lugar | None:
    """Tipo + o que o identifica: nome ("restaurante Mangiare", "Museu do Ipiranga", "praia de
    Maresias") ou cozinha ("um restaurante mexicano"). Só o tipo ("a praia") não basta."""
    p, t = fr.p, fr.texto
    codigo, genero = _TIPO_PALAVRAS[_espacos(mt.group())]
    ini, pos = mt.start(), mt.end()
    fim: int | None = None
    qualificado = tem_nome = False
    nome_direto: int | None = None              # onde começa o nome próprio que vem colado ao tipo
    for _ in range(2):                          # "japonês", "novo": o que qualifica o tipo
        w = re.match(r"\s+([\w-]+)", p[pos:])
        if not w:
            break
        if w.group(1) in _COZINHAS:
            qualificado = True
            pos += w.end()
            fim = pos
        elif w.group(1) in _NOVO:
            pos += w.end()
        else:
            break
    espaco = re.match(r"\s+", t[pos:])
    if espaco:
        s = pos + espaco.end()
        cap = _capitalizado(t, s)
        if cap and not _nome_de_data(t[cap[0]:cap[1]]):
            if pos == mt.end():
                nome_direto = cap[0]
            fim = pos = cap[1]
            tem_nome = True
        elif pos == mt.end() and not indefinido and not re.match(r"(?:de|do|da|dos|das|di|del)\b", p[s:]):
            f = _minusculas_ok(p, s, 2)
            if f > s:
                fim = pos = f
                tem_nome = True
    for _ in range(3):                          # "do Ipiranga", "de Maresias", "do ze"
        c = re.match(r"\s+(?:de|do|da|dos|das|di|del)\s+", p[pos:])
        if not c:
            break
        s = pos + c.end()
        cap = _capitalizado(t, s)
        if cap and not _nome_de_data(t[cap[0]:cap[1]]):
            fim = pos = cap[1]
            tem_nome = True
            continue
        f = _minusculas_ok(p, s, 2)
        if f > s and not indefinido:
            fim = pos = f
            tem_nome = True
            continue
        break
    if fim is None and t[ini].isupper():
        fim, tem_nome = mt.end(), True             # "o Jardim Botânico", "o Mercadão": o tipo é o nome
    if fim is None or not (tem_nome or qualificado):
        return None
    if nome_direto is not None and t[ini].islower():
        nome = t[nome_direto:fim]                  # "restaurante Mangiare" -> "Mangiare"
    else:
        nome = t[ini:fim]
    return _Lugar(ini, fim, nome.strip(), codigo, genero, generico=not tem_nome, palavra=_espacos(mt.group()))


def _lugar_em(fr: _Frase, pos: int, ctx: _Ctx, *, estrito: bool = False, dica: str | None = None,
              indefinido: bool = False, prep: str | None = None) -> _Lugar | None:
    """O lugar que começa em `pos` (depois da preposição), ou None.

    Vale: tipo + nome/cozinha; um lugar conhecido (cidade, rede, ponto turístico), mesmo em
    minúsculas; e, fora do modo `estrito`, um nome com maiúscula que não é gente nem data."""
    p, t = fr.p, fr.texto
    while pos < len(p) and p[pos].isspace():
        pos += 1
    m = _RE_ARTIGO.match(p, pos)
    if m:
        indefinido = indefinido or m.group().split()[0] in _INDEFINIDOS
        pos = m.end()
    mt = _RE_TIPO.match(p, pos)
    if mt:
        return _com_tipo(fr, mt, ctx, indefinido)
    mk = _RE_CONHECIDO.match(p, pos)
    if mk:
        chave = _espacos(mk.group())
        maiuscula = t[pos].isupper()
        if (maiuscula or chave not in _SO_MAIUSCULA) and not (chave in _FERIADOS and prep in ("no", "na")):
            fim = mk.end()
            cap = _capitalizado(t, pos) if maiuscula else None
            if cap and cap[1] > fim and not _nome_excluido(t[cap[0]:cap[1]], ctx, prep):
                fim = cap[1]
            tipo = _CONHECIDOS[chave]
            return _Lugar(pos, fim, t[pos:fim], tipo, _GENERO_PADRAO.get(tipo))
    if estrito or pos == 0:
        return None                             # no começo da frase, maiúscula não diz que é nome
    cap = _capitalizado(t, pos)
    if cap and not _nome_excluido(t[cap[0]:cap[1]], ctx, prep):
        tipo = dica or "outro"
        return _Lugar(cap[0], cap[1], t[cap[0]:cap[1]], tipo, _GENERO_PADRAO.get(tipo))
    return None


_RE_EM_CIDADE = re.compile(r"\s+(?:em|la\s+em)\s+")


def _cidade_depois(fr: _Frase, fim: int, ctx: _Ctx) -> tuple[str, int] | None:
    """'... no Mangiare em Campinas': a cidade logo depois do lugar."""
    m = _RE_EM_CIDADE.match(fr.p, fim)
    if not m:
        return None
    lug = _lugar_em(fr, m.end(), ctx, prep="em")
    if lug is None or lug.tipo != "cidade" and not (lug.tipo == "outro" and fr.texto[m.end()].isupper()):
        return None
    return lug.nome, lug.fim


_RE_VIAGEM_CIDADE = re.compile(r"\b(?:viagem|viajamos|viajar|viajou|ferias|feriado|lua\s+de\s+mel)\s+"
                               r"(?:pra|para|a|pro|em|de)\s+")


def _cidade_da_frase(fr: _Frase, ctx: _Ctx) -> str | None:
    """A cidade de uma viagem dita na frase ("na viagem pra Gramado, fomos no Café X")."""
    for m in _RE_VIAGEM_CIDADE.finditer(fr.p):
        lug = _lugar_em(fr, m.end(), ctx, dica="cidade", prep="pra")
        if lug is not None and lug.tipo == "cidade":
            return lug.nome
    return None


# ---------------------------------------------------------------- quem fala / quem faz

_ADVERBIOS = frozenset(_lista("ainda|tambem|tb|tbm|ja|sempre|so|muito|mt|mto|nunca|mesmo|ate|hoje|ontem|tanto|"
                              "realmente|super|finalmente|enfim|agora|entao|ai|dai"))
_RE_FALANTE = re.compile(r"(?P<q>[\w-]+)\s+(?:me\s+|nos\s+|pra\s+mim\s+|pra\s+gente\s+)?(?:disse|falou|contou|comentou"
                         r"|explicou|avisou|mencionou|confessou|garantiu|jurou|respondeu|diz|fala|vive\s+dizendo"
                         r"|vive\s+falando)\s+(?:pra\s+mim\s+|pra\s+gente\s+)?que$")
_RE_ME_FALOU = re.compile(r"^(?:me\s+)?(?:disse|falou|contou|comentou|explicou|avisou|mencionou|confessou)\s+que$")


def _quem_antes(fr: _Frase, pos: int, ctx: _Ctx) -> str | None:
    """'ela' | 'eu' | 'nos' | 'outro' | None (sem sujeito) pelo pedaço da oração logo antes de `pos`."""
    antes = fr.p[max(0, pos - 80):pos]
    corte = max(antes.rfind(","), antes.rfind(";"), antes.rfind(":"), antes.rfind("("))
    for m in re.finditer(r"\s(?:mas|porque|pq|pois|entao|so\s+que|quando)\s", antes):
        corte = max(corte, m.start())
    pedaco = antes[corte + 1:].strip()
    original = fr.texto[max(0, pos - 80):pos][corte + 1:].strip()
    palavras = pedaco.split()
    originais = original.split()
    while palavras and palavras[-1] in _ADVERBIOS:
        palavras.pop()
        originais = originais[:-1]
    if not palavras:
        return None
    ultima = palavras[-1]
    duas = " ".join(palavras[-2:])
    if ultima == "ela" or (ctx.nome_dela and originais and e_nome_dela(originais[-1], ctx.nome_dela)):
        return "ela"
    if duas in ("a gente", "nos dois") or ultima == "nos":
        return "nos"
    if ultima == "eu":
        return "eu"
    if ultima == "que":
        texto = " ".join(palavras)
        falante = _RE_FALANTE.search(texto)
        if falante:
            q = falante.group("q")
            if q == "ela" or (ctx.nome_dela and e_nome_dela(q[:1].upper() + q[1:], ctx.nome_dela)):
                return "ela"
            return "eu" if q == "eu" else "outro"
        if _RE_ME_FALOU.search(texto) and len(palavras) <= 4:
            return None                       # "Me disse que quer conhecer..." (sem sujeito = ela)
        return "outro" if re.search(r"\b(?:ele|eles|elas|voce)\s+(?:\w+\s+){0,2}que$", texto) else None
    if ultima in ("ele", "eles", "elas", "voce", "voces", "alguem", "dela", "dele") or ultima in RELACOES \
            or duas in RELACOES:
        return "outro"                         # "a mãe dela quer conhecer", "ele foi"
    if originais and originais[-1][:1].isupper() and normalizar(originais[-1]) not in _NAO_LUGAR:
        return "outro"                         # um nome: "a Mariana quer conhecer"
    return None


# ---------------------------------------------------------------- reações dela

_INTENS = r"(?:(?:muito|mt|mto|super|bem|tao|toda|demais|mo|meio|um\s+pouco|bastante|simplesmente)\s+)?"
_RE_SUJEITO_REACAO = r"(?P<suj>[\w-]+)\s+(?:(?:ja|tambem|tb|simplesmente|realmente)\s+)?"
_RE_REACAO_NEUTRA = re.compile(
    _RE_SUJEITO_REACAO + r"(?P<r>achou\s+(?:ok|normal|mais\s+ou\s+menos|razoavel|so\s+ok|legalzinho|okay)"
    r"|gostou\s+mais\s+ou\s+menos|curtiu\s+mais\s+ou\s+menos)")
_RE_REACAO_NEG = re.compile(
    _RE_SUJEITO_REACAO + r"(?P<r>(?:nao|n)\s+(?:gostou|curtiu|amou)|odiou|detestou"
    r"|ficou\s+" + _INTENS + r"(?:decepcionada|frustrada|entediada|irritada|incomodada)"
    r"|achou\s+" + _INTENS + r"(?:ruim|horrivel|pessim[oa]|chat[oa]|sem\s+graca|frac[oa]|meh|estranh[oa]|fei[oa]"
    r"|nada\s+demais|uma\s+droga|um\s+saco)"
    r"|(?:nao|n)\s+quer\s+(?:voltar|ir\s+de\s+novo|mais\s+ir)|disse\s+que\s+nunca\s+mais\s+(?:volta|vai)"
    r"|nunca\s+mais\s+quer\s+(?:voltar|ir))")
_RE_REACAO_POS = re.compile(
    _RE_SUJEITO_REACAO + r"(?P<r>amou|adorou|gostou|curtiu|se\s+apaixonou|se\s+encantou|se\s+divertiu"
    r"|ficou\s+" + _INTENS + r"(?:encantada|apaixonada|feliz|felizona|radiante|maravilhada|empolgada|boba)"
    r"|achou\s+" + _INTENS + r"(?:lind[oa]s?|incriveis?|maravilhos[oa]s?|perfeit[oa]s?|top|demais|delicios[oa]s?"
    r"|uma\s+delicia|o\s+maximo|sensacional|fof[oa]s?|aconchegante|charmos[oa]|bonit[oa]|otim[oa]|uma\s+graca"
    r"|um\s+charme|um\s+sonho))")
_RE_QUER_VOLTAR = re.compile(
    _RE_SUJEITO_REACAO + r"(?P<r>(?:ja\s+)?(?:quer|ta\s+querendo|falou\s+que\s+quer|disse\s+que\s+quer)\s+"
    r"(?:muito\s+)?(?:voltar|ir\s+de\s+novo))")
_FIM_REACAO = frozenset(_lista("e|mas|que|porque|pq|so|quando|ontem|hoje|la|pra|para|de novo|porem|ai|dai|kk|kkk|rs"))


def _sujeito_ela(fr: _Frase, m: Any, ctx: _Ctx, sem_sujeito: bool = False) -> bool:
    """Quem reagiu foi ela? `sem_sujeito`: na oração dela ("ela foi no X ... e amou"), "e amou" vale."""
    suj = m.group("suj")
    if suj == "ela" or (sem_sujeito and suj in ("e", "mas", "ai", "dai", "ja")):
        return True
    original = fr.texto[m.start("suj"):m.end("suj")]
    return bool(ctx.nome_dela and e_nome_dela(original, ctx.nome_dela))


def _estender(fr: _Frase, fim: int, maximo: int = 3) -> int:
    """Mais até `maximo` palavras depois da reação ("amou o camarão"), sem passar de fronteira."""
    pos = fim
    for _ in range(maximo):
        w = re.match(r"\s+([\w-]+)", fr.p[pos:])
        if not w or w.group(1) in _FIM_REACAO:
            break
        pos += w.end()
    return pos


def _reacao(fr: _Frase, ini: int, fim: int, ctx: _Ctx,
            sem_sujeito: bool = False) -> tuple[int | None, str | None, bool]:
    """(opinião, reação literal, quer voltar) dita por ela entre `ini` e `fim` da frase."""
    p = fr.p
    opiniao: int | None = None
    reacao: str | None = None
    voltar = False
    vistos: list[tuple[int, int]] = []
    for rx, valor in ((_RE_REACAO_NEUTRA, 0), (_RE_REACAO_NEG, -1), (_RE_QUER_VOLTAR, None), (_RE_REACAO_POS, 1)):
        for m in rx.finditer(p, ini, fim):
            # "ela achou caro mas gostou": o sujeito do "gostou" é o "ela" de antes do "mas" (revisão de 07/10).
            continua_dela = bool(re.search(r"(?<![\w-])ela\s+(?:[\w-]+\s+){0,4}?(?:mas|e|porem|so\s+que)\s+$",
                                           p[max(0, m.start("r") - 45):m.start("r")]))
            if not _sujeito_ela(fr, m, ctx, sem_sujeito or continua_dela) or not fr.afirmado(m.start(), m.end()):
                continue

            if any(a <= m.start("r") < b for a, b in vistos):
                continue
            if valor == 1 and fr.negado(m.start("r"), 2):
                continue
            vistos.append((m.start("r"), m.end("r")))
            if valor is None:
                voltar = True
                reacao = reacao or fr.texto[m.start("r"):m.end("r")]
                continue
            literal = fr.texto[m.start("r"):_estender(fr, m.end("r")) if valor else m.end("r")].strip(" ,;")
            if valor == 0:
                reacao = reacao or literal
                continue
            if opiniao is None:
                opiniao, reacao = valor, literal
            elif opiniao != valor:
                return None, reacao, voltar   # gostou e não gostou: fica sem opinião (a tela mostra o trecho)
    return opiniao, (reacao[:MAX_REACAO] if reacao else None), voltar


def _ressalvas(fr: _Frase, ini: int, fim: int) -> list[str]:
    saida: list[str] = []
    for m in _RE_RESSALVA.finditer(fr.p, ini, fim):
        if fr.negado(m.start(), 2):
            continue                          # "não era caro"
        palavra = fr.texto[m.start():m.end()]
        if normalizar(palavra) not in {normalizar(s) for s in saida}:
            saida.append(palavra)
    return saida[:MAX_RESSALVAS]


# ---------------------------------------------------------------- os padrões

_RE_ENTRE = re.compile(
    r"\s+(?:ontem|hoje|hj|anteontem|finalmente|enfim|juntos|juntinhos|(?P<novo>de\s+novo|denovo|novamente|outra\s+vez"
    r"|mais\s+uma\s+vez)|la|tambem|tb|tbm|ja|so|(?P<comigo>comigo|com\s+a\s+gente|com\s+(?:eu|mim))|pela\s+primeira\s+vez"
    r"|cedo|a\s+noite|de\s+noite|a\s+tarde|de\s+tarde|no\s+sabado|no\s+domingo|na\s+sexta|no\s+feriado|no\s+fds"
    r"|no\s+fim\s+de\s+semana|semana\s+passada|sabado|domingo|sexta|um\s+dia|algum\s+dia|logo|muito|mt|tanto)"
    r"(?=\s|$|[,.!?;])")
_RE_PREP = re.compile(
    r"\s+(?P<prep>no|na|nos|nas|num|numa|ao|aos|a|pro|pra|pros|pras|para|em|naquele|naquela|nesse|nessa|neste|nesta"
    r"|ate\s+o|ate\s+a|ate)\s+")


def _apos_verbo(fr: _Frase, pos: int, artigo: bool = False,
                pular: int = 0) -> tuple[int, str | None, bool, bool, bool] | None:
    """Depois do verbo: palavras soltas ("ontem", "de novo", "comigo") e a preposição (ou, com
    `artigo`, nada: "conhecer o X"). `pular`: até quantas palavras podem vir antes da preposição
    ("comemos pizza no X"). (onde começa o lugar, preposição, indefinido, comigo, de novo)."""
    p = fr.p
    comigo = novo = False
    for _ in range(4):
        m = _RE_ENTRE.match(p, pos)
        if not m:
            break
        comigo = comigo or bool(m.group("comigo"))
        novo = novo or bool(m.group("novo"))
        pos = m.end()
    if artigo:
        m = re.match(r"\s+", p[pos:])
        return (pos + m.end(), None, False, comigo, novo) if m else None
    for _ in range(pular + 1):
        m = _RE_PREP.match(p, pos)
        if m:
            prep = m.group("prep")
            return m.end(), prep, prep in _INDEFINIDOS, comigo, novo
        w = re.match(r"\s+([\w-]+)", p[pos:])
        if not w or w.group(1) in _FIM_REACAO or w.group(1) in ("e", "que", "mas", "com"):
            return None
        pos += w.end()
    return None


_DICAS = (
    (re.compile(r"jant|almoc|lanch|comemos|comer\b|rodizio"), "restaurante"),
    (re.compile(r"cafe|brunch"), "cafe"),
    (re.compile(r"sorvete|acai"), "doces"),
    (re.compile(r"drink|chopp?|cerveja|vinho|bebemos"), "bar"),
    (re.compile(r"viaj|viagem|passamos|passou|passar|ferias|feriado|fim\s+de\s+semana|fds"), "cidade"),
    (re.compile(r"dormimos|hospedad|hospedamos"), "hospedagem"),
)


def _dica(verbo: str) -> str | None:
    for rx, tipo in _DICAS:
        if rx.search(verbo):
            return tipo
    return None


_RE_VISITA_NOS = re.compile(
    r"\b(?P<v>fomos|(?:a\s+gente|nos|eu\s+e\s+ela|ela\s+e\s+eu)\s+(?:foi|fomos|foram)|fui\s+com\s+ela|jantamos|almocamos"
    r"|lanchamos|comemos|bebemos|tomamos\s+(?:um\s+|uns\s+|umas\s+)?(?:cafe|cafezinho|sorvete|drinks?|chopps?|chopes?"
    r"|cervejas?|vinho|acai|brunch)|passeamos|curtimos|dormimos|ficamos\s+hospedados|nos\s+hospedamos|levei\s+ela"
    r"|eu\s+levei\s+ela|ela\s+me\s+levou|saimos\s+(?:pra|para)\s+(?:jantar|almocar|comer|beber|dancar|passear))\b")
_RE_CONHECEMOS = re.compile(r"\b(?P<v>conhecemos|visitamos|(?:a\s+gente|nos)\s+(?:conheceu|visitou))\b")
_RE_VIAJAMOS = re.compile(
    r"\b(?P<v>viajamos|(?:a\s+gente|nos)\s+viajou|passamos\s+(?:o\s+|um\s+|uns\s+|a\s+)?(?:dia|dias|fim\s+de\s+semana"
    r"|final\s+de\s+semana|fds|feriado|feriadao|ferias|reveillon|natal|ano\s+novo|carnaval|lua\s+de\s+mel"
    r"|(?:dois|tres|quatro|cinco|\d+)\s+dias))\b")
_RE_VISITA_ELA = re.compile(
    r"\b(?P<v>foi|esteve|tava|estava|jantou|almocou|lanchou|viajou|passou\s+(?:o\s+|um\s+|uns\s+)?(?:dia|dias"
    r"|fim\s+de\s+semana|fds|feriado|ferias))\b")
_RE_CONHECEU = re.compile(r"\b(?P<v>conheceu|visitou)\b")

_ACAO = (r"(?P<a>conhecer|ir\s+(?:conhecer|visitar|ver)|ir|visitar|voltar|viajar|passear|experimentar|provar|jantar"
         r"|almocar|comer|tomar\s+(?:um\s+)?(?:cafe|sorvete|drink|vinho|chopp?)|fazer\s+uma\s+viagem"
         r"|passar\s+(?:o\s+|um\s+|uns\s+)?(?:dia|fim\s+de\s+semana|fds|feriado|ferias|dias))")
_MAIS = r"(?:\s+(?:muito|mt|mto|tanto|demais|bastante|mesmo|tb|tambem|ainda|um\s+dia|algum\s+dia|logo|de\s+novo))*"
_V3 = (r"quer|queria|quis|sempre\s+quis|gostaria\s+de|adoraria|amaria|sonha\s+em|sonhava\s+em"
       r"|tem\s+(?:muita\s+|uma\s+|um\s+)?(?:vontade|sonho)\s+de|tinha\s+vontade\s+de|morre\s+de\s+vontade\s+de"
       r"|(?:ta|esta|anda|vive)\s+(?:doida|louca|doidinha|loucona|doidona|morrendo\s+de\s+vontade|querendo)"
       r"(?:\s+(?:pra|para|de))?|me\s+chamou\s+(?:pra|para)|me\s+convidou\s+(?:pra|para)|pediu\s+(?:pra|para)"
       r"|me\s+pediu\s+(?:pra|para)|vive\s+falando\s+que\s+quer|falou\s+que\s+quer|disse\s+que\s+quer")
_RE_VONTADE_ELA = re.compile(r"\b(?P<v>" + _V3 + r")" + _MAIS + r"\s+" + _ACAO + r"\b")
_RE_VONTADE_EU = re.compile(
    r"\b(?P<v>quero|queria|vou|pretendo|planejo|preciso|tenho\s+que|pensei\s+em|penso\s+em|(?:to|estou|tava|estava)"
    r"\s+pensando\s+em|gostaria\s+de|adoraria|prometi|combinei\s+de|ia)" + _MAIS
    + r"\s+(?P<a>levar|leva-la)(?:\s+(?:a\s+|o\s+)?(?P<obj>ela|[\w-]+))?\b")
_RE_VONTADE_EU_COM = re.compile(
    r"\b(?P<v>quero|queria|vou|pretendo|pensei\s+em|penso\s+em|gostaria\s+de|adoraria)" + _MAIS
    + r"\s+(?P<a>conhecer|ir|visitar|voltar)\b")
_RE_VONTADE_NOS = re.compile(
    r"\b(?P<v>queremos|vamos|combinamos\s+de|marcamos\s+de|ficamos\s+de|planejamos|pretendemos|pensamos\s+em"
    r"|(?:estamos|tamos|tamo)\s+pensando\s+em|precisamos|temos\s+que|(?:a\s+gente|nos)\s+(?:quer|vai|combinou\s+de"
    r"|marcou\s+de|ficou\s+de|pensa\s+em|(?:ta|esta)\s+pensando\s+em|planeja|pretende|precisa|tem\s+que|queria"
    r"|sempre\s+quis))" + _MAIS + r"(?:\s+" + _ACAO + r")?\b")
_RE_OPINIAO = re.compile(
    r"\b(?P<neg>(?:nao|n)\s+)?(?P<v>adora|ama|amou|adorou|curte|curtiu|gosta(?:\s+(?:muito|demais|bastante))?"
    r"|gostou(?:\s+(?:muito|demais))?|(?:e|ficou)\s+apaixonada|odeia|odiou|detesta|detestou)"
    r"(?:\s+(?:do|da|dos|das|de|pelo|pela|pelos|pelas|por))?(?=\s)")
_RE_FAVORITO = re.compile(
    r"\b(?:o|a)\s+(?P<r>(?P<pal>lugar|lugarzinho|restaurante|bar|barzinho|cafe|cafeteria|praia|parque|museu|cidade"
    r"|sorveteria|pizzaria|hamburgueria|doceria|padaria|cinema|shopping|balada|pub|boteco)\s+(?:favorit[oa]"
    r"|preferid[oa]|predilet[oa]|do\s+coracao)\s+dela)\s+(?:e|era|eh|continua\s+sendo)\s+")
_RE_REVERSO = re.compile(
    r"\s*,?\s+(?:que|onde|aonde)\s+(?:(?P<suj>ela|a\s+gente|nos|eu\s+e\s+ela)\s+)?(?:(?:sempre|tanto|muito|ja)\s+)*"
    r"(?P<v>quer\s+(?:muito\s+|tanto\s+)?(?:conhecer|ir|visitar)|queria\s+(?:muito\s+)?(?:conhecer|ir|visitar)"
    r"|sempre\s+quis\s+(?:conhecer|ir)|quis\s+(?:conhecer|ir)|tem\s+vontade\s+de\s+(?:conhecer|ir)"
    r"|queremos\s+(?:conhecer|ir)|fomos|foi|jantamos|almocamos|conhecemos|visitamos|ficamos)\b")
_RE_OPINIAO_ANTES = re.compile(
    r"(?P<suj>[\w-]+)\s+(?P<v>(?:nao|n)\s+(?:gostou|curtiu)(?:\s+(?:muito|nada))?|amou|adorou|gostou(?:\s+muito)?|curtiu"
    r"|odiou|detestou)(?:\s+(?:do|da|dos|das|de))?\s+(?:o\s+|a\s+|os\s+|as\s+)?$")
_RE_VIAGEM = re.compile(r"\b(?P<det>nossa|a|uma|essa|aquela)\s+viagem\s+(?:pra|para|a|pro|ate|em)\s+")
_RE_FUTURO = re.compile(r"\b(?:vai\s+ser|vamos|sera|vai\s+rolar|vai\s+chegar|ta\s+chegando|esta\s+chegando|marcada|marcamos)\b")
_RE_PASSADO = re.compile(r"\b(?:foi|fizemos|tivemos|voltamos|passamos|curtimos)\b")
_RE_COM_ELA = re.compile(r"\s+com\s+(?P<q>ela|[\w-]+)\b")
_RE_CONTINUA = re.compile(r"\s*(?:,\s*e|,|e)\s+(?:(?:depois|tambem|ainda|ai|dai)\s+)?(?:fomos\s+)?"
                          r"(?P<prep>no|na|nos|nas|num|numa|ao|a|pro|pra|em)\s+")
_RE_CONTINUA_SEM_PREP = re.compile(r"\s*(?:,\s*e|,|e)\s+(?:tambem\s+)?")


def _e_ela(fr: _Frase, ini: int, fim: int, ctx: _Ctx) -> bool:
    """A palavra entre `ini` e `fim` é "ela" ou o nome (apelido) dela?"""
    if fr.p[ini:fim] == "ela":
        return True
    return bool(ctx.nome_dela and e_nome_dela(fr.texto[ini:fim], ctx.nome_dela))


def _ja_tem(achados: list[_Achado], lug: _Lugar) -> bool:
    return any(not (lug.fim <= a.lugar.ini or lug.ini >= a.lugar.fim) for a in achados)


def _continuacao(fr: _Frase, base: _Achado, achados: list[_Achado], ctx: _Ctx) -> None:
    """"Fomos no Madero e depois na sorveteria Bacio di Latte", "ela quer conhecer Paris e Roma":
    mais lugares da mesma ação (sem preposição, só lugar com tipo ou conhecido)."""
    fim = base.lugar.fim
    for _ in range(3):
        m = _RE_CONTINUA.match(fr.p, fim)
        if m:
            lug = _lugar_em(fr, m.end(), ctx, dica=base.lugar.tipo if base.lugar.tipo == "cidade" else None,
                            indefinido=m.group("prep") in _INDEFINIDOS, prep=m.group("prep"))
        else:
            m = _RE_CONTINUA_SEM_PREP.match(fr.p, fim)
            lug = _lugar_em(fr, m.end(), ctx, estrito=True) if m else None
        if lug is None or _ja_tem(achados, lug):
            return
        achados.append(_Achado(lug, base.mencao, base.quem, base.verbo, voltar=base.voltar, futuro=base.futuro))
        fim = lug.fim


def _acao_e_lugar(fr: _Frase, m: re.Match, ctx: _Ctx) -> tuple[_Lugar | None, bool]:
    """Depois de "quer conhecer" / "quer ir" / "vamos voltar": o lugar e se é voltar."""
    acao = m.group("a") or ""
    voltar = acao.startswith("voltar")
    artigo = bool(re.match(r"(?:conhecer|visitar|experimentar|provar|ir\s+(?:conhecer|visitar|ver))", acao))
    depois = _apos_verbo(fr, m.end(), artigo=artigo)
    if depois is None:
        return None, voltar
    pos, prep, indef, _, novo = depois
    lug = _lugar_em(fr, pos, ctx, dica=_dica(acao) or ("cidade" if "viag" in acao else None), indefinido=indef,
                    prep=prep)
    return lug, voltar or novo


def _achados_da_frase(fr: _Frase, ctx: _Ctx) -> list[_Achado]:
    p = fr.p
    achados: list[_Achado] = []

    def juntar(a: _Achado) -> None:
        if not _ja_tem(achados, a.lugar):
            achados.append(a)
            _continuacao(fr, a, achados, ctx)

    # 1) Reverso: "o restaurante japonês que fomos semana passada", "o Mangiare, que ela quer conhecer".
    for m in _RE_REVERSO.finditer(p):
        if not fr.afirmado(m.start(), m.end()):
            continue
        v, suj = m.group("v"), m.group("suj")
        lug = _lugar_terminando_em(fr, m.start(), ctx)
        if lug is None:
            continue
        if v.startswith(("quer ", "queria", "sempre quis", "quis", "tem vontade")):
            if suj == "ela":
                mencao, quem = "vontade", "ela"
            elif suj in ("a gente", "nos"):
                mencao, quem = "vontade", "nos"
            else:
                continue
        elif v.startswith("queremos"):
            mencao, quem = "vontade", "nos"
        elif v == "foi":
            if suj == "ela":
                mencao, quem = "visita", "ela"
            elif suj in ("a gente", "nos"):
                mencao, quem = "visita", "nos"
            else:
                continue
        else:
            if suj == "ela":
                continue
            mencao, quem = "visita", "nos"
        achado = _Achado(lug, mencao, quem, lug.ini)
        if mencao == "visita":
            antes = _RE_OPINIAO_ANTES.search(p[max(0, lug.ini - 40):lug.ini])
            if antes and _sujeito_ela(fr, _Deslocado(antes, max(0, lug.ini - 40)), ctx):
                verbo = antes.group("v")
                achado.opiniao = -1 if re.match(r"(?:nao|n)\s|odiou|detestou", verbo) else 1
                ini_v = max(0, lug.ini - 40) + antes.start("v")
                achado.reacao = fr.texto[ini_v:max(0, lug.ini - 40) + antes.end("v")].strip()
                achado.verbo = ini_v
        juntar(achado)

    # 2) Vocês foram: "fomos no X", "jantamos no X", "levei ela no X", "passamos o feriado em X".
    for rx, artigo in ((_RE_VISITA_NOS, False), (_RE_CONHECEMOS, True), (_RE_VIAJAMOS, False)):
        for m in rx.finditer(p):
            if not fr.valido(m.start(), m.end()):
                continue
            comida = bool(re.match(r"(?:jant|almoc|lanch|comemos|bebemos|tomamos)", m.group("v")))
            depois = _apos_verbo(fr, m.end(), artigo=artigo, pular=3 if comida else 0)
            if depois is None:
                continue
            pos, prep, indef, _, _ = depois
            dica = _dica(m.group("v")) or ("cidade" if rx is _RE_VIAJAMOS else None)
            lug = _lugar_em(fr, pos, ctx, dica=dica, indefinido=indef, prep=prep)
            if lug is not None:
                juntar(_Achado(lug, "visita", "nos", m.start()))

    # 3) Ela foi: "ela foi no bar do Zé com as amigas" ("ela foi comigo no X" = vocês foram).
    for rx, artigo in ((_RE_VISITA_ELA, False), (_RE_CONHECEU, True)):
        for m in rx.finditer(p):
            if not fr.valido(m.start(), m.end()) or _quem_antes(fr, m.start(), ctx) != "ela":
                continue
            depois = _apos_verbo(fr, m.end(), artigo=artigo)
            if depois is None:
                continue
            pos, prep, indef, comigo, _ = depois
            lug = _lugar_em(fr, pos, ctx, dica=_dica(m.group("v")), indefinido=indef, prep=prep)
            if lug is None:
                continue
            if re.match(r"\s+(?:comigo|com\s+a\s+gente|com\s+mim)\b", p[lug.fim:]):
                comigo = True
            juntar(_Achado(lug, "visita", "nos" if comigo else "ela", m.start()))

    # 4) Ela quer conhecer / ir / voltar.
    for m in _RE_VONTADE_ELA.finditer(p):
        if not fr.valido(m.start(), m.end()):
            continue
        quem = _quem_antes(fr, m.start(), ctx)
        if quem not in ("ela", None):
            continue
        lug, voltar = _acao_e_lugar(fr, m, ctx)
        if lug is not None:
            certeza = Certeza.INFERENCIA if m.group("v").startswith("vive") else None
            juntar(_Achado(lug, "vontade", "ela", m.start(), voltar=voltar, certeza_max=certeza))

    # 5) Vocês querem / combinaram: "a gente quer conhecer o X", "vamos no X sábado".
    for m in _RE_VONTADE_NOS.finditer(p):
        if not fr.valido(m.start(), m.end()):
            continue
        if m.group("a"):
            lug, voltar = _acao_e_lugar(fr, m, ctx)
        else:
            depois = _apos_verbo(fr, m.end())
            if depois is None:
                continue
            pos, prep, indef, _, novo = depois
            lug, voltar = _lugar_em(fr, pos, ctx, indefinido=indef, prep=prep), novo
        if lug is not None:
            juntar(_Achado(lug, "vontade", "nos", m.start(), voltar=voltar))

    # 6) A sua ideia: "quero levar ela no X", "quero conhecer o X com ela".
    for m in _RE_VONTADE_EU.finditer(p):
        if not fr.valido(m.start(), m.end()) or _quem_antes(fr, m.start(), ctx) not in ("eu", None):
            continue
        fim = m.end()
        if m.group("a") == "levar":
            if not m.group("obj") or not _e_ela(fr, m.start("obj"), m.end("obj"), ctx):
                continue
        elif m.group("obj"):
            fim = m.start("obj")              # "leva-la no X": o que vem depois já é a preposição
        depois = _apos_verbo(fr, fim)
        if depois is None:
            continue
        pos, prep, indef, _, _ = depois
        lug = _lugar_em(fr, pos, ctx, indefinido=indef, prep=prep)
        if lug is not None:
            juntar(_Achado(lug, "vontade", "eu", m.start()))
    for m in _RE_VONTADE_EU_COM.finditer(p):
        if not fr.valido(m.start(), m.end()) or _quem_antes(fr, m.start(), ctx) not in ("eu", None):
            continue
        lug, voltar = _acao_e_lugar(fr, m, ctx)
        if lug is None:
            continue
        com = _RE_COM_ELA.match(p, lug.fim)
        if com and _e_ela(fr, com.start("q"), com.end("q"), ctx):
            juntar(_Achado(lug, "vontade", "eu", m.start(), voltar=voltar))

    # 7) Opinião dela sobre um lugar: "ela adora o Parque Ibirapuera", "ela odeia o shopping X".
    for m in _RE_OPINIAO.finditer(p):
        if not fr.afirmado(m.start(), m.end()) or fr.condicional(m.start()):
            continue
        if _quem_antes(fr, m.start(), ctx) not in ("ela", None):
            continue
        negativo = bool(m.group("neg")) or fr.negado(m.start(), 2) or m.group("v") in ("odeia", "odiou", "detesta",
                                                                                     "detestou")
        lug = _lugar_em(fr, m.end(), ctx, estrito=True)
        if lug is None:
            continue
        juntar(_Achado(lug, "opiniao", "ela", m.start(), opiniao=-1 if negativo else 1,
                       reacao=fr.texto[m.start():m.end()].strip()))

    # 8) "O restaurante favorito dela é o Mangiare".
    for m in _RE_FAVORITO.finditer(p):
        if not fr.valido(m.start(), m.end()):
            continue
        pal = m.group("pal")
        dica = _TIPO_PALAVRAS.get(pal, ("outro", None))[0] if pal not in ("lugar", "lugarzinho", "cidade") else (
            "cidade" if pal == "cidade" else None)
        lug = _lugar_em(fr, m.end(), ctx, dica=dica)
        if lug is not None:
            juntar(_Achado(lug, "opiniao", "ela", m.start(), opiniao=1, reacao=fr.texto[m.start("r"):m.end("r")]))

    # 9) "Nossa viagem para Paraty foi incrível" / "a viagem pra Gramado vai ser em dezembro".
    for m in _RE_VIAGEM.finditer(p):
        if not fr.valido(m.start(), m.end()):
            continue
        lug = _lugar_em(fr, m.end(), ctx, dica="cidade", prep="pra")
        if lug is None:
            continue
        resto = p[lug.fim:]
        if _RE_FUTURO.search(resto):
            juntar(_Achado(lug, "vontade", "nos", m.start(), futuro=True))
        elif _RE_PASSADO.search(resto) or m.group("det") == "nossa" and re.search(r"\bfoi\b", p):
            juntar(_Achado(lug, "visita", "nos", m.start()))
    return achados


@dataclass
class _Deslocado:
    """Um match de um pedaço da frase com as posições trazidas para a frase inteira."""

    m: re.Match
    base: int

    def group(self, nome: int | str) -> str:
        return self.m.group(nome)

    def start(self, nome: int | str = 0) -> int:
        return self.base + self.m.start(nome)

    def end(self, nome: int | str = 0) -> int:
        return self.base + self.m.end(nome)


def _lugar_terminando_em(fr: _Frase, fim: int, ctx: _Ctx) -> _Lugar | None:
    """O lugar escrito logo antes de `fim` ("...do restaurante japonês| que fomos")."""
    inicio = max(0, fim - 70)
    candidatos: list[int] = []
    for m in _RE_TIPO.finditer(fr.p, inicio, fim):
        if m.start() == 0 or not fr.p[m.start() - 1].isalnum():
            candidatos.append(m.start())
    for m in _RE_CONHECIDO.finditer(fr.p, inicio, fim):
        if m.start() == 0 or not fr.p[m.start() - 1].isalnum():
            candidatos.append(m.start())
    for m in re.finditer(r"(?<![\wÀ-ÿ])[A-ZÀ-Þ]", fr.texto[inicio:fim]):
        candidatos.append(inicio + m.start())
    for pos in sorted(set(candidatos)):
        lug = _lugar_em(fr, pos, ctx)
        if lug is not None and lug.fim == fim:
            return lug
    return None


def _data_da_frase(fr: _Frase, datas: list, referencia: date, futuro: bool) -> tuple[date | None, str | None]:
    """A data dita na frase, no tempo certo (ida no passado, plano no futuro). A data só sai com
    precisão de dia; "semana passada" ou "em 2019" ficam só como expressão (nunca um dia que
    ninguém disse)."""
    so_expressao = None
    for d in datas:
        inicio, fim = _campo(d, "inicio"), _campo(d, "fim")
        if inicio is None or fim is None or not (fr.ini <= inicio and fim <= fr.fim):
            continue
        valor = _campo(d, "data")
        if not isinstance(valor, date) or not (futuro and valor >= referencia or not futuro and valor <= referencia):
            continue
        if _valor(_campo(d, "precisao")) in (None, "dia"):
            return valor, _campo(d, "expressao")
        so_expressao = so_expressao or _campo(d, "expressao")
    return None, so_expressao


def _resumo(e: dict) -> str:
    nome = e["nome"]
    mencao, quem, op = e["mencao"], e["quem"], e.get("opiniao")
    if mencao == "visita":
        base = f"Vocês foram: {nome}" if quem == "nos" else f"Ela foi: {nome}"
    elif mencao == "vontade":
        if quem == "ela":
            base = f"Ela quer {'voltar' if e.get('voltar') else 'ir' if e.get('generico') else 'conhecer'}: {nome}"
        elif quem == "nos":
            base = f"Vocês querem {'voltar' if e.get('voltar') else 'ir'}: {nome}"
        else:
            base = f"Você quer levar ela: {nome}"
    else:
        base = f"Ela gosta de: {nome}" if op == 1 else f"Ela não gosta de: {nome}" if op == -1 else f"Ela comentou: {nome}"
    cidade = e.get("cidade")
    if cidade and normalizar(cidade) not in normalizar(nome):
        base += f" ({cidade})"
    if mencao == "visita" and op in (1, -1):
        base += " — ela gostou" if op == 1 else " — ela não gostou"
    if e.get("data") and mencao == "vontade":
        base += f" — {date.fromisoformat(e['data']).strftime('%d/%m')}"
    return base


def _ctx(pessoas: list, conhecimento: Any) -> _Ctx:
    nome_dela = _campo(conhecimento, "nome_dela") if conhecimento is not None else None
    nomes: set[str] = set()
    for c in (_campo(conhecimento, "pessoas") or []) if conhecimento is not None else []:
        for n in [_campo(c, "nome"), *(_campo(c, "apelidos") or [])]:
            if isinstance(n, str) and n.strip():
                nomes.add(_espacos(normalizar(n)))
    for pe in pessoas or []:
        n = _campo(pe, "nome")
        if isinstance(n, str) and n.strip() and (_campo(pe, "relacao") or _campo(pe, "pessoa_id") is not None):
            nomes.add(_espacos(normalizar(n)))
    return _Ctx(nome_dela if isinstance(nome_dela, str) and nome_dela.strip() else None, frozenset(nomes))


def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Lugares no texto, cada um com o `trecho` literal de onde saiu e o nome como está escrito.

    Nada aqui é gravado: a revisão mostra e o usuário confirma. Frase com negação ("nunca fomos no
    X"), condição ("se a gente for"), pergunta, ironia ("sqn"), dúvida ("não sei se ela quer") ou
    fala negada não vira lugar; nem lugar de outra pessoa ("a mãe dela quer conhecer Gramado")."""
    if not isinstance(texto, str) or not texto.strip():
        return []
    ctx = _ctx(list(pessoas or []), conhecimento)
    lista = [_Frase(f, plano(f), ini, fim, trechos_nao_afirmados(f)) for ini, fim, f in frases(texto)]
    saida: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()
    for n, fr in enumerate(lista):
        if fr.texto.rstrip().endswith("?") or _RE_IRONIA.search(fr.p):
            continue
        proxima = lista[n + 1] if n + 1 < len(lista) else None
        if proxima is not None and (proxima.texto.rstrip().endswith("?") or _RE_IRONIA.search(proxima.p)):
            proxima = None
        achados = sorted(_achados_da_frase(fr, ctx), key=lambda a: a.lugar.ini)
        if not achados:
            continue
        cidade_frase = _cidade_da_frase(fr, ctx)
        for i, a in enumerate(achados):
            fim_regiao = achados[i + 1].lugar.ini if i + 1 < len(achados) else len(fr.p)
            trecho_fim = fr.fim
            opiniao, reacao, voltar = a.opiniao, a.reacao, a.voltar
            ressalvas: list[str] = []
            if a.mencao in ("visita", "opiniao"):
                dela = a.quem == "ela"
                op2, reac2, volta2 = _reacao(fr, a.lugar.fim, fim_regiao, ctx, sem_sujeito=dela)
                if opiniao is None and op2 is not None:
                    opiniao, reacao = op2, reac2
                elif reacao is None:
                    reacao = reac2
                voltar = voltar or volta2
                ressalvas = _ressalvas(fr, a.lugar.fim, fim_regiao)
                if opiniao is None and not ressalvas and len(achados) == 1 and proxima is not None \
                        and len(proxima.p.split()) <= 10:
                    op3, reac3, volta3 = _reacao(proxima, 0, len(proxima.p), ctx)
                    res3 = _ressalvas(proxima, 0, len(proxima.p))
                    if op3 is not None or volta3 or res3:
                        opiniao, reacao, voltar = op3, reac3 or reacao, voltar or volta3
                        ressalvas = res3
                        trecho_fim = proxima.fim
            futuro = a.mencao == "vontade"
            data, expressao = _data_da_frase(fr, datas, referencia, futuro)
            if a.mencao == "opiniao":
                data, expressao = None, None
            cidade = None
            if a.lugar.tipo != "cidade":
                depois = _cidade_depois(fr, a.lugar.fim, ctx)
                cidade = depois[0] if depois else cidade_frase
            trecho = texto[fr.ini:trecho_fim]
            certeza = classificar_certeza(trecho)
            if a.certeza_max is not None and certeza != Certeza.INFERENCIA:
                certeza = a.certeza_max
            extra = {
                "tipo": "lugar",
                "trecho": trecho,
                "nome": a.lugar.nome,
                "tipo_lugar": a.lugar.tipo,
                "palavra_tipo": a.lugar.palavra,
                "generico": a.lugar.generico,
                "cidade": cidade if cidade and normalizar(cidade) != normalizar(a.lugar.nome) else None,
                "status": "queremos_ir" if a.mencao == "vontade" else "fomos",
                "mencao": a.mencao,
                "quem": a.quem,
                "opiniao": opiniao if opiniao in (1, -1) else None,
                "reacao": reacao,
                "ressalvas": ressalvas,
                "voltar": bool(voltar),
                "data": data.isoformat() if data else None,
                "data_expressao": expressao,
                "certeza": certeza.value,
            }
            extra["resumo"] = _resumo(extra)
            chave = (chave_lugar(extra["nome"]), a.mencao, a.quem)
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append(extra)
    return saida[:50]


# ======================================================================== gravar (o que foi confirmado)

def _texto_curto(v: Any, maximo: int, erro: str, obrigatorio: bool = True) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if obrigatorio:
            raise ValueError(erro)
        return None
    if not isinstance(v, str):
        raise ValueError(erro)
    v = _espacos(v)
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


_ORDEM_CERTEZA = {"inferencia": 1, "observado": 2, "confirmado": 3}


def _certeza_do_trecho(pedida: Any, trecho: str) -> str:
    """A certeza nunca sobe acima do que o trecho permite (princípio 3)."""
    permitida = classificar_certeza(trecho).value
    if permitida not in _ORDEM_CERTEZA:
        permitida = "observado"
    if not isinstance(pedida, str) or pedida not in _ORDEM_CERTEZA:
        return permitida
    return pedida if _ORDEM_CERTEZA[pedida] <= _ORDEM_CERTEZA[permitida] else permitida


def _tipo_campo(v: Any) -> str:
    if v in (None, ""):
        return "outro"
    if not isinstance(v, str) or v not in TIPOS:
        raise ValueError(ERRO_TIPO)
    return v


def _opiniao_campo(v: Any) -> int | None:
    if v in (None, "", 0):
        return None
    if isinstance(v, bool) or v not in (1, -1):
        raise ValueError(ERRO_OPINIAO)
    return int(v)


def _status_campo(v: Any) -> str:
    if not isinstance(v, str) or v not in STATUS:
        raise ValueError(ERRO_STATUS)
    return v


def _literais(valores: Any, trecho: str, maximo: int) -> list[str]:
    """Só o que está escrito no trecho (reação, ressalvas): o resto é descartado em silêncio."""
    if not isinstance(valores, list):
        return []
    plano_trecho = normalizar(trecho)
    saida = []
    for v in valores:
        if isinstance(v, str) and v.strip() and len(v) <= MAX_REACAO and normalizar(v) in plano_trecho:
            saida.append(_espacos(v))
    return saida[:maximo]


def _mencao_de_extra(e: dict, registro_id: int, referencia: date) -> dict:
    trecho = e["trecho"]
    status = _status_campo(e.get("status") or ("queremos_ir" if e.get("mencao") == "vontade" else "fomos"))
    mencao = e.get("mencao")
    if mencao is not None and mencao not in MENCOES:
        raise ValueError(ERRO_MENCAO)
    quem = e.get("quem")
    if quem is not None and quem not in QUEM:
        raise ValueError(ERRO_QUEM)
    # O usuário pode ter trocado a situação na revisão: a menção acompanha, e a data do texto (que
    # era de outra coisa: "vamos sábado" virou "já fomos") deixa de valer.
    trocou = (status == "fomos" and mencao == "vontade") or (status == "queremos_ir" and mencao in ("visita", "opiniao"))
    if status == "fomos" and mencao not in ("visita", "opiniao"):
        mencao = "visita"
    elif status == "queremos_ir":
        mencao = "vontade"
    quem = quem or ("ela" if mencao == "opiniao" else "nos")
    if mencao == "opiniao":
        quem = "ela"
    opiniao = _opiniao_campo(e.get("opiniao"))
    if mencao == "vontade":
        opiniao = None
    data = _data_campo(e.get("data"))
    if data and mencao == "visita" and date.fromisoformat(data) > referencia and not trocou:
        raise ValueError(ERRO_DATA_FUTURA)
    voltar = e.get("voltar", False)
    if not isinstance(voltar, bool):
        raise ValueError("“voltar” precisa ser verdadeiro ou falso.")
    reacao = _literais([e.get("reacao")], trecho, 1)
    expressao = e.get("data_expressao")
    if trocou:
        data = expressao = None
    return {
        "registro_id": registro_id,
        "mencao": mencao,
        "quem": quem,
        "opiniao": opiniao,
        "reacao": reacao[0] if reacao else None,
        "ressalvas": _literais(e.get("ressalvas"), trecho, MAX_RESSALVAS) if mencao != "vontade" else [],
        "voltar": voltar,
        "data": data,
        "data_expressao": expressao.strip()[:MAX_EXPRESSAO] if isinstance(expressao, str) and expressao.strip()
        and normalizar(expressao) in normalizar(trecho) else None,
        "nome": _texto_curto(e.get("nome"), MAX_NOME, ERRO_NOME),
        "tipo_lugar": _tipo_campo(e.get("tipo_lugar")),
        "palavra_tipo": e["palavra_tipo"] if isinstance(e.get("palavra_tipo"), str)
        and e["palavra_tipo"] in _TIPO_PALAVRAS else None,
        "cidade": _texto_curto(e.get("cidade"), MAX_CIDADE, ERRO_CIDADE, obrigatorio=False),
        "trecho": trecho,
        "certeza": _certeza_do_trecho(e.get("certeza"), trecho),
        "criado_em": referencia,
    }


def _achar_lugar(banco: Any, nome: str, generico: bool) -> int | None:
    chave = chave_lugar(nome)
    linhas = banco.consultar("SELECT id, nome, chave, generico FROM lugares ORDER BY id")
    for l in linhas:
        if l["chave"] == chave:
            return l["id"]
    if generico:
        return None
    for l in linhas:
        if not l["generico"] and mesmo_lugar(l["nome"], nome):
            return l["id"]
    return None


def _novo_lugar(banco: Any, m: dict, generico: bool, criado: date) -> int:
    return banco.inserir("lugares", {
        "nome": m["nome"], "tipo": m["tipo_lugar"], "status": "queremos_ir" if m["mencao"] == "vontade" else "fomos",
        "opiniao": None, "cidade": m["cidade"], "registro_ids": [m["registro_id"]], "criado_em": criado,
        "chave": chave_lugar(m["nome"]), "generico": generico, "juntos": 0, "quem_quer": None, "editado": [],
    })


def _gravar_mencao(banco: Any, m: dict, generico: bool, criado: date) -> tuple[int, bool]:
    """(id do lugar, se a menção é nova). A mesma menção (registro + lugar + tipo + quem) não repete."""
    lugar_id = _achar_lugar(banco, m["nome"], generico)
    if lugar_id is None:
        lugar_id = _novo_lugar(banco, m, generico, criado)
    elif banco.consultar("SELECT 1 FROM lugares_mencoes WHERE lugar_id = ? AND registro_id = ? AND mencao = ? "
                         "AND quem = ? AND trecho = ?",
                         (lugar_id, m["registro_id"], m["mencao"], m["quem"], m["trecho"])):
        return lugar_id, False
    banco.inserir("lugares_mencoes", {**m, "lugar_id": lugar_id})
    return lugar_id, True


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Grava os lugares que o usuário confirmou (o servidor já conferiu tipo e trecho; aqui confere o
    resto, a certeza nunca sobe e só fica a reação que está escrita). ValueError = 400, nada gravado."""
    mencoes = []
    for e in extras:
        if e.get("tipo") != "lugar":
            continue
        generico = e.get("generico", False)
        mencoes.append((_mencao_de_extra(e, registro_id, referencia), generico is True))
    tocados: set[int] = set()
    for m, generico in mencoes:
        lugar_id, _ = _gravar_mencao(banco, m, generico, referencia)
        tocados.add(lugar_id)
    for lugar_id in tocados:
        _recalcular(banco, lugar_id)
    if mencoes:
        _limpar_orfaos(banco)


# ---------------------------------------------------------------- a situação do lugar sai das menções

def _datas_registros(banco: Any, ids: list[int]) -> dict[int, date]:
    if not ids:
        return {}
    marcas = ",".join("?" * len(ids))
    return {l["id"]: _data(l["data_registro"]) for l in
            banco.consultar(f"SELECT id, data_registro FROM registros WHERE id IN ({marcas})", tuple(ids))}


def _recalcular(banco: Any, lugar_id: int) -> None:
    """Nome, tipo, cidade, situação, opinião, quem quer e registros, a partir das menções (menos o
    que o usuário corrigiu). Sem menção nenhuma, o lugar sai (o nome veio de um texto apagado)."""
    linhas = banco.consultar("SELECT * FROM lugares WHERE id = ?", (lugar_id,))
    if not linhas:
        return
    linha = linhas[0]
    mencoes = banco.consultar("SELECT * FROM lugares_mencoes WHERE lugar_id = ? ORDER BY id", (lugar_id,))
    if not mencoes:
        banco.apagar("lugares", lugar_id)
        banco.esquecer_alertas(f"{NOME}:sugestao:{lugar_id}:")
        return
    editado = set(_json_lista(linha["editado"]))
    datas = _datas_registros(banco, sorted({m["registro_id"] for m in mencoes}))

    def quando(m: dict) -> date:
        return _data(m["data"]) or datas.get(m["registro_id"]) or date.min

    novo: dict[str, Any] = {"registro_ids": sorted({m["registro_id"] for m in mencoes})}
    if "nome" not in editado:
        novo["nome"] = mencoes[0]["nome"]
        novo["chave"] = chave_lugar(novo["nome"])
    if "tipo" not in editado:
        especificos = [m["tipo_lugar"] for m in mencoes if m["tipo_lugar"] and m["tipo_lugar"] != "outro"]
        novo["tipo"] = especificos[-1] if especificos else "outro"
    if "cidade" not in editado:
        cidades = [m["cidade"] for m in mencoes if m["cidade"]]
        novo["cidade"] = cidades[-1] if cidades else None
    visitou = any(m["mencao"] in ("visita", "opiniao") for m in mencoes) or bool(linha["visitado_em"])
    if "status" not in editado:
        novo["status"] = "fomos" if visitou else "queremos_ir"
    novo["juntos"] = any(m["mencao"] == "visita" and m["quem"] == "nos" for m in mencoes) or bool(linha["visitado_em"])
    if "opiniao" not in editado:
        com_opiniao = [m for m in mencoes if m["opiniao"] in (1, -1)]
        novo["opiniao"] = max(com_opiniao, key=lambda m: (quando(m), m["id"]))["opiniao"] if com_opiniao else None
    vontades = [m["quem"] for m in mencoes if m["mencao"] == "vontade"]
    novo["quem_quer"] = next((q for q in ("ela", "nos", "eu") if q in vontades), None)

    def igual(k: str, v: Any) -> bool:
        if k == "registro_ids":
            return _json_lista(linha[k]) == v
        if k == "juntos":
            return bool(linha[k]) == bool(v)
        return linha[k] == v

    mudou = {k: v for k, v in novo.items() if not igual(k, v)}
    if mudou:
        banco.atualizar("lugares", lugar_id, mudou, historico=False)


def _limpar_orfaos(banco: Any) -> None:
    """Lugar sem menção (o recurso não abriu quando um registro foi apagado) sai na próxima escrita."""
    for l in banco.consultar("SELECT id FROM lugares WHERE id NOT IN (SELECT lugar_id FROM lugares_mencoes)"):
        _recalcular(banco, l["id"])


# ======================================================================== leitura (a aba e o Hoje)

@dataclass
class _Base:
    """O que a aba e o Hoje consultam, lido uma vez só."""

    d: alertas.Dados
    lugares: list[dict]
    mencoes: dict[int, list[dict]]
    dispensados: set[str]

    @property
    def ref(self) -> date:
        return self.d.referencia

    def data_registro(self, rid: int | None) -> date | None:
        r = self.d.registros.get(rid) if rid is not None else None
        return r["data_registro"] if r else None


def _carregar(banco: Any, referencia: date) -> _Base:
    mencoes: dict[int, list[dict]] = defaultdict(list)
    for m in banco.consultar("SELECT * FROM lugares_mencoes ORDER BY id"):
        mencoes[m["lugar_id"]].append(m)
    lugares = [l for l in banco.consultar("SELECT * FROM lugares ORDER BY id") if mencoes.get(l["id"])]
    return _Base(d=alertas.carregar(banco, referencia), lugares=lugares, mencoes=dict(mencoes),
                 dispensados=set(banco.alertas_dispensados()))


def _citacao(b: _Base, m: dict) -> dict:
    dr = b.data_registro(m["registro_id"])
    return {"registro_id": m["registro_id"], "trecho": m["trecho"], "certeza": m["certeza"] or "observado",
            "data_registro": dr, "quando": _quando(dr, b.ref)}


def _quando_mencao(b: _Base, m: dict) -> date | None:
    return _data(m["data"]) or b.data_registro(m["registro_id"])


def _artigo(lugar: dict) -> str:
    """'o Museu do Ipiranga', 'a praia de Maresias', 'o Coco Bambu', 'Gramado'."""
    nome = lugar["nome"]
    n = normalizar(nome)
    m = _RE_TIPO.match(n)
    if m:
        genero = _TIPO_PALAVRAS[_espacos(m.group())][1]
    elif lugar.get("tipo") in ("cidade", "passeio", "outro", None):
        return nome
    else:
        genero = _GENERO_PADRAO.get(lugar.get("tipo") or "", "")
    return f"{'a' if genero == 'f' else 'o'} {nome}" if genero in ("f", "m") else nome


def _ao(lugar: dict) -> str:
    """'ao Mangiare', 'à praia de Maresias', 'a Gramado' (para 'voltar a')."""
    com = _artigo(lugar)
    if com.startswith("o "):
        return "ao " + com[2:]
    if com.startswith("a "):
        return "à " + com[2:]
    return "a " + com


def _lugar(b: _Base, linha: dict) -> dict:
    ref = b.ref
    mencoes = b.mencoes.get(linha["id"], [])
    visitas = [m for m in mencoes if m["mencao"] == "visita"]
    vontades = [m for m in mencoes if m["mencao"] == "vontade"]
    quando_visitas = [q for q in (_quando_mencao(b, m) for m in visitas) if q]
    visitado = _data(linha["visitado_em"])
    if visitado:
        quando_visitas.append(visitado)
    ultima_visita = max(quando_visitas, default=None)
    planos = [(_data(m["data"]), m) for m in vontades if _data(m["data"]) and _data(m["data"]) >= ref]
    plano = min(planos, key=lambda x: x[0]) if planos else None
    datas_vontade = [q for q in (b.data_registro(m["registro_id"]) for m in vontades) if q]
    ultima_vontade = max(datas_vontade, default=None)
    quer_voltar = any(m["voltar"] and (ultima_visita is None or (_quando_mencao(b, m) or date.min) >= ultima_visita)
                      for m in mencoes)
    ressalvas: list[str] = []
    for m in mencoes:
        for r in _json_lista(m["ressalvas"]):
            if normalizar(r) not in {normalizar(x) for x in ressalvas}:
                ressalvas.append(r)
    reacoes = [{"texto": m["reacao"], "opiniao": m["opiniao"], **_citacao(b, m)} for m in mencoes if m["reacao"]]
    certezas = [m["certeza"] for m in mencoes]
    tipo = linha["tipo"] if linha["tipo"] in TIPOS else "outro"
    datas_todas = [q for q in (b.data_registro(m["registro_id"]) for m in mencoes) if q]
    return {
        "id": linha["id"],
        "nome": linha["nome"],
        "tipo": tipo,
        "tipo_rotulo": TIPOS[tipo][0],
        "emoji": TIPOS[tipo][1],
        "status": linha["status"],
        "opiniao": linha["opiniao"],
        "cidade": linha["cidade"],
        "generico": bool(linha["generico"]),
        "juntos": bool(linha["juntos"]),
        "quem_quer": linha["quem_quer"],
        "registro_ids": sorted({m["registro_id"] for m in mencoes}),
        "editado": _json_lista(linha["editado"]),
        "vezes": len({m["registro_id"] for m in vontades}),
        "visitas": len({m["registro_id"] for m in visitas}) + (1 if visitado and not any(
            q == visitado for q in quando_visitas[:-1]) else 0),
        "ultima_visita": ultima_visita,
        "ultima_visita_quando": _quando(ultima_visita, ref),
        "visitado_em": visitado,
        "plano": {"data": plano[0], "dias": (plano[0] - ref).days, "expressao": plano[1]["data_expressao"],
                  "registro_id": plano[1]["registro_id"]} if plano else None,
        "quer_voltar": quer_voltar,
        "palavras_tipo": sorted({m["palavra_tipo"] for m in mencoes if m["palavra_tipo"]}),
        "ressalvas": ressalvas,
        "reacoes": reacoes,
        "certeza": max(certezas, key=lambda c: _ORDEM_CERTEZA.get(c, 0)) if certezas else "observado",
        "primeira_mencao": min(datas_todas, default=None),
        "ultima_mencao": max(datas_todas, default=None),
        "ultima_vontade": ultima_vontade,
        "quando": _quando(max(datas_todas, default=None), ref),
        "mencoes": [{"id": m["id"], "mencao": m["mencao"], "quem": m["quem"], "opiniao": m["opiniao"],
                     "reacao": m["reacao"], "ressalvas": _json_lista(m["ressalvas"]), "voltar": bool(m["voltar"]),
                     "data": _data(m["data"]), "data_expressao": m["data_expressao"], **_citacao(b, m)}
                    for m in sorted(mencoes, key=lambda m: (b.data_registro(m["registro_id"]) or date.min, m["id"]),
                                    reverse=True)],
    }


# ---------------------------------------------------------------- o que já está registrado sobre ela

@dataclass
class _Gosto:
    """Um gosto dela (itens do núcleo com o mesmo alvo), forte o bastante para pesar."""

    texto: str
    norm: str
    polaridade: int
    item: Item
    restricao: bool = False


def _gostos(b: _Base) -> list[_Gosto]:
    d = b.d
    datas = {i.id: d.data_registro(i) for i in d.itens}
    grupos: dict[str, list[Item]] = defaultdict(list)
    for i in d.ativos:
        if i.tipo in (TipoItem.PREFERENCIA, TipoItem.RESTRICAO) and d.dono(i) is None and (i.alvo or i.resumo):
            grupos[_espacos(normalizar(i.alvo or i.resumo))].append(i)
    saida = []
    for norm, grupo in grupos.items():
        forca = forca_preferencia(grupo, b.ref, datas)
        restricoes = [i for i in grupo if i.tipo == TipoItem.RESTRICAO]
        if restricoes:
            r = max(restricoes, key=lambda i: (datas.get(i.id) or date.min, i.id or 0))
            saida.append(_Gosto(r.alvo or r.resumo, _espacos(normalizar(r.trecho + " " + (r.alvo or r.resumo))),
                                -1, r, restricao=True))
            continue
        if forca not in ("gosta", "comentou_uma_vez", "nao_gosta"):
            continue
        polaridade = -1 if forca == "nao_gosta" else 1
        rep = max([i for i in grupo if (i.polaridade or 1) * polaridade > 0] or grupo,
                  key=lambda i: (_ORDEM_CERTEZA.get(Certeza(i.certeza).value, 0), datas.get(i.id) or date.min,
                                 i.id or 0))
        saida.append(_Gosto(rep.alvo or rep.resumo, norm, polaridade, rep))
    return saida


def _termos_do_lugar(lugar: dict) -> tuple[set[str], str]:
    """Palavras que descrevem o lugar (nome, tipo, cozinha) e o texto normalizado delas."""
    nome = normalizar(lugar["nome"])
    extra: set[str] = set(_SINONIMOS_TIPO.get(lugar["tipo"], ()))
    for palavra in [*re.findall(r"[\w-]+", nome), *lugar.get("palavras_tipo", [])]:
        base = _COZINHA_PALAVRA.get(palavra, palavra)
        extra |= _COZINHA_TERMOS.get(base, frozenset())
        if palavra in _TIPO_PALAVRAS:
            extra.add(palavra)
    for expressao, conjunto in _COZINHA_TERMOS.items():
        if " " in expressao and expressao in nome:
            extra |= conjunto
    texto = " ".join(sorted(extra | {nome}))
    return {_raiz(w) for w in extra | set(termos(nome)) if w not in _GENERICOS}, texto


def _combina(lugar: dict, termos_lugar: set[str], texto_lugar: str, g: _Gosto) -> bool:
    alvo = _conjunto(g.texto)
    if alvo and alvo & termos_lugar:
        return True
    if _contem(g.norm, {e for e in termos_lugar if len(e) > 3}):
        return True
    # atributos ("lugar barulhento", "lugares tranquilos") que um tipo de lugar costuma ter
    atributos = _ATRIBUTOS_TIPO.get(lugar["tipo"], frozenset())
    return bool(atributos and {_raiz(w) for w in termos(g.norm)} & {_raiz(a) for a in atributos})


def _conflito(lugar: dict, termos_lugar: set[str], texto_lugar: str, g: _Gosto) -> bool:
    """Uma restrição dela que pode não combinar com este lugar (vegetariana x churrascaria)."""
    n = g.norm
    for chave_, conflitos in _CONFLITOS.items():
        if chave_ in n and (_contem(texto_lugar, conflitos) or {_raiz(c) for c in conflitos} & termos_lugar):
            return True
    return _combina(lugar, termos_lugar, texto_lugar, g)


def _momento(b: _Base) -> dict | None:
    """O estado mais recente dela, se for difícil e dos últimos 7 dias (sem diagnóstico: só o que
    você registrou, citado)."""
    d, ref = b.d, b.ref
    estados = [(d.quando(i), i.id or 0, i) for i in d.ativos
               if i.tipo == TipoItem.ESTADO and d.dono(i) is None and d.quando(i) is not None and d.quando(i) <= ref]
    if not estados:
        return None
    q, _, ultimo = max(estados, key=lambda x: (x[0], x[1]))
    dias = (ref - q).days
    if dias > DIAS_MOMENTO or (ultimo.polaridade or 0) >= 0:
        return None
    dr = d.data_registro(ultimo)
    return {"texto": ultimo.resumo, "dias": dias, "data": q, "quando": _quando(q, ref),
            "registro_id": ultimo.registro_id, "trecho": ultimo.trecho,
            "certeza": Certeza(ultimo.certeza).value, "data_registro": dr, "bloqueia": dias <= DIAS_MOMENTO_BLOQUEIA}


_FRASE_OCASIAO = {"namoro": "o aniversário de namoro", "mesversario": "o mesversário de vocês"}
_DIAS_POR_OCASIAO = {"aniversario_dela": DIAS_OCASIAO, "namoro": DIAS_OCASIAO, "mesversario": 7}


def _data_especial_perto(b: _Base) -> bool:
    """Mesversário, aniversário dela ou de namoro em até DIAS_DATA_ESPECIAL dias (hoje conta)."""
    return any(oc.tipo in _DIAS_POR_OCASIAO and oc.dias(b.ref) <= DIAS_DATA_ESPECIAL
               for oc in alertas.ocorrencias(b.d, DIAS_DATA_ESPECIAL))


def _ocasiao(b: _Base) -> dict | None:
    """A data de vocês mais perto: aniversário dela ou de namoro em até 21 dias, mesversário em até 7."""
    for oc in alertas.ocorrencias(b.d, DIAS_OCASIAO):
        limite = _DIAS_POR_OCASIAO.get(oc.tipo)
        if limite is None or oc.dias(b.ref) > limite:
            continue
        frase = oc.frase if oc.tipo == "aniversario_dela" and oc.frase else _FRASE_OCASIAO.get(oc.tipo,
                                                                                               "o aniversário dela")
        return {"tipo": oc.tipo, "texto": oc.texto, "frase": frase, "data": oc.data, "dias": oc.dias(b.ref),
                "porque": " ".join(oc.porque), "registro_ids": list(oc.registro_ids)}
    return None


_RE_VIAGEM_EVENTO = re.compile(r"\b(?:viagem|viajar|viajamos|vamos\s+(?:pra|para)|ferias|lua\s+de\s+mel)\b")


def _viagens(b: _Base) -> list[dict]:
    """Viagens registradas nos próximos 60 dias (eventos do núcleo), com o texto para achar a cidade."""
    d, ref = b.d, b.ref
    saida = []
    for i in d.ativos:
        if i.tipo != TipoItem.EVENTO or i.data_evento is None or not (ref <= i.data_evento <= ref + timedelta(
                days=DIAS_VIAGEM)):
            continue
        texto = normalizar(f"{i.trecho} {i.resumo}")
        if _RE_VIAGEM_EVENTO.search(texto):
            precisao = getattr(i.precisao, "value", i.precisao) or "dia"
            saida.append({"item": i, "texto": texto, "data": i.data_evento, "registro_id": i.registro_id,
                          "trecho": i.trecho, "precisao": precisao,
                          "rotulo": _rotulo_data(i.data_evento, precisao, ref)})
    return saida


_NOMES_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
                "novembro", "dezembro")


def _rotulo_data(d: date, precisao: str | None, ref: date) -> str:
    """A data com a precisão que foi dita (seção 16): "novembro", "semana de 02/11", "2027" ou "01/11"."""
    if precisao == "mes":
        return _NOMES_MESES[d.month - 1] + (f" de {d.year}" if d.year != ref.year else "")
    if precisao == "ano":
        return str(d.year)
    if precisao == "semana":
        return f"semana de {_dia(d, ref)}"
    return _dia(d, ref)


@dataclass
class _Contexto:
    gostos: list[_Gosto]
    nao_quer: list[Item]
    momento: dict | None
    ocasiao: dict | None
    viagens: list[dict]
    segunda: date


def _contexto(b: _Base) -> _Contexto:
    d = b.d
    nao_quer = [i for i in d.ativos if i.tipo == TipoItem.DESEJO and d.dono(i) is None and (i.polaridade or 1) < 0]
    return _Contexto(_gostos(b), nao_quer, _momento(b), _ocasiao(b), _viagens(b), _segunda(b.ref))


def _quem_quer(lugar: dict, modo: str) -> str | None:
    """Quem quer ir. "Ela já quer voltar", dito junto com a ida, é dela (a regra só aceita ela)."""
    if lugar["quem_quer"]:
        return lugar["quem_quer"]
    return "ela" if modo == "voltar" else None


def _cita_item(b: _Base, i: Item) -> dict:
    dr = b.d.data_registro(i)
    return {"registro_id": i.registro_id, "trecho": i.trecho, "certeza": Certeza(i.certeza).value,
            "data_registro": dr, "quando": _quando(dr, b.ref)}


def _avaliar(b: _Base, lugar: dict, cx: _Contexto, modo: str) -> dict:
    """Pontos (explicáveis) para sugerir este lugar. Cada motivo diz de onde veio.

    modo: "conhecer" (ela/vocês querem ir), "voltar" (ela quer voltar) ou "rever" (ela amou, faz tempo)."""
    ref = cx.segunda
    pontos = 0
    motivos: list[str] = []
    avisos: list[str] = []
    ids: list[int] = []
    excluido: str | None = None
    mencoes = b.mencoes.get(lugar["id"], [])
    vontades = [m for m in mencoes if m["mencao"] == "vontade" or (modo == "voltar" and m["voltar"])]
    quem = _quem_quer(lugar, modo)
    if modo in ("conhecer", "voltar"):
        if quem == "ela":
            pontos += 3
            motivos.append("ela mesma quer " + ("voltar" if modo == "voltar" else "conhecer"))
        elif quem == "nos":
            pontos += 2
            motivos.append("vocês combinaram ou querem ir")
        elif quem == "eu":
            pontos += 1
            motivos.append("é uma ideia sua")
        if any(m["certeza"] == "confirmado" for m in vontades):
            pontos += 2
            motivos.append("ela disse com as palavras dela 🟢")
        if lugar["vezes"] >= 2:
            pontos += min(3, lugar["vezes"])
            motivos.append(f"apareceu em {lugar['vezes']} registros")
        ultima = max((d for d in (b.data_registro(m["registro_id"]) for m in vontades) if d), default=None)
        if ultima is not None:
            dias = (ref - ultima).days
            if dias <= 30:
                pontos += 2
            elif dias <= 120:
                pontos += 1
            elif dias > 365:
                pontos -= 1
                avisos.append("Faz mais de um ano que ela comentou: talvez valha perguntar se ela ainda quer.")
            motivos.append(f"comentou {tempo_atras(max(0, (b.ref - ultima).days))}")
        ids += [m["registro_id"] for m in sorted(vontades, key=lambda m: m["id"], reverse=True)[:MAX_ORIGENS]]
    else:
        if lugar["opiniao"] == 1:
            pontos += 2
            motivos.append("ela gostou quando vocês foram")
        if any(m["certeza"] == "confirmado" and m["opiniao"] == 1 for m in mencoes):
            pontos += 1
            motivos.append("ela mesma disse que gostou 🟢")
        if lugar["ultima_visita"] is not None:
            motivos.append(f"a última ida foi {tempo_atras((b.ref - lugar['ultima_visita']).days)}")
        ids += [m["registro_id"] for m in mencoes if m["opiniao"] == 1][-MAX_ORIGENS:]
    if lugar["opiniao"] == -1:
        excluido = "ela não gostou"
    if modo in ("voltar", "rever") and lugar["ressalvas"]:
        avisos.append(f"Ressalvas que você registrou dela: {', '.join(lugar['ressalvas'])}.")
    termos_lugar, texto_lugar = _termos_do_lugar(lugar)
    chave = chave_lugar(lugar["nome"])
    for i in cx.nao_quer:
        alvo = normalizar(i.alvo or i.resumo)
        if chave and (chave in alvo or mesmo_lugar(i.alvo, lugar["nome"])):
            excluido = "ela disse que não quer"
            ids.append(i.registro_id)
    combinou = 0
    for g in cx.gostos:
        if g.restricao:
            if lugar["tipo"] in _COMIDA or _conflito(lugar, termos_lugar, texto_lugar, g):
                dr = b.d.data_registro(g.item)
                cita = f"Lembre do que você registrou{(' em ' + _dia(dr, b.ref)) if dr else ''}: “{g.item.trecho.strip()}”"
                if _conflito(lugar, termos_lugar, texto_lugar, g):
                    pontos -= 3
                    avisos.append(cita + " — pode não combinar com este lugar.")
                elif len(avisos) < 2:
                    avisos.append(cita)
            continue
        if g.norm == chave or mesmo_lugar(g.texto, lugar["nome"]):
            if g.polaridade < 0:
                excluido = "ela não gosta"
            continue                       # a opinião sobre o próprio lugar já conta na menção
        if not _combina(lugar, termos_lugar, texto_lugar, g):
            continue
        dr = b.d.data_registro(g.item)
        quando = f" (você registrou em {_dia(dr, b.ref)})" if dr else ""
        if g.polaridade > 0 and combinou < 2:
            combinou += 1
            pontos += 2
            motivos.append(f"combina com o que ela gosta: {g.texto}{quando}")
            if g.item.registro_id is not None:
                ids.append(g.item.registro_id)
        elif g.polaridade < 0:
            pontos -= 3
            avisos.append(f"Atenção ao que você registrou{(' em ' + _dia(dr, b.ref)) if dr else ''}: "
                          f"{_citar(g.item.trecho)}")
    if cx.momento and not cx.momento["bloqueia"]:
        if lugar["tipo"] in _CALMOS:
            pontos += 1
            motivos.append("é um lugar tranquilo, e ela andou num momento difícil")
        elif lugar["tipo"] in _AGITADOS:
            pontos -= 1
    viagem = None
    if modo != "rever" and lugar["tipo"] != "cidade" and lugar["cidade"]:
        cidade = normalizar(lugar["cidade"])
        for v in cx.viagens:
            if re.search(r"(?<![\w-])" + re.escape(cidade) + r"(?![\w-])", v["texto"]):
                viagem = {"cidade": lugar["cidade"], "data": v["data"], "registro_id": v["registro_id"],
                          "trecho": v["trecho"], "dias": (v["data"] - b.ref).days, "precisao": v["precisao"],
                          "rotulo": v["rotulo"]}
                pontos += 3
                motivos.append(f"vocês têm uma viagem para {lugar['cidade']} ({v['rotulo']})")
                if v["registro_id"] is not None:
                    ids.append(v["registro_id"])
                break
    planejado = None
    if lugar["tipo"] == "cidade":
        nome = normalizar(lugar["nome"])
        for v in cx.viagens:
            if re.search(r"(?<![\w-])" + re.escape(nome) + r"(?![\w-])", v["texto"]):
                planejado = {"data": v["data"], "registro_id": v["registro_id"], "texto": "Vocês já têm uma viagem "
                             f"marcada para lá ({v['rotulo']})."}
    if lugar["plano"] and lugar["plano"]["dias"] <= DIAS_PLANO:
        planejado = {"data": lugar["plano"]["data"], "registro_id": lugar["plano"]["registro_id"],
                     "texto": f"Vocês já combinaram de ir ({_dia(lugar['plano']['data'], b.ref)})."}
    return {"pontos": pontos, "motivos": motivos, "avisos": avisos, "excluido": excluido, "viagem": viagem,
            "planejado": planejado, "registro_ids": list(dict.fromkeys(i for i in ids if i is not None))}


def _dispensas(b: _Base) -> dict[int, list[str]]:
    """{lugar_id: [semanas em que você disse "agora não"]} pelas chaves dispensadas do Hoje."""
    saida: dict[int, list[str]] = defaultdict(list)
    for chave in b.dispensados:
        m = re.fullmatch(rf"{NOME}:sugestao:(\d+):(\d{{4}}-W\d{{2}})", chave)
        if m:
            saida[int(m.group(1))].append(m.group(2))
    return saida


def _semanas_entre(a: str, b: str) -> int:
    def segunda(s: str) -> date:
        ano, sem = s.split("-W")
        return date.fromisocalendar(int(ano), int(sem), 1)
    return abs((segunda(b) - segunda(a)).days) // 7


def _candidatos(b: _Base, cx: _Contexto, lugares: list[dict]) -> list[dict]:
    """Os lugares que podem ser a sugestão da semana, cada um com a avaliação, melhor primeiro."""
    semana = _semana(b.ref)
    dispensas = _dispensas(b)
    saida = []
    for lugar in lugares:
        if lugar["status"] == "queremos_ir":
            modo = "conhecer"
        elif lugar["quer_voltar"] and lugar["opiniao"] != -1:
            modo = "voltar"
        elif lugar["opiniao"] == 1 and lugar["ultima_visita"] is not None and \
                (b.ref - lugar["ultima_visita"]).days >= DIAS_VOLTAR:
            modo = "rever"
        else:
            continue
        av = _avaliar(b, lugar, cx, modo)
        if av["excluido"] or av["planejado"]:
            continue
        recusas = dispensas.get(lugar["id"], [])
        if any(_semanas_entre(s, semana) < SEMANAS_DESCANSO for s in recusas):
            continue
        if recusas:
            av["pontos"] -= min(3, len(recusas))
            av["motivos"].append(f"você deixou para depois {_plural(len(recusas), 'vez', 'vezes')}")
        prioridade = {"conhecer": 2, "voltar": 2, "rever": 0}[modo]
        saida.append({"lugar": lugar, "modo": modo, **av, "_ordem": (prioridade, av["pontos"],
                                                                     lugar["ultima_mencao"] or date.min, -lugar["id"])})
    saida.sort(key=lambda c: c["_ordem"], reverse=True)
    return saida


def _bloqueio(b: _Base, cx: _Contexto, lugares: list[dict]) -> str | None:
    """Por que não há sugestão esta semana (ou None): momento dela, já saíram, você disse "agora não"."""
    if cx.momento and cx.momento["bloqueia"]:
        return "momento"
    semana = _semana(b.ref)
    if any(chave.startswith(f"{NOME}:sugestao:") and chave.endswith(f":{semana}") for chave in b.dispensados):
        return "dispensada"
    for lugar in lugares:
        for m in b.mencoes.get(lugar["id"], []):
            if m["mencao"] != "visita" or m["quem"] != "nos":
                continue
            # Sem data dita, vale a do registro, mas só se o texto não fala de outro tempo ("em 2019").
            q = _data(m["data"]) or (b.data_registro(m["registro_id"]) if not m["data_expressao"] else None)
            if q and _semana(q) == semana and q <= b.ref:
                return "saiu"
        if lugar["visitado_em"] and _semana(lugar["visitado_em"]) == semana:
            return "saiu"
    return None


def _um(lugar: dict) -> str:
    """'um restaurante japonês', 'uma pizzaria napolitana' (lugar que é um tipo, sem nome próprio)."""
    m = _RE_TIPO.match(normalizar(lugar["nome"]))
    genero = _TIPO_PALAVRAS[_espacos(m.group())][1] if m else _GENERO_PADRAO.get(lugar.get("tipo") or "", "m")
    return f"{'uma' if genero == 'f' else 'um'} {lugar['nome']}"


def _citar(trecho: str) -> str:
    """“trecho” com ponto final só se o trecho não termina em pontuação (o trecho fica literal)."""
    t = trecho.strip()
    return f"“{t}”" + ("" if re.search(r"[.!?…]$", t) else ".")


def _texto_sugestao(c: dict, cx: _Contexto, ref: date) -> str:
    lugar, modo = c["lugar"], c["modo"]
    nome = _artigo(lugar)
    if lugar.get("generico") and modo == "conhecer":
        quem = _quem_quer(lugar, modo)
        destino = "para o próximo encontro"
        if c["viagem"]:
            v = c["viagem"]
            destino = f"na viagem para {v['cidade']} ({v.get('rotulo') or _dia(v['data'], ref)})"
        elif cx.ocasiao:
            destino = f"para {cx.ocasiao['frase']} ({_quando_futuro(cx.ocasiao['dias'])})"
        porque = {"ela": "ela comentou que quer ir", "nos": "vocês comentaram que querem ir",
                  "eu": "é uma ideia sua"}.get(quem or "", "está na lista de vocês")
        return f"Talvez {_um(lugar)} seja uma boa {destino}: {porque}."
    if modo == "rever":
        return f"Talvez voltar {_ao(lugar)}, que ela gostou, seja uma boa para o próximo encontro."
    if c["viagem"]:
        v = c["viagem"]
        verbo = "voltar" if modo == "voltar" else "conhecer"
        return f"Talvez dê para {verbo} {nome} na viagem para {v['cidade']} ({v.get('rotulo') or _dia(v['data'], ref)})."

    quem = _quem_quer(lugar, modo)
    if modo == "voltar":
        quem_txt = "que ela quer voltar" if quem == "ela" else "que vocês querem voltar"
        if quem == "eu":
            quem_txt = "que você pensou em levar ela de novo"
    else:
        quem_txt = {"ela": "que ela quer conhecer", "nos": "que vocês querem conhecer",
                    "eu": "que você pensou em levar ela"}.get(quem or "", "que está na lista de vocês")
    destino = "para uma próxima viagem de vocês" if lugar["tipo"] == "cidade" else "para o próximo encontro"
    if cx.ocasiao and lugar["tipo"] != "cidade":
        destino = f"para {cx.ocasiao['frase']} ({_quando_futuro(cx.ocasiao['dias'])})"
    return f"Talvez {nome}, {quem_txt}, seja uma boa {destino}."


def _sugestao(b: _Base, cx: _Contexto, lugares: list[dict]) -> tuple[dict | None, str | None]:
    """(a sugestão da semana, por que não há). No máximo uma por semana."""
    bloqueio = _bloqueio(b, cx, lugares)
    if bloqueio:
        return None, bloqueio
    candidatos = _candidatos(b, cx, lugares)
    if not candidatos:
        return None, "sem_lugares"
    c = candidatos[0]
    lugar = c["lugar"]
    ref = b.ref
    mencoes = b.mencoes.get(lugar["id"], [])
    if c["modo"] == "rever":
        origens = [m for m in mencoes if m["opiniao"] == 1]
    else:
        origens = [m for m in mencoes if m["mencao"] == "vontade" or (c["modo"] == "voltar" and m["voltar"])]
    origens = sorted(origens or mencoes, key=lambda m: m["id"], reverse=True)[:MAX_ORIGENS]
    porque = []
    for m in origens:
        dr = b.data_registro(m["registro_id"])
        if m["registro_id"] in b.d.discretos and _reservado(m["trecho"] or ""):
            # A frase do ciclo não é citada fora do 🌙 (seção 20.10).
            porque.append(f"Você registrou{(' em ' + _dia(dr, ref)) if dr else ''} algo sobre este lugar "
                          "(um registro reservado).")
            continue
        porque.append(f"Você registrou{(' em ' + _dia(dr, ref)) if dr else ''}: {_citar(m['trecho'])}")
    if c["motivos"]:
        porque.append("Por que este: " + "; ".join(c["motivos"]) + ".")
    ids = [m["registro_id"] for m in origens] + c["registro_ids"]
    ocasiao = cx.ocasiao if (cx.ocasiao and lugar["tipo"] != "cidade" and c["modo"] != "rever" and not c["viagem"]) \
        else None
    if ocasiao:
        porque.append(ocasiao["porque"])
        ids += ocasiao["registro_ids"]
    return {
        "lugar_id": lugar["id"], "nome": lugar["nome"], "tipo": lugar["tipo"], "tipo_rotulo": lugar["tipo_rotulo"],
        "emoji": lugar["emoji"], "cidade": lugar["cidade"], "modo": c["modo"], "quem_quer": lugar["quem_quer"],
        "texto": _texto_sugestao(c, cx, ref), "porque": " ".join(porque), "pontos": c["pontos"],
        "motivos": c["motivos"], "avisos": c["avisos"],
        "origens": [_citacao(b, m) for m in origens],
        "registro_ids": list(dict.fromkeys(i for i in ids if i is not None)),
        "ocasiao": {k: ocasiao[k] for k in ("texto", "data", "dias")} if ocasiao else None,
        "viagem": c["viagem"], "semana": _semana(ref),
    }, None


def _pistas(b: _Base, gostos: list[_Gosto], lugares: list[dict]) -> dict:
    """O que ela curte e o que é melhor evitar em lugares (das preferências e restrições já
    registradas), sem repetir o que já é um lugar da lista."""
    chaves = {chave_lugar(l["nome"]) for l in lugares}
    curte, evitar = [], []
    for g in gostos:
        cats = set(g.item.categorias or [])
        relevante = cats & {"Lugares", "Viagens"} or _contem(g.norm, set().union(*_SINONIMOS_TIPO.values())) or (
            g.restricao and cats & {"Comidas", "Bebidas", "Saúde"})
        if not relevante or g.norm in chaves or chave_lugar(g.texto) in chaves:
            continue
        entrada = {"texto": g.item.resumo, "alvo": g.texto, "restricao": g.restricao, **_cita_item(b, g.item)}
        (curte if g.polaridade > 0 else evitar).append(entrada)
    return {"curte": curte[:12], "evitar": evitar[:12]}


def _sem_internos(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _sem_internos(v) for k, v in x.items() if not str(k).startswith("_")}
    if isinstance(x, list):
        return [_sem_internos(v) for v in x]
    return x


def painel(banco: Any, referencia: date) -> dict:
    """Tudo o que a aba "Lugares" mostra (datas como `date`: o núcleo aplica para_json)."""
    b = _carregar(banco, referencia)
    lugares = [_lugar(b, l) for l in b.lugares]
    cx = _contexto(b)
    sugestao, bloqueio = _sugestao(b, cx, lugares)
    quer, fomos, evitar_lugares = [], [], []
    for lugar in lugares:
        modo = "conhecer" if lugar["status"] == "queremos_ir" else ("voltar" if lugar["quer_voltar"] else "rever")
        av = _avaliar(b, lugar, cx, modo)
        lugar.update(pontos=av["pontos"], motivos=av["motivos"], avisos=av["avisos"], excluido=av["excluido"],
                     planejado=av["planejado"], viagem=av["viagem"])
        if lugar["status"] == "queremos_ir":
            (evitar_lugares if av["excluido"] else quer).append(lugar)
        else:
            fomos.append(lugar)
    quer.sort(key=lambda l: (-l["pontos"], -(l["ultima_vontade"] or date.min).toordinal(), l["id"]))
    fomos.sort(key=lambda l: ((l["ultima_visita"] or l["ultima_mencao"] or date.min), l["id"]), reverse=True)
    contagem: dict[str, int] = defaultdict(int)
    for lugar in lugares:
        contagem[lugar["tipo"]] += 1
    resposta = {
        "referencia": referencia,
        "semana": _semana(referencia),
        "quer_conhecer": quer,
        "fomos": fomos,
        "evitar_lugares": evitar_lugares,
        "pistas": _pistas(b, cx.gostos, lugares),
        "momento": cx.momento,
        "sugestao": sugestao,
        "sem_sugestao": bloqueio,
        "tipos": {k: {"rotulo": v[0], "emoji": v[1]} for k, v in TIPOS.items()},
        "contagem_tipos": dict(contagem),
        "status": STATUS,
    }
    resposta["vazio"] = not lugares and not any(resposta["pistas"].values())
    # A frase do ciclo de um registro discreto não é citada aqui (seção 20.10).
    return sem_frases_reservadas(_sem_internos(resposta), b.d.discretos)


def lugar(banco: Any, lugar_id: int, referencia: date) -> dict:
    """Um lugar com todas as menções (cada uma com o trecho e o registro)."""
    b = _carregar(banco, referencia)
    linha = next((l for l in b.lugares if l["id"] == lugar_id), None)
    if linha is None:
        raise NaoEncontrado(NAO_ACHEI)
    return sem_frases_reservadas(_sem_internos(_lugar(b, linha)), b.d.discretos)


# ======================================================================== Hoje

def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """No máximo um lugar por semana, como possibilidade, com o porquê e a origem. Sem nada que ela
    queira conhecer (ou um lugar que ela amou para voltar), com um momento difícil dela nos últimos
    3 dias, se vocês já saíram esta semana ou se você disse "agora não": nenhum cartão. E só na quinta
    e na sexta (planejar o fim de semana) ou com uma data de vocês em até 3 dias (20.5.1)."""
    b = _carregar(banco, referencia)
    if not b.lugares:
        return []
    # Nos outros dias o cartão não aparece; a aba Lugares continua mostrando a sugestão.
    if referencia.weekday() not in DIAS_DA_SUGESTAO and not _data_especial_perto(b):
        return []
    lugares = [_lugar(b, l) for l in b.lugares]
    cx = _contexto(b)
    s, _ = _sugestao(b, cx, lugares)
    if s is None:
        return []
    relevancia = 45 + min(20, 2 * max(0, s["pontos"]))
    if s["viagem"]:
        relevancia += 8
    if s["ocasiao"]:
        relevancia += 5
    if referencia.weekday() in (3, 4):          # quinta e sexta: o fim de semana está chegando
        relevancia += 4
    dados = sem_frases_reservadas({k: s[k] for k in ("lugar_id", "nome", "tipo", "tipo_rotulo", "emoji", "cidade", "modo",
                                                     "quem_quer", "motivos", "avisos", "origens", "ocasiao", "viagem",
                                                     "semana")}, b.d.discretos)

    return [cartao(chave=f"sugestao:{s['lugar_id']}:{s['semana']}", nivel="sugestao", texto=s["texto"],
                   porque=s["porque"], relevancia=min(80, relevancia), registro_ids=s["registro_ids"],
                   tipo="lugares_sugestao", dados=dados)]


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


def _linha_lugar(banco: Any, lugar_id: Any) -> dict:
    linhas = banco.consultar("SELECT * FROM lugares WHERE id = ?", (lugar_id,))
    if not linhas or not banco.consultar("SELECT 1 FROM lugares_mencoes WHERE lugar_id = ?", (lugar_id,)):
        raise NaoEncontrado(NAO_ACHEI)
    return linhas[0]


def _rota_painel(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return painel(banco, _hoje_do_pedido(nucleo, pedido))


def _rota_lugar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return lugar(banco, pedido.id, _hoje_do_pedido(nucleo, pedido))


_EDITAVEIS = frozenset({"nome", "tipo", "cidade", "status", "opiniao"})


def _rota_editar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """Correções do usuário: valem mais que o que os registros dizem (ficam em `editado`)."""
    corpo = _corpo(pedido)
    if not corpo or set(corpo) - _EDITAVEIS:
        raise ValueError(ERRO_CAMPO)
    atual = _linha_lugar(banco, pedido.id)
    mudancas: dict[str, Any] = {}
    if "nome" in corpo:
        nome = _texto_curto(corpo["nome"], MAX_NOME, ERRO_NOME)
        chave = chave_lugar(nome)
        if not chave:
            raise ValueError(ERRO_NOME)
        if banco.consultar("SELECT 1 FROM lugares WHERE chave = ? AND id != ?", (chave, pedido.id)):
            raise ValueError(ERRO_REPETIDO)
        mudancas.update(nome=nome, chave=chave)
    if "tipo" in corpo:
        mudancas["tipo"] = _tipo_campo(corpo["tipo"])
    if "cidade" in corpo:
        mudancas["cidade"] = _texto_curto(corpo["cidade"], MAX_CIDADE, ERRO_CIDADE, obrigatorio=False)
    if "status" in corpo:
        mudancas["status"] = _status_campo(corpo["status"])
        if mudancas["status"] == "queremos_ir":
            mudancas["visitado_em"] = None
    if "opiniao" in corpo:
        mudancas["opiniao"] = _opiniao_campo(corpo["opiniao"])
    editado = set(_json_lista(atual["editado"])) | (set(corpo) & _EDITAVEIS)
    mudancas["editado"] = sorted(editado)
    mudancas["atualizado_em"] = _hoje_do_pedido(nucleo, pedido)
    banco.atualizar("lugares", pedido.id, mudancas)
    return {}


def _rota_fomos(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """"Fomos!": o lugar passa para os lugares de vocês (a opinião dela fica para quando você contar)."""
    corpo = _corpo(pedido)
    if set(corpo) - {"data"}:
        raise ValueError(ERRO_CAMPO)
    hoje = _hoje_do_pedido(nucleo, pedido)
    data = _data_campo(corpo.get("data")) or hoje.isoformat()
    if date.fromisoformat(data) > hoje:
        raise ValueError(ERRO_DATA_FUTURA)
    atual = _linha_lugar(banco, pedido.id)
    editado = [c for c in _json_lista(atual["editado"]) if c != "status"]
    with banco.transacao():
        banco.atualizar("lugares", pedido.id, {"visitado_em": data, "editado": editado, "atualizado_em": hoje})
        _recalcular(banco, pedido.id)
    return {}


def _apagar_lugar(banco: Any, lugar_id: int) -> bool:
    if not banco.consultar("SELECT 1 FROM lugares WHERE id = ?", (lugar_id,)):
        return False
    with banco.transacao():
        banco.executar("DELETE FROM lugares_mencoes WHERE lugar_id = ?", (lugar_id,))
        banco.apagar("lugares", lugar_id)
        banco.esquecer_alertas(f"{NOME}:sugestao:{lugar_id}:")
    return True


def _rota_apagar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    _linha_lugar(banco, pedido.id)
    _apagar_lugar(banco, pedido.id)
    lugar_id = pedido.id

    def nas_copias(copia: Any) -> bool | None:
        try:
            return None if _apagar_lugar(copia, lugar_id) else False
        except (NaoEncontrado, OperacaoProibida):
            return False
    nucleo.limpar_copias(nas_copias)
    return {}


def _apagar_mencao(banco: Any, mencao_id: int) -> bool:
    linhas = banco.consultar("SELECT lugar_id FROM lugares_mencoes WHERE id = ?", (mencao_id,))
    if not linhas:
        return False
    with banco.transacao():
        banco.apagar("lugares_mencoes", mencao_id)
        _recalcular(banco, linhas[0]["lugar_id"])
    return True


def _rota_apagar_mencao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    if not _apagar_mencao(banco, pedido.id):
        raise NaoEncontrado(NAO_ACHEI_MENCAO)
    mencao_id = pedido.id

    def nas_copias(copia: Any) -> bool | None:
        try:
            return None if _apagar_mencao(copia, mencao_id) else False
        except (NaoEncontrado, OperacaoProibida):
            return False
    nucleo.limpar_copias(nas_copias)
    return {}


def rotas() -> list:
    return [
        rota("GET", "", _rota_painel),
        rota("GET", "{id}", _rota_lugar),
        rota("PATCH", "{id}", _rota_editar),
        rota("DELETE", "{id}", _rota_apagar),
        rota("POST", "{id}/fomos", _rota_fomos),
        rota("DELETE", "mencoes/{id}", _rota_apagar_mencao),
    ]


# ======================================================================== exportar, importar, apagar

_COLUNAS_MENCAO = ("id", "lugar_id", "registro_id", "mencao", "quem", "opiniao", "reacao", "ressalvas", "voltar", "data",
                   "data_expressao", "nome", "tipo_lugar", "palavra_tipo", "cidade", "trecho", "certeza", "criado_em")


def exportar(banco: Any) -> dict:
    mencoes = []
    com_mencao: set[int] = set()
    for m in banco.consultar("SELECT * FROM lugares_mencoes ORDER BY id"):
        com_mencao.add(m["lugar_id"])
        mencoes.append({k: m[k] for k in _COLUNAS_MENCAO} | {"ressalvas": _json_lista(m["ressalvas"]),
                                                              "voltar": bool(m["voltar"])})
    lugares = []
    for l in banco.consultar("SELECT * FROM lugares ORDER BY id"):
        if l["id"] not in com_mencao:
            continue                             # apagar é apagar: lugar sem registro não sai
        lugares.append({"id": l["id"], "nome": l["nome"], "tipo": l["tipo"], "status": l["status"],
                        "opiniao": l["opiniao"], "cidade": l["cidade"], "registro_ids": _json_lista(l["registro_ids"]),
                        "criado_em": l["criado_em"], "generico": bool(l["generico"]), "juntos": bool(l["juntos"]),
                        "quem_quer": l["quem_quer"], "editado": _json_lista(l["editado"]),
                        "visitado_em": l["visitado_em"]})
    return {"versao": 1, "lugares": lugares, "mencoes": mencoes}


def importar(banco: Any, dados: Any, mapa_registros: dict[int, int]) -> dict:
    """Junta sem duplicar: só menções de registros que vieram (ou já estavam) no diário e cujo
    trecho está no texto; o lugar casa pelo nome. Correções do arquivo só valem para lugar novo
    (as daqui mandam). Linha estranha é ignorada."""
    if not isinstance(dados, dict):
        raise ValueError("Os dados de lugares no arquivo não estão no formato certo.")
    mapa = {int(k): v for k, v in mapa_registros.items()}
    lugares_arquivo = {l["id"]: l for l in (dados.get("lugares") or []) if isinstance(l, dict)
                       and isinstance(l.get("id"), int)}
    novos = ignorados = 0
    lugares_novos: set[int] = set()
    tocados: set[int] = set()
    origem: dict[int, int] = {}
    antes = {l["id"] for l in banco.consultar("SELECT id FROM lugares")}
    for m in dados.get("mencoes") or []:
        try:
            if not isinstance(m, dict):
                ignorados += 1
                continue
            rid = mapa.get(m.get("registro_id")) if isinstance(m.get("registro_id"), int) else None
            trecho = m.get("trecho")
            if rid is None or not isinstance(trecho, str) or not trecho.strip():
                ignorados += 1
                continue
            texto = banco.consultar("SELECT texto, data_registro FROM registros WHERE id = ?", (rid,))
            if not texto or trecho not in texto[0]["texto"]:
                ignorados += 1
                continue
            referencia = _data(texto[0]["data_registro"]) or date.today()
            extra = {**m, "tipo": "lugar", "status": "queremos_ir" if m.get("mencao") == "vontade" else "fomos"}
            linha = _mencao_de_extra(extra, rid, referencia)
            if _data(m.get("criado_em")):
                linha["criado_em"] = _data(m.get("criado_em"))
            do_arquivo = lugares_arquivo.get(m.get("lugar_id"))
            generico = bool(do_arquivo.get("generico")) if do_arquivo else False
            lugar_id, nova = _gravar_mencao(banco, linha, generico, linha["criado_em"])
            tocados.add(lugar_id)
            if isinstance(m.get("lugar_id"), int):
                origem.setdefault(lugar_id, m["lugar_id"])
            if lugar_id not in antes:
                lugares_novos.add(lugar_id)
            if nova:
                novos += 1
        except (TypeError, ValueError, AttributeError):
            ignorados += 1
    for lugar_id in lugares_novos:
        do_arquivo = lugares_arquivo.get(origem.get(lugar_id))
        if not do_arquivo:
            continue
        try:
            editado = [c for c in _json_lista(do_arquivo.get("editado")) if c in _EDITAVEIS]
            mudancas: dict[str, Any] = {}
            for c in editado:
                if c == "nome":
                    mudancas["nome"] = _texto_curto(do_arquivo.get("nome"), MAX_NOME, ERRO_NOME)
                    mudancas["chave"] = chave_lugar(mudancas["nome"])
                elif c == "tipo":
                    mudancas["tipo"] = _tipo_campo(do_arquivo.get("tipo"))
                elif c == "cidade":
                    mudancas["cidade"] = _texto_curto(do_arquivo.get("cidade"), MAX_CIDADE, ERRO_CIDADE, False)
                elif c == "status":
                    mudancas["status"] = _status_campo(do_arquivo.get("status"))
                elif c == "opiniao":
                    mudancas["opiniao"] = _opiniao_campo(do_arquivo.get("opiniao"))
            if editado:
                mudancas["editado"] = sorted(editado)
            if _data(do_arquivo.get("visitado_em")):
                mudancas["visitado_em"] = _data(do_arquivo.get("visitado_em"))
            if mudancas:
                banco.atualizar("lugares", lugar_id, mudancas, historico=False)
        except (TypeError, ValueError, AttributeError):
            ignorados += 1
    for lugar_id in tocados:
        _recalcular(banco, lugar_id)
    if novos:
        texto = f"{_plural(novos, 'menção', 'menções')} de lugares"
        if lugares_novos:
            texto += f" ({_plural(len(lugares_novos), 'lugar novo', 'lugares novos')})"
    else:
        texto = "Nada novo em Lugares"
    if ignorados:
        texto += f" ({_plural(ignorados, 'linha ignorada', 'linhas ignoradas')})"
    return {"novos": novos, "lugares_novos": len(lugares_novos), "texto": texto + ".", "ignorados": ignorados}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """O registro ainda existe: as menções dele saem, e cada lugar refaz a situação pelo que sobrou
    (sem menção nenhuma, o lugar sai também, com as chaves de cartão que o citavam)."""
    afetados = [l["lugar_id"] for l in banco.consultar(
        "SELECT DISTINCT lugar_id FROM lugares_mencoes WHERE registro_id = ?", (registro_id,))]
    banco.executar("DELETE FROM lugares_mencoes WHERE registro_id = ?", (registro_id,))
    for lugar_id in afetados:
        _recalcular(banco, lugar_id)
    _limpar_orfaos(banco)


__all__ = ["ESQUEMA", "ESQUEMA_VERSAO", "NOME", "ROTULO", "STATUS", "TIPOS", "TIPOS_EXTRAS", "ao_apagar_registro",
           "cartoes_hoje", "chave_lugar", "exportar", "extrair", "gravar_extras", "importar", "lugar", "mesmo_lugar",
           "painel", "rotas"]
