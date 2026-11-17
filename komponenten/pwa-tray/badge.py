"""Tray-Symbol mit Status-Punkt (unten rechts) und Zaehler (oben rechts) zeichnen – wie Teams
unter Windows. Ergebnis ist eine PNG-Datei in ~/.cache/pwa-tray (Cinnamon muss sie lesen koennen).
"""
import math
import os

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

SIZE = 64
CACHE = os.path.expanduser("~/.cache/pwa-tray")

# Farben wie im Teams-Client
PRESENCE = {
    "available": ("#6bb700", "Verfügbar"),
    "busy": ("#c4314b", "Beschäftigt"),
    "dnd": ("#c4314b", "Nicht stören"),
    "away": ("#f8d22a", "Abwesend"),
    "ooo": ("#b4009e", "Außer Haus"),
    "offline": ("#8a8886", "Offline"),
}
BADGE_RED = "#c4314b"


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _load_base(icon):
    try:
        if os.path.isabs(icon):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(icon, SIZE, SIZE, True)
        return Gtk.IconTheme.get_default().load_icon(icon, SIZE, 0)
    except Exception:  # noqa: BLE001
        return None


def _presence_glyph(cr, code, cx, cy, r):
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(r * 0.32)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if code == "available":                     # Haken
        cr.move_to(cx - r * 0.45, cy + r * 0.02)
        cr.line_to(cx - r * 0.1, cy + r * 0.38)
        cr.line_to(cx + r * 0.5, cy - r * 0.35)
        cr.stroke()
    elif code == "dnd":                         # Strich
        cr.move_to(cx - r * 0.5, cy)
        cr.line_to(cx + r * 0.5, cy)
        cr.stroke()
    elif code == "away":                        # Uhr
        cr.move_to(cx, cy - r * 0.5)
        cr.line_to(cx, cy)
        cr.line_to(cx + r * 0.4, cy + r * 0.25)
        cr.stroke()
    elif code == "ooo":                         # Pfeil nach links
        cr.move_to(cx + r * 0.5, cy)
        cr.line_to(cx - r * 0.45, cy)
        cr.move_to(cx - r * 0.1, cy - r * 0.38)
        cr.line_to(cx - r * 0.48, cy)
        cr.line_to(cx - r * 0.1, cy + r * 0.38)
        cr.stroke()
    elif code == "offline":                     # x
        d = r * 0.35
        cr.move_to(cx - d, cy - d)
        cr.line_to(cx + d, cy + d)
        cr.move_to(cx - d, cy + d)
        cr.line_to(cx + d, cy - d)
        cr.stroke()


def render(icon, presence=None, unread=0, path=None):
    """Zeichnet das Symbol und liefert den Dateipfad (oder None bei Fehler)."""
    base = _load_base(icon)
    if base is None:
        return None
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    cr = cairo.Context(surf)
    Gdk.cairo_set_source_pixbuf(cr, base, (SIZE - base.get_width()) / 2,
                                (SIZE - base.get_height()) / 2)
    cr.paint()

    if presence in PRESENCE:
        color = _rgb(PRESENCE[presence][0])
        r = 13
        cx, cy = SIZE - r - 1, SIZE - r - 1
        # Rand ausstanzen, damit der Punkt auf jedem Hintergrund absetzt
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.arc(cx, cy, r + 3, 0, 2 * math.pi)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
        if presence == "offline":
            cr.set_source_rgb(*color)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.fill()
        else:
            cr.set_source_rgb(*color)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.fill()
        if presence != "busy":
            _presence_glyph(cr, presence, cx, cy, r)

    if unread and unread > 0:
        text = str(unread) if unread < 10 else "9+"
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(22)
        ext = cr.text_extents(text)
        h = 28
        w = max(h, ext.width + 14)
        x0, y0 = SIZE - w, 0
        cr.set_operator(cairo.OPERATOR_CLEAR)
        _pill(cr, x0 - 3, y0 - 3, w + 6, h + 6)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_rgb(*_rgb(BADGE_RED))
        _pill(cr, x0, y0, w, h)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x0 + (w - ext.width) / 2 - ext.x_bearing,
                   y0 + (h - ext.height) / 2 - ext.y_bearing)
        cr.show_text(text)

    os.makedirs(CACHE, exist_ok=True)
    path = path or os.path.join(CACHE, "icon.png")
    surf.write_to_png(path)
    return path


def _pill(cr, x, y, w, h):
    r = h / 2
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, math.pi / 2)
    cr.arc(x + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


def presence_text(code):
    return PRESENCE.get(code, (None, ""))[1]
