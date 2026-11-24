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
//    Add-in-Dialoge) – nur Tabs in normalen Browserfenstern zählen; Links aus
//    einem leeren Mail-Fenster von Outlook (Doppelklick) zählen wie aus Outlook;
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

// Fenster des neuen Tabs ist evtl. noch nicht fertig (Chrome legt es gerade an):
// so oft und so lange nachfragen, statt gleich aufzugeben.
const RETRIES = 5;
const RETRY_MS = 150;

// tabId → { url, http, viaKeep, ready, sourceWindowId, timer, created, tries }
const watched = new Map();

// ---------- Entscheidungsprotokoll ----------
// Die letzten Entscheidungen stehen in chrome.storage.local «log» (nur Hostnamen,
// keine Adressen) und in den Einstellungen. So lässt sich nachsehen, warum ein
// Link doch in Chrome aufging. Kein Eintrag zu einem Klick = Chrome hat der
// Erweiterung den neuen Tab gar nicht gemeldet.
const LOG_MAX = 50;
let logChain = Promise.resolve();

function hostOf(url) {
  const u = parse(url || '');
  if (!u) return '';
  if (u.protocol === 'http:' || u.protocol === 'https:') return u.hostname;
  return u.protocol === 'about:' ? u.href : u.protocol.replace(':', '');
}

function note(entry, why, url) {
  const item = { t: Date.now(), host: url === null ? '' : hostOf(url ?? (entry && entry.url)), why };
  if (entry) {
    item.ms = item.t - entry.created;
    if (entry.tries) item.tries = entry.tries;
  }
  // nacheinander schreiben, sonst überschreiben sich gleichzeitige Einträge
  logChain = logChain.then(async () => {
    const { log } = await chrome.storage.local.get('log');
    const list = Array.isArray(log) ? log : [];
    list.push(item);
    await chrome.storage.local.set({ log: list.slice(-LOG_MAX) });
  }).catch(e => console.warn('PWA link organizer: Protokoll nicht geschrieben:', e.message));
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// ---------- Mail-Fenster von Outlook ----------
// Öffnet man eine Mail per Doppelklick, öffnet Outlook ein leeres Pop-up-Fenster
// (about:blank) und schreibt die Mail selbst hinein. Das Fenster hat nie eine
// Outlook-Adresse, Links darin kämen also aus einer «fremden» Quelle. Deshalb
// merkt sich die Erweiterung solche Fenster (chrome.storage.session übersteht das
// Schlafen des Service Workers, nicht aber einen Chrome-Neustart) und behandelt
// Links darin wie Links aus Outlook – solange das Fenster leer/Outlook bleibt.
let childChain = Promise.resolve();

async function outlookChildren() {
  const { outlookChildren: list } = await chrome.storage.session.get('outlookChildren');
  return Array.isArray(list) ? list : [];
}

function updateChildren(change) {
  childChain = childChain.then(async () => {
    const list = await outlookChildren();
    const next = change(list);
    if (next) await chrome.storage.session.set({ outlookChildren: next.slice(-50) });
  }).catch(e => console.warn('PWA link organizer: Mail-Fenster nicht gemerkt:', e.message));
  return childChain;
}

const rememberChild = tabId => updateChildren(list => (list.includes(tabId) ? null : [...list, tabId]));
const forgetChild = tabId => updateChildren(list => (list.includes(tabId) ? list.filter(id => id !== tabId) : null));

// leer (about:blank) oder Outlook-Adresse
function blankOrOutlook(url) {
  const u = parse(url || 'about:blank');
  return !u || u.protocol === 'about:' || isOutlook(url);
}

// Adresse des Hauptrahmens eines Tabs – webNavigation nennt sie auch ohne Rechte
// für die Seite (chrome.tabs.get liefert url nur für Outlook-Adressen).
async function frameUrl(tabId) {
  try {
    const frame = await chrome.webNavigation.getFrame({ tabId, frameId: 0 });
    return frame ? frame.url : '';
  } catch {
    return '';
  }
}

// Kommt der Klick aus Outlook? Outlook selbst oder ein gemerktes Mail-Fenster.
async function fromOutlook(source) {
  if (isOutlook(source.url)) return true;
  if (!(await outlookChildren()).includes(source.id)) return false;
  return blankOrOutlook(await frameUrl(source.id));
}

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
  entry.timer = setTimeout(() => {
    if (watched.get(tabId) !== entry) return;
    if (entry.ready) note(entry, 'Zeit abgelaufen – bleibt in Chrome');
    unwatch(tabId);
  }, ms);
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
  const entry = { url: '', http: false, viaKeep: false, ready: false, sourceWindowId: undefined,
                  timer: 0, created: Date.now(), tries: 0 };
  arm(details.tabId, entry, BLANK_MS);
  track(details.tabId, entry, details.url);
  watched.set(details.tabId, entry);
  qualify(details.tabId, details.sourceTabId, entry);
});

