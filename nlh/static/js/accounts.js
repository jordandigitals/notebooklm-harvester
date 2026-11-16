// Accounts: sidebar list, add panel, the selected account's card, folder picker.
import { api } from "./api.js";
import { $, el, icon, button, pill, show, plural } from "./dom.js";
import { toast, dialog } from "./ui.js";
import { lib, apply, renderFilters } from "./library.js";

export const acctState = { windows: false, interactive: true, startJob: null, busy: () => false, defaultOutput: "", cardOpen: false, saveCardOpen: () => {} };

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
  // D-032: one compact line (name, sign-in state, short save folder, Refresh notebooks, More);
  // More opens the rest (email, Check sign-in, full folder, Choose folder, Open, Scan folder).
  // Without a folder the card stays open: harvesting needs one, so that step is never hidden.
  const card = el("div", "account-card");
  const busy = acctState.busy();
  const needsFolder = !a.output_dir;
  const head = el("div", "head");
  const name = el("input", "name");
  name.value = a.label;
  name.setAttribute("aria-label", "Account name");
  name.addEventListener("change", async () => {
    try { await api(`/api/accounts/${a.id}/label`, { method: "POST", body: { label: name.value } }); a.label = name.value.trim(); renderAccountList(); }
    catch (e) { toast(e.message, "bad"); name.value = a.label; }
  });
  head.append(name, pill(a.status));
  const where = el("span", "hint card-folder", a.output_dir ? `Save to ${shortPath(a.output_dir)}` : "No folder chosen");
  if (a.output_dir) where.title = a.output_dir;
  head.append(where);
  const refresh = button("Refresh notebooks", { cls: "btn btn-sm", iconName: "refresh" });
  refresh.disabled = busy;
  refresh.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/refresh`, { method: "POST" })));
  head.append(refresh);
  if (a.status !== "ok") {
    const again = button("Sign in again", { cls: "btn btn-sm btn-primary" });
    again.disabled = busy;
    again.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/signin`, { method: "POST" })));
    head.append(again);
  }
  const more = el("div", "card-more");
  more.id = "account-more";
  const open = acctState.cardOpen || needsFolder;
  const toggle = button(open ? "Less" : "More", { cls: "btn btn-sm btn-ghost card-toggle", iconName: "chevron" });
  toggle.setAttribute("aria-controls", "account-more");
  toggle.setAttribute("aria-expanded", String(open));
  toggle.addEventListener("click", () => {
    const now = toggle.getAttribute("aria-expanded") !== "true";
    acctState.cardOpen = now;
    acctState.saveCardOpen(now);
    toggle.setAttribute("aria-expanded", String(now));
    toggle.lastChild.textContent = now ? "Less" : "More";
    more.hidden = !now;
  });
  head.append(toggle);
  card.append(head);

  more.hidden = !open;
  more.append(el("p", "hint", [a.email, a.chrome_profile ? `Chrome ${a.chrome_profile}` : "Signed in directly"].filter(Boolean).join(" · ")));
  const check = button("Check sign-in", { cls: "btn btn-sm btn-ghost" });
  check.disabled = busy;
  check.addEventListener("click", () => acctState.startJob(api(`/api/accounts/${a.id}/check`, { method: "POST" })));
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
  folder.append(check);
  more.append(folder);
  card.append(more);
  if (needsFolder) {
    card.append(notice("info", "Choose a folder before harvesting. Any folder works, including one synced by Google Drive, MEGA or OneDrive."));
    if (!a.output_dir) addQuickPicks(card, a);
  }
  if (a.long_folder) card.append(notice("warn", "This folder path is long for Windows. Titles are shortened automatically; a much deeper folder would be refused."));
  box.append(card);
}

/** Up to three remembered folders as one-click picks for an account without a folder (D-033).
 * Nothing is assigned until one is pressed. */
async function addQuickPicks(card, a) {
  const folders = (await rememberedFolders()).filter((f) => f.available).slice(0, 3);
  if (!folders.length) return;
  const row = el("div", "row quick-picks");
  row.append(el("span", "hint", "Recent folders:"));
  for (const f of folders) {
    const b = button(`Use ${middlePath(f.path, 28)}`, { cls: "btn btn-sm" });
    b.title = f.path;
    b.setAttribute("aria-label", `Use ${f.path}`);
    b.addEventListener("click", () => setAccountFolder(a, f.path));
    row.append(b);
  }
  card.append(row);
}

