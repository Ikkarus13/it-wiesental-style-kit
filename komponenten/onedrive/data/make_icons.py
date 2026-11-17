#!/usr/bin/env python3
"""Erzeugt die SVG-Symbole (Tray, Embleme, App-Symbol) in data/icons/hicolor."""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons", "hicolor", "scalable")

import json

# Farben aus der Marke (onedrive_mint/branding.json) – bewusst nicht das OneDrive-Blau
_B = {}
for _p in (os.path.join(HERE, "..", "onedrive_mint", "branding.json"),
           os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "onedrive-mint", "branding.json")):
    try:
        with open(_p, encoding="utf-8") as _f:
            _B.update(json.load(_f))
    except (OSError, ValueError):
        pass
BLUE = _B.get("farbe", "#0087CB")
LIGHT_BLUE = _B.get("farbe_hell", "#4FB3E3")
GREEN = "#13A10E"
GRAY = "#8A8A8A"
RED = "#D13438"
YELLOW = "#F7B500"

CLOUD = ("M7 19h10.5a4.25 4.25 0 0 0 .55-8.46A6.25 6.25 0 0 0 6.2 9.06 "
         "5 5 0 0 0 7 19z")


# Programmzeichen: zwei gegenläufige Pfeile (Synchronisieren) – eigenes Motiv, keine Wolke
def sync_glyph(color, sw=2.3, cx=12, cy=12, r=6.2):
    return (f'<g fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round">'
            f'<path d="M{cx - r:.2f} {cy - 0.6:.2f}A{r} {r} 0 0 1 {cx + r * 0.72:.2f} {cy - r * 0.68:.2f}"/>'
            f'<path d="M{cx + r:.2f} {cy + 0.6:.2f}A{r} {r} 0 0 1 {cx - r * 0.72:.2f} {cy + r * 0.68:.2f}"/>'
            '</g>'
            f'<path d="M{cx + r * 1.12:.2f} {cy - r * 1.15:.2f}v{r * 0.95:.2f}h-{r * 0.95:.2f}z" fill="{color}"/>'
            f'<path d="M{cx - r * 1.12:.2f} {cy + r * 1.15:.2f}v-{r * 0.95:.2f}h{r * 0.95:.2f}z" fill="{color}"/>')


def tray_mark(fill):
    """Leistensymbol: gefüllter Kreis in Markenfarbe mit weissen Pfeilen."""
    return (f'<circle cx="11" cy="11" r="9.5" fill="{fill}"/>'
            + sync_glyph("#ffffff", sw=1.9, cx=11, cy=11, r=4.8))


def svg(body, size=24):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 24 24">{body}</svg>\n')


def cloud(fill, stroke="#ffffff", sw=1.4):
    return f'<path d="{CLOUD}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" ' \
           'stroke-linejoin="round"/>'


def badge(color, inner):
    return (f'<circle cx="18" cy="18" r="5.6" fill="{color}" stroke="#ffffff" stroke-width="1.2"/>'
            + inner)


SYNC_ARROWS = ('<g fill="none" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round">'
               '<path d="M15.4 17.2a2.8 2.8 0 0 1 4.9-1.2"/><path d="M20.6 18.8a2.8 2.8 0 0 1-4.9 1.2"/>'
               '</g><path d="M20.9 14.6v2.2h-2.2z" fill="#ffffff"/>'
               '<path d="M15.1 21.4v-2.2h2.2z" fill="#ffffff"/>')
PAUSE = '<rect x="16.2" y="15.6" width="1.4" height="4.8" fill="#fff"/>' \
        '<rect x="18.4" y="15.6" width="1.4" height="4.8" fill="#fff"/>'
CROSS = '<path d="M16 16l4 4M20 16l-4 4" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>'
BANG = '<rect x="17.3" y="15" width="1.4" height="3.8" fill="#fff"/>' \
       '<rect x="17.3" y="19.6" width="1.4" height="1.4" fill="#fff"/>'
