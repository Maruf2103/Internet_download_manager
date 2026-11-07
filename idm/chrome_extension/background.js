"use strict";
const HOST = "com.maruf2103.flow";

function bridge(message) {
  return chrome.runtime.sendNativeMessage(HOST, message).then((result) => {
    if (!result?.ok) throw new Error(result?.error || "Flow did not accept the request.");
    return result;
  });
}

async function status(message, failed = false) {
  await chrome.storage.local.set({ lastStatus: message, failed });
  await chrome.action.setBadgeText({ text: failed ? "!" : "✓" });
  await chrome.action.setBadgeBackgroundColor({ color: failed ? "#b44747" : "#087f5b" });
  await chrome.action.setTitle({ title: `Flow: ${message}` });
}

function directURL(url) {
  try { return ["http:", "https:"].includes(new URL(url).protocol); } catch { return false; }
}

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({ id: "flow-link", title: "Download link with Flow", contexts: ["link"], targetUrlPatterns: ["http://*/*", "https://*/*"] });
  chrome.contextMenus.create({ id: "flow-folder", title: "Download linked folder with Flow", contexts: ["link"], targetUrlPatterns: ["http://*/*", "https://*/*"] });
  const settings = await chrome.storage.local.get("capture");
  if (settings.capture === undefined) await chrome.storage.local.set({ capture: false });
});
chrome.contextMenus.onClicked.addListener(async (info) => {
  try {
    const response = await bridge(info.menuItemId === "flow-folder" ? { action: "folder", url: info.linkUrl } : { action: "queue", urls: [info.linkUrl] });
    await status(`${response.count} file(s) added to Flow.`);
  } catch (error) { await status(`${error.message} Install Flow 2 and its Chrome bridge first.`, true); }
});

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  bridge(message).then(reply).catch((error) => reply({ ok: false, error: error.message }));
  return true;
});

// Opt-in handoff: Chrome is cancelled only after Flow confirms queue acceptance.
// No cookies or browser history are collected. Local Flow file saves are ignored.
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  suggest();
  (async () => {
    const { capture } = await chrome.storage.local.get("capture");
    const url = item.finalUrl || item.url;
    if (!capture || !directURL(url)) return;
    if (["127.0.0.1", "localhost", "[::1]"].includes(new URL(url).hostname)) return;
    try {
      const name = (item.filename || "").split(/[\\/]/).pop();
      await bridge({ action: "queue", items: [{ url, ...(name ? { name } : {}) }] });
      try { await chrome.downloads.cancel(item.id); } catch { /* A very small file may already have finished. */ }
      await status("Download sent to Flow.");
    } catch (error) {
      await status(`Chrome kept this download: ${error.message}`, true);
    }
  })();
});
