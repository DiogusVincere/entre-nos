"""Banco do app: SQLite em memória, carregado de/para bytes (DESIGN.md, seções 14 e 17.4).

O arquivo em disco é do Cofre (criptografado); aqui só existe a cópia em memória. Toda
escrita termina chamando `ao_salvar(self.para_bytes())`, que o servidor liga ao Cofre.
`para_bytes` compacta o índice de busca e faz VACUUM: o que foi apagado some dos bytes.
O histórico guarda só o que aconteceu ("registro #12 apagado"), nunca o conteúdo.

Segredos da IA (a chave de cada IA e o verificador do PIN da IA) ficam só em `config()`,
para uso interno: `exportar()` e o histórico nunca os trazem.

Restaurar e importar (seção 18.2): `conferir_integridade` e `resumo` servem para conferir um
backup antes de trocar o diário; `importar_json` junta ao diário o JSON de `exportar()` sem
duplicar, tudo numa transação (na prévia, a transação é desfeita e nada é gravado).
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import unicodedata
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from types import EllipsisType
from typing import Any, Callable, Iterable, Iterator

from . import config as cfg
from .cofre import verificador_valido
from .modelos import (CATEGORIAS, Certeza, Item, PessoaEncontrada, Precisao, Relacao, TipoItem,
                      item_de_dict, para_json, pessoa_encontrada_de_dict, relacao_de_dict)
from .texto import frases, normalizar, termos

ESQUEMA_VERSAO = 3
DADOS_DE_VERSAO_NOVA = "Estes dados foram gravados por uma versão mais nova do Entre Nós."
STATUS_ITEM = ("ativo", "resolvido", "mudou", "descartado")
STATUS_RELACAO = ("pendente", "confirmada", "descartada")
TIPOS_IA = ("ollama", "openai")
IA_TEMPO_PADRAO = 120
IA_TEMPO_MINIMO = 5
IA_TEMPO_MAXIMO = 600
# "url_exata" (opcional, só quando True): o endereço de "Outro" vale como foi digitado, sem ganhar /v1.
CAMPOS_IA = ("provedor", "url", "modelo", "tipo", "chave", "url_exata")

CONFIG_PADRAO: dict[str, Any] = {
    "nome_dela": None,
    "aniversario_dela": None,
    "inicio_namoro": None,
    "antecedencias": list(cfg.ANTECEDENCIAS_PADRAO),
    "minutos_bloqueio": cfg.MINUTOS_BLOQUEIO_PADRAO,
    "pergunta_ambigua": True,
    # IA sob demanda (seção 17): {provedor, url, modelo, tipo, chave} ou None.
    "ia_principal": None,
    "ia_reserva": None,
    "ia_pin": None,                    # verificador scrypt do PIN da IA (cofre.criar_verificador)
    "ia_tempo_limite": IA_TEMPO_PADRAO,
}
_CONFIG_DATAS = frozenset({"aniversario_dela", "inicio_namoro"})
_RE_CHAVE_CONFIG = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RE_PROVEDOR = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_RE_CHAVE_IA = re.compile(r"[\x21-\x7e]{1,4096}")      # ASCII visível, sem espaço (vai num cabeçalho HTTP)
_RE_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
# Ajustes do motor antigo (antes da seção 17): convertidos na migração v2 -> v3 e recusados depois.
_RE_MOTOR_ANTIGO = re.compile(r"motor(?:_.*)?")
_OLLAMA_ANTIGO = "http://127.0.0.1:11434"

_COLUNAS_ITEM = ("tipo", "resumo", "trecho", "certeza", "categorias", "importancia",
                 "data_evento", "data_expressao", "precisao", "recorrente", "pessoa",
                 "pessoa_id", "alvo", "polaridade", "lembrar_em", "status")
_EDITAVEIS_ITEM = frozenset(_COLUNAS_ITEM)
_EDITAVEIS_PESSOA = frozenset({"nome", "relacao", "apelidos", "observacoes", "aniversario"})
_CAMPOS_BUSCA_ITEM = frozenset({"resumo", "trecho", "alvo", "pessoa", "pessoa_id"})

_MAX_TERMOS = 40          # termos numa consulta de busca (o contexto manda textos inteiros)
_RE_ARTIGO = re.compile(r"^(?:a|o|as|os)\s+")
_RE_DONO = re.compile(r"\s+(?:dela|dele)$")
_RE_PARENTESE = re.compile(r"\s*\(.*?\)\s*")

_ESQUEMA_V1 = """
CREATE TABLE config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE registros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em TEXT NOT NULL,
    data_registro TEXT NOT NULL,
    texto TEXT NOT NULL,
    contexto TEXT,
    humor TEXT
);
CREATE TABLE pessoas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT,
    relacao TEXT,
    apelidos TEXT NOT NULL DEFAULT '[]',
    observacoes TEXT NOT NULL DEFAULT '',
    aniversario TEXT,
    criado_em TEXT NOT NULL,
    automatica INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registro_id INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    resumo TEXT NOT NULL,
    trecho TEXT NOT NULL,
    certeza TEXT NOT NULL,
    categorias TEXT NOT NULL DEFAULT '[]',
    importancia INTEGER NOT NULL DEFAULT 2,
    data_evento TEXT,
    data_expressao TEXT,
    precisao TEXT,
    recorrente INTEGER NOT NULL DEFAULT 0,
    pessoa TEXT,
    pessoa_id INTEGER,
    alvo TEXT,
    polaridade INTEGER,
    lembrar_em TEXT,
    status TEXT NOT NULL DEFAULT 'ativo',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE registro_pessoas (
    registro_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    PRIMARY KEY (registro_id, pessoa_id)
);
CREATE TABLE relacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registro_id INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    motivo TEXT NOT NULL,
    item_novo_id INTEGER,
    item_antigo_id INTEGER,
    registro_antigo_id INTEGER,
    sugestao TEXT,
    status TEXT NOT NULL DEFAULT 'pendente',
    criado_em TEXT NOT NULL
);
CREATE TABLE alertas_dispensados (
    chave TEXT PRIMARY KEY,
    quando TEXT NOT NULL
);
CREATE TABLE historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quando TEXT NOT NULL,
    acao TEXT NOT NULL,
    tabela TEXT NOT NULL,
    linha_id INTEGER,
    resumo TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE busca_fts USING fts5(
    conteudo, tipo UNINDEXED, ref_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE INDEX itens_registro ON itens(registro_id);
CREATE INDEX itens_pessoa ON itens(pessoa_id);
CREATE INDEX itens_tipo ON itens(tipo, status);
CREATE INDEX registros_data ON registros(data_registro, id);
CREATE INDEX registro_pessoas_pessoa ON registro_pessoas(pessoa_id);
CREATE INDEX relacoes_registro ON relacoes(registro_id);
"""

# v2: de qual registro veio o nome (ou a relação) que o banco preencheu sozinho numa pessoa.
# Apagado esse registro, o que veio do texto dele sai também.
_MIGRACAO_V2 = """
ALTER TABLE pessoas ADD COLUMN nome_registro_id INTEGER;
ALTER TABLE pessoas ADD COLUMN relacao_registro_id INTEGER;
"""

def _provedor_da_url(url: str) -> str:
    """Id do serviço de uma URL no formato comum; "outro" quando não dá para saber."""
    try:
        from . import provedores      # importado na hora: o banco não depende dele para abrir
        achado = provedores.detectar_por_url(url)
        if achado is not None and getattr(achado, "tipo", None) == "openai":
            return str(achado.id)
    except Exception:  # noqa: BLE001 - sem o módulo, ou URL estranha: "outro" funciona igual
        pass
    return "outro"


def _ia_do_motor_antigo(antigos: dict[str, Any]) -> dict[str, Any] | None:
    """Motor antigo configurado (motor, motor_url, motor_modelo, motor_chave) -> ia_principal.

    O formato comum antigo chamava `{base}/v1/chat/completions` (tirando um /v1 do fim da
    URL); o novo chama `{url}/chat/completions` com a versão já na URL, então a URL ganha
    /v1 quando não tem. O Ollama continua em `{url}/api/chat`. Sem modelo, não há IA.
    """
    tipo = antigos.get("motor")
    modelo = antigos.get("motor_modelo")
    if tipo not in TIPOS_IA or not isinstance(modelo, str) or not modelo.strip():
        return None
    url = antigos.get("motor_url")
    url = url.strip() if isinstance(url, str) else ""
    if tipo == "ollama":
        url = url or _OLLAMA_ANTIGO
    elif not url:
        return None
    try:
        partes = urllib.parse.urlsplit(url)
        partes.port
    except ValueError:
        return None
    # Usuário e senha dentro da URL seriam um segredo fora do lugar da chave: não converte.
    if partes.scheme not in ("http", "https") or not partes.hostname or "@" in partes.netloc:
        return None
    url = urllib.parse.urlunsplit((partes.scheme, partes.netloc, partes.path.rstrip("/"), "", ""))
    if tipo == "openai" and not url.endswith("/v1"):
        url += "/v1"
    chave = antigos.get("motor_chave")
    chave = chave.strip() if isinstance(chave, str) and _RE_CHAVE_IA.fullmatch(chave.strip()) else None
    return {"provedor": "ollama" if tipo == "ollama" else _provedor_da_url(url), "url": url,
            "modelo": modelo.strip()[:200], "tipo": tipo, "chave": chave}


def _tempo_antigo(valor: Any) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not valor > 0:
        return None
    return int(min(IA_TEMPO_MAXIMO, max(IA_TEMPO_MINIMO, round(valor))))


def _migrar_v3(con: sqlite3.Connection) -> None:
    """v3: registros.privado ("não enviar para a IA") e o motor antigo vira ia_principal."""
    con.execute("ALTER TABLE registros ADD COLUMN privado INTEGER NOT NULL DEFAULT 0")
    antigos: dict[str, Any] = {}
    for chave, valor in con.execute("SELECT chave, valor FROM config").fetchall():
        if _RE_MOTOR_ANTIGO.fullmatch(chave):
            try:
                antigos[chave] = json.loads(valor)
            except ValueError:
                pass
    existentes = {l[0] for l in con.execute("SELECT chave FROM config WHERE chave LIKE 'ia\\_%' ESCAPE '\\'")}
    ia = _ia_do_motor_antigo(antigos) if "ia_principal" not in existentes else None
    if ia is not None:
        con.execute("INSERT INTO config (chave, valor) VALUES ('ia_principal', ?)",
                    (json.dumps(ia, ensure_ascii=False),))
        tempo = _tempo_antigo(antigos.get("motor_tempo_limite"))
        if tempo is not None and "ia_tempo_limite" not in existentes:
            con.execute("INSERT INTO config (chave, valor) VALUES ('ia_tempo_limite', ?)", (json.dumps(tempo),))
    con.execute("DELETE FROM config WHERE chave = 'motor' OR chave LIKE 'motor\\_%' ESCAPE '\\'")


# _MIGRACOES[v] leva o esquema da versão v para v + 1 (texto SQL ou função que recebe a conexão).
_MIGRACOES: list[str | Callable[[sqlite3.Connection], None]] = [_ESQUEMA_V1, _MIGRACAO_V2, _migrar_v3]


class NaoEncontrado(KeyError):
    """O id pedido não existe (o servidor responde 404)."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "Não encontrado."


# ---------------------------------------------------------------- conversões

def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(v: Any) -> str | None:
    """date/datetime/'AAAA-MM-DD' -> 'AAAA-MM-DD'. ValueError se não for data."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return date.fromisoformat(v.strip()[:10]).isoformat()
    raise ValueError("Data inválida.")


def _data(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _data_hora(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _texto_opcional(v: Any) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _lista_apelidos(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    return list(dict.fromkeys(a for a in (_texto_opcional(x) for x in v) if a))


def _id(v: Any) -> int:
    if isinstance(v, bool):
        raise ValueError("Id inválido.")
    return int(v)


def _marcas(n: int) -> str:
    return ",".join("?" * n)


def _chave_pessoa(s: str | None) -> str:
    """'a mãe dela' -> 'mae'; 'Mariana' -> 'mariana'. Para casar pessoas, nunca para mostrar.

    'avó' e 'avô' ficam diferentes ('avo´' e 'avo^'): sem acento seriam a mesma pessoa.
    """
    if not s:
        return ""
    k = _RE_ARTIGO.sub("", _RE_DONO.sub("", normalizar(str(s)))).strip()
    if k in ("avo", "vo", "vovo"):
        minusculo = str(s).lower()
        k = "avo" + ("^" if "ô" in minusculo else "´" if "ó" in minusculo else "")
    return k


def _valores_item(item: Item) -> dict[str, Any]:
    """Item -> colunas da tabela itens, validando enums, datas e situação."""
    status = item.status or "ativo"
    if status not in STATUS_ITEM:
        raise ValueError(f"Situação de item desconhecida: {status!r}.")
    polaridade = item.polaridade
    if polaridade is not None:
        polaridade = int(polaridade)
        polaridade = 1 if polaridade > 0 else (-1 if polaridade < 0 else None)
    categorias = item.categorias or []
    if isinstance(categorias, str):
        categorias = [categorias]
    categorias = list(dict.fromkeys(c for c in categorias if c in CATEGORIAS))
    return {
        "tipo": TipoItem(item.tipo).value,
        "resumo": str(item.resumo or ""),
        "trecho": str(item.trecho or ""),
        "certeza": Certeza(item.certeza).value,
        "categorias": json.dumps(categorias, ensure_ascii=False),
        "importancia": min(3, max(1, int(item.importancia or 2))),
        "data_evento": _iso(item.data_evento),
        "data_expressao": _texto_opcional(item.data_expressao),
        "precisao": Precisao(item.precisao).value if item.precisao else None,
        "recorrente": 1 if item.recorrente else 0,
        "pessoa": _texto_opcional(item.pessoa),
        "pessoa_id": item.pessoa_id,
        "alvo": _texto_opcional(item.alvo),
        "polaridade": polaridade,
        "lembrar_em": _iso(item.lembrar_em),
        "status": status,
    }


def _item_de_linha(linha: sqlite3.Row) -> Item:
    d = dict(linha)
    d["categorias"] = json.loads(d.get("categorias") or "[]")
    return item_de_dict(d)


def _relacao_de_linha(linha: sqlite3.Row) -> Relacao:
    return Relacao(
        tipo=linha["tipo"], motivo=linha["motivo"], item_novo=linha["item_novo_id"],
        item_antigo_id=linha["item_antigo_id"], registro_antigo_id=linha["registro_antigo_id"],
        sugestao=linha["sugestao"], status=linha["status"], id=linha["id"],
    )


def _pessoa_dict(linha: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": linha["id"],
        "nome": linha["nome"],
        "relacao": linha["relacao"],
        "apelidos": json.loads(linha["apelidos"] or "[]"),
        "observacoes": linha["observacoes"] or "",
        "aniversario": _data(linha["aniversario"]),
    }


def _registro_dict(linha: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": linha["id"],
        "criado_em": _data_hora(linha["criado_em"]),
        "data_registro": _data(linha["data_registro"]),
        "texto": linha["texto"],
        "contexto": linha["contexto"],
        "humor": linha["humor"],
        "privado": bool(linha["privado"]),        # "não enviar para a IA"
    }


def config_sem_segredos(ajustes: dict) -> dict:
    """Cópia dos ajustes sem a chave de cada IA e sem o verificador do PIN da IA."""
    limpo = copy.deepcopy(ajustes)
    for qual in ("ia_principal", "ia_reserva"):
        ia = limpo.get(qual)
        if isinstance(ia, dict):
            limpo[qual] = {k: v for k, v in ia.items() if k != "chave"} | {"tem_chave": bool(ia.get("chave"))}
    limpo.pop("ia_pin", None)
    return limpo


def _texto_busca(*partes: str | None) -> str:
    return "\n".join(p for p in partes if p)


def _frase_com(texto: str, chaves: Iterable[str]) -> str:
    """A primeira frase do texto que cita uma das chaves; senão o começo do texto."""
    padroes = [re.compile(r"\b" + re.escape(k) + r"\b") for k in chaves if k]
    lista = frases(texto)
    for _, _, frase in lista:
        n = normalizar(frase)
        if any(p.search(n) for p in padroes):
            return frase
    inicio = lista[0][2] if lista else texto.strip()
    return inicio if len(inicio) <= 200 else inicio[:199].rstrip() + "…"


def _conectar() -> sqlite3.Connection:
    # isolation_level=None: as transações são abertas e fechadas à mão em _escrita.
    con = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    # Sem isto o VACUUM (e ordenações grandes) monta cópias em arquivos temporários do
    # disco (%TEMP%\etilqs_*), sem criptografia, quando o banco passa do cache de páginas.
    con.execute("PRAGMA temp_store = MEMORY")
    return con


def _texto_ia(valor: Any, maximo: int, mensagem: str) -> str:
    if not isinstance(valor, str) or not valor.strip() or len(valor.strip()) > maximo or _RE_CONTROLE.search(valor):
        raise ValueError(mensagem)
    return valor.strip()


def validar_ia(valor: Any) -> dict[str, Any]:
    """{provedor, url, modelo, tipo, chave} conferido. As mensagens nunca citam a chave."""
    if not isinstance(valor, dict):
        raise ValueError("Configuração de IA inválida.")
    if set(valor) - set(CAMPOS_IA):
        raise ValueError("Configuração de IA com campos desconhecidos: "
                         + ", ".join(sorted(str(k) for k in set(valor) - set(CAMPOS_IA))))
    provedor = valor.get("provedor")
    if not isinstance(provedor, str) or not _RE_PROVEDOR.fullmatch(provedor):
        raise ValueError("Serviço de IA inválido.")
    url = _texto_ia(valor.get("url"), 500, "Endereço da IA inválido.")
    partes = urllib.parse.urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname or any(c.isspace() for c in url):
        raise ValueError("Endereço da IA inválido: use http:// ou https://.")
    if "@" in partes.netloc:
        raise ValueError("Não coloque usuário e senha no endereço da IA; use o campo da chave.")
    try:
        partes.port                 # porta fora do lugar ("host:abc") levanta ValueError
    except ValueError:
        raise ValueError("Endereço da IA inválido.") from None
    url = urllib.parse.urlunsplit((partes.scheme, partes.netloc, partes.path.rstrip("/"), "", ""))
    modelo = _texto_ia(valor.get("modelo"), 200, "Informe o modelo da IA.")
    tipo = valor.get("tipo")
    if tipo not in TIPOS_IA:
        raise ValueError("Tipo de IA inválido.")
    chave = valor.get("chave")
    if chave is not None:
        if not isinstance(chave, str):
            raise ValueError("A chave da IA precisa ser um texto.")
        chave = chave.strip() or None
        if chave is not None and not _RE_CHAVE_IA.fullmatch(chave):
            raise ValueError("A chave da IA tem caracteres que não podem estar numa chave.")
    exata = valor.get("url_exata", False)
    if not isinstance(exata, bool):
        raise ValueError("“url_exata” precisa ser verdadeiro ou falso.")
    ia = {"provedor": provedor, "url": url, "modelo": modelo, "tipo": tipo, "chave": chave}
    if exata:
        ia["url_exata"] = True
    return ia


def _validar_config(chave: str, valor: Any) -> Any:
    if not isinstance(chave, str) or not _RE_CHAVE_CONFIG.fullmatch(chave):
        raise ValueError(f"Nome de ajuste inválido: {chave!r}.")
    if _RE_MOTOR_ANTIGO.fullmatch(chave):
        raise ValueError("Esse ajuste da IA não existe mais: a IA é configurada em Ajustes → IA.")
    if valor is None:
        return None
    if chave in ("ia_principal", "ia_reserva"):
        return validar_ia(valor)
    if chave == "ia_pin":
        if not verificador_valido(valor):
            raise ValueError("O PIN da IA não foi guardado do jeito certo.")
        return dict(valor)
    if chave == "ia_tempo_limite":
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not (
                IA_TEMPO_MINIMO <= valor <= IA_TEMPO_MAXIMO):
            raise ValueError(f"O tempo limite da IA deve ficar entre {IA_TEMPO_MINIMO} e {IA_TEMPO_MAXIMO} segundos.")
        return int(valor) if float(valor).is_integer() else float(valor)
    if chave in _CONFIG_DATAS:
        return _iso(valor)
    if chave == "nome_dela":
        return _texto_opcional(valor)
    if chave == "antecedencias":
        if isinstance(valor, (str, bytes)) or not isinstance(valor, Iterable):
            raise ValueError("As antecedências devem ser uma lista de dias.")
        dias = []
        for d in valor:
            if isinstance(d, bool) or int(d) < 0 or int(d) > 366:
                raise ValueError("Antecedência inválida.")
            dias.append(int(d))
        return sorted(set(dias), reverse=True)
    if chave == "minutos_bloqueio":
        if isinstance(valor, bool) or not 1 <= int(valor) <= 24 * 60:
            raise ValueError("O bloqueio automático deve ficar entre 1 e 1440 minutos.")
        return int(valor)
    valor = para_json(valor)
    json.dumps(valor)       # TypeError/ValueError se não couber em JSON
    return valor


# ---------------------------------------------------------------- importar JSON (seção 18.2)
# O arquivo vem de `exportar()` (Ajustes → Exportar), deste ou de outro computador. Tudo é
# conferido antes de abrir a transação: tipo errado, id repetido ou arquivo grande demais é
# ValueError (400 no servidor) e nada é gravado.

MAX_JSON_IMPORTAR = 60 * 1024 * 1024       # caracteres do texto do arquivo (o pedido tem até 64 MB)
MAX_IMPORTAR_REGISTROS = 100_000
MAX_IMPORTAR_PESSOAS = 20_000
MAX_IMPORTAR_ITENS = 1_000_000             # somando todos os registros
MAX_IMPORTAR_RELACOES = 1_000_000
# Conferir que cada item cita um trecho do texto custa (tamanho do texto × itens do registro):
# um JSON pequeno com um texto de 2 MB e 2 mil itens levaria 16 s. Somando todos os registros,
# acima disto o arquivo é "grande demais" (um diário de verdade fica muito abaixo: 3 mil
# registros de 500 caracteres com 5 itens cada são 7,5 milhões).
MAX_CUSTO_TRECHOS = 500_000_000
_MAX_TIPO_RELACAO = 40
# Lista de nomes na prévia da importação (as contagens continuam exatas).
MAX_NOMES_PREVIA = 100
JSON_INVALIDO = "O arquivo não é um JSON válido."
NAO_E_EXPORTACAO = "Este arquivo não é uma exportação do Entre Nós (o JSON de Ajustes → Exportar)."
GRANDE_DEMAIS = "O arquivo é grande demais para importar de uma vez."
EXPORTACAO_NOVA = "Este arquivo foi exportado por uma versão mais nova do Entre Nós."
BANCO_DANIFICADO = "O diário está danificado."
_TIPOS_ITEM = frozenset(t.value for t in TipoItem)
_CERTEZAS = frozenset(c.value for c in Certeza)
_PRECISOES = frozenset(p.value for p in Precisao)

# Tabelas e colunas que um diário do esquema atual precisa ter (conferir_integridade).
_ESTRUTURA = {
    "config": ("chave", "valor"),
    "registros": ("id", "criado_em", "data_registro", "texto", "contexto", "humor", "privado"),
    "itens": ("id", "registro_id", *_COLUNAS_ITEM, "criado_em", "atualizado_em"),
    "pessoas": ("id", "nome", "relacao", "apelidos", "observacoes", "aniversario", "criado_em", "automatica",
                "nome_registro_id", "relacao_registro_id"),
    "registro_pessoas": ("registro_id", "pessoa_id"),
    "relacoes": ("id", "registro_id", "tipo", "motivo", "item_novo_id", "item_antigo_id", "registro_antigo_id",
                 "sugestao", "status", "criado_em"),
    "alertas_dispensados": ("chave", "quando"),
    "historico": ("id", "quando", "acao", "tabela", "linha_id", "resumo"),
    "busca_fts": ("conteudo", "tipo", "ref_id"),
}
# Tabelas com AUTOINCREMENT (os ids nunca se repetem: é o que deixa apagar pelo id nos backups).
_TABELAS_COM_ID = ("registros", "itens", "pessoas", "relacoes", "historico")
# Tabelas que o próprio SQLite pode criar (ANALYZE) e que não mudam o comportamento de nada.
_ESTATISTICAS_SQLITE = frozenset({"sqlite_stat1", "sqlite_stat4"})
_OBJETOS_DO_ESQUEMA: frozenset[tuple[str, str]] | None = None


def _objetos_do_esquema() -> frozenset[tuple[str, str]]:
    """(tipo, nome) de tudo o que um diário novo tem em sqlite_master: tabelas, índices,
    `sqlite_sequence` e as tabelas-sombra do FTS5. Nenhum gatilho, nenhuma visão."""
    global _OBJETOS_DO_ESQUEMA
    if _OBJETOS_DO_ESQUEMA is None:
        modelo = Banco.novo()
        try:
            _OBJETOS_DO_ESQUEMA = frozenset(
                (l[0], l[1]) for l in modelo._con.execute("SELECT type, name FROM sqlite_master"))
        finally:
            modelo.fechar()
    return _OBJETOS_DO_ESQUEMA


def _recusar_constante_json(nome: str) -> Any:
    raise ValueError(JSON_INVALIDO)


def ler_exportacao(texto: str | bytes) -> dict:
    """Texto do arquivo de `exportar()` -> dict, conferindo tamanho, JSON e que veio do Entre Nós.

    Só lê: os campos são conferidos em `Banco.importar_json`. ValueError com a mensagem para a tela.
    """
    if isinstance(texto, (bytes, bytearray)):
        if len(texto) > MAX_JSON_IMPORTAR:
            raise ValueError(GRANDE_DEMAIS)
        try:
            texto = bytes(texto).decode("utf-8")
        except UnicodeError:
            raise ValueError(JSON_INVALIDO) from None
    if not isinstance(texto, str):
        raise ValueError("Envie o texto do arquivo exportado pelo Entre Nós.")
    if len(texto) > MAX_JSON_IMPORTAR:
        raise ValueError(GRANDE_DEMAIS)
    try:
        dados = json.loads(texto.lstrip("﻿"), parse_constant=_recusar_constante_json)
        # Escapes como "\ud800" viram texto que o SQLite não grava: recusa já na leitura.
        json.dumps(dados, ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):
        raise ValueError(JSON_INVALIDO) from None
    if not isinstance(dados, dict) or dados.get("app") != "entre-nos":
        raise ValueError(NAO_E_EXPORTACAO)
    return dados


class _Leitor:
    """Um objeto do arquivo importado, com cada campo conferido pelo tipo.

    Tipo errado é ValueError com o caminho do campo ("registros[3].itens[0].importancia"),
    nunca com o valor (que é texto do usuário). Textos saem em NFC, como o servidor grava.
    """

    def __init__(self, dados: Any, caminho: str):
        if not isinstance(dados, dict):
            raise _formato_errado(caminho)
        self.dados = dados
        self.caminho = caminho

    def _erro(self, chave: str) -> ValueError:
        return _formato_errado(f"{self.caminho}.{chave}" if self.caminho else chave)

    def texto(self, chave: str, obrigatorio: bool = False) -> str | None:
        valor = self.dados.get(chave)
        if valor is None:
            if obrigatorio:
                raise self._erro(chave)
            return None
        if not isinstance(valor, str) or (obrigatorio and not valor.strip()):
            raise self._erro(chave)
        return unicodedata.normalize("NFC", valor)

    def inteiro(self, chave: str, obrigatorio: bool = False) -> int | None:
        valor = self.dados.get(chave)
        if valor is None and not obrigatorio:
            return None
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise self._erro(chave)
        return valor

    def booleano(self, chave: str) -> bool:
        valor = self.dados.get(chave)
        if valor is None:
            return False
        if not isinstance(valor, bool):
            raise self._erro(chave)
        return valor

    def data(self, chave: str, obrigatorio: bool = False) -> str | None:
        valor = self.texto(chave, obrigatorio)
        if valor is None or not valor.strip():
            if obrigatorio:
                raise self._erro(chave)
            return None
        try:
            return date.fromisoformat(valor.strip()[:10]).isoformat()
        except ValueError:
            raise self._erro(chave) from None

    def data_hora(self, chave: str) -> str | None:
        valor = self.texto(chave)
        if valor is None or not valor.strip():
            return None
        try:
            return datetime.fromisoformat(valor.strip()).isoformat(timespec="seconds")
        except ValueError:
            raise self._erro(chave) from None

    def opcao(self, chave: str, opcoes: Iterable[str], padrao: str | None, obrigatorio: bool = False) -> str | None:
        valor = self.texto(chave, obrigatorio)
        if valor is None:
            return padrao
        if valor not in opcoes:
            raise self._erro(chave)
        return valor

    def lista(self, chave: str, obrigatorio: bool = False) -> list:
        valor = self.dados.get(chave)
        if valor is None and not obrigatorio:
            return []
        if not isinstance(valor, list):
            raise self._erro(chave)
        return valor

    def textos(self, chave: str) -> list[str]:
        valores = self.lista(chave)
        if not all(isinstance(v, str) for v in valores):
            raise self._erro(chave)
        return [unicodedata.normalize("NFC", v) for v in valores]


def _formato_errado(caminho: str) -> ValueError:
    return ValueError(f"O arquivo tem um dado com formato errado em “{caminho}”.")


@dataclass
class _PessoaImportada:
    chave: Any                      # id no arquivo, ou ("sem-id", nome, relação) quando não tem
    nome: str | None
    relacao: str | None
    apelidos: list[str] = field(default_factory=list)
    observacoes: str = ""
    aniversario: str | None = None

    def so_nome(self) -> bool:
        """Sem nada que o usuário tenha escrito à mão: parece criada a partir de um registro."""
        return not self.apelidos and not self.observacoes.strip() and not self.aniversario


@dataclass
class _ItemImportado:
    id: int | None                  # id no arquivo
    valores: dict[str, Any]         # colunas da tabela itens (pessoa_id ainda é o do arquivo)
    criado_em: str | None


@dataclass
class _RelacaoImportada:
    tipo: str
    motivo: str
    item_novo: int | None           # ids do arquivo
    item_antigo_id: int | None
    registro_antigo_id: int | None
    sugestao: str | None
    status: str


@dataclass
class _RegistroImportado:
    id: int
    texto: str
    data_registro: str
    criado_em: str | None
    contexto: str | None
    humor: str | None
    privado: bool
    itens: list[_ItemImportado]
    pessoas: list[_PessoaImportada]
    relacoes: list[_RelacaoImportada]
    itens_sem_trecho: int           # itens que não citam um trecho do texto: ficam de fora


@dataclass
class _Exportacao:
    pessoas: list[_PessoaImportada]
    registros: list[_RegistroImportado]
    com_ajustes: bool               # o arquivo traz ajustes ou avisos dispensados (que não entram)


def _ler_pessoa(dados: Any, caminho: str, do_registro: bool = False) -> _PessoaImportada | None:
    """Pessoa do cadastro (`pessoas[i]`) ou citada num registro (`registros[i].pessoas[j]`)."""
    l = _Leitor(dados, caminho)
    pid = l.inteiro("id", obrigatorio=not do_registro)
    nome, relacao = _texto_opcional(l.texto("nome")), _texto_opcional(l.texto("relacao"))
    if do_registro:
        apelidos, observacoes, aniversario = [], "", None
    else:
        apelidos = _lista_apelidos(l.textos("apelidos"))
        observacoes = l.texto("observacoes") or ""
        aniversario = l.data("aniversario")
    if not nome and not relacao:
        return None                 # nada para casar nem mostrar
    chave = pid if pid is not None else ("sem-id", _chave_pessoa(nome), _chave_pessoa(relacao))
    return _PessoaImportada(chave, nome, relacao, apelidos, observacoes, aniversario)


def _ler_item(dados: Any, caminho: str) -> _ItemImportado:
    l = _Leitor(dados, caminho)
    importancia = l.inteiro("importancia")
    precisao = l.opcao("precisao", _PRECISOES, None)
    data_evento, lembrar_em = l.data("data_evento"), l.data("lembrar_em")
    item = Item(
        tipo=TipoItem(l.opcao("tipo", _TIPOS_ITEM, None, obrigatorio=True)),
        resumo=l.texto("resumo") or "",
        trecho=l.texto("trecho") or "",
        certeza=Certeza(l.opcao("certeza", _CERTEZAS, Certeza.OBSERVADO.value)),
        categorias=l.textos("categorias"),
        importancia=importancia if importancia is not None else 2,
        data_evento=_data(data_evento),
        data_expressao=l.texto("data_expressao"),
        precisao=Precisao(precisao) if precisao else None,
        recorrente=l.booleano("recorrente"),
        pessoa=l.texto("pessoa"),
        pessoa_id=l.inteiro("pessoa_id"),
        alvo=l.texto("alvo"),
        polaridade=l.inteiro("polaridade"),
        lembrar_em=_data(lembrar_em),
        status=l.opcao("status", STATUS_ITEM, "ativo"),
    )
    return _ItemImportado(l.inteiro("id"), _valores_item(item), l.data_hora("criado_em"))


def _ler_relacao(dados: Any, caminho: str) -> _RelacaoImportada:
    l = _Leitor(dados, caminho)
    tipo = l.texto("tipo", obrigatorio=True).strip()
    if len(tipo) > _MAX_TIPO_RELACAO:
        raise l._erro("tipo")
    return _RelacaoImportada(tipo, l.texto("motivo") or "", l.inteiro("item_novo"), l.inteiro("item_antigo_id"),
                             l.inteiro("registro_antigo_id"), _texto_opcional(l.texto("sugestao")),
                             l.opcao("status", STATUS_RELACAO, "pendente"))


def _ler_registro(dados: Any, caminho: str) -> _RegistroImportado:
    l = _Leitor(dados, caminho)
    texto = l.texto("texto", obrigatorio=True)
    itens, sem_trecho = [], 0
    for j, bruto in enumerate(l.lista("itens")):
        item = _ler_item(bruto, f"{caminho}.itens[{j}]")
        # Nunca inventar: item que não cita um pedaço literal do texto não entra.
        if item.valores["trecho"].strip() and item.valores["trecho"] in texto:
            itens.append(item)
        else:
            sem_trecho += 1
    pessoas = [p for j, bruto in enumerate(l.lista("pessoas"))
               if (p := _ler_pessoa(bruto, f"{caminho}.pessoas[{j}]", do_registro=True)) is not None]
    relacoes = [_ler_relacao(bruto, f"{caminho}.relacoes[{j}]") for j, bruto in enumerate(l.lista("relacoes"))]
    return _RegistroImportado(
        id=l.inteiro("id", obrigatorio=True), texto=texto, data_registro=l.data("data_registro", obrigatorio=True),
        criado_em=l.data_hora("criado_em"), contexto=_texto_opcional(l.texto("contexto")),
        humor=_texto_opcional(l.texto("humor")), privado=l.booleano("privado"), itens=itens, pessoas=pessoas,
        relacoes=relacoes, itens_sem_trecho=sem_trecho)


def validar_exportacao(dados: Any) -> _Exportacao:
    """O dict de `ler_exportacao` conferido campo a campo (ValueError no primeiro problema).

    Não usa o banco: o servidor chama fora da trava do app e passa o resultado para
    `Banco.importar_json`, que só junta (seção 18.6)."""
    if not isinstance(dados, dict) or dados.get("app") != "entre-nos":
        raise ValueError(NAO_E_EXPORTACAO)
    raiz = _Leitor(dados, "")
    raiz.texto("versao", obrigatorio=True)
    esquema = raiz.inteiro("esquema")
    if esquema is not None and esquema > ESQUEMA_VERSAO:
        raise ValueError(EXPORTACAO_NOVA)
    if esquema is not None and esquema < 1:
        raise raiz._erro("esquema")
    brutos_registros, brutos_pessoas = raiz.lista("registros", obrigatorio=True), raiz.lista("pessoas")
    if len(brutos_registros) > MAX_IMPORTAR_REGISTROS or len(brutos_pessoas) > MAX_IMPORTAR_PESSOAS:
        raise ValueError(GRANDE_DEMAIS)
    pessoas = [p for i, bruto in enumerate(brutos_pessoas) if (p := _ler_pessoa(bruto, f"pessoas[{i}]")) is not None]
    if len({p.chave for p in pessoas}) != len(pessoas):
        raise ValueError("O arquivo tem duas pessoas com o mesmo número (id).")
    registros: list[_RegistroImportado] = []
    total_itens = total_relacoes = custo_trechos = 0
    for i, bruto in enumerate(brutos_registros):
        if isinstance(bruto, dict):
            # Conta antes de ler tudo: um registro com um milhão de itens já é grande demais.
            itens, relacoes, texto = bruto.get("itens"), bruto.get("relacoes"), bruto.get("texto")
            total_itens += len(itens) if isinstance(itens, list) else 0
            total_relacoes += len(relacoes) if isinstance(relacoes, list) else 0
            if isinstance(itens, list) and isinstance(texto, str):
                custo_trechos += len(texto) * len(itens)
            if (total_itens > MAX_IMPORTAR_ITENS or total_relacoes > MAX_IMPORTAR_RELACOES
                    or custo_trechos > MAX_CUSTO_TRECHOS):
                raise ValueError(GRANDE_DEMAIS)
        registros.append(_ler_registro(bruto, f"registros[{i}]"))
    if len({r.id for r in registros}) != len(registros):
        raise ValueError("O arquivo tem dois registros com o mesmo número (id).")
    ids_itens = [i.id for r in registros for i in r.itens if i.id is not None]
    if len(set(ids_itens)) != len(ids_itens):
        raise ValueError("O arquivo tem dois itens com o mesmo número (id).")
    raiz.lista("alertas_dispensados")
    ajustes = dados.get("config")
    if ajustes is not None and not isinstance(ajustes, dict):
        raise raiz._erro("config")
    com_ajustes = bool(ajustes) or bool(dados.get("alertas_dispensados"))
    return _Exportacao(pessoas, registros, com_ajustes)


def _chave_registro(data_registro: Any, texto: str) -> tuple[Any, str]:
    """Quando dois registros são "o mesmo" ao importar: mesma data e mesmo texto, sem contar
    maiúsculas, acentos, espaços e pontuação ("Ela adora café" = "ela adora cafe."). Acento
    diferente funde ("é" e "e"): é raro mudar o sentido, e duplicar seria pior (DESIGN 18.6)."""
    return data_registro, re.sub(r"[\s.!?…,;:]+", " ", normalizar(texto)).strip()


def _rotulo(nome: str | None, relacao: str | None) -> str:
    """"Mariana (melhor amiga)", "Mariana" ou "mãe": como a tela mostra uma pessoa."""
    if nome and relacao:
        return f"{nome} ({relacao})"
    return nome or relacao or "Sem nome"


class _CadastroParaCasar:
    """As pessoas do diário, para casar com as do arquivo por nome, apelido ou relação.

    Cada pessoa do diário casa com uma só do arquivo (`reivindicadas`): duas pessoas do arquivo
    com o mesmo nome (o usuário disse que eram gente diferente) continuam duas, e importar o
    mesmo arquivo de novo casa cada uma com a mesma de antes.
    """

    def __init__(self, con: sqlite3.Connection):
        self.nome: dict[int, str] = {}
        self.relacao: dict[int, str] = {}
        self.por_nome: dict[str, list[int]] = {}
        self.por_apelido: dict[str, list[int]] = {}
        self.por_relacao: dict[str, list[int]] = {}
        self.reivindicadas: set[int] = set()
        for l in con.execute("SELECT id, nome, relacao, apelidos FROM pessoas ORDER BY id"):
            self.acrescentar(l["id"], l["nome"], l["relacao"], json.loads(l["apelidos"] or "[]"))

    def acrescentar(self, pid: int, nome: str | None, relacao: str | None, apelidos: Iterable[str]) -> None:
        self.nome[pid], self.relacao[pid] = _chave_pessoa(nome), _chave_pessoa(relacao)
        if self.nome[pid]:
            self.por_nome.setdefault(self.nome[pid], []).append(pid)
        for apelido in apelidos:
            if _chave_pessoa(apelido):
                self.por_apelido.setdefault(_chave_pessoa(apelido), []).append(pid)
        if self.relacao[pid]:
            self.por_relacao.setdefault(self.relacao[pid], []).append(pid)

    def _livres(self, ids: Iterable[int]) -> list[int]:
        return sorted({i for i in ids if i not in self.reivindicadas})

    def casar(self, p: _PessoaImportada) -> int | None:
        k_nome, k_rel = _chave_pessoa(p.nome), _chave_pessoa(p.relacao)
        if k_nome:
            candidatos = self._livres([*self.por_nome.get(k_nome, ()), *self.por_apelido.get(k_nome, ()),
                                       *(i for a in p.apelidos for i in self.por_nome.get(_chave_pessoa(a), ()))])
            # Mesmo nome com outra relação ("Mariana, a prima" x "Mariana, a amiga") é outra pessoa.
            compativeis = [i for i in candidatos if not k_rel or not self.relacao[i] or self.relacao[i] == k_rel]
            mesma_relacao = [i for i in compativeis if k_rel and self.relacao[i] == k_rel]
            if mesma_relacao or compativeis:
                return (mesma_relacao or compativeis)[0]
            if k_rel:
                # "Sandra, a mãe dela" quando o diário já tem uma "mãe" ainda sem nome.
                sem_nome = [i for i in self._livres(self.por_relacao.get(k_rel, ())) if not self.nome[i]]
                if len(sem_nome) == 1:
                    return sem_nome[0]
            return None
        if k_rel:
            mesmas = self._livres(self.por_relacao.get(k_rel, ()))
            sem_nome = [i for i in mesmas if not self.nome[i]]
            if sem_nome:
                return sem_nome[0]
            if len(mesmas) == 1:
                return mesmas[0]
        return None


def _avisos_da_importacao(exp: _Exportacao, novos: int, sem_trecho: int, relacoes_fora: int,
                          marcados: int = 0, completadas: int = 0, no_arquivo: int = 0) -> list[str]:
    avisos = []
    if not exp.registros:
        avisos.append("O arquivo não tem registros.")
    elif not novos:
        if marcados or completadas:
            avisos.append("Todos os registros deste arquivo já estão no diário; só mudam as marcas e "
                          "informações listadas aqui.")
        else:
            avisos.append("Todos os registros deste arquivo já estão no diário: nada novo para juntar.")
    if marcados:
        avisos.append(f"{marcados} {'registro que já estava aqui passa' if marcados == 1 else 'registros que já estavam aqui passam'} "
                      "a ficar 🔒 fora da IA, como no arquivo. (Nada perde o 🔒 ao importar.)")
    if no_arquivo:
        avisos.append(f"O arquivo tem {no_arquivo} {'registro repetido' if no_arquivo == 1 else 'registros repetidos'} "
                      f"dentro dele mesmo; {'entra' if no_arquivo == 1 else 'entram'} uma vez só.")
    if sem_trecho:
        avisos.append(f"{sem_trecho} {'item não cita' if sem_trecho == 1 else 'itens não citam'} um trecho do "
                      f"texto do registro e {'ficou' if sem_trecho == 1 else 'ficaram'} de fora.")
    if relacoes_fora:
        avisos.append(f"{relacoes_fora} {'ligação' if relacoes_fora == 1 else 'ligações'} entre registros "
                      f"{'apontava' if relacoes_fora == 1 else 'apontavam'} para algo que não veio no arquivo e "
                      f"{'ficou' if relacoes_fora == 1 else 'ficaram'} de fora.")
    if exp.com_ajustes:
        avisos.append("Ajustes, PIN da IA, configuração da IA e avisos dispensados do arquivo não são "
                      "importados: ficam os deste diário.")
    return avisos


# ---------------------------------------------------------------- Banco

class Banco:
    """Acesso ao banco em memória. Crie com Banco.novo() ou Banco.de_bytes().

    Seguro entre threads: tudo passa por um RLock.
    """

    def __init__(self, conexao: sqlite3.Connection,
                 ao_salvar: Callable[[bytes], None] | None = None):
        self._con = conexao
        self._trava = threading.RLock()
        self.ao_salvar = ao_salvar
        self._compactar_busca = False   # houve remoção no FTS desde o último para_bytes

    # ------------------------------------------------------------ ciclo de vida

    @classmethod
    def novo(cls, ao_salvar: Callable[[bytes], None] | None = None) -> Banco:
        banco = cls(_conectar(), ao_salvar)
        banco._migrar()
        return banco

    @classmethod
    def de_bytes(cls, dados: bytes, ao_salvar: Callable[[bytes], None] | None = None) -> Banco:
        if not dados:
            raise ValueError("Não há dados para carregar.")
        con = _conectar()
        try:
            con.deserialize(bytes(dados))
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("O banco está danificado.")
            banco = cls(con, ao_salvar)
            banco._migrar()
        except sqlite3.DatabaseError as erro:
            con.close()
            raise ValueError("Os dados não são um banco do Entre Nós.") from erro
        except BaseException:
            con.close()
            raise
        return banco

    def para_bytes(self) -> bytes:
        with self._trava:
            if self._compactar_busca:
                # Funde os segmentos do FTS5: sem isso os termos apagados seguem no índice.
                self._con.execute("INSERT INTO busca_fts(busca_fts) VALUES ('optimize')")
                self._compactar_busca = False
            self._con.execute("VACUUM")
            return self._con.serialize()

    def fechar(self) -> None:
        with self._trava:
            self._con.close()

    def __del__(self) -> None:
        # O servidor descarta o Banco ao bloquear; a conexão em memória vai junto.
        try:
            self._con.close()
        except Exception:  # noqa: BLE001 - no encerramento do Python tudo pode faltar
            pass

    def _migrar(self) -> None:
        with self._trava:
            con = self._con
            con.execute("PRAGMA secure_delete = ON")
            versao = con.execute("PRAGMA user_version").fetchone()[0]
            if versao > ESQUEMA_VERSAO:
                raise ValueError(DADOS_DE_VERSAO_NOVA)
            if versao == 0 and con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                raise ValueError("Os dados não são um banco do Entre Nós.")
            for v in range(versao, ESQUEMA_VERSAO):
                passo = _MIGRACOES[v]
                try:
                    if callable(passo):
                        con.execute("BEGIN")
                        passo(con)
                        con.execute(f"PRAGMA user_version = {v + 1}")
                        con.execute("COMMIT")
                    else:
                        con.executescript(f"BEGIN;\n{passo}\nPRAGMA user_version = {v + 1};\nCOMMIT;")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise

    @contextmanager
    def _escrita(self) -> Iterator[sqlite3.Connection]:
        """Transação de escrita; no fim, entrega os bytes novos a `ao_salvar`."""
        with self._trava:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
                self._con.execute("COMMIT")
            except BaseException:
                if self._con.in_transaction:
                    self._con.execute("ROLLBACK")
                raise
            if self.ao_salvar is not None:
                self.ao_salvar(self.para_bytes())

    def _historico_add(self, acao: str, tabela: str, linha_id: int | None, resumo: str) -> None:
        """Só metadados: nunca texto, nome ou trecho do usuário."""
        self._con.execute(
            "INSERT INTO historico (quando, acao, tabela, linha_id, resumo) VALUES (?, ?, ?, ?, ?)",
            (_agora(), acao, tabela, linha_id, resumo))

    def _linha(self, tabela: str, id: int) -> sqlite3.Row | None:
        return self._con.execute(f"SELECT * FROM {tabela} WHERE id = ?", (_id(id),)).fetchone()

    def _id_existente(self, tabela: str, v: Any) -> int | None:
        """v como id de `tabela`, ou None se não for inteiro ou não existir."""
        if not isinstance(v, int) or isinstance(v, bool):
            return None
        return v if self._linha(tabela, v) is not None else None

    # ------------------------------------------------------------ índice de busca
    # rowid do FTS: 2*id para registros, 2*id + 1 para itens (ids nunca se repetem).

    def _busca_gravar(self, tipo: str, ref_id: int, conteudo: str, substituir: bool = False) -> None:
        rowid = 2 * ref_id + (1 if tipo == "item" else 0)
        if substituir:
            self._busca_apagar(tipo, ref_id)
        self._con.execute("INSERT INTO busca_fts (rowid, conteudo, tipo, ref_id) VALUES (?, ?, ?, ?)",
                          (rowid, conteudo, tipo, ref_id))

    def _busca_apagar(self, tipo: str, ref_id: int) -> None:
        rowid = 2 * ref_id + (1 if tipo == "item" else 0)
        self._con.execute("DELETE FROM busca_fts WHERE rowid = ?", (rowid,))
        self._compactar_busca = True

    def _busca_item(self, item_id: int, v: dict[str, Any], substituir: bool = False) -> None:
        self._busca_gravar("item", item_id, _texto_busca(v["resumo"], v["trecho"], v["alvo"], v["pessoa"]),
                           substituir)

    # ------------------------------------------------------------ config

    def config(self) -> dict:
        with self._trava:
            guardado = {l["chave"]: json.loads(l["valor"])
                        for l in self._con.execute("SELECT chave, valor FROM config")}
        resultado = copy.deepcopy(CONFIG_PADRAO)
        resultado.update(guardado)
        for chave in _CONFIG_DATAS:
            try:
                resultado[chave] = _data(resultado[chave])
            except (TypeError, ValueError):
                resultado[chave] = None
        return resultado

    def salvar_config(self, **valores) -> None:
        normalizados = {chave: _validar_config(chave, valor) for chave, valor in valores.items()}
        if not normalizados:
            return
        with self._escrita() as con:
            for chave, valor in normalizados.items():
                if valor is None:
                    con.execute("DELETE FROM config WHERE chave = ?", (chave,))
                else:
                    con.execute("INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)",
                                (chave, json.dumps(valor, ensure_ascii=False)))
            self._historico_add("configurar", "config", None,
                                "ajustes alterados: " + ", ".join(sorted(normalizados)))

    def _chave_nome_dela(self) -> str:
        linha = self._con.execute("SELECT valor FROM config WHERE chave = 'nome_dela'").fetchone()
        return _chave_pessoa(json.loads(linha["valor"])) if linha else ""

    # ------------------------------------------------------------ registros

    def criar_registro(self, texto: str, data_registro: date, itens: list[Item],
                       pessoas: list[PessoaEncontrada], relacoes: list[Relacao],
                       contexto: str | None = None, humor: str | None = None,
                       privado: bool = False) -> int:
        """Grava o registro revisado. Cria as pessoas novas, liga os itens às pessoas pelo
        texto de `item.pessoa` e converte `relacao.item_novo` (índice em `itens`) em id.

        `privado`: "não enviar para a IA" (o registro nunca sai do computador)."""
        if not isinstance(texto, str) or not texto.strip():
            raise ValueError("O registro está vazio.")
        if not isinstance(privado, bool):
            raise ValueError("“privado” precisa ser verdadeiro ou falso.")
        data_iso = _iso(data_registro)
        if data_iso is None:
            raise ValueError("Informe a data do registro.")
        itens = [item_de_dict(i) if isinstance(i, dict) else i for i in (itens or [])]
        # "nova_escolhida": na revisão o usuário disse que é outra pessoa (um homônimo, por exemplo).
        novas_escolhidas = [bool(isinstance(p, dict) and p.get("nova_escolhida") is True) for p in (pessoas or [])]
        pessoas = [pessoa_encontrada_de_dict(p) if isinstance(p, dict) else p for p in (pessoas or [])]
        relacoes = [relacao_de_dict(r) if isinstance(r, dict) else r for r in (relacoes or [])]
        valores = [_valores_item(i) for i in itens]     # valida tudo antes de gravar
        with self._escrita() as con:
            agora = _agora()
            rid = con.execute(
                "INSERT INTO registros (criado_em, data_registro, texto, contexto, humor, privado)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (agora, data_iso, texto, _texto_opcional(contexto), _texto_opcional(humor),
                 1 if privado else 0)).lastrowid
            self._busca_gravar("registro", rid, _texto_busca(texto, contexto, humor))
            locais = self._gravar_pessoas_do_registro(rid, pessoas, novas_escolhidas)
            ids_itens = [self._inserir_item(rid, v, locais, agora) for v in valores]
            for relacao in relacoes:
                self._inserir_relacao(rid, relacao, ids_itens, agora)
            qtd = len(ids_itens)
            self._historico_add("criar", "registros", rid,
                                f"registro #{rid} criado com {qtd} {'item' if qtd == 1 else 'itens'}")
        return rid

    def _gravar_pessoas_do_registro(self, rid: int, pessoas: list[PessoaEncontrada],
                                    novas_escolhidas: list[bool] | None = None) -> dict[str, int]:
        """Liga (e cria, se preciso) as pessoas citadas. Devolve chave -> pessoa_id deste registro.

        `novas_escolhidas[i]`: o usuário marcou a pessoa i como nova; não procura no cadastro.
        """
        nome_dela = self._chave_nome_dela()
        locais: dict[str, int] = {}
        novas_escolhidas = novas_escolhidas or [False] * len(pessoas)
        for p, escolhida in zip(pessoas, novas_escolhidas):
            nome, relacao = _texto_opcional(p.nome), _texto_opcional(p.relacao)
            if nome and nome_dela and _chave_pessoa(nome) == nome_dela:
                continue    # é ela: a namorada é implícita
            pid = self._id_existente("pessoas", p.pessoa_id)
            if pid is None:
                if not nome and not relacao:
                    continue
                if not escolhida:
                    pid = self._achar_local(locais, nome, relacao) or self._achar_pessoa(nome, relacao)
                if pid is None:
                    pid = self._inserir_pessoa(nome, relacao, [], "", automatica=True, registro_id=rid)
            self._completar_pessoa(pid, nome, relacao, rid)
            self._con.execute("INSERT OR IGNORE INTO registro_pessoas (registro_id, pessoa_id) VALUES (?, ?)",
                              (rid, pid))
            guardada = self._linha("pessoas", pid)
            chaves = [nome, relacao, p.trecho, guardada["nome"], guardada["relacao"],
                      *json.loads(guardada["apelidos"] or "[]")]
            for k in chaves:
                if _chave_pessoa(k):
                    # A pessoa nova escolhida vale para os itens deste registro, mesmo com nome repetido.
                    if escolhida:
                        locais[_chave_pessoa(k)] = pid
                    else:
                        locais.setdefault(_chave_pessoa(k), pid)
        return locais

    @staticmethod
    def _achar_local(locais: dict[str, int], nome: str | None, relacao: str | None) -> int | None:
        if nome:
            return locais.get(_chave_pessoa(nome))
        return locais.get(_chave_pessoa(relacao))

    def _achar_pessoa(self, nome: str | None, relacao: str | None) -> int | None:
        """Pessoa já cadastrada: por nome/apelido; sem nome, pela relação se for única."""
        cadastro = self._con.execute("SELECT id, nome, relacao, apelidos FROM pessoas ORDER BY id").fetchall()
        k_rel = _chave_pessoa(relacao)
        if nome:
            k = _chave_pessoa(nome)
            for p in cadastro:
                if k == _chave_pessoa(p["nome"]) or k in {_chave_pessoa(a) for a in json.loads(p["apelidos"])}:
                    return p["id"]
            # "a mãe dela, Sandra" quando já existe uma "mãe" ainda sem nome.
            sem_nome = [p for p in cadastro if k_rel and not p["nome"] and _chave_pessoa(p["relacao"]) == k_rel]
            return sem_nome[0]["id"] if len(sem_nome) == 1 else None
        mesma = [p for p in cadastro if k_rel and _chave_pessoa(p["relacao"]) == k_rel]
        return mesma[0]["id"] if len(mesma) == 1 else None

    def _completar_pessoa(self, pid: int, nome: str | None, relacao: str | None, rid: int) -> None:
        """Preenche nome ou relação que faltavam (anotando de qual registro vieram).
        Nunca sobrescreve o que já existe."""
        atual = self._linha("pessoas", pid)
        if nome and not atual["nome"]:
            self._con.execute("UPDATE pessoas SET nome = ?, nome_registro_id = ? WHERE id = ?", (nome, rid, pid))
        if relacao and not atual["relacao"]:
            self._con.execute("UPDATE pessoas SET relacao = ?, relacao_registro_id = ? WHERE id = ?",
                              (relacao, rid, pid))

    def _inserir_pessoa(self, nome: str | None, relacao: str | None, apelidos: list[str],
                        observacoes: str, automatica: bool = False, registro_id: int | None = None) -> int:
        """`automatica`: criada a partir de um registro (e não pelo usuário na tela de pessoas)."""
        pid = self._con.execute(
            "INSERT INTO pessoas (nome, relacao, apelidos, observacoes, criado_em, automatica,"
            " nome_registro_id, relacao_registro_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (nome, relacao, json.dumps(apelidos, ensure_ascii=False), observacoes, _agora(),
             1 if automatica else 0, registro_id if nome else None,
             registro_id if relacao else None)).lastrowid
        self._historico_add("criar", "pessoas", pid, f"pessoa #{pid} criada")
        return pid

    def _resolver_pessoa(self, texto: str | None, locais: dict[str, int]) -> int | None:
        """Texto de item.pessoa ('mãe', 'Mariana', 'Mari') -> pessoa_id, ou None se não casar."""
        k = _chave_pessoa(texto)
        if not k:
            return None
        if k in locais:
            return locais[k]
        cadastro = self._con.execute("SELECT id, nome, relacao, apelidos FROM pessoas ORDER BY id").fetchall()
        for p in cadastro:
            if k == _chave_pessoa(p["nome"]) or k in {_chave_pessoa(a) for a in json.loads(p["apelidos"])}:
                return p["id"]
        mesma = [p for p in cadastro if _chave_pessoa(p["relacao"]) == k]
        return mesma[0]["id"] if len(mesma) == 1 else None

    def _rotulo_pessoa(self, pid: int) -> str | None:
        p = self._linha("pessoas", pid)
        return (p["nome"] or p["relacao"]) if p else None

    def _ajustar_pessoa_do_item(self, v: dict[str, Any], locais: dict[str, int], nome_dela: str) -> None:
        """Acerta v['pessoa'] e v['pessoa_id'] (None nos dois = é dela)."""
        pid = self._id_existente("pessoas", v["pessoa_id"])
        if v["pessoa"] and nome_dela and _chave_pessoa(v["pessoa"]) == nome_dela:
            v["pessoa"], v["pessoa_id"] = None, None
            return
        if pid is None and v["pessoa"]:
            pid = self._resolver_pessoa(v["pessoa"], locais)
        elif pid is not None and not v["pessoa"]:
            v["pessoa"] = self._rotulo_pessoa(pid)
        v["pessoa_id"] = pid

    def _inserir_item(self, rid: int, v: dict[str, Any], locais: dict[str, int], agora: str) -> int:
        v = dict(v)
        self._ajustar_pessoa_do_item(v, locais, self._chave_nome_dela())
        colunas = ("registro_id", *_COLUNAS_ITEM, "criado_em", "atualizado_em")
        iid = self._con.execute(
            f"INSERT INTO itens ({', '.join(colunas)}) VALUES ({_marcas(len(colunas))})",
            (rid, *(v[c] for c in _COLUNAS_ITEM), agora, agora)).lastrowid
        self._busca_item(iid, v)
        if v["pessoa_id"] is not None:
            self._con.execute("INSERT OR IGNORE INTO registro_pessoas (registro_id, pessoa_id) VALUES (?, ?)",
                              (rid, v["pessoa_id"]))
        return iid

    def _inserir_relacao(self, rid: int, r: Relacao, ids_itens: list[int], agora: str) -> None:
        indice = r.item_novo
        novo_id = (ids_itens[indice] if isinstance(indice, int) and not isinstance(indice, bool)
                   and 0 <= indice < len(ids_itens) else None)
        # O item ou registro citado pode ter sido apagado enquanto o usuário revisava: a
        # relação traz trechos dele no `motivo` e não deve ser gravada.
        item_antigo = registro_antigo = None
        if r.item_antigo_id is not None:
            item_antigo = self._id_existente("itens", r.item_antigo_id)
            if item_antigo is None:
                return
            registro_antigo = self._linha("itens", item_antigo)["registro_id"]
        if r.registro_antigo_id is not None:
            registro_antigo = self._id_existente("registros", r.registro_antigo_id)
            if registro_antigo is None:
                return
        status = r.status if r.status in STATUS_RELACAO else "pendente"
        self._con.execute(
            "INSERT INTO relacoes (registro_id, tipo, motivo, item_novo_id, item_antigo_id,"
            " registro_antigo_id, sugestao, status, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, str(r.tipo), str(r.motivo or ""), novo_id, item_antigo, registro_antigo,
             _texto_opcional(r.sugestao), status, agora))

    def registro(self, id: int) -> dict | None:
        with self._trava:
            linha = self._linha("registros", id)
            if linha is None:
                return None
            d = _registro_dict(linha)
            d["itens"] = [_item_de_linha(l) for l in self._con.execute(
                "SELECT * FROM itens WHERE registro_id = ? ORDER BY id", (d["id"],))]
            d["pessoas"] = [_pessoa_dict(l) for l in self._con.execute(
                "SELECT p.* FROM pessoas p JOIN registro_pessoas rp ON rp.pessoa_id = p.id"
                " WHERE rp.registro_id = ? ORDER BY p.id", (d["id"],))]
            d["relacoes"] = self.relacoes(status=None, registro_id=d["id"])
            return d

    def registros(self, limite: int = 50, antes_de: int | None = None) -> list[dict]:
        """Mais recentes primeiro (data do registro, depois id). `antes_de` = id do último da página."""
        limite = max(1, min(int(limite), 1000))
        with self._trava:
            filtro, parametros = "", []
            if antes_de is not None:
                ref = self._linha("registros", antes_de)
                if ref is not None:
                    filtro = "WHERE data_registro < ? OR (data_registro = ? AND id < ?)"
                    parametros = [ref["data_registro"], ref["data_registro"], ref["id"]]
                else:
                    filtro, parametros = "WHERE id < ?", [_id(antes_de)]
            linhas = self._con.execute(
                f"SELECT * FROM registros {filtro} ORDER BY data_registro DESC, id DESC LIMIT ?",
                (*parametros, limite)).fetchall()
            resultado = [_registro_dict(l) | {"qtd_itens": 0, "categorias": [], "pessoas": []} for l in linhas]
            por_id = {r["id"]: r for r in resultado}
            if not por_id:
                return []
            marcas = _marcas(len(por_id))
            for l in self._con.execute(
                    f"SELECT registro_id, categorias FROM itens WHERE registro_id IN ({marcas}) ORDER BY id",
                    tuple(por_id)):
                r = por_id[l["registro_id"]]
                r["qtd_itens"] += 1
                for c in json.loads(l["categorias"] or "[]"):
                    if c not in r["categorias"]:
                        r["categorias"].append(c)
            for l in self._con.execute(
                    "SELECT rp.registro_id, p.id, p.nome, p.relacao FROM registro_pessoas rp"
                    f" JOIN pessoas p ON p.id = rp.pessoa_id WHERE rp.registro_id IN ({marcas}) ORDER BY p.id",
                    tuple(por_id)):
                por_id[l["registro_id"]]["pessoas"].append(
                    {"id": l["id"], "nome": l["nome"], "relacao": l["relacao"]})
            return resultado

    def registros_privados(self) -> set[int]:
        """Ids dos registros marcados "não enviar para a IA"."""
        with self._trava:
            return {l[0] for l in self._con.execute("SELECT id FROM registros WHERE privado != 0")}

    def marcar_privado(self, id: int, privado: bool) -> None:
        """Marca (ou desmarca) o registro como "não enviar para a IA"."""
        if not isinstance(privado, bool):
            raise ValueError("“privado” precisa ser verdadeiro ou falso.")
        with self._escrita() as con:
            linha = self._linha("registros", id)
            if linha is None:
                raise NaoEncontrado(f"Registro #{id} não encontrado.")
            con.execute("UPDATE registros SET privado = ? WHERE id = ?", (1 if privado else 0, linha["id"]))
            self._historico_add("atualizar", "registros", linha["id"],
                                f"registro #{linha['id']}: " + ("não enviar para a IA" if privado
                                                                 else "pode ir para a IA"))

    def apagar_registro(self, id: int) -> None:
        """Apaga o registro, os itens, as relações que o citam e as entradas de busca.

        Pessoas que só existiam por causa dele (sem outra menção, item ou dado próprio)
        também saem: o nome veio do texto apagado.
        """
        with self._escrita() as con:
            if self._linha("registros", id) is None:
                raise NaoEncontrado(f"Registro #{id} não encontrado.")
            rid = _id(id)
            ids_itens = [l[0] for l in con.execute("SELECT id FROM itens WHERE registro_id = ?", (rid,))]
            ligadas = [l[0] for l in con.execute("SELECT pessoa_id FROM registro_pessoas WHERE registro_id = ?",
                                                 (rid,))]
            self._apagar_relacoes([rid], ids_itens)
            self._apagar_dispensados(ids_itens)
            for iid in ids_itens:
                self._busca_apagar("item", iid)
            con.execute("DELETE FROM itens WHERE registro_id = ?", (rid,))
            con.execute("DELETE FROM registro_pessoas WHERE registro_id = ?", (rid,))
            self._busca_apagar("registro", rid)
            con.execute("DELETE FROM registros WHERE id = ?", (rid,))
            self._esquecer_aprendido(rid)
            self._apagar_pessoas_orfas(ligadas)
            self._historico_add("apagar", "registros", rid, f"registro #{rid} apagado")

    def _apagar_relacoes(self, registro_ids: list[int], item_ids: list[int]) -> None:
        """Relações que citam o que vai ser apagado (o `motivo` traz trechos dele)."""
        if registro_ids:
            m = _marcas(len(registro_ids))
            self._con.execute(f"DELETE FROM relacoes WHERE registro_id IN ({m}) OR registro_antigo_id IN ({m})",
                              (*registro_ids, *registro_ids))
        if item_ids:
            m = _marcas(len(item_ids))
            self._con.execute(f"DELETE FROM relacoes WHERE item_novo_id IN ({m}) OR item_antigo_id IN ({m})",
                              (*item_ids, *item_ids))

    def _esquecer_aprendido(self, rid: int) -> None:
        """Nome ou relação que o banco tirou do texto do registro `rid` (já apagado).

        Se outro registro da mesma pessoa também cita o nome, ele fica (e passa a "vir" desse
        outro registro); senão, sai. A pessoa nunca fica sem nome e sem relação.
        """
        suspeitas = self._con.execute(
            "SELECT * FROM pessoas WHERE nome_registro_id = ? OR relacao_registro_id = ?", (rid, rid)).fetchall()
        for p in suspeitas:
            textos = [(l["id"], normalizar(l["texto"])) for l in self._con.execute(
                "SELECT id, texto FROM registros WHERE id IN (SELECT registro_id FROM registro_pessoas"
                " WHERE pessoa_id = ? UNION SELECT registro_id FROM itens WHERE pessoa_id = ?) ORDER BY id",
                (p["id"], p["id"]))]

            def fonte(valor: str | None) -> int | None:
                chave = normalizar(_RE_PARENTESE.sub(" ", valor or ""))
                if not chave:
                    return None
                rx = re.compile(r"(?<!\w)" + re.escape(chave) + r"(?!\w)")
                return next((i for i, t in textos if rx.search(t)), None)

            nome, relacao = p["nome"], p["relacao"]
            nome_rid, relacao_rid = p["nome_registro_id"], p["relacao_registro_id"]
            if nome_rid == rid:
                nome_rid = fonte(nome)
                nome = nome if nome_rid is not None else None
            if relacao_rid == rid:
                relacao_rid = fonte(relacao)
                relacao = relacao if relacao_rid is not None else None
            if not nome and not relacao:
                # Sem outro dado, a pessoa só existia por causa do registro: se ninguém mais a
                # cita, _apagar_pessoas_orfas tira; se citam, fica como estava.
                nome, relacao = p["nome"], p["relacao"]
                nome_rid = relacao_rid = None
            self._con.execute("UPDATE pessoas SET nome = ?, relacao = ?, nome_registro_id = ?,"
                              " relacao_registro_id = ? WHERE id = ?",
                              (nome, relacao, nome_rid, relacao_rid, p["id"]))

    def _apagar_dispensados(self, item_ids: Iterable[int] = (), pessoa_id: int | None = None) -> None:
        """Alertas dispensados que citam o que foi apagado (a chave traz o id e a data)."""
        padroes: list[str] = []
        for iid in item_ids:
            padroes += [f"{origem}:{iid}:%" for origem in ("data", "evento", "pendencia", "depois")]
            padroes.append(f"estado:{iid}")
        if pessoa_id is not None:
            padroes.append(f"pessoa:{pessoa_id}:%")
        for padrao in padroes:
            self._con.execute("DELETE FROM alertas_dispensados WHERE chave LIKE ?", (padrao,))

    def _apagar_pessoas_orfas(self, pessoa_ids: Iterable[int]) -> None:
        """Só pessoas criadas a partir de registros e que o usuário nunca editou."""
        for pid in pessoa_ids:
            p = self._linha("pessoas", pid)
            if p is None or not p["automatica"] or p["observacoes"] or p["aniversario"]:
                continue
            citada = self._con.execute(
                "SELECT 1 FROM registro_pessoas WHERE pessoa_id = ? UNION ALL"
                " SELECT 1 FROM itens WHERE pessoa_id = ? LIMIT 1", (pid, pid)).fetchone()
            if citada is None:
                self._con.execute("DELETE FROM pessoas WHERE id = ?", (pid,))
                self._apagar_dispensados(pessoa_id=pid)
                self._historico_add("apagar", "pessoas", pid, f"pessoa #{pid} apagada (sem outras menções)")

    # ------------------------------------------------------------ itens

    def item(self, id: int) -> Item | None:
        with self._trava:
            linha = self._linha("itens", id)
            return _item_de_linha(linha) if linha else None

    def itens(self, tipo: TipoItem | None = None, categoria: str | None = None,
              pessoa_id: int | None | EllipsisType = ..., status: str | None = "ativo",
              desde: date | None = None) -> list[Item]:
        """Filtra itens. pessoa_id=... (padrão) = de qualquer pessoa; None = dela; int = da pessoa.

        `desde` compara com a data do registro. Mais recentes primeiro.
        """
        condicoes, parametros = [], []
        if tipo is not None:
            condicoes.append("i.tipo = ?")
            parametros.append(TipoItem(tipo).value)
        if categoria is not None:
            condicoes.append("EXISTS (SELECT 1 FROM json_each(i.categorias) WHERE json_each.value = ?)")
            parametros.append(categoria)
        if pessoa_id is None:
            condicoes.append("i.pessoa_id IS NULL AND (i.pessoa IS NULL OR i.pessoa = '')")
        elif pessoa_id is not ...:
            condicoes.append("i.pessoa_id = ?")
            parametros.append(_id(pessoa_id))
        if status is not None:
            condicoes.append("i.status = ?")
            parametros.append(status)
        if desde is not None:
            condicoes.append("r.data_registro >= ?")
            parametros.append(_iso(desde))
        onde = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
        with self._trava:
            linhas = self._con.execute(
                f"SELECT i.* FROM itens i JOIN registros r ON r.id = i.registro_id {onde}"
                " ORDER BY r.data_registro DESC, i.id DESC", parametros).fetchall()
        return [_item_de_linha(l) for l in linhas]

    def atualizar_item(self, id: int, **campos) -> None:
        desconhecidos = set(campos) - _EDITAVEIS_ITEM
        if desconhecidos:
            raise ValueError("Campos que não podem ser alterados: " + ", ".join(sorted(desconhecidos)))
        if not campos:
            return
        with self._escrita() as con:
            linha = self._linha("itens", id)
            if linha is None:
                raise NaoEncontrado(f"Item #{id} não encontrado.")
            item = _item_de_linha(linha)
            for chave, valor in campos.items():
                setattr(item, chave, valor)
            if "trecho" in campos:
                texto = self._linha("registros", item.registro_id)["texto"]
                if not item.trecho or item.trecho not in texto:
                    raise ValueError("O trecho precisa ser parte do texto do registro.")
            v = _valores_item(item)
            if "pessoa_id" in campos and v["pessoa_id"] is not None:
                if isinstance(v["pessoa_id"], bool) or not self._linha("pessoas", v["pessoa_id"]):
                    raise ValueError("Pessoa não encontrada.")
                if "pessoa" not in campos:
                    v["pessoa"] = self._rotulo_pessoa(v["pessoa_id"])
            elif "pessoa_id" in campos and "pessoa" not in campos:
                v["pessoa"] = None                     # pessoa_id=None: passa a ser dela
            elif "pessoa" in campos and "pessoa_id" not in campos:
                v["pessoa_id"] = None
                self._ajustar_pessoa_do_item(v, {}, self._chave_nome_dela())
            atribuicoes = ", ".join(f"{c} = ?" for c in _COLUNAS_ITEM)
            con.execute(f"UPDATE itens SET {atribuicoes}, atualizado_em = ? WHERE id = ?",
                        (*(v[c] for c in _COLUNAS_ITEM), _agora(), item.id))
            if _CAMPOS_BUSCA_ITEM & set(campos):
                self._busca_item(item.id, v, substituir=True)
            self._historico_add("atualizar", "itens", item.id,
                                f"item #{item.id} atualizado: " + ", ".join(sorted(campos)))

    def acrescentar_item(self, registro_id: int, item: Item) -> int:
        """Item novo num registro que já existe (o trecho precisa ser parte do texto dele)."""
        v = _valores_item(item_de_dict(item) if isinstance(item, dict) else item)
        with self._escrita() as con:
            linha = self._linha("registros", registro_id)
            if linha is None:
                raise NaoEncontrado(f"Registro #{registro_id} não encontrado.")
            if not v["trecho"] or v["trecho"] not in linha["texto"]:
                raise ValueError("O trecho precisa ser parte do texto do registro.")
            iid = self._inserir_item(linha["id"], v, {}, _agora())
            self._historico_add("criar", "itens", iid, f"item #{iid} criado no registro #{linha['id']}")
        return iid

    def apagar_item(self, id: int) -> None:
        with self._escrita() as con:
            if self._linha("itens", id) is None:
                raise NaoEncontrado(f"Item #{id} não encontrado.")
            iid = _id(id)
            self._apagar_relacoes([], [iid])
            self._apagar_dispensados([iid])
            self._busca_apagar("item", iid)
            con.execute("DELETE FROM itens WHERE id = ?", (iid,))
            self._historico_add("apagar", "itens", iid, f"item #{iid} apagado")

    # ------------------------------------------------------------ pessoas

    def pessoas(self) -> list[dict]:
        with self._trava:
            return [_pessoa_dict(l) for l in self._con.execute("SELECT * FROM pessoas ORDER BY id")]

    def pessoa(self, id: int) -> dict | None:
        with self._trava:
            linha = self._linha("pessoas", id)
            return _pessoa_dict(linha) if linha else None

    def criar_pessoa(self, nome: str | None, relacao: str | None, apelidos=(),
                     observacoes: str = "") -> int:
        nome, relacao = _texto_opcional(nome), _texto_opcional(relacao)
        if not nome and not relacao:
            raise ValueError("Informe o nome ou a relação da pessoa.")
        with self._escrita():
            pid = self._inserir_pessoa(nome, relacao, _lista_apelidos(apelidos), str(observacoes or ""))
        return pid

    def atualizar_pessoa(self, id: int, **campos) -> None:
        desconhecidos = set(campos) - _EDITAVEIS_PESSOA
        if desconhecidos:
            raise ValueError("Campos que não podem ser alterados: " + ", ".join(sorted(desconhecidos)))
        if not campos:
            return
        with self._escrita() as con:
            linha = self._linha("pessoas", id)
            if linha is None:
                raise NaoEncontrado(f"Pessoa #{id} não encontrada.")
            novo = {"nome": linha["nome"], "relacao": linha["relacao"], "apelidos": linha["apelidos"],
                    "observacoes": linha["observacoes"], "aniversario": linha["aniversario"]}
            for chave, valor in campos.items():
                if chave in ("nome", "relacao"):
                    novo[chave] = _texto_opcional(valor)
                elif chave == "apelidos":
                    novo[chave] = json.dumps(_lista_apelidos(valor), ensure_ascii=False)
                elif chave == "observacoes":
                    novo[chave] = str(valor or "")
                else:
                    novo[chave] = _iso(valor)
            if not novo["nome"] and not novo["relacao"]:
                raise ValueError("A pessoa precisa de um nome ou de uma relação.")
            # Editada pelo usuário: deixa de ser "automática" e não some junto com registros.
            con.execute("UPDATE pessoas SET nome = ?, relacao = ?, apelidos = ?, observacoes = ?,"
                        " aniversario = ?, automatica = 0 WHERE id = ?", (*novo.values(), linha["id"]))
            # O que o usuário escreveu é dele: não sai junto com o registro de onde veio.
            if "nome" in campos:
                con.execute("UPDATE pessoas SET nome_registro_id = NULL WHERE id = ?", (linha["id"],))
            if "relacao" in campos:
                con.execute("UPDATE pessoas SET relacao_registro_id = NULL WHERE id = ?", (linha["id"],))
            self._historico_add("atualizar", "pessoas", linha["id"],
                                f"pessoa #{linha['id']} atualizada: " + ", ".join(sorted(campos)))

    def apagar_pessoa(self, id: int) -> None:
        """Os itens da pessoa ficam, com pessoa_id NULL e pessoa = nome (ou a relação)."""
        with self._escrita() as con:
            linha = self._linha("pessoas", id)
            if linha is None:
                raise NaoEncontrado(f"Pessoa #{id} não encontrada.")
            pid = linha["id"]
            rotulo = linha["nome"] or linha["relacao"]
            afetados = con.execute("SELECT * FROM itens WHERE pessoa_id = ?", (pid,)).fetchall()
            con.execute("UPDATE itens SET pessoa_id = NULL, pessoa = ?, atualizado_em = ? WHERE pessoa_id = ?",
                        (rotulo, _agora(), pid))
            for l in afetados:
                v = dict(l) | {"pessoa": rotulo}
                self._busca_item(l["id"], v, substituir=True)
            con.execute("DELETE FROM registro_pessoas WHERE pessoa_id = ?", (pid,))
            con.execute("DELETE FROM pessoas WHERE id = ?", (pid,))
            self._apagar_dispensados(pessoa_id=pid)
            self._historico_add("apagar", "pessoas", pid, f"pessoa #{pid} apagada")

    def mencoes(self, pessoa_id: int, limite: int = 5) -> list[dict]:
        """Últimos registros que citam a pessoa: {registro_id, data_registro, trecho}."""
        with self._trava:
            p = self._linha("pessoas", pessoa_id)
            if p is None:
                return []
            pid = p["id"]
            linhas = self._con.execute(
                "SELECT id, data_registro, texto FROM registros WHERE id IN ("
                " SELECT registro_id FROM registro_pessoas WHERE pessoa_id = ?"
                " UNION SELECT registro_id FROM itens WHERE pessoa_id = ?)"
                " ORDER BY data_registro DESC, id DESC LIMIT ?", (pid, pid, max(1, int(limite)))).fetchall()
            chaves = [normalizar(p["nome"] or ""), normalizar(_RE_PARENTESE.sub(" ", p["relacao"] or "")),
                      *(normalizar(a) for a in json.loads(p["apelidos"] or "[]"))]
            resultado = []
            for l in linhas:
                do_item = self._con.execute(
                    "SELECT trecho FROM itens WHERE registro_id = ? AND pessoa_id = ? AND trecho != ''"
                    " ORDER BY id LIMIT 1", (l["id"], pid)).fetchone()
                resultado.append({
                    "registro_id": l["id"],
                    "data_registro": _data(l["data_registro"]),
                    "trecho": do_item["trecho"] if do_item else _frase_com(l["texto"], chaves),
                })
            return resultado

    # ------------------------------------------------------------ busca

    def buscar_texto(self, consulta: str, limite: int = 20) -> list[dict]:
        """Busca por termos soltos (OR, com prefixo, sem acento). Nunca lança por sintaxe.

        Devolve [{tipo: "item"|"registro", ref_id, score}], score maior = melhor.
        """
        if not isinstance(consulta, str):
            return []
        # Cada termo vira uma string FTS5 entre aspas: AND, NOT, (, *, " etc. não são operadores.
        unicos = [t for t in dict.fromkeys(termos(consulta)) if any(c.isalnum() for c in t)][:_MAX_TERMOS]
        if not unicos:
            return []
        expressao = " OR ".join('"' + t.replace('"', '""') + '"*' for t in unicos)
        try:
            limite = max(1, min(int(limite), 500))
            with self._trava:
                linhas = self._con.execute(
                    "SELECT tipo, ref_id, bm25(busca_fts) AS nota FROM busca_fts WHERE busca_fts MATCH ?"
                    " ORDER BY nota LIMIT ?", (expressao, limite)).fetchall()
        except (sqlite3.Error, ValueError, TypeError):
            return []
        return [{"tipo": l["tipo"], "ref_id": int(l["ref_id"]), "score": -float(l["nota"])} for l in linhas]

    # ------------------------------------------------------------ relações

    def relacoes(self, status: str | None = "pendente", registro_id: int | None = None) -> list[Relacao]:
        condicoes, parametros = [], []
        if status is not None:
            condicoes.append("status = ?")
            parametros.append(status)
        if registro_id is not None:
            condicoes.append("registro_id = ?")
            parametros.append(_id(registro_id))
        onde = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
        with self._trava:
            return [_relacao_de_linha(l) for l in
                    self._con.execute(f"SELECT * FROM relacoes {onde} ORDER BY id", parametros)]

    def resolver_relacao(self, id: int, status: str) -> None:
        if status not in STATUS_RELACAO:
            raise ValueError(f"Situação de relação desconhecida: {status!r}.")
        with self._escrita() as con:
            if self._linha("relacoes", id) is None:
                raise NaoEncontrado(f"Relação #{id} não encontrada.")
            con.execute("UPDATE relacoes SET status = ? WHERE id = ?", (status, _id(id)))
            self._historico_add("resolver", "relacoes", _id(id), f"relação #{_id(id)} marcada como {status}")

    # ------------------------------------------------------------ alertas

    def dispensar_alerta(self, chave: str) -> None:
        chave = _texto_opcional(chave)
        if not chave:
            raise ValueError("Chave de alerta vazia.")
        with self._escrita() as con:
            con.execute("INSERT OR REPLACE INTO alertas_dispensados (chave, quando) VALUES (?, ?)",
                        (chave, _agora()))

    def alertas_dispensados(self) -> set[str]:
        with self._trava:
            return {l[0] for l in self._con.execute("SELECT chave FROM alertas_dispensados")}

    # ------------------------------------------------------------ histórico e exportação

    def historico(self, limite: int = 100) -> list[dict]:
        with self._trava:
            return self._historico(max(1, int(limite)))

    def _historico(self, limite: int | None) -> list[dict]:
        sql = "SELECT * FROM historico ORDER BY id DESC" + (" LIMIT ?" if limite else "")
        return [{"id": l["id"], "quando": _data_hora(l["quando"]), "acao": l["acao"], "tabela": l["tabela"],
                 "linha_id": l["linha_id"], "resumo": l["resumo"]}
                for l in self._con.execute(sql, (limite,) if limite else ())]

    def exportar(self) -> dict:
        """Tudo, em estruturas prontas para json.dumps (sem criptografia: a tela avisa)."""
        with self._trava:
            registros = []
            for (rid,) in self._con.execute("SELECT id FROM registros ORDER BY data_registro, id").fetchall():
                r = self.registro(rid)
                r["pessoas"] = [{"id": p["id"], "nome": p["nome"], "relacao": p["relacao"]} for p in r["pessoas"]]
                registros.append(r)
            dados = {
                "app": "entre-nos",
                "versao": cfg.VERSAO,
                "esquema": ESQUEMA_VERSAO,
                "exportado_em": datetime.now(),
                "config": config_sem_segredos(self.config()),     # sem chave de IA e sem PIN da IA
                "pessoas": self.pessoas(),
                "registros": registros,
                "alertas_dispensados": [dict(l) for l in self._con.execute(
                    "SELECT chave, quando FROM alertas_dispensados ORDER BY chave")],
                "historico": self._historico(None),
            }
        return para_json(dados)

    # ------------------------------------------------------------ restaurar (seção 18.2)

    def resumo(self) -> dict:
        """{registros, pessoas, primeiro_registro, ultimo_registro}: o que a tela mostra de um
        diário restaurado (datas do registro, não de criação)."""
        with self._trava:
            qtd, primeiro, ultimo = self._con.execute(
                "SELECT count(*), min(data_registro), max(data_registro) FROM registros").fetchone()
            pessoas = self._con.execute("SELECT count(*) FROM pessoas").fetchone()[0]
        return {"registros": qtd, "pessoas": pessoas, "primeiro_registro": _data(primeiro),
                "ultimo_registro": _data(ultimo)}

    def conferir_integridade(self) -> None:
        """ValueError se o diário não está inteiro: `PRAGMA integrity_check`, índice de busca,
        tabelas e colunas do esquema atual (e nada além dele: nem gatilho, nem visão, nem coluna
        calculada), ajustes e datas legíveis. Não muda nada."""
        with self._trava:
            try:
                if [l[0] for l in self._con.execute("PRAGMA integrity_check")] != ["ok"]:
                    raise ValueError(BANCO_DANIFICADO)
                # Um gatilho ou uma visão escondidos num backup (feito por quem sabia o PIN dele)
                # ficariam para sempre, mesmo depois de trocar o PIN, e poderiam tirar o 🔒 de todo
                # registro novo ou fingir que apagou.
                esperados = _objetos_do_esquema()
                for tipo, nome in self._con.execute("SELECT type, name FROM sqlite_master").fetchall():
                    if (tipo, nome) not in esperados and not (tipo == "table" and nome in _ESTATISTICAS_SQLITE):
                        raise ValueError(BANCO_DANIFICADO)
                for tabela, colunas in _ESTRUTURA.items():
                    self._con.execute(f"SELECT {', '.join(colunas)} FROM {tabela} LIMIT 1").fetchall()
                    # hidden 2 e 3: coluna calculada (GENERATED), que não guarda o que é gravado nela.
                    if any(l["hidden"] in (2, 3) for l in self._con.execute(f"PRAGMA table_xinfo({tabela})")):
                        raise ValueError(BANCO_DANIFICADO)
                # O FTS5 não entra no integrity_check: tem conferência própria (não grava nada).
                self._con.execute("INSERT INTO busca_fts(busca_fts) VALUES ('integrity-check')")
                self.config()
                self.resumo()
            except (sqlite3.Error, ValueError, TypeError, KeyError):
                raise ValueError(BANCO_DANIFICADO) from None

    def maiores_ids(self) -> dict[str, int]:
        """O maior id que cada tabela já usou (inclusive de linhas apagadas)."""
        with self._trava:
            try:
                return {l[0]: int(l[1]) for l in self._con.execute("SELECT name, seq FROM sqlite_sequence")
                        if l[0] in _TABELAS_COM_ID and isinstance(l[1], int)}
            except sqlite3.Error:
                return {}

    def garantir_ids_acima(self, maiores: dict[str, int]) -> None:
        """Os próximos ids de cada tabela passam dos de `maiores` (de outro diário).

        Ao restaurar uma cópia antiga deste mesmo diário, os backups automáticos (mesmo PIN)
        têm registros criados depois da cópia. Sem isto, um registro novo ganharia o id de um
        deles, e apagá-lo apagaria o outro das cópias. Não muda nenhum dado.
        """
        with self._escrita() as con:
            for tabela in _TABELAS_COM_ID:
                minimo = maiores.get(tabela)
                if isinstance(minimo, bool) or not isinstance(minimo, int) or minimo <= 0:
                    continue
                linha = con.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (tabela,)).fetchone()
                if linha is None:
                    con.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (tabela, minimo))
                elif (linha[0] or 0) < minimo:
                    con.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (minimo, tabela))

    # ------------------------------------------------------------ importar JSON (seção 18.2)

    def importar_json(self, dados: Any, previa: bool = False) -> dict:
        """Junta ao diário o JSON de `exportar()` (texto, bytes, o dict já lido, ou o resultado
        de `validar_exportacao`, que o servidor prepara fora da trava do app).

        Registro repetido (mesma data e mesmo texto, sem contar maiúsculas, acentos, espaços e
        pontuação) fica de fora, mas o 🔒 do arquivo passa para o daqui (só liga, nunca
        desliga); pessoa casa por nome, apelido ou relação, senão é criada; pessoa casada ganha
        o que falta; itens e relações ganham ids novos e relação que aponta para algo que não
        veio é descartada; `privado` é mantido. Ajustes, PIN da IA, chaves, avisos dispensados e
        histórico do arquivo não entram.

        Devolve {registros_novos, registros_repetidos, registros_repetidos_no_arquivo,
        registros_marcados_privados, pessoas_novas, pessoas_existentes, pessoas_completadas,
        itens, detalhes_pessoas, avisos} e, gravando, também {importados}. `detalhes_pessoas` =
        {novas: [{rotulo, parecida_com}], existentes: [{arquivo, daqui}], completadas: [{rotulo,
        campos, apelidos}]} (até MAX_NOMES_PREVIA em cada lista). `previa=True`: faz tudo numa
        transação e a desfaz (o resumo é exatamente o da importação, e nada é gravado). Arquivo
        com problema: ValueError antes de mexer em qualquer coisa.
        """
        if not isinstance(previa, bool):
            raise ValueError("“previa” precisa ser verdadeiro ou falso.")
        if isinstance(dados, _Exportacao):
            exportacao = dados
        else:
            if not isinstance(dados, dict):
                dados = ler_exportacao(dados)
            exportacao = validar_exportacao(dados)
        with self._trava:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                resumo = self._juntar(exportacao)
                if previa:
                    self._con.execute("ROLLBACK")          # a prévia desfaz tudo e não salva
                    return resumo
                self._con.execute("COMMIT")
            except BaseException:
                if self._con.in_transaction:
                    self._con.execute("ROLLBACK")
                raise
            if self.ao_salvar is not None:
                self.ao_salvar(self.para_bytes())
        return {**resumo, "importados": resumo["registros_novos"]}

    def _juntar(self, exp: _Exportacao) -> dict:
        """O trabalho de `importar_json`, dentro da transação aberta por ele."""
        con, agora = self._con, _agora()
        existentes: dict[tuple[Any, str], int] = {}
        privado_aqui: dict[int, bool] = {}
        for l in con.execute("SELECT id, data_registro, texto, privado FROM registros ORDER BY id"):
            existentes.setdefault(_chave_registro(l["data_registro"], l["texto"]), l["id"])
            privado_aqui[l["id"]] = bool(l["privado"])
        de_antes = set(privado_aqui)                    # registros que já estavam no diário

        # Pessoas: as do cadastro do arquivo e, se faltar alguma, as citadas nos registros novos.
        cadastro = _CadastroParaCasar(con)
        de_antes_pessoas = set(cadastro.nome)
        mapa_pessoas: dict[Any, int] = {}
        criadas: dict[int, _PessoaImportada] = {}
        completadas: dict[int, set[str]] = {}        # pessoa do diário -> campos preenchidos agora
        apelidos_novos: dict[int, list[str]] = {}
        casadas: set[int] = set()
        novas_detalhe: list[dict] = []
        existentes_detalhe: list[dict] = []

        def rotulo_daqui(pid: int) -> str:
            linha = self._linha("pessoas", pid)
            return _rotulo(linha["nome"], linha["relacao"]) if linha else "Sem nome"

        def parecida(p: _PessoaImportada) -> str | None:
            """Alguém que já estava no diário com o mesmo nome (ou apelido) de uma pessoa que vai
            entrar como nova: talvez seja a mesma, com a relação escrita de outro jeito."""
            k = _chave_pessoa(p.nome)
            if not k:
                return None
            for pid in sorted({*cadastro.por_nome.get(k, ()), *cadastro.por_apelido.get(k, ())}):
                if pid in de_antes_pessoas:
                    return rotulo_daqui(pid)
            return None

        def pessoa_de(p: _PessoaImportada) -> int:
            if p.chave in mapa_pessoas:
                return mapa_pessoas[p.chave]
            pid = cadastro.casar(p)
            if pid is None:
                semelhante = parecida(p)
                pid = self._inserir_pessoa_importada(p, agora)
                criadas[pid] = p
                novas_detalhe.append({"rotulo": _rotulo(p.nome, p.relacao), "parecida_com": semelhante})
                cadastro.acrescentar(pid, p.nome, p.relacao, p.apelidos)
            else:
                if pid not in criadas and pid not in casadas:
                    casadas.add(pid)
                    existentes_detalhe.append({"arquivo": _rotulo(p.nome, p.relacao), "daqui": rotulo_daqui(pid)})
                preenchidos, apelidos = self._completar_pessoa_importada(pid, p)
                if preenchidos and pid not in criadas:
                    completadas.setdefault(pid, set()).update(preenchidos)
                    apelidos_novos.setdefault(pid, []).extend(apelidos)
                if preenchidos:
                    linha = self._linha("pessoas", pid)
                    cadastro.acrescentar(pid, linha["nome"], linha["relacao"], json.loads(linha["apelidos"] or "[]"))
            cadastro.reivindicadas.add(pid)
            mapa_pessoas[p.chave] = pid
            return pid

        for p in exp.pessoas:
            pessoa_de(p)

        # Registros: repetidos ficam de fora (mas os ids deles valem para as relações dos novos).
        mapa_registros: dict[int, int] = {}
        mapa_itens: dict[int, tuple[int, int]] = {}      # id no arquivo -> (id aqui, registro aqui)
        novos: list[tuple[_RegistroImportado, int]] = []
        # pessoa -> registros novos que a citam, na ordem (dict: conferir é O(1), não O(n)).
        citadas: dict[int, dict[int, None]] = {}
        chaves_do_arquivo: set[tuple[Any, str]] = set()
        marcados: set[int] = set()                       # registros daqui que ganharam o 🔒
        repetidos = no_arquivo = qtd_itens = sem_trecho = 0

        def ligar(rid: int, pid: int) -> None:
            con.execute("INSERT OR IGNORE INTO registro_pessoas (registro_id, pessoa_id) VALUES (?, ?)", (rid, pid))
            citadas.setdefault(pid, {})[rid] = None

        colunas = ("registro_id", *_COLUNAS_ITEM, "criado_em", "atualizado_em")
        inserir_item = f"INSERT INTO itens ({', '.join(colunas)}) VALUES ({_marcas(len(colunas))})"
        for r in exp.registros:
            chave = _chave_registro(r.data_registro, r.texto)
            if chave in existentes:
                if chave in chaves_do_arquivo:
                    no_arquivo += 1                      # repetido dentro do próprio arquivo
                else:
                    repetidos += 1
                chaves_do_arquivo.add(chave)
                daqui = existentes[chave]
                mapa_registros.setdefault(r.id, daqui)
                self._casar_itens_repetidos(daqui, r, mapa_itens)
                # O 🔒 marcado no outro computador vale aqui também (só liga, nunca desliga).
                if r.privado and not privado_aqui.get(daqui):
                    con.execute("UPDATE registros SET privado = 1 WHERE id = ?", (daqui,))
                    privado_aqui[daqui] = True
                    if daqui in de_antes:
                        marcados.add(daqui)
                continue
            chaves_do_arquivo.add(chave)
            rid = con.execute(
                "INSERT INTO registros (criado_em, data_registro, texto, contexto, humor, privado)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (r.criado_em or agora, r.data_registro, r.texto, r.contexto, r.humor, 1 if r.privado else 0)).lastrowid
            self._busca_gravar("registro", rid, _texto_busca(r.texto, r.contexto, r.humor))
            existentes[chave] = rid
            privado_aqui[rid] = r.privado
            mapa_registros[r.id] = rid
            novos.append((r, rid))
            sem_trecho += r.itens_sem_trecho
            for p in r.pessoas:
                ligar(rid, pessoa_de(p))
            for item in r.itens:
                v = dict(item.valores)
                antigo = v["pessoa_id"]
                # Id de pessoa do outro diário nunca vale aqui: ou vira o daqui, ou nenhum.
                v["pessoa_id"] = mapa_pessoas.get(antigo) if antigo is not None else None
                if v["pessoa_id"] is not None and not v["pessoa"]:
                    v["pessoa"] = self._rotulo_pessoa(v["pessoa_id"])
                iid = con.execute(inserir_item, (rid, *(v[c] for c in _COLUNAS_ITEM), item.criado_em or agora,
                                                 agora)).lastrowid
                self._busca_item(iid, v)
                if v["pessoa_id"] is not None:
                    ligar(rid, v["pessoa_id"])
                if item.id is not None:
                    mapa_itens[item.id] = (iid, rid)
                qtd_itens += 1

        relacoes_fora = 0
        for r, rid in novos:
            for rel in r.relacoes:
                if not self._inserir_relacao_importada(rid, rel, mapa_registros, mapa_itens, agora):
                    relacoes_fora += 1

        self._marcar_aprendidas(criadas, completadas, {pid: list(rids) for pid, rids in citadas.items()})
        if novos or criadas or completadas or marcados:
            # Só contagens: nada de nome ou texto no histórico.
            qtd = len(novos)
            partes = [f"{qtd} {'registro' if qtd == 1 else 'registros'}"]
            if completadas:
                partes.append(f"{len(completadas)} "
                              f"{'pessoa completada' if len(completadas) == 1 else 'pessoas completadas'}")
            if marcados:
                partes.append(f"{len(marcados)} {'registro marcado' if len(marcados) == 1 else 'registros marcados'} "
                              "para não ir para a IA")
            self._historico_add("importar", "registros", None, "importação: " + ", ".join(partes))
        completadas_detalhe = [
            {"rotulo": rotulo_daqui(pid), "campos": sorted(campos), "apelidos": apelidos_novos.get(pid, [])}
            for pid, campos in completadas.items()]
        return {"registros_novos": len(novos), "registros_repetidos": repetidos,
                "registros_repetidos_no_arquivo": no_arquivo, "registros_marcados_privados": len(marcados),
                "pessoas_novas": len(criadas), "pessoas_existentes": len(casadas),
                "pessoas_completadas": len(completadas), "itens": qtd_itens,
                "detalhes_pessoas": {"novas": novas_detalhe[:MAX_NOMES_PREVIA],
                                     "existentes": existentes_detalhe[:MAX_NOMES_PREVIA],
                                     "completadas": completadas_detalhe[:MAX_NOMES_PREVIA]},
                "avisos": _avisos_da_importacao(exp, len(novos), sem_trecho, relacoes_fora, len(marcados),
                                                len(completadas), no_arquivo)}

    def _inserir_pessoa_importada(self, p: _PessoaImportada, agora: str) -> int:
        """Pessoa nova vinda do arquivo. `automatica` e de qual registro veio o nome são
        acertados depois, em `_marcar_aprendidas` (quando algum registro novo a cita)."""
        return self._con.execute(
            "INSERT INTO pessoas (nome, relacao, apelidos, observacoes, aniversario, criado_em, automatica)"
            " VALUES (?, ?, ?, ?, ?, ?, 0)",
            (p.nome, p.relacao, json.dumps(p.apelidos, ensure_ascii=False), p.observacoes, p.aniversario,
             agora)).lastrowid

    def _completar_pessoa_importada(self, pid: int, p: _PessoaImportada) -> tuple[set[str], list[str]]:
        """Pessoa do diário que casou com uma do arquivo: preenche só o que falta (nunca
        sobrescreve) e junta os apelidos. Devolve os campos preenchidos e os apelidos novos."""
        atual = self._linha("pessoas", pid)
        mudar: dict[str, Any] = {}
        if p.nome and not atual["nome"]:
            mudar["nome"] = p.nome
        if p.relacao and not atual["relacao"]:
            mudar["relacao"] = p.relacao
        if p.aniversario and not atual["aniversario"]:
            mudar["aniversario"] = p.aniversario
        if p.observacoes.strip() and not (atual["observacoes"] or "").strip():
            mudar["observacoes"] = p.observacoes
        apelidos = json.loads(atual["apelidos"] or "[]")
        conhecidos = {_chave_pessoa(a) for a in apelidos} | {_chave_pessoa(atual["nome"]), _chave_pessoa(p.nome)}
        faltam = [a for a in p.apelidos if _chave_pessoa(a) not in conhecidos]
        if faltam:
            mudar["apelidos"] = json.dumps(apelidos + faltam, ensure_ascii=False)
        if mudar:
            self._con.execute(f"UPDATE pessoas SET {', '.join(f'{c} = ?' for c in mudar)} WHERE id = ?",
                              (*mudar.values(), pid))
        return set(mudar), faltam

    def _casar_itens_repetidos(self, rid: int, r: _RegistroImportado, mapa_itens: dict[int, tuple[int, int]]) -> None:
        """Registro do arquivo que já está no diário: os itens dele casam com os de lá (mesmo
        tipo e trecho, e de preferência o mesmo resumo), para as relações dos registros novos
        que apontam para eles continuarem valendo."""
        daqui = self._con.execute("SELECT id, tipo, trecho, resumo FROM itens WHERE registro_id = ? ORDER BY id",
                                  (rid,)).fetchall()
        usados: set[int] = set()
        for item in r.itens:
            if item.id is None or item.id in mapa_itens:
                continue
            v = item.valores
            mesmos = [l for l in daqui if l["id"] not in usados and l["tipo"] == v["tipo"] and l["trecho"] == v["trecho"]]
            iguais = [l for l in mesmos if l["resumo"] == v["resumo"]]
            if iguais or mesmos:
                achado = (iguais or mesmos)[0]["id"]
                usados.add(achado)
                mapa_itens[item.id] = (achado, rid)

    def _inserir_relacao_importada(self, rid: int, rel: _RelacaoImportada, mapa_registros: dict[int, int],
                                   mapa_itens: dict[int, tuple[int, int]], agora: str) -> bool:
        """Grava a relação com os ids daqui. False (e nada gravado) quando ela aponta para
        registro ou item que não veio no arquivo (o `motivo` cita esse outro registro)."""
        novo_id = None
        if rel.item_novo is not None:
            novo = mapa_itens.get(rel.item_novo)
            if novo is None or novo[1] != rid:
                return False
            novo_id = novo[0]
        item_antigo = registro_antigo = None
        if rel.item_antigo_id is not None:
            antigo = mapa_itens.get(rel.item_antigo_id)
            if antigo is None:
                return False
            item_antigo, registro_antigo = antigo
        if rel.registro_antigo_id is not None:
            registro_antigo = mapa_registros.get(rel.registro_antigo_id)
            if registro_antigo is None:
                return False
        self._con.execute(
            "INSERT INTO relacoes (registro_id, tipo, motivo, item_novo_id, item_antigo_id, registro_antigo_id,"
            " sugestao, status, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, rel.tipo, rel.motivo, novo_id, item_antigo, registro_antigo, rel.sugestao, rel.status, agora))
        return True

    def _marcar_aprendidas(self, criadas: dict[int, _PessoaImportada], completadas: dict[int, set[str]],
                           citadas: dict[int, list[int]]) -> None:
        """Pessoa que veio do arquivo e que só existe pelos registros (sem apelido, observação
        nem aniversário) passa a ser "automática", e o nome/relação dela fica ligado ao
        primeiro registro novo que o escreve: apagar esses registros apaga o que veio deles,
        como acontece com o que o próprio app aprende (seção 14)."""
        textos: dict[int, str] = {}

        def fonte(valor: str | None, registros: list[int]) -> int | None:
            chave = normalizar(_RE_PARENTESE.sub(" ", valor or ""))
            if not chave:
                return None
            rx = re.compile(r"(?<!\w)" + re.escape(chave) + r"(?!\w)")
            for rid in registros:
                if rid not in textos:
                    textos[rid] = normalizar(self._linha("registros", rid)["texto"])
                if rx.search(textos[rid]):
                    return rid
            return None

        for pid in sorted(set(criadas) | set(completadas)):
            registros = citadas.get(pid)
            if not registros:
                continue
            linha = self._linha("pessoas", pid)
            if pid in criadas:
                if not criadas[pid].so_nome():
                    continue            # tem dado escrito à mão: é do usuário, não do texto
                self._con.execute("UPDATE pessoas SET automatica = 1, nome_registro_id = ?, relacao_registro_id = ?"
                                  " WHERE id = ?", (fonte(linha["nome"], registros),
                                                    fonte(linha["relacao"], registros), pid))
                continue
            for campo in ("nome", "relacao") if linha["automatica"] else ():
                if campo in completadas[pid]:
                    self._con.execute(f"UPDATE pessoas SET {campo}_registro_id = ? WHERE id = ?",
                                      (fonte(linha[campo], registros), pid))


__all__ = ["Banco", "CAMPOS_IA", "CONFIG_PADRAO", "ESQUEMA_VERSAO", "IA_TEMPO_MAXIMO", "IA_TEMPO_MINIMO",
           "IA_TEMPO_PADRAO", "MAX_CUSTO_TRECHOS", "MAX_JSON_IMPORTAR", "NaoEncontrado", "STATUS_ITEM",
           "STATUS_RELACAO", "TIPOS_IA", "config_sem_segredos", "ler_exportacao", "validar_exportacao", "validar_ia"]
