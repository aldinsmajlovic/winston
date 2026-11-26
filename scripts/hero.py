"""Compose docs/images/hero-{light,dark}.png from the History and wall display shots."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

IMAGES = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "docs" / "images"
W, H = 1600, 940


def rounded(im, r):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *im.size), r, fill=255)
    im = im.convert("RGBA")
    im.putalpha(mask)
    return im


def shadow(canvas, box, r, blur, alpha):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, r, fill=(0, 0, 0, alpha))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def hero(theme):
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    border = (210, 208, 203) if theme == "light" else (58, 57, 54)

    # The window: History, cropped to its column.
    page = Image.open(IMAGES / f"history-{theme}.png").convert("RGB").crop((280, 0, 1120, 860))
    pw, ph = page.size
    x, y = 40, 40
    shadow(canvas, (x, y + 16, x + pw, y + ph + 16), 14, 28, 70)
    frame = Image.new("RGB", (pw + 2, ph + 2), border)
    frame.paste(page, (1, 1))
    canvas.alpha_composite(rounded(frame, 14), (x - 1, y - 1))

    # The tablet: the wall display in a bezel, in front, bottom right.
    wall = Image.open(IMAGES / f"wall-{theme}.png").convert("RGB").resize((660, 405), Image.LANCZOS)
    bezel = 22
    tw, th = 660 + 2 * bezel, 405 + 2 * bezel
    tx, ty = W - tw - 40, H - th - 70
    shadow(canvas, (tx, ty + 20, tx + tw, ty + th + 20), 34, 30, 110)
    tablet = Image.new("RGB", (tw, th), (28, 28, 27))
    tablet.paste(rounded(wall, 8), (bezel, bezel), rounded(wall, 8))
    canvas.alpha_composite(rounded(tablet, 34), (tx, ty))

    canvas.save(IMAGES / f"hero-{theme}.png", optimize=True)


for t in ("light", "dark"):
    hero(t)
