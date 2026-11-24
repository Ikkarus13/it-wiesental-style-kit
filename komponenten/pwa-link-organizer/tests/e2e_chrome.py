#!/usr/bin/env python3
"""End-to-End-Test: echte Chrome-Instanz auf Xvfb :99 mit der Erweiterung.

- eigenes Chrome-Profil, eigenes HOME/XDG, kein D-Bus → das laufende Chrome,
  Edge und der Desktop bleiben unberührt;
- «Outlook» ist eine lokale Testseite: outlook.cloud.microsoft, login.microsoft-
  online.com und www.example.org zeigen per --host-resolver-rules auf einen
  HTTPS-Server hier (selbst signiert, per --ignore-certificate-errors-spki-list zugelassen);
- statt Edge schreibt ein Schein-Programm die URLs in eine Datei
  (EDGE_LINKS_BROWSER, Chrome reicht die Umgebung an den Host durch).

    python3 tests/e2e_chrome.py [--mode app|pwa] [--keep] [--shot DATEI]

Erwartet ein laufendes Xvfb :99 (Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &).
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import select
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(ROOT, "extension")
HOST_SCRIPT = os.path.join(ROOT, "native-host", "edge_links_host.py")
CHROME = "/opt/google/chrome/chrome"
HOSTS = ["outlook.cloud.microsoft", "login.microsoftonline.com", "www.example.org"]
OUTLOOK = "https://outlook.cloud.microsoft/mail/"

MAIL_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Outlook (Test)</title>
<link rel=manifest href=/manifest.webmanifest>
<style>body{font:16px sans-serif} a,button{display:block;margin:6px;font-size:16px}</style></head><body>
<h3>Outlook-Testseite</h3>
<a id=t1 href="https://www.example.org/t1" target="_blank" rel="noopener noreferrer">t1 Link _blank</a>
<button id=t2 onclick="window.open('https://www.example.org/t2','_blank','noopener')">t2 window.open noopener</button>
<button id=t3 onclick="var w=window.open('about:blank');w.opener=null;w.location='https://www.example.org/t3'">t3 about:blank → Ziel</button>
<button id=t3b onclick="var w=window.open();setTimeout(function(){w.location.href='https://www.example.org/t3b'},400)">t3b about:blank, Ziel nach 400 ms</button>
<button id=t3c onclick="var w=window.open();setTimeout(function(){w.location.href='https://www.example.org/t3c'},12000)">t3c about:blank, Ziel erst nach 12 s</button>
<button id=t4 onclick="window.open('https://login.microsoftonline.com/t4','login','width=500,height=600')">t4 Anmelde-Pop-up</button>
<button id=t5 onclick="window.open('https://outlook.cloud.microsoft/mail/popout/t5','_blank')">t5 Outlook-Pop-out</button>
<a id=t6 href="https://login.microsoftonline.com/t6" target="_blank">t6 Link zur Anmeldung</a>
<button id=t7 onclick="var w=window.open('');w.document.write('<title>Druck</title><p>Druck t7</p>');w.document.close()">t7 Drucken (document.write)</button>
<a id=t9 href="https://www.example.org/t9" target="_blank">t9 Mittelklick</a>
<a id=t10 href="https://outlook.cloud.microsoft/redirect?to=https%3A%2F%2Fwww.example.org%2Ft10" target="_blank">t10 Weiterleitung über Outlook</a>
<a id=t11 href="https://www.example.org/t11-edgefail" target="_blank">t11 Edge-Fehler → bleibt in Chrome</a>
<a id=t12 href="mailto:test@example.org">t12 mailto</a>
<a id=t13 href="https://www.example.org/t13" target="_blank" rel="noopener">t13 zweiter Link</a>
<a id=t14 href="https://www.example.org/t14" target="_blank">t14 mit gewähltem Profil</a>
<a id=t15 href="https://www.example.org/t15" target="_blank">t15 zuletzt benutztes Profil</a>
<a id=t16 href="https://outlook.cloud.microsoft/redirect?to=https%3A%2F%2Flogin.microsoftonline.com%2Fredirect%3Fto%3Dhttps%253A%252F%252Fwww.example.org%252Fadfs%252Fls%252F%253FSAMLRequest%253Dt16" target="_blank">t16 Outlook → Anmeldung → fremde Anmeldeseite</a>
<a id=t17 href="https://outlook.cloud.microsoft/keep/t17" target="_blank">t17 Outlook-Seite im Tab, danach Adresse eingetippt</a>
<button id=t18 onclick="var w=window.open('','mail18','width=640,height=480');w.document.write('<title>Mail t18</title><p>Mail</p><a id=l18 href=&quot;https://www.example.org/t18&quot; target=_blank>Link in der Mail</a>');w.document.close()">t18 Mail im eigenen Fenster (Doppelklick)</button>
</body></html>"""

