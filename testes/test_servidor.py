"""Testes do servidor (DESIGN.md, seção 15): segurança e o fluxo completo pela API.

Cada servidor sobe numa thread, numa porta livre de 127.0.0.1, com pasta de dados
temporária e data de referência fixa (quarta-feira, 30/09/2026). O scrypt do cofre usa
n=2**12 aqui, em vez de 2**16, só para os testes andarem rápido; o próprio cofre é testado
com o valor real em test_cofre.py.
"""
from __future__ import annotations

import contextlib
import fnmatch
import http.client
import inspect
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

from entrenos import cofre, config, nucleo, servidor

REF = date(2026, 9, 30)
PIN = "pizza de sexta"
TEXTO = "A mãe dela, Sandra, vai fazer uma cirurgia em novembro. Ela disse que adora tulipas."
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    remendo = mock.patch.object(cofre, "SCRYPT_N", 2 ** 12)
    remendo.start()
    _remendos.append(remendo)


def tearDownModule() -> None:
    for remendo in _remendos:
        remendo.stop()
    if _env_antigo is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _env_antigo
    shutil.rmtree(_pasta_env, ignore_errors=True)


@dataclass
class Resposta:
    status: int
    dados: Any
    cabecalhos: http.client.HTTPMessage
    bruto: bytes


class Relogio:
    """Relógio de mentira para o núcleo (`relogio=`, segundos monotônicos): só anda quando o
    teste manda. Assim o bloqueio do PIN (seção 19) é testado sem esperar de verdade."""

    def __init__(self, inicio: float = 1000.0):
        self._agora = inicio
        self._trava = threading.Lock()

    def __call__(self) -> float:
        with self._trava:
            return self._agora

    def avancar(self, segundos: float) -> None:
        with self._trava:
            self._agora += segundos


class ServidorDeTeste:
    """Servidor real numa thread, com cliente HTTP simples (http.client).

    PIN errado não bloqueia nada por padrão (`atraso_pin=0`): os testes do bloqueio passam o
    atraso que querem e um `Relogio` de mentira.
    """

    def __init__(self, pasta: str | None = None, **opcoes):
        self.pasta = pasta or tempfile.mkdtemp(prefix="entrenos-teste-")
        parametros = {"atraso_pin": 0, "hoje": lambda: REF}
        parametros.update(opcoes)
        self.servidor = servidor.criar_servidor(self.pasta, 0, **parametros)
        self.app = self.servidor.app
        self.porta = self.servidor.server_address[1]
        self.thread = threading.Thread(target=self.servidor.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()
        self.token: str | None = None

    def fechar(self, apagar: bool = True) -> None:
        if self.thread.is_alive():
            self.servidor.shutdown()
            self.thread.join(5)
        self.servidor.server_close()
        if apagar:
            shutil.rmtree(self.pasta, ignore_errors=True)

    def pedir(self, metodo: str, rota: str, corpo: Any = None, *, token: Any = _PADRAO,
              cabecalhos: dict | None = None, tipo: str | None = "application/json",
              bruto: bytes | None = None) -> Resposta:
        conexao = http.client.HTTPConnection(servidor.ENDERECO, self.porta, timeout=20)
        try:
            h: dict[str, str] = {}
            token = self.token if token is _PADRAO else token
            if token:
                h["X-EntreNos-Token"] = token
            dados = bruto
            if dados is None and (corpo is not None or metodo in ("POST", "PATCH", "PUT", "DELETE")):
                dados = json.dumps(corpo if corpo is not None else {}).encode("utf-8")
            if dados is not None and tipo:
                h["Content-Type"] = tipo
            h.update(cabecalhos or {})
            conexao.request(metodo, rota, body=dados, headers=h)
            resposta = conexao.getresponse()
            conteudo = resposta.read()
            try:
                convertido = json.loads(conteudo.decode("utf-8")) if conteudo else None
            except (UnicodeError, ValueError):
                convertido = None
            return Resposta(resposta.status, convertido, resposta.headers, conteudo)
        finally:
            conexao.close()

    def configurar(self, pin: str = PIN, **extras) -> Resposta:
        r = self.pedir("POST", "/api/configurar", {"pin": pin, **extras}, token=None)
        assert r.status == 200, (r.status, r.dados)
        self.token = r.dados["token"]
        return r

    def desbloquear(self, pin: str = PIN) -> Resposta:
        r = self.pedir("POST", "/api/desbloquear", {"pin": pin}, token=None)
        if r.status == 200:
            self.token = r.dados["token"]
        return r

    def registrar(self, texto: str = TEXTO, data_registro: str = "2026-09-30") -> tuple[int, dict]:
        """analisar -> gravar do jeito que a tela faz. Devolve (id, proposta)."""
        proposta = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data_registro})
        assert proposta.status == 200, (proposta.status, proposta.dados)
        p = proposta.dados
        corpo = {"texto": p["texto"], "data_registro": data_registro, "itens": p["itens"],
                 "pessoas": p["pessoas"], "relacoes": p["relacoes"]}
        r = self.pedir("POST", "/api/registros", corpo)
        assert r.status == 200, (r.status, r.dados)
        return r.dados["id"], p


def _esperar(condicao, limite: float = 3.0) -> bool:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return condicao()


def _caminho_de_exemplo(rota: servidor.Rota) -> str:
    return rota.padrao.pattern.replace("([0-9]{1,12})", "1").replace("\\", "")


# ---------------------------------------------------------------- funções puras

