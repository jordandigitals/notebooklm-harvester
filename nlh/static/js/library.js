// Library view: search, filters, sort, grouping, virtual list, selection, saved views.
// The whole cached listing stays in memory; search and filters run over all of it, and
// only the rows in view (plus a buffer) exist in the page (pagination ruling 2026-11-14).
import { api } from "./api.js";
import { $, el, icon, button, pill, show, shortDate, duration, size, plural, STATUS_TEXT } from "./dom.js";
import { toast, confirmDialog, promptDialog, popover, closePopover } from "./ui.js";
import { openDrawer } from "./drawer.js";
import {
  fold, parseQuery, haystack, matchesQuery, matchesFilters, comparator, highlightRanges,
} from "./search.js";

const ROW_H = 64;
const GROUP_H = 44;
const BUFFER = 8;
const SELECTABLE = new Set(["new", "failed", "saved_but_missing"]);
const STATUS_LIST = ["new", "saved", "saved_but_missing", "folder_unreachable", "in_progress", "failed"];
const BIG_HARVEST = 20;

export const lib = {
  rows: [],
  accounts: new Map(),
  neverRefreshed: [],
  loaded: false,
  query: "",
  parsed: parseQuery(""),
  scope: "all",
  account: null, // the selected account id (sidebar and "This account")
  filters: emptyFilters(),
  sort: "newest",
  group: "none",
  collapsed: new Set(),
  selection: new Set(),
  filtered: [],
  items: [],
  offsets: [],
  focus: -1,
  anchor: -1,
  views: [],
  demo: false,
  perf: [],
  startJob: null,
  onChange: () => {},
};

export function emptyFilters() {
  return { status: [], account: [], notebook: [], created: null, saved: null, multi: false, clash: false,
    moved: false, include: "", exclude: "" };
}

// ------------------------------------------------------------ data

/** Load the flat library from the server and prepare the search strings once. */
export async function loadLibrary() {
  const data = await api("/api/library");
  lib.accounts = new Map(data.accounts.map((a) => [a.id, a]));
  lib.neverRefreshed = data.never_refreshed;
  lib.rows = data.rows;
  for (const r of lib.rows) {
    const a = lib.accounts.get(r.account_id);
    r._hay = haystack(r, a);
    r._acct = fold(a ? `${a.label} ${a.email || ""}` : "");
    r._key = r.artifact_id;
    r._ft = fold(r.title);
  }
  const keys = new Set(lib.rows.map((r) => r._key));
  for (const k of [...lib.selection]) if (!keys.has(k)) lib.selection.delete(k);
  lib.loaded = true;
  if (lib.account === null && data.accounts.length) lib.account = data.accounts[0].id;
  if (lib.account !== null && !lib.accounts.has(lib.account)) lib.account = data.accounts.length ? data.accounts[0].id : null;
  apply({ keepScroll: true });
  renderFilters();
}

/** Rows in scope and matching the search (before the filter panel). */
function scopedRows() {
  const q = lib.parsed;
  const scope = lib.scope === "account" ? lib.account : null;
  const out = [];
  for (const r of lib.rows) {
    if (scope !== null && r.account_id !== scope) continue;
    if (!matchesQuery(r, r._hay, r._acct, q)) continue;
    out.push(r);
  }
  return out;
}

/** Recompute the filtered, sorted, grouped list and repaint. */
export function apply({ keepScroll = false } = {}) {
  const t0 = performance.now();
  const now = new Date();
  const base = scopedRows();
  lib.filtered = base.filter((r) => matchesFilters(r, lib.filters, now)).sort(comparator(lib.sort));
  buildItems();
  const list = $("vlist");
  if (!keepScroll && list) list.scrollTop = 0;
  render();
  renderChips();
  renderCounts();
  renderActionBar();
  lib.onChange();
  if (lib.demo) {
    requestAnimationFrame(() => { lib.perf.push(performance.now() - t0); if (lib.perf.length > 200) lib.perf.shift(); });
  }
}

function groupKey(r) {
  switch (lib.group) {
    case "notebook": return [r.notebook_id, r.notebook || "Untitled notebook"];
    case "account": { const a = lib.accounts.get(r.account_id); return [String(r.account_id), a ? a.label : "Account"]; }
    case "month": {
      const m = (r.created_at || "").slice(0, 7);
      if (!m) return ["none", "No date"];
      const d = new Date(`${m}-01T00:00:00Z`);
      return [m, d.toLocaleDateString(undefined, { year: "numeric", month: "long", timeZone: "UTC" })];
    }
    case "status": return [r.status, STATUS_TEXT[r.status] || r.status];
    default: return null;
  }
}

