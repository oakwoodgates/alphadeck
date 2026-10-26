import type { CallCardResponse, MemberDataFlagsOut, ThesisSummary } from "../api/hooks";
import { DataFlagMark } from "../cockpit/DataFlagMark";
import { gradeClass, tickerLabel, verdictLabel } from "../util/format";

// A board card. Its loudness comes from the enclosing column's state class (.col.armed .card, …).
export function ThesisCard({
  thesis,
  call,
  onSelect,
  flags = null,
}: {
  thesis: ThesisSummary;
  call: CallCardResponse;
  onSelect: (id: string) => void;
  /** S3 — a single-name card's data flags (the quiet mark after its ticker); null for a theme or no flag. */
  flags?: MemberDataFlagsOut | null;
}) {
  const armed = call.state === "armed";
  const managing = call.state === "managing";

  return (
    <button type="button" className="card" onClick={() => onSelect(thesis.id)}>
      {armed && (
        <span className="call-flag">
          <span className="b" />
          CALL READY
        </span>
      )}
      <div className="tk">
        {tickerLabel(thesis.ticker, thesis.basket_size)}
        <DataFlagMark member={flags} />
      </div>
      <div className="nm">{thesis.name}</div>
      <div className="desc">{thesis.narrative}</div>
      <div className="foot">
        {armed ? (
          // Lead with the entry verdict (what to DO — e.g. STARTER), colored by the entry grade;
          // the conviction grade is secondary context ("core thesis"), not the headline. A bare
          // "CORE" badge here reads as "go big" — the over-commit misread this split exists to stop.
          <>
            <span className={`grade ${gradeClass(call.entry_grade)}`}>
              {verdictLabel(call.verdict).toUpperCase()}
            </span>
            {call.conviction_grade && (
              <span className="conv">{call.conviction_grade} thesis</span>
            )}
          </>
        ) : managing ? (
          <span className="countchip">{verdictLabel(call.verdict)}</span>
        ) : (
          <Readiness
            conviction={call.key_conviction.turned}
            confirmation={call.key_confirmation.turned}
          />
        )}
      </div>
    </button>
  );
}

// The two keys, as readiness pips — one pip per KEY, conviction first then confirmation (the CallCard's
// order), so a lit pip says WHICH key has turned, not just how many. A count-only pip row read a
// confirmation-only (Watch) card as "conviction fired": the first pip lit either way.
function Readiness({ conviction, confirmation }: { conviction: boolean; confirmation: boolean }) {
  const keys = [
    { name: "Conviction", on: conviction },
    { name: "Confirmation", on: confirmation },
  ];
  const on = keys.filter((k) => k.on).length;
  const label = keys.map((k) => `${k.name} ${k.on ? "✓" : "○"}`).join(" · ");
  return (
    <div className="readiness" role="img" aria-label={label} title={label}>
      <span style={{ color: "var(--txt-4)", fontSize: 10 }}>
        {on}/{keys.length}
      </span>
      <div className="pips">
        {keys.map((k) => (
          <span key={k.name} className={`pip ${k.on ? "on" : ""}`} />
        ))}
      </div>
    </div>
  );
}
