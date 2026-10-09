/* Ollie reading out loud.
 *
 * This uses the browser's own speech engine (the Web Speech API), not a text-to-
 * speech service. That decision is worth stating plainly, because the hosted
 * voices do sound better: a service means an API key, a per-character bill, a
 * network round trip before the first word, and one more thing that stops
 * working when a key expires the morning of a demo. The browser engine costs
 * nothing, starts instantly, works with the Wi-Fi off, and on Windows and macOS
 * the modern system voices are good. If a hosted voice is ever wanted, only
 * `speak()` below has to change; nothing that calls it would notice.
 *
 * Three things about this API bite everyone who uses it, and all three are
 * handled here rather than discovered later:
 *
 *   1. getVoices() returns an empty list on the first call in Chrome. The list
 *      arrives later on a `voiceschanged` event, so anything that reads it at
 *      startup silently gets the wrong voice forever.
 *   2. A long utterance stops dead after roughly fifteen seconds in Chrome.
 *      The usual workaround is a timer that pauses and resumes to reset the
 *      clock; chunking into sentences avoids the problem instead of fighting
 *      it, and gives sentence-level highlighting for free.
 *   3. cancel() immediately followed by speak() can leave the queue wedged, so
 *      a new reading starts a tick after the old one is cleared.
 *
 * It never starts on its own. Speech that begins without being asked for is
 * startling in a library and useless to everyone else, and browsers block it
 * without a click anyway.
 */