/** The last two folders of a path, for the compact card ("...\Notebook Harvester\Work"). */
function shortPath(path) {
  const sep = path.includes("\\") ? "\\" : "/";
  const parts = path.split(sep).filter(Boolean);
  return parts.length <= 2 ? path : `...${sep}${parts.slice(-2).join(sep)}`;
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

/** Show or hide the add panel. The Chrome list loads only when its block is open. */
export async function toggleAdd() {
  const panel = $("add-panel");
  const open = panel.classList.contains("is-hidden");
  show(panel, open);
  $("add-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  $("signin-msg").textContent = "";
  if (open && !$("add-chrome").classList.contains("is-hidden")) await loadChromeProfiles();
}

/** Open or close the Chrome-profile route under "Use a Chrome profile instead" (D-029, D-030). */
export async function toggleChromeRoute() {
  const block = $("add-chrome");
  const open = block.classList.contains("is-hidden");
  show(block, open);
  $("chrome-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) await loadChromeProfiles();
}

/** Start "Continue with Google": errors (such as no Chrome or Edge) show under the button. */
export async function continueWithGoogle() {
  const msg = $("signin-msg");
  msg.textContent = "";
  if (acctState.busy()) return;
  let job;
  try {
    job = await api("/api/accounts/signin", { method: "POST", body: { label: $("signin-label").value } });
  } catch (e) { msg.textContent = e.message; return; }
  acctState.startJob(Promise.resolve(job), { add: true });
}

/** "above" or "below": where the own sign-in block sits relative to the Chrome block right now. */
function signinWhere() {
  const pos = $("add-chrome").compareDocumentPosition($("add-signin"));
  return pos & Node.DOCUMENT_POSITION_PRECEDING ? "above" : "below";
}

/** Where to send someone with no usable Chrome profile (the own window when this copy has it). */
function noProfileHint(chromeFound) {
  const other = acctState.interactive ? ` Or use "${$("signin-add").textContent.trim()}" ${signinWhere()}.` : "";
  return chromeFound
    ? `No Chrome profile on this computer is signed in to Google. Sign in to Google in Chrome first, then press Refresh.${other}`
    : `Google Chrome was not found on this computer.${other}`;
}

/** Read the Chrome profiles on this computer (local only, D-030) and draw the pick list. */
export async function loadChromeProfiles() {
  const list = $("chrome-list");
  const refresh = $("chrome-refresh");
  refresh.disabled = true;
  list.replaceChildren(el("p", "hint", "Looking for Chrome profiles..."));
  try {
    const data = await api("/api/chrome");
    list.replaceChildren();
    if (!data.profiles.length) list.append(el("p", "hint", noProfileHint(data.chrome)));
    for (const p of data.profiles) {
      const row = el("div", "chrome-item");
      const who = el("div", "who");
      who.append(el("span", "", p.email), el("span", "hint", p.name));
      const b = button(p.added ? "Added" : "Add", { cls: "btn btn-sm" });
      b.disabled = p.added || acctState.busy();
      if (!p.added) b.setAttribute("aria-label", `Add ${p.email}`);
      b.addEventListener("click", () => acctState.startJob(api("/api/accounts/chrome", { method: "POST", body: { folder: p.folder } }), { add: true }));
      row.append(who, b);
      list.append(row);
    }
  } catch (e) { list.replaceChildren(el("p", "error", e.message)); }
  finally { refresh.disabled = false; }
}

/** Platform texts for the add panel. The own window comes first and is recommended on both
 * systems (D-029); the Chrome route sits behind "Use a Chrome profile instead". */
export function adjustAddPanel() {
  if (acctState.windows) {
    $("chrome-hint").textContent =
      "Copies the Google sign-in from your Chrome. Quit Chrome completely first (also its icon near the clock). " +
      "Windows Security may report the reader as \"Trojan InfoStealer\" because it reads Chrome's saved sign-in; " +
      "it only reads the Google sign-in cookies it needs, on this computer. To allow it: Windows Security, " +
      "Virus and threat protection, Protection history, select the entry, Actions, Allow on device.";
  }
  if (!acctState.interactive) {
    // A copy without the sign-in window: the Chrome list is the only route, shown at once.
    show($("add-signin"), false);
    show($("chrome-toggle"), false);
    show($("add-chrome"), true);
  }
}

// ------------------------------------------------------------ folder picker

/** The plain notice for a folder another account already saves to (D-033). */
export function sharedNotice(names) {
  return `Also used by ${names.join(", ")}. Files from both accounts are saved together; existing files are never overwritten.`;
}

/** Remembered folders (D-033), or an empty list when they cannot be read. */
async function rememberedFolders() {
  try { return await api("/api/folders"); } catch (e) { return []; }
}

/** Set an account's folder; says so when another account already uses it. */
export async function setAccountFolder(account, chosen) {
  try {
    const r = await api(`/api/accounts/${account.id}/output`, { method: "POST", body: { path: chosen } });
    toast(r.also_used_by && r.also_used_by.length ? `Folder saved. ${sharedNotice(r.also_used_by)}` : "Folder saved.", "ok", 7000);
    document.dispatchEvent(new CustomEvent("nlh:reload"));
  } catch (e) { toast(e.message, "bad"); }
}

/** Choose an output folder for an account: starts at its folder, else the most recent
 * remembered folder that is there, else the suggested folder (D-033). */
export async function openPicker(account) {
  const recent = await rememberedFolders();
  const lastHere = recent.find((f) => f.available);
  const start = account.output_dir || (lastHere && lastHere.path) || acctState.defaultOutput || "";
  const chosen = await openFolderPicker(start, { accountId: account.id, accountLabel: account.label, recent });
  if (chosen) await setAccountFolder(account, chosen);
}

/** Shorten a long path in the middle for small screens; the full path stays in the title. */
function middlePath(p, max = 42) {
  return p.length <= max ? p : `${p.slice(0, 14)}...${p.slice(-(max - 17))}`;
}

/** The folder picker dialog: remembered folders, browse, type a path, make a new folder.
 * Resolves to a path or null. With accountId, notes when another account uses the folder. */
export async function openFolderPicker(startPath = "", { accountId = null, accountLabel = null, recent = null } = {}) {
  const body = el("div", "filters");
  const recentBox = el("div", "recent-folders");
  const recentList = el("ul", "recent-list");
  const drawRecent = (folders) => {
    recentList.replaceChildren();
    show(recentBox, folders.length > 0);
    for (const f of folders) {
      const li = el("li", f.available ? "" : "is-missing");
      const name = el("code", "", middlePath(f.path));
      name.title = f.path;
      li.append(name);
      const others = (f.used_by || []).filter((label) => label !== accountLabel);
      if (others.length) li.append(el("span", "hint used-by", `Used by ${others.join(", ")}`));
      if (f.available) {
        const use = button("Use", { cls: "btn btn-sm" });
        use.setAttribute("aria-label", `Use ${f.path}`);
        use.addEventListener("click", async () => {
          await browse(f.path);
          $("dlg").querySelector(".dlg-actions .btn-primary").click();
        });
        li.append(use);
      } else {
        li.append(el("span", "hint", "Not available"));
      }
      const forget = button("Forget", { cls: "btn btn-sm btn-ghost" });
      forget.setAttribute("aria-label", `Forget ${f.path}`);
      forget.title = "Remove from this list. Nothing on disk is changed.";
      forget.addEventListener("click", async () => {
        try {
          await api("/api/folders/forget", { method: "POST", body: { path: f.path } });
          drawRecent(folders.filter((x) => x !== f));
        } catch (e) { err.textContent = e.message; }
      });
      li.append(forget);
      recentList.append(li);
    }
  };
  recentBox.append(el("h3", "", "Recent folders"), recentList);
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
  const shared = el("p", "notice notice-info is-hidden");
  shared.setAttribute("role", "status");
  body.append(recentBox, pathRow, nav, list, shared, err);
  let parent = null;
  const browse = async (p) => {
    try {
      const q = accountId !== null ? `&account=${accountId}` : "";
      const d = await api(`/api/browse?path=${encodeURIComponent(p)}${q}`);
      path.value = d.path;
      parent = d.parent;
      const others = d.used_by || [];
      shared.textContent = others.length ? sharedNotice(others) : "";
      show(shared, others.length > 0);
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
  drawRecent(recent || await rememberedFolders());
  await browse(startPath);
  const choice = await dialog({ title: "Choose a folder", body, actions: [{ label: "Cancel", value: null }, { label: "Use this folder", value: "use", primary: true }] });
  return choice === "use" ? path.value : null;
}
