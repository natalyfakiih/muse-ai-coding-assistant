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

const state = { courseId: "CS101", studentId: null, assignmentId: null, overview: null };

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

async function loadProviderLog() {
  const ops = await api("/ops/llm-calls?limit=12");
  $("provider-stats").innerHTML = [
    stat(ops.total_calls, "calls"),
    stat(`${Math.round(ops.fallback_rate * 100)}%`, "fell back"),
    ...Object.entries(ops.mean_latency_ms_by_provider).map(([p, ms]) => stat(`${ms}`, `${p} mean ms`)),
  ].join("");

  $("provider-log").innerHTML = ops.recent.length
    ? ops.recent.map((row) => `
        <tr>
          <td>${escapeHtml(row.task)}</td>
          <td>${escapeHtml(row.provider)}${row.fell_back ? ' <span class="tag">fallback</span>' : ""}</td>
          <td class="num">${row.latency_ms}</td>
          <td>${row.success ? '<span class="tag factual">ok</span>'
                            : `<span class="tag bug">${escapeHtml(row.error_type || "failed")}</span>`}</td>
        </tr>`).join("")
    : `<tr><td colspan="4" class="tagline">No calls logged yet.</td></tr>`;
}

/* ----------------------------------------------------------- student view */
async function loadStudents() {
  const students = await api(`/students?course_id=${state.courseId}`);
  const rows = await Promise.all(students.map(async (student) => {
    const overview = await api(`/courses/${state.courseId}/students/${student.id}/overview`);
    // Across every assignment, not just the first — a student who only worked on
    // assignment 2 was invisible here before.
    const sum = (pick) => overview.assignments.reduce((n, a) => n + pick(a).length, 0);
    return {
      ...student,
      versions: sum((a) => a.specification_versions),
      questions: sum((a) => a.interactions),
      diffs: sum((a) => a.diff_results),
    };
  }));

  $("students").innerHTML = rows.map((row) => `
    <tr data-student="${row.id}">
      <td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.cohort || "")}</td>
      <td class="num">${row.versions}</td><td class="num">${row.questions}</td>
      <td class="num">${row.diffs}</td>
    </tr>`).join("");

  document.querySelectorAll("[data-student]").forEach((tr) => {
    tr.addEventListener("click", () => selectStudent(Number(tr.dataset.student)));
  });

  const withWork = rows.find((r) => r.questions || r.diffs) || rows[0];
  if (withWork) selectStudent(withWork.id);
}

async function selectStudent(studentId) {
  state.studentId = studentId;
  state.assignmentId = null;
  $("summarize").disabled = false;
  $("summary").textContent = "";

  document.querySelectorAll("#students [data-student]").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.student) === studentId);
  });

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
    loadProviderLog();
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
    await Promise.all([loadClass(), loadProviderLog(), loadStudents(), loadAssignments()]);
  } catch (error) {
    banner(error.message, "bad");
  }
}

// This page is a snapshot, not a live feed: reload after a student submits.
$("refresh").addEventListener("click", boot);
boot();