class TestFuncoes(unittest.TestCase):
    def test_atraso_do_pin_dobra_ate_o_maximo(self):
        self.assertEqual([servidor.atraso_do_pin(n) for n in range(0, 8)], [0, 1, 2, 4, 8, 16, 30, 30])
        self.assertEqual(servidor.atraso_do_pin(10 ** 6), 30)
        self.assertAlmostEqual(servidor.atraso_do_pin(3, 0.05, 0.1), 0.1)
        # Para a tela e o Retry-After: segundos inteiros, para cima, no mínimo 1.
        self.assertEqual([nucleo.segundos_de_espera(x) for x in (0.01, 0.2, 1.0, 1.0000001, 2.5, 30)],
                         [1, 1, 1, 1, 3, 30])

    def test_so_escuta_em_127_0_0_1(self):
        self.assertEqual(servidor.ENDERECO, "127.0.0.1")
        self.assertNotIn("host", inspect.signature(servidor.criar_servidor).parameters)
        s = ServidorDeTeste()
        try:
            self.assertEqual(s.servidor.server_address[0], "127.0.0.1")
            self.assertEqual(s.servidor.socket.getsockname()[0], "127.0.0.1")
        finally:
            s.fechar()

    def test_rotas_protegidas(self):
        publicas = {(r.metodo, _caminho_de_exemplo(r)) for r in servidor.ROTAS if r.acesso == servidor.PUBLICA}
        self.assertEqual(publicas, {("GET", "/api/ping"), ("GET", "/api/estado"),
                                    ("POST", "/api/configurar"), ("POST", "/api/desbloquear")})

    def test_arquivo_estatico_nao_sai_de_web(self):
        web = config.PASTA_WEB
        self.assertEqual(servidor.arquivo_estatico(web, "/").name, "index.html")
        self.assertEqual(servidor.arquivo_estatico(web, "/app.js").name, "app.js")
        for caminho in ("/../entrenos/config.py", "/%2e%2e/entrenos/config.py", "/..%2fentrenos%2fconfig.py",
                        "/..%5centrenos%5cconfig.py", "/..\\entrenos\\config.py", "/C:/Windows/win.ini",
                        "/C:%5cWindows%5cwin.ini", "/index.html%00.js", "/index.html.", "/index.html%20",
                        "/./index.html", "//index.html", "/.gitignore", "/../README.md", "/%ff%fe",
                        "/app.js::$DATA", "/web/../../iniciar.py", "/nao-existe.js", "/"+"a/" * 3):
            with self.subTest(caminho=caminho):
                self.assertIsNone(servidor.arquivo_estatico(web, caminho))

    def test_log_guarda_so_o_tipo_do_erro(self):
        registros: list[logging.LogRecord] = []

        class Coletor(logging.Handler):
            def emit(self, record):
                registros.append(record)

        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        try:
            try:
                try:
                    raise KeyError("Mariana adora tulipas")
                except KeyError as causa:
                    raise ValueError("PIN pizza de sexta") from causa
            except ValueError as erro:
                servidor.registrar_erro(erro)
        finally:
            logging.getLogger("entrenos").removeHandler(coletor)
        texto = "\n".join(r.getMessage() for r in registros)
        self.assertIn("ValueError", texto)
        self.assertIn("causado por KeyError", texto)
        self.assertIn("test_servidor.py", texto)
        self.assertNotIn("Mariana", texto)
        self.assertNotIn("pizza", texto)

    def test_traduzir_erro(self):
        from entrenos.banco import NaoEncontrado
        self.assertEqual(servidor.traduzir_erro(NaoEncontrado("Registro #3 não encontrado.")).status, 404)
        self.assertEqual(servidor.traduzir_erro(cofre.PinErrado()).status, 401)
        self.assertEqual(servidor.traduzir_erro(cofre.CofreFechado()).status, 423)
        self.assertEqual(servidor.traduzir_erro(ValueError("O registro está vazio.")).mensagem,
                         "O registro está vazio.")
        # Mensagens do próprio Python (em inglês) não vão para a tela.
        for erro in (ValueError("invalid literal for int() with base 10: 'x'"),
                     ValueError("Invalid isoformat string: 'amanhã'"), ValueError("'x' is not a valid TipoItem")):
            self.assertEqual(servidor.traduzir_erro(erro).mensagem, servidor.DADO_INVALIDO)
        with mock.patch.object(nucleo, "registrar_erro"):
            falha = servidor.traduzir_erro(RuntimeError("Mariana"))
        self.assertEqual((falha.status, falha.mensagem), (500, servidor.FALHA_INTERNA))


