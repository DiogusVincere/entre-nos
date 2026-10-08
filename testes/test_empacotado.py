"""Empacotamento (DESIGN.md, seção 18.3): o programa instalado, o instalador e o README.

Não constrói nada (isso é do construir_instalador.ps1): confere o modo empacotado do código e
que os arquivos do instalador continuam dizendo o que foi decidido.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from entrenos import config, servidor

RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
INSTALADOR = RAIZ / "instalador"


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8-sig")


class TestModoEmpacotado(unittest.TestCase):
    def test_versao(self):
        self.assertEqual(config.VERSAO, "0.3.0")

    def test_pelo_codigo_web_vem_da_raiz(self):
        self.assertFalse(config.empacotado())
        self.assertEqual(config._raiz(), RAIZ)
        self.assertEqual(config.PASTA_WEB, RAIZ / "web")

    def test_empacotado_web_vem_de_dentro_do_pacote(self):
        pacote = tempfile.mkdtemp(prefix="entrenos-teste-pacote-")
        self.addCleanup(shutil.rmtree, pacote, True)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", pacote, create=True):
            self.assertTrue(config.empacotado())
            self.assertEqual(config._raiz(), Path(os.path.abspath(pacote)))

    def test_empacotado_sem_meipass_usa_a_pasta_do_exe(self):
        self.assertFalse(hasattr(sys, "_MEIPASS"))
        exe = os.path.join(tempfile.gettempdir(), "Entre Nos", "EntreNos.exe")
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", exe):
            self.assertEqual(config._raiz(), Path(os.path.dirname(exe)))

    def test_nome_do_programa_na_ajuda(self):
        with mock.patch.object(config, "empacotado", return_value=True), \
                mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                mock.patch.object(servidor, "_avisar") as avisar:
            with self.assertRaises(SystemExit) as saida:
                servidor._argumentos(["--help"])
        self.assertEqual(saida.exception.code, 0)
        avisar.assert_called_once()
        ajuda = avisar.call_args.args[0]
        self.assertIn("EntreNos.exe", ajuda)
        for opcao in ("--sem-navegador", "--porta", "--dados"):
            self.assertIn(opcao, ajuda)

    def test_sem_console_erro_de_argumento_aparece_numa_janela(self):
        with mock.patch.object(sys, "stderr", None), mock.patch.object(servidor, "_avisar") as avisar:
            with self.assertRaises(SystemExit) as saida:
                servidor._argumentos(["--porta", "abc"])
        self.assertEqual(saida.exception.code, 2)
        avisar.assert_called_once()                      # uma janela só, com o uso e o erro
        self.assertIn("--porta", avisar.call_args.args[0])

    def test_com_console_continua_o_argparse_de_sempre(self):
        args = servidor._argumentos(["--sem-navegador", "--porta", "8797", "--dados", "x"])
        self.assertEqual((args.sem_navegador, args.porta, args.dados), (True, 8797, "x"))

    def test_sem_console_erro_ao_abrir_vai_para_o_log_da_pasta_de_dados(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-main-")
        self.addCleanup(shutil.rmtree, pasta, True)

        def explodir(*_a, **_k):
            raise RuntimeError("Mariana adora tulipas")

        with mock.patch.object(sys, "stderr", None), mock.patch.object(servidor, "criar_servidor", explodir), \
                mock.patch.object(servidor, "_avisar") as avisar:
            self.assertEqual(servidor.main(["--sem-navegador", "--porta", "8797", "--dados", pasta]), 1)
        avisar.assert_called_once()
        self.assertIn("erros.log", avisar.call_args.args[0])
        self.assertNotIn("Mariana", avisar.call_args.args[0])
        log = (Path(pasta) / "erros.log").read_text(encoding="utf-8")
        self.assertIn("RuntimeError", log)
        self.assertNotIn("Mariana", log)
        self.assertFalse(any(isinstance(h, logging.handlers.RotatingFileHandler)
                             for h in logging.getLogger("entrenos").handlers))

    def test_com_console_o_erro_continua_aparecendo(self):
        pasta = tempfile.mkdtemp(prefix="entrenos-teste-main-")
        self.addCleanup(shutil.rmtree, pasta, True)
        with mock.patch.object(servidor, "criar_servidor", side_effect=RuntimeError("defeito")), \
                mock.patch.object(servidor, "_avisar") as avisar:
            with self.assertRaises(RuntimeError):
                servidor.main(["--sem-navegador", "--porta", "8797", "--dados", pasta])
        avisar.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "mutex do Windows")
    def test_mutex_tambem_pelo_codigo(self):
        # Aberto pelo iniciar.py (pythonw) o Entre Nós usa a mesma pasta de dados do instalado: o
        # desinstalador precisa saber que ele está aberto antes de apagar o diário (seção 18.6).
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        with mock.patch.object(config, "empacotado", return_value=False):
            alca = servidor._marcar_aberto()
        self.assertTrue(alca)
        kernel32.CloseHandle(alca)

    def test_sem_windows_nao_ha_mutex(self):
        with mock.patch.object(servidor.os, "name", "posix"):
            self.assertIsNone(servidor._marcar_aberto())

    @unittest.skipUnless(os.name == "nt", "mutex do Windows")
    def test_mutex_que_o_instalador_procura(self):
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        sincronizar = 0x00100000
        with mock.patch.object(config, "empacotado", return_value=True):
            alca = servidor._marcar_aberto()
        self.assertTrue(alca)
        try:
            outra = kernel32.OpenMutexW(sincronizar, False, servidor.MUTEX_ABERTO)
            self.assertTrue(outra)
            kernel32.CloseHandle(outra)
        finally:
            kernel32.CloseHandle(alca)


class TestArquivosDoInstalador(unittest.TestCase):
    def setUp(self):
        self.iss = _ler(INSTALADOR / "EntreNos.iss")
        self.spec = _ler(INSTALADOR / "entrenos.spec")

    def diretiva(self, nome: str) -> str:
        achado = re.search(rf"^{nome}=(.*)$", self.iss, re.MULTILINE)
        self.assertIsNotNone(achado, nome)
        return achado.group(1).strip()

    def test_setup(self):
        self.assertRegex(self.diretiva("AppId"), r"^\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}$")
        self.assertEqual(self.diretiva("PrivilegesRequired"), "lowest")
        self.assertEqual(self.diretiva("DefaultDirName"), r"{localappdata}\Programs\Entre Nos")
        self.assertEqual(self.diretiva("AppVersion"), "{#AppVersao}")
        self.assertEqual(self.diretiva("OutputBaseFilename"), "EntreNos-Setup-{#AppVersao}")
        self.assertEqual(self.diretiva("SetupIconFile"), "entrenos.ico")
        self.assertIn(r"compiler:Languages\BrazilianPortuguese.isl", self.iss)
        self.assertNotIn("PrivilegesRequiredOverridesAllowed", self.iss)

    def test_mutex_igual_ao_do_programa(self):
        self.assertEqual(self.diretiva("AppMutex"), "{#AppMutexNome}")
        self.assertIn(f'#define AppMutexNome "{servidor.MUTEX_ABERTO}"', self.iss)

    def test_atalhos_e_abrir_agora(self):
        self.assertRegex(self.iss, r'Name: "desktopicon";[^\n]*Flags: unchecked')
        self.assertRegex(self.iss, r'Name: "\{autodesktop\}[^\n]*Tasks: desktopicon')
        self.assertRegex(self.iss, r'\[Run\]\nFilename: "\{app\}\\\{#AppExe\}";[^\n]*postinstall skipifsilent')

    def test_desinstalador_nunca_apaga_dados_em_silencio(self):
        codigo = self.iss.split("[Code]", 1)[1]
        silencioso = codigo.index("if UninstallSilent then\n    Exit;")
        self.assertLess(silencioso, codigo.index("SuppressibleMsgBox"))
        self.assertLess(silencioso, codigo.index("DelTree"))
        self.assertIn("MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES then\n    Exit;", codigo)   # "Não" é o padrão
        self.assertIn(r"'{localappdata}\EntreNos'", codigo)
        self.assertEqual(codigo.count("DelTree("), 1)

    def test_desinstalador_nao_apaga_com_o_app_aberto(self):
        # Um Entre Nós aberto (inclusive pelo .bat) recriaria o diário: confere o mutex logo antes
        # de apagar, depois do "Sim" (seção 18.6).
        codigo = self.iss.split("[Code]", 1)[1]
        mutex = codigo.index("if CheckForMutexes('{#AppMutexNome}') then")
        self.assertLess(codigo.index("<> IDYES then\n    Exit;"), mutex)
        self.assertLess(mutex, codigo.index("DelTree("))
        bloco = codigo[mutex:codigo.index("DelTree(")]
        self.assertIn("Exit;", bloco)
        self.assertIn("NÃO foi apagado", bloco)

    def test_atualizacao_so_apaga_o_internal_do_proprio_entre_nos(self):
        # A pasta é escolhida na instalação: na de outro programa do PyInstaller, o _internal é dele.
        self.assertRegex(self.iss, r'Type: filesandordirs; Name: "\{app\}\\_internal"; Check: PastaJaEraDoEntreNos')
        codigo = self.iss.split("[Code]", 1)[1]
        self.assertIn("function PastaJaEraDoEntreNos: Boolean;", codigo)
        self.assertIn("FileExists(ExpandConstant('{app}\\{#AppExe}'))", codigo)

    def test_spec(self):
        for trecho in ("console=False", "upx=False", 'name="EntreNos"', "exclude_binaries=True",
                       'datas=[(os.path.join(RAIZ, "web"), "web")]', "icon=[ICONE]"):
            self.assertIn(trecho, self.spec)
        excluir = re.search(r"EXCLUIR = \[(.*?)\]", self.spec, re.DOTALL).group(1)
        for pacote in ("tkinter", "numpy", "pandas", "PIL"):
            self.assertIn(f'"{pacote}"', excluir)
        for necessario in ("cryptography", "rapidfuzz", "sqlite3", "email", "http", "json", "ctypes"):
            self.assertNotIn(f'"{necessario}"', excluir)

    def test_icone(self):
        icone = (INSTALADOR / "entrenos.ico").read_bytes()
        reservado, tipo, quantos = struct.unpack_from("<HHH", icone, 0)
        self.assertEqual((reservado, tipo), (0, 1))
        lados = {icone[6 + 16 * i] or 256 for i in range(quantos)}
        self.assertTrue({16, 32, 48, 256} <= lados, lados)
        self.assertTrue((INSTALADOR / "gerar_icone.py").is_file())

    def test_script_de_construcao(self):
        caminho = RAIZ / "construir_instalador.ps1"
        self.assertTrue(caminho.read_bytes().startswith(b"\xef\xbb\xbf"),
                        "o PowerShell 5.1 só lê acentos em .ps1 com BOM")
        script = _ler(caminho)
        for trecho in ("'unittest', 'discover', '-s', 'testes', '-t', '.'", "'--distpath'", "'--workpath'",
                       "build\\pyinstaller", "build\\trabalho", "EntreNos-Setup-$versao.exe", "/DAppVersao=$versao"):
            self.assertIn(trecho, script)

    def test_gitignore_e_readme(self):
        regras = {l.strip() for l in _ler(RAIZ / ".gitignore").splitlines()}
        self.assertTrue({"build/", "dist/"} <= regras)
        readme = _ler(RAIZ / "README.md")
        self.assertIn("## Instalar em outra máquina", readme)
        for trecho in ("Mais informações", "Executar assim mesmo", "EntreNos-Setup-", "%LOCALAPPDATA%\\EntreNos"):
            self.assertIn(trecho, readme)


if __name__ == "__main__":
    unittest.main()
