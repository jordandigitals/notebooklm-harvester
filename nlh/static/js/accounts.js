// Accounts: sidebar list, add panel, the selected account's card, folder picker.
import { api } from "./api.js";
import { $, el, icon, button, pill, show, plural } from "./dom.js";
import { toast, dialog } from "./ui.js";
import { lib, apply, renderFilters } from "./library.js";

export const acctState = { windows: false, interactive: true, startJob: null, busy: () => false, defaultOutput: "" };

/** Draw the sidebar account list. */
export function renderAccountList() {
  const list = $("acct-list");
  list.replaceChildren();
  for (const a of lib.accounts.values()) {
    const li = el("li");
    const b = el("button", "acct");
    b.type = "button";
    b.setAttribute("aria-current", lib.scope === "account" && lib.account === a.id ? "true" : "false");
    b.append(el("span", "an", a.label), el("span", "ae", a.email || ""), pill(a.status));
    b.addEventListener("click", () => {
      lib.account = a.id;
      lib.scope = "account";
      syncScopeButtons();
      apply();
      renderFilters();
      renderAccountList();
      renderAccountCard();
      $("sidebar").classList.remove("is-open");
    });
    li.append(b);
    list.append(li);
  }
  if (!lib.accounts.size) list.append(el("li", "hint", "No accounts yet."));
}

/** Keep the scope switch in step with lib.scope. */
export function syncScopeButtons() {
  for (const b of document.querySelectorAll("[data-scope]")) b.setAttribute("aria-pressed", b.dataset.scope === lib.scope ? "true" : "false");
}

/** The account card above the list: name, sign-in, folder, refresh, scan. */
export function renderAccountCard() {
  const box = $("account-card");
  box.replaceChildren();
  if (!lib.accounts.size) return;
  if (lib.scope === "all") {
    const card = el("div", "account-card");
    const head = el("div", "head");
    head.append(el("span", "name", "All accounts"), el("span", "hint", plural(lib.accounts.size, "account")));
    const refreshAll = button("Refresh all accounts", { cls: "btn btn-sm", iconName: "refresh" });
    refreshAll.disabled = acctState.busy();
    refreshAll.addEventListener("click", async () => {
      for (const a of lib.accounts.values()) await acctState.startJob(api(`/api/accounts/${a.id}/refresh`, { method: "POST" }), { quiet: true });
    });
    head.append(refreshAll);
    card.append(head);
    const stale = [...lib.accounts.values()].filter((a) => a.status !== "ok");
    const notes = el("div", "card-notes");
    if (stale.length) notes.append(notice("warn", `${stale.map((a) => a.label).join(", ")} ${stale.length === 1 ? "needs" : "need"} attention. Pick the account in Accounts and filters to sign in again.`));
    if (lib.neverRefreshed.length) {
      const names = lib.neverRefreshed.map((id) => lib.accounts.get(id)?.label).filter(Boolean);
      notes.append(notice("info", `Not read yet: ${names.join(", ")}. Press Refresh all accounts.`));
    }
    if (notes.childElementCount) {
      // Phones: one compact toggle instead of panels of status text above the list.
      const toggle = button(`${notes.childElementCount === 1 && stale.length ? `${stale.length} needs attention` : "Notes"}`, { cls: "btn btn-sm btn-quiet notes-toggle", iconName: "alert" });
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", () => {
        const open = notes.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", String(open));
      });
      head.append(toggle);
      card.append(notes);
    }
    box.append(card);
    return;
  }
  const a = lib.accounts.get(lib.account);
  if (!a) return;
  const card = el("div", "account-card");
  const head = el("div", "head");
  const name = el("input", "name");
  name.value = a.label;
  name.setAttribute("aria-label", "Account name");
  name.addEventListener("change", async () => {
    try { await api(`/api/accounts/${a.id}/label`, { method: "POST", body: { label: name.value } }); a.label = name.value.trim(); renderAccountList(); }
    catch (e) { toast(e.message, "bad"); name.value = a.label; }
  });
  head.append(name, pill(a.status));
  card.append(head);
  card.append(el("p", "hint", [a.email, a.chrome_profile ? `Chrome ${a.chrome_profile}` : "Signed in directly"].filter(Boolean).join(" · ")));
  const actions = el("div", "row");
  const busy = acctState.busy();
  const refresh = button("Refresh notebooks", { cls: "btn btn-sm", iconName: "refresh" });
  refresh.disabled = busy;
  refresh.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/refresh`, { method: "POST" })));
  const check = button("Check sign-in", { cls: "btn btn-sm btn-ghost" });
  check.disabled = busy;
  check.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/check`, { method: "POST" })));
  actions.append(refresh, check);
  if (a.status !== "ok") {
    const again = button("Sign in again", { cls: "btn btn-sm btn-primary" });
    again.disabled = busy;
    again.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/signin`, { method: "POST" })));
    actions.append(again);
  }
  card.append(actions);
  const folder = el("div", "folder");
  folder.append(icon("folder"), el("span", "", "Save to"), el("code", "", a.output_dir || "No folder chosen"));
  const choose = button("Choose folder", { cls: "btn btn-sm" });
  choose.disabled = busy;
  choose.addEventListener("click", () => openPicker(a));
  folder.append(choose);
  if (a.output_dir) {
    folder.append(button("Open", { cls: "btn btn-sm btn-ghost", onClick: () => openFolder(a.output_dir) }));
    const scan = button("Scan folder", { cls: "btn btn-sm btn-ghost", iconName: "scan", title: "Find MP3s this app saved before, for example on another computer" });
    scan.disabled = busy;
    scan.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/scan`, { method: "POST" })));
    folder.append(scan);
  }
  card.append(folder);
  if (!a.output_dir) card.append(notice("info", "Choose a folder before harvesting. Any folder works, including one synced by Google Drive, MEGA or OneDrive."));
  if (a.long_folder) card.append(notice("warn", "This folder path is long for Windows. Titles are shortened automatically; a much deeper folder would be refused."));
  box.append(card);
}

