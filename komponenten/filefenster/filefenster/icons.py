"""Icons: Fluent UI System Icons (MIT, assets/icons/fluent), eigene Ordner-SVGs
im Windows-11-Stil, farbige Orts-Icons und Datei-Icons aus dem System-Theme.

Wichtigste Funktionen:
    fluent(name, size=16, color=None, filled=False) -> Gtk.Image
    fluent_pixbuf(name, size=16, color=None, filled=False) -> GdkPixbuf
    folder_pixbuf(size, special=None) -> GdkPixbuf   (special: 'desktop',
        'downloads', 'documents', 'pictures', 'music', 'videos', 'home', 'onedrive')
    place_pixbuf(kind, size) -> GdkPixbuf  (kind: 'home','desktop','downloads',
        'documents','pictures','music','videos','onedrive','thispc','network',
        'drive','drive-system','usb','optical','trash','folder','search','user',
        'gallery')
    file_pixbuf(info: Gio.FileInfo, size, path=None) -> GdkPixbuf
        (Ordner -> eigenes Ordner-Icon inkl. Spezialordner, Bilder -> Vorschau
        aus dem Thumbnail-Cache falls vorhanden, sonst Theme-Icon)
    gicon_pixbuf(gicon, size) -> GdkPixbuf
    caption_image(kind) -> Gtk.Image  ('minimize','maximize','restore','close')
"""
import functools
import glob
import os
import re

from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from . import theme

FLUENT_DIR = os.path.join(theme.ASSETS, "icons", "fluent")

# Farben der Orts-Icons (Navigationsbereich, Start, Dieser PC)
PLACE_COLORS = {
    "home": "#3b8eea",
    "desktop": "#3a96dd",
    "downloads": "#2e9e62",
    "documents": "#6f87a6",
    "pictures": "#2f8fe0",
    "music": "#e8603c",
    "videos": "#8a5cf5",
    "onedrive": "#1a73d9",
    "thispc": "#3a96dd",
    "network": "#3a96dd",
    "usb": "#6f87a6",
    "trash": "#6f87a6",
    "search": "#6f87a6",
    "user": "#3b8eea",
}

# Glyphe (Fluent-Name) je Ort
PLACE_GLYPH = {
    "home": "home",
    "desktop": "desktop",
    "downloads": "arrow_download",
    "documents": "document_text",
    "pictures": "image",
    "music": "music_note_2",
    "videos": "video",
    "onedrive": "cloud",
    "thispc": "desktop",
    "network": "globe",
    "usb": "usb_stick",
    "trash": "delete",
    "search": "search",
    "user": "person",
}


def _available(name, style):
    files = glob.glob(os.path.join(FLUENT_DIR, f"{name}_*_{style}.svg"))
    sizes = []
    for f in files:
        m = re.search(rf"{re.escape(name)}_(\d+)_{style}\.svg$", f)
        if m:
            sizes.append(int(m.group(1)))
    return sorted(sizes)


@functools.lru_cache(maxsize=None)
def _fluent_svg(name, size, filled):
    style = "filled" if filled else "regular"
    sizes = _available(name, style)
    if not sizes and filled:
        style, sizes = "regular", _available(name, "regular")
    if not sizes:
        return None
    # passende oder nächstgrössere Vorlage nehmen
    best = min(sizes, key=lambda s: (s < size, abs(s - size)))
    with open(os.path.join(FLUENT_DIR, f"{name}_{best}_{style}.svg"), encoding="utf-8") as fh:
        return fh.read()


def _svg_to_pixbuf(svg, w, h=None):
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(w, h or w)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


@functools.lru_cache(maxsize=None)
def fluent_pixbuf(name, size=16, color=None, filled=False):
    svg = _fluent_svg(name, size, filled)
    if svg is None:
        svg = _fluent_svg("document", size, False)
    color = color or theme.P["icon"]
    svg = svg.replace("#212121", color)
    return _svg_to_pixbuf(svg, size)


def fluent(name, size=16, color=None, filled=False):
    img = Gtk.Image.new_from_pixbuf(fluent_pixbuf(name, size, color, filled))
    img.get_style_context().add_class("fluent")
    return img


