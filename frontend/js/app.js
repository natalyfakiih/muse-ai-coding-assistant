/* MUSE student client.
   Three phases, one page. The editor never offers a completion — that is
   enforced in the Monaco options below, not just by policy. */

const $ = (id) => document.getElementById(id);
const api = async (path, options = {}) => {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(body.detail || response.statusText), { body, status: response.status });
  return body;
};

const remember = (key, value) => {
  try { localStorage.setItem(`muse.${key}`, String(value)); } catch { /* private mode */ }
};
const recall = (key) => {
  try { return localStorage.getItem(`muse.${key}`); } catch { return null; }
};

const state = {
  studentId: null,
  assignmentId: null,
  assignment: null,
  specId: null,
  locked: false,
  rubric: [],
  askCounts: {},
  currentGap: null,
  escapeAvailable: false,
  submission: null,
  finalized: false,
  phase: 1,
  maxPhase: 1,
  gateTurns: 0,
  lastItemsMet: 0,
  stats: null,
  versionHistory: [],
  phase2Loaded: false,
  editor: null,
  usingFallback: false,
  saveTimer: null,
};

/* --------------------------------------------------------------- memes */
const MEME_SECONDS = 3.5;
const memes = {
  fail: [], success: [],
  on: recall("memes") !== "off",   // on unless the student turned it off
  lastShown: { fail: null, success: null },
  timer: null,
};

const FAIL_LINES = ["Not quite.", "Nope.", "That's a no.", "Try again.", "Close, but no."];
const WIN_LINES = ["Nice.", "Got it.", "That's the one.", "Correct.", "Clean."];

async function loadMemes() {
  try {
    const found = await api("/memes");
    memes.fail = found.fail || [];
    memes.success = found.success || [];
  } catch { /* no pics folder — the UI simply never shows one */ }
  renderMemeToggle();
}

function renderMemeToggle() {
  const button = $("meme-toggle");
  if (!button) return;
  const available = memes.fail.length + memes.success.length > 0;
  button.classList.toggle("hidden", !available);
  button.setAttribute("aria-pressed", String(memes.on));
  button.querySelector(".meme-toggle-label").textContent =
    memes.on ? "Memes on" : "Memes off";
  button.title = memes.on ? "Click to turn reaction memes off"
                          : "Click to turn reaction memes on";
}

/* Random, but never the same one twice in a row — repeats kill the joke. */
function pickMeme(kind) {
  const pool = memes[kind] || [];
  if (!pool.length) return null;
  if (pool.length === 1) return pool[0];
  let choice;
  do { choice = pool[Math.floor(Math.random() * pool.length)]; }
  while (choice === memes.lastShown[kind]);
  memes.lastShown[kind] = choice;
  return choice;
}

function showMeme(kind, caption) {
  if (!memes.on) return;
  const source = pickMeme(kind);
  if (!source) return;

  const overlay = $("meme-overlay");
  const lines = kind === "fail" ? FAIL_LINES : WIN_LINES;
  $("meme-image").src = source;
  $("meme-image").alt = kind === "fail" ? "fail reaction" : "success reaction";
  $("meme-caption").textContent = caption || lines[Math.floor(Math.random() * lines.length)];
  overlay.className = `meme-overlay ${kind}`;

  clearTimeout(memes.timer);
  memes.timer = setTimeout(hideMeme, MEME_SECONDS * 1000);
}

function hideMeme() {
  const overlay = $("meme-overlay");
  if (overlay.classList.contains("hidden")) return;
  clearTimeout(memes.timer);
  overlay.classList.add("leaving");
  setTimeout(() => { overlay.className = "meme-overlay hidden"; }, 300);
}

function toggleMemes() {
  memes.on = !memes.on;
  remember("memes", memes.on ? "on" : "off");
  if (!memes.on) hideMeme();
  renderMemeToggle();
}

/* ---------------------------------------------------------------- utils */
function banner(text, kind = "") {
  const element = $("banner");
  if (!text) { element.classList.add("hidden"); return; }
  element.className = `banner ${kind}`;
  element.textContent = text;
}

/* Phase navigation.
   A student can always walk back to a phase they've reached — and forward again
   without redoing anything. Going back to the specification is READ-ONLY once
   it's locked: the Reflection Engine measures the final code against exactly
   that version, so editing it after coding started would quietly destroy the
   drift signal the whole system exists to produce. */
function reachable(number) {
  if (number === 1) return true;
  if (number === 2) return state.locked;
  return Boolean(state.submission);
}

function whyBlocked(number) {
  if (number === 2) return "The editor unlocks once your specification is complete.";
  if (number === 3) return "Submit your code from the editor to run reflection.";
  return "";
}