function buildItems() {
  const items = [];
  if (lib.group === "none") {
    for (const r of lib.filtered) items.push({ type: "row", row: r });
  } else {
    const groups = new Map();
    for (const r of lib.filtered) {
      const [k, label] = groupKey(r);
      if (!groups.has(k)) groups.set(k, { key: k, label, rows: [] });
      groups.get(k).rows.push(r);
    }
    for (const g of groups.values()) {
      const counts = { new: 0, saved: 0 };
      for (const r of g.rows) if (r.status in counts) counts[r.status]++;
      items.push({ type: "group", group: g, counts });
      if (!lib.collapsed.has(g.key)) for (const r of g.rows) items.push({ type: "row", row: r, group: g.key });
    }
  }
  lib.items = items;
  lib.offsets = new Array(items.length + 1);
  let y = 0;
  for (let i = 0; i < items.length; i++) {
    lib.offsets[i] = y;
    y += items[i].type === "group" ? GROUP_H : ROW_H;
  }
  lib.offsets[items.length] = y;
  if (lib.focus >= items.length) lib.focus = items.length - 1;
}

function indexAt(y) {
  let lo = 0, hi = lib.items.length - 1, ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lib.offsets[mid] <= y) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

// ------------------------------------------------------------ rendering

let rafPending = false;
/** Repaint only the rows in view. */
export function render() {
  const list = $("vlist"), spacer = $("vspacer");
  if (!list) return;
  spacer.style.setProperty("height", `${lib.offsets[lib.items.length] || 0}px`);
  spacer.replaceChildren();
  if (!lib.loaded) {
    for (let i = 0; i < 8; i++) spacer.append(el("div", "skeleton"));
    spacer.style.setProperty("height", "auto");
    return;
  }
  if (!lib.items.length) {
    spacer.style.setProperty("height", "auto");
    spacer.append(emptyState());
    $("sticky-group").replaceChildren();
    show($("backtop"), false);
    return;
  }
  const top = list.scrollTop, h = list.clientHeight || 600;
  const start = Math.max(0, indexAt(top) - BUFFER);
  const end = Math.min(lib.items.length, indexAt(top + h) + BUFFER + 1);
  const frag = document.createDocumentFragment();
  for (let i = start; i < end; i++) frag.append(renderItem(lib.items[i], i));
  spacer.append(frag);
  renderSticky(top);
  show($("backtop"), top > 600);
}

function onScroll() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; render(); });
}

function renderSticky(top) {
  const holder = $("sticky-group");
  holder.replaceChildren();
  if (lib.group === "none" || top <= 0) return;
  let i = indexAt(top);
  while (i >= 0 && lib.items[i].type !== "group") i--;
  if (i < 0) return;
  const node = renderGroup(lib.items[i], i);
  node.classList.add("sticky-group");
  node.style.setProperty("top", "0px");
  holder.append(node);
}

function renderItem(item, i) {
  const node = item.type === "group" ? renderGroup(item, i) : renderRow(item.row, i);
  node.style.setProperty("top", `${lib.offsets[i]}px`);
  return node;
}

function renderGroup(item, i) {
  const g = item.group;
  const b = el("button", "vgroup");
  b.type = "button";
  b.setAttribute("aria-expanded", lib.collapsed.has(g.key) ? "false" : "true");
  b.append(icon("chevron", "icon chev"), el("span", "gname", g.label),
    el("span", "gcount", `${g.rows.length} · ${item.counts.new} new · ${item.counts.saved} saved`));
  b.addEventListener("click", () => {
    if (lib.collapsed.has(g.key)) lib.collapsed.delete(g.key); else lib.collapsed.add(g.key);
    buildItems();
    render();
    lib.onChange();
  });
  b.dataset.index = i;
  return b;
}

function highlighted(text) {
  const span = el("span");
  const ranges = highlightRanges(text, lib.parsed.words);
  let pos = 0;
  for (const [a, b] of ranges) {
    if (a > pos) span.append(document.createTextNode(text.slice(pos, a)));
    span.append(el("mark", "", text.slice(a, b)));
    pos = b;
  }
  if (pos < text.length) span.append(document.createTextNode(text.slice(pos)));
  return span;
}

