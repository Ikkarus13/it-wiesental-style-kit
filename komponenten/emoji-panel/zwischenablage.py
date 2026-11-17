"""Komplette Zwischenablage sichern und wiederherstellen – mit allen Formaten.

So bleiben z. B. in Nemo kopierte Dateien, formatierter Text (HTML/RTF) oder Bilder nach
dem Einfügen eines Emoji erhalten, nicht nur der reine Text.
"""
from gi.repository import Gdk, Gtk

# Meta-Ziele, die GTK selbst beantwortet oder die keine Daten tragen
SKIP = {'TARGETS', 'MULTIPLE', 'TIMESTAMP', 'SAVE_TARGETS', 'DELETE', 'INSERT_PROPERTY',
        'INSERT_SELECTION'}
MAX_BYTES = 64 * 1024 * 1024
TEXT_TARGETS = ('UTF8_STRING', 'text/plain;charset=utf-8', 'STRING', 'TEXT', 'text/plain')


class Snapshot:
    """Inhalt der Zwischenablage als Liste (Ziel, Typ, Format, Daten)."""

    def __init__(self, items):
        self.items = items
        self.text = None
        for name in TEXT_TARGETS:
            for atom, _typ, _fmt, data in items:
                if atom.name() == name:
                    try:
                        self.text = data.decode('utf-8')
                    except UnicodeDecodeError:
                        self.text = data.decode('latin-1')
                    return

    @classmethod
    def take(cls, cb):
        """Liest alle Formate; None bei leerer, zu grosser oder nicht lesbarer Zwischenablage."""
        ok, atoms = cb.wait_for_targets()
        if not ok or not atoms:
            return None
        names = [a.name() for a in atoms]
        images = [n for n in names if n.startswith('image/')]
        # Bildbesitzer rechnen jedes Format einzeln um – PNG genügt praktisch allen Programmen
        keep_image = 'image/png' if 'image/png' in images else (images[0] if images else None)
        items, total = [], 0
        for atom in atoms:
            name = atom.name()
            if name in SKIP or (name.startswith('image/') and name != keep_image):
                continue
            sd = cb.wait_for_contents(atom)
            if sd is None or sd.get_length() < 0:
                continue
            data = sd.get_data() or b''
            total += len(data)
            if total > MAX_BYTES:
                return None
            items.append((atom, sd.get_data_type(), sd.get_format(), bytes(data)))
        return cls(items) if items else None


class Owner:
    """Stellt einen Snapshot als neuer Besitzer der Zwischenablage bereit."""

    def __init__(self):
        self.widget = Gtk.Invisible()
        self.items = {}
        self.widget.connect('selection-get', self._get)

    def restore(self, snap):
        sel = Gdk.SELECTION_CLIPBOARD
        Gtk.selection_clear_targets(self.widget, sel)
        self.items = {}
        for i, item in enumerate(snap.items):
            Gtk.selection_add_target(self.widget, sel, item[0], i)
            self.items[i] = item
        return Gtk.selection_owner_set(self.widget, sel, Gdk.CURRENT_TIME)

    def _get(self, widget, sd, info, time):
        item = self.items.get(info)
        if item:
            sd.set(item[1], item[2], item[3])
