"""Ordnersicherung – Desktop, Dokumente und Bilder mit OneDrive verbinden (wie unter Windows).

* Dokumente, Bilder: werden ins OneDrive verschoben. Das XDG-Benutzerverzeichnis zeigt danach
  auf den Ordner im OneDrive, der alte Pfad (z. B. ~/Dokumente) wird eine Verknuepfung dorthin.
  Es gilt „Dateien bei Bedarf“.
* Desktop: bleibt ein echter lokaler Ordner, damit er auch ohne laufenden Client funktioniert.
  Die Dateien darauf werden per `rclone bisync` in beide Richtungen mit OneDrive/Desktop
  abgeglichen. Ordner auf dem Desktop liegen im OneDrive und erscheinen als Verknuepfung.

Nichts davon ist aktiv, bis es in den Einstellungen (Reiter „Sicherung“) eingeschaltet wird.
"""

import os
import shutil
import socket
import subprocess
import threading
import time

from . import config, rclone

KNOWN = {
    "desktop": {"xdg": "DESKTOP", "label": "Desktop", "icon": "user-desktop",
                "remote": ("Desktop", "Schreibtisch")},
    "documents": {"xdg": "DOCUMENTS", "label": "Dokumente", "icon": "folder-documents",
                  "remote": ("Dokumente", "Documents")},
    "pictures": {"xdg": "PICTURES", "label": "Bilder", "icon": "folder-pictures",
                 "remote": ("Bilder", "Pictures")},
}

USER_DIRS = os.path.join(config._xdg("XDG_CONFIG_HOME", "~/.config"), "user-dirs.dirs")
USER_DIRS_CONF = os.path.join(config._xdg("XDG_CONFIG_HOME", "~/.config"), "user-dirs.conf")
BISYNC_DIR = os.path.join(config.STATE_DIR, "bisync")
BISYNC_LOG = os.path.join(config.LOG_DIR, "desktop-bisync.log")

# Dateien, die nicht zwischen Desktop und OneDrive abgeglichen werden
DESKTOP_EXCLUDES = ("/*/**", "desktop.ini", "Thumbs.db", ".hidden", ".Trash-*/**",
                    "~$*", ".~lock.*#", "*.tmp", "*.part", "*.crdownload")
DESKTOP_POLL_SECONDS = 5        # lokale Aenderungen erkennen
DESKTOP_PERIODIC_SECONDS = 60   # Aenderungen aus OneDrive holen


class BackupError(Exception):
    pass


# ------------------------------------------------------------------ XDG-Benutzerverzeichnisse

def read_user_dirs():
    result = {}
    try:
        with open(USER_DIRS) as f:
            for line in f:
                line = line.strip()
                if line.startswith("XDG_") and "=" in line:
                    key, val = line.split("=", 1)
                    val = val.strip('"').replace("$HOME", os.path.expanduser("~"))
                    result[key[4:-4]] = val          # XDG_DOCUMENTS_DIR -> DOCUMENTS
    except OSError:
        pass
    return result


def set_user_dir(key, path):
    subprocess.run(["xdg-user-dirs-update", "--set", key, path], capture_output=True)


def _set_autoupdate(enabled):
    """xdg-user-dirs-update setzt beim Anmelden Verzeichnisse zurueck, die gerade nicht
    existieren – bei einem noch nicht verbundenen OneDrive waere das fatal."""
    try:
        with open(USER_DIRS_CONF) as f:
            lines = [l for l in f.read().splitlines() if not l.startswith("enabled=")]
    except OSError:
        lines = []
    lines.insert(0, f"enabled={'True' if enabled else 'False'}")
    with open(USER_DIRS_CONF, "w") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ Hilfsfunktionen

def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.lstat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    return total


def conflict_name(target):
    """Freier Name nach Windows-Art: 'Bericht-RECHNER.docx', 'Bericht-RECHNER-2.docx'."""
    if not os.path.lexists(target):
        return target
    host = socket.gethostname()
    d, fn = os.path.split(target)
    stem, ext = os.path.splitext(fn) if not os.path.isdir(target) else (fn, "")
    n, cand = 1, os.path.join(d, f"{stem}-{host}{ext}")
    while os.path.lexists(cand):
        n += 1
        cand = os.path.join(d, f"{stem}-{host}-{n}{ext}")
    return cand