function phase(number) {
  if (!reachable(number)) {
    const hint = $("phase-hint");
    hint.textContent = whyBlocked(number);
    hint.classList.add("show");
    setTimeout(() => hint.classList.remove("show"), 3200);
    return;
  }

  state.phase = number;
  state.maxPhase = Math.max(state.maxPhase || 1, number);
  [1, 2, 3].forEach((n) => $(`phase-${n}`).classList.toggle("hidden", n !== number));

  document.querySelectorAll(".phase").forEach((element) => {
    const n = Number(element.dataset.phase);
    element.classList.toggle("active", n === number);
    // "done" marks a phase you've completed, not merely one with a lower
    // number — otherwise walking back makes finished phases look unfinished.
    element.classList.toggle("done", n !== number && phaseComplete(n));
    // aria-disabled, not `disabled`: a disabled button swallows the click, and
    // then a student who taps a locked phase gets no explanation at all.
    const open = reachable(n);
    element.setAttribute("aria-disabled", String(!open));
    element.title = open ? "" : whyBlocked(n);
  });

  if (number === 1) renderPhase1Mode();
  if (number === 3) {
    const has = Boolean(state.submission);
    $("no-submission").classList.toggle("hidden", has);
    $("submission-view").classList.toggle("hidden", !has);
  }
}

function phaseComplete(n) {
  if (n === 1) return state.locked;
  if (n === 2) return Boolean(state.submission);
  return Boolean(state.finalized);
}

/* Phase 1 revisited after locking: readable, not editable. */
function renderPhase1Mode() {
  const chat = document.querySelector("#phase-1 .chat");
  chat.classList.toggle("readonly", state.locked);
  $("gate-locked-notice").classList.toggle("hidden", !state.locked);
  $("gate-input").disabled = state.locked;
  $("gate-send").disabled = state.locked;
  $("gate-dont-know").disabled = state.locked;
  if (state.locked) {
    $("gate-lock").classList.add("hidden");
    renderOptions(null);
    if (!state.gateTurns) renderVersionTimeline();
  }
}

/* After a reload the in-page conversation is gone, but the versions aren't —
   show how the specification actually evolved instead of an empty panel. */
