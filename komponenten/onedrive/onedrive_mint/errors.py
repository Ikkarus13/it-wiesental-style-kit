"""Technische Fehlermeldungen (rclone, Microsoft Graph) in verstaendliche Texte uebersetzen."""

import re

# (Muster in der technischen Meldung, verstaendliche Erklaerung)
EXPLANATIONS = [
    (r"invalid_grant|AADSTS700082|AADSTS50173|token.*expired|refresh token",
     "Die Anmeldung ist abgelaufen. Bitte das Konto in den Einstellungen neu anmelden."),
    (r"couldn't fetch token|no such host|server misbehaving|network is unreachable|dial tcp|"
     r"i/o timeout|connection refused|connection reset|TLS handshake",
     "Keine Verbindung zu Microsoft (Netzwerk oder DNS). Wird automatisch erneut versucht, "
     "sobald die Verbindung wieder steht."),
    (r"quotaLimitReached|insufficientStorage|\b507\b|not enough space|quota",
     "Der Speicherplatz in OneDrive ist voll. Bitte online Platz schaffen oder Speicher erweitern."),
    (r"\b401\b|Unauthorized",
     "Microsoft hat den Zugriff auf diese Datei abgelehnt. Das ist meist eine vorübergehende "
     "Störung bei Microsoft und wird später erneut versucht. Online lässt sich die Datei "
     "weiterhin öffnen."),
    (r"accessDenied|\b403\b|Forbidden|permission",
     "Keine Berechtigung. Sie dürfen in diesem Ordner keine Dateien ändern."),
    (r"resourceLocked|lockMismatch|\b423\b|locked",
     "Die Datei ist in OneDrive gesperrt, z. B. weil jemand sie gerade in Office bearbeitet."),
    (r"nameAlreadyExists|\b409\b|conflict",
     "In OneDrive gibt es bereits ein Element mit diesem Namen."),
    (r"pathTooLong|path is too long|too long",
     "Der Pfad ist zu lang. OneDrive erlaubt höchstens 400 Zeichen für den gesamten Pfad."),
    (r"invalidRequest.*name|invalid.*character|not a valid file name",
     "Der Dateiname enthält Zeichen, die OneDrive nicht erlaubt "
     "(z. B. \" * : < > ? / \\ |) oder ist ein reservierter Name."),
    (r"fileTooLarge|maxFileSizeExceeded|too large",
     "Die Datei ist zu gross für OneDrive (maximal 250 GB)."),
    (r"itemNotFound|\b404\b|not found|object not found",
     "Das Element wurde in OneDrive nicht gefunden. Es wurde vermutlich online gelöscht "
     "oder verschoben."),
    (r"activityLimitReached|\b429\b|throttl|tooManyRequests|\b503\b|serviceNotAvailable",
     "Microsoft drosselt gerade die Anfragen. Wird automatisch später erneut versucht."),
    (r"Der Ordner .* ist nicht leer",
     "Der OneDrive-Ordner ist nicht leer. Bitte leeren oder in den Einstellungen einen anderen "
     "Ordner wählen."),
    (r"rclone ist nicht installiert", "rclone ist nicht installiert."),
]


def explain(error):
    """Verstaendliche Erklaerung (ein bis zwei Saetze) zu einer technischen Meldung."""
    for pattern, text in EXPLANATIONS:
        if re.search(pattern, error or "", re.I):
            return text
    low = (error or "").lower()
    if "upload" in low or "failed to copy" in low:
        return "Die Datei konnte nicht hochgeladen werden. Wird automatisch erneut versucht."
    if "download" in low or "vfs reader" in low or "cache file" in low or "open" in low:
        return "Die Datei konnte nicht heruntergeladen werden. Wird beim nächsten Öffnen erneut versucht."
    return "Die Datei konnte nicht synchronisiert werden."


def short(error):
    """Erster Satz der Erklaerung – fuer einzeilige Anzeigen (Aktivitaetscenter)."""
    text = explain(error)
    return text.split(". ")[0].rstrip(".") + "."


def technical(error):
    """Technische Meldung ohne angehaengte HTML-Fehlerseiten, Skripte und ueberlange Zeichenketten."""
    text = error or ""
    m = re.search(r"<(!doctype|html|head|body|script)\b", text, re.I)
    if m:
        body = re.search(r"<body[^>]*>(.*?)</body>", text[m.start():], re.I | re.S)
        hint = re.sub(r"<[^>]+>|\s+", " ", body.group(1)).strip() if body else ""
        text = text[:m.start()].rstrip() + (f" (Antwort von Microsoft: {hint[:120]})" if hint
                                            else " (Antwort von Microsoft: HTML-Fehlerseite)")
    text = re.sub(r"\S{120,}", "…", text)
    return re.sub(r"\s+", " ", text).strip()
