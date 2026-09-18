import { useState } from "react";

import { fmtDelta, fmtMetric, plateauLine, readSweep } from "./sweep";

// THE SWEEP — a curve, never a winner.
//
// The whole surface is built so that "which setting won?" has no answer on it. Points render in the
// sweep's own dial order (never sorted by outcome), no row is highlighted for being highest, and what
// is marked instead is the PLATEAU: the contiguous band the backend computed. A plateau one point wide
// is the honest null result and says so in words.
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
  const v = readSweep(sweep);
  if (!v) return null;

  return (
    <section className="bt-sweep" aria-label="Dial sweep">
      <button
        type="button"
        className="grp-h rp-head"
        aria-expanded={open}
        onClick={() => setOpen((x) => !x)}
      >
        <span className="chev">▾</span>
        <span className="lbl">Dial sweep — the curve</span>
        <em className="hint">
          · {v.dialNames.join(", ") || "no dial named"} · {v.points.length} points ·{" "}
          {v.windows.length || 1} window{(v.windows.length || 1) === 1 ? "" : "s"} · {v.clock} clock ·
          latest sweep only
        </em>
      </button>

      {open && (
        <>
          {v.banner && <div className="sb-banner">{v.banner}</div>}
          <div className="bt-plateau">{plateauLine(v)}</div>
          <p className="bt-quiet">
            Window {v.windowStart ?? "—"} → {v.windowEnd ?? "—"} · baseline policy{" "}
            {v.baselineConfigShort || "—"}
            {!v.baselineIsDefault &&
              " (NOT the production dials — this grid does not contain them, so every delta below is" +
                " relative to a chosen setting, not to today's behavior)"}{" "}
            · one frozen mirror (
            <span title={v.mirrorHash}>{v.mirrorHash.slice(0, 8) || "—"}</span>), so a difference
            between points is attributable to the dial rather than to the tape moving underneath it.
            Points are in dial order, never in outcome order.
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
                  <th title="every sub-window moved the same way — a delta that reverses when the window is cut has found nothing">
                    Holds its sign
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
                    <td>{p.isBaseline ? "—" : p.signAgreement ? "yes" : "no"}</td>
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
