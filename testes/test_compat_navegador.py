"""O núcleo roda no navegador (DESIGN.md, seção 19): Pyodide no iPhone e no Android.

- Sintaxe: todo .py de entrenos/ abre com a gramática do Python 3.12. O Pyodide 314 roda o
  Python 3.14; o 3.12 é margem (Pyodide mais antigo), e quem tem anotações usa
  `from __future__ import annotations` (no 3.12 e no 3.13 elas rodam ao importar).
- Dependências: nucleo.py, ponte.py e tudo o que eles importam (inclusive na hora do uso, pelo
  `_importar`) não importam servidor HTTP, socket, ctypes, webbrowser, nem iniciam thread ou
  dormem (`threading.Thread`, `threading.Timer`, `time.sleep`).
- O núcleo sozinho: `Nucleo.tratar` sem transporte nenhum.
- A ponte: `tratar_json` (texto entra, texto sai) e um diário inteiro num Python separado em que
  iniciar thread e dormir dão erro e o servidor, o ctypes, o webbrowser, o ssl, o rapidfuzz e o
  OpenSSL do hashlib não existem.
- Recursos (seção 20.0): tudo de entrenos/recursos/ (o registro e cada recurso, importados na hora
  por nome) passa pela mesma varredura, e os nove abrem no Python de navegador.
- A revisão de 07/10/2026 (seção 19.7): o bloqueio do PIN sobrevive a reabrir o app, os dois
  relógios (o monotônico do navegador para enquanto o celular dorme), a plataforma "navegador"
  (backup baixando, IA indisponível, limite menor, menos cópias, textos sem Windows), duas abas no
  mesmo diário, bloquear sem regravar, a semelhança igual no computador e no celular, a ponte que
  nunca levanta e o autoteste do Worker.

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026, relógios de mentira e scrypt
pequeno (o real é testado em test_cofre.py).
"""
from __future__ import annotations

import ast
import base64
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from entrenos import cofre, config, motor, nucleo, ponte, texto
from entrenos.regras import pessoas as regras_pessoas
from testes.test_servidor import PIN, REF, TEXTO, Relogio, ServidorDeTeste

RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
PACOTE = RAIZ / "entrenos"
VERSAO_MINIMA = (3, 12)

# O que o núcleo usa (a lista da seção 19); a varredura acha estes e o que mais eles importarem.
ESPERADOS = {"entrenos", "entrenos.nucleo", "entrenos.ponte", "entrenos.config", "entrenos.texto",
             "entrenos.modelos", "entrenos.regras", "entrenos.regras.datas", "entrenos.regras.pessoas",
             "entrenos.regras.categorias", "entrenos.regras.certeza", "entrenos.regras.itens",
             "entrenos.motor", "entrenos.banco", "entrenos.cofre", "entrenos.busca", "entrenos.alertas",
             "entrenos.perfil", "entrenos.contexto", "entrenos.provedores", "entrenos.recursos",
             "entrenos.sincronizacao"}      # seção 22: a mescla roda igual no celular
# Os recursos (seção 20.0) são importados pelo registro na hora do uso (importlib, por nome): a
# varredura os acrescenta pelos arquivos de entrenos/recursos/.
PASTA_RECURSOS = PACOTE / "recursos"
# Módulos que não existem (ou não funcionam) no navegador, ou que só fazem sentido no computador.
MODULOS_PROIBIDOS = ("socket", "socketserver", "http.server", "ctypes", "webbrowser", "_thread",
                     "subprocess", "multiprocessing", "winreg", "msvcrt", "tkinter")
# Nomes que iniciam thread ou dormem.
NOMES_PROIBIDOS = {("threading", "Thread"), ("threading", "Timer"), ("time", "sleep")}
# Funções do núcleo que importam um módulo do pacote na hora do uso: _importar("motor").
IMPORTA_NA_HORA = {"_importar", "_funcao", "_modulo"}

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-navegador-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    for nome, valor in (("SCRYPT_N", 2 ** 12), ("VERIFICADOR_N", 2 ** 10)):
        remendo = mock.patch.object(cofre, nome, valor)
        remendo.start()
        _remendos.append(remendo)


def tearDownModule() -> None:
    for remendo in _remendos:
        remendo.stop()
    _remendos.clear()
    if _env_antigo is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _env_antigo
    shutil.rmtree(_pasta_env, ignore_errors=True)


# ---------------------------------------------------------------- varredura (ast)

def _arquivos() -> list[Path]:
    return sorted(p for p in PACOTE.rglob("*.py") if "__pycache__" not in p.parts)


def _modulos_dos_recursos() -> list[str]:
    """entrenos.recursos e cada entrenos.recursos.<nome> (pelos arquivos da pasta)."""
    return sorted(_nome_do_modulo(p) for p in PASTA_RECURSOS.glob("*.py"))


def _nome_do_modulo(arquivo: Path) -> str:
    partes = list(arquivo.relative_to(RAIZ).with_suffix("").parts)
    if partes[-1] == "__init__":
        partes.pop()
    return ".".join(partes)


def _arquivo_do_modulo(nome: str) -> Path | None:
    base = RAIZ.joinpath(*nome.split("."))
    for candidato in (base.with_suffix(".py"), base / "__init__.py"):
        if candidato.is_file():
            return candidato
    return None


def _ler(arquivo: Path) -> ast.Module:
    return ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))


def _nos_so_para_tipos(arvore: ast.AST) -> set[int]:
    """Nós dentro de `if TYPE_CHECKING:` (não rodam: só servem para as anotações)."""
    ignorar: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.If) and (
                (isinstance(no.test, ast.Name) and no.test.id == "TYPE_CHECKING")
                or (isinstance(no.test, ast.Attribute) and no.test.attr == "TYPE_CHECKING")):
            for filho in no.body:
                ignorar.update(id(n) for n in ast.walk(filho))
    return ignorar


def _base_relativa(modulo: str, e_pacote: bool, nivel: int) -> str:
    partes = modulo.split(".")
    if not e_pacote:
        partes = partes[:-1]
    if nivel > 1:
        partes = partes[:len(partes) - (nivel - 1)]
    return ".".join(partes)


def _importados(arvore: ast.AST, modulo: str, e_pacote: bool) -> set[str]:
    """Nomes absolutos de tudo o que o módulo importa (em qualquer lugar, fora do TYPE_CHECKING),
    inclusive o que o núcleo importa na hora pelo `_importar("motor")`."""
    ignorar = _nos_so_para_tipos(arvore)
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if id(no) in ignorar:
            continue
        if isinstance(no, ast.Import):
            nomes.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom):
            base = no.module or ""
            if no.level:
                pai = _base_relativa(modulo, e_pacote, no.level)
                base = f"{pai}.{base}" if base else pai
            nomes.add(base)
            nomes.update(f"{base}.{a.name}" for a in no.names)
        elif (isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id in IMPORTA_NA_HORA
              and no.args and isinstance(no.args[0], ast.Constant) and isinstance(no.args[0].value, str)):
            nomes.add(f"entrenos.{no.args[0].value}")
    return nomes


def alcancados(*inicio: str) -> set[str]:
    """Os módulos do pacote que `inicio` importa, direta ou indiretamente."""
    vistos: set[str] = set()
    fila = list(inicio)
    while fila:
        nome = fila.pop()
        arquivo = _arquivo_do_modulo(nome)
        if nome in vistos or arquivo is None:
            continue
        vistos.add(nome)
        # Importar entrenos.x roda antes o entrenos/__init__.py (e o de cada pacote no caminho).
        partes = nome.split(".")
        fila.extend(".".join(partes[:i]) for i in range(1, len(partes)))
        fila.extend(n for n in _importados(_ler(arquivo), nome, arquivo.name == "__init__.py")
                    if n == "entrenos" or n.startswith("entrenos."))
    return vistos


def _proibido(modulo: str) -> bool:
    return any(modulo == p or modulo.startswith(p + ".") for p in MODULOS_PROIBIDOS)


