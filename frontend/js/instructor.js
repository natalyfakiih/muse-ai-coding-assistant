/* MUSE instructor client. Read-only over data that already exists; the only
   AI call on this page is the narrative summary, behind a button. */

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

const state = { courseId: "CS101", studentId: null };

function banner(text, kind = "") {
  const element = $("banner");
  if (!text) { element.classList.add("hidden"); return; }
  element.className = `banner ${kind}`;
  element.textContent = text;
}

const stat = (n, label) => `<div class="stat"><div class="n">${n}</div><div class="l">${label}</div></div>`;

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
    <tr data-student="${row.id}" style="cursor:pointer">
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
  $("summarize").disabled = false;
  $("summary").textContent = "";

  const overview = await api(`/courses/${state.courseId}/students/${studentId}/overview`);
  $("transcript-who").textContent = overview.student.name;
  // Show the assignment the student actually worked on, not blindly the first.
  const assignment =
    overview.assignments.find((a) => a.diff_results.length || a.interactions.length) ||
    overview.assignments[0];

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
    const result = await api(`/students/${state.studentId}/narrative-summary`);
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

$("summarize").addEventListener("click", summarize);
$("save-visibility").addEventListener("click", saveVisibility);
$("course").addEventListener("change", (e) => { state.courseId = e.target.value; boot(); });

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
    await Promise.all([loadClass(), loadProviderLog(), loadStudents()]);
  } catch (error) {
    banner(error.message, "bad");
  }
}

// This page is a snapshot, not a live feed: reload after a student submits.
$("refresh").addEventListener("click", boot);
boot();
