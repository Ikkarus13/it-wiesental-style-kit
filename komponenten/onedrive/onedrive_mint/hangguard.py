"""Festhaengende Laufwerkszugriffe erkennen und loesen.

Antwortet rclone auf eine Anfrage des Kernels nicht (z. B. weil eine Verbindung zu Microsoft nach
dem Standby oder einem Netzwechsel ins Leere laeuft), bleibt jeder Prozess, der gerade auf das
Laufwerk zugreift, im Zustand D stehen – ohne Zeitlimit. Solche Prozesse kann der Kernel nicht
einfrieren, und der Standby scheitert immer wieder, bis der Rechner haengt.

Erkennung: Ein Thread steht im Kernel in `request_wait_answer` (wartet auf eine FUSE-Antwort) und
hat sich seit einer Weile nicht mehr bewegt (Kontextwechsel unveraendert), und eines unserer
Laufwerke hat offene Anfragen (`/sys/fs/fuse/connections/<n>/waiting`). Loesung: Die Verbindung
ueber `/sys/fs/fuse/connections/<n>/abort` abbrechen (gehoert dem Benutzer, kein root noetig) –
alle wartenden Zugriffe kehren dann mit einem Fehler zurueck – und das Laufwerk neu einhaengen.

Liest nie etwas vom Laufwerk selbst, sonst koennte die Pruefung selbst haengen bleiben.
"""

import os
import time

FUSECTL = "/sys/fs/fuse/connections"
FUSE_WAIT = ("request_wait_answer",)


def _unescape(path):
    # /proc/self/mountinfo kodiert Leerzeichen & Co. oktal (\040)
    out, i = [], 0
    while i < len(path):
        if path[i] == "\\" and path[i + 1:i + 4].isdigit():
            out.append(chr(int(path[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def fuse_conn(path):
    """Nummer der FUSE-Verbindung, die unter `path` eingehaengt ist (oder None)."""
    path = os.path.realpath(path)
    found = None
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and _unescape(parts[4]) == path:
                    found = parts[2].split(":")[1]   # zuletzt eingehaengt gewinnt
    except (OSError, IndexError):
        return None
    if found and os.path.isdir(os.path.join(FUSECTL, found)):
        return found
    return None


def waiting(conn):
    try:
        with open(os.path.join(FUSECTL, conn, "waiting")) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def abort(conn):
    try:
        with open(os.path.join(FUSECTL, conn, "abort"), "w") as f:
            f.write("1")
        return True
    except OSError:
        return False


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def own_pids():
    """Nur Prozesse dieses Benutzers: Andere Benutzer koennen unsere Laufwerke ohnehin nicht
    oeffnen (kein allow_other), und fremde Prozesse abzufragen sieht nach Ausspaehen aus."""
    uid = os.getuid()
    try:
        entries = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []
    pids = []
    for p in entries:
        try:
            if os.stat(f"/proc/{p}").st_uid == uid:
                pids.append(p)
        except OSError:
            continue
    return pids


def fuse_waiters():
    """{(pid, tid): (name, kontextwechsel)} eigener Threads, die auf eine FUSE-Antwort warten."""
    result = {}
    for pid in own_pids():
        try:
            tids = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for tid in tids:
            base = f"/proc/{pid}/task/{tid}"
            if _read(base + "/wchan").strip() not in FUSE_WAIT:
                continue
            name, switches = "?", 0
            for line in _read(base + "/status").splitlines():
                if line.startswith("Name:"):
                    name = (line.split(None, 1) + ["?"])[1].strip()
                elif line.startswith(("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")):
                    switches += int(line.split()[1])
            result[(int(pid), int(tid))] = (name, switches)
    return result


class HangGuard:
    def __init__(self):
        self._seen = {}   # (pid, tid) -> (kontextwechsel, seit)

    def check(self, threshold, mounts, now=None):
        """mounts: [(objekt, verbindung)]. Liefert ([objekte mit Haenger], [beschreibungen]),
        sobald ein Thread mindestens `threshold` Sekunden unbewegt auf eine Antwort wartet."""
        now = time.monotonic() if now is None else now
        if not any(conn and waiting(conn) > 0 for _obj, conn in mounts):
            # Keine offene Anfrage an unsere Laufwerke: nichts kann haengen, /proc bleibt unberuehrt
            self._seen = {}
            return [], []
        current = fuse_waiters()
        seen = {}
        stuck = []
        for key, (name, switches) in current.items():
            old = self._seen.get(key)
            since = old[1] if old and old[0] == switches else now
            seen[key] = (switches, since)
            if now - since >= threshold:
                stuck.append(f"{name} (PID {key[0]}, seit {int(now - since)} s)")
        self._seen = seen
        if not stuck:
            return [], []
        hung = [obj for obj, conn in mounts if conn and waiting(conn) > 0]
        return hung, (stuck if hung else [])
