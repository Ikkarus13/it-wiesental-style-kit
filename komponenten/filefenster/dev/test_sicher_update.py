#!/usr/bin/env python3
"""Sichere Updates und abgeschottete Vorschau-Programme testen:

  * Signatur: gültige Signatur → installiert; falsche/fehlende Signatur oder
    ausgetauschtes Paket → abgelehnt, sobald ein Schlüssel eingetragen ist;
    Platzhalter → nur Prüfsumme, aber gemeldet (Protokoll + Dialoghinweis)
  * GitHub zuerst ohne Anmeldung; Zugangsdaten nur bei privatem Repository,
    nie an andere Rechner (Umleitung zum Speicherdienst)
  * tools/make_signing_key.sh und tools/make_release.sh (nur --build-only)
  * Sandbox (bwrap): ein falscher „Thumbnailer“ kann ausserhalb seines
    Ausgabeordners nichts schreiben, das Home nicht lesen und nicht ins Netz;
    Rückfall ohne bwrap; Abbrechen beendet die Sandbox

Alles läuft mit Test-HOME, lokalem Fake-GitHub (http.server) und Schlüsseln in
einem Temp-Ordner – keine echten Netzzugriffe, kein echtes gh, nichts in ~/.config:

    Xvfb :102 -screen 0 1600x1000x24 -nolisten tcp &
    DISPLAY=:102 python3 dev/test_sicher_update.py [ARBEITSORDNER]
"""
import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:102)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = sys.argv[1] if len(sys.argv) > 1 else (
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
os.makedirs(BASE, exist_ok=True)
TMP = tempfile.mkdtemp(prefix="ff-sicher-", dir=BASE)
HOME = os.path.join(TMP, "home")
BIN = os.path.join(TMP, "bin")
for d in (HOME, BIN):
    os.makedirs(d)

# Test-Umgebung: eigenes HOME, keine Zugangsdaten, kein Proxy, falsches gh zuerst
for k in ("GITHUB_TOKEN", "GH_TOKEN", "FILEFENSTER_GITHUB_TOKEN", "XDG_CACHE_HOME",
          "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
          "FILEFENSTER_BWRAP", "FILEFENSTER_SIGNING_KEY"):
    os.environ.pop(k, None)
os.environ.update(HOME=HOME, XDG_DATA_HOME=os.path.join(HOME, ".local", "share"),
                  XDG_CONFIG_HOME=os.path.join(HOME, ".config"),
                  FILEFENSTER_CONFIG_DIR=os.path.join(TMP, "cfg"),
                  NO_PROXY="*", no_proxy="*", FILEFENSTER_NO_UNIQUE="1",
                  PATH=BIN + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
                  FAKE_GH_LOG=os.path.join(TMP, "gh-aufrufe.txt"), FAKE_GH_MODE="fail")
TOKEN = "testtoken-gh-123"
with open(os.path.join(BIN, "gh"), "w") as fh:
    fh.write('#!/bin/sh\necho "$*" >> "$FAKE_GH_LOG"\n'
             'if [ "$FAKE_GH_MODE" = token ] && [ "$1 $2" = "auth token" ]; then\n'
             f'  echo {TOKEN}; exit 0\nfi\nexit 1\n')
os.chmod(os.path.join(BIN, "gh"), 0o755)
assert shutil.which("gh") == os.path.join(BIN, "gh")     # nie das echte gh

sys.path.insert(0, ROOT)
from filefenster import sandbox, updater  # noqa: E402

ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok    {name}")
    else:
        fail += 1
        print(f"  FEHLER {name} {info}")


def openssl(*args, data=None):
    return subprocess.run(["openssl", *args], input=data, capture_output=True, check=True).stdout


def sign(key, path, sig):
    openssl("pkeyutl", "-sign", "-inkey", key, "-rawin", "-in", path, "-out", sig)
    with open(sig, "rb") as fh:
        return fh.read()


def gh_calls():
    try:
        with open(os.environ["FAKE_GH_LOG"]) as fh:
            return fh.read().split("\n")[:-1]
    except OSError:
        return []


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


# ---------------------------------------------------------------------- Schlüssel + Paket
KEYS = os.path.join(TMP, "keys")
os.makedirs(KEYS, mode=0o700)
GOOD, BAD = os.path.join(KEYS, "gut.pem"), os.path.join(KEYS, "fremd.pem")
for k in (GOOD, BAD):
    openssl("genpkey", "-algorithm", "ed25519", "-out", k)
GOOD_PUB = openssl("pkey", "-in", GOOD, "-pubout").decode()

VERSION = "9.9.9"
NAME = f"filefenster-{VERSION}.tar.gz"


def make_pkg(version, extra=b""):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data, mode=0o644):
            ti = tarfile.TarInfo(f"filefenster-{version}/{name}")
            ti.size, ti.mode, ti.mtime = len(data), mode, int(time.time())
            tar.addfile(ti, io.BytesIO(data))
        add("filefenster.py", b"print('FileFenster')\n" + extra)
        add("filefenster/__init__.py", f'APP_NAME = "FileFenster"\nVERSION = "{version}"\n'.encode())
        add("installieren.sh", b"#!/bin/sh\nexit 0\n", 0o755)
        add("assets/leer.txt", b"")
    return buf.getvalue()