def proibidos(arvore: ast.AST) -> list[str]:
    """O que o código usa e o navegador não tem: imports proibidos e threading.Thread/Timer e
    time.sleep (também com apelido: `import time as t; t.sleep`)."""
    ignorar = _nos_so_para_tipos(arvore)
    achados: list[str] = []
    apelidos: dict[str, str] = {}
    for no in ast.walk(arvore):
        if id(no) in ignorar:
            continue
        if isinstance(no, ast.Import):
            for a in no.names:
                if _proibido(a.name):
                    achados.append(f"linha {no.lineno}: import {a.name}")
                apelidos[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(no, ast.ImportFrom) and not no.level and no.module:
            if _proibido(no.module):
                achados.append(f"linha {no.lineno}: from {no.module} import ...")
            for a in no.names:
                if (no.module, a.name) in NOMES_PROIBIDOS or _proibido(f"{no.module}.{a.name}"):
                    achados.append(f"linha {no.lineno}: from {no.module} import {a.name}")
    for no in ast.walk(arvore):
        if id(no) in ignorar or not isinstance(no, ast.Attribute) or not isinstance(no.value, ast.Name):
            continue
        if (apelidos.get(no.value.id, no.value.id), no.attr) in NOMES_PROIBIDOS:
            achados.append(f"linha {no.lineno}: {no.value.id}.{no.attr}")
    return achados


def _tem_anotacao(arvore: ast.AST) -> bool:
    for no in ast.walk(arvore):
        if isinstance(no, ast.AnnAssign):
            return True
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            argumentos = no.args
            todos = [*argumentos.posonlyargs, *argumentos.args, *argumentos.kwonlyargs,
                     argumentos.vararg, argumentos.kwarg]
            if no.returns is not None or any(a is not None and a.annotation is not None for a in todos):
                return True
    return False


def _futuro_das_anotacoes(arvore: ast.Module) -> bool:
    return any(isinstance(no, ast.ImportFrom) and no.module == "__future__"
               and any(a.name == "annotations" for a in no.names) for no in arvore.body)


class TestSintaxe(unittest.TestCase):
    def test_todos_os_modulos_abrem_no_python_3_12(self):
        arquivos = _arquivos()
        self.assertGreater(len(arquivos), 15)
        for arquivo in arquivos:
            with self.subTest(arquivo=arquivo.name):
                ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo),
                          feature_version=VERSAO_MINIMA)

    def test_anotacoes_nao_rodam_ao_importar(self):
        # No Python 3.14 (o do Pyodide 314) as anotações só são lidas quando alguém pede; no 3.12 e
        # no 3.13 (Pyodide mais antigo) elas rodam ao importar, e um nome que só existe no
        # TYPE_CHECKING quebraria o import.
        for arquivo in _arquivos():
            arvore = _ler(arquivo)
            if _tem_anotacao(arvore):
                with self.subTest(arquivo=str(arquivo.relative_to(RAIZ))):
                    self.assertTrue(_futuro_das_anotacoes(arvore))


class TestDependencias(unittest.TestCase):
    def test_varredura_acha_os_modulos_do_nucleo(self):
        achados = alcancados("entrenos.nucleo", "entrenos.ponte")
        self.assertLessEqual(ESPERADOS, achados, sorted(ESPERADOS - achados))
        self.assertNotIn("entrenos.servidor", achados)
        self.assertNotIn("entrenos.__main__", achados)

    def test_nucleo_e_ponte_nao_importam_o_servidor(self):
        for nome in ("entrenos.nucleo", "entrenos.ponte"):
            arquivo = _arquivo_do_modulo(nome)
            with self.subTest(nome=nome):
                importados = _importados(_ler(arquivo), nome, False)
                self.assertFalse({n for n in importados if n == "entrenos.servidor"
                                  or n.startswith("entrenos.servidor.")}, importados)

    def test_nada_de_thread_sleep_socket_ctypes_nem_navegador(self):
        for nome in sorted(alcancados("entrenos.nucleo", "entrenos.ponte", *_modulos_dos_recursos()) | ESPERADOS):
            arquivo = _arquivo_do_modulo(nome)
            with self.subTest(modulo=nome):
                self.assertIsNotNone(arquivo)
                self.assertEqual(proibidos(_ler(arquivo)), [])

    def test_recursos_entram_na_varredura(self):
        # Seção 20.0: tudo de entrenos/recursos/ roda no navegador também; a lista fixa do registro
        # é a dos arquivos, e o núcleo chega ao registro.
        from entrenos import recursos
        nomes = _modulos_dos_recursos()
        self.assertEqual(sorted(n.rsplit(".", 1)[-1] for n in nomes if n != "entrenos.recursos"),
                         sorted(recursos.NOMES))
        varridos = alcancados("entrenos.nucleo", "entrenos.ponte", *nomes)
        self.assertLessEqual(set(nomes), varridos)
        self.assertIn("entrenos.recursos", alcancados("entrenos.nucleo"))
        for nome in nomes:
            with self.subTest(modulo=nome):
                arvore = _ler(_arquivo_do_modulo(nome))
                self.assertEqual(proibidos(arvore), [])
                ast.parse(_arquivo_do_modulo(nome).read_text(encoding="utf-8"), feature_version=VERSAO_MINIMA)
                if _tem_anotacao(arvore):
                    self.assertTrue(_futuro_das_anotacoes(arvore))
                self.assertNotIn("entrenos.servidor", _importados(arvore, nome, nome == "entrenos.recursos"))

    def test_a_varredura_pega_o_que_deve(self):
        codigo = textwrap.dedent("""
            import socket
            import time as relogio
            import threading
            from threading import Thread
            from http import server
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import ctypes
            def f():
                import webbrowser
                relogio.sleep(1)
                threading.Timer(1, f).start()
                threading.RLock()
                relogio.monotonic()
        """)
        achados = proibidos(ast.parse(codigo))
        self.assertEqual(len(achados), 6, achados)
        for trecho in ("import socket", "import Thread", "import server", "webbrowser", "relogio.sleep",
                       "threading.Timer"):
            self.assertTrue(any(trecho in a for a in achados), (trecho, achados))
        self.assertFalse(any("ctypes" in a for a in achados))           # só para as anotações
        self.assertEqual(_importados(ast.parse("_funcao('busca', 'buscar')\n_importar(nome)"), "entrenos.nucleo",
                                     False), {"entrenos.busca"})

    def test_o_servidor_continua_sendo_o_transporte_do_computador(self):
        # O que foi proibido no núcleo mora aqui (e só aqui).
        achados = proibidos(_ler(PACOTE / "servidor.py"))
        for trecho in ("http.server", "socket", "webbrowser", "threading.Thread", "time.sleep"):
            self.assertTrue(any(trecho in a for a in achados), (trecho, achados))

    def test_falha_de_dns_sem_importar_socket(self):
        # O motor deixou de importar `socket`: a falha de nome continua reconhecida.
        erro = motor._erro_ia(urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), 10)
        self.assertEqual((erro.tipo, erro.mensagem), ("conexao", motor._MSG["dns"]))
        recusado = motor._erro_ia(urllib.error.URLError(ConnectionRefusedError()), 10)
        self.assertEqual((recusado.tipo, recusado.mensagem), ("conexao", motor._MSG["conexao"]))


# ---------------------------------------------------------------- o núcleo sem transporte

class TestNucleoSemTransporte(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-nucleo-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.relogio = Relogio()
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: REF, relogio=self.relogio, atraso_pin=1)
        self.addCleanup(self.app.bloquear)

    def pedir(self, metodo, caminho, corpo=None, **kw) -> nucleo.RespostaApi:
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        resposta = self.app.tratar(metodo, caminho, corpo=bruto, **kw)
        self.assertIsInstance(resposta, nucleo.RespostaApi)
        self.assertEqual(json.loads(resposta.corpo.decode("utf-8")), resposta.dados)    # corpo = dados em JSON
        return resposta

    def test_fluxo_e_erros_sem_http(self):
        self.assertEqual(self.pedir("GET", "/api/ping").dados["app"], "entre-nos")
        token = self.pedir("POST", "/api/configurar", {"pin": PIN}).dados["token"]
        self.assertEqual(self.pedir("GET", "/api/registros").status, 401)
        r = self.pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30"}, token=token)
        self.assertEqual(r.status, 200, r.dados)
        lista = self.pedir("GET", "/api/registros", token=token, consulta={"limite": ["9", "1"]})
        self.assertEqual(len(lista.dados), 1)                     # como o parse_qs: vale o último
        self.assertEqual(self.pedir("GET", "/api/registros", token=token, consulta={"limite": "0"}).status, 400)
        self.assertEqual(self.pedir("GET", "/api/registros", token=token, consulta={"limite": 3}).status, 400)
        self.assertEqual(self.pedir("GET", "/api/registros", token=token, consulta=["limite"]).status, 400)
        nao = self.pedir("GET", "/api/nada")
        self.assertEqual((nao.status, nao.cabecalhos), (404, {}))
        errado = self.pedir("GET", "/api/configurar")
        self.assertEqual((errado.status, errado.cabecalhos), (405, {"Allow": "POST"}))
        grande = self.app.tratar("POST", "/api/analisar", corpo=b" " * (nucleo.LIMITE_CORPO + 1), token=token)
        self.assertEqual((grande.status, grande.dados["erro"]), (413, nucleo.mensagem_de_limite(nucleo.LIMITE_CORPO)))
        self.assertEqual(self.app.tratar("POST", "/api/analisar", corpo=b"[1]", token=token).status, 400)
        self.assertEqual(self.app.tratar("POST", "/api/analisar", corpo=None, token=token).status, 400)   # {} sem texto
        with mock.patch.object(nucleo.Nucleo, "_perfil", side_effect=RuntimeError("Mariana")), \
                mock.patch.object(nucleo, "registrar_erro"):
            falha = self.pedir("GET", "/api/perfil", token=token)
        self.assertEqual((falha.status, falha.dados), (500, {"erro": nucleo.FALHA_INTERNA}))
        sair = self.pedir("POST", "/api/sair", {}, token=token)
        self.assertEqual((sair.status, sair.encerrar), (200, True))
        self.assertEqual(self.pedir("POST", "/api/desbloquear", {"pin": PIN}).status, 503)

    def test_inatividade_e_conferida_no_comeco_de_cada_pedido(self):
        token = self.pedir("POST", "/api/configurar", {"pin": PIN}).dados["token"]
        self.relogio.avancar(9 * 60)
        self.assertEqual(self.pedir("GET", "/api/config", token=token).status, 200)     # uso renova
        self.relogio.avancar(9 * 60)
        self.assertTrue(self.app.desbloqueado)
        self.relogio.avancar(60)
        self.assertEqual(self.pedir("GET", "/api/ping").status, 200)
        self.assertFalse(self.app.desbloqueado)                   # bloqueou no começo do ping
        self.assertFalse(self.app.cofre.aberto)
        self.assertEqual(self.pedir("GET", "/api/config", token=token).status, 423)

    def test_verificar_inatividade_para_quem_chama_de_tempos_em_tempos(self):
        self.pedir("POST", "/api/configurar", {"pin": PIN})
        self.assertFalse(self.app.verificar_inatividade())
        self.relogio.avancar(10 * 60)
        self.assertTrue(self.app.verificar_inatividade())
        self.assertFalse(self.app.desbloqueado)
        self.assertFalse(self.app.verificar_inatividade())


