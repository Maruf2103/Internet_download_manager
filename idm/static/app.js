"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { jobs: [], filter: "all", query: "", sort: "recent", rows: new Map(), loading: false, stopped: false };
const activeStates = new Set(["queued", "downloading", "pausing", "cancelling"]);
const labels = { queued: "Queued", downloading: "Downloading", paused: "Paused", pausing: "Pausing…", cancelling: "Cancelling…", completed: "Completed", failed: "Failed", cancelled: "Cancelled" };
const filters = {
  all: () => true, active: (job) => activeStates.has(job.state), completed: (job) => job.state === "completed",
  paused: (job) => ["paused", "pausing"].includes(job.state), failed: (job) => ["failed", "cancelled"].includes(job.state),
};
const dialogOpeners = new WeakMap();

function showDialog(dialog) {
  dialogOpeners.set(dialog, document.activeElement);
  dialog.showModal();
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(svg.namespaceURI, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function size(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${Math.round(value)} B`;
  const exponent = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** exponent).toFixed(1)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`;
}

function timeLeft(job) {
  if (!job.total || !job.speed) return "Connecting…";
  const seconds = Math.max(1, Math.ceil((job.total - job.received) / job.speed));
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min left`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m left`;
}

async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, { ...options, cache: "no-store", signal: AbortSignal.timeout(path === "/api/folders/preview" ? 40000 : 15000) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}

let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 5000);
}

function makeButton(symbol, action, title) {
  const button = document.createElement("button");
  button.type = "button";
  button.append(icon(symbol));
  if (["resume", "retry"].includes(action)) button.append(document.createTextNode(action === "resume" ? "Resume" : "Retry"));
  button.dataset.action = action;
  button.title = title;
  button.setAttribute("aria-label", title);
  if (["remove", "cancel"].includes(action)) button.className = "destructive";
  return button;
}

