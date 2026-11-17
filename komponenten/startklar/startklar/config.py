"""Einstellungen und Nutzungszähler (JSON unter ~/.config/startklar).

Zum Testen: STARTKLAR_CONFIG_DIR=<ordner> – dann wird nichts Echtes angefasst.
"""
import json
import os
import tempfile
import time

DEFAULTS = {
    "pinned": None,               # None = beim ersten Start aus Favoriten erzeugen
    "view": "category",           # „Alle“: category | grid | list
    "show_recommended": True,
    "folders": ["documents", "downloads", "explorer", "settings"],
    "web_search": "https://www.bing.com/search?q={}",
    "search_files": True,
    "theme": "auto",              # auto | dark | light
    "acrylic": True,
    "update_check": True,
    "update_checked": 0,
    "github_token": "",
    "layout": "default",          # more_pins | default | more_recs
    "icon_size": "medium",        # small | medium | large
    "width": "normal",            # narrow | normal | wide
    "show_recent_apps": True,
    "show_recent_files": True,
    "show_all": True,
}

_TYPES = {
    "pinned": (list, type(None)), "view": str, "show_recommended": bool,
    "folders": list, "web_search": str, "search_files": bool, "theme": str,
    "acrylic": bool, "update_check": bool, "update_checked": int, "github_token": str,
    "layout": str, "icon_size": str, "width": str, "show_recent_apps": bool,
    "show_recent_files": bool, "show_all": bool,
}


def config_dir():
    d = os.environ.get("STARTKLAR_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "startklar")
    os.makedirs(d, exist_ok=True)
    return d


def _load(name):
    try:
        with open(os.path.join(config_dir(), name), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(name, data):
    d = config_dir()
    fd, tmp = tempfile.mkstemp(prefix="." + name, dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(d, name))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class Config:
    def __init__(self):
        raw = _load("config.json")
        self.data = dict(DEFAULTS)
        for k, v in raw.items():
            if k in _TYPES and isinstance(v, _TYPES[k]):
                self.data[k] = v
        if self.data["view"] not in ("category", "grid", "list"):
            self.data["view"] = "category"
        if self.data["pinned"] is not None:
            self.data["pinned"] = [p for p in self.data["pinned"] if isinstance(p, str)]
        usage = _load("usage.json")
        self.usage = {k: v for k, v in usage.items()
                      if isinstance(v, dict) and isinstance(v.get("count"), int)}

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        _save("config.json", self.data)

    # ---------------------------------------------------------------- Nutzung
    def note_launch(self, app_id):
        u = self.usage.setdefault(app_id, {"count": 0, "last": 0})
        u["count"] += 1
        u["last"] = int(time.time())
        _save("usage.json", self.usage)

    def score(self, app_id):
        """Wie oft (und wie kürzlich) eine App gestartet wurde – für Reihenfolgen."""
        u = self.usage.get(app_id)
        if not u:
            return 0.0
        age_days = max(0.0, (time.time() - u.get("last", 0)) / 86400)
        return u["count"] / (1.0 + age_days / 14)


# Grössen je Einstellung: Symbol, Kachelbreite, Kachelhöhe, Symbol in Listen
SIZES = {"small": (24, 84, 74, 20), "medium": (32, 96, 84, 24), "large": (40, 108, 96, 28)}
WIDTHS = {"narrow": 720, "normal": 840, "wide": 960}
# Zeilen „Angeheftet“, Einträge „Empfohlen“ (wie Windows: mehr Pins / Standard / mehr Empfehlungen)
LAYOUTS = {"more_pins": (3, 2), "default": (2, 6), "more_recs": (1, 8)}


def metrics():
    """Alle Masse, die von den Einstellungen abhängen."""
    cfg = get_config()
    icon, tw, th, list_icon = SIZES.get(cfg.get("icon_size"), SIZES["medium"])
    width = WIDTHS.get(cfg.get("width"), WIDTHS["normal"])
    inner = width - 64
    rows, rec = LAYOUTS.get(cfg.get("layout"), LAYOUTS["default"])
    return {"icon": icon, "tile_w": tw, "tile_h": th, "list_icon": list_icon,
            "width": width, "cols": max(4, inner // tw), "pin_rows": rows,
            "rec": rec, "rec_more": 18, "cat_cols": max(3, (inner + 16) // 192)}


_config = None


def get_config():
    global _config
    if _config is None:
        _config = Config()
    return _config
