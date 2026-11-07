"use strict";
const $ = (id) => document.getElementById(id);
async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error || "The Flow bridge could not be reached.");
  return response;
}
async function initialize() {
  const settings = await chrome.storage.local.get(["capture", "lastStatus"]);
  $("capture").checked = !!settings.capture;
  $("message").textContent = settings.lastStatus || "";
  try { const result = await send({ action: "ping" }); $("connection").textContent = `Flow ${result.version} bridge is ready`; }
  catch { $("connection").textContent = "Install or repair Flow 2 to connect the Chrome bridge."; }
}
$("send-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("send").disabled = true;
  try {
    const urls = $("links").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!urls.length) throw new Error("Enter a download link.");
    const folder = $("mode").value === "folder";
    if (folder && urls.length !== 1) throw new Error("Use one public folder URL at a time.");
    const result = await send(folder ? { action: "folder", url: urls[0] } : { action: "queue", urls });
    $("message").textContent = `${result.count} file(s) added. Open Flow to see progress.`; $("links").value = "";
  } catch (error) { $("message").textContent = error.message; }
  finally { $("send").disabled = false; }
});
$("capture").addEventListener("change", () => chrome.storage.local.set({ capture: $("capture").checked }));
$("open").addEventListener("click", async () => { try { await send({ action: "open" }); } catch (error) { $("message").textContent = error.message; } });
$("page-links").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => [...new Set([...document.querySelectorAll("a[href]")].filter((a) => a.hasAttribute("download") || /\.(?:exe|msi|msix|apk|dmg|deb|rpm|zip|7z|rar|tar|gz|pdf|docx?|xlsx?|pptx?|txt|csv|json|iso|mp4|mkv|webm|mp3|wav|flac|png|jpe?g|webp)(?:[?#]|$)/i.test(a.href)).map((a) => a.href).filter((url) => /^https?:/i.test(url)))].slice(0, 50) });
    const urls = results[0]?.result || [];
    if (!urls.length) throw new Error("No direct file links found. Right-click the download link or paste its URL instead.");
    $("links").value = urls.join("\n"); $("mode").value = "queue";
    $("message").textContent = `${urls.length} link(s) found. Review them, then download.`;
  } catch (error) { $("message").textContent = error.message; }
});
initialize();