function renderVersionTimeline() {
  const history = state.versionHistory || [];
  if (!history.length) return;
  const box = $("gate-messages");
  box.innerHTML = `<div class="msg system">Your specification, version by version.</div>`;
  const wrap = document.createElement("div");
  wrap.className = "timeline";
  wrap.innerHTML = history.map((version) => {
    const when = version.created_at
      ? new Date(version.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "";
    return `<div class="step ${version.status === "locked" ? "locked" : ""}">
      <div class="when">version ${version.version}${when ? ` · ${when}` : ""}${
        version.status === "locked" ? " · locked" : ""}</div>
      <div class="what">${escapeHtml(summariseVersion(version.content)) ||
        "<em>nothing recorded yet</em>"}</div>
    </div>`;
  }).join("");
  box.appendChild(wrap);
  box.scrollTop = 0;
}

function summariseVersion(content) {
  if (!content) return "";
  const bits = [];
  if (content.goal) bits.push(`goal: ${content.goal}`);
  if (content.inputs?.length) bits.push(`${content.inputs.length} input(s)`);
  if (content.outputs?.length) bits.push(`${content.outputs.length} output(s)`);
  if (content.edge_cases?.length) bits.push(`${content.edge_cases.length} edge case(s)`);
  if (content.examples?.length) bits.push(`${content.examples.length} example(s)`);
  return bits.join(" · ");
}

function bubble(container, role, text, meta) {
  const element = document.createElement("div");
  element.className = `msg ${role}`;
  element.textContent = text;
  if (meta) {
    const span = document.createElement("span");
    span.className = "meta";
    span.innerHTML = meta;
    element.appendChild(span);
  }
  container.appendChild(element);
  container.scrollTop = container.scrollHeight;
  return element;
}

function thinking(container) {
  const element = document.createElement("div");
  element.className = "msg muse thinking";
  element.textContent = "thinking";
  container.appendChild(element);
  container.scrollTop = container.scrollHeight;
  return element;
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------ spec sheet */
function renderSpec(content, target) {
  const labels = {
    goal: "Goal", inputs: "Inputs", outputs: "Outputs",
    edge_cases: "Edge cases", examples: "Examples",
    explicit_exclusions: "Explicitly out of scope",
  };
  target.innerHTML = Object.entries(labels).map(([key, label]) => {
    const value = content?.[key];
    let html;
    if (!value || (Array.isArray(value) && value.length === 0) || value === "") {
      html = `<div class="v empty">not stated yet</div>`;
    } else if (key === "inputs") {
      html = `<ul>${value.map((i) =>
        `<li><code>${escapeHtml(i.name)}</code>: ${escapeHtml(i.type)}${
          i.constraints ? ` — ${escapeHtml(i.constraints)}` : ""}</li>`).join("")}</ul>`;
    } else if (key === "outputs") {
      html = `<ul>${value.map((o) =>
        `<li><code>${escapeHtml(o.name)}</code>: ${escapeHtml(o.type)}</li>`).join("")}</ul>`;
    } else if (key === "examples") {
      html = `<ul>${value.map((e) =>
        `<li><code>${escapeHtml(e.input)}</code> → <code>${escapeHtml(e.output)}</code></li>`).join("")}</ul>`;
    } else if (Array.isArray(value)) {
      html = `<ul>${value.map((v) => `<li>${escapeHtml(v)}</li>`).join("")}</ul>`;
    } else {
      html = `<div class="v">${escapeHtml(value)}</div>`;
    }
    return `<div class="spec-field"><div class="k">${label}</div>${html}</div>`;
  }).join("");
}

function renderRubric(status) {
  // Prefer the server's per-item status (it knows which fields are filled);
  // fall back to the bare rubric before the first exchange.
  const items = status && status.length
    ? status
    : state.rubric.map((item) => ({ ...item, met: false, current: false, asks: 0 }));

  $("rubric").innerHTML = items.map((item) => {
    const current = item.current ||
      (state.currentGap && state.currentGap.id === item.id);
    const classes = [item.met ? "met" : "", item.asks ? "asked" : "",
                     current && !item.met ? "current" : ""].join(" ");
    // Tick when answered, otherwise the number of times it's been asked —
    // "?" only for a question raised for the first time right now.
    const mark = item.met ? "✓" : item.asks ? String(item.asks) : current ? "?" : "";
    const bits = [];
    if (item.asks) bits.push(`asked ${item.asks}×`);
    if (item.dont_knows) bits.push(`${item.dont_knows} × "I don't know"`);
    const tip = bits.join(", ");
    const notes = [];
    if (item.confirmed) notes.push("your call, on record");
    if (item.dont_knows) notes.push(`${item.dont_knows} × "I don't know"`);
    const note = notes.length ? `<span class="note">${escapeHtml(notes.join(" · "))}</span>` : "";
    return `<li class="${classes}"${tip ? ` title="${escapeHtml(tip)}"` : ""}>
      <span class="box">${mark}</span>
      <span class="label">${escapeHtml(item.text)}${note}</span></li>`;
  }).join("");

  const met = items.filter((i) => i.met).length;
  $("rubric-count").textContent = `${met} of ${items.length} answered`;
  $("progress-fill").style.width = items.length ? `${(met / items.length) * 100}%` : "0";
}

function renderOptions(options) {
  const box = $("gate-options");
  if (!options || !options.length) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  box.classList.remove("hidden");
  const template = options.some((o) => String(o.answer || "").includes("..."));
  const label = template
    ? "a shape to fill in — replace the dots with real values"
    : "either of these is a valid choice — pick one, or type your own";
  box.innerHTML = `<span class="options-label">${label}</span>` +
    options.map((o, i) =>
      `<button data-option="${i}" title="${escapeHtml(o.answer || o.label || o)}">${
        escapeHtml(o.label || o)}</button>`).join("");
  box.querySelectorAll("[data-option]").forEach((button) => {
    button.addEventListener("click", () => {
      const option = options[Number(button.dataset.option)];
      // Put the full sentence in the box, not the short label: the label alone
      // ("an empty list") loses the condition it answers.
      const text = (option && (option.answer || option.label)) || String(option);
      const input = $("gate-input");
      input.value = text;
      renderOptions(null);
      input.focus();
      // A template is a blank to fill: drop the caret on the first gap instead
      // of leaving them to hunt for it.
      const blank = text.indexOf("...");
      if (blank !== -1) input.setSelectionRange(blank, blank + 3);
    });
  });
}

/* --------------------------------------------------------------- phase 1 */
async function loadSpecification() {
  const data = await api(`/assignments/${state.assignmentId}/specification?student_id=${state.studentId}`);
  state.assignment = data.assignment;
  state.rubric = data.rubric || [];
  state.specId = data.specification_id;
  state.locked = data.locked;
  state.askCounts = data.gap_ask_counts || {};
  state.versionHistory = data.version_history || [];
  state.lastItemsMet = data.rubric_status
    ? data.rubric_status.filter((i) => i.met).length : 0;
  state.gateTurns = 0;
  state.phase2Loaded = false;
  state.submission = null;
  state.finalized = false;
  state.maxPhase = 1;

  $("assignment-prompt").textContent = data.assignment.prompt_text;
  $("spec-version").textContent = data.version ? `version ${data.version}` : "";
  $("gate-status").innerHTML = data.locked
    ? '<span class="tag locked">locked</span>' : "draft";
  renderSpec(data.content, $("spec-sheet"));
  renderRubric(data.rubric_status);
  renderOptions(null);

  $("gate-messages").innerHTML = "";
  bubble($("gate-messages"), "system",
    "Before the editor unlocks, say what you're going to build. MUSE asks one " +
    "question at a time until every rubric item is answered.");

  if (data.locked) {
    renderSpec(data.content, $("spec-sheet-locked"));
    await enterPhase2();
    await restoreSubmission();
  } else {
    phase(1);
  }
}

async function sendGateMessage() {
  const input = $("gate-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  bubble($("gate-messages"), "student", message);
  const pending = thinking($("gate-messages"));
  $("gate-send").disabled = true;

  try {
    const result = await api(`/assignments/${state.assignmentId}/specification`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, message }),
    });
    pending.remove();
    state.specId = result.specification_id;
    state.locked = result.locked;
    state.currentGap = result.gap || null;
    if (result.gap) state.askCounts[result.gap.id] = result.asks_on_gap;

    renderSpec(result.content, $("spec-sheet"));
    $("spec-version").textContent = `version ${result.version}`;
    renderRubric(result.rubric_status);
    renderOptions(null);

    // Wrong or incomplete -> fail meme; another rubric item answered -> success.
    if (result.shortfall) {
      showMeme("fail");
    } else if (result.locked) {
      showMeme("success", "Specification locked.");
    } else if ((result.items_met || 0) > (state.lastItemsMet || 0)) {
      showMeme("success");
    }
    state.lastItemsMet = result.items_met || 0;

    if (result.shortfall) {
      bubble($("gate-messages"), "muse", result.shortfall).classList.add("shortfall");
    }
    if (result.message && result.question) bubble($("gate-messages"), "system", result.message);
    state.gateTurns += 1;
    bubble($("gate-messages"), "muse", result.question || result.message || "…",
      result.gap ? `on rubric item: ${escapeHtml(result.gap.text)}` : null);

    $("gate-lock").classList.toggle("hidden", !result.can_force_lock);
    if (result.can_force_lock) {
      $("gate-hint").textContent =
        "Asked twice about this one. Answer again, or confirm your answer stands.";
    }

    if (result.locked) {
      $("gate-status").innerHTML = '<span class="tag locked">locked</span>';
      renderSpec(result.content, $("spec-sheet-locked"));
      setTimeout(enterPhase2, 600);
    }
  } catch (error) {
    pending.remove();
    bubble($("gate-messages"), "system", `⚠ ${error.message}`);
  } finally {
    $("gate-send").disabled = false;
    input.focus();
  }
}