function notice(kind, text) {
  const n = el("div", `notice notice-${kind}`);
  n.append(icon(kind === "info" ? "info" : "alert"), el("span", "grow", text));
  return n;
}

/** Open a folder in Finder or Explorer (only folders the app knows). */
export async function openFolder(path) {
  try { await api("/api/open", { method: "POST", body: { path } }); }
  catch (e) { toast(e.message, "bad"); }
}

// ------------------------------------------------------------ add account

/** Show or hide the add panel and load the Chrome profiles. */
export async function toggleAdd() {
  const panel = $("add-panel");
  const open = panel.classList.contains("is-hidden");
  show(panel, open);
  $("add-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  if (!open) return;
  const list = $("chrome-list");
  list.replaceChildren(el("p", "hint", "Looking for Chrome profiles..."));
  try {
    const profiles = await api("/api/chrome");
    list.replaceChildren();
    if (!profiles.length) list.append(el("p", "hint", "No signed-in Chrome profiles found."));
    for (const p of profiles) {
      const row = el("div", "chrome-item");
      const who = el("div", "who");
      who.append(el("span", "", p.name), el("span", "hint", p.email));
      const b = button(p.added ? "Added" : "Add", { cls: "btn btn-sm" });
      b.disabled = p.added || acctState.busy();
      b.addEventListener("click", () => acctState.startJob(api("/api/accounts/chrome", { method: "POST", body: { folder: p.folder } }), { add: true }));
      row.append(who, b);
      list.append(row);
    }
  } catch (e) { list.replaceChildren(el("p", "error", e.message)); }
}

/** Platform texts for the add panel (D-022 on Windows; Chrome-only builds). */
export function adjustAddPanel() {
  if (acctState.windows) {
    if (acctState.interactive) {
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
  if (!acctState.interactive) {
    $("signin-hint").textContent = "This copy adds accounts from Chrome only. Sign in to the account in Chrome first, then pick it above.";
    show($("signin-row"), false);
  }
}

// ------------------------------------------------------------ folder picker

/** Choose an output folder for an account (starts at its folder or the default suggestion). */
export async function openPicker(account) {
  const start = account.output_dir || acctState.defaultOutput || "";
  const chosen = await openFolderPicker(start);
  if (!chosen) return;
  try {
    await api(`/api/accounts/${account.id}/output`, { method: "POST", body: { path: chosen } });
    toast("Folder saved.");
    document.dispatchEvent(new CustomEvent("nlh:reload"));
  } catch (e) { toast(e.message, "bad"); }
}

/** The folder picker dialog: browse, type a path, make a new folder. Resolves to a path or null. */
export async function openFolderPicker(startPath = "") {
  const body = el("div", "filters");
  const pathRow = el("div", "row");
  const path = el("input", "input grow");
  path.setAttribute("aria-label", "Folder path");
  const go = button("Go", { cls: "btn" });
  pathRow.append(path, go);
  const nav = el("div", "row");
  const up = button("Up one level", { cls: "btn btn-sm" });
  const home = button("Home", { cls: "btn btn-sm" });
  const mk = button("New folder", { cls: "btn btn-sm", iconName: "plus" });
  nav.append(up, home, mk);
  const list = el("ul", "picker-list");
  const err = el("p", "error");
  body.append(pathRow, nav, list, err);
  let parent = null;
  const browse = async (p) => {
    try {
      const d = await api(`/api/browse?path=${encodeURIComponent(p)}`);
      path.value = d.path;
      parent = d.parent;
      up.disabled = d.parent === null;
      mk.disabled = !d.path;
      err.textContent = "";
      list.replaceChildren();
      for (const dir of d.dirs) {
        const li = el("li");
        const b = el("button", "", dir.split(/[\\/]/).filter(Boolean).pop() || dir);
        b.type = "button";
        b.title = dir;
        b.addEventListener("click", () => browse(dir));
        li.append(b);
        list.append(li);
      }
      if (!d.dirs.length) list.append(el("li", "hint", "No folders inside."));
    } catch (e) { err.textContent = e.message; }
  };
  go.addEventListener("click", () => browse(path.value));
  path.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); browse(path.value); } });
  up.addEventListener("click", () => browse(parent || ""));
  home.addEventListener("click", () => browse(""));
  mk.addEventListener("click", async () => {
    const input = el("input", "input");
    input.placeholder = "New folder name";
    input.setAttribute("aria-label", "New folder name");
    const ok = button("Create", { cls: "btn btn-sm" });
    const row = el("div", "row");
    row.append(input, ok);
    nav.after(row);
    input.focus();
    const create = async () => {
      try {
        const d = await api("/api/mkdir", { method: "POST", body: { parent: path.value, name: input.value.trim() } });
        row.remove();
        await browse(d.path);
      } catch (e) { err.textContent = e.message; }
    };
    ok.addEventListener("click", create);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); create(); } });
  });
  await browse(startPath);
  const choice = await dialog({ title: "Choose a folder", body, actions: [{ label: "Cancel", value: null }, { label: "Use this folder", value: "use", primary: true }] });
  return choice === "use" ? path.value : null;
}
