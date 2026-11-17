'use strict';
// Einstellungen: Edge-Profil wählen. Gespeichert in chrome.storage.local
// ("profile": Profilordner, "" = zuletzt benutztes); background.js schickt es
// bei jedem Link an den Host. Ohne gespeicherte Wahl gilt config.json des Hosts.

const NATIVE_HOST = 'ch.sascha.edge_links';
const TRY_URL = 'https://myaccount.microsoft.com/';

const list = document.getElementById('list');
const status = document.getElementById('status');
const tryButton = document.getElementById('try');

function say(text, bad = false) {
  status.textContent = text;
  status.classList.toggle('bad', bad);
}

function choice(value, name, sub, checked) {
  const label = document.createElement('label');
  label.className = 'choice';
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'profile';
  input.value = value;
  input.checked = checked;
  const n = document.createElement('span');
  n.className = 'name';
  n.textContent = name;
  const s = document.createElement('span');
  s.className = 'sub';
  s.textContent = sub;
  label.append(input, n, s);
  return label;
}

function selected() {
  const input = list.querySelector('input:checked');
  return input ? input.value : null;
}

async function load() {
  let info;
  try {
    info = await chrome.runtime.sendNativeMessage(NATIVE_HOST, { profiles: true });
  } catch (e) {
    info = { ok: false, error: e.message };
  }
  if (!info || !info.ok) {
    say(`Edge-Profile nicht lesbar: ${info && info.error}. Im Projektordner ./install.sh ausführen.`, true);
    return;
  }
  const { profile: stored } = await chrome.storage.local.get('profile');
  const current = typeof stored === 'string' ? stored : (info.default || '');
  const known = info.profiles.some(p => p.dir === current);

  for (const p of info.profiles) {
    const sub = [p.user, p.dir === info.last_used ? 'zuletzt benutzt' : '', p.dir].filter(Boolean).join(' · ');
    list.append(choice(p.dir, p.name, sub, p.dir === current));
  }
  list.append(choice('', 'Zuletzt benutztes Edge-Profil', 'Edge entscheidet selbst', current === '' || !known));
  if (current && !known) say(`Das gewählte Profil «${current}» gibt es in Edge nicht mehr.`, true);
  tryButton.disabled = false;
}

list.addEventListener('change', async () => {
  try {
    await chrome.storage.local.set({ profile: selected() });
    say('Gespeichert.');
  } catch (e) {
    say(`Nicht gespeichert: ${e.message}`, true);
  }
});

tryButton.addEventListener('click', async () => {
  let reply;
  try {
    reply = await chrome.runtime.sendNativeMessage(NATIVE_HOST, { url: TRY_URL, profile: selected() || '' });
  } catch (e) {
    reply = { ok: false, error: e.message };
  }
  say(reply && reply.ok ? 'In Edge geöffnet.' : `Edge nicht erreichbar: ${reply && reply.error}`, !(reply && reply.ok));
});

load();
