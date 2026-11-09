"""Gera assets/jarvis.ico — reator arc em ciano neon (multi-resolução para a barra de tarefas)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 512
OUT = Path(__file__).resolve().parent.parent / "assets" / "jarvis.ico"


def ring(d, r, w, color):
    c = S / 2
    d.ellipse([c - r, c - r, c + r, c + r], outline=color, width=w)


def reactor() -> Image.Image:
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = S / 2

    # fundo: disco escuro
    bg = ImageDraw.Draw(base)
    bg.ellipse([8, 8, S - 8, S - 8], fill=(4, 14, 20, 255), outline=(0, 120, 150, 255), width=10)

    # brilho (camada desfocada)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([c - 150, c - 150, c + 150, c + 150], fill=(0, 229, 255, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    base = Image.alpha_composite(base, glow)

    d = ImageDraw.Draw(base)
    ring(d, 200, 14, (0, 229, 255, 255))
    # 10 segmentos (bobinas) entre os anéis
    for i in range(10):
        a0 = math.radians(i * 36 + 4)
        a1 = math.radians(i * 36 + 30)
        pts = []
        for r, a in ((178, a0), (178, a1), (120, a1), (120, a0)):
            pts.append((c + r * math.cos(a), c + r * math.sin(a)))
        d.polygon(pts, fill=(0, 190, 230, 255), outline=(150, 250, 255, 255))
    ring(d, 112, 10, (120, 245, 255, 255))
    # núcleo
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    k = ImageDraw.Draw(core)
    k.ellipse([c - 95, c - 95, c + 95, c + 95], fill=(0, 229, 255, 230))
    core = core.filter(ImageFilter.GaussianBlur(18))
    base = Image.alpha_composite(base, core)
    d = ImageDraw.Draw(base)
    d.ellipse([c - 62, c - 62, c + 62, c + 62], fill=(235, 255, 255, 255))
    # triângulo estilizado (Mark VI)
    tri = [(c, c - 48), (c + 42, c + 26), (c - 42, c + 26)]
    d.polygon(tri, outline=(0, 140, 190, 255), width=8)
    return base


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    img = reactor()
    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print("ícone salvo:", OUT)
