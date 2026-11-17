// Settings: theme, naming template with live preview, default folder, default view,
// notifications, diagnostics (redacted), trash and about. Stored in <home>/settings.json.
import { api } from "./api.js";
import { $, el, button, size, plural } from "./dom.js";
import { toast, confirmDialog } from "./ui.js";
import { UNITS, toDisplay, checkPause, describe } from "./duration.js";
import { openFolderPicker, acctState } from "./accounts.js";

export const prefs = { settings: null, views: [], onTheme: () => {}, appWindow: false, windows: false, windowCapable: false };

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
  const notes = section("Notifications", "Messages in the page always appear. Optionally, you can also be told when a long harvest ends while the app is in the background.");
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
  if (prefs.appWindow) {
    // D-035: the app window cannot show browser notifications; the switch stays, disabled, and says why.
    nbox.disabled = true;
    nlab.classList.add("is-disabled");
    notes.append(el("p", "hint notify-window-note", "Notifications work when the app is open in your browser."));
  }

  // where the app opens (D-035, Windows and Mac)
  let wcard = null;
  if (prefs.windowCapable) {
    wcard = section("App window", "The app opens in its own window. Choose the browser instead if the window does not suit this computer.");
    const wlab = el("label", "field", "Open the app in");
    const wsel = el("select", "select");
    for (const [v, t] of [["window", "App window"], ["browser", "Browser"]]) { const o = el("option", "", t); o.value = v; wsel.append(o); }
    wsel.value = st.open_in || "window";
    wsel.addEventListener("change", () => save({ open_in: wsel.value }, "Saved. This takes effect the next time the app starts."));
    wlab.append(wsel);
    wcard.append(wlab);
  }

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
  /** "Pause between requests": a whole number plus seconds, minutes or hours; saved as seconds. */
  const pauseField = (min, max) => {
    const key = "studio_pause_s";
    const wrap = el("div", "field pause-field");
    const lbl = el("span", "", "Pause between requests");
    lbl.id = "pause-label";
    const row = el("div", "row");
    const inp = el("input", "input num");
    inp.id = "pause-value";
    inp.type = "number";
    inp.min = "1";
    inp.step = "1";
    inp.inputMode = "numeric";
    inp.setAttribute("aria-labelledby", "pause-label");
    const unit = el("select", "select pause-unit");
    unit.id = "pause-unit";
    unit.setAttribute("aria-label", "Unit for the pause between requests");
    for (const u of UNITS.slice().reverse()) { const o = el("option", "", u.label); o.value = u.key; unit.append(o); }
    const msg = el("p", "error pause-msg");
    msg.id = "pause-msg";
    msg.setAttribute("aria-live", "polite");
    const hint = el("p", "hint", `From ${describe(min)} to ${describe(max)}.`);
    hint.id = "pause-hint";
    inp.setAttribute("aria-describedby", "pause-hint pause-msg");
    const show = (seconds) => { const d = toDisplay(seconds); inp.value = String(d.value); unit.value = d.unit; };
    show(st[key]);
    const commit = async () => {
      const r = checkPause(inp.value, unit.value, min, max);
      if (r.error) { msg.textContent = r.error; inp.setAttribute("aria-invalid", "true"); return; }
      msg.textContent = "";
      inp.removeAttribute("aria-invalid");
      if (r.seconds === prefs.settings[key]) return;
      if (await save({ [key]: r.seconds }, `Pause between requests saved: ${describe(r.seconds)}.`)) show(r.seconds);
    };
    inp.addEventListener("change", commit);
    unit.addEventListener("change", commit);
    row.append(inp, unit);
    wrap.append(lbl, row, hint, msg);
    return wrap;
  };
  studioCard.append(
    numField("Overviews per account per day", "studio_daily_cap", 1, 20, "counted when a request is sent"),
    pauseField(30, 86400),
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

  // Phone (D-026): listen from a phone on the same Wi-Fi
  const phoneCard = section("Phone",
    "Listen to your saved overviews from a phone on the same Wi-Fi. The phone can browse, search and play; everything else stays on this laptop. It works only while this laptop is on and the app is running. The connection is plain HTTP on your home network, and nothing is opened to the internet.");
  phoneCard.id = "phone-card";
  await drawPhone(phoneCard);

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

  wrap.append(theme, naming, defaults, studioCard, phoneCard, notes, ...(wcard ? [wcard] : []), diag, trash, about);
  view.replaceChildren(wrap);
}


let phoneTimer = null;
let livePair = null; // {link, qr_svg, until}: kept so a redraw does not lose a code that still works