EXTERN_PAGE = """<!doctype html><title>Fremde Seite</title>
<a id=t8 href="https://www.example.org/t8" target="_blank">t8 Link auf fremder Seite</a>"""

MANIFEST = {
    "name": "Outlook Test", "short_name": "OutlookTest", "id": "/mail/?edge-links-test",
    "start_url": "/mail/", "scope": "/mail/", "display": "standalone",
    "icons": [{"src": "/icon.png", "sizes": "512x512", "type": "image/png"}],
}


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def send(self, body, ctype="text/html; charset=utf-8", code=200, headers=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        path = urllib.parse.urlsplit(self.path)
        Handler.requests.append(f"{host}{self.path}")
        if host == "outlook.cloud.microsoft" and path.path == "/mail/":
            return self.send(MAIL_PAGE)
        if path.path == "/manifest.webmanifest":
            return self.send(json.dumps(MANIFEST), "application/manifest+json")
        if path.path == "/icon.png":  # Installation verlangt ein grosses Symbol
            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.open(os.path.join(EXT_DIR, "icons", "icon128.png")).resize((512, 512)).save(buf, "PNG")
            return self.send(buf.getvalue(), "image/png")
        if path.path == "/redirect":
            to = urllib.parse.parse_qs(path.query).get("to", ["/"])[0]
            return self.send("", code=302, headers=[("Location", to)])
        if host == "www.example.org" and path.path == "/page8":
            return self.send(EXTERN_PAGE)
        return self.send(f"<!doctype html><title>{host}{path.path}</title><p>{host}{path.path}</p>")


class CDP:
    """Chrome DevTools Protocol über --remote-debugging-pipe (fd 3/4, \\0-getrennt)."""

    def __init__(self, wfd, rfd):
        self.wfd, self.rfd = wfd, rfd
        self.buf = b""
        self.next_id = 0

    def call(self, method, params=None, session=None, timeout=20):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self.wfd, json.dumps(msg).encode() + b"\0")
        deadline = time.time() + timeout
        while True:
            m = self._recv(deadline)
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def _recv(self, deadline):
        while b"\0" not in self.buf:
            ready, _, _ = select.select([self.rfd], [], [], max(0, deadline - time.time()))
            if not ready:
                raise TimeoutError("CDP antwortet nicht")
            chunk = os.read(self.rfd, 1 << 20)
            if not chunk:
                raise EOFError("Chrome beendet")
            self.buf += chunk
        raw, self.buf = self.buf.split(b"\0", 1)
        return json.loads(raw)


