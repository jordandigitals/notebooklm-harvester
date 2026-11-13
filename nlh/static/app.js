// NotebookLM Harvester page logic. Plain JavaScript, no build step.
// All text from NotebookLM is inserted with textContent, never as HTML.
"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const STATUS_TEXT = {
  ok: "Signed in", needs_sign_in: "Sign in again", library_error: "Library error", unknown: "Not checked",
  new: "New", saved: "Saved", saved_but_missing: "Saved, file missing", in_progress: "Not finished",
  queued: "Waiting", running: "Working", done: "Done", failed: "Problem",
};

const state = {
  accounts: [], selected: null, jobId: null, library: null, pickerPath: "", pickerParent: null,
  autoLoaded: new Set(),
};

// ------------------------------------------------------------ helpers

/** Call the local API with the launch token. */
async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.method || "GET",
    headers: { "X-NLH-Token": TOKEN, "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) throw new Error(data.error || "The request failed.");
  return data;
}

/** Shorthand for getElementById. */
const $ = (id) => document.getElementById(id);

/** Make an element with a class and text. */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/** A status pill. */
function pill(status) {
  return el("span", `pill pill-${status}`, STATUS_TEXT[status] || status);
}

/** Show or hide an element. */
function show(node, visible) { node.classList.toggle("is-hidden", !visible); }