async function forceLock() {
  try {
    const result = await api(`/assignments/${state.assignmentId}/specification/lock`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, gap_id: state.currentGap?.id || null }),
    });
    bubble($("gate-messages"), "system", result.message);
    if ((result.message || "").includes("can't be waived")) showMeme("fail");
    state.currentGap = result.gap || null;
    if (result.gap) state.askCounts[result.gap.id] = result.asks_on_gap;
    renderSpec(result.content, $("spec-sheet"));
    renderRubric(result.rubric_status);

    if (result.locked) {
      state.locked = true;
      $("gate-status").innerHTML = '<span class="tag locked">locked</span>';
      renderSpec(result.content, $("spec-sheet-locked"));
      setTimeout(enterPhase2, 500);
      return;
    }
    // Confirming one gap settles that gap only — the gate moves to the next
    // unmet rubric item rather than unlocking.
    if (result.question) bubble($("gate-messages"), "muse", result.question,
      result.gap ? `on rubric item: ${escapeHtml(result.gap.text)}` : null);
    $("gate-lock").classList.toggle("hidden", !result.can_force_lock);
  } catch (error) {
    bubble($("gate-messages"), "system", `⚠ ${error.message}`);
  }
}

/* --------------------------------------------------------------- phase 2 */
async function enterPhase2() {
  state.locked = true;
  // Load the editor and transcript once. Re-running this on every visit would
  // overwrite whatever the student has typed since the last autosave.
  if (state.phase2Loaded) { phase(2); return; }
  phase(2);
  state.phase2Loaded = true;
  const snapshot = await api(`/specifications/${state.specId}/snapshots/latest`);
  const code = snapshot.content || state.assignment.starter_code || "";
  setEditorValue(code);

  const history = await api(`/specifications/${state.specId}/interactions`);
  const box = $("tutor-messages");
  box.innerHTML = "";
  if (!history.interactions.length) {
    bubble(box, "system",
      "Reference questions get a direct answer. Questions about your own code get " +
      "a counter-question — the point is that you find it.");
  }
  history.interactions.forEach((row) => {
    bubble(box, "student", row.student_message);
    bubble(box, "muse", row.ai_response, tutorMeta(row));
  });
  updateThreadState(history.open_thread_rounds, history.escape_hatch_available,
                    history.escape_hatch_threshold);
}

const tutorMeta = (row) =>
  `<span class="tag ${row.response_mode === "hint" ? "hint" : row.classified_type}">` +
  `${row.response_mode === "hint" ? "direct hint" : row.classified_type}</span>` +
  (row.reasoning ? ` ${escapeHtml(row.reasoning)}` : "");

function updateThreadState(rounds, available, threshold) {
  state.escapeAvailable = available;
  $("escape-hatch").disabled = !available;
  $("thread-state").textContent = rounds ? `round ${rounds} on this issue` : "";
  $("tutor-hint").textContent = available
    ? "Still stuck after several rounds — a direct hint is available, and it is logged."
    : `Questions about your own code get a counter-question, not a fix.` +
      (rounds ? ` (${rounds}/${threshold} rounds)` : "");
}

