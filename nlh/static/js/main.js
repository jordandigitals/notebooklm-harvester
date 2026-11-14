// Page start-up and wiring. ES module, no build step, no third-party code.
import { api } from "./api.js";
import { $, el, icon, button, show, STATUS_TEXT } from "./dom.js";
import { toast, confirmDialog, popover, dialog } from "./ui.js";
import {
  lib, initLibrary, loadLibrary, apply, renderFilters, setQuery, clearAll, applyView, openViewsMenu,
  selectAllVisible, selectNew, invertSelection, clearSelection, harvestSelection, moveFocus, toggleFocused,
  openFocused, testHooks,
} from "./library.js";
import { acctState, renderAccountList, renderAccountCard, syncScopeButtons, toggleAdd, adjustAddPanel } from "./accounts.js";
import { renderHistory, hist, historyHooks } from "./history.js";
import { renderStats } from "./stats.js";
import { playerState, togglePlay } from "./player.js";
import { renderQueue, retryItems } from "./queue.js";
import { renderSettings, applyTheme, prefs } from "./settings.js";
import { closeDrawer } from "./drawer.js";
import { encode, decode, hasState } from "./urlstate.js";

const app = { view: "library", jobId: null, settings: null };

// ------------------------------------------------------------ jobs

/** Start a job (the promise of its POST) and follow it until it ends. */
async function startJob(promise, opts = {}) {
  try {
    const job = await promise;
    if (app.jobId && opts.quiet) return job;
    app.jobId = job.id;
    refreshChrome();
    poll();
    return job;
  } catch (e) {
    toast(e.message, "bad");
    return null;
  }
}

async function poll() {
  if (!app.jobId) return;
  let job;
  try { job = await api(`/api/jobs/${app.jobId}`); } catch (e) { app.jobId = null; return; }
  renderJob(job);
  if (job.state === "queued" || job.state === "running") { setTimeout(poll, 700); return; }
  app.jobId = null;
  notifyIfAway(job);
  const kind = job.state === "done" ? "ok" : job.state === "cancelled" ? "warn" : "bad";
  toast(job.message || `${job.title}: ${STATUS_TEXT[job.state] || job.state}`, kind, 7000);
  if (job.kind === "add" && job.account_id) { lib.account = job.account_id; lib.scope = "account"; show($("add-panel"), false); }
  await reload();
  // Another job may have been queued meanwhile (Refresh all accounts).
  const st = await api("/api/state");
  const next = st.jobs.find((j) => j.state === "queued" || j.state === "running");
  if (next) { app.jobId = next.id; poll(); }
}

function renderJob(job) {
  renderQueue(job, {
    onCancel: async (j) => { try { await api(`/api/jobs/${j.id}/cancel`, { method: "POST" }); poll(); } catch (e) { toast(e.message, "bad"); } },
    onRetry: (j, failed) => startJob(retryItems(failed)),
  });
}

function notifyIfAway(job) {
  if (!app.settings || !app.settings.notifications || !document.hidden) return;
  if (job.kind !== "harvest" || (job.elapsed_s || 0) < 60) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(document.title.split(" · ")[0], { body: job.message || job.title }); } catch (e) { /* ignore */ }
}

// ------------------------------------------------------------ state and chrome

async function loadState() {
  const st = await api("/api/state");
  acctState.windows = st.windows;
  acctState.interactive = st.interactive;
  lib.demo = st.demo;
  const banners = $("banners");
  banners.replaceChildren();
  const missing = Object.entries(st.tools).filter(([, ok]) => !ok).map(([k]) => k);
  if (missing.length) banners.append(noticeBanner("bad", `Missing ${missing.join(" and ")}. Install it (see INSTALL.md), then restart the app.`));
  if (st.demo) banners.append(noticeBanner("info", "Demo mode: made-up accounts and notebooks. Nothing here touches a real Google account."));
  if (st.portable && !st.portable_ack) {
    const n = noticeBanner("warn", "Portable mode. This copy keeps its data, including your Google sign-ins, in the \"data\" folder beside the app. Do not keep it on a shared drive or a drive you might lose, and do not hand that folder to anyone.");
    n.append(button("I understand", { cls: "btn btn-sm", onClick: async () => { await api("/api/portable-ack", { method: "POST" }); n.remove(); } }));
    banners.append(n);
  }
  const running = st.jobs.find((j) => j.state === "queued" || j.state === "running");
  if (running && !app.jobId) { app.jobId = running.id; poll(); }
  return st;
}

function noticeBanner(kind, text) {
  const n = el("div", `notice notice-${kind}`);
  n.append(icon(kind === "info" ? "info" : "alert"), el("span", "grow", text));
  return n;
}

