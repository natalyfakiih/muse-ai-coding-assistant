/* MUSE instructor client. Read-only over data that already exists, except for
   two writes: the narrative summary (behind a button) and — added below —
   creating a new exercise, which is the one action that changes what
   students see on their own page. */

const $ = (id) => document.getElementById(id);
const api = async (path, options = {}) => {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(body.detail || response.statusText), { status: response.status });
  return body;
};

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { courseId: "CS101", studentId: null, assignmentId: null,
                overview: null, subjectId: null, checkers: null };

function banner(text, kind = "") {
  const element = $("banner");
  if (!text) { element.classList.add("hidden"); return; }
  element.className = `banner ${kind}`;
  element.textContent = text;
}

const stat = (n, label) => `<div class="stat"><div class="n">${n}</div><div class="l">${label}</div></div>`;
const truncate = (text, n) => {
  const value = String(text ?? "");
  return value.length > n ? `${value.slice(0, n - 1)}…` : value;
};

/* ------------------------------------------------------------- class view */
async function loadClass() {
  const patterns = await api(`/courses/${state.courseId}/analytics/class-patterns`);
  const mix = patterns.question_mix || {};
  const total = (mix.factual || 0) + (mix.problem_solving || 0);

  $("stats").innerHTML = [
    stat(patterns.students, "students"),
    stat(total, "tutor questions"),
    stat(total ? `${Math.round((mix.problem_solving || 0) / total * 100)}%` : "—", "problem-solving"),
    stat(patterns.escape_hatch_uses, "direct hints taken"),
    stat(patterns.spec_versions_mean, "mean spec versions"),
    stat(Object.values(patterns.diff_category_totals || {}).reduce((a, b) => a + b, 0), "discrepancies"),
  ].join("");

  $("patterns").innerHTML = patterns.diff_patterns.length
    ? patterns.diff_patterns.map((p) => `
        <tr>
          <td>${escapeHtml(p.spec_item)}</td>
          <td><span class="tag ${p.category}">${p.category}</span></td>
          <td class="num">${p.students}</td>
          <td><div class="bar ${p.category}"><span style="width:${Math.round(p.share_of_class * 100)}%"></span></div>
              <span class="tagline">${Math.round(p.share_of_class * 100)}%</span></td>
        </tr>`).join("")
    : `<tr><td colspan="4" class="tagline">No submissions yet.</td></tr>`;
}

/* ----------------------------------------------------------- student view */
/* ------------------------------------------------------------- subjects */
/* A subject is what decides how phase 3 marks: Python runs the code, maths
   compares the answer symbolically. Phases 1 and 2 are the same either way.
   Material is written or pasted here, and Ollie explains out of it and
   nothing else. */

