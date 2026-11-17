"""Minimaler Microsoft-Graph-Zugriff (nur lesend) fuer Kontoinfos und Web-Links."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"


class GraphError(Exception):
    pass


def _get(url, access_token, timeout=20):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + access_token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read())["error"]["message"]
        except (ValueError, KeyError):
            msg = str(e)
        raise GraphError(msg) from None
    except (urllib.error.URLError, OSError) as e:
        raise GraphError(str(e)) from None


def token_valid(token):
    if not token or "access_token" not in token:
        return False
    expiry = token.get("expiry", "")
    try:
        # rclone schreibt z.B. 2026-10-13T10:11:12.123456789+02:00
        from datetime import datetime
        exp = datetime.fromisoformat(expiry[:26] + expiry[-6:] if "." in expiry else expiry)
        return exp.timestamp() - time.time() > 60
    except ValueError:
        return True


def my_drive(access_token):
    """Liefert dict mit id, driveType, owner, quota, webUrl."""
    return _get(GRAPH + "/me/drive", access_token)


def org_from_web_url(web_url):
    """https://contoso-my.sharepoint.com/personal/... -> 'contoso'."""
    host = urllib.parse.urlparse(web_url or "").hostname or ""
    if host.endswith("-my.sharepoint.com"):
        return host[: -len("-my.sharepoint.com")]
    if host.endswith(".sharepoint.com"):
        return host[: -len(".sharepoint.com")]
    return ""


def item_web_url(access_token, drive_id, rel_path):
    if rel_path:
        quoted = urllib.parse.quote("/" + rel_path)
        url = f"{GRAPH}/drives/{drive_id}/root:{quoted}"
    else:
        url = f"{GRAPH}/drives/{drive_id}/root"
    return _get(url, access_token).get("webUrl", "")


def recycle_bin_url(kind, drive_web_url, site_url=""):
    if kind == "library" and site_url:
        return site_url.rstrip("/") + "/_layouts/15/RecycleBin.aspx"
    if kind == "business" and "/personal/" in (drive_web_url or ""):
        base = drive_web_url.split("/Documents")[0].rstrip("/")
        return base + "/_layouts/15/RecycleBin.aspx"
    return "https://onedrive.live.com/?v=recyclebin"


# ---------------------------------------------------------------- SharePoint / Teams

class LibraryInfo:
    """Ergebnis der Aufloesung eines Teams-/SharePoint-Ordners."""

    def __init__(self, host, drive_id, drive_name, drive_web_url, rel_path, site_name):
        self.host = host                    # contoso.sharepoint.com
        self.drive_id = drive_id
        self.drive_name = drive_name        # "Dokumente"
        self.drive_web_url = drive_web_url
        self.rel_path = rel_path.strip("/")  # Unterordner in der Bibliothek ('' = ganze Bibliothek)
        self.site_name = site_name          # "Projektteam"

    @property
    def folder_name(self):
        return self.rel_path.rsplit("/", 1)[-1] if self.rel_path else self.drive_name

    @property
    def display_name(self):
        """Name wie unter Windows: 'Projektteam - Dokumente' bzw. 'Projektteam - Allgemein'."""
        return f"{self.site_name} - {self.folder_name}"

    @property
    def breadcrumb(self):
        parts = [self.site_name, self.drive_name] + (self.rel_path.split("/") if self.rel_path else [])
        return " › ".join(parts)

    @property
    def site_url(self):
        return self.drive_web_url.rstrip("/").rsplit("/", 1)[0]

    @property
    def web_url(self):
        if not self.rel_path:
            return self.drive_web_url
        return self.drive_web_url.rstrip("/") + "/" + urllib.parse.quote(self.rel_path)


def tenant_from_host(host):
    host = (host or "").lower()
    for suffix in ("-my.sharepoint.com", ".sharepoint.com"):
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return ""


def _url_path(url):
    return urllib.parse.unquote(urllib.parse.urlparse(url).path).rstrip("/")


def _site_name(token, site_id):
    try:
        return _get(f"{GRAPH}/sites/{site_id}?$select=displayName,name", token).get("displayName", "")
    except GraphError:
        return ""


def _rel_in_drive(drive_web_url, server_path):
    base = _url_path(drive_web_url)
    if server_path == base:
        return ""
    if server_path.startswith(base + "/"):
        return server_path[len(base) + 1:]
    return None


