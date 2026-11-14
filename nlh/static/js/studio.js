// Studio (D-025): ask a notebook for an overview plan, edit and keep it (S1). Every request
// toward NotebookLM starts from a click here; nothing runs on its own. No account is chosen
// for you: pick one every time.
import { api } from "./api.js";
import { $, el, button, plural, shortDate } from "./dom.js";
import { toast, dialog, confirmDialog } from "./ui.js";
import { play } from "./player.js";

const st = {
  data: null, // /api/studio/state
  account: null, // chosen account id (never preselected)
  notebooks: [],
  notebook: null, // {id, title}
  nbFilter: "",
  question: "",
  asking: null, // job id while a plan request runs
  answer: null, // {raw, parsed, notebook_id, notebook_title, account_id}
  draft: [], // editable rows [{title, focus}]
  queue: null, // /api/studio/queue
  making: null, // plan id whose "make overviews" panel is open
  makeRows: [], // per-item choices for that plan
  pollTimer: null,
};

const FORMAT_TEXT = { deep_dive: "Deep dive", brief: "Brief", critique: "Critique", debate: "Debate" };
const LENGTH_TEXT = { short: "Short", default: "Default", long: "Long" };
export { FORMAT_TEXT, LENGTH_TEXT };

function card(title, hint, cls = "") {
  const s = el("section", `set-card studio-card ${cls}`.trim());
  s.append(el("h2", "", title));
  if (hint) s.append(el("p", "hint", hint));
  return s;
}

function chosenAccount() {
  return st.data ? st.data.accounts.find((a) => a.id === st.account) || null : null;
}

/** Draw the Studio view. */
export async function renderStudio() {
  const view = $("view-studio");
  try {
    st.data = await api("/api/studio/state");
  } catch (e) {
    view.replaceChildren(el("p", "error", e.message));
    return;
  }
  try { st.queue = await api("/api/studio/queue"); } catch (e) { st.queue = null; }
  if (!st.question) st.question = st.data.default_question;
  if (st.account !== null && !chosenAccount()) st.account = null;
  draw();
}

function draw() {
  const view = $("view-studio");
  const wrap = el("div", "studio");
  const intro = el("div", "studio-intro");
  intro.append(
    el("h1", "view-title", "Studio"),
    el("p", "hint", "Ask a notebook how its sources split into audio overviews, edit the plan, then keep it. Nothing is asked or made until you click."),
  );
  wrap.append(intro);
  if (st.queue && st.queue.rows.length) wrap.append(queueCard());
  wrap.append(accountCard(), notebookCard());
  if (st.answer || st.asking) wrap.append(planCard());
  wrap.append(keptCard());
  const keepFocus = document.activeElement && document.activeElement.id;
  view.replaceChildren(wrap);
  if (keepFocus && $(keepFocus)) $(keepFocus).focus();
  schedulePoll();
}

// ------------------------------------------------------------ account

function accountCard() {
  const c = card("1. Account", "Choose the Google account to work in. The app never picks one for you.");
  const lab = el("label", "field", "Account");
  const sel = el("select", "select");
  sel.id = "studio-account";
  const ph = el("option", "", "Choose an account");
  ph.value = "";
  sel.append(ph);
  for (const a of st.data.accounts) {
    const o = el("option", "", `${a.label}${a.email ? ` (${a.email})` : ""}${a.shared ? " - used by other automated tools" : ""}`);
    o.value = String(a.id);
    sel.append(o);
  }
  sel.value = st.account === null ? "" : String(st.account);
  sel.addEventListener("change", async () => {
    st.account = sel.value ? Number(sel.value) : null;
    st.notebook = null;
    st.notebooks = [];
    st.answer = null;
    st.draft = [];
    if (st.account !== null) await loadNotebooks();
    draw();
    $("studio-account")?.focus();
  });
  lab.append(sel);
  c.append(lab);
  const a = chosenAccount();
  if (a) {
    const used = st.data.used_today[a.id] || 0;
    const facts = el("p", "studio-cap");
    facts.id = "studio-cap";
    facts.textContent = `${used} of ${st.data.cap} overviews made today with this account (the limit resets at midnight; change it in Settings).`;
    c.append(facts);
    if (a.shared) {
      const warn = el("div", "notice notice-warn");
      warn.append(el("span", "grow", "This account is marked as used by other automated tools. Their runs share its daily NotebookLM allowance."));
      c.append(warn);
    }
    if (a.status === "needs_sign_in") c.append(el("p", "error", "This account needs Sign in again (Library tab) before Studio can use it."));
  }
  return c;
}

