// Leitet Meldungen der Teams-Seite an den lokalen PWA-Tray-Helfer weiter (Native Messaging).
const HOST = "ch.sascha.pwa_tray";
const TEAMS_URLS = ["https://teams.cloud.microsoft/*", "https://teams.microsoft.com/*"];

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || (msg.type !== "status" && msg.type !== "diag")) return;
  chrome.runtime.sendNativeMessage(HOST, msg, () => {
    void chrome.runtime.lastError;   // Helfer nicht installiert/gestartet -> still ignorieren
  });
});

// Nach Installation/Update/Neuladen: Skript in bereits offene Teams-Fenster nachladen,
// sonst bliebe die Seite bis zum naechsten Neuladen stumm.
async function injectIntoOpenTabs() {
  try {
    const tabs = await chrome.tabs.query({ url: TEAMS_URLS });
    for (const tab of tabs) {
      chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] })
        .catch(() => {});
    }
  } catch (e) { /* ignorieren */ }
}
chrome.runtime.onInstalled.addListener(injectIntoOpenTabs);
chrome.runtime.onStartup.addListener(injectIntoOpenTabs);
