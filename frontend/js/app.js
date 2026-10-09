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
  ollieForPhase(number);

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
  if (state.locked && window.OLLIE) {
    const intro = $("ollie-intro");
    if (intro) intro.innerHTML = OLLIE.avatar("cheering", { size: 46 }) +
      `<p class="t">${OLLIE.line("gate_unlocked")}</p>`;
  }
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

/* Ollie introduces himself once, at the top of the gate. */
function renderOllieIntro() {
  const host = $("ollie-intro");
  if (!host || !window.OLLIE) return;
  host.innerHTML = OLLIE.avatar("asking", { size: 46 }) +
    `<p class="t"><b>Hi, I'm Ollie.</b> I won't write your code — that part is
     yours. What I'll do is ask until we both know exactly what you're building,
     then stay with you while you build it.</p>`;
}

/* Ollie turns up in all three phases, not only in the chat. One short line
   about the work in front of them — never a nudge, never small talk. */
function ollieSays(slotId, mood, text) {
  const host = $(slotId);
  if (!host || !window.OLLIE || !text) return;
  host.innerHTML = OLLIE.avatar(mood, { size: slotId === "ollie-coding" ? 32 : 38 }) +
    `<p class="ollie-line">${text.replace(/[<>&]/g, "")}</p>`;
  host.classList.remove("hidden");
}

function ollieForPhase(number) {
  if (!window.OLLIE) return;
  if (number === 2) {
    ollieSays("ollie-coding", "asking", OLLIE.line("coding_start"));
  } else if (number === 3 && state.submission) {
    const clean = !state.submission.diff_results.length;
    ollieSays("ollie-reflect", clean ? "cheering" : "gentle",
              OLLIE.line(clean ? "reflect_clean" : "reflect_diffs"));
  }
}

/* ------------------------------------------------- Ollie on the material */
/* The honest part of this panel is the "not covered" branch. The server
   decides that in Python before any model runs, so when Ollie says a topic
   isn't in the notes, that is a fact about the notes — not a model being
   modest. The student can trust it, and so can their instructor. */

function showExplainCard(assignment) {
  const card = $("explain-card");
  if (!card) return;
  const has = Boolean(assignment && assignment.subject_id);
  card.classList.toggle("hidden", !has);
  if (!has) return;
  state.subjectId = assignment.subject_id;
  $("explain-subject").textContent = assignment.subject_name || "the subject";
}