async function loadNotebooks() {
  try {
    const r = await api(`/api/studio/notebooks?account_id=${st.account}`);
    st.notebooks = r.notebooks;
    st.notebooksLoaded = r.loaded;
  } catch (e) {
    toast(e.message, "bad");
    st.notebooks = [];
  }
}

// ------------------------------------------------------------ notebook and question

function notebookCard() {
  const c = card("2. Notebook and question");
  if (st.account === null) {
    c.append(el("p", "hint", "Choose an account first."));
    return c;
  }
  if (!st.notebooksLoaded) {
    c.append(el("p", "hint", "This account's notebooks have not been read yet. Use Refresh on the Library tab first."));
    return c;
  }
  const flab = el("label", "field", "Find a notebook");
  const find = el("input", "input");
  find.type = "search";
  find.id = "studio-nb-find";
  find.value = st.nbFilter;
  find.placeholder = "Type part of a notebook name";
  flab.append(find);
  const list = el("fieldset", "nb-list");
  list.id = "studio-nb-list";
  list.append(el("legend", "sr-only", "Notebook"));
  const fill = () => {
    const q = st.nbFilter.trim().toLowerCase();
    const rows = st.notebooks.filter((n) => !q || n.title.toLowerCase().includes(q));
    list.replaceChildren(el("legend", "sr-only", "Notebook"));
    for (const n of rows.slice(0, 200)) {
      const l = el("label", "nb-option");
      const r = el("input");
      r.type = "radio";
      r.name = "studio-nb";
      r.value = n.id;
      r.checked = st.notebook && st.notebook.id === n.id;
      r.addEventListener("change", () => { st.notebook = { id: n.id, title: n.title }; askBtn.disabled = !!st.asking; chosen.textContent = `Chosen: ${n.title}`; });
      l.append(r, el("span", "grow", n.title), el("span", "hint", plural(n.overviews, "overview")));
      list.append(l);
    }
    if (!rows.length) list.append(el("p", "hint", "No notebook matches."));
    else if (rows.length > 200) list.append(el("p", "hint", `Showing 200 of ${rows.length}. Type more of the name to narrow the list.`));
  };
  find.addEventListener("input", () => { st.nbFilter = find.value; fill(); });
  const chosen = el("p", "studio-chosen", st.notebook ? `Chosen: ${st.notebook.title}` : "No notebook chosen.");
  chosen.setAttribute("aria-live", "polite");

  const qd = el("details", "studio-question");
  qd.append(el("summary", "", "Question that will be asked"));
  const qlab = el("label", "field", "Question");
  const q = el("textarea", "input");
  q.id = "studio-question";
  q.rows = 5;
  q.maxLength = 2000;
  q.value = st.question;
  q.addEventListener("input", () => { st.question = q.value; });
  qlab.append(q);
  const reset = button("Use the suggested question", { cls: "btn btn-sm btn-ghost", onClick: () => { st.question = st.data.default_question; q.value = st.question; } });
  qd.append(qlab, reset);

  const askBtn = button(st.asking ? "Asking..." : "Ask for a plan", { cls: "btn btn-primary", iconName: "studio", onClick: () => ask() });
  askBtn.id = "studio-ask";
  askBtn.disabled = !st.notebook || !!st.asking;
  const row = el("div", "row");
  row.append(askBtn);
  c.append(flab, list, chosen, qd, row, el("p", "hint", "Asking sends one message in that notebook's chat, on its current conversation. It does not make any audio."));
  fill();
  return c;
}

