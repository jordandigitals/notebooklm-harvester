// History view: server-side paging (the ledger only grows), filters, sort, row actions,
// export of the whole filtered ledger (pagination ruling 2026-11-14).
import { api } from "./api.js";
import { $, el, icon, button, show, shortDate, duration, size, plural } from "./dom.js";
import { toast, popover, closePopover } from "./ui.js";
import { openFolder } from "./accounts.js";
import { play } from "./player.js";
import { fold } from "./search.js";

export const hist = {
  page: 1,
  size: 50,
  sort: "saved_desc",
  q: "",
  accounts: [],
  notebooks: [],
  savedFrom: "", savedTo: "",
  sizeMin: "", sizeMax: "", lengthMin: "", lengthMax: "",
  total: 0,
  rows: [],
  accountList: [],
  notebookBase: "",
  startJob: null,
  saveSize: () => {},
};

const SORTS = [
  ["saved_desc", "Saved, newest"], ["saved_asc", "Saved, oldest"], ["title_asc", "Title A to Z"],
  ["title_desc", "Title Z to A"], ["notebook_asc", "Notebook"], ["size_desc", "Largest"],
  ["size_asc", "Smallest"], ["length_desc", "Longest"], ["length_asc", "Shortest"],
];

function phone() { return window.matchMedia("(max-width: 767px)").matches; }

/** Query string for the server: the same filters drive the page, the export and stats. */
export function historyQuery(extra = {}) {
  const p = new URLSearchParams();
  if (hist.q) p.set("q", hist.q);
  if (hist.accounts.length) p.set("accounts", hist.accounts.join(","));
  for (const n of hist.notebooks) p.append("notebook", n);
  if (hist.savedFrom) p.set("saved_from", hist.savedFrom);
  if (hist.savedTo) p.set("saved_to", hist.savedTo);
  if (hist.sizeMin !== "") p.set("size_min", String(Number(hist.sizeMin) * 1e6));
  if (hist.sizeMax !== "") p.set("size_max", String(Number(hist.sizeMax) * 1e6));
  if (hist.lengthMin !== "") p.set("length_min", String(Number(hist.lengthMin) * 60));
  if (hist.lengthMax !== "") p.set("length_max", String(Number(hist.lengthMax) * 60));
  p.set("sort", hist.sort);
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

async function fetchPage(append = false) {
  const data = await api(`/api/history/page?${historyQuery({ page: hist.page, size: hist.size })}`);
  hist.total = data.total;
  hist.rows = append ? [...hist.rows, ...data.rows] : data.rows;
}

/** Draw the history view (fetches the current page). */
export async function renderHistory({ keepPage = false } = {}) {
  const view = $("view-history");
  if (!keepPage) hist.page = 1;
  if (!view.dataset.built) build(view);
  $("h-body").replaceChildren(el("div", "skeleton"), el("div", "skeleton"));
  try { await fetchPage(); } catch (e) { toast(e.message, "bad"); return; }
  drawRows();
}

function build(view) {
  view.dataset.built = "1";
  view.replaceChildren();
  const bar = el("div", "toolbar");
  const search = el("input", "input grow");
  search.type = "search";
  search.placeholder = "Search saved files: title, notebook, account, path";
  search.setAttribute("aria-label", "Search saved files");
  search.value = hist.q;
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { hist.q = search.value.trim(); renderHistory(); }, 200); });
  const sortLab = el("label", "lbl-inline", "Sort");
  const sort = el("select", "select");
  for (const [v, l] of SORTS) { const o = el("option", "", l); o.value = v; sort.append(o); }
  sort.value = hist.sort;
  sort.addEventListener("change", () => { hist.sort = sort.value; renderHistory(); });
  sortLab.append(sort);
  const filt = button("Filters", { cls: "btn btn-sm", iconName: "filter" });
  filt.id = "h-filters";
  filt.addEventListener("click", (e) => openFilters(e.currentTarget));
  const exp = button("Export", { cls: "btn btn-sm", iconName: "export" });
  exp.addEventListener("click", (e) => {
    const menu = el("div", "menu");
    for (const fmt of ["csv", "json"]) {
      const a = el("a", "btn btn-ghost", fmt === "csv" ? "CSV (spreadsheet)" : "JSON");
      a.href = `/api/export.${fmt}?${historyQuery()}`;
      a.setAttribute("download", "");
      a.addEventListener("click", () => setTimeout(closePopover, 100));
      menu.append(a);
    }
    menu.append(el("p", "hint", `Exports all ${plural(hist.total, "matching file")}, not only this page. No sign-in data is included.`));
    popover(e.currentTarget, menu, "Export");
  });
  bar.append(search, sortLab, filt, exp);
  const chips = el("div", "chips");
  chips.id = "h-chips";
  const info = el("div", "resultbar");
  info.id = "h-info";
  const body = el("div", "hbody");
  body.id = "h-body";
  const pager = el("div", "pager");
  pager.id = "h-pager";
  view.append(bar, chips, info, body, pager);
}

