"""Gera os ícones PNG do app no celular (PWA, DESIGN.md seção 21.1) a partir do mesmo desenho do
ícone do instalador (`gerar_icone.desenhar`): o coração azul-noite com o coração coral dentro.

Só para quem mexe no desenho (precisa do Pillow; o app não usa). Os PNG ficam guardados em
web/icones/, então só é preciso rodar de novo para mudar o desenho:

    python instalador/gerar_icones_web.py

- icone-192.png e icone-512.png: fundo transparente ("any" no manifest);
- icone-maskable-192.png e icone-maskable-512.png: fundo creme cheio e o coração dentro da zona
  segura (o círculo de 80% do meio), porque o Android recorta o ícone em círculo, gota ou quadrado;
- apple-touch-icon.png (180): fundo creme sem transparência (o iPhone pinta o transparente de
  preto e arredonda os cantos sozinho).
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gerar_icone import desenhar  # noqa: E402

CREME = (0xFB, 0xF7, 0xF2, 255)          # --fundo da interface (web/estilo.css)
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO = os.path.join(RAIZ, "web", "icones")
ESCALA_MASKABLE = 0.60                   # o coração cabe no círculo de 80% (zona segura do maskable)
ESCALA_APPLE = 0.80


def com_fundo(tamanho: int, escala: float) -> Image.Image:
    """O coração em `escala` do lado, centrado num quadrado creme sem transparência."""
    lado = round(tamanho * escala)
    fundo = Image.new("RGBA", (tamanho, tamanho), CREME)
    coracao = desenhar(lado)
    fundo.alpha_composite(coracao, ((tamanho - lado) // 2, (tamanho - lado) // 2))
    return fundo.convert("RGB")


def gerar(destino: str = DESTINO) -> list[str]:
    os.makedirs(destino, exist_ok=True)
    imagens = {
        "icone-192.png": desenhar(192),
        "icone-512.png": desenhar(512),
        "icone-maskable-192.png": com_fundo(192, ESCALA_MASKABLE),
        "icone-maskable-512.png": com_fundo(512, ESCALA_MASKABLE),
        "apple-touch-icon.png": com_fundo(180, ESCALA_APPLE),
    }
    gravados = []
    for nome, imagem in imagens.items():
        caminho = os.path.join(destino, nome)
        imagem.save(caminho, format="PNG", optimize=True)
        gravados.append(caminho)
    return gravados


def main() -> int:
    leitor = argparse.ArgumentParser(description="Gera os ícones PNG do Entre Nós para o celular.")
    leitor.add_argument("--destino", default=DESTINO, help="pasta dos PNG (padrão web/icones)")
    args = leitor.parse_args()
    for caminho in gerar(args.destino):
        print(f"{caminho} ({os.path.getsize(caminho)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
