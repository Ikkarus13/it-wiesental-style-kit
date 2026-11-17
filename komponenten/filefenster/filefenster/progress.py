"""Fortschrittsfenster für Kopieren, Verschieben und Löschen wie im
Windows-11-Explorer: "37 % abgeschlossen", Anhalten/Abbrechen, "Mehr Details"
(Geschwindigkeitskurve, Name, Restzeit, verbleibende Elemente) und die
Rückfrage bei Namenskonflikten (Ersetzen / Überspringen / Beide behalten).

    watch(job, parent)   – Vorgang beobachten (ruft fileops beim Start auf)
    unwatch(job)         – Vorgang fertig (ruft fileops im Hauptthread auf)

Das Fenster erscheint erst, wenn ein Vorgang länger als SHOW_DELAY_MS dauert
oder eine Rückfrage offen ist. Mehrere Vorgänge stehen untereinander. Das
Schliessen des Fensters bricht alle Vorgänge ab (wie Windows).
"""
import time

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import config, icons, places, theme

SHOW_DELAY_MS = 700
TICK_MS = 120
GREEN = "#06b025"
PAUSED = "#c29c00"

_win = None


def watch(job, parent=None):
    global _win
    if _win is None:
        _win = ProgressWindow()
    _win.add_job(job, parent)


def unwatch(job):
    if _win is not None:
        _win.remove_job(job)


# ---------------------------------------------------------------------------
# Formatierung
# ---------------------------------------------------------------------------
def _elements(n):
    return "1 Element" if n == 1 else f"{n} Elementen"


def _remaining_text(sec):
    if sec is None:
        return "Wird berechnet…"
    if sec < 5:
        return "Wenige Sekunden"
    if sec < 60:
        return f"Ungefähr {max(5, int(round(sec / 5.0)) * 5)} Sekunden"
    m = int(round(sec / 60.0))
    if m < 60:
        return "Ungefähr 1 Minute" if m == 1 else f"Ungefähr {m} Minuten"
    h, m = divmod(m, 60)
    hs = "1 Stunde" if h == 1 else f"{h} Stunden"
    return f"Ungefähr {hs} und {m} Minuten" if m else f"Ungefähr {hs}"


def _when(info):
    if info is None or not info.has_attribute("time::modified"):
        return ""
    return places.format_date(info.get_attribute_uint64("time::modified"))


def _loc_name(gfile):
    if gfile is None:
        return ""
    try:
        return places.display_name(gfile.get_uri())
    except Exception:
        return gfile.get_basename() or gfile.get_uri()


