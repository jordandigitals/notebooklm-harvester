// Page start-up and wiring. ES module, no build step, no third-party code.
import { api } from "./api.js";
import { $, el, icon, button, show, STATUS_TEXT, role } from "./dom.js";
import { toast, confirmDialog, popover, dialog } from "./ui.js";
import {
  lib, initLibrary, loadLibrary, apply, renderFilters, setQuery, clearAll, applyView, openViewsMenu,
  selectAllVisible, selectNew, invertSelection, clearSelection, harvestSelection, moveFocus, toggleFocused,
  openFocused, testHooks,
} from "./library.js";
import { acctState, renderAccountList, renderAccountCard, syncScopeButtons, toggleAdd, adjustAddPanel, loadChromeProfiles, toggleChromeRoute, continueWithGoogle } from "./accounts.js";
import { renderHistory, hist, historyHooks } from "./history.js";
import { renderStats } from "./stats.js";
import { renderStudio, studioHooks } from "./studio.js";
import { playerState, togglePlay, savePositionNow, carryOverPositions } from "./player.js";
import { renderQueue, retryItems, setQueuePrefs } from "./queue.js";
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
  role.phone = st.role === "phone";
  document.body.classList.toggle("is-phone", role.phone);
  acctState.windows = st.windows;
  acctState.interactive = st.interactive;
  lib.demo = st.demo;
  const banners = $("banners");
  // Tool and demo banners come from the server inside the page (no shift); only add the rest.
  banners.querySelector(".portable-banner")?.remove();
  banners.querySelector(".window-banner")?.remove();
  prefs.appWindow = !!st.app_window;
  prefs.windows = !!st.windows;
  prefs.windowCapable = !!st.window_capable;
  // D-035: the app window could not open, so the page is in the browser. One plain line, dismissable.
  if (st.window_note && !role.phone && !sessionStorageGet("nlh-window-note-seen")) {
    const n = noticeBanner("info window-banner", st.window_note);
    n.append(button("OK", { cls: "btn btn-sm", onClick: () => { sessionStorageSet("nlh-window-note-seen", "1"); n.remove(); } }));
    banners.append(n);
  }
  if (st.portable && !st.portable_ack && !role.phone) {
    const n = noticeBanner("warn portable-banner", "Portable mode. This copy keeps its data, including your Google sign-ins, in the \"data\" folder beside the app. Do not keep it on a shared drive or a drive you might lose, and do not hand that folder to anyone.");
    n.append(button("I understand", { cls: "btn btn-sm", onClick: async () => { await api("/api/portable-ack", { method: "POST" }); n.remove(); } }));
    banners.append(n);
  }
  const running = st.jobs.find((j) => j.state === "queued" || j.state === "running");
  if (running && !app.jobId) { app.jobId = running.id; poll(); }
  return st;
}

// The search box placeholder: the longest wording that fits the box, cut only at a word, never mid word
// ("...accounts, fil" at the app window's first width). The label and description keep the full text.
const Q_PLACEHOLDERS = ["Search titles, notebooks, accounts, files", "Search titles, notebooks, accounts", "Search titles, notebooks", "Search"];
let measureCtx = null;
// WebKit (Safari, the Mac app window) keeps room inside a search box for its clear button even while the box
// is empty, so a wording that fits the padded box was still cut ("account" + half an "s" at 1280 in the Mac
// window, D-035 Mac). Chromium (Chrome, Edge, WebView2) does the same with a smaller button: at 320 px
// "Search titles, notebooks" lost its "s" with 4 px to spare.
const IS_WEBKIT = /AppleWebKit/.test(navigator.userAgent) && !/(Chrome|Chromium|Edg)\//.test(navigator.userAgent);
const SEARCH_CLEAR_ROOM = IS_WEBKIT ? 28 : 20;

/** Pick the placeholder that fits the search box now. */
export function fitPlaceholder() {
  const q = $("q");
  const box = q ? q.getBoundingClientRect().width : 0;
  if (!box) return;
  const cs = getComputedStyle(q);
  // The rendered width, minus borders and padding (an input's clientWidth already leaves the padding out
  // in Chromium, so it cannot be used here).
  const room = box - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth)
    - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 4 - SEARCH_CLEAR_ROOM;
  measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
  measureCtx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  q.placeholder = Q_PLACEHOLDERS.find((t) => measureCtx.measureText(t).width <= room) || Q_PLACEHOLDERS[Q_PLACEHOLDERS.length - 1];
}

/** sessionStorage that never throws (private windows can refuse it). */
function sessionStorageGet(key) { try { return sessionStorage.getItem(key); } catch (e) { return null; } }
function sessionStorageSet(key, value) { try { sessionStorage.setItem(key, value); } catch (e) { /* ignore */ } }