function updateRow(row, job) {
  const set = (selector, value) => { row.querySelector(selector).textContent = value; };
  row.dataset.id = job.id;
  set(".file-name", job.name);
  row.querySelector(".file-name").title = job.name;
  set(".file-source", job.folder_name ? `${job.folder_name} / ${job.relative_path}` : new URL(job.url).hostname);
  row.querySelector(".file-source").title = job.sha256 ? `SHA-256: ${job.sha256}` : "";
  set(".file-error", job.error || "");
  const extension = job.name.includes(".") ? job.name.split(".").pop().slice(0, 4).toLowerCase() : "file";
  const type = ["zip", "gz", "rar", "7z", "tar"].includes(extension) ? "archive" : ["png", "jpg", "jpeg", "webp", "svg", "gif"].includes(extension) ? "image" : ["mp4", "mkv", "webm", "mov"].includes(extension) ? "video" : ["pdf", "doc", "docx", "txt"].includes(extension) ? "document" : "file";
  const fileIcon = row.querySelector(".file-icon");
  if (fileIcon.dataset.type !== type) {
    fileIcon.dataset.type = type;
    fileIcon.className = `file-icon ${type}`;
    fileIcon.querySelector("use").setAttribute("href", `#i-${type === "document" ? "file" : type}`);
  }
  set(".file-extension", extension.toUpperCase());
  const percent = job.total ? Math.min(100, job.received / job.total * 100) : (job.state === "completed" ? 100 : 0);
  set(".file-size", job.total !== null ? `${size(job.received)} / ${size(job.total)}` : `${size(job.received)} received`);
  set(".file-percent", job.total !== null || job.state === "completed" ? `${Math.floor(percent)}%` : "—");
  const track = row.querySelector(".progress-track");
  track.setAttribute("aria-label", `${job.name}: ${labels[job.state]}`);
  if (job.total !== null || job.state === "completed") track.setAttribute("aria-valuenow", Math.floor(percent));
  else track.removeAttribute("aria-valuenow");
  const fill = row.querySelector(".progress-fill");
  fill.style.width = `${percent}%`;
  fill.className = `progress-fill ${job.state}`;
  let detail = "";
  if (job.state === "downloading") detail = job.speed ? `${size(job.speed)}/s · ${timeLeft(job)}${job.connections > 1 ? ` · ${job.connections} connections` : ""}` : "Starting transfer…";
  if (job.state === "paused") detail = "Ready to pick up where you left off";
  if (job.state === "queued") detail = "Waiting for an available slot";
  if (job.state === "completed") detail = "Saved and ready";
  set(".file-speed", detail);
  const status = row.querySelector(".state-pill");
  status.textContent = labels[job.state] || job.state;
  status.className = `state-pill ${job.state}`;
  const actions = row.querySelector(".row-actions");
  if (actions.dataset.state === job.state) return;
  actions.dataset.state = job.state;
  actions.replaceChildren();
  if (["downloading", "queued"].includes(job.state)) {
    actions.append(makeButton("pause", "pause", `Pause ${job.name}`), makeButton("close", "cancel", `Cancel ${job.name}`));
  } else if (job.state === "paused") {
    actions.append(makeButton("play", "resume", `Resume ${job.name}`), makeButton("close", "cancel", `Cancel ${job.name}`));
  } else if (["failed", "cancelled"].includes(job.state)) {
    actions.append(makeButton("retry", "retry", `Retry ${job.name}`), makeButton("trash", "remove", `Remove ${job.name}`));
  } else if (job.state === "completed") {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/file`;
    link.textContent = "Save";
    link.title = `Save ${job.name} to your browser`;
    actions.append(link, makeButton("copy", "hash", "Copy SHA-256 checksum"), makeButton("trash", "remove", `Delete ${job.name} and remove it from history`));
  }
}

function render() {
  for (const [filter, predicate] of Object.entries(filters)) $(`#count-${filter}`).textContent = state.jobs.filter(predicate).length;
  const downloading = state.jobs.filter((job) => job.state === "downloading");
  const queued = state.jobs.filter((job) => job.state === "queued");
  const completed = state.jobs.filter(filters.completed);
  $("#stat-active").replaceChildren(document.createTextNode(`${downloading.length} `), Object.assign(document.createElement("small"), { textContent: downloading.length === 1 ? "file" : "files" }));
  $("#stat-queue").textContent = queued.length ? `${queued.length} more waiting in your queue` : (downloading.length ? "Making steady progress" : "Your queue is clear");
  const rate = size(downloading.reduce((sum, job) => sum + job.speed, 0)).split(" ");
  $("#stat-speed").replaceChildren(document.createTextNode(`${rate[0]} `), Object.assign(document.createElement("small"), { textContent: `${rate[1]}/s` }));
  $("#stat-completed").replaceChildren(document.createTextNode(`${completed.length} `), Object.assign(document.createElement("small"), { textContent: completed.length === 1 ? "file" : "files" }));
  $("#stat-size").textContent = `${size(completed.reduce((sum, job) => sum + job.received, 0))} downloaded`;
  const names = { all: "All downloads", active: "In progress", completed: "Completed", paused: "Paused", failed: "Needs attention" };
  const visible = state.jobs.filter((job) => filters[state.filter](job) && `${job.name} ${job.url}`.toLowerCase().includes(state.query));
  visible.sort((a, b) => state.sort === "name" ? a.name.localeCompare(b.name) : state.sort === "size" ? (b.total || b.received) - (a.total || a.received) : state.sort === "oldest" ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at));
  $("#list-heading").firstChild.textContent = `${names[state.filter]} `;
  $("#list-count").textContent = visible.length;
  $("#showing").textContent = `${visible.length} of ${state.jobs.length} downloads`;
  $("#pause-all").disabled = !state.jobs.some((job) => ["queued", "downloading"].includes(job.state));
  $("#resume-all").disabled = !state.jobs.some((job) => job.state === "paused");
  const empty = $("#empty");
  empty.hidden = visible.length > 0;
  empty.querySelector("h3").textContent = state.jobs.length ? "No files here. Yet." : "A clear queue. A fresh start.";
  empty.querySelector("p").textContent = state.jobs.length ? "Try a different search or filter, or add something new to your queue." : "That file you need is just a link away. Paste it above, or add a few downloads at once.";
  empty.querySelector("button span").textContent = state.jobs.length ? "New download" : "Add your first download";
  const visibleIds = new Set(visible.map((job) => job.id));
  for (const [id, row] of state.rows) if (!visibleIds.has(id)) { row.remove(); state.rows.delete(id); }
  for (const job of visible) {
    let row = state.rows.get(job.id);
    if (!row) { row = $("#download-template").content.firstElementChild.cloneNode(true); state.rows.set(job.id, row); }
    updateRow(row, job);
  }
  const list = $("#download-list");
  visible.forEach((job, index) => {
    const row = state.rows.get(job.id);
    if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
  });
  document.title = downloading.length ? `↓ ${downloading.length} active — Flow` : "Flow — Download Manager";
}

