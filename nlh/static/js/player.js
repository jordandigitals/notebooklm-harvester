// In-page player: one at a time, a mini bar that stays while you switch tabs.
// Resume playback (P2-J): the laptop's local server keeps where the listener left each file, so
// the laptop page and the phone page share it and it survives the app window's private wipe.
import { $, el, icon, button, duration } from "./dom.js";
import { toast } from "./ui.js";

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const SAVE_EVERY_MS = 5000; // while playing
const REWIND_S = 5; // continue a little before where the listener stopped
const IGNORE_UNDER_S = 10; // the server applies the same two rules
const TAIL_S = 15; // this close to the end counts as finished
const LINE_MS = 12000; // how long "Continuing from" stays
const QUIT_WAIT_MS = 1500;
const state = { id: null, title: "", notebook: "", audio: null, lastSave: 0, lineTimer: 0, applied: true, ownSeek: false };

const live = new Map(); // positions saved in this page since the rows were loaded

/** Tell the rows (library, history) the new position so their markers follow without a reload. */
function announce(id, seconds) {
  live.set(id, seconds);
  document.dispatchEvent(new CustomEvent("nlh:position", { detail: { id, seconds } }));
}

function describe(p, seconds, total) {
  const text = `Listened to ${duration(seconds)} of ${duration(total)}`;
  p.setAttribute("aria-label", text);
  p.title = text;
  p.classList.toggle("is-hidden", !(seconds > 0));
}

/** A thin progress line for a partly played file (no badge, no extra height). */
export function marker(id, seconds, total) {
  const p = el("progress", "row-progress");
  const now = live.has(id) ? live.get(id) : Number(seconds) || 0;
  p.max = total > 0 ? total : 1;
  p.value = Math.min(now, p.max);
  p.dataset.hid = String(id);
  describe(p, now, total);
  return p;
}

document.addEventListener("nlh:position", (e) => {
  const { id, seconds } = e.detail;
  for (const p of document.querySelectorAll(`progress.row-progress[data-hid="${id}"]`)) {
    p.value = Math.min(seconds, p.max);
    describe(p, seconds, p.max);
  }
});

/** The position this page knows for a row (a save in this page wins over the loaded row). */
export function knownPosition(id, seconds) {
  return live.has(id) ? live.get(id) : Number(seconds) || 0;
}

/** The value worth keeping: 0 when too early or within the finished tail. */
function keepable(seconds, total) {
  if (!(seconds >= IGNORE_UNDER_S)) return 0;
  if (total && seconds >= total - TAIL_S) return 0;
  return seconds;
}

const PENDING = "nlh.pos.pending.";
function pending(id, seconds) {
  try {
    if (seconds === null) localStorage.removeItem(PENDING + id);
    else localStorage.setItem(PENDING + id, String(seconds));
  } catch (e) { /* private window: nothing to keep */ }
}

/** Send a position to the local server. keepalive lets it finish while the page closes. */
async function send(id, seconds, { final = false, playing = false, keepalive = false } = {}) {
  try {
    const res = await fetch(`/api/position/${id}`, {
      method: "POST",
      headers: { "X-NLH-Req": "1", "Content-Type": "application/json" },
      credentials: "same-origin",
      keepalive,
      body: JSON.stringify({ position_s: Math.max(0, seconds), final, playing }),
    });
    if (!res.ok) return false;
    pending(id, null);
    return true;
  } catch (e) {
    pending(id, seconds); // off Wi-Fi or the laptop away: send it on the next load or play
    return false;
  }
}

/** Save where the player is now. */
function save({ final = false, keepalive = false } = {}) {
  const a = state.audio;
  if (!a || state.id === null || !state.applied) return Promise.resolve(false);
  state.lastSave = Date.now();
  const total = Number.isFinite(a.duration) ? a.duration : 0;
  const value = keepable(a.currentTime, total);
  announce(state.id, value);
  return send(state.id, value, { final, playing: !a.paused && !a.ended, keepalive });
}

/** The window asks this before it closes (resume playback: no lost seconds on close or Cmd+Q). */
window.__nlhSavePosition = () => { save({ final: true }); };

/** The page's Quit: save first, at most QUIT_WAIT_MS, then carry on. */
export function savePositionNow() {
  if (!state.audio || state.id === null) return Promise.resolve();
  return Promise.race([save({ final: true }), new Promise((r) => setTimeout(r, QUIT_WAIT_MS))]);
}

async function fetchPosition(id) {
  try {
    const res = await fetch(`/api/position/${id}`, { credentials: "same-origin", headers: { "X-NLH-Req": "1" } });
    if (!res.ok) return 0;
    const data = await res.json();
    return Number(data.position_s) || 0;
  } catch (e) { return 0; }
}

function hideLine() {
  clearTimeout(state.lineTimer);
  const line = $("pl-resume");
  if (line) line.classList.add("is-hidden");
  const sub = $("pl-sub");
  if (sub) sub.classList.remove("is-hidden");
}

function showLine(seconds) {
  $("pl-resume-text").textContent = `Continuing from ${duration(seconds)}`;
  $("pl-sub").classList.add("is-hidden");
  $("pl-resume").classList.remove("is-hidden");
  clearTimeout(state.lineTimer);
  state.lineTimer = setTimeout(hideLine, LINE_MS);
}