async function loadSubjects() {
  const [checkers, subjects] = await Promise.all([
    api("/checkers"),
    api(`/courses/${state.courseId}/subjects`),
  ]);
  state.checkers = checkers;

  const select = $("new-subject-checker");
  if (select && !select.options.length) {
    select.innerHTML = checkers
      .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.id)}</option>`).join("");
    select.addEventListener("change", showCheckerNote);
  }
  showCheckerNote();

  $("subject-list").innerHTML = subjects.length
    ? subjects.map((s) => `
        <button type="button" class="subject-row" data-subject="${s.id}"
                aria-pressed="${state.subjectId === s.id}">
          <span class="nm">${escapeHtml(s.name)}</span>
          <span class="ck">${escapeHtml(s.checker)}</span>
          <span class="ct">${s.materials} note${s.materials === 1 ? "" : "s"}</span>
        </button>`).join("")
    : `<p class="tagline">No subjects yet. Add one below.</p>`;

  document.querySelectorAll("[data-subject]").forEach((b) => {
    b.addEventListener("click", () => selectSubject(Number(b.dataset.subject),
                                                    b.querySelector(".nm").textContent));
  });
}

function showCheckerNote() {
  const select = $("new-subject-checker"), note = $("checker-note");
  if (!select || !note || !state.checkers) return;
  const found = state.checkers.find((c) => c.id === select.value);
  // The honest description, so an instructor picking "math" knows what phase 3
  // will actually do rather than finding out after their students submit.
  note.textContent = found ? found.description : "";
}

async function selectSubject(id, name) {
  state.subjectId = id;
  document.querySelectorAll("[data-subject]").forEach((b) => {
    b.setAttribute("aria-pressed", String(Number(b.dataset.subject) === id));
  });
  $("material-panel").classList.remove("hidden");
  $("material-subject").textContent = name || "";
  const materials = await api(`/subjects/${id}/materials`);
  $("material-list").innerHTML = materials.length
    ? materials.map((m) => `
        <div class="material-item">
          <b>${escapeHtml(m.title || "Untitled")}</b>
          ${escapeHtml(truncate(m.body, 150))}
        </div>`).join("")
    : `<p class="tagline">Nothing yet. Ollie will say a question isn't covered
       until there is something here.</p>`;
}

async function createSubject(event) {
  event.preventDefault();
  const status = $("subject-form-status");
  status.textContent = "Adding…";
  try {
    const subject = await api(`/courses/${state.courseId}/subjects`, {
      method: "POST",
      body: JSON.stringify({
        name: $("new-subject-name").value,
        checker: $("new-subject-checker").value,
        description: $("new-subject-desc").value || null,
      }),
    });
    $("new-subject-name").value = "";
    $("new-subject-desc").value = "";
    status.textContent = "Added.";
    await loadSubjects();
    selectSubject(subject.id, subject.name);
  } catch (error) {
    status.textContent = error.message;
  }
}

async function addMaterial(event) {
  event.preventDefault();
  if (!state.subjectId) return;
  const status = $("material-form-status");
  status.textContent = "Saving…";
  try {
    const result = await api(`/subjects/${state.subjectId}/materials`, {
      method: "POST",
      body: JSON.stringify({
        title: $("new-material-title").value,
        body: $("new-material-body").value,
      }),
    });
    $("new-material-title").value = "";
    $("new-material-body").value = "";
    status.textContent = `Saved — ${result.paragraphs} paragraph(s) Ollie can retrieve.`;
    await Promise.all([loadSubjects(), selectSubject(state.subjectId,
                                                     $("material-subject").textContent)]);
  } catch (error) {
    status.textContent = error.message;
  }
}

/* An instructor asking their own material the questions their students will.
   The useful half is the refusal: when Ollie says a topic isn't covered, that
   is decided in Python before any model runs, so it is a fact about the notes
   and a to-do list for whoever wrote them. */
async function tryOllie(event) {
  event.preventDefault();
  if (!state.subjectId) return;
  const question = $("try-question").value.trim();
  if (!question) return;
  const out = $("try-answer");
  out.innerHTML = `<p class="tagline">Looking through the notes…</p>`;
  try {
    const result = await api(`/subjects/${state.subjectId}/explain`, {
      method: "POST", body: JSON.stringify({ question }),
    });
    const cites = (result.excerpts || []).map((e) =>
      `<li>${escapeHtml(typeof e === "string" ? e : (e.title || e.body || ""))}</li>`).join("");
    out.innerHTML = `
      <div class="try-verdict ${result.covered ? "is-covered" : "is-gap"}">
        ${result.covered ? "covered by your notes" : "not in your notes"}
      </div>
      <p class="try-text">${escapeHtml(result.answer)}</p>
      ${cites ? `<p class="explain-from">drawn from:</p><ul class="explain-cites">${cites}</ul>` : ""}`;
  } catch (error) {
    out.innerHTML = `<p class="tagline">${escapeHtml(error.message)}</p>`;
  }
}

async function loadStudents() {
  // One call for the whole roster. The previous version fetched a full overview
  // per student, which was N requests to render a table of N rows.
  const data = await api(`/courses/${state.courseId}/analytics/insights`);
  state.classNorms = data.class_norms;

  // The distribution belongs beside the class tiles: it is the one number an
  // instructor wants before any individual name.
  const spread = $("tier-spread");
  if (spread) spread.innerHTML = MUSE_CHARTS.tiers(data.distribution,
    { empty: "No students yet." });

  $("students").innerHTML = data.roster.length
    ? data.roster.map((row) => `
        <tr data-student="${row.student_id}">
          <td>${escapeHtml(row.name)}</td>
          <td><span class="tier tier-${row.tier}">${escapeHtml(row.label)}</span></td>
          <td class="num">${MUSE_CHARTS.human(row.mean_total_seconds)}</td>
          <td class="num">${row.questions}</td>
          <td class="num">${row.hints}</td>
          <td class="num">${row.differences}</td>
        </tr>`).join("")
    : `<tr><td colspan="6" class="tagline">No students in this course yet.</td></tr>`;

  document.querySelectorAll("#students [data-student]").forEach((tr) => {
    tr.addEventListener("click", () => selectStudent(Number(tr.dataset.student)));
  });

  // Lead with whoever needs attention most, rather than with whoever is first
  // alphabetically — that is the reason an instructor opened this page.
  const rank = { at_risk: 0, needs_attention: 1, on_track: 2, not_started: 3 };
  const first = [...data.roster].sort((a, b) => rank[a.tier] - rank[b.tier])[0];
  if (first) selectStudent(first.student_id);
}

/* ------------------------------------------------------------- dashboard */
/* Every number on this panel is arithmetic over rows, and every flag carries
   the rows that produced it. A label on a student's name that an instructor
   cannot interrogate is worse than no label, because they act on it anyway. */

const TIER_TONE = { on_track: "ok", needs_attention: "warn", at_risk: "bad", not_started: "mute" };

function renderSignals(status) {
  const flagged = status.signals.filter((s) => s.points > 0);
  if (!flagged.length) {
    return `<p class="dash-none">No flags raised. Every signal is at or below the
            class norm.</p>`;
  }
  return flagged.map((s) => `
    <div class="signal sig-${s.severity}">
      <div class="signal-head">
        <span class="signal-label">${escapeHtml(s.label)}</span>
        <span class="signal-points">+${s.points}</span>
      </div>
      <div class="signal-nums">
        <b>${escapeHtml(s.value)}</b>
        ${s.compared ? `<span>${escapeHtml(s.compared)}</span>` : ""}
      </div>
      ${s.proof && s.proof.length ? `<ul class="signal-proof">${
        s.proof.map((x) => `<li>${escapeHtml(x)}</li>`).join("")
      }</ul>` : ""}
    </div>`).join("");
}

function renderAreas(areas) {
  const rows = Object.entries(areas)
    .map(([key, v]) => ({ label: AREA_LABELS[key] || key, score: v.score }))
    .sort((a, b) => b.score - a.score);
  return MUSE_CHARTS.diverging(rows, {
    empty: "Not enough work yet to judge.",
    leftLabel: "needs work", rightLabel: "solid",
  });
}

const AREA_LABELS = {
  specification: "Writing the specification",
  edge_cases: "Boundary and edge cases",
  types: "Return types and shapes",
  examples: "Concrete examples",
  debugging: "Debugging their own code",
  reflection: "Explaining their own work",
  independence: "Working without hints",
};

function renderTimings(data) {
  if (!data.timings.length) return `<p class="dash-none">No assignments started.</p>`;
  return `<div class="attempt-grid">` + data.timings.map((t) => {
    const parts = [
      { label: "specification", value: t.specification_seconds || 0,
        display: MUSE_CHARTS.human(t.specification_seconds), tone: "a" },
      { label: "coding", value: t.coding_seconds || 0,
        display: MUSE_CHARTS.human(t.coding_seconds), tone: "b" },
    ];
    return `<div class="dash-attempt">
        <div class="dash-attempt-head">
          <b>${escapeHtml(t.entry_point || "assignment")}</b>
          <span>${t.solved ? "solved" : t.submitted ? "submitted, differences open"
                                                    : t.locked ? "never submitted" : "in progress"}</span>
        </div>
        ${MUSE_CHARTS.stack(parts, { empty: "No time recorded." })}
        ${MUSE_CHARTS.timeline(t.retry_gaps_seconds, {
            attempts: t.submissions, solved: t.solved,
            empty: "Never submitted." })}
      </div>`;
  }).join("") + `</div>`;
}

function renderPlan(plan) {
  if (!plan.length) return `<p class="dash-none">No weak areas stand out right now.</p>`;
  return plan.map((item) => `
    <div class="plan">
      <div class="plan-head">
        <span class="plan-area">${escapeHtml(item.area_label)}</span>
        <h4>${escapeHtml(item.title)}</h4>
      </div>
      <p class="plan-because">Because: ${escapeHtml(item.because)}</p>
      ${item.leaning_on ? `<p class="plan-via">${escapeHtml(item.leaning_on)}</p>` : ""}
      <ol class="plan-steps">${item.steps.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ol>
    </div>`).join("");
}

async function renderStudentDashboard(studentId) {
  const host = $("student-dashboard");
  host.innerHTML = `<p class="tagline">Loading…</p>`;
  let data;
  try {
    data = await api(`/students/${studentId}/insights?viewer=instructor`);
  } catch (err) {
    host.innerHTML = `<p class="tagline">Could not load this student's dashboard.</p>`;
    return;
  }
  const t = data.totals, status = data.status, norms = data.class_norms;
  const tone = TIER_TONE[status.tier] || "mute";

  const mix = [
    { label: "about their own code", value: t.questions_own_code, tone: "a" },
    { label: "about Python", value: t.questions - t.questions_own_code, tone: "b" },
  ];
  const diffs = [
    { label: "drift", value: data.difference_mix.drift || 0, tone: "warn" },
    { label: "bug", value: data.difference_mix.bug || 0, tone: "bad" },
    { label: "revision", value: data.difference_mix.revision || 0, tone: "a" },
  ];

  host.innerHTML = `
    <div class="dash-status tone-${tone}">
      <div class="dash-status-main">
        <span class="tier tier-${status.tier}">${escapeHtml(status.label)}</span>
        <span class="dash-who">${escapeHtml(data.student.name)}</span>
      </div>
      <span class="dash-score">${status.score} flag point${status.score === 1 ? "" : "s"}
        · needs attention above ${status.thresholds.needs_attention}, at risk from
        ${status.thresholds.at_risk}</span>
    </div>

    <div class="dash-grid">
      <section class="panel half">
        <h3 class="panel-h">Why, with the rows behind it</h3>
        <div class="signals">${renderSignals(status)}</div>
      </section>

      <section class="panel half">
        <h3 class="panel-h">At a glance</h3>
        <div class="meter-row">
          ${MUSE_CHARTS.meter("Questions about own code", t.questions_own_code, t.questions,
              { caption: `${t.questions_own_code} of ${t.questions}` })}
          ${MUSE_CHARTS.meter("Differences explained", t.differences_explained, t.differences,
              { caption: `${t.differences_explained} of ${t.differences}` })}
          ${MUSE_CHARTS.meter("Exercises solved", t.assignments_solved, t.assignments_started,
              { caption: `${t.assignments_solved} of ${t.assignments_started}` })}
        </div>
      </section>

      <section class="panel wide">
        <h3 class="panel-h">Where the time went
          <span class="panel-note">specification vs coding</span></h3>
        ${renderTimings(data)}
      </section>

      <section class="panel">
        <h3 class="panel-h">Against the class</h3>
        <div class="dash-compare">
          ${MUSE_CHARTS.compare("Time per exercise", t.mean_total_seconds,
              norms.total_seconds, { format: MUSE_CHARTS.human })}
          ${MUSE_CHARTS.compare("Hints taken", t.hints, Math.round(norms.hints * 10) / 10)}
          ${MUSE_CHARTS.compare("Differences found", t.differences, norms.differences)}
        </div>
      </section>

      <section class="panel">
        <h3 class="panel-h">Questions asked</h3>
        ${MUSE_CHARTS.stack(mix, { empty: "No questions asked yet." })}
      </section>

      <section class="panel">
        <h3 class="panel-h">Kinds of difference</h3>
        ${MUSE_CHARTS.stack(diffs, { empty: "No differences found yet." })}
      </section>

      <section class="panel wide">
        <h3 class="panel-h">Strengths and weaknesses
          <span class="panel-note">from gate re-asks and the kind of difference found</span></h3>
        ${renderAreas(data.areas)}
      </section>

      <section class="panel wide">
        <h3 class="panel-h">A plan, built from the weakest areas</h3>
        <div class="plan-grid">${renderPlan(data.plan)}</div>
      </section>
    </div>`;
}

