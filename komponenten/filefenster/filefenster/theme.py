"""Farben (hell/dunkel wie Windows 11), Schrift und CSS-Laden.

Alle CSS-Dateien in filefenster/style/*.css werden geladen (alphabetisch,
base.css zuerst). Vor jede Datei werden die Palettenfarben als
@define-color gesetzt, also in CSS z. B. `color: @text;` verwenden.
In Python: theme.P["accent"] usw. – zur Zeichenzeit lesen, nicht merken:
refresh() tönt Tab-Leiste und Navigationsbereich nach dem Desktop-Hintergrund
(mica.py) und lädt das CSS neu, sobald sich der Hintergrund ändert.
"""
import ctypes
import glob
import os
import subprocess

from gi.repository import Gdk, Gtk

from .config import get_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
STYLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style")

FONT_FAMILY = "Selawik"

LIGHT = {
    "tabs_bg": "#e9edf2",        # Tab-Leiste (Mica-Ton; mica.py tönt nach dem Hintergrund)
    "dialog_bg": "#e9edf2",      # Dialoge (ohne Mica)
    "tab_hover": "#f1f3f6",
    "toolbar_bg": "#f9f9f9",     # aktiver Tab + Adresszeile + Befehlsleiste
    "content_bg": "#ffffff",     # Dateiliste, Navigationsbereich
    "pane_bg": "#ffffff",
    "text": "#1b1b1b",
    "text2": "#5f5f5f",          # Nebenspalten (Datum, Typ, Grösse)
    "text3": "#8a8a8a",          # Platzhalter, deaktiviert
    "icon": "#1b1b1b",
    "icon_disabled": "#a0a0a0",
    "border": "#e5e5e5",
    "divider": "#ebebeb",
    "hover": "#e5f3ff",          # Zeile unter Maus (Dateiliste)
    "selected": "#cce8ff",       # Auswahl
    "selected_hover": "#b9dffc",
    "selected_border": "#99d1ff",
    "nav_hover": "#eef0f3",
    "nav_selected": "#e3e6ea",
    "button_hover": "rgba(0,0,0,0.045)",
    "button_active": "rgba(0,0,0,0.03)",
    "control_bg": "#ffffff",     # Textfelder (Adresse, Suche)
    "control_hover": "#f9f9f9",
    "control_border": "#e5e5e5",
    "control_border_bottom": "#8d8d8d",
    "accent": "#005fb8",
    "accent_text": "#ffffff",
    "menu_bg": "#f9f9f9",
    "menu_border": "rgba(0,0,0,0.10)",
    "menu_hover": "rgba(0,0,0,0.045)",
    "close_hover": "#c42b1c",
    "bar_bg": "#e6e6e6",
    "bar_fill": "#26a0da",
    "bar_full": "#da2626",
    "scroll_thumb": "rgba(0,0,0,0.40)",
    "folder_back": "#e8a830",
    "folder_front": "#ffd15c",
    "folder_front_hi": "#ffe08a",
}

DARK = {
    "tabs_bg": "#202020",
    "dialog_bg": "#202020",
    "tab_hover": "#2a2a2a",
    "toolbar_bg": "#2c2c2c",
    "content_bg": "#191919",
    "pane_bg": "#191919",
    "text": "#ffffff",
    "text2": "#c8c8c8",
    "text3": "#8f8f8f",
    "icon": "#ffffff",
    "icon_disabled": "#6b6b6b",
    "border": "#3a3a3a",
    "divider": "#2b2b2b",
    "hover": "#2b2b2b",
    "selected": "#3a3a3a",
    "selected_hover": "#444444",
    "selected_border": "#5a5a5a",
    "nav_hover": "#2b2b2b",
    "nav_selected": "#353535",
    "button_hover": "rgba(255,255,255,0.065)",
    "button_active": "rgba(255,255,255,0.04)",
    "control_bg": "#333333",
    "control_hover": "#383838",
    "control_border": "#3d3d3d",
    "control_border_bottom": "#9a9a9a",
    "accent": "#60cdff",
    "accent_text": "#000000",
    "menu_bg": "#2c2c2c",
    "menu_border": "rgba(0,0,0,0.45)",
    "menu_hover": "rgba(255,255,255,0.075)",
    "close_hover": "#c42b1c",
    "bar_bg": "#3d3d3d",
    "bar_fill": "#26a0da",
    "bar_full": "#da2626",
    "scroll_thumb": "rgba(255,255,255,0.45)",
    "folder_back": "#e0a028",
    "folder_front": "#ffc83d",
    "folder_front_hi": "#ffd96b",
}

P = dict(LIGHT)
DARK_MODE = False


def system_prefers_dark():
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.cinnamon.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=2).stdout.lower()
        if "dark" in out:
            return True
    except Exception:
        pass
    settings = Gtk.Settings.get_default()
    if settings is not None:
        name = (settings.get_property("gtk-theme-name") or "").lower()
        if "dark" in name or settings.get_property("gtk-application-prefer-dark-theme"):
            return True
    return False


def load_fonts():
    """Selawik (OFL, metrisch kompatibel zu Segoe UI) nur für diese App registrieren."""
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        for path in glob.glob(os.path.join(ASSETS, "fonts", "*.ttf")):
            fc.FcConfigAppFontAddFile(None, path.encode())
    except OSError:
        pass


def setup():
    """Einmal beim Start (vor dem ersten Fenster) aufrufen."""
    global DARK_MODE
    load_fonts()
    mode = get_config().get("theme", "auto")
    DARK_MODE = system_prefers_dark() if mode == "auto" else (mode == "dark")
    from . import mica
    mica.start(refresh)        # Farbe des Desktop-Hintergrunds (wartet kurz darauf)
    _build_palette()

    settings = Gtk.Settings.get_default()
    # Vorhersagbare Basis statt des System-Themes, darüber unser CSS.
    settings.set_property("gtk-theme-name", "Adwaita")
    settings.set_property("gtk-application-prefer-dark-theme", DARK_MODE)
    settings.set_property("gtk-font-name", f"{FONT_FAMILY} 9")
    load_css()


def _build_palette():
    """P = Grundpalette (hell/dunkel) + Mica-Tönung. P bleibt dasselbe dict."""
    from . import mica
    base = DARK if DARK_MODE else LIGHT
    P.clear()
    P.update(base)
    P.update(mica.overrides(base, DARK_MODE))


def refresh():
    """Palette neu zusammensetzen (neuer Desktop-Hintergrund, Mica ein/aus),
    CSS neu laden und alle Fenster neu zeichnen."""
    _build_palette()
    load_css()
    for win in Gtk.Window.list_toplevels():
        win.queue_draw()


_providers = []


def load_css():
    screen = Gdk.Screen.get_default()
    for prov in _providers:
        Gtk.StyleContext.remove_provider_for_screen(screen, prov)
    _providers.clear()
    defines = "".join(f"@define-color {k} {v};\n" for k, v in P.items())
    files = sorted(glob.glob(os.path.join(STYLE_DIR, "*.css")),
                   key=lambda f: (os.path.basename(f) != "base.css", f))
    for path in files:
        with open(path, encoding="utf-8") as fh:
            css = defines + fh.read()
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css.encode())
        except Exception as exc:  # Syntaxfehler sichtbar machen, aber weiterlaufen
            print(f"[filefenster] CSS-Fehler in {os.path.basename(path)}: {exc}")
            continue
        Gtk.StyleContext.add_provider_for_screen(
            screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10)
        _providers.append(prov)
