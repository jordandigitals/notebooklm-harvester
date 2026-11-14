// Settings: theme, naming template with live preview, default folder, default view,
// notifications, diagnostics (redacted), trash and about. Stored in <home>/settings.json.
import { api } from "./api.js";
import { $, el, button, size, plural } from "./dom.js";
import { toast, confirmDialog } from "./ui.js";
import { openFolderPicker, acctState } from "./accounts.js";

export const prefs = { settings: null, views: [], onTheme: () => {} };

/** Apply a theme now: auto follows the system. */
export function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

async function save(update, okText = "Saved.") {
  try {
    prefs.settings = await api("/api/settings", { method: "POST", body: update });
    if (okText) toast(okText);
    return true;
  } catch (e) { toast(e.message, "bad"); return false; }
}

function section(title, hint) {
  const s = el("section", "set-card");
  s.append(el("h2", "", title));
  if (hint) s.append(el("p", "hint", hint));
  return s;
}

/** Draw the Settings view. */
export async function renderSettings() {
  const view = $("view-settings");
  prefs.settings = await api("/api/settings");
  const st = prefs.settings;
  const wrap = el("div", "settings");

  // theme
  const theme = section("Appearance");
  const seg = el("div", "seg");
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", "Theme");
  for (const [v, l] of [["auto", "Match system"], ["light", "Light"], ["dark", "Dark"]]) {
    const b = el("button", "", l);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", st.theme === v ? "true" : "false");
    b.addEventListener("click", async () => {
      if (await save({ theme: v }, "")) {
        applyTheme(v);
        for (const x of seg.children) x.setAttribute("aria-checked", String(x === b));
      }
    });
    seg.append(b);
  }
  theme.append(seg);

  // naming
  const naming = section("File names",
    "How new files are named inside each account's folder. Tokens: {notebook}, {title}, {date}, {account}. One folder level at most, written as folder/file. Files already saved are never renamed.");
  const tlab = el("label", "field", "Template");
  const tin = el("input", "input");
  tin.value = st.naming_template;
  tin.spellcheck = false;
  tlab.append(tin);
  const preview = el("p", "preview");
  preview.setAttribute("aria-live", "polite");
  const err = el("p", "error");
  const tsave = button("Save template", { cls: "btn btn-sm btn-primary" });
  const reset = button("Use the default", { cls: "btn btn-sm btn-ghost" });
  let t;
  const refreshPreview = async () => {
    try {
      const r = await api("/api/naming/preview", { method: "POST", body: { template: tin.value } });
      preview.replaceChildren(el("span", "hint", "Example: "), el("code", "", r.path));
      err.textContent = "";
      tsave.disabled = tin.value === prefs.settings.naming_template;
    } catch (e) { err.textContent = e.message; preview.replaceChildren(); tsave.disabled = true; }
  };
  tin.addEventListener("input", () => { clearTimeout(t); t = setTimeout(refreshPreview, 200); });
  tsave.addEventListener("click", async () => { if (await save({ naming_template: tin.value.trim() }, "File name template saved.")) refreshPreview(); });
  reset.addEventListener("click", () => { tin.value = "{notebook}/{title}"; refreshPreview(); });
  const trow = el("div", "row");
  trow.append(tsave, reset);
  naming.append(tlab, preview, err, trow);
  if (acctState.windows) naming.append(el("p", "hint", "On Windows long titles are shortened so the full path stays under 240 characters."));
  refreshPreview();

  // default folder and view
  const defaults = section("Defaults");
  const dlab = el("div", "field");
  dlab.append(el("span", "", "Suggested folder for new accounts"));
  const drow = el("div", "folder");
  const dcode = el("code", "", st.default_output || "None");
  const dpick = button("Choose", { cls: "btn btn-sm", onClick: async () => {
    const p = await openFolderPicker(st.default_output || "");
    if (p && await save({ default_output: p }, "Suggested folder saved.")) { dcode.textContent = p; acctState.defaultOutput = p; }
  } });
  drow.append(dcode, dpick);
  if (st.default_output) drow.append(button("Clear", { cls: "btn btn-sm btn-ghost", onClick: async () => { if (await save({ default_output: "" }, "Cleared.")) { dcode.textContent = "None"; acctState.defaultOutput = ""; } } }));
  dlab.append(drow);
  const vlab = el("label", "field", "View to open with");
  const vsel = el("select", "select");
  const none = el("option", "", "Everything (no saved view)");
  none.value = "";
  vsel.append(none);
  for (const v of st.views) { const o = el("option", "", v.name); o.value = v.name; vsel.append(o); }
  vsel.value = st.default_view || "";
  vsel.addEventListener("change", () => save({ default_view: vsel.value }, "Start view saved."));
  vlab.append(vsel);
  defaults.append(dlab, vlab);

  // notifications
  const notes = section("Notifications", "Messages in the page always appear. Optionally, the browser can also tell you when a long harvest ends while this tab is in the background.");
  const nlab = el("label", "check");
  const nbox = el("input");
  nbox.type = "checkbox";
  nbox.checked = !!st.notifications;
  nbox.addEventListener("change", async () => {
    if (nbox.checked && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission();
      if (p !== "granted") { nbox.checked = false; toast("The browser did not allow notifications.", "warn"); return; }
    }
    if (nbox.checked && !("Notification" in window)) { nbox.checked = false; toast("This browser has no notifications.", "warn"); return; }
    save({ notifications: nbox.checked }, nbox.checked ? "Browser notifications on." : "Browser notifications off.");
  });
  nlab.append(nbox, el("span", "lbl", "Tell me when a long harvest ends"));
  notes.append(nlab);

  // Studio (D-025)
  const studioCard = section("Studio",
    "Limits for making audio overviews from a plan. NotebookLM does not show how much of its daily allowance is left, so the app keeps its own count per account.");
  const numField = (label, key, min, max, unit) => {
    const lab = el("label", "field", label);
    const row = el("div", "row");
    const inp = el("input", "input num");
    inp.type = "number";
    inp.min = String(min);
    inp.max = String(max);
    inp.step = "1";
    inp.value = String(st[key]);
    inp.addEventListener("change", () => {
      const v = Number(inp.value);
      if (!Number.isInteger(v) || v < min || v > max) { toast(`Use a whole number from ${min} to ${max}.`, "warn"); inp.value = String(prefs.settings[key]); return; }
      save({ [key]: v }, `${label} saved.`);
    });
    row.append(inp, el("span", "hint", unit));
    lab.append(row);
    return lab;
  };
  studioCard.append(
    numField("Overviews per account per day", "studio_daily_cap", 1, 20, "counted when a request is sent"),
    numField("Pause between requests", "studio_pause_s", 30, 300, "seconds"),
  );
  const langLab = el("label", "field", "Language for new overviews");
  const lang = el("input", "input num");
  lang.value = st.studio_language;
  lang.spellcheck = false;
  lang.addEventListener("change", () => save({ studio_language: lang.value.trim() }, "Language saved."));
  langLab.append(lang);
  studioCard.append(langLab, el("p", "hint", "Mark accounts that other automated tools also use. Studio shows a warning when you pick one."));
  try {
    const sstate = await api("/api/studio/state");
    for (const a of sstate.accounts) {
      const lab = el("label", "check");
      const box = el("input");
      box.type = "checkbox";
      box.checked = a.shared;
      box.addEventListener("change", () => {
        const now = new Set(prefs.settings.studio_shared_accounts);
        if (box.checked) now.add(a.id); else now.delete(a.id);
        save({ studio_shared_accounts: [...now] }, box.checked ? `${a.label} marked as shared.` : `${a.label} no longer marked.`);
      });
      lab.append(box, el("span", "lbl", `${a.label}: used by other automated tools`));
      studioCard.append(lab);
    }
  } catch (e) { studioCard.append(el("p", "error", e.message)); }

  // diagnostics
  const diag = section("Diagnostics", "Facts to include when asking for help. No account names, emails or sign-in data are included.");
  const pre = el("pre", "diag");
  pre.tabIndex = 0;
  pre.setAttribute("role", "region");
  pre.setAttribute("aria-label", "Diagnostics text");
  try { pre.textContent = (await api("/api/diagnostics")).text; } catch (e) { pre.textContent = e.message; }
  diag.append(pre, button("Copy diagnostics", { cls: "btn btn-sm", iconName: "copy", onClick: async () => {
    try { await navigator.clipboard.writeText(pre.textContent); toast("Diagnostics copied."); } catch (e) { toast("Could not copy. Select the text and copy it.", "warn"); }
  } }));

  // trash
  const trash = section("Trash", "Browser profiles from sign-ins that failed or were refused are moved here instead of being deleted. They may hold a Google sign-in, so clear them when you no longer need them.");
  const tlist = el("ul", "plain");
  const drawTrash = async () => {
    const entries = await api("/api/trash");
    tlist.replaceChildren();
    if (!entries.length) tlist.append(el("li", "hint", "The trash is empty."));
    for (const e of entries) tlist.append(el("li", "mono", `${e.name} (${size(e.size_bytes)})`));
    clear.disabled = !entries.length;
  };
  const clear = button("Clear trash", { cls: "btn btn-sm btn-danger", iconName: "trash", onClick: async () => {
    const n = tlist.querySelectorAll("li.mono").length;
    if (!(await confirmDialog("Clear the trash?", `${plural(n, "folder")} will be removed for good. Your saved MP3s are not affected.`, "Clear trash", true))) return;
    try { const r = await api("/api/trash/clear", { method: "POST", body: { confirm: true } }); toast(`${plural(r.removed, "folder")} removed.`); drawTrash(); }
    catch (e) { toast(e.message, "bad"); }
  } });
  trash.append(tlist, clear);
  drawTrash();

  // about
  const about = section("About");
  about.append(el("p", "", `${document.title.split(" · ")[0]} ${prefs.version || ""}`.trim()));
  about.append(el("p", "hint", "Runs only on this computer. Reads from NotebookLM and never changes anything there. No tracking, no data sent anywhere except to Google for your own notebooks."));
  const det = el("details");
  det.append(el("summary", "", "Third-party notices"));
  const npre = el("pre", "diag");
  npre.tabIndex = 0;
  npre.setAttribute("role", "region");
  npre.setAttribute("aria-label", "Third-party notices text");
  det.addEventListener("toggle", async () => {
    if (!det.open || npre.textContent) return;
    try { const r = await fetch("/static/THIRD-PARTY-NOTICES.txt"); npre.textContent = await r.text(); } catch (e) { npre.textContent = "Could not load the notices."; }
  });
  det.append(npre);
  about.append(det);

  wrap.append(theme, naming, defaults, studioCard, notes, diag, trash, about);
  view.replaceChildren(wrap);
}
