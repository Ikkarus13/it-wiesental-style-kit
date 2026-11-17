"""Mica-Nachbildung: Tab-Leiste und Navigationsbereich nehmen – wie bei
Windows 11 – ganz leicht die Farbe des Desktop-Hintergrunds an.

Windows legt über das stark weichgezeichnete Hintergrundbild eine neutrale
Tönung (hell zu 50 %, dunkel zu 80 % deckend) und übernimmt vom Bild nur
Farbton und Sättigung (Mischmodus „Luminosität“). Hier geschieht dasselbe mit
einer einzigen, festen Farbe: dem Mittelwert des Bildes (im Hintergrund-Thread
berechnet). Die Helligkeit kommt aus der bisherigen Palette – der Kontrast zum
Text bleibt also gleich. Ohne lesbares Hintergrundbild bleibt alles wie bisher.

    start(on_change)            Hintergrund lesen und beobachten (einmal, in theme.setup)
    overrides(base, dark)       getönte Palettenwerte oder {} (aus/kein Bild)
    reload()                    Hintergrund neu lesen (Signal, Option, Datei geändert)
    wallpaper_source(dark)      ("file", Pfad) | ("color", (r, g, b)) | None
    average_color(path)         (r, g, b) 0…1 oder None
    tint(base, dark, avg)       {Schlüssel: "#rrggbb"} für tabs_bg, tab_hover,
                                pane_bg, nav_hover, nav_selected

Einstellung „mica“ (Ordneroptionen → Ansicht), Standard: ein.
Zum Testen (ohne die echten Desktop-Einstellungen anzufassen):
FILEFENSTER_WALLPAPER=/pfad/bild.jpg (auch Datei-URI oder „#rrggbb“;
leer = kein Hintergrundbild).
"""
import os
import stat
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib

from .config import get_config

SAMPLE = 64                     # Kantenlänge des verkleinerten Bildes
MAX_BYTES = 160 * 1024 * 1024   # grössere Dateien gar nicht erst lesen
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 60_000_000         # PNG & Co. werden ganz dekodiert …
MAX_PIXELS_JPEG = 400_000_000   # … JPEG verkleinert schon beim Dekodieren
START_WAIT = 0.3                # so lange wartet der Start auf die Farbe (s)


def too_many_pixels(fmt_name, w, h):
    """Bräuchte das Dekodieren zu viel Speicher? gdk-pixbuf dekodiert PNG & Co.
    in voller Grösse, auch wenn nur ein kleines Bild gebraucht wird (ein paar
    KB grosses PNG → mehrere GB). SVG wird direkt klein gerendert."""
    name = (fmt_name or "").lower()
    if name == "svg" or not w or not h:
        return False
    return w * h > (MAX_PIXELS_JPEG if name == "jpeg" else MAX_PIXELS)

# Anteil der neutralen Tönung (Windows: TintOpacity von Mica)
TINT_OPACITY = {False: 0.5, True: 0.8}
# Höchstens so viel Farbe (grösster minus kleinster Kanal) – bleibt dezent,
# auch bei sehr bunten Bildern
MAX_CHROMA = {False: 0.06, True: 0.07}
# Wie stark Navigationsbereich und seine Hover-/Auswahlfarben mitgetönt werden
PANE_STRENGTH = {False: 0.5, True: 0.5}
# Tab unter der Maus: so viel Weiss über der Mica-Farbe
TAB_HOVER_WHITE = {False: 0.35, True: 0.045}

_SCHEMAS = ("org.cinnamon.desktop.background", "org.gnome.desktop.background")
_KEYS = ("picture-uri", "picture-uri-dark", "picture-options", "primary-color",
         "secondary-color", "color-shading-type")

_on_change = None
_avg = None             # Mittelwert des aktuellen Hintergrunds oder None
_gen = 0                # zählt Neuberechnungen (veraltete Ergebnisse verwerfen)
_cache = {}             # (Pfad, mtime, Grösse) -> Mittelwert
_settings = {}          # Schema -> Gio.Settings (Referenz halten, sonst keine Signale)
_monitor = None         # (Pfad, Gio.FileMonitor) des Hintergrundbilds
_pending = 0            # Entprellung von Änderungssignalen


# ----------------------------------------------------------------------
# Farbrechnung (alles 0…1, sRGB)
# ----------------------------------------------------------------------
def _lum(c):
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def _clip(c):
    """ClipColor aus dem W3C-Mischmodus „Luminosität“."""
    lum = _lum(c)
    n, x = min(c), max(c)
    if n < 0:
        c = tuple(lum + (v - lum) * lum / (lum - n) for v in c)
    if x > 1:
        c = tuple(lum + (v - lum) * (1 - lum) / (x - lum) for v in c)
    return tuple(min(1.0, max(0.0, v)) for v in c)