async function selectStudent(studentId) {
  state.studentId = studentId;
  state.assignmentId = null;
  $("summarize").disabled = false;
  $("summary").textContent = "";

  document.querySelectorAll("#students [data-student]").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.student) === studentId);
  });

  renderStudentDashboard(studentId);

  const overview = await api(`/courses/${state.courseId}/students/${studentId}/overview`);
  state.overview = overview;
  $("transcript-who").textContent = overview.student.name;

  const assignmentSelect = $("assignment");
  const hasWork = overview.assignments.length > 0;
  $("assignment-label").style.display = hasWork ? "" : "none";
  assignmentSelect.classList.toggle("hidden", !hasWork);

  if (!hasWork) {
    assignmentSelect.innerHTML = "";
    $("transcript").innerHTML = `<div class="body tagline">No work yet.</div>`;
    $("spec-history").innerHTML = `<div class="body tagline">—</div>`;
    $("student-diffs").innerHTML = `<p class="tagline">—</p>`;
    return;
  }

  // This student's assignments, in the order they appear on their own
  // dropdown — same ordering the student sees on their own page.
  const sorted = [...overview.assignments].sort((a, b) => a.assignment_id - b.assignment_id);
  assignmentSelect.innerHTML = sorted.map((a) => `
    <option value="${a.assignment_id}">
      ${escapeHtml(truncate(a.assignment_prompt, 46))}${a.locked_specification_id ? "" : " (in progress)"}
    </option>`).join("");

  // Default to the assignment the student actually worked on, not blindly the first.
  const defaultAssignment =
    sorted.find((a) => a.diff_results.length || a.interactions.length) || sorted[0];
  assignmentSelect.value = String(defaultAssignment.assignment_id);
  renderAssignment(defaultAssignment.assignment_id);
}

