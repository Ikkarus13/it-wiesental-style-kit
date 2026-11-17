"""Updates in der Oberfläche.

* „··· → Nach Updates suchen“ öffnet den Update-Dialog: installierte und neue
  Version, Neuerungen, „Jetzt aktualisieren“ / „Später“, Fortschritt.
* Automatische Suche im Hintergrund höchstens einmal am Tag (config
  update_check), nur in der installierten Kopie. Findet sie etwas, erscheint
  ein kleiner Punkt am „···“-Knopf und der Menüeintrag heisst „Update auf
  Version X verfügbar“ – sonst nichts.
* Nach dem Einsetzen der neuen Version startet FileFenster neu und öffnet alle
  Fenster mit ihren Registerkarten wieder. Laufende Dateivorgänge werden
  vorher abgewartet (oder der Nutzer bricht das Update ab).

Netzwerk und Dateien laufen nur in Threads (updater.py blockiert); Ergebnisse
kommen per GLib.idle_add in den Hauptthread.
"""
import json
import os
import threading
import time

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from . import APP_NAME, VERSION, places, updater
from .config import get_config

START_DELAY = 45        # Sekunden nach dem Start bis zur ersten automatischen Suche
TICK = 3600             # danach stündlich prüfen, ob wieder eine Suche fällig ist
SESSION_MAX_AGE = 300   # Sitzungsdatei vom Neustart gilt so lange


def _running_jobs():
    from . import fileops
    return fileops.running_jobs()


def _jobs_text(n):
    return "1 Dateivorgang läuft" if n == 1 else f"{n} Dateivorgänge laufen"


# ---------------------------------------------------------------------------
# Sitzung (Fenster und Registerkarten) über den Neustart retten
# ---------------------------------------------------------------------------
def session_file():
    return os.path.join(updater.cache_home(), "filefenster", "sitzung.json")


def collect_session(app):
    wins = []
    for w in app.get_windows():
        tabs = getattr(w, "tabs", None)
        if not tabs:
            continue
        # nie Passwörter (smb://user:pw@…) in die Sitzungsdatei schreiben
        locs = [places.without_password(t.location) for t in tabs
                if t.location and not t.location.startswith("volume:")]
        if not locs:
            continue
        cur = getattr(w, "current", None)
        idx = locs.index(cur.location) if cur is not None and cur.location in locs else 0
        wins.append({"tabs": locs, "current": idx})
    return {"version": VERSION, "time": time.time(), "windows": wins}