function renderRow(r, i) {
  const row = el("div", "vrow");
  row.setAttribute("role", "listitem");
  row.dataset.index = i;
  row.dataset.id = r._key;
  const selected = lib.selection.has(r._key);
  if (selected) row.classList.add("is-selected");
  if (i === lib.focus) row.classList.add("is-focus");
  const cell = el("span");
  if (SELECTABLE.has(r.status)) {
    const box = el("input");
    box.type = "checkbox";
    box.checked = selected;
    box.setAttribute("aria-label", `Select ${r.title}`);
    box.addEventListener("click", (e) => toggle(r, i, e.shiftKey));
    cell.append(box);
  }
  const body = el("div", "rbody");
  const title = el("div", "rtitle");
  title.append(highlighted(r.title));
  title.title = r.title;
  const meta = el("div", "rmeta");
  const nb = el("span");
  nb.append(highlighted(r.notebook || ""));
  meta.append(nb);
  if (lib.scope === "all") {
    const a = lib.accounts.get(r.account_id);
    if (a) meta.append(el("span", "rbadge", a.label));
  }
  meta.append(el("span", "sep", "·"), el("span", "", shortDate(r.created_at)));
  if (r.duration_s) meta.append(el("span", "sep", "·"), el("span", "", duration(r.duration_s)));
  if (r.size_bytes) meta.append(el("span", "sep", "·"), el("span", "", size(r.size_bytes)));
  body.append(title, meta);
  body.addEventListener("click", () => { lib.focus = i; openDetails(r); });
  const right = el("div", "rright");
  right.append(pill(r.status));
  row.append(cell, body, right);
  return row;
}

function emptyState() {
  const box = el("div", "empty");
  if (!lib.accounts.size) {
    box.append(icon("user"), el("h2", "", "No accounts yet"),
      el("p", "", "Add an account to see its notebooks: use Add account and pick a Chrome profile or sign in."));
    return box;
  }
  const scopeAcct = lib.scope === "account" ? lib.accounts.get(lib.account) : null;
  if (scopeAcct && lib.neverRefreshed.includes(scopeAcct.id)) {
    box.append(icon("refresh"), el("h2", "", "Not read yet"),
      el("p", "", `Press Refresh notebooks to read ${scopeAcct.label}'s notebooks.`));
    return box;
  }
  if (!lib.rows.length) {
    box.append(icon("refresh"), el("h2", "", "No notebooks read yet"),
      el("p", "", "Press Refresh notebooks on an account to read its audio overviews."));
    return box;
  }
  box.append(icon("search"), el("h2", "", "No matching overviews"));
  const what = [];
  if (lib.query) what.push(`search "${lib.query}"`);
  const n = activeFilterCount();
  if (n) what.push(plural(n, "filter"));
  if (lib.scope === "account") what.push("this account only");
  box.append(el("p", "", `Nothing matches ${what.join(", ") || "the current view"}. Clear the filters or search to see everything.`));
  const b = button("Clear search and filters", { cls: "btn", onClick: () => clearAll() });
  box.append(b);
  return box;
}

// ------------------------------------------------------------ selection

function toggle(r, i, shift) {
  const rowsInView = lib.items.map((it) => it.type === "row" ? it.row : null);
  if (shift && lib.anchor >= 0) {
    const [a, b] = lib.anchor < i ? [lib.anchor, i] : [i, lib.anchor];
    const target = !lib.selection.has(r._key);
    for (let k = a; k <= b; k++) {
      const row = rowsInView[k];
      if (row && SELECTABLE.has(row.status)) { if (target) lib.selection.add(row._key); else lib.selection.delete(row._key); }
    }
  } else if (lib.selection.has(r._key)) lib.selection.delete(r._key);
  else lib.selection.add(r._key);
  lib.anchor = i;
  render();
  renderActionBar();
}

/** Select every selectable row in the filtered set (not only the rendered ones). */
export function selectAllVisible() {
  for (const r of lib.filtered) if (SELECTABLE.has(r.status)) lib.selection.add(r._key);
  render(); renderActionBar();
}
export function selectNew() {
  for (const r of lib.filtered) if (r.status === "new") lib.selection.add(r._key);
  render(); renderActionBar();
}
export function invertSelection() {
  for (const r of lib.filtered) {
    if (!SELECTABLE.has(r.status)) continue;
    if (lib.selection.has(r._key)) lib.selection.delete(r._key); else lib.selection.add(r._key);
  }
  render(); renderActionBar();
}
export function clearSelection() { lib.selection.clear(); render(); renderActionBar(); }