function drawChips() {
  const box = $("h-chips");
  box.replaceChildren();
  const add = (label, clear) => {
    const c = el("span", "chip");
    c.append(el("span", "lbl", label));
    const b = button("", { cls: "", iconName: "x", title: `Remove filter: ${label}` });
    b.addEventListener("click", () => { clear(); renderHistory(); });
    c.append(b);
    box.append(c);
  };
  for (const id of hist.accounts) add(`Account: ${hist.accountList.find((a) => a.id === id)?.label || id}`, () => { hist.accounts = hist.accounts.filter((x) => x !== id); });
  for (const n of hist.notebooks) add(`Notebook: ${n}`, () => { hist.notebooks = hist.notebooks.filter((x) => x !== n); });
  if (hist.savedFrom || hist.savedTo) add(`Saved ${hist.savedFrom || "..."} to ${hist.savedTo || "..."}`, () => { hist.savedFrom = ""; hist.savedTo = ""; });
  if (hist.sizeMin !== "" || hist.sizeMax !== "") add(`Size ${hist.sizeMin || 0} to ${hist.sizeMax || "any"} MB`, () => { hist.sizeMin = ""; hist.sizeMax = ""; });
  if (hist.lengthMin !== "" || hist.lengthMax !== "") add(`Length ${hist.lengthMin || 0} to ${hist.lengthMax || "any"} min`, () => { hist.lengthMin = ""; hist.lengthMax = ""; });
}