PKG = make_pkg(VERSION)
EVIL = make_pkg(VERSION, b"import os  # fremder Code\n")
pkg_path, evil_path = os.path.join(TMP, NAME), os.path.join(TMP, "evil.tar.gz")
with open(pkg_path, "wb") as fh:
    fh.write(PKG)
with open(evil_path, "wb") as fh:
    fh.write(EVIL)
SIG_GOOD = sign(GOOD, pkg_path, os.path.join(TMP, "gut.sig"))
SIG_BAD = sign(BAD, pkg_path, os.path.join(TMP, "fremd.sig"))
SIG_EVIL_BAD = sign(BAD, evil_path, os.path.join(TMP, "evil-fremd.sig"))


def sha(data):
    return f"{hashlib.sha256(data).hexdigest()}  {NAME}\n".encode()


# ---------------------------------------------------------------------- Fake-GitHub
class St:
    private = False
    unauth_code = 404
    has_release = True
    assets = {}         # Name → Bytes (in der Release-Liste)
    log = []            # (dienst, pfad, Authorization)


API = "/repos/test/filefenster"


class Api(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        auth = self.headers.get("Authorization")
        St.log.append(("api", self.path, auth))
        if St.private and auth != f"Bearer {TOKEN}":
            return self._send(St.unauth_code, b'{"message": "Not Found"}')
        if self.path == API:
            return self._send(200, json.dumps({"full_name": "test/filefenster"}).encode())
        if self.path == API + "/releases/latest":
            if not St.has_release:
                return self._send(404, b'{"message": "Not Found"}')
            port = self.server.server_address[1]
            assets = []
            for n, data in St.assets.items():
                kind = "redirect" if n.endswith(".tar.gz") else "assets"
                assets.append({"name": n, "size": len(data),
                               "url": f"http://127.0.0.1:{port}{API}/{kind}/{n}"})
            return self._send(200, json.dumps({
                "tag_name": f"v{VERSION}", "name": f"FileFenster {VERSION}", "body": "- Test",
                "html_url": "http://127.0.0.1/release", "assets": assets}).encode())
        n = self.path.rsplit("/", 1)[-1]
        if self.path.startswith(API + "/assets/") and n in St.assets:
            return self._send(200, St.assets[n], "application/octet-stream")
        if self.path.startswith(API + "/redirect/") and n in St.assets:
            return self._send(302, headers=[("Location",
                                             f"http://localhost:{STORE_PORT}/files/{n}")])
        return self._send(404, b'{"message": "Not Found"}')


class Store(Api):
    def do_GET(self):
        St.log.append(("store", self.path, self.headers.get("Authorization")))
        n = self.path.rsplit("/", 1)[-1]
        if n in St.assets:
            return self._send(200, St.assets[n], "application/octet-stream")
        return self._send(404)


def serve(handler, host):
    srv = http.server.ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


api_srv = serve(Api, "127.0.0.1")
store_srv = serve(Store, "127.0.0.1")
STORE_PORT = store_srv.server_address[1]
os.environ["FILEFENSTER_UPDATE_API"] = f"http://127.0.0.1:{api_srv.server_address[1]}{API}"

PINNED = os.path.join(TMP, "release_key.pem")
PLACEHOLDER = "# Öffentlicher Schlüssel\n# %s – noch kein Schlüssel eingetragen\n" % updater.KEY_PLACEHOLDER


def pin(text):
    with open(PINNED, "w") as fh:
        fh.write(text)
    updater.RELEASE_KEY_FILE = PINNED


def publish(pkg=PKG, sig=None, checksum=True):
    St.assets = {NAME: pkg}
    if checksum:
        St.assets[NAME + ".sha256"] = sha(pkg) if checksum is True else checksum
    if sig is not None:
        St.assets[NAME + ".sig"] = sig


def fresh_install():
    d = updater.install_dir()
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "filefenster"))
    with open(os.path.join(d, "filefenster", "__init__.py"), "w") as fh:
        fh.write('VERSION = "0.0.1"\n')
    with open(os.path.join(d, "filefenster.py"), "w") as fh:
        fh.write("")


