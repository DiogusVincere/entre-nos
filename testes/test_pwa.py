"""Testes do app no celular e no navegador (DESIGN.md, seção 21): a agenda de avisos (só datas e
contagens), o construir_web.py, o servidor do PC servindo os arquivos novos com o tipo certo e a
mesma CSP, e o que dá para conferir dos arquivos da PWA sem navegador.

O Pyodide de verdade é testado à parte, no Node: testes/navegador/teste_pyodide.mjs.
Tudo em pastas temporárias; nada em %LOCALAPPDATA%.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import construir_web
from entrenos import alertas, cofre, config, nucleo, ponte, servidor
from entrenos.banco import Banco
from entrenos.modelos import Certeza, Item, Precisao, TipoItem
from testes.test_servidor import Relogio, ServidorDeTeste

REF = date(2026, 10, 7)
RAIZ = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = RAIZ / "web"
PIN = "girassol na janela"
_antes: dict = {}


def setUpModule() -> None:
    _antes["env"] = os.environ.get("ENTRENOS_DADOS")
    _antes["pasta"] = tempfile.mkdtemp(prefix="entrenos-teste-pwa-")
    os.environ["ENTRENOS_DADOS"] = _antes["pasta"]
    remendo = mock.patch.object(cofre, "SCRYPT_N", 2 ** 12)
    remendo.start()
    _antes["remendo"] = remendo


def tearDownModule() -> None:
    _antes["remendo"].stop()
    if _antes["env"] is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _antes["env"]
    shutil.rmtree(_antes["pasta"], ignore_errors=True)


def _evento(banco: Banco, resumo: str, quando: date, precisao: Precisao = Precisao.DIA) -> int:
    trecho = f"{resumo} dia {quando:%d/%m}."
    item = Item(tipo=TipoItem.EVENTO, resumo=resumo, trecho=trecho, certeza=Certeza.CONFIRMADO,
                data_evento=quando, precisao=precisao, categorias=["Eventos futuros"])
    return banco.criar_registro(trecho, REF - timedelta(days=2), [item], [], [])


def _agenda_dia_a_dia(banco: Banco, referencia: date, dias: int) -> dict[str, int]:
    """A mesma conta feita do jeito mais lento e direto: a página Hoje de cada dia (só os avisos de
    data, que é o que a agenda conta), com a referência daquele dia."""
    saida = {}
    for n in range(dias):
        dia = referencia + timedelta(days=n)
        d = alertas.carregar(banco, dia)
        antecedencias = alertas._antecedencias(d)
        janela = max([alertas.DIAS_PROXIMOS, 1, *alertas.DIAS_NAMORO, *antecedencias])
        importantes, lembretes = alertas._avisos_de_data(d, alertas.ocorrencias(d, janela), antecedencias, ([], []))
        dispensados = banco.alertas_dispensados()
        total = (len([a for a in importantes if a.chave not in dispensados])
                 + min(alertas.MAX_LEMBRETES, len([a for a in lembretes if a.chave not in dispensados])))
        if total:
            saida[dia.isoformat()] = total
    return saida


# ---------------------------------------------------------------- agenda de avisos (seção 21.5)

class TestAgendaDeAvisos(unittest.TestCase):
    def setUp(self):
        self.banco = Banco.novo()
        self.addCleanup(self.banco.fechar)

    def test_igual_a_pagina_hoje_de_cada_dia(self):
        self.banco.salvar_config(nome_dela="Ana", aniversario_dela=date(1998, 10, 20), inicio_namoro=date(2025, 3, 12))
        _evento(self.banco, "Apresentação no trabalho", REF + timedelta(days=1))
        _evento(self.banco, "Cirurgia da mãe dela", REF + timedelta(days=15))
        _evento(self.banco, "Prova de inglês", REF + timedelta(days=40))
        pid = self.banco.criar_pessoa("Sandra", "mãe")
        self.banco.atualizar_pessoa(pid, aniversario=date(1970, 10, 9))
        agenda = alertas.agenda_de_avisos(self.banco, REF, 60)
        self.assertEqual(agenda, _agenda_dia_a_dia(self.banco, REF, 60))
        self.assertIn(REF.isoformat(), agenda)                    # amanhã é a apresentação
        self.assertIn("2026-10-20", agenda)                       # o aniversário dela
        _evento(self.banco, "Viagem", date(2026, 11, 1), Precisao.MES)          # mês: não avisa
        self.assertEqual(alertas.agenda_de_avisos(self.banco, REF, 60), agenda)

    def test_so_datas_e_numeros(self):
        self.banco.salvar_config(nome_dela="Ana", aniversario_dela=date(1998, 10, 9))
        _evento(self.banco, "Show da Marisa Monte", REF + timedelta(days=3))
        agenda = alertas.agenda_de_avisos(self.banco, REF)
        self.assertTrue(agenda)
        for dia, n in agenda.items():
            self.assertRegex(dia, r"^\d{4}-\d{2}-\d{2}$")
            self.assertIsInstance(n, int)
            self.assertGreater(n, 0)
        texto = json.dumps(agenda, ensure_ascii=False)
        for proibido in ("Ana", "Marisa", "Show", "anivers"):
            self.assertNotIn(proibido, texto)

    def test_dispensado_nao_conta_e_lembretes_tem_teto(self):
        self.banco.salvar_config(antecedencias=[3])
        alvo = REF + timedelta(days=3)
        for i in range(5):
            _evento(self.banco, f"Compromisso {i}", alvo)
        agenda = alertas.agenda_de_avisos(self.banco, REF, 5)
        self.assertEqual(agenda[REF.isoformat()], alertas.MAX_LEMBRETES)      # 5 lembretes, a página mostra 3
        self.assertEqual(agenda[alvo.isoformat()], 5)                           # importantes: todos
        hoje = alertas.hoje(self.banco, REF)
        for alerta in hoje["alertas"]:
            self.banco.dispensar_alerta(alerta.chave)
        agenda = alertas.agenda_de_avisos(self.banco, REF, 5)
        self.assertEqual(agenda.get(REF.isoformat(), 0), 2)                    # os 2 que não apareceram
        self.assertEqual(agenda, _agenda_dia_a_dia(self.banco, REF, 5))

    def test_janela_e_diario_vazio(self):
        self.assertEqual(alertas.agenda_de_avisos(self.banco, REF), {})
        _evento(self.banco, "Formatura", REF + timedelta(days=90))
        self.assertEqual(alertas.agenda_de_avisos(self.banco, REF, 30), {})
        agenda = alertas.agenda_de_avisos(self.banco, REF, 100)
        self.assertIn((REF + timedelta(days=60)).isoformat(), agenda)          # 30 dias antes
        self.assertTrue(all(d < (REF + timedelta(days=100)).isoformat() for d in agenda))
        self.assertEqual(alertas.agenda_de_avisos(self.banco, REF, 0), {})

    def test_so_le(self):
        self.banco.salvar_config(aniversario_dela=date(1998, 10, 9))
        self.banco.marcar_salvo()
        historico = self.banco.historico(1000)
        alertas.agenda_de_avisos(self.banco, REF)
        self.assertFalse(self.banco.sujo)
        self.assertEqual(self.banco.historico(1000), historico)


class TestAgendaNoNucleo(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-pwa-nucleo-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.relogio = Relogio()
        self.parede = Relogio(1_000_000.0)
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: REF, relogio=self.relogio, relogio_parede=self.parede,
                                 atraso_pin=0, plataforma="navegador")
        self.addCleanup(self.app.bloquear)

    def pedir(self, metodo, caminho, corpo=None, token=None):
        r = self.app.tratar(metodo, caminho, corpo=None if corpo is None else json.dumps(corpo).encode("utf-8"),
                            token=token)
        return r.status, json.loads(r.corpo)

    def test_bloqueado_nao_tem_agenda_e_nao_conta_como_uso(self):
        self.assertIsNone(self.app.agenda_de_avisos())
        st, d = self.pedir("POST", "/api/configurar", {"pin": PIN, "aniversario_dela": "1998-10-09"})
        self.assertEqual(st, 200)
        # Aniversário dia 9: a 2 dias (2 não é antecedência), amanhã e no dia.
        self.assertEqual(self.app.agenda_de_avisos(), {"2026-10-08": 1, "2026-10-09": 1})
        # 9 minutos sem uso, a agenda é calculada, e no minuto 10 o diário bloqueia mesmo assim.
        self.relogio.avancar(9 * 60)
        self.parede.avancar(9 * 60)
        self.assertIsNotNone(self.app.agenda_de_avisos())
        self.relogio.avancar(61)
        self.parede.avancar(61)
        self.assertIsNone(self.app.agenda_de_avisos())
        self.assertFalse(self.app.desbloqueado)


class TestAgendaNaPonte(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-pwa-ponte-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.addCleanup(self._soltar_ponte)

    @staticmethod
    def _soltar_ponte():
        if ponte._nucleo is not None:
            ponte._nucleo.bloquear()
        ponte._nucleo = None
        if ponte._log_manipulador is not None:
            logging.getLogger("entrenos").removeHandler(ponte._log_manipulador)
            ponte._log_manipulador.close()
            ponte._log_manipulador = None

    def test_agenda_pela_ponte(self):
        ponte._nucleo = None
        self.assertEqual(json.loads(ponte.agenda_de_avisos()), {"aberto": False, "dias": {}, "gravou": False})
        ponte.iniciar_no_navegador(self.pasta, atraso_pin=0, hoje=lambda: REF)
        r = json.loads(ponte.tratar_json("POST", "/api/configurar", json.dumps({"pin": PIN, "aniversario_dela": "1998-10-08"})))
        self.assertEqual(r["status"], 200)
        agenda = json.loads(ponte.agenda_de_avisos())
        self.assertEqual(agenda, {"aberto": True, "dias": {"2026-10-07": 1, "2026-10-08": 1}, "gravou": False})
        token = r["dados"]["token"]
        json.loads(ponte.tratar_json("POST", "/api/bloquear", "{}", token))
        self.assertEqual(json.loads(ponte.agenda_de_avisos())["aberto"], False)

    def test_erro_vira_agenda_fechada(self):
        ponte.iniciar_no_navegador(self.pasta, atraso_pin=0, hoje=lambda: REF)
        with mock.patch.object(nucleo.Nucleo, "agenda_de_avisos", side_effect=RuntimeError("Ana")):
            r = json.loads(ponte.agenda_de_avisos())
        self.assertEqual((r["aberto"], r["dias"]), (False, {}))       # o erros.log mudou: gravou pode vir True
        self.assertNotIn("Ana", (Path(self.pasta) / "erros.log").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- servidor do PC (seção 21.3)

class TestServidorServeAPwa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = ServidorDeTeste()

    @classmethod
    def tearDownClass(cls):
        cls.s.fechar()

    def test_tipos_novos(self):
        self.assertEqual(servidor.TIPOS_MIME[".mjs"], "text/javascript; charset=utf-8")
        self.assertEqual(servidor.TIPOS_MIME[".wasm"], "application/wasm")
        self.assertEqual(servidor.TIPOS_MIME[".webmanifest"], "application/manifest+json")
        self.assertEqual(servidor.TIPOS_MIME[".zip"], "application/zip")
        self.assertEqual(servidor.TIPOS_MIME[".whl"], "application/zip")
        esperados = {
            "/trabalhador.mjs": "text/javascript; charset=utf-8",
            "/entrenos_pyodide.mjs": "text/javascript; charset=utf-8",
            "/sw.js": "text/javascript; charset=utf-8",
            "/agenda_avisos.js": "text/javascript; charset=utf-8",
            "/manifest.webmanifest": "application/manifest+json",
            "/icones/icone-192.png": "image/png",
            "/pyodide/314.0.7/pyodide.mjs": "text/javascript; charset=utf-8",
            "/pyodide/314.0.7/pyodide.asm.wasm": "application/wasm",
            "/pyodide/314.0.7/python_stdlib.zip": "application/zip",
            "/pyodide/314.0.7/six-1.17.0-py2.py3-none-any.whl": "application/zip",
            "/pyodide/314.0.7/licencas/LEIA-ME.txt": "text/plain; charset=utf-8",
        }
        for caminho, tipo in esperados.items():
            with self.subTest(caminho=caminho):
                r = self.s.pedir("GET", caminho, token=None)
                self.assertEqual(r.status, 200)
                self.assertEqual(r.cabecalhos["Content-Type"], tipo)
                self.assertEqual(r.cabecalhos["Content-Security-Policy"], servidor.CSP)
                self.assertEqual(r.cabecalhos["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.s.pedir("GET", "/pyodide/314.0.7/../../app.js", token=None).status, 404)

    def test_csp_sem_codigo_do_google_e_minima(self):
        diretivas = dict(d.strip().split(" ", 1) for d in servidor.CSP.split(";"))
        # Seção 22.2: nenhum script, estilo ou quadro do Google (o login é uma janela que volta para o
        # oauth.html); só as conexões com a Drive API e a revogação do acesso.
        self.assertEqual(diretivas["script-src"], "'self'")             # no PC a página não roda WebAssembly
        self.assertEqual(diretivas["style-src"], "'self'")
        self.assertNotIn("frame-src", diretivas)                        # default-src 'self'
        self.assertEqual(diretivas["connect-src"], "'self' https://www.googleapis.com/drive/v3/ "
                         "https://www.googleapis.com/upload/drive/v3/ https://oauth2.googleapis.com/revoke")
        self.assertEqual(diretivas["worker-src"], "'self'")
        self.assertEqual(diretivas["object-src"], "'none'")
        self.assertEqual(diretivas["form-action"], "'self'")
        self.assertEqual(diretivas["frame-ancestors"], "'none'")
        self.assertNotIn("'unsafe-eval'", servidor.CSP)
        self.assertNotIn("'unsafe-inline'", servidor.CSP)
        self.assertNotIn("accounts.google.com", servidor.CSP)
        # Nada de fora além da Drive API e da revogação (nada de http://, nada de curinga).
        enderecos = {p for p in servidor.CSP.replace(";", " ").split() if "://" in p}
        self.assertEqual({e.split("/")[2] for e in enderecos}, {"www.googleapis.com", "oauth2.googleapis.com"})
        self.assertTrue(all(e.startswith("https://") and "*" not in e for e in enderecos))
        self.assertEqual(dict(servidor.CABECALHOS_SEGURANCA)["Cross-Origin-Opener-Policy"], "same-origin-allow-popups")
        # O site: a mesma, com 'wasm-unsafe-eval' para o Pyodide do Worker.
        site = dict(d.strip().split(" ", 1) for d in construir_web.csp_do_site(servidor.CSP).split(";"))
        self.assertEqual(site["script-src"], "'self' 'wasm-unsafe-eval'")
        self.assertEqual({k: v for k, v in site.items() if k != "script-src"},
                         {k: v for k, v in diretivas.items() if k != "script-src"})


# ---------------------------------------------------------------- construir_web.py (seção 21.1)

def _copiar_web(destino: Path) -> Path:
    alvo = destino / "web"
    shutil.copytree(WEB, alvo, ignore=shutil.ignore_patterns("entrenos-py.zip", "__pycache__"))
    return alvo


class TestConstruirWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="entrenos-teste-site-"))
        cls.web = _copiar_web(cls.tmp)
        cls.resumo = construir_web.construir(cls.tmp / "site", web=cls.web)
        cls.site = cls.tmp / "site"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_site_completo(self):
        for nome in ("index.html", "app.js", "estilo.css", "sw.js", "_headers", "trabalhador.mjs",
                     "entrenos_pyodide.mjs", "agenda_avisos.js", "manifest.webmanifest", "entrenos-py.zip",
                     "icones/icone-512.png", "telas/eu.js", "pyodide/314.0.7/pyodide.asm.wasm",
                     "pyodide/314.0.7/licencas/LEIA-ME.txt", "pyodide/314.0.7/LICENSE-pyodide.txt"):
            with self.subTest(nome=nome):
                self.assertTrue((self.site / nome).is_file())
        self.assertFalse(any(p.suffix in (".exe", ".pyc", ".cofre") for p in self.site.rglob("*")))
        self.assertFalse(any(p.name == "__pycache__" for p in self.site.rglob("*")))
        self.assertTrue((self.web / "entrenos-py.zip").is_file())            # o build também deixa em web/
        self.assertEqual(self.resumo["rodas"], ["cffi-2.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
                                                "cryptography-47.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl",
                                                "pycparser-3.0-py3-none-any.whl", "six-1.17.0-py2.py3-none-any.whl"])

    def test_zip_do_python(self):
        with zipfile.ZipFile(self.site / "entrenos-py.zip") as z:
            nomes = z.namelist()
            self.assertTrue(all(n.startswith("entrenos/") for n in nomes))
            for precisa in ("entrenos/__init__.py", "entrenos/ponte.py", "entrenos/nucleo.py", "entrenos/cofre.py",
                            "entrenos/recursos/__init__.py", "entrenos/recursos/ciclo.py", "entrenos/regras/datas.py"):
                self.assertIn(precisa, nomes)
            for fora in ("entrenos/servidor.py", "entrenos/__main__.py"):
                self.assertNotIn(fora, nomes)
            self.assertFalse(any("__pycache__" in n or not n.endswith(".py") for n in nomes))
            self.assertEqual(z.read("entrenos/ponte.py"), (RAIZ / "entrenos" / "ponte.py").read_bytes())
            self.assertEqual({i.date_time for i in z.infolist()}, {construir_web.DATA_FIXA})
        pacote = RAIZ / "entrenos"
        esperado = sorted(f"entrenos/{p.relative_to(pacote).as_posix()}" for p in pacote.rglob("*.py")
                          if "__pycache__" not in p.parts
                          and not (p.parent == pacote and p.name in ("servidor.py", "__main__.py")))
        self.assertEqual(sorted(nomes), esperado)

    def test_zip_sempre_igual_e_versao_estavel(self):
        outro = self.tmp / "outro.zip"
        construir_web.montar_zip_python(outro)
        self.assertEqual(outro.read_bytes(), (self.site / "entrenos-py.zip").read_bytes())
        segundo = construir_web.construir(self.tmp / "site2", web=self.web)
        self.assertEqual(segundo["versao"], self.resumo["versao"])
        self.assertEqual((self.tmp / "site2" / "sw.js").read_bytes(), (self.site / "sw.js").read_bytes())
        self.assertEqual((self.tmp / "site2" / "_headers").read_bytes(), (self.site / "_headers").read_bytes())

    def test_versao_muda_quando_um_arquivo_muda(self):
        web = _copiar_web(self.tmp / "mudado")
        with open(web / "estilo.css", "a", encoding="utf-8") as arquivo:
            arquivo.write("\n/* mudou */\n")
        resumo = construir_web.construir(self.tmp / "mudado" / "site", web=web)
        self.assertNotEqual(resumo["versao"], self.resumo["versao"])

    def test_sw_do_site(self):
        sw = (self.site / "sw.js").read_text(encoding="utf-8")
        self.assertIn(f'const VERSAO = "{self.resumo["versao"]}";', sw)
        self.assertNotIn('const VERSAO = "dev"', sw)
        app = json.loads(re.search(r"const ARQUIVOS_APP = (\[.*?\]);", sw, re.S).group(1).replace(",\n]", "\n]"))
        pyodide = json.loads(re.search(r"const ARQUIVOS_PYODIDE = (\[.*?\]);", sw, re.S).group(1).replace(",\n]", "\n]"))
        self.assertEqual(app[0], "./")
        self.assertNotIn("index.html", app)                     # o Cloudflare redireciona /index.html para /
        self.assertNotIn("sw.js", app)
        for nome in ("app.js", "estilo.css", "trabalhador.mjs", "entrenos_pyodide.mjs", "agenda_avisos.js",
                     "entrenos-py.zip", "manifest.webmanifest", "icones/icone-192.png", "telas/ciclo.js"):
            self.assertIn(nome, app)
        self.assertFalse(any(a.startswith("pyodide/") for a in app))
        self.assertTrue(all((self.site / a).is_file() for a in app[1:] + pyodide))
        self.assertIn("pyodide/314.0.7/pyodide.asm.wasm", pyodide)
        self.assertFalse(any("licen" in a.lower() or a.endswith("package.json") for a in pyodide))
        # As telas que o index.html carrega estão todas no pré-cache.
        html = (self.site / "index.html").read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="([^"#:]+)"', html):
            self.assertIn(ref, app, ref)

    def test_headers(self):
        texto = (self.site / "_headers").read_text(encoding="utf-8")
        regras: dict[str, list[str]] = {}
        atual = None
        for linha in texto.splitlines():
            if not linha.strip() or linha.startswith("#"):
                continue
            if not linha.startswith(" "):
                self.assertNotIn(linha, regras, "caminho repetido")
                atual = regras.setdefault(linha, [])
            else:
                atual.append(linha.strip())
        csp = f"Content-Security-Policy: {construir_web.csp_do_site(servidor.CSP)}"
        self.assertEqual(construir_web.ler_csp(), servidor.CSP)
        # Seção 22.2: a CSP UMA vez, em "/*" (qualquer caminho, até um que o Cloudflare respondesse
        # com a página, sai com ela) e em nenhuma outra regra (o Cloudflare juntaria duas com vírgula).
        self.assertIn(csp, regras["/*"])
        self.assertEqual(sum(any(x.startswith("Content-Security-Policy") for x in r) for r in regras.values()), 1)
        self.assertTrue((self.site / "404.html").is_file())             # caminho desconhecido: 404, não a página
        self.assertTrue((self.site / "oauth.html").is_file() and (self.site / "oauth.js").is_file())
        self.assertIn("X-Frame-Options: DENY", regras["/*"])
        self.assertIn("Cross-Origin-Opener-Policy: same-origin-allow-popups", regras["/*"])
        self.assertIn("X-Content-Type-Options: nosniff", regras["/*"])
        self.assertIn("Content-Type: application/wasm", regras["/pyodide/314.0.7/pyodide.asm.wasm"])
        self.assertIn("Content-Type: text/javascript; charset=utf-8", regras["/trabalhador.mjs"])
        self.assertIn("Content-Type: application/manifest+json", regras["/manifest.webmanifest"])
        self.assertIn("Content-Type: application/zip", regras["/entrenos-py.zip"])
        self.assertIn("Content-Type: application/zip", regras["/pyodide/314.0.7/cryptography-47.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl"])
        self.assertIn("Cache-Control: public, max-age=31536000, immutable", regras["/pyodide/314.0.7/*"])
        self.assertIn("Cache-Control: no-cache", regras["/sw.js"])
        # Content-Type em uma regra só por arquivo (o Cloudflare juntaria dois com vírgula).
        tipos = [c for c, r in regras.items() if any(x.startswith("Content-Type") for x in r)]
        self.assertEqual(len(tipos), len(set(tipos)))

    def test_recusa_pyodide_mexido(self):
        casos = {
            "byte trocado": lambda p: p.joinpath("pyodide.asm.mjs").write_bytes(p.joinpath("pyodide.asm.mjs").read_bytes() + b" "),
            "roda trocada": lambda p: p.joinpath("six-1.17.0-py2.py3-none-any.whl").write_bytes(b"PK"),
            "roda faltando": lambda p: p.joinpath("cffi-2.0.0-cp314-cp314-pyemscripten_2026_0_wasm32.whl").unlink(),
            "roda sobrando": lambda p: p.joinpath("numpy-9.9-py3-none-any.whl").write_bytes(b"PK"),
            "outra versão": lambda p: p.parent.joinpath("0.26.0").mkdir(),
            "licença faltando": lambda p: p.joinpath("licencas", "six-1.17.0-LICENSE.txt").unlink(),
            "licença mexida": lambda p: p.joinpath("licencas", "cffi-2.0.0-LICENSE.txt").write_text("outra", encoding="utf-8"),
        }
        for nome, estragar in casos.items():
            with self.subTest(caso=nome):
                base = self.tmp / f"estragado-{abs(hash(nome))}"
                web = _copiar_web(base)
                estragar(web / "pyodide" / "314.0.7")
                with self.assertRaises(construir_web.ErroDeConstrucao):
                    construir_web.construir(base / "site", web=web)
                self.assertFalse((base / "site").exists())                 # nada pela metade

    def test_destino_seguro(self):
        for proibido in (RAIZ, WEB, RAIZ / "entrenos", WEB / "site", RAIZ.parent):
            with self.subTest(destino=proibido), self.assertRaises(construir_web.ErroDeConstrucao):
                construir_web._destino_seguro(proibido)
        alheia = self.tmp / "alheia"
        alheia.mkdir()
        (alheia / "meu-arquivo.txt").write_text("não apague", encoding="utf-8")
        with self.assertRaises(construir_web.ErroDeConstrucao):
            construir_web.construir(alheia, web=self.web)
        self.assertTrue((alheia / "meu-arquivo.txt").is_file())
        construir_web._destino_seguro(self.tmp / "nova")                 # não existe: pode
        vazia = self.tmp / "vazia"
        vazia.mkdir()
        construir_web._destino_seguro(vazia)

    def test_main(self):
        saida = io.StringIO()
        with mock.patch.object(construir_web, "WEB", self.web), contextlib.redirect_stdout(saida):
            self.assertEqual(construir_web.main(["--destino", str(self.tmp / "site-main")]), 0)
        self.assertIn("Site montado", saida.getvalue())
        self.assertTrue((self.tmp / "site-main" / "_headers").is_file())


class TestArquivosDoProjeto(unittest.TestCase):
    def test_sw_dev_acompanha_os_arquivos(self):
        # python construir_web.py --sw-dev refaz este bloco quando entra ou sai um arquivo de web/.
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        inicio = sw.index(construir_web.INICIO_BLOCO_SW)
        fim = sw.index(construir_web.FIM_BLOCO_SW) + len(construir_web.FIM_BLOCO_SW)
        self.assertEqual(sw[inicio:fim], construir_web.bloco_de_desenvolvimento())

    def test_pyodide_e_licencas_conferem(self):
        self.assertEqual(len(construir_web.conferir_pyodide()), 4)
        construir_web.conferir_licencas()
        leia = (WEB / "pyodide" / "314.0.7" / "licencas" / "LEIA-ME.txt").read_text(encoding="utf-8")
        for nome in ("Pyodide 314.0.7", "cryptography 47.0.0", "cffi 2.0.0", "pycparser 3.0", "six 1.17.0",
                     "CPython", "Emscripten"):
            self.assertIn(nome, leia)

    def test_manifest_e_icones(self):
        m = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((m["name"], m["short_name"], m["lang"], m["display"]), ("Entre Nós", "Entre Nós", "pt-BR", "standalone"))
        self.assertEqual((m["start_url"], m["scope"]), ("./", "./"))
        self.assertRegex(m["background_color"], r"^#[0-9A-Fa-f]{6}$")
        tamanhos = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertEqual(tamanhos, {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")})
        for icone in m["icons"]:
            largura, altura = _tamanho_png(WEB / icone["src"])
            self.assertEqual(f"{largura}x{altura}", icone["sizes"])
        self.assertEqual(_tamanho_png(WEB / "icones" / "apple-touch-icon.png"), (180, 180))
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="manifest" href="manifest.webmanifest">', html)
        self.assertIn('<link rel="apple-touch-icon" href="icones/apple-touch-icon.png">', html)

    def test_gitignore(self):
        linhas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("site/", linhas)
        self.assertIn("web/entrenos-py.zip", linhas)

    def test_js_da_pwa_sem_html_montado_nem_eval_nem_log(self):
        for nome in ("app.js", "sw.js", "agenda_avisos.js", "trabalhador.mjs", "entrenos_pyodide.mjs"):
            with self.subTest(arquivo=nome):
                js = (WEB / nome).read_text(encoding="utf-8")
                for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                                 "new Function", "importScripts('http"):
                    self.assertNotIn(proibido, js)
                if nome != "app.js":                        # (a área da IA do app.js cita endereços dos serviços)
                    self.assertNotIn("https://", js)
                    self.assertNotIn("http://", js)
                    self.assertNotRegex(js, r"console\.(log|info|warn|error|debug)")

    def test_sw_so_pula_a_espera_a_pedido(self):
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertEqual(sw.count("skipWaiting("), 1)
        self.assertRegex(sw, r"dados\.tipo === 'pular-espera'\) self\.skipWaiting\(\)")
        self.assertIn("redirect: 'error'", sw)
        self.assertIn("cache: modoCache", sw)
        self.assertIn("resposta.type === 'basic'", sw)
        self.assertIn("!resposta.redirected", sw)

    def test_app_js_modo_navegador(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        for trecho in ("fetch('api/ping'", "new Worker('trabalhador.mjs', { type: 'module'", "beforeinstallprompt",
                       "navigator.storage.persist()", "navigator.storage.estimate()", "periodicSync.register(",
                       "navigator.share(", "Adicionar à Tela de Início", "register('sw.js'",
                       "noNavegador() ? null : secaoIA()", "tipo: 'restaurar'", "'/backup/baixar'"):
            self.assertIn(trecho, app)
        self.assertIn("quandoPronto(carregarEstado)", app)

    def test_trabalhador_na_ordem_do_contrato(self):
        js = (WEB / "trabalhador.mjs").read_text(encoding="utf-8")
        abrir = js[js.index("async function abrir()"):js.index("async function semDiarioSemAgenda")]
        ordem = [abrir.index(t) for t in ("prepararPython(", "python.autoteste()", "await trava",
                                          "python.montarDados(", "python.iniciar(")]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIn("NOME_TRAVA = 'entrenos-cofre'", js)
        self.assertIn("ifAvailable: true", js)
        logica = (WEB / "entrenos_pyodide.mjs").read_text(encoding="utf-8")
        self.assertIn("PACOTES = ['cryptography']", logica)
        self.assertIn("loadPackage(PACOTES", logica)
        self.assertIn("FS.filesystems.IDBFS", logica)
        self.assertIn("if (resposta.gravou) await this.sincronizar(false)", logica)


def _tamanho_png(caminho: Path) -> tuple[int, int]:
    with open(caminho, "rb") as arquivo:
        cabecalho = arquivo.read(24)
    assert cabecalho[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", cabecalho[16:24])


if __name__ == "__main__":
    unittest.main()
