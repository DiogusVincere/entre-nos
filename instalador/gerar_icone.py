"""Gera instalador/entrenos.ico: coração azul-noite com um coração coral dentro.

Só para construir o instalador (precisa do Pillow; o app não usa). O .ico fica guardado no
projeto, então só é preciso rodar de novo para mudar o desenho:

    python instalador/gerar_icone.py [--previa ARQUIVO.png]

Tamanhos 16, 20, 24, 32, 40, 48, 64, 128 e 256. Cada um é desenhado grande e reduzido (bordas
suaves). Até 128 px as imagens vão como bitmap de 32 bits (com a máscara de transparência) e a
de 256 como PNG, o mesmo formato dos ícones do próprio Windows: funciona no Explorer, no menu
Iniciar, no PyInstaller e no Inno Setup.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import struct

from PIL import Image, ImageChops, ImageDraw

AZUL_NOITE = (0x3D, 0x5A, 0x80, 255)     # primária da interface (DESIGN.md, seção 16)
CORAL = (0xE0, 0x7A, 0x5F, 255)          # destaque da interface
TAMANHOS = (16, 20, 24, 32, 40, 48, 64, 128, 256)
GRANDE = 1024                            # desenha nesta escala e reduz
PASTA = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(PASTA, "entrenos.ico")


def _contorno_coracao(cx: float, cy: float, largura: float, pontos: int = 720) -> list[tuple[float, float]]:
    """Curva clássica do coração, com largura `largura` e o centro da caixa em (cx, cy)."""
    brutos = []
    for i in range(pontos):
        t = 2 * math.pi * i / pontos
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        brutos.append((x, -y))                       # na tela o y cresce para baixo
    xs = [p[0] for p in brutos]
    ys = [p[1] for p in brutos]
    escala = largura / (max(xs) - min(xs))
    meio_x, meio_y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [(cx + (x - meio_x) * escala, cy + (y - meio_y) * escala) for x, y in brutos]


def _mascara(contorno: list[tuple[float, float]]) -> Image.Image:
    m = Image.new("L", (GRANDE, GRANDE), 0)
    ImageDraw.Draw(m).polygon(contorno, fill=255)
    return m


def desenhar(tamanho: int) -> Image.Image:
    """O ícone em `tamanho` x `tamanho`, RGBA com fundo transparente."""
    s = GRANDE
    # Nos tamanhos pequenos o coração de dentro fica maior, para não virar um ponto.
    dentro_largura = 0.40 if tamanho >= 32 else 0.46
    fora = _mascara(_contorno_coracao(s * 0.5, s * 0.5, s * 0.96))
    dentro = _mascara(_contorno_coracao(s * 0.5, s * 0.47, s * dentro_largura))
    # Um respiro transparente entre os dois corações.
    folga = _mascara(_contorno_coracao(s * 0.5, s * 0.47, s * (dentro_largura + 0.09)))
    azul = ImageChops.subtract(fora, folga)

    imagem = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    imagem.paste(Image.new("RGBA", (s, s), AZUL_NOITE), (0, 0), azul)
    imagem.paste(Image.new("RGBA", (s, s), CORAL), (0, 0), dentro)
    return imagem.resize((tamanho, tamanho), Image.Resampling.LANCZOS)


def _dib(imagem: Image.Image) -> bytes:
    """Bitmap de ícone: cabeçalho com altura dobrada, BGRA de baixo para cima e máscara AND."""
    largura, altura = imagem.size
    invertida = imagem.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    cores = invertida.tobytes("raw", "BGRA")
    alfa = invertida.getchannel("A").tobytes()
    linha_mascara = ((largura + 31) // 32) * 4           # cada linha da máscara fecha em 4 bytes
    mascara = bytearray(linha_mascara * altura)
    for y in range(altura):
        for x in range(largura):
            if alfa[y * largura + x] == 0:                # 1 = transparente
                mascara[y * linha_mascara + x // 8] |= 0x80 >> (x % 8)
    cabecalho = struct.pack("<IiiHHIIiiII", 40, largura, altura * 2, 1, 32, 0,
                            len(cores) + len(mascara), 0, 0, 0, 0)
    return cabecalho + cores + bytes(mascara)


def _png(imagem: Image.Image) -> bytes:
    saida = io.BytesIO()
    imagem.save(saida, format="PNG", optimize=True)
    return saida.getvalue()


def montar_ico(imagens: dict[int, Image.Image]) -> bytes:
    """Um .ico com todas as imagens (bitmap até 128 px, PNG no de 256)."""
    partes = []
    for tamanho in sorted(imagens):
        imagem = imagens[tamanho]
        conteudo = _png(imagem) if tamanho >= 256 else _dib(imagem)
        lado = tamanho if tamanho < 256 else 0              # 0 quer dizer 256
        partes.append((struct.pack("<BBBBHH", lado, lado, 0, 0, 1, 32), conteudo))
    deslocamento = 6 + 16 * len(partes)
    diretorio, dados = b"", b""
    for inicio, conteudo in partes:
        diretorio += inicio + struct.pack("<II", len(conteudo), deslocamento + len(dados))
        dados += conteudo
    return struct.pack("<HHH", 0, 1, len(partes)) + diretorio + dados


def main() -> int:
    leitor = argparse.ArgumentParser(description="Gera o ícone do Entre Nós.")
    leitor.add_argument("--destino", default=DESTINO, help="onde gravar o .ico")
    leitor.add_argument("--previa", default=None, help="também grava um PNG com todos os tamanhos lado a lado")
    args = leitor.parse_args()

    imagens = {t: desenhar(t) for t in TAMANHOS}
    conteudo = montar_ico(imagens)
    with open(args.destino, "wb") as arquivo:
        arquivo.write(conteudo)
    print(f"{args.destino} ({len(conteudo)} bytes; tamanhos {', '.join(map(str, TAMANHOS))})")

    if args.previa:
        largura = sum(TAMANHOS) + 12 * (len(TAMANHOS) + 1)
        folha = Image.new("RGBA", (largura, 256 + 24), (0xFB, 0xF7, 0xF2, 255))   # fundo creme da interface
        x = 12
        for t in TAMANHOS:
            folha.alpha_composite(imagens[t], (x, 12 + (256 - t)))
            x += t + 12
        folha.save(args.previa)
        print(args.previa)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
