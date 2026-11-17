#!/usr/bin/env python3
"""Tests für den Native-Messaging-Host (ohne Chrome, mit Schein-Edge).

    python3 -m unittest tests/test_host.py -v
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "native-host", "edge_links_host.py")


class HostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.calls = os.path.join(self.dir, "calls.txt")
        self.fake = os.path.join(self.dir, "fake-edge")
        with open(self.fake, "w") as f:
            f.write('#!/bin/sh\n'
                    'env | grep -c "^CHROME_" > "$0.chromevars"\n'
                    'printf "%s\\n" "$@" >> "' + self.calls + '"\n'
                    'case "$*" in *edgefail*) exit 3;; *edgeslow*) sleep 3;; esac\n')
        os.chmod(self.fake, 0o755)
        self.env = dict(os.environ, EDGE_LINKS_BROWSER=self.fake,
                        XDG_CONFIG_HOME=os.path.join(self.dir, "config"),
                        XDG_CACHE_HOME=os.path.join(self.dir, "cache"),
                        CHROME_DESKTOP="google-chrome.desktop", CHROME_WRAPPER="/opt/google/chrome/google-chrome")

    def tearDown(self):
        self.tmp.cleanup()

    def run_host(self, payload, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode()
        msg = data if raw is not None else struct.pack("=I", len(data)) + data
        p = subprocess.run([sys.executable, HOST, "chrome-extension://x/"], input=msg,
                           capture_output=True, env=self.env, timeout=10)
        out = p.stdout
        if not out:
            return p.returncode, None
        (n,) = struct.unpack("=I", out[:4])
        self.assertEqual(len(out), 4 + n)
        return p.returncode, json.loads(out[4:])

    def calls_list(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def test_ping(self):
        code, reply = self.run_host({"ping": True})
        self.assertEqual(code, 0)
        self.assertTrue(reply["ok"])
        self.assertIn("version", reply)
        self.assertEqual(self.calls_list(), [])

    def test_opens_url(self):
        url = "https://www.example.org/a?b=1&c=%20x#frag"
        code, reply = self.run_host({"url": url})
        self.assertEqual((code, reply), (0, {"ok": True}))
        self.assertEqual(self.calls_list(), [url])
        with open(self.fake + ".chromevars") as f:
            self.assertEqual(f.read().strip(), "0", "CHROME_*-Variablen an Edge vererbt")

    def test_profile_from_config(self):
        os.makedirs(os.path.join(self.dir, "config", "edge-links"))
        with open(os.path.join(self.dir, "config", "edge-links", "config.json"), "w") as f:
            json.dump({"profile": "Profile 1"}, f)
        code, reply = self.run_host({"url": "http://example.org/"})
        self.assertTrue(reply["ok"])
        self.assertEqual(self.calls_list(), ["--profile-directory=Profile 1", "http://example.org/"])

    def write_edge_state(self, cache, order=None, last_used="Default"):
        path = os.path.join(self.dir, "config", "microsoft-edge")
        os.makedirs(path, exist_ok=True)
        profile = {"info_cache": cache, "last_used": last_used}
        if order is not None:
            profile["profiles_order"] = order
        with open(os.path.join(path, "Local State"), "w") as f:
            json.dump({"profile": profile}, f)

    def write_config(self, cfg):
        os.makedirs(os.path.join(self.dir, "config", "edge-links"), exist_ok=True)
        with open(os.path.join(self.dir, "config", "edge-links", "config.json"), "w") as f:
            json.dump(cfg, f)

    def test_profiles(self):
        self.write_edge_state({"Profile 1": {"name": "Persönlich", "user_name": "p@example.org"},
                               "Default": {"name": "Arbeit", "user_name": "a@example.com"},
                               "Profile 3": {"name": "Ohne Liste"}},
                              order=["Default", "Profile 1", "Weg"], last_used="Profile 1")
        self.write_config({"profile": "Default"})
        code, reply = self.run_host({"profiles": True})
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["profiles"], [
            {"dir": "Default", "name": "Arbeit", "user": "a@example.com"},
            {"dir": "Profile 1", "name": "Persönlich", "user": "p@example.org"},
            {"dir": "Profile 3", "name": "Ohne Liste", "user": ""}])
        self.assertEqual((reply["last_used"], reply["default"]), ("Profile 1", "Default"))
        self.assertEqual(self.calls_list(), [])

    def test_profiles_without_edge(self):
        code, reply = self.run_host({"profiles": True})
        self.assertFalse(reply["ok"])

    def test_profile_from_message(self):
        self.write_edge_state({"Default": {"name": "A"}, "Profile 1": {"name": "B"}})
        self.write_config({"profile": "Default"})
        self.run_host({"url": "https://example.org/1", "profile": "Profile 1"})  # Wahl schlägt config
        self.run_host({"url": "https://example.org/2", "profile": ""})           # zuletzt benutztes
        self.run_host({"url": "https://example.org/3"})                          # config.json
        self.run_host({"url": "https://example.org/4", "profile": "Profile 9"})  # gibt es nicht
        self.assertEqual(self.calls_list(), [
            "--profile-directory=Profile 1", "https://example.org/1",
            "https://example.org/2",
            "--profile-directory=Default", "https://example.org/3",
            "https://example.org/4"])

    def test_bad_profile_names(self):
        for profile in ["-x", "../Default", "a/b", "..", 5, ["Default"], "x" * 300,
                        "x\n2026-01-01 00:00:00 FAKE", "a\0b", "a\x7fb"]:
            with self.subTest(profile=profile):
                code, reply = self.run_host({"url": "https://example.org/", "profile": profile})
                self.assertFalse(reply["ok"])
        self.assertEqual(self.calls_list(), [])

    def test_log_one_line_per_entry(self):
        # Profilnamen mit Zeilenumbruch dürfen keine Protokolleinträge vortäuschen
        self.write_edge_state({"Default": {"name": "A"}})
        self.write_config({"profile": "x\n2026-01-01 00:00:00 FAKE"})
        self.run_host({"url": "https://example.org/1"})
        self.run_host({"url": "https://example.org/2", "profile": "y\n2026-01-01 00:00:00 FAKE"})
        self.write_config({"profile": "Weg"})
        self.run_host({"url": "https://example.org/3"})
        with open(os.path.join(self.dir, "cache", "edge-links", "host.log")) as f:
            log = f.read()
        self.assertEqual(len(log.splitlines()), 3, log)
        self.assertNotIn("FAKE", log)

    def test_bad_profile_in_config_ignored(self):
        # Tippfehler in config.json: Link geht trotzdem an Edge (Edge wählt das Profil)
        for profile in ["-x", 5, "a/b"]:
            with self.subTest(profile=profile):
                self.write_config({"profile": profile})
                code, reply = self.run_host({"url": f"https://example.org/{profile}"})
                self.assertTrue(reply["ok"])
        self.assertEqual(self.calls_list(), ["https://example.org/-x", "https://example.org/5",
                                             "https://example.org/a/b"])

    def test_rejects(self):
        for url in ["javascript:alert(1)", "file:///etc/passwd", "about:blank", "https://", "",
                    "https://a.example/ x", "https://a.example/\n--evil", "-https://x", None, 5,
                    "https://" + "a" * 40000 + ".example/", "chrome://settings", "https://[::1"]:
            with self.subTest(url=url):
                code, reply = self.run_host({"url": url})
                self.assertEqual(code, 1)
                self.assertFalse(reply["ok"])
        self.assertEqual(self.calls_list(), [])

    def test_bad_messages(self):
        self.assertEqual(self.run_host(None, raw=b""), (0, None))  # Port geschlossen
        code, reply = self.run_host(None, raw=b"\x05\x00")
        self.assertFalse(reply["ok"])
        code, reply = self.run_host(None, raw=struct.pack("=I", 10) + b"{}")
        self.assertFalse(reply["ok"])
        code, reply = self.run_host(None, raw=struct.pack("=I", 2 * 1024 * 1024))
        self.assertFalse(reply["ok"])
        code, reply = self.run_host(["https://example.org/"])
        self.assertFalse(reply["ok"])

    def test_edge_error_reported(self):
        code, reply = self.run_host({"url": "https://example.org/edgefail"})
        self.assertFalse(reply["ok"])
        self.assertIn("Code 3", reply["error"])

    def test_slow_edge_not_awaited(self):
        t = time.monotonic()
        code, reply = self.run_host({"url": "https://example.org/edgeslow"})
        self.assertTrue(reply["ok"])
        self.assertLess(time.monotonic() - t, 2.8)

    def test_missing_edge(self):
        self.env["EDGE_LINKS_BROWSER"] = os.path.join(self.dir, "gibt-es-nicht")
        code, reply = self.run_host({"url": "https://example.org/"})
        self.assertFalse(reply["ok"])
        with open(os.path.join(self.dir, "cache", "edge-links", "host.log")) as f:
            log = f.read()
        self.assertIn("Fehler", log)
        self.assertNotIn("example.org", log, "URLs sollen nicht ins Protokoll")


if __name__ == "__main__":
    unittest.main()
