# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: o Entre Nós numa pasta com EntreNos.exe (DESIGN.md, seção 18.3).

Não rode direto: use construir_instalador.ps1 na raiz, que roda os testes, este arquivo e o
Inno Setup. Se precisar rodar só esta parte (da raiz do projeto):

    python -m PyInstaller --noconfirm --clean --distpath build\\pyinstaller --workpath build\\trabalho instalador\\entrenos.spec

- pasta única (onedir): EntreNos.exe + _internal\\ (Python, bibliotecas e web\\);
- sem console (abre só o navegador); sem UPX (antivírus desconfiam de executável comprimido);
- entra só o que o app usa: biblioteca padrão, cryptography e rapidfuzz. Tkinter, numpy, pandas,
  Pillow e o resto que estiver instalado neste Python ficam de fora.
"""
import os
import re

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

PASTA = os.path.abspath(SPECPATH)                 # instalador\
RAIZ = os.path.dirname(PASTA)                     # raiz do projeto
ICONE = os.path.join(PASTA, "entrenos.ico")

# Nada que o app não usa (alguns vêm junto só porque estão instalados neste Python).
EXCLUIR = [
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib",
    "numpy", "pandas", "PIL", "matplotlib", "scipy", "pyarrow", "streamlit", "altair",
    "openpyxl", "xlrd", "xlwt", "lxml", "pdfplumber", "pdfminer", "pypdfium2",
    "IPython", "jupyter", "jupyter_client", "jupyter_core", "notebook", "ipykernel",
    "PyInstaller", "setuptools", "pkg_resources", "pip", "wheel", "distutils",
    "ensurepip", "venv", "lib2to3", "pydoc", "pydoc_data", "doctest", "unittest", "test",
    "curses", "readline", "win32com", "pythoncom", "pywintypes",
]


def _versao() -> str:
    with open(os.path.join(RAIZ, "entrenos", "config.py"), encoding="utf-8") as arquivo:
        achado = re.search(r'^VERSAO\s*=\s*"(\d+\.\d+\.\d+)"', arquivo.read(), re.MULTILINE)
    if not achado:
        raise SystemExit("Não achei VERSAO = \"x.y.z\" em entrenos\\config.py")
    return achado.group(1)


def _modulos_do_app() -> list[str]:
    """Todos os módulos de entrenos/: o servidor importa alguns só na hora do uso (importlib)."""
    nomes = []
    for pasta, _, arquivos in os.walk(os.path.join(RAIZ, "entrenos")):
        if "__pycache__" in pasta:
            continue
        pacote = os.path.relpath(pasta, RAIZ).replace(os.sep, ".")
        for nome in arquivos:
            if nome.endswith(".py") and nome != "__main__.py":
                nomes.append(pacote if nome == "__init__.py" else f"{pacote}.{nome[:-3]}")
    return sorted(nomes)


VERSAO = _versao()
_numeros = tuple(int(n) for n in VERSAO.split(".")) + (0,)
INFO_VERSAO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numeros, prodvers=_numeros, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("041604B0", [
            StringStruct("CompanyName", "Entre Nós"),
            StringStruct("FileDescription", "Entre Nós"),
            StringStruct("FileVersion", VERSAO),
            StringStruct("InternalName", "EntreNos"),
            StringStruct("OriginalFilename", "EntreNos.exe"),
            StringStruct("ProductName", "Entre Nós"),
            StringStruct("ProductVersion", VERSAO),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0416, 1200])]),
    ],
)

a = Analysis(
    [os.path.join(RAIZ, "iniciar.py")],
    pathex=[RAIZ],
    binaries=[],
    datas=[(os.path.join(RAIZ, "web"), "web")],
    hiddenimports=_modulos_do_app() + ["rapidfuzz", "rapidfuzz.fuzz"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUIR,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EntreNos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICONE],
    version=INFO_VERSAO,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EntreNos",
)