/** Called by the app window when the person quits during a harvest (D-035). */
window.__nlhClosing = () => {
  const banners = $("banners");
  banners.querySelector(".closing-banner")?.remove();
  banners.append(noticeBanner("warn closing-banner", "Finishing the current file, then closing."));
  toast("Finishing the current file, then closing.", "warn", 600000);
};

/** Called by the Mac app window when a download (History export) was saved: WKWebView shows no bar. */
window.__nlhSaved = (text) => { toast(String(text), "ok", 7000); };

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
  if (app.view === "studio") renderStudio();
}

function syncUrl(push = false) {
  const qs = encode(app.view);
  const url = `/${qs ? `?${qs}` : ""}`;
  if (url === location.pathname + location.search) return;
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
}

function switchView(view, push = true) {
  if (role.phone && (view === "studio" || view === "settings")) view = "library"; // laptop only
  app.view = view;
  for (const t of document.querySelectorAll(".tab[data-view]")) {
    if (t.dataset.view === view) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  }
  show($("view-library"), view === "library");
  show($("view-history"), view === "history");
  show($("view-stats"), view === "stats");
  show($("view-studio"), view === "studio");
  show($("view-settings"), view === "settings");
  if (view === "settings") renderSettings();
  if (view === "history") renderHistory();
  if (view === "stats") renderStats();
  if (view === "studio") renderStudio();
  const base = document.title.split(" · ")[0];
  const names = { history: "History", stats: "Statistics", settings: "Settings", studio: "Studio" };
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
  ["g then h", "History"], ["g then t", "Studio"], ["g then s", "Statistics"], ["g then ,", "Settings"],
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
    if (e.key === "t") { switchView("studio"); lastG = 0; return; }
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
  // D-032: the account card's More part and the harvest Details stay as the person left them.
  const rememberFlag = (key) => (value) => api("/api/settings", { method: "POST", body: { [key]: value } }).catch(() => {});
  acctState.cardOpen = Boolean(app.settings.account_card_open);
  acctState.saveCardOpen = rememberFlag("account_card_open");
  setQueuePrefs(app.settings.job_details_open, rememberFlag("job_details_open"));
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
  carryOverPositions(); // resume playback: one time, from this browser's older store (not awaited)
  if (!hasState(location.search) && app.settings.default_view) {
    const v = lib.views.find((x) => x.name === app.settings.default_view);
    if (v) applyView(v);
  }
  refreshChrome();
  switchView(view, false);

  // search box: instant, over the whole listing
  $("q").addEventListener("input", () => { show($("q-clear"), !!$("q").value); setQuery($("q").value); });
  fitPlaceholder();
  if (window.ResizeObserver) new ResizeObserver(() => fitPlaceholder()).observe($("q"));
  else window.addEventListener("resize", fitPlaceholder);
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
  $("chrome-refresh").addEventListener("click", loadChromeProfiles);
  $("signin-add").addEventListener("click", continueWithGoogle);
  $("chrome-toggle").addEventListener("click", toggleChromeRoute);
  $("sidebar-open").addEventListener("click", () => { $("sidebar").classList.add("is-open"); $("sidebar").querySelector("button")?.focus(); });
  for (const b of document.querySelectorAll(".close-sidebar")) b.addEventListener("click", () => { $("sidebar").classList.remove("is-open"); $("sidebar-open").focus(); });
  $("keys-help").addEventListener("click", cheatSheet);
  $("quit").addEventListener("click", async () => {
    if (!(await confirmDialog("Quit the harvester?", "You can start it again from its icon. Nothing is lost.", "Quit"))) return;
    try {
      await savePositionNow(); // resume playback: keep where the listener was (at most 1.5 s)
      const res = await api("/api/quit", { method: "POST" });
      // In the app window the window closes by itself; this text shows only if it cannot.
      const where = res && res.window ? "window" : "tab";
      const box = el("main", "empty");
      box.append(el("h2", "", "The harvester has stopped"), el("p", "", `You can close this ${where}. Start the app again from its icon when you need it.`));
      document.body.replaceChildren(box);
    } catch (err) { toast(err.message, "bad"); }
  });
  document.addEventListener("keydown", onKey);
  document.addEventListener("nlh:reload", () => reload());
  window.addEventListener("popstate", () => { const v = decode(location.search); $("q").value = lib.query; $("sort").value = lib.sort; $("group").value = lib.group; switchView(v, false); apply(); renderFilters(); refreshChrome(); });

  if (st.demo) window.__nlhTest = { ...testHooks(), history: historyHooks(), player: playerState, studio: studioHooks() };
  document.querySelector(".app").classList.remove("is-loading");
  document.body.dataset.ready = "1";
}

init().catch((e) => {
  document.querySelector(".app")?.classList.remove("is-loading");
  const box = el("div", "notice notice-bad");
  box.append(icon("alert"), el("span", "grow", `The page could not start: ${e.message} Reload the page; if it keeps happening, restart the app.`));
  $("banners").replaceChildren(box);
});