async function ask() {
  if (!st.notebook || st.asking) return;
  const body = { account_id: st.account, notebook_id: st.notebook.id, question: st.question };
  let job;
  try {
    job = await api("/api/studio/ask", { method: "POST", body });
  } catch (e) {
    if (e.data && e.data.needs_ack) {
      const ok = await dialog({
        title: "Ask this notebook?",
        body: el("p", "", "Asking adds your question and NotebookLM's answer to this notebook's chat, where you will also see them in NotebookLM. Nothing else in the notebook changes. This message is shown once."),
        actions: [{ label: "Cancel", value: false }, { label: "Ask", value: true, primary: true }],
      });
      if (ok !== true) return;
      try { job = await api("/api/studio/ask", { method: "POST", body: { ...body, acknowledged: true } }); }
      catch (e2) { toast(e2.message, "bad"); return; }
    } else { toast(e.message, "bad"); return; }
  }
  st.asking = job.id;
  st.answer = null;
  draw();
  pollAsk();
}

async function pollAsk() {
  if (!st.asking) return;
  let job;
  try { job = await api(`/api/jobs/${st.asking}`); } catch (e) { st.asking = null; toast(e.message, "bad"); draw(); return; }
  if (job.state === "queued" || job.state === "running") { setTimeout(pollAsk, 800); return; }
  st.asking = null;
  if (job.state === "done" && job.result) {
    st.answer = job.result;
    st.draft = job.result.parsed.items.map((i) => ({ ...i }));
    toast(job.message);
  } else {
    toast(job.message || "The plan request did not finish.", "bad");
  }
  draw();
  $("studio-plan")?.focus();
}

// ------------------------------------------------------------ plan editor

function planCard() {
  const c = card("3. Plan", null, "studio-plan-card");
  if (st.asking) {
    const p = el("p", "hint", "Waiting for NotebookLM's answer. This usually takes about half a minute.");
    p.setAttribute("aria-live", "polite");
    const bar = el("div", "bar indeterminate");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Waiting for the plan");
    bar.append(el("span", "bar-fill"));
    c.append(bar, p);
    return c;
  }
  const a = st.answer;
  const parsed = a.parsed;
  const sum = el("p", "studio-summary");
  sum.id = "studio-plan";
  sum.tabIndex = -1;
  const found = parsed.items.length;
  sum.textContent = `${a.notebook_title}: `
    + (parsed.count ? `NotebookLM suggests ${plural(parsed.count, "overview")}. ` : "")
    + (found ? `${plural(found, "row")} read from the answer. Check and edit them before keeping the plan.` : "No list could be read from the answer. Add the overviews yourself, using the answer below.");
  c.append(sum);

  const rows = el("ol", "plan-rows");
  rows.id = "studio-rows";
  st.draft.forEach((it, i) => rows.append(planRow(it, i)));
  c.append(rows);
  const add = button("Add an overview", { cls: "btn btn-sm", iconName: "plus", onClick: () => { st.draft.push({ title: "", focus: "" }); draw(); const t = document.querySelectorAll(".plan-row input"); t[t.length - 1]?.focus(); } });
  add.id = "studio-add-row";
  const keep = button("Keep this plan", { cls: "btn btn-primary", iconName: "check", onClick: () => keepPlan() });
  keep.id = "studio-keep";
  keep.disabled = !st.draft.length;
  const discard = button("Discard", { cls: "btn btn-ghost", onClick: () => { st.answer = null; st.draft = []; draw(); } });
  const actions = el("div", "row");
  actions.append(add, keep, discard);
  c.append(actions);

  const raw = el("details", "studio-raw");
  raw.open = true;
  raw.append(el("summary", "", "NotebookLM's answer, as received"));
  const pre = el("pre", "raw-answer");
  pre.id = "studio-raw";
  pre.tabIndex = 0;
  pre.setAttribute("role", "region");
  pre.setAttribute("aria-label", "NotebookLM's answer");
  pre.textContent = a.raw;
  raw.append(pre, el("p", "hint", "This text is not saved. Only the plan you keep is stored."));
  c.append(raw);
  return c;
}