/** Move to the saved place once the file's length is known, a few seconds back, and say so. */
function resumeAt(id, seconds) {
  const a = state.audio;
  const go = () => {
    if (state.id !== id) return;
    const total = Number.isFinite(a.duration) ? a.duration : 0;
    if (keepable(seconds, total) > 0) {
      state.ownSeek = true; // this seek is the player's, not the listener's: keep the line
      a.currentTime = Math.max(0, seconds - REWIND_S);
      showLine(seconds);
    }
    a.muted = false;
    state.applied = true;
  };
  if (a.readyState >= 1) go(); else a.addEventListener("loadedmetadata", go, { once: true });
}

/** Play a saved file by its ledger row id. */
export function play(id, title, notebook) {
  if (!state.audio) build();
  const a = state.audio;
  if (state.id === id) { if (a.paused) a.play(); return; }
  if (state.id !== null) save();
  hideLine();
  state.id = id;
  state.title = title;
  state.notebook = notebook || "";
  state.applied = false; // no saves until the saved place is applied (it would overwrite it with 0)
  a.src = `/api/play/${id}`;
  $("pl-title").textContent = title;
  $("pl-sub").textContent = notebook || "";
  // Start inside the click (phones allow sound only then), silent until the saved place is known.
  a.muted = true;
  // AbortError only means another play or a pause came first (a quick switch): not a fault.
  a.play().catch((e) => { if (!e || e.name !== "AbortError") toast("The file could not be played.", "bad"); });
  let local = null;
  try { local = localStorage.getItem(PENDING + id); } catch (e) { local = null; }
  const known = local !== null ? Promise.resolve(Number(local) || 0) : fetchPosition(id);
  const timer = setTimeout(() => { if (state.id === id && !state.applied) { a.muted = false; state.applied = true; } }, QUIT_WAIT_MS);
  known.then((seconds) => {
    clearTimeout(timer);
    if (state.id !== id) return;
    if (local !== null) send(id, seconds); // the save that did not get through earlier
    if (state.applied) { // the wait ran out: still move, unless the listener has gone far already
      if (seconds > 0 && a.currentTime < IGNORE_UNDER_S) resumeAt(id, seconds);
      return;
    }
    resumeAt(id, seconds);
  });
  $("player").classList.remove("is-hidden");
  document.body.classList.add("has-player");
  mediaSession(title, notebook);
}

/** One time: positions an older version kept only in this browser go to the server (unless it has one). */
export async function carryOverPositions() {
  let keys = [];
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (/^nlh\.pos\.\d+$/.test(k) || k.startsWith(PENDING)) keys.push(k);
    }
  } catch (e) { return; }
  for (const k of keys) {
    const isPending = k.startsWith(PENDING);
    const id = Number(k.slice(isPending ? PENDING.length : "nlh.pos.".length));
    let seconds = 0;
    try { seconds = Number(localStorage.getItem(k)) || 0; } catch (e) { continue; }
    if (!isPending && (await fetchPosition(id)) > 0) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } continue; }
    if (seconds >= IGNORE_UNDER_S && (await send(id, seconds))) {
      try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
    } else if (!isPending) {
      try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
    }
  }
}

/** Title, notebook and buttons on a phone's lock screen and in system controls, where supported. */
function mediaSession(title, notebook) {
  if (!("mediaSession" in navigator)) return;
  const a = state.audio;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title, artist: notebook || "", album: document.title.split(" · ")[0] });
    const seek = (d) => {
      const end = Number.isFinite(a.duration) ? a.duration - 0.5 : Infinity; // length unknown yet: no upper clamp
      a.currentTime = Math.max(0, Math.min(end, a.currentTime + d));
    };
    navigator.mediaSession.setActionHandler("play", () => a.play());
    navigator.mediaSession.setActionHandler("pause", () => a.pause());
    navigator.mediaSession.setActionHandler("seekbackward", (e) => seek(-(e.seekOffset || 15)));
    navigator.mediaSession.setActionHandler("seekforward", (e) => seek(e.seekOffset || 15));
    navigator.mediaSession.setActionHandler("seekto", (e) => { if (e.seekTime !== undefined) a.currentTime = e.seekTime; });
  } catch (e) { /* older browsers: the page controls still work */ }
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
  const resume = el("div", "pl-resume is-hidden");
  resume.id = "pl-resume";
  const resumeText = el("span", "pl-resume-text");
  resumeText.id = "pl-resume-text";
  const restart = button("Start from the beginning", { cls: "btn-link pl-restart" });
  restart.id = "pl-restart";
  restart.addEventListener("click", () => {
    a.currentTime = 0;
    hideLine();
    if (a.paused) a.play();
  });
  resume.append(resumeText, restart);
  info.append(t, sub, resume);
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
    if (state.id !== null) save();
    hideLine();
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
  a.addEventListener("pause", () => { setIcon("play", "Play"); if (!a.ended) save(); });
  a.addEventListener("seeked", () => {
    if (state.ownSeek) { state.ownSeek = false; return; }
    if (state.applied && !a.paused) save();
  });
  a.addEventListener("seeking", () => { if (!state.ownSeek && state.applied) hideLine(); });
  a.addEventListener("timeupdate", () => {
    if (a.duration) seek.value = String(Math.round((a.currentTime / a.duration) * 1000));
    time.textContent = `${duration(a.currentTime)} / ${duration(a.duration || 0)}`;
    if (!a.paused && Date.now() - state.lastSave >= SAVE_EVERY_MS) save();
  });
  a.addEventListener("ended", () => {
    if (state.id === null) return;
    announce(state.id, 0);
    send(state.id, 0); // finished: the next play starts at 0:00
  });
  // The tab or window going away: save with keepalive so the request outlives the page.
  window.addEventListener("pagehide", () => save({ keepalive: true }));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") save({ keepalive: true }); });
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