function renderAssignment(assignmentId) {
  state.assignmentId = assignmentId;
  const assignment = (state.overview?.assignments || [])
    .find((a) => a.assignment_id === assignmentId);

  if (!assignment) {
    $("transcript").innerHTML = `<div class="body tagline">No work yet.</div>`;
    $("spec-history").innerHTML = `<div class="body tagline">—</div>`;
    $("student-diffs").innerHTML = `<p class="tagline">—</p>`;
    return;
  }

  $("transcript").innerHTML = assignment.interactions.length
    ? assignment.interactions.map((row) => `
        <div class="turn">
          <div class="q">${escapeHtml(row.student_message)}</div>
          <div class="a">${escapeHtml(row.ai_response)}</div>
          <div class="m">
            <span class="tag ${row.response_mode === "hint" ? "hint" : row.classified_type}">
              ${row.response_mode === "hint" ? "direct hint" : row.classified_type}</span>
            ${row.confidence != null ? `confidence ${row.confidence}` : ""}
            ${row.reasoning ? `· ${escapeHtml(row.reasoning)}` : ""}
            ${row.thread_round ? `· round ${row.thread_round}` : ""}
          </div>
        </div>`).join("")
    : `<div class="body tagline">No tutor questions.</div>`;

  $("spec-history").innerHTML = assignment.specification_versions.map((version) => `
    <div class="turn" style="padding:10px 14px;border-bottom:1px solid var(--line)">
      <div class="m" style="margin:0 0 6px">
        version ${version.version} · ${version.status}
        ${version.id === assignment.locked_specification_id ? '· <span class="tag locked">locked</span>' : ""}
      </div>
      <pre class="json" style="padding:0">${escapeHtml(JSON.stringify(version.content, null, 1))}</pre>
    </div>`).join("");

  $("student-diffs").innerHTML = assignment.diff_results.length
    ? assignment.diff_results.map((diff) => `
        <div class="diff">
          <h3><span class="tag ${diff.category}">${diff.category}</span> ${escapeHtml(diff.description)}</h3>
          <div class="detail">${escapeHtml(diff.test_detail?.failure || "")}</div>
          ${diff.evidence ? `<div class="evidence">evidence: ${escapeHtml(diff.evidence)}</div>` : ""}
          <div class="a" style="margin-top:8px">
            <strong class="tagline">student's explanation:</strong><br>
            ${diff.student_explanation ? escapeHtml(diff.student_explanation)
                                       : '<span class="tagline">not written yet</span>'}
          </div>
        </div>`).join("")
    : `<p class="tagline">No discrepancies recorded.</p>`;
}

