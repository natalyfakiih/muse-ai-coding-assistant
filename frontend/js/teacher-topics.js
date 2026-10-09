/* "What students ask Ollie": topic counts from the Chat with Ollie page.
 * Only which note a question matched and how many students asked; the
 * questions themselves are never stored, so they can't be shown here. */
(() => {
  const box = document.getElementById("ollie-topics");
  const esc = (t) => String(t == null ? "" : t)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const course = () => (document.getElementById("course") || {}).value || "CS101";

  async function load() {
    try {
      const subject = (document.getElementById("subject-filter") || {}).value;
      const res = await fetch(`/api/courses/${encodeURIComponent(course())}/analytics/ollie-topics?days=7${subject ? `&subject_id=${subject}` : ""}`);
      if (!res.ok) throw new Error(`the server answered ${res.status}`);
      render(await res.json());
    } catch (err) {
      box.innerHTML = `<p class="tagline">Couldn't load Ollie's topics (${esc(err.message)}).</p>`;
    }
  }

  function render(d) {
    const privacy = `<p class="ot-private">You see topics and counts only. What students wrote to Ollie is never stored.</p>`;
    if (!d.total_questions) {
      box.innerHTML = `<p class="ot-lead">No questions to Ollie in the last ${d.days} days yet.</p>${privacy}`;
      return;
    }
    const max = Math.max(1, ...d.topics.map((t) => t.questions));
    const rows = d.topics.map((t) => `
      <tr>
        <td><b>${esc(t.note)}</b><div class="tagline">${esc(t.subject)}</div></td>
        <td class="num">${t.questions}</td>
        <td class="num">${t.students}</td>
        <td class="ot-barcell"><div class="ot-bar"><span style="width:${Math.round(t.questions / max * 100)}%"></span></div></td>
      </tr>`).join("");
    const gap = d.not_in_notes.questions
      ? `<div class="ot-gap"><b>${d.not_in_notes.questions} question${d.not_in_notes.questions === 1 ? "" : "s"}
           from ${d.not_in_notes.students} student${d.not_in_notes.students === 1 ? "" : "s"}</b>
           didn't match any of your notes. Adding a note under Subjects &amp; notes lets Ollie answer from it.</div>`
      : "";
    const top = d.topics[0];
    box.innerHTML = `
      <p class="ot-lead"><b>${d.total_questions}</b> question${d.total_questions === 1 ? "" : "s"} from
        <b>${d.students}</b> student${d.students === 1 ? "" : "s"} in the last ${d.days} days.
        ${top ? `Most asked about: <b>${esc(top.note)}</b>.` : ""}</p>
      ${rows ? `<div class="table-scroll"><table>
        <thead><tr><th>Note</th><th class="num">Questions</th><th class="num">Students</th><th>Share</th></tr></thead>
        <tbody>${rows}</tbody></table></div>` : ""}
      ${gap}
      ${privacy}`;
  }

  load();
  const refresh = document.getElementById("refresh");
  if (refresh) refresh.addEventListener("click", load);
  const sel = document.getElementById("course");
  if (sel) sel.addEventListener("change", load);
  const subj = document.getElementById("subject-filter");
  if (subj) subj.addEventListener("change", load);
  // The picker is filled after the first load; load again once it has a value.
  if (subj) new MutationObserver(load).observe(subj, { childList: true });
})();
