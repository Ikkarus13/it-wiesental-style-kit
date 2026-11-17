// Liest in der Teams-Seite den eigenen Status und die Zahl ungelesener Meldungen
// und schickt beides an PWA-Tray (ueber background.js -> Native Messaging).
// Nichts verlaesst den Rechner.

(() => {
  "use strict";
  // Nicht doppelt laufen (deklariert + nachgeladen durch background.js)
  if (window.__pwaTrayTeams) return;
  window.__pwaTrayTeams = true;

  // Status-Woerter (Deutsch/Englisch) -> Statuscode
  const PRESENCE = [
    [/nicht st[öo]ren|do not disturb|präsentier|presenting|fokus|focusing/i, "dnd"],
    [/im anruf|in a call|in einer besprechung|in a meeting|besch[äa]ftigt|busy/i, "busy"],
    [/gleich zur[üu]ck|be right back|abwesend|away|inaktiv|inactive/i, "away"],
    [/au[ßs]er haus|out of office|abwesenheitsnotiz/i, "ooo"],
    [/offline|unsichtbar|appear offline/i, "offline"],
    [/verf[üu]gbar|available/i, "available"],
  ];

  const ME_SELECTORS = [
    '[data-tid="me-control-avatar-trigger"]',
    '[data-tid*="me-control"]',
    '#idna-me-control-avatar-trigger',
    'button[id*="me-control"]',
    '[data-tid*="avatar"][aria-label]',
  ];

  function labelsOf(el) {
    const out = [];
    const push = (s) => { if (s && s.trim()) out.push(s.trim()); };
    push(el.getAttribute("aria-label"));
    push(el.getAttribute("title"));
    for (const c of el.querySelectorAll("[aria-label],[title]")) {
      push(c.getAttribute("aria-label"));
      push(c.getAttribute("title"));
    }
    return out;
  }

  function classify(text) {
    for (const [re, code] of PRESENCE) if (re.test(text)) return code;
    return null;
  }

  // Teams liefert den eigenen Status direkt als Code (englisch) am Profilbild
  const CODES = {
    available: "available", busy: "busy", inacall: "busy", inameeting: "busy",
    donotdisturb: "dnd", presenting: "dnd", focusing: "dnd", urgentinterruptionsonly: "dnd",
    away: "away", berightback: "away", inactive: "away",
    outofoffice: "ooo", offline: "offline", invisible: "offline", presenceunknown: null,
  };

  function findPresence() {
    const own = document.querySelector('[data-tid="me-control-avatar-presence"]');
    if (own) {
      const raw = (own.getAttribute("aria-label") || "").replace(/[\s_-]/g, "").toLowerCase();
      if (raw in CODES && CODES[raw]) {
        const trig = document.querySelector('[data-tid="me-control-avatar-trigger"]');
        const t = trig ? (trig.getAttribute("aria-label") || "") : "";
        return { presence: CODES[raw], presenceText: t.replace(/^.*Status\s*/i, "") };
      }
    }
    for (const sel of ME_SELECTORS) {
      for (const el of document.querySelectorAll(sel)) {
        for (const t of labelsOf(el)) {
          const code = classify(t);
          if (code) return { presence: code, presenceText: t };
        }
      }
    }
    // Rueckfall: Profil-Knopf oben rechts ueber seinen Text finden
    for (const el of document.querySelectorAll('button[aria-label], [role="button"][aria-label]')) {
      const t = el.getAttribute("aria-label") || "";
      if (!/profil|profile|konto|account/i.test(t)) continue;
      const r = el.getBoundingClientRect();
      if (r.top > 120 || r.left < window.innerWidth / 2) continue;
      for (const s of labelsOf(el)) {
        const code = classify(s);
        if (code) return { presence: code, presenceText: s };
      }
    }
    return { presence: "unknown", presenceText: "" };
  }

  // Zahlen-Badges in der linken App-Leiste (Aktivitaet, Chat, Teams …)
  function appBarBadges() {
    const bar = document.querySelector('[data-tid="app-bar-wrapper"]');
    if (!bar) return 0;
    let sum = 0;
    for (const el of bar.querySelectorAll("*")) {
      if (el.children.length) continue;
      const t = (el.textContent || "").trim();
      const m = /^(\d{1,3})\+?$/.exec(t);
      if (m && el.offsetParent !== null) sum += parseInt(m[1], 10);
    }
    return sum;
  }

  function findUnread() {
    // 1) Seitentitel "(3) Chat | …"
    const m = /^\((\d+)\)/.exec(document.title);
    const fromTitle = m ? parseInt(m[1], 10) : 0;
    const fromBadges = appBarBadges();
    // 2) Zaehler an Aktivitaet/Chat in der App-Leiste
    let fromBar = 0;
    const seen = new Set();
    for (const el of document.querySelectorAll('[aria-label]')) {
      const t = el.getAttribute("aria-label");
      if (!/(aktivit|activity|chat)/i.test(t)) continue;
      const n = /(\d+)\s*(neue|ungelesen|unread|new|benachrichtigung|notification)/i.exec(t) ||
                /(ungelesen|unread|neue|new)[^\d]{0,20}(\d+)/i.exec(t);
      if (!n) continue;
      const num = parseInt(/\d+/.exec(n[0])[0], 10);
      const key = /aktivit|activity/i.test(t) ? "activity" : "chat";
      if (seen.has(key)) continue;
      seen.add(key);
      fromBar += num;
    }
    return Math.max(fromTitle, fromBar, fromBadges);
  }

  function send(msg) {
    try { chrome.runtime.sendMessage(msg); } catch (e) { /* Erweiterung neu geladen */ }
  }

  let last = "";
  let lastSent = 0;
  function check() {
    const p = findPresence();
    const state = { type: "status", app: "teams", unread: findUnread(),
                    presence: p.presence, presenceText: p.presenceText };
    const key = JSON.stringify(state);
    const now = Date.now();
    if (key !== last || now - lastSent > 60000) {   // Aenderung oder Lebenszeichen
      last = key;
      lastSent = now;
      send(state);
    }
  }

  // Diagnose fuer die Einrichtung: welche Beschriftungen kommen in Frage?
  function diag() {
    const items = [];
    for (const el of document.querySelectorAll("[aria-label],[title],[data-tid]")) {
      const t = (el.getAttribute("aria-label") || el.getAttribute("title") || "").trim();
      const tid = el.getAttribute("data-tid") || "";
      if (classify(t) || /\d+\s*(neue|ungelesen|unread|new)|me-control|avatar|presence|app-bar|badge/i.test(t + " " + tid)) {
        items.push(`${el.tagName.toLowerCase()} tid=${tid} label=${t}`.slice(0, 200));
      }
      if (items.length >= 60) break;
    }
    // App-Leiste im Detail (Beschriftungen und Zahlen, keine Inhalte aus Chats)
    const bar = [];
    const wrap = document.querySelector('[data-tid="app-bar-wrapper"]');
    if (wrap) {
      for (const el of wrap.querySelectorAll("*")) {
        const tid = el.getAttribute("data-tid") || "";
        const lab = el.getAttribute("aria-label") || "";
        const txt = el.children.length ? "" : (el.textContent || "").trim().slice(0, 40);
        if (tid || lab || /^\d/.test(txt)) bar.push(`${el.tagName.toLowerCase()} tid=${tid} label=${lab} text=${txt}`.slice(0, 200));
        if (bar.length >= 80) break;
      }
    }
    const p = findPresence();
    send({ type: "diag", app: "teams", title: document.title, items, bar,
           detected: { unread: findUnread(), presence: p.presence, presenceText: p.presenceText } });
  }

  new MutationObserver(() => {
    clearTimeout(check._t);
    check._t = setTimeout(check, 500);
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true,
                                          attributeFilter: ["aria-label", "title"] });
  new MutationObserver(check).observe(document.querySelector("title") || document.head,
                                      { subtree: true, childList: true, characterData: true });
  setInterval(check, 15000);
  setTimeout(check, 3000);
  setTimeout(diag, 20000);
  setInterval(diag, 10 * 60000);   // Diagnose aktuell halten (lokal, ~/.cache/pwa-tray/diag.json)
})();