def _inner_paths(svg):
    """Inhalt eines Fluent-SVG ohne <svg>-Hülle."""
    m = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S)
    return m.group(1) if m else ""


def _folder_svg(special=None):
    b, f, hi = theme.P["folder_back"], theme.P["folder_front"], theme.P["folder_front_hi"]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">',
        # Rückwand mit Reiter
        f'<path d="M2 7.6A2.6 2.6 0 0 1 4.6 5H11.3a2.2 2.2 0 0 1 1.5.6L14.8 7.6H27.4'
        f'A2.6 2.6 0 0 1 30 10.2V24.4A2.6 2.6 0 0 1 27.4 27H4.6A2.6 2.6 0 0 1 2 24.4Z" fill="{b}"/>',
        # Vorderseite
        f'<path d="M2 12.1A2.1 2.1 0 0 1 4.1 10H27.9A2.1 2.1 0 0 1 30 12.1V24.6'
        f'A2.4 2.4 0 0 1 27.6 27H4.4A2.4 2.4 0 0 1 2 24.6Z" fill="{f}"/>',
        # Lichtkante oben
        f'<path d="M2 12.1A2.1 2.1 0 0 1 4.1 10H27.9A2.1 2.1 0 0 1 30 12.1V12.9H2Z" fill="{hi}"/>',
    ]
    if special and special in PLACE_GLYPH:
        glyph = _fluent_svg(PLACE_GLYPH[special], 20, True) or ""
        color = PLACE_COLORS.get(special, "#3a96dd")
        inner = _inner_paths(glyph).replace("#212121", color)
        # Glyphe unten rechts auf der Vorderseite
        parts.append(f'<g transform="translate(14.5 13.5) scale(0.62)">{inner}</g>')
    parts.append("</svg>")
    return "".join(parts)


@functools.lru_cache(maxsize=None)
def folder_pixbuf(size, special=None):
    return _svg_to_pixbuf(_folder_svg(special), size)


def _drive_svg(system=False):
    body = "#b9c0c8" if not theme.DARK_MODE else "#9aa3ad"
    face = "#8d96a1" if not theme.DARK_MODE else "#77818c"
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">',
        f'<rect x="3" y="11" width="26" height="12" rx="2.2" fill="{body}"/>',
        f'<rect x="3" y="18" width="26" height="5" rx="2" fill="{face}"/>',
        '<circle cx="25.5" cy="20.5" r="1.1" fill="#3fd06b"/>',
    ]
    if system:
        # blauer Streifen als "System"-Kennzeichen (bewusst kein Logo)
        svg.append('<rect x="5.5" y="13" width="7" height="2.4" rx="1.2" fill="#2f8fe0"/>')
    svg.append("</svg>")
    return "".join(svg)


# Gebäude für Organisationsordner (Teams/SharePoint), wie das Nemo-Icon von onedrive-mint
_ORG_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="4" y="3" width="11" height="19" rx="1" fill="#0078D4"/>'
    '<rect x="15" y="9" width="6" height="13" rx="1" fill="#28A8EA"/>'
    + "".join(f'<rect x="{x}" y="{y}" width="2" height="2" fill="#fff"/>'
              for x in (6.5, 10.5) for y in (6, 10, 14))
    + '<rect x="8.5" y="18" width="2.5" height="4" fill="#fff"/>'
    '<rect x="17" y="12" width="2" height="2" fill="#fff"/>'
    '<rect x="17" y="16" width="2" height="2" fill="#fff"/></svg>')


# Katalog (Gallery): Foto mit Bergen und Sonne vor einer zweiten Karte, wie Windows 11
_GALLERY_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="6" y="2" width="16" height="13.5" rx="2.2" fill="#8fc3f7"/>'
    '<rect x="2" y="6" width="17.5" height="15" rx="2.4" fill="#1a73d9"/>'
    '<rect x="3.6" y="7.6" width="14.3" height="11.8" rx="1.3" fill="#d4eaff"/>'
    '<path d="M3.6 17.6L8.4 12.4L11.7 15.7L13.9 13.6L17.9 17.6V18.1A1.3 1.3 0 0 1 16.6 19.4'
    'H4.9A1.3 1.3 0 0 1 3.6 18.1Z" fill="#1a73d9"/>'
    '<circle cx="14.4" cy="10.8" r="1.6" fill="#ffc83d"/></svg>')


