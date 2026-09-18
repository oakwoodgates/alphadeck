import type { PooledReport } from "../api/hooks";
import {
  fmtN,
  fmtPct,
  fmtStat,
  hasBasketMedian,
  metricColumns,
  mixRows,
  SLICE_COLUMNS,
  sortedSlices,
  statTone,
  timingNullCaveat,
} from "./pooled";

// THE POOLED PANEL — the page's first and primary reading, and the reason the surface exists.
//
// The unit is the ALGORITHM, never the thesis. There is no thesis column anywhere below, by
// construction rather than by convention: the payload carries no thesis identifier at all (a backend
// test walks it looking for one), so a ranking could not be rendered here even by accident. Checking a
// number against its own rows is the LEDGER's job, further down, one thesis at a time.
//
// Every metric renders its two nulls and its excess-over-basket BESIDE it, never underneath or behind
// a hover. An absolute return on a universe the operator assembled in 2026 over names that had already
// moved is not evidence; the same number against random timing and random names on that same universe
// is the only part that says anything about the algorithm.
//
// The firing diagnostics carry no outcome at all — counts, mixes, arm density. They are the half of
// this report with no hindsight risk, and they are what catches a broken detector.

export function PooledPanel({ pooled }: { pooled: PooledReport }) {
  const d = pooled.diagnostics;
  const slices = sortedSlices(pooled.slices ?? []);
  const gate = pooled.min_n ?? 0;
  const caveat = timingNullCaveat(d?.timing_candidate_sessions ?? 0, pooled.n_episodes ?? 0);
  // A report written before B carries no basket median, and the older column set is what it was read
  // under — so the pair of excess columns appears only where there is a pair to show.
  const headline = pooled.headline_excess ?? "mean";
  const showMedian = (pooled.metrics ?? []).some(hasBasketMedian);

  return (
    <section className="bt-pooled" aria-label="Pooled analysis">
      <div className="sect-h">
        Pooled — the algorithm, not the thesis <em>· read every metric against its two nulls</em>
      </div>
      {/* BACKEND-authored, rendered verbatim: the sentence that says what these numbers are. */}
      <div className="sb-banner">{pooled.banner}</div>

      <div className="bt-metrics">
        {(pooled.metrics ?? []).map((m) => (
          <div key={m.name} className={`bt-metric${m.insufficient_n ? " gated" : ""}`}>
            <div className="bt-mhead">
              <span className="bt-mname">{m.name.replaceAll("_", " ")}</span>
              {m.insufficient_n && (
                <span className="sb-badge b-gate" title={`fewer than ${gate} scored episodes`}>
                  n&lt;{gate}
                </span>
              )}
            </div>
            <div className="bt-mclaim">{m.claim}</div>
            <div className="bt-mcols">
              {metricColumns(m, headline).map((c) => (
                <div key={c.id} className="bt-mcol" title={c.title}>
                  <div className="bt-mcol-l">{c.label}</div>
                  <div className={`bt-mcol-v ${statTone(c.stat)}`}>{fmtStat(c.stat)}</div>
                  <div className="bt-mcol-n">{fmtN(c.stat)}</div>
                </div>
              ))}
            </div>
            {m.note && <div className="bt-mnote">{m.note}</div>}
          </div>
        ))}
      </div>

      {showMedian && (
        <p className="bt-quiet">
          TWO EXCESS FIGURES, ONE POPULATION. The basket's move over the same window, taken as an
          equal-weight MEAN (what an equal-weight basket position earned) and as a MEDIAN (what the
          typical member did) over exactly the same priced names. They agree when the basket is
          symmetric and part company when it is skewed — one runaway name lifts the mean by a fraction
          of its own move and leaves the median where it was, so a figure read only against the mean
          can say "lost to the basket" where the algorithm beat every name but one.
          {headline === "median"
            ? " This report leads with the median."
            : " This report leads with the mean."}
        </p>
      )}

      {caveat && <div className="bt-caveat">{caveat}</div>}

      <h4>Algorithm slices</h4>
      <p className="bt-quiet">
        The same four figures, cut by what the ALGORITHM did — which key turned the lock, how it was
        confirmed, how many names armed together, how the run ended. A slice below the gate stays on
        screen with its count visible: three observations is a finding, and a row that disappeared
        would read as none.
      </p>
      {slices.length === 0 ? (
        <p className="bt-quiet">No scoreable episode in this run — nothing to slice.</p>
      ) : (
        <div className="sb-scroll">
          <table className="basket bt-slices">
            <thead>
              <tr>
                {SLICE_COLUMNS.map((c) => (
                  <th key={c.id} title={c.title}>
                    {c.label}
                  </th>
                ))}
                <th title="scored episodes in this slice">n</th>
                <th>Actual</th>
                <th title="against the basket's equal-weight MEAN move — what an equal-weight basket position earned">
                  Excess
                </th>
                {showMedian && (
                  <th title="against the basket's MEDIAN move — what the typical member did, over the same priced names">
                    Excess (typical)
                  </th>
                )}
                <th>vs timing</th>
                <th>vs name</th>
              </tr>
            </thead>
            <tbody>
              {slices.map((s) => (
                <tr
                  key={JSON.stringify(s.key)}
                  className={s.insufficient_n ? "bt-row gated" : "bt-row"}
                >
                  {SLICE_COLUMNS.map((c) => (
                    <td key={c.id}>{s.key?.[c.id] ?? "—"}</td>
                  ))}
                  <td className="bt-n">{s.n}</td>
                  <td className={`bt-v ${statTone(s.actual)}`}>{fmtStat(s.actual)}</td>
                  <td className={`bt-v ${statTone(s.excess)}`}>{fmtStat(s.excess)}</td>
                  {showMedian && (
                    <td className={`bt-v ${statTone(s.excess_vs_basket_median)}`}>
                      {fmtStat(s.excess_vs_basket_median)}
                    </td>
                  )}
                  <td className={`bt-v ${statTone(s.vs_timing)}`}>{fmtStat(s.vs_timing)}</td>
                  <td className={`bt-v ${statTone(s.vs_name)}`}>{fmtStat(s.vs_name)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4>Firing diagnostics</h4>
      <p className="bt-quiet">
        Counts and mixes — no outcome anywhere in this block, so nothing here can be biased by
        hindsight. This is the half of the report that catches a detector that stopped firing.
      </p>
      <div className="bt-diag">
        <div className="bt-diagline">
          <b>{d?.n_episodes ?? 0}</b> episodes · <b>{d?.n_scoreable ?? 0}</b> scoreable ·{" "}
          <b>{fmtPct(d?.pct_armed_with_a_co_member)}</b> armed alongside a co-member · widest single
          session <b>{d?.widest_single_session_group ?? 0}</b> names · timing null drew from{" "}
          <b>{d?.timing_candidate_sessions ?? 0}</b> sessions
        </div>
        <div className="bt-mixes">
          <Mix
            title="Key 1 source"
            hint="which trigger family turned the first lock"
            mix={d?.key1_source_mix}
            total={d?.n_episodes ?? 0}
          />
          <Mix
            title="Confirmation grade"
            hint="volume-backed core vs momentum-only flip, at the arm"
            mix={d?.confirmation_grade_mix}
            total={d?.n_episodes ?? 0}
          />
          <Mix
            title="Co-arm bucket"
            hint="how many members armed together that thesis-session"
            mix={d?.co_arm_bucket_mix}
            total={d?.n_episodes ?? 0}
          />
          <Mix
            title="Entry grade"
            hint="the call-strength class at the arm"
            mix={d?.entry_grade_mix}
            total={d?.n_episodes ?? 0}
          />
          <Mix
            title="Closed by"
            hint="why each arm run ended"
            mix={d?.close_reason_mix}
            total={d?.n_episodes ?? 0}
          />
        </div>
      </div>
    </section>
  );
}

function Mix({
  title,
  hint,
  mix,
  total,
}: {
  title: string;
  hint: string;
  mix: Record<string, number> | undefined;
  total: number;
}) {
  const rows = mixRows(mix, total);
  if (!rows.length) return null;
  return (
    <div className="bt-mix" title={hint}>
      <div className="bt-mixname">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="bt-mixrow">
          <span className="bt-mixlbl">{r.label}</span>
          <span className="bt-mixn">{r.n}</span>
          <span className="bt-mixpct">{fmtPct(r.pct)}</span>
        </div>
      ))}
    </div>
  );
}
