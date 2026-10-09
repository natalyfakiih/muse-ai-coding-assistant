/* My progress — growth skills.
 *
 * Every score is part ÷ whole from the counts in its caption, so a student can
 * always see where the number came from. These counts are demo values; when the
 * backend exposes them (sessions, school days practised, retries), replace
 * SKILLS with an API call and keep the same shape.
 */

(() => {
  const SKILLS = [
    {
      name: "Focus", tint: "blue", icon: "target",
      blurb: "Finding your rhythm, one practice at a time.",
      part: 8, whole: 11,
      how: (p, w) => `Based on ${p} of ${w} practice sessions completed without a break. Not a measure of attention or ability.`,
    },
    {
      name: "Consistency", tint: "purple", icon: "brain",
      blurb: "Little habits make a big difference.",
      part: 8, whole: 10,
      how: (p, w) => `Based on practice on ${p} of the last ${w} school days. Missed days never define you.`,
    },
    {
      name: "Persistence", tint: "green", icon: "heart",
      blurb: "You keep trying, even when it's tricky.",
      part: 9, whole: 10,
      how: (p, w) => `Based on retrying ${p} of ${w} initially incorrect questions. Every new attempt counts.`,
    },
  ];

  const ICONS = {
    target: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="2"/></svg>',
    brain: '<svg viewBox="0 0 24 24"><path d="M12 5.5a3 3 0 0 0-5.6-1.4A3 3 0 0 0 4 8.6a3.2 3.2 0 0 0 .3 5.6A3 3 0 0 0 7 19a3 3 0 0 0 5 1.2z"/><path d="M12 5.5a3 3 0 0 1 5.6-1.4A3 3 0 0 1 20 8.6a3.2 3.2 0 0 1-.3 5.6A3 3 0 0 1 17 19a3 3 0 0 1-5 1.2z"/><path d="M12 5.5v14.7"/><path d="M8 9.5c1 .2 1.8.8 2.2 1.7"/><path d="M16 9.5c-1 .2-1.8.8-2.2 1.7"/><path d="M8.5 15c.9-.4 1.9-.3 2.6.3"/><path d="M15.5 15c-.9-.4-1.9-.3-2.6.3"/></svg>',
    heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/></svg>',
  };

  const box = document.getElementById("skills");
  box.innerHTML = SKILLS.map((s) => {
    const score = s.whole ? Math.floor((s.part / s.whole) * 100) : 0;
    return `<article class="skill tint-${s.tint}">
        <div class="skill-icon" aria-hidden="true">${ICONS[s.icon]}</div>
        <h2 class="skill-name">${s.name}</h2>
        <p class="skill-blurb">${s.blurb}</p>
        <div class="skill-score"><b>${score}</b><span>/ 100</span></div>
        <div class="bar" role="meter" aria-label="${s.name}" aria-valuemin="0"
             aria-valuemax="100" aria-valuenow="${score}"><i data-w="${score}"></i></div>
        <p class="skill-how">${s.how(s.part, s.whole)}</p>
      </article>`;
  }).join("");

  // The comeback story. Demo moments for now; same shape an API would return.
  const NAME = "Nataly";
  const MOMENTS = [
    { date: "2026-10-02", title: "A grade drop, not a dead end",
      text: "Math dropped from 76 to 68. MUSE explained the missing practice and fraction questions." },
    { date: "2026-10-05", title: "A kind nudge and a clear next step",
      text: "A 5-minute visual practice with Ollie made common denominators click." },
    { date: "2026-10-09", title: "Confidence is coming back",
      text: `${NAME} is practicing regularly. Her overall average is up 4 points this month.` },
  ];
  const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  document.getElementById("story-name").textContent = NAME;
  document.getElementById("timeline").innerHTML = MOMENTS.map((m) => {
    const d = new Date(`${m.date}T12:00:00`);
    const label = d.toLocaleDateString("en-US", { month: "short", day: "numeric" }).toUpperCase();
    return `<li class="moment">
        <time datetime="${m.date}">${label}</time>
        <div><h3>${esc(m.title)}</h3><p>${esc(m.text)}</p></div>
      </li>`;
  }).join("");

  // Fill the bars after first paint so they grow in.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    box.querySelectorAll(".bar i").forEach((bar) => { bar.style.width = `${bar.dataset.w}%`; });
  }));
})();