function planRow(it, i) {
  const li = el("li", "plan-row");
  const tl = el("label", "field", `Overview ${i + 1} title`);
  const t = el("input", "input");
  t.value = it.title;
  t.maxLength = 200;
  t.addEventListener("input", () => { it.title = t.value; });
  tl.append(t);
  const fl = el("label", "field", "Focus (what this overview should cover)");
  const f = el("textarea", "input");
  f.rows = 3;
  f.maxLength = 2000;
  f.value = it.focus;
  f.addEventListener("input", () => { it.focus = f.value; });
  fl.append(f);
  const tools = el("div", "row plan-tools");
  const up = button("", { cls: "btn btn-ghost btn-icon btn-sm", iconName: "up", title: `Move overview ${i + 1} up`, onClick: () => { if (i > 0) { [st.draft[i - 1], st.draft[i]] = [st.draft[i], st.draft[i - 1]]; draw(); } } });
  up.disabled = i === 0;
  const rm = button("", { cls: "btn btn-ghost btn-icon btn-sm", iconName: "trash", title: `Remove overview ${i + 1}`, onClick: () => { st.draft.splice(i, 1); draw(); } });
  tools.append(up, rm);
  li.append(tl, fl, tools);
  return li;
}

async function keepPlan() {
  const items = st.draft.map((x) => ({ title: x.title.trim(), focus: x.focus.trim() }));
  if (items.some((x) => !x.title)) { toast("Every overview needs a title.", "warn"); return; }
  try {
    await api("/api/studio/plans", { method: "POST", body: { account_id: st.answer.account_id, notebook_id: st.answer.notebook_id, items } });
    toast(`Plan kept: ${plural(items.length, "overview")}.`);
    st.answer = null;
    st.draft = [];
    await renderStudio();
  } catch (e) { toast(e.message, "bad"); }
}

// ------------------------------------------------------------ kept plans

function keptCard() {
  const c = card("Kept plans");
  c.id = "studio-kept";
  const plans = st.data.plans.filter((p) => st.account === null || p.account_id === st.account);
  if (!plans.length) {
    c.append(el("p", "hint", st.account === null ? "No kept plans yet." : "No kept plans for this account yet."));
    return c;
  }
  const list = el("ul", "kept-list");
  for (const p of plans) {
    const li = el("li", "kept-plan");
    const head = el("div", "kept-head");
    const acct = st.data.accounts.find((a) => a.id === p.account_id);
    head.append(el("strong", "grow", p.notebook_title), el("span", "hint", `${plural(p.items.length, "overview")} · ${acct ? acct.label : ""} · ${shortDate(p.created_at)}`));
    const ol = el("ol", "kept-items");
    for (const it of p.items) {
      const item = el("li");
      item.append(el("span", "kept-title", it.title));
      if (it.focus) item.append(el("span", "hint kept-focus", it.focus));
      ol.append(item);
    }
    const actions = el("div", "row");
    if (st.making !== p.id) {
      const mk = button("Make overviews...", { cls: "btn btn-sm btn-primary", iconName: "studio", title: `Choose which overviews to make for ${p.notebook_title}`, onClick: () => openMake(p) });
      mk.dataset.plan = String(p.id);
      actions.append(mk);
    }
    actions.append(button("Hide", { cls: "btn btn-sm btn-ghost", title: `Hide the plan for ${p.notebook_title}`, onClick: async () => {
      try { await api(`/api/studio/plans/${p.id}/archive`, { method: "POST" }); await renderStudio(); } catch (e) { toast(e.message, "bad"); }
    } }));
    li.append(head, ol, actions);
    if (st.making === p.id) li.append(makePanel(p));
    list.append(li);
  }
  c.append(list);
  return c;
}

