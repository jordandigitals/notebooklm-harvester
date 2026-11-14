// View state in the address bar, so refresh, back and forward keep the view. The page key
// is never here (it lives in an HttpOnly cookie), so the address is safe to copy.
import { lib, emptyFilters } from "./library.js";
import { parseQuery } from "./search.js";

const LIST_KEYS = { status: "status", acct: "account", nb: "notebook" };

/** Encode the library state as a query string. */
export function encode(view) {
  const p = new URLSearchParams();
  if (view && view !== "library") p.set("v", view);
  if (lib.query) p.set("q", lib.query);
  if (lib.scope === "account" && lib.account !== null) p.set("scope", String(lib.account));
  for (const [k, key] of Object.entries(LIST_KEYS)) if (lib.filters[key].length) p.set(k, lib.filters[key].join(","));
  for (const key of ["created", "saved"]) {
    const v = lib.filters[key];
    if (typeof v === "string") p.set(key, v);
    else if (v) p.set(key, `${v.from || ""}..${v.to || ""}`);
  }
  for (const key of ["multi", "clash", "moved"]) if (lib.filters[key]) p.set(key, "1");
  if (lib.filters.include) p.set("inc", lib.filters.include);
  if (lib.filters.exclude) p.set("exc", lib.filters.exclude);
  if (lib.sort !== "newest") p.set("sort", lib.sort);
  if (lib.group !== "none") p.set("group", lib.group);
  return p.toString();
}

/** Read the library state from a query string; returns the view name. */
export function decode(search) {
  const p = new URLSearchParams(search);
  const f = emptyFilters();
  for (const [k, key] of Object.entries(LIST_KEYS)) {
    const v = p.get(k);
    if (v) f[key] = v.split(",").filter(Boolean).map((x) => (key === "account" ? Number(x) : x));
  }
  for (const key of ["created", "saved"]) {
    const v = p.get(key);
    if (!v) continue;
    if (v.includes("..")) { const [from, to] = v.split(".."); f[key] = { from: from || undefined, to: to || undefined }; }
    else f[key] = v;
  }
  for (const key of ["multi", "clash", "moved"]) f[key] = p.get(key) === "1";
  f.include = p.get("inc") || "";
  f.exclude = p.get("exc") || "";
  lib.filters = f;
  lib.query = p.get("q") || "";
  lib.parsed = parseQuery(lib.query);
  const scope = p.get("scope");
  if (scope) { lib.scope = "account"; lib.account = Number(scope); } else lib.scope = "all";
  lib.sort = p.get("sort") || "newest";
  lib.group = p.get("group") || "none";
  return p.get("v") || "library";
}

/** True if the address carries any library state. */
export function hasState(search) {
  const p = new URLSearchParams(search);
  return [...p.keys()].some((k) => k !== "v");
}