async function refresh() {
  if (state.loading || state.stopped) return;
  state.loading = true;
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
    if (document.activeElement !== $("#connections")) $("#connections").value = String(data.connections || 1);
    $("#worker-count").textContent = data.workers;
    $("#connection").replaceChildren(Object.assign(document.createElement("span"), { className: "status-dot" }), document.createTextNode(" Connected"));
    $("#connection").removeAttribute("title");
    render();
  } catch (error) { $("#connection").textContent = "Reconnecting…"; $("#connection").title = error.message; }
  finally { state.loading = false; }
}

function validateLinks(urls) {
  if (!urls.length || urls.length > 50) throw new Error("Add between 1 and 50 links.");
  for (const url of urls) {
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error("Each link must be a valid HTTP or HTTPS URL."); }
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error("Use HTTP(S) links without embedded usernames or passwords.");
  }
}

async function addLinks(urls) {
  validateLinks(urls);
  await api("/api/jobs", { urls });
  toast(`${urls.length} download${urls.length === 1 ? "" : "s"} added to your queue.`);
  await refresh();
}

function openAddDialog() { $("#form-error").textContent = ""; showDialog($("#add-dialog")); $("#urls").focus(); }

document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((item) => {
    const selected = item.dataset.filter === state.filter;
    item.classList.toggle("selected", selected);
    if (selected) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  render();
}));
$("#search").addEventListener("input", (event) => { state.query = event.target.value.toLowerCase(); render(); });
$("#sort").addEventListener("change", (event) => { state.sort = event.target.value; render(); });
document.querySelectorAll("[data-open-add]").forEach((button) => button.addEventListener("click", openAddDialog));
for (const id of ["#close-dialog", "#cancel-dialog"]) $(id).addEventListener("click", () => $("#add-dialog").close());
$("#add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const urls = $("#urls").value.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
  $("#form-error").textContent = "";
  $("#add-submit").disabled = true;
  try { await addLinks(urls); $("#urls").value = ""; $("#add-dialog").close(); }
  catch (error) { $("#form-error").textContent = error.message; }
  finally { $("#add-submit").disabled = false; }
});
$("#quick-add").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#quick-error").textContent = "";
  $("#quick-submit").disabled = true;
  try { await addLinks([$("#quick-url").value.trim()]); $("#quick-url").value = ""; }
  catch (error) { $("#quick-error").textContent = error.message; }
  finally { $("#quick-submit").disabled = false; }
});
$("#paste-url").addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (text.includes("\n")) { openAddDialog(); $("#urls").value = text; }
    else { $("#quick-url").value = text; $("#quick-url").focus(); }
  } catch { toast("Paste your link in the field using Ctrl+V or your browser’s paste command."); $("#quick-url").focus(); }
});

function askConfirmation({ title, description, label, quit = false }) {
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  $("#confirm-submit").textContent = label;
  dialog.querySelector(".dialog-heading .dialog-icon use").setAttribute("href", quit ? "#i-power" : "#i-trash");
  dialog.querySelector(".dialog-footer [data-close-confirm]").textContent = quit ? "Keep running" : "Keep it";
  dialog.returnValue = "";
  showDialog(dialog);
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true }));
}
$("#confirm-form").addEventListener("submit", (event) => { event.preventDefault(); $("#confirm-dialog").close("yes"); });
document.querySelectorAll("[data-close-confirm]").forEach((button) => button.addEventListener("click", () => $("#confirm-dialog").close()));
// Use dialog bounds: clicking the dialog's own padding should not dismiss it.
for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("click", (event) => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
});
function restoreDialogFocus(dialog) {
  // Native focus restoration can leave focus in a closed modal when its
  // opener was a row button removed by the next queue refresh.
  if (document.activeElement === document.body || dialog.contains(document.activeElement)) {
    const opener = dialogOpeners.get(dialog);
    if (opener && opener !== document.body && opener.isConnected && opener.getClientRects().length) opener.focus();
    else $(".page-heading [data-open-add]").focus();
  }
}
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("close", () => restoreDialogFocus(dialog));
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dialog.close();
    restoreDialogFocus(dialog);
  });
}

