// Statistics: totals and breakdowns over the whole filtered ledger, drawn as small inline
// SVG charts (no chart library), with the same numbers in tables for screen readers.
import { api } from "./api.js";
import { $, el, size } from "./dom.js";
import { toast } from "./ui.js";
import { historyQuery } from "./history.js";

const SVG = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}

function tile(label, value, hint) {
  const t = el("div", "tile");
  t.append(el("div", "tile-v", value), el("div", "tile-l", label));
  if (hint) t.append(el("div", "hint", hint));
  return t;
}

/** Horizontal bars: label and number in page text, the bar itself a small inline SVG. */
function hbars(title, entries, valueOf, fmt) {
  const card = el("section", "chart-card");
  card.append(el("h2", "", title));
  if (!entries.length) { card.append(el("p", "hint", "Nothing yet.")); return card; }
  const max = Math.max(...entries.map(valueOf), 1);
  const list = el("div", "hbars");
  list.setAttribute("role", "img");
  list.setAttribute("aria-label", `${title} chart. The table below has the numbers.`);
  for (const e of entries) {
    const row = el("div", "hbar");
    const head = el("div", "hbar-head");
    head.append(el("span", "hbar-name", e.name || "No date"), el("span", "hbar-val", fmt(e)));
    const bar = svg("svg", { viewBox: "0 0 100 6", preserveAspectRatio: "none", class: "hbar-svg", "aria-hidden": "true" });
    bar.append(svg("rect", { x: 0, y: 0, width: 100, height: 6, rx: 3, class: "bar-track" }));
    bar.append(svg("rect", { x: 0, y: 0, width: Math.max(1, (100 * valueOf(e)) / max), height: 6, rx: 3, class: "bar-rect" }));
    row.append(head, bar);
    list.append(row);
  }
  card.append(list, table(title, entries, fmt));
  return card;
}

/** Columns over time (by month saved). */
function columns(title, entries) {
  const card = el("section", "chart-card");
  card.append(el("h2", "", title));
  if (!entries.length) { card.append(el("p", "hint", "Nothing yet.")); return card; }
  const w = 600, h = 180, pad = 24;
  const max = Math.max(...entries.map((e) => e.files), 1);
  const cw = (w - pad) / entries.length;
  const chart = svg("svg", { viewBox: `0 0 ${w} ${h + 28}`, class: "chart", role: "img", "aria-label": `${title} chart. The table below has the numbers.` });
  chart.append(svg("line", { x1: pad, y1: h, x2: w, y2: h, class: "axis" }));
  entries.forEach((e, i) => {
    const bh = Math.max(2, ((h - 16) * e.files) / max);
    const x = pad + i * cw + cw * 0.15;
    chart.append(svg("rect", { x, y: h - bh, width: cw * 0.7, height: bh, rx: 3, class: "bar-rect" }));
    if (entries.length <= 8 || i % Math.ceil(entries.length / 6) === 0) {
      const t = svg("text", { x: x + cw * 0.35, y: h + 22, class: "chart-label col-label", "text-anchor": "middle" });
      t.textContent = e.name ? e.name.slice(2) : "";
      chart.append(t);
    }
  });
  card.append(chart, table(title, entries, (e) => `${e.files} files, ${e.hours.toFixed(1)} h`));
  return card;
}

function table(caption, entries, fmt) {
  const t = el("table", "chart-table");
  const cap = el("caption", "sr-only", caption);
  t.append(cap);
  const body = el("tbody");
  for (const e of entries) {
    const tr = el("tr");
    const th = el("th", "", e.name || "No date");
    th.scope = "row";
    tr.append(th, el("td", "num", fmt(e)));
    body.append(tr);
  }
  t.append(body);
  const d = el("details", "chart-details");
  d.append(el("summary", "", "Show as a table"), t);
  return d;
}

/** Draw the statistics view for the current history filters. */
export async function renderStats() {
  const view = $("view-stats");
  view.replaceChildren(el("div", "skeleton"));
  let s;
  try { s = await api(`/api/stats?${historyQuery()}`); } catch (e) { toast(e.message, "bad"); return; }
  const wrap = el("div", "stats");
  const intro = el("p", "hint", "Numbers cover every saved file that matches the History filters, not only one page.");
  const tiles = el("div", "tiles");
  tiles.append(
    tile("Saved files", s.totals.files.toLocaleString()),
    tile("Hours of audio", s.totals.hours.toLocaleString(undefined, { maximumFractionDigits: 1 })),
    tile("On disk", size(s.totals.bytes) || "0 KB"),
    tile("Harvest success", s.success_rate === null ? "No harvests yet" : `${Math.round(s.success_rate * 100)}%`,
      s.success_rate === null ? "" : `${s.attempts.saved.toLocaleString()} saved, ${s.attempts.failed.toLocaleString()} failed`),
    tile("Average download", s.avg_download_s === null ? "Not measured" : `${s.avg_download_s.toFixed(1)} s`),
  );
  wrap.append(intro, tiles,
    hbars("By account", s.by_account, (e) => e.files, (e) => `${e.files} files, ${e.hours.toFixed(1)} h`),
    hbars("Top notebooks", s.by_notebook, (e) => e.files, (e) => `${e.files} files, ${size(e.bytes)}`),
    columns("By month saved", s.by_month));
  view.replaceChildren(wrap);
}
