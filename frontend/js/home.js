/* Student home — fills in today's date, the name and the streak week.
 * The numbers below are demo values for Nataly; swap STUDENT for an API call
 * once the backend exposes streak and XP. */

(() => {
  const STUDENT = { name: "Nataly", streak: 7, xp: 240 };

  // Shared by every student page (overview, my learning), so each element is
  // optional: a page that doesn't have it simply skips it.
  const $ = (id) => document.getElementById(id) || { set textContent(_) {}, setAttribute() {} };
  const today = new Date();

  $("student-name").textContent = STUDENT.name;
  $("avatar").textContent = STUDENT.name.charAt(0).toUpperCase();
  $("avatar").setAttribute("aria-label", STUDENT.name);
  $("top-streak").textContent = STUDENT.streak;
  $("streak-days").textContent = STUDENT.streak;
  $("top-xp").textContent = STUDENT.xp;
  $("today").textContent = today.toLocaleDateString("en-US",
    { weekday: "long", month: "long", day: "numeric" });

  if (!document.getElementById("week")) return;

  // Week runs Monday → Sunday. Days before today are done, today is the
  // flame, the rest are still ahead.
  const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  const FLAME = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3c.5 2.6-.9 4.2-2.3 5.8C8.3 10.4 7 12 7 14.6 7 17.6 9.2 20 12 20s5-2.4 5-5.3c0-2.1-.9-3.7-1.9-4.8-.3 1.1-.9 1.9-1.8 2.3.3-3.2-.4-6.4-1.3-9.2z"/></svg>';
  const LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
  const todayIdx = (today.getDay() + 6) % 7; // Monday = 0
  const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  document.getElementById("week").innerHTML = LETTERS.map((l, i) => {
    const state = i < todayIdx ? "done" : i === todayIdx ? "today" : "ahead";
    const label = `${names[i]}: ${state === "done" ? "studied" : state === "today" ? "today" : "coming up"}`;
    const inner = state === "done" ? CHECK : state === "today" ? FLAME : "";
    return `<div class="day ${state}" title="${label}">
              <span class="day-l">${l}</span>
              <span class="day-c" role="img" aria-label="${label}">${inner}</span>
            </div>`;
  }).join("");
})();
