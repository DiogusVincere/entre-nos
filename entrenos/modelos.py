"""Modelos de dados compartilhados por todo o app.

Este arquivo é o CONTRATO entre os módulos: regras, motor, banco, alertas,
perfil, busca e servidor falam através destas classes. Mudou aqui, mude em
todos (e no DESIGN.md).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class Certeza(str, Enum):
    """Grau de confiança de uma informação (seção 17 da especificação)."""

    CONFIRMADO = "confirmado"   # 🟢 ela disse explicitamente
    OBSERVADO = "observado"     # 🟡 comportamento ou situação registrada
    INFERENCIA = "inferencia"   # 🔵 hipótese (minha ou do app)
    INCERTO = "incerto"         # 🔴 antiga ou contraditória


class TipoItem(str, Enum):
    FATO = "fato"                        # informação geral sobre ela ou alguém
    PREFERENCIA = "preferencia"          # gosta / não gosta (alvo + polaridade)
    RESTRICAO = "restricao"              # alergia, não pode, não come
    EVENTO = "evento"                    # acontecimento com data (futuro ou passado)
    DATA_IMPORTANTE = "data_importante"  # aniversário e outras datas que se repetem
    ESTADO = "estado"                    # como ela está agora (preocupada, cansada...)
    PADRAO = "padrao"                    # tendência relatada ("fica nervosa antes de apresentações")
    APOIO = "apoio"                      # o que ajuda (+1) / o que ela não quer (-1) em momentos difíceis
    PENDENCIA = "pendencia"              # assunto em aberto para perguntar depois
    DESEJO = "desejo"                    # quer, sonha, gostaria (inclui presente desejado)
    PESSOA_INFO = "pessoa_info"          # quem é uma pessoa, relação com ela
    NOTA = "nota"                        # frase sem estrutura reconhecida (nunca se perde nada)


class Precisao(str, Enum):
    DIA = "dia"
    SEMANA = "semana"
    MES = "mes"
    ANO = "ano"


# Categorias da seção 1 da especificação. A ordem é a de exibição.
CATEGORIAS: list[str] = [
    "Família", "Amigos", "Trabalho", "Estudos", "Saúde", "Viagens",
    "Datas importantes", "Preferências", "Comidas", "Bebidas", "Filmes",
    "Séries", "Música", "Livros", "Hobbies", "Lugares", "Presentes", "Sonhos",
    "Objetivos", "Medos", "Preocupações", "Coisas que deixam feliz",
    "Coisas que incomodam", "Experiências importantes", "Infância",
    "Relacionamento", "Conversas importantes", "Situações pendentes",
    "Eventos futuros", "Pessoas importantes", "Aniversários",
    "Memórias compartilhadas",
]


@dataclass
class DataEncontrada:
    """Uma data achada no texto. O Python resolve; a IA (quando houver) nunca calcula data."""

    expressao: str                  # trecho literal ("dia 20", "sexta que vem")
    inicio: int                     # posição no texto original
    fim: int
    data: date | None               # melhor palpite; None quando não deu para resolver
    precisao: Precisao = Precisao.DIA
    ambigua: bool = False           # True quando há mais de uma leitura razoável
    alternativas: list[date] = field(default_factory=list)  # as leituras possíveis (inclui `data`)
    recorrente: bool = False        # repete todo ano (aniversário, data comemorativa)
    passado: bool = False           # a data já passou em relação à referência


@dataclass
class PessoaEncontrada:
    """Uma pessoa mencionada no texto (a namorada é implícita e não aparece aqui)."""

    trecho: str                     # trecho literal ("a mãe dela", "Mariana")
    inicio: int
    fim: int
    nome: str | None = None         # "Mariana"
    relacao: str | None = None      # "mãe", "melhor amiga", "chefe"
    pessoa_id: int | None = None    # preenchido quando casou com alguém já cadastrado
    nova: bool = True               # False quando pessoa_id foi encontrado


@dataclass
class Item:
    """Uma informação estruturada extraída de um registro."""

    tipo: TipoItem
    resumo: str                     # frase curta gerada por modelo de frase, nunca inventada
    trecho: str                     # frase LITERAL do registro de onde saiu (substring do texto)
    certeza: Certeza
    categorias: list[str] = field(default_factory=list)  # a primeira é a principal; nomes de CATEGORIAS
    importancia: int = 2            # 1 baixa, 2 normal, 3 alta
    data_evento: date | None = None
    data_expressao: str | None = None
    precisao: Precisao | None = None
    recorrente: bool = False
    pessoa: str | None = None       # de quem é (nome ou relação). None = dela (a namorada)
    pessoa_id: int | None = None    # None = dela
    alvo: str | None = None         # objeto da preferência/desejo/restrição ("tulipas", "sushi")
    polaridade: int | None = None   # +1 gosta/ajuda, -1 não gosta/não ajuda
    lembrar_em: date | None = None  # pendência: quando vale perguntar
    status: str = "ativo"           # ativo | resolvido | mudou | descartado
    id: int | None = None
    registro_id: int | None = None
    criado_em: datetime | None = None


@dataclass
class Relacao:
    """Ligação entre o que acabou de ser registrado e algo antigo (detector de contexto)."""

    tipo: str                       # "contexto" | "contradicao" | "mesma_pessoa"
    motivo: str                     # explicação que cita a origem ("Em 12/09 você registrou que...")
    item_novo: int | None = None    # índice do item na Proposta (antes de gravar) ou id (depois)
    item_antigo_id: int | None = None
    registro_antigo_id: int | None = None
    sugestao: str | None = None     # sempre como possibilidade ("Talvez...")
    status: str = "pendente"        # pendente | confirmada | descartada
    id: int | None = None


@dataclass
class Proposta:
    """O que o motor entendeu de um texto. Nada é gravado sem o usuário revisar."""

    texto: str
    referencia: date                # data do registro, usada para resolver "amanhã", "dia 20"
    itens: list[Item] = field(default_factory=list)
    pessoas: list[PessoaEncontrada] = field(default_factory=list)
    datas: list[DataEncontrada] = field(default_factory=list)
    categorias: list[str] = field(default_factory=list)
    relacoes: list[Relacao] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)   # ex.: data ambígua, pessoa nova
    motor: str = "regras"
    # O que os recursos (entrenos/recursos/, DESIGN 20.0) acharam no texto, por recurso:
    # {"presentes": [{tipo, resumo, trecho, ...}]}. `trecho` é sempre literal. Nunca vai para a IA.
    extras: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class Alerta:
    """Uma linha da página "Hoje"."""

    chave: str                      # estável, para dispensar ("evento:12:d3")
    nivel: str                      # "importante" | "lembrete" | "sugestao"
    texto: str
    porque: str                     # de onde veio ("você registrou em 12/09 que...")
    relevancia: int                 # 0-100; decide o que aparece
    data: date | None = None
    registro_ids: list[int] = field(default_factory=list)


@dataclass
class Resposta:
    """Resposta da busca inteligente. Só usa o que está gravado; nunca inventa."""

    pergunta: str
    texto: str                      # resumo por modelo de frase, ou "Não encontrei essa informação."
    encontrados: list[dict[str, Any]] = field(default_factory=list)
    motor: str = "regras"


# ---------------------------------------------------------------- JSON

def para_json(obj: Any) -> Any:
    """Converte dataclasses, enums, datas e listas em algo que o json.dumps aceita."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: para_json(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: para_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [para_json(v) for v in obj]
    return obj


