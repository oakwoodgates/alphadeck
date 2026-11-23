import { useMemo, useState } from "react";

import type { DisplaySignal } from "../api/hooks";
import { DisplayHeadlineRow } from "../cockpit/DisplaySignalsSection";
import { fmtDate } from "../util/format";
import { type IdentityCell, ledgerRow } from "./ledger";
import { eventSetAside, legendEntries, type OverlayEvent, type OverlayFamily } from "./overlay";
import { ExternalLink } from "../components/ExternalLink";

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
  identity?: IdentityCell[];
  signals?: DisplaySignal[];
  /** Set (to the drawer's asof) for a CLOSED/matured episode → the "current tape · as-of X" caption, so a
   *  name-current 90d figure isn't misread as the episode's own period. Null for a still-open episode. */
  tapeAsof?: string | null;
  /** Editions S4: a re-run edition's drawer — drop the "ingested Nd later" lines (our fetch timing). */
  hideIngest?: boolean;
  /** When false, renders only the cross-highlighting event table (used by CompanyDrawer, which shows
   *  identity and headlines in dedicated hero/context sections). Default true (Scoreboard). */
  showCockpitStrip?: boolean;
  /** Optional heading override (default "Event ledger"). */
  heading?: string;
  /** When true and 2+ event families exist, renders quiet reversible family filter chips above the table
   *  (row #N numbers stay fixed to match the chart chips). Default false (Scoreboard). */
  filterable?: boolean;
}

export function EventLedger({
  events,
  activeN,
  onActivate,
  identity = [],
  signals = [],
  tapeAsof,
  hideIngest = false,
  showCockpitStrip = true,
  heading = "Event ledger",
  filterable = false,
}: EventLedgerProps) {
  const [familyFilter, setFamilyFilter] = useState<OverlayFamily | null>(null);

  const familyChips = useMemo(() => {
    if (!filterable) return [];
    const counts = new Map<OverlayFamily, number>();
    for (const e of events) counts.set(e.family, (counts.get(e.family) ?? 0) + 1);
    return legendEntries(events).map((entry) => ({
      ...entry,
      count: counts.get(entry.family) ?? 0,
    }));
  }, [events, filterable]);

  if (events.length === 0) return null; // no drawn events yet → no table (mirrors the chart's empty state)

  const activeFilter =
    familyFilter != null && familyChips.some((c) => c.family === familyFilter) ? familyFilter : null;
  const visibleEvents = activeFilter ? events.filter((e) => e.family === activeFilter) : events;

  return (
    <section className={showCockpitStrip ? "sc-lens sb-evledger" : "cd-evledger"}>
      {heading && <div className={showCockpitStrip ? "sc-h" : "cd-sec-h"}>{heading}</div>}
      {filterable && familyChips.length >= 2 && (
        <div className="cd-ev-filters" role="group" aria-label="Filter event ledger by type">
          <button
            type="button"
            className={`cd-ev-chip${activeFilter === null ? " active" : ""}`}
            aria-pressed={activeFilter === null}
            onClick={() => setFamilyFilter(null)}
          >
            All <span className="n">{events.length}</span>
          </button>
          {familyChips.map((c) => {
            const on = activeFilter === c.family;
            return (
              <button
                key={c.family}
                type="button"
                className={`cd-ev-chip${on ? " active" : ""}`}
                aria-pressed={on}
                onClick={() => setFamilyFilter(on ? null : c.family)}
              >
                <span className={`ov-dot ${c.cls}`} aria-hidden="true" />
                {c.label} <span className="n">{c.count}</span>
              </button>
            );
          })}
        </div>
      )}
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
            {visibleEvents.map((e) => {
              const r = ledgerRow(e, hideIngest);
              // A set-aside row (screened buy/sell, passive 13G) is MUTED, never removed (WB #2) —
              // the type cell keeps its family label; the gray + the detail's screen line carry the
              // exception (#7). Same `eventSetAside` the chart chip reads — the two never disagree.
              const setAside = eventSetAside(e);
              return (
                <tr
                  key={r.n}
                  className={`${r.n === activeN ? "active" : ""}${setAside ? " evled-setaside" : ""}`.trim()}
                  tabIndex={0}
                  onMouseEnter={() => onActivate(r.n)}
                  onMouseLeave={() => onActivate(null)}
                  onFocus={() => onActivate(r.n)}
                  onBlur={() => onActivate(null)}
                >
                  <td className={`evled-n ${r.cls}`}>{r.n}</td>
                  <td className="evled-d">{fmtDate(r.date)}</td>
                  <td className="evled-t">{r.type}</td>
                  <td className="evled-x" title={r.note}>
                    {r.detail}
                    {/* the filing behind the fact, where the server resolved a link — quiet anchors
                        appended to the text (the refs themselves already ride in `detail`, so a
                        link-less source is still visible; this only adds the jump, #6) */}
                    {/* the index rides in the key: two sources can resolve to the SAME filing url
                        (one accession, two refs), and a bare url key would collide */}
                    {r.links?.map((l, i) => (
                      <ExternalLink key={`${i}-${l.url}`} className="evled-link" href={l.url}>
                        {l.label} ↗
                      </ExternalLink>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {showCockpitStrip && (
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
      )}
    </section>
  );
}