async function sendTutorMessage() {
  const input = $("tutor-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  bubble($("tutor-messages"), "student", message);
  const pending = thinking($("tutor-messages"));
  $("tutor-send").disabled = true;

  try {
    const result = await api(`/specifications/${state.specId}/interactions`, {
      method: "POST",
      body: JSON.stringify({ message, code: getEditorValue() }),
    });
    pending.remove();
    const meta = `<span class="tag ${result.classified_type}">${result.classified_type}</span>` +
      (result.classifier_overridden ? ' <span class="tag">re-classified from your code</span>' : "") +
      (result.targets ? ` probes: ${escapeHtml(result.targets)}` : "");
    bubble($("tutor-messages"), "muse", result.response, meta);
    updateThreadState(result.thread_round, result.escape_hatch_available, 3);
  } catch (error) {
    pending.remove();
    bubble($("tutor-messages"), "system", `⚠ ${error.message}`);
  } finally {
    $("tutor-send").disabled = false;
    input.focus();
  }
}

async function useEscapeHatch() {
  $("escape-hatch").disabled = true;
  const pending = thinking($("tutor-messages"));
  try {
    const result = await api(`/specifications/${state.specId}/interactions/escape-hatch`, {
      method: "POST", body: JSON.stringify({}),
    });
    pending.remove();
    bubble($("tutor-messages"), "muse", result.response,
      `<span class="tag hint">direct hint</span> logged as assist cost ${result.assist_cost}`);
    updateThreadState(0, false, 3);
  } catch (error) {
    pending.remove();
    bubble($("tutor-messages"), "system", `⚠ ${error.message}`);
  }
}

async function dontKnow() {
  const button = $("gate-dont-know");
  button.disabled = true;
  // It's a reply like any other, so it belongs in the transcript — otherwise
  // MUSE's explanation appears to answer a question nobody asked.
  bubble($("gate-messages"), "student", "I don't know");
  state.gateTurns += 1;
  const pending = thinking($("gate-messages"));
  try {
    const result = await api(`/assignments/${state.assignmentId}/specification/dont-know`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, gap_id: state.currentGap?.id || null }),
    });
    pending.remove();
    state.currentGap = result.gap || null;

    // Scaffolding, not an answer: it explains the question and, where this is a
    // real design decision, offers legitimate options. The student still picks.
    if (result.explanation) {
      const node = bubble($("gate-messages"), "muse", result.explanation);
      node.classList.add("explain");
    }
    if (result.question) bubble($("gate-messages"), "muse", result.question);
    renderOptions(result.options);
    renderRubric(result.rubric_status);
    $("gate-lock").classList.toggle("hidden", !result.can_force_lock);
    if (result.locked) { state.locked = true; setTimeout(enterPhase2, 600); }
  } catch (error) {
    pending.remove();
    bubble($("gate-messages"), "system", `⚠ ${error.message}`);
  } finally {
    button.disabled = false;
    $("gate-input").focus();
  }
}

/* ---------------------------------------------------------------- editor */
function setEditorValue(code) {
  if (state.editor) state.editor.setValue(code);
  else $("editor-fallback").value = code;
}
function getEditorValue() {
  return state.editor ? state.editor.getValue() : $("editor-fallback").value;
}

function scheduleSave() {
  $("save-state").textContent = "unsaved";
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    if (!state.specId) return;
    try {
      await api(`/specifications/${state.specId}/snapshots`, {
        method: "POST", body: JSON.stringify({ code: getEditorValue() }),
      });
      $("save-state").textContent = "saved";
    } catch {
      $("save-state").textContent = "save failed";
    }
  }, 1200);
}

function initEditor() {
  const useFallback = () => {
    state.usingFallback = true;
    $("editor").classList.add("hidden");
    $("editor-fallback").classList.remove("hidden");
    $("editor-fallback").addEventListener("input", scheduleSave);
  };

  if (typeof require === "undefined") { useFallback(); return; }

  const timeout = setTimeout(useFallback, 6000);
  try {
    require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs" } });
    require(["vs/editor/editor.main"], () => {
      clearTimeout(timeout);
      state.editor = monaco.editor.create($("editor"), {
        value: "", language: "python", theme: "vs-dark",
        fontSize: 13.5, minimap: { enabled: false }, automaticLayout: true,
        scrollBeyondLastLine: false, renderLineHighlight: "gutter",

        /* The "no AI autocomplete" rule from Section 3.2, enforced here.
           Monaco's own IntelliSense counts: the student types every character. */
        quickSuggestions: false,
        suggestOnTriggerCharacters: false,
        wordBasedSuggestions: "off",
        acceptSuggestionOnEnter: "off",
        tabCompletion: "off",
        parameterHints: { enabled: false },
        inlineSuggest: { enabled: false },
        suggest: { showWords: false, showSnippets: false },
        hover: { enabled: false },
      });
      state.editor.onDidChangeModelContent(scheduleSave);
    }, () => { clearTimeout(timeout); useFallback(); });
  } catch { clearTimeout(timeout); useFallback(); }
}

