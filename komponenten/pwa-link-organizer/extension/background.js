'use strict';
// PWA link organizer: Links, die die Outlook-App in Chrome in einem neuen Tab öffnen
// würde, gehen stattdessen an Microsoft Edge (über den Native-Messaging-Host).
//
// Ablauf: Outlook öffnet einen Link → Chrome legt einen Tab in einem normalen
// Browserfenster an (webNavigation.onCreatedNavigationTarget). Stammt er aus
// Outlook und ist das Ziel eine http(s)-Seite ausserhalb von Outlook und der
// Microsoft-Anmeldung, wird der Tab geschlossen und die URL an Edge gegeben.
// Outlook verarbeitet den Klick dabei selbst (Safe Links, Warnhinweise bleiben).
//
// Nicht angefasst werden:
//  - neue App-Fenster (Pop-out einer Mail) und Pop-up-Fenster (Anmeldung,
//    Add-in-Dialoge) – nur Tabs in normalen Browserfenstern zählen;
//  - Outlook-eigene Adressen und die Microsoft-Anmeldung (KEEP_HOSTS);
//  - Tabs, die leer bleiben oder per document.write gefüllt werden (Drucken).

const NATIVE_HOST = 'ch.sascha.edge_links';

// Neue Tabs aus diesen Seiten werden umgeleitet.
const OUTLOOK_HOSTS = [
  'outlook.cloud.microsoft',
  'outlook.office.com',
  'outlook.office365.com',
  'outlook.live.com',
];

// Anmeldung: Tab in Ruhe lassen und nicht weiter beobachten.
const LOGIN_HOSTS = [
  'login.microsoftonline.com',
  'login.microsoft.com',
  'login.live.com',
  'login.windows.net',
  'autologon.microsoftazuread-sso.com',
];

// Bleiben in Chrome (Outlook selbst, Anhänge/CDN, Anmeldung). Subdomains zählen mit.
const KEEP_HOSTS = [...OUTLOOK_HOSTS, ...LOGIN_HOSTS, 'office.net'];

// So lange wird ein neuer Tab beobachtet: solange er leer ist (about:blank – Outlook
// setzt das Ziel evtl. erst nach einer Prüfung) …
const BLANK_MS = 30000;
// … ab der ersten echten Adresse (Weiterleitungen über Outlook und die Anmeldung) …
const WATCH_MS = 10000;
// … und ab dem fertigen Laden einer Outlook-Seite nur noch kurz für eine Skript-
// Weiterleitung. Danach gehört der Tab dem Benutzer: Eingetipptes bleibt in Chrome.
const SETTLED_MS = 3000;

// Fremde Anmeldeseiten (ADFS, Okta …) erkennt man an den Parametern der Protokolle.
// Kommt ein Tab über Outlook/die Microsoft-Anmeldung dort an, ist es eine Anmeldung
// für Chrome (z. B. eines Anhangs) und kein Link für Edge.
function isSignIn(u) {
  const p = u.searchParams;
  return p.has('SAMLRequest') || p.has('SAMLResponse') || p.has('wtrealm')
    || (p.get('wa') || '').startsWith('wsignin')
    || (p.has('client_id') && (p.has('redirect_uri') || p.has('response_type')))
    || /\/adfs\//i.test(u.pathname);
}

// tabId → { url, http, viaKeep, ready, sourceWindowId, timer }
const watched = new Map();

function hostMatches(host, list) {
  return list.some(h => host === h || host.endsWith('.' + h));
}

