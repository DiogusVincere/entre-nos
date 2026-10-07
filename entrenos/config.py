"""Caminhos e constantes do app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

VERSAO = "0.2.1"                      # o instalador (instalador/EntreNos.iss) usa esta versão
NOME_APP = "Entre Nós"
PORTA_PADRAO = 8787
ARQUIVO_COFRE = "entrenos.cofre"      # banco SQLite serializado e criptografado
PASTA_BACKUPS = "backups"             # cópias automáticas (também criptografadas)
BACKUPS_AUTOMATICOS = 5               # quantas versões antigas manter
MINUTOS_BLOQUEIO_PADRAO = 10
ANTECEDENCIAS_PADRAO = [30, 15, 7, 3, 1]
PIN_MINIMO = 6


def empacotado() -> bool:
    """True no programa instalado (PyInstaller, `sys.frozen`); False rodando pelo iniciar.py."""
    return bool(getattr(sys, "frozen", False))


def _raiz() -> Path:
    """Onde fica a pasta web/.

    Empacotado, os arquivos vêm de dentro do pacote (`sys._MEIPASS`, a pasta `_internal` ao lado
    do EntreNos.exe). Pelo código, é a pasta acima de `entrenos/`. os.path.abspath, e não
    Path.resolve(), para não trocar letra de unidade mapeada pelo caminho de rede.
    """
    if empacotado():
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return Path(os.path.abspath(base))
    return Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))


RAIZ = _raiz()
PASTA_WEB = RAIZ / "web"


def pasta_dados() -> Path:
    """Onde ficam os dados. NUNCA dentro da pasta do código (que vai para o GitHub).

    Ordem: variável ENTRENOS_DADOS (testes) > %LOCALAPPDATA%\\EntreNos > ~/.entrenos.
    """
    if os.environ.get("ENTRENOS_DADOS"):
        p = Path(os.environ["ENTRENOS_DADOS"])
    elif os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "EntreNos"
    else:
        p = Path.home() / ".entrenos"
    p.mkdir(parents=True, exist_ok=True)
    return p
