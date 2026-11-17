"""Updates über GitHub-Releases: prüfen, herunterladen, an Ort und Stelle einspielen.

Neue Version = neues Release mit Tag «vX.Y.Z» (siehe tools/release.sh). Ein Git-Checkout
wird per «git pull» aktualisiert, eine entpackte Installation durch Überschreiben der Dateien.
"""
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from version import VERSION

APP_DIR = Path(__file__).resolve().parent
REPO = 'Ikkarus13/emoji-panel'

# --- Style Kit ----------------------------------------------------------
# Wurde das Emoji-Panel über ein Style Kit installiert, kommen Updates nur noch über
# das Kit: keine eigene Suche, keine Meldung, keine Installation. Das Kit schreibt
# ${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json mit «kit», «komponenten»
# und «verwalten» (Befehl als Liste). Die Datei wird bei jedem Aufruf neu gelesen.
KIT_KENNUNG = 'emoji-panel'


def _kit_datei():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'style-kit', 'verwaltet.json')


def _kit_info_datei():
    """{'kit': Name, 'verwalten': Befehl oder None}, wenn ein Style Kit das Panel verwaltet."""
    try:
        with open(_kit_datei(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    kit, komp, cmd = data.get('kit'), data.get('komponenten'), data.get('verwalten')
    if not isinstance(kit, str) or not kit.strip() or not isinstance(komp, list) \
            or KIT_KENNUNG not in komp:
        return None
    if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) and x for x in cmd)):
        cmd = None
    return {'kit': kit.strip(), 'verwalten': cmd}


_KIT_MARKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.kit-verwaltet')


def kit_info():
    """Wie _kit_info_datei(); zusätzlich gilt das Programm als verwaltet, wenn die Kit-Fassung
    die Datei .kit-verwaltet mitbringt (Inhalt: Name des Kits). Die eigene Update-Funktion
    bleibt dann auch abgeschaltet, wenn verwaltet.json fehlt oder gelöscht wurde."""
    info = _kit_info_datei()
    if info:
        return info
    try:
        with open(_KIT_MARKE, encoding='utf-8') as f:
            name = f.read().strip()[:80]
    except OSError:
        return None
    return {'kit': name or 'Style Kit', 'verwalten': None}


def kit_verwaltet():
    """Name des Style Kits, das das Emoji-Panel verwaltet, sonst None."""
    info = kit_info()
    return info['kit'] if info else None


def kit_meldung(kit):
    return f'Updates für Emoji-Panel kommen über das {kit}.'


def kit_oeffnen():
    """Startet die Verwaltung des Kits (Befehl aus verwaltet.json, ohne Shell)."""
    info = kit_info()
    if not info or not info['verwalten']:
        return False
    try:
        subprocess.Popen(info['verwalten'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def _ver(s):
    return tuple(int(p) if p.isdigit() else 0 for p in s.lstrip('vV').split('.'))


def gh_token():
    """Token der GitHub-CLI als Rückfall (das Repository ist privat)."""
    try:
        r = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


class _NoTokenRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen (z. B. Asset-Download → Speicher-Host) ohne GitHub-Token folgen."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header('Authorization')
        return new


_opener = urllib.request.build_opener(_NoTokenRedirect)


def _get(url, token=None, accept='application/vnd.github+json'):
    req = urllib.request.Request(url, headers={'Accept': accept, 'User-Agent': 'Emoji-Panel/' + VERSION})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    return _opener.open(req, timeout=30)


def check(repo=REPO, token=None):
    """Neueres Release als dict (version, notes, url, tarball) oder None. Wirft bei Netzfehlern.
    Verwaltet ein Style Kit das Panel, ohne Anfrage None."""
    if kit_verwaltet():
        return None
    token = token or gh_token()
    try:
        with _get(f'https://api.github.com/repos/{repo}/releases/latest', token) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError('Kein Release gefunden – das Repository ist privat und es ist '
                               'kein Zugriffstoken eingetragen (oder «gh» nicht angemeldet).') from e
        raise
    v = rel.get('tag_name', '').lstrip('vV')
    if not v or _ver(v) <= _ver(VERSION):
        return None
    asset = next((a['url'] for a in rel.get('assets', []) if a['name'].endswith('.tar.gz')), None)
    return {'version': v, 'notes': rel.get('body') or '', 'url': rel.get('html_url'),
            'tarball': asset or rel['tarball_url'], 'is_asset': bool(asset), 'token': token}


def is_git():
    return (APP_DIR / '.git').exists()


def install(info):
    """Spielt die neue Version ins Programmverzeichnis ein (danach run_installer)."""
    kit = kit_verwaltet()
    if kit:
        raise RuntimeError(kit_meldung(kit))
    if is_git():
        subprocess.run(['git', '-C', str(APP_DIR), 'pull', '--ff-only'],
                       check=True, capture_output=True, text=True, timeout=120)
        got = (APP_DIR / 'version.py').read_text().split("'")[1]
        if _ver(got) < _ver(info['version']):
            raise RuntimeError(f'Der Git-Stand enthält Version {info["version"]} noch nicht '
                               f'(vorhanden: {got}). Bitte Branch prüfen.')
        return
    accept = 'application/octet-stream' if info.get('is_asset') else 'application/vnd.github+json'
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / 'paket.tar.gz'
        with _get(info['tarball'], info.get('token'), accept) as r, open(pkg, 'wb') as f:
            shutil.copyfileobj(r, f)
        out = Path(tmp) / 'x'
        with tarfile.open(pkg) as t:
            t.extractall(out, filter='data')
        src = next((p.parent for p in out.rglob('emojipanel.py')
                    if (p.parent / 'installieren.sh').exists()), None)
        if not src:
            raise RuntimeError('Paket enthält kein Emoji-Panel')
        shutil.copytree(src, APP_DIR, dirs_exist_ok=True)
        for name in ('emoji-panel', 'installieren.sh', 'deinstallieren.sh', 'emojipanel.py'):
            p = APP_DIR / name
            if p.exists():
                p.chmod(p.stat().st_mode | 0o111)


def run_installer():
    """installieren.sh losgelöst starten: beendet diese Instanz und startet die neue Version."""
    if kit_verwaltet():
        return
    subprocess.Popen(['bash', str(APP_DIR / 'installieren.sh')], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     env={**os.environ, 'EMOJIPANEL_UPDATE': '1'})
