// History view (basic list; U2 brings server paging, filters, player and export).
import { api } from "./api.js";
import { $, el, button, shortDate, duration, size } from "./dom.js";
import { toast } from "./ui.js";
import { openFolder } from "./accounts.js";

/** Draw the saved files. */
export async function renderHistory() {
  const view = $("view-history");
  view.replaceChildren(el("div", "skeleton"));
  try {
    const rows = await api("/api/history");
    const list = el("ul", "hlist");
    if (!rows.length) {
      const e = el("div", "empty");
      e.append(el("h2", "", "Nothing saved yet"), el("p", "", "Harvest an overview from the Library and it appears here."));
      view.replaceChildren(e);
      return;
    }
    for (const r of rows) {
      const li = el("li", "hitem");
      li.append(el("div", "ht", r.title));
      li.append(el("div", "muted", `${r.notebook} · ${r.account} · ${duration(r.duration_s)} · ${size(r.size_bytes)} · saved ${shortDate(r.saved_at)}`));
      li.append(el("div", "hp", r.path));
      const row = el("div", "row");
      if (!r.present) row.append(el("span", `pill ${r.reachable ? "st-saved_but_missing" : "st-folder_unreachable"}`, r.reachable ? "File missing" : "Folder not reachable"));
      row.append(button("Show in folder", { cls: "btn btn-sm", onClick: () => openFolder(r.folder) }));
      li.append(row);
      list.append(li);
    }
    view.replaceChildren(list);
  } catch (e) {
    toast(e.message, "bad");
  }
}