/** Format an ISO date as a short local date. */
function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Format seconds as h:mm:ss or m:ss. */
function duration(s) {
  const t = Math.round(s), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function busy() { return state.jobId !== null; }

function current() { return state.accounts.find((a) => a.id === state.selected) || null; }

// ------------------------------------------------------------ accounts

async function loadState() {
  const data = await api("/api/state");
  state.accounts = data.accounts;
  const missing = Object.entries(data.tools).filter(([, ok]) => !ok).map(([k]) => k);
  $("tools-warning").textContent = missing.length
    ? `Missing ${missing.join(" and ")}. Install it (see INSTALL.md), then restart the app.` : "";
  show($("tools-warning"), missing.length > 0);
  show($("portable-warning"), data.portable && !data.portable_ack);
  if (data.windows) windowsAddPanel(data.interactive);
  if (!data.interactive) {
    $("signin-hint").textContent =
      "This copy adds accounts from Chrome only. Sign in to the account in Chrome first, then pick it above.";
    show($("signin-row"), false);
  }
  if (state.selected === null && state.accounts.length) state.selected = state.accounts[0].id;
  renderAccounts();
  renderDetail();
}

// On Windows the fresh sign-in comes first and is recommended (D-022): it needs no Chrome
// lock and trips no Windows Security alert. The Chrome route follows, for a quick start.
function windowsAddPanel(interactive) {
  if (interactive) {
    $("add-panel").insertBefore($("add-signin"), $("add-chrome"));
    $("signin-head").textContent = "Sign in to Google (recommended)";
    $("signin-hint").textContent =
      "Opens a blank Chrome window that looks like a guest profile. That is expected: it is the harvester's own private window, separate from your Chrome. Sign in there once and it remembers you; your normal Chrome stays open and untouched.";
  }
  $("chrome-head").textContent = "Use a Chrome profile (quick start)";
  $("chrome-hint").textContent =
    "Copies the Google sign-in from your Chrome. Quit Chrome completely first (also its icon near the clock). " +
    "Windows Security may report the reader as \"Trojan InfoStealer\" because it reads Chrome's saved sign-in; " +
    "it only reads the Google sign-in cookies it needs, on this computer. To allow it: Windows Security, " +
    "Virus and threat protection, Protection history, select the entry, Actions, Allow on device.";
}

function renderAccounts() {
  const list = $("account-list");
  list.replaceChildren();
  for (const a of state.accounts) {
    const li = el("li");
    const btn = el("button", "account" + (a.id === state.selected ? " is-selected" : ""));
    btn.type = "button";
    const name = el("span");
    name.append(el("span", "account-name", a.label), el("span", "account-sub", a.email || ""));
    btn.append(name, pill(a.status));
    btn.addEventListener("click", () => selectAccount(a.id));
    li.append(btn);
    list.append(li);
  }
}

async function selectAccount(id) {
  state.selected = id;
  state.library = null;
  renderAccounts();
  renderDetail();
  await loadLibrary();
}

function renderDetail() {
  const a = current();
  show($("no-account"), !a);
  show($("account-detail"), !!a);
  if (!a) return;
  if (document.activeElement !== $("acct-label")) $("acct-label").value = a.label;
  $("acct-email").textContent = [a.email, a.chrome_profile ? `Chrome ${a.chrome_profile}` : "Signed in directly"]
    .filter(Boolean).join(" · ");
  const st = $("acct-status");
  st.className = `pill pill-${a.status}`;
  st.textContent = STATUS_TEXT[a.status] || a.status;
  show($("btn-signin"), a.status !== "ok");
  $("out-dir").textContent = a.output_dir || "No folder chosen";
  show($("out-warning"), !!a.long_folder);
  show($("btn-open-out"), !!a.output_dir);
  show($("btn-scan"), !!a.output_dir);
  for (const id of ["btn-check", "btn-signin", "btn-refresh", "btn-harvest-new", "btn-harvest-sel", "btn-choose", "btn-scan"]) {
    $(id).disabled = busy();
  }
  $("btn-harvest-new").disabled = busy() || !a.output_dir || !state.library;
  $("btn-harvest-sel").disabled = busy() || !a.output_dir || !state.library;
}

async function saveLabel() {
  const a = current();
  const value = $("acct-label").value.trim();
  if (!a || !value || value === a.label) return;
  try { await api(`/api/accounts/${a.id}/label`, { method: "POST", body: { label: value } }); }
  catch (e) { alert(e.message); }
  await loadState();
}

async function toggleAdd() {
  const panel = $("add-panel");
  show(panel, panel.classList.contains("is-hidden"));
  if (panel.classList.contains("is-hidden")) return;
  const list = $("chrome-list");
  list.replaceChildren(el("li", "hint", "Looking for Chrome profiles..."));
  const profiles = await api("/api/chrome");
  list.replaceChildren();
  if (!profiles.length) list.append(el("li", "hint", "No signed-in Chrome profiles found."));
  for (const p of profiles) {
    const li = el("li", "chrome-item");
    const label = el("span");
    label.append(el("span", "account-name", p.name), el("span", "account-sub", p.email));
    const btn = el("button", "btn btn-small", p.added ? "Added" : "Add");
    btn.type = "button";
    btn.disabled = p.added || busy();
    btn.addEventListener("click", () => startJob(api("/api/accounts/chrome", { method: "POST", body: { folder: p.folder } }), true));
    li.append(label, btn);
    list.append(li);
  }
}

// ------------------------------------------------------------ notebooks

async function loadLibrary() {
  const a = current();
  if (!a) return;
  const data = await api(`/api/accounts/${a.id}/library`);
  state.library = data.loaded ? data.notebooks : null;
  renderLibrary();
  renderDetail();
  // Read the notebooks automatically the first time an account is shown (once per page load).
  if ((!data.loaded || data.cached_at) && !busy() && !state.autoLoaded.has(a.id)) {
    state.autoLoaded.add(a.id);
    startJob(api(`/api/accounts/${a.id}/refresh`, { method: "POST" }));
  }
}

function renderLibrary() {
  const box = $("notebooks");
  box.replaceChildren();
  if (!state.library) {
    box.append(el("p", "empty", busy() ? "Reading notebooks..." : "Press Refresh notebooks to read this account's notebooks."));
    return;
  }
  const withAudio = state.library.filter((nb) => nb.overviews.length);
  const counts = { new: 0, saved: 0 };
  for (const nb of withAudio) for (const ov of nb.overviews) if (ov.status in counts) counts[ov.status] += 1;
  box.append(el("p", "muted",
    `${state.library.length} notebooks, ${withAudio.length} with audio. ${counts.new} new, ${counts.saved} saved.`));
  for (const nb of withAudio) {
    const sec = el("div", "notebook");
    sec.append(el("div", "notebook-title", nb.title));
    for (const ov of nb.overviews) {
      const row = el("label", "overview");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.dataset.id = ov.artifact_id;
      box2.dataset.status = ov.status;
      box2.disabled = ov.status === "in_progress" || ov.status === "saved";
      const meta = el("span", "overview-meta");
      meta.append(pill(ov.status), el("span", "muted", shortDate(ov.created_at)));
      row.append(box2, el("span", "overview-title", ov.title), meta);
      sec.append(row);
    }
    box.append(sec);
  }
  if (!withAudio.length) box.append(el("p", "empty", "No audio overviews in this account yet."));
}

function selectedIds() {
  const boxes = [...document.querySelectorAll("#notebooks input[type=checkbox]:checked")];
  return {
    artifact_ids: boxes.filter((b) => b.dataset.status === "new").map((b) => b.dataset.id),
    redownload: boxes.filter((b) => b.dataset.status === "saved_but_missing").map((b) => b.dataset.id),
  };
}

// ------------------------------------------------------------ jobs

async function startJob(promise, refreshAccountsAfter = false) {
  try {
    const job = await promise;
    state.jobId = job.id;
    state.refreshAfter = refreshAccountsAfter;
    renderDetail();
    pollJob();
  } catch (e) {
    alert(e.message);
  }
}

async function pollJob() {
  if (!state.jobId) return;
  const job = await api(`/api/jobs/${state.jobId}`);
  renderJob(job);
  if (job.state === "queued" || job.state === "running") {
    setTimeout(pollJob, 700);
    return;
  }
  state.jobId = null;
  if (job.kind === "add" && job.account_id) state.selected = job.account_id;
  await loadState();
  // Close the add panel only on success, so a failed add can be retried right away.
  if (job.kind === "add" && job.state === "done") { show($("add-panel"), false); }
  await loadLibrary();
}

function renderJob(job) {
  show($("job-box"), true);
  $("job-title").textContent = job.title;
  const st = $("job-state");
  st.className = `pill pill-${job.state}`;
  st.textContent = STATUS_TEXT[job.state] || job.state;
  const last = job.lines.length ? job.lines[job.lines.length - 1] : "";
  $("job-message").textContent = job.message || last;
  const overall = $("job-overall");
  const active = job.state === "queued" || job.state === "running";
  if (job.kind === "harvest") {
    show(overall, true);
    setBar(overall, job.pct || 0, false);
    const done = Object.values(job.items).filter((it) => it.outcome).length;
    $("job-message").textContent = job.message || `${done} of ${job.total} done. ${Math.round((job.pct || 0) * 100)}% overall.`;
  } else {
    // Sign-ins, checks and listing have no measurable size: show a moving bar while working.
    show(overall, active);
    setBar(overall, null, true);
  }
  const list = $("job-items");
  list.replaceChildren();
  for (const [id, item] of Object.entries(job.items)) {
    const li = el("li", "job-item");
    const head = el("div", "job-item-head");
    head.append(el("span", "", item.title), el("span", "muted", item.step));
    const bar = el("div", "bar bar-thin");
    bar.setAttribute("role", "progressbar");
    bar.append(el("span", "bar-fill"));
    const working = active && job.current === id && !item.outcome;
    setBar(bar, item.outcome ? 1 : item.pct, working && (item.pct === null || item.pct === undefined));
    if (item.outcome === "failed") bar.classList.add("bar-failed");
    li.append(head, bar);
    list.append(li);
  }
}

/** Set a progress bar: a fraction 0 to 1, or a moving stripe when the size is unknown. */
function setBar(bar, pct, indeterminate) {
  bar.classList.toggle("bar-indeterminate", !!indeterminate);
  const fill = bar.querySelector(".bar-fill");
  const value = indeterminate ? 100 : Math.round((pct || 0) * 100);
  fill.style.setProperty("--pct", `${value}%`);
  if (indeterminate) bar.removeAttribute("aria-valuenow");
  else bar.setAttribute("aria-valuenow", String(value));
}

// ------------------------------------------------------------ folder picker

async function openPicker() {
  const a = current();
  show($("picker"), true);
  $("picker-error").textContent = "";
  await browse(a && a.output_dir ? a.output_dir : "");
}

async function browse(path) {
  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(path)}`);
    state.pickerPath = data.path;
    state.pickerParent = data.parent;
    $("picker-path").value = data.path;
    $("picker-error").textContent = "";
    const list = $("picker-list");
    list.replaceChildren();
    for (const d of data.dirs) {
      const li = el("li");
      const btn = el("button", "", d.split(/[\\/]/).filter(Boolean).pop() || d);
      btn.type = "button";
      btn.title = d;
      btn.addEventListener("click", () => browse(d));
      li.append(btn);
      list.append(li);
    }
    if (!data.dirs.length) list.append(el("li", "hint", "No folders inside."));
    $("picker-up").disabled = data.parent === null;
    $("picker-new").disabled = !data.path;
    show($("picker-new-row"), false);
  } catch (e) {
    $("picker-error").textContent = e.message;
  }
}

/** Show the new-folder name box in the picker. */
function startNewFolder() {
  show($("picker-new-row"), true);
  $("picker-new-name").value = "";
  $("picker-new-name").focus();
}

/** Create a folder inside the one being browsed, then open it. */
async function createFolder() {
  const name = $("picker-new-name").value.trim();
  if (!name) return;
  try {
    const data = await api("/api/mkdir", { method: "POST", body: { parent: state.pickerPath, name } });
    show($("picker-new-row"), false);
    await browse(data.path);
  } catch (e) {
    $("picker-error").textContent = e.message;
  }
}

async function usePickedFolder() {
  const a = current();
  const path = $("picker-path").value.trim();
  try {
    await api(`/api/accounts/${a.id}/output`, { method: "POST", body: { path } });
    show($("picker"), false);
    await loadState();
  } catch (e) {
    $("picker-error").textContent = e.message;
  }
}

// ------------------------------------------------------------ history

async function loadHistory() {
  const sel = $("history-account");
  const keep = sel.value;
  sel.replaceChildren(el("option", "", "All accounts"));
  sel.firstChild.value = "";
  for (const a of state.accounts) {
    const o = el("option", "", a.label);
    o.value = a.id;
    sel.append(o);
  }
  sel.value = keep;
  const rows = await api(`/api/history${sel.value ? `?account=${sel.value}` : ""}`);
  const box = $("history-list");
  box.replaceChildren();
  if (!rows.length) box.append(el("p", "empty", "Nothing saved yet."));
  for (const r of rows) {
    const item = el("div", "history-item");
    item.append(el("div", "history-title", r.title));
    item.append(el("div", "muted",
      `${r.notebook} · ${r.account} · ${duration(r.duration_s)} · ${(r.size_bytes / 1e6).toFixed(1)} MB · saved ${new Date(r.saved_at).toLocaleString()}`));
    item.append(el("div", "history-path", r.path));
    const actions = el("div", "row");
    if (!r.present) actions.append(pill("saved_but_missing"));
    const open = el("button", "btn btn-small btn-quiet", "Open folder");
    open.type = "button";
    open.addEventListener("click", () => openFolder(r.folder));
    actions.append(open);
    item.append(actions);
    box.append(item);
  }
}

async function openFolder(path) {
  try { await api("/api/open", { method: "POST", body: { path } }); }
  catch (e) { alert(e.message); }
}

/** Stop the app after asking; the page then explains how to start it again. */
async function quitApp() {
  if (!confirm("Quit the harvester? You can start it again from its icon.")) return;
  try {
    await api("/api/quit", { method: "POST" });
    document.body.replaceChildren(el("main", "layout"));
    const box = el("section", "panel");
    box.append(el("h1", "brand", document.title), el("p", "", "The harvester has stopped. You can close this tab."));
    document.body.firstChild.append(box);
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------ wiring

function switchView(view) {
  for (const t of document.querySelectorAll(".tab[data-view]")) t.classList.toggle("is-active", t.dataset.view === view);
  show($("view-harvest"), view === "harvest");
  show($("view-history"), view === "history");
  if (view === "history") loadHistory();
}

function wire() {
  for (const t of document.querySelectorAll(".tab[data-view]")) t.addEventListener("click", () => switchView(t.dataset.view));
  $("quit").addEventListener("click", quitApp);
  $("add-toggle").addEventListener("click", toggleAdd);
  $("signin-add").addEventListener("click", () =>
    startJob(api("/api/accounts/signin", { method: "POST", body: { label: $("signin-label").value } }), true));
  $("acct-label").addEventListener("change", saveLabel);
  $("btn-check").addEventListener("click", () => startJob(api(`/api/accounts/${state.selected}/check`, { method: "POST" })));
  $("btn-signin").addEventListener("click", () => startJob(api(`/api/accounts/${state.selected}/signin`, { method: "POST" })));
  $("btn-refresh").addEventListener("click", () => startJob(api(`/api/accounts/${state.selected}/refresh`, { method: "POST" })));
  $("btn-harvest-new").addEventListener("click", () =>
    startJob(api(`/api/accounts/${state.selected}/harvest`, { method: "POST", body: { all_new: true } })));
  $("btn-harvest-sel").addEventListener("click", () => {
    const ids = selectedIds();
    if (!ids.artifact_ids.length && !ids.redownload.length) { alert("Tick one or more overviews first."); return; }
    startJob(api(`/api/accounts/${state.selected}/harvest`, { method: "POST", body: ids }));
  });
  $("btn-choose").addEventListener("click", openPicker);
  $("portable-ok").addEventListener("click", async () => { await api("/api/portable-ack", { method: "POST" }); show($("portable-warning"), false); });
  $("btn-scan").addEventListener("click", () => startJob(api(`/api/accounts/${state.selected}/scan`, { method: "POST" })));
  $("btn-open-out").addEventListener("click", () => openFolder(current().output_dir));
  $("picker-go").addEventListener("click", () => browse($("picker-path").value));
  $("picker-path").addEventListener("keydown", (e) => { if (e.key === "Enter") browse($("picker-path").value); });
  $("picker-up").addEventListener("click", () => browse(state.pickerParent || ""));
  $("picker-home").addEventListener("click", () => browse(""));
  $("picker-new").addEventListener("click", startNewFolder);
  $("picker-new-ok").addEventListener("click", createFolder);
  $("picker-new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") createFolder(); });
  $("picker-cancel").addEventListener("click", () => show($("picker"), false));
  $("picker-ok").addEventListener("click", usePickedFolder);
  $("history-account").addEventListener("change", loadHistory);
}

async function init() {
  wire();
  await loadState();
  const st = await api("/api/state");
  const running = st.jobs.find((j) => j.state === "queued" || j.state === "running");
  if (running) { state.jobId = running.id; pollJob(); }
  await loadLibrary();
}

init().catch((e) => { document.body.prepend(el("div", "banner", e.message)); });
