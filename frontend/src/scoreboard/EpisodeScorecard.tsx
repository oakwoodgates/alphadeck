import type { ScoreboardEpisodeOut } from "../api/hooks";
import { fmtDate, gradeClass } from "../util/format";
import { episodeBadges, fmtReturn, returnLabel } from "./rows";
import {
  edgeLens,
  fmtPrice,
  horizonLens,
  moveNote,
  noForwardBar,
  setupStrengthPct,
} from "./scorecard";

// The Slice-1 scorecard mounted inside the Drawer: the richer per-episode outcome the backend
// already ships but the ledger row never showed — four timing lenses that let the operator judge
// whether the platform's TIMING was any good. Read-only, provenance-first, honest loudness: a lens
// with no data renders nothing or one quiet muted line (never "null"/"NaN"). It surfaces more of
// what's computed — it changes no computation. Every field read here is on ScoreboardEpisodeOut.

export function EpisodeScorecard({
  ep,
  thesisName,
}: {
  ep: ScoreboardEpisodeOut;
  thesisName?: string;
}) {
  // A just-armed episode (no forward bar yet) carries degenerate 0.0% forward/peak/arm_until
  // returns; mirror the ledger row and never render those as a flat move (fix: false-flat 0.0%).
  const noBar = noForwardBar(ep);
  const fwd = fmtReturn(ep.forward_return);
  const note = moveNote(ep);
  const horizon = noBar ? null : horizonLens(ep); // no real peak to judge until a bar lands
  const edge = edgeLens(ep);
  const armUntil = fmtReturn(ep.arm_until_return);
  const strength = setupStrengthPct(ep);
  const badges = episodeBadges(ep);
  // the arm_until_return row is a forward-return too — degenerate before the first bar; the grades
  // and setup strength are arm-date facts, so they stay.
  const showArmUntil = !noBar && (ep.arm_until != null || ep.arm_until_return != null);
  const showGrades = ep.entry_grade != null || ep.conviction_grade != null || strength != null;
  const hasSetup = showArmUntil || showGrades;

  return (
    <div className="sc">
      <div className="sc-head">
        <span className="sc-tk">{ep.ticker ?? "—"}</span>
        {thesisName && <span>· {thesisName}</span>}
        <span className="sc-span">
          {fmtDate(ep.arm_date)} → {ep.dearm_date ? fmtDate(ep.dearm_date) : "still armed"}
        </span>
      </div>
      {badges.length > 0 && (
        <div className="sc-badges">
          {badges.map((b) => (
            <span key={b.label} className={`sb-badge ${b.cls}`} title={b.title}>
              {b.label}
            </span>
          ))}
        </div>
      )}
      {ep.status === "closed" && ep.close_reason && (
        <div className="sc-reason">closed · {ep.close_reason}</div>
      )}

      {/* Lens 1 — The move (provenance: closes the "show the prices" gap). */}
      <section className="sc-lens">
        <div className="sc-h">The move</div>
        {/* Slice-3 seam: the intra-window [arm → exit_by] price SERIES is not on the wire yet — the
            sparkline mounts here, and the drawer's expanded mode gives it room. Not built now. */}
        {noBar
          ? // no forward bar yet: the ENTRY only — an exit "@ arm_date" would be the same bar (a
            // false round-trip). No arrow, no exit.
            ep.entry_close != null && (
              <div className="sc-flow">
                <span>
                  {fmtPrice(ep.entry_close)} @ {fmtDate(ep.arm_date)}
                </span>
              </div>
            )
          : (ep.entry_close != null || ep.exit_close != null) && (
              <div className="sc-flow">
                <span>
                  {fmtPrice(ep.entry_close)} @ {fmtDate(ep.arm_date)}
                </span>
                <span className="sc-arrow">→</span>
                <span>
                  {fmtPrice(ep.exit_close)} @ {fmtDate(ep.exit_date)}
                </span>
              </div>
            )}
        <div className="sc-ret">
          {/* never a false-flat 0.0% before a forward bar — "—", exactly as the ledger row does */}
          <span className={`ret ${fwd.cls}`}>{noBar ? "—" : fwd.text}</span>
          <span className="sc-retlabel">{returnLabel(ep)}</span>
        </div>
        {note && <div className="sc-muted">{note}</div>}
      </section>

      {/* Lens 2 — Horizon calibration: is exit_by well-timed? Hidden entirely without a peak. */}
      {horizon && (
        <section className="sc-lens">
          <div className="sc-h">Horizon calibration</div>
          <div className="sc-ret">
            <span className={`ret ${horizon.peak.cls}`}>{horizon.peak.text}</span>
            <span className="sc-retlabel">peak @ {fmtDate(ep.peak_date)}</span>
          </div>
          {horizon.timing && <div className="sc-line">{horizon.timing}</div>}
          {horizon.giveback && <div className="sc-muted">{horizon.giveback}</div>}
        </section>
      )}

      {/* Lens 3 — Edge preservation: did we arm in time, or miss the early move? */}
      <section className="sc-lens">
        <div className="sc-h">Edge preservation</div>
        {edge.kind === "no_warm" ? (
          <div className="sc-muted">{edge.line}</div>
        ) : noBar ? (
          // a warm-vs-arm comparison here would be 0.0% vs 0.0% — say so, don't fake it
          <div className="sc-muted">not scorable until a forward bar lands</div>
        ) : (
          <>
            <div className="sc-line">{edge.lead}</div>
            <div className="sc-cmp">
              <span>
                warm-up <span className={`ret ${edge.warm.cls}`}>{edge.warm.text}</span> (from{" "}
                {fmtDate(ep.warm_date)})
              </span>
              <span>
                armed <span className={`ret ${edge.forward.cls}`}>{edge.forward.text}</span> (from{" "}
                {fmtDate(ep.arm_date)})
              </span>
            </div>
          </>
        )}
      </section>

      {/* Lens 4 — Entry window + setup. */}
      {hasSetup && (
        <section className="sc-lens">
          <div className="sc-h">Entry window + setup</div>
          {showArmUntil && (
            <div className="sc-ret">
              <span className={`ret ${armUntil.cls}`}>{armUntil.text}</span>
              <span className="sc-retlabel">
                to entry-window close{ep.arm_until ? ` ${fmtDate(ep.arm_until)}` : ""}
              </span>
            </div>
          )}
          {showGrades && (
            <div className="sc-setup">
              {ep.entry_grade && (
                <span>
                  entry <span className={`grade ${gradeClass(ep.entry_grade)}`}>
                    {ep.entry_grade.toUpperCase()}
                  </span>
                </span>
              )}
              {ep.conviction_grade && (
                <span>
                  conviction{" "}
                  <span className={`grade ${gradeClass(ep.conviction_grade)}`}>
                    {ep.conviction_grade.toUpperCase()}
                  </span>
                </span>
              )}
              {strength != null && (
                <span title="an experimental relative indicator — not a probability">
                  setup strength {strength}%
                </span>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
