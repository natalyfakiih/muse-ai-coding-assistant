/* Chat with Ollie — talks to POST /api/ollie/chat (through the app's LLM
 * client). The conversation lives only in this tab: it is sent with each
 * message and kept in sessionStorage so a reload doesn't lose it, but the
 * server never stores it. */

(() => {
  const NAME = "Nataly";
  const STORE = "muse.chat";
  const $ = (id) => document.getElementById(id);
  const log = $("chat-log"), input = $("chat-input"), send = $("send");

  const GREETING =
    `Hey ${NAME}! I'm Ollie, your learning buddy. 🦦\n` +
    "I know you love learning through pictures and examples. We can untangle a tricky lesson, " +
    "try a little quiz, or make a gentle plan for your week.\nWhat's on your mind today?";

  let history = [];       // [{role: "user"|"assistant", content}]
  let courseId = null;
  // The same student the tutor page is working as (it remembers this as
  // muse.studentId); only used so the teacher can count *how many* students
  // asked about a topic, never what they asked.
  let studentId = null;
  try { studentId = Number(localStorage.getItem("muse.studentId")) || null; } catch { /* ignore */ }
  let busy = false;

  try { history = JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch { history = []; }
  const save = () => { try { sessionStorage.setItem(STORE, JSON.stringify(history.slice(-40))); } catch { /* ignore */ } };

  // ---- tiny, safe formatter: escape first, then **bold**, `code`, ``` blocks, lists
  const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  function format(text) {
    const blocks = [];
    let s = esc(text).replace(/```[a-z]*\n?([\s\S]*?)```/g, (_, code) => {
      blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
      return `\u0000${blocks.length - 1}\u0000`;
    });
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
         .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    const out = [];
    let list = null;
    for (const line of s.split("\n")) {
      const m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (m) { (list = list || []).push(`<li>${m[1]}</li>`); continue; }
      if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
      if (line.trim()) out.push(`<p>${line}</p>`);
    }
    if (list) out.push(`<ul>${list.join("")}</ul>`);
    return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[Number(i)]);
  }

  function bubble(role, text, cls = "") {
    const div = document.createElement("div");
    div.className = `msg ${role === "user" ? "from-me" : "from-ollie"} ${cls}`.trim();
    div.innerHTML = format(text);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function renderAll() {
    log.innerHTML = "";
    bubble("assistant", GREETING);
    history.forEach((t) => bubble(t.role, t.content));
  }

  async function ask(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true; send.disabled = true;
    history.push({ role: "user", content: text });
    save();
    bubble("user", text);
    input.value = ""; autosize();

    const wait = document.createElement("div");
    wait.className = "msg from-ollie";
    wait.innerHTML = '<span class="thinking" aria-label="Ollie is thinking"><i></i><i></i><i></i></span>';
    log.appendChild(wait); log.scrollTop = log.scrollHeight;

    try {
      const res = await fetch("/api/ollie/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student_name: NAME, student_id: studentId, course_id: courseId, messages: history.slice(-12) }),
      });
      const data = await res.json().catch(() => ({}));
      wait.remove();
      if (!res.ok) throw new Error(data.detail || data.error || `The server answered ${res.status}.`);
      history.push({ role: "assistant", content: data.reply });
      save();
      bubble("assistant", data.reply);
      setMode(data.mode);
    } catch (err) {
      wait.remove();
      history.pop(); save();               // let them resend the same message
      bubble("assistant", `I couldn't reach my brain just now (${err.message || "network error"}). Try sending that again.`, "error");
      input.value = text; autosize();
    } finally {
      busy = false; send.disabled = false; input.focus();
    }
  }

  function setMode(mode) {
    const el = $("chat-mode");
    const live = mode === "live";
    el.textContent = live ? "Online" : "Offline demo";
    el.classList.toggle("is-live", live);
    el.title = live ? "Ollie is online" : "No API key set, so Ollie is using the offline stub";
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  function chips(subjects) {
    const first = subjects[0] ? subjects[0].name : "my lesson";
    const list = [`Help me with ${first.toLowerCase()}`, "Quiz me", "Plan my week"];
    $("chips").innerHTML = list.map((c) => `<button type="button" class="chip">${esc(c)}</button>`).join("");
    $("chips").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
  }

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); }
  });
  input.addEventListener("input", autosize);

  renderAll();
  chips([]);

  // Course + live/offline status, both best-effort.
  fetch("/api/health").then((r) => r.json()).then((h) => setMode(h.llm)).catch(() => {});
  fetch("/api/assignments").then((r) => r.json()).then(async (a) => {
    courseId = a[0] && a[0].course_id;
    if (!courseId) return;
    if (!studentId) {
      const students = await fetch(`/api/students?course_id=${encodeURIComponent(courseId)}`).then((r) => r.json());
      studentId = students[0] ? students[0].id : null;
    }
    const subjects = await fetch(`/api/courses/${encodeURIComponent(courseId)}/subjects`).then((r) => r.json());
    chips(subjects);
  }).catch(() => {});
})();
