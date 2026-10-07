"""Abre o Entre Nós: servidor local em 127.0.0.1 e o navegador.

Uso: python iniciar.py [--sem-navegador] [--porta N] [--dados PASTA]
"""
import os
import sys

# os.path.abspath, e não Path.resolve(): não troca letra de unidade pelo caminho de rede.
RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from entrenos.servidor import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
