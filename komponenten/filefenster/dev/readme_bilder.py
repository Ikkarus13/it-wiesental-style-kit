#!/usr/bin/env python3
"""Bilder für die README erzeugen – mit einem Demo-Benutzer („Anna Beispiel“),
erfundenen Dateien und erfundenem OneDrive, damit keine echten Daten zu sehen
sind. Läuft nur auf dem Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 python3 dev/readme_bilder.py docs/bilder

Der Demo-Home-Ordner entsteht in /tmp/home/anna (neutraler Pfad, er steht im
Detailbereich unter „Speicherort“) und wird als HOME an FileFenster übergeben.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_HOME = os.path.expanduser("~")
W, H, PAD = 1200, 720, 24
SCENES = ["explorer-dunkel", "symbole-hell", "onedrive-status", "ziehen"]


# ======================================================================
# Demo-Home
# ======================================================================
def build_home(home):
    shutil.rmtree(home, ignore_errors=True)

    def mk(path, size=0, text=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w" if text is not None else "wb") as fh:
            if text is not None:
                fh.write(text)
            else:
                fh.truncate(size)

    def img(path, spec):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(["convert", "-size", "640x427", "-seed", "7", spec, path], check=True)

    dirs = {"DESKTOP": "Desktop", "DOWNLOAD": "Downloads", "DOCUMENTS": "Dokumente",
            "PICTURES": "Bilder", "MUSIC": "Musik", "VIDEOS": "Videos"}
    for d in dirs.values():
        os.makedirs(os.path.join(home, d))
    mk(os.path.join(home, ".config/user-dirs.dirs"), text="".join(
        f'XDG_{k}_DIR="$HOME/{v}"\n' for k, v in dirs.items()))
    doc = os.path.join(home, "Dokumente")
    for d in ("Projekte", "Rechnungen", "Vorlagen"):
        os.makedirs(os.path.join(doc, d))
    for name, size in (("Jahresbericht 2026.docx", 48_211), ("Budget 2027.xlsx", 23_870),
                       ("Präsentation Herbst.pptx", 1_204_331), ("Reiseplanung.pdf", 310_500),
                       ("Protokoll Teammeeting.docx", 19_402)):
        mk(os.path.join(doc, name), size)
    mk(os.path.join(doc, "Notizen.txt"), text="Einkaufen\nTermine\n")
    pics = os.path.join(home, "Bilder")
    os.makedirs(os.path.join(pics, "Urlaub 2026"))
    os.makedirs(os.path.join(pics, "Bildschirmfotos"))
    for name, spec in (("Berge.jpg", "plasma:lightskyblue-darkgreen"),
                       ("Sonnenuntergang.jpg", "gradient:#ffb347-#6a2c70"),
                       ("Meer.jpg", "gradient:#a1c4fd-#0b3d91"),
                       ("Wald.jpg", "plasma:#1b5e20-#a5d6a7"),
                       ("Blumenwiese.jpg", "plasma:#f48fb1-#fff59d"),
                       ("Nordlicht.jpg", "gradient:#00e5a0-#0d1b2a")):
        img(os.path.join(pics, name), spec)
    mk(os.path.join(home, "Downloads", "Anleitung.pdf"), 512_000)

    # --- erfundenes OneDrive (Zustände wie onedrive-mint sie berechnet) ---
    accounts, state = [], {"pid": 0, "accounts": [], "busy": {}, "errors": {}}

    def account(acc_id, mount, files, dirs_state, pins, busy=(), kind="personal", extra=None):
        data = os.path.join(home, ".cache/onedrive-mint", acc_id, "vfs")
        meta = os.path.join(home, ".cache/onedrive-mint", acc_id, "vfsMeta")
        os.makedirs(meta, exist_ok=True)
        for d, st in dirs_state.items():
            os.makedirs(os.path.join(mount, d))
            if st != "cloud":
                os.makedirs(os.path.join(meta, d), exist_ok=True)
        for name, size, local in files:
            mk(os.path.join(mount, name), size)
            if local:
                mk(os.path.join(data, name), size)
                mk(os.path.join(meta, name), text=json.dumps(
                    {"Size": size, "Rs": [{"Pos": 0, "Size": size}]}))
        mk(os.path.join(home, ".config/onedrive-mint/accounts", acc_id, "pins.json"),
           text=json.dumps({p: "pin" for p in pins}))
        accounts.append(dict({"id": acc_id, "kind": kind, "mount_path": mount,
                              "display_name": "Anna Beispiel"}, **(extra or {})))
        state["accounts"].append({"id": acc_id, "label": os.path.basename(mount), "mount": mount,
                                  "real": "", "data_dir": data, "meta_dir": meta, "online": True})
        state["busy"][acc_id] = list(busy)

    account("personal-demo", os.path.join(home, "OneDrive - Persönlich"),
            [("Lebenslauf.docx", 35_100, True), ("Steuern 2025.pdf", 820_000, False),
             ("Haushaltsbuch.xlsx", 41_000, True), ("Ferien.jpg", 2_400_000, False),
             ("Rezepte.docx", 27_000, False), ("Mietvertrag.pdf", 390_000, True)],
            {"Dokumente": "cloud", "Fotos": "pinned", "Anhänge": "cloud"},
            pins=("Fotos", "Haushaltsbuch.xlsx"), busy=("Ferien.jpg",))
    account("library-demo", os.path.join(home, "Beispiel AG", "Marketing - Allgemein"),
            [("Kampagne Frühling.pptx", 3_100_000, False), ("Budget Marketing.xlsx", 52_000, True),
             ("Pressetext.docx", 22_000, True)],
            {"Logos": "cloud"}, pins=("Budget Marketing.xlsx",), kind="library",
            extra={"org": "Beispiel AG", "library_name": "Marketing - Allgemein"})
    mk(os.path.join(home, ".config/onedrive-mint/accounts.json"),
       text=json.dumps({"accounts": accounts}, ensure_ascii=False, indent=1))
    mk(os.path.join(home, ".local/state/onedrive-mint/state.json"),
       text=json.dumps(state, ensure_ascii=False, indent=1))
    # nur Programmcode/Symbole aus dem echten Home (keine Daten)
    for rel in (".local/share/onedrive-mint", ".local/share/icons", ".icons"):
        src = os.path.join(REAL_HOME, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(home, rel)), exist_ok=True)
            os.symlink(src, os.path.join(home, rel))


# ======================================================================
# Eine Szene (läuft als eigener Prozess mit HOME = Demo-Home)
# ======================================================================
def run_scene(scene, out):
    sys.path.insert(0, ROOT)
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib
    from filefenster import places
    places.user_display_name = lambda: "Anna Beispiel"
    from filefenster.app import FileFensterApp

    home = os.path.expanduser("~")
    P = lambda *p: os.path.join(home, *p)  # noqa: E731
    U = lambda *p: Gio.File.new_for_path(P(*p)).get_uri()  # noqa: E731
    bg = {"symbole-hell": "#b8c7d9", "onedrive-status": "#b8c7d9"}.get(scene, "#2d3a4a")
    subprocess.run(["xsetroot", "-solid", bg])

    def shot():
        gw = app.win.get_window()
        _r, x, y = gw.get_origin()
        w, h = gw.get_width(), gw.get_height()
        subprocess.run(["import", "-window", "root", "-crop",
                        f"{w + 2 * PAD}x{h + 2 * PAD}+{max(0, x - PAD)}+{max(0, y - PAD)}",
                        os.path.join(out, scene + ".png")])

    class App(FileFensterApp):
        def do_activate(self):
            self._ensure_theme()
            from filefenster.window import ExplorerWindow
            start = {"explorer-dunkel": U("Dokumente"), "symbole-hell": U("Bilder"),
                     "onedrive-status": U("OneDrive - Persönlich"), "ziehen": U("Dokumente")}[scene]
            self.win = win = ExplorerWindow(self, start)
            win.move(PAD, PAD)
            win.resize(W, H)
            win.present()
            self.steps = self.script(win)
            GLib.timeout_add(1500, self.tick)

        def tick(self):
            try:
                GLib.timeout_add(next(self.steps), self.tick)
            except StopIteration:
                self.quit()
            return False

        def script(self, win):
            fv = lambda: win.current.views["folder"]  # noqa: E731
            if scene == "explorer-dunkel":
                win.current.open_in_new_tab(U("Bilder"))
                win.current.open_in_new_tab(U("OneDrive - Persönlich"))
                yield 500
                fv().reveal([U("Dokumente", "Jahresbericht 2026.docx")])
            elif scene == "onedrive-status":
                yield 300
                fv().reveal([U("OneDrive - Persönlich", "Lebenslauf.docx")])
            elif scene == "ziehen":
                yield from self.drag(win, fv())
                return
            yield 1500
            shot()

        def drag(self, win, f):
            from Xlib import X, display
            from Xlib.ext import xtest
            from filefenster.folderview import COL_NAME
            d = display.Display()

            def xy(name):
                path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
                rect = f.tree.get_background_area(path, f.col_name)
                x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 50, rect.y + rect.height // 2)
                wx, wy = f.tree.translate_coordinates(win, x, y)
                _r, ox, oy = win.get_window().get_origin()
                return ox + wx, oy + wy

            def mv(x, y):
                xtest.fake_input(d, X.MotionNotify, x=int(x), y=int(y))
                d.sync()
            yield 300
            f.reveal([Gio.File.new_for_path(P("Dokumente", n)).get_uri()
                      for n in ("Budget 2027.xlsx", "Reiseplanung.pdf")])
            yield 400
            a, b = xy("Budget 2027.xlsx"), xy("Projekte")
            mv(*a)
            yield 150
            xtest.fake_input(d, X.ButtonPress, 1)
            d.sync()
            yield 150
            for i in range(1, 15):
                mv(a[0] + (b[0] - a[0]) * i / 14 + 30, a[1] + (b[1] - a[1]) * i / 14)
                yield 40
            yield 700
            shot()
            xtest.fake_input(d, X.KeyPress, d.keysym_to_keycode(0xff1b))   # Esc: abbrechen
            xtest.fake_input(d, X.KeyRelease, d.keysym_to_keycode(0xff1b))
            xtest.fake_input(d, X.ButtonRelease, 1)
            d.sync()
            yield 500

    app = App()
    app.run([sys.argv[0]])


# ======================================================================
if __name__ == "__main__":
    if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
        sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99), siehe dev/testbildschirm.py")
    out = os.path.abspath(sys.argv[1])
    if os.environ.get("README_SCENE"):
        run_scene(os.environ["README_SCENE"], out)
        sys.exit(0)
    home = "/tmp/home/anna"
    os.makedirs(out, exist_ok=True)
    build_home(home)
    settings = {"explorer-dunkel": ("dark", "details", True),
                "symbole-hell": ("light", "large", False),
                "onedrive-status": ("light", "details", True),
                "ziehen": ("dark", "details", False)}
    for scene in SCENES:
        theme, mode, pane = settings[scene]
        cfg = os.path.join(home, ".config", "filefenster")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "settings.json"), "w") as fh:
            json.dump({"theme": theme, "view_mode": mode, "details_pane": pane,
                       "folder_views": {}}, fh)
        env = dict(os.environ, HOME=home, README_SCENE=scene, FILEFENSTER_CONFIG_DIR=cfg,
                   FILEFENSTER_NO_UNIQUE="1")
        subprocess.run([sys.executable, os.path.abspath(__file__), out], env=env, timeout=90)
        print("Bild:", os.path.join(out, scene + ".png"), flush=True)