async function summarize() {
  $("summarize").disabled = true;
  $("summary").textContent = "generating…";
  try {
    const query = state.assignmentId ? `?assignment_id=${state.assignmentId}` : "";
    const result = await api(`/students/${state.studentId}/narrative-summary${query}`);
    $("summary").textContent = result.summary;
  } catch (error) {
    $("summary").textContent = error.message;
  } finally {
    $("summarize").disabled = false;
  }
}

async function saveVisibility() {
  try {
    const result = await api(`/courses/${state.courseId}/settings/dependency-profile-visibility`, {
      method: "PUT", body: JSON.stringify({ visibility: $("visibility").value }),
    });
    $("visibility-status").textContent = `saved — ${result.visibility}`;
  } catch (error) {
    $("visibility-status").textContent = error.message;
  }
}

/* ---------------------------------------------------------- add exercise
   A checklist row per specification requirement (the Gate walks these one
   at a time). No "field" is exposed here — spec_gate infers goal / inputs /
   outputs / examples from the wording itself, so the instructor just writes
   plain-language criteria the way the seeded assignments read. */
function addRubricRow(value = "") {
  const row = document.createElement("div");
  row.className = "rubric-row";
  row.innerHTML = `
    <input type="text" class="rubric-item" placeholder="e.g. must state the behavior when n = 0">
    <button type="button" class="row-remove" aria-label="Remove this item">&times;</button>`;
  row.querySelector(".rubric-item").value = value;
  row.querySelector(".row-remove").addEventListener("click", () => row.remove());
  $("rubric-rows").appendChild(row);
  return row;
}

