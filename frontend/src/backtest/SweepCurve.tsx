import { useState } from "react";

import {
  agreesUnderBandRule,
  fmtDelta,
  fmtMetric,
  plateauLine,
  readSweep,
  statusLine,
  verdictHeadline,
  verdictQualifier,
} from "./sweep";

// THE SWEEP — a curve, never a winner.
//
// The whole surface is built so that "which setting won?" has no answer on it. Points render in the
// sweep's own dial order (never sorted by outcome), no row is highlighted for being highest, and what
// is marked instead is the PLATEAU: the contiguous band the backend computed. A plateau one point wide
// is the honest null result and says so in words.
//
// HIERARCHY (issue #3). The open section now reads top-to-bottom: a loud one-line VERDICT (band or no
// band, distilled from the SAME plateau the table marks), a compact chip row of what the curve IS, the
// methodology folded behind a "How to read this" toggle (the guardrails are FOLDED, never deleted — one
// click away, default shut), then the table. The verdict is a faithful distillation, never a softening:
// "No band — nothing found" stays unambiguous, and nothing here names or highlights a best point.
//
// Sub-window sign agreement rides every row for the same reason. A delta that reverses when the window
// is cut in half is a number that found nothing, and putting that beside the delta is what stops the
// delta from being read alone.
//
// LATEST-ONLY: a store holds one `sweep.json`, so a second sweep overwrites the first. Each point cites
// its own run_id and those runs stay addressable, so the evidence survives even though the curve does
// not. Said here because a reader comparing this curve to one they saw last week deserves to know it
// is not the same artifact.