def leftovers():
    try:
        return [n for n in os.listdir(updater.download_dir()) if not n.startswith(".")
                or n.startswith(".sig-")]
    except OSError:
        return []


def try_update():
    """check + download + stage + activate wie im Dialog. → (rel, fehlertext)"""
    rel, newer = updater.check()
    if not newer:
        return rel, "nicht neuer"
    try:
        archive = updater.download(rel)
        staged = updater.stage(archive, rel.version)
        os.unlink(archive)
        updater.activate(staged)
        return rel, None
    except updater.UpdateError as e:
        return rel, str(e)


def log_text():
    try:
        return read(updater.log_file(), "r")
    except OSError:
        return ""


print("Signaturen")
fresh_install()
pin(PLACEHOLDER)
check("Platzhalter wird als „kein Schlüssel“ erkannt", updater.release_key() is None)
check("Dialoghinweis beim Platzhalter", "Prüfsumme" in updater.trust_note()
      and "Signaturschlüssel" in updater.trust_note(), updater.trust_note())

pin(GOOD_PUB)
check("echter Schlüssel wird erkannt", updater.release_key() is not None)
check("kein Dialoghinweis mit echtem Schlüssel", updater.trust_note() == "")

publish(sig=SIG_BAD)
rel, err = try_update()
check("falsche Signatur (fremder Schlüssel) → abgelehnt", err and "Signatur" in err, err)
check("… nichts installiert", updater.read_version(updater.install_dir()) == "0.0.1")
check("… keine Reste im Download-Ordner", leftovers() == [], leftovers())

publish(sig=None)
rel, err = try_update()
check("fehlende Signatur → abgelehnt", err and "nicht signiert" in err, err)

publish(pkg=EVIL, sig=SIG_GOOD)       # Konto übernommen: Paket + .sha256 getauscht
rel, err = try_update()
check("ausgetauschtes Paket mit passender Prüfsumme, alte Signatur → abgelehnt",
      err and "Signatur" in err, err)
publish(pkg=EVIL, sig=SIG_EVIL_BAD)
rel, err = try_update()
check("ausgetauschtes Paket mit eigener (fremder) Signatur → abgelehnt",
      err and "Signatur" in err, err)
publish(sig=SIG_GOOD[:-1] + bytes([SIG_GOOD[-1] ^ 1]))
rel, err = try_update()
check("verfälschte Signatur → abgelehnt", err and "Signatur" in err, err)
publish(sig=SIG_GOOD, checksum=sha(EVIL))
rel, err = try_update()
check("Prüfsumme wird weiter kontrolliert", err and "Prüfsumme" in err, err)
check("… immer noch alte Version", updater.read_version(updater.install_dir()) == "0.0.1")

pin("-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAkaputt\n-----END PUBLIC KEY-----\n")
publish(sig=SIG_GOOD)
rel, err = try_update()
check("unbrauchbarer Schlüssel → abgelehnt (kein Rückfall)", err and "Schlüssel" in err, err)
pin("irgendwas ohne Schlüssel\n")
check("Datei ohne Schlüssel und ohne Platzhalter-Marke → Fehler",
      "ungültig" in updater.trust_note(), updater.trust_note())
