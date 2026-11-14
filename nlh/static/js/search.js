// Search and filter logic: pure functions, no DOM. Mirrored by tests/oracle.py, which the
// UI tests use to compute expected results independently of the page.

/** Lower case, accents removed (NFKD), so "Cafe" finds "Café". */
export function fold(text) {
  return String(text || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

const PREFIXES = ["account", "notebook", "status", "before", "after"];
export const STATUS_WORDS = {
  new: "new",
  saved: "saved",
  missing: "saved_but_missing",
  unreachable: "folder_unreachable",
  offline: "folder_unreachable",
  notfinished: "in_progress",
  unfinished: "in_progress",
  inprogress: "in_progress",
  failed: "failed",
};

/** Split a query into words and prefix terms; "quoted text" stays one word. */
export function parseQuery(query) {
  const out = { words: [], account: [], notebook: [], status: [], before: null, after: null };
  const re = /(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)/g;
  let m;
  while ((m = re.exec(String(query || ""))) !== null) {
    const key = (m[1] || m[3] || "").toLowerCase();
    const val = m[2] !== undefined ? m[2] : m[4];
    if (key && PREFIXES.includes(key)) {
      if (key === "before" || key === "after") {
        if (/^\d{4}-\d{2}-\d{2}$/.test(val)) out[key] = val;
      } else if (key === "status") {
        const s = STATUS_WORDS[fold(val).replace(/[^a-z]/g, "")];
        if (s) out.status.push(s);
      } else if (val) {
        out[key].push(fold(val));
      }
      continue;
    }
    const word = m[5] !== undefined ? m[5] : m[6] || `${m[1] || m[3]}:${val}`;
    const f = fold(word).trim();
    if (f) out.words.push(f);
  }
  return out;
}

/** The text one row is searched in (built once per row). */
export function haystack(row, account) {
  return fold([
    row.title, row.notebook, account ? account.label : "", account ? account.email : "",
    row.file_name, row.saved_path, (row.created_at || "").slice(0, 10), (row.saved_at || "").slice(0, 10),
    row.artifact_id, row.notebook_id, row.status,
  ].join(" \u0001 "));
}

/** Does a row match a parsed query? */
export function matchesQuery(row, hay, accountHay, q) {
  for (const w of q.words) if (!hay.includes(w)) return false;
  for (const a of q.account) if (!accountHay.includes(a)) return false;
  if (q.notebook.length) {
    const nb = fold(row.notebook);
    for (const n of q.notebook) if (!nb.includes(n)) return false;
  }
  if (q.status.length && !q.status.includes(row.status)) return false;
  const created = (row.created_at || "").slice(0, 10);
  if (q.before && !(created && created < q.before)) return false;
  if (q.after && !(created && created > q.after)) return false;
  return true;
}

const DAY = 86400000;

/** Inclusive lower bound (YYYY-MM-DD) for a date preset relative to now. */
export function presetStart(preset, now = new Date()) {
  const d = new Date(now.getTime());
  if (preset === "today") return d.toISOString().slice(0, 10);
  if (preset === "7d") return new Date(d.getTime() - 6 * DAY).toISOString().slice(0, 10);
  if (preset === "30d") return new Date(d.getTime() - 29 * DAY).toISOString().slice(0, 10);
  if (preset === "year") return `${d.getUTCFullYear()}-01-01`;
  return null;
}

function inRange(date, spec, now) {
  if (!spec) return true;
  if (!date) return false;
  const day = date.slice(0, 10);
  if (typeof spec === "string") return day >= presetStart(spec, now);
  if (spec.from && day < spec.from) return false;
  if (spec.to && day > spec.to) return false;
  return true;
}

/** Filters that combine with AND. Each key is optional. "except" skips one facet (for counts). */
export function matchesFilters(row, f, now = new Date(), except = null) {
  if (except !== "status" && f.status && f.status.length && !f.status.includes(row.status)) return false;
  if (except !== "account" && f.account && f.account.length && !f.account.includes(row.account_id)) return false;
  if (except !== "notebook" && f.notebook && f.notebook.length && !f.notebook.includes(row.notebook_id)) return false;
  if (except !== "created" && !inRange(row.created_at, f.created, now)) return false;
  if (except !== "saved" && f.saved && !inRange(row.saved_at, f.saved, now)) return false;
  if (except !== "multi" && f.multi && !row.multi) return false;
  if (except !== "clash" && f.clash && !row.clash) return false;
  if (except !== "moved" && f.moved && row.origin !== "relinked" && row.origin !== "scan") return false;
  if (f.include) {
    const t = fold(row.title);
    if (!fold(f.include).split(/\s+/).filter(Boolean).every((w) => t.includes(w))) return false;
  }
  if (f.exclude) {
    const t = fold(row.title);
    if (fold(f.exclude).split(/\s+/).filter(Boolean).some((w) => t.includes(w))) return false;
  }
  return true;
}

const STATUS_ORDER = { failed: 0, saved_but_missing: 1, folder_unreachable: 2, new: 3, in_progress: 4, saved: 5 };

/** Comparator for a sort key. Ties break on artifact id so the order is stable. */
export function comparator(sort) {
  const by = (fn, dir = 1) => (a, b) => {
    const x = fn(a), y = fn(b);
    if (x < y) return -dir;
    if (x > y) return dir;
    return a.artifact_id < b.artifact_id ? -1 : a.artifact_id > b.artifact_id ? 1 : 0;
  };
  switch (sort) {
    case "oldest": return by((r) => r.created_at || "");
    case "title_asc": return by((r) => r._ft ?? fold(r.title));
    case "title_desc": return by((r) => r._ft ?? fold(r.title), -1);
    case "notebook": return by((r) => fold(r.notebook) + "\u0000" + (r.created_at || ""));
    case "status": return by((r) => STATUS_ORDER[r.status] ?? 9);
    case "saved": return by((r) => r.saved_at || "", -1);
    case "size": return by((r) => r.size_bytes ?? -1, -1);
    case "length": return by((r) => r.duration_s ?? -1, -1);
    default: return by((r) => r.created_at || "", -1);
  }
}

/** Ranges in the original text to highlight for the query words (accent- and case-blind). */
export function highlightRanges(text, words) {
  if (!text || !words.length) return [];
  const map = [];
  let folded = "";
  for (let i = 0; i < text.length; i++) {
    const f = fold(text[i]);
    for (let k = 0; k < f.length; k++) map.push(i);
    folded += f;
  }
  const ranges = [];
  for (const w of words) {
    let from = 0;
    while (w && (from = folded.indexOf(w, from)) !== -1) {
      ranges.push([map[from], map[from + w.length - 1] + 1]);
      from += w.length;
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}