@functools.lru_cache(maxsize=None)
def place_pixbuf(kind, size=16):
    if kind == "org":
        return _svg_to_pixbuf(_ORG_SVG, size)
    if kind == "gallery":
        return _svg_to_pixbuf(_GALLERY_SVG, size)
    if kind == "folder":
        return folder_pixbuf(size)
    if kind in ("drive", "drive-system"):
        return _svg_to_pixbuf(_drive_svg(kind == "drive-system"), size)
    if kind == "optical":
        return fluent_pixbuf("storage", size, "#6f87a6", True)
    glyph = PLACE_GLYPH.get(kind, "folder")
    return fluent_pixbuf(glyph, size, PLACE_COLORS.get(kind, "#6f87a6"), True)


_theme = None


def _icon_theme():
    global _theme
    if _theme is None:
        _theme = Gtk.IconTheme.get_default()
    return _theme


@functools.lru_cache(maxsize=2048)
def _gicon_pixbuf_cached(key, size):
    gicon = Gio.Icon.new_for_string(key)
    info = _icon_theme().lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
    if info is None:
        info = _icon_theme().lookup_icon("text-x-generic", size, Gtk.IconLookupFlags.FORCE_SIZE)
    try:
        return info.load_icon() if info else None
    except GLib.Error:
        return None


def gicon_pixbuf(gicon, size):
    if gicon is None:
        return _gicon_pixbuf_cached("text-x-generic", size)
    try:
        key = gicon.to_string()
    except Exception:
        key = None
    if not key:
        return _gicon_pixbuf_cached("text-x-generic", size)
    return _gicon_pixbuf_cached(key, size)


def thumbnail_pixbuf(thumb_path, size):
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb_path, size, size, True)
        return pb
    except GLib.Error:
        return None


def file_pixbuf(info, size, path=None):
    """Icon für einen Eintrag einer Ordnerliste (Gio.FileInfo)."""
    from . import places  # spät importieren (Zyklus vermeiden)
    ftype = info.get_file_type()
    if ftype == Gio.FileType.DIRECTORY or ftype == Gio.FileType.MOUNTABLE:
        special = places.special_key_for_path(path) if path else None
        if special == "org":
            return place_pixbuf("org", size)
        return folder_pixbuf(size, special)
    if size >= 48 and info.has_attribute("thumbnail::path"):
        tp = info.get_attribute_byte_string("thumbnail::path")
        if tp:
            pb = thumbnail_pixbuf(tp, size)
            if pb is not None:
                return pb
    return gicon_pixbuf(info.get_icon(), size)


def _caption_svg(kind, color):
    s = f'stroke="{color}" stroke-width="1" fill="none" shape-rendering="crispEdges"'
    if kind == "minimize":
        body = f'<path d="M3 8.5H13" {s}/>'
    elif kind == "maximize":
        body = f'<rect x="3.5" y="3.5" width="9" height="9" rx="1" {s}/>'
    elif kind == "restore":
        body = (f'<rect x="3.5" y="5.5" width="7" height="7" rx="1" {s}/>'
                f'<path d="M5.5 3.5H11.5A1 1 0 0 1 12.5 4.5V10.5" {s}/>')
    else:  # close
        body = (f'<path d="M3.5 3.5L12.5 12.5M12.5 3.5L3.5 12.5" stroke="{color}" '
                f'stroke-width="1.05" fill="none"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">{body}</svg>'


@functools.lru_cache(maxsize=None)
def caption_pixbuf(kind, color):
    return _svg_to_pixbuf(_caption_svg(kind, color), 16)


def caption_image(kind, color=None):
    return Gtk.Image.new_from_pixbuf(caption_pixbuf(kind, color or theme.P["icon"]))