/** The selection restricted to the current filter (Harvest never touches hidden rows). */
export function selectedInView() {
  return lib.filtered.filter((r) => lib.selection.has(r._key) && SELECTABLE.has(r.status));
}

function renderActionBar() {
  const rows = selectedInView();
  const hidden = lib.selection.size - rows.length;
  show($("actionbar"), rows.length > 0);
  document.body.classList.toggle("has-selection", rows.length > 0);
  if (!rows.length) return;
  const parts = { new: 0, failed: 0, saved_but_missing: 0 };
  for (const r of rows) parts[r.status]++;
  const bits = [];
  if (parts.new) bits.push(`${parts.new.toLocaleString()} new`);
  if (parts.failed) bits.push(`${parts.failed.toLocaleString()} to retry`);
  if (parts.saved_but_missing) bits.push(`${parts.saved_but_missing.toLocaleString()} missing`);
  let text = `${rows.length.toLocaleString()} selected (${bits.join(", ")})`;
  if (hidden > 0) text += `. ${plural(hidden, "selected item")} hidden by filters will not be harvested.`;
  const blocked = rows.filter((r) => !lib.accounts.get(r.account_id)?.folder_ok).length;
  if (blocked) text += `. ${blocked.toLocaleString()} cannot start: their account has no reachable folder.`;
  $("sel-summary").textContent = text;
  $("sel-harvest").textContent = "";
  $("sel-harvest").append(icon("download"), document.createTextNode(`Harvest ${rows.length.toLocaleString()}`));
}

/** Harvest the selection within the current view, after a summary for big batches. */
export async function harvestSelection(startJob) {
  const rows = selectedInView();
  if (!rows.length) return;
  if (rows.length > BIG_HARVEST) {
    const saved = lib.rows.filter((r) => r.size_bytes);
    const avg = saved.length ? saved.reduce((s, r) => s + r.size_bytes, 0) / saved.length : 0;
    const est = avg ? ` About ${size(avg * rows.length)} in total, based on files saved so far.` : "";
    const ok = await confirmDialog("Harvest these overviews?",
      `${rows.length.toLocaleString()} overviews will be downloaded and converted one at a time.${est} You can cancel at any time.`,
      `Harvest ${rows.length.toLocaleString()}`);
    if (!ok) return;
  }
  const items = rows.map((r) => ({ account_id: r.account_id, artifact_id: r.artifact_id }));
  const redownload = rows.filter((r) => r.status === "saved_but_missing").map((r) => r.artifact_id);
  await startJob(api("/api/harvest", { method: "POST", body: { items, redownload } }));
  lib.selection.clear();
  renderActionBar();
}

// ------------------------------------------------------------ counts, chips, filters

function activeFilterCount() {
  const f = lib.filters;
  return f.status.length + f.account.length + f.notebook.length + (f.created ? 1 : 0) + (f.saved ? 1 : 0)
    + (f.multi ? 1 : 0) + (f.clash ? 1 : 0) + (f.moved ? 1 : 0) + (f.include ? 1 : 0) + (f.exclude ? 1 : 0);
}

function renderCounts() {
  const total = lib.scope === "account" ? lib.rows.filter((r) => r.account_id === lib.account).length : lib.rows.length;
  const node = $("result-count");
  node.replaceChildren();
  const strong = el("strong", "", lib.filtered.length.toLocaleString());
  node.append(strong, document.createTextNode(lib.filtered.length === total
    ? ` ${lib.filtered.length === 1 ? "overview" : "overviews"}`
    : ` of ${total.toLocaleString()} overviews`));
  show($("group-toggle"), lib.group !== "none");
  $("group-toggle").textContent = lib.collapsed.size ? "Expand all" : "Collapse all";
}

const PRESET_TEXT = { today: "today", "7d": "last 7 days", "30d": "last 30 days", year: "this year" };
function rangeText(spec) {
  if (!spec) return "";
  if (typeof spec === "string") return PRESET_TEXT[spec] || spec;
  return [spec.from ? `from ${spec.from}` : "", spec.to ? `to ${spec.to}` : ""].filter(Boolean).join(" ");
}