// fn() liefert das Ergebnis oder undefined (= noch nicht so weit); Fehler und
// undefined werden kurz wiederholt, solange der Tab noch beobachtet wird.
async function retry(tabId, entry, fn) {
  let error = new Error('keine Antwort');
  for (let i = 0; i <= RETRIES; i++) {
    if (i) {
      entry.tries++;
      await sleep(RETRY_MS);
      if (watched.get(tabId) !== entry) return undefined; // Tab zu oder ersetzt
    }
    try {
      const value = await fn();
      if (value !== undefined) return value;
    } catch (e) {
      error = e;
    }
  }
  throw error;
}

async function qualify(tabId, sourceTabId, entry) {
  try {
    if (!(sourceTabId >= 0)) {
      note(entry, 'Quelle unbekannt – bleibt in Chrome', null);
      return unwatch(tabId);
    }
    const source = await retry(tabId, entry, () => chrome.tabs.get(sourceTabId));
    if (!source) return;
    // Ohne Rechte für die Quellseite ist source.url leer → keine Outlook-Seite
    if (!(await fromOutlook(source))) {
      const from = hostOf(await frameUrl(sourceTabId)) || '?';
      note(entry, `Quelle nicht Outlook (${from}) – bleibt in Chrome`, null);
      return unwatch(tabId);
    }
    const win = await retry(tabId, entry, async () => {
      const tab = await chrome.tabs.get(tabId);
      if (tab.windowId === undefined || tab.windowId < 0) return undefined; // noch ohne Fenster
      return chrome.windows.get(tab.windowId);
    });
    if (!win || watched.get(tabId) !== entry) return;
    if (win.type !== 'normal') {
      // Leeres oder Outlook-eigenes Fenster (Mail per Doppelklick, Pop-out):
      // Links darin sollen später auch an Edge gehen. Anmelde-/Add-in-Fenster nicht.
      const child = blankOrOutlook(entry.url);
      if (child) await rememberChild(tabId);
      note(entry, `Fenster «${win.type}» – bleibt in Chrome${child ? ', Links darin gehen an Edge' : ''}`);
      return unwatch(tabId);
    }
    entry.sourceWindowId = source.windowId;
    entry.ready = true;
    consider(tabId, entry);
  } catch (e) {
    if (watched.get(tabId) === entry) note(entry, `Prüfung fehlgeschlagen (${e.message}) – bleibt in Chrome`);
    unwatch(tabId);
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
chrome.tabs.onRemoved.addListener(tabId => {
  unwatch(tabId);
  forgetChild(tabId);
});

function consider(tabId, entry) {
  const u = parseHttp(entry.url);
  if (!u) return; // about:blank: abwarten
  if (hostMatches(u.hostname, LOGIN_HOSTS)) {
    note(entry, 'Microsoft-Anmeldung – bleibt in Chrome');
    return unwatch(tabId);
  }
  if (hostMatches(u.hostname, KEEP_HOSTS)) return; // Outlook-intern: evtl. folgt eine Weiterleitung
  // Server-Weiterleitungen dazwischen sieht die Erweiterung nicht – über Outlook und
  // die Microsoft-Anmeldung bei einer fremden Anmeldeseite gelandet: in Chrome lassen.
  if (entry.viaKeep && isSignIn(u)) {
    note(entry, 'fremde Anmeldeseite – bleibt in Chrome');
    return unwatch(tabId);
  }
  unwatch(tabId);
  handOver(tabId, entry);
}

async function handOver(tabId, entry) {
  const { url, sourceWindowId } = entry;
  let windowId;
  try {
    windowId = (await chrome.tabs.get(tabId)).windowId;
    await chrome.tabs.remove(tabId);
  } catch {
    note(entry, 'Tab schon geschlossen');
    return;
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
  if (reply && reply.ok) {
    note(entry, 'an Edge übergeben');
  } else {
    note(entry, `Edge-Fehler (${reply && reply.error}) – in Chrome geöffnet`);
    console.warn('PWA link organizer: Edge nicht erreichbar, Link bleibt in Chrome:', reply && reply.error);
    // nichts verlieren – notfalls in einem neuen Fenster
    chrome.tabs.create({ url })
      .catch(() => chrome.windows.create({ url }))
      .catch(e => console.error('PWA link organizer: Link konnte nicht geöffnet werden:', e.message));
  }
}