async function loadAssignments() {
  const assignments = await api(`/assignments?course_id=${state.courseId}`);
  $("assignment-list").innerHTML = assignments.length
    ? assignments.map((a) => `
        <div class="assignment-row">
          <div class="a-prompt">${escapeHtml(truncate(a.prompt_text, 100))}</div>
          <div class="a-meta tagline">
            <code>${escapeHtml(a.entry_point || "—")}</code>
            · ${a.rubric_items.length} checklist item${a.rubric_items.length === 1 ? "" : "s"}
          </div>
        </div>`).join("")
    : `<p class="tagline">No exercises yet for this course.</p>`;
}

async function createAssignment(event) {
  event.preventDefault();
  const status = $("assignment-form-status");
  const button = $("create-assignment");

  const prompt = $("new-prompt").value.trim();
  const entryPoint = $("new-entry-point").value.trim();
  const starterCode = $("new-starter-code").value;
  const itemTexts = Array.from(document.querySelectorAll("#rubric-rows .rubric-item"))
    .map((el) => el.value.trim())
    .filter(Boolean);

  if (!prompt) { status.textContent = "Write the exercise prompt first."; return; }
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(entryPoint)) {
    status.textContent = "Function name must be a valid identifier, e.g. \"fib\".";
    return;
  }
  if (!itemTexts.length) { status.textContent = "Add at least one checklist item."; return; }

  const items = itemTexts.map((text) => ({ text }));

  button.disabled = true;
  status.textContent = "creating…";
  try {
    await api("/assignments", {
      method: "POST",
      body: JSON.stringify({
        course_id: state.courseId,
        prompt_text: prompt,
        entry_point: entryPoint,
        starter_code: starterCode || null,
        items,
      }),
    });
    status.textContent = "Created — it appears on the student side as a new assignment.";
    $("assignment-form").reset();
    $("rubric-rows").innerHTML = "";
    addRubricRow();
    loadAssignments();
    // Let the instructor see the confirmation for a beat before the popup closes.
    setTimeout(closeAssignmentDialog, 900);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/* -------------------------------------------------------- exercise popup
   A plain overlay toggled with a CSS class, instead of an inline accordion
   panel or the native <dialog> element — the form is long enough that
   expanding it in place pushed everything below it down the page. */
function openAssignmentDialog() {
  $("assignment-form-status").textContent = "";
  // Always reopen on the form, not mid-way through a previous "show all" view.
  $("assignment-list-section").classList.add("hidden");
  $("toggle-assignment-list").textContent = "Show all questions";
  $("assignment-dialog").classList.add("open");
}

function closeAssignmentDialog() {
  $("assignment-dialog").classList.remove("open");
}

function toggleAssignmentList() {
  const section = $("assignment-list-section");
  const nowHidden = section.classList.toggle("hidden");
  $("toggle-assignment-list").textContent = nowHidden ? "Show all questions" : "Hide questions";
}

$("open-assignment-dialog").addEventListener("click", openAssignmentDialog);
$("close-assignment-dialog").addEventListener("click", closeAssignmentDialog);
$("toggle-assignment-list").addEventListener("click", toggleAssignmentList);
// Click on the backdrop (outside the popup's own box) closes it too.
$("assignment-dialog").addEventListener("click", (e) => {
  if (e.target === $("assignment-dialog")) closeAssignmentDialog();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("assignment-dialog").classList.contains("open")) closeAssignmentDialog();
});

