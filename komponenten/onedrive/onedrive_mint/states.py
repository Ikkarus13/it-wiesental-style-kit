"""Zustandsberechnung fuer "Dateien bei Bedarf".

Wird vom Hintergrunddienst UND von der Nemo/Nautilus-Erweiterung benutzt,
deshalb hier keine GTK-Importe.

Zustaende (wie im Windows-Client):
  cloud   - Nur online verfuegbar (nicht im lokalen Cache)
  local   - Lokal verfuegbar (vollstaendig im Cache, darf freigegeben werden)
  pinned  - Immer auf diesem Geraet behalten
  sync    - Wird synchronisiert (Upload ausstehend / Download laeuft)
  error   - Synchronisierungsfehler
"""

import json
import os
import threading

from . import config

CLOUD, LOCAL, PINNED, SYNC, ERROR = "cloud", "local", "pinned", "sync", "error"

LABELS = {
    CLOUD: "Nur online verfügbar",
    LOCAL: "Auf diesem Gerät verfügbar",
    PINNED: "Immer auf diesem Gerät verfügbar",
    SYNC: "Wird synchronisiert",
    ERROR: "Synchronisierungsfehler",
}

EMBLEMS = {
    CLOUD: "odm-cloud",
    LOCAL: "odm-local",
    PINNED: "odm-pinned",
    SYNC: "odm-sync",
    ERROR: "odm-error",
}

PIN, UNPIN = "pin", "unpin"


class _MtimeJson:
    """Liest eine JSON-Datei nur neu, wenn sie sich geaendert hat."""

    def __init__(self, path, default):
        self.path, self.default = path, default
        self._mtime, self._data = None, default

    def get(self):
        try:
            m = os.stat(self.path).st_mtime_ns
        except OSError:
            self._mtime, self._data = None, self.default
            return self._data
        if m != self._mtime:
            self._data = config.read_json(self.path, self.default)
            self._mtime = m
        return self._data


class PinStore:
    """Explizite Entscheidungen pro Pfad (relativ zur Kontowurzel, '' = Wurzel).

    Die naechste explizite Entscheidung entlang des Pfades gilt; so kann man
    einen Ordner "immer behalten" und einzelne Unterordner trotzdem "nur online" machen.
    """

    def __init__(self, acc_id):
        self.path = config.account_pins_file(acc_id)
        self._file = _MtimeJson(self.path, {})
        self._lock = threading.Lock()

    def decisions(self):
        return self._file.get()

    def decision(self, rel):
        d = self.decisions()
        if not d:
            return None
        p = rel
        while True:
            if p in d:
                return d[p]
            if not p:
                return None
            p = os.path.dirname(p)

    def is_pinned(self, rel):
        return self.decision(rel) == PIN

    def set(self, rel, value):
        """value: PIN, UNPIN oder None (Entscheidung entfernen)."""
        with self._lock:
            d = dict(config.read_json(self.path, {}))
            prefix = rel + "/" if rel else ""
            # Unterentscheidungen werden durch die neue Entscheidung ersetzt
            for k in list(d):
                if k == rel or k.startswith(prefix):
                    del d[k]
            if value is not None:
                # nicht speichern, wenn es ohnehin vom Elternordner geerbt wird
                parent = self._decision_in(d, os.path.dirname(rel)) if rel else None
                if value != (parent or UNPIN):
                    d[rel] = value
            config.atomic_write_json(self.path, d)

    @staticmethod
    def _decision_in(d, rel):
        p = rel
        while True:
            if p in d:
                return d[p]
            if not p:
                return None
            p = os.path.dirname(p)

    def pinned_roots(self):
        return sorted(k for k, v in self.decisions().items() if v == PIN)