/** Fill the Phone card: the on/off switch, the address, pairing (link and QR code) and paired phones. */
async function drawPhone(card) {
  clearInterval(phoneTimer);
  const keep = [...card.children].slice(0, 2); // heading and hint
  card.replaceChildren(...keep);
  let ph;
  try { ph = await api("/api/phone"); } catch (e) { card.append(el("p", "error", e.message)); return; }
  const lab = el("label", "check");
  const box = el("input");
  box.type = "checkbox";
  box.id = "phone-enable";
  box.checked = ph.enabled;
  box.addEventListener("change", async () => {
    try { await api("/api/phone/enable", { method: "POST", body: { on: box.checked } }); toast(box.checked ? "Phone listening is on." : "Phone listening is off. Phones can no longer connect."); }
    catch (e) { toast(e.message, "bad"); }
    drawPhone(card);
  });
  lab.append(box, el("span", "lbl", "Allow my phone on this Wi-Fi"));
  card.append(lab);
  if (ph.enabled && !ph.listening) card.append(el("p", "error", `${ph.stopped_reason || "Phone listening is on but not running on this network."} Turn it off and on again to listen on the network you are on now.`));
  if (ph.listening) {
    const addr = el("p", "");
    addr.append(el("span", "hint", "Address for the phone: "), el("code", "phone-link", ph.url));
    const firewall = acctState.windows
      ? "The first time, Windows Security asks about this app. Choose Allow, not Cancel: Cancel blocks the phone. If Windows treats this Wi-Fi as a Public network, tick Public networks in that window, or set this Wi-Fi to Private in Windows Settings (Network and internet, then Wi-Fi, then this network)."
      : "The first time, this computer may ask whether to allow incoming connections. Allow it on your home network only.";
    const fw = el("p", "hint", firewall);
    fw.id = "phone-firewall-hint";
    card.append(addr, fw);
    const home = el("details", "phone-home");
    home.id = "phone-home-help";
    home.append(el("summary", "", "Put it on the phone's home screen"));
    home.append(
      el("p", "hint", "After pairing, add a shortcut so the page opens like an app: on Android, open the browser menu and choose Add to Home screen; on iPhone, tap Share, then Add to Home Screen."),
      el("p", "hint", "It is a shortcut to this page, not an offline app: it plays only while this laptop is on, the app is running and the phone is on the same Wi-Fi. If the laptop's address changes or the phone is removed here, open the new address or pair again."),
    );
    card.append(home);
    const pairBox = el("div", "phone-pair");
    pairBox.id = "phone-pair";
    const pairBtn = button("Pair a phone", { cls: "btn btn-primary", iconName: "plus", onClick: async () => {
      try {
        const r = await api("/api/phone/pair", { method: "POST" });
        livePair = { link: r.link, qr_svg: r.qr_svg, until: Date.now() + r.expires_s * 1000 };
        showPair(r);
      } catch (e) { toast(e.message, "bad"); }
    } });
    const showPair = (r) => {
        const qr = el("div", "phone-qr");
        qr.innerHTML = r.qr_svg; // SVG made on the laptop by the app, no user text in it
        qr.setAttribute("role", "img");
        qr.setAttribute("aria-label", "QR code for the pairing link");
        const link = el("p", "phone-link", r.link);
        link.id = "phone-pair-link";
        const left = el("p", "hint");
        left.id = "phone-pair-left";
        let t = r.expires_s;
        const tick = () => { left.textContent = t > 0 ? `Scan the code with the phone's camera, or open the link on the phone. It works once and expires in ${Math.floor(t / 60)} min ${String(t % 60).padStart(2, "0")} s.` : "This pairing link has expired. Press Pair a phone again."; t -= 1; if (t < -1) clearInterval(phoneTimer); };
        clearInterval(phoneTimer);
        tick();
        phoneTimer = setInterval(tick, 1000);
        pairBox.replaceChildren(pairBtn, qr, link, left);
    };
    pairBtn.id = "phone-pair-btn";
    pairBox.append(pairBtn);
    card.append(pairBox);
    if (livePair && livePair.until > Date.now() + 1000 && ph.code_left_s) {
      showPair({ ...livePair, expires_s: Math.min(ph.code_left_s, Math.round((livePair.until - Date.now()) / 1000)) });
    }
  }
  card.append(el("h3", "", "Paired phones"));
  if (!ph.devices.length) card.append(el("p", "hint", "No phone is paired yet."));
  else {
    const list = el("ul", "phone-devices");
    list.id = "phone-devices";
    for (const d of ph.devices) {
      const li = el("li");
      li.append(el("strong", "grow", d.name), el("span", "hint", d.last_seen ? `Last used ${new Date(d.last_seen).toLocaleString()}` : "Not used yet"));
      li.append(button("Remove", { cls: "btn btn-sm btn-danger", title: `Remove ${d.name}`, onClick: async () => {
        try { await api(`/api/phone/devices/${d.id}/revoke`, { method: "POST" }); toast(`${d.name} can no longer connect.`); } catch (e) { toast(e.message, "bad"); }
        drawPhone(card);
      } }));
      list.append(li);
    }
    card.append(list);
  }
}

