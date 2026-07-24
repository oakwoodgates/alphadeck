import type { ScoreboardEpisodeOut } from "../api/hooks";
import { fmtDate } from "../util/format";
import {
  awaitingForwardBar,
  episodeBadges,
  fmtPastPeak,
  fmtReturn,
  operatorLine,
  returnLabel,
  type LedgerView,
} from "./rows";
import { noForwardBar } from "./scorecard";

// One episode ledger row — shared by the live record table and the historical (replayed) panel.
// `historical` swaps the operator cell: history predates decision capture, so it says so
// (structurally absent) instead of faking a "no decision logged" capture gap.
// `view` (Slice 2) swaps the middle cells: Summary keeps today's Why · Exit-by · Status · Return ·
// Operator; Timing shows the timing-calibration lens Return · Peak · Past peak · Status. Name (with
// the ⤢) + Armed lead both; the Status cell is identical in both (built once, placed per view).

export function EpisodeRow({
  ep,
  thesisId,
  onSelect,
  onOpenScorecard,
  historical = false,
  view = "summary",
}: {
  ep: ScoreboardEpisodeOut;
  thesisId: string;
  /** nameKey deep-links the clicked NAME's panel in the Cockpit (?name=) — the ticker when
   *  resolved, else the security_id (always present on an episode). */
  onSelect: (id: string, nameKey?: string) => void;
  /** Opens the episode scorecard drawer. A DISTINCT affordance from the row's click-to-Cockpit:
   *  its handler stops propagation so it never also fires onSelect. Omitted → the control is not
   *  rendered (no dead affordance). */
  onOpenScorecard?: (ep: ScoreboardEpisodeOut) => void;
  historical?: boolean;
  view?: LedgerView;
}) {
  const ret = fmtReturn(ep.forward_return);
  // a single-bar arm carries forward_return 0.0 (only the arm-day bar) — show "—", not a false flat
  // "0.0%"; the label ("awaiting forward bar") carries the reason, mirroring the insufficient-prices dash
  const awaiting = awaitingForwardBar(ep);
  // No forward bar yet (single arm-day bar OR no bar at all): the peak / past-peak returns are a
  // degenerate 0.0% / 0d, so the Timing view dashes them — the SAME honest-loudness guard the
  // scorecard uses to hide its horizon lens (never a false-flat peak). See scorecard.ts.
  const noBar = noForwardBar(ep);
  const peak = fmtReturn(ep.peak_return);
  const op = operatorLine(ep);

  // The Status cell is a SHARED column — identical content in both views, only its position differs
  // (mid-row in Summary, last in Timing). Build it once so the two views never drift.
  const statusCell = (
    <td className="sb-status">
      {episodeBadges(ep).map((b) => (
        <span key={b.label} className={`sb-badge ${b.cls}`} title={b.title}>
          {b.label}
        </span>
      ))}
      {ep.status === "closed" && <span className="sb-reason">{ep.close_reason}</span>}
    </td>
  );

  return (
    <tr
      className="sb-row"
      onClick={() => onSelect(thesisId, ep.ticker ?? ep.security_id)}
      tabIndex={0}
    >
      <td className="tk">
        {onOpenScorecard && (
          <button
            type="button"
            className="sb-expand"
            aria-label={`open scorecard for ${ep.ticker ?? "this name"}`}
            title="open scorecard"
            // a distinct affordance: stop the bubble so the row's click-to-Cockpit never fires too
            onClick={(e) => {
              e.stopPropagation();
              onOpenScorecard(ep);
            }}
          >
            ⤢
          </button>
        )}
        {ep.ticker ?? "—"}
      </td>
      <td className="sb-armed">
        {fmtDate(ep.arm_date)}
        {ep.censored_start && (
          <span className="sb-cen" title="the record began mid-arm — true arm date unknowable">
            *
          </span>
        )}
        {ep.dearm_date && <span className="sb-dearm"> → {fmtDate(ep.dearm_date)}</span>}
      </td>

      {view === "timing" ? (
        <>
          {/* Return — forward_return, dashed before a forward bar exactly as the Summary row does. */}
          <td className="sb-ret">
            <span className={`ret ${ret.cls}`}>{awaiting ? "—" : ret.text}</span>
          </td>
          {/* Peak — the realized high; "—" until a forward bar lands (honest loudness, no false 0.0%). */}
          <td className="sb-ret">
            <span className={`ret ${noBar ? "" : peak.cls}`}>{noBar ? "—" : peak.text}</span>
          </td>
          {/* Past peak — trading days from the peak to the exit; "—" with no forward bar (a degenerate
              0d would read as "exited at the peak" — a real 0d, WITH a bar, is kept and meaningful). */}
          <td className="sb-pp">{noBar ? "—" : fmtPastPeak(ep.exit_vs_peak_days)}</td>
          {statusCell}
        </>
      ) : (
        <>
          <td className="sb-why">
            {ep.triggers_at_arm.length ? (
              ep.triggers_at_arm.map((t, i) => (
                <span key={i} className="sb-trig" title={t.label}>
                  {t.kind}
                </span>
              ))
            ) : (
              <span className="muted">—</span>
            )}
          </td>
          <td className="exitby">{fmtDate(ep.exit_by)}</td>
          {statusCell}
          <td className="sb-ret">
            <span className={`ret ${ret.cls}`}>{awaiting ? "—" : ret.text}</span>
            <span className="sb-retlabel"> {returnLabel(ep)}</span>
          </td>
          {historical ? (
            <td className="sb-op sb-op-none">— predates decision capture</td>
          ) : (
            <td className={`sb-op sb-op-${op.kind}`}>
              {op.text}
              {op.ret && <span className={`ret ${op.ret.cls}`}> {op.ret.text}</span>}
              {op.inferred && (
                <span className="sb-inf" title="no fill price logged — the close stands in">
                  ≈
                </span>
              )}
              {ep.operator?.reason && <span className="sb-reason"> · {ep.operator.reason}</span>}
            </td>
          )}
        </>
      )}
    </tr>
  );
}