async function openFilters(anchor) {
  const box = el("div", "filters");
  const accSec = el("section");
  accSec.append(el("h3", "", "Accounts"));
  for (const a of hist.accountList) {
    const lab = el("label", "check");
    const c = el("input");
    c.type = "checkbox";
    c.checked = hist.accounts.includes(a.id);
    c.addEventListener("change", () => { hist.accounts = c.checked ? [...hist.accounts, a.id] : hist.accounts.filter((x) => x !== a.id); renderHistory(); });
    lab.append(c, el("span", "lbl", a.label));
    accSec.append(lab);
  }
  const nbSec = el("section");
  nbSec.append(el("h3", "", "Notebooks"));
  const nbs = await api("/api/history/notebooks");
  const find = el("input", "input");
  find.type = "search";
  find.placeholder = "Find a notebook";
  find.setAttribute("aria-label", "Find a notebook");
  const opts = el("div", "opts");
  const draw = () => {
    opts.replaceChildren();
    const f = fold(find.value);
    const shown = nbs.filter((n) => !f || fold(n.name).includes(f)).slice(0, 200);
    for (const n of shown) {
      const lab = el("label", "check");
      const c = el("input");
      c.type = "checkbox";
      c.checked = hist.notebooks.includes(n.name);
      c.addEventListener("change", () => { hist.notebooks = c.checked ? [...hist.notebooks, n.name] : hist.notebooks.filter((x) => x !== n.name); renderHistory(); });
      lab.append(c, el("span", "lbl", n.name), el("span", "count", String(n.count)));
      opts.append(lab);
    }
    if (!shown.length) opts.append(el("p", "hint", "No match."));
  };
  find.addEventListener("input", draw);
  draw();
  if (nbs.length > 8) nbSec.append(find);
  nbSec.append(opts);
  const range = (title, a, b, unit, type = "number") => {
    const sec = el("section");
    sec.append(el("h3", "", title));
    const row = el("div", "row");
    for (const [key, lbl] of [[a, "From"], [b, "To"]]) {
      const lab = el("label", "field grow", `${lbl}${unit ? ` (${unit})` : ""}`);
      const inp = el("input", "input");
      inp.type = type;
      if (type === "number") inp.min = "0";
      inp.value = hist[key];
      inp.addEventListener("change", () => { hist[key] = inp.value; renderHistory(); });
      lab.append(inp);
      row.append(lab);
    }
    sec.append(row);
    return sec;
  };
  box.append(accSec, nbSec, range("Saved date", "savedFrom", "savedTo", "", "date"),
    range("Size", "sizeMin", "sizeMax", "MB"), range("Length", "lengthMin", "lengthMax", "minutes"));
  popover(anchor, box, "History filters");
}

function actions(r) {
  const row = el("div", "hactions");
  if (r.playable) {
    row.append(button("Play", { cls: "btn btn-sm", iconName: "play", onClick: () => play(r.id, r.title, r.notebook) }));
  }
  if (r.present) row.append(button("Show in folder", { cls: "btn btn-sm btn-ghost", iconName: "folder", onClick: () => openFolder(r.folder) }));
  row.append(button("", {
    cls: "btn btn-sm btn-ghost btn-icon", iconName: "copy", title: "Copy path",
    onClick: async () => { try { await navigator.clipboard.writeText(r.path); toast("Path copied."); } catch (e) { toast("Could not copy. Select the path and copy it.", "warn"); } },
  }));
  if (!r.present && r.reachable) {
    row.append(button("Download again", {
      cls: "btn btn-sm", iconName: "download",
      onClick: () => hist.startJob(api(`/api/history/${r.id}/redownload`, { method: "POST" })),
    }));
  }
  if (hist.notebookBase && r.notebook_id) {
    const a = el("a", "btn btn-sm btn-ghost btn-icon");
    a.href = `${hist.notebookBase}notebook/${encodeURIComponent(r.notebook_id)}`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = "Open notebook in NotebookLM";
    a.setAttribute("aria-label", `Open notebook ${r.notebook} in NotebookLM`);
    a.append(icon("external", "icon icon-sm"));
    row.append(a);
  }
  return row;
}

function status(r) {
  if (r.present) return null;
  return el("span", `pill ${r.reachable ? "st-saved_but_missing" : "st-folder_unreachable"}`, r.reachable ? "File missing" : "Folder not reachable");
}