# ---------------------------------------------------------------- a ponte

PAREDE_INICIO = 1_790_000_000.0          # um relógio de parede de mentira (segundos desde 1970)
PIN_IA = "outra frase so da ia"


class BasePonte(unittest.TestCase):
    """O núcleo do navegador pela ponte, com dois relógios de mentira: `relogio` (o monotônico,
    o `performance.now()` do Worker) e `parede` (o `Date.now()`/time.time)."""

    atraso_pin: float | None = None          # None = o padrão do núcleo (1 s)

    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-ponte-")
        self.relogio = Relogio()
        self.parede = Relogio(PAREDE_INICIO)
        self.addCleanup(self.desligar)
        self.app = self.iniciar()

    def iniciar(self, pasta: str | None = None, **opcoes) -> nucleo.Nucleo:
        parametros = {"hoje": lambda: REF, "relogio": self.relogio, "relogio_parede": self.parede}
        if self.atraso_pin is not None:
            parametros["atraso_pin"] = self.atraso_pin
        parametros.update(opcoes)
        return ponte.iniciar_no_navegador(pasta or self.pasta, **parametros)

    def desligar(self) -> None:
        if ponte._nucleo is not None:
            ponte._nucleo.bloquear()
        ponte._nucleo = None
        if ponte._log_manipulador is not None:
            logging.getLogger("entrenos").removeHandler(ponte._log_manipulador)
            ponte._log_manipulador.close()
            ponte._log_manipulador = None
        shutil.rmtree(self.pasta, ignore_errors=True)

    @staticmethod
    def pedir(metodo, caminho, corpo=None, token=None, consulta=None, corpo_texto=None) -> dict:
        texto = corpo_texto if corpo_texto is not None else (None if corpo is None else json.dumps(corpo))
        saida = ponte.tratar_json(metodo, caminho, texto, token, None if consulta is None else json.dumps(consulta))
        assert isinstance(saida, str)
        return json.loads(saida)

    def configurar(self) -> str:
        r = self.pedir("POST", "/api/configurar", {"pin": PIN})
        self.assertEqual(r["status"], 200, r)
        return r["dados"]["token"]

    def registrar(self, token: str, texto_registro: str = TEXTO) -> int:
        r = self.pedir("POST", "/api/registros", {"texto": texto_registro, "data_registro": "2026-09-30"}, token)
        self.assertEqual(r["status"], 200, r)
        return r["dados"]["id"]

    def desbloquear(self, pin: str = PIN) -> dict:
        return self.pedir("POST", "/api/desbloquear", {"pin": pin})

    def baixar(self, token: str) -> bytes:
        r = self.pedir("POST", "/api/backup/baixar", {}, token)
        self.assertEqual(r["status"], 200, r)
        return base64.b64decode(r["dados"]["arquivo_b64"])


class TestPonte(BasePonte):

    def test_diario_pela_ponte(self):
        r = self.pedir("GET", "/api/estado")
        self.assertEqual((r["status"], r["dados"]["configurado"], r["gravou"]), (200, False, False))
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Ana"})
        self.assertEqual((r["status"], r["gravou"]), (200, True))                 # o Worker sincroniza o IDBFS
        token = r["dados"]["token"]
        proposta = self.pedir("POST", "/api/analisar", {"texto": TEXTO, "data_registro": "2026-09-30"}, token)
        self.assertEqual((proposta["status"], proposta["gravou"]), (200, False))
        p = proposta["dados"]
        r = self.pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30", "itens": p["itens"],
                                                  "pessoas": p["pessoas"], "relacoes": p["relacoes"]}, token)
        self.assertEqual((r["status"], r["gravou"]), (200, True))
        rid = r["dados"]["id"]
        # A consulta pode vir no caminho (como no fetch) ou em JSON; o método em minúsculas também vale.
        busca = self.pedir("get", "/api/busca?q=tulipas", token=token)
        self.assertIn(rid, [e["registro_id"] for e in busca["dados"]["encontrados"]])
        busca = self.pedir("GET", "/api/busca?q=nada", token=token, consulta={"q": "tulipas"})
        self.assertIn(rid, [e["registro_id"] for e in busca["dados"]["encontrados"]])
        self.assertEqual(self.pedir("GET", "/api/datas", token=token, consulta={"dias": "30"})["status"], 200)
        for consulta in ("[1]", '{"dias": 30}', "nao e json"):
            with self.subTest(consulta=consulta):
                saida = json.loads(ponte.tratar_json("GET", "/api/datas", None, token, consulta))
                self.assertEqual(saida["status"], 400)
        muitos = "&".join(f"p{i}=1" for i in range(60))
        self.assertEqual(self.pedir("GET", f"/api/registros?{muitos}", token=token)["status"], 400)
        # Corpo com metade de um par de surrogates (o JavaScript deixa): 400, como no computador.
        r = self.pedir("POST", "/api/analisar", token=token, corpo_texto='{"texto": "\ud800"}')
        self.assertEqual(r["status"], 400)
        r = self.pedir("GET", "/api/configurar")
        self.assertEqual((r["status"], r["cabecalhos"]), (405, {"Allow": "POST"}))
        sair = self.pedir("POST", "/api/sair", {}, token)
        self.assertEqual((sair["status"], sair["encerrado"]), (200, True))

    def test_pin_errado_429_com_retry_after(self):
        token = self.pedir("POST", "/api/configurar", {"pin": PIN})["dados"]["token"]
        self.pedir("POST", "/api/bloquear", {}, token)
        r = self.pedir("POST", "/api/desbloquear", {"pin": "pin errado demais"})
        self.assertEqual((r["status"], r["dados"]["espere_segundos"], r["cabecalhos"]), (401, 1, {}))
        r = self.pedir("POST", "/api/desbloquear", {"pin": PIN})
        self.assertEqual((r["status"], r["dados"]["espere_segundos"], r["cabecalhos"]), (429, 1, {"Retry-After": "1"}))
        self.assertFalse(r["gravou"])
        self.relogio.avancar(1)
        self.assertEqual(self.pedir("POST", "/api/desbloquear", {"pin": PIN})["status"], 200)

    def test_o_que_nao_e_texto_vale_como_nao_veio(self):
        # O `null`/`undefined` do JavaScript pode chegar como outro objeto, conforme o Pyodide.
        token = self.pedir("POST", "/api/configurar", {"pin": PIN})["dados"]["token"]
        estranho = object()
        self.assertEqual(json.loads(ponte.tratar_json("GET", "/api/registros", None, estranho, estranho))["status"], 401)
        r = json.loads(ponte.tratar_json("POST", "/api/analisar", estranho, token, None))
        self.assertEqual((r["status"], r["dados"]["erro"]), (400, "Escreva alguma coisa antes de organizar."))

    def test_verificar_inatividade(self):
        self.pedir("POST", "/api/configurar", {"pin": PIN})
        self.assertEqual(json.loads(ponte.verificar_inatividade()), {"bloqueou": False, "gravou": False})
        self.relogio.avancar(10 * 60)
        # Tudo já estava salvo: bloquear não regrava o arquivo (nem o IDBFS precisa sincronizar).
        self.assertEqual(json.loads(ponte.verificar_inatividade()), {"bloqueou": True, "gravou": False})
        self.assertFalse(self.app.desbloqueado)

    def test_sem_iniciar_e_iniciar_de_novo(self):
        token = self.pedir("POST", "/api/configurar", {"pin": PIN})["dados"]["token"]
        self.pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30"}, token)
        novo = self.iniciar()
        self.assertIsNot(novo, self.app)
        self.assertFalse(self.app.desbloqueado)                   # o anterior bloqueou (e salvou)
        self.assertEqual(self.pedir("GET", "/api/registros", token=token)["status"], 423)
        token = self.pedir("POST", "/api/desbloquear", {"pin": PIN})["dados"]["token"]
        self.assertEqual(len(self.pedir("GET", "/api/registros", token=token)["dados"]), 1)
        manipuladores = [h for h in logging.getLogger("entrenos").handlers if h is ponte._log_manipulador]
        self.assertEqual(len(manipuladores), 1)                   # um erros.log só
        with mock.patch.object(ponte, "_nucleo", None):
            r = self.pedir("GET", "/api/ping")
            self.assertEqual((r["status"], r["dados"]["erro"]), (503, ponte.NAO_INICIADO))
            self.assertEqual(json.loads(ponte.verificar_inatividade()), {"bloqueou": False, "gravou": False})


