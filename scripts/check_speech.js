#!/usr/bin/env node
/* Checks on what Ollie actually says out loud.
 *
 * The speech module is browser code, so there is no pytest for it, but the two
 * things most worth protecting are pure text handling and run fine in node:
 *
 *   - Equations have to be spoken as words. "v = u + a t" read by a speech
 *     engine as four stray letters is noise, and a maths course is exactly
 *     where this feature earns its keep.
 *   - Long text is chunked into sentences, because an utterance much longer
 *     than this stops dead partway through in Chrome. The split is on full
 *     stops only: a colon was in that list once, and "Constant acceleration:
 *     the three equations" came out as two utterances with a gap in the
 *     middle of the title.
 *
 * Run:  node scripts/check_speech.js
 */
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "frontend", "js", "speech.js"), "utf8");
const SPEECH = eval(`${source}; SPEECH`);   // no window here, so it loads inert

let failures = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) console.log(`       got  ${JSON.stringify(got)}\n       want ${JSON.stringify(want)}`);
}

// --- what gets said -------------------------------------------------------
check("the offline stub's marker is not read to a student",
  SPEECH.forSpeech("[offline stub] Here is what free fall is for."),
  "Here is what free fall is for.");

check("prose is left exactly as written",
  SPEECH.forSpeech("An object falling freely accelerates downward.", false),
  "An object falling freely accelerates downward.");

check("an equation is spoken as words",
  SPEECH.forSpeech("v = u + a t", true), "v equals u plus a t");

check("powers are spoken",
  SPEECH.forSpeech("v^2 = u^2 + 2 a s", true),
  "v squared equals u squared plus 2 a s");

check("a fraction is spoken",
  SPEECH.forSpeech("s = u t + (1/2) a t^2", true),
  "s equals u t plus (1 over 2) a t squared");

check("units are spoken",
  SPEECH.forSpeech("g = 9.81 m/s^2", true),
  "g equals 9.81 metres per second squared");

check("prose keeps its symbols, since it is not being read as algebra",
  SPEECH.forSpeech("It accelerates at g = 9.81 m/s^2.", false),
  "It accelerates at g = 9.81 m/s^2.");

// --- how it is broken up --------------------------------------------------
check("sentences split on full stops, questions and exclamations",
  SPEECH.sentences("One. Two! Three? Four."), ["One.", "Two!", "Three?", "Four."]);

check("a colon does not split a title",
  SPEECH.sentences("Constant acceleration: the three equations"),
  ["Constant acceleration: the three equations"]);

const long = "a, ".repeat(160) + "end.";
const chunks = SPEECH.sentences(long);
const longest = Math.max(...chunks.map((c) => c.length));
check("a long sentence is broken up rather than sent whole",
  chunks.length > 1 && longest <= 240, true);

// --- it stays switched off where there is no engine -----------------------
check("it reports itself unsupported outside a browser", SPEECH.supported(), false);
check("speaking is a no-op when there is no engine",
  SPEECH.speak([{ id: "x", text: "hello" }]), false);

console.log(failures ? `\n${failures} failing` : "\nall speech checks pass");
process.exit(failures ? 1 : 0);
