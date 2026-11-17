# OneDrive fuer Linux – Nemo-Erweiterung (Embleme, Status-Spalte, Kontextmenue)
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.local/share/onedrive-mint"))

import gi  # noqa: E402

gi.require_version("Nemo", "3.0")

from onedrive_mint.fm_extension import build  # noqa: E402

OneDriveMintExtension = build("Nemo")