def wait_for(fn, timeout=10, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


class Run:
    def __init__(self, args):
        self.args = args
        self.dir = tempfile.mkdtemp(prefix="edge-links-e2e-")
        self.calls = os.path.join(self.dir, "edge-calls.txt")
        self.failures = []

    # ---------- Aufbau ----------
    def setup(self):
        d = self.dir
        cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
        san = ",".join(f"DNS:{h}" for h in HOSTS)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", cert, "-days", "2", "-subj", "/CN=edge-links-test",
                        "-addext", f"subjectAltName={san}"], check=True, capture_output=True)
        # SPKI-Hash: Chrome behandelt genau dieses Zertifikat als gültig (sicherer Kontext, PWA)
        pub = subprocess.run(["openssl", "x509", "-in", cert, "-pubkey", "-noout"], check=True,
                             capture_output=True).stdout
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=pub, check=True,
                             capture_output=True).stdout
        self.spki = base64.b64encode(hashlib.sha256(der).digest()).decode()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.httpd = httpd  # erst jetzt: shutdown() wartet sonst ewig auf serve_forever

        fake = os.path.join(d, "fake-edge")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$*\" >> '" + self.calls + "'\n"
                    "case \"$*\" in *edgefail*) exit 3;; esac\n")
        os.chmod(fake, 0o755)
        # Chrome gibt mailto: & Co. an xdg-open – im Test nur protokollieren, sonst
        # startet das System-Mailprogramm (Thunderbird) unsichtbar auf :99.
        self.bin = os.path.join(d, "bin")
        self.xdg_calls = os.path.join(d, "xdg-calls.txt")
        os.makedirs(self.bin)
        for tool in ("xdg-open", "xdg-settings", "xdg-mime", "gio", "gvfs-open", "kde-open", "exo-open"):
            path = os.path.join(self.bin, tool)
            with open(path, "w") as f:
                f.write(f"#!/bin/sh\nprintf '{tool} %s\\n' \"$*\" >> '{self.xdg_calls}'\nexit 0\n")
            os.chmod(path, 0o755)

        self.profile = os.path.join(d, "chrome-profile")
        nmh = os.path.join(self.profile, "NativeMessagingHosts")
        os.makedirs(nmh)
        ext_id = extension_id()
        with open(os.path.join(nmh, "ch.sascha.edge_links.json"), "w") as f:
            json.dump({"name": "ch.sascha.edge_links", "description": "Edge-Links (Test)",
                       "path": HOST_SCRIPT, "type": "stdio",
                       "allowed_origins": [f"chrome-extension://{ext_id}/"]}, f)
        self.ext_id = ext_id
        self.sw = None

        home = os.path.join(d, "home")
        os.makedirs(os.path.join(home, ".config", "microsoft-edge"))
        with open(os.path.join(home, ".config", "microsoft-edge", "Local State"), "w") as f:
            json.dump({"profile": {"last_used": "Profile 1", "profiles_order": ["Default", "Profile 1"],
                                   "info_cache": {"Default": {"name": "Arbeit", "user_name": "a@example.com"},
                                                  "Profile 1": {"name": "Persönlich", "user_name": ""}}}}, f)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "DBUS_", "CHROME_"))}
        # Chrome legt seinen Singleton-Socket in TMPDIR an (Pfad max. ~100 Zeichen)
        self.short_tmp = tempfile.mkdtemp(prefix="elt-", dir="/tmp")
        env.update(DISPLAY=":99", HOME=home, TMPDIR=self.short_tmp,
                   XDG_CONFIG_HOME=os.path.join(home, ".config"),
                   XDG_CACHE_HOME=os.path.join(home, ".cache"), XDG_DATA_HOME=os.path.join(home, ".local/share"),
                   DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/edge-links-test",
                   EDGE_LINKS_BROWSER=fake, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        rules = ", ".join(f"MAP {h} 127.0.0.1:{self.port}" for h in HOSTS)
        flags = [f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                 "--password-store=basic", "--disable-sync", "--disable-search-engine-choice-screen",
                 "--remote-debugging-pipe", "--enable-unsafe-extension-debugging",
                 f"--ignore-certificate-errors-spki-list={self.spki}", f"--host-resolver-rules={rules}",
                 "--window-size=1200,900", "--lang=de"]
        flags.append(f"--app={OUTLOOK}" if self.args.mode == "app" else "about:blank")

        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()
        self.chrome = subprocess.Popen(
            ["/bin/sh", "-c", 'a=$1; b=$2; shift 2; exec 3<&"$a" 4>&"$b" "$@"', "sh",
             str(to_chrome_r), str(from_chrome_w), CHROME, *flags],
            pass_fds=(to_chrome_r, from_chrome_w), env=env,
            stdout=open(os.path.join(d, "chrome.log"), "w"), stderr=subprocess.STDOUT)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        self.cdp = CDP(to_chrome_w, from_chrome_r)

        got = self.cdp.call("Extensions.loadUnpacked", {"path": EXT_DIR})["id"]
        if got != ext_id:
            raise RuntimeError(f"Erweiterungs-ID {got} ≠ erwartet {ext_id}")
        print(f"Chrome {self.cdp.call('Browser.getVersion')['product']}, Erweiterung {got}")

        if self.args.mode == "pwa":
            # Eigene Manifest-ID → andere App-ID als die echte Outlook-PWA; dazu
            # eigenes XDG_DATA_HOME, damit keine Verknüpfung im Startmenü landet.
            mid = "https://outlook.cloud.microsoft/mail/?edge-links-test"
            try:
                self.cdp.call("PWA.install", {"manifestId": mid, "installUrlOrBundleUrl": OUTLOOK}, timeout=30)
            except RuntimeError:
                print("Server-Anfragen:", Handler.requests)
                raise
            self.cdp.call("PWA.changeAppUserSettings", {"manifestId": mid, "displayMode": "standalone"})
            self.cdp.call("PWA.launch", {"manifestId": mid})
            self.outlook = wait_for(lambda: self.find_in_window(OUTLOOK, "app"), 15)
            for t in self.cdp.call("Target.getTargets")["targetInfos"]:  # Installations-Tab weg
                if t["type"] == "page" and self.outlook and t["targetId"] != self.outlook["targetId"]:
                    self.cdp.call("Target.closeTarget", {"targetId": t["targetId"]})
        else:
            self.outlook = wait_for(lambda: self.find_target(OUTLOOK), 15)
        if not self.outlook:
            raise RuntimeError("Outlook-Testfenster nicht gefunden")
        self.osess = self.cdp.call("Target.attachToTarget",
                                   {"targetId": self.outlook["targetId"], "flatten": True})["sessionId"]
        wait_for(lambda: self.eval(self.osess, "document.readyState") == "complete", 10)

    def find_in_window(self, prefix, want_type):
        types = self.window_types()
        for t in self.cdp.call("Target.getTargets")["targetInfos"]:
            if t["type"] == "page" and t["url"].startswith(prefix):
                wid = self.cdp.call("Browser.getWindowForTarget", {"targetId": t["targetId"]})["windowId"]
                if types.get(wid) == want_type:
                    return t
        return None

    def find_target(self, prefix, kind="page"):
        for t in self.cdp.call("Target.getTargets")["targetInfos"]:
            if t["type"] == kind and t["url"].startswith(prefix):
                return t
        return None

    def eval(self, session, expr):
        r = self.cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                               "awaitPromise": True}, session=session)
        return r.get("result", {}).get("value")

    def window_types(self):
        """Fenster-ID → Typ (normal/popup/app), aus dem Service Worker der Erweiterung."""
        if not self.sw:
            t = wait_for(lambda: self.find_target(f"chrome-extension://{self.ext_id}/", "service_worker"), 10)
            self.sw = self.cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        ws = self.eval(self.sw, "chrome.windows.getAll({windowTypes:['normal','popup','app']})"
                                ".then(ws=>ws.map(w=>[w.id,w.type]))")
        return dict(ws)

    def windows(self):
        """Alle Seiten samt Fenstertyp. URLs kommen aus CDP – die Erweiterung sieht
        ohne tabs-Recht nur Outlook-URLs."""
        types = self.window_types()
        out = {}
        for t in self.cdp.call("Target.getTargets")["targetInfos"]:
            if t["type"] != "page":
                continue
            try:
                wid = self.cdp.call("Browser.getWindowForTarget", {"targetId": t["targetId"]})["windowId"]
            except RuntimeError:
                continue
            out.setdefault(wid, {"type": types.get(wid, "?"), "tabs": []})["tabs"].append(t["url"])
        return list(out.values())

    # ---------- Aktionen ----------
    def click(self, elem_id, button="left", session=None, selector=None):
        session = session or self.osess
        selector = selector or f"#{elem_id}"
        box = self.eval(session, f"(()=>{{const r=document.querySelector({json.dumps(selector)})"
                                 f".getBoundingClientRect();return [r.x+r.width/2, r.y+r.height/2]}})()")
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": typ, "x": box[0], "y": box[1]}
            if typ != "mouseMoved":
                p.update(button=button, clickCount=1, buttons=1 if button == "left" else 4)
            self.cdp.call("Input.dispatchMouseEvent", p, session=session)

    def edge_calls(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def tabs_with(self, needle):
        return [(w["type"], u) for w in self.windows() for u in w["tabs"] if needle in (u or "")]

    def cleanup_tabs(self):
        """Alles ausser dem Outlook-Fenster und dem Service Worker schliessen."""
        for t in self.cdp.call("Target.getTargets")["targetInfos"]:
            if t["type"] == "page" and t["targetId"] != self.outlook["targetId"]:
                try:
                    self.cdp.call("Target.closeTarget", {"targetId": t["targetId"]})
                except RuntimeError:
                    pass
        time.sleep(0.5)

    def check(self, name, ok, info=""):
        print(f"  {'OK ' if ok else 'FEHLER'} {name} {info}")
        if not ok:
            self.failures.append(name)

    def expect_edge(self, tid, elem=None, button="left", url=None, line=None, wait=8, session=None):
        """line: erwarteter Edge-Aufruf (Argumente), sonst nur die URL am Ende."""
        url = url or f"https://www.example.org/{tid}"
        before = len(self.edge_calls())
        t0 = time.time()
        self.click(elem or tid, button, session=session)
        t1 = time.time()
        got = wait_for(lambda: [c for c in self.edge_calls()[before:] if c.split(" ")[-1] == url], wait, 0.05)
        dt = time.time() - t1
        time.sleep(1.0)
        left = self.tabs_with(f"/{tid}")
        self.check(f"{tid} → Edge", bool(got) and not left and (line is None or got == [line]),
                   f"(Klick {(t1 - t0) * 1000:.0f} ms + {dt * 1000:.0f} ms bis Edge; "
                   f"Edge-Aufrufe {self.edge_calls()[before:]}, Rest-Tabs {left})")
        self.cleanup_tabs()

    def expect_chrome(self, tid, needle, want_type=None, elem=None, session=None, wait=2.5):
        before = len(self.edge_calls())
        self.click(elem or tid, session=session)
        time.sleep(wait)
        tabs = self.tabs_with(needle)
        ok = not self.edge_calls()[before:] and tabs and (want_type is None or any(t == want_type for t, _ in tabs))
        self.check(f"{tid} bleibt in Chrome", bool(ok), f"(Tabs {tabs}, Edge-Aufrufe {self.edge_calls()[before:]})")
        if not self.args.keep:
            self.cleanup_tabs()

    def stored_log(self):
        return self.eval(self.sw, "chrome.storage.local.get('log').then(r => r.log || [])") or []

    def log_tests(self):
        """Entscheidungsprotokoll: ein Eintrag je Entscheidung, nur Hostnamen."""
        time.sleep(1.0)
        log = self.stored_log()

        def has(why, host=None):
            return any(e.get("why", "").startswith(why) and (host is None or e.get("host") == host) for e in log)

        for name, ok in [
            ("an Edge übergeben", has("an Edge übergeben", "www.example.org")),
            ("Pop-up-Fenster (t4)", has("Fenster «popup»", "login.microsoftonline.com")),
            ("Microsoft-Anmeldung (t6)", has("Microsoft-Anmeldung", "login.microsoftonline.com")),
            ("fremde Anmeldeseite (t16)", has("fremde Anmeldeseite", "www.example.org")),
            ("Outlook-Seite abgelaufen (t17)", has("Zeit abgelaufen", "outlook.cloud.microsoft")),
            ("Edge-Fehler (t11)", has("Edge-Fehler", "www.example.org")),
            ("Quelle nicht Outlook (t8)", has("Quelle nicht Outlook (www.example.org)", "")),
            ("Mail-Fenster gemerkt (t18)", any("Links darin gehen an Edge" in e.get("why", "")
                                               and e.get("host") == "about:blank" for e in log)),
        ]:
            self.check(f"Protokoll: {name}", ok)
        clean = all("/" not in e.get("host", "") and set(e) <= {"t", "host", "why", "ms", "tries"} for e in log)
        self.check("Protokoll ohne Adressen, höchstens 50 Einträge", clean and 0 < len(log) <= 50,
                   f"({len(log)} Einträge)")

        opts = f"chrome-extension://{self.ext_id}/options.html"
        t = self.cdp.call("Target.createTarget", {"url": opts})
        s = self.cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        rows = wait_for(lambda: self.eval(s, "document.querySelectorAll('#log li:not(.empty)').length"), 10)
        first = self.eval(s, "(document.querySelector('#log li')||{}).innerText||''").replace("\n", " ")
        self.check("Einstellungen zeigen das Protokoll", rows == len(log), f"({rows} Zeilen, neueste: {first!r})")
        if self.args.shot:
            shot = self.cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True},
                                 session=s)["data"]
            with open(os.path.splitext(self.args.shot)[0] + "-protokoll.png", "wb") as f:
                f.write(base64.b64decode(shot))
        self.click("clear", session=s)
        emptied = wait_for(lambda: not self.stored_log()
                           and self.eval(s, "!!document.querySelector('#log li.empty')"), 5)
        self.check("«Protokoll leeren» leert das Protokoll", bool(emptied))
        self.cdp.call("Target.closeTarget", {"targetId": t["targetId"]})

    def profile_tests(self):
        """Einstellungsseite: Profile aus Edges «Local State», Wahl geht mit jedem Link mit."""
        opts = f"chrome-extension://{self.ext_id}/options.html"
        t = self.cdp.call("Target.createTarget", {"url": opts})
        s = self.cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        n = wait_for(lambda: self.eval(s, "document.querySelectorAll('#list input').length") == 3, 10)
        texts = self.eval(s, "[...document.querySelectorAll('label.choice')].map(l=>l.innerText.replace(/\\s+/g,' '))")
        checked = self.eval(s, "(document.querySelector('#list input:checked')||{}).value")
        self.check("Einstellungen zeigen Edge-Profile", n and "Arbeit" in texts[0] and "Persönlich" in texts[1]
                   and "Zuletzt" in texts[2] and checked == "", f"({texts}, gewählt {checked!r})")
        if self.args.shot:
            shot = self.cdp.call("Page.captureScreenshot", {"format": "png"}, session=s)["data"]
            with open(os.path.splitext(self.args.shot)[0] + "-optionen.png", "wb") as f:
                f.write(base64.b64decode(shot))

        self.click(None, session=s, selector="#list input[value='Profile 1']")
        stored = wait_for(lambda: self.eval(self.sw, "chrome.storage.local.get('profile').then(r=>r.profile)")
                          == "Profile 1", 5)
        self.check("Profilwahl gespeichert", bool(stored))
        before = len(self.edge_calls())
        self.click("try", session=s)
        ok = wait_for(lambda: "--profile-directory=Profile 1 https://myaccount.microsoft.com/"
                      in self.edge_calls()[before:], 5)
        status = self.eval(s, "document.getElementById('status').textContent")
        self.check("Ausprobieren öffnet im gewählten Profil", bool(ok), f"({status})")
        self.expect_edge("t14", line="--profile-directory=Profile 1 https://www.example.org/t14")

        t = self.cdp.call("Target.createTarget", {"url": opts})
        s = self.cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        wait_for(lambda: self.eval(s, "document.querySelectorAll('#list input').length") == 3, 10)
        checked = self.eval(s, "(document.querySelector('#list input:checked')||{}).value")
        self.check("Einstellungen merken die Wahl", checked == "Profile 1", f"(gewählt {checked!r})")
        self.click(None, session=s, selector="#list input[value='']")
        wait_for(lambda: self.eval(self.sw, "chrome.storage.local.get('profile').then(r=>r.profile)") == "", 5)
        self.expect_edge("t15", line="https://www.example.org/t15")

    # ---------- Testfälle ----------
    def run(self):
        self.setup()
        print("Fenster am Anfang:", self.windows())
        self.expect_edge("t1")
        self.expect_edge("t2")
        self.expect_edge("t3")
        self.expect_edge("t3b")
        self.expect_chrome("t4", "login.microsoftonline.com/t4", "popup")
        self.expect_chrome("t5", "outlook.cloud.microsoft/mail/popout/t5")
        self.expect_chrome("t6", "login.microsoftonline.com/t6")
        self.expect_chrome("t7", "about:blank")
        self.expect_edge("t3c", wait=20)  # Ziel kommt erst nach 12 s
        self.expect_edge("t9", button="middle")
        self.expect_edge("t10")
        # Weiterleitung über Outlook und die Microsoft-Anmeldung zu einer fremden
        # Anmeldeseite (ADFS) – Anmeldung bleibt in Chrome
        self.expect_chrome("t16", "SAMLRequest=t16", "normal")
        # Outlook-Seite im normalen Tab, fertig geladen; danach tippt der Benutzer
        # eine Adresse ein → bleibt in Chrome
        before = len(self.edge_calls())
        self.click("t17")
        tab = wait_for(lambda: self.find_target("https://outlook.cloud.microsoft/keep/t17"), 5)
        time.sleep(4.5)
        s = self.cdp.call("Target.attachToTarget", {"targetId": tab["targetId"], "flatten": True})["sessionId"]
        self.cdp.call("Page.navigate", {"url": "https://www.example.org/t17-getippt", "transitionType": "typed"},
                      session=s)
        time.sleep(2.5)
        tabs = self.tabs_with("/t17-getippt")
        self.check("t17 eingetippte Adresse bleibt in Chrome", bool(tabs) and not self.edge_calls()[before:],
                   f"(Tabs {tabs}, Edge-Aufrufe {self.edge_calls()[before:]})")
        self.cleanup_tabs()
        # Mail per Doppelklick: Outlook öffnet ein leeres Pop-up-Fenster und schreibt
        # die Mail hinein (bleibt about:blank) – ein Link darin muss trotzdem an Edge
        self.click("t18")
        mail = wait_for(lambda: next((t for t in self.cdp.call("Target.getTargets")["targetInfos"]
                                      if t["type"] == "page" and t["title"] == "Mail t18"), None), 5)
        if not mail:
            self.check("t18 Mail-Fenster geöffnet", False)
        else:
            ms = self.cdp.call("Target.attachToTarget", {"targetId": mail["targetId"], "flatten": True})["sessionId"]
            time.sleep(0.5)
            wid = self.cdp.call("Browser.getWindowForTarget", {"targetId": mail["targetId"]})["windowId"]
            print(f"  (t18 Mail-Fenster: {self.window_types().get(wid)}, {mail['url']})")
            self.expect_edge("t18", elem="l18", session=ms)
        # Edge meldet Fehler → Link wird doch in Chrome geöffnet
        before = len(self.edge_calls())
        self.click("t11")
        time.sleep(3.5)
        tabs = self.tabs_with("/t11-edgefail")
        self.check("t11 Edge-Fehler → Chrome-Tab", len(self.edge_calls()) > before and len(tabs) == 1, f"(Tabs {tabs})")
        self.cleanup_tabs()
        # mailto bleibt bei Chrome/System (kein Edge); xdg-open ist hier eine Attrappe
        before = len(self.edge_calls())
        self.click("t12")
        time.sleep(1.5)
        try:
            with open(self.xdg_calls) as f:
                xdg = f.read().splitlines()
        except FileNotFoundError:
            xdg = []
        self.check("t12 mailto nicht an Edge", not self.edge_calls()[before:], f"(xdg-Aufrufe {xdg})")
        self.cleanup_tabs()
        # Ein normales Chrome-Fenster ist schon offen: Tab darf dort nicht stehen bleiben
        self.cdp.call("Target.createTarget", {"url": "https://www.example.org/schon-offen", "newWindow": True})
        time.sleep(1.5)
        self.expect_edge("t13")
        # Link aus einer fremden Seite (normaler Tab) bleibt in Chrome
        t = self.cdp.call("Target.createTarget", {"url": "https://www.example.org/page8"})
        s = self.cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
        wait_for(lambda: self.eval(s, "document.readyState") == "complete", 10)
        self.expect_chrome("t8", "www.example.org/t8", session=s)

        self.log_tests()
        self.profile_tests()

        print("Fenster am Ende:", self.windows())
        if self.args.shot:
            subprocess.run(["import", "-display", ":99", "-window", "root", self.args.shot])
        return not self.failures

    def close(self):
        """Auch nach einem Abbruch mitten im Aufbau: nur aufräumen, was es gibt."""
        if getattr(self, "cdp", None):
            try:
                self.cdp.call("Browser.close", timeout=5)
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "chrome", None):
            try:
                self.chrome.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.chrome.kill()
                self.chrome.wait()
        if getattr(self, "httpd", None):
            self.httpd.shutdown()
            self.httpd.server_close()
        if getattr(self, "short_tmp", None):
            shutil.rmtree(self.short_tmp, ignore_errors=True)
        if not self.args.keep_dir:
            shutil.rmtree(self.dir, ignore_errors=True)
        else:
            print("Testordner:", self.dir)


def extension_id():
    with open(os.path.join(EXT_DIR, "manifest.json")) as f:
        key = json.load(f)["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["app", "pwa"], default="app")
    ap.add_argument("--keep", action="store_true", help="Tabs der Chrome-Fälle offen lassen")
    ap.add_argument("--keep-dir", action="store_true", help="Testordner nicht löschen")
    ap.add_argument("--shot", help="Bildschirmfoto von :99 am Ende")
    args = ap.parse_args()
    if subprocess.run(["xdpyinfo", "-display", ":99"], capture_output=True).returncode != 0:
        sys.exit("Xvfb :99 läuft nicht")
    # Bei kill/Strg+C trotzdem Chrome beenden und Testordner löschen (finally unten)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    run = Run(args)
    try:
        ok = run.run()
    finally:
        run.close()
    print("ALLES OK" if ok else f"FEHLGESCHLAGEN: {run.failures}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