def find_unsupported(path):
    """Symbolische Links und Spezialdateien – kann OneDrive nicht speichern."""
    import stat
    found = []
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                mode = os.lstat(p).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                found.append(p)
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return found


def move_into(src_dir, dst_dir, only_dirs=False, progress=None):
    """Inhalt von src_dir nach dst_dir verschieben (ueber den Mount -> Upload).
    Gibt Liste der Fehler zurueck."""
    errors = []
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        if os.path.islink(src):
            continue                    # OneDrive kennt keine symbolischen Links
        if only_dirs and not os.path.isdir(src):
            continue
        dst = conflict_name(os.path.join(dst_dir, name))
        try:
            if progress:
                progress(name)
            shutil.move(src, dst)
        except (OSError, shutil.Error) as e:
            errors.append(f"{name}: {e}")
    return errors


# ------------------------------------------------------------------ Verwaltung

class FolderBackup:
    def __init__(self, engine):
        self.engine = engine
        self.settings = engine.settings
        self.errors = {}                 # key -> Meldung
        self.busy = {}                   # key -> Statustext waehrend Ein-/Ausschalten
        self.last_sync = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    # -- Zustand

    @property
    def state(self):
        return self.settings["folder_backup"] or {"account": "", "folders": {}}

    def _save(self, state):
        self.settings["folder_backup"] = state

    def account_id(self):
        return self.state.get("account", "")

    def enabled(self, key):
        return key in self.state.get("folders", {})

    def any_enabled(self):
        return bool(self.state.get("folders"))

    def desktop_local(self):
        f = self.state.get("folders", {}).get("desktop")
        return f["local"] if f else ""

    def _rt(self, acc_id=None):
        rt = self.engine.runtimes.get(acc_id or self.account_id())
        if not rt or not rt.mount or not rt.mount.running:
            raise BackupError("Das gewählte OneDrive ist nicht verbunden.")
        return rt

    def _remote_name(self, rt, key):
        """Vorhandenen Ordner im OneDrive verwenden (z. B. vom Windows-PC), sonst anlegen."""
        names = {n.lower(): n for n in os.listdir(rt.account.mount_path)}
        for cand in KNOWN[key]["remote"]:
            if cand.lower() in names:
                return names[cand.lower()]
        name = KNOWN[key]["remote"][0]
        os.makedirs(os.path.join(rt.account.mount_path, name), exist_ok=True)
        return name

    def info(self, key):
        """Anzeige fuer die Einstellungen."""
        f = self.state.get("folders", {}).get(key)
        dirs = read_user_dirs()
        rt = self.engine.runtimes.get(self.account_id())
        if f:
            target = os.path.join(rt.account.mount_path, f["remote"]) if rt else f["remote"]
            return {"enabled": True, "local": f["local"], "target": target,
                    "account": rt.account.label if rt else "?", "busy": self.busy.get(key),
                    "error": self.errors.get(key)}
        return {"enabled": False, "local": dirs.get(KNOWN[key]["xdg"], ""),
                "busy": self.busy.get(key), "error": self.errors.get(key)}

    def check_space(self, acc_id, key):
        """(lokale Groesse, freier OneDrive-Speicher oder None)."""
        local = read_user_dirs().get(KNOWN[key]["xdg"], "")
        size = dir_size(local) if local and os.path.isdir(local) else 0
        free = None
        rt = self.engine.runtimes.get(acc_id)
        try:
            about = rt.mount.call("operations/about", fs=rt.account.remote, timeout=30)
            if about.get("free") is not None:
                free = about["free"]
            elif about.get("total"):
                free = about["total"] - about.get("used", 0)
        except (rclone.RcError, AttributeError):
            pass
        return size, free

    # -- Einschalten

    def enable(self, key, acc_id, progress=None):
        with self._lock:
            if self.any_enabled() and self.account_id() != acc_id:
                raise BackupError("Andere Ordner sind bereits mit einem anderen OneDrive verbunden. "
                                  "Bitte dort zuerst die Sicherung beenden.")
            rt = self._rt(acc_id)
            dirs = read_user_dirs()
            local = dirs.get(KNOWN[key]["xdg"]) or os.path.expanduser("~/" + KNOWN[key]["label"])
            if os.path.islink(local):
                raise BackupError(f"{local} ist bereits eine Verknüpfung.")
            os.makedirs(local, exist_ok=True)
            remote = self._remote_name(rt, key)
            target = os.path.join(rt.account.mount_path, remote)
            self.busy[key] = "Wird eingerichtet …"
            self.errors.pop(key, None)
            try:
                if key == "desktop":
                    self._enable_desktop(rt, local, target, progress)
                else:
                    self._enable_redirect(key, local, target, progress)
            finally:
                self.busy.pop(key, None)
            state = self.state
            state["account"] = acc_id
            state.setdefault("folders", {})[key] = {"local": local, "remote": remote,
                                                    "since": time.time(), "links": []}
            if key == "desktop":
                state["folders"][key]["links"] = sorted(
                    n for n in os.listdir(local) if os.path.islink(os.path.join(local, n)))
            self._save(state)
            _set_autoupdate(False)
            # Dokumente/Bilder liegen jetzt im OneDrive -> Datei-Indexer dort heraushalten
            self.engine.exclude_from_indexer()
            if key == "desktop":
                self.start()

    def _enable_redirect(self, key, local, target, progress):
        unsupported = find_unsupported(local)
        if unsupported:
            raise BackupError(f"{local} enthält Elemente, die OneDrive nicht speichern kann "
                              "(symbolische Links, Sockets, Pipes). Bitte zuerst entfernen:\n" +
                              "\n".join(unsupported[:10]) +
                              (f"\n… und {len(unsupported) - 10} weitere" if len(unsupported) > 10
                               else ""))
        errs = move_into(local, target, progress=progress)
        if errs or os.listdir(local):
            raise BackupError("Nicht alles konnte verschoben werden (die Dateien liegen noch in "
                              f"{local}):\n" + "\n".join(errs[:10]))
        os.rmdir(local)
        os.symlink(target, local)
        set_user_dir(KNOWN[key]["xdg"], target)

    def _enable_desktop(self, rt, local, target, progress):
        unsupported = [u for u in find_unsupported(local)
                       if os.sep in os.path.relpath(u, local)]   # Links direkt auf dem Desktop ok
        if unsupported:
            raise BackupError("Ordner auf dem Desktop enthalten Elemente, die OneDrive nicht "
                              "speichern kann (symbolische Links, Sockets, Pipes):\n" +
                              "\n".join(unsupported[:10]))
        # 1. lokale Ordner ins OneDrive verschieben und durch Verknuepfungen ersetzen
        errs = move_into(local, target, only_dirs=True, progress=progress)
        if errs:
            raise BackupError("Nicht alle Ordner konnten verschoben werden:\n" + "\n".join(errs[:10]))
        self._link_folders(local, target, links=set())
        # 2. Dateien erstmalig abgleichen (Vereinigung, neuere Fassung gewinnt)
        if progress:
            progress("Dateien werden abgeglichen …")
        ok, msg = self._bisync(rt, local, f"{rt.account.remote}{os.path.basename(target)}",
                               resync=True)
        if not ok:
            raise BackupError("Erster Abgleich fehlgeschlagen: " + msg)
        self._write_hidden(local)

    # -- Ausschalten

    def disable(self, key, copy_back=False, progress=None):
        with self._lock:
            state = self.state
            f = state.get("folders", {}).get(key)
            if not f:
                return
            rt = self.engine.runtimes.get(state.get("account"))
            target = os.path.join(rt.account.mount_path, f["remote"]) if rt else ""
            local = f["local"]
            self.busy[key] = "Sicherung wird beendet …"
            try:
                if key == "desktop":
                    self._disable_desktop(local, target, copy_back, progress)
                else:
                    if os.path.islink(local):
                        os.unlink(local)
                    os.makedirs(local, exist_ok=True)
                    set_user_dir(KNOWN[key]["xdg"], local)
                    if copy_back and target and os.path.isdir(target):
                        self._copy_tree(target, local, progress)
            finally:
                self.busy.pop(key, None)
            state["folders"].pop(key, None)
            if not state["folders"]:
                state["account"] = ""
                _set_autoupdate(True)
            self._save(state)
            self.errors.pop(key, None)

    def _disable_desktop(self, local, target, copy_back, progress):
        self._stop.set()
        self._wake.set()
        for name in os.listdir(local):
            p = os.path.join(local, name)
            if os.path.islink(p) and os.path.realpath(p).startswith(os.path.realpath(target) + "/"):
                real = os.path.realpath(p)
                os.unlink(p)
                if copy_back and os.path.isdir(real):
                    self._copy_tree(real, p, progress)
        shutil.rmtree(BISYNC_DIR, ignore_errors=True)

    @staticmethod
    def _copy_tree(src, dst, progress):
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            os.makedirs(os.path.join(dst, rel), exist_ok=True)
            for fn in files:
                if progress:
                    progress(fn)
                out = conflict_name(os.path.join(dst, rel, fn))
                try:
                    shutil.copy2(os.path.join(root, fn), out)
                except OSError:
                    pass

    # -- Desktop: Hintergrund-Abgleich

    def start(self):
        if not self.enabled("desktop") or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._desktop_loop, daemon=True, name="desktop")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def on_mounted(self, rt):
        if rt.account.id == self.account_id():
            self.start()
            self._wake.set()

    def sync_now(self):
        self._wake.set()

    def _signature(self, local):
        sig = []
        try:
            for e in os.scandir(local):
                try:
                    st = e.stat(follow_symlinks=False)
                    sig.append((e.name, st.st_size, st.st_mtime_ns, e.is_symlink()))
                except OSError:
                    pass
        except OSError:
            pass
        return tuple(sorted(sig))

    def _desktop_loop(self):
        last_sig, last_run, pending_since = None, 0, None
        while not self._stop.is_set():
            self._wake.wait(DESKTOP_POLL_SECONDS)
            woke = self._wake.is_set()
            self._wake.clear()
            if self._stop.is_set() or not self.enabled("desktop"):
                break
            if self.engine.sleeping.is_set():
                continue    # Standby: nicht aufs Laufwerk zugreifen
            f = self.state["folders"]["desktop"]
            sig = self._signature(f["local"])
            now = time.time()
            if sig != last_sig:
                # lokale Aenderung: kurz warten, bis Speichern/Kopieren fertig ist
                pending_since = pending_since or now
                last_sig = sig
                if now - pending_since < 3 and not woke:
                    continue
            due = woke or pending_since or now - last_run > DESKTOP_PERIODIC_SECONDS
            if not due:
                continue
            try:
                with self.engine.fs_job():
                    self._desktop_cycle()
                last_sig = self._signature(f["local"])
            except Exception as e:  # noqa: BLE001
                self.errors["desktop"] = str(e)
            pending_since, last_run = None, time.time()
            self.engine.on_status()

    def _desktop_cycle(self):
        rt = self._rt()
        state = self.state
        f = state["folders"]["desktop"]
        local, target = f["local"], os.path.join(rt.account.mount_path, f["remote"])
        links = set(f.get("links", []))
        # neue lokale Ordner ins OneDrive verschieben
        errs = move_into(local, target, only_dirs=True)
        links = self._link_folders(local, target, links, rt=rt, remote=f["remote"])
        f["links"] = sorted(links)
        self._save(state)
        self._write_hidden(local)
        ok, msg = self._bisync(rt, local, f"{rt.account.remote}{f['remote']}")
        if not ok and "--resync" in msg:
            ok, msg = self._bisync(rt, local, f"{rt.account.remote}{f['remote']}", resync=True)
        if ok and not errs:
            self.errors.pop("desktop", None)
            self.last_sync = time.time()
        else:
            self.errors["desktop"] = msg if not ok else "; ".join(errs[:5])

    def _link_folders(self, local, target, links, rt=None, remote=None):
        """Ordner im OneDrive-Desktop als Verknuepfungen auf dem lokalen Desktop abbilden."""
        try:
            remote_dirs = {n for n in os.listdir(target)
                           if not n.startswith(".") and os.path.isdir(os.path.join(target, n))}
        except OSError:
            return links
        for name in sorted(remote_dirs):
            p = os.path.join(local, name)
            if os.path.lexists(p):
                if os.path.islink(p):
                    links.add(name)
                continue
            if name in links and rt is not None:
                # Verknuepfung wurde auf dem Desktop geloescht -> Ordner in OneDrive loeschen
                # (landet im OneDrive-Papierkorb und ist wiederherstellbar)
                try:
                    rt.mount.call("operations/purge", fs=rt.account.remote,
                                  remote=f"{remote}/{name}", timeout=300)
                    rt.mount.call("vfs/forget", dir=remote)
                except rclone.RcError as e:
                    self.errors["desktop"] = f"Ordner „{name}“ konnte nicht gelöscht werden: {e}"
                    continue
                links.discard(name)
                continue
            os.symlink(os.path.join(target, name), p)
            links.add(name)
        # Verknuepfungen auf Ordner, die es online nicht mehr gibt
        for name in list(links):
            p = os.path.join(local, name)
            if name not in remote_dirs and os.path.islink(p):
                os.unlink(p)
                links.discard(name)
            elif not os.path.lexists(p) and name not in remote_dirs:
                links.discard(name)
        return links

    @staticmethod
    def _write_hidden(local):
        """Windows-Verknuepfungen (.lnk) ausblenden – unter Linux nicht nutzbar."""
        names = sorted(n for n in os.listdir(local) if n.lower().endswith((".lnk", ".url")))
        path = os.path.join(local, ".hidden")
        try:
            with open(path) as fh:
                if fh.read().splitlines() == names:
                    return
        except OSError:
            pass
        if names or os.path.exists(path):
            with open(path, "w") as fh:
                fh.write("\n".join(names) + ("\n" if names else ""))

    def _bisync(self, rt, local, remote, resync=False):
        binary = self.engine.binary
        if not binary:
            return False, "rclone ist nicht installiert."
        workdir = os.path.join(BISYNC_DIR, rt.account.id)
        os.makedirs(workdir, exist_ok=True)
        os.makedirs(config.LOG_DIR, exist_ok=True)
        args = [binary, "bisync", local, remote,
                "--config", config.account_rclone_conf(rt.account.id),
                "--workdir", workdir,
                "--resilient", "--recover", "--max-lock", "5m",
                "--conflict-resolve", "newer", "--conflict-loser", "pathname",
                "--conflict-suffix", f"{socket.gethostname()},OneDrive", "--suffix-keep-extension",
                "--skip-links", "--compare", "size,modtime",
                "--log-file", BISYNC_LOG, "--log-level", "INFO"]
        for ex in DESKTOP_EXCLUDES:
            args += ["--exclude", ex]
        if resync:
            args += ["--resync", "--resync-mode", "newer"]
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return False, "Zeitüberschreitung beim Abgleich"
        if p.returncode == 0:
            return True, ""
        out = (p.stderr or p.stdout or "").strip().splitlines()
        relevant = [l for l in out if "ERROR" in l or "resync" in l.lower() or "Bisync aborted" in l]
        return False, " ".join((relevant or out)[-3:])[:500] or f"Exitcode {p.returncode}"

    def aliases(self, rt):
        """(Ziel im OneDrive, lokaler Pfad) – Dateimanager sehen Dateien auch unter diesen
        Verknuepfungspfaden (z. B. ~/Dokumente/…, ~/Schreibtisch/Ordner/…)."""
        if rt.account.id != self.account_id():
            return []
        result = []
        for f in self.state.get("folders", {}).values():
            result.append((os.path.join(rt.account.mount_path, f["remote"]), f["local"]))
        return result

    # -- fuer die Oberflaeche

    def problems(self):
        rt = self.engine.runtimes.get(self.account_id())
        label = rt.account.label if rt else "OneDrive"
        return [{"acc_id": self.account_id(), "account": label, "rel": "",
                 "path": self.state.get("folders", {}).get(k, {}).get("local", ""),
                 "error": f"Ordnersicherung „{KNOWN[k]['label']}“: {msg}", "time": 0,
                 "kind": "backup", "backup_key": k}
                for k, msg in self.errors.items()]