function refreshChrome() {
  hist.accountList = [...lib.accounts.values()].map((a) => ({ id: a.id, label: a.label }));
  renderAccountList();
  renderAccountCard();
  syncScopeButtons();
}

async function reload() {
  await loadState();
  await loadLibrary();
  refreshChrome();
  if (app.view === "history") renderHistory({ keepPage: true });
  if (app.view === "stats") renderStats();
  if (app.view === "settings") renderSettings();
}

function syncUrl(push = false) {
  const qs = encode(app.view);
  const url = `/${qs ? `?${qs}` : ""}`;
  if (url === location.pathname + location.search) return;
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
}

function switchView(view, push = true) {
  app.view = view;
  for (const t of document.querySelectorAll(".tab[data-view]")) {
    if (t.dataset.view === view) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  }
  show($("view-library"), view === "library");
  show($("view-history"), view === "history");
  show($("view-stats"), view === "stats");
  show($("view-settings"), view === "settings");
  if (view === "settings") renderSettings();
  if (view === "history") renderHistory();
  if (view === "stats") renderStats();
  const base = document.title.split(" · ")[0];
  const names = { history: "History", stats: "Statistics", settings: "Settings" };
  document.title = names[view] ? `${base} · ${names[view]}` : base;
  syncUrl(push);
  if (view === "library") apply({ keepScroll: true });
}

function searchHelp(anchor) {
  const box = el("div", "helplist");
  box.append(el("p", "", "Searches titles, notebook names, accounts, file names and dates. It does not search what is said in the audio."));
  const rows = [
    ["garden soil", "both words, any order, any part of a word"],
    ["\"crop rotation\"", "the exact phrase"],
    ["status:new", "new, saved, missing, unreachable, notfinished, failed"],
    ["account:work", "accounts whose name or email contains work"],
    ["notebook:garden", "notebooks whose name contains garden"],
    ["after:2026-11-01", "created after a date (also before:)"],
  ];
  for (const [code, text] of rows) {
    const p = el("p");
    p.append(el("code", "", code), document.createTextNode(` ${text}`));
    box.append(p);
  }
  popover(anchor, box, "Search help");
}

// ------------------------------------------------------------ keyboard

const KEYS = [
  ["/", "Search (also Ctrl or Cmd + K)"], ["?", "This list of shortcuts"], ["g then l", "Library"],
  ["g then h", "History"], ["g then s", "Statistics"], ["g then ,", "Settings"],
  ["j / k", "Next or previous overview"], ["x", "Select or unselect the overview"],
  ["Enter", "Open its details"], ["p", "Play or pause the player"], ["Esc", "Close details, menus and dialogs"],
];

function cheatSheet() {
  const dl = el("dl", "keys");
  for (const [k, what] of KEYS) {
    const dt = el("dt");
    for (const part of k.split(" ")) {
      if (["then", "/", "or"].includes(part)) dt.append(document.createTextNode(` ${part} `));
      else dt.append(el("kbd", "kbd", part));
    }
    dl.append(dt, el("dd", "", what));
  }
  dialog({ title: "Keyboard shortcuts", body: dl, actions: [{ label: "Close", value: true, primary: true }] });
}

let lastG = 0;
function onKey(e) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("q").focus(); $("q").select(); return; }
  if (e.key === "Escape" && document.activeElement === $("q")) { $("q").blur(); return; }
  if (e.key === "Escape" && closeDrawer()) return;
  if (typing || e.metaKey || e.ctrlKey || e.altKey || $("dlg").open) return;
  if (e.key === "/") { e.preventDefault(); $("q").focus(); return; }
  if (e.key === "?") { e.preventDefault(); cheatSheet(); return; }
  if (e.key === "p" && togglePlay()) { e.preventDefault(); return; }
  if (e.key === "g") { lastG = Date.now(); return; }
  if (Date.now() - lastG < 1000) {
    if (e.key === "l") { switchView("library"); lastG = 0; return; }
    if (e.key === "h") { switchView("history"); lastG = 0; return; }
    if (e.key === "s") { switchView("stats"); lastG = 0; return; }
    if (e.key === ",") { switchView("settings"); lastG = 0; return; }
  }
  if (app.view !== "library") return;
  if (e.key === "j") { e.preventDefault(); moveFocus(1); }
  else if (e.key === "k") { e.preventDefault(); moveFocus(-1); }
  else if (e.key === "x") { e.preventDefault(); toggleFocused(); }
  else if (e.key === "Enter" && lib.focus >= 0 && document.activeElement === $("vlist")) { e.preventDefault(); openFocused(); }
}

// ------------------------------------------------------------ start

