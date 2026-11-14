// Toasts, dialogs and popovers. Dialogs use <dialog> so focus is trapped and Esc closes.
import { $, el, icon, button } from "./dom.js";

/** A short message in the corner; kind is ok, bad or warn. */
export function toast(message, kind = "ok", timeout = 5000) {
  const t = el("div", `toast ${kind}`);
  t.append(icon(kind === "ok" ? "check" : kind === "warn" ? "alert" : "alert"), el("div", "grow", message));
  const close = button("", { cls: "btn btn-ghost btn-icon btn-sm", iconName: "x", title: "Dismiss" });
  close.addEventListener("click", () => t.remove());
  t.append(close);
  $("toasts").append(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
}

/** Open the shared dialog with built content; resolves with the clicked action's value. */
export function dialog({ title, body, actions }) {
  const dlg = $("dlg");
  const box = el("form", "dlg");
  box.method = "dialog";
  const h = el("h2", "", title);
  h.id = "dlg-title";
  dlg.setAttribute("aria-labelledby", "dlg-title");
  box.append(h);
  if (body) box.append(body);
  const row = el("div", "dlg-actions");
  return new Promise((resolve) => {
    for (const a of actions) {
      const b = button(a.label, { cls: a.primary ? "btn btn-primary" : a.danger ? "btn btn-danger" : "btn" });
      b.addEventListener("click", (e) => { e.preventDefault(); dlg.close(); resolve(a.value); });
      row.append(b);
    }
    box.append(row);
    dlg.replaceChildren(box);
    dlg.addEventListener("close", () => resolve(null), { once: true });
    dlg.showModal();
    const first = box.querySelector("input, select, .btn-primary");
    if (first) first.focus();
  });
}

/** Ask yes or no. */
export async function confirmDialog(title, message, yes = "Continue", danger = false) {
  const v = await dialog({
    title,
    body: el("p", "", message),
    actions: [{ label: "Cancel", value: false }, { label: yes, value: true, primary: !danger, danger }],
  });
  return v === true;
}

/** Ask for a short text. */
export async function promptDialog(title, label, value = "") {
  const wrap = el("label", "field", label);
  const input = el("input", "input");
  input.value = value;
  wrap.append(input);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("dlg").close(); input.dataset.ok = "1"; }
  });
  const v = await dialog({ title, body: wrap, actions: [{ label: "Cancel", value: null }, { label: "Save", value: "ok", primary: true }] });
  return v === "ok" || input.dataset.ok ? input.value.trim() : null;
}

let openPop = null;

/** Show a popover under an anchor; closes on outside click or Esc. */
export function popover(anchor, content, label) {
  closePopover();
  const pop = el("div", "popover");
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", label || "Options");
  pop.append(content);
  $("popover-root").append(pop);
  const r = anchor.getBoundingClientRect();
  const w = Math.min(pop.offsetWidth || 300, window.innerWidth - 16);
  pop.style.setProperty("left", `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`);
  pop.style.setProperty("top", `${Math.min(r.bottom + 6, window.innerHeight - 80)}px`);
  openPop = { pop, anchor };
  anchor.setAttribute("aria-expanded", "true");
  setTimeout(() => {
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escClose, true);
  });
  const first = pop.querySelector("button, input, select");
  if (first) first.focus();
  return pop;
}

function outside(e) { if (openPop && !openPop.pop.contains(e.target) && !openPop.anchor.contains(e.target)) closePopover(); }
function escClose(e) { if (e.key === "Escape" && openPop) { e.stopPropagation(); const a = openPop.anchor; closePopover(); a.focus(); } }

/** Close any open popover. */
export function closePopover() {
  if (!openPop) return;
  openPop.anchor.setAttribute("aria-expanded", "false");
  openPop.pop.remove();
  openPop = null;
  document.removeEventListener("pointerdown", outside, true);
  document.removeEventListener("keydown", escClose, true);
}