function parse(url) {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

function parseHttp(url) {
  const u = parse(url || '');
  return u && (u.protocol === 'http:' || u.protocol === 'https:') ? u : null;
}

function isOutlook(url) {
  const u = parse(url || '');
  return !!u && u.protocol === 'https:' && hostMatches(u.hostname, OUTLOOK_HOSTS);
}

function unwatch(tabId) {
  const entry = watched.get(tabId);
  if (entry) {
    clearTimeout(entry.timer);
    watched.delete(tabId);
  }
}

// Beobachtung (neu) befristen
function arm(tabId, entry, ms) {
  clearTimeout(entry.timer);
  entry.timer = setTimeout(() => unwatch(tabId), ms);
}

// Neue Adresse des Tabs merken
function track(tabId, entry, url) {
  entry.url = url;
  const u = parseHttp(url);
  if (!u) return;
  if (!entry.http) {
    entry.http = true;
    arm(tabId, entry, WATCH_MS);
  }
  if (hostMatches(u.hostname, KEEP_HOSTS)) entry.viaKeep = true;
}

chrome.webNavigation.onCreatedNavigationTarget.addListener(details => {
  // Sofort eintragen, damit eine schnelle Folgenavigation (about:blank → Ziel)
  // nicht verloren geht, während unten noch geprüft wird.
  unwatch(details.tabId);
  const entry = { url: '', http: false, viaKeep: false, ready: false, sourceWindowId: undefined, timer: 0 };
  arm(details.tabId, entry, BLANK_MS);
  track(details.tabId, entry, details.url);
  watched.set(details.tabId, entry);
  qualify(details.tabId, details.sourceTabId, entry);
});

async function qualify(tabId, sourceTabId, entry) {
  try {
    const source = await chrome.tabs.get(sourceTabId);
    if (!isOutlook(source.url)) return unwatch(tabId);
    const tab = await chrome.tabs.get(tabId);
    const win = await chrome.windows.get(tab.windowId);
    if (win.type !== 'normal') return unwatch(tabId);
    if (watched.get(tabId) !== entry) return;
    entry.sourceWindowId = source.windowId;
    entry.ready = true;
    consider(tabId, entry);
  } catch {
    unwatch(tabId); // Tab schon wieder zu o. Ä.
  }
}

function onNavigation(details) {
  if (details.frameId !== 0) return;
  const entry = watched.get(details.tabId);
  if (!entry) return;
  track(details.tabId, entry, details.url);
  if (entry.ready) consider(details.tabId, entry);
}

// Outlook-Seite im Tab fertig geladen: nur noch kurz auf eine Weiterleitung warten
function onCompleted(details) {
  if (details.frameId !== 0) return;
  const entry = watched.get(details.tabId);
  const u = parseHttp(details.url);
  if (entry && u && hostMatches(u.hostname, KEEP_HOSTS)) arm(details.tabId, entry, SETTLED_MS);
}

// Klick aufs Symbol (Erweiterungsmenü in normalen Chrome-Fenstern) → Profilwahl
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.webNavigation.onBeforeNavigate.addListener(onNavigation);
chrome.webNavigation.onCommitted.addListener(onNavigation); // nach Server-Weiterleitungen
chrome.webNavigation.onCompleted.addListener(onCompleted);
chrome.tabs.onRemoved.addListener(unwatch);

function consider(tabId, entry) {
  const u = parseHttp(entry.url);
  if (!u) return; // about:blank: abwarten
  if (hostMatches(u.hostname, LOGIN_HOSTS)) return unwatch(tabId);
  if (hostMatches(u.hostname, KEEP_HOSTS)) return; // Outlook-intern: evtl. folgt eine Weiterleitung
  // Server-Weiterleitungen dazwischen sieht die Erweiterung nicht – über Outlook und
  // die Microsoft-Anmeldung bei einer fremden Anmeldeseite gelandet: in Chrome lassen.
  if (entry.viaKeep && isSignIn(u)) return unwatch(tabId);
  unwatch(tabId);
  handOver(tabId, entry.url, entry.sourceWindowId);
}

async function handOver(tabId, url, sourceWindowId) {
  let windowId;
  try {
    windowId = (await chrome.tabs.get(tabId)).windowId;
    await chrome.tabs.remove(tabId);
  } catch {
    return; // Tab inzwischen geschlossen
  }
  // Bleibt das Browserfenster offen (es hatte noch andere Tabs), steht es jetzt
  // vor Outlook – Outlook wieder nach vorn holen, danach kommt Edge darüber.
  // Nicht darauf warten: Das Fokussieren kann dauern, Edge soll sofort starten.
  if (sourceWindowId !== undefined) {
    chrome.windows.get(windowId)
      .then(() => chrome.windows.update(sourceWindowId, { focused: true }))
      .catch(() => {}); // Fenster ist mit dem Tab verschwunden – nichts zu tun.
  }
  let reply;
  try {
    const message = { url };
    // Gewähltes Edge-Profil (Einstellungen); ohne Wahl gilt config.json des Hosts.
    const { profile } = await chrome.storage.local.get('profile');
    if (typeof profile === 'string') message.profile = profile;
    reply = await chrome.runtime.sendNativeMessage(NATIVE_HOST, message);
  } catch (e) {
    reply = { ok: false, error: e.message };
  }
  if (!reply || !reply.ok) {
    console.warn('PWA link organizer: Edge nicht erreichbar, Link bleibt in Chrome:', reply && reply.error);
    // nichts verlieren – notfalls in einem neuen Fenster
    chrome.tabs.create({ url })
      .catch(() => chrome.windows.create({ url }))
      .catch(e => console.error('PWA link organizer: Link konnte nicht geöffnet werden:', e.message));
  }
}
