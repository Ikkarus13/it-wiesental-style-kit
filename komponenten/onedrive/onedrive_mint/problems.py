"""Fenster „Synchronisierungsprobleme“ – wie „Synchronisierungsprobleme anzeigen“ unter Windows."""

import os
import re
import time

from gi.repository import GLib, Gtk, Pango

from .errors import explain, technical
from . import marke

def _ago(ts):
    if not ts:
        return ""
    d = time.time() - ts
    if d < 60:
        return "gerade eben"
    if d < 3600:
        return f"vor {int(d // 60)} Min."
    if d < 86400:
        return f"vor {int(d // 3600)} Std."
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


class ProblemsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=f"{marke.NAME} – Synchronisierungsprobleme")
        self.app = app
        self.set_default_size(640, 460)
        self.set_icon_name("onedrive-mint")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("delete-event", lambda *_: self.hide() or True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=14)
        self.add(outer)
        self.header = Gtk.Label(xalign=0)
        outer.pack_start(self.header, False, False, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        empty = Gtk.Label(label="Keine Synchronisierungsprobleme. Alles in Ordnung.")
        empty.set_margin_top(40)
        empty.show()
        self.listbox.set_placeholder(empty)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.listbox)
        outer.pack_start(sw, True, True, 0)

        bottom = Gtk.Box(spacing=6)
        retry_all = Gtk.Button(label="Alle erneut versuchen")
        retry_all.connect("clicked", lambda *_: self._for_all(retry=True))
        dismiss_all = Gtk.Button(label="Alle ausblenden")
        dismiss_all.connect("clicked", lambda *_: self._for_all(retry=False))
        logs = Gtk.Button(label="Protokolle öffnen")
        logs.connect("clicked", lambda *_: self.app.open_logs())
        bottom.pack_start(retry_all, False, False, 0)
        bottom.pack_start(dismiss_all, False, False, 0)
        bottom.pack_end(logs, False, False, 0)
        outer.pack_start(bottom, False, False, 0)
        self._sig = None

    def present_refreshed(self):
        self.refresh(force=True)
        self.show_all()
        self.present()

    def _for_all(self, retry):
        eng = self.app.engine
        for p in eng.problems():
            if p["kind"] == "backup":
                if retry:
                    eng.backup.sync_now()
                else:
                    eng.backup.errors.pop(p["backup_key"], None)
            elif retry:
                eng.retry_problem(p["acc_id"], p["rel"])
            else:
                eng.dismiss_problem(p["acc_id"], p["rel"])
        GLib.timeout_add(500, lambda: self.refresh(force=True) and False)

    def refresh(self, force=False):
        problems = self.app.engine.problems()
        sig = tuple((p["acc_id"], p["rel"], p["error"]) for p in problems)
        if sig == self._sig and not force:
            return
        self._sig = sig
        n = len(problems)
        self.header.set_markup(
            "<b>Alles in Ordnung</b>" if not n else
            f"<b>{n} {'Problem' if n == 1 else 'Probleme'} bei der Synchronisierung</b>")
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        for p in problems:
            self.listbox.add(self._row(p))
        self.listbox.show_all()

    def _row(self, p):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, border_width=10)
        head = Gtk.Box(spacing=8)
        head.pack_start(Gtk.Image.new_from_icon_name(
            "emblem-odm-error" if p["kind"] == "file" else "dialog-warning", Gtk.IconSize.DND),
            False, False, 0)
        title = Gtk.Label(xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name = os.path.basename(p["rel"]) if p["rel"] else p["account"]
        where = os.path.dirname(p["rel"]) if p["rel"] else "Konto"
        if p["rel"]:
            sub = p["account"] + (" › " + where if where else "")
        else:
            sub = "Verbindungs- oder Kontoproblem"
        title.set_markup(f"<b>{GLib.markup_escape_text(name)}</b>\n<small>"
                         f"{GLib.markup_escape_text(sub)}"
                         f"{'  ·  ' + _ago(p['time']) if p['time'] else ''}</small>")
        head.pack_start(title, True, True, 0)
        box.pack_start(head, False, False, 0)

        expl = Gtk.Label(label=explain(p["error"]), xalign=0)
        expl.set_line_wrap(True)
        expl.set_max_width_chars(70)
        box.pack_start(expl, False, False, 0)

        details = Gtk.Expander(label="Technische Details")
        tech = Gtk.Label(label=technical(p["error"]), xalign=0, selectable=True)
        tech.set_line_wrap(True)
        tech.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        tech.set_max_width_chars(80)
        tech.get_style_context().add_class("dim-label")
        details.add(tech)
        box.pack_start(details, False, False, 0)

        btns = Gtk.Box(spacing=6)
        eng = self.app.engine
        actions = []
        if p["kind"] == "backup":
            actions.append(("Jetzt abgleichen", eng.backup.sync_now))
            actions.append(("Einstellungen", self.app.show_settings))
        elif p["kind"] == "file":
            if os.path.lexists(p["path"]):
                actions.append(("Im Ordner anzeigen", lambda: self.app.reveal(p["path"])))
            actions.append(("Erneut versuchen", lambda: eng.retry_problem(p["acc_id"], p["rel"])))
            actions.append(("Ausblenden", lambda: eng.dismiss_problem(p["acc_id"], p["rel"])))
        else:
            rt = eng.runtimes.get(p["acc_id"])
            if rt and re.search(r"Anmeldung|invalid_grant|token", p["error"], re.I):
                actions.append(("Neu anmelden", lambda: self.app.reauth(rt.account, self)))
            actions.append(("Neu verbinden", lambda: eng.retry_problem(p["acc_id"], "")))
            actions.append(("Einstellungen", self.app.show_settings))
        for label, cb in actions:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, c=cb: (c(), GLib.timeout_add(
                400, lambda: self.refresh(force=True) and False)))
            btns.pack_start(b, False, False, 0)
        box.pack_start(btns, False, False, 0)
        row.add(box)
        return row
