#!/usr/bin/env python3
"""Zeichnet die Erweiterungs-Symbole (Pfeil «nach draussen» auf blau-grünem Grund)."""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "extension", "icons")
S = 512  # gross zeichnen, dann verkleinern


def gradient():
    img = Image.new("RGBA", (S, S))
    top, bottom = (0, 120, 212), (31, 190, 140)  # Outlook-Blau → Edge-Grün
    px = img.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
    return img


def main():
    bg = gradient()
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((16, 16, S - 16, S - 16), radius=110, fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    w = 46
    white = (255, 255, 255, 255)
    # Kasten (offen nach oben rechts)
    d.line([(300, 150), (140, 150), (140, 372), (362, 372), (362, 212)], fill=white, width=w, joint="curve")
    for x, y in [(300, 150), (140, 150), (140, 372), (362, 372), (362, 212)]:
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=white)
    # Pfeil nach oben rechts
    d.line([(228, 284), (392, 120)], fill=white, width=w)
    d.polygon([(410, 102), (410, 252), (260, 102)], fill=white)

    os.makedirs(OUT, exist_ok=True)
    for size in (16, 32, 48, 128):
        icon.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, f"icon{size}.png"))


if __name__ == "__main__":
    main()