CHECK = '<path d="M15.6 18.1l1.7 1.7 3.2-3.4" fill="none" stroke="{c}" stroke-width="1.6" ' \
        'stroke-linecap="round" stroke-linejoin="round"/>'

# ------------------------------------------------------------------ Leistensymbole
# Symbolische Symbole (Name endet auf -symbolic): Cinnamon/GTK färben sie in der Textfarbe
# der Leiste ein – sichtbar auf heller wie dunkler Leiste. GTK färbt nur «fill», deshalb sind
# alle Linien als Flächen gebaut (keine strokes). Klassen success/warning/error ergeben die
# Theme-Farben für die kleinen Zusatzzeichen.
# Ruhezustand = Wolke mit Haken; Pfeile/Zeichen nur, wenn wirklich etwas los ist.

import math

_CX, _CY = 11.6, 13.0          # Mitte der Wolke (für den inneren Rand)


# Die Wolke ist breit und flach – auf volle Breite ziehen, damit sie in der Leiste so gross
# wirkt wie andere Symbole. Direkt in die Koordinaten gerechnet: der Symbol-Lader von GTK
# wertet «transform» nicht zuverlässig aus.
GROSS = 1.17


def _T(x, y):
    return 12 + (x - 12) * GROSS, 12.4 + (y - 12.15) * GROSS


def _cloud_path(sx=1.0, sy=1.0):
    """Wolkenumriss (wie CLOUD), um die Mitte skaliert und vergrössert."""
    def P(x, y):
        tx, ty = _T(_CX + (x - _CX) * sx, _CY + (y - _CY) * sy)
        return f"{tx:.3f} {ty:.3f}"
    def A(rx, ry, x, y, large=0, sweep=0):
        return f"A{rx * sx * GROSS:.3f} {ry * sy * GROSS:.3f} 0 {large} {sweep} {P(x, y)}"
    return (f"M{P(7, 19)}L{P(17.5, 19)}"
            f"{A(4.25, 4.25, 18.05, 10.54)}"
            f"{A(6.25, 6.25, 6.2, 9.06)}"
            f"{A(5, 5, 7, 19)}Z")


def _ring():
    """Wolke als Rand (Fläche mit Loch, ca. 1,7 px breit)."""
    return (f'<path fill-rule="evenodd" d="{_cloud_path()} {_cloud_path(0.77, 0.65)}"/>')


def _linie(punkte, w):
    """Polylinie mit runden Enden als Flächen (Rechtecke + Kreise)."""
    teile = []
    for (x1, y1), (x2, y2) in zip(punkte, punkte[1:]):
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1
        ox, oy = -dy / n * w / 2, dx / n * w / 2
        teile.append(f'<path d="M{x1 + ox:.3f} {y1 + oy:.3f}L{x2 + ox:.3f} {y2 + oy:.3f}'
                     f'L{x2 - ox:.3f} {y2 - oy:.3f}L{x1 - ox:.3f} {y1 - oy:.3f}Z"/>')
    for x, y in punkte:
        teile.append(f'<circle cx="{x}" cy="{y}" r="{w / 2}"/>')
    return "".join(teile)


def _haken():
    return _linie([_T(9.0, 14.0), _T(11.0, 16.0), _T(14.8, 12.0)], 2.0 * GROSS)


BX, BY, BR = 18.3, 18.3, 5.6   # Zusatzzeichen unten rechts


def _ausgeschnitten(inhalt):
    """Wolke dort ausschneiden, wo das Zusatzzeichen sitzt (Abstand 1 px)."""
    r = BR + 1.1
    return ('<defs><clipPath id="frei"><path clip-rule="evenodd" d="M-1 -1H25V25H-1Z '
            f'M{BX - r} {BY}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0Z"/></clipPath></defs>'
            f'<g clip-path="url(#frei)">{inhalt}</g>')