function renderChips() {
  const box = $("chips");
  box.replaceChildren();
  const f = lib.filters;
  const add = (label, remove) => {
    const c = el("span", "chip");
    c.append(el("span", "lbl", label));
    const b = button("", { cls: "", iconName: "x", title: `Remove filter: ${label}` });
    b.addEventListener("click", () => { remove(); apply(); renderFilters(); });
    c.append(b);
    box.append(c);
  };
  for (const s of f.status) add(`Status: ${STATUS_TEXT[s]}`, () => { f.status = f.status.filter((x) => x !== s); });
  for (const a of f.account) add(`Account: ${lib.accounts.get(a)?.label || a}`, () => { f.account = f.account.filter((x) => x !== a); });
  const nbNames = new Map(lib.rows.map((r) => [r.notebook_id, r.notebook]));
  for (const n of f.notebook) add(`Notebook: ${nbNames.get(n) || n}`, () => { f.notebook = f.notebook.filter((x) => x !== n); });
  if (f.created) add(`Created ${rangeText(f.created)}`, () => { f.created = null; });
  if (f.saved) add(`Saved ${rangeText(f.saved)}`, () => { f.saved = null; });
  if (f.multi) add("Notebooks with several overviews", () => { f.multi = false; });
  if (f.clash) add("File name has a (2) suffix", () => { f.clash = false; });
  if (f.moved) add("Found by Scan folder", () => { f.moved = false; });
  if (f.include) add(`Title contains "${f.include}"`, () => { f.include = ""; });
  if (f.exclude) add(`Title excludes "${f.exclude}"`, () => { f.exclude = ""; });
  if (activeFilterCount() > 1) {
    box.append(button("Clear all", { cls: "btn btn-ghost btn-sm", onClick: () => { lib.filters = emptyFilters(); apply(); renderFilters(); } }));
  }
}