def read_meta(meta_path):
    try:
        with open(meta_path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def meta_fully_cached(meta, data_path):
    if not meta:
        return False
    size = meta.get("Size") or 0
    if size == 0:
        return os.path.exists(data_path)
    pos = 0
    for r in sorted(meta.get("Rs") or [], key=lambda r: r.get("Pos", 0)):
        if r.get("Pos", 0) > pos:
            return False
        pos = max(pos, r.get("Pos", 0) + r.get("Size", 0))
    return pos >= size and os.path.exists(data_path)


class AccountView:
    """Leseansicht auf ein Konto: Mountpunkt, Cache-Pfade, Pins."""

    def __init__(self, acc_id, mount, data_dir, meta_dir, real=""):
        self.id = acc_id
        self.mount = mount.rstrip("/")
        self.real = real.rstrip("/")     # versteckter Einhaengepunkt hinter der Verknuepfung
        self.data_dir = data_dir
        self.meta_dir = meta_dir
        self.pins = PinStore(acc_id)

    def rel(self, path):
        path = os.path.abspath(path)
        if path == self.mount:
            return ""
        if path.startswith(self.mount + "/"):
            return path[len(self.mount) + 1:]
        if self.real and path == self.real:
            return ""
        if self.real and path.startswith(self.real + "/"):
            return path[len(self.real) + 1:]
        return None

    def path(self, rel):
        """Sichtbarer Pfad (ueber die Verknuepfung) zu rel."""
        return os.path.join(self.mount, rel) if rel else self.mount

    def data_path(self, rel):
        return os.path.join(self.data_dir, rel) if self.data_dir else ""

    def meta_path(self, rel):
        return os.path.join(self.meta_dir, rel) if self.meta_dir else ""

    def file_state(self, rel, busy=(), errors=()):
        if rel in errors:
            return ERROR
        if rel in busy:
            return SYNC
        meta = read_meta(self.meta_path(rel)) if self.meta_dir else None
        if meta and meta.get("Dirty"):
            return SYNC
        full = meta_fully_cached(meta, self.data_path(rel))
        if self.pins.is_pinned(rel):
            return PINNED if full else SYNC
        return LOCAL if full else CLOUD

    def dir_state_quick(self, rel, busy=()):
        """Zustand eines Ordners ohne den Mount zu lesen (None = unbekannt, genauer pruefen)."""
        prefix = rel + "/" if rel else ""
        if any(b == rel or b.startswith(prefix) for b in busy):
            return SYNC
        if self.pins.is_pinned(rel):
            return PINNED
        if self.meta_dir and not os.path.isdir(self.meta_path(rel)):
            return CLOUD
        return None

    def dir_state_full(self, rel, busy=()):
        """Ordner ist 'lokal', wenn alle direkten Dateien vollstaendig im Cache sind."""
        quick = self.dir_state_quick(rel, busy)
        if quick is not None:
            return quick
        try:
            entries = list(os.scandir(os.path.join(self.mount, rel)))
        except OSError:
            return CLOUD
        for e in entries:
            child = os.path.join(rel, e.name) if rel else e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                return CLOUD
            if is_dir:
                if self.dir_state_quick(child, busy) in (CLOUD, None) and \
                        not os.path.isdir(self.meta_path(child)):
                    return CLOUD
                continue
            st = self.file_state(child, busy)
            if st == SYNC:
                return SYNC
            if st == CLOUD:
                return CLOUD
        return LOCAL


class RuntimeState:
    """Liest state.json, das der Hintergrunddienst schreibt."""

    def __init__(self):
        self._file = _MtimeJson(config.STATE_FILE, {})
        self._views = {}
        self._sig = None

    def raw(self):
        return self._file.get()

    def accounts(self):
        data = self.raw()
        sig = json.dumps(data.get("accounts", []), sort_keys=True)
        if sig != self._sig:
            self._views = {}
            for a in data.get("accounts", []):
                self._views[a["id"]] = AccountView(a["id"], a["mount"],
                                                   a.get("data_dir", ""), a.get("meta_dir", ""),
                                                   a.get("real", ""))
            self._sig = sig
        return list(self._views.values())

    def find(self, path):
        for v in self.accounts():
            rel = v.rel(path)
            if rel is not None:
                return v, rel
        return None, None

    def busy(self, acc_id):
        return set(self.raw().get("busy", {}).get(acc_id, []))

    def errors(self, acc_id):
        return set(self.raw().get("errors", {}).get(acc_id, []))

    def running(self):
        return bool(self.raw().get("pid")) and _pid_alive(self.raw().get("pid"))


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False
