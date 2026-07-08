import type { ScoredMemberOut } from "../api/hooks";
import { useExtract } from "../api/hooks";
import { Meter } from "./Meter";
import { archLabel, errText, formatMarketCap, memberHasFundamentals } from "./format";

interface Props {
  member: ScoredMemberOut;
  selected: boolean;
  onSelect: () => void;
  // the active thesis — threaded to the extract so the row's "get data" and the rail's FactsPanel share ONE
  // query (same key, same grounded-purity behavior). Optional: an un-wired/test render omits it.
  thesisId?: string;
}

/** One scored basket member: ticker + archetype + market-cap figure, then the four meters. purity /
 *  runway / catalysts cluster as the "goodness" group; dilution sits after a faint separator as the
 *  ember RISK axis (the pressure meter), so it can't be misread as a fourth goodness meter.
 *
 *  GATE 2 of the three-gate TRIAGE flow — "mark for data": the ⇣ get-data control fires THIS ONE name's
 *  extraction (the existing per-name endpoint; 2–4 EDGAR requests, cache-first). The mark and the spend
 *  collapse into one deliberate click — cost is the operator's to spend, never ambient. It shares the
 *  FactsPanel's query (same key), so a fetched row's candidates render in the rail instantly; once a fact
 *  is RATIFIED the control disappears (the meters + funnel take over). Failures are per-name + retryable. */
export function ScoredRow({ member, selected, onSelect, thesisId }: Props) {
  const extract = useExtract(member.security_id, thesisId);
  const loaded = memberHasFundamentals(member);
  const dataReady = extract.data !== undefined;
  return (
    // the row DIV owns the whole-surface click; the ticker block below is the real <button> (the
    // accessible select target) — a nested-button structure would be invalid HTML, hence the split.
    <div className={`nmrow${selected ? " sel" : ""}`} onClick={onSelect}>
      <div className="top">
        <button type="button" className="nmrow-sel" onClick={onSelect}>
          <span className="tk">{member.ticker ?? "◇"}</span>
          {/* archetype chip only when DECIDED (item F: unset renders nothing here — quiet; the ✦ dot +
              the rail carry the pending decision, so an all-unset fresh basket isn't a wall of chips) */}
          {member.archetype && (
            <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
          )}
          {member.archetype_hint && member.archetype_hint !== member.archetype && (
            <span
              className="arch-rec-dot"
              title={`figures suggest ${archLabel(member.archetype_hint)} — open the name to apply`}
            >
              ✦
            </span>
          )}
        </button>
        <span className="cap">
          <small>mkt cap</small>
          {formatMarketCap(member.market_cap.value)}
        </span>
        {/* gate 2 — per-name, opt-in, visible cost; hidden once ANY fact is confirmed (loaded) */}
        {!loaded &&
          (extract.isFetching ? (
            <span className="wb-getdata busy">extracting…</span>
          ) : dataReady ? (
            <button
              type="button"
              className="wb-getdata ready"
              aria-label={`data ready for ${member.ticker ?? "name"} — open to ratify`}
              title="candidates are loaded — open the name and ratify them in the rail"
              onClick={(e) => {
                e.stopPropagation();
                onSelect();
              }}
            >
              ✓ data ready — ratify
            </button>
          ) : extract.error ? (
            <button
              type="button"
              className="wb-getdata err"
              aria-label={`retry get data for ${member.ticker ?? "name"}`}
              title={`couldn't extract — ${errText(extract.error)}; click to retry`}
              onClick={(e) => {
                e.stopPropagation();
                extract.refetch();
              }}
            >
              ⚠ retry get data
            </button>
          ) : (
            <button
              type="button"
              className="wb-getdata"
              aria-label={`get data for ${member.ticker ?? "name"}`}
              title="pull this name's latest 10-Q/10-K from EDGAR (2–4 requests, cache-first) — the candidates land in the rail for you to ratify"
              onClick={(e) => {
                e.stopPropagation();
                extract.refetch();
              }}
            >
              ⇣ get data
            </button>
          ))}
      </div>
      <div className="wb-meters">
        <Meter label="purity" figure={member.purity} />
        <Meter label="runway" figure={member.runway} />
        <Meter label="catalysts" figure={member.catalysts} />
        <span className="wb-meter-sep" aria-hidden="true" />
        <Meter label="dilution" figure={member.dilution} risk />
        <span className="fit">{member.fit}</span>
      </div>
    </div>
  );
}
