import type { DisplaySignal } from "../api/hooks";
import { DisplayHeadlineRow } from "../cockpit/DisplaySignalsSection";
import { fmtDate } from "../util/format";
import { type IdentityCell, ledgerRow } from "./ledger";
import type { OverlayEvent } from "./overlay";

// The per-episode event LEDGER (Slice B) — the tabular companion to the drawer chart. It lists the SAME
// unified numbered events the chart draws (row #N ↔ chip #N, sharing the ONE `events` array from
// EpisodeScorecard, never re-numbered), cross-highlights with the chart (hover a row → its chip rings on
// the chart; hover a chip → this row tints), and carries the Cockpit identity line + present-only signal
// headlines. READ-ONLY, like the Scoreboard itself: every row traces to a recorded fact (#6); a missing
// identity field reads "—" (never a guess); only signals that HAVE a headline render (honest loudness #7).
// Presentational — the numbering + all formatting are pure (overlay.ts + ledger.ts); this only lays them
// out and wires the lightweight hover emphasis (the full tooltip stays chart-hover-only — it needs chip
// coordinates). Known limit: a row whose chip is off the chart's visible range has no chip to ring; the
// row still highlights.

export interface EventLedgerProps {
  events: OverlayEvent[];
  activeN: number | null;
  onActivate: (n: number | null) => void;
  identity: IdentityCell[];
  signals: DisplaySignal[];
  /** Set (to the drawer's asof) for a CLOSED/matured episode → the "current tape · as-of X" caption, so a
   *  name-current 90d figure isn't misread as the episode's own period. Null for a still-open episode. */
  tapeAsof?: string | null;
}

export function EventLedger({
  events,
  activeN,
  onActivate,
  identity,
  signals,
  tapeAsof,
}: EventLedgerProps) {
  if (events.length === 0) return null; // no drawn events yet → no table (mirrors the chart's empty state)
  return (
    <section className="sc-lens sb-evledger">
      <div className="sc-h">Event ledger</div>
      <div className="evled-scroll">
        <table className="evled-tbl">
          <thead>
            <tr>
              <th className="evled-n">#</th>
              <th>date</th>
              <th>type</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => {
              const r = ledgerRow(e);
              return (
                <tr
                  key={r.n}
                  className={r.n === activeN ? "active" : ""}
                  tabIndex={0}
                  onMouseEnter={() => onActivate(r.n)}
                  onMouseLeave={() => onActivate(null)}
                  onFocus={() => onActivate(r.n)}
                  onBlur={() => onActivate(null)}
                >
                  <td className={`evled-n ${r.cls}`}>{r.n}</td>
                  <td className="evled-d">{fmtDate(r.date)}</td>
                  <td className="evled-t">{r.type}</td>
                  <td className="evled-x">{r.detail}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* The Cockpit strip: the identity line + present-only signal headlines (reusing the Cockpit's
          DisplayHeadlineRow). In expanded mode the CSS floats this beside the table — no new prop. */}
      <div className="evled-cockpit">
        <div className="evled-identity">
          {identity.map((c) => (
            <span className="evled-idcell" key={c.label}>
              <span className="k">{c.label}</span>
              <span className="v">{c.value}</span>
            </span>
          ))}
        </div>
        {tapeAsof && <div className="evled-tape">current tape · as-of {fmtDate(tapeAsof)}</div>}
        {signals.length > 0 && (
          <div className="np-headlines evled-headlines">
            {signals.map((s) => (
              <DisplayHeadlineRow key={s.kind} headline={s.headline!} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
