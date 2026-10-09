/* Chart kit for the MUSE dashboards.
 *
 * Bars are HTML/CSS rather than SVG: a bar chart is a row of labelled
 * rectangles, and building it in HTML means text never collides, it reflows at
 * phone width for free, and it inherits the theme tokens. SVG is used only for
 * the one thing HTML cannot do cleanly, the retry timeline.
 *
 * Colour rules this file follows:
 *   - The two-series pair (specification / coding) is a validated categorical
 *     pair: dark #4287d4 + #b5853c, light #2b56c4 + #9a6a10. CVD separation
 *     dE 20.5 protan, normal-vision 23.1, both >= 3:1 on their surface.
 *   - drift / revision / bug keep the product's status colours and are always
 *     shipped with a text label, never colour alone.
 *   - Every value is printed as text beside its bar, so the chart is readable
 *     without seeing colour at all.
 */

const MUSE_CHARTS = (() => {

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  /** Seconds -> something a human reads at a glance. */
  function human(seconds) {
    if (seconds == null || seconds === 0) return "—";
    if (seconds < 90) return seconds + "s";
    const minutes = seconds / 60;
    if (minutes < 90) return Math.round(minutes) + " min";
    return (minutes / 60).toFixed(1) + " h";
  }

  function pct(part, whole) {
    if (!whole) return "0%";
    return Math.round((part / whole) * 100) + "%";
  }

  /* ---------------------------------------------------------------- bars */
  /**
   * Horizontal bars, one row per item. Single series, so no legend: the row
   * label names each bar directly.
   *
   * rows: [{ label, value, display?, tone?, note? }]
   * opts: { max?, reference?: {value, label}, empty? }
   */
  function bars(rows, opts = {}) {
    if (!rows || !rows.length) return emptyState(opts.empty || "Nothing recorded yet");
    const max = opts.max || Math.max(...rows.map(r => r.value || 0), 1);
    const ref = opts.reference && opts.reference.value
      ? Math.min(100, (opts.reference.value / max) * 100) : null;

    const body = rows.map(r => {
      const width = Math.max(1.5, ((r.value || 0) / max) * 100);
      const tone = r.tone ? ` t-${r.tone}` : "";
      const title = `${r.label}: ${r.display != null ? r.display : r.value}`;
      return `<div class="ch-row" title="${esc(title)}">
          <span class="ch-label">${esc(r.label)}</span>
          <span class="ch-track">
            <span class="ch-fill${tone}" style="width:${width.toFixed(1)}%"></span>
            ${ref != null ? `<span class="ch-ref" style="left:${ref.toFixed(1)}%"></span>` : ""}
          </span>
          <span class="ch-value">${esc(r.display != null ? r.display : r.value)}</span>
        </div>`;
    }).join("");

    const legend = opts.reference && opts.reference.value
      ? `<p class="ch-note"><span class="ch-ref-key"></span>${esc(opts.reference.label)}</p>` : "";
    return `<div class="ch">${body}${legend}</div>`;
  }

  /**
   * One stacked bar: parts of a single whole. Two or more series, so a legend
   * is always present and every segment is also directly labelled with its own
   * value underneath.
   *
   * parts: [{ label, value, display?, tone }]
   */
  function stack(parts, opts = {}) {
    const live = (parts || []).filter(p => (p.value || 0) > 0);
    if (!live.length) return emptyState(opts.empty || "Nothing recorded yet");
    const total = live.reduce((sum, p) => sum + p.value, 0);

    const segments = live.map(p => {
      const share = (p.value / total) * 100;
      const title = `${p.label}: ${p.display != null ? p.display : p.value} (${pct(p.value, total)})`;
      return `<span class="ch-seg t-${esc(p.tone)}" style="flex:0 0 ${share.toFixed(2)}%"
                    title="${esc(title)}"></span>`;
    }).join("");

    const keys = live.map(p => `<span class="ch-key">
        <i class="t-${esc(p.tone)}"></i>${esc(p.label)}
        <b>${esc(p.display != null ? p.display : p.value)}</b>
        <em>${pct(p.value, total)}</em>
      </span>`).join("");

    return `<div class="ch">
        <div class="ch-stack">${segments}</div>
        <div class="ch-keys">${keys}</div>
      </div>`;
  }

  /**
   * One value measured against the class. The bar is the student, the tick is
   * the class, and the gap between them is the whole point.
   */
  function compare(label, value, classValue, opts = {}) {
    const fmt = opts.format || (v => v);
    if (value == null) return emptyState(opts.empty || "Not enough data yet");
    const max = Math.max(value, classValue || 0) * 1.15 || 1;
    const width = Math.max(1.5, (value / max) * 100);
    const tick = classValue ? (classValue / max) * 100 : null;
    const above = classValue != null && value > classValue;

    return `<div class="ch ch-compare">
        <div class="ch-row">
          <span class="ch-label">${esc(label)}</span>
          <span class="ch-track">
            <span class="ch-fill${above ? " t-over" : ""}" style="width:${width.toFixed(1)}%"></span>
            ${tick != null ? `<span class="ch-ref" style="left:${Math.min(100, tick).toFixed(1)}%"></span>` : ""}
          </span>
          <span class="ch-value">${esc(fmt(value))}</span>
        </div>
        ${classValue != null ? `<p class="ch-note"><span class="ch-ref-key"></span>class ${esc(fmt(classValue))}</p>` : ""}
      </div>`;
  }

  /**
   * Attempts along a time axis. The dots are submissions, the spacing is the
   * real gap between them, and a filled dot is the one that came back clean.
   */
  /**
   * Attempts along a time axis. The dots are submissions, the spacing is the
   * real gap between them, and the last dot is filled when that attempt came
   * back clean.
   *
   * Built from positioned HTML rather than SVG: a viewBox that stretches to the
   * container width squashes circles into ovals, and a circle that is not round
   * reads as a bug in the chart.
   */
  function timeline(gaps, opts = {}) {
    const count = opts.attempts || 0;
    if (!count) return emptyState(opts.empty || "No submissions yet");

    const offsets = [0];
    (gaps || []).forEach(g => offsets.push(offsets[offsets.length - 1] + (g || 0)));
    while (offsets.length < count) offsets.push(offsets[offsets.length - 1] + 600);
    const span = offsets[count - 1] || 1;

    const dots = offsets.slice(0, count).map((o, i) => {
      const left = count === 1 ? 0 : (o / span) * 100;
      const last = i === count - 1;
      const clean = last && opts.solved;
      const title = i === 0
        ? "attempt 1"
        : `attempt ${i + 1} · ${human(gaps[i - 1])} after the previous one`;
      return `<span class="ch-dot${clean ? " is-clean" : ""}"
                    style="left:${left.toFixed(2)}%" title="${esc(title)}">
                <b>${i + 1}</b>
              </span>`;
    }).join("");

    const mean = gaps && gaps.length
      ? Math.round(gaps.reduce((a, b) => a + b, 0) / gaps.length) : null;
    return `<div class="ch">
        <div class="ch-line" role="img"
             aria-label="${count} submission${count === 1 ? "" : "s"}">${dots}</div>
        <p class="ch-note">${count} submission${count === 1 ? "" : "s"}${
          mean ? " · " + human(mean) + " apart on average" : ""}</p>
      </div>`;
  }


  /* ---------------------------------------------------------------- meter */
  /**
   * One ratio against its limit. The sanctioned form for a single proportion:
   * a 2-slice donut comparing two categories is an anti-pattern, but a meter
   * showing how far along one value is against its own maximum is not, and the
   * number sits in the middle so it never rests on colour.
   *
   * The unfilled track is a lighter step of the SAME hue as the fill, not a
   * neutral grey, so the state reads across the whole dial.
   */
  function meter(label, value, total, opts = {}) {
    if (value == null || !total) {
      return `<div class="ch-meter is-empty">
          <div class="ch-ring" style="--v:0"><span class="ch-ring-v">—</span></div>
          <span class="ch-meter-l">${esc(label)}</span>
        </div>`;
    }
    const share = Math.max(0, Math.min(100, (value / total) * 100));
    // Severity runs with the meaning of the number, which is not always
    // "more is better" — the caller says which way round it is.
    const good = opts.invert ? share <= 40 : share >= 65;
    const mid = opts.invert ? share <= 65 : share >= 40;
    const tone = good ? "ok" : mid ? "warn" : "bad";
    const caption = opts.caption != null ? opts.caption : `${value} of ${total}`;

    return `<div class="ch-meter t-${tone}" title="${esc(label)}: ${esc(caption)}">
        <div class="ch-ring" style="--v:${share.toFixed(1)}">
          <span class="ch-ring-v">${Math.round(share)}<i>%</i></span>
        </div>
        <span class="ch-meter-l">${esc(label)}</span>
        <span class="ch-meter-c">${esc(caption)}</span>
      </div>`;
  }

  /* ------------------------------------------------------------ diverging */
  /**
   * Strengths and weaknesses as one picture. Bars grow right from a neutral
   * midpoint when an area is solid and left when it needs work, which turns a
   * sorted list into a shape you can read in one glance.
   *
   * Validated diverging pair: blue (solid) <-> red (needs work), neutral grey
   * midpoint. Two hues reading as opposites, per the diverging rule; a single
   * hue would make "weak" look like "less strong" rather than its own state.
   *
   * rows: [{ label, score }]  score 0..1
   */
  function diverging(rows, opts = {}) {
    if (!rows || !rows.length) return emptyState(opts.empty || "Nothing to score yet");
    const pivot = opts.pivot == null ? 0.6 : opts.pivot;
    const reach = Math.max(pivot, 1 - pivot);

    const body = rows.map(r => {
      const delta = r.score - pivot;
      const width = (Math.abs(delta) / reach) * 50;
      const solid = delta >= 0;
      return `<div class="ch-div-row" title="${esc(r.label)}: ${Math.round(r.score * 100)}%">
          <span class="ch-label">${esc(r.label)}</span>
          <span class="ch-div-track">
            <span class="ch-div-axis"></span>
            <span class="ch-div-bar ${solid ? "is-solid" : "is-weak"}"
                  style="${solid ? "left:50%" : `right:50%`};width:${Math.max(1, width).toFixed(1)}%"></span>
          </span>
          <span class="ch-value">${Math.round(r.score * 100)}%</span>
        </div>`;
    }).join("");

    return `<div class="ch ch-div">
        <div class="ch-div-head">
          <span><i class="k-weak"></i>${esc(opts.leftLabel || "needs work")}</span>
          <span><i class="k-solid"></i>${esc(opts.rightLabel || "solid")}</span>
        </div>
        ${body}
      </div>`;
  }

  /** Class distribution across the status tiers. */
  function tiers(distribution, opts = {}) {
    const order = [
      ["on_track", "On track", "ok"],
      ["needs_attention", "Needs attention", "warn"],
      ["at_risk", "At risk", "bad"],
      ["not_started", "Not started", "mute"],
    ];
    const parts = order
      .map(([key, label, tone]) => ({ label, value: distribution[key] || 0, tone }))
      .filter(p => p.value > 0);
    return stack(parts, opts);
  }

  function emptyState(message) {
    return `<p class="ch-empty">${esc(message)}</p>`;
  }

  return { bars, stack, compare, timeline, tiers, meter, diverging,
           human, pct, emptyState };
})();

if (typeof window !== "undefined") window.MUSE_CHARTS = MUSE_CHARTS;
