"""Servidor local do Entre Nós: o transporte HTTP do computador (DESIGN.md, seções 15 e 19).

O app em si (sessão, PIN, rotas, IA, restaurar, importar) está em `nucleo.py`, que não sabe
nada de HTTP e também roda no navegador (`ponte.py`). Aqui fica só o que é do computador:

- HTTP só em 127.0.0.1 (nunca 0.0.0.0), um pedido por thread (`ThreadingHTTPServer`);
- `Host` precisa ser 127.0.0.1:porta ou localhost:porta (contra DNS rebinding); `Origin`, quando
  vem, precisa ser a própria origem; `Sec-Fetch-Site` de outro site é recusado;
- POST/PATCH/PUT/DELETE só com `Content-Type: application/json`; o corpo é lido com o limite da
  rota, que vem da tabela do núcleo (1 MB; 64 MB em restaurar e importar, com o token conferido
  antes de receber o corpo e um desses pedidos por vez);
- CSP e cabeçalhos de proteção em toda resposta; `Cache-Control: no-store` na API;
- arquivos estáticos só de `web/`, sem `..`, com o tipo MIME certo; sem os arquivos da página, o
  programa avisa em vez de abrir em branco;
- a thread vigia, que bloqueia o diário por inatividade mesmo sem pedido nenhum;
- o mutex do Windows que o instalador procura, a linha de comando e o navegador.

Nada de conteúdo do usuário vai para log: `erros.log` guarda só o tipo do erro e onde ele
aconteceu no código (`nucleo.registrar_erro`).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import cofre as _cofre
from . import config
from .banco import Banco, validar_exportacao  # noqa: F401 - nomes que os testes usam por aqui
from .modelos import para_json
from .nucleo import (AREA_IA_FECHADA, ATRASO_PIN, ATRASO_PIN_MAXIMO, BACKUP_DANIFICADO,  # noqa: F401
                     BLOQUEADO, CHAVE_DE_NOVO, COMPARACOES_SEM_ESPERA, DADO_INVALIDO, DIARIO_DANIFICADO,
                     ESPERE_O_PIN, FALHA_INTERNA, INDISPONIVEL, LIMITE_CORPO, LIMITE_CORPO_ARQUIVO, LIVRE,
                     MAX_CONTEXTO_SUGESTAO, MINUTOS_AREA_IA, NAO_E_BACKUP, NAO_SALVOU_ANTES, PASTA_SUMIU,
                     PIN_DO_BACKUP, PRIMEIRO_USO, PRIVADOS_DE_FORA, PUBLICA, REGISTRO_PRIVADO, ROTAS, SEM_IA,
                     SESSAO, ErroHttp, Nucleo, RespostaApi, Rota, achar_rota, atraso_do_pin, configurar_log,
                     contexto_sugestao, mensagem_de_limite, registrar_erro, resposta_de_erro, traduzir_erro)

ENDERECO = "127.0.0.1"               # só este computador; nunca 0.0.0.0
PORTAS_EXTRAS = 12                   # 8788-8799 quando a 8787 está ocupada por outro programa
INTERVALO_VIGIA = 30.0               # de quanto em quanto tempo a thread confere a inatividade
# Mutex do programa instalado aberto; o mesmo nome está no AppMutex de instalador/EntreNos.iss.
MUTEX_ABERTO = "EntreNos-Aberto-A5F79238-49BF-4FE8-8496-7CD3D084F9B4"
_MUTEX: Any = None                   # guardado enquanto o processo vive

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
CABECALHOS_SEGURANCA = (
    ("Content-Security-Policy", CSP),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
TIPOS_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}
TIPO_JSON = "application/json; charset=utf-8"
ARQUIVO_OCUPADO = "Outro arquivo grande está sendo recebido agora. Espere terminar e tente de novo."
PAGINA_SEM_ARQUIVOS = (
    "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\"><title>Entre Nós</title></head><body>"
    "<h1>Faltam arquivos do Entre Nós</h1>"
    "<p>A página do Entre Nós não está na pasta do programa. O antivírus pode ter colocado algum arquivo "
    "em quarentena.</p>"
    "<p>Reinstale o Entre Nós (rode o instalador de novo por cima) ou peça à TI para restaurar o arquivo "
    "da quarentena. Não desligue o antivírus.</p>"
    "<p>Seu diário não foi afetado: ele fica em outra pasta (%LOCALAPPDATA%\\EntreNos).</p>"
    "</body></html>")
ARQUIVOS_DA_PAGINA = ("index.html", "app.js", "estilo.css")

_log = logging.getLogger("entrenos")

# No computador o antivírus às vezes segura o arquivo do diário por um instante: o cofre espera
# um pouco entre as tentativas de gravar. No navegador não há antivírus (nem como dormir), e o
# núcleo não importa este módulo: lá o cofre tenta de novo sem esperar.
_cofre.esperar_entre_tentativas = time.sleep


def arquivo_estatico(pasta_web: Path, caminho_url: str) -> Path | None:
    """Arquivo de `web/` para o caminho pedido, ou None. Nada fora de `web/`."""
    try:
        caminho = urllib.parse.unquote(caminho_url, errors="strict")
    except UnicodeError:
        return None
    if caminho in ("", "/"):
        caminho = "/index.html"
    if not caminho.startswith("/") or any(c in caminho for c in "\\:") or any(ord(c) < 32 for c in caminho):
        return None
    partes = caminho[1:].split("/")
    # Sem "..", sem arquivo oculto e sem ponto ou espaço no fim (o Windows ignoraria).
    if any(not p or p.startswith(".") or p.endswith((".", " ")) or p != p.strip() for p in partes):
        return None
    base = os.path.realpath(pasta_web)
    alvo = os.path.realpath(os.path.join(base, *partes))
    if not os.path.normcase(alvo).startswith(os.path.normcase(base) + os.sep):
        return None
    if os.path.splitext(alvo)[1].lower() not in TIPOS_MIME or not os.path.isfile(alvo):
        return None
    return Path(alvo)


# ---------------------------------------------------------------- o app no computador

class EntreNos(Nucleo):
    """O núcleo com o que só existe no computador: porta, servidor HTTP, página e vigia."""

    def __init__(self, pasta: Path | str, *, atraso_pin: float = ATRASO_PIN,
                 atraso_pin_maximo: float = ATRASO_PIN_MAXIMO, minutos_bloqueio: float | None = None,
                 intervalo_verificacao: float = INTERVALO_VIGIA, hoje: Callable[[], date] | None = None,
                 relogio: Callable[[], float] | None = None, relogio_parede: Callable[[], float] | None = None,
                 pasta_web: Path | str | None = None, minutos_area_ia: float = MINUTOS_AREA_IA):
        super().__init__(pasta, atraso_pin=atraso_pin, atraso_pin_maximo=atraso_pin_maximo,
                         minutos_bloqueio=minutos_bloqueio, hoje=hoje, relogio=relogio,
                         relogio_parede=relogio_parede, minutos_area_ia=minutos_area_ia)
        self.pasta_web = Path(pasta_web) if pasta_web is not None else config.PASTA_WEB
        self.intervalo_verificacao = float(intervalo_verificacao)
        self.porta: int | None = None
        self.servidor: ThreadingHTTPServer | None = None
        # Um pedido de corpo grande (até 64 MB) por vez: cada um custa centenas de MB de memória
        # antes de qualquer PIN, e no primeiro uso a rota de restaurar não pede token.
        self.vez_do_arquivo = threading.BoundedSemaphore(1)
        self._parar = threading.Event()
        self._vigia: threading.Thread | None = None

    def iniciar_vigia(self) -> None:
        if self._vigia is None:
            self._vigia = threading.Thread(target=self._vigiar, name="entrenos-vigia", daemon=True)
            self._vigia.start()

    def _vigiar(self) -> None:
        while not self._parar.wait(self.intervalo_verificacao):
            try:
                self.verificar_inatividade()
            except Exception as erro:  # noqa: BLE001 - a vigia continua de pé
                registrar_erro(erro)

    def encerrar(self) -> None:
        """Salva, bloqueia e para a vigia (fim do programa)."""
        self._encerrando = True
        self._parar.set()
        super().encerrar()
        vigia = self._vigia
        if vigia is not None and vigia is not threading.current_thread():
            vigia.join(timeout=5)

    def host_permitido(self, host: str | None) -> bool:
        if not host or self.porta is None:
            return False
        return host.strip().lower() in {f"127.0.0.1:{self.porta}", f"localhost:{self.porta}"}

    @staticmethod
    def origem_permitida(origem: str, host: str) -> bool:
        return origem.strip().lower() == f"http://{host.strip().lower()}"


# ---------------------------------------------------------------- HTTP

class Manipulador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60                  # conexão parada não prende a thread para sempre
    server: ServidorHTTP

    def version_string(self) -> str:
        return "EntreNos"

    def log_message(self, formato: str, *args: Any) -> None:
        """Nada no log: o caminho pode trazer a pergunta da busca."""

    def end_headers(self) -> None:
        for nome, valor in CABECALHOS_SEGURANCA:
            self.send_header(nome, valor)
        super().end_headers()

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Erros do próprio http.server (pedido malformado, método que não existe), em JSON."""
        self.close_connection = True
        mensagens = {400: "Pedido malformado.", 408: "Tempo esgotado.", 414: "Endereço longo demais.",
                     431: "Cabeçalhos grandes demais.", 501: "Método não suportado.",
                     505: "Versão de HTTP não suportada."}
        try:
            if code == 501:       # método desconhecido: os cabeçalhos deste pedido já foram lidos
                self._corpo_lido = False
                self._descartar_corpo()
            self._json(code, {"erro": mensagens.get(code, "Pedido recusado.")})
        except OSError:
            pass

    def do_GET(self) -> None:
        self._tratar("GET")

    def do_POST(self) -> None:
        self._tratar("POST")

    def do_PATCH(self) -> None:
        self._tratar("PATCH")

    def do_PUT(self) -> None:
        self._tratar("PUT")

    def do_DELETE(self) -> None:
        self._tratar("DELETE")

    # ------------------------------------------------------------ respostas

    def _responder(self, status: int, corpo: bytes, tipo: str, cache: str,
                   extras: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        for nome, valor in (extras or {}).items():
            self.send_header(nome, valor)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, status: int, dados: Any, extras: dict[str, str] | None = None) -> None:
        self._responder(status, _serializar(dados), TIPO_JSON, "no-store", extras)

    # ------------------------------------------------------------ pedido

    def _um_cabecalho(self, nome: str) -> str | None:
        valores = self.headers.get_all(nome) or []
        if len(valores) > 1:
            raise ErroHttp(403, "Pedido com cabeçalhos repetidos recusado.")
        return valores[0] if valores else None

    def _tem_corpo_pendente(self) -> bool:
        return (not self._corpo_lido and (self.headers.get("Transfer-Encoding") is not None
                                          or self.headers.get("Content-Length", "0").strip() not in ("", "0")))

    def _descartar_corpo(self, maximo: int = 8 * LIMITE_CORPO) -> None:
        """Joga fora o corpo não lido e fecha a conexão depois da resposta.

        Sem ler, o resto do corpo viraria o "próximo pedido"; e fechar com dados não lidos
        faz o Windows mandar RST, que pode sumir com a resposta antes de o navegador lê-la.
        Lê em pedaços de 64 KB (sem guardar), até `maximo` bytes.
        """
        self.close_connection = True
        if self._corpo_lido or self.headers.get("Transfer-Encoding") is not None:
            return
        try:
            restante = int(self.headers.get("Content-Length", "0"))
            if restante > maximo:
                return
            while restante > 0:
                bloco = self.rfile.read(min(65536, restante))
                if not bloco:
                    break
                restante -= len(bloco)
            self._corpo_lido = True
        except (ValueError, OSError):
            pass

    def _ler_corpo(self, metodo: str, limite: int = LIMITE_CORPO) -> bytes:
        if metodo not in ("POST", "PATCH", "PUT", "DELETE"):
            return b""
        tipo = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if tipo != "application/json":
            raise ErroHttp(415, "Envie os dados em JSON (Content-Type: application/json).")
        if self.headers.get("Transfer-Encoding") is not None:
            raise ErroHttp(411, "Informe o tamanho do corpo (Content-Length).")
        tamanhos = self.headers.get_all("Content-Length") or []
        if len(tamanhos) > 1:
            raise ErroHttp(400, "Pedido com tamanho repetido.")
        if not tamanhos:
            return b""
        try:
            tamanho = int(tamanhos[0].strip())
            if tamanho < 0:
                raise ValueError
        except ValueError:
            raise ErroHttp(400, "Tamanho do corpo inválido.") from None
        if tamanho > limite:
            raise ErroHttp(413, mensagem_de_limite(limite))
        corpo = self.rfile.read(tamanho)
        self._corpo_lido = True
        if len(corpo) != tamanho:
            self.close_connection = True
            raise ErroHttp(400, "O corpo do pedido chegou incompleto.")
        return corpo

    def _tratar(self, metodo: str) -> None:
        app = self.server.app
        self._corpo_lido = False
        vez_do_arquivo: threading.BoundedSemaphore | None = None
        try:
            caminho, _, consulta_bruta = self.path.split("#", 1)[0].partition("?")
            if not caminho.startswith("/") or caminho.startswith("//"):
                raise ErroHttp(404, "Não encontrado.")
            host = self._um_cabecalho("Host")
            if not app.host_permitido(host):
                raise ErroHttp(403, "Endereço não permitido. Abra o Entre Nós pelo atalho.")
            origem = self._um_cabecalho("Origin")
            if origem is not None and not app.origem_permitida(origem, host):
                raise ErroHttp(403, "Pedido vindo de outra página recusado.")
            if caminho != "/api" and not caminho.startswith("/api/"):
                self._estatico(app, metodo, caminho)
                return
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site.strip().lower() not in ("same-origin", "none"):
                raise ErroHttp(403, "Pedido vindo de outra página recusado.")
            rota, _ = achar_rota(metodo, caminho)        # o limite do corpo vem da tabela do núcleo
            limite = app.limite_do_corpo(rota)
            token = self.headers.get("X-EntreNos-Token")
            if limite > LIMITE_CORPO:
                # Até 64 MB: quem não tem sessão (ou não está no primeiro uso) nem chega a mandar.
                app.conferir_antes_do_corpo(rota, token)
                # E um de cada vez: cada um custa centenas de MB antes de qualquer PIN. O corpo do
                # recusado é lido e jogado fora em pedaços (sem memória), para a resposta chegar.
                if not app.vez_do_arquivo.acquire(blocking=False):
                    self._descartar_corpo(limite)
                    raise ErroHttp(503, ARQUIVO_OCUPADO)
                vez_do_arquivo = app.vez_do_arquivo
            try:
                consulta = urllib.parse.parse_qs(consulta_bruta, keep_blank_values=True, max_num_fields=50)
            except ValueError:
                raise ErroHttp(400, "Parâmetros demais no endereço.") from None
            # O corpo vai direto para o núcleo, sem outra referência aqui: o pedido o solta assim
            # que o JSON é lido (um arquivo de 64 MB não fica duas vezes na memória).
            resposta = app.tratar(metodo, caminho, corpo=self._ler_corpo(metodo, limite),
                                  token=token, consulta=consulta)
        except Exception as erro:  # noqa: BLE001 - todo erro vira resposta JSON
            if self._tem_corpo_pendente():
                self._descartar_corpo()
            resposta = resposta_de_erro(erro)
        finally:
            if vez_do_arquivo is not None:
                vez_do_arquivo.release()
        self._enviar(resposta)

    def _enviar(self, resposta: RespostaApi) -> None:
        """A resposta do núcleo em HTTP. Depois de /api/sair, desliga o servidor (depois de a
        resposta sair: shutdown() espera o serve_forever terminar)."""
        if resposta.encerrar:
            self.close_connection = True
        self._responder(resposta.status, resposta.corpo, TIPO_JSON, "no-store", resposta.cabecalhos)
        if resposta.encerrar:
            self.wfile.flush()
            threading.Thread(target=self.server.shutdown, name="entrenos-sair", daemon=True).start()

    def _estatico(self, app: EntreNos, metodo: str, caminho: str) -> None:
        """Arquivos da página (web/)."""
        if metodo != "GET":
            raise ErroHttp(405, "Este endereço não aceita esse tipo de pedido.", {"Allow": "GET"})
        arquivo = arquivo_estatico(app.pasta_web, caminho)
        if arquivo is None and caminho in ("/", "/index.html"):
            # A página sumiu (antivírus, instalação pela metade): um texto que diz o que
            # fazer, em vez do JSON cru no navegador.
            if self._tem_corpo_pendente():
                self._descartar_corpo()
            self._responder(404, PAGINA_SEM_ARQUIVOS.encode("utf-8"), TIPOS_MIME[".html"], "no-store")
            return
        if arquivo is None:
            raise ErroHttp(404, "Não encontrado.")
        conteudo = arquivo.read_bytes()
        if self._tem_corpo_pendente():
            self._descartar_corpo()
        self._responder(200, conteudo, TIPOS_MIME[arquivo.suffix.lower()], "no-cache")