async function init() {
  acctState.startJob = startJob;
  acctState.busy = () => app.jobId !== null;
  initLibrary();
  lib.onChange = () => syncUrl(false);
  const st = await loadState();
  app.settings = await api("/api/settings");
  lib.views = app.settings.views || [];
  hist.size = app.settings.history_page_size || 50;
  hist.saveSize = (n) => api("/api/settings", { method: "POST", body: { history_page_size: n } }).catch(() => {});
  hist.startJob = startJob;
  hist.notebookBase = st.notebook_base || "";
  prefs.version = st.version;
  applyTheme(app.settings.theme);
  acctState.defaultOutput = app.settings.default_output || "";
  lib.startJob = startJob;
  adjustAddPanel();

  const view = decode(location.search);
  if (!hasState(location.search)) {
    lib.sort = app.settings.sort || "newest";
    lib.group = app.settings.group || "none";
  }
  $("sort").value = lib.sort;
  $("group").value = lib.group;
  $("q").value = lib.query;
  show($("q-clear"), !!lib.query);
  await loadLibrary();
  if (!hasState(location.search) && app.settings.default_view) {
    const v = lib.views.find((x) => x.name === app.settings.default_view);
    if (v) applyView(v);
  }
  refreshChrome();
  switchView(view, false);

  // search box: instant, over the whole listing
  $("q").addEventListener("input", () => { show($("q-clear"), !!$("q").value); setQuery($("q").value); });
  $("q-clear").addEventListener("click", () => { $("q").value = ""; show($("q-clear"), false); setQuery(""); $("q").focus(); });
  $("q-help").addEventListener("click", (e) => searchHelp(e.currentTarget));

  for (const t of document.querySelectorAll(".tab[data-view]")) t.addEventListener("click", () => switchView(t.dataset.view));
  for (const b of document.querySelectorAll("[data-scope]")) {
    b.addEventListener("click", () => {
      lib.scope = b.dataset.scope;
      if (lib.scope === "account" && lib.account === null && lib.accounts.size) lib.account = [...lib.accounts.keys()][0];
      syncScopeButtons(); apply(); renderFilters(); renderAccountList(); renderAccountCard();
    });
  }
  const remember = (key, value) => api("/api/settings", { method: "POST", body: { [key]: value } }).catch(() => {});
  $("sort").addEventListener("change", () => { lib.sort = $("sort").value; apply({ keepScroll: true }); remember("sort", lib.sort); });
  $("group").addEventListener("change", () => { lib.group = $("group").value; lib.collapsed.clear(); apply(); remember("group", lib.group); });
  $("views-btn").addEventListener("click", (e) => openViewsMenu(e.currentTarget));
  $("sel-visible").addEventListener("click", selectAllVisible);
  $("sel-new").addEventListener("click", selectNew);
  $("sel-invert").addEventListener("click", invertSelection);
  $("sel-clear").addEventListener("click", clearSelection);
  $("sel-harvest").addEventListener("click", () => harvestSelection(startJob));
  $("filters-clear").addEventListener("click", clearAll);
  $("add-toggle").addEventListener("click", toggleAdd);
  $("signin-add").addEventListener("click", () => startJob(api("/api/accounts/signin", { method: "POST", body: { label: $("signin-label").value } }), { add: true }));
  $("sidebar-open").addEventListener("click", () => { $("sidebar").classList.add("is-open"); $("sidebar").querySelector("button")?.focus(); });
  for (const b of document.querySelectorAll(".close-sidebar")) b.addEventListener("click", () => { $("sidebar").classList.remove("is-open"); $("sidebar-open").focus(); });
  $("keys-help").addEventListener("click", cheatSheet);
  $("quit").addEventListener("click", async () => {
    if (!(await confirmDialog("Quit the harvester?", "You can start it again from its icon. Nothing is lost.", "Quit"))) return;
    try {
      await api("/api/quit", { method: "POST" });
      const box = el("main", "empty");
      box.append(el("h2", "", "The harvester has stopped"), el("p", "", "You can close this tab. Start the app again from its icon when you need it."));
      document.body.replaceChildren(box);
    } catch (err) { toast(err.message, "bad"); }
  });
  document.addEventListener("keydown", onKey);
  document.addEventListener("nlh:reload", () => reload());
  window.addEventListener("popstate", () => { const v = decode(location.search); $("q").value = lib.query; $("sort").value = lib.sort; $("group").value = lib.group; switchView(v, false); apply(); renderFilters(); refreshChrome(); });

  if (st.demo) window.__nlhTest = { ...testHooks(), history: historyHooks(), player: playerState };
  document.body.dataset.ready = "1";
}

init().catch((e) => {
  const box = el("div", "notice notice-bad");
  box.append(icon("alert"), el("span", "grow", `The page could not start: ${e.message} Reload the page; if it keeps happening, restart the app.`));
  $("banners").replaceChildren(box);
});