def _zeichen(loecher, klasse=""):
    """Kreis mit ausgestanzten Formen (evenodd)."""
    k = f' class="{klasse}"' if klasse else ""
    kreis = (f"M{BX - BR} {BY}a{BR} {BR} 0 1 0 {2 * BR} 0a{BR} {BR} 0 1 0 {-2 * BR} 0Z")
    return f'<path{k} fill-rule="evenodd" d="{kreis} {loecher}"/>'


def _rechteck(x, y, w, h):
    return f"M{x} {y}h{w}v{h}h{-w}Z"


PFEILE = (f"M{BX - 2.6} {BY + 2.4}L{BX - 2.6} {BY - 0.6}L{BX - 3.9} {BY - 0.6}"
          f"L{BX - 1.85} {BY - 3.0}L{BX + 0.2} {BY - 0.6}L{BX - 1.1} {BY - 0.6}L{BX - 1.1} {BY + 2.4}Z "
          f"M{BX + 1.1} {BY - 2.4}L{BX + 1.1} {BY + 0.6}L{BX - 0.2} {BY + 0.6}"
          f"L{BX + 1.85} {BY + 3.0}L{BX + 3.9} {BY + 0.6}L{BX + 2.6} {BY + 0.6}L{BX + 2.6} {BY - 2.4}Z")
PAUSE_L = _rechteck(BX - 2.3, BY - 2.4, 1.6, 4.8) + " " + _rechteck(BX + 0.7, BY - 2.4, 1.6, 4.8)
AUSRUF = _rechteck(BX - 0.8, BY - 3.1, 1.6, 3.8) + " " + _rechteck(BX - 0.8, BY + 1.5, 1.6, 1.6)


def _kreuz():
    a, b = 0.85, 2.9           # halbe Balkenbreite, halbe Länge, um 45° gedreht
    pts = [(-a, -b), (a, -b), (a, -a), (b, -a), (b, a), (a, a), (a, b), (-a, b),
           (-a, a), (-b, a), (-b, -a), (-a, -a)]
    c = math.sqrt(0.5)
    rot = [(BX + (x - y) * c, BY + (x + y) * c) for x, y in pts]
    return "M" + "L".join(f"{x:.3f} {y:.3f}" for x, y in rot) + "Z"


WOLKE = _ring()
TRAY = {
    "odm-tray-ok-symbolic": _ring() + _haken(),
    "odm-tray-sync-symbolic": _ausgeschnitten(WOLKE) + _zeichen(PFEILE),
    "odm-tray-paused-symbolic": _ausgeschnitten(WOLKE) + _zeichen(PAUSE_L),
    "odm-tray-error-symbolic": _ausgeschnitten(WOLKE) + _zeichen(_kreuz(), "error"),
    "odm-tray-warning-symbolic": _ausgeschnitten(WOLKE) + _zeichen(AUSRUF, "warning"),
    "odm-tray-offline-symbolic": f'<g opacity="0.55">{WOLKE}</g>'
                                 + _linie([(3.0, 3.5), (21.0, 21.5)], 1.9),
    "odm-tray-setup-symbolic": f'<g opacity="0.55">{WOLKE}</g>',
}