async function askOllieToExplain(inputId = "explain-input", outId = "explain-answer") {
  const input = $(inputId);
  const question = input.value.trim();
  if (!question || !state.subjectId) return;
  const out = $(outId);
  input.value = "";
  out.innerHTML = OLLIE.avatar("thinking", { size: 30 }) +
    `<div class="explain-body"><p class="explain-text">Looking through the notes…</p></div>`;

  try {
    const result = await api(`/subjects/${state.subjectId}/explain`, {
      method: "POST", body: JSON.stringify({ question }),
    });
    const excerpts = (result.excerpts || []).map((e) =>
      `<li>${escapeHtml(typeof e === "string" ? e : (e.title || e.body || ""))}</li>`).join("");
    out.innerHTML = OLLIE.avatar(result.covered ? "asking" : "gentle", { size: 30 }) +
      `<div class="explain-body">
         <p class="explain-text">${escapeHtml(result.answer)}</p>
         ${excerpts ? `<p class="explain-from">from your notes:</p>
                       <ul class="explain-cites">${excerpts}</ul>` : ""}
       </div>`;
  } catch (error) {
    out.innerHTML = `<p class="tagline">${escapeHtml(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------ Learn mode */
/* Reading notes feels like learning and largely isn't: having just read
   something fluently, you cannot tell recognising it from being able to
   produce it. The two things that reliably move material into memory are
   elaboration — tying a new idea to something concrete — and retrieval, being
   made to produce the answer before you see it. So the lesson is shaped around
   both: one idea, a few steps, an analogy for the mechanism, and a question
   answered with the note hidden.

   The note's own text sits beside it the whole time. The lesson is a way in,
   not a replacement, and a student revising for an exam needs the real thing. */

const learn = { notes: [], current: null, lesson: null, revealed: false };

function learnReadingTime(words) {
  // 200 wpm, rounded up, floored at one minute. Honest enough to decide with.
  return Math.max(1, Math.ceil((words || 0) / 200));
}

async function openLearn() {
  view("learn");
  $("learn-h").textContent = state.subject ? state.subject.name : "Your notes";
  renderVoiceControls();
  if (window.SPEECH && SPEECH.supported()) {
    $("voice-rate").value = String(SPEECH.rate);
  }
  const tabs = $("note-tabs");
  tabs.innerHTML = `<p class="tagline" style="margin:0">Loading your notes…</p>`;

  try {
    learn.notes = await api(`/subjects/${state.subjectId}/materials`);
  } catch (error) {
    tabs.innerHTML = `<p class="tagline">${escapeHtml(error.message)}</p>`;
    return;
  }

  if (!learn.notes.length) {
    tabs.innerHTML = "";
    $("note-body").innerHTML =
      `<p class="tagline">Your instructor hasn't added any notes for
        ${escapeHtml(state.subject ? state.subject.name : "this subject")} yet.</p>`;
    $("lesson").innerHTML = ollieNote("gentle",
      "Nothing to teach from yet. Once your instructor writes the material in, " +
      "this is where I'll go through it with you.");
    return;
  }

  tabs.innerHTML = learn.notes.map((note, index) => `
    <button type="button" class="note-tab" data-note="${note.id}">
      <span class="nt-n">${index + 1}</span>
      <span class="nt-t">${escapeHtml(note.title)}</span>
      <span class="nt-m">${learnReadingTime(note.words)} min read</span>
    </button>`).join("");
  tabs.querySelectorAll("[data-note]").forEach((button) => {
    button.addEventListener("click", () => openNote(Number(button.dataset.note)));
  });

  const last = Number(recall(`note.${state.subjectId}`));
  const pick = learn.notes.some((n) => n.id === last) ? last : learn.notes[0].id;
  await openNote(pick);
}

/** One line from Ollie, used where a whole lesson would be too much. */
function ollieNote(mood, text) {
  if (!window.OLLIE) return `<p class="tagline">${escapeHtml(text)}</p>`;
  return `<div class="ollie-says">${OLLIE.avatar(mood, { size: 38 })}
            <p class="ollie-line">${escapeHtml(text)}</p></div>`;
}

/** The My learning page ticks off notes the student has opened. Kept in this
 *  browser only, like the rest of what this page remembers. */
function markNoteRead(subjectId, noteId) {
  try {
    const key = `muse.read.${subjectId}`;
    const read = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
    read.add(noteId);
    localStorage.setItem(key, JSON.stringify([...read]));
  } catch { /* private mode */ }
}

async function openNote(noteId) {
  const note = learn.notes.find((n) => n.id === noteId);
  if (!note) return;
  stopSpeaking();           // a new note must not be read over the old one
  learn.current = note;
  learn.revealed = false;
  remember(`note.${state.subjectId}`, noteId);
  markNoteRead(state.subjectId, noteId);

  document.querySelectorAll(".note-tab").forEach((tab) => {
    tab.classList.toggle("active", Number(tab.dataset.note) === noteId);
  });

  renderNoteBody(note);
  $("lesson").innerHTML = window.OLLIE
    ? `<div class="lesson-wait">${OLLIE.avatar("thinking", { size: 44 })}
         <p class="ollie-line">Reading <b>${escapeHtml(note.title)}</b> and working
            out how to explain it…</p></div>`
    : `<p class="tagline">Working…</p>`;
  $("learn-answer").innerHTML = "";

  try {
    learn.lesson = await api(
      `/subjects/${state.subjectId}/materials/${noteId}/teach`, { method: "POST" });
    renderLesson(learn.lesson);
  } catch (error) {
    $("lesson").innerHTML = `<p class="tagline">${escapeHtml(error.message)}</p>`;
  }
}

/* The note, paragraph by paragraph, with numbers — the lesson cites them, so
   "from paragraph 2" has to be something the student can actually find. */
function renderNoteBody(note) {
  const paragraphs = String(note.body || "").split(/\n\s*\n/)
    .map((p) => p.trim()).filter(Boolean);
  $("note-body").innerHTML = `
    <h3 class="note-title">${escapeHtml(note.title)}</h3>
    <div class="note-text">${paragraphs.map((p, i) =>
      `<p id="para-${i}" class="${looksPreformatted(p) ? "is-pre" : ""}">
         <span class="para-n">${i + 1}</span>${escapeHtml(p)}</p>`
    ).join("")}</div>`;
}

function renderLesson(lesson) {
  const host = $("lesson");

  if (!lesson.taught) {
    host.innerHTML = ollieNote("gentle", lesson.idea);
    $("lesson-source").textContent = "nothing to teach from yet";
    return;
  }

  $("lesson-source").textContent = lesson.dropped
    ? `${lesson.points.length} point${lesson.points.length === 1 ? "" : "s"}, all traced to the note`
    : "from your instructor's notes";

  const steps = lesson.points.map((point, index) => `
    <li class="lesson-step" id="seg-step-${index}">
      <span class="ls-n">${index + 1}</span>
      <div class="ls-body">
        <h4>${escapeHtml(point.title)}</h4>
        <p>${escapeHtml(point.body)}</p>
        <div class="ls-tools">
          ${point.paragraph != null
            ? `<button class="ls-from" type="button" data-para="${point.paragraph}">
                 paragraph ${point.paragraph + 1} of the note</button>`
            : ""}
          <button class="ls-say speak-btn hidden" type="button" data-say="${index}"
                  title="Hear this step on its own">▶ hear this step</button>
        </div>
      </div>
    </li>`).join("");

  host.innerHTML = `
    ${lesson.hook ? `<p class="lesson-hook" id="seg-hook">${escapeHtml(lesson.hook)}</p>` : ""}

    <div class="lesson-idea" id="seg-idea">
      ${window.OLLIE ? OLLIE.avatar("asking", { size: 40 }) : ""}
      <div>
        <span class="li-label">The one thing to take away</span>
        <p class="li-text">${escapeHtml(lesson.idea)}</p>
      </div>
    </div>

    <ol class="lesson-steps">${steps}</ol>

    ${lesson.example ? `
      <div class="lesson-worked${looksPreformatted(lesson.example) ? " is-pre" : ""}" id="seg-worked">
        <span class="lw-label">Worked through</span>
        <p>${escapeHtml(lesson.example)}</p>
      </div>` : ""}

    ${lesson.memory_hook ? `
      <div class="lesson-hook-box" id="seg-memory">
        <span class="lh-label">Ollie's way of remembering it</span>
        <p>${escapeHtml(lesson.memory_hook)}</p>
        <span class="lh-caveat">My analogy, not your instructor's — it's there to
          hold the idea in place, so check the note for the exact wording.</span>
      </div>` : ""}

    ${lesson.check ? `
      <div class="lesson-check" id="seg-check">
        <span class="lc-label">Before you move on</span>
        <p class="lc-q">${escapeHtml(lesson.check.question)}</p>
        <textarea id="check-answer" rows="2"
          placeholder="Answer from memory — don't look at the note yet…"></textarea>
        <div class="lc-actions">
          <button class="primary" id="check-reveal" type="button">
            Show me what the note says</button>
          <span class="tagline" id="check-nudge">Even a wrong guess makes it
            stick better than reading the answer. Nothing here is marked or
            recorded.</span>
        </div>
        <div id="check-result"></div>
      </div>` : ""}

    <div class="lesson-next">
      <button class="primary" type="button" id="lesson-practice">
        Practise this subject →</button>
      ${nextNoteButton()}
    </div>`;

  host.querySelectorAll("[data-para]").forEach((button) => {
    button.addEventListener("click", () => highlightParagraph(Number(button.dataset.para)));
  });
  host.querySelectorAll("[data-say]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.say);
      const point = lesson.points[index];
      if (!point) return;
      speakToggle(`step-${index}`,
        [{ id: `seg-step-${index}`, text: `${point.title}. ${point.body}` }]);
    });
  });
  renderVoiceControls();

  const reveal = $("check-reveal");
  if (reveal) reveal.addEventListener("click", revealCheck);
  const practice = $("lesson-practice");
  if (practice) practice.addEventListener("click", () => startPractice());
  const next = $("lesson-next-note");
  if (next) next.addEventListener("click", () => openNote(Number(next.dataset.next)));
}

/* ------------------------------------------------- Ollie reading it out */
/* Two separate things: the lesson, and the instructor's note. Each is read as
   a list of blocks rather than one wall of text, so the page can light up
   whichever block is being spoken — which is most of the value. Hearing a
   lesson without seeing where you are in it is worse than reading it. */

function lessonSegments(lesson) {
  if (!lesson || !lesson.taught) return [];
  const out = [];
  if (lesson.hook) out.push({ id: "seg-hook", text: lesson.hook });
  out.push({ id: "seg-idea", text: `The one thing to take away. ${lesson.idea}` });
  lesson.points.forEach((point, i) => {
    out.push({ id: `seg-step-${i}`, text: `${point.title}. ${point.body}` });
  });
  if (lesson.example) {
    out.push({ id: "seg-worked", text: `Worked through. ${lesson.example}`,
               formula: looksPreformatted(lesson.example) });
  }
  if (lesson.memory_hook) {
    out.push({ id: "seg-memory", text: `My way of remembering it. ${lesson.memory_hook}` });
  }
  if (lesson.check) {
    // The answer is deliberately not read. Speaking it aloud straight after the
    // question would undo the one thing the question is for.
    out.push({ id: "seg-check", text: `Before you move on. ${lesson.check.question}` });
  }
  return out;
}

function noteSegments(note) {
  return String((note || {}).body || "").split(/\n\s*\n/)
    .map((p, i) => ({ id: `para-${i}`, text: p.trim(), formula: looksPreformatted(p) }))
    .filter((s) => s.text);
}

/* The spoken block gets a marker, and the page scrolls to keep it in view —
   but only when it has drifted off screen, because scrolling under someone who
   is reading ahead is its own kind of rude. */
function markSpeaking(id) {
  document.querySelectorAll(".is-speaking").forEach((el) => el.classList.remove("is-speaking"));
  if (!id) return;
  const element = $(id);
  if (!element) return;
  element.classList.add("is-speaking");
  const box = element.getBoundingClientRect();
  if (box.top < 70 || box.bottom > window.innerHeight - 40) {
    element.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function renderVoiceControls() {
  const bar = $("voice-bar");
  if (!bar) return;
  if (!window.SPEECH || !SPEECH.supported()) {
    // Hidden rather than shown broken. Saying "your browser can't do this" to
    // someone who never asked for it is noise.
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");

  const status = SPEECH.status();
  const toggle = $("voice-toggle");
  toggle.setAttribute("aria-pressed", String(status.on));
  toggle.classList.toggle("is-on", status.on);
  toggle.querySelector(".vt-label").textContent =
    `Read aloud: ${status.on ? "on" : "off"}`;
  $("voice-rate-wrap").classList.toggle("hidden", !status.on);

  // Every play button on the page, including the per-step ones that only exist
  // once a lesson has rendered. The one currently reading becomes its own
  // pause button, so there is never a second control to hunt for.
  const LABELS = { "speak-lesson": "Read it to me", "speak-note": "Read" };
  document.querySelectorAll(".speak-btn").forEach((button) => {
    button.classList.toggle("hidden", !status.on);
    const key = button.dataset.say != null ? `step-${button.dataset.say}` : button.id;
    const label = LABELS[key] || "hear this step";
    if (speech.reading === key && status.speaking) {
      button.textContent = status.paused ? "▶ Resume" : "❚❚ Pause";
      button.classList.add("is-live");
    } else {
      button.textContent = `▶ ${label}`;
      button.classList.remove("is-live");
    }
  });
}

const speech = { reading: null };

function speechHandlers(buttonId) {
  return {
    onSegment: markSpeaking,
    onState: (status) => {
      if (!status.speaking) {
        if (speech.reading === buttonId) speech.reading = null;
        markSpeaking(null);
      }
      renderVoiceControls();
    },
  };
}

/** One button that starts, pauses and resumes the thing it belongs to. */
function speakToggle(buttonId, segments) {
  if (!window.SPEECH || !SPEECH.supported() || !SPEECH.enabled) return;
  const status = SPEECH.status();

  if (speech.reading === buttonId && status.speaking) {
    if (status.paused) SPEECH.resume(); else SPEECH.pause();
    renderVoiceControls();
    return;
  }

  speech.reading = buttonId;
  const started = SPEECH.speak(segments, speechHandlers(buttonId));
  if (!started) speech.reading = null;
  renderVoiceControls();
}

/** Anything that changes what is on screen also stops the voice. */
function stopSpeaking() {
  if (!window.SPEECH || !SPEECH.supported()) return;
  speech.reading = null;
  SPEECH.stop();
  markSpeaking(null);
  renderVoiceControls();
}

/* Algebra and code get a monospace block, laid out as written; a worked
   example in prose does not. Counting sentences was not enough — a sentence
   saying "accelerates at g = 9.81 m/s^2" is prose with an equals sign in it,
   and it came out set in monospace. So the test is structural: either every
   line is a relation and none is a sentence, or the block is indented code. */
function looksPreformatted(text) {
  const raw = String(text || "");
  const lines = raw.split("\n").filter((l) => l.trim());
  if (!lines.length) return false;

  const indented = lines.filter((l) => /^(\s{4,}|\t)/.test(l)).length;
  if (/\bdef\s+\w+\(/.test(raw) || indented >= Math.max(1, Math.floor(lines.length / 2))) {
    return true;
  }

  const t = raw.trim();
  if (!/[=<>≤≥]/.test(t)) return false;
  const allRelations = lines.every((l) => /[=<>≤≥]/.test(l) && !/[.!?]/.test(l));
  return allRelations && (lines.length > 1 || t.length <= 80);
}

function nextNoteButton() {
  const index = learn.notes.findIndex((n) => n.id === (learn.current || {}).id);
  const next = learn.notes[index + 1];
  if (!next) return "";
  return `<button class="ghost" type="button" id="lesson-next-note"
                  data-next="${next.id}">Next note: ${escapeHtml(next.title)} →</button>`;
}

/* Clicking a citation scrolls the note to that paragraph and flashes it. The
   point is that every taught claim is checkable in one click. */
function highlightParagraph(index) {
  const target = $(`para-${index}`);
  if (!target) return;
  if ($("note-body").closest(".card").classList.contains("note-hidden")) toggleNote();
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.remove("flash");
  void target.offsetWidth;          // restart the animation if it is mid-flight
  target.classList.add("flash");
}

function revealCheck() {
  if (learn.revealed) return;
  learn.revealed = true;
  const mine = ($("check-answer").value || "").trim();
  const answer = (learn.lesson.check || {}).answer || "";

  // Their answer is shown beside the note's so they compare rather than just
  // read. It is deliberately not graded: a word-overlap score on a free-text
  // answer would be wrong often enough to be worse than nothing, and a wrong
  // "incorrect" from Ollie here would cost more than it bought.
  $("check-result").innerHTML = `
    ${mine ? `<div class="lc-mine">
                <span class="lc-sub">What you said</span>
                <p>${escapeHtml(mine)}</p>
              </div>` : ""}
    <div class="lc-theirs">
      <span class="lc-sub">What the note says</span>
      <p>${escapeHtml(answer)}</p>
    </div>
    <p class="lc-close">${escapeHtml(mine
      ? "Compare them yourself — if yours is close, you've got it. If it isn't, "
        + "read that paragraph again before you move on."
      : "Have a go at saying it out loud now, with the answer covered. That's "
        + "the bit that does the work.")}</p>`;
  $("check-reveal").disabled = true;
  $("check-nudge").textContent = "";
}

function toggleNote() {
  const card = $("note-body").closest(".card");
  const hidden = card.classList.toggle("note-hidden");
  $("note-toggle").textContent = hidden ? "Show" : "Hide";
}

function bubble(container, role, text, meta, mood) {
  const element = document.createElement("div");
  element.className = `msg ${role}`;

  if (role === "muse" && window.OLLIE) {
    // Ollie's lines get his face and his name. A question lands differently
    // from someone you recognise than from an unlabelled box.
    element.innerHTML = OLLIE.avatar(mood || "asking", { size: 32 });
    const body = document.createElement("div");
    body.className = "ollie-body";

    const who = document.createElement("span");
    who.className = "who";
    who.textContent = "Ollie";
    body.appendChild(who);

    const line = document.createElement("div");
    line.className = "line";
    line.textContent = text;
    body.appendChild(line);

    if (meta) {
      const span = document.createElement("span");
      span.className = "meta";
      span.innerHTML = meta;
      body.appendChild(span);
    }
    element.appendChild(body);
  } else {
    element.textContent = text;
    if (meta) {
      const span = document.createElement("span");
      span.className = "meta";
      span.innerHTML = meta;
      element.appendChild(span);
    }
  }

  container.appendChild(element);
  container.scrollTop = container.scrollHeight;
  return element;
}

function thinking(container) {
  const element = document.createElement("div");
  element.className = "msg muse thinking";
  if (window.OLLIE) {
    element.innerHTML = OLLIE.avatar("thinking", { size: 32 }) +
      `<div class="ollie-body"><span class="who">Ollie</span>
       <div class="line">thinking…</div></div>`;
  } else {
    element.textContent = "thinking";
  }
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
  renderOllieIntro();
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
  showExplainCard(data.assignment);
  $("spec-version").textContent = data.version ? `version ${data.version}` : "";
  $("gate-status").innerHTML = data.locked
    ? '<span class="tag locked">locked</span>' : "draft";
  renderSpec(data.content, $("spec-sheet"));
  renderRubric(data.rubric_status);
  renderOptions(null);

  $("gate-messages").innerHTML = "";
  bubble($("gate-messages"), "system",
    "Before the editor unlocks, say what you're going to build. Ollie asks one " +
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
      bubble($("gate-messages"), "muse", result.shortfall, null, "gentle")
      .classList.add("shortfall");
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
        value: "", language: "python", theme: "vs",
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
    ollieSays("ollie-reflect",
              result.diff_results.length ? "gentle" : "cheering",
              OLLIE.line(result.diff_results.length ? "reflect_diffs" : "reflect_clean"));
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
    $("finalize-note").textContent = "Your code matches your plan. You can submit.";
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
    ? "Every difference is explained. You can submit."
    : `${status.explanations_outstanding} more to explain before you can submit.`;
}

async function finalize() {
  try {
    const result = await api(`/specifications/${state.specId}/finalize`, { method: "POST" });
    banner(`Submitted at ${new Date(result.finalized_at).toLocaleString()}. Well done.`, "ok");
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

/* ------------------------------------------------- the student's own view */
/* Same engine and the same charts the instructor sees, scoped to this student.
   The status tier is deliberately absent: the API does not send it to a
   student viewer, so it is not hidden in the page, it was never fetched. */

const MY_AREA_LABELS = {
  specification: "Writing your specification",
  edge_cases: "Boundary and edge cases",
  types: "Return types and shapes",
  examples: "Concrete examples",
  debugging: "Debugging your own code",
  reflection: "Explaining your own work",
  independence: "Working without hints",
};

async function renderMyDashboard() {
  const host = $("my-dashboard");
  if (!host || !state.studentId || !window.MUSE_CHARTS) return;
  let data;
  try {
    data = await api(`/students/${state.studentId}/insights`);
  } catch {
    host.innerHTML = `<p class="tagline">Not available yet.</p>`;
    return;
  }

  const t = data.totals, norms = data.class_norms;
  const mix = [
    { label: "about your own code", value: t.questions_own_code, tone: "a" },
    { label: "about Python", value: t.questions - t.questions_own_code, tone: "b" },
  ];
  const diffs = [
    { label: "drift", value: data.difference_mix.drift || 0, tone: "warn" },
    { label: "bug", value: data.difference_mix.bug || 0, tone: "bad" },
    { label: "revision", value: data.difference_mix.revision || 0, tone: "a" },
  ];
  const timings = data.timings.map((item) => `
    <div class="dash-attempt">
      <div class="dash-attempt-head">
        <b>${escapeHtml(item.entry_point || "assignment")}</b>
        <span>${item.solved ? "solved" : item.submitted ? "differences still open"
                                       : item.locked ? "not submitted" : "in progress"}</span>
      </div>
      ${MUSE_CHARTS.stack([
        { label: "specification", value: item.specification_seconds || 0,
          display: MUSE_CHARTS.human(item.specification_seconds), tone: "a" },
        { label: "coding", value: item.coding_seconds || 0,
          display: MUSE_CHARTS.human(item.coding_seconds), tone: "b" },
      ], { empty: "No time recorded yet." })}
      ${MUSE_CHARTS.timeline(item.retry_gaps_seconds,
        { attempts: item.submissions, solved: item.solved, empty: "Not submitted yet." })}
    </div>`).join("");

  const areas = Object.entries(data.areas)
    .map(([k, v]) => ({ label: MY_AREA_LABELS[k] || k, score: v.score }))
    .sort((a, b) => b.score - a.score);

  host.innerHTML = `
    <div class="dash-grid">
      <section class="panel half">
        <h3 class="panel-h">At a glance</h3>
        <div class="meter-row">
          ${MUSE_CHARTS.meter("Questions about your own code", t.questions_own_code,
              t.questions, { caption: `${t.questions_own_code} of ${t.questions}` })}
          ${MUSE_CHARTS.meter("Differences you explained", t.differences_explained,
              t.differences, { caption: `${t.differences_explained} of ${t.differences}` })}
          ${MUSE_CHARTS.meter("Exercises solved", t.assignments_solved,
              t.assignments_started, { caption: `${t.assignments_solved} of ${t.assignments_started}` })}
        </div>
      </section>

      <section class="panel">
        <h3 class="panel-h">Compared with the class</h3>
        <div class="dash-compare">
          ${MUSE_CHARTS.compare("Your time per exercise", t.mean_total_seconds,
              norms.total_seconds, { format: MUSE_CHARTS.human })}
          ${MUSE_CHARTS.compare("Hints you took", t.hints, Math.round(norms.hints * 10) / 10)}
        </div>
        <p class="ch-note">The line is the class. Either side of it is not a score.</p>
      </section>

      <section class="panel wide">
        <h3 class="panel-h">Where your time went
          <span class="panel-note">specification vs coding</span></h3>
        ${timings ? `<div class="attempt-grid">${timings}</div>`
                  : `<p class="dash-none">Nothing submitted yet.</p>`}
      </section>

      <section class="panel">
        <h3 class="panel-h">What you asked about</h3>
        ${MUSE_CHARTS.stack(mix, { empty: "You haven't asked anything yet." })}
      </section>

      <section class="panel">
        <h3 class="panel-h">Kinds of difference found</h3>
        ${MUSE_CHARTS.stack(diffs, { empty: "No differences found." })}
      </section>

      <section class="panel wide">
        <h3 class="panel-h">Your strengths and weak spots</h3>
        ${MUSE_CHARTS.diverging(areas, {
            empty: "Not enough work yet to tell.",
            leftLabel: "worth practising", rightLabel: "solid" })}
      </section>

      ${data.plan.length ? `
      <section class="panel wide">
        <h3 class="panel-h">What to try next</h3>
        <div class="plan-grid">${data.plan.map((item) => `
          <div class="plan">
            <div class="plan-head">
              <span class="plan-area">${escapeHtml(item.area_label)}</span>
              <h4>${escapeHtml(item.title)}</h4>
            </div>
            <p class="plan-because">Because: ${escapeHtml(item.because)}</p>
            ${item.leaning_on ? `<p class="plan-via">${escapeHtml(item.leaning_on)}</p>` : ""}
            <ol class="plan-steps">${item.steps.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ol>
          </div>`).join("")}</div>
      </section>` : ""}
    </div>`;
}

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
      statLine("Questions Ollie asked", one.questions_asked),
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
      statLine("Questions Ollie asked you", data.totals.ai_questions),
      statLine("Messages you sent", data.totals.student_messages),
    ]),
  ].join("");

  renderTranscript(data.transcript);
  renderMyDashboard();
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

/* --------------------------------------------------------- the first page */
/* Every assignment used to sit in one flat dropdown, so a maths exercise and
   a Python one looked like the same kind of thing. Choosing the subject first
   is also the honest order: the subject is what decides how the work gets
   marked, so it belongs before the exercise, not after it. */

function renderStart(subjects, assignments) {
  const intro = $("start-intro");
  if (intro && window.OLLIE) {
    intro.innerHTML = OLLIE.avatar("asking", { size: 52 }) +
      `<p class="t"><b>Hi, I'm Ollie.</b> I won't do the work for you, and I
       won't hand you answers. I'll ask until you know what you're building,
       then stay with you while you build it.</p>`;
  }

  const counts = {};
  assignments.forEach((a) => {
    counts[a.subject_id] = (counts[a.subject_id] || 0) + 1;
  });

  $("subject-grid").innerHTML = subjects.map((sub) => `
    <button type="button" class="subject-card" data-pick="${sub.id}">
      <span class="sc-top">
        <h3>${escapeHtml(sub.name)}</h3>
        <span class="ck">${escapeHtml(sub.checker)}</span>
      </span>
      ${sub.description ? `<p class="sc-desc">${escapeHtml(sub.description)}</p>` : ""}
      <span class="sc-meta">
        <span>${counts[sub.id] || 0} exercise${(counts[sub.id] || 0) === 1 ? "" : "s"}</span>
        <span>${sub.materials} note${sub.materials === 1 ? "" : "s"}</span>
      </span>
      <p class="sc-how">${escapeHtml(sub.checker_description || "")}</p>
    </button>`).join("");

  document.querySelectorAll("[data-pick]").forEach((b) => {
    b.addEventListener("click", () => chooseSubject(Number(b.dataset.pick)));
  });
}

/* One function owns which of the four pages is on screen — picker, the
   learn-or-practise choice, the lesson, and the three-phase exercise flow.
   Toggling sections from four different places is how you end up with two
   pages visible at once, or a dashboard rendering inside a hidden parent. */
function view(name) {
  // Leaving the page silences it. Ollie reading a lesson the student has
  // walked away from is the single most annoying thing this feature could do.
  if (state.view !== name) stopSpeaking();
  state.view = name;
  $("start").classList.toggle("hidden", name !== "start");
  $("mode").classList.toggle("hidden", name !== "mode");
  $("learn").classList.toggle("hidden", name !== "learn");
  $("practice-intro").classList.toggle("hidden", name !== "intro");

  const practising = name === "practice";
  document.querySelector("nav.phases").classList.toggle("hidden", !practising);
  if (!practising) [1, 2, 3].forEach((n) => $(`phase-${n}`).classList.add("hidden"));

  $("change-mode").classList.toggle("hidden", name === "start" || name === "mode");
  // An empty exercise dropdown in the header reads as broken, so it only
  // appears while an exercise is actually being worked on.
  document.querySelectorAll(".needs-subject").forEach((el) =>
    el.classList.toggle("hidden", !practising));
}

/** Show the picker; hide everything else. */
/* Subjects are chosen on the My learning page now, so the old in-page
   subject picker is never shown: anything that asked for it goes there. */
function showStart() { location.href = "/learning"; }

/* The subject is chosen, so now: read it, or do it. Both doors are described
   by what actually happens behind them, and a door with nothing behind it says
   so rather than opening onto an empty page. */
function showModeChoice() {
  const subject = state.subject || {};
  const exercises = (state.allAssignments || [])
    .filter((a) => a.subject_id === subject.id).length;
  const notes = subject.materials || 0;

  const intro = $("mode-intro");
  if (intro && window.OLLIE) {
    intro.innerHTML = OLLIE.avatar("asking", { size: 48 }) +
      `<p class="t">You picked <b>${escapeHtml(subject.name || "a subject")}</b>.
        Want me to take you through the material first, or go straight to the
        exercises?</p>`;
  }
  $("mode-h").textContent = subject.name || "What now?";

  const cards = [
    {
      mode: "learn", title: "Learn the material", count: notes,
      unit: "note", icon: "notes",
      desc: "I take one note at a time, give you the one idea it is really "
          + "about, walk through it in steps, and leave you with a question to "
          + "answer from memory.",
      how: "Nothing here is marked or recorded. Everything I say comes out of "
         + "your instructor's own notes, with the paragraph it came from one "
         + "click away.",
      blocked: notes ? null : "Your instructor hasn't added any notes for this subject yet.",
    },
    {
      mode: "practice", title: "Practise", count: exercises,
      unit: "exercise", icon: "editor",
      desc: "Say what you're going to build before the editor unlocks, build it "
          + "yourself, then see exactly where your code and your own plan "
          + "disagree.",
      how: subject.checker_description || "",
      blocked: exercises ? null : "This subject has no exercises yet.",
    },
  ];

  $("mode-grid").innerHTML = cards.map((card) => `
    <button type="button" class="subject-card mode-card${card.blocked ? " is-blocked" : ""}"
            data-mode="${card.mode}"${card.blocked ? " disabled" : ""}>
      <span class="sc-top">
        <h3>${escapeHtml(card.title)}</h3>
        <span class="ck">${card.count} ${card.unit}${card.count === 1 ? "" : "s"}</span>
      </span>
      <p class="sc-desc">${escapeHtml(card.desc)}</p>
      <p class="sc-how">${escapeHtml(card.blocked || card.how)}</p>
    </button>`).join("");

  $("mode-grid").querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => chooseMode(button.dataset.mode));
  });
  view("mode");
}

function chooseMode(mode) {
  remember(`mode.${state.subjectId}`, mode);
  if (mode === "learn") return openLearn();
  return startPractice();
}

/* A short briefing between "practise" and the coding page: which exercise,
   how far the student already got with each, and what the three steps are.
   Going straight into a locked editor with a rubric felt abrupt. */
async function showPracticeIntro() {
  const subject = state.subject || {};
  const mine = (state.allAssignments || []).filter((a) => a.subject_id === state.subjectId);
  if (!mine.length) {
    banner(`${subject.name || "This subject"} has no exercises yet.`, "warn");
    return showModeChoice();
  }
  remember(`mode.${state.subjectId}`, "practice");
  view("intro");

  const isMath = subject.checker === "math";
  $("pi-ollie").innerHTML = ollieNote("cheering",
    "Take a breath. You'll say what you plan to do, do it yourself, and I'll be right here the whole time.");
  $("pi-h").textContent = `Ready to practise ${subject.name || "this subject"}?`;
  $("pi-steps").innerHTML = [
    ["Plan it", "Tell Ollie what you're going to do, in your own words. The next step unlocks once your plan covers everything."],
    [isMath ? "Work it out" : "Build it", isMath
      ? "Solve it yourself. Ask Ollie about the ideas and he'll answer; ask about your working and he'll ask back."
      : "Write every line yourself. Ask Ollie about Python and he answers; ask about your code and he asks back."],
    ["Check it", isMath
      ? "Your answer is compared with your own plan, and you explain any difference."
      : "Your code is run against your own plan, and you explain any difference in your own words."],
  ].map(([t, d], i) => `<li class="pi-step"><span class="pi-n">${i + 1}</span>
      <div><h3>${t}</h3><p>${escapeHtml(d)}</p></div></li>`).join("");

  const last = Number(recall("assignmentId"));
  const row = (a, i, st) => {
    const label = { new: "Not started", plan: "Planning", code: "Plan done, coding" }[st] || "Not started";
    const cta = st === "new" ? "Start" : "Continue";
    const text = a.prompt_text.length > 150 ? `${a.prompt_text.slice(0, 150).trim()}…` : a.prompt_text;
    return `<li class="pi-ex${a.id === last ? " is-last" : ""}">
        <span class="pi-ex-n">${i + 1}</span>
        <p class="pi-ex-t">${escapeHtml(text)}</p>
        <span class="pi-status st-${st}">${label}</span>
        <button type="button" class="primary pi-go" data-go="${a.id}">${cta} →</button>
      </li>`;
  };
  $("pi-list").innerHTML = mine.map((a, i) => row(a, i, "new")).join("");
  const bind = () => $("pi-list").querySelectorAll("[data-go]").forEach((b) =>
    b.addEventListener("click", () => {
      remember("assignmentId", Number(b.dataset.go));
      startPractice({ direct: true });
    }));
  bind();

  // How far this student got with each one. Best-effort: if it fails the list
  // simply says "Not started".
  const states = await Promise.all(mine.map((a) =>
    api(`/assignments/${a.id}/specification?student_id=${state.studentId}`)
      .then((s) => (!s || !s.specification_id) ? "new" : s.locked ? "code" : "plan")
      .catch(() => "new")));
  if (state.view !== "intro") return;
  $("pi-list").innerHTML = mine.map((a, i) => row(a, i, states[i])).join("");
  bind();
}

/** The three-phase flow, on this subject's first (or last-used) exercise.
 *  Shows the briefing first unless called with {direct: true}. */
async function startPractice(opts = {}) {
  if (!opts.direct) return showPracticeIntro();
  const mine = (state.allAssignments || [])
    .filter((a) => a.subject_id === state.subjectId);
  if (!mine.length) {
    banner(`${(state.subject || {}).name || "This subject"} has no exercises yet.`, "warn");
    return showModeChoice();
  }
  remember(`mode.${state.subjectId}`, "practice");

  const last = Number(recall("assignmentId"));
  state.assignmentId = mine.some((a) => a.id === last) ? last : mine[0].id;
  $("assignment").value = String(state.assignmentId);

  view("practice");
  state.maxPhase = 1;
  phase(1);
  await loadSpecification();
}

async function chooseSubject(subjectId, opts = {}) {
  const subject = (state.subjects || []).find((s) => s.id === subjectId);
  if (!subject) return;
  state.subject = subject;
  state.subjectId = subjectId;
  remember("subjectId", subjectId);
  banner("");

  // Only this subject's exercises, numbered from 1 within it.
  const mine = (state.allAssignments || []).filter((a) => a.subject_id === subjectId);
  $("assignment").innerHTML = mine.map((a, i) =>
    `<option value="${a.id}">${i + 1}. ${escapeHtml(a.prompt_text.slice(0, 46))}…</option>`).join("");
  $("subject").value = String(subjectId);

  // A reload mid-demo shouldn't cost you your place, but a first visit to a
  // subject always shows the choice rather than guessing which one was meant.
  const remembered = opts.restore ? recall(`mode.${subjectId}`) : null;
  if (remembered === "learn" && subject.materials) return openLearn();
  if (remembered === "practice" && mine.length) return startPractice({ direct: !opts.intro });
  showModeChoice();
}

async function boot() {
  await checkProviders();
  await loadMemes();
  const [students, assignments] = await Promise.all([
    api("/students"), api("/assignments"),
  ]);
  state.allAssignments = assignments;
  if (!students.length || !assignments.length) {
    banner("No data yet — run  python -m app.seed  to create a course.", "warn");
    return;
  }
  $("student").innerHTML = students.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");

  const lastStudent = Number(recall("studentId"));
  state.studentId = students.some((s) => s.id === lastStudent) ? lastStudent : students[0].id;
  $("student").value = String(state.studentId);

  // Subjects come from the course the assignments belong to.
  const courseId = assignments[0].course_id;
  const subjects = await api(`/courses/${courseId}/subjects`);
  state.subjects = subjects;
  $("subject").innerHTML = subjects.map((sub) =>
    `<option value="${sub.id}">${escapeHtml(sub.name)}</option>`).join("");

  initEditor();
  renderStart(subjects, assignments);

  // Arriving from a card on My learning: open that subject straight into its
  // notes or its exercises, then drop the query so a reload behaves normally.
  const params = new URLSearchParams(location.search);
  const wanted = Number(params.get("subject"));
  if (subjects.some((sub) => sub.id === wanted)) {
    const mode = params.get("mode");
    const note = Number(params.get("note"));
    if (mode === "learn" || mode === "practice") remember(`mode.${wanted}`, mode);
    if (note) remember(`note.${wanted}`, note);
    history.replaceState(null, "", location.pathname);
    await chooseSubject(wanted, { restore: mode === "learn" || mode === "practice", intro: true });
    return;
  }

  // Come back to where you were — a reload mid-demo shouldn't cost you your
  // place — but a first visit starts at the picker rather than dropping you
  // into an exercise you never chose.
  const lastSubject = Number(recall("subjectId"));
  if (subjects.some((sub) => sub.id === lastSubject)) {
    await chooseSubject(lastSubject, { restore: true });
  } else {
    showStart();
  }
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

/* Both composers talk to the same endpoint; only the panel they write into
   differs. Wrapped rather than passed by reference, because a listener hands
   the callback a click event and that would arrive as the element id. */
$("explain-send").addEventListener("click", () => askOllieToExplain());
$("explain-input").addEventListener("keydown", (event) => {
  // Enter sends, Shift+Enter makes a new line — the same as the other composers
  // on this page, so the habit carries over.
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    askOllieToExplain();
  }
});

$("learn-ask").addEventListener("click",
  () => askOllieToExplain("learn-question", "learn-answer"));
$("learn-question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    askOllieToExplain("learn-question", "learn-answer");
  }
});

$("subject").addEventListener("change", (e) => chooseSubject(Number(e.target.value)));
$("change-mode").addEventListener("click", showModeChoice);
$("mode-back").addEventListener("click", showStart);
$("pi-back").addEventListener("click", showModeChoice);

/* Reflection: the four "session in detail" panels share one tab strip. */
document.querySelectorAll(".rm-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".rm-tab").forEach((t) => {
      const on = t === tab;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      $(t.dataset.tab).hidden = !on;
    });
  });
});
$("note-toggle").addEventListener("click", toggleNote);
$("learn-to-practice").addEventListener("click", () => startPractice());

/* ---- the voice ------------------------------------------------------- */
$("voice-toggle").addEventListener("click", () => {
  const on = SPEECH.toggleEnabled();
  if (!on) stopSpeaking();
  renderVoiceControls();
  // Turning it on is itself the click a browser requires before it will speak,
  // so the first thing Ollie says can happen right here. One short line, not
  // the lesson: being read a whole page you did not ask for is the thing the
  // toggle exists to prevent.
  if (on) {
    speech.reading = "voice-toggle";
    SPEECH.speak([{ id: null, text: "Right, I'll read it out. Press play whenever you want me to start." }],
                 speechHandlers("voice-toggle"));
  }
});
$("voice-rate").addEventListener("change", (event) => {
  SPEECH.setRate(event.target.value);
});
$("speak-lesson").addEventListener("click",
  () => speakToggle("speak-lesson", lessonSegments(learn.lesson)));
$("speak-note").addEventListener("click",
  () => speakToggle("speak-note", noteSegments(learn.current)));