// ------------------------------------------------------------ S2: make overviews from a plan

function openMake(p) {
  st.making = p.id;
  st.makeRows = p.items.map((it) => ({ ...it, on: true, audio_format: "deep_dive", audio_length: "long", language: st.data.language }));
  draw();
  document.querySelector(".make-panel input")?.focus();
}

function selectField(label, value, options, onChange) {
  const lab = el("label", "field", label);
  const sel = el("select", "select");
  for (const [v, t] of options) { const o = el("option", "", t); o.value = v; sel.append(o); }
  sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value));
  lab.append(sel);
  return lab;
}

function makePanel(p) {
  const box = el("div", "make-panel");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", `Make overviews for ${p.notebook_title}`);
  const used = (st.queue && st.queue.used_today[p.account_id]) || 0;
  const cap = st.data.cap;
  const info = el("p", "studio-cap make-cap");
  const update = () => {
    const n = st.makeRows.filter((r) => r.on).length;
    info.textContent = `${used} of ${cap} made today with this account. ${n} chosen.`
      + (used + n > cap ? ` Only ${Math.max(0, cap - used)} can go today; the rest wait in the queue for another day.` : "");
    add.disabled = n === 0;
  };
  box.append(info);
  const list = el("ol", "make-rows");
  st.makeRows.forEach((r, i) => {
    const li = el("li", "make-row");
    const chk = el("label", "check");
    const box2 = el("input");
    box2.type = "checkbox";
    box2.checked = r.on;
    box2.addEventListener("change", () => { r.on = box2.checked; update(); });
    chk.append(box2, el("span", "lbl make-title", r.title));
    const focus = el("label", "field", "Focus");
    const ta = el("textarea", "input");
    ta.rows = 2;
    ta.maxLength = 2000;
    ta.value = r.focus;
    ta.addEventListener("input", () => { r.focus = ta.value; });
    focus.append(ta);
    const opts = el("div", "make-opts");
    opts.append(
      selectField("Format", r.audio_format, st.data.formats.map((f) => [f, FORMAT_TEXT[f]]), (v) => { r.audio_format = v; }),
      selectField("Length", r.audio_length, st.data.lengths.map((f) => [f, LENGTH_TEXT[f]]), (v) => { r.audio_length = v; }),
    );
    const lang = el("label", "field", "Language");
    const li2 = el("input", "input num");
    li2.value = r.language;
    li2.maxLength = 12;
    li2.spellcheck = false;
    li2.addEventListener("input", () => { r.language = li2.value.trim(); });
    lang.append(li2);
    opts.append(lang);
    li.append(chk, focus, opts);
    li.dataset.index = String(i);
    list.append(li);
  });
  box.append(list);
  const add = button("Add to the queue", { cls: "btn btn-primary", iconName: "plus", onClick: () => enqueue(p) });
  add.id = "studio-enqueue";
  const cancel = button("Close", { cls: "btn btn-ghost", onClick: () => { st.making = null; draw(); } });
  const row = el("div", "row");
  row.append(add, cancel);
  box.append(row, el("p", "hint", "Adding to the queue sends nothing. Requests go one at a time only after you click Start in the queue."));
  update();
  return box;
}

async function enqueue(p) {
  const items = st.makeRows.filter((r) => r.on).map((r) => ({ title: r.title, focus: r.focus.trim(), audio_format: r.audio_format, audio_length: r.audio_length, language: r.language }));
  try {
    st.queue = await api("/api/studio/queue", { method: "POST", body: { plan_id: p.id, items } });
    toast(`${plural(items.length, "overview")} added to the queue. Click Start when you are ready.`);
    st.making = null;
    draw();
    $("studio-start")?.focus();
  } catch (e) { toast(e.message, "bad"); }
}

// ------------------------------------------------------------ S2: the queue