os.unlink(PINNED)
rel, err = try_update()
check("fehlende Schlüsseldatei → abgelehnt", err and "fehlt" in err, err)

pin(GOOD_PUB)
path_saved = os.environ["PATH"]
os.environ["PATH"] = BIN            # ohne openssl
rel, err = try_update()
os.environ["PATH"] = path_saved
check("ohne openssl → abgelehnt", err and "openssl" in err, err)

publish(sig=SIG_GOOD)
rel, err = try_update()
check("gültige Signatur → installiert", err is None and rel.trust == "signatur", err)
check("… Version 9.9.9 eingesetzt", updater.read_version(updater.install_dir()) == VERSION)
check("… Protokoll: Signatur geprüft", "Signatur geprüft" in log_text())

fresh_install()
pin(PLACEHOLDER)
publish(sig=None)
rel, err = try_update()
check("Platzhalter: ohne Signatur installiert (nur Prüfsumme)",
      err is None and rel.trust == "pruefsumme", err)
check("… im Protokoll gemeldet", "WARNUNG: Signatur" in log_text() and "Platzhalter" in log_text())
fresh_install()
publish(sig=SIG_BAD)
rel, err = try_update()
check("Platzhalter: Prüfsumme gilt weiter", err is None and rel.trust == "pruefsumme", err)

# ---------------------------------------------------------------------- Zugangsdaten
print("Zugangsdaten")
pin(GOOD_PUB)


def auth_seen(service=None):
    return [p for s, p, a in St.log if a and (service is None or s == service)]


fresh_install()
publish(sig=SIG_GOOD)
St.private, St.log = False, []
os.environ["FILEFENSTER_GITHUB_TOKEN"] = TOKEN
os.environ["FAKE_GH_MODE"] = "token"
rel, err = try_update()
check("öffentlich: Update klappt", err is None, err)
check("öffentlich: nie Zugangsdaten gesendet (trotz Token in der Umgebung)",
      auth_seen() == [], auth_seen())
check("öffentlich: gh nicht aufgerufen", gh_calls() == [], gh_calls())
check("öffentlich: Paket über die Umleitung geladen",
      any(s == "store" for s, _p, _a in St.log))

del os.environ["FILEFENSTER_GITHUB_TOKEN"]
St.has_release, St.log = False, []
check("öffentlich ohne Release → None, ohne gh", updater.latest() is None and gh_calls() == [])
St.has_release = True

fresh_install()
St.private, St.log = True, []
rel, err = try_update()
first = St.log[0] if St.log else ("", "", "x")
check("privat: erste Anfrage ohne Anmeldung", first[1].endswith("/releases/latest")
      and first[2] is None, St.log[:2])
check("privat: dann Token aus gh", gh_calls() and set(gh_calls()) == {"auth token"},
      gh_calls())
check("privat: Update mit Token klappt", err is None and rel.needs_auth, err)
check("privat: Token nur an die API", auth_seen("api") != [] and auth_seen("store") == [],
      auth_seen("store"))

St.unauth_code, St.log = 401, []
os.environ["FILEFENSTER_GITHUB_TOKEN"] = TOKEN
os.environ["FAKE_GH_MODE"] = "fail"
open(os.environ["FAKE_GH_LOG"], "w").close()
rel = updater.latest()
check("privat (401): Token aus der Umgebung, kein gh",
      rel is not None and rel.needs_auth and gh_calls() == [], gh_calls())
del os.environ["FILEFENSTER_GITHUB_TOKEN"]
St.unauth_code = 404
try:
    updater.latest()
    err = None
except updater.UpdateError as e:
    err = str(e)
check("privat ohne Zugangsdaten → verständlicher Fehler", err and "gh auth login" in err, err)
St.private = False

# ---------------------------------------------------------------------- Release-Skripte
print("Release-Skripte")
REL_KEY = os.path.join(TMP, "release", "signing-key.pem")
env = dict(os.environ, FILEFENSTER_SIGNING_KEY=REL_KEY)
r = subprocess.run(["bash", os.path.join(ROOT, "tools", "make_signing_key.sh")], env=env,
                   capture_output=True, text=True)
