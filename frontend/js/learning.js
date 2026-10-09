/* My learning — the subjects the course already has, as cards.
 *
 * Nothing here is new data: subjects come from /api/courses/{id}/subjects,
 * their lessons are the subject's notes (/api/subjects/{id}/materials), and
 * "Start practice" opens that subject's exercises in the existing
 * three-phase flow at /learn. Which notes count as read is what the Learn page
 * records in this browser when a note is opened.
 */

(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  async function api(path) {
    const res = await fetch(`/api${path}`);
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    return res.json();
  }

  function readNotes(subjectId) {
    try { return JSON.parse(localStorage.getItem(`muse.read.${subjectId}`) || "[]"); }
    catch { return []; }
  }

  // Friendly one-liners for the seeded subjects; anything an instructor adds
  // later falls back to its own description.
  const BLURBS = {
    programming: "One small step toward code that does what you meant.",
    mechanics: "See how things move, one equation at a time.",
    mathematics: "One small step toward fraction confidence.",
    science: "Stay curious about the world around you.",
    english: "Turn your ideas into stories worth sharing.",
  };

  const ICONS = {
    code: '<svg viewBox="0 0 24 24"><path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m13.5 4-3 16"/></svg>',
    calc: '<svg viewBox="0 0 24 24"><rect x="5" y="2.5" width="14" height="19" rx="2"/><rect x="8" y="5.5" width="8" height="3.5" rx=".6"/><circle class="fill" cx="9" cy="12.5" r=".9"/><circle class="fill" cx="12" cy="12.5" r=".9"/><circle class="fill" cx="15" cy="12.5" r=".9"/><circle class="fill" cx="9" cy="15.5" r=".9"/><circle class="fill" cx="12" cy="15.5" r=".9"/><circle class="fill" cx="15" cy="15.5" r=".9"/><circle class="fill" cx="9" cy="18.5" r=".9"/><circle class="fill" cx="12" cy="18.5" r=".9"/><circle class="fill" cx="15" cy="18.5" r=".9"/></svg>',
    flask: '<svg viewBox="0 0 24 24"><path d="M9.5 3h5"/><path d="M10 3v6L5 19a1.5 1.5 0 0 0 1.3 2h11.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7.5 14h9"/></svg>',
    motion: '<svg viewBox="0 0 24 24"><circle cx="16.5" cy="7.5" r="3.5"/><path d="M3 9h7"/><path d="M5 13h6"/><path d="M3 17h10"/><path d="M16.5 13v8"/><path d="m13.5 18 3 3 3-3"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M2.5 5c3-1.3 6.3-1.3 9.5.8v14c-3.2-2.1-6.5-2.1-9.5-.8z"/><path d="M21.5 5c-3-1.3-6.3-1.3-9.5.8v14c3.2-2.1 6.5-2.1 9.5-.8z"/></svg>',
  };
  const TINTS = ["blue", "green", "purple", "orange"];

  function iconFor(subject) {
    const n = (subject.name || "").toLowerCase();
    if (subject.checker === "python" || /program|code|python/.test(n)) return "code";
    if (/mechanic|physic|motion/.test(n)) return "motion";
    if (/math|algebra|fraction/.test(n)) return "calc";
    if (/science|biolog|chem/.test(n)) return "flask";
    return "book";
  }

  const STATE_ICON = {
    done: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle class="disc" cx="12" cy="12" r="9"/><path d="m8 12.3 2.7 2.7L16 9.7"/></svg>',
    next: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.7 2.7L16 9.7"/></svg>',
    later: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><circle cx="12" cy="15.5" r="1"/></svg>',
  };
  const STATE_LABEL = { done: "read", next: "up next", later: "not started" };

  const SHOWN = 3;

  function card(subject, notes, exercises, i) {
    const read = new Set(readNotes(subject.id));
    const nextId = (notes.find((n) => !read.has(n.id)) || {}).id;
    const blurb = BLURBS[(subject.name || "").toLowerCase()] || subject.description || "";

    const items = notes.slice(0, SHOWN).map((n) => {
      const st = read.has(n.id) ? "done" : n.id === nextId ? "next" : "later";
      return `<li class="lesson ${st}">
          <a href="/learn?subject=${subject.id}&mode=learn&note=${n.id}"
             aria-label="${esc(n.title)} (${STATE_LABEL[st]})">
            ${STATE_ICON[st]}<span class="lesson-t">${esc(n.title)}</span>
          </a></li>`;
    }).join("");

    const more = notes.length > SHOWN
      ? `<div class="lessons-more">+${notes.length - SHOWN} more ·
           <a href="/learn?subject=${subject.id}&mode=learn">see all notes</a></div>`
      : "";

    const lessons = notes.length
      ? `<ul class="lessons">${items}</ul>${more}`
      : `<p class="lessons-none">Your instructor hasn't added notes here yet.</p>`;

    const doneCount = notes.filter((n) => read.has(n.id)).length;
    const meta = [
      notes.length ? `${doneCount} of ${notes.length} notes read` : null,
      `${exercises} exercise${exercises === 1 ? "" : "s"}`,
    ].filter(Boolean).join(" · ");

    const button = exercises
      ? `<a class="btn-green" href="/learn?subject=${subject.id}&mode=practice">
           Start practice
           <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>
         </a>`
      : `<span class="btn-green is-off" aria-disabled="true">No exercises yet</span>`;

    return `<article class="subj tint-${TINTS[i % TINTS.length]}">
        <a class="subj-icon" href="/learn?subject=${subject.id}" aria-hidden="true" tabindex="-1">${ICONS[iconFor(subject)]}</a>
        <h2 class="subj-name"><a href="/learn?subject=${subject.id}" title="Learn the material or practise">${esc(subject.name)}</a></h2>
        <p class="subj-blurb">${esc(blurb)}</p>
        ${lessons}
        <div class="subj-foot">
          ${button}
          <a class="subj-choose" href="/learn?subject=${subject.id}&mode=learn">Learn the material →</a>
          <div class="subj-meta">${meta}</div>
        </div>
      </article>`;
  }

  async function load() {
    const box = $("subjects");
    try {
      const [assignments, courses] = await Promise.all([
        api("/assignments"), api("/courses").catch(() => []),
      ]);
      const courseId = (assignments[0] && assignments[0].course_id)
        || (courses[0] && (courses[0].id || courses[0].course_id));
      if (!courseId) {
        box.innerHTML = `<div class="subj-empty">No course yet. Run <code>python -m app.seed</code> to create one.</div>`;
        return;
      }
      const subjects = await api(`/courses/${encodeURIComponent(courseId)}/subjects`);
      if (!subjects.length) {
        box.innerHTML = `<div class="subj-empty">Your course has no subjects yet.</div>`;
        return;
      }
      const notes = await Promise.all(subjects.map((s) =>
        api(`/subjects/${s.id}/materials`).catch(() => [])));
      box.innerHTML = subjects.map((s, i) => {
        const ordered = notes[i].slice().sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
        const exercises = assignments.filter((a) => a.subject_id === s.id).length;
        return card(s, ordered, exercises, i);
      }).join("");
    } catch (err) {
      box.innerHTML = `<div class="subj-empty">Couldn't load your subjects (${esc(err.message)}). Is the server running?</div>`;
    }
  }

  load();
})();