$("#download-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled) return;
  const id = button.closest(".download-row").dataset.id;
  const job = state.jobs.find((item) => item.id === id);
  const action = button.dataset.action;
  if (!job) return;
  if (action === "remove" && !await askConfirmation({ title: job.state === "completed" ? "Delete this download?" : "Remove this download?", description: `“${job.name}” and its local ${job.state === "completed" ? "file" : "partial data"} will be deleted from this machine and your history. This cannot be undone.`, label: "Delete download" })) return;
  button.disabled = true;
  try {
    if (action === "hash") { await navigator.clipboard.writeText(job.sha256); toast("SHA-256 checksum copied."); }
    else { await api(`/api/jobs/${id}/${action}`, {}); await refresh(); }
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
});
for (const action of ["pause-all", "resume-all"]) $(`#${action}`).addEventListener("click", async () => {
  try { await api(`/api/actions/${action}`, {}); await refresh(); } catch (error) { toast(error.message); }
});
for (const button of [$("#quit"), $("#quit-top")]) button.addEventListener("click", async () => {
  if (!await askConfirmation({ title: "All done for now?", description: "Flow will close and active transfers will be paused. Your files and history stay right here. Open Flow again to pick up where you left off.", label: "Quit Flow", quit: true })) return;
  try {
    await api("/api/shutdown", {});
    state.stopped = true;
    $("#connection").textContent = "Flow is closed";
    document.querySelectorAll("button").forEach((button) => { if (button.id !== "theme") button.disabled = true; });
    toast("Flow is closed. You can close this tab and open Flow again whenever you need it.");
  } catch (error) { toast(error.message); }
});
try {
  const stored = localStorage.getItem("flow-theme");
  document.documentElement.dataset.theme = ["light", "dark"].includes(stored) ? stored : "light";
} catch { /* Optional preference storage. */ }
$("#theme").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  $("#theme").setAttribute("aria-pressed", theme === "dark");
  try { localStorage.setItem("flow-theme", theme); } catch { /* Browsing works without storage. */ }
});
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || state.stopped || $("dialog[open]") || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
  if (event.key === "/") { event.preventDefault(); $("#search").focus(); }
  if (event.key.toLowerCase() === "n") { event.preventDefault(); openAddDialog(); }
});
let folderPreview = null;
let folderGeneration = 0;
$("#open-folder").addEventListener("click", () => { folderGeneration++; folderPreview = null; $("#folder-submit").disabled = false; $("#folder-preview").hidden = true; $("#folder-error").textContent = ""; $("#folder-submit").textContent = "Preview files"; showDialog($("#folder-dialog")); $("#folder-url").focus(); });
document.querySelectorAll("[data-close-folder]").forEach((button) => button.addEventListener("click", () => $("#folder-dialog").close()));
$("#folder-dialog").addEventListener("close", () => { folderGeneration++; });
$("#folder-url").addEventListener("input", () => { folderGeneration++; folderPreview = null; $("#folder-submit").disabled = false; $("#folder-preview").hidden = true; $("#folder-submit").textContent = "Preview files"; });
$("#folder-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const generation = ++folderGeneration; $("#folder-submit").disabled = true; $("#folder-error").textContent = "";
  try {
    if (!folderPreview) {
      const preview = await api("/api/folders/preview", { url: $("#folder-url").value.trim() });
      if (generation !== folderGeneration) return;
      folderPreview = preview;
      $("#folder-summary").textContent = `${folderPreview.name}: ${folderPreview.files.length} files`;
      $("#folder-files").replaceChildren(...folderPreview.files.map((file) => Object.assign(document.createElement("li"), { textContent: file.path })));
      $("#folder-preview").hidden = false; $("#folder-submit").textContent = "Download folder";
    } else {
      const result = await api("/api/jobs", { items: folderPreview.files, folder_name: folderPreview.name });
      $("#folder-dialog").close(); toast(`${result.jobs.length} folder files added.`); await refresh();
    }
  } catch (error) { if (generation === folderGeneration) $("#folder-error").textContent = error.message; }
  finally { if (generation === folderGeneration) $("#folder-submit").disabled = false; }
});
$("#manifest-file").addEventListener("change", async (event) => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 1024 * 1024) throw new Error("Manifest must be smaller than 1 MB.");
    const data = JSON.parse(await file.text());
    if (!Array.isArray(data.files)) throw new Error("Use a JSON object with name and files: [{url, path}].");
    const result = await api("/api/jobs", { items: data.files, folder_name: data.name || "Imported folder" });
    toast(`${result.jobs.length} manifest files added.`); await refresh();
  } catch (error) { toast(error.message); }
  finally { event.target.value = ""; }
});
$("#connections").addEventListener("change", async (event) => {
  try { await api("/api/settings", { connections: Number(event.target.value) }); toast("Connection limit saved. Applies to the next transfer or resume."); }
  catch (error) { toast(error.message); } await refresh();
});
$("#open-chrome").addEventListener("click", () => showDialog($("#chrome-dialog")));
$("#close-chrome").addEventListener("click", () => $("#chrome-dialog").close());
refresh();
setInterval(refresh, 800);