export function SweepCurve({ sweep }: { sweep: unknown }) {
  const [open, setOpen] = useState(false);
  // The methodology is a SECOND, independent fold, default collapsed — the caveats are guardrails, so
  // they are folded rather than deleted, and never in the reader's way of the verdict and the table.
  const [howOpen, setHowOpen] = useState(false);
  const v = readSweep(sweep);
  if (!v) return null;
  const nWindows = v.windows.length || v.subwindows;

  return (
    <section className="bt-sweep" aria-label="Dial sweep">
      <button
        type="button"
        className="grp-h rp-head"
        aria-expanded={open}
        onClick={() => setOpen((x) => !x)}
      >
        <span className="chev">{open ? "▾" : "▸"}</span>
        <span className="lbl">Dial sweep — the curve</span>
        {/* Slimmed: the chip row below carries the dial/slice/windows/clock, so the hint keeps only the
            one caveat that lives nowhere else — the store holds a single, overwritable sweep. */}
        <em className="hint"> · latest sweep only</em>
      </button>

      {open && (
        <>
          {/* 1. THE VERDICT — the sweep's one answer, loud. Neutral in tone: a band is not "good" and a
              null is not "bad" (#4), the words carry the result. */}
          <div className="bt-verdict">
            <span className="v-head">{verdictHeadline(v)}</span>
            <span className="v-qual"> · {verdictQualifier(v)}</span>
          </div>

          {/* 2. THE FACTS — what this curve IS, one chip each (the ManifestCard idiom); this replaces the
              old "Window … baseline … one frozen mirror …" paragraph. */}
          <dl className="bt-facts">
            <div>
              <dt>Dial{v.dialNames.length === 1 ? "" : "s"}</dt>
              <dd>{v.dialNames.join(", ") || "—"}</dd>
            </div>
            {/* Slice ALWAYS visible, so the reader always sees what the numbers are scoped to; the full
                rationale for a sliced read folds away below. */}
            <div>
              <dt>Read on</dt>
              <dd
                title={
                  v.metricSlice
                    ? "ONE algorithm slice — every episode count, level and delta below is this slice's, not the pool's"
                    : "the whole pooled population"
                }
              >
                {v.metricSlice || "the whole pool"}
              </dd>
            </div>
            <div>
              <dt>Settings</dt>
              <dd>{v.points.length}</dd>
            </div>
            <div>
              <dt>Windows</dt>
              <dd title={`${v.windowStart ?? "—"} → ${v.windowEnd ?? "—"}`}>{nWindows}</dd>
            </div>
            <div>
              <dt>Clock</dt>
              <dd>{v.clock}</dd>
            </div>
            <div>
              <dt>Baseline</dt>
              <dd>
                {v.baselineConfigShort || "—"}
                {/* Load-bearing: when the grid did not contain the production default, every delta is
                    relative to a CHOSEN setting rather than to today's behavior. Marked only then
                    (honest loudness — the exception). */}
                {!v.baselineIsDefault && (
                  <span
                    className="sb-badge b-base"
                    title="NOT the production dials — this grid does not contain them, so every delta below is relative to a chosen setting, not to today's behavior"
                  >
                    not default
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt>Mirror</dt>
              <dd title={v.mirrorHash}>{v.mirrorHash.slice(0, 8) || "—"}</dd>
            </div>
          </dl>

          {/* 3. HOW TO READ THIS — the methodology, folded (default shut). Nothing here is deleted; it is
              one click away. */}
          <button
            type="button"
            className="bt-method-toggle"
            aria-expanded={howOpen}
            onClick={() => setHowOpen((x) => !x)}
          >
            <span className="chev">{howOpen ? "▾" : "▸"}</span> How to read this ⓘ
          </button>
          {howOpen && (
            <div className="bt-method-body">
              {/* backend-authored — rendered VERBATIM */}
              {v.banner && <div className="sb-banner">{v.banner}</div>}
              <p className="bt-quiet">{plateauLine(v)}</p>
              {v.metricSlice && (
                <p className="bt-quiet">
                  READ ON ONE SLICE — {v.metricSlice}. Every episode count, level and delta below is
                  that slice's, not the pool's. A dial that touches a small family cannot move a median
                  over the whole pool, so the slice is the only place its effect can show; read against
                  the pool, every number here would be wrong.
                </p>
              )}
              <p className="bt-quiet">
                One frozen mirror, so a difference between points is attributable to the dial rather
                than to the tape moving underneath it. Points are in dial order, never in outcome order.
              </p>
              {v.windows.length > 0 && (
                <p className="bt-quiet">
                  Each point is one run PER WINDOW, pooled — {v.windows.length} disjoint window
                  {v.windows.length === 1 ? "" : "s"} (
                  {v.windows.map((w) => `${w.start}→${w.end}`).join(" · ")}), {v.concurrency} at a time.
                  Sign agreement below is agreement across those windows, which were run as separate
                  measurements rather than sliced out of one.
                  {v.windows.length === 1 &&
                    " With a single window there is nothing to agree with, so no point can report agreement."}
                </p>
              )}
            </div>
          )}

          {/* 4. THE TABLE — unchanged: the same columns, header-cell legends, plateau `inband` highlight
              and BASELINE/SHARED badges. Points stay in dial order; the band is the only mark. */}
          <div className="sb-scroll">
            <table className="basket bt-sweeptbl">
              <thead>
                <tr>
                  <th>Setting</th>
                  <th title="episodes this point produced">Episodes</th>
                  <th title="of those, scoreable against realized prices">Scored</th>
                  <th title={v.metricName}>Level</th>
                  <th title="the same metric against the baseline setting, in percentage points">
                    vs baseline
                  </th>
                  <th title="the same delta recomputed on each disjoint window, in the order above">
                    Windows
                  </th>
                  <th title="every window moved the same way — a delta that reverses when the window is cut has found nothing. Under the strict rule a window that did not move, or that held no episode to move, withholds agreement rather than granting it. The column answers under the rule THIS band was keyed on; the cell's tooltip carries all three.">
                    Holds its sign
                    <em className="hint"> · {v.plateauRule.replaceAll("_", " ")}</em>
                  </th>
                  <th title="the contiguous band of settings that behave alike; a band one point wide is not a band">
                    Plateau
                  </th>
                </tr>
              </thead>
              <tbody>
                {v.points.map((p) => (
                  <tr
                    key={p.runIds.join("-") || p.configShort}
                    className={p.inPlateau ? "bt-row inband" : "bt-row"}
                  >
                    <td title={`runs: ${p.runIds.join(", ") || "—"} · policy ${p.configShort}`}>
                      {p.dials.map((d) => `${d.name}=${d.value}`).join(", ") || "—"}
                      {p.isBaseline && (
                        <span className="sb-badge b-base" title="the production dials">
                          BASELINE
                        </span>
                      )}
                      {p.runsShared && (
                        <span
                          className="sb-badge b-base"
                          title="this setting resolves to the same config as another point — one measurement cited twice, not two"
                        >
                          SHARED
                        </span>
                      )}
                    </td>
                    <td className="bt-n">{p.nEpisodes}</td>
                    <td className="bt-n">{p.nScored}</td>
                    <td className="bt-v">{fmtMetric(p.metric)}</td>
                    <td className="bt-v">{p.isBaseline ? "—" : fmtDelta(p.delta)}</td>
                    <td className="bt-v">
                      {p.windowDeltas.length
                        ? p.windowDeltas.map((d) => fmtDelta(d)).join(" · ")
                        : "—"}
                    </td>
                    <td
                      title={
                        `strict: ${p.strictSignAgreement ? "yes" : "no"} · strict over measurable: ` +
                        `${p.strictMeasurableAgreement ? "yes" : "no"}` +
                        (p.nUnmeasurable ? ` (${p.nUnmeasurable} unmeasurable)` : "") +
                        ` · pre-registered: ${p.signAgreement ? "yes" : "no"}` +
                        (statusLine(p) ? ` · windows: ${statusLine(p)}` : "")
                      }
                    >
                      {p.isBaseline ? (
                        <span title="the reference point — its deltas are zero against itself by construction, so it has nothing to agree or disagree with">
                          —
                        </span>
                      ) : agreesUnderBandRule(v, p) ? (
                        "yes"
                      ) : (
                        "no"
                      )}
                    </td>
                    <td>{p.inPlateau ? "in band" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