class TestGitignore(unittest.TestCase):
    """A exportação (sem criptografia) e os logs não podem ir para o Git por engano."""

    @staticmethod
    def ignorado(nome: str) -> bool:
        regras = [l.strip() for l in (config.RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#") and not l.strip().endswith("/")]
        return any(fnmatch.fnmatch(nome.lower(), r.lower()) for r in regras)

    def test_nome_da_exportacao_da_tela_e_ignorado(self):
        app = (config.PASTA_WEB / "app.js").read_text(encoding="utf-8")
        m = re.search(r"baixarJson\(dados, `([^`$]+)\$\{", app)
        self.assertIsNotNone(m, "o nome do arquivo exportado mudou: confira o .gitignore")
        self.assertTrue(self.ignorado(m.group(1) + "2026-09-30.json"), m.group(1))

    def test_logs_e_cofres_sao_ignorados(self):
        for nome in ("erros.log", "erros.log.1", "entrenos.cofre", "entrenos.cofre.tmp-ab12",
                     "entrenos-backup-2026-09-30.cofre", "exportacao.json", "entrenos-export-2026-09-30.json"):
            with self.subTest(nome=nome):
                self.assertTrue(self.ignorado(nome))


# ---------------------------------------------------------------- segurança

class TestSeguranca(unittest.TestCase):
    """Um servidor configurado e desbloqueado para a classe inteira (nada aqui bloqueia)."""

    @classmethod
    def setUpClass(cls):
        cls.s = ServidorDeTeste()
        cls.s.configurar()

    @classmethod
    def tearDownClass(cls):
        cls.s.fechar()

    def test_host_errado_403(self):
        for host in ("evil.com", f"evil.com:{self.s.porta}", "127.0.0.1", "127.0.0.1:1", f"0.0.0.0:{self.s.porta}",
                     f"192.168.0.10:{self.s.porta}", f"127.0.0.1.nip.io:{self.s.porta}"):
            with self.subTest(host=host):
                r = self.s.pedir("GET", "/api/ping", cabecalhos={"Host": host})
                self.assertEqual(r.status, 403)
                self.assertIn("erro", r.dados)
                self.assertEqual(self.s.pedir("GET", "/", cabecalhos={"Host": host}).status, 403)
                self.assertEqual(self.s.pedir("GET", "/api/config", cabecalhos={"Host": host}).status, 403)

    def test_host_certo(self):
        for host in (f"127.0.0.1:{self.s.porta}", f"localhost:{self.s.porta}", f"LOCALHOST:{self.s.porta}"):
            with self.subTest(host=host):
                r = self.s.pedir("GET", "/api/ping", cabecalhos={"Host": host})
                self.assertEqual(r.status, 200)
                self.assertEqual(r.dados["app"], "entre-nos")

    def test_origin_estranho_403(self):
        for origem in ("http://evil.com", "null", f"http://localhost:{self.s.porta}", f"https://127.0.0.1:{self.s.porta}",
                       f"http://127.0.0.1:{self.s.porta + 1}", "file://"):
            with self.subTest(origem=origem):
                r = self.s.pedir("POST", "/api/analisar", {"texto": "Ela adora café."},
                                 cabecalhos={"Origin": origem})
                self.assertEqual(r.status, 403)
                self.assertEqual(self.s.pedir("POST", "/api/desbloquear", {"pin": PIN}, token=None,
                                              cabecalhos={"Origin": origem}).status, 403)

    def test_origin_da_propria_pagina(self):
        r = self.s.pedir("GET", "/api/config", cabecalhos={"Origin": f"http://127.0.0.1:{self.s.porta}"})
        self.assertEqual(r.status, 200)
        r = self.s.pedir("GET", "/api/config", cabecalhos={"Host": f"localhost:{self.s.porta}",
                                                         "Origin": f"http://localhost:{self.s.porta}"})
        self.assertEqual(r.status, 200)

    def test_sec_fetch_site_de_outro_site_403(self):
        for valor in ("cross-site", "same-site"):
            with self.subTest(valor=valor):
                r = self.s.pedir("GET", "/api/config", cabecalhos={"Sec-Fetch-Site": valor})
                self.assertEqual(r.status, 403)
        for valor in ("same-origin", "none"):
            self.assertEqual(self.s.pedir("GET", "/api/config", cabecalhos={"Sec-Fetch-Site": valor}).status, 200)

    def test_cabecalhos_repetidos_403(self):
        conexao = http.client.HTTPConnection(servidor.ENDERECO, self.s.porta, timeout=10)
        try:
            conexao.putrequest("GET", "/api/ping", skip_host=True)
            conexao.putheader("Host", f"127.0.0.1:{self.s.porta}")
            conexao.putheader("Host", "evil.com")
            conexao.endheaders()
            self.assertEqual(conexao.getresponse().status, 403)
        finally:
            conexao.close()

    def test_sem_token_401_em_toda_rota_protegida(self):
        for rota in servidor.ROTAS:
            if rota.acesso == servidor.PUBLICA:
                continue
            caminho = _caminho_de_exemplo(rota)
            with self.subTest(rota=f"{rota.metodo} {caminho}"):
                r = self.s.pedir(rota.metodo, caminho, token=None)
                self.assertEqual(r.status, 401, r.dados)
                self.assertEqual(self.s.pedir(rota.metodo, caminho, token="token-inventado").status, 401)
        self.assertTrue(self.s.app.desbloqueado)       # nem bloquear nem sair sem token

    def test_rotas_publicas_sem_token(self):
        self.assertEqual(self.s.pedir("GET", "/api/ping", token=None).dados,
                         {"app": "entre-nos", "versao": config.VERSAO})
        estado = self.s.pedir("GET", "/api/estado", token=None).dados
        self.assertEqual(estado["configurado"], True)
        self.assertEqual(estado["desbloqueado"], True)
        # Seção 17: "ia" no lugar de "motor" (sem nome do serviço, URL nem modelo). Correção final
        # da seção 17: também `utilizavel` e `espera_maxima`.
        self.assertEqual(estado["ia"], {"configurada": False, "utilizavel": False, "sai_do_computador": False,
                                        "espera_maxima": None})
        self.assertNotIn("motor", estado)

    def test_content_type_texto_415(self):
        corpo = json.dumps({"texto": "Ela adora café."}).encode()
        r = self.s.pedir("POST", "/api/analisar", bruto=corpo, tipo="text/plain")
        self.assertEqual(r.status, 415)
        self.assertEqual(r.cabecalhos["Connection"], "close")
        self.assertEqual(self.s.pedir("POST", "/api/analisar", bruto=corpo, tipo=None).status, 415)
        self.assertEqual(self.s.pedir("POST", "/api/analisar", bruto=corpo,
                                      tipo="application/x-www-form-urlencoded").status, 415)
        self.assertEqual(self.s.pedir("POST", "/api/desbloquear", bruto=b'{"pin": "x"}', tipo="text/plain",
                                      token=None).status, 415)
        self.assertEqual(self.s.pedir("DELETE", "/api/registros/1", bruto=b"{}", tipo="text/plain").status, 415)
        r = self.s.pedir("POST", "/api/analisar", bruto=corpo, tipo="application/json; charset=utf-8")
        self.assertEqual(r.status, 200)

    def test_corpo_maior_que_1mb_413(self):
        grande = json.dumps({"texto": "a" * servidor.LIMITE_CORPO}).encode()
        self.assertGreater(len(grande), servidor.LIMITE_CORPO)
        r = self.s.pedir("POST", "/api/analisar", bruto=grande)
        self.assertEqual(r.status, 413)
        # Só o cabeçalho anunciando 50 MB: recusa sem esperar o corpo.
        conexao = http.client.HTTPConnection(servidor.ENDERECO, self.s.porta, timeout=10)
        try:
            conexao.putrequest("POST", "/api/analisar")
            conexao.putheader("Content-Type", "application/json")
            conexao.putheader("Content-Length", str(50 * 1024 * 1024))
            conexao.putheader("X-EntreNos-Token", self.s.token)
            conexao.endheaders()
            self.assertEqual(conexao.getresponse().status, 413)
        finally:
            conexao.close()

    def test_json_invalido_400(self):
        for bruto in (b"{nao e json", b"[1, 2]", b'"texto"', b'{"texto": NaN}', b"\xff\xfe", b'{"texto": "\\ud800"}'):
            with self.subTest(bruto=bruto):
                r = self.s.pedir("POST", "/api/analisar", bruto=bruto)
                self.assertEqual(r.status, 400)
                self.assertIn("erro", r.dados)

    def test_barra_ponto_ponto_404(self):
        for caminho in ("/../entrenos/config.py", "/%2e%2e/entrenos/config.py", "/..%2fentrenos%2fconfig.py",
                        "/..%5centrenos%5cconfig.py", "/web/../../iniciar.py", "/%2e%2e%2f%2e%2e%2fWindows/win.ini",
                        "/api/../web/index.html", "/.gitignore", "/C:%5cWindows%5cwin.ini", "//etc/passwd"):
            with self.subTest(caminho=caminho):
                r = self.s.pedir("GET", caminho)
                self.assertEqual(r.status, 404)
                self.assertNotIn(b"VERSAO", r.bruto)
                self.assertNotIn(b"import", r.bruto)

    def test_estaticos_com_tipo_certo_e_cabecalhos(self):
        esperados = {"/": "text/html; charset=utf-8", "/index.html": "text/html; charset=utf-8",
                     "/app.js": "text/javascript; charset=utf-8", "/estilo.css": "text/css; charset=utf-8"}
        for caminho, tipo in esperados.items():
            with self.subTest(caminho=caminho):
                r = self.s.pedir("GET", caminho, token=None)
                self.assertEqual(r.status, 200)
                self.assertEqual(r.cabecalhos["Content-Type"], tipo)
                self.assertEqual(r.cabecalhos["Content-Security-Policy"], servidor.CSP)
                self.assertEqual(r.cabecalhos["X-Frame-Options"], "DENY")
                self.assertEqual(r.cabecalhos["Referrer-Policy"], "no-referrer")
                self.assertEqual(r.cabecalhos["X-Content-Type-Options"], "nosniff")
        self.assertTrue(self.s.pedir("GET", "/").bruto.lstrip().lower().startswith(b"<!doctype html>"))
        self.assertEqual(self.s.pedir("POST", "/index.html", {}).status, 405)

    def test_cabecalhos_da_api(self):
        csp = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
               "connect-src 'self'; frame-ancestors 'none'")
        for r in (self.s.pedir("GET", "/api/config"), self.s.pedir("GET", "/api/nada"),
                  self.s.pedir("GET", "/api/config", token=None)):
            self.assertTrue(r.cabecalhos["Content-Security-Policy"].startswith(csp))
            self.assertEqual(r.cabecalhos["Cache-Control"], "no-store")
            self.assertEqual(r.cabecalhos["X-Frame-Options"], "DENY")
            self.assertEqual(r.cabecalhos["Referrer-Policy"], "no-referrer")
            self.assertEqual(r.cabecalhos["Content-Type"], "application/json; charset=utf-8")
            self.assertEqual(r.cabecalhos["Server"], "EntreNos")

    def test_rota_desconhecida_404_e_metodo_errado_405(self):
        r = self.s.pedir("GET", "/api/nada")
        self.assertEqual(r.status, 404)
        self.assertIn("erro", r.dados)
        r = self.s.pedir("GET", "/api/configurar")
        self.assertEqual(r.status, 405)
        self.assertEqual(r.cabecalhos["Allow"], "POST")
        # PUT existe desde a seção 17 (/api/ia/config/...): em /api/config é método errado.
        self.assertEqual(self.s.pedir("PUT", "/api/config", {}).status, 405)
        self.assertEqual(self.s.pedir("PROPFIND", "/api/config", {}).status, 501)
        self.assertEqual(self.s.pedir("GET", "/api/registros/abc").status, 404)

    def test_configurar_de_novo_409(self):
        r = self.s.pedir("POST", "/api/configurar", {"pin": "outro pin qualquer"}, token=None)
        self.assertEqual(r.status, 409)

    def test_conexao_reaproveitada(self):
        conexao = http.client.HTTPConnection(servidor.ENDERECO, self.s.porta, timeout=10)
        try:
            for _ in range(3):
                conexao.request("GET", "/api/ping")
                resposta = conexao.getresponse()
                self.assertEqual(json.loads(resposta.read())["app"], "entre-nos")
        finally:
            conexao.close()

    def test_nada_vai_para_o_stderr(self):
        saida = io.StringIO()
        with contextlib.redirect_stderr(saida):
            self.s.pedir("GET", "/api/busca?q=" + urllib.parse.quote("segredo da Mariana"))
            self.s.pedir("GET", "/nao-existe-segredo")
            self.s.pedir("POST", "/api/analisar", {"texto": "segredo da Mariana"}, tipo="text/plain")
        self.assertEqual(saida.getvalue(), "")


# ---------------------------------------------------------------- fluxo completo

class TestFluxo(unittest.TestCase):
    def setUp(self):
        self.s = ServidorDeTeste()
        self.addCleanup(self.s.fechar)

    def test_configurar_analisar_registrar_hoje_busca_bloquear_desbloquear(self):
        s = self.s
        estado = s.pedir("GET", "/api/estado", token=None).dados
        self.assertEqual((estado["configurado"], estado["desbloqueado"]), (False, False))
        self.assertEqual(s.pedir("POST", "/api/desbloquear", {"pin": PIN}, token=None).status, 409)
        self.assertEqual(s.pedir("GET", "/api/hoje", token=None).status, 423)
        self.assertEqual(s.pedir("POST", "/api/configurar", {"pin": "12345"}, token=None).status, 400)

        s.configurar(nome_dela="Ana", inicio_namoro="2025-02-10", aniversario_dela="1999-05-12")
        token1 = s.token
        self.assertGreaterEqual(len(token1), 40)
        estado = s.pedir("GET", "/api/estado", token=None).dados
        self.assertEqual((estado["configurado"], estado["desbloqueado"]), (True, True))
        self.assertFalse(estado["ia"]["configurada"])
        self.assertFalse(estado["ia"]["sai_do_computador"])
        cfg = s.pedir("GET", "/api/config").dados
        self.assertEqual((cfg["nome_dela"], cfg["inicio_namoro"], cfg["aniversario_dela"]),
                         ("Ana", "2025-02-10", "1999-05-12"))

        r = s.pedir("POST", "/api/analisar", {"texto": TEXTO, "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 200, r.dados)
        proposta = r.dados
        self.assertEqual(proposta["texto"], TEXTO)
        self.assertEqual(proposta["referencia"], "2026-09-30")
        self.assertTrue(proposta["itens"])
        for item in proposta["itens"]:
            self.assertIn(item["trecho"], TEXTO)
        self.assertIsInstance(proposta["relacoes"], list)
        self.assertEqual(s.pedir("GET", "/api/registros").dados, [])     # analisar não grava

        rid, _ = s.registrar()
        lista = s.pedir("GET", "/api/registros").dados
        self.assertEqual([r["id"] for r in lista], [rid])
        registro = s.pedir("GET", f"/api/registros/{rid}").dados
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["data_registro"], "2026-09-30")
        self.assertEqual(len(registro["itens"]), len(proposta["itens"]))

        hoje = s.pedir("GET", "/api/hoje")
        self.assertEqual(hoje.status, 200, hoje.dados)
        self.assertEqual(set(hoje.dados), {"alertas", "proximos", "lembrar", "ultimos"})
        self.assertIn(rid, [u["id"] for u in hoje.dados["ultimos"]])
        self.assertEqual(s.pedir("GET", "/api/hoje?data=2026-10-01").status, 200)

        busca = s.pedir("GET", "/api/busca?q=" + urllib.parse.quote("o que ela disse sobre tulipas"))
        self.assertEqual(busca.status, 200, busca.dados)
        self.assertIn(rid, [e["registro_id"] for e in busca.dados["encontrados"]])
        self.assertTrue(busca.dados["texto"])

        self.assertEqual(s.pedir("POST", "/api/bloquear").status, 200)
        self.assertFalse(s.app.desbloqueado)
        self.assertFalse(s.app.cofre.aberto)
        r = s.pedir("GET", "/api/hoje")
        self.assertEqual(r.status, 423)
        self.assertIn("bloqueado", r.dados["erro"])
        estado = s.pedir("GET", "/api/estado", token=None).dados
        self.assertEqual((estado["configurado"], estado["desbloqueado"]), (True, False))
        self.assertIsNone(estado["ia"]["configurada"])         # os ajustes estão no cofre fechado

        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        self.assertEqual(s.desbloquear().status, 200)
        self.assertNotEqual(s.token, token1)
        self.assertEqual(s.pedir("GET", "/api/registros", token=token1).status, 401)
        registro = s.pedir("GET", f"/api/registros/{rid}").dados       # relido do arquivo cifrado
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(len(registro["itens"]), len(proposta["itens"]))

        conteudo = (Path(s.pasta) / config.ARQUIVO_COFRE).read_bytes()
        self.assertTrue(conteudo.startswith(b"ENTRENOS1"))
        for palavra in ("tulipas", "Sandra", "cirurgia"):
            self.assertNotIn(palavra.encode("utf-8"), conteudo)

    def test_segunda_aba_recebe_o_mesmo_token(self):
        self.s.configurar()
        token = self.s.token
        self.assertEqual(self.s.desbloquear().status, 200)
        self.assertEqual(self.s.token, token)

    def test_registro_nao_aceita_trecho_inventado(self):
        s = self.s
        s.configurar()
        proposta = s.pedir("POST", "/api/analisar", {"texto": TEXTO}).dados
        itens = proposta["itens"]
        itens[0]["trecho"] = "Ela odeia tulipas."
        r = s.pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30", "itens": itens})
        self.assertEqual(r.status, 400)
        self.assertEqual(s.pedir("GET", "/api/registros").dados, [])
        for corpo in ({"texto": "", "itens": []}, {"texto": TEXTO, "data_registro": "30/09/2026"},
                      {"texto": TEXTO, "itens": "nada"}, {"texto": TEXTO, "itens": [{"trecho": TEXTO}]}):
            with self.subTest(corpo=corpo):
                self.assertEqual(s.pedir("POST", "/api/registros", corpo).status, 400)
        self.assertEqual(s.pedir("POST", "/api/analisar", {"texto": "   "}).status, 400)
        self.assertEqual(s.pedir("POST", "/api/analisar", {"texto": TEXTO, "data_registro": "amanhã"}).status, 400)

    def test_demais_rotas(self):
        s = self.s
        s.configurar(nome_dela="Ana")
        rid, _ = s.registrar()

        # pessoas
        r = s.pedir("POST", "/api/pessoas", {"nome": "Mariana", "relacao": "melhor amiga", "apelidos": ["Mari"],
                                             "observacoes": "", "aniversario": "1998-05-12"})
        self.assertEqual(r.status, 200, r.dados)
        pid = r.dados["id"]
        pessoa = next(p for p in s.pedir("GET", "/api/pessoas").dados if p["id"] == pid)
        self.assertEqual((pessoa["nome"], pessoa["aniversario"], pessoa["apelidos"]), ("Mariana", "1998-05-12", ["Mari"]))
        self.assertEqual(s.pedir("PATCH", f"/api/pessoas/{pid}", {"observacoes": "Amiga de escola"}).status, 200)
        self.assertEqual(s.pedir("PATCH", f"/api/pessoas/{pid}", {"aniversario": "12/05"}).status, 400)
        self.assertEqual(s.pedir("PATCH", f"/api/pessoas/{pid}", {"idade": 3}).status, 400)
        self.assertEqual(s.pedir("PATCH", f"/api/pessoas/{pid}", {"id": 3}).status, 400)
        self.assertEqual(s.pedir("POST", "/api/pessoas", {"nome": None, "relacao": None}).status, 400)
        self.assertEqual(s.pedir("DELETE", f"/api/pessoas/{pid}").status, 200)
        self.assertEqual(s.pedir("DELETE", f"/api/pessoas/{pid}").status, 404)

        # itens
        itens = s.pedir("GET", f"/api/registros/{rid}").dados["itens"]
        iid = itens[0]["id"]
        self.assertEqual(s.pedir("PATCH", f"/api/itens/{iid}", {"importancia": 3, "certeza": "confirmado"}).status, 200)
        item = next(i for i in s.pedir("GET", f"/api/registros/{rid}").dados["itens"] if i["id"] == iid)
        self.assertEqual((item["importancia"], item["certeza"]), (3, "confirmado"))
        r = s.pedir("PATCH", f"/api/itens/{iid}", {"id": 99})
        self.assertEqual((r.status, r.dados["erro"]), (400, "Campos que não podem ser alterados: id"))
        self.assertEqual(s.pedir("PATCH", f"/api/itens/{iid}", {"trecho": "inventado"}).status, 400)
        self.assertEqual(s.pedir("PATCH", "/api/itens/9999", {"importancia": 1}).status, 404)
        self.assertEqual(s.pedir("DELETE", f"/api/itens/{iid}").status, 200)
        self.assertEqual(s.pedir("DELETE", f"/api/itens/{iid}").status, 404)

        # relações: contradição gravada com a revisão e resolvida depois
        antigo = s.pedir("GET", f"/api/registros/{rid}").dados["itens"][0]
        texto2 = "Hoje ela comentou uma coisa diferente."
        r = s.pedir("POST", "/api/registros", {
            "texto": texto2, "data_registro": "2026-09-30",
            "itens": [{"tipo": "nota", "resumo": "Comentou algo diferente", "trecho": texto2, "certeza": "observado"}],
            "relacoes": [{"tipo": "contradicao", "motivo": "teste", "item_novo": 0, "item_antigo_id": antigo["id"]}]})
        self.assertEqual(r.status, 200, r.dados)
        rid2 = r.dados["id"]
        relacao = s.pedir("GET", f"/api/registros/{rid2}").dados["relacoes"][0]
        self.assertEqual(relacao["status"], "pendente")
        self.assertEqual(s.pedir("POST", f"/api/relacoes/{relacao['id']}", {"acao": "sei-la"}).status, 400)
        self.assertEqual(s.pedir("POST", "/api/relacoes/9999", {"acao": "atualizar_preferencia"}).status, 404)
        self.assertEqual(s.pedir("POST", f"/api/relacoes/{relacao['id']}", {"acao": "atualizar_preferencia"}).status, 200)
        atualizado = next(i for i in s.pedir("GET", f"/api/registros/{rid}").dados["itens"] if i["id"] == antigo["id"])
        self.assertEqual(atualizado["status"], "mudou")
        self.assertEqual(s.pedir("GET", f"/api/registros/{rid2}").dados["relacoes"][0]["status"], "confirmada")
        self.assertEqual(s.pedir("POST", f"/api/relacoes/{relacao['id']}", {"acao": "descartar"}).status, 200)

        # alertas, datas, perfil
        self.assertEqual(s.pedir("POST", "/api/alertas/dispensar", {"chave": "evento:1:2026-10-20:d7"}).status, 200)
        self.assertEqual(s.pedir("POST", "/api/alertas/dispensar", {"chave": ""}).status, 400)
        datas = s.pedir("GET", "/api/datas?dias=365")
        self.assertEqual(datas.status, 200, datas.dados)
        self.assertIsInstance(datas.dados, list)
        self.assertEqual(s.pedir("GET", "/api/datas?dias=0").status, 400)
        self.assertEqual(s.pedir("GET", "/api/datas?dias=abc").status, 400)
        perfil = s.pedir("GET", "/api/perfil")
        self.assertEqual(perfil.status, 200, perfil.dados)
        self.assertEqual(perfil.dados["ela"]["nome"], "Ana")

        # busca
        self.assertEqual(s.pedir("GET", "/api/busca?q=").status, 400)
        self.assertEqual(s.pedir("GET", "/api/busca?q=" + "a" * 600).status, 400)

        # ajustes
        cfg = s.pedir("GET", "/api/config").dados
        self.assertEqual(cfg["minutos_bloqueio"], config.MINUTOS_BLOQUEIO_PADRAO)
        r = s.pedir("PATCH", "/api/config", {"minutos_bloqueio": 5, "antecedencias": [7, 1]})
        self.assertEqual((r.status, r.dados["minutos_bloqueio"], r.dados["antecedencias"]), (200, 5, [7, 1]))
        r = s.pedir("PATCH", "/api/config", {"minutos_bloqueio": 0})
        self.assertEqual(r.status, 400)
        self.assertIn("1440", r.dados["erro"])
        # Seção 17.4: ajustes da IA (e os antigos do motor) só pela área da IA.
        for campos in ({"motor_chave": "sk-segredo"}, {"motor": "openai"}, {"ia_pin": None},
                       {"ia_principal": {"provedor": "outro"}}, {"ia_tempo_limite": 30, "minutos_bloqueio": 7}):
            with self.subTest(campos=campos):
                self.assertEqual(s.pedir("PATCH", "/api/config", campos).status, 400)
        self.assertEqual(s.pedir("GET", "/api/config").dados["minutos_bloqueio"], 5)
        self.assertNotIn("motor_chave", s.pedir("GET", "/api/config").dados)

        # histórico e exportação
        historico = s.pedir("GET", "/api/historico")
        self.assertEqual(historico.status, 200)
        self.assertTrue(historico.dados)
        self.assertNotIn("tulipas", json.dumps(historico.dados, ensure_ascii=False))
        exportado = s.pedir("GET", "/api/exportar").dados
        self.assertEqual(exportado["app"], "entre-nos")
        self.assertIn(TEXTO, [r["texto"] for r in exportado["registros"]])
        self.assertNotIn("sk-segredo", json.dumps(exportado))

        # paginação e apagar
        self.assertEqual(len(s.pedir("GET", "/api/registros?limite=1").dados), 1)
        self.assertEqual(s.pedir("GET", "/api/registros?limite=0").status, 400)
        primeira = s.pedir("GET", "/api/registros?limite=1").dados[0]["id"]
        resto = s.pedir("GET", f"/api/registros?limite=10&antes_de={primeira}").dados
        self.assertNotIn(primeira, [r["id"] for r in resto])
        self.assertEqual(s.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual(s.pedir("GET", f"/api/registros/{rid}").status, 404)
        self.assertEqual(s.pedir("DELETE", f"/api/registros/{rid}").status, 404)
        busca = s.pedir("GET", "/api/busca?q=tulipas").dados
        self.assertNotIn(rid, [e["registro_id"] for e in busca["encontrados"]])
        self.assertNotIn(b"tulipas", (Path(s.pasta) / config.ARQUIVO_COFRE).read_bytes())

    def test_pin_facil_recusado_na_configuracao(self):
        for pin in ("000000", "123456", "aaaaaa", "      ", "senha123", "12345678"):
            with self.subTest(pin=pin):
                r = self.s.pedir("POST", "/api/configurar", {"pin": pin}, token=None)
                self.assertEqual(r.status, 400)
                self.assertTrue(r.dados["erro"].startswith(("O PIN", "Um PIN", "Esse PIN")))
        self.assertFalse(self.s.app.cofre.existe())
        self.s.configurar()
        r = self.s.pedir("POST", "/api/pin", {"atual": PIN, "novo": "111111"})
        self.assertEqual(r.status, 400)
        self.assertIn("O PIN novo", r.dados["erro"])

    def test_apagar_tira_das_copias_automaticas(self):
        dia = [date(2026, 9, 29)]
        s = ServidorDeTeste(hoje=lambda: dia[0])
        self.addCleanup(s.fechar)
        s.configurar()
        texto = "Ela me contou em segredo sobre o Projeto Mandragorix."
        rid, _ = s.registrar(texto, "2026-09-29")
        fica, _ = s.registrar("Ela adora tulipas amarelas.", "2026-09-29")
        s.registrar("Ela comentou do trabalho.", "2026-09-29")    # cria o backup do dia 29 com o segredo
        dia[0] = date(2026, 9, 30)
        self.assertEqual(s.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        backups = s.app.cofre.backups()
        self.assertEqual([b.name for b in backups], ["entrenos-2026-09-30.cofre", "entrenos-2026-09-29.cofre"])
        abertos = []
        for backup in backups:
            copia = cofre.Cofre(Path(tempfile.mkdtemp(prefix="entrenos-teste-copia-")))
            self.addCleanup(shutil.rmtree, copia.pasta, True)
            shutil.copyfile(backup, copia.arquivo)
            abertos.append(copia.abrir(PIN))
            copia.fechar()
            with self.subTest(backup=backup.name):
                self.assertNotIn(b"Mandragorix", abertos[-1])
                self.assertNotIn(b"mandragorix", abertos[-1])
        # A cópia de hoje (feita logo antes de apagar) perdeu só o registro apagado.
        banco = servidor.Banco.de_bytes(abertos[0])
        self.addCleanup(banco.fechar)
        self.assertIsNone(banco.registro(rid))
        self.assertIsNotNone(banco.registro(fica))

    def test_atualizar_preferencia_vinda_de_uma_acao(self):
        # "Ela pediu sushi no jantar e amou." vira nota + contradição; atualizar não pode sumir com o gosto.
        s = self.s
        s.configurar()
        s.registrar("Ela não gosta de sushi.", "2026-03-12")
        rid, proposta = s.registrar("Ela pediu sushi no jantar.", "2026-09-30")
        relacoes = s.pedir("GET", f"/api/registros/{rid}").dados["relacoes"]
        self.assertEqual([r["tipo"] for r in relacoes], ["contradicao"])
        self.assertEqual(s.pedir("POST", f"/api/relacoes/{relacoes[0]['id']}", {"acao": "atualizar_preferencia"}).status,
                         200)
        itens = s.pedir("GET", f"/api/registros/{rid}").dados["itens"]
        novos = [(i["tipo"], i["alvo"], i["polaridade"], i["certeza"]) for i in itens]
        self.assertIn(("preferencia", "sushi", 1, "observado"), novos)
        perfil = s.pedir("GET", "/api/perfil").dados["preferencias"]
        texto = json.dumps(perfil, ensure_ascii=False)
        self.assertIn("Gosta de sushi", texto)

    def test_backup(self):
        s = self.s
        s.configurar()
        s.registrar()
        destino = tempfile.mkdtemp(prefix="entrenos-teste-backup-")
        self.addCleanup(shutil.rmtree, destino, True)
        r = s.pedir("POST", "/api/backup", {"pasta": destino})
        self.assertEqual(r.status, 200, r.dados)
        arquivo = Path(r.dados["arquivo"])
        self.assertEqual(arquivo.parent, Path(destino))
        self.assertEqual(arquivo.name, "entrenos-backup-2026-09-30.cofre")
        self.assertTrue(arquivo.read_bytes().startswith(b"ENTRENOS1"))
        self.assertNotIn(b"tulipas", arquivo.read_bytes())
        # A cópia abre com o PIN e traz o registro.
        copia = cofre.Cofre(Path(tempfile.mkdtemp(prefix="entrenos-teste-copia-")))
        self.addCleanup(shutil.rmtree, copia.pasta, True)
        shutil.copyfile(arquivo, copia.arquivo)
        self.assertIn("tulipas".encode(), copia.abrir(PIN))
        copia.fechar()
        for pasta in ("relativa", "", os.path.join(destino, "nao-existe")):
            with self.subTest(pasta=pasta):
                r = s.pedir("POST", "/api/backup", {"pasta": pasta})
                self.assertEqual(r.status, 400)
                self.assertIn("erro", r.dados)


# ---------------------------------------------------------------- PIN

class TestPin(unittest.TestCase):
    """PIN errado bloqueia por 1 s, 2 s, 4 s... até 30 s (aqui 0,2 s e 0,4 s), sem esperar dentro
    do pedido: quem tenta antes da hora recebe 429 e nenhum PIN é testado (seção 19)."""

    def novo(self, **opcoes) -> tuple[ServidorDeTeste, Relogio]:
        relogio = Relogio()
        s = ServidorDeTeste(relogio=relogio, **opcoes)
        self.addCleanup(s.fechar)
        s.configurar()
        return s, relogio

    def assert_espere(self, r: Resposta, segundos: int) -> None:
        self.assertEqual(r.status, 429, r.dados)
        self.assertEqual(r.dados["espere_segundos"], segundos)
        self.assertEqual(r.cabecalhos["Retry-After"], str(segundos))
        self.assertEqual(r.dados["erro"], f"Espere {segundos} s antes de tentar o PIN de novo.")

    def test_pin_errado_bloqueia_cada_vez_mais(self):
        s, relogio = self.novo(atraso_pin=0.2, atraso_pin_maximo=0.4)
        s.pedir("POST", "/api/bloquear")
        r = s.desbloquear("pin errado demais")
        self.assertEqual((r.status, r.dados["erro"], r.dados["espere_segundos"]), (401, "PIN incorreto.", 1))
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.2)
        # Dentro do bloqueio nem o PIN certo é testado.
        self.assert_espere(s.desbloquear(), 1)
        self.assertFalse(s.app.desbloqueado)
        relogio.avancar(0.19)
        self.assert_espere(s.desbloquear("pin errado demais"), 1)
        self.assertEqual(s.app._falhas_pin, 1)                # tentativa no bloqueio não conta
        relogio.avancar(0.02)
        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.4)    # dobra
        relogio.avancar(0.41)
        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.4)    # sem o teto seriam 0,8 s
        self.assertEqual(s.app._falhas_pin, 3)
        relogio.avancar(0.41)
        self.assertEqual(s.desbloquear().status, 200)
        self.assertEqual((s.app._falhas_pin, s.app.espera_do_pin()), (0, 0))
        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        self.assertAlmostEqual(s.app.espera_do_pin(), 0.2)    # voltou para o primeiro degrau

    def test_nao_espera_dentro_do_pedido(self):
        # Antes o pedido dormia o atraso (inviável no navegador); agora responde na hora e diz
        # quanto falta, em segundos inteiros, no JSON e no Retry-After.
        s, relogio = self.novo(atraso_pin=30, atraso_pin_maximo=30)
        s.pedir("POST", "/api/bloquear")
        inicio = time.monotonic()
        r = s.desbloquear("pin errado demais")
        self.assertEqual((r.status, r.dados["espere_segundos"]), (401, 30))
        self.assert_espere(s.desbloquear(), 30)
        relogio.avancar(29.5)
        self.assert_espere(s.desbloquear(), 1)                # arredonda para cima
        self.assertLess(time.monotonic() - inicio, 5)
        relogio.avancar(0.5)
        self.assertEqual(s.desbloquear().status, 200)

    def test_tentativas_em_paralelo_testam_um_pin_so(self):
        # A conferência do bloqueio e o teste do PIN acontecem juntos, com a trava do PIN:
        # quatro tentativas ao mesmo tempo testam um PIN só, e as outras três recebem 429.
        s, _ = self.novo(atraso_pin=5, atraso_pin_maximo=5)
        s.pedir("POST", "/api/bloquear")
        testados: list[str] = []
        abrir = s.app.cofre.abrir

        def abrir_devagar(pin):
            testados.append(pin)
            time.sleep(0.05)                                  # alarga a janela das outras
            return abrir(pin)

        status: list[int] = []
        with mock.patch.object(s.app.cofre, "abrir", side_effect=abrir_devagar):
            threads = [threading.Thread(target=lambda: status.append(s.desbloquear("errado errado").status))
                       for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(sorted(status), [401, 429, 429, 429])
        self.assertEqual(len(testados), 1)
        self.assertEqual(s.app._falhas_pin, 1)

    def test_trocar_pin(self):
        s, relogio = self.novo(atraso_pin=0.2)
        s.registrar()
        r = s.pedir("POST", "/api/pin", {"atual": "nao e esse", "novo": "frase nova e secreta"})
        self.assertEqual((r.status, r.dados["erro"], r.dados["espere_segundos"]), (401, "O PIN atual não confere.", 1))
        self.assertTrue(s.pedir("GET", "/api/estado", token=None).dados["desbloqueado"])
        self.assertEqual(s.pedir("POST", "/api/pin", {"atual": PIN, "novo": "curto"}).status, 400)
        self.assert_espere(s.pedir("POST", "/api/pin", {"atual": PIN, "novo": "frase nova e secreta"}), 1)
        relogio.avancar(0.21)
        self.assertEqual(s.pedir("POST", "/api/pin", {"atual": PIN, "novo": "frase nova e secreta"}).status, 200)
        self.assertEqual(s.pedir("GET", "/api/registros").status, 200)      # sessão continua
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(s.desbloquear(PIN).status, 401)
        relogio.avancar(0.21)
        self.assertEqual(s.desbloquear("frase nova e secreta").status, 200)
        self.assertEqual(len(s.pedir("GET", "/api/registros").dados), 1)


# ---------------------------------------------------------------- bloqueio automático

class TestInatividade(unittest.TestCase):
    def test_vigia_bloqueia_sozinha(self):
        s = ServidorDeTeste(minutos_bloqueio=0.02, intervalo_verificacao=0.05)      # 1,2 s
        self.addCleanup(s.fechar)
        s.configurar()
        s.registrar()
        self.assertTrue(s.app.desbloqueado)
        self.assertTrue(_esperar(lambda: not s.app.desbloqueado, 5))    # sem nenhum pedido
        self.assertFalse(s.app.cofre.aberto)
        self.assertEqual(s.pedir("GET", "/api/registros").status, 423)
        self.assertFalse(s.pedir("GET", "/api/estado", token=None).dados["desbloqueado"])
        self.assertEqual(s.desbloquear().status, 200)
        self.assertEqual(len(s.pedir("GET", "/api/registros").dados), 1)   # salvo antes de bloquear

    def test_uso_mantem_aberto(self):
        s = ServidorDeTeste(minutos_bloqueio=0.01, intervalo_verificacao=0.05)      # 0,6 s
        self.addCleanup(s.fechar)
        s.configurar()
        fim = time.monotonic() + 1.5
        while time.monotonic() < fim:
            self.assertEqual(s.pedir("GET", "/api/config").status, 200)
            time.sleep(0.15)
        self.assertTrue(s.app.desbloqueado)
        self.assertTrue(_esperar(lambda: not s.app.desbloqueado, 3))

    def test_pedido_atrasado_nao_passa_mesmo_antes_da_vigia(self):
        s = ServidorDeTeste(minutos_bloqueio=0.005, intervalo_verificacao=1000)
        self.addCleanup(s.fechar)
        s.configurar()
        time.sleep(0.5)
        self.assertTrue(s.app.desbloqueado)               # a vigia ainda não passou
        self.assertEqual(s.pedir("GET", "/api/config").status, 423)
        self.assertFalse(s.app.desbloqueado)

    def test_minutos_dos_ajustes(self):
        s = ServidorDeTeste(intervalo_verificacao=1000)
        self.addCleanup(s.fechar)
        s.configurar()
        s.pedir("PATCH", "/api/config", {"minutos_bloqueio": 1})
        self.assertEqual(s.app._limite_inatividade(s.app._banco), 60)
        s.app._ultima_atividade -= 61
        self.assertTrue(s.app.verificar_inatividade())
        self.assertEqual(s.pedir("GET", "/api/config").status, 423)


# ---------------------------------------------------------------- sair, módulos faltando e main

class TestSair(unittest.TestCase):
    def test_sair_salva_e_encerra(self):
        s = ServidorDeTeste()
        self.addCleanup(s.fechar, False)
        self.addCleanup(shutil.rmtree, s.pasta, True)
        s.configurar()
        rid, _ = s.registrar()
        self.assertEqual(s.pedir("POST", "/api/sair", token=None).status, 401)
        r = s.pedir("POST", "/api/sair")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.dados, {})
        s.thread.join(5)
        self.assertFalse(s.thread.is_alive())
        self.assertFalse(s.app.desbloqueado)
        self.assertFalse(s.app.cofre.aberto)
        s.servidor.server_close()
        with self.assertRaises(OSError):
            socket.create_connection((servidor.ENDERECO, s.porta), timeout=2).close()
        # Abrindo de novo na mesma pasta, está tudo lá.
        outro = ServidorDeTeste(pasta=s.pasta)
        self.addCleanup(outro.fechar, False)
        self.assertEqual(outro.desbloquear().status, 200)
        self.assertEqual(outro.pedir("GET", f"/api/registros/{rid}").dados["texto"], TEXTO)


class TestModulosFaltando(unittest.TestCase):
    def setUp(self):
        self.s = ServidorDeTeste()
        self.addCleanup(self.s.fechar)
        self.s.configurar()
        self.real = nucleo._importar

    def _sem(self, *nomes):
        def importar(nome):
            if nome in nomes:
                raise ImportError(f"sem {nome}")
            return self.real(nome)
        return mock.patch.object(nucleo, "_importar", importar)

    def test_rota_do_modulo_que_falta_responde_503(self):
        with self._sem("alertas", "perfil", "busca"):
            for rota in ("/api/hoje", "/api/datas", "/api/perfil", "/api/busca?q=tulipas"):
                with self.subTest(rota=rota):
                    r = self.s.pedir("GET", rota)
                    self.assertEqual(r.status, 503)
                    self.assertEqual(r.dados["erro"], servidor.INDISPONIVEL)
            self.assertEqual(self.s.pedir("GET", "/api/registros").status, 200)
            self.assertEqual(self.s.pedir("GET", "/api/ping").status, 200)

    def test_sem_motor_analisar_503_e_estado_continua(self):
        with self._sem("motor"):
            self.assertEqual(self.s.pedir("POST", "/api/analisar", {"texto": TEXTO}).status, 503)
            estado = self.s.pedir("GET", "/api/estado", token=None)
            self.assertEqual(estado.status, 200)
            self.assertEqual(estado.dados["ia"], {"configurada": False, "utilizavel": False,
                                                  "sai_do_computador": False, "espera_maxima": None})

    def test_sem_contexto_a_analise_sai_sem_relacoes(self):
        with self._sem("contexto"):
            r = self.s.pedir("POST", "/api/analisar", {"texto": TEXTO})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.dados["relacoes"], [])
        self.assertTrue(any("ligações" in a for a in r.dados["avisos"]))

    def test_erro_interno_500_sem_conteudo_no_log(self):
        registros: list[str] = []

        class Coletor(logging.Handler):
            def emit(self, record):
                registros.append(record.getMessage())

        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)

        def explodir(*_):
            raise RuntimeError("Mariana adora tulipas")

        with mock.patch.object(servidor.EntreNos, "_perfil", explodir):
            r = self.s.pedir("GET", "/api/perfil")
        self.assertEqual((r.status, r.dados["erro"]), (500, servidor.FALHA_INTERNA))
        self.assertTrue(any("RuntimeError" in m for m in registros))
        self.assertFalse(any("Mariana" in m for m in registros))