/* --------------------------------------------------------------- phase 3 */
/* A reload shouldn't cost you your reflection results — rebuild phase 3 from
   what the server already has, so all three phases stay reachable. */
async function restoreSubmission() {
  try {
    const status = await api(`/specifications/${state.specId}/submission-status`);
    if (!status.submitted && !status.diff_results.length) return;
    state.submission = {
      diff_results: status.diff_results,
      results: status.diff_results.map((d) => d.test_detail).filter(Boolean),
      cases_run: status.diff_results.length,
      cases_passed: 0,
      restored: true,
    };
    state.finalized = Boolean(status.finalized_at);
    renderSubmission(state.submission);
    loadProfile();
    loadSessionStats();
    document.querySelectorAll(".phase").forEach((tab) => {
      const n = Number(tab.dataset.phase);
      tab.setAttribute("aria-disabled", String(!reachable(n)));
    });
  } catch { /* nothing submitted yet */ }
}

async function submitForReflection() {
  $("submit-code").disabled = true;
  banner("Running your code against every case you declared…");
  try {
    const result = await api(`/specifications/${state.specId}/submit`, {
      method: "POST", body: JSON.stringify({ code: getEditorValue() }),
    });
    state.submission = result;
    // At least one case failed -> fail. Everything green -> success.
    if (result.diff_results.length) {
      showMeme("fail", `${result.cases_run - result.cases_passed} of ${result.cases_run} cases failed`);
    } else if (result.cases_run > 0) {
      showMeme("success", `All ${result.cases_run} cases passed`);
    }
    banner("");
    phase(3);
    renderSubmission(result);
    loadProfile();
    loadSessionStats();
  } catch (error) {
    banner(error.message, "bad");
  } finally {
    $("submit-code").disabled = false;
  }
}

function renderSubmission(result) {
  $("case-summary").textContent = result.restored
    ? "from your last submission"
    : `${result.cases_passed}/${result.cases_run} passed`;
  $("cases").innerHTML = result.results.map((c) => `
    <div class="case ${c.passed ? "pass" : "fail"}">
      <span class="mark">${c.passed ? "PASS" : "FAIL"}</span>
      <span>${escapeHtml(c.source)}<br>
        <code>${escapeHtml(c.failure || `returned ${c.actual}`)}</code></span>
    </div>`).join("") || `<div class="body tagline">No declared cases to run.</div>`;
  if (result.restored) {
    $("cases").insertAdjacentHTML("beforeend",
      `<div class="case"><span class="tagline">Showing the discrepancies from your
       last submission. Submit again from the editor to re-run every case.</span></div>`);
  }

  $("diff-count").textContent = `${result.diff_results.length}`;
  if (!result.diff_results.length) {
    $("diffs").innerHTML =
      `<p class="tagline">Your code matches everything you specified. Nothing to explain.</p>`;
    $("finalize").disabled = false;
    $("finalize-note").textContent = "No discrepancies — you can finalize.";
    return;
  }

  $("diffs").innerHTML = result.diff_results.map((d) => `
    <div class="diff" id="diff-${d.id}">
      <h3><span class="tag ${d.category}">${d.category}</span> ${escapeHtml(d.description)}</h3>
      <div class="detail">${escapeHtml(d.test_detail?.failure || "")}</div>
      ${d.evidence ? `<div class="evidence">from your interaction log: ${escapeHtml(d.evidence)}</div>` : ""}

      <!-- Work out WHY, before writing the explanation. Counter-questions only:
           the tutor probes here, it doesn't hand over the cause. -->
      <div class="diagnose" id="diagnose-${d.id}">
        <div class="diagnose-thread" id="thread-${d.id}">
          ${d.diagnostic_question
            ? `<div class="msg muse">${escapeHtml(d.diagnostic_question)}</div>` : ""}
        </div>
        <div class="diagnose-composer">
          <textarea rows="2" data-reply="${d.id}"
            placeholder="What did you find? Answer the question, or say what you tried…"></textarea>
          <button data-ask="${d.id}">Reply</button>
        </div>
      </div>

      <label class="explain-label">Your explanation — required before submitting</label>
      <textarea placeholder="Why is your code different from what you specified? Your own words."></textarea>
      <div class="row">
        <button data-diff="${d.id}" class="save-explanation">Save explanation</button>
        <span class="tagline" data-status="${d.id}"></span>
      </div>
    </div>`).join("");

  document.querySelectorAll(".save-explanation").forEach((button) => {
    button.addEventListener("click", () => saveExplanation(Number(button.dataset.diff)));
  });
  document.querySelectorAll("[data-ask]").forEach((button) => {
    button.addEventListener("click", () => askDiagnosis(Number(button.dataset.ask)));
  });
  document.querySelectorAll("[data-reply]").forEach((box) => {
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        askDiagnosis(Number(box.dataset.reply));
      }
    });
  });
  refreshStatus();
}

