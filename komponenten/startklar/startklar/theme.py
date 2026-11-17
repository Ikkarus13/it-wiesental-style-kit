"""Aussehen: Windows-11-Farben (hell/dunkel), Schrift Selawik (metrisch wie
Segoe UI, OFL) und das CSS des Menüs."""
import ctypes
import glob
import os

from gi.repository import Gdk, Gtk

from . import ASSETS
from .config import get_config

FONT = "Selawik"

DARK = {
    "text": "#ffffff",
    "text2": "rgba(255,255,255,0.786)",
    "text3": "rgba(255,255,255,0.544)",
    "hover": "rgba(255,255,255,0.0605)",
    "pressed": "rgba(255,255,255,0.0419)",
    "ctrl": "rgba(255,255,255,0.0605)",
    "ctrl_hover": "rgba(255,255,255,0.0837)",
    "ctrl_border": "rgba(255,255,255,0.0698)",
    "border": (1.0, 1.0, 1.0, 0.10),
    "tint": (0.125, 0.125, 0.125),        # #202020
    "tint_alpha": 0.80,
    "solid": (0.141, 0.141, 0.141),       # ohne Acryl
    "footer": "rgba(0,0,0,0.20)",
    "footer_line": "rgba(255,255,255,0.06)",
    "search_bg": "rgba(30,30,30,0.70)",
    "search_border": "rgba(255,255,255,0.08)",
    "search_line": "rgba(255,255,255,0.54)",
    "accent": "#60cdff",
    "accent_fill": "#4cc2ff",
    "on_accent": "#000000",
    "popup_bg": "#2c2c2c",
    "popup_border": "rgba(255,255,255,0.09)",
    "tile": "rgba(255,255,255,0.045)",
    "tile_border": "rgba(255,255,255,0.05)",
    "separator": "rgba(255,255,255,0.0837)",
    "icon": "#ffffff",
    "avatar_bg": "#5c5c5c",
    "scrim": (0.0, 0.0, 0.0, 0.30),
}

LIGHT = {
    "text": "rgba(0,0,0,0.896)",
    "text2": "rgba(0,0,0,0.606)",
    "text3": "rgba(0,0,0,0.446)",
    "hover": "rgba(0,0,0,0.0373)",
    "pressed": "rgba(0,0,0,0.0241)",
    "ctrl": "rgba(255,255,255,0.70)",
    "ctrl_hover": "rgba(249,249,249,0.50)",
    "ctrl_border": "rgba(0,0,0,0.0578)",
    "border": (0.0, 0.0, 0.0, 0.10),
    "tint": (0.953, 0.953, 0.953),        # #f3f3f3
    "tint_alpha": 0.82,
    "solid": (0.953, 0.953, 0.953),
    "footer": "rgba(0,0,0,0.028)",
    "footer_line": "rgba(0,0,0,0.06)",
    "search_bg": "rgba(255,255,255,0.80)",
    "search_border": "rgba(0,0,0,0.06)",
    "search_line": "rgba(0,0,0,0.45)",
    "accent": "#005fb8",
    "accent_fill": "#005fb8",
    "on_accent": "#ffffff",
    "popup_bg": "#f9f9f9",
    "popup_border": "rgba(0,0,0,0.10)",
    "tile": "rgba(255,255,255,0.55)",
    "tile_border": "rgba(0,0,0,0.05)",
    "separator": "rgba(0,0,0,0.0803)",
    "icon": "#1a1a1a",
    "avatar_bg": "#c4c4c4",
    "scrim": (0.0, 0.0, 0.0, 0.12),
}

P = dict(DARK)
dark = True
_provider = None


def load_fonts():
    """Selawik nur für diese App registrieren."""
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        for path in glob.glob(os.path.join(ASSETS, "fonts", "*.ttf")):
            fc.FcConfigAppFontAddFile(None, path.encode())
    except OSError:
        pass


def system_prefers_dark():
    if "dark" in os.environ.get("GTK_THEME", "").lower():
        return True
    settings = Gtk.Settings.get_default()
    if settings is not None:
        name = (settings.get_property("gtk-theme-name") or "").lower()
        if "dark" in name or settings.get_property("gtk-application-prefer-dark-theme"):
            return True
    return False