/** Facet counts: rows matching everything except that facet. */
function facet(name, valueOf) {
  const counts = new Map();
  const now = new Date();
  for (const r of scopedRows()) {
    if (!matchesFilters(r, lib.filters, now, name)) continue;
    const v = valueOf(r);
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return counts;
}

function checkOption(label, count, checked, onChange) {
  const lab = el("label", "check");
  const box = el("input");
  box.type = "checkbox";
  box.checked = checked;
  box.addEventListener("change", () => onChange(box.checked));
  lab.append(box, el("span", "lbl", label), el("span", "count", (count || 0).toLocaleString()));
  lab.title = label;
  return lab;
}

function listSection(title, entries, selected, onToggle) {
  // entries: [{value, label, count}]; long lists get their own search box (over 100 entries).
  const sec = el("section");
  sec.append(el("h3", "", title));
  const opts = el("div", "opts");
  const draw = (filter = "") => {
    opts.replaceChildren();
    const f = fold(filter);
    const shown = entries.filter((e) => !f || fold(e.label).includes(f));
    const limit = 200;
    for (const e of shown.slice(0, limit)) {
      opts.append(checkOption(e.label, e.count, selected.includes(e.value), (on) => onToggle(e.value, on)));
    }
    if (shown.length > limit) opts.append(el("p", "hint", `${shown.length - limit} more. Type to narrow the list.`));
    if (!shown.length) opts.append(el("p", "hint", "No match."));
  };
  if (entries.length > 100) {
    const s = el("input", "input");
    s.type = "search";
    s.placeholder = `Find a ${title.toLowerCase().replace(/s$/, "")}`;
    s.setAttribute("aria-label", s.placeholder);
    s.addEventListener("input", () => draw(s.value));
    sec.append(s);
  }
  draw();
  sec.append(opts);
  return sec;
}

function dateSection(title, key) {
  const sec = el("section");
  sec.append(el("h3", "", title));
  const presets = el("div", "date-presets");
  for (const [v, label] of [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["year", "This year"]]) {
    const b = button(label, { cls: "btn btn-sm" });
    b.setAttribute("aria-pressed", lib.filters[key] === v ? "true" : "false");
    b.addEventListener("click", () => { lib.filters[key] = lib.filters[key] === v ? null : v; apply(); renderFilters(); });
    presets.append(b);
  }
  sec.append(presets);
  const custom = el("div", "row");
  const spec = typeof lib.filters[key] === "object" && lib.filters[key] ? lib.filters[key] : {};
  for (const side of ["from", "to"]) {
    const lab = el("label", "field grow", side === "from" ? "From" : "To");
    const inp = el("input", "input");
    inp.type = "date";
    inp.value = spec[side] || "";
    inp.addEventListener("change", () => {
      const cur = typeof lib.filters[key] === "object" && lib.filters[key] ? { ...lib.filters[key] } : {};
      cur[side] = inp.value || undefined;
      lib.filters[key] = cur.from || cur.to ? cur : null;
      apply(); renderFilters();
    });
    lab.append(inp);
    custom.append(lab);
  }
  sec.append(custom);
  return sec;
}

/** Draw the filter panel with live counts. */
export function renderFilters() {
  const box = $("filters");
  if (!box) return;
  box.replaceChildren();
  const f = lib.filters;
  const st = facet("status", (r) => r.status);
  box.append(listSection("Status", STATUS_LIST.map((s) => ({ value: s, label: STATUS_TEXT[s], count: st.get(s) })), f.status,
    (v, on) => { f.status = on ? [...f.status, v] : f.status.filter((x) => x !== v); apply(); renderFilters(); }));
  if (lib.scope === "all" && lib.accounts.size > 1) {
    const ac = facet("account", (r) => r.account_id);
    box.append(listSection("Accounts", [...lib.accounts.values()].map((a) => ({ value: a.id, label: a.label, count: ac.get(a.id) })), f.account,
      (v, on) => { f.account = on ? [...f.account, v] : f.account.filter((x) => x !== v); apply(); renderFilters(); }));
  }
  const nb = facet("notebook", (r) => r.notebook_id);
  const names = new Map();
  for (const r of lib.rows) if (!names.has(r.notebook_id)) names.set(r.notebook_id, r.notebook);
  const nbEntries = [...names.entries()].map(([id, label]) => ({ value: id, label: label || "Untitled notebook", count: nb.get(id) || 0 }))
    .filter((e) => e.count > 0 || f.notebook.includes(e.value))
    .sort((a, b) => b.count - a.count || fold(a.label).localeCompare(fold(b.label)));
  box.append(listSection("Notebooks", nbEntries, f.notebook,
    (v, on) => { f.notebook = on ? [...f.notebook, v] : f.notebook.filter((x) => x !== v); apply(); renderFilters(); }));
  box.append(dateSection("Created", "created"));
  box.append(dateSection("Saved", "saved"));
  const more = el("section");
  more.append(el("h3", "", "More"));
  const flag = (key, label) => {
    const c = facet(key, (r) => (key === "multi" ? r.multi : key === "clash" ? r.clash : r.origin === "relinked" || r.origin === "scan"));
    more.append(checkOption(label, c.get(true), f[key], (on) => { f[key] = on; apply(); renderFilters(); }));
  };
  flag("multi", "Notebook has several overviews");
  flag("clash", "File name has a (2) suffix");
  flag("moved", "Found by Scan folder");
  for (const [key, label] of [["include", "Title contains"], ["exclude", "Title excludes"]]) {
    const lab = el("label", "field", label);
    const inp = el("input", "input");
    inp.type = "search";
    inp.value = f[key];
    let t;
    inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { f[key] = inp.value.trim(); apply(); }, 200); });
    lab.append(inp);
    more.append(lab);
  }
  box.append(more);
}

/** Reset search and filters. */
export function clearAll() {
  lib.filters = emptyFilters();
  lib.query = "";
  lib.parsed = parseQuery("");
  const q = $("q");
  if (q) q.value = "";
  apply();
  renderFilters();
}

let filtersTimer = null;
/** Redraw the filter panel's live counts once typing pauses (keeps keystrokes fast). */
export function renderFiltersSoon(delay = 220) {
  clearTimeout(filtersTimer);
  filtersTimer = setTimeout(renderFilters, delay);
}

/** Set the search text (called as the user types). */
export function setQuery(text) {
  lib.query = text;
  lib.parsed = parseQuery(text);
  apply();
  renderFiltersSoon();
}

// ------------------------------------------------------------ details

function openDetails(r) {
  openDrawer(r, lib.accounts.get(r.account_id), { startJob: lib.startJob });
}

// ------------------------------------------------------------ saved views

function currentView(name) {
  return { name, query: lib.query, filters: JSON.parse(JSON.stringify(lib.filters)), sort: lib.sort, group: lib.group };
}

