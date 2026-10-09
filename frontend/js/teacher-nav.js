/* Teacher side: the sidebar shows one dashboard box at a time.
 *
 * Every box is still the same element instructor.js fills in, so nothing about
 * how the data loads changes; this only decides which one is visible. Picking
 * a student in the Students table jumps to their dashboard, and the boxes that
 * are about one student say so when nobody has been picked yet.
 */
(() => {
  const STORE = "muse.teacher.view";
  const STUDENT_VIEWS = new Set(["tv-dashboard", "tv-summary", "tv-history", "tv-diffs", "tv-transcript"]);
  const cards = [...document.querySelectorAll(".dash [data-view]")];
  const items = [...document.querySelectorAll(".teacher-side [data-nav]")];
  const pick = document.getElementById("tv-pick");
  const title = document.getElementById("tv-title");
  const chip = document.getElementById("tv-student");

  const selectedName = () => {
    const row = document.querySelector("#students tr.selected td");
    return row ? row.textContent.trim() : "";
  };

  let current = null;

  // Keep the "Viewing …" chip and the "pick a student" note in step with
  // whichever row is selected, however it got selected (instructor.js picks
  // the most urgent student by itself on load).
  function syncStudent() {
    const name = selectedName();
    chip.hidden = !name;
    chip.textContent = name ? `Viewing ${name}` : "";
    pick.hidden = !(STUDENT_VIEWS.has(current) && !name);
  }
  new MutationObserver(syncStudent).observe(document.getElementById("students"),
    { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });

  function show(view) {
    if (!cards.some((c) => c.dataset.view === view)) view = cards[0].dataset.view;
    current = view;
    cards.forEach((c) => {
      const on = c.dataset.view === view;
      c.hidden = !on;
      if (on && c.tagName === "DETAILS") c.open = true;
    });
    items.forEach((b) => {
      const on = b.dataset.nav === view;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    const active = items.find((b) => b.dataset.nav === view);
    title.textContent = active ? active.dataset.label : "";
    syncStudent();
    try { localStorage.setItem(STORE, view); } catch { /* ignore */ }
    window.scrollTo({ top: 0 });
  }

  items.forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.opens) { document.getElementById(b.dataset.opens).click(); return; }
    if (b.dataset.goto) {          // e.g. "New subject": open that box at its form
      show(b.dataset.goto);
      const field = document.getElementById(b.dataset.focus);
      if (field) { field.scrollIntoView({ block: "center" }); field.focus(); }
      return;
    }
    show(b.dataset.nav);
  }));

  // A box on its own page shouldn't fold shut when its header is clicked.
  document.querySelectorAll(".dash details > summary").forEach((s) =>
    s.addEventListener("click", (e) => e.preventDefault()));

  // Choosing a student in the table opens their dashboard.
  document.getElementById("students").addEventListener("click", (e) => {
    if (!e.target.closest("[data-student]")) return;
    setTimeout(() => show("tv-dashboard"), 0);
  });

  document.getElementById("tv-pick-go").addEventListener("click", () => show("tv-students"));

  let start = null;
  try { start = localStorage.getItem(STORE); } catch { /* ignore */ }
  show(start || "tv-glance");
})();