def _resolve_server_path(token, host, server_path):
    """'/sites/Team/Shared Documents/Allgemein' -> LibraryInfo, ueber die Bibliotheken der Site."""
    segs = [s for s in server_path.split("/") if s]
    site = None
    if len(segs) >= 2 and segs[0].lower() in ("sites", "teams"):
        try:
            site = _get(f"{GRAPH}/sites/{host}:/{segs[0]}/{urllib.parse.quote(segs[1])}", token)
        except GraphError:
            site = None
    if site is None:
        site = _get(f"{GRAPH}/sites/{host}", token)
    drives = _get(f"{GRAPH}/sites/{site['id']}/drives?$select=id,name,webUrl", token).get("value", [])
    best = None
    for d in drives:
        rel = _rel_in_drive(d.get("webUrl", ""), server_path)
        if rel is not None and (best is None or len(_url_path(d["webUrl"])) > len(_url_path(best[0]["webUrl"]))):
            best = (d, rel)
    if not best:
        raise GraphError("Zu diesem Link wurde keine Dokumentbibliothek gefunden.")
    d, rel = best
    if rel.startswith("Forms/") or rel == "Forms":
        rel = ""
    return LibraryInfo(host, d["id"], d.get("name", ""), d.get("webUrl", ""), rel,
                       site.get("displayName") or site.get("name", ""))


def _resolve_item(token, drive_id, item_id, host):
    item = _get(f"{GRAPH}/drives/{drive_id}/items/{item_id}?$select=id,name,folder,parentReference,"
                "root", token)
    drive = _get(f"{GRAPH}/drives/{drive_id}?$select=id,name,webUrl,driveType", token)
    if "folder" not in item and "root" not in item:
        raise GraphError("Der Link zeigt auf eine Datei. Bitte einen Ordner oder eine Bibliothek wählen.")
    if "root" in item:
        rel = ""
    else:
        ppath = item.get("parentReference", {}).get("path", "")
        parent = ppath.split("root:", 1)[1] if "root:" in ppath else ""
        rel = (urllib.parse.unquote(parent).strip("/") + "/" + item["name"]).strip("/")
    site_id = item.get("parentReference", {}).get("siteId", "")
    site = _site_name(token, site_id) if site_id else ""
    host = host or urllib.parse.urlparse(drive.get("webUrl", "")).hostname or ""
    return LibraryInfo(host, drive["id"], drive.get("name", ""), drive.get("webUrl", ""), rel,
                       site or drive.get("name", ""))


def resolve_link(token, url):
    """Teams-/SharePoint-Link (Freigabelink, Browseradresse oder Ordner-URL) aufloesen."""
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host.endswith(".sharepoint.com"):
        raise GraphError("Das ist kein SharePoint-/Teams-Link (…sharepoint.com).")
    if host.endswith("-my.sharepoint.com"):
        raise GraphError("Das ist ein OneDrive-Link. Bitte einen Link aus Teams oder SharePoint verwenden.")
    query = urllib.parse.parse_qs(parsed.query)
    # Browseradresse einer Bibliotheksansicht: .../Forms/AllItems.aspx?id=/sites/Team/Shared Documents/X
    for key in ("id", "RootFolder"):
        if key in query:
            return _resolve_server_path(token, host, query[key][0].rstrip("/"))
    path = _url_path(url)
    if "/Forms/" in path or path.lower().endswith(".aspx"):
        # Bibliotheksansicht ohne Ordnerangabe -> ganze Bibliothek
        return _resolve_server_path(token, host, path.split("/Forms/")[0])
    # Freigabelink (…/:f:/s/Team/Exyz…) oder direkte Ordner-URL ueber die Shares-API
    enc = "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    try:
        item = _get(f"{GRAPH}/shares/{enc}/driveItem?$select=id,parentReference", token)
        return _resolve_item(token, item["parentReference"]["driveId"], item["id"], host)
    except (GraphError, KeyError):
        if "/:" in parsed.path:      # Freigabelink, der nicht aufgeloest werden konnte
            raise
    return _resolve_server_path(token, host, path)


def resolve_odopen(token, params):
    """Parameter der SharePoint-Schaltflaeche „Synchronisieren“ (odopen://sync/?...) aufloesen."""
    def p(name):
        v = params.get(name) or params.get(name.lower()) or ""
        return v.strip("{}") if name.endswith("Id") else v

    web_url = p("webUrl")
    host = urllib.parse.urlparse(web_url).hostname or ""
    if not host:
        raise GraphError("Der Synchronisierungslink enthält keine Website-Adresse.")
    site_id, web_id, list_id = p("siteId"), p("webId"), p("listId")
    drive = None
    if site_id and web_id and list_id:
        try:
            drive = _get(f"{GRAPH}/sites/{host},{site_id},{web_id}/lists/{list_id}/drive"
                         "?$select=id,name,webUrl", token)
        except GraphError:
            drive = None
    folder_url = p("folderUrl")
    if drive is None:
        target = folder_url or web_url
        return _resolve_server_path(token, host, _url_path(target))
    rel = ""
    if folder_url:
        r = _rel_in_drive(drive.get("webUrl", ""), _url_path(folder_url))
        rel = r or ""
    site = p("webTitle") or _site_name(token, f"{host},{site_id},{web_id}")
    return LibraryInfo(host, drive["id"], p("listTitle") or drive.get("name", ""),
                       drive.get("webUrl", ""), rel, site)
