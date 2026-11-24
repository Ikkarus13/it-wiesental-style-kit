#!/usr/bin/env node
// Test für extension/background.js ohne Chrome: ein nachgebautes chrome-Objekt
// spielt Fälle durch, die der E2E-Test nicht auslösen kann (Fenster des neuen
// Tabs noch nicht fertig, Quelle kurz nicht abrufbar, Mail-Fenster weitergeleitet).
//
//     node tests/test_background.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const CODE = fs.readFileSync(path.join(__dirname, '..', 'extension', 'background.js'), 'utf8');

// Tab 1 = Outlook (App-Fenster 10); neue Tabs bekommen Fenster 20 (normal),
// 30 (popup) usw. je nach Fall.
function load({ targetWindowAfter = 0, sourceFailures = 0, windows = {}, tabs = {}, frames = {} } = {}) {
  const listeners = {};
  const ev = name => ({ addListener: fn => { listeners[name] = fn; } });
  const store = {};
  const session = {};
  const calls = { removed: [], native: [], created: [] };
  const allTabs = { 1: { id: 1, url: 'https://outlook.cloud.microsoft/mail/inbox', windowId: 10 },
                    2: { id: 2, windowId: 20 }, ...tabs };
  const allWindows = { 10: 'app', 20: 'normal', 30: 'popup', ...windows };
  let targetGets = 0;
  let sourceGets = 0;
  const chrome = {
    webNavigation: {
      onCreatedNavigationTarget: ev('created'), onBeforeNavigate: ev('before'),
      onCommitted: ev('committed'), onCompleted: ev('completed'),
      async getFrame({ tabId }) { return tabId in frames ? { url: frames[tabId] } : null; },
    },
    tabs: {
      onRemoved: ev('removed'),
      async get(id) {
        if (id === 1 && sourceGets++ < sourceFailures) throw new Error('No tab with id: 1.');
        const tab = allTabs[id];
        if (!tab) throw new Error(`No tab with id: ${id}.`);
        if (id === 2 && targetGets++ < targetWindowAfter) return { ...tab, windowId: -1 };
        return tab;
      },
      async remove(id) { calls.removed.push(id); },
      async create(props) { calls.created.push(props.url); return {}; },
    },
    windows: {
      async get(id) {
        if (!(id in allWindows)) throw new Error(`No window with id: ${id}.`);
        return { id, type: allWindows[id] };
      },
      async update() { return {}; },
      async create(props) { calls.created.push(props.url); return {}; },
    },
    action: { onClicked: ev('action') },
    runtime: {
      openOptionsPage() {},
      async sendNativeMessage(host, msg) { calls.native.push(msg); return { ok: true }; },
    },
    storage: {
      local: {
        async get(key) { return { [key]: store[key] }; },
        async set(obj) { Object.assign(store, obj); },
      },
      session: {
        async get(key) { return { [key]: session[key] }; },
        async set(obj) { Object.assign(session, obj); },
      },
    },
  };
  vm.runInNewContext(CODE, { chrome, console, URL, Date, Promise, setTimeout, clearTimeout });
  return { listeners, store, session, calls };
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const url = 'https://www.example.org/link';

async function click(env, { tabId = 2, sourceTabId = 1, target = url } = {}) {
  env.listeners.created({ tabId, sourceTabId, url: target });
  env.listeners.before({ tabId, frameId: 0, url: target });
  await wait(1200);
}

// Outlook öffnet ein leeres Mail-Fenster (Tab 3 in Pop-up-Fenster 30)
async function openMailWindow(env, first = 'about:blank') {
  await click(env, { tabId: 3, target: first });
}

const MAIL = { tabs: { 3: { id: 3, windowId: 30 } } };
const last = env => env.store.log[env.store.log.length - 1];

const cases = {
  async 'Fenster erst beim 3. Nachfragen da → an Edge, 2× nachgefragt'() {
    const env = load({ targetWindowAfter: 2 });
    await click(env);
    assert.deepStrictEqual(env.calls.removed, [2]);
    assert.deepStrictEqual(env.calls.native.map(m => m.url), [url]);
    const [entry] = env.store.log;
    assert.strictEqual(entry.why, 'an Edge übergeben');
    assert.strictEqual(entry.host, 'www.example.org');
    assert.strictEqual(entry.tries, 2);
  },
  async 'Fenster nie da → Prüfung fehlgeschlagen, Tab bleibt'() {
    const env = load({ targetWindowAfter: 99 });
    await click(env);
    assert.deepStrictEqual(env.calls.removed, []);
    assert.deepStrictEqual(env.calls.native, []);
    assert.match(env.store.log[0].why, /^Prüfung fehlgeschlagen/);
  },
  async 'Quelle kurz nicht abrufbar → trotzdem an Edge'() {
    const env = load({ sourceFailures: 1 });
    await click(env);
    assert.deepStrictEqual(env.calls.native.map(m => m.url), [url]);
    assert.strictEqual(env.store.log[0].tries, 1);
  },
  async 'Pop-up-Fenster → bleibt, Grund im Protokoll'() {
    const env = load({ windows: { 20: 'popup' } });
    await click(env);
    assert.deepStrictEqual(env.calls.native, []);
    assert.match(env.store.log[0].why, /^Fenster «popup» – bleibt in Chrome/);
  },
  async 'Tab während des Nachfragens geschlossen → kein Eintrag'() {
    const env = load({ targetWindowAfter: 99 });
    env.listeners.created({ tabId: 2, sourceTabId: 1, url });
    await wait(200);
    env.listeners.removed(2);
    await wait(1000);
    assert.deepStrictEqual(env.calls.native, []);
    assert.strictEqual(env.store.log, undefined);
  },
  async 'Protokoll hält höchstens 50 Einträge, ohne Pfade'() {
    const env = load({ windows: { 20: 'popup' } });
    for (let i = 0; i < 60; i++) {
      env.listeners.created({ tabId: 2, sourceTabId: 1, url: `${url}/${i}?geheim=1` });
      await wait(5);
    }
    await wait(600);
    assert.strictEqual(env.store.log.length, 50);
    assert.ok(env.store.log.every(e => e.host === 'www.example.org'));
  },
  async 'Mail per Doppelklick (leeres Fenster) → Link darin geht an Edge'() {
    const env = load({ ...MAIL, frames: { 3: 'about:blank' } });
    await openMailWindow(env);
    assert.deepStrictEqual([...env.session.outlookChildren], [3]);
    assert.match(env.store.log[0].why, /Links darin gehen an Edge/);
    await click(env, { sourceTabId: 3 });
    assert.deepStrictEqual(env.calls.native.map(m => m.url), [url]);
    assert.strictEqual(last(env).why, 'an Edge übergeben');
  },
  async 'Mail-Fenster zu einer fremden Seite weitergegangen → Link bleibt'() {
    const env = load({ ...MAIL, frames: { 3: 'https://addin.example/dialog' } });
    await openMailWindow(env);
    await click(env, { sourceTabId: 3 });
    assert.deepStrictEqual(env.calls.native, []);
    assert.strictEqual(last(env).why, 'Quelle nicht Outlook (addin.example) – bleibt in Chrome');
  },
  async 'Anmelde-Pop-up wird nicht gemerkt → Link daraus bleibt'() {
    const env = load({ ...MAIL, frames: { 3: 'https://login.microsoftonline.com/x' } });
    await openMailWindow(env, 'https://login.microsoftonline.com/x');
    assert.strictEqual(env.session.outlookChildren, undefined);
    await click(env, { sourceTabId: 3 });
    assert.deepStrictEqual(env.calls.native, []);
  },
  async 'Mail-Fenster geschlossen → vergessen'() {
    const env = load({ ...MAIL, frames: { 3: 'about:blank' } });
    await openMailWindow(env);
    env.listeners.removed(3);
    await wait(50);
    assert.deepStrictEqual([...env.session.outlookChildren], []);
  },
  async 'Link aus fremder Seite → bleibt, Quelle im Protokoll'() {
    const env = load({ tabs: { 5: { id: 5, windowId: 20 } }, frames: { 5: 'https://www.github.com/x' } });
    await click(env, { sourceTabId: 5 });
    assert.deepStrictEqual(env.calls.native, []);
    assert.strictEqual(env.store.log[0].why, 'Quelle nicht Outlook (www.github.com) – bleibt in Chrome');
    assert.strictEqual(env.store.log[0].host, '');
  },
};

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(cases)) {
    try {
      await fn();
      console.log(`  OK     ${name}`);
    } catch (e) {
      failed++;
      console.log(`  FEHLER ${name}: ${e.message}`);
    }
  }
  console.log(failed ? `FEHLGESCHLAGEN: ${failed}` : 'ALLES OK');
  process.exit(failed ? 1 : 0);
})();
