"""Symbole: Fluent UI System Icons (MIT) für Bedienelemente, App- und
Dateisymbole aus dem System-Theme."""
import functools
import os

from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from . import ASSETS, theme

ICON_DIR = os.path.join(ASSETS, "icons")


@functools.lru_cache(maxsize=256)
def _fluent_pixbuf(name, size, color, scale):
    path = os.path.join(ICON_DIR, name + ".svg")
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return None
    svg = svg.replace("#212121", color)
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size * scale, size * scale)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


def fluent(name, size=16, color=None):
    """Gtk.Image mit einem eingefärbten Fluent-Symbol (Name ohne .svg)."""
    img = Gtk.Image()
    pb = _fluent_pixbuf(name, size, color or theme.P["icon"], 1)
    if pb is not None:
        img.set_from_pixbuf(pb)
    img.set_size_request(size, size)
    return img


def app_image(gicon, size):
    img = Gtk.Image()
    if gicon is None:
        gicon = Gio.ThemedIcon.new("application-x-executable")
    img.set_from_gicon(gicon, Gtk.IconSize.BUTTON)
    img.set_pixel_size(size)
    return img


def file_gicon(path, content_type=None, is_dir=False):
    """Symbol nach Dateityp (nur nach Namen geraten – liest die Datei nicht)."""
    if is_dir or content_type == "inode/directory":
        return Gio.ThemedIcon.new_from_names(["folder"])
    if not content_type:
        content_type, _ = Gio.content_type_guess(os.path.basename(path), None)
    return Gio.content_type_get_icon(content_type or "application/octet-stream")


def avatar_path():
    for p in (os.path.expanduser("~/.face"), os.path.expanduser("~/.face.icon"),
              os.path.join("/var/lib/AccountsService/icons", GLib.get_user_name())):
        try:
            if os.path.isfile(p) and os.access(p, os.R_OK):
                return p
        except OSError:
            pass
    return None


def avatar(size):
    """Rundes Benutzerbild (oder Person-Symbol auf grauem Kreis wie Windows)."""
    path = avatar_path()
    if path:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, False)
            area = Gtk.DrawingArea()
            area.set_size_request(size, size)
            area.set_halign(Gtk.Align.CENTER)
            area.set_valign(Gtk.Align.CENTER)

            def draw(_w, cr, pb=pb):
                import math
                from gi.repository import Gdk
                cr.arc(size / 2, size / 2, size / 2, 0, 2 * math.pi)
                cr.clip()
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.paint()
            area.connect("draw", draw)
            return area
        except GLib.Error:
            pass
    box = Gtk.Box()
    box.get_style_context().add_class("avatar")
    box.set_size_request(size, size)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    img = fluent("person_20_regular", max(12, size // 2), theme.P["text"])
    img.set_halign(Gtk.Align.CENTER)
    img.set_valign(Gtk.Align.CENTER)
    box.set_center_widget(img)
    return box