# ---------------------------------------------------------------- um Python "de navegador"

ROTEIRO = r'''
import importlib.abc, json, sys, threading, time, _thread
from datetime import date

# O que o Pyodide 314 não tem (ou tem só como casca): nada disso pode fazer falta ao núcleo.
BLOQUEADOS = ("ctypes", "webbrowser", "http.server", "socketserver", "entrenos.servidor", "rapidfuzz",
              "_hashlib", "ssl", "_ssl", "zoneinfo", "tzdata", "subprocess", "multiprocessing")


class SemIsso(importlib.abc.MetaPathFinder):
    def find_spec(self, nome, caminho=None, alvo=None):
        if any(nome == b or nome.startswith(b + ".") for b in BLOQUEADOS):
            raise ImportError(f"{nome} não existe no navegador")
        return None


def proibido(*_a, **_k):
    raise RuntimeError("no navegador não dá para iniciar thread nem dormir")


sys.meta_path.insert(0, SemIsso())
threading.Thread.start = proibido
time.sleep = proibido
# No 3.13+ o threading inicia threads pelo _thread.start_joinable_thread (e guarda um apelido).
for nome in ("start_new_thread", "start_joinable_thread", "start_new"):
    if hasattr(_thread, nome):
        setattr(_thread, nome, proibido)
for nome in ("_start_joinable_thread", "_start_new_thread"):
    if hasattr(threading, nome):
        setattr(threading, nome, proibido)
sys.path.insert(0, sys.argv[1])

from entrenos import cofre, ponte, texto

cofre.SCRYPT_N = 2 ** 12
PIN, TEXTO = sys.argv[3], sys.argv[4]
relogio = [1000.0]
ponte.iniciar_no_navegador(sys.argv[2], relogio=lambda: relogio[0], hoje=lambda: date(2026, 9, 30))


def pedir(metodo, caminho, corpo=None, token=None):
    return json.loads(ponte.tratar_json(metodo, caminho, None if corpo is None else json.dumps(corpo), token))


passos = {}
passos["autoteste"] = json.loads(ponte.autoteste())
passos["semelhanca"] = texto.semelhanca("Ana", "Ana Paula")
passos["ping"] = pedir("GET", "/api/ping")["status"]
r = pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Ana"})
token = r["dados"]["token"]
passos["configurar"] = [r["status"], r["gravou"]]
p = pedir("POST", "/api/analisar", {"texto": TEXTO, "data_registro": "2026-09-30"}, token)["dados"]
r = pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30", "itens": p["itens"],
                                     "pessoas": p["pessoas"], "relacoes": p["relacoes"]}, token)
passos["registrar"] = [r["status"], r["gravou"], len(p["itens"])]
passos["busca"] = len(pedir("GET", "/api/busca?q=tulipas", token=token)["dados"]["encontrados"])
passos["telas"] = [pedir("GET", rota, token=token)["status"] for rota in
                   ("/api/hoje", "/api/perfil", "/api/datas?dias=30", "/api/pessoas", "/api/historico")]
passos["backup_pasta"] = pedir("POST", "/api/backup", {"pasta": sys.argv[2]}, token)["status"]
r = pedir("POST", "/api/backup/baixar", {}, token)
passos["baixar"] = [r["status"], r["dados"].get("tamanho", 0) > 0]
passos["ia"] = pedir("POST", "/api/ia/sugestao", {}, token)["status"]
passos["bloquear"] = pedir("POST", "/api/bloquear", {}, token)["status"]
r = pedir("POST", "/api/desbloquear", {"pin": "pin errado demais"})
passos["errado"] = [r["status"], r["dados"].get("espere_segundos")]
r = pedir("POST", "/api/desbloquear", {"pin": PIN})
passos["cedo"] = [r["status"], r["cabecalhos"].get("Retry-After")]
# Recarregar a página: outro núcleo, e a espera continua (pin_espera.json).
ponte.iniciar_no_navegador(sys.argv[2], relogio=lambda: relogio[0], hoje=lambda: date(2026, 9, 30))
r = pedir("POST", "/api/desbloquear", {"pin": PIN})
passos["reaberto"] = [r["status"], r["cabecalhos"].get("Retry-After")]
relogio[0] += 1.01
r = pedir("POST", "/api/desbloquear", {"pin": PIN})
token = r["dados"]["token"]
passos["certo"] = r["status"]
passos["registros"] = len(pedir("GET", "/api/registros", token=token)["dados"])
# Sincronização (seção 22): a senha, o arquivo deste "celular" e outro aparelho que o junta.
from entrenos import sincronizacao
from entrenos.nucleo import Nucleo
import os as _os
sincronizacao.SCRYPT_N = 2 ** 12
r = pedir("POST", "/api/sync/senha", {"senha": "biscoito de polvilho"}, token)
passos["sync_senha"] = [r["status"], bool(r["dados"].get("sync_json"))]
config_sync = r["dados"]["sync_json"]
r = pedir("POST", "/api/sync/preparar", {}, token)
passos["sync_preparar"] = [r["status"], r["dados"].get("tamanho", 0) > 0, r["gravou"]]
meu = r["dados"]
outro = Nucleo(_os.path.join(sys.argv[2], "outro"), atraso_pin=0, plataforma="navegador",
               relogio=lambda: relogio[0], hoje=lambda: date(2026, 9, 30))
def pedir_outro(metodo, caminho, corpo=None, tk=None):
    rr = outro.tratar(metodo, caminho, corpo=None if corpo is None else json.dumps(corpo).encode(), token=tk)
    return rr.status, rr.dados
tk2 = pedir_outro("POST", "/api/configurar", {"pin": PIN})[1]["token"]
pedir_outro("POST", "/api/sync/senha", {"senha": "biscoito de polvilho", "sync_json": config_sync}, tk2)
_st, _d = pedir_outro("POST", "/api/sync/receber", {"nome": meu["nome"], "conteudo_b64": meu["conteudo_b64"]}, tk2)
passos["sync_receber"] = [_st, _d.get("registros_novos")]
outro.encerrar()
relogio[0] += 11 * 60
passos["inatividade"] = json.loads(ponte.verificar_inatividade())
passos["depois"] = pedir("GET", "/api/registros", token=token)["status"]
token = pedir("POST", "/api/desbloquear", {"pin": PIN})["dados"]["token"]
r = pedir("POST", "/api/sair", {}, token)
passos["sair"] = [r["status"], r.get("encerrado")]
passos["threads"] = threading.active_count()
passos["espera_do_cofre"] = cofre.esperar_entre_tentativas is None
from entrenos import recursos
passos["recursos"] = [recursos.nomes_carregados(), recursos.falhas(), p.get("extras")]
passos["modulos"] = sorted(m for m in BLOQUEADOS if m in sys.modules)
print(json.dumps(passos))
'''


