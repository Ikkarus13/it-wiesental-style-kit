"""Synchronisations-Engine: verwaltet alle Konten, Status, Pins, Papierkorb, Speicheroptimierung.

Laeuft komplett in Hintergrund-Threads. Die Oberflaeche wird ueber Callbacks informiert
(die App leitet sie per GLib.idle_add in den GTK-Hauptthread um).
"""

import collections
import contextlib
import os
import queue
import shutil
import socket
import subprocess
import threading
import time

from . import config, folders, graph, hangguard, indexer, mimefix, rclone, states, thumbs
from . import marke

POLL_SECONDS = 2
HANG_CHECK_SECONDS = 15
HANG_SECONDS = 180          # so lange darf ein Zugriff unbewegt auf rclone warten
SLEEP_HANG_SECONDS = 1.5    # vor dem Standby: Netz geht gleich weg, jeder Haenger blockiert ihn
PIN_RESCAN_SECONDS = 600
STORAGE_SENSE_SECONDS = 3600
QUOTA_SECONDS = 300
PAUSE_EXPIRY = 10 ** 9
EVICT_GRACE_SECONDS = 20
PARTIAL_CHECK_SECONDS = 60
PARTIAL_MAX_AGE_SECONDS = 600
HEAD_ONLY_BYTES = 16 * 1024 * 1024
HEAD_ONLY_MAX_AGE_SECONDS = 60
SNIFF_SMALL_BYTES = 2 * 1024 * 1024     # = --buffer-size: so viel laedt ein kurzer Blick hinein


class Activity:
    __slots__ = ("acc_id", "rel", "kind", "size", "ts", "error")

    def __init__(self, acc_id, rel, kind, size=0, error=""):
        self.acc_id, self.rel, self.kind, self.size = acc_id, rel, kind, size
        self.ts, self.error = time.time(), error


class AccountRuntime:
    def __init__(self, engine, account):
        self.engine = engine
        self.account = account
        self.pins = states.PinStore(account.id)
        self.mount = None
        self.status = "stopped"      # stopped | starting | ok | syncing | error | offline
        self.error = ""
        self.queue = []              # vfs/queue Eintraege
        self.transfers = []          # core/stats transferring
        self.busy = set()            # relative Pfade mit laufender Aktivitaet
        self.errors = {}             # rel -> Fehlermeldung
        self.error_times = {}        # rel -> Zeitpunkt des Fehlers
        self.hydrating = None
        self.quota = None            # (used, total)
        self._seen_transferred = set()
        self._recent_uploads = set()
        self._last_quota = 0
        self._trash_busy = False

    @property
    def view(self):
        m = self.mount
        return states.AccountView(self.account.id, self.account.mount_path,
                                  m.data_dir if m else "", m.meta_dir if m else "",
                                  self.account.real_path)

    def abs(self, rel):
        return os.path.join(self.account.mount_path, rel) if rel else self.account.mount_path

    def rel(self, path):
        return self.view.rel(path)


class ShareTarget:
    """Ein Element fuer den Freigabedialog. Jeder Aufruf holt ein frisches Token (der Dialog
    kann lange offen sein) und laeuft im Hintergrund-Thread des Aufrufers."""

    def __init__(self, engine, rt, full_path):
        self.engine, self.rt = engine, rt
        acc = rt.account
        self.drive_id = acc.drive_id
        self.personal = acc.kind == "personal"
        self.org = acc.org
        self.account_label = acc.label
        self._path = full_path
        self._item = None

    def _token(self):
        tok = self.engine._fresh_token(self.rt)
        if not tok:
            raise graph.GraphError("Keine gültige Anmeldung – bitte das Konto neu anmelden.")
        return tok

    def item(self, tok):
        if self._item is None:
            self._item = graph.item_id(tok, self.drive_id, self._path)
        return self._item

    def create_link(self, kind, scope, password="", expires=""):
        tok = self._token()
        return graph.create_link(tok, self.drive_id, self.item(tok), kind, scope, password,
                                 expires)

    def invite(self, emails, role, send_mail, message=""):
        tok = self._token()
        return graph.invite(tok, self.drive_id, self.item(tok), emails, role, send_mail, message)

    def permissions(self):
        tok = self._token()
        return graph.permissions(tok, self.drive_id, self.item(tok))

    def remove(self, perm_id):
        tok = self._token()
        graph.remove_permission(tok, self.drive_id, self.item(tok), perm_id)


