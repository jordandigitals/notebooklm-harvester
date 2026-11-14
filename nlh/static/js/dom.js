// Small DOM helpers. Text from NotebookLM is always set with textContent, never as HTML.

/** document.getElementById shorthand. */
export const $ = (id) => document.getElementById(id);

/** Make an element with a class and optional text. */
export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/** An inline SVG icon from the sprite in index.html. */
export function icon(name, cls = "icon") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

/** A button with an optional icon. */
export function button(label, { cls = "btn", iconName = null, title = null, onClick = null } = {}) {
  const b = el("button", cls);
  b.type = "button";
  if (iconName) b.append(icon(iconName, cls.includes("btn-sm") ? "icon icon-sm" : "icon"));
  if (label) b.append(document.createTextNode(label));
  if (title) { b.title = title; if (!label) b.setAttribute("aria-label", title); }
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

export const STATUS_TEXT = {
  ok: "Signed in", needs_sign_in: "Sign in again", library_error: "Library error", unknown: "Not checked",
  new: "New", saved: "Saved", saved_but_missing: "File missing", folder_unreachable: "Folder not reachable",
  in_progress: "Not finished", failed: "Failed last time",
  queued: "Waiting", running: "Working", done: "Done", cancelled: "Cancelled",
};
const STATUS_ICON = {
  new: "plus", saved: "check", saved_but_missing: "alert", folder_unreachable: "alert", failed: "alert",
  in_progress: "refresh", ok: "check", needs_sign_in: "alert", library_error: "alert",
};

/** A status pill: icon plus words, so colour is never the only signal. */
export function pill(status) {
  const p = el("span", `pill st-${status}`);
  if (STATUS_ICON[status]) p.append(icon(STATUS_ICON[status]));
  p.append(el("span", "ptext", STATUS_TEXT[status] || status));
  p.title = STATUS_TEXT[status] || status;
  return p;
}

/** Show or hide. */
export function show(node, visible) { node.classList.toggle("is-hidden", !visible); }

/** Short local date. */
export function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** h:mm:ss or m:ss. */
export function duration(s) {
  if (s === null || s === undefined) return "";
  const t = Math.round(s), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/** Human file size. */
export function size(bytes) {
  if (bytes === null || bytes === undefined) return "";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

/** "1 thing" or "3 things". */
export function plural(n, one, many = `${one}s`) { return `${n.toLocaleString()} ${n === 1 ? one : many}`; }