check("make_signing_key.sh erzeugt Schlüssel", r.returncode == 0 and os.path.isfile(REL_KEY),
      r.stderr)
check("… privater Schlüssel 0600", os.path.isfile(REL_KEY)
      and (os.stat(REL_KEY).st_mode & 0o777) == 0o600)
check("… zeigt den öffentlichen Schlüssel", "BEGIN PUBLIC KEY" in r.stdout
      and "PRIVATE" not in r.stdout)
r2 = subprocess.run(["bash", os.path.join(ROOT, "tools", "make_signing_key.sh")], env=env,
                    capture_output=True, text=True)
check("… überschreibt keinen vorhandenen Schlüssel", r2.returncode != 0)
r3 = subprocess.run(["bash", os.path.join(ROOT, "tools", "make_signing_key.sh")],
                    env=dict(env, FILEFENSTER_SIGNING_KEY=os.path.join(ROOT, "dev", "x.pem")),
                    capture_output=True, text=True)
check("… verweigert Schlüssel im Repository", r3.returncode != 0
      and not os.path.exists(os.path.join(ROOT, "dev", "x.pem")))
dist = os.path.join(TMP, "dist")
r = subprocess.run(["bash", os.path.join(ROOT, "tools", "make_release.sh"), "--build-only",
                    "--dist", dist], env=env, capture_output=True, text=True)
sigs = [n for n in os.listdir(dist) if n.endswith(".sig")] if os.path.isdir(dist) else []
head_key = subprocess.run(["git", "-C", ROOT, "show", "HEAD:filefenster/release_key.pem"],
                          capture_output=True, text=True).stdout
if head_key and updater.KEY_PLACEHOLDER not in head_key:
    # im Repo ist der echte Schlüssel eingetragen: ein fremder darf nicht signieren
    check("make_release.sh lehnt einen fremden Signaturschlüssel ab",
          r.returncode != 0 and "passt nicht" in r.stderr and not sigs, r.stderr[-300:])
else:
    check("make_release.sh --build-only signiert das Paket", r.returncode == 0 and len(sigs) == 1,
          r.stderr[-300:])
if sigs:
    tgz = os.path.join(dist, sigs[0][:-4])
    pub = openssl("pkey", "-in", REL_KEY, "-pubout").decode()
    check("… Signatur passt zum öffentlichen Schlüssel (Updater-Prüfung)",
          updater.verify_signature(tgz, read(os.path.join(dist, sigs[0])), pub))
    pin(pub)
    check("… und ein so eingetragener Schlüssel wird erkannt", updater.release_key() is not None)

# ---------------------------------------------------------------------- Sandbox
print("Sandbox")
SB = os.path.join(TMP, "sb")
for d in ("in", "outside", "tools"):
    os.makedirs(os.path.join(SB, d))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
                       "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
inp = os.path.join(SB, "in", "bild.png")
with open(inp, "wb") as fh:
    fh.write(PNG)
secret = os.path.join(HOME, "geheim.txt")
with open(secret, "w") as fh:
    fh.write("geheim")
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(5)
PORT = listener.getsockname()[1]
ESCAPES = [os.path.join(SB, "outside", "raus.txt"), os.path.join(SB, "in", "daneben.txt"),
           os.path.join(HOME, "raus.txt")]
fake = os.path.join(SB, "tools", "falscher-thumbnailer")
with open(fake, "w") as fh:
    fh.write(f"""#!/usr/bin/python3
import json, os, socket, sys
inp, out = sys.argv[1], sys.argv[2]
r = {{}}
def t(name, fn):
    try:
        fn(); r[name] = True
    except Exception:
        r[name] = False
t("lesen", lambda: open(inp, "rb").read())
t("ausgabe", lambda: open(out, "wb").write(open(inp, "rb").read()))
for i, p in enumerate({ESCAPES!r}):
    t("raus%d" % i, lambda p=p: open(p, "w").write("x"))
t("eingabe_aendern", lambda: open(inp, "ab").write(b"x"))
t("geheim", lambda: open({secret!r}).read())
t("netz", lambda: socket.create_connection(("127.0.0.1", {PORT}), timeout=3).close())
print(json.dumps(r))
""")
os.chmod(fake, 0o755)


