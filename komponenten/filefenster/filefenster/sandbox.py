"""Externe Vorschau-Programme abgeschottet ausführen (bubblewrap, wie GNOME).

pdftoppm, ffmpegthumbnailer und die Thumbnailer aus /usr/share/thumbnailers
lesen fremde, womöglich präparierte Dateien. Eine Lücke in einem dieser
Programme soll nicht an Home, Schlüssel oder Netz kommen. Deshalb laufen sie
in einer bwrap-Sandbox:

  * /usr (und die Verweise /bin, /lib …) nur lesbar, aus /etc nur das Nötigste
    (Bibliothekspfade, Schriften, alternatives, Zeitzone)
  * die Eingabedatei(en) nur lesbar am selben Pfad – sonst kein Home
  * ein privater, beschreibbarer Ordner für die Ausgabe (outdir, zugleich HOME),
    sonst nur ein leeres /tmp im Speicher
  * kein Netzwerk, keine Sicht auf andere Prozesse/IPC (--unshare-all),
    stirbt mit FileFenster (--die-with-parent), eigene Sitzung (--new-session:
    kein Einschleusen von Eingaben ins Terminal per TIOCSTI)
  * minimale Umgebung (PATH, LANG, HOME, GIO_USE_VFS=local)

Fehlt bwrap oder ist es nicht nutzbar (z. B. Benutzer-Namensräume gesperrt),
läuft das Programm wie bisher ohne Sandbox; das wird einmal gemeldet.

FILEFENSTER_BWRAP: anderer Pfad zu bwrap; "0"/"aus" schaltet die Sandbox ab.
"""
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

MAX_OUTPUT = 64 * 1024 * 1024     # mehr Ausgabe liest FileFenster von keinem Hilfsprogramm

# aus /etc nur, was Programme zum Starten und Zeichnen brauchen
_ETC = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/fonts",
        "/etc/alternatives", "/etc/localtime")
_TOPLEVEL = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")

_lock = threading.Lock()
_state = None           # (bwrap-Pfad oder None, Grund)


def _bwrap_path():
    v = os.environ.get("FILEFENSTER_BWRAP")
    if v is not None:
        if v.strip().lower() in ("", "0", "aus", "off", "no"):
            return None
        return v if os.path.isabs(v) and os.access(v, os.X_OK) else None
    return shutil.which("bwrap")


def _base_args(bwrap):
    args = [bwrap, "--ro-bind", "/usr", "/usr"]
    for d in _TOPLEVEL:
        if os.path.islink(d):
            args += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            args += ["--ro-bind", d, d]
    for p in _ETC:
        args += ["--ro-bind-try", p, p]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
             "--unshare-all", "--die-with-parent", "--new-session", "--chdir", "/"]
    return args


def _env(home):
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "TMPDIR": "/tmp",
           "GIO_USE_VFS": "local"}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def status():
    """(bwrap-Pfad, "") wenn die Sandbox nutzbar ist, sonst (None, Grund).
    Wird einmal geprüft; ein Fehlschlag wird einmal auf stderr gemeldet."""
    global _state
    with _lock:
        if _state is not None:
            return _state
        bwrap = _bwrap_path()
        reason = ""
        if bwrap is None:
            reason = "bwrap (bubblewrap) ist nicht installiert oder abgeschaltet"
        else:
            true = shutil.which("true") or "/bin/true"
            try:
                r = subprocess.run(_base_args(bwrap) + ["--", true], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   timeout=10, env=_env("/tmp"))
                if r.returncode != 0:
                    err = r.stderr.decode("utf-8", "replace").strip().splitlines()
                    reason = "bwrap ist nicht nutzbar" + (f" ({err[-1]})" if err else "")
            except (OSError, subprocess.SubprocessError) as e:
                reason = f"bwrap ist nicht nutzbar ({e})"
        if reason:
            bwrap = None
            sys.stderr.write(f"FileFenster: {reason} – Vorschau-Programme laufen ohne "
                             "Sandbox.\n")
        _state = (bwrap, reason)
        return _state


def reset():
    """Prüfung vergessen (Tests, geänderte Umgebung)."""
    global _state
    with _lock:
        _state = None


@contextlib.contextmanager
def private_dir(base=None):
    """Frischer, nur für einen Aufruf beschriebbarer Ordner (danach gelöscht)."""
    d = tempfile.mkdtemp(prefix="filefenster-sandbox-", dir=base)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def command(argv, inputs=(), outdir=None):
    """(argv, env) für den Aufruf – mit bwrap, wenn nutzbar, sonst unverändert.
    None, wenn das Programm nicht gefunden wird."""
    exe = shutil.which(argv[0]) if argv else None
    if not exe:
        return None
    bwrap, _reason = status()
    if bwrap is None:
        return list(argv), None
    exe = os.path.realpath(exe)
    args = _base_args(bwrap)
    if not exe.startswith("/usr/"):
        args += ["--ro-bind", exe, exe]       # z. B. Thumbnailer in ~/.local
    for p in inputs:
        if p and os.path.isabs(p) and os.path.exists(p):
            args += ["--ro-bind", p, p]
    home = "/tmp"
    if outdir:
        args += ["--bind", outdir, outdir]
        home = outdir
    return args + ["--", exe] + list(argv[1:]), _env(home)


def run(argv, cancel=None, timeout=20, inputs=(), outdir=None):
    """Programm (abgeschottet) ausführen; bricht bei cancel (threading.Event)
    oder Zeitüberschreitung ab. → stdout (bytes) bei Exit 0, sonst None.

    inputs: Dateien, die das Programm lesen darf (absolute, aufgelöste Pfade –
    dieselben wie in argv). outdir: Ordner, in den es schreiben darf."""
    cmd = command(argv, inputs, outdir)
    if cmd is None:
        return None
    args, env = cmd
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except OSError:
        return None
    over = threading.Event()
    chunks = []

    def reader():       # begrenzt lesen: ein entgleistes Programm füllt sonst den Speicher
        total = 0
        try:
            while True:
                b = proc.stdout.read1(1 << 16)
                if not b:
                    return
                total += len(b)
                if total > MAX_OUTPUT:
                    over.set()
                    return
                chunks.append(b)
        except (OSError, ValueError):
            return
    t = threading.Thread(target=reader, name="filefenster-sandbox-out", daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while True:
        t.join(0.1)
        if (over.is_set() or (cancel is not None and cancel.is_set())
                or time.monotonic() > deadline):
            proc.kill()     # bwrap: --die-with-parent nimmt die Sandbox mit
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            t.join(1)
            return None
        if not t.is_alive():
            if proc.poll() is not None:
                return b"".join(chunks) if proc.returncode == 0 else None
            time.sleep(0.05)