/** Apply a saved view. */
export function applyView(v) {
  lib.filters = { ...emptyFilters(), ...(v.filters || {}) };
  lib.sort = v.sort || "newest";
  lib.group = v.group || "none";
  lib.collapsed.clear();
  $("sort").value = lib.sort;
  $("group").value = lib.group;
  const q = $("q");
  if (q) q.value = v.query || "";
  lib.query = v.query || "";
  lib.parsed = parseQuery(lib.query);
  apply();
  renderFilters();
}

/** The Views menu: recall, save current, delete. */
export function openViewsMenu(anchor) {
  const menu = el("div", "menu");
  for (const v of lib.views) {
    const item = el("div", "item");
    const b = button(v.name, { cls: "", iconName: "bookmark" });
    b.addEventListener("click", () => { closePopover(); applyView(v); toast(`View "${v.name}" applied.`); });
    const del = button("", { cls: "btn btn-ghost btn-icon btn-sm del", iconName: "trash", title: `Delete view ${v.name}` });
    del.addEventListener("click", async () => {
      closePopover();
      if (!(await confirmDialog("Delete this view?", `"${v.name}" will be removed from your saved views.`, "Delete", true))) return;
      await saveViews(lib.views.filter((x) => x !== v));
      toast(`View "${v.name}" deleted.`);
    });
    item.append(b, del);
    menu.append(item);
  }
  if (!lib.views.length) menu.append(el("p", "hint", "No saved views yet."));
  menu.append(el("div", "sepline"));
  const save = button("Save current view", { cls: "", iconName: "plus" });
  save.addEventListener("click", async () => {
    closePopover();
    const name = await promptDialog("Save this view", "Name");
    if (!name) return;
    const others = lib.views.filter((v) => v.name.toLowerCase() !== name.toLowerCase());
    await saveViews([...others, currentView(name)]);
    toast(`View "${name}" saved.`);
  });
  menu.append(save);
  popover(anchor, menu, "Saved views");
}

async function saveViews(views) {
  try {
    const s = await api("/api/settings", { method: "POST", body: { views } });
    lib.views = s.views;
  } catch (e) { toast(e.message, "bad"); }
}

// ------------------------------------------------------------ keyboard movement

/** Move the focus row by delta (j and k) and keep it in view. */
export function moveFocus(delta) {
  if (!lib.items.length) return;
  let i = lib.focus;
  do { i = Math.max(0, Math.min(lib.items.length - 1, i + delta)); } while (lib.items[i].type !== "row" && i > 0 && i < lib.items.length - 1);
  lib.focus = i;
  const list = $("vlist");
  const y = lib.offsets[i];
  if (y < list.scrollTop + GROUP_H) list.scrollTop = Math.max(0, y - GROUP_H);
  else if (y + ROW_H > list.scrollTop + list.clientHeight) list.scrollTop = y + ROW_H - list.clientHeight;
  render();
}

/** Toggle selection of the focused row (x). */
export function toggleFocused() {
  const it = lib.items[lib.focus];
  if (it && it.type === "row" && SELECTABLE.has(it.row.status)) toggle(it.row, lib.focus, false);
}

/** Open details of the focused row (Enter). */
export function openFocused() {
  const it = lib.items[lib.focus];
  if (it && it.type === "row") openDetails(it.row);
}

/** Wire list events once. */
export function initLibrary() {
  $("vlist").addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  $("backtop").addEventListener("click", () => { $("vlist").scrollTop = 0; $("vlist").focus(); });
  $("group-toggle").addEventListener("click", () => {
    if (lib.collapsed.size) lib.collapsed.clear();
    else for (const it of lib.items) if (it.type === "group") lib.collapsed.add(it.group.key);
    buildItems(); render(); renderCounts(); lib.onChange();
  });
}

/** Test hooks (demo mode only): what the page shows, for comparison with the oracle. */
export function testHooks() {
  return {
    filteredIds: () => lib.filtered.map((r) => r.artifact_id),
    renderedRowCount: () => document.querySelectorAll("#vspacer .vrow").length,
    selectedInView: () => selectedInView().map((r) => r.artifact_id),
    perf: () => [...lib.perf],
    state: () => ({ scope: lib.scope, account: lib.account, sort: lib.sort, group: lib.group, query: lib.query, filters: lib.filters }),
  };
}
