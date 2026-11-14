// In-page player: one at a time, a mini bar that stays while you switch tabs, remembers
// the position per file in this browser (localStorage: ledger row id to seconds only).
import { $, el, icon, button, duration } from "./dom.js";
import { toast } from "./ui.js";

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const state = { id: null, title: "", audio: null };

function store(id, seconds) {
  try { localStorage.setItem(`nlh.pos.${id}`, String(Math.floor(seconds))); } catch (e) { /* private window */ }
}
function recall(id) {
  try { return Number(localStorage.getItem(`nlh.pos.${id}`) || 0); } catch (e) { return 0; }
}

/** Play a saved file by its ledger row id. */
export function play(id, title, notebook) {
  if (!state.audio) build();
  const a = state.audio;
  if (state.id === id) { if (a.paused) a.play(); return; }
  if (state.id !== null) store(state.id, a.currentTime);
  state.id = id;
  state.title = title;
  a.src = `/api/play/${id}`;
  $("pl-title").textContent = title;
  $("pl-sub").textContent = notebook || "";
  const resume = recall(id);
  a.addEventListener("loadedmetadata", () => { if (resume > 5 && resume < a.duration - 5) a.currentTime = resume; }, { once: true });
  a.play().catch(() => toast("The file could not be played.", "bad"));
  $("player").classList.remove("is-hidden");
  document.body.classList.add("has-player");
}

function build() {
  const bar = el("div", "player is-hidden");
  bar.id = "player";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Player");
  const a = el("audio");
  a.preload = "metadata";
  state.audio = a;
  const info = el("div", "pl-info");
  const t = el("div", "pl-title");
  t.id = "pl-title";
  const sub = el("div", "pl-sub");
  sub.id = "pl-sub";
  info.append(t, sub);
  const back = button("", { cls: "btn btn-ghost btn-icon", iconName: "back15", title: "Back 15 seconds" });
  const toggle = button("", { cls: "btn btn-primary btn-icon", iconName: "play", title: "Play" });
  toggle.id = "pl-toggle";
  const fwd = button("", { cls: "btn btn-ghost btn-icon", iconName: "fwd15", title: "Forward 15 seconds" });
  back.addEventListener("click", () => { a.currentTime = Math.max(0, a.currentTime - 15); });
  fwd.addEventListener("click", () => { a.currentTime = Math.min(a.duration || 0, a.currentTime + 15); });
  toggle.addEventListener("click", () => { if (a.paused) a.play(); else a.pause(); });
  const seek = el("input", "pl-seek");
  seek.type = "range";
  seek.min = "0";
  seek.max = "1000";
  seek.value = "0";
  seek.setAttribute("aria-label", "Position");
  seek.addEventListener("input", () => { if (a.duration) a.currentTime = (Number(seek.value) / 1000) * a.duration; });
  const time = el("span", "pl-time", "0:00 / 0:00");
  time.setAttribute("aria-live", "off");
  const speed = el("select", "select pl-speed");
  speed.setAttribute("aria-label", "Speed");
  for (const s of SPEEDS) {
    const o = el("option", "", `${s}x`);
    o.value = String(s);
    if (s === 1) o.selected = true;
    speed.append(o);
  }
  speed.addEventListener("change", () => { a.playbackRate = Number(speed.value); });
  const close = button("", { cls: "btn btn-ghost btn-icon", iconName: "x", title: "Close player" });
  close.addEventListener("click", () => {
    if (state.id !== null) store(state.id, a.currentTime);
    a.pause();
    a.removeAttribute("src");
    state.id = null;
    bar.classList.add("is-hidden");
    document.body.classList.remove("has-player");
  });
  const controls = el("div", "pl-controls");
  controls.append(back, toggle, fwd);
  const track = el("div", "pl-track");
  track.append(seek, time);
  bar.append(info, controls, track, speed, close, a);
  document.body.append(bar);
  const setIcon = (name, label) => {
    toggle.replaceChildren(icon(name));
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  };
  a.addEventListener("play", () => setIcon("pause", "Pause"));
  a.addEventListener("pause", () => { setIcon("play", "Play"); if (state.id !== null) store(state.id, a.currentTime); });
  a.addEventListener("timeupdate", () => {
    if (a.duration) seek.value = String(Math.round((a.currentTime / a.duration) * 1000));
    time.textContent = `${duration(a.currentTime)} / ${duration(a.duration || 0)}`;
    if (state.id !== null && Math.floor(a.currentTime) % 10 === 0) store(state.id, a.currentTime);
  });
  a.addEventListener("ended", () => { if (state.id !== null) store(state.id, 0); });
  a.addEventListener("error", () => { if (a.getAttribute("src")) toast("The file could not be played. It may have moved.", "bad"); });
  bar.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT") return;
    if (e.key === " " && e.target === bar) { e.preventDefault(); toggle.click(); }
  });
  bar.tabIndex = -1;
}

/** Keyboard from anywhere (k or space is used elsewhere): "p" toggles when a file is loaded. */
export function togglePlay() {
  if (!state.audio || state.id === null) return false;
  if (state.audio.paused) state.audio.play(); else state.audio.pause();
  return true;
}

/** For tests and the details drawer. */
export function playerState() {
  return { id: state.id, paused: state.audio ? state.audio.paused : true, time: state.audio ? state.audio.currentTime : 0 };
}