const QSTATE = {
  queued: ["Waiting", "new"],
  submitting: ["Sending", "running"],
  generating: ["Being made", "running"],
  done: ["Done", "done"],
  failed: ["Failed", "failed"],
  cancelled: ["Dropped", "cancelled"],
  paused_limit: ["Paused: limit", "needs_sign_in"],
  needs_review: ["Check needed", "needs_sign_in"],
};

function mmss(s) {
  const t = Math.max(0, Math.round(s));
  return t >= 60 ? `${Math.floor(t / 60)} min ${String(t % 60).padStart(2, "0")} s` : `${t} s`;
}

function queueCard() {
  const q = st.queue;
  const r = q.runner;
  const c = card("Queue", null, "studio-queue");
  c.id = "studio-queue";
  const head = el("div", "row queue-head");
  const status = el("p", "grow queue-status");
  status.id = "studio-run-status";
  status.setAttribute("aria-live", "polite");
  let text = r.message || "";
  if (r.running && r.pause_left_s !== null) text = `Pausing between requests: next in ${mmss(r.pause_left_s)}.`;
  else if (r.running && r.step) text = `${r.step}. ${r.message || ""}`;
  else if (!r.running && !text) text = "Not running. Nothing is sent until you click Start.";
  status.textContent = text;
  head.append(status);
  const waiting = q.rows.filter((x) => x.state === "queued").length;
  const making = q.rows.filter((x) => x.state === "generating").length;
  if (!r.running && (waiting || making)) {
    const start = button(making && !waiting ? "Resume" : waiting && making ? "Resume and start" : "Start", { cls: "btn btn-primary", iconName: "play", onClick: () => runQueue() });
    start.id = "studio-start";
    head.append(start);
  }
  if (r.running && !r.cancelling) {
    const stop = button("Cancel", { cls: "btn", iconName: "x", onClick: () => cancelQueue() });
    stop.id = "studio-cancel";
    head.append(stop);
  }
  c.append(head);
  if (r.running) {
    const bar = el("div", "bar indeterminate");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Studio queue running");
    bar.append(el("span", "bar-fill"));
    c.append(bar);
  }
  if (q.limited_today.length) {
    const names = q.limited_today.map((id) => (st.data.accounts.find((a) => a.id === id) || {}).label).filter(Boolean).join(", ");
    const n = el("div", "notice notice-warn");
    n.append(el("span", "grow", `NotebookLM's usage limit was reached today for ${names}. Nothing more is sent for it until tomorrow.`));
    c.append(n);
  }
  // Rows that still need something first, in queue order; finished ones after, newest first.
  const FIN = ["done", "failed", "cancelled"];
  const active = q.rows.filter((x) => !FIN.includes(x.state));
  const finished = q.rows.filter((x) => FIN.includes(x.state)).reverse();
  if (active.length) {
    const list = el("ol", "queue-rows");
    list.id = "studio-queue-rows";
    for (const row of active) list.append(queueRow(row, r));
    c.append(list);
  }
  if (finished.length) {
    const box = el("details", "queue-finished");
    box.open = !active.length || st.finishedOpen === true;
    box.addEventListener("toggle", () => { st.finishedOpen = box.open; });
    const saved = finished.filter((x) => x.state === "done").length;
    box.append(el("summary", "", `Recently finished (${finished.length}${saved ? `, ${saved} done` : ""})`));
    const list = el("ul", "queue-rows");
    for (const row of finished.slice(0, 30)) list.append(queueRow(row, r));
    box.append(list);
    c.append(box);
  }
  return c;
}

