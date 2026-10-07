"""Ponte do Entre Nós no navegador: o Pyodide chama estas funções de dentro de um Web Worker
(DESIGN.md, seção 19).

No iPhone e no Android não há servidor: a página (PWA) manda cada pedido da API para o Worker,
que chama `tratar_json` com texto e recebe texto (JSON), sem objetos Python atravessando para o
JavaScript. O app é o mesmo `Nucleo` do computador, com os dados numa pasta do sistema de
arquivos do Emscripten (IDBFS, guardado no armazenamento do navegador) e o diário no mesmo
formato `.cofre`. O núcleo nasce com `plataforma="navegador"` (seção 19.7): sem backup numa
pasta, sem IA por enquanto, arquivos enviados de até 16 MB e menos cópias em backups/.

Nada aqui cria thread, dorme, abre socket nem importa o servidor.py. Bloqueio por inatividade:
o núcleo confere a cada pedido; para bloquear sem pedido nenhum, o Worker chama
`verificar_inatividade()` de tempos em tempos (um `setInterval` no JavaScript).

`gravou` nas respostas diz se algum arquivo da pasta de dados mudou durante a chamada: aí o
Worker manda o IDBFS gravar no armazenamento do navegador (`FS.syncfs(false, ...)`).

Ordem no Worker: carregar o Pyodide e o `cryptography`, chamar `autoteste()` (este navegador tem
tudo o que o app usa?), montar o IDBFS, `FS.syncfs(true, ...)` e só então `iniciar_no_navegador`.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any

from .nucleo import (MAX_PARAMETROS, NAVEGADOR, ErroHttp, Nucleo, RespostaApi, configurar_log, registrar_erro,
                     resposta_de_erro)

# Cabeçalhos da resposta que interessam à página (o resto é do HTTP, que aqui não existe).
CABECALHOS_UTEIS = ("Allow", "Retry-After")
NAO_INICIADO = "O Entre Nós ainda não terminou de abrir. Espere um instante e tente de novo."
PARAMETROS_INVALIDOS = "Parâmetros inválidos no endereço."

_nucleo: Nucleo | None = None
_log_manipulador: Any = None


def iniciar_no_navegador(pasta_dados: str, **opcoes: Any) -> Nucleo:
    """Cria o núcleo do navegador com os dados em `pasta_dados` (a pasta montada com IDBFS).

    `opcoes` vão para o Nucleo (os testes usam atraso_pin, relogio, relogio_parede e hoje); a
    plataforma é "navegador", a não ser que venha outra. Não cria thread: quem chama pode ser o
    único fio de execução do Worker. Chamar de novo troca o núcleo (o anterior é bloqueado, o que
    salva o que estava aberto). O bloqueio do PIN errado continua (pin_espera.json, seção 19.7).
    """
    global _nucleo, _log_manipulador
    pasta = Path(pasta_dados)
    pasta.mkdir(parents=True, exist_ok=True)
    anterior, _nucleo = _nucleo, None
    if anterior is not None:
        try:
            anterior.bloquear()
        except Exception as erro:  # noqa: BLE001 - o novo abre do mesmo jeito
            registrar_erro(erro)
    if _log_manipulador is not None:
        logging.getLogger("entrenos").removeHandler(_log_manipulador)
        _log_manipulador.close()
    _log_manipulador = configurar_log(pasta)
    opcoes.setdefault("plataforma", NAVEGADOR)
    _nucleo = Nucleo(pasta, **opcoes)
    return _nucleo


def tratar_json(metodo: str, caminho: str, corpo_texto: str | None = None, token: str | None = None,
                consulta_json: str | None = None) -> str:
    """Um pedido da página, como o `fetch` faria: devolve
    `{"status", "cabecalhos", "gravou", "dados"[, "encerrado"]}` em JSON.

    `caminho` pode trazer a consulta ("/api/busca?q=tulipas"); `consulta_json` ({"q": "..."})
    vale por cima dela. `corpo_texto` é o JSON do corpo, como texto. `token` é o
    X-EntreNos-Token (None/"" = sem sessão). Nunca levanta: erro vira status e {"erro"}.
    """
    nucleo = _nucleo
    if nucleo is None:
        return _saida(resposta_de_erro(ErroHttp(503, NAO_INICIADO)), False)
    antes = _assinatura(nucleo.pasta)
    try:
        caminho, consulta = _separar_consulta(caminho, _texto(consulta_json))
        corpo = _bytes(_texto(corpo_texto))
        corpo_texto = None           # a referência daqui sai antes do pedido (o texto pode ter MB)
        # O corpo vai direto (sem outra referência aqui): o núcleo o solta depois de ler o JSON.
        resposta = nucleo.tratar(str(metodo or "").upper(), caminho, corpo=corpo,
                                 token=_texto(token) or None, consulta=consulta)
    except Exception as erro:  # noqa: BLE001 - nada de PythonError chegando ao JavaScript
        resposta = resposta_de_erro(erro)
    return _saida(resposta, _assinatura(nucleo.pasta) != antes)


def restaurar_arquivo(arquivo: Any, corpo_texto: str | None = None, token: str | None = None) -> str:
    """POST /api/restaurar com o arquivo .cofre em bytes (o Uint8Array do arquivo escolhido, que o
    Pyodide entrega como JsProxy), sem passar por base64 dentro do JSON: no celular a memória é
    curta e o wasm não a devolve. `corpo_texto`: {"pin"[, "pin_atual", "substituir_danificado"]}.
    Mesma saída que `tratar_json`. Nunca levanta."""
    nucleo = _nucleo
    if nucleo is None:
        return _saida(resposta_de_erro(ErroHttp(503, NAO_INICIADO)), False)
    antes = _assinatura(nucleo.pasta)
    try:
        conteudo = _bytes_do_arquivo(arquivo)
        arquivo = None
        if conteudo is None:
            raise ErroHttp(400, "Escolha o arquivo do backup (.cofre).")
        resposta = nucleo.restaurar_arquivo(conteudo, corpo=_bytes(_texto(corpo_texto)),
                                            token=_texto(token) or None)
    except Exception as erro:  # noqa: BLE001
        resposta = resposta_de_erro(erro)
    return _saida(resposta, _assinatura(nucleo.pasta) != antes)


def verificar_inatividade() -> str:
    """Para o Worker chamar de tempos em tempos: bloqueia se passou do tempo sem uso.
    Devolve `{"bloqueou", "gravou"}` em JSON."""
    nucleo = _nucleo
    if nucleo is None:
        return json.dumps({"bloqueou": False, "gravou": False})
    antes = _assinatura(nucleo.pasta)
    try:
        bloqueou = nucleo.verificar_inatividade()
    except Exception as erro:  # noqa: BLE001 - nunca derruba o Worker
        registrar_erro(erro)
        bloqueou = False
    return json.dumps({"bloqueou": bool(bloqueou), "gravou": _assinatura(nucleo.pasta) != antes})


def autoteste() -> str:
    """Confere, antes de abrir qualquer diário, se este Python (o Pyodide deste navegador) tem o
    que o Entre Nós usa: SQLite com FTS5 (`remove_diacritics 2`, bm25, optimize), serialize e
    deserialize, o autorizador do SQLite (que segura os recursos, seção 20.0), o esquema do diário
    inteiro indo e voltando, scrypt e AES-GCM com valores conhecidos, o formato .cofre e os módulos
    importados na hora do uso (inclusive o registro dos recursos). Nada vai para o disco.

    Devolve `{"ok", "falhas": ["passo: TipoDoErro"], "python", "sqlite"}` em JSON. Com `ok`
    falso, a página explica que este navegador não roda o Entre Nós, em vez de abrir pela metade.
    """
    import importlib
    import platform
    import sqlite3

    falhas: list[str] = []

    def passo(nome: str, funcao: Any) -> None:
        try:
            if funcao() is False:
                falhas.append(f"{nome}: resultado errado")
        except Exception as erro:  # noqa: BLE001 - o que falhar vira um item da lista
            falhas.append(f"{nome}: {type(erro).__name__}")

    def fts5() -> bool:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(c, tokenize='unicode61 remove_diacritics 2')")
            con.execute("INSERT INTO t(c) VALUES ('Ela adora café com pão de queijo')")
            achados = con.execute("SELECT bm25(t) FROM t WHERE t MATCH 'cafe AND pao'").fetchall()
            con.execute("INSERT INTO t(t) VALUES ('optimize')")
            return len(achados) == 1 and con.execute("SELECT count(*) FROM json_each('[1,2]')").fetchone()[0] == 2
        finally:
            con.close()

    def serializar() -> bool:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE TABLE x (v TEXT)")
            con.execute("INSERT INTO x VALUES ('ida e volta')")
            con.commit()
            dados = con.serialize()
        finally:
            con.close()
        outra = sqlite3.connect(":memory:")
        try:
            outra.deserialize(dados)
            return outra.execute("SELECT v FROM x").fetchone() == ("ida e volta",)
        finally:
            outra.close()

    def autorizador() -> bool:
        # Os ajudantes dos recursos (seção 20.0) confiam no autorizador do SQLite para não mexerem
        # no que não é deles; set_authorizer(None) desliga (Python 3.11+).
        con = sqlite3.connect(":memory:", isolation_level=None)
        try:
            con.execute("CREATE TABLE x (v TEXT)")
            con.set_authorizer(lambda acao, *_: sqlite3.SQLITE_DENY if acao == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK)
            try:
                con.execute("INSERT INTO x VALUES ('negado')")
                return False
            except sqlite3.DatabaseError:
                pass
            con.set_authorizer(None)
            con.execute("INSERT INTO x VALUES ('liberado')")
            return con.execute("SELECT v FROM x").fetchall() == [("liberado",)]
        finally:
            con.close()

    def diario() -> bool:
        from datetime import date

        from .banco import Banco
        banco = Banco.novo()
        try:
            banco.criar_registro("Ela adora café.", date(2026, 9, 30), [], [], [])
            copia = Banco.de_bytes(banco.para_bytes())
        finally:
            banco.fechar()
        try:
            copia.conferir_integridade()
            return bool(copia.buscar_texto("cafe"))
        finally:
            copia.fechar()

    def criptografia() -> bool:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        # RFC 7914, seção 12 (primeiro vetor), e AES-256-GCM com chave, nonce e texto zerados.
        chave = Scrypt(salt=b"", length=64, n=16, r=1, p=1).derive(b"")
        cifrado = AESGCM(bytes(32)).encrypt(bytes(12), bytes(16), None)
        return (chave.hex() == _SCRYPT_RFC7914 and cifrado.hex() == _AESGCM_ZERADO
                and AESGCM(bytes(32)).decrypt(bytes(12), cifrado, None) == bytes(16))

    def formato_cofre() -> bool:
        import secrets

        from . import cofre
        sal = secrets.token_bytes(cofre.TAMANHO_SAL)
        chave = cofre._derivar("girassol na janela", sal, 2 ** 10, 8, 1)
        conteudo = cofre._cifrar(chave, sal, (2 ** 10, 8, 1), b"\x00" * 512)
        recebido = cofre.abrir_conteudo(conteudo, "girassol na janela")
        try:
            return recebido.dados == b"\x00" * 512 and len(secrets.token_urlsafe(32)) > 30
        finally:
            recebido.descartar()

    def modulos() -> None:
        for nome in ("motor", "busca", "alertas", "perfil", "contexto", "provedores", "recursos"):
            importlib.import_module(f"{__package__}.{nome}")
        # Um recurso que não abre não impede o diário (seção 20.0): carrega, mas não reprova.
        importlib.import_module(f"{__package__}.recursos").carregados()

    passo("sqlite_fts5", fts5)
    passo("sqlite_serialize", serializar)
    passo("sqlite_autorizador", autorizador)
    passo("diario", diario)
    passo("criptografia", criptografia)
    passo("formato_cofre", formato_cofre)
    passo("modulos", modulos)
    return json.dumps({"ok": not falhas, "falhas": falhas, "python": platform.python_version(),
                       "sqlite": sqlite3.sqlite_version})


# Valores conhecidos do autoteste (conferidos com o cryptography do computador).
_SCRYPT_RFC7914 = ("77d6576238657b203b19ca42c18a0497f16b4844e3074ae8dfdffa3fede21442"
                   "fcd0069ded0948f8326a753a0fc81f17e8d3e0fb2e0d3628cf35e20c38d18906")
_AESGCM_ZERADO = "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919"


# ---------------------------------------------------------------- auxiliares

def _texto(valor: Any) -> str | None:
    """Só texto passa; o resto (None, o `null`/`undefined` do JavaScript como o Pyodide os
    entregar) vale como "não veio"."""
    return valor if isinstance(valor, str) else None


def _bytes(corpo_texto: str | None) -> bytes | None:
    """O corpo em UTF-8. Texto com metade de um par de surrogates (que o JavaScript permite)
    passa adiante como está, e o núcleo recusa o JSON (400), como no computador."""
    if corpo_texto is None:
        return None
    return corpo_texto.encode("utf-8", "surrogatepass")


def _bytes_do_arquivo(valor: Any) -> bytes | bytearray | None:
    """O arquivo que veio do JavaScript: bytes, memoryview, ou o JsProxy de um Uint8Array (que
    o Pyodide converte com `to_bytes()`). Outra coisa: None."""
    if isinstance(valor, (bytes, bytearray)):
        return valor
    if isinstance(valor, memoryview):
        return valor.tobytes()
    if valor is None or isinstance(valor, (str, int, float)):
        return None              # int também tem to_bytes(), mas não é arquivo
    converter = getattr(valor, "to_bytes", None)
    if not callable(converter):
        return None
    dados = converter()
    return dados if isinstance(dados, (bytes, bytearray)) else None


def _separar_consulta(caminho: str, consulta_json: str | None) -> tuple[str, dict[str, str]]:
    caminho, _, bruta = str(caminho or "").split("#", 1)[0].partition("?")
    try:
        consulta = {nome: valores[-1] for nome, valores in urllib.parse.parse_qs(
            bruta, keep_blank_values=True, max_num_fields=MAX_PARAMETROS).items()}
    except ValueError:
        raise ErroHttp(400, "Parâmetros demais no endereço.") from None
    if consulta_json:
        try:
            extra = json.loads(consulta_json)
        except (ValueError, RecursionError):
            raise ErroHttp(400, PARAMETROS_INVALIDOS) from None
        if not isinstance(extra, dict) or not all(isinstance(v, str) for v in extra.values()):
            raise ErroHttp(400, PARAMETROS_INVALIDOS)
        consulta.update(extra)
    return caminho, consulta


def _saida(resposta: RespostaApi, gravou: bool) -> str:
    """A resposta em JSON. Os `dados` entram já serializados (uma exportação grande não é
    convertida duas vezes)."""
    cabecalhos = {k: v for k, v in resposta.cabecalhos.items() if k in CABECALHOS_UTEIS}
    partes: dict[str, Any] = {"status": resposta.status, "cabecalhos": cabecalhos, "gravou": gravou}
    if resposta.encerrar:
        partes["encerrado"] = True
    inicio = json.dumps(partes, ensure_ascii=False)
    return f'{inicio[:-1]},"dados":{resposta.corpo.decode("utf-8")}}}'


def _assinatura(pasta: Path) -> tuple:
    """Nome, inode, tamanho e data de cada arquivo da pasta de dados: muda quando algo foi
    gravado (o cofre grava num arquivo novo e troca, então o inode muda mesmo no mesmo instante)."""
    itens = []
    try:
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                try:
                    info = os.stat(caminho)
                except OSError:
                    continue
                itens.append((caminho, info.st_ino, info.st_size, info.st_mtime_ns))
    except OSError:
        pass
    return tuple(sorted(itens))


__all__ = ["autoteste", "iniciar_no_navegador", "restaurar_arquivo", "tratar_json", "verificar_inatividade"]