async function askDiagnosis(diffId) {
  const box = document.querySelector(`[data-reply="${diffId}"]`);
  const thread = $(`thread-${diffId}`);
  const message = box.value.trim();
  if (!message) return;
  box.value = "";
  bubble(thread, "student", message);
  const pending = thinking(thread);
  try {
    const result = await api(`/diff-results/${diffId}/discuss`, {
      method: "POST", body: JSON.stringify({ explanation: message }),
    });
    pending.remove();
    bubble(thread, "muse", result.question,
      result.targets ? `probes: ${escapeHtml(result.targets)}` : null);
  } catch (error) {
    pending.remove();
    bubble(thread, "system", `⚠ ${error.message}`);
  }
}

async function saveExplanation(diffId) {
  const card = $(`diff-${diffId}`);
  const text = card.querySelector("textarea").value;
  const status = document.querySelector(`[data-status="${diffId}"]`);
  try {
    await api(`/diff-results/${diffId}/explanation`, {
      method: "POST", body: JSON.stringify({ explanation: text }),
    });
    card.classList.add("explained");
    status.textContent = "saved";
    refreshStatus();
    loadSessionStats();
  } catch (error) {
    status.textContent = error.message;
    showMeme("fail");
  }
}

async function refreshStatus() {
  const status = await api(`/specifications/${state.specId}/submission-status`);
  $("finalize").disabled = !status.can_finalize;
  $("finalize-note").textContent = status.can_finalize
    ? "All discrepancies explained — you can finalize."
    : `${status.explanations_outstanding} explanation(s) still needed before this submission is accepted.`;
}

async function finalize() {
  try {
    const result = await api(`/specifications/${state.specId}/finalize`, { method: "POST" });
    banner(`Submission accepted at ${new Date(result.finalized_at).toLocaleString()}.`, "ok");
    $("finalize").disabled = true;
    state.finalized = true;
    showMeme("success", "Submission accepted.");
    phase(3);
  } catch (error) {
    banner(error.message, "bad");
  }
}

/* --------------------------------------------------------- session stats */
const fmtTime = (seconds) => {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m}m ${seconds % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
};

const statLine = (label, value, tone = "") =>
  `<div class="stat-line ${tone}"><span class="k">${escapeHtml(label)}</span>
   <span class="dots"></span><span class="v">${escapeHtml(String(value))}</span></div>`;

const statGroup = (title, lines) =>
  `<div class="stat-group"><h4>${escapeHtml(title)}</h4>
   <div class="stat-lines">${lines.join("")}</div></div>`;

async function loadSessionStats() {
  let data;
  try {
    data = await api(`/specifications/${state.specId}/session-stats`);
  } catch {
    $("session-stats").innerHTML = `<p class="tagline">Statistics unavailable.</p>`;
    return;
  }
  state.stats = data;
  const { phase1: one, phase2: two, phase3: three } = data;

  $("session-stats").innerHTML = [
    statGroup("Specification", [
      statLine("Versions written", one.versions),
      statLine("Questions MUSE asked", one.questions_asked),
      statLine("Answers that needed a second go", one.asked_again,
               one.asked_again > 2 ? "warn" : ""),
      statLine("\u201cI don't know\u201d presses", one.dont_know_presses),
      statLine("Answers you put on record", one.answers_on_record),
      statLine("Time to lock", fmtTime(one.seconds)),
    ]),
    statGroup("While coding", [
      statLine("Questions you asked", two.questions_asked),
      statLine("  of those, factual", two.factual),
      statLine("  of those, about your own code", two.problem_solving),
      statLine("Counter-questions you got back", two.counter_questions),
      statLine("Direct hints taken", two.direct_hints, two.direct_hints ? "warn" : "good"),
      statLine("Longest run on one issue", `${two.longest_thread} rounds`),
      statLine("Time coding", fmtTime(two.seconds)),
    ]),
    statGroup("Reflection", [
      statLine("Submissions", three.submissions, three.submissions > 1 ? "warn" : ""),
      statLine("Cases that failed", three.cases_failed,
               three.cases_failed ? "bad" : "good"),
      ...Object.entries(three.by_category).map(([k, v]) => statLine(`  ${k}`, v)),
      statLine("Diagnosis rounds", three.diagnosis_rounds),
      statLine("Explanations written", three.explanations_written),
    ]),
    statGroup("Overall", [
      statLine("Questions MUSE asked you", data.totals.ai_questions),
      statLine("Messages you sent", data.totals.student_messages),
    ]),
  ].join("");

  renderTranscript(data.transcript);
}