def _serializar(dados: Any) -> bytes:
    return json.dumps(para_json(dados), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ServidorHTTP(ThreadingHTTPServer):
    """HTTP em 127.0.0.1. No Windows, porta exclusiva: ninguém mais escuta na mesma porta."""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False

    def __init__(self, app: EntreNos, porta: int):
        self.app = app
        super().__init__((ENDERECO, porta), Manipulador)
        app.porta = self.server_address[1]
        app.servidor = self

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        # Sem socket.getfqdn(): a consulta de nome pode demorar segundos no Windows.
        self.server_name, self.server_port = ENDERECO, self.server_address[1]

    def handle_error(self, request: Any, client_address: Any) -> None:
        erro = sys.exc_info()[1]
        if erro is not None and not isinstance(erro, (ConnectionError, TimeoutError)):
            registrar_erro(erro)

    def server_close(self) -> None:
        super().server_close()
        self.app.encerrar()


def criar_servidor(pasta: Path | str | None = None, porta: int = config.PORTA_PADRAO,
                   **opcoes: Any) -> ServidorHTTP:
    """Servidor já escutando em 127.0.0.1:porta (0 = porta livre qualquer), com a vigia ligada.

    `opcoes` vão para EntreNos: atraso_pin, atraso_pin_maximo, minutos_bloqueio,
    intervalo_verificacao, hoje, relogio, relogio_parede, pasta_web, minutos_area_ia (os testes
    usam valores pequenos e relógio de mentira).
    """
    app = EntreNos(pasta if pasta is not None else config.pasta_dados(), **opcoes)
    servidor = ServidorHTTP(app, porta)
    app.iniciar_vigia()
    return servidor


# ---------------------------------------------------------------- linha de comando

def e_entre_nos(porta: int, espera: float = 2.0) -> bool:
    """True se quem responde em 127.0.0.1:porta é um Entre Nós (GET /api/ping)."""
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(f"http://{ENDERECO}:{porta}/api/ping", timeout=espera) as resposta:
            dados = json.loads(resposta.read(4096).decode("utf-8"))
        return isinstance(dados, dict) and dados.get("app") == "entre-nos"
    except Exception:  # noqa: BLE001 - qualquer falha = não é o Entre Nós
        return False


def _avisar(mensagem: str) -> None:
    """Mensagem para a pessoa: no console, ou numa janela quando aberto pelo pythonw."""
    if sys.stderr is not None:
        print(mensagem, file=sys.stderr)
        return
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, mensagem, config.NOME_APP, 0x30)
        except Exception:  # noqa: BLE001
            pass