def _set_lum(c, lum):
    d = lum - _lum(c)
    return _clip(tuple(v + d for v in c))


def _mix(a, b, t):
    """a → b, t = Anteil von b."""
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _shift(c, off, k):
    """Farbe um k·off verschieben, ohne über Weiss/unter Schwarz zu laufen."""
    r = [v + k * o for v, o in zip(c, off)]
    hi, lo = max(r), min(r)
    if hi > 1:
        r = [v - (hi - 1) for v in r]
    elif lo < 0:
        r = [v - lo for v in r]
    return tuple(min(1.0, max(0.0, v)) for v in r)


def _rgb(text):
    """'#rrggbb' (oder andere CSS-Farbe) → (r, g, b) 0…1, sonst None."""
    rgba = Gdk.RGBA()
    if not text or not rgba.parse(text.strip()):
        return None
    return (rgba.red, rgba.green, rgba.blue)


def _hex(c):
    return "#" + "".join(f"{int(round(min(1.0, max(0.0, v)) * 255)):02x}" for v in c)


def tint(base, dark, avg):
    """Getönte Palettenwerte: Farbton/Sättigung vom Hintergrund (avg), die
    Helligkeit vom jeweiligen Palettenwert in base."""
    tabs = _rgb(base["tabs_bg"])
    lum = _lum(tabs)
    grey = (lum, lum, lum)
    mica = _mix(_set_lum(avg, lum), grey, TINT_OPACITY[dark])
    off = tuple(m - g for m, g in zip(mica, grey))
    chroma = max(off) - min(off)
    if chroma > MAX_CHROMA[dark]:
        off = tuple(o * MAX_CHROMA[dark] / chroma for o in off)
        mica = tuple(g + o for g, o in zip(grey, off))
    out = {"tabs_bg": mica,
           "tab_hover": _mix(mica, (1.0, 1.0, 1.0), TAB_HOVER_WHITE[dark])}
    for key in ("pane_bg", "nav_hover", "nav_selected"):
        c = _rgb(base[key])
        if c is None:
            continue
        g = _lum(c)
        out[key] = _shift((g, g, g), off, PANE_STRENGTH[dark])
    return {k: _hex(v) for k, v in out.items()}


