// Studio (D-025): ask a notebook for an overview plan, edit and keep it (S1). Every request
// toward NotebookLM starts from a click here; nothing runs on its own. No account is chosen
// for you: pick one every time.
import { api } from "./api.js";
import { $, el, button, plural, shortDate } from "./dom.js";
import { toast, dialog } from "./ui.js";

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
  wrap.append(intro, accountCard(), notebookCard());
  if (st.answer || st.asking) wrap.append(planCard());
  wrap.append(keptCard());
  view.replaceChildren(wrap);
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
    actions.append(button("Hide", { cls: "btn btn-sm btn-ghost", title: `Hide the plan for ${p.notebook_title}`, onClick: async () => {
      try { await api(`/api/studio/plans/${p.id}/archive`, { method: "POST" }); await renderStudio(); } catch (e) { toast(e.message, "bad"); }
    } }));
    li.append(head, ol, actions);
    list.append(li);
  }
  c.append(list);
  return c;
}

/** Demo-mode test hooks. */
export function studioHooks() {
  return {
    state: () => ({ account: st.account, notebook: st.notebook, asking: st.asking, rows: st.draft.length, answer: !!st.answer }),
  };
}