/* ------------------------------------------------------------ add course
   Backed by the `courses` table (app/models.py Course) via GET/POST
   /api/courses — so a course created here survives a reload and shows up
   for anyone else opening this dashboard, not just this browser tab. */
async function loadCourses() {
  const courses = await api("/courses");
  const select = $("course");
  const current = state.courseId;
  select.innerHTML = courses
    .map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`)
    .join("");
  // Keep the course already in view selected if it's still in the list,
  // otherwise fall back to the first one the API returned.
  if (courses.includes(current)) {
    select.value = current;
  } else if (courses.length) {
    select.value = courses[0];
    state.courseId = courses[0];
  }
  renderCourseList(courses);
}

function renderCourseList(courses) {
  $("course-list").innerHTML = courses.length
    ? courses.map((id) => `
        <div class="assignment-row">
          <div class="a-prompt">${escapeHtml(id)}</div>
        </div>`).join("")
    : `<p class="tagline">No courses yet.</p>`;
}

function toggleCourseList() {
  const section = $("course-list-section");
  const nowHidden = section.classList.toggle("hidden");
  $("toggle-course-list").textContent = nowHidden ? "Show all courses" : "Hide courses";
}

function openCourseDialog() {
  $("course-form-status").textContent = "";
  $("new-course-id").value = "";
  // Always reopen on the form, not mid-way through a previous "show all" view.
  $("course-list-section").classList.add("hidden");
  $("toggle-course-list").textContent = "Show all courses";
  $("course-dialog").classList.add("open");
  $("new-course-id").focus();
}

function closeCourseDialog() {
  $("course-dialog").classList.remove("open");
}

async function createCourse(event) {
  event.preventDefault();
  const status = $("course-form-status");
  const button = $("create-course");
  const raw = $("new-course-id").value.trim();

  if (!raw) { status.textContent = "Enter a course ID first."; return; }
  if (!/^[A-Za-z0-9_-]+$/.test(raw)) {
    status.textContent = "Use letters, numbers, \"-\" or \"_\" only, e.g. \"CS201\".";
    return;
  }

  button.disabled = true;
  status.textContent = "saving…";
  try {
    const result = await api("/courses", {
      method: "POST", body: JSON.stringify({ course_id: raw }),
    });
    status.textContent = result.created
      ? `Added — now viewing ${result.course_id}.`
      : `"${result.course_id}" already exists — now viewing it.`;
    state.courseId = result.course_id;
    await boot();
    $("course").value = state.courseId;
    setTimeout(closeCourseDialog, 900);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

$("open-course-dialog").addEventListener("click", openCourseDialog);
$("close-course-dialog").addEventListener("click", closeCourseDialog);
$("toggle-course-list").addEventListener("click", toggleCourseList);
$("course-dialog").addEventListener("click", (e) => {
  if (e.target === $("course-dialog")) closeCourseDialog();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("course-dialog").classList.contains("open")) closeCourseDialog();
});
$("course-form").addEventListener("submit", createCourse);

$("summarize").addEventListener("click", summarize);
$("save-visibility").addEventListener("click", saveVisibility);
$("course").addEventListener("change", (e) => { state.courseId = e.target.value; boot(); });
$("assignment").addEventListener("change", (e) => {
  renderAssignment(Number(e.target.value));
  $("summary").textContent = "";
});
$("assignment-form").addEventListener("submit", createAssignment);
$("add-rubric-row").addEventListener("click", () => addRubricRow());

// One empty checklist row to start with, so the form isn't blank.
addRubricRow();

async function boot() {
  try {
    const health = await api("/health");
    if (health.llm !== "live") {
      banner(
        "Running on the offline stub — no API keys were loaded, so everything " +
        "below is canned text. Stop uvicorn, check .env, restart it " +
        "(--reload does not pick up .env changes).", "warn");
    } else {
      banner("");
    }
    await loadCourses();
    await Promise.all([loadClass(), loadStudents(), loadAssignments(), loadSubjects()]);
  } catch (error) {
    banner(error.message, "bad");
  }
}

// This page is a snapshot, not a live feed: reload after a student submits.
$("refresh").addEventListener("click", boot);
boot();

$("subject-form").addEventListener("submit", createSubject);
$("material-form").addEventListener("submit", addMaterial);

$("try-form").addEventListener("submit", tryOllie);