def _rgba(cr, color, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(color)
    cr.set_source_rgba(c.red, c.green, c.blue, alpha)


def _icon_button(name, tooltip):
    b = Gtk.Button()
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.get_style_context().add_class("job-btn")
    b._img = icons.fluent(name, 16)
    b.add(b._img)
    b.set_tooltip_text(tooltip)
    b.set_valign(Gtk.Align.CENTER)
    return b


# ---------------------------------------------------------------------------
# Ein Vorgang
# ---------------------------------------------------------------------------
class JobPanel(Gtk.Box):
    def __init__(self, job, parent):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.job = job
        self.parent_win = parent
        self.created = time.monotonic()
        self.samples = []        # (Zeit, Bytes) der letzten Sekunden
        self.history = []        # (Anteil, Bytes/s) für die Kurve
        self.speed = 0.0
        self._conflict_req = None
        self._cancelling = False

        self.sep = Gtk.Separator()
        self.pack_start(self.sep, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.get_style_context().add_class("job")
        self.pack_start(body, True, True, 0)

        self.head = Gtk.Label(xalign=0)
        self.head.set_line_wrap(True)
        self.head.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.head.set_max_width_chars(52)
        self.head.get_style_context().add_class("job-head")
        body.pack_start(self.head, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pct = Gtk.Label(xalign=0)
        self.pct.get_style_context().add_class("job-pct")
        row.pack_start(self.pct, True, True, 0)
        self.btn_cancel = _icon_button("dismiss", "Abbrechen")
        self.btn_cancel.connect("clicked", lambda *_: self.cancel())
        row.pack_end(self.btn_cancel, False, False, 0)
        self.btn_pause = _icon_button("pause", "Anhalten")
        self.btn_pause.connect("clicked", self._on_pause)
        row.pack_end(self.btn_pause, False, False, 0)
        body.pack_start(row, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.bar.get_style_context().add_class("job-bar")
        body.pack_start(self.bar, False, False, 0)

        self.graph = Gtk.DrawingArea()
        self.graph.set_size_request(-1, 72)
        self.graph.connect("draw", self._draw_graph)
        body.pack_start(self.graph, False, False, 0)

        self.info = Gtk.Grid(column_spacing=12, row_spacing=4)
        self.info.get_style_context().add_class("job-info")
        self._vals = {}
        for i, key in enumerate(("Name:", "Verbleibende Zeit:", "Verbleibende Elemente:")):
            k = Gtk.Label(label=key, xalign=0)
            k.get_style_context().add_class("key")
            v = Gtk.Label(xalign=0)
            v.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            v.set_max_width_chars(40)
            v.set_hexpand(True)
            self.info.attach(k, 0, i, 1, 1)
            self.info.attach(v, 1, i, 1, 1)
            self._vals[key] = v
        body.pack_start(self.info, False, False, 0)

        self.conflict_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.conflict_box.get_style_context().add_class("conflict")
        body.pack_start(self.conflict_box, False, False, 0)
        self.show_all()

    # -- Bedienung --------------------------------------------------------
    def cancel(self):
        self._cancelling = True
        self.job.cancel()
        self.btn_pause.set_sensitive(False)
        self.btn_cancel.set_sensitive(False)

    def _on_pause(self, *_):
        if self.job.paused:
            self.job.resume()
        else:
            self.job.pause()
        self._sync_pause()

    def _sync_pause(self):
        paused = self.job.paused
        self.btn_pause._img.set_from_pixbuf(icons.fluent_pixbuf("play" if paused else "pause", 16))
        self.btn_pause.set_tooltip_text("Fortsetzen" if paused else "Anhalten")
        ctx = self.get_style_context()
        (ctx.add_class if paused else ctx.remove_class)("paused")

    # -- Texte ------------------------------------------------------------
    def _header_markup(self):
        job = self.job
        acc = theme.P["accent"]

        def link(text):
            return f'<span foreground="{acc}">{GLib.markup_escape_text(places.safe_text(text or ""))}</span>'
        n = job.total_items if job.phase != "scan" else 0
        if job.kind == "restore":
            n = n or len(job.sources)
            return f"Wiederherstellen von {_elements(n)} aus {link('Papierkorb')}"
        if job.kind in ("copy", "move"):
            n = n or len(job.sources)
            src = _loc_name(job.sources[0].get_parent()) if job.sources else ""
            dest = job.dest or (job.targets[0].get_parent() if job.targets else None)
            verb = "Kopiervorgang" if job.kind == "copy" else "Verschiebevorgang"
            return f"{verb} von {_elements(n)} von {link(src)} nach {link(_loc_name(dest))}"
        if job.kind == "zip":
            n = n or len(job.sources)
            src = _loc_name(job.sources[0].get_parent()) if job.sources else ""
            return f"Komprimieren von {_elements(n)} von {link(src)} nach {link(job.dest.get_basename())}"
        if job.kind == "extract":
            what = f"von {_elements(n)} " if n else ""
            return (f"Extrahieren {what}aus {link(job.archive.get_basename())} "
                    f"nach {link(_loc_name(job.dest))}")
        if job.kind == "zipupdate":     # Umbenennen/Neuer Ordner in einer ZIP-Datei
            return f"Aktualisieren von {link(job.zip.get_basename())}"
        n = n or len(job.files)
        src = _loc_name(job.files[0].get_parent()) if job.files else ""
        if job.kind == "trash":
            return f"Verschiebevorgang von {_elements(n)} von {link(src)} nach {link('Papierkorb')}"
        return f"Löschvorgang von {_elements(n)} aus {link(src)}"

    def title(self):
        job = self.job
        if job.conflict is not None:
            return "Dateien ersetzen oder überspringen"
        if job.phase == "scan":
            return "Wird berechnet…"
        return f"{int(job.fraction() * 100)} % abgeschlossen"

    # -- Aktualisieren (Takt des Fensters) --------------------------------
    def update(self, details):
        job = self.job
        now = time.monotonic()
        frac = job.fraction()
        running = job.phase == "run" and not job.paused and job.conflict is None
        if running:
            self.samples.append((now, job.done_bytes))
            self.samples = [s for s in self.samples if now - s[0] <= 3.0]
            t0, b0 = self.samples[0]
            if now - t0 >= 0.5:
                self.speed = max(0.0, (job.done_bytes - b0) / (now - t0))
            if not self.history or frac > self.history[-1][0]:
                self.history.append((frac, self.speed))
        else:
            self.samples = []

        self.head.set_markup(self._header_markup())
        if self._cancelling:
            self.pct.set_text("Wird abgebrochen…")
        elif job.phase == "scan":
            self.pct.set_text("Wird berechnet…")
        elif job.paused:
            self.pct.set_text(f"Angehalten – {int(frac * 100)} % abgeschlossen")
        else:
            self.pct.set_text(f"{int(frac * 100)} % abgeschlossen")
        if job.phase == "scan":
            self.bar.pulse()
        else:
            self.bar.set_fraction(frac)

        has_bytes = job.total_bytes > 0
        in_conflict = job.conflict is not None
        self.bar.set_visible(not in_conflict and not (details and has_bytes and job.phase == "run"))
        self.graph.set_visible(not in_conflict and details and has_bytes and job.phase == "run")
        self.info.set_visible(not in_conflict and details)
        self.conflict_box.set_visible(in_conflict)
        self.btn_pause.set_visible(not in_conflict)
        if details and not in_conflict:
            left_items = max(0, job.total_items - job.done_items)
            left_bytes = max(0, job.total_bytes - job.done_bytes)
            self._vals["Name:"].set_text(places.safe_text(job.current or ""))
            remain = None
            if job.phase == "run" and self.speed > 0 and has_bytes:
                remain = left_bytes / self.speed
            elif job.paused:
                remain = None
            self._vals["Verbleibende Zeit:"].set_text(
                "Angehalten" if job.paused else _remaining_text(remain))
            items = str(left_items) if job.phase != "scan" else "…"
            if has_bytes and job.phase != "scan":
                items += f" ({places.format_size(left_bytes)})"
            self._vals["Verbleibende Elemente:"].set_text(items)
            if self.graph.get_visible():
                self.graph.queue_draw()
        if in_conflict and job.conflict is not self._conflict_req:
            self._build_conflict(job.conflict)
        elif not in_conflict:
            self._conflict_req = None

    # -- Konflikt ---------------------------------------------------------
    def _build_conflict(self, req):
        self._conflict_req = req
        for c in self.conflict_box.get_children():
            c.destroy()
        sinfo, dinfo = req["sinfo"], req["dinfo"]
        name = places.safe_text(dinfo.get_display_name())
        is_file = dinfo.get_file_type() != Gio.FileType.DIRECTORY
        what = "eine Datei" if is_file else "einen Ordner"
        msg = Gtk.Label(xalign=0)
        msg.set_line_wrap(True)
        msg.set_max_width_chars(52)
        msg.set_markup(f"Das Ziel enthält bereits {what} namens "
                       f"\"<b>{GLib.markup_escape_text(name)}</b>\".")
        msg.get_style_context().add_class("conflict-msg")
        self.conflict_box.pack_start(msg, False, False, 0)

        def facts(label, info):
            parts = []
            if info.get_file_type() != Gio.FileType.DIRECTORY:
                parts.append(places.format_size(info.get_size()))
            when = _when(info)
            if when:
                parts.append(f"Änderungsdatum: {when}")
            return f"{label}: " + " · ".join(parts) if parts else ""
        cmp_text = "\n".join(t for t in (facts("Quelle", sinfo), facts("Ziel", dinfo)) if t)
        if cmp_text:
            cmp_lab = Gtk.Label(label=cmp_text, xalign=0)
            cmp_lab.get_style_context().add_class("conflict-facts")
            self.conflict_box.pack_start(cmp_lab, False, False, 4)

        noun = "Datei" if sinfo.get_file_type() != Gio.FileType.DIRECTORY else "Element"
        choices = [
            ("checkmark", f"Die {noun} im Ziel ersetzen", "replace", req["can_replace"]),
            ("arrow_redo", f"Diese {noun} überspringen", "skip", True),
            ("copy", f"Beide {noun}en behalten" if noun == "Datei" else "Beide Elemente behalten",
             "keep", True),
        ]
        self._apply_all = Gtk.CheckButton(label="Für alle weiteren Konflikte übernehmen")
        self._apply_all.get_style_context().add_class("conflict-all")
        for icon, text, key, ok in choices:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("conflict-choice")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.pack_start(icons.fluent(icon, 20, color=theme.P["accent"]), False, False, 0)
            lab = Gtk.Label(label=text, xalign=0)
            box.pack_start(lab, True, True, 0)
            b.add(box)
            b.set_sensitive(ok)
            if not ok:
                b.set_tooltip_text("Ein Ordner und eine Datei können sich nicht ersetzen.")
            b.connect("clicked", lambda _b, k=key: self.job.answer(k, self._apply_all.get_active()))
            self.conflict_box.pack_start(b, False, False, 0)
        self.conflict_box.pack_start(self._apply_all, False, False, 6)
        self.conflict_box.show_all()

    # -- Geschwindigkeitskurve --------------------------------------------
    def _draw_graph(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        P = theme.P
        _rgba(cr, P["content_bg"])
        cr.rectangle(0, 0, w, h)
        cr.fill()
        speeds = [s for _f, s in self.history] or [0.0]
        top = max(max(speeds), self.speed, 1.0) * 1.3
        frac = self.job.fraction()
        if self.history:
            cr.move_to(0, h)
            for f, s in self.history:
                cr.line_to(f * w, h - s / top * h)
            cr.line_to(frac * w, h - self.speed / top * h)
            cr.line_to(frac * w, h)
            cr.close_path()
            _rgba(cr, PAUSED if self.job.paused else GREEN, 0.35)
            cr.fill()
        y = h - self.speed / top * h
        _rgba(cr, P["text"], 0.75)
        cr.set_line_width(1)
        cr.move_to(0, int(y) + 0.5)
        cr.line_to(w, int(y) + 0.5)
        cr.stroke()
        layout = area.create_pango_layout(
            f"Geschwindigkeit: {places.format_size(int(self.speed))}/s")
        tw, th = layout.get_pixel_size()
        ty = y - th - 2 if y - th - 2 > 0 else y + 2
        Gtk.render_layout(area.get_style_context(), cr, w - tw - 6, ty, layout)
        _rgba(cr, P["control_border"])
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()
        return False


# ---------------------------------------------------------------------------
# Fenster
# ---------------------------------------------------------------------------
class ProgressWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="FileFenster")
        self.set_resizable(False)
        self.set_icon(icons.folder_pixbuf(64))
        ctx = self.get_style_context()
        for c in ("filefenster", "win-dialog", "progress-dialog"):
            ctx.add_class(c)
        self.panels = []
        self._timer = None
        self._placed = False
        self._details = bool(config.get_config().get("progress_details", False))
        self.connect("delete-event", self._on_delete)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list.set_size_request(480, -1)
        outer.pack_start(self.list, True, True, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.get_style_context().add_class("win-footer")
        self.toggle = Gtk.Button()
        self.toggle.set_relief(Gtk.ReliefStyle.NONE)
        self.toggle.get_style_context().add_class("details-toggle")
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toggle_img = icons.fluent("chevron_down", 16)
        self.toggle_lab = Gtk.Label()
        tb.pack_start(self.toggle_img, False, False, 0)
        tb.pack_start(self.toggle_lab, False, False, 0)
        self.toggle.add(tb)
        self.toggle.connect("clicked", self._on_toggle)
        footer.pack_start(self.toggle, False, False, 12)
        outer.pack_end(footer, False, False, 0)
        outer.show_all()
        self._sync_toggle()

    def _sync_toggle(self):
        self.toggle_img.set_from_pixbuf(
            icons.fluent_pixbuf("chevron_up" if self._details else "chevron_down", 16))
        self.toggle_lab.set_text("Weniger Details" if self._details else "Mehr Details")

    def _on_toggle(self, *_):
        self._details = not self._details
        config.get_config().set("progress_details", self._details)
        self._sync_toggle()
        self._tick()
        self.resize(1, 1)

    def add_job(self, job, parent):
        p = JobPanel(job, parent)
        self.panels.append(p)
        self.list.pack_start(p, False, False, 0)
        self._relabel()
        p.update(self._details)
        if self._timer is None:
            self._timer = GLib.timeout_add(TICK_MS, self._tick)

    def remove_job(self, job):
        for p in list(self.panels):
            if p.job is job:
                self.panels.remove(p)
                p.destroy()
        self._relabel()
        if not self.panels:
            self.hide()
            self._placed = False
            if self._timer is not None:
                GLib.source_remove(self._timer)
                self._timer = None
        else:
            self.resize(1, 1)

    def _relabel(self):
        for i, p in enumerate(self.panels):
            p.sep.set_visible(i > 0)

    def _on_delete(self, *_):
        for p in self.panels:
            p.cancel()
        return True  # Fenster verschwindet, sobald die Vorgänge enden

    def _tick(self):
        if not self.panels:
            self._timer = None
            return False
        now = time.monotonic()
        want = False
        conflict = False
        for p in self.panels:
            try:
                p.update(self._details)
            except Exception as exc:    # nie den Takt verlieren (sonst hängt ein Job ewig)
                print("[filefenster] Fortschritt:", exc)
            if p.job.conflict is not None:
                want = conflict = True
            elif now - p.created >= SHOW_DELAY_MS / 1000.0:
                want = True
        if len(self.panels) == 1:
            self.set_title(self.panels[0].title())
        else:
            self.set_title(f"{len(self.panels)} Vorgänge werden ausgeführt")
        if want and not self.get_visible():
            self._place()
            self.show()
            self.present()
        elif conflict and not self.is_active() and not getattr(self, "_presented", False):
            self.present()
        self._presented = conflict
        return True

    def _place(self):
        if self._placed:
            return
        self._placed = True
        parent = next((p.parent_win for p in self.panels
                       if p.parent_win is not None and p.parent_win.get_mapped()), None)
        if parent is None:
            self.set_position(Gtk.WindowPosition.CENTER)
            return
        px, py = parent.get_position()
        pw, ph = parent.get_size()
        _m, nat = self.get_preferred_size()
        self.move(px + max(0, (pw - nat.width) // 2), py + max(0, (ph - nat.height) // 3))