def save_session(app, path=None):
    path = path or session_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(collect_session(app), fh, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def restore_session(app, path=None):
    """Nach dem Neustart: Fenster aus der Sitzungsdatei öffnen (Pfad aus
    FILEFENSTER_SITZUNG). True, wenn mindestens ein Fenster aufging."""
    path = path or os.environ.pop("FILEFENSTER_SITZUNG", None)
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        os.unlink(path)
    except (OSError, ValueError):
        return False
    try:    # fremde/kaputte Sitzungsdatei: normal starten
        if not isinstance(data, dict) or time.time() - float(data.get("time") or 0) > SESSION_MAX_AGE:
            return False
        windows = [w for w in data.get("windows") or [] if isinstance(w, dict)]
    except (TypeError, ValueError, AttributeError):
        return False
    opened = 0
    for w in windows:
        tabs = w.get("tabs")
        tabs = [t for t in tabs if isinstance(t, str) and t] if isinstance(tabs, list) else []
        if not tabs:
            continue
        try:
            win = app._new_window(tabs[0])
            for loc in tabs[1:]:
                win.new_tab(loc)
            cur = int(w.get("current") or 0)
            if 0 <= cur < len(win.tabs):
                win.select_tab(win.tabs[cur])
            opened += 1
        except Exception as exc:  # ein kaputter Ort soll den Start nicht verhindern
            print(f"[filefenster] Fenster nicht wiederhergestellt: {exc}")
    return opened > 0


# ---------------------------------------------------------------------------
# Verwaltung: Suchen, Hinweis an „···“, Neustart
# ---------------------------------------------------------------------------
class UpdateManager:
    def __init__(self, app):
        self.app = app
        self.config = get_config()
        self.release = None         # neuere Version (updater.Release) oder None
        self.checking = False
        self.dialog = None
        self._callbacks = []
        known = self.config.get("update_known") or ""
        if known and not updater.is_newer(known):
            self.config.set("update_known", "")   # inzwischen installiert
        if self.auto_possible():
            GLib.timeout_add_seconds(START_DELAY, self._first_tick)

    # -- automatische Suche -----------------------------------------------
    @staticmethod
    def auto_possible():
        """Nur die installierte Kopie sucht von selbst (nicht Tests, nicht git)."""
        return (os.environ.get("FILEFENSTER_NO_UPDATE_CHECK") != "1"
                and updater.run_mode() == "installed"
                and not updater.kit_verwaltet())      # Updates kommen über das Style Kit

    def auto_enabled(self):
        return bool(self.config.get("update_check", True)) and self.auto_possible()

    def due(self, now=None):
        now = time.time() if now is None else now
        last = float(self.config.get("update_last_check") or 0)
        attempt = float(self.config.get("update_last_attempt") or 0)
        if last > now + 3600:          # Uhr zurückgestellt
            last = 0
        if attempt > now + 3600:
            attempt = 0
        return now - last >= updater.CHECK_INTERVAL and now - attempt >= updater.RETRY_INTERVAL

    def maybe_auto_check(self, now=None):
        if self.checking or not self.auto_enabled() or not self.due(now):
            return False
        self.check(manual=False)
        return True

    def _first_tick(self):
        self.maybe_auto_check()
        GLib.timeout_add_seconds(TICK, self._tick)
        return False

    def _tick(self):
        self.maybe_auto_check()
        return True

    # -- Suchen ---------------------------------------------------------------
    def check(self, callback=None, manual=False):
        """Im Hintergrund suchen; callback(release, newer, fehlertext) im Hauptthread."""
        if callback is not None:
            self._callbacks.append(callback)
        if self.checking:
            return
        self.checking = True
        self.config.set("update_last_attempt", time.time())

        def work():
            rel, newer, err = None, False, None
            try:
                rel, newer = updater.check()
            except updater.UpdateError as e:
                err = str(e)
            except Exception as e:  # nie die Oberfläche mitreissen
                err = f"Unerwarteter Fehler bei der Update-Suche: {e}"
            GLib.idle_add(self._checked, rel, newer, err)
        threading.Thread(target=work, daemon=True, name="filefenster-update-check").start()

    def _checked(self, rel, newer, err):
        self.checking = False
        if err is None:
            self.config.set("update_last_check", time.time())
            self.release = rel if newer else None
            self.config.set("update_known", rel.version if newer else "")
        else:
            updater.log(f"Update-Suche: {err}")
        self.sync_badges()
        cbs, self._callbacks = self._callbacks, []
        for cb in cbs:
            try:
                cb(rel, newer, err)
            except Exception:
                import traceback
                traceback.print_exc()
        return False

    # -- Hinweis --------------------------------------------------------------
    def badge_version(self):
        """Neuere Version für den Hinweis am „···“-Knopf ('' = kein Hinweis)."""
        if updater.kit_verwaltet():
            return ""
        v = self.release.version if self.release else (self.config.get("update_known") or "")
        if v and updater.is_newer(v) and updater.run_mode() == "installed":
            return v
        return ""

    def sync_badges(self):
        for w in self.app.get_windows():
            sync_badge(w, self)

    # -- Neustart ---------------------------------------------------------------
    def restart(self):
        """Fenster merken, Neustart vormerken und FileFenster beenden."""
        try:
            path = save_session(self.app)
        except OSError:
            path = None
        self.config.set("update_known", "")
        self.config.save_now()
        updater.schedule_restart(path)
        self.quit_for_restart()

    def quit_for_restart(self):
        for w in list(self.app.get_windows()):
            if hasattr(w, "_on_delete"):
                try:
                    w._on_delete()
                except Exception:
                    pass
            w.destroy()
        self.app.quit()


def manager(app):
    mgr = getattr(app, "_update_manager", None)
    if mgr is None:
        mgr = UpdateManager(app)
        app._update_manager = mgr
    return mgr


# ---------------------------------------------------------------------------
# Fenster: Punkt am „···“-Knopf und Menüeintrag
# ---------------------------------------------------------------------------
def attach_window(win):
    """Hinweispunkt in den „···“-Knopf des Fensters setzen (unsichtbar, bis
    ein Update gefunden ist)."""
    btn = getattr(win, "btn_more", None)
    if btn is None or getattr(btn, "_update_badge", None) is not None:
        return
    child = btn.get_child()
    if child is None:
        return
    btn.remove(child)
    ov = Gtk.Overlay()
    ov.add(child)
    dot = Gtk.Box()
    dot.get_style_context().add_class("update-badge")
    dot.set_halign(Gtk.Align.END)
    dot.set_valign(Gtk.Align.START)
    dot.set_no_show_all(True)
    ov.add_overlay(dot)
    ov.set_overlay_pass_through(dot, True)
    btn.add(ov)
    ov.show_all()
    btn._update_badge = dot
    btn._update_tip = btn.get_tooltip_text() or "Weitere Optionen anzeigen"
    sync_badge(win, manager(win.app))


def sync_badge(win, mgr):
    btn = getattr(win, "btn_more", None)
    dot = getattr(btn, "_update_badge", None) if btn is not None else None
    if dot is None:
        return
    v = mgr.badge_version()
    dot.set_visible(bool(v))
    btn.set_tooltip_text(btn._update_tip + (f"\nUpdate auf Version {v} verfügbar" if v else ""))


def add_menu_item(menu, win):
    """Eintrag im „···“-Menü (window._on_more_menu)."""
    v = manager(win.app).badge_version()
    label = f"Update auf Version {v} verfügbar" if v else "Nach Updates suchen"
    menu.item(label, lambda: show_dialog(win), icon="arrow_download", bold=bool(v))


def show_dialog(win):
    mgr = manager(win.app)
    if mgr.dialog is not None:
        mgr.dialog.present()
        return mgr.dialog
    mgr.dialog = UpdateDialog(win, mgr)
    mgr.dialog.connect("destroy", lambda *_: setattr(mgr, "dialog", None))
    mgr.dialog.present()
    mgr.dialog.start_check()
    return mgr.dialog


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------
def _notes_into(buf, text):
    """Release-Notizen (Markdown-Stichpunkte) lesbar in den TextBuffer."""
    bold = buf.create_tag("b", weight=Pango.Weight.SEMIBOLD)
    it = buf.get_end_iter()
    for raw in (text or "").splitlines():
        line = raw.rstrip().replace("**", "").replace("`", "")
        stripped = line.lstrip()
        if stripped.startswith("#"):
            buf.insert_with_tags(it, stripped.lstrip("#").strip() + "\n", bold)
            continue
        if stripped[:2] in ("- ", "* "):
            indent = "    " if len(line) - len(stripped) >= 2 else ""
            line = indent + "•  " + stripped[2:]
        buf.insert(it, line + "\n")


class UpdateDialog(Gtk.Window):
    """Zustände: checking, uptodate, error, available, downloading, preparing,
    waiting (Dateivorgänge vor dem Einsetzen), installing, waiting_restart
    (eingesetzt, Dateivorgänge laufen noch), restarting, failed."""

    def __init__(self, parent, mgr):
        super().__init__(title="FileFenster – Updates")
        from .propsdialog import _fluent_button
        self.mgr = mgr
        self.parent_win = parent
        self.config = get_config()
        self.state = None
        self.release = None
        self.staged = None
        self._cancel = threading.Event()
        self._wait_id = 0
        self._pulse_id = 0
        self._last_progress = 0.0
        self.mode = updater.run_mode()
        for c in ("filefenster", "win-dialog", "props-dialog", "update-dialog"):
            self.get_style_context().add_class(c)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        else:
            self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        self.set_resizable(False)
        try:
            from . import icons
            self.set_icon(icons.folder_pixbuf(64))
        except Exception:
            pass

        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(area)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.get_style_context().add_class("win-body")
        for side in ("top", "start", "end"):
            getattr(body, f"set_margin_{side}")(24)
        body.set_margin_bottom(20)
        area.pack_start(body, True, True, 0)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        head.pack_start(self._app_icon(), False, False, 0)
        htext = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        htext.set_valign(Gtk.Align.CENTER)
        self.title_lab = Gtk.Label(xalign=0)
        self.title_lab.get_style_context().add_class("update-title")
        self.title_lab.set_line_wrap(True)
        self.sub_lab = Gtk.Label(xalign=0)
        self.sub_lab.get_style_context().add_class("update-sub")
        self.sub_lab.set_line_wrap(True)
        htext.pack_start(self.title_lab, False, False, 0)
        htext.pack_start(self.sub_lab, False, False, 0)
        head.pack_start(htext, True, True, 0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        head.pack_end(self.spinner, False, False, 0)
        body.pack_start(head, False, False, 0)

        self.notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        nl = Gtk.Label(label="Neuerungen", xalign=0)
        nl.get_style_context().add_class("update-notes-title")
        self.notes_box.pack_start(nl, False, False, 0)
        sw = Gtk.ScrolledWindow()
        sw.get_style_context().add_class("update-notes")
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(480, 190)
        self.notes = Gtk.TextView(editable=False, cursor_visible=False,
                                  wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10,
                                  right_margin=10, top_margin=8, bottom_margin=8)
        sw.add(self.notes)
        self.notes_box.pack_start(sw, True, True, 0)
        self.notes_box.set_no_show_all(True)
        body.pack_start(self.notes_box, True, True, 0)

        self.info = Gtk.Label(xalign=0)
        self.info.set_line_wrap(True)
        self.info.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.info.set_max_width_chars(64)
        self.info.set_selectable(True)
        self.info.set_can_focus(False)
        body.pack_start(self.info, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.bar.set_show_text(True)
        self.bar.set_no_show_all(True)
        body.pack_start(self.bar, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.get_style_context().add_class("win-footer")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for side in ("top", "bottom"):
            getattr(inner, f"set_margin_{side}")(16)
        for side in ("start", "end"):
            getattr(inner, f"set_margin_{side}")(24)
        footer.pack_start(inner, True, True, 0)
        self.auto_check = Gtk.CheckButton(label="Täglich automatisch suchen")
        self.auto_check.set_tooltip_text("Einmal am Tag im Hintergrund nach einer neuen Version "
                                         "suchen und sie am „···“-Knopf anzeigen")
        self.auto_check.set_active(bool(self.config.get("update_check", True)))
        self.auto_check.connect("toggled",
                                lambda b: self.config.set("update_check", b.get_active()))
        self.auto_check.set_no_show_all(True)
        self.auto_check.set_visible(self.mode == "installed")
        inner.pack_start(self.auto_check, False, False, 0)
        self.btn_kit = _fluent_button("Style Kit öffnen", accent=True)
        self.btn_kit.connect("clicked", lambda *_: (updater.kit_oeffnen(), self.on_close()))
        self.btn_update = _fluent_button("Jetzt aktualisieren", accent=True)
        self.btn_retry = _fluent_button("Erneut versuchen")
        self.btn_close = _fluent_button("Später")
        self.btn_update.connect("clicked", lambda *_: self.start_update())
        self.btn_retry.connect("clicked", lambda *_: self.start_check())
        self.btn_close.connect("clicked", lambda *_: self.on_close())
        for b in (self.btn_close, self.btn_update, self.btn_retry, self.btn_kit):
            b.set_no_show_all(True)
            inner.pack_end(b, False, False, 0)
        area.pack_end(footer, False, False, 0)

        self.connect("delete-event", lambda *_: (self.on_close(), True)[1])
        self.connect("key-press-event", self._on_key)
        self.connect("destroy", self._on_destroy)
        self.show_all()

    @staticmethod
    def _app_icon():
        svg = os.path.join(updater.APP_ROOT, "assets", "filefenster.svg")
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(svg, 48, 48)
            return Gtk.Image.new_from_pixbuf(pb)
        except GLib.Error:
            return Gtk.Image.new_from_icon_name("system-software-update", Gtk.IconSize.DIALOG)

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.on_close()
            return True
        return False

    def _on_destroy(self, *_):
        self.state = "closed"       # späte Antworten der Prüfung nicht mehr anzeigen
        self._cancel.set()
        for attr in ("_wait_id", "_pulse_id"):
            if getattr(self, attr):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, 0)
        if self.staged is not None:
            self.staged.discard()
            self.staged = None

    # -- Zustand ----------------------------------------------------------------
    def set_state(self, state, info="", error=False):
        self.state = state
        rel = self.release
        cur = f"Installiert: Version {VERSION}"
        titles = {
            "checking": "Nach Updates suchen …",
            "uptodate": f"{APP_NAME} ist auf dem neuesten Stand",
            "error": "Die Suche nach Updates ist fehlgeschlagen",
            "failed": "Das Update ist fehlgeschlagen",
            "managed": f"{APP_NAME} wird über ein Style Kit aktualisiert",
        }
        title = titles.get(state) or (f"Version {rel.version} ist verfügbar" if rel else APP_NAME)
        sub = cur
        if rel is not None and state not in ("checking", "uptodate", "error"):
            size = f" · Paket {places.format_size(rel.size)}" if rel.size else ""
            sub = f"{cur} · Neu: Version {rel.version}{size}"
        elif state == "uptodate" and rel is not None:
            sub = f"{cur} · Neueste Veröffentlichung: Version {rel.version}"
        self.title_lab.set_text(title)
        self.sub_lab.set_text(sub)
        busy = state in ("checking", "downloading", "preparing", "installing", "restarting")
        self.spinner.set_visible(busy)
        self.spinner.start() if busy else self.spinner.stop()
        self.notes_box.set_visible(rel is not None and state not in ("checking", "uptodate",
                                                                      "error"))
        ctx = self.info.get_style_context()
        (ctx.add_class if error else ctx.remove_class)("update-error")
        self.info.set_text(info)
        self.info.set_visible(bool(info))
        self.bar.set_visible(state in ("downloading", "preparing", "installing"))
        if state in ("preparing", "installing") and not self._pulse_id:
            self.bar.set_text("")
            self._pulse_id = GLib.timeout_add(150, self._pulse)
        installable = self.mode == "installed"
        self.btn_update.set_visible(state == "available" and installable)
        self.btn_retry.set_visible(state in ("error", "failed"))
        kit = updater.kit_info() if state == "managed" else None
        if kit and kit["verwalten"]:
            self.btn_kit.set_label(f"{kit['kit']} öffnen")
        self.btn_kit.set_visible(bool(kit and kit["verwalten"]))
        self.auto_check.set_visible(self.mode == "installed" and state != "managed")
        close_label = {
            "available": "Später" if installable else "Schliessen",
            "downloading": "Abbrechen", "preparing": "Abbrechen", "waiting": "Abbrechen",
            "waiting_restart": "Später neu starten",
        }.get(state, "Schliessen")
        self.btn_close.set_label(close_label)
        self.btn_close.set_visible(state not in ("installing", "restarting"))
        default = (self.btn_update if self.btn_update.get_visible() else
                   self.btn_kit if self.btn_kit.get_visible() else self.btn_close)
        default.set_can_default(True)
        self.set_default(default)
        if default.get_visible():
            default.grab_focus()
        self.resize(1, 1)

    def _pulse(self):
        if self.state in ("preparing", "installing"):
            self.bar.pulse()
            return True
        self._pulse_id = 0
        return False

    def on_close(self):
        if self.state in ("installing", "restarting"):
            return          # gleich vorbei, nicht mittendrin abbrechen
        self.destroy()

    # -- Suchen ------------------------------------------------------------------
    def show_managed(self, kit):
        self.release = None
        self.set_state("managed", updater.kit_meldung(kit))

    def start_check(self):
        self.release = None
        kit = updater.kit_verwaltet()
        if kit:                         # Updates kommen über das Style Kit
            self.show_managed(kit)
            return
        self.set_state("checking", "Es wird nach einer neuen Version gesucht …")
        self.mgr.check(self._checked, manual=True)

    def _checked(self, rel, newer, err):
        if self.state != "checking":
            return
        self.release = rel if newer else None
        if err:
            self.set_state("error", err, error=True)
            return
        if not newer:
            self.release = rel
            info = "" if rel else "Es gibt noch keine veröffentlichte Version."
            self.set_state("uptodate", info)
            self.release = None
            return
        buf = self.notes.get_buffer()
        buf.set_text("")
        _notes_into(buf, rel.notes or "Keine Beschreibung vorhanden.")
        if self.mode == "installed":
            info = ("FileFenster startet danach neu und öffnet die Fenster und "
                    "Registerkarten wieder.")
            note = updater.trust_note()
            if note:
                info = f"{info}\n\n{note}"
        elif self.mode == "checkout":
            info = (f"FileFenster läuft aus dem Git-Arbeitsverzeichnis {updater.APP_ROOT} und "
                    "wird hier nicht aktualisiert. Dort mit „git pull“ aktualisieren – oder "
                    "einmal ./installieren.sh ausführen: Dann läuft die installierte Kopie, "
                    "die sich hier selbst aktualisiert.")
        else:
            info = (f"FileFenster läuft aus {updater.APP_ROOT}, nicht aus der installierten "
                    f"Kopie ({updater.install_dir()}), und wird hier nicht aktualisiert. "
                    "Zum Installieren ./installieren.sh ausführen.")
        self.set_state("available", info)

    # -- Aktualisieren -------------------------------------------------------------
    def start_update(self):
        kit = updater.kit_verwaltet()
        if kit:
            self.show_managed(kit)
            return
        if self.mode != "installed" or self.release is None or self.state != "available":
            return
        rel = self.release
        self._cancel.clear()
        self.bar.set_fraction(0)
        self.bar.set_text("")
        self.set_state("downloading", "Das Update wird heruntergeladen …")

        def work():
            try:
                archive = updater.download(rel, self._progress, self._cancel)
                GLib.idle_add(self._phase, "preparing")
                staged = updater.stage(archive, rel.version)
                try:
                    os.unlink(archive)
                except OSError:
                    pass
                GLib.idle_add(self._staged, staged)
            except updater.Cancelled:
                pass
            except updater.UpdateError as e:
                GLib.idle_add(self._failed, str(e))
            except Exception as e:
                GLib.idle_add(self._failed, f"Unerwarteter Fehler: {e}")
        threading.Thread(target=work, daemon=True, name="filefenster-update-download").start()

    def _progress(self, done, total):
        now = time.monotonic()
        if now - self._last_progress < 0.1 and done != total:
            return
        self._last_progress = now

        def upd():
            if self.state != "downloading":
                return False
            if total:
                self.bar.set_fraction(min(done / total, 1.0))
                self.bar.set_text(f"{places.format_size(done)} von {places.format_size(total)}")
            else:
                self.bar.pulse()
                self.bar.set_text(places.format_size(done))
            return False
        GLib.idle_add(upd)

    def _phase(self, state):
        if self.state in ("downloading", "preparing"):
            self.set_state(state, "Das Update wird geprüft und entpackt …")
        return False

    def _staged(self, staged):
        if self._cancel.is_set() or self.state not in ("downloading", "preparing"):
            staged.discard()
            return False
        self.staged = staged
        self._install_when_idle()
        return False

    def _install_when_idle(self):
        n = len(_running_jobs())
        if n:
            self.set_state("waiting", f"{_jobs_text(n)} noch. Das Update wird eingesetzt und "
                           "FileFenster neu gestartet, sobald alles abgeschlossen ist.")
            self._poll(self._install_when_idle)
            return
        self._install()

    def _poll(self, fn):
        if self._wait_id:
            return

        def tick():
            self._wait_id = 0
            fn()
            return False
        self._wait_id = GLib.timeout_add(500, tick)

    def _install(self):
        staged, self.staged = self.staged, None
        if staged is None:
            return
        self.set_state("installing", "Die neue Version wird eingesetzt …")

        def work():
            try:
                updater.activate(staged)
                ok, out = updater.refresh_launcher()
                GLib.idle_add(self._installed, staged.version, ok)
            except updater.UpdateError as e:
                GLib.idle_add(self._failed, str(e))
            except Exception as e:
                GLib.idle_add(self._failed, f"Unerwarteter Fehler: {e}")
        threading.Thread(target=work, daemon=True, name="filefenster-update-install").start()

    def _installed(self, version, launcher_ok):
        self.mgr.release = None
        self.config.set("update_known", "")
        self.mgr.sync_badges()
        self._restart_when_idle(version)
        return False

    def _restart_when_idle(self, version):
        if self.state not in ("installing", "waiting_restart"):
            return
        n = len(_running_jobs())
        if n:
            self.set_state("waiting_restart",
                           f"Version {version} ist installiert. {_jobs_text(n)} noch – "
                           "FileFenster startet neu, sobald sie abgeschlossen sind. "
                           "„Später neu starten“: Die neue Version startet beim nächsten Öffnen.")
            self._poll(lambda: self._restart_when_idle(version))
            return
        self.set_state("restarting", f"Version {version} ist installiert. "
                       "FileFenster wird neu gestartet …")
        GLib.timeout_add(400, self._do_restart)

    def _do_restart(self):
        self.mgr.restart()
        return False

    def _failed(self, msg):
        if self.state in ("error", "failed") or self.get_window() is None:
            return False
        if self.staged is not None:
            self.staged.discard()
            self.staged = None
        updater.log(f"Update fehlgeschlagen: {msg}")
        self.set_state("failed", f"{msg}\nDie bisherige Version bleibt unverändert. "
                       f"Protokoll: {updater.log_file()}", error=True)
        return False