class TestPythonDeNavegador(unittest.TestCase):
    """Um diário inteiro pela ponte, num Python separado em que iniciar thread e dormir dão erro
    e o servidor, o http.server, o ctypes e o webbrowser não existem (como no Pyodide)."""

    def test_diario_inteiro_sem_thread_sleep_nem_servidor(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-navegador-")
        self.addCleanup(shutil.rmtree, pasta, True)
        ambiente = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "ENTRENOS_DADOS": pasta, "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-c", ROTEIRO, str(RAIZ), pasta, PIN, TEXTO], capture_output=True,
                           timeout=120, cwd=str(RAIZ), env=ambiente)
        saida, erros = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, erros[-3000:])
        passos = json.loads(saida.strip().splitlines()[-1])
        self.assertEqual(passos["autoteste"]["falhas"], [])
        self.assertTrue(passos["autoteste"]["ok"])
        self.assertEqual(passos["semelhanca"], 100.0)              # sem rapidfuzz, a mesma medida
        self.assertEqual(passos["ping"], 200)
        self.assertEqual(passos["configurar"], [200, True])
        self.assertEqual(passos["registrar"][:2], [200, True])
        self.assertGreater(passos["registrar"][2], 0)
        self.assertGreater(passos["busca"], 0)
        self.assertEqual(passos["telas"], [200, 200, 200, 200, 200])
        self.assertEqual(passos["backup_pasta"], 409)              # no navegador: "Baixar uma cópia"
        self.assertEqual(passos["baixar"], [200, True])
        self.assertEqual(passos["ia"], 503)                        # a IA ainda não alcança a rede ali
        self.assertEqual(passos["bloquear"], 200)
        self.assertEqual(passos["errado"], [401, 1])
        self.assertEqual(passos["cedo"], [429, "1"])
        self.assertEqual(passos["reaberto"], [429, "1"])
        self.assertEqual(passos["certo"], 200)
        # Seção 22: a sincronização também roda sem thread, sem _hashlib e sem ssl.
        self.assertEqual(passos["sync_senha"], [200, True])
        self.assertEqual(passos["sync_preparar"], [200, True, True])
        self.assertEqual(passos["sync_receber"], [200, 1])
        self.assertEqual(passos["registros"], 1)                   # o diário voltou do arquivo cifrado
        self.assertEqual(passos["inatividade"], {"bloqueou": True, "gravou": False})
        self.assertEqual(passos["depois"], 423)
        self.assertEqual(passos["sair"], [200, True])
        self.assertEqual(passos["threads"], 1)                     # só a principal, do começo ao fim
        self.assertTrue(passos["espera_do_cofre"])
        # Seção 20.0: os nove recursos abrem também ali, e a proposta já traz `extras`.
        from entrenos import recursos
        self.assertEqual(passos["recursos"], [list(recursos.NOMES), {}, {}])
        self.assertEqual(passos["modulos"], [])
        self.assertTrue((Path(pasta) / "entrenos.cofre").read_bytes().startswith(cofre.MAGICO))


# ================================================================ revisão de 07/10/2026 (seção 19.7)

# ---------------------------------------------------------------- o bloqueio do PIN guardado

class TestEsperaDoPinGuardada(BasePonte):
    """Achado (paridade-seguranca e navegador, média): o bloqueio do PIN errado só existia na
    memória do núcleo. No celular, recarregar a página ou o iOS fechar o Worker criava outro
    núcleo, e o próximo PIN era testado na hora: o teto de 30 s caía para o tempo de abrir o app."""

    atraso_pin = 1.0

    def setUp(self):
        super().setUp()
        self.token = self.configurar()
        self.pedir("POST", "/api/bloquear", {}, self.token)

    def andar(self, segundos: float) -> None:
        self.relogio.avancar(segundos)
        self.parede.avancar(segundos)

    def arquivo(self) -> Path:
        return Path(self.pasta) / nucleo.ARQUIVO_ESPERA_PIN

    def test_reabrir_nao_zera_a_espera(self):
        esperas = []
        for vez in range(7):
            r = self.desbloquear("pin errado demais")
            self.assertEqual(r["status"], 401, r)
            self.assertTrue(r["gravou"])                     # o Worker sincroniza antes de responder
            esperas.append(r["dados"]["espere_segundos"])
            if vez < 6:
                self.andar(esperas[-1])
        self.assertEqual(esperas, [1, 2, 4, 8, 16, 30, 30])
        r = self.desbloquear()
        self.assertEqual((r["status"], r["cabecalhos"]), (429, {"Retry-After": "30"}))
        # Vinte reaberturas no mesmo instante: nenhum PIN testado (antes eram vinte).
        for _ in range(20):
            app = self.iniciar()
            with mock.patch.object(app.cofre, "abrir", wraps=app.cofre.abrir) as abrir:
                r = self.desbloquear("pin errado demais")
            self.assertEqual((r["status"], r["cabecalhos"]), (429, {"Retry-After": "30"}))
            self.assertEqual(abrir.call_count, 0)
        self.andar(30)
        r = self.desbloquear("pin errado demais")
        self.assertEqual((r["status"], r["dados"]["espere_segundos"]), (401, 30))       # o contador seguiu
        self.assertEqual(ponte._nucleo._falhas_pin, 8)
        self.andar(30)
        self.assertEqual(self.desbloquear()["status"], 200)
        self.assertFalse(self.arquivo().exists())           # acertou: o arquivo sai

    def test_adiantar_a_hora_nao_pula_a_espera(self):
        for segundos in (1, 2):
            self.assertEqual(self.desbloquear("pin errado demais")["status"], 401)
            self.andar(segundos)
        r = self.desbloquear("pin errado demais")
        self.assertEqual((r["status"], r["dados"]["espere_segundos"]), (401, 4))
        self.parede.avancar(3600)                           # a hora do aparelho, adiantada
        self.assertEqual(self.desbloquear()["status"], 429)   # no núcleo aberto vale o monotônico
        self.iniciar()                                      # e reabrir aplica de novo o degrau inteiro
        r = self.desbloquear()
        self.assertEqual((r["status"], r["cabecalhos"]), (429, {"Retry-After": "4"}))
        self.relogio.avancar(4)
        self.assertEqual(self.desbloquear()["status"], 200)

    def test_atrasar_a_hora_da_o_teto(self):
        self.assertEqual(self.desbloquear("pin errado demais")["status"], 401)
        self.parede.avancar(-3600)
        self.iniciar()
        r = self.desbloquear()
        self.assertEqual((r["status"], r["cabecalhos"]), (429, {"Retry-After": "30"}))

    def test_o_arquivo_nao_guarda_segredo(self):
        self.desbloquear("pin errado demais")
        bruto = self.arquivo().read_text("utf-8")
        dados = json.loads(bruto)
        self.assertEqual(set(dados), {"principal", "visto"})
        self.assertEqual(set(dados["principal"]), {"falhas", "livre_em"})
        self.assertEqual(dados["principal"]["falhas"], 1)
        self.assertNotIn("errado", bruto)
        self.assertNotIn(PIN, bruto)

    def test_arquivo_estranho_e_ignorado(self):
        for conteudo in ("isto não é json", '{"principal": {"falhas": NaN, "livre_em": Infinity}}', "[1, 2]",
                         '{"principal": {"falhas": true, "livre_em": "amanhã"}, "visto": null}'):
            with self.subTest(conteudo=conteudo):
                self.arquivo().write_text(conteudo, encoding="utf-8")
                self.iniciar()
                r = self.desbloquear()
                self.assertEqual(r["status"], 200, r)
                self.pedir("POST", "/api/bloquear", {}, r["dados"]["token"])

    def test_pin_da_ia_tambem_continua_e_pasta_apagada_nao_volta(self):
        token = self.desbloquear()["dados"]["token"]
        self.assertEqual(self.pedir("POST", "/api/ia/pin", {"novo": PIN_IA}, token)["status"], 200)
        self.pedir("POST", "/api/ia/sair", {}, token)
        r = self.pedir("POST", "/api/ia/entrar", {"pin_ia": "nao e o da ia"}, token)
        self.assertEqual((r["status"], r["dados"]["espere_segundos"]), (401, 1))
        self.assertEqual(set(json.loads(self.arquivo().read_text("utf-8"))), {"ia", "visto"})
        # A pasta de dados apagada com o app aberto não volta por causa do bloqueio (seção 18.6).
        self.andar(1)
        shutil.rmtree(self.pasta)
        r = self.pedir("POST", "/api/ia/entrar", {"pin_ia": "nao e o da ia de novo"}, token)
        self.assertEqual(r["status"], 401)
        self.assertFalse(os.path.exists(self.pasta))