def apply():
    """Palette wählen und CSS (neu) laden. Liefert True, wenn sich hell/dunkel änderte."""
    global P, dark, _provider
    mode = get_config().get("theme")
    new_dark = system_prefers_dark() if mode == "auto" else (mode == "dark")
    changed = new_dark != dark or _provider is None
    dark = new_dark
    P = dict(DARK if dark else LIGHT)
    screen = Gdk.Screen.get_default()
    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _provider)
    _provider = Gtk.CssProvider()
    _provider.load_from_data(css().encode())
    Gtk.StyleContext.add_provider_for_screen(
        screen, _provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
    return changed


def _css(rgb):
    return "rgb({},{},{})".format(*(int(round(c * 255)) for c in rgb))


def css():
    p = P
    return f"""
.sk, .sk * {{
    font-family: "{FONT}", "Segoe UI", sans-serif;
    color: {p['text']};
    text-shadow: none;
    -gtk-icon-shadow: none;
}}
window.sk, window.sk > * {{ background: transparent; }}
.sk label {{ font-size: 12px; }}
.sk .h {{ font-size: 14px; font-weight: 600; }}
.sk .sub {{ color: {p['text2']}; }}
.sk .dim {{ color: {p['text3']}; }}
.sk .big {{ font-size: 18px; font-weight: 600; }}
.sk .name13 {{ font-size: 13px; }}

/* flache Schaltflächen (Kacheln, Zeilen, Fusszeile) */
.sk button {{
    background: transparent; background-image: none;
    border: 1px solid transparent; border-radius: 4px;
    box-shadow: none; padding: 0; min-height: 0; min-width: 0;
    outline-color: {p['text']}; outline-width: 2px; outline-offset: -2px;
    -gtk-outline-radius: 4px;
}}
.sk button:hover {{ background-color: {p['hover']}; }}
.sk button:active, .sk button:checked {{ background-color: {p['pressed']}; }}
.sk button.ctrl {{
    background-color: {p['ctrl']}; border-color: {p['ctrl_border']};
    padding: 3px 10px 4px 10px; min-height: 22px;
}}
.sk button.ctrl:hover {{ background-color: {p['ctrl_hover']}; }}
.sk button.ctrl label {{ font-size: 12px; }}
.sk button.round {{ border-radius: 999px; -gtk-outline-radius: 999px; }}

/* Suchfeld */
.sk entry.search {{
    background-color: {p['search_bg']}; background-image: none;
    border: 1px solid {p['search_border']}; border-bottom-color: {p['search_line']};
    border-radius: 18px; box-shadow: none; min-height: 34px;
    padding: 0 16px 0 14px; font-size: 14px; color: {p['text']};
    caret-color: {p['text']};
}}
.sk entry.search:focus {{
    border-bottom: 2px solid {p['accent_fill']}; padding-bottom: 0;
}}
.sk .placeholder {{ font-size: 14px; }}
.sk entry.search image {{ color: {p['text2']}; margin-right: 6px; }}
.sk entry.search selection {{ background-color: {p['accent_fill']}; color: {p['on_accent']}; }}

/* Fusszeile */
.sk .footer {{ background-color: {p['footer']}; border-top: 1px solid {p['footer_line']};
              border-radius: 0 0 8px 8px; }}
.sk .dot {{ background-color: {p['accent_fill']}; border-radius: 999px;
           min-width: 6px; min-height: 6px; }}
.sk .avatar {{ background-color: {p['avatar_bg']}; border-radius: 999px; }}

/* Kategorien */
.sk .cattile {{ background-color: {p['tile']}; border: 1px solid {p['tile_border']};
               border-radius: 8px; padding: 6px; }}
.sk .letter {{ font-size: 14px; font-weight: 600; padding: 4px 10px; }}

/* Überlagerungen: Kontextmenüs, Ordner, Buchstaben */
.sk .popup {{
    background-color: {p['popup_bg']}; border: 1px solid {p['popup_border']};
    border-radius: 8px; padding: 4px;
}}
.sk .popup button.item {{ padding: 0 12px 0 10px; min-height: 30px; margin: 1px 0; }}
.sk .popup button.item label {{ font-size: 13px; }}
.sk .popup button.item:disabled label {{ color: {p['text3']}; }}
.sk .popup separator {{ background-color: {p['separator']}; min-height: 1px; margin: 4px -4px; }}
.sk .folder {{ background-color: {p['popup_bg']}; border: 1px solid {p['popup_border']};
              border-radius: 8px; padding: 20px 24px 24px 24px; }}

/* Suche */
.sk button.chip {{ padding: 4px 10px; border-radius: 4px; }}
.sk button.chip label {{ font-size: 13px; color: {p['text2']}; }}
.sk button.chip.active label {{ color: {p['text']}; font-weight: 600; }}
.sk .chipline {{ background-color: {p['accent_fill']}; border-radius: 2px;
                min-height: 3px; min-width: 16px; }}
.sk button.result {{ padding: 0; }}
.sk button.result.selected {{ background-color: {p['hover']}; }}
.sk .selbar {{ background-color: {p['accent_fill']}; border-radius: 2px; min-width: 3px; }}
.sk .detail {{ background-color: {p['tile']}; border: 1px solid {p['tile_border']};
              border-radius: 8px; }}
.sk .detail separator {{ background-color: {p['separator']}; min-height: 1px; }}
.sk button.action {{ padding: 0 12px; min-height: 36px; }}
.sk button.action label {{ font-size: 13px; }}
.sk .grouphead {{ font-size: 14px; font-weight: 600; margin: 10px 0 4px 12px; }}

/* Einstellungsfenster */
window.sk.skset {{ background-color: {_css(p['solid'])}; }}
.sk .pagetitle {{ font-size: 28px; font-weight: 600; margin-bottom: 4px; }}
.sk .sectiontitle {{ font-size: 14px; font-weight: 600; margin: 20px 0 6px 2px; }}
.sk .card {{ background-color: {p['tile']}; border: 1px solid {p['tile_border']};
            border-radius: 6px; padding: 12px 18px; min-height: 44px; }}
.sk .cardtitle {{ font-size: 14px; }}
.sk .seg button {{ background-color: {p['ctrl']}; border: 1px solid {p['ctrl_border']};
                  border-radius: 0; padding: 5px 14px; }}
.sk .seg button:first-child {{ border-radius: 4px 0 0 4px; }}
.sk .seg button:last-child {{ border-radius: 0 4px 4px 0; }}
.sk .seg button:checked {{ background-color: {p['accent_fill']}; border-color: {p['accent_fill']}; }}
.sk .seg button:checked label {{ color: {p['on_accent']}; }}
.sk combobox button {{ background-color: {p['ctrl']}; border: 1px solid {p['ctrl_border']};
                      padding: 4px 10px; min-height: 26px; min-width: 120px; }}

.sk scrollbar {{ background: transparent; border: none; }}
.sk scrollbar slider {{ background-color: {p['text3']}; border-radius: 999px;
                        min-width: 3px; border: none; }}
.sk scrollbar:hover slider {{ min-width: 6px; }}
"""