def contrast(a, b):
    """WCAG-Kontrastverhältnis zweier Farben ('#rrggbb')."""
    def rel(c):
        lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in _rgb(c)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    la, lb = sorted((rel(a), rel(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ----------------------------------------------------------------------
# Hintergrundbild
# ----------------------------------------------------------------------
def _parse(text):
    text = (text or "").strip().strip("'\"")
    if not text or text.lower() == "none":
        return None
    if text.startswith(("#", "rgb")):
        c = _rgb(text)
        return ("color", c) if c else None
    if "://" in text:
        if not text.startswith("file://"):
            return None
        path = Gio.File.new_for_uri(text).get_path()
    else:
        path = os.path.expanduser(text)
    return ("file", path) if path else None


def _schema_ids():
    desk = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "gnome" in desk and "cinnamon" not in desk:
        return tuple(reversed(_SCHEMAS))
    return _SCHEMAS


def _get_settings(sid):
    if sid in _settings:
        return _settings[sid]
    s = None
    src = Gio.SettingsSchemaSource.get_default()
    schema = src.lookup(sid, True) if src is not None else None
    if schema is not None:     # Gio.Settings.new() bricht bei fehlendem Schema ab
        s = Gio.Settings.new_full(schema, None, None)
    _settings[sid] = s
    return s


def wallpaper_source(dark=False):
    """Woher die Farbe kommt: ("file", Pfad), ("color", (r, g, b)) oder None."""
    env = os.environ.get("FILEFENSTER_WALLPAPER")
    if env is not None:
        return _parse(env)
    for sid in _schema_ids():
        s = _get_settings(sid)
        if s is None:
            continue
        keys = s.props.settings_schema.list_keys()

        def get(key):
            return s.get_string(key) if key in keys else ""
        uri = (get("picture-uri-dark") if dark else "") or get("picture-uri")
        if uri and get("picture-options") != "none":
            src = _parse(uri)
            if src is not None:
                return src
            continue
        # kein Bild: einfarbiger Hintergrund (oder Verlauf aus zwei Farben)
        c1 = _rgb(get("primary-color"))
        c2 = _rgb(get("secondary-color"))
        if c1 is not None:
            if c2 is not None and get("color-shading-type") not in ("", "solid"):
                c1 = _mix(c1, c2, 0.5)
            return ("color", c1)
    return None


def _pixbuf_average(pb):
    n = pb.get_n_channels()
    alpha = pb.get_has_alpha()
    stride = pb.get_rowstride()
    w, h = pb.get_width(), pb.get_height()
    data = pb.get_pixels()
    sr = sg = sb = sw = 0
    for y in range(h):
        row = y * stride
        for x in range(w):
            i = row + x * n
            a = data[i + 3] if alpha else 255
            if a:
                sr += data[i] * a
                sg += data[i + 1] * a
                sb += data[i + 2] * a
                sw += a
    if not sw:
        return None
    return (sr / sw / 255, sg / sw / 255, sb / sw / 255)


def average_color(path):
    """Mittelwert eines Bildes (starkes Weichzeichnen auf eine Farbe) oder
    None (fehlt, kein Bild, zu gross, kaputt). Läuft im Hintergrund-Thread."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_BYTES:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]
    try:
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    except (GLib.Error, TypeError):
        return None
    if fmt is None:
        return None
    name = (fmt.get_name() or "").lower()
    if name == "svg":
        if st.st_size > MAX_SVG_BYTES:
            return None
    elif w * h > (MAX_PIXELS_JPEG if name == "jpeg" else MAX_PIXELS):
        return None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, SAMPLE, SAMPLE, True)
    except GLib.Error:
        return None
    avg = _pixbuf_average(pb) if pb is not None else None
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = avg
    return avg


# ----------------------------------------------------------------------
# Zustand, Beobachten
# ----------------------------------------------------------------------
def enabled():
    return bool(get_config().get("mica", True))


def current():
    """Mittelwert des aktuellen Hintergrunds (r, g, b) oder None."""
    return _avg if enabled() else None


def overrides(base, dark):
    """Palettenwerte, die theme.P überschreiben – {} ohne Hintergrund/Option aus."""
    avg = current()
    return tint(base, dark, avg) if avg is not None else {}


def start(on_change, wait=START_WAIT):
    """Einmal beim Start: Farbe berechnen (bis wait Sekunden darauf warten,
    damit das erste Fenster nicht erst ungetönt erscheint) und beobachten."""
    global _on_change
    if _on_change is not None:
        return
    _on_change = on_change
    get_config().connect_changed(_on_config)
    if os.environ.get("FILEFENSTER_WALLPAPER") is None:
        for sid in _schema_ids():
            s = _get_settings(sid)
            if s is not None:
                s.connect("changed", _on_setting)
    reload(wait=wait, notify=False)


def _dark():
    from . import theme
    return theme.DARK_MODE


def reload(wait=0, notify=True):
    """Hintergrund neu lesen; die Farbe kommt aus einem Hintergrund-Thread."""
    global _gen
    _gen += 1
    gen = _gen
    src = wallpaper_source(_dark()) if enabled() else None
    _watch_file(src[1] if src and src[0] == "file" else None)
    if src is None or src[0] == "color":
        _deliver(gen, src[1] if src else None, notify)
        return
    path = src[1]
    done = threading.Event()
    box = {}

    def work():
        try:
            box["avg"] = average_color(path)
        except Exception:      # nie den Start stören
            box["avg"] = None
        done.set()
        GLib.idle_add(_deliver, gen, box["avg"], True)
    threading.Thread(target=work, name="filefenster-mica", daemon=True).start()
    if wait and done.wait(wait):
        _deliver(gen, box["avg"], notify)


def _deliver(gen, avg, notify=True):
    global _avg
    if gen != _gen:
        return False
    changed = avg != _avg
    _avg = avg
    if changed and notify and _on_change is not None:
        _on_change()
    return False


def _schedule(*_args):
    global _pending
    if _pending:
        GLib.source_remove(_pending)
    _pending = GLib.timeout_add(300, _fire)


def _fire():
    global _pending
    _pending = 0
    reload()
    return False


def _on_setting(_settings_obj, key):
    if key in _KEYS:
        _schedule()


def _on_config(key, _value):
    if key == "mica":
        reload()


def _watch_file(path):
    """Bilddatei beobachten – manche Programme überschreiben sie unter
    gleichem Namen, dann ändert sich keine Einstellung."""
    global _monitor
    if _monitor is not None and _monitor[0] == path:
        return
    if _monitor is not None:
        _monitor[1].cancel()
        _monitor = None
    if not path:
        return
    try:
        mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
    except GLib.Error:
        return
    mon.connect("changed", _on_file_changed)
    _monitor = (path, mon)


def _on_file_changed(_mon, _f, _other, event):
    if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                 Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.MOVED_IN,
                 Gio.FileMonitorEvent.RENAMED):
        _schedule()
