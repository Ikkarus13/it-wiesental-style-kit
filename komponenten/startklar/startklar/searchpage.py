"""Suchansicht: Filter oben, Ergebnisse links, Details mit Aktionen rechts –
wie die Windows-Suche im Startmenü."""
import os
import time

from gi.repository import Gio, GLib, Gtk, Pango

from . import files, icons, session, taskbar, ui
from .apps import fold
from .config import get_config

FILTERS = [("all", "Alle"), ("apps", "Apps"), ("docs", "Dokumente"),
           ("folders", "Ordner"), ("settings", "Einstellungen"), ("web", "Web")]
LIMITS_ALL = {"apps": 6, "settings": 4, "docs": 6, "folders": 4}
LIMITS_ONE = {"apps": 40, "settings": 30, "docs": 25, "folders": 20}
GROUP_TITLES = {"apps": "Apps", "settings": "Einstellungen", "docs": "Dokumente",
                "folders": "Ordner", "web": "Im Web suchen"}


class Result:
    __slots__ = ("kind", "title", "type", "gicon", "obj", "subtitle")

    def __init__(self, kind, title, type_, gicon, obj, subtitle=None):
        self.kind, self.title, self.type, self.gicon, self.obj = kind, title, type_, gicon, obj
        self.subtitle = subtitle


def _short(path):
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def _bold_match(text, query):
    """Markup mit fett gesetztem Treffer (Vergleich ohne Gross/klein/Akzente)."""
    esc = GLib.markup_escape_text
    q = fold(query).strip()
    if not q:
        return esc(text)
    # Position in der gefalteten Form → gleiche Zeichenzahl nur bei einfachen
    # Texten; sonst ohne Hervorhebung
    folded = fold(text)
    pos = folded.find(q)
    if pos < 0 or len(folded) != len(text):
        return esc(text)
    return esc(text[:pos]) + "<b>" + esc(text[pos:pos + len(q)]) + "</b>" + esc(text[pos + len(q):])


class SearchPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.filter = "all"
        self.query = ""
        self.results = []          # flache Liste in Anzeige-Reihenfolge
        self.buttons = []
        self.selected = 0
        self.set_margin_start(24)
        self.set_margin_end(24)
        self.set_margin_bottom(20)

        chips = Gtk.Box(spacing=4)
        chips.set_margin_start(4)
        chips.set_margin_bottom(10)
        self.chips = {}
        for key, text in FILTERS:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            ui.add_class(b, "chip")
            b.add(ui.label(text, ellipsize=False))
            b.connect("clicked", lambda _b, k=key: self.set_filter(k))
            col.pack_start(b, False, False, 0)
            line = Gtk.Box()
            ui.add_class(line, "chipline")
            line.set_halign(Gtk.Align.CENTER)
            col.pack_start(line, False, False, 0)
            chips.pack_start(col, False, False, 0)
            self.chips[key] = (b, line)
        self.pack_start(chips, False, False, 0)

        body = Gtk.Box(spacing=12)
        self.list_sw = Gtk.ScrolledWindow()
        self.list_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_sw.set_size_request(410, -1)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_sw.add(self.list_box)
        body.pack_start(self.list_sw, False, False, 0)
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ui.add_class(self.detail, "detail")
        body.pack_start(self.detail, True, True, 0)
        self.pack_start(body, True, True, 0)
        self._update_chips()

    # ------------------------------------------------------------- Filter
    def set_filter(self, key):
        self.filter = key
        self._update_chips()
        self.update(self.query)

    def _update_chips(self):
        for key, (b, line) in self.chips.items():
            ctx = b.get_style_context()
            if key == self.filter:
                ctx.add_class("active")
                line.set_opacity(1)
            else:
                ctx.remove_class("active")
                line.set_opacity(0)

    def reset(self):
        self.filter = "all"
        self.chips["web"][0].get_parent().set_visible(bool(get_config().get("web_search")))
        self._update_chips()

    # ------------------------------------------------------------- Suche
    def collect(self, query):
        """Ergebnisse je Gruppe: {kind: [Result]}."""
        win = self.win
        out = {}
        f = self.filter
        limits = LIMITS_ALL if f == "all" else LIMITS_ONE
        if f in ("all", "apps"):
            out["apps"] = [Result("apps", e.name, "App", e.gicon, e)
                           for _s, e in win.index.search(query)[:limits["apps"]]]
        if f in ("all", "settings"):
            out["settings"] = [Result("settings", e.name, "Systemeinstellungen", e.gicon, e,
                                      e.description or None)
                               for _s, e in win.index.search(query, settings=True)[:limits["settings"]]]
        if f in ("all", "docs", "folders"):
            q = fold(query).strip()
            recent = [r for r in win.recent() if q and q in fold(r.name)]
            docs, dirs = [], []
            seen = set()
            for r in recent:
                (dirs if r.is_dir else docs).append(r.path)
                seen.add(r.path)
            if get_config().get("search_files"):
                fi, di = win.file_index.search(query, limits["docs"], limits["folders"])
                docs += [p for p in fi if p not in seen]
                dirs += [p for p in di if p not in seen]
            if f in ("all", "docs"):
                out["docs"] = [Result("docs", os.path.basename(p), _file_type(p),
                                      icons.file_gicon(p), p, _short(os.path.dirname(p)))
                               for p in docs[:limits["docs"]]]
            if f in ("all", "folders"):
                out["folders"] = [Result("folders", os.path.basename(p) or p, "Dateiordner",
                                         icons.file_gicon(p, is_dir=True), p,
                                         _short(os.path.dirname(p)))
                                  for p in dirs[:limits["folders"]]]
        if f in ("all", "web") and query.strip() and get_config().get("web_search"):
            out["web"] = [Result("web", query.strip(), "Webergebnisse anzeigen",
                                 Gio.ThemedIcon.new("web-browser"), query.strip())]
        return out

    def update(self, query):
        self.query = query
        groups = self.collect(query)
        for c in self.list_box.get_children():
            c.destroy()
        self.results, self.buttons = [], []
        order = ["apps", "settings", "docs", "folders", "web"]
        best = None
        if self.filter == "all":
            for k in order[:-1]:
                if groups.get(k):
                    best = groups[k].pop(0)
                    break
        if best is not None:
            self._group_head("Höchste Übereinstimmung")
            self._add_result(best, big=True)
        for k in order:
            items = groups.get(k) or []
            if not items:
                continue
            self._group_head(GROUP_TITLES[k])
            for r in items:
                self._add_result(r)
        if not self.results:
            msg = ui.label(f"Keine Ergebnisse für „{query.strip()}“.", "sub", ellipsize=False)
            msg.set_margin_top(16)
            msg.set_margin_start(12)
            self.list_box.pack_start(msg, False, False, 0)
        self.list_box.show_all()
        self.list_sw.get_vadjustment().set_value(0)
        self.select(0)

    def _group_head(self, title):
        lb = ui.label(title, "grouphead")
        self.list_box.pack_start(lb, False, False, 0)

    def _add_result(self, r, big=False):
        idx = len(self.results)
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_can_focus(False)
        ui.add_class(btn, "result")
        outer = Gtk.Box(spacing=0)
        bar = Gtk.Box()
        ui.add_class(bar, "selbar")
        bar.set_size_request(3, 16)
        bar.set_valign(Gtk.Align.CENTER)
        bar.set_opacity(0)
        outer.pack_start(bar, False, False, 0)
        inner = Gtk.Box(spacing=12)
        inner.set_margin_start(9)
        inner.set_margin_end(10)
        img = icons.app_image(r.gicon, 32 if big else 24)
        img.set_valign(Gtk.Align.CENTER)
        inner.pack_start(img, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label()
        t.set_markup(_bold_match(r.title, self.query) if r.kind != "web"
                     else GLib.markup_escape_text(r.title) + " <span alpha='70%'>– "
                     + GLib.markup_escape_text(r.type) + "</span>")
        t.set_xalign(0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        ui.add_class(t, "name13")
        texts.pack_start(t, False, False, 0)
        sub = r.type if big else (r.subtitle if r.kind in ("docs", "folders") else None)
        if sub:
            texts.pack_start(ui.label(sub, "sub"), False, False, 0)
        inner.pack_start(texts, True, True, 0)
        outer.pack_start(inner, True, True, 0)
        btn.add(outer)
        btn.set_size_request(-1, 64 if big else (48 if sub else 38))
        btn.connect("clicked", lambda _b: self.activate(idx))
        btn.connect("enter-notify-event", lambda *_a: None)
        ui.on_context(btn, lambda w, x, y: (self.select(idx), self._context(w, x, y, idx)))
        btn.bar = bar
        self.list_box.pack_start(btn, False, False, 0)
        self.results.append(r)
        self.buttons.append(btn)

    # ------------------------------------------------------------- Auswahl
    def select(self, idx):
        if not self.results:
            self.selected = 0
            self._show_detail(None)
            return
        idx = max(0, min(idx, len(self.results) - 1))
        for n, b in enumerate(self.buttons):
            ctx = b.get_style_context()
            if n == idx:
                ctx.add_class("selected")
                b.bar.set_opacity(1)
            else:
                ctx.remove_class("selected")
                b.bar.set_opacity(0)
        self.selected = idx
        self._show_detail(self.results[idx])
        btn = self.buttons[idx]
        res = btn.translate_coordinates(self.list_box, 0, 0)
        if res:
            adj = self.list_sw.get_vadjustment()
            y, h = res[1], btn.get_allocated_height()
            if y < adj.get_value():
                adj.set_value(max(0, y - 30))
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())

    def move(self, delta):
        self.select(self.selected + delta)

    def activate(self, idx=None):
        if not self.results:
            return
        r = self.results[self.selected if idx is None else idx]
        self._open(r)

    def _open(self, r):
        w = self.win
        if r.kind in ("apps", "settings"):
            w.launch_entry(r.obj)
        elif r.kind in ("docs", "folders"):
            w.open_file(r.obj)
        elif r.kind == "web":
            w.hide_menu()
            session.web_search(get_config().get("web_search"), r.obj)

    # ------------------------------------------------------------- Details
    def _actions(self, r):
        w = self.win
        acts = []
        if r.kind == "apps":
            e = r.obj
            acts.append(("open_20_regular", "Öffnen", lambda: w.launch_entry(e)))
            for aid, name in e.actions():
                acts.append((None, name, lambda a=aid: w.launch_entry(e, a)))
            if e.id in w.pinned:
                acts.append(("pin_off_20_regular", "Vom Start lösen", lambda: w.unpin(e.id)))
            else:
                acts.append(("pin_20_regular", "An Start anheften", lambda: w.pin(e.id)))
            if taskbar.available():
                if taskbar.is_pinned(e.id):
                    acts.append(("pin_off_20_regular", "Von Taskleiste lösen",
                                 lambda: (taskbar.set_pinned(e.id, False), self.select(self.selected))))
                else:
                    acts.append(("taskbar_pin_20_regular", "An Taskleiste anheften",
                                 lambda: (taskbar.set_pinned(e.id, True), self.select(self.selected))))
            if session.can_uninstall():
                acts.append(("delete_20_regular", "Deinstallieren", lambda: w.uninstall(e)))
        elif r.kind == "settings":
            acts.append(("open_20_regular", "Öffnen", lambda: w.launch_entry(r.obj)))
        elif r.kind in ("docs", "folders"):
            p = r.obj
            acts.append(("open_20_regular", "Öffnen", lambda: w.open_file(p)))
            acts.append(("folder_open_20_regular", "Dateispeicherort öffnen",
                         lambda: w.reveal(p)))
            acts.append(("copy_20_regular", "Pfad kopieren", lambda: session.copy_text(p)))
        elif r.kind == "web":
            acts.append(("globe_20_regular", "Im Browser öffnen", lambda: self._open(r)))
        return acts

    def _show_detail(self, r):
        for c in self.detail.get_children():
            c.destroy()
        if r is None:
            self.detail.show_all()
            return
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top.set_margin_top(28)
        top.set_margin_bottom(20)
        top.set_margin_start(16)
        top.set_margin_end(16)
        img = icons.app_image(r.gicon, 64)
        img.set_margin_bottom(12)
        top.pack_start(img, False, False, 0)
        name = ui.label(r.title, "big", xalign=0.5, ellipsize=False, lines=2)
        name.set_justify(Gtk.Justification.CENTER)
        top.pack_start(name, False, False, 0)
        top.pack_start(ui.label(r.type, "sub", xalign=0.5), False, False, 0)
        if r.kind in ("docs", "folders"):
            top.pack_start(self._file_facts(r.obj), False, False, 0)
        elif r.kind == "settings" and r.subtitle:
            d = ui.label(r.subtitle, "sub", xalign=0.5, ellipsize=False, lines=3)
            d.set_justify(Gtk.Justification.CENTER)
            d.set_margin_top(6)
            top.pack_start(d, False, False, 0)
        self.detail.pack_start(top, False, False, 0)
        sep = Gtk.Separator()
        sep.set_margin_start(16)
        sep.set_margin_end(16)
        self.detail.pack_start(sep, False, False, 0)
        acts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        acts.set_margin_top(8)
        acts.set_margin_start(12)
        acts.set_margin_end(12)
        for icon, text, cb in self._actions(r):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            ui.add_class(b, "action")
            inner = Gtk.Box(spacing=12)
            if icon:
                inner.pack_start(icons.fluent(icon, 16), False, False, 0)
            else:
                sp = Gtk.Box()
                sp.set_size_request(16, 16)
                inner.pack_start(sp, False, False, 0)
            inner.pack_start(ui.label(text), True, True, 0)
            b.add(inner)
            b.connect("clicked", lambda _b, cb=cb: cb())
            acts.pack_start(b, False, False, 0)
        self.detail.pack_start(acts, False, False, 0)
        self.detail.show_all()

    def _file_facts(self, path):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(14)
        box.pack_start(ui.label("Speicherort", "dim", xalign=0.5), False, False, 0)
        loc = ui.label(_short(os.path.dirname(path)), xalign=0.5)
        loc.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.pack_start(loc, False, False, 0)
        if not files._under(path, files.foreign_mounts()):
            try:
                mtime = os.stat(path).st_mtime
                box.pack_start(ui.label("Zuletzt geändert", "dim", xalign=0.5), False, False, 4)
                box.pack_start(ui.label(time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime)),
                                        xalign=0.5), False, False, 0)
            except OSError:
                pass
        return box

    def _context(self, w, x, y, idx):
        r = self.results[idx]
        items = [(i, t, cb) for i, t, cb in self._actions(r)]
        ui.popup_menu(self.win.layer, items, self.win.layer.anchor_of(w, x, y))


def _file_type(path):
    ct, _ = Gio.content_type_guess(os.path.basename(path), None)
    desc = Gio.content_type_get_description(ct) if ct else ""
    return desc or "Datei"
