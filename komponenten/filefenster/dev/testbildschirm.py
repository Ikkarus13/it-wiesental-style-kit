#!/usr/bin/env python3
"""Unsichtbarer Test-Bildschirm, damit Tests (auch mit Maus) nicht auf dem
Arbeitsbildschirm laufen:

    python3 dev/testbildschirm.py [:99] &
    DISPLAY=:99 python3 dev/test_dnd.py OUTDIR

Nimmt Xvfb, falls installiert (sudo apt install xvfb); sonst Xephyr in einem
Host-Fenster, das nie angezeigt wird. Screenshots: import -display :99 -window root
"""
import os
import shutil
import subprocess
import sys

disp = sys.argv[1] if len(sys.argv) > 1 else ":99"
W, H = 1600, 1000
if shutil.which("Xvfb"):
    os.execvp("Xvfb", ["Xvfb", disp, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"])

from Xlib import X, display  # noqa: E402

host = display.Display()
scr = host.screen()
# Eltern-Fenster wird nie gemappt → Xephyr bleibt unsichtbar, rendert aber intern
parent = scr.root.create_window(0, 0, W, H, 0, scr.root_depth, X.InputOutput)
host.sync()
proc = subprocess.Popen(["Xephyr", disp, "-parent", hex(parent.id), "-screen", f"{W}x{H}",
                         "-no-host-grab", "-nolisten", "tcp"])
try:
    proc.wait()
finally:
    parent.destroy()
    host.sync()