def _data(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _data_hora(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def item_de_dict(d: dict[str, Any]) -> Item:
    """Reconstrói um Item vindo do navegador ou do banco. Ignora chaves desconhecidas."""
    precisao = d.get("precisao")
    return Item(
        tipo=TipoItem(d["tipo"]),
        resumo=str(d.get("resumo") or ""),
        trecho=str(d.get("trecho") or ""),
        certeza=Certeza(d.get("certeza") or Certeza.OBSERVADO.value),
        categorias=[c for c in (d.get("categorias") or []) if c in CATEGORIAS],
        importancia=int(d.get("importancia") or 2),
        data_evento=_data(d.get("data_evento")),
        data_expressao=d.get("data_expressao"),
        precisao=Precisao(precisao) if precisao else None,
        recorrente=bool(d.get("recorrente")),
        pessoa=d.get("pessoa"),
        pessoa_id=d.get("pessoa_id"),
        alvo=d.get("alvo"),
        polaridade=d.get("polaridade"),
        lembrar_em=_data(d.get("lembrar_em")),
        status=d.get("status") or "ativo",
        id=d.get("id"),
        registro_id=d.get("registro_id"),
        criado_em=_data_hora(d.get("criado_em")),
    )


def relacao_de_dict(d: dict[str, Any]) -> Relacao:
    return Relacao(
        tipo=d["tipo"],
        motivo=str(d.get("motivo") or ""),
        item_novo=d.get("item_novo"),
        item_antigo_id=d.get("item_antigo_id"),
        registro_antigo_id=d.get("registro_antigo_id"),
        sugestao=d.get("sugestao"),
        status=d.get("status") or "pendente",
        id=d.get("id"),
    )


def pessoa_encontrada_de_dict(d: dict[str, Any]) -> PessoaEncontrada:
    return PessoaEncontrada(
        trecho=str(d.get("trecho") or ""),
        inicio=int(d.get("inicio") or 0),
        fim=int(d.get("fim") or 0),
        nome=d.get("nome"),
        relacao=d.get("relacao"),
        pessoa_id=d.get("pessoa_id"),
        nova=bool(d.get("nova", True)),
    )


__all__ = [
    "CATEGORIAS", "Alerta", "Certeza", "DataEncontrada", "Item", "PessoaEncontrada",
    "Precisao", "Proposta", "Relacao", "Resposta", "TipoItem", "asdict",
    "item_de_dict", "para_json", "pessoa_encontrada_de_dict", "relacao_de_dict",
]