function queueRow(row, r) {
  const li = el("li", "queue-row");
  li.dataset.state = row.state;
  li.dataset.id = String(row.id);
  const [label, cls] = QSTATE[row.state] || [row.state, "unknown"];
  const top = el("div", "queue-top");
  const name = el("span", "queue-title grow", row.title);
  const pillEl = el("span", `pill st-${cls}`, label);
  top.append(name, pillEl);
  const acct = st.data.accounts.find((a) => a.id === row.account_id);
  const meta = el("p", "hint queue-meta", `${row.notebook_title} · ${FORMAT_TEXT[row.audio_format]}, ${LENGTH_TEXT[row.audio_length].toLowerCase()}, ${row.language}${acct ? ` · ${acct.label}` : ""}`);
  li.append(top, meta);
  if (row.message) li.append(el("p", "queue-msg", row.message));
  const actions = el("div", "row queue-actions");
  const busy = r.running && r.current === row.id;
  if (row.state === "done" && row.harvest_id) {
    actions.append(button("Play", { cls: "btn btn-sm btn-primary", iconName: "play", title: `Play ${row.title}`, onClick: () => play(row.harvest_id, row.title, row.notebook_title) }));
  }
  if (row.state === "needs_review") {
    actions.append(button("Check NotebookLM", { cls: "btn btn-sm btn-primary", iconName: "search", title: `Check whether ${row.title} was made`, onClick: () => rowAction(row, "check") }));
  }
  if (["failed", "cancelled", "paused_limit"].includes(row.state)) {
    actions.append(button("Queue again", { cls: "btn btn-sm", iconName: "refresh", title: `Queue ${row.title} again`, onClick: () => rowAction(row, "requeue") }));
  }
  if (["queued", "generating", "needs_review", "paused_limit"].includes(row.state) && !busy) {
    actions.append(button("Drop", { cls: "btn btn-sm btn-ghost", iconName: "x", title: `Drop ${row.title}`, onClick: () => rowAction(row, "drop") }));
  }
  if (actions.children.length) li.append(actions);
  return li;
}

async function runQueue() {
  const waiting = st.queue.rows.filter((x) => x.state === "queued").length;
  if (waiting) {
    const ok = await confirmDialog(
      "Start making overviews?",
      `This sends up to ${plural(waiting, "request")} to NotebookLM, one at a time with a pause between them. Each takes a few minutes and counts toward the daily limit of ${st.queue.cap} per account. You can cancel at any time.`,
      "Start",
    );
    if (!ok) return;
  }
  try { st.queue = await api("/api/studio/run", { method: "POST" }); draw(); } catch (e) { toast(e.message, "bad"); }
}

async function cancelQueue() {
  try { st.queue = await api("/api/studio/cancel", { method: "POST" }); draw(); } catch (e) { toast(e.message, "bad"); }
}

async function rowAction(row, what) {
  try {
    const r = await api(`/api/studio/queue/${row.id}/${what}`, { method: "POST" });
    st.queue = r;
    if (what === "check") toast(r.found ? "Found in NotebookLM. Click Resume to collect it." : "Not found, so it was not made. Use Queue again to send it.", r.found ? "ok" : "warn");
    draw();
  } catch (e) { toast(e.message, "bad"); }
}

function schedulePoll() {
  clearTimeout(st.pollTimer);
  const q = st.queue;
  if (!q || !q.runner.running) return;
  st.pollTimer = setTimeout(async () => {
    if ($("view-studio").classList.contains("is-hidden")) { schedulePoll(); return; }
    try {
      const before = q.runner.running;
      st.queue = await api("/api/studio/queue");
      if (before && !st.queue.runner.running) {
        st.data = await api("/api/studio/state");
        toast(st.queue.runner.message || "The queue stopped.", "ok");
      }
    } catch (e) { /* keep the last view; try again */ }
    const old = $("studio-queue");
    if (st.queue.runner.running && old) {
      // Only the queue card changes while it runs; typing elsewhere is never disturbed.
      const focusId = document.activeElement && old.contains(document.activeElement) ? document.activeElement.id : null;
      old.replaceWith(queueCard());
      if (focusId && $(focusId)) $(focusId).focus();
      schedulePoll();
    } else draw();
  }, 1000);
}

/** Demo-mode test hooks. */
export function studioHooks() {
  return {
    state: () => ({ account: st.account, notebook: st.notebook, asking: st.asking, rows: st.draft.length, answer: !!st.answer }),
    queue: () => (st.queue ? { running: st.queue.runner.running, states: st.queue.rows.map((r) => r.state) } : null),
  };
}
