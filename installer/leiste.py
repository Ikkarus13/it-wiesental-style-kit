"""Cinnamon-Leiste anpassen (org.cinnamon enabled-applets).

Einträge haben die Form «panel1:right:4:tray-collapse@sascha:30»
(Leiste, Bereich, Reihenfolge, UUID, Instanz-ID). Cinnamon lädt Änderungen
selbst nach. Zum Testen: GSETTINGS_BACKEND=memory.
"""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

TRAY_COLLAPSE = "tray-collapse@sascha"
TRAY_NACHBARN = ("xapp-status@cinnamon.org", "systray@cinnamon.org")
FENSTERLISTE = "grouped-window-list@cinnamon.org"
MENUS = ("startklar@sascha", "Cinnamenu@json", "menu@cinnamon.org")


def _settings():
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup("org.cinnamon", True) is None:
        return None
    return Gio.Settings.new("org.cinnamon")


def _parse(eintrag):
    # Cinnamons Vorgabe hat noch keine Instanz-ID («panel1:left:0:menu@cinnamon.org»)
    t = eintrag.split(":")
    if len(t) == 4:
        t.append("")
    if len(t) < 5:
        return None
    try:
        return [t[0], t[1], int(t[2]), t[3], t[4]]
    except ValueError:
        return None


def _join(p):
    return ":".join([p[0], p[1], str(p[2]), p[3]] + ([p[4]] if p[4] else []))


def _neue_id(s, applets):
    ids = [int(p[4]) for p in map(_parse, applets) if p and p[4].isdigit()]
    return max(ids + [s.get_int("next-applet-id") - 1]) + 1


def ids_ergaenzen():
    """Einträgen ohne Instanz-ID (Cinnamons Vorgabe vor der ersten Anmeldung)
    eine geben – sonst erkennen Startklar & Co. das alte Menü nicht."""
    s = _settings()
    if s is None:
        return
    applets = list(s.get_strv("enabled-applets"))
    naechste = _neue_id(s, applets)
    out = []
    for a in applets:
        p = _parse(a)
        if p and not p[4]:
            p[4] = str(naechste)
            naechste += 1
            a = _join(p)
        out.append(a)
    if out != applets:
        s.set_strv("enabled-applets", out)
        s.set_int("next-applet-id", naechste)
        Gio.Settings.sync()


def applet_hinzufuegen_vor_tray(uuid=TRAY_COLLAPSE):
    """Applet links neben den Infobereich setzen. Gibt einen Text zurück."""
    s = _settings()
    if s is None:
        return "Keine Cinnamon-Einstellungen – Leiste nicht geändert."
    applets = list(s.get_strv("enabled-applets"))
    teile = [_parse(a) for a in applets]
    if any(p and p[3] == uuid for p in teile):
        return "Ist schon in der Leiste."
    nachbarn = [p for p in teile if p and p[3] in TRAY_NACHBARN]
    if nachbarn:
        ziel = min(nachbarn, key=lambda p: p[2])
        leiste, bereich, pos = ziel[0], ziel[1], ziel[2]
    else:
        leiste, bereich, pos = "panel1", "right", 0
    neu_id = _neue_id(s, applets)
    out = []
    for a, p in zip(applets, teile):
        if p and p[0] == leiste and p[1] == bereich and p[2] >= pos:
            p[2] += 1
            out.append(_join(p))
        else:
            out.append(a)
    out.append(_join([leiste, bereich, pos, uuid, str(neu_id)]))
    s.set_strv("enabled-applets", out)
    s.set_int("next-applet-id", neu_id + 1)
    Gio.Settings.sync()
    return f"In die Leiste gesetzt ({leiste}, {bereich})."


def applet_entfernen(uuid):
    s = _settings()
    if s is None:
        return
    s.set_strv("enabled-applets", [a for a in s.get_strv("enabled-applets")
                                   if (_parse(a) or [None] * 5)[3] != uuid])
    Gio.Settings.sync()


def mittig(sicherung):
    """Startknopf und Fensterliste der Hauptleiste in die Mitte setzen."""
    s = _settings()
    if s is None:
        return "Keine Cinnamon-Einstellungen – Leiste nicht geändert."
    applets = list(s.get_strv("enabled-applets"))
    try:
        with open(sicherung, "x", encoding="utf-8") as f:
            f.write("\n".join(applets) + "\n")
    except FileExistsError:
        pass
    out, haupt = [], None
    for a in applets:
        p = _parse(a)
        if p and p[3] in MENUS and haupt is None:
            haupt = p[0]
    haupt = haupt or "panel1"
    for a in applets:
        p = _parse(a)
        if p and p[0] == haupt and p[3] in MENUS:
            p[1], p[2] = "center", 0
            a = _join(p)
        elif p and p[0] == haupt and p[3] == FENSTERLISTE:
            p[1], p[2] = "center", 1
            a = _join(p)
        out.append(a)
    if out != applets:
        s.set_strv("enabled-applets", out)
        Gio.Settings.sync()
        return "Startknopf und Fensterliste stehen jetzt in der Mitte."
    return "Leiste war schon mittig."


def wiederherstellen(sicherung):
    s = _settings()
    try:
        with open(sicherung, encoding="utf-8") as f:
            alt = [z.strip() for z in f if z.strip()]
    except OSError:
        return False
    if s is not None and alt:
        s.set_strv("enabled-applets", alt)
        Gio.Settings.sync()
    return True
