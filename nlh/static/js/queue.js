// Harvest queue panel: each item's status (waiting, downloading with MB, converting with
// percent, saving, saved, failed with reason), elapsed time and a plain estimate of time
// left, Cancel (finishes the current safe step), Retry failed and Clear finished.
import { api } from "./api.js";
import { $, el, icon, button, plural } from "./dom.js";

const ITEM_ICON = { saved: "check", failed: "alert", cancelled: "x", skipped: "info" };
let finished = null; // the last finished job, shown until cleared

function mmss(s) {
  const t = Math.max(0, Math.round(s));
  const m = Math.floor(t / 60), sec = t % 60;
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m ? `${m} min ${String(sec).padStart(2, "0")} s` : `${sec} s`;
}

/** Plain estimate of time left, from the items finished so far. */
export function estimate(job) {
  const items = Object.values(job.items);
  const done = items.filter((i) => i.outcome).length;
  if (!done || done >= items.length) return null;
  return (job.elapsed_s / done) * (items.length - done);
}

/** Draw the queue for a job (running, or the last finished one until cleared). */
export function renderQueue(job, { onRetry, onCancel } = {}) {
  const box = $("job-box");
  if (job && !["queued", "running"].includes(job.state) && job.kind === "harvest") finished = job;
  const show = job && ["queued", "running"].includes(job.state) ? job : finished;
  if (!show) { box.replaceChildren(); return; }
  const running = ["queued", "running"].includes(show.state);
  const card = el("section", "job");
  card.setAttribute("aria-label", "Harvest queue");
  const head = el("div", "job-head");
  head.append(el("span", "grow", show.title));
  const JOB_TEXT = { queued: "Waiting", running: "Working", done: "Done", failed: "Finished with problems", cancelled: "Cancelled" };
  const pillText = show.cancelling ? "Stopping after this step" : JOB_TEXT[show.state] || show.state;
  head.append(el("span", `pill st-${show.cancelling ? "running" : show.state}`, pillText));
  if (running && show.kind === "harvest" && !show.cancelling) {
    head.append(button("Cancel", { cls: "btn btn-sm", iconName: "x", onClick: () => onCancel && onCancel(show) }));
  }
  const items = Object.entries(show.items || {});
  const failed = items.filter(([, i]) => i.outcome === "failed");
  if (!running && failed.length && onRetry) {
    head.append(button(`Retry ${plural(failed.length, "failed item")}`, { cls: "btn btn-sm btn-primary", iconName: "refresh", onClick: () => onRetry(show, failed) }));
  }
  if (!running) head.append(button("Clear finished", { cls: "btn btn-sm btn-ghost", onClick: () => { finished = null; box.replaceChildren(); } }));
  card.append(head);

  const bar = el("div", "bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", show.title);
  const fill = el("span", "bar-fill");
  if (show.kind === "harvest") {
    const pct = Math.round((show.pct || 0) * 100);
    fill.style.setProperty("--pct", `${pct}%`);
    bar.setAttribute("aria-valuenow", String(pct));
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
  } else if (running) bar.classList.add("indeterminate");
  bar.append(fill);
  card.append(bar);

  const facts = el("p", "hint");
  if (show.state === "queued") {
    facts.textContent = "Waiting for the job before it to finish.";
  } else if (show.kind === "harvest") {
    const done = items.filter(([, i]) => i.outcome).length;
    const left = running ? estimate(show) : null;
    facts.textContent = `${done} of ${items.length} done · ${mmss(show.elapsed_s || 0)} elapsed`
      + (left !== null ? ` · about ${mmss(left)} left` : running && !done ? " · estimating time left" : "")
      + (show.message ? ` · ${show.message}` : "");
  } else {
    facts.textContent = show.message || (show.lines.length ? show.lines[show.lines.length - 1] : "");
  }
  card.append(facts);

  if (show.kind === "harvest" && items.length) {
    const list = el("ol", "job-items");
    for (const [id, it] of items.slice(0, 200)) {
      const li = el("li");
      li.dataset.id = id;
      const jh = el("div", "jh");
      const name = el("span", "jt");
      if (ITEM_ICON[it.outcome]) name.append(icon(ITEM_ICON[it.outcome], "icon icon-sm"));
      name.append(document.createTextNode(it.title));
      jh.append(name, el("span", it.outcome === "failed" ? "jerr" : "", it.step));
      const b = el("div", "bar bar-thin");
      const f = el("span", "bar-fill");
      const working = show.current === id && !it.outcome;
      if (working && (it.pct === null || it.pct === undefined)) b.classList.add("indeterminate");
      else f.style.setProperty("--pct", `${Math.round((it.outcome ? 1 : it.pct || 0) * 100)}%`);
      if (it.outcome === "failed") b.classList.add("failed");
      b.append(f);
      li.append(jh, b);
      list.append(li);
    }
    if (items.length > 200) list.append(el("li", "hint", `${items.length - 200} more in this job.`));
    card.append(list);
  }
  box.replaceChildren(card);
}

/** Start a retry job for the failed items of a finished job. */
export function retryItems(failed) {
  const items = failed.map(([id, it]) => ({ account_id: it.account_id, artifact_id: id }));
  const redownload = failed.filter(([, it]) => it.redo).map(([id]) => id);
  return api("/api/harvest", { method: "POST", body: { items, redownload } });
}