class TestMain(unittest.TestCase):
    def test_e_entre_nos(self):
        s = ServidorDeTeste()
        self.addCleanup(s.fechar)
        self.assertTrue(servidor.e_entre_nos(s.porta))
        with socket.socket() as ocupado:
            ocupado.bind((servidor.ENDERECO, 0))
            self.assertFalse(servidor.e_entre_nos(ocupado.getsockname()[1], espera=1))

    def test_main_so_mostra_quando_ja_esta_aberto(self):
        s = ServidorDeTeste()
        self.addCleanup(s.fechar)
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-main-")
        self.addCleanup(shutil.rmtree, pasta, True)
        with mock.patch.object(servidor, "_abrir_navegador") as abrir:
            self.assertEqual(servidor.main(["--porta", str(s.porta), "--dados", pasta]), 0)
            abrir.assert_called_once_with(s.porta)
            self.assertEqual(servidor.main(["--sem-navegador", "--porta", str(s.porta), "--dados", pasta]), 0)
            self.assertEqual(abrir.call_count, 1)
        self.assertEqual(s.pedir("GET", "/api/ping").status, 200)       # o primeiro continua de pé

    def test_main_porta_de_outro_programa(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-main-")
        self.addCleanup(shutil.rmtree, pasta, True)
        with socket.socket() as ocupado:
            ocupado.bind((servidor.ENDERECO, 0))
            porta = ocupado.getsockname()[1]
            saida = io.StringIO()
            with contextlib.redirect_stderr(saida), mock.patch.object(servidor, "_abrir_navegador") as abrir:
                self.assertEqual(servidor.main(["--sem-navegador", "--porta", str(porta), "--dados", pasta]), 1)
            abrir.assert_not_called()
        self.assertIn("ocupada por outro programa", saida.getvalue())
        self.assertFalse(any(isinstance(h, logging.handlers.RotatingFileHandler)
                             for h in logging.getLogger("entrenos").handlers))


if __name__ == "__main__":
    unittest.main()