# Embleme werden klein unten links ueber das Dateisymbol gelegt -> kraeftige Formen
EMBLEMS = {
    "emblem-odm-cloud": '<circle cx="12" cy="12" r="11" fill="#fff"/>'
                        f'<path d="M7.6 16.5h8.2a3.2 3.2 0 0 0 .4-6.37A4.7 4.7 0 0 0 7.3 9.1 '
                        f'3.75 3.75 0 0 0 7.6 16.5z" fill="none" stroke="{BLUE}" stroke-width="1.8" '
                        'stroke-linejoin="round"/>',
    "emblem-odm-local": f'<circle cx="12" cy="12" r="10" fill="#fff" stroke="{GREEN}" '
                        'stroke-width="2"/>'
                        f'<path d="M7.5 12.3l3 3 6-6.3" fill="none" stroke="{GREEN}" '
                        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "emblem-odm-pinned": f'<circle cx="12" cy="12" r="11" fill="{GREEN}" stroke="#fff" '
                         'stroke-width="1"/>'
                         '<path d="M7.5 12.3l3 3 6-6.3" fill="none" stroke="#fff" '
                         'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    "emblem-odm-sync": f'<circle cx="12" cy="12" r="11" fill="{LIGHT_BLUE}" stroke="#fff"/>'
                       '<g fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round">'
                       '<path d="M6.8 10.8a5.4 5.4 0 0 1 9.6-2.3"/>'
                       '<path d="M17.2 13.2a5.4 5.4 0 0 1-9.6 2.3"/></g>'
                       '<path d="M17.6 5.2v4.4h-4.4z" fill="#fff"/>'
                       '<path d="M6.4 18.8v-4.4h4.4z" fill="#fff"/>',
    "emblem-odm-error": f'<circle cx="12" cy="12" r="11" fill="{RED}" stroke="#fff"/>'
                        '<path d="M8 8l8 8M16 8l-8 8" stroke="#fff" stroke-width="2.4" '
                        'stroke-linecap="round"/>',
}

APP = {
    # Firmenordner (~/Firma) fuer Teams-/SharePoint-Bibliotheken – Gebaeude wie unter Windows
    "odm-org": f'<rect x="4" y="3" width="11" height="19" rx="1" fill="{BLUE}"/>'
               f'<rect x="15" y="9" width="6" height="13" rx="1" fill="{LIGHT_BLUE}"/>'
               + "".join(f'<rect x="{x}" y="{y}" width="2" height="2" fill="#fff"/>'
                         for x in (6.5, 10.5) for y in (6, 10, 14))
               + '<rect x="8.5" y="18" width="2.5" height="4" fill="#fff"/>'
               + "".join(f'<rect x="17" y="{y}" width="2" height="2" fill="#fff"/>'
                         for y in (12, 16)),
    "onedrive-mint": f'<rect x="1" y="1" width="22" height="22" rx="5" fill="{BLUE}"/>'
                     + sync_glyph("#ffffff"),
    "odm-folder": f'<path d="M2 6.5A1.5 1.5 0 0 1 3.5 5h5l2 2h10A1.5 1.5 0 0 1 22 8.5v10A1.5 '
                  f'1.5 0 0 1 20.5 20h-17A1.5 1.5 0 0 1 2 18.5z" fill="{LIGHT_BLUE}"/>'
                  '<path d="M8.5 17h7.2a2.6 2.6 0 0 0 .35-5.18A3.9 3.9 0 0 0 8.6 11a3.1 3.1 0 0 '
                  '0-.1 6z" fill="#fff"/>',
}


def write(sub, name, body, size=24):
    d = os.path.join(OUT, sub)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".svg"), "w") as f:
        f.write(svg(body, size))


if __name__ == "__main__":
    for n, b in TRAY.items():
        write("status", n, b)
        # farbige Fassungen in Leistengrösse: weiss (dunkle Leiste) und schwarz (helle Leiste)
        for farbe, fg in (("weiss", "#ffffff"), ("schwarz", "#1f1f1f")):
            bunt = (b.replace(' class="error"', ' fill="#D13438"')
                     .replace(' class="warning"', ' fill="#E8A317"'))
            write("status", n.replace("-symbolic", f"-{farbe}"), f'<g fill="{fg}">{bunt}</g>')
    # alte, farbige Leistensymbole (bis 2.2.0) entfernen
    for alt in ("ok", "sync", "paused", "error", "warning", "offline", "setup"):
        try:
            os.unlink(os.path.join(OUT, "status", f"odm-tray-{alt}.svg"))
        except OSError:
            pass
    for n, b in EMBLEMS.items():
        write("emblems", n, b)
    for n, b in APP.items():
        write("apps", n, b, 48)
    print("Symbole erzeugt in", OUT)