function renderTranscript(entries) {
  const box = $("transcript-body");
  if (!entries || !entries.length) {
    box.innerHTML = `<div class="body tagline">Nothing recorded.</div>`;
    return;
  }
  box.innerHTML = `<div class="transcript-list">${entries.map((e) => `
    <div class="entry">
      <div class="who">
        <span class="phase-pill p${e.phase}">phase ${e.phase}</span>
        <span>${escapeHtml(e.tag || "")}</span>
        ${e.about ? `<span>· ${escapeHtml(e.about)}</span>` : ""}
      </div>
      ${e.student ? `<div class="said">${escapeHtml(e.student)}</div>` : ""}
      ${e.note ? `<div class="why">${escapeHtml(e.note)}</div>` : ""}
      ${e.muse ? `<div class="asked">${escapeHtml(e.muse)}</div>` : ""}
    </div>`).join("")}</div>`;
}

async function loadProfile() {
  try {
    const profile = await api(`/students/${state.studentId}/dependency-profile`);
    $("profile").innerHTML = `
      <div class="stat-row">
        <div class="stat"><div class="n">${profile.problem_solving}</div><div class="l">problem-solving</div></div>
        <div class="stat"><div class="n">${profile.factual}</div><div class="l">factual</div></div>
        <div class="stat"><div class="n">${profile.escape_hatch_uses}</div><div class="l">direct hints</div></div>
        <div class="stat"><div class="n">${profile.specification_versions}</div><div class="l">spec versions</div></div>
      </div>
      ${profile.repeated_topics.length ? `<p class="tagline" style="margin-bottom:0">
        You kept coming back to: ${profile.repeated_topics.map((t) =>
          `${escapeHtml(t.topic)} (${t.questions})`).join(", ")}.</p>` : ""}
      <p class="tagline" style="margin-bottom:0">${escapeHtml(profile.note)}</p>`;
  } catch {
    $("profile").innerHTML = `<p class="tagline">Profile unavailable.</p>`;
  }
}

/* ------------------------------------------------------------------ init */
async function checkProviders() {
  try {
    const health = await api("/health");
    if (health.llm !== "live") {
      banner(
        "Running on the offline stub — no API keys were loaded, so every reply " +
        "below is canned text, not a real model. Stop uvicorn, check .env, and " +
        "restart it (--reload does not pick up .env changes).", "warn");
    }
  } catch { /* health is advisory */ }
}

async function boot() {
  await checkProviders();
  await loadMemes();
  const [students, assignments] = await Promise.all([
    api("/students"), api("/assignments"),
  ]);
  if (!students.length || !assignments.length) {
    banner("No data yet — run  python -m app.seed  to create a course.", "warn");
    return;
  }
  $("student").innerHTML = students.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  $("assignment").innerHTML = assignments.map((a, i) =>
    `<option value="${a.id}">${i + 1}. ${escapeHtml(a.prompt_text.slice(0, 46))}…</option>`).join("");

  // Come back to the student and assignment you were last on, not always the
  // first one — a reload mid-demo shouldn't throw away where you were.
  const lastStudent = Number(recall("studentId"));
  const lastAssignment = Number(recall("assignmentId"));
  state.studentId = students.some((s) => s.id === lastStudent) ? lastStudent : students[0].id;
  state.assignmentId = assignments.some((a) => a.id === lastAssignment)
    ? lastAssignment : assignments[0].id;
  $("student").value = String(state.studentId);
  $("assignment").value = String(state.assignmentId);
  initEditor();
  await loadSpecification();
}

$("student").addEventListener("change", (e) => {
  state.studentId = Number(e.target.value);
  remember("studentId", state.studentId);
  loadSpecification();
});
$("assignment").addEventListener("change", (e) => {
  state.assignmentId = Number(e.target.value);
  remember("assignmentId", state.assignmentId);
  loadSpecification();
});
$("gate-send").addEventListener("click", sendGateMessage);
$("gate-lock").addEventListener("click", forceLock);
$("gate-dont-know").addEventListener("click", dontKnow);
$("meme-toggle").addEventListener("click", toggleMemes);
$("transcript-toggle").addEventListener("click", () => {
  const body = $("transcript-body");
  const hidden = body.classList.toggle("hidden");
  $("transcript-toggle").textContent = hidden ? "Show" : "Hide";
});
$("meme-overlay").addEventListener("click", hideMeme);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideMeme(); });
$("gate-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendGateMessage(); }
});
$("tutor-send").addEventListener("click", sendTutorMessage);
$("tutor-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendTutorMessage(); }
});
$("escape-hatch").addEventListener("click", useEscapeHatch);
$("submit-code").addEventListener("click", submitForReflection);
$("finalize").addEventListener("click", finalize);
document.querySelectorAll(".phase").forEach((tab) => {
  tab.addEventListener("click", () => phase(Number(tab.dataset.phase)));
});
document.querySelectorAll("[data-goto]").forEach((button) => {
  button.addEventListener("click", () => phase(Number(button.dataset.goto)));
});

boot().catch((error) => banner(error.message, "bad"));
