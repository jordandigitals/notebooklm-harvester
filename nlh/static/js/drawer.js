// Details drawer: every field for one overview, with copy buttons. Non-modal: focus moves
// in on open, Esc or Close returns it to where it was, and the page stays usable.
import { api } from "./api.js";
import { $, el, button, pill, shortDate, duration, size } from "./dom.js";
import { toast } from "./ui.js";
import { openFolder } from "./accounts.js";
import { play } from "./player.js";

let returnFocus = null;

function field(dl, label, value, copy = null) {
  if (value === null || value === undefined || value === "") return;
  const dt = el("dt", "", label);
  const dd = el("dd");
  dd.append(el("span", "", String(value)));
  if (copy !== null) {
    dd.append(button("", {
      cls: "btn btn-ghost btn-icon btn-sm", iconName: "copy", title: `Copy ${label.toLowerCase()}`,
      onClick: async () => {
        try { await navigator.clipboard.writeText(String(copy)); toast(`${label} copied.`); }
        catch (e) { toast("Could not copy. Select the text and copy it.", "warn"); }
      },
    }));
  }
  dl.append(dt, dd);
}

/** Close the drawer. */
export function closeDrawer() {
  const d = $("drawer");
  if (!d || d.classList.contains("is-hidden")) return false;
  d.classList.add("is-hidden");
  if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
  return true;
}

/** Open the drawer for a library row (account given for its label). */
export async function openDrawer(row, account, opts = {}) {
  returnFocus = document.activeElement;
  let d = $("drawer");
  if (!d) {
    d = el("aside", "drawer is-hidden");
    d.id = "drawer";
    d.setAttribute("aria-labelledby", "drawer-title");
    d.setAttribute("role", "complementary");
    document.body.append(d);
  }
  const head = el("div", "drawer-head");
  const h = el("h2", "", row.title);
  h.id = "drawer-title";
  const close = button("", { cls: "btn btn-ghost btn-icon", iconName: "x", title: "Close details" });
  close.addEventListener("click", closeDrawer);
  head.append(h, close);
  const body = el("div", "drawer-body");
  body.append(pill(row.status));
  const dl = el("dl", "kv");
  field(dl, "Notebook", row.notebook);
  field(dl, "Account", account ? `${account.label}${account.email ? ` (${account.email})` : ""}` : "");
  field(dl, "Created", shortDate(row.created_at));
  field(dl, "Overview id", row.artifact_id.slice(0, 14), row.artifact_id);
  field(dl, "Notebook id", (row.notebook_id || "").slice(0, 14), row.notebook_id);
  if (row.last_error) field(dl, "Last error", row.last_error);
  body.append(dl);
  const savedBox = el("div", "drawer-saved");
  savedBox.append(el("p", "hint", row.status === "new" ? "Not saved yet." : "Loading saved file details..."));
  body.append(savedBox);
  d.replaceChildren(head, body);
  d.classList.remove("is-hidden");
  close.focus();
  try {
    const info = await api(`/api/item/${encodeURIComponent(row.artifact_id)}`);
    savedBox.replaceChildren();
    if (info.saved) {
      const s = info.saved;
      const kv = el("dl", "kv");
      field(kv, "Saved", shortDate(s.saved_at));
      field(kv, "File", s.path, s.path);
      field(kv, "Length", duration(s.duration_s));
      field(kv, "Size", size(s.size_bytes));
      field(kv, "Hash", (s.sha256 || "").slice(0, 16), s.sha256);
      field(kv, "Found by", { harvest: "", studio: "Made in Studio", relinked: "Scan folder (moved file)", scan: "Scan folder" }[s.origin] ?? "");
      savedBox.append(el("h3", "", "Saved file"), kv);
      if (info.tags && Object.keys(info.tags).length) {
        const tk = el("dl", "kv");
        for (const k of ["title", "album", "artist", "date", "comment"]) field(tk, k[0].toUpperCase() + k.slice(1), info.tags[k]);
        savedBox.append(el("h3", "", "Tags written in the file"), tk);
      }
      const actions = el("div", "row");
      if (s.playable) actions.append(button("Play", { cls: "btn btn-sm btn-primary", iconName: "play", onClick: () => play(s.id, s.title, s.notebook) }));
      else if (s.present && s.play_reason) actions.append(el("p", "hint", s.play_reason));
      if (s.present) actions.append(button("Show in folder", { cls: "btn btn-sm", iconName: "folder", onClick: () => openFolder(s.folder) }));
      if (!s.present && s.reachable && opts.startJob) {
        actions.append(button("Download again", { cls: "btn btn-sm", iconName: "download", onClick: () => opts.startJob(api(`/api/history/${s.id}/redownload`, { method: "POST" })) }));
      }
      if (!s.present && !s.reachable) actions.append(el("p", "hint", "The folder that holds this file is not reachable. Plug in the drive, then refresh."));
      savedBox.append(actions);
    } else if (row.status !== "new") {
      savedBox.append(el("p", "hint", "No saved file for this overview."));
    }
    if (info.attempts.length) {
      const list = el("ul", "attempts");
      for (const a of info.attempts) {
        const word = { saved: "Saved", failed: "Failed", cancelled: "Cancelled" }[a.outcome] || a.outcome;
        list.append(el("li", "", `${shortDate(a.started_at)}: ${word}${a.message && a.outcome !== "saved" ? `, ${a.message}` : ""}`));
      }
      savedBox.append(el("h3", "", "Harvest attempts"), list);
    }
  } catch (e) {
    savedBox.replaceChildren(el("p", "error", e.message));
  }
}