class Engine:
    def __init__(self, settings, on_status, on_changed, on_notify):
        self.settings = settings
        self.on_status = on_status
        self.on_changed = on_changed
        self.on_notify = on_notify
        self.binary = rclone.find_rclone(settings)
        self.runtimes = {}
        self.activity = collections.deque(maxlen=60)
        self.jobs = queue.Queue()
        self._job_keys = set()
        self._job_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_written_state = None
        self._threads = []
        self.backup = folders.FolderBackup(self)
        # Standby: solange gesetzt, greifen die eigenen Hintergrundarbeiten nicht aufs Laufwerk zu
        self.sleeping = threading.Event()
        self._fs_jobs = 0
        self._fs_cond = threading.Condition()
        self._sleep_guard = None
        self._hang = hangguard.HangGuard()
        self._hang_lock = threading.Lock()
        self._unsticking = set()     # Konten, deren Laufwerk gerade neu eingehaengt wird

    # ------------------------------------------------------------ Standby

    @contextlib.contextmanager
    def fs_job(self):
        """Markiert eine laufende Hintergrundarbeit auf dem Laufwerk (fuer den Standby-Schutz)."""
        with self._fs_cond:
            self._fs_jobs += 1
        try:
            yield
        finally:
            with self._fs_cond:
                self._fs_jobs -= 1
                self._fs_cond.notify_all()

    def quiesce(self, timeout):
        """Vor dem Standby: neue Laufwerkszugriffe stoppen, laufende auslaufen lassen."""
        self.sleeping.set()
        start = time.monotonic()
        deadline = start + timeout
        self._check_hangs(SLEEP_HANG_SECONDS)     # Ausgangslage merken
        with self._fs_cond:
            while self._fs_jobs > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._fs_cond.wait(left)
        # Was jetzt noch unbewegt auf rclone wartet, liesse sich nicht einfrieren -> loesen
        rest = start + SLEEP_HANG_SECONDS + 0.2 - time.monotonic()
        if rest > 0:
            time.sleep(rest)
        self._check_hangs(SLEEP_HANG_SECONDS, before_sleep=True)

    # ------------------------------------------------------------ Haenger

    def _fuse_mounts(self):
        mounts = []
        for rt in list(self.runtimes.values()):
            if rt.mount and rt.mount.proc and rt.account.id not in self._unsticking:
                mounts.append((rt, hangguard.fuse_conn(rt.account.real_path)))
        return mounts

    def _check_hangs(self, threshold, before_sleep=False):
        with self._hang_lock:
            hung, stuck = self._hang.check(threshold, self._fuse_mounts())
            for rt in hung:
                self._unstick(rt, stuck, before_sleep)

    def _hang_loop(self):
        # laeuft auch waehrend self.sleeping: genau dann darf nichts haengen bleiben
        while not self._stop.wait(HANG_CHECK_SECONDS):
            try:
                self._check_hangs(HANG_SECONDS)
            except Exception:  # noqa: BLE001
                pass

    def _unstick(self, rt, stuck, before_sleep):
        conn = hangguard.fuse_conn(rt.account.real_path)
        if not conn or rt.account.id in self._unsticking:
            return
        self._unsticking.add(rt.account.id)
        ok = hangguard.abort(conn)
        print(f"onedrive-mint: {rt.account.label}: rclone antwortet nicht, Verbindung "
              f"{'abgebrochen' if ok else 'NICHT abgebrochen'} "
              f"({'vor Standby' if before_sleep else 'Ueberwachung'}); wartend: {', '.join(stuck)}",
              flush=True)
        if not before_sleep:
            self.on_notify(rt.account.label, f"{marke.NAME}: Die Verbindung reagierte nicht mehr und wird neu "
                           "verbunden. Laufende Zugriffe auf das Laufwerk wurden abgebrochen.",
                           "info")

        def work():
            try:
                old = rt.mount
                if old:
                    old.stop()
                    if old._thread:
                        old._thread.join(10)   # dessen "stopped" soll nicht den Neustart ueberholen
                # Im Standby nicht neu einhaengen – erst nach dem Aufwachen
                while self.sleeping.is_set() and not self._stop.is_set():
                    time.sleep(1)
                if not self._stop.is_set() and rt.account.id in self.runtimes:
                    self._start_mount(rt)
                    self.on_status()
            finally:
                self._unsticking.discard(rt.account.id)
        threading.Thread(target=work, daemon=True, name=f"unstick-{rt.account.id}").start()

    def resume(self):
        self.sleeping.clear()
        self.backup.sync_now()
        self.on_status()

    def exclude_from_indexer(self):
        mounts = [p for rt in self.runtimes.values()
                  for p in (rt.account.mount_path, rt.account.real_path)]
        try:
            removed = indexer.exclude_mounts(mounts, folders.read_user_dirs())
        except Exception:  # noqa: BLE001
            return
        if removed:
            print("onedrive-mint: Datei-Indexer durchsucht OneDrive nicht mehr: " +
                  ", ".join(removed), flush=True)

    # ------------------------------------------------------------ Lebenszyklus

    def start(self):
        for acc in config.load_accounts():
            self.runtimes[acc.id] = AccountRuntime(self, acc)
        self.exclude_from_indexer()
        try:
            from .sleepguard import SleepGuard
            self._sleep_guard = SleepGuard(self.quiesce, self.resume)
        except Exception:  # noqa: BLE001
            self._sleep_guard = None
        for name, fn in (("poll", self._poll_loop), ("jobs", self._job_loop),
                         ("housekeeping", self._housekeeping_loop),
                         ("hangguard", self._hang_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        for rt in self.runtimes.values():
            if rt.account.enabled:
                self._start_mount(rt)
        self._write_state()

    def stop(self):
        self._stop.set()
        self.backup.stop()
        if self._sleep_guard:
            self._sleep_guard.close()
        threads = []
        for rt in self.runtimes.values():
            if rt.mount:
                t = threading.Thread(target=rt.mount.stop, daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join(20)
        try:
            os.unlink(config.STATE_FILE)
        except OSError:
            pass

    def _start_mount(self, rt):
        if not self.binary:
            rt.status, rt.error = "error", "rclone ist nicht installiert."
            return
        rt.status, rt.error = "starting", ""
        rt.mount = rclone.Mount(self.binary, rt.account, self.settings, self._mount_event)
        rt.mount.start()

    def _mount_event(self, acc_id, kind, text):
        rt = self.runtimes.get(acc_id)
        if not rt:
            return
        if kind == "mounted":
            rt.status, rt.error = "ok", ""
            if rt.mount.rescued:
                rescued, rt.mount.rescued = rt.mount.rescued, None
                threading.Thread(target=self.merge_rescued, args=(rt, rescued),
                                 daemon=True).start()
            self._write_state()
            self.on_changed([rt.account.mount_path])
            self.queue_pinned_rescan(rt)
            self.start_thumb_scan(rt)
            self.backup.on_mounted(rt)
        elif kind == "error":
            rt.status, rt.error = "error", text
            self.on_notify(rt.account.label, text, "error")
            self._write_state()
        elif kind == "stopped":
            rt.status = "stopped"
            self._write_state()
        self.on_status()

    # ------------------------------------------------------------ Konten

    def add_account(self, account):
        accounts = config.load_accounts()
        accounts.append(account)
        config.save_accounts(accounts)
        rt = AccountRuntime(self, account)
        self.runtimes[account.id] = rt
        self._start_mount(rt)
        self.on_status()
        return rt

    def add_library(self, parent_id, info, mount_path):
        """Teams-/SharePoint-Bibliothek (oder Ordner daraus) als eigenen Sync-Ordner einbinden.
        Verwendet die Anmeldung des Geschaeftskontos (gleiches Token, andere Bibliothek)."""
        parent = self.runtimes[parent_id].account
        accounts = config.load_accounts()
        acc_id = config.new_account_id("library", f"{info.site_name}-{info.folder_name}", accounts)
        token = rclone.read_remote_option(parent_id, "token")
        rclone.write_remote_config(acc_id, token, info.drive_id, "documentLibrary",
                                   rclone.read_remote_option(parent_id, "client_id"),
                                   rclone.read_remote_option(parent_id, "client_secret"),
                                   rclone.read_remote_option(parent_id, "tenant"))
        acc = config.Account(id=acc_id, kind="library", display_name=parent.display_name,
                             email=parent.email, org=parent.org, mount_path=mount_path,
                             drive_id=info.drive_id, drive_type="documentLibrary",
                             web_url=info.web_url, parent_id=parent_id,
                             site_name=info.site_name, library_name=info.drive_name,
                             remote_path=info.rel_path, site_url=info.site_url, host=info.host)
        return self.add_account(acc)

    def find_library(self, drive_id, rel_path):
        for rt in self.runtimes.values():
            a = rt.account
            if a.is_library and a.drive_id == drive_id and a.remote_path == rel_path:
                return rt
        return None

    def remove_account(self, acc_id):
        for child in [r.account.id for r in self.runtimes.values() if r.account.parent_id == acc_id]:
            self.remove_account(child)
        rt = self.runtimes.pop(acc_id, None)
        if rt and rt.mount:
            rt.mount.stop()
        config.save_accounts([a for a in config.load_accounts() if a.id != acc_id])
        shutil.rmtree(config.account_dir(acc_id), ignore_errors=True)
        shutil.rmtree(config.account_cache_dir(acc_id), ignore_errors=True)
        if rt:
            try:
                if os.path.islink(rt.account.mount_path):
                    os.unlink(rt.account.mount_path)
                    os.rmdir(rt.account.real_path)
                else:
                    os.rmdir(rt.account.mount_path)
                if rt.account.is_library:   # leeren Firmenordner (~/Firma) mit aufraeumen
                    os.rmdir(os.path.dirname(rt.account.mount_path))
            except OSError:
                pass
        self._write_state()
        self.on_status()

    def restart_account(self, acc_id):
        rt = self.runtimes.get(acc_id)
        if not rt:
            return

        def work():
            if rt.mount:
                rt.mount.stop()
            self._start_mount(rt)
            self.on_status()
        threading.Thread(target=work, daemon=True).start()

    def restart_all(self):
        self.binary = rclone.find_rclone(self.settings)
        for acc_id in list(self.runtimes):
            self.restart_account(acc_id)

    def _add_activity(self, act):
        """Neuer Eintrag oben; derselbe Vorgang zur selben Datei erscheint nur einmal (sonst
        fuellen wiederholte Fehlversuche oder doppelt gemeldete Uploads die Liste)."""
        for old in list(self.activity):
            if (old.acc_id, old.rel, old.kind) == (act.acc_id, act.rel, act.kind):
                self.activity.remove(old)
        self.activity.appendleft(act)

    def unsynced_count(self):
        return sum(len(rt.queue) for rt in self.runtimes.values())

    # ------------------------------------------------------------ Pfade

    def locate(self, path):
        path = os.path.abspath(path)
        for candidate in (path, os.path.realpath(path)):
            for rt in self.runtimes.values():
                rel = rt.rel(candidate)
                if rel is not None:
                    return rt, rel
        return None, None

    def state_of(self, path):
        rt, rel = self.locate(path)
        if not rt:
            return None
        v = rt.view
        if os.path.isdir(path):
            return v.dir_state_full(rel, rt.busy)
        return v.file_state(rel, rt.busy, rt.errors)

    # ------------------------------------------------------------ Dateien bei Bedarf

    def set_pinned(self, paths, pinned):
        """'Immer auf diesem Geraet behalten' an/aus."""
        changed = []
        for p in paths:
            rt, rel = self.locate(p)
            if not rt:
                continue
            if pinned:
                rt.pins.set(rel, states.PIN)
                self._enqueue("hydrate", rt, rel)
            else:
                # Ausschalten laesst Dateien lokal verfuegbar (wie unter Windows)
                rt.pins.set(rel, states.UNPIN)
            changed.append(p)
        self._changed_with_parents(changed)

    def free_up(self, paths):
        """'Speicher freigeben' -> nur online."""
        for p in paths:
            rt, rel = self.locate(p)
            if not rt:
                continue
            rt.pins.set(rel, states.UNPIN)
            self._enqueue("evict", rt, rel)
        self._changed_with_parents(paths)

    def make_available(self, paths):
        """Einmalig herunterladen, ohne anzupinnen (Doppelklick-Verhalten)."""
        for p in paths:
            rt, rel = self.locate(p)
            if rt:
                self._enqueue("hydrate", rt, rel)

    def queue_pinned_rescan(self, rt):
        for rel in rt.pins.pinned_roots():
            self._enqueue("hydrate", rt, rel)

    def _enqueue(self, op, rt, rel):
        key = (op, rt.account.id, rel)
        with self._job_lock:
            if key in self._job_keys:
                return
            self._job_keys.add(key)
        self.jobs.put(key)

    def _job_loop(self):
        while not self._stop.is_set():
            try:
                key = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            with self._job_lock:
                self._job_keys.discard(key)
            op, acc_id, rel = key
            rt = self.runtimes.get(acc_id)
            if not rt or not rt.mount or not rt.mount.running:
                continue
            try:
                if op == "hydrate":
                    self._hydrate(rt, rel)
                elif op == "evict":
                    self._evict(rt, rel)
            except Exception as e:  # noqa: BLE001
                self._add_activity(Activity(acc_id, rel, "error", error=str(e)))
            self.on_status()

    def _paused(self):
        return self.settings["paused"] or self.sleeping.is_set()

    def _hydrate(self, rt, rel):
        while self.sleeping.is_set() and not self._stop.is_set():
            time.sleep(2)
        with self.fs_job():
            files = self._hydrate_list(rt, rel)
        if files is None:
            # Standby kam dazwischen -> spaeter nochmal
            self._enqueue("hydrate", rt, rel)
            return
        self._hydrate_files(rt, files)

    def _hydrate_list(self, rt, rel):
        root = rt.abs(rel)
        if os.path.isfile(root):
            return [rel]
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            if self._stop.is_set():
                return []
            if self.sleeping.is_set():
                return None
            drel = rt.rel(dirpath)
            # Unterordner, die explizit "nur online" sind, auslassen
            dirnames[:] = [d for d in dirnames if not d.startswith(".Trash-") and
                           rt.pins.decision(os.path.join(drel, d) if drel else d)
                           != states.UNPIN]
            for fn in filenames:
                files.append(os.path.join(drel, fn) if drel else fn)
        return files

    def _hydrate_files(self, rt, files):
        view = rt.view
        todo = [f for f in files if not states.meta_fully_cached(
            states.read_meta(view.meta_path(f)), view.data_path(f))]
        for f in todo:
            while self._paused() and not self._stop.is_set():
                time.sleep(2)
            if self._stop.is_set() or not rt.mount.running:
                return
            if rt.pins.decision(f) == states.UNPIN:
                continue
            rt.hydrating = f
            rt.busy.add(f)
            self._write_state()
            self.on_changed([rt.abs(f)])
            self.on_status()
            try:
                interrupted = False
                with self.fs_job(), open(rt.abs(f), "rb") as fh:
                    while fh.read(4 * 1024 * 1024):
                        if self._stop.is_set() or self.sleeping.is_set():
                            interrupted = True
                            break
                if interrupted:
                    # Standby: Rest spaeter laden (Pins werden periodisch neu geprueft)
                    self._enqueue("hydrate", rt, f)
                else:
                    rt.errors.pop(f, None)
            except OSError as e:
                rt.errors[f] = str(e)
                rt.error_times[f] = time.time()
                self._add_activity(Activity(rt.account.id, f, "error", error=str(e)))
            finally:
                rt.busy.discard(f)
                rt.hydrating = None
                self._write_state()
                self._changed_with_parents([rt.abs(f)])
        if todo:
            self.on_status()

    def start_thumb_scan(self, rt):
        if getattr(rt, "_thumb_scanning", False):
            return
        rt._thumb_scanning = True
        threading.Thread(target=self._thumb_scan, args=(rt,), daemon=True,
                         name=f"thumbs-{rt.account.id}").start()

    def _thumb_scan(self, rt):
        """Vorab fuer alle Nur-online-Dateien mit Vorschau-Typ einen Thumbnail-Fehleintrag
        anlegen. Sonst erzeugt Nautilus/Nemo beim ersten Oeffnen eines Ordners Vorschauen,
        bevor die Erweiterung reagieren kann – und laedt dafuer die Dateien herunter."""
        pending, last_flush = set(), time.time()

        def flush():
            nonlocal pending, last_flush
            if pending:
                mimefix.register(pending)
                pending = set()
            last_flush = time.time()

        aliases = self.backup.aliases(rt)
        job = None

        def uris(path):
            """URI im OneDrive plus URIs unter Verknuepfungen der Ordnersicherung."""
            out = [thumbs.file_uri(path)]
            for target, local in aliases:
                if path.startswith(target + "/"):
                    out.append(thumbs.file_uri(local + path[len(target):]))
            return out

        try:
            view = rt.view
            root = rt.account.mount_path
            job = self.fs_job()
            job.__enter__()
            for dirpath, dirnames, filenames in os.walk(root):
                if self._stop.is_set() or not rt.mount or not rt.mount.running:
                    return
                if self.sleeping.is_set():
                    return      # naechster Durchlauf nach dem Aufwachen (housekeeping)
                dirnames[:] = [d for d in dirnames if not d.startswith(".Trash-")]
                drel = rt.rel(dirpath)
                for fn in filenames:
                    # Endungen, fuer die der Dateimanager sonst in die Datei schauen wuerde
                    ext = mimefix.uncertain_extension(fn)
                    if ext:
                        pending.add(ext)
                    if not thumbs.is_thumbnailable(fn):
                        continue
                    rel = os.path.join(drel, fn) if drel else fn
                    path = os.path.join(dirpath, fn)
                    meta = states.read_meta(view.meta_path(rel))
                    if states.meta_fully_cached(meta, view.data_path(rel)):
                        for u in uris(path):
                            thumbs.unblock(u)
                        continue
                    try:
                        mtime = os.stat(path).st_mtime
                    except OSError:
                        continue
                    for u in uris(path):
                        thumbs.block(u, mtime)
                if pending and time.time() - last_flush > 3:
                    flush()
                time.sleep(0.02)      # Microsoft-API und Dateisystem schonen
            flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            if job is not None:
                job.__exit__(None, None, None)
            rt._thumb_scanning = False

    def _open_files(self, mount):
        """Pfade unterhalb des Mounts, die gerade von irgendeinem Prozess geoeffnet sind."""
        result = set()
        prefix = mount.rstrip("/") + "/"
        for pid in hangguard.own_pids():
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                    except OSError:
                        continue
                    if target.startswith(prefix):
                        result.add(target[len(prefix):])
            except OSError:
                continue
        return result

    def _evict(self, rt, rel, older_than=None, only_partial=False):
        """Entfernt Dateien aus dem rclone-Cache. rclone erkennt das und laedt bei Bedarf neu."""
        view = rt.view
        if not view.meta_dir:
            return 0
        open_files = self._open_files(rt.account.real_path)
        base_meta = view.meta_path(rel)
        candidates = []
        if os.path.isfile(base_meta):
            candidates = [rel]
        elif os.path.isdir(base_meta):
            for dirpath, _, filenames in os.walk(base_meta):
                drel = os.path.relpath(dirpath, view.meta_dir)
                drel = "" if drel == "." else drel
                for fn in filenames:
                    candidates.append(os.path.join(drel, fn) if drel else fn)
        freed, changed, deferred = 0, [], False
        now = time.time()
        for f in candidates:
            if f.startswith(".Trash-") or rt.pins.is_pinned(f):
                continue
            meta = states.read_meta(view.meta_path(f))
            if not meta or meta.get("Dirty"):
                continue
            atime = _atime(meta)
            if older_than is not None and atime > older_than:
                continue
            if only_partial:
                if states.meta_fully_cached(meta, view.data_path(f)):
                    # Kleine Datei mit mehrdeutigem Typ (.png, .pem, .json, .html, …): der
                    # Dateimanager hat sie zur Typ-Erkennung angelesen und damit ganz geladen.
                    sniffed = (meta.get("Size", 0) <= SNIFF_SMALL_BYTES and
                               _type_uncertain(os.path.basename(f)))
                    if not sniffed or now - atime < HEAD_ONLY_MAX_AGE_SECONDS:
                        continue
                else:
                    # Nur der Dateianfang geladen (Typ-Erkennung, Vorschau) -> schnell freigeben,
                    # sonstige Teildownloads erst nach laengerer Ruhe
                    rs = meta.get("Rs") or []
                    head_only = len(rs) <= 1 and (not rs or (rs[0].get("Pos", 0) == 0 and
                                                             rs[0].get("Size", 0) <= HEAD_ONLY_BYTES))
                    limit = HEAD_ONLY_MAX_AGE_SECONDS if head_only else PARTIAL_MAX_AGE_SECONDS
                    if now - atime < limit:
                        continue
            # rclone haelt Dateien nach dem Schliessen noch kurz intern offen (Grace-Period).
            # Geoeffnete oder gerade benutzte Dateien spaeter erneut versuchen.
            if f in open_files or now - atime < EVICT_GRACE_SECONDS or \
                    now - _mtime(view.data_path(f)) < EVICT_GRACE_SECONDS:
                deferred = True
                continue
            data = view.data_path(f)
            try:
                freed += os.path.getsize(data)
                os.unlink(data)
            except OSError:
                pass
            try:
                os.unlink(view.meta_path(f))
            except OSError:
                pass
            changed.append(rt.abs(f))
        if deferred and older_than is None and not only_partial:
            t = threading.Timer(EVICT_GRACE_SECONDS + 2, self._enqueue, ("evict", rt, rel))
            t.daemon = True
            t.start()
        if changed:
            self._changed_with_parents(changed)
        return freed

    # ------------------------------------------------------------ Online-Aktionen

    def token_for(self, acc_id):
        rt = self.runtimes.get(acc_id)
        return self._fresh_token(rt) if rt else ""

    def _fresh_token(self, rt):
        tok = rclone.read_token(rt.account.id)
        if not graph.token_valid(tok):
            # rclone erneuert das Token bei einem API-Aufruf und schreibt es in die Config
            try:
                rt.mount.call("operations/about", fs=rt.account.remote, timeout=30)
            except (rclone.RcError, AttributeError):
                pass
            tok = rclone.read_token(rt.account.id)
        if not graph.token_valid(tok) and self.binary:
            # Mount (noch) nicht verbunden: rclone direkt das Token erneuern lassen
            subprocess.run([self.binary, "about", rt.account.id + ":", "--json",
                            "--config", config.account_rclone_conf(rt.account.id)],
                           capture_output=True, timeout=60)
            tok = rclone.read_token(rt.account.id)
        return (tok or {}).get("access_token", "")

    def web_url(self, path):
        rt, rel = self.locate(path)
        if not rt:
            return ""
        if rel and rel.startswith(".Trash-"):
            return ""
        try:
            full = "/".join(p for p in (rt.account.remote_path, rel) if p)
            url = graph.item_web_url(self._fresh_token(rt), rt.account.drive_id, full)
        except graph.GraphError:
            url = ""
        return url or rt.account.web_url

    def share_target(self, path):
        """Alles, was der Freigabedialog fuer path braucht. Wirft graph.GraphError."""
        rt, rel = self.locate(path)
        if not rt or (rel and rel.startswith(".Trash-")):
            raise graph.GraphError("Das Element liegt nicht in einem verbundenen Konto.")
        return ShareTarget(self, rt, "/".join(p for p in (rt.account.remote_path, rel) if p))

    def share_link(self, path):
        rt, rel = self.locate(path)
        if not rt or not rt.mount:
            raise rclone.RcError("Nicht in OneDrive")
        res = rt.mount.call("operations/publiclink", fs=rt.account.remote, remote=rel, timeout=60)
        return res.get("url", "")

    def sync_now(self):
        for rt in self.runtimes.values():
            if rt.mount and rt.mount.running:
                try:
                    rt.mount.call("vfs/refresh", recursive=False, timeout=60)
                except rclone.RcError:
                    pass
                self.queue_pinned_rescan(rt)
                self._release_queue(rt)

    def set_paused(self, paused):
        self.settings["paused"] = bool(paused)
        for rt in self.runtimes.values():
            if not paused:
                self._release_queue(rt)
        self.on_status()

    def _release_queue(self, rt):
        if not rt.mount or not rt.mount.running:
            return
        try:
            q = rt.mount.call("vfs/queue").get("queue") or []
        except rclone.RcError:
            return
        for item in q:
            if item.get("expiry", 0) > PAUSE_EXPIRY / 2:
                try:
                    rt.mount.call("vfs/queue-set-expiry", id=item["id"], expiry=0)
                except rclone.RcError:
                    pass

    # ------------------------------------------------------------ Status-Polling

    def _poll_loop(self):
        while not self._stop.wait(POLL_SECONDS):
            if self.sleeping.is_set():
                continue
            dirty = False
            for rt in list(self.runtimes.values()):
                try:
                    dirty |= self._poll_account(rt)
                except Exception as e:  # noqa: BLE001
                    rt.error = str(e)
            if dirty:
                self._write_state()
                self.on_status()

    def _poll_account(self, rt):
        m = rt.mount
        if not m or not m.running:
            return False
        old_busy, old_errors, old_status = set(rt.busy), set(rt.errors), rt.status
        try:
            q = m.call("vfs/queue").get("queue") or []
        except rclone.RcError:
            try:
                st = m.call("vfs/stats")
            except rclone.RcError as e:
                rt.status, rt.error = "offline", str(e)
                return old_status != rt.status
            q = [{"name": "", "uploading": True}] * st.get("diskCache", {}).get("uploadsQueued", 0)
        try:
            stats = m.call("core/stats")
            done = m.call("core/transferred").get("transferred") or []
        except rclone.RcError:
            stats, done = {}, []

        if self._paused():
            for item in q:
                if not item.get("uploading") and item.get("expiry", 0) < PAUSE_EXPIRY / 2:
                    try:
                        m.call("vfs/queue-set-expiry", id=item["id"], expiry=PAUSE_EXPIRY)
                    except rclone.RcError:
                        pass

        rt.queue = q
        rt.transfers = stats.get("transferring") or []
        names = {i["name"] for i in q if i.get("name")}
        rt._recent_uploads |= names
        busy = names | {t.get("name", "") for t in rt.transfers if t.get("name")}
        if rt.hydrating:
            busy.add(rt.hydrating)
        rt.busy = busy

        newly_done = set()
        # chronologisch verarbeiten, sonst ueberschreibt ein alter Fehler einen spaeteren Erfolg
        done = sorted(done, key=lambda t: t.get("completed_at") or t.get("started_at") or "")
        for t in done:
            key = (t.get("name"), t.get("completed_at") or t.get("timestamp"), t.get("what"))
            if key in rt._seen_transferred:
                continue
            rt._seen_transferred.add(key)
            name = t.get("name", "")
            newly_done.add(name)
            if name.startswith(".Trash-") or t.get("checked") and t.get("what") != "deleting":
                continue
            if t.get("error"):
                if "context canceled" in t["error"]:
                    # Upload abgebrochen, weil die Datei waehrenddessen geaendert wurde –
                    # rclone laedt automatisch die neue Fassung hoch. Kein Fehler.
                    continue
                rt.errors[name] = t["error"]
                rt.error_times[name] = time.time()
                self._add_activity(Activity(rt.account.id, name, "error", t.get("size", 0),
                                                  t["error"]))
                continue
            rt.errors.pop(name, None)
            rt.error_times.pop(name, None)
            if t.get("what") in ("moving", "renaming", "checking", "listing", "hashing"):
                continue
            if t.get("what") == "deleting":
                kind = "delete"
            elif name in rt._recent_uploads:
                kind = "upload"
                rt._recent_uploads.discard(name)
            else:
                kind = "download"
                size, got = t.get("size") or 0, t.get("bytes") or 0
                if got < size or (size <= SNIFF_SMALL_BYTES and
                                  _type_uncertain(os.path.basename(name))):
                    # nur angelesen (Dateityp-Erkennung/Vorschau) – kein echter Download
                    continue
            self._add_activity(Activity(rt.account.id, name, kind, t.get("size", 0)))
        self._drop_stale_errors(rt, names)
        if len(rt._seen_transferred) > 2000:
            rt._seen_transferred = set(list(rt._seen_transferred)[-500:])

        last_error = stats.get("lastError", "")
        if rt.status in ("ok", "syncing", "offline", "error"):
            if q or rt.transfers or rt.hydrating:
                rt.status = "syncing"
            elif _looks_offline(last_error) and stats.get("errors"):
                rt.status = "offline"
            else:
                rt.status = "ok"
            if rt.status != "error":
                rt.error = ""

        if time.time() - rt._last_quota > QUOTA_SECONDS:
            rt._last_quota = time.time()
            threading.Thread(target=self._update_quota, args=(rt,), daemon=True).start()

        if self.settings["redirect_trash"] and not rt._trash_busy:
            self._process_trash(rt)

        changed = (old_busy ^ rt.busy) | (old_errors ^ set(rt.errors)) | \
            {n for n in newly_done if n and not n.startswith(".Trash-")}
        if changed:
            self._changed_with_parents([rt.abs(p) for p in changed])
        return bool(changed) or old_status != rt.status or bool(q) or bool(rt.transfers)

    def _drop_stale_errors(self, rt, queued):
        """Fehler entfernen, die sich erledigt haben (Upload inzwischen fertig, Datei weg)."""
        view = rt.view
        now = time.time()
        for rel in list(rt.errors):
            if rel in rt.busy or rel in queued or now - rt.error_times.get(rel, 0) < 30:
                continue
            meta = states.read_meta(view.meta_path(rel)) if view.meta_dir else None
            gone = not os.path.lexists(rt.abs(rel))
            if gone or (meta and not meta.get("Dirty") and
                        states.meta_fully_cached(meta, view.data_path(rel))):
                rt.errors.pop(rel, None)
                rt.error_times.pop(rel, None)

    # ------------------------------------------------------------ Synchronisierungsprobleme

    def problems(self):
        """Alle offenen Probleme fuer die Oberflaeche."""
        result = []
        for rt in self.runtimes.values():
            if rt.status == "error" and rt.error:
                result.append({"acc_id": rt.account.id, "account": rt.account.label,
                               "rel": "", "path": rt.account.mount_path, "error": rt.error,
                               "time": 0, "kind": "account"})
            for rel, err in rt.errors.items():
                result.append({"acc_id": rt.account.id, "account": rt.account.label,
                               "rel": rel, "path": rt.abs(rel), "error": err,
                               "time": rt.error_times.get(rel, 0), "kind": "file"})
        result += self.backup.problems()
        return sorted(result, key=lambda p: -p["time"])

    def dismiss_problem(self, acc_id, rel):
        rt = self.runtimes.get(acc_id)
        if rt:
            rt.errors.pop(rel, None)
            rt.error_times.pop(rel, None)
            self._write_state()
            self._changed_with_parents([rt.abs(rel)])
            self.on_status()

    def retry_problem(self, acc_id, rel):
        rt = self.runtimes.get(acc_id)
        if not rt:
            return
        if not rel:
            self.restart_account(acc_id)
            return
        self.dismiss_problem(acc_id, rel)
        meta = states.read_meta(rt.view.meta_path(rel)) if rt.view.meta_dir else None
        if meta and meta.get("Dirty"):
            self._release_queue(rt)                 # Upload sofort neu anstossen
        else:
            self._enqueue("hydrate", rt, rel)       # Download erneut versuchen

    def merge_rescued(self, rt, rescued):
        """Vor dem Verbinden lokal gespeicherte Dateien in OneDrive uebernehmen.
        Gibt es online schon eine Datei gleichen Namens, entsteht eine Kopie
        „Name-RECHNER.ext“ (wie beim Windows-Client)."""
        host = socket.gethostname()
        moved, failed = 0, []
        for dirpath, _dirs, files in os.walk(rescued):
            rel_dir = os.path.relpath(dirpath, rescued)
            rel_dir = "" if rel_dir == "." else rel_dir
            for fn in files:
                src = os.path.join(dirpath, fn)
                rel = os.path.join(rel_dir, fn) if rel_dir else fn
                target = rt.abs(rel)
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.lexists(target):
                        stem, ext = os.path.splitext(fn)
                        n, cand = 1, os.path.join(os.path.dirname(target), f"{stem}-{host}{ext}")
                        while os.path.lexists(cand):
                            n += 1
                            cand = os.path.join(os.path.dirname(target), f"{stem}-{host}-{n}{ext}")
                        target = cand
                    shutil.copy2(src, target)
                    os.remove(src)
                    moved += 1
                except OSError as e:
                    failed.append(f"{rel}: {e}")
        for dirpath, _dirs, _files in sorted(os.walk(rescued), key=lambda x: -len(x[0])):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
        if moved:
            self.on_notify(rt.account.label,
                           f"{moved} {'Datei' if moved == 1 else 'Dateien'}, die vor dem Verbinden "
                           "gespeichert wurden, in OneDrive übernommen.", "info")
        if failed:
            rt.errors["(gerettete Dateien)"] = "Nicht übernommen, liegen in " + rescued + ": " + \
                "; ".join(failed[:5])
            rt.error_times["(gerettete Dateien)"] = time.time()
            self.on_status()

    def _update_quota(self, rt):
        try:
            about = rt.mount.call("operations/about", fs=rt.account.remote, timeout=30)
            rt.quota = (about.get("used", 0), about.get("total", 0))
            self.on_status()
        except (rclone.RcError, AttributeError):
            pass

    # ------------------------------------------------------------ Papierkorb

    def _process_trash(self, rt):
        trash = os.path.join(rt.account.mount_path, f".Trash-{os.getuid()}")
        files_dir = os.path.join(trash, "files")
        try:
            entries = os.listdir(files_dir)
        except OSError:
            return
        if not entries:
            return
        rt._trash_busy = True
        threading.Thread(target=self._empty_trash, args=(rt, trash, entries), daemon=True).start()

    def _empty_trash(self, rt, trash, entries):
        """Dateien, die Nemo/Nautilus in den Papierkorb verschiebt, werden auf OneDrive geloescht.
        OneDrive verschiebt geloeschte Elemente in den Online-Papierkorb (wiederherstellbar)."""
        try:
            view = rt.view
            trash_rel = os.path.relpath(trash, rt.account.mount_path)
            for name in entries:
                path = os.path.join(trash, "files", name)
                rel = f"{trash_rel}/files/{name}"
                try:
                    if os.path.isdir(path) and not os.path.islink(path):
                        if _subtree_dirty(view.meta_path(rel)):
                            shutil.rmtree(path)
                        else:
                            # ganzer Ordner in einem Schritt -> im Online-Papierkorb als Ordner
                            rt.mount.call("operations/purge", fs=rt.account.remote, remote=rel,
                                          timeout=300)
                            rt.mount.call("vfs/forget", dir=f"{trash_rel}/files")
                            shutil.rmtree(view.meta_path(rel), ignore_errors=True)
                            shutil.rmtree(view.data_path(rel), ignore_errors=True)
                    else:
                        os.remove(path)
                    self._add_activity(Activity(rt.account.id, name, "trash"))
                except (OSError, rclone.RcError) as e:
                    self._add_activity(Activity(rt.account.id, name, "error", error=str(e)))
                try:
                    os.remove(os.path.join(trash, "info", name + ".trashinfo"))
                except OSError:
                    pass
        finally:
            rt._trash_busy = False
            self.on_status()

    # ------------------------------------------------------------ Speicheroptimierung

    def _housekeeping_loop(self):
        last_pin, last_sense, last_partial = time.time(), 0, 0
        while not self._stop.wait(30):
            if self.sleeping.is_set():
                continue
            with self.fs_job():
                last_pin, last_sense, last_partial = self._housekeeping_round(
                    last_pin, last_sense, last_partial)

    def _housekeeping_round(self, last_pin, last_sense, last_partial):
        now = time.time()
        if now - last_partial > PARTIAL_CHECK_SECONDS:
            # Nur angelesene Dateien (Vorschau, Dateityp-Erkennung, abgebrochene Downloads)
            # belegen bei rclone trotzdem die volle Dateigroesse -> wieder freigeben.
            last_partial = now
            for rt in self.runtimes.values():
                if rt.mount and rt.mount.running:
                    self._evict(rt, "", only_partial=True)
        if now - last_pin > PIN_RESCAN_SECONDS:
            last_pin = now
            for rt in self.runtimes.values():
                if rt.mount and rt.mount.running:
                    self.queue_pinned_rescan(rt)
                    self.start_thumb_scan(rt)
        days = self.settings["storage_sense_days"]
        if days and now - last_sense > STORAGE_SENSE_SECONDS:
            last_sense = now
            for rt in self.runtimes.values():
                if rt.mount and rt.mount.running:
                    freed = self._evict(rt, "", older_than=now - days * 86400)
                    if freed > 50 * 1024 * 1024:
                        self.on_notify("Speicheroptimierung",
                                       f"{human_size(freed)} freigegeben ({rt.account.label})",
                                       "info")
        return last_pin, last_sense, last_partial

    def free_up_all_unused(self):
        total = 0
        for rt in self.runtimes.values():
            if rt.mount and rt.mount.running:
                total += self._evict(rt, "", older_than=time.time())
        return total

    # ------------------------------------------------------------ gemeinsamer Zustand

    def _changed_with_parents(self, paths):
        out = set()
        for p in paths:
            out.add(p)
            rt, _ = self.locate(p)
            if rt:
                parent = os.path.dirname(p)
                while len(parent) >= len(rt.account.mount_path):
                    out.add(parent)
                    if parent == rt.account.mount_path:
                        break
                    parent = os.path.dirname(parent)
        if out:
            self.on_changed(sorted(out))

    def _write_state(self):
        data = {
            "pid": os.getpid(),
            "accounts": [],
            "busy": {},
            "errors": {},
        }
        for rt in self.runtimes.values():
            m = rt.mount
            data["accounts"].append({
                "id": rt.account.id,
                "label": rt.account.label,
                "mount": rt.account.mount_path,
                "real": rt.account.real_path,
                "data_dir": m.data_dir if m and m.running else "",
                "meta_dir": m.meta_dir if m and m.running else "",
                "online": bool(m and m.running),
            })
            data["busy"][rt.account.id] = sorted(rt.busy)
            data["errors"][rt.account.id] = sorted(rt.errors)
        data["desktop"] = self.backup.desktop_local()
        if data != self._last_written_state:
            self._last_written_state = data
            try:
                config.atomic_write_json(config.STATE_FILE, data)
            except OSError:
                pass

    # ------------------------------------------------------------ Zusammenfassung

    def summary(self):
        """(zustand, text) fuer Tray-Symbol und Aktivitaetsfenster."""
        rts = list(self.runtimes.values())
        if not rts:
            return "setup", f"{marke.NAME} ist nicht eingerichtet"
        if self._paused():
            return "paused", "Synchronisierung angehalten"
        errors = [rt for rt in rts if rt.status == "error"]
        if errors:
            return "error", errors[0].error or "Fehler bei der Synchronisierung"
        if any(rt.status == "offline" for rt in rts):
            return "offline", "Keine Verbindung"
        if any(rt.status == "starting" for rt in rts):
            return "syncing", "Verbindung wird hergestellt …"
        uploads = sum(len(rt.queue) for rt in rts)
        downloads = sum(1 for rt in rts if rt.hydrating) + self.jobs.qsize()
        transfers = [t for rt in rts for t in rt.transfers]
        if uploads or downloads or transfers:
            parts = []
            if uploads:
                parts.append(f"{uploads} {'Datei wird' if uploads == 1 else 'Dateien werden'} "
                             "hochgeladen")
            if downloads:
                parts.append("Dateien werden heruntergeladen")
            if transfers and not parts:
                parts.append("Synchronisierung läuft")
            speed = sum(t.get("speed", 0) for t in transfers)
            text = ", ".join(parts)
            if speed:
                text += f" ({human_size(speed)}/s)"
            return "syncing", text
        if any(rt.errors for rt in rts):
            n = sum(len(rt.errors) for rt in rts)
            return "warning", f"{n} {'Element konnte' if n == 1 else 'Elemente konnten'} " \
                              "nicht synchronisiert werden"
        return "ok", "Ihre Dateien sind auf dem neuesten Stand"


def _atime(meta):
    from datetime import datetime
    s = meta.get("ATime") or meta.get("ModTime") or ""
    try:
        s = s[:26] + s[-6:] if "." in s and len(s) > 32 else s
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _subtree_dirty(meta_root):
    if not os.path.isdir(meta_root):
        return False
    for dirpath, _, files in os.walk(meta_root):
        for fn in files:
            m = states.read_meta(os.path.join(dirpath, fn))
            if m and m.get("Dirty"):
                return True
    return False


def _type_uncertain(name):
    """True, wenn GNOME den Typ nur durch Hineinlesen bestimmen kann (-> Content-Sniffing)."""
    from gi.repository import Gio
    return Gio.content_type_guess(name, None)[1]


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


def _looks_offline(err):
    err = (err or "").lower()
    return any(s in err for s in ("no such host", "network is unreachable", "dial tcp",
                                  "connection refused", "i/o timeout", "tls handshake"))


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
