import type { ScoreboardEpisodeOut } from "../api/hooks";
import { fmtDate } from "../util/format";
import { EpisodeSparkline } from "./EpisodeSparkline";
import {
  awaitingForwardBar,
  closeReasonBadge,
  episodeBadges,
  excursionTitle,
  fmtPastPeak,
  fmtReturn,
  operatorLine,
  returnLabel,
  triggerChips,
  type LedgerView,
} from "./rows";
import { noForwardBar } from "./scorecard";

// One episode ledger row — shared by the live record table and the historical (replayed) panel.
// `historical` swaps the operator cell: history predates decision capture, so it says so
// (structurally absent) instead of faking a "no decision logged" capture gap.
// `view` (Slice 2) swaps the middle cells: Summary keeps today's Why · Exit-by · Status · Return ·
// Peak · Operator; Timing shows the timing-calibration lens Path · Return · Peak · Peak high ·
// Worst · Worst low · Past peak · Status — the CLOSE basis (Return · Peak · Worst) and the WICK
// basis (Peak high · Worst low) side by side, each close figure adjacent to its own wick. Name
// (with the ↗ cockpit jump) + Armed + De-armed lead both; the Status cell is identical in both
// (built once, placed per view). The ROW opens the episode scorecard (the scoreboard's own
// drill-down); the ↗ jumps to the fuller per-name Cockpit.

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
  /** nameKey deep-links the NAME's panel in the Cockpit (?name=) — the ticker when resolved, else
   *  the security_id (always present on an episode). Fired by the ↗ icon (not the row). */
  onSelect: (id: string, nameKey?: string) => void;
  /** Opens the episode scorecard drawer — the ROW's click (the scoreboard's own drill-down). The ↗
   *  icon is the distinct affordance to the fuller Cockpit; its handler stops propagation so opening
   *  the Cockpit never also opens the drawer. Optional so a caller without a drawer leaves the row
   *  inert rather than dead-linking. */
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
  // the adverse twin of Peak, on the same CLOSE basis and behind the same guard: with no forward bar
  // a degenerate 0.0% would read as "it never went against you", which is the opposite of unknown.
  const worst = fmtReturn(ep.trough_return);
  // the WICK twins — the best/worst price that actually traded. Independently null when the window's
  // bars don't all carry a wick (all-or-nothing, per column), so one of these can dash on a row whose
  // close figures are present; `fmtReturn` already renders that null as "—" and never substitutes
  // the close for it.
  const peakHigh = fmtReturn(ep.intraday_high_return);
  const worstLow = fmtReturn(ep.intraday_low_return);
  const op = operatorLine(ep);

  // The Status cell is a SHARED column — identical content in both views, only its position differs
  // (mid-row in Summary, last in Timing). Build it once so the two views never drift.
  // the de-arm reason joins the badge row rather than trailing it as a sentence — one scannable
  // chip, muted because every closed row has one. Its hover carries the composed detail AND the raw
  // wire token, so the short label defers nothing (see closeReasonBadge).
  const reason = closeReasonBadge(ep);
  const statusCell = (
    <td className="sb-status">
      {[...episodeBadges(ep), ...(reason ? [reason] : [])].map((b) => (
        <span key={b.label} className={`sb-badge ${b.cls}`} title={b.title}>
          {b.label}
        </span>
      ))}
    </td>
  );

  return (
    <tr
      className="sb-row"
      onClick={() => onOpenScorecard?.(ep)}
      tabIndex={0}
      // the row was focusable but not activatable — Tab landed on it and Enter did nothing. Same
      // idiom as the Cockpit's basket rows: Enter/Space do what the click does, and preventDefault
      // stops Space from paging the ledger out from under the drawer it just opened.
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenScorecard?.(ep);
        }
      }}
    >
      <td className="tk">
        <button
          type="button"
          className="sb-gocockpit"
          aria-label={`open ${ep.ticker ?? "this name"} in the cockpit`}
          title="open in cockpit"
          // a distinct affordance from the row's click-to-scorecard: stop the bubble so opening the
          // Cockpit never also opens the scorecard drawer
          onClick={(e) => {
            e.stopPropagation();
            onSelect(thesisId, ep.ticker ?? ep.security_id);
          }}
        >
          ↗
        </button>
        {ep.ticker ?? "—"}
      </td>
      {/* Armed and De-armed are two measurements, so they are two columns — one cell reading
          "Aug 7 → Aug 11" could be neither scanned down nor sorted on. The censored-start marker
          belongs to the ARM date (it is the arm that is unknowable), so it stays here. */}
      <td className="sb-armed">
        {fmtDate(ep.arm_date)}
        {ep.censored_start && (
          <span className="sb-cen" title="the record began mid-arm — true arm date unknowable">
            *
          </span>
        )}
      </td>
      {/* a still-open episode has no de-arm — "—", never an empty cell or a guessed date */}
      <td className="sb-armed sb-dearm">{ep.dearm_date ? fmtDate(ep.dearm_date) : "—"}</td>

      {view === "timing" ? (
        <>
          {/* Path — the episode's own closes, the shape behind the three numbers to its right. */}
          <td className="sb-path">
            <EpisodeSparkline ep={ep} />
          </td>
          {/* Return — forward_return, dashed before a forward bar exactly as the Summary row does. */}
          <td className="sb-ret">
            <span className={`ret ${ret.cls}`}>{awaiting ? "—" : ret.text}</span>
          </td>
          {/* The excursion quartet — close, then its wick, on each side. "—" until a forward bar
              lands (honest loudness, no false 0.0%); each cell's hover answers only for itself. */}
          {/* Peak — the best CLOSE, the same basis Return and Past peak are measured on. */}
          <td className="sb-ret" title={noBar ? undefined : excursionTitle(ep, "peak", "close")}>
            <span className={`ret ${noBar ? "" : peak.cls}`}>{noBar ? "—" : peak.text}</span>
          </td>
          {/* Peak high — the best price that actually TRADED. Quieter than the close beside it: the
              close is the figure the rest of the row is measured on, the wick is the check. */}
          <td
            className="sb-ret sb-wick"
            title={noBar ? undefined : excursionTitle(ep, "peak", "wick")}
          >
            <span className={`ret ${noBar ? "" : peakHigh.cls}`}>
              {noBar ? "—" : peakHigh.text}
            </span>
          </td>
          {/* Worst — the worst CLOSE. A real 0.0% here is a measurement (the name never closed below
              entry — 24% of the record), so it renders as 0.0%, not a dash; only the no-forward-bar
              case dashes. */}
          <td className="sb-ret" title={noBar ? undefined : excursionTitle(ep, "worst", "close")}>
            <span className={`ret ${noBar ? "" : worst.cls}`}>{noBar ? "—" : worst.text}</span>
          </td>
          {/* Worst low — the worst price that actually TRADED. MEASURED: this reads 0 on 0 of 250
              episodes, so unlike Worst it can never say "it never went against you". */}
          <td
            className="sb-ret sb-wick"
            title={noBar ? undefined : excursionTitle(ep, "worst", "wick")}
          >
            <span className={`ret ${noBar ? "" : worstLow.cls}`}>
              {noBar ? "—" : worstLow.text}
            </span>
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
              // one chip per KIND, counted — repeating a kind's name said nothing the first chip
              // hadn't. Every collapsed label still rides the title, so the fires behind the count
              // stay one hover away (see triggerChips).
              triggerChips(ep.triggers_at_arm).map((c) => (
                <span key={c.kind} className="sb-trig" title={c.labels.join("\n")}>
                  {c.kind}
                  {c.n > 1 && <span className="sb-trign">×{c.n}</span>}
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
          {/* Peak — the realized high; "—" until a forward bar lands (same honest-loudness guard as Timing). */}
          <td className="sb-ret">
            <span className={`ret ${noBar ? "" : peak.cls}`}>{noBar ? "—" : peak.text}</span>
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