function drawRows() {
  drawChips();
  const info = $("h-info");
  info.replaceChildren();
  const from = hist.total ? (hist.page - 1) * hist.size + 1 : 0;
  const to = phone() ? hist.rows.length : Math.min(hist.total, hist.page * hist.size);
  const strong = el("strong", "", hist.total.toLocaleString());
  info.setAttribute("role", "status");
  info.append(strong, document.createTextNode(` saved ${hist.total === 1 ? "file" : "files"}`));
  if (hist.total) info.append(el("span", "muted", ` · showing ${phone() ? 1 : from.toLocaleString()} to ${to.toLocaleString()}`));
  const body = $("h-body");
  body.replaceChildren();
  if (!hist.rows.length) {
    const e = el("div", "empty");
    e.append(icon("history"), el("h2", "", hist.q || hist.accounts.length || hist.notebooks.length ? "No saved files match" : "Nothing saved yet"),
      el("p", "", hist.q || hist.accounts.length ? "Clear the search or filters to see everything." : "Harvest an overview from the Library and it appears here."));
    body.append(e);
    $("h-pager").replaceChildren();
    return;
  }
  if (phone()) {
    const list = el("ul", "hcards");
    for (const r of hist.rows) {
      const li = el("li", "hcard");
      li.append(el("div", "ht", r.title));
      li.append(el("div", "hint", `${r.notebook} · ${r.account}`));
      li.append(el("div", "hint", `${duration(r.duration_s)} · ${size(r.size_bytes)} · saved ${shortDate(r.saved_at)}`));
      li.append(el("div", "hp", r.path));
      const st = status(r);
      if (st) li.append(st);
      li.append(actions(r));
      list.append(li);
    }
    body.append(list);
  } else {
    const table = el("table", "htable");
    const head = el("thead");
    const tr = el("tr");
    for (const h of ["Title", "Notebook", "Account", "Saved", "Length", "Size", "Actions"]) {
      const th = el("th", "", h);
      th.scope = "col";
      tr.append(th);
    }
    head.append(tr);
    const tb = el("tbody");
    for (const r of hist.rows) {
      const row = el("tr");
      const t = el("td", "tt");
      t.append(el("div", "ht", r.title), el("div", "hp", r.file_name));
      const st = status(r);
      if (st) t.append(st);
      row.append(t, el("td", "", r.notebook), el("td", "", r.account), el("td", "nowrap", shortDate(r.saved_at)),
        el("td", "num", duration(r.duration_s)), el("td", "num", size(r.size_bytes)));
      const act = el("td");
      act.append(actions(r));
      row.append(act);
      tb.append(row);
    }
    table.append(head, tb);
    const wrap = el("div", "hwrap");
    wrap.append(table);
    body.append(wrap);
  }
  drawPager();
}

function drawPager() {
  const p = $("h-pager");
  p.replaceChildren();
  const pages = Math.max(1, Math.ceil(hist.total / hist.size));
  if (phone()) {
    if (hist.rows.length < hist.total) {
      p.append(button(`Load more (${(hist.total - hist.rows.length).toLocaleString()} left)`, {
        cls: "btn", onClick: async () => { hist.page += 1; await fetchPage(true); drawRows(); },
      }));
    }
    return;
  }
  const prev = button("Previous", { cls: "btn btn-sm", onClick: () => { hist.page -= 1; renderHistory({ keepPage: true }); } });
  prev.disabled = hist.page <= 1;
  const next = button("Next", { cls: "btn btn-sm", onClick: () => { hist.page += 1; renderHistory({ keepPage: true }); } });
  next.disabled = hist.page >= pages;
  const jumpLab = el("label", "lbl-inline", "Page");
  const jump = el("input", "input pager-jump");
  jump.type = "number";
  jump.min = "1";
  jump.max = String(pages);
  jump.value = String(hist.page);
  jump.addEventListener("change", () => {
    const n = Math.min(pages, Math.max(1, Number(jump.value) || 1));
    hist.page = n;
    renderHistory({ keepPage: true });
  });
  jumpLab.append(jump, document.createTextNode(` of ${pages.toLocaleString()}`));
  const sizeLab = el("label", "lbl-inline", "Per page");
  const sel = el("select", "select");
  for (const n of [50, 100, 250]) { const o = el("option", "", String(n)); o.value = String(n); sel.append(o); }
  sel.value = String(hist.size);
  sel.addEventListener("change", () => { hist.size = Number(sel.value); hist.saveSize(hist.size); renderHistory(); });
  sizeLab.append(sel);
  p.append(prev, jumpLab, next, el("span", "spacer"), sizeLab);
}

/** Test hook: what the history shows. */
export function historyHooks() {
  return { ids: () => hist.rows.map((r) => r.artifact_id), total: () => hist.total, page: () => hist.page };
}
