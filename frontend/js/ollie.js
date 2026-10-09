/* Ollie — the otter the student actually talks to.
 *
 * MUSE is the system; Ollie is who shows up. The point of a character rather
 * than a panel labelled AI is that "a thing that keeps asking me questions" is
 * far easier to take from someone you recognise, and that a student who is
 * stuck at 1am should feel like somebody is sitting with them.
 *
 * He is an original character, drawn from plain geometry: round head, two
 * small ears, a cream muzzle with whiskers, and a cowlick that moves when he
 * is thinking. Moods are the same face with four things changed — where the
 * pupils look, whether the eyes are open, how the mouth curves, and how far
 * the cowlick leans.
 *
 * He is a study companion, not a friend substitute. He talks about the work,
 * he is glad when it goes well, and he never pretends to feelings he does not
 * have or asks a student to confide in him.
 */

const OLLIE = (() => {

  // mood: [pupil dx, pupil dy, eyes, mouth path, cowlick tilt]
  // eyes: "open" | "happy" (upward arcs) | "squint"
  const MOODS = {
    asking:   [0,    0,    "open",  "M20.5 34.4 q3.5 2.6 7 0",        0],
    thinking: [1.4, -1.4,  "squint","M21 34.6 q3 1.2 6 0",          -11],
    pleased:  [0,    0,    "happy", "M19.6 33.6 q4.4 4.2 8.8 0",      5],
    gentle:   [0,    0.7,  "open",  "M21 34.8 q3 1.4 6 0",           -4],
    cheering: [0,   -0.4,  "happy", "M19.2 33.2 q4.8 5.4 9.6 0",      8],
  };

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  function eyes(kind, dx, dy) {
    if (kind === "happy") {
      // closed, curving up — the shape a real smile makes around the eyes
      return `<path d="M13.6 25.4 q3.4 -3.6 6.8 0" stroke="var(--ollie-ink)"
                    stroke-width="2.1" stroke-linecap="round" fill="none"/>
              <path d="M27.6 25.4 q3.4 -3.6 6.8 0" stroke="var(--ollie-ink)"
                    stroke-width="2.1" stroke-linecap="round" fill="none"/>`;
    }
    const r = kind === "squint" ? 2.5 : 3.4;
    return `<g class="ollie-eyes">
        <circle cx="17" cy="25.5" r="${r}" fill="var(--ollie-ink)"/>
        <circle cx="31" cy="25.5" r="${r}" fill="var(--ollie-ink)"/>
        <circle cx="${(16 + dx).toFixed(2)}" cy="${(24.4 + dy).toFixed(2)}" r="1.15"
                fill="#fff" opacity=".95"/>
        <circle cx="${(30 + dx).toFixed(2)}" cy="${(24.4 + dy).toFixed(2)}" r="1.15"
                fill="#fff" opacity=".95"/>
      </g>`;
  }

  /**
   * @param {string} mood  asking | thinking | pleased | gentle | cheering
   * @param {object} opts  {size, idle}
   */
  function avatar(mood = "asking", opts = {}) {
    const [dx, dy, eyeKind, mouth, tilt] = MOODS[mood] || MOODS.asking;
    const size = opts.size || 34;
    const idle = opts.idle === false ? "" : " is-idle";

    return `<span class="ollie${idle}" style="--ollie-size:${size}px"
                  role="img" aria-label="Ollie the otter">
      <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
        <!-- cowlick: leans when he is thinking -->
        <g class="ollie-tuft" transform="rotate(${tilt} 24 10)">
          <path d="M24 12.5 q-1.2-4 1.6-5.6 q-1 2.6 1.4 3.4"
                stroke="var(--ollie-fur-dark)" stroke-width="2"
                stroke-linecap="round" fill="none"/>
        </g>

        <!-- ears -->
        <circle cx="11.5" cy="15.5" r="5.2" fill="var(--ollie-fur-dark)"/>
        <circle cx="36.5" cy="15.5" r="5.2" fill="var(--ollie-fur-dark)"/>
        <circle cx="11.5" cy="15.5" r="2.3" fill="var(--ollie-inner-ear)"/>
        <circle cx="36.5" cy="15.5" r="2.3" fill="var(--ollie-inner-ear)"/>

        <!-- head -->
        <ellipse cx="24" cy="27" rx="15.5" ry="13.8" fill="var(--ollie-fur)"/>

        <!-- cream muzzle, the thing that makes him read as an otter -->
        <ellipse cx="24" cy="33.5" rx="10.2" ry="7.2" fill="var(--ollie-muzzle)"/>
        <ellipse cx="24" cy="39.2" rx="6.4" ry="2.6" fill="var(--ollie-muzzle)"/>

        ${eyes(eyeKind, dx, dy)}

        <!-- nose -->
        <path d="M21.4 29.6 q2.6-2 5.2 0 q0 2.6-2.6 3.2 q-2.6-.6-2.6-3.2z"
              fill="var(--ollie-ink)"/>
        <!-- mouth -->
        <path d="${mouth}" stroke="var(--ollie-ink)" stroke-width="1.6"
              stroke-linecap="round" fill="none"/>

        <!-- whiskers -->
        <g stroke="var(--ollie-whisker)" stroke-width="1.1" stroke-linecap="round" opacity=".75">
          <path d="M13.2 31.6 L6.6 30"/><path d="M13.2 34 L6.8 34.4"/>
          <path d="M34.8 31.6 L41.4 30"/><path d="M34.8 34 L41.2 34.4"/>
        </g>
      </svg>
    </span>`;
  }

  /** The mood that fits what just happened. */
  function moodFor(kind) {
    if (kind === "cheering" || kind === "solved" || kind === "unlocked") return "cheering";
    if (kind === "pleased" || kind === "progress") return "pleased";
    if (kind === "thinking") return "thinking";
    if (kind === "shortfall" || kind === "hint" || kind === "stuck" ||
        kind === "failing") return "gentle";
    return "asking";
  }

  /* --------------------------------------------------------------- lines */
  /* Short, about the work, and never praise that was not earned. Picked at
     random so he does not say the same sentence every single time. */
  const LINES = {
    gate_start: [
      "No code yet. Just tell me what you're trying to build.",
      "Start wherever you like. I'll ask about the rest.",
    ],
    gate_progress: [
      "That's one more decided. Keep going.",
      "Good — that's a real answer, not a guess.",
    ],
    gate_unlocked: [
      "That's everything. The editor's yours now.",
      "Nicely specified. Go and build it.",
    ],
    coding_start: [
      "Your turn. I won't type ahead of you.",
      "I'm here if you get stuck. Ask me anything about Python.",
    ],
    coding_stuck: [
      "Stuck is fine. Tell me what you expected that line to do.",
      "Say what you thought would happen, then what did. Usually that's the bug.",
    ],
    reflect_clean: [
      "Everything matched what you said you'd build. That's the hard part.",
      "No differences. Your code and your plan agree.",
    ],
    reflect_diffs: [
      "A few things came back different from your plan. Let's look at them.",
      "Some differences to work through. This is the useful bit.",
    ],
  };

  function line(context) {
    const options = LINES[context];
    if (!options) return "";
    return options[Math.floor(Math.random() * options.length)];
  }

  /**
   * A small companion bar: Ollie plus one line. Used where there is no chat
   * but he should still be around — beside the editor, on the reflection page.
   */
  function companion(mood, text, opts = {}) {
    return `<div class="ollie-says${opts.compact ? " compact" : ""}">
        ${avatar(mood, { size: opts.size || 38 })}
        <p class="ollie-line">${esc(text)}</p>
      </div>`;
  }

  return { avatar, moodFor, companion, line, LINES, MOODS };
})();

if (typeof window !== "undefined") window.OLLIE = OLLIE;
