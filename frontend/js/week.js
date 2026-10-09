/* My week — one small session a day, Monday to Sunday.
 *
 * The plan uses the course's real subjects and notes: each entry names a
 * subject and a note title (or an exercise), and the links are resolved
 * against the API so a row opens exactly that lesson in /learn. Days before
 * today show as done, today gets a Start button, the rest are coming up.
 * The plan itself is demo data; swap PLAN for an API call later.
 */

(() => {
  const PLAN = [
    { subject: "Programming", note: "What a function hands back", title: "Find out what a function hands back", minutes: 10 },
    { subject: "Mechanics", note: "Constant acceleration: the three equations", title: "Meet the three equations of motion", minutes: 10 },
    { subject: "Programming", note: "Deciding the edge cases before you write code", title: "Decide the edge cases first", minutes: 10 },
    { subject: "Mechanics", note: "Free fall near the Earth's surface", title: "Explore free fall", minutes: 10 },
    { subject: "Programming", practice: true, title: "Try a little programming practice", minutes: 15 },
    { subject: "Mechanics", note: "Units, exactness and how answers are marked", title: "Get your units right", minutes: 10 },
    { rest: true, title: "Rest and recharge", text: "No plan today. Rest is part of learning too." },
  ];

  const ICONS = {
    code: '<svg viewBox="0 0 24 24"><path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m13.5 4-3 16"/></svg>',
    motion: '<svg viewBox="0 0 24 24"><circle cx="16.5" cy="7.5" r="3.5"/><path d="M3 9h7"/><path d="M5 13h6"/><path d="M3 17h10"/><path d="M16.5 13v8"/><path d="m13.5 18 3 3 3-3"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M2.5 5c3-1.3 6.3-1.3 9.5.8v14c-3.2-2.1-6.5-2.1-9.5-.8z"/><path d="M21.5 5c-3-1.3-6.3-1.3-9.5.8v14c3.2-2.1 6.5-2.1 9.5-.8z"/></svg>',
    moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  };
  const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  const ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>';

  const esc = (t) => String(t == null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  const iconFor = (name) => /program|code|python/i.test(name) ? "code" : /mechanic|physic|motion/i.test(name) ? "motion" : "book";

  // This week, Monday to Sunday.
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const todayIdx = (today.getDay() + 6) % 7;
  const monday = new Date(today); monday.setDate(today.getDate() - todayIdx);
  const days = PLAN.map((_, i) => { const d = new Date(monday); d.setDate(monday.getDate() + i); return d; });

  const fmt = (d, o) => d.toLocaleDateString("en-US", o);
  const first = days[0], last = days[6];
  document.getElementById("week-range").textContent = first.getMonth() === last.getMonth()
    ? `${fmt(first, { month: "long" })} ${first.getDate()}–${last.getDate()}`
    : `${fmt(first, { month: "short", day: "numeric" })} – ${fmt(last, { month: "short", day: "numeric" })}`;

  function render(links, tints) {
    document.getElementById("plan").innerHTML = PLAN.map((item, i) => {
      const d = days[i];
      const state = i < todayIdx ? "done" : i === todayIdx ? "today" : "later";
      const when = `<div class="when"><span class="dow">${fmt(d, { weekday: "short" })}</span>
                    <span class="num">${d.getDate()}</span></div>`;

      if (item.rest) {
        return `<li class="day-row is-rest${state === "today" ? " is-today" : ""}">${when}
            <div class="task tint-rest">
              <div class="task-icon" aria-hidden="true">${ICONS.moon}</div>
              <h2 class="task-title">${esc(item.title)}</h2>
              <div class="task-meta">${esc(item.text)}</div>
            </div><span></span></li>`;
      }

      const href = links[i] || "/learning";
      const status = { done: "Completed", today: "Today", later: "Coming up" }[state];
      const kind = item.practice ? "Exercise" : "Note";
      const right = state === "done"
        ? `<span class="status done">${CHECK}Done</span>`
        : state === "today"
          ? `<a class="btn-green" href="${href}">Start ${ARROW}</a>`
          : `<span class="status later">Coming up</span>`;

      return `<li class="day-row${state === "today" ? " is-today" : ""}">${when}
          <div class="task tint-${tints[item.subject] || "blue"}">
            <div class="task-icon" aria-hidden="true">${ICONS[iconFor(item.subject)]}</div>
            <h2 class="task-title"><a href="${href}">${esc(item.title)}</a></h2>
            <div class="task-meta">${esc(item.subject)} · ${kind} · ${item.minutes} minutes · ${status}</div>
          </div>
          ${right}
        </li>`;
    }).join("");
  }

  // Draw straight away with fallback links, then point each row at the real
  // note or exercise once the subjects have loaded.
  render([], {});

  (async () => {
    try {
      const get = (p) => fetch(`/api${p}`).then((r) => { if (!r.ok) throw new Error(p); return r.json(); });
      const assignments = await get("/assignments");
      const courseId = assignments[0] && assignments[0].course_id;
      if (!courseId) return;
      const subjects = await get(`/courses/${encodeURIComponent(courseId)}/subjects`);
      const tintList = ["blue", "green", "purple", "orange"];
      const tints = {}; subjects.forEach((s, i) => { tints[s.name] = tintList[i % tintList.length]; });
      const notes = {};
      await Promise.all(subjects.map(async (s) => {
        notes[s.id] = await get(`/subjects/${s.id}/materials`).catch(() => []);
      }));
      const links = PLAN.map((item) => {
        const s = subjects.find((x) => x.name === item.subject);
        if (!s) return null;
        if (item.practice) return `/learn?subject=${s.id}&mode=practice`;
        const n = (notes[s.id] || []).find((m) => m.title === item.note);
        return n ? `/learn?subject=${s.id}&mode=learn&note=${n.id}` : `/learn?subject=${s.id}&mode=learn`;
      });
      render(links, tints);
    } catch { /* keep the fallback links */ }
  })();
})();