class TestEsperaDoPinNoComputador(unittest.TestCase):
    """O mesmo no computador: fechar o programa (Gerenciador de Tarefas) e abrir não zera a espera."""

    def test_reabrir_o_servidor_mantem_a_espera(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-espera-")
        self.addCleanup(shutil.rmtree, pasta, True)
        s = ServidorDeTeste(pasta, atraso_pin=5, relogio=Relogio())
        s.configurar()
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        s.fechar(apagar=False)
        outro = ServidorDeTeste(pasta, atraso_pin=5, relogio=Relogio())
        self.addCleanup(outro.fechar, False)
        r = outro.desbloquear()
        self.assertEqual((r.status, r.cabecalhos["Retry-After"]), (429, "5"))


# ---------------------------------------------------------------- os dois relógios

class TestRelogiosDoNavegador(BasePonte):
    """Achado (navegador, alta): no Pyodide o `time.monotonic` é o `performance.now()`, que não anda
    enquanto o celular dorme (só no Windows ele conta). Com a tela travada por horas, o diário
    continuava desbloqueado ao acordar. Aqui o `relogio` fica parado e só a `parede` anda."""

    def test_celular_dormindo_bloqueia_pelo_relogio_de_parede(self):
        token = self.configurar()
        self.parede.avancar(2 * 3600)
        r = self.pedir("GET", "/api/registros", token=token)
        self.assertEqual(r["status"], 423)
        self.assertFalse(self.app.desbloqueado)

    def test_verificar_inatividade_pelo_relogio_de_parede(self):
        self.configurar()
        self.parede.avancar(9 * 60)
        self.assertEqual(json.loads(ponte.verificar_inatividade())["bloqueou"], False)
        self.parede.avancar(60)
        self.assertEqual(json.loads(ponte.verificar_inatividade())["bloqueou"], True)

    def test_hora_atrasada_nao_impede_o_bloqueio(self):
        token = self.configurar()
        self.parede.avancar(-86400)
        self.relogio.avancar(10 * 60)
        self.assertEqual(self.pedir("GET", "/api/registros", token=token)["status"], 423)

    def test_uso_renova_nos_dois_relogios(self):
        token = self.configurar()
        self.parede.avancar(9 * 60)
        self.assertEqual(self.pedir("GET", "/api/config", token=token)["status"], 200)
        self.parede.avancar(9 * 60)
        self.assertEqual(self.pedir("GET", "/api/config", token=token)["status"], 200)

    def test_area_da_ia_fecha_pelo_relogio_de_parede(self):
        self.app.minutos_bloqueio = 60
        token = self.configurar()
        self.assertEqual(self.pedir("POST", "/api/ia/pin", {"novo": PIN_IA}, token)["status"], 200)
        self.parede.avancar(9 * 60)
        estado = self.pedir("GET", "/api/ia/estado", token=token)["dados"]
        self.assertTrue(estado["liberada"])
        self.assertEqual(estado["segundos_restantes"], 60)
        self.parede.avancar(60)
        self.assertFalse(self.pedir("GET", "/api/ia/estado", token=token)["dados"]["liberada"])

    def test_bloqueio_do_pin_nao_encurta_com_a_hora_adiantada(self):
        token = self.configurar()
        self.pedir("POST", "/api/bloquear", {}, token)
        self.assertEqual(self.desbloquear("pin errado demais")["status"], 401)
        self.parede.avancar(3600)
        self.assertEqual(self.desbloquear()["status"], 429)

    def test_o_padrao_e_o_relogio_do_sistema(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-relogio-")
        self.addCleanup(shutil.rmtree, pasta, True)
        app = nucleo.Nucleo(pasta)
        self.assertIs(app.relogio_parede, time.time)


# ---------------------------------------------------------------- plataforma "navegador"

class TestPlataformaNavegador(BasePonte):
    def test_estado_diz_a_plataforma(self):
        dados = self.pedir("GET", "/api/estado")["dados"]
        self.assertEqual((dados["plataforma"], dados["backup_em_pasta"], dados["limite_arquivo"]),
                         ("navegador", False, nucleo.LIMITE_CORPO_ARQUIVO_NAVEGADOR))
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-plataforma-")
        self.addCleanup(shutil.rmtree, pasta, True)
        computador = nucleo.Nucleo(pasta)
        dados = computador.tratar("GET", "/api/estado").dados
        self.assertEqual((dados["plataforma"], dados["backup_em_pasta"], dados["limite_arquivo"]),
                         ("computador", True, nucleo.LIMITE_CORPO_ARQUIVO))
        with self.assertRaises(ValueError):
            nucleo.Nucleo(pasta, plataforma="celular")

    def test_backup_numa_pasta_e_recusado_e_a_copia_baixada_restaura(self):
        # Achado (navegador, média): no Pyodide, "/tmp" é memória que some ao recarregar, e a
        # resposta dizia 200 com o caminho do arquivo: a pessoa achava que tinha backup.
        token = self.configurar()
        self.registrar(token)
        destino = tempfile.mkdtemp(prefix="entrenos-teste-destino-")
        self.addCleanup(shutil.rmtree, destino, True)
        r = self.pedir("POST", "/api/backup", {"pasta": destino}, token)
        self.assertEqual((r["status"], r["dados"]["erro"], r["dados"]["baixar"]), (409, nucleo.BACKUP_SO_BAIXANDO, True))
        self.assertEqual(os.listdir(destino), [])
        r = self.pedir("POST", "/api/backup/baixar", {}, token)
        self.assertEqual(r["status"], 200, r)
        self.assertFalse(r["gravou"])                        # já estava salvo: nada regravado
        copia = base64.b64decode(r["dados"]["arquivo_b64"])
        self.assertEqual(r["dados"]["nome"], "entrenos-backup-2026-09-30.cofre")
        self.assertEqual(r["dados"]["tamanho"], len(copia))
        self.assertEqual(copia, (Path(self.pasta) / config.ARQUIVO_COFRE).read_bytes())
        # Num celular novo (outra pasta), a cópia baixada vira o diário.
        outra = tempfile.mkdtemp(prefix="entrenos-teste-outro-celular-")
        self.addCleanup(shutil.rmtree, outra, True)
        self.iniciar(outra)
        r = json.loads(ponte.restaurar_arquivo(copia, json.dumps({"pin": PIN})))
        self.assertEqual(r["status"], 200, r)
        self.assertTrue(r["gravou"])
        registros = self.pedir("GET", "/api/registros", token=r["dados"]["token"])["dados"]
        self.assertEqual([x["texto"] for x in registros], [TEXTO])

    def test_baixar_copia_no_computador_tambem(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-baixar-")
        self.addCleanup(shutil.rmtree, pasta, True)
        app = nucleo.Nucleo(pasta, hoje=lambda: REF)
        self.addCleanup(app.bloquear)
        token = app.tratar("POST", "/api/configurar", corpo=json.dumps({"pin": PIN}).encode()).dados["token"]
        r = app.tratar("POST", "/api/backup/baixar", corpo=b"{}", token=token)
        self.assertEqual(r.status, 200)
        self.assertEqual(base64.b64decode(r.dados["arquivo_b64"]), (Path(pasta) / config.ARQUIVO_COFRE).read_bytes())
        self.assertEqual(app.tratar("POST", "/api/backup/baixar", corpo=b"{}").status, 401)

    def test_ia_no_navegador_responde_503_com_o_motivo(self):
        # Achado (navegador, média): um diário do PC com IA chegava ao celular com `utilizavel:
        # true`, os botões ✨ apareciam e cada uso falhava com "tempo esgotado".
        token = self.configurar()
        self.assertEqual(self.pedir("POST", "/api/ia/pin", {"novo": PIN_IA}, token)["status"], 200)
        r = self.pedir("PUT", "/api/ia/config/principal",
                       {"provedor": "outro", "url": "http://127.0.0.1:9/v1", "modelo": "modelo"}, token)
        self.assertEqual(r["status"], 200, r)
        ia = self.pedir("GET", "/api/estado")["dados"]["ia"]
        self.assertEqual((ia["configurada"], ia["utilizavel"], ia["espera_maxima"], ia["motivo"]),
                         (True, False, None, nucleo.IA_SO_NO_COMPUTADOR))
        estado = self.pedir("GET", "/api/ia/estado", token=token)["dados"]
        self.assertEqual((estado["utilizavel"], estado["motivo"]), (False, nucleo.IA_SO_NO_COMPUTADOR))
        for rota, corpo in (("analisar", {"texto": TEXTO}), ("busca", {"q": "tulipas"}), ("sugestao", {}),
                            ("testar", {"qual": "principal"}),
                            ("modelos", {"provedor": "outro", "url": "http://127.0.0.1:9/v1"})):
            with self.subTest(rota=rota):
                r = self.pedir("POST", f"/api/ia/{rota}", corpo, token)
                self.assertEqual((r["status"], r["dados"]["erro"], r["dados"]["ia_indisponivel"]),
                                 (503, nucleo.IA_SO_NO_COMPUTADOR, True))
        # Os ajustes continuam guardados (o mesmo diário volta ao computador com a IA).
        self.assertEqual(self.pedir("GET", "/api/ia/config", token=token)["dados"]["principal"]["modelo"], "modelo")

    def test_limite_dos_arquivos_e_menor(self):
        # Achado (navegador, média): o pico de memória chega a ~15x o corpo, e o wasm não devolve.
        token = self.configurar()
        limite = nucleo.LIMITE_CORPO_ARQUIVO_NAVEGADOR
        corpo = json.dumps({"json": "x" * limite})
        r = self.pedir("POST", "/api/importar/previa", token=token, corpo_texto=corpo)
        self.assertEqual((r["status"], r["dados"]["erro"]), (413, nucleo.mensagem_de_limite(limite)))
        self.assertIn("16 MB", r["dados"]["erro"])
        del corpo
        r = json.loads(ponte.restaurar_arquivo(b"\0" * (limite + 1), json.dumps({"pin": PIN, "pin_atual": PIN}), token))
        self.assertEqual(r["status"], 413)
        self.assertEqual(self.app.limite_do_corpo(nucleo.achar_rota("POST", "/api/analisar")[0]), nucleo.LIMITE_CORPO)

    def test_textos_sem_windows_nem_pasta(self):
        token = self.configurar()
        self.registrar(token)                                # a 2ª gravação deixa uma cópia automática
        self.pedir("POST", "/api/bloquear", {}, token)
        os.remove(Path(self.pasta) / config.ARQUIVO_COFRE)
        r = self.desbloquear()
        self.assertEqual((r["status"], r["dados"]["sem_diario"]), (409, True))
        for trecho in ("antivírus", "quarentena", self.pasta, "backups"):
            self.assertNotIn(trecho, r["dados"]["erro"])
        self.assertIn("navegador", r["dados"]["erro"])
        copias = self.pedir("GET", "/api/estado")["dados"]["copias_anteriores"]
        self.assertEqual((copias["quantas"], copias["pasta"]), (1, None))

    def test_restaurar_varias_vezes_guarda_so_as_copias_mais_novas(self):
        self.assertEqual(self.app.cofre.copias_extras_maximas, nucleo.COPIAS_EXTRAS_NAVEGADOR)
        self.assertEqual(self.app.cofre.backups_automaticos, nucleo.BACKUPS_AUTOMATICOS_NAVEGADOR)
        token = self.configurar()
        copia = self.baixar(token)
        segundos = iter(range(10, 60))
        self.app.cofre.agora = lambda: datetime(2026, 9, 30, 12, 0, next(segundos))
        nomes = []
        for _ in range(4):
            r = json.loads(ponte.restaurar_arquivo(copia, json.dumps({"pin": PIN, "pin_atual": PIN}), token))
            self.assertEqual(r["status"], 200, r)
            token = r["dados"]["token"]
            nomes.append(r["dados"]["copia"])
        antes = sorted(p.name for p in (Path(self.pasta) / config.PASTA_BACKUPS).iterdir()
                       if p.name.startswith(cofre.PREFIXO_ANTES_DA_RESTAURACAO))
        self.assertEqual(antes, sorted(nomes[-nucleo.COPIAS_EXTRAS_NAVEGADOR:]))      # as mais novas

    def test_restaurar_com_o_app_aberto_devolve_so_o_nome_da_copia(self):
        token = self.configurar()
        copia = self.baixar(token)
        r = json.loads(ponte.restaurar_arquivo(copia, json.dumps({"pin": PIN, "pin_atual": PIN}), token))
        self.assertEqual(r["status"], 200, r)
        nome = r["dados"]["copia"]
        self.assertNotIn("/", nome)
        self.assertNotIn("\\", nome)
        self.assertTrue((Path(self.pasta) / config.PASTA_BACKUPS / nome).is_file())


class TestCopiasNoNavegador(unittest.TestCase):
    """Achado (navegador, média): o IDBFS carrega a pasta de dados inteira na memória ao abrir, e as
    cópias fora do rodízio (de antes da restauração, anteriores, danificadas) só cresciam, sem um
    gerenciador de arquivos para apagá-las."""

    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="entrenos-teste-copias-"))
        self.addCleanup(shutil.rmtree, self.pasta, True)

    def test_menos_copias_automaticas(self):
        c = cofre.Cofre(self.pasta)
        c.backups_automaticos = nucleo.BACKUPS_AUTOMATICOS_NAVEGADOR
        dia = [date(2026, 9, 1)]
        c.hoje = lambda: dia[0]
        c.criar(PIN, b"\0" * 512)
        for d in range(2, 8):
            dia[0] = date(2026, 9, d)
            c.salvar(b"\1" * 512)
        self.assertEqual([p.name for p in c.backups()], ["entrenos-2026-09-07.cofre", "entrenos-2026-09-06.cofre"])

    def criar_copias(self) -> Path:
        backups = self.pasta / config.PASTA_BACKUPS
        backups.mkdir()
        nomes = [f"{cofre.PREFIXO_ANTES_DA_RESTAURACAO}2026-09-0{d}-120000.cofre" for d in (1, 2, 3, 4)]
        nomes += [f"{cofre.PREFIXO_DIARIO_ANTERIOR}2026-08-0{d}.cofre" for d in (1, 2, 3)]
        nomes += [f"{cofre.PREFIXO_DANIFICADO}2026-07-01-080000.cofre", "nota.txt", "entrenos-2026-09-01.cofre"]
        for nome in nomes:
            (backups / nome).write_bytes(b"x")
        return backups

    def test_no_navegador_ficam_as_mais_novas_de_cada_tipo(self):
        backups = self.criar_copias()
        c = cofre.Cofre(self.pasta)
        c.copias_extras_maximas = nucleo.COPIAS_EXTRAS_NAVEGADOR
        c._podar_copias_extras()
        self.assertEqual(sorted(p.name for p in backups.iterdir()), sorted([
            f"{cofre.PREFIXO_ANTES_DA_RESTAURACAO}2026-09-04-120000.cofre",
            f"{cofre.PREFIXO_ANTES_DA_RESTAURACAO}2026-09-03-120000.cofre",
            f"{cofre.PREFIXO_DIARIO_ANTERIOR}2026-08-03.cofre", f"{cofre.PREFIXO_DIARIO_ANTERIOR}2026-08-02.cofre",
            f"{cofre.PREFIXO_DANIFICADO}2026-07-01-080000.cofre", "nota.txt", "entrenos-2026-09-01.cofre"]))

    def test_no_computador_nada_sai(self):
        backups = self.criar_copias()
        antes = sorted(p.name for p in backups.iterdir())
        cofre.Cofre(self.pasta)._podar_copias_extras()
        self.assertEqual(sorted(p.name for p in backups.iterdir()), antes)



# ---------------------------------------------------------------- duas abas no mesmo diário

class TestDuasAbas(unittest.TestCase):
    """Achado (navegador, média): duas abas (ou a aba e o app instalado) são dois núcleos no mesmo
    armazenamento, e o cofre gravava por cima sem conferir: o que a outra gravou sumia."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-abas-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.a = nucleo.Nucleo(self.pasta, hoje=lambda: REF, plataforma=nucleo.NAVEGADOR)
        self.addCleanup(self.a.bloquear)
        self.token_a = self.pedir(self.a, "POST", "/api/configurar", {"pin": PIN}).dados["token"]
        self.b = nucleo.Nucleo(self.pasta, hoje=lambda: REF, plataforma=nucleo.NAVEGADOR)
        self.addCleanup(self.b.bloquear)
        self.token_b = self.pedir(self.b, "POST", "/api/desbloquear", {"pin": PIN}).dados["token"]

    @staticmethod
    def pedir(app, metodo, caminho, corpo=None, token=None) -> nucleo.RespostaApi:
        return app.tratar(metodo, caminho, corpo=None if corpo is None else json.dumps(corpo).encode(), token=token)

    def registrar(self, app, token, texto_registro) -> nucleo.RespostaApi:
        return self.pedir(app, "POST", "/api/registros", {"texto": texto_registro, "data_registro": "2026-09-30"}, token)

    def test_a_segunda_a_gravar_nao_apaga_a_primeira(self):
        self.assertEqual(self.registrar(self.a, self.token_a, TEXTO).status, 200)
        r = self.registrar(self.b, self.token_b, "Ela gosta de girassóis.")
        self.assertEqual((r.status, r.dados["erro"]), (409, nucleo.DIARIO_MUDOU))
        self.assertFalse(self.b.desbloqueado)                 # bloqueou sem salvar
        token = self.pedir(self.b, "POST", "/api/desbloquear", {"pin": PIN}).dados["token"]
        self.assertEqual([x["texto"] for x in self.pedir(self.b, "GET", "/api/registros", token=token).dados], [TEXTO])
        self.assertEqual(self.registrar(self.b, token, "Ela gosta de girassóis.").status, 200)
        # Agora quem está atrás é a primeira.
        self.assertEqual(self.registrar(self.a, self.token_a, "Ela foi ao cinema.").status, 409)

    def test_trocar_pin_e_baixar_copia_tambem_conferem(self):
        self.assertEqual(self.registrar(self.a, self.token_a, TEXTO).status, 200)
        r = self.pedir(self.b, "POST", "/api/pin", {"atual": PIN, "novo": "frase nova e secreta"}, self.token_b)
        self.assertEqual((r.status, r.dados["erro"]), (409, nucleo.DIARIO_MUDOU))
        r = self.pedir(self.b, "POST", "/api/backup/baixar", {}, self.token_b)
        self.assertEqual(r.status, 409)
        self.assertEqual(self.pedir(self.a, "POST", "/api/bloquear", {}, self.token_a).status, 200)
        self.assertEqual(self.pedir(self.a, "POST", "/api/desbloquear", {"pin": PIN}).status, 200)   # PIN de antes

    def test_bloquear_com_algo_por_salvar_nao_grava_por_cima(self):
        self.assertEqual(self.registrar(self.a, self.token_a, TEXTO).status, 200)
        self.b._banco._sujo = True                            # um salvar que tinha falhado
        self.b.bloquear()
        token = self.pedir(self.b, "POST", "/api/desbloquear", {"pin": PIN}).dados["token"]
        self.assertEqual(len(self.pedir(self.b, "GET", "/api/registros", token=token).dados), 1)


# ---------------------------------------------------------------- bloquear sem regravar

class TestBloquearSoGravaOQueFaltou(BasePonte):
    """Achado (navegador, baixa): cada bloqueio recifrava e regravava o .cofre, mesmo sem mudança
    (no celular, um syncfs do arquivo inteiro; com o Drive, uma versão nova a cada bloqueio)."""

    def test_sem_mudanca_o_arquivo_fica_como_esta(self):
        token = self.configurar()
        self.registrar(token)
        arquivo = Path(self.pasta) / config.ARQUIVO_COFRE
        antes = arquivo.stat()
        r = self.pedir("POST", "/api/bloquear", {}, token)
        self.assertEqual((r["status"], r["gravou"]), (200, False))
        depois = arquivo.stat()
        self.assertEqual((antes.st_ino, antes.st_size, antes.st_mtime_ns), (depois.st_ino, depois.st_size, depois.st_mtime_ns))

    def test_o_que_nao_foi_salvo_e_gravado_ao_bloquear(self):
        token = self.configurar()
        with mock.patch.object(self.app.cofre, "salvar", side_effect=PermissionError), \
                mock.patch.object(nucleo, "registrar_erro"):
            r = self.pedir("POST", "/api/registros", {"texto": TEXTO, "data_registro": "2026-09-30"}, token)
        self.assertEqual(r["status"], 500)
        self.assertTrue(self.app._banco.sujo)
        r = self.pedir("POST", "/api/bloquear", {}, token)
        self.assertEqual((r["status"], r["gravou"]), (200, True))
        token = self.desbloquear()["dados"]["token"]
        self.assertEqual(len(self.pedir("GET", "/api/registros", token=token)["dados"]), 1)


# ---------------------------------------------------------------- a ponte nunca levanta

class TestPonteNuncaLevanta(BasePonte):
    """Achado (navegador, baixa): uma consulta muito aninhada levantava RecursionError, que chegava
    ao JavaScript como PythonError em vez de {status: 400}."""

    def test_consulta_e_corpo_muito_aninhados(self):
        r = json.loads(ponte.tratar_json("GET", "/api/ping", None, None, "[" * 200000))
        self.assertEqual((r["status"], r["dados"]["erro"]), (400, ponte.PARAMETROS_INVALIDOS))
        r = json.loads(ponte.tratar_json("POST", "/api/configurar", "[" * 200000, None))
        self.assertEqual(r["status"], 400)

    def test_erro_inesperado_vira_500_sem_conteudo(self):
        with mock.patch.object(self.app, "tratar", side_effect=RuntimeError("Mariana")), \
                mock.patch.object(nucleo, "registrar_erro"):
            saida = ponte.tratar_json("GET", "/api/ping")
        r = json.loads(saida)
        self.assertEqual((r["status"], r["dados"]), (500, {"erro": nucleo.FALHA_INTERNA}))
        self.assertNotIn("Mariana", saida)

    def test_restaurar_aceita_o_que_o_pyodide_entrega(self):
        token = self.configurar()
        copia = self.baixar(token)

        class Uint8ArrayDoPyodide:          # o JsProxy de um Uint8Array tem to_bytes()
            def to_bytes(self):
                return copia

        for estranho in (None, 12345, "texto", object()):
            with self.subTest(arquivo=type(estranho).__name__):
                r = json.loads(ponte.restaurar_arquivo(estranho, json.dumps({"pin": PIN, "pin_atual": PIN}), token))
                self.assertEqual((r["status"], r["dados"]["erro"]), (400, "Escolha o arquivo do backup (.cofre)."))
        for arquivo in (Uint8ArrayDoPyodide(), memoryview(copia), bytearray(copia)):
            with self.subTest(arquivo=type(arquivo).__name__):
                r = json.loads(ponte.restaurar_arquivo(arquivo, json.dumps({"pin": PIN, "pin_atual": PIN}), token))
                self.assertEqual(r["status"], 200, r)
                token = r["dados"]["token"]
        r = json.loads(ponte.restaurar_arquivo(copia, "[" * 200000, token))
        self.assertEqual(r["status"], 400)
        with mock.patch.object(ponte, "_nucleo", None):
            r = json.loads(ponte.restaurar_arquivo(copia, json.dumps({"pin": PIN})))
            self.assertEqual(r["status"], 503)


class TestAutoteste(unittest.TestCase):
    """O Worker confere este Python antes de abrir o diário (achado do navegador sobre o
    `loadPackage` e o SQLite do Pyodide, seção 19.6)."""

    def test_passa_aqui(self):
        r = json.loads(ponte.autoteste())
        self.assertEqual((r["ok"], r["falhas"]), (True, []))
        self.assertTrue(r["python"] and r["sqlite"])

    def test_aponta_o_que_falta(self):
        with mock.patch.object(cofre, "abrir_conteudo", side_effect=RuntimeError("Mariana")):
            r = json.loads(ponte.autoteste())
        self.assertEqual((r["ok"], r["falhas"]), (False, ["formato_cofre: RuntimeError"]))


# ---------------------------------------------------------------- a mesma semelhança em todo lugar

class TestSemelhancaIgualEmTodoLugar(unittest.TestCase):
    """Achado (navegador, média): o Pyodide não tem rapidfuzz, e o `difflib` mede outra coisa
    ("Ana" x "Ana Paula": 100 no PC, 50 no celular). Agora é uma implementação só, em Python puro,
    igual ao `token_set_ratio` do rapidfuzz."""

    PARES = (("Ana", "Ana Paula", 100.0), ("chocolate amargo", "chocolate", 100.0),
             ("tulipas amarelas", "tulipas", 100.0), ("café", "cafe com leite", 100.0),
             ("Mariana", "Ana Paula", 37.5), ("sushi", "suchi", 80.0), ("", "Ana", 0.0), ("  ", "", 0.0))

    def test_pares_do_achado(self):
        for a, b, esperado in self.PARES:
            with self.subTest(a=a, b=b):
                self.assertEqual(texto.semelhanca(a, b), esperado)
                self.assertEqual(texto.semelhanca(b, a), esperado)

    def test_nome_parecido_casa_sem_rapidfuzz(self):
        conhecidas = [{"id": 1, "nome": "Ana Paula"}, {"id": 2, "nome": "Mariana"}]
        with mock.patch.dict(sys.modules, {"rapidfuzz": None, "rapidfuzz.fuzz": None}):
            self.assertEqual(regras_pessoas._nome_parecido("Ana", conhecidas), 1)

    def test_distancia_confere_com_a_conta_lenta(self):
        def lenta(a: str, b: str) -> int:
            anterior = [0] * (len(b) + 1)
            for x in a:
                atual = [0]
                for j, y in enumerate(b):
                    atual.append(anterior[j] + 1 if x == y else max(anterior[j + 1], atual[j]))
                anterior = atual
            return len(a) + len(b) - 2 * anterior[-1]

        sorteio = random.Random(19)
        letras = "abcçãé 🙂"
        for _ in range(150):
            a = "".join(sorteio.choice(letras) for _ in range(sorteio.randint(0, 120)))
            b = "".join(sorteio.choice(letras) for _ in range(sorteio.randint(0, 120)))
            self.assertEqual(texto._distancia_indel(a, b), lenta(a, b), (a, b))

    def test_igual_ao_rapidfuzz_quando_ele_existe(self):
        try:
            from rapidfuzz import fuzz
        except ImportError:
            self.skipTest("rapidfuzz não instalado")
        sorteio = random.Random(23)
        for _ in range(3000):
            a = "".join(sorteio.choice("abcde  ") for _ in range(sorteio.randint(0, 25)))
            b = "".join(sorteio.choice("abcde  ") for _ in range(sorteio.randint(0, 25)))
            self.assertEqual(texto.token_set_ratio(a, b), fuzz.token_set_ratio(a, b), (a, b))

    def test_texto_nao_usa_rapidfuzz_nem_difflib(self):
        importados = _importados(_ler(PACOTE / "texto.py"), "entrenos.texto", False)
        self.assertFalse({n for n in importados if n.split(".")[0] in ("rapidfuzz", "difflib")}, importados)


if __name__ == "__main__":
    unittest.main()