const SPEECH = (() => {

  const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
  const KEY = "muse.voice";
  const RATE_KEY = "muse.voiceRate";

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v == null ? fallback : v; }
      catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, String(value)); } catch { /* private mode */ } },
  };

  const state = {
    // Off until the student turns it on. An accessibility feature that shouts
    // at people who did not ask for it is not an accessibility feature.
    on: store.get(KEY, "off") === "on",
    rate: Number(store.get(RATE_KEY, "0.95")) || 0.95,
    voice: null,
    queue: [],
    index: 0,
    speaking: false,
    paused: false,
    onSegment: null,
    onState: null,
  };

  const supported = () => Boolean(synth && typeof SpeechSynthesisUtterance === "function");

  /* ------------------------------------------------------------- voices */
  /* Preference order, best first. The Edge "Natural"/"Online" voices are
     neural and markedly better than the old local ones; everything after them
     is a decent local fallback, and the OS default wins if none match. */
  const PREFERRED = ["natural", "online", "google", "samantha", "aria", "jenny", "libby"];

  function pickVoice() {
    if (!supported()) return null;
    const all = synth.getVoices().filter((v) => /^en\b|^en-/i.test(v.lang));
    if (!all.length) return null;
    for (const want of PREFERRED) {
      const found = all.find((v) => v.name.toLowerCase().includes(want));
      if (found) return found;
    }
    return all.find((v) => v.default) || all[0];
  }

  function refreshVoice() { state.voice = pickVoice(); }

  if (supported()) {
    refreshVoice();
    // The list is usually empty on the first call; this is when it really lands.
    synth.addEventListener("voiceschanged", refreshVoice);
    // A page left mid-sentence otherwise keeps talking into the next one.
    window.addEventListener("beforeunload", () => synth.cancel());
    window.addEventListener("pagehide", () => synth.cancel());
  }

  /* ---------------------------------------------------------- the words */
  /* Said out loud, "v = u + a t" is read by most engines as four stray letters.
     Spelling the relations out is the difference between an equation a student
     can follow with their eyes shut and a noise. Applied only where the text is
     actually algebra — the caller says so. */
  const SYMBOLS = [
    [/\s*\^\s*2\b/g, " squared"],
    [/\s*\^\s*3\b/g, " cubed"],
    [/\s*\^\s*(-?\d+)/g, " to the power $1"],
    [/\s*=\s*/g, " equals "],
    [/\s*≤\s*/g, " is less than or equal to "],
    [/\s*≥\s*/g, " is greater than or equal to "],
    [/\s*<\s*/g, " is less than "],
    [/\s*>\s*/g, " is greater than "],
    [/\s*\+\s*/g, " plus "],
    [/(\d)\s*-\s*(\d)/g, "$1 minus $2"],
    [/\s*\*\s*/g, " times "],
    [/(\d)\s*\/\s*(\d)/g, "$1 over $2"],
    [/\bm\/s\b/g, " metres per second"],
  ];

  function forSpeech(text, isFormula) {
    let out = String(text || "")
      // The offline stub's marker is a note to whoever is testing, not a thing
      // to read to a student.
      .replace(/\[offline stub\]\s*/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    if (isFormula) {
      for (const [pattern, word] of SYMBOLS) out = out.replace(pattern, word);
      out = out.replace(/\s+/g, " ").trim();
    }
    return out;
  }

  /* Sentences, so nothing runs past the engine's cutoff and the highlight can
     keep up. A very long sentence is split again at a comma rather than being
     sent whole. */
  function sentences(text) {
    // Full stops, questions and exclamations only. A colon was in this list
    // once, and "Constant acceleration: the three equations" came out as two
    // utterances with a gap in the middle of the title.
    const rough = String(text).split(/(?<=[.!?])\s+/).filter(Boolean);
    const out = [];
    for (const part of rough) {
      if (part.length <= 220) { out.push(part); continue; }
      let buffer = "";
      for (const piece of part.split(/(?<=,)\s+/)) {
        if ((buffer + piece).length > 220 && buffer) { out.push(buffer.trim()); buffer = ""; }
        buffer += piece + " ";
      }
      if (buffer.trim()) out.push(buffer.trim());
    }
    return out;
  }

  /* -------------------------------------------------------------- speak */
  function announce() { if (state.onState) state.onState(status()); }

  function status() {
    return { on: state.on, speaking: state.speaking, paused: state.paused,
             rate: state.rate, voice: state.voice ? state.voice.name : null };
  }

  /**
   * Read a list of blocks aloud, in order.
   * @param {Array} segments  [{ id, text, formula? }] — id is handed back as
   *                          each block starts, so the caller can highlight it.
   * @param {object} opts     { onSegment(id|null), onState(status) }
   */
  function speak(segments, opts = {}) {
    if (!supported() || !state.on) return false;

    // The previous caller's callbacks are dropped BEFORE anything is
    // cancelled. Leave them attached and stop()'s "finished" notification goes
    // to whoever was reading last — which, when the same button is pressed
    // again, is the very caller now starting up. It would tear down its own
    // reading a moment after beginning it, and the symptom is a pause button
    // that silently restarts from the top instead of pausing.
    state.onSegment = null;
    state.onState = null;
    stop();

    state.queue = [];
    (segments || []).forEach((seg) => {
      const text = forSpeech(seg.text, seg.formula);
      if (!text) return;
      sentences(text).forEach((sentence, i) => {
        state.queue.push({ id: seg.id, text: sentence, first: i === 0 });
      });
    });
    if (!state.queue.length) return false;

    state.index = 0;
    state.speaking = true;
    state.paused = false;
    state.onSegment = opts.onSegment || null;
    state.onState = opts.onState || null;
    // A tick after cancel(): starting in the same turn can wedge the queue.
    setTimeout(next, 60);
    announce();
    return true;
  }

  function next() {
    if (!state.speaking) return;
    const item = state.queue[state.index];
    if (!item) {
      state.speaking = false;
      if (state.onSegment) state.onSegment(null);
      announce();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(item.text);
    if (state.voice) utterance.voice = state.voice;
    utterance.rate = state.rate;
    utterance.pitch = 1;
    utterance.onstart = () => {
      if (item.first && state.onSegment) state.onSegment(item.id);
    };
    utterance.onend = () => { state.index += 1; next(); };
    utterance.onerror = (event) => {
      // "interrupted" and "canceled" are what stop() looks like from in here,
      // and are not failures. Anything else: skip the sentence rather than
      // stranding the rest of the lesson behind it.
      if (event.error === "interrupted" || event.error === "canceled") return;
      state.index += 1;
      next();
    };
    synth.speak(utterance);
  }

  function stop() {
    if (!supported()) return;
    state.speaking = false;
    state.paused = false;
    state.queue = [];
    state.index = 0;
    synth.cancel();
    if (state.onSegment) state.onSegment(null);
    announce();
  }

  function pause() {
    if (!supported() || !state.speaking || state.paused) return;
    synth.pause();
    state.paused = true;
    announce();
  }

  function resume() {
    if (!supported() || !state.paused) return;
    synth.resume();
    state.paused = false;
    announce();
  }

  function toggleEnabled(on) {
    state.on = on == null ? !state.on : Boolean(on);
    store.set(KEY, state.on ? "on" : "off");
    if (!state.on) stop();
    announce();
    return state.on;
  }

  function setRate(rate) {
    state.rate = Math.min(2, Math.max(0.5, Number(rate) || 0.95));
    store.set(RATE_KEY, state.rate);
    // Rate is fixed for the life of an utterance, so a change mid-sentence
    // only takes effect from the next one. Restarting the current block would
    // be worse than waiting one sentence.
    announce();
    return state.rate;
  }

  return { supported, speak, stop, pause, resume, status, toggleEnabled,
           setRate, forSpeech, sentences, pickVoice,
           get enabled() { return state.on; },
           get rate() { return state.rate; } };
})();

if (typeof window !== "undefined") window.SPEECH = SPEECH;
