import type { CallCardResponse, ThesisSummary } from "../api/hooks";
import { gradeClass, tickerLabel, verdictLabel } from "../util/format";

// A board card. Its loudness comes from the enclosing column's state class (.col.armed .card, …).
export function ThesisCard({
  thesis,
  call,
  onSelect,
}: {
  thesis: ThesisSummary;
  call: CallCardResponse;
  onSelect: (id: string) => void;
}) {
  const armed = call.state === "armed";
  const managing = call.state === "managing";
  const keysOn = (call.key_conviction.turned ? 1 : 0) + (call.key_confirmation.turned ? 1 : 0);

  return (
    <button type="button" className="card" onClick={() => onSelect(thesis.id)}>
      {armed && (
        <span className="call-flag">
          <span className="b" />
          CALL READY
        </span>
      )}
      <div className="tk">{tickerLabel(thesis.ticker, thesis.basket_size)}</div>
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
          <Readiness on={keysOn} total={2} />
        )}
      </div>
    </button>
  );
}

// The two keys, as readiness pips.
function Readiness({ on, total }: { on: number; total: number }) {
  return (
    <div className="readiness">
      <span style={{ color: "var(--txt-4)", fontSize: 10 }}>
        {on}/{total}
      </span>
      <div className="pips">
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className={`pip ${i < on ? "on" : ""}`} />
        ))}
      </div>
    </div>
  );
}