def escaped():
    return [p for p in ESCAPES if os.path.exists(p)]


sandbox.reset()
bw, reason = sandbox.status()
check("bwrap ist nutzbar", bw is not None, reason)
with sandbox.private_dir(TMP) as outdir:
    out = os.path.join(outdir, "aus.png")
    res = sandbox.run([fake, inp, out], timeout=20, inputs=(inp,), outdir=outdir)
    r = json.loads(res) if res else {}
    check("Sandbox: Eingabe lesbar, Ausgabe geschrieben",
          r.get("lesen") and r.get("ausgabe") and read(out) == PNG, r)
check("Sandbox: nichts ausserhalb des Ausgabeordners geschrieben", escaped() == [], escaped())
check("Sandbox: Eingabedatei nur lesbar", read(inp) == PNG and r.get("eingabe_aendern") is False)
check("Sandbox: Home nicht lesbar", r.get("geheim") is False)
check("Sandbox: kein Netzwerk", r.get("netz") is False)
check("Sandbox: privater Ordner danach gelöscht", not os.path.exists(outdir))

# derselbe Aufruf über die Vorschau (Thumbnailer aus ~/.local/share/thumbnailers)
import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from filefenster import previewpane  # noqa: E402
tdir = os.path.join(HOME, ".local", "share", "thumbnailers")
os.makedirs(tdir)
with open(os.path.join(tdir, "test.thumbnailer"), "w") as fh:
    fh.write(f"[Thumbnailer Entry]\nExec={fake} %i %o\n"
             "MimeType=application/x-filefenster-test;\n")
previewpane._THUMBNAILERS = None
pb = previewpane._run_thumbnailer(inp, "application/x-filefenster-test", 128, threading.Event())
check("Vorschau: Thumbnailer liefert Bild aus der Sandbox", pb is not None)
check("Vorschau: Thumbnailer kommt nicht aus der Sandbox", escaped() == [] and read(inp) == PNG,
      escaped())

# Abbrechen beendet die Sandbox samt Programm
ev = threading.Event()
threading.Timer(0.5, ev.set).start()
t0 = time.monotonic()
res = sandbox.run(["sleep", "31.337"], cancel=ev, timeout=20)
dt = time.monotonic() - t0
time.sleep(0.5)
left = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "^sleep 31.337$"],
                      capture_output=True, text=True).stdout.split()
check("Abbrechen beendet Sandbox und Programm", res is None and dt < 5 and left == [],
      f"{dt:.1f}s {left}")

# Gegenprobe ohne Sandbox: die Proben sind echt (sonst wäre der Test wertlos)
inp2 = os.path.join(SB, "in", "kopie.png")
with open(inp2, "wb") as fh:
    fh.write(PNG)
err_buf, sys.stderr = sys.stderr, io.StringIO()
try:
    os.environ["FILEFENSTER_BWRAP"] = "0"
    sandbox.reset()
    with sandbox.private_dir(TMP) as outdir:
        res = sandbox.run([fake, inp2, os.path.join(outdir, "aus.png")], inputs=(inp2,),
                          outdir=outdir)
    sandbox.run(["true"])
    os.environ["FILEFENSTER_BWRAP"] = os.path.join(TMP, "gibt-es-nicht", "bwrap")
    sandbox.reset()
    res2 = sandbox.run(["sh", "-c", "echo ohne"])
    reason2 = sandbox.status()[1]
finally:
    captured, sys.stderr = sys.stderr.getvalue(), err_buf
r = json.loads(res) if res else {}
check("ohne bwrap: Programm läuft weiter (Rückfall)", r.get("ausgabe") and res2 == b"ohne\n")
check("ohne bwrap: Gegenprobe schreibt wirklich ausserhalb und ins Netz",
      len(escaped()) == len(ESCAPES) and r.get("netz") is True, r)
check("ohne bwrap: einmal gemeldet", captured.count("ohne Sandbox") == 2
      and "nicht installiert" in reason2, captured)
for p in escaped():
    os.unlink(p)
os.environ.pop("FILEFENSTER_BWRAP", None)
sandbox.reset()

listener.close()
api_srv.shutdown()
store_srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
sys.exit(1 if fail else 0)