def _abrir_navegador(porta: int) -> None:
    try:
        webbrowser.open(f"http://{ENDERECO}:{porta}/")
    except Exception as erro:  # noqa: BLE001
        registrar_erro(erro)


def _marcar_aberto() -> Any:
    """Cria o mutex que o instalador procura (`AppMutex` e `CheckForMutexes` do EntreNos.iss).

    Assim o instalador e o desinstalador avisam "feche o Entre Nós" em vez de trocar arquivos
    de um programa aberto, e o desinstalador não apaga a pasta de dados com o diário aberto na
    memória. Vale também pelo iniciar.py (pythonw): ele usa a mesma pasta de dados, e o atalho
    do instalado, achando a porta ocupada por ele, só abre o navegador. O Windows desfaz o
    mutex sozinho quando o processo termina.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_ABERTO)
    except Exception:  # noqa: BLE001 - sem o mutex o app funciona; só o instalador não avisa
        return None


class _LeitorArgumentos(argparse.ArgumentParser):
    """Sem console (EntreNos.exe e pythonw), a ajuda e os erros de argumento aparecem numa janela."""

    def _print_message(self, message: str, file: Any = None) -> None:
        if message and (file or sys.stderr) is None:
            _avisar(message.strip())
            return
        super()._print_message(message, file)

    def error(self, message: str) -> None:  # type: ignore[override]
        if sys.stderr is None:
            _avisar(f"{self.format_usage().strip()}\n\n{self.prog}: {message}")
            raise SystemExit(2)
        super().error(message)


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    programa = "EntreNos.exe" if config.empacotado() else "iniciar.py"
    leitor = _LeitorArgumentos(prog=programa, description="Abre o Entre Nós neste computador.")
    leitor.add_argument("--sem-navegador", action="store_true", help="não abre o navegador")
    leitor.add_argument("--porta", type=int, default=None,
                        help=f"porta local (padrão {config.PORTA_PADRAO}, depois as seguintes)")
    leitor.add_argument("--dados", default=None, help="pasta dos dados (padrão %%LOCALAPPDATA%%\\EntreNos)")
    return leitor.parse_args(argv)


def faltam_arquivos_da_pagina(pasta_web: Path | None = None) -> list[str]:
    """Arquivos de web/ que deveriam estar lá e não estão (o antivírus pode tê-los levado)."""
    pasta_web = Path(pasta_web) if pasta_web is not None else config.PASTA_WEB
    return [nome for nome in ARQUIVOS_DA_PAGINA if not (pasta_web / nome).is_file()]


def main(argv: list[str] | None = None) -> int:
    global _MUTEX
    args = _argumentos(argv)
    faltam = faltam_arquivos_da_pagina()
    if faltam:
        # Sem a página não há o que abrir: avisa já, em vez de um navegador em branco (seção 18.6).
        _avisar(f"Faltam arquivos do Entre Nós em {config.PASTA_WEB}: {', '.join(faltam)}.\n\n"
                "O antivírus pode tê-los colocado em quarentena. Reinstale o Entre Nós (rode o instalador "
                "de novo por cima) ou peça à TI para restaurar os arquivos da quarentena. Não desligue o "
                "antivírus.\n\nSeu diário não foi afetado: ele fica em outra pasta (%LOCALAPPDATA%\\EntreNos).")
        return 1
    pasta = Path(os.path.abspath(args.dados)) if args.dados else config.pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    manipulador = configurar_log(pasta)
    gancho_threads, gancho_sys = threading.excepthook, sys.excepthook
    threading.excepthook = lambda a: registrar_erro(a.exc_value) if a.exc_value is not None else None
    sys.excepthook = lambda tipo, erro, tb: registrar_erro(erro)
    try:
        if args.porta is not None:
            portas = [args.porta]
        else:
            portas = list(range(config.PORTA_PADRAO, config.PORTA_PADRAO + PORTAS_EXTRAS + 1))
        servidor = None
        for porta in portas:
            try:
                servidor = criar_servidor(pasta, porta)
                break
            except OSError:
                if porta and e_entre_nos(porta):
                    # Já está aberto: só mostra.
                    if not args.sem_navegador:
                        _abrir_navegador(porta)
                    return 0
        if servidor is None:
            if len(portas) == 1:
                _avisar(f"Não consegui abrir o Entre Nós: a porta {portas[0]} está ocupada por outro programa.")
            else:
                _avisar(f"Não consegui abrir o Entre Nós: as portas {portas[0]} a {portas[-1]} estão "
                        "ocupadas por outros programas.")
            return 1
        porta = servidor.server_address[1]
        _MUTEX = _marcar_aberto()
        print(f"{config.NOME_APP} aberto em http://{ENDERECO}:{porta}/ (Ctrl+C para fechar)")
        if not args.sem_navegador:
            threading.Timer(0.3, _abrir_navegador, (porta,)).start()
        try:
            servidor.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            servidor.server_close()
        return 0
    except Exception as erro:
        if sys.stderr is not None:
            raise
        # Sem console (EntreNos.exe, pythonw): anota em erros.log, sem conteúdo, e avisa numa janela.
        registrar_erro(erro)
        _avisar("O Entre Nós fechou por causa de um erro. O tipo do erro ficou anotado no arquivo "
                f"erros.log, em {pasta}.")
        return 1
    finally:
        threading.excepthook, sys.excepthook = gancho_threads, gancho_sys
        _log.removeHandler(manipulador)
        manipulador.close()


__all__ = ["ENDERECO", "EntreNos", "ErroHttp", "MINUTOS_AREA_IA", "Manipulador", "ROTAS", "ServidorHTTP",
           "arquivo_estatico", "atraso_do_pin", "configurar_log", "contexto_sugestao", "criar_servidor",
           "e_entre_nos", "faltam_arquivos_da_pagina", "main", "registrar_erro", "traduzir_erro"]
