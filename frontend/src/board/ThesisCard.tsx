import type { CallCardResponse, MemberDataFlagsOut, ThesisSummary } from "../api/hooks";
import { DataFlagMark } from "../cockpit/DataFlagMark";
import { daysFrom, fmtDate, gradeClass, tickerLabel, verdictLabel } from "../util/format";

// A board card. Its loudness comes from the enclosing column's state class (.col.armed .card, …).
export function ThesisCard({
  thesis,
  call,
  onSelect,
  flags = null,
  origin = null,
}: {
  thesis: ThesisSummary;
  call: CallCardResponse;
  onSelect: (id: string) => void;
  /** S3 — a single-name card's data flags (the quiet mark after its ticker); null for a theme or no flag. */
  flags?: MemberDataFlagsOut | null;
  /** F2 countdown origin date (YYYY-MM-DD) for entry/exit clock cues; falls back to call.asof. */
  origin?: string | null;
}) {
  const armed = call.state === "armed";
  const managing = call.state === "managing";
  const clockOrigin = origin ?? call.asof ?? null;
  const armDays = clockOrigin && call.arm_until ? daysFrom(clockOrigin, call.arm_until) : null;
  const exitDays = clockOrigin && call.exit_by ? daysFrom(clockOrigin, call.exit_by) : null;
  const riskCount = call.risk_signals?.length ?? 0;
  const armedCount = call.armed_members?.length ?? 0;
  const headlineClosing = Boolean(call.armed_members?.[0]?.entry_window_closing);
  const strength = typeof call.confidence === "number" ? Math.max(0, Math.min(1, call.confidence)) : null;

  return (
    <button type="button" className="card" onClick={() => onSelect(thesis.id)}>
      <div className="card-top">
        <div className="tk">
          {tickerLabel(thesis.ticker, thesis.basket_size)}
          <DataFlagMark member={flags} />
          {/* quiet, lowercase, only when on (WB#3): a meta index card's names never count toward the call */}
          {thesis.meta_index && (
            <span className="meta-tag" title="Meta index: its names never count toward the call">
              meta index
            </span>
          )}
        </div>
        {armed && (
          <span className="call-flag">
            <span className="b" />
            CALL READY
          </span>
        )}
      </div>
      <div className="nm">{thesis.name}</div>
      <div className="desc">{thesis.narrative}</div>
      <div className="foot">
        {armed ? (
          // Lead with the entry verdict (what to DO — e.g. STARTER), colored by the entry grade;
          // the conviction grade is secondary context ("core setup"), not the headline. A bare
          // "CORE" badge here reads as "go big" — the over-commit misread this split exists to stop.
          <>
            <span className={`grade ${gradeClass(call.entry_grade)}`}>
              {verdictLabel(call.verdict).toUpperCase()}
            </span>
            {call.conviction_grade && (
              <span className="conv">{call.conviction_grade} setup</span>
            )}
            {armedCount > 1 && (
              <span className="card-cue" title={`${armedCount} armed names in this basket`}>
                +{armedCount - 1} armed
              </span>
            )}
          </>
        ) : managing ? (
          <>
            {exitDays !== null && (
              <span
                className={`card-cue${exitDays <= 14 ? " lapsing" : ""}`}
                title={`Signal validity horizon: ${fmtDate(call.exit_by)}`}
              >
                {exitDays < 0 ? "exit reached" : exitDays === 0 ? "exit today" : `exit ${exitDays}d`}
              </span>
            )}
            {riskCount > 0 && (
              <span className="card-cue" title={`${riskCount} active risk signal${riskCount === 1 ? "" : "s"}`}>
                {riskCount} risk
              </span>
            )}
            <span className="countchip">{verdictLabel(call.verdict)}</span>
          </>
        ) : (
          <Readiness
            conviction={call.key_conviction.turned}
            confirmation={call.key_confirmation.turned}
          />
        )}
      </div>
      {armed && (armDays !== null || strength !== null || riskCount > 0) && (
        <div className="card-cues">
          {armDays !== null && (
            <span
              className={`card-cue${headlineClosing ? " closing" : ""}`}
              title={`Entry window through ${fmtDate(call.arm_until)}`}
            >
              {armDays < 0 ? "window lapsed" : armDays === 0 ? "act today" : `${armDays}d left`}
            </span>
          )}
          {riskCount > 0 && (
            <span className="card-cue" title={`${riskCount} active risk signal${riskCount === 1 ? "" : "s"}`}>
              {riskCount} risk
            </span>
          )}
          {strength !== null && (
            <span
              className="card-str"
              title={`Setup strength: ${strength.toFixed(2)} (relative indicator, not a probability)`}
            >
              <span className="card-str-track" aria-hidden="true">
                <span className="card-str-fill" style={{ width: `${Math.round(strength * 100)}%` }} />
              </span>
              <span>{strength.toFixed(2)}</span>
            </span>
          )}
        </div>
      )}
    </button>
  );
}

// The two keys, as readiness pips — one pip per KEY, conviction first then confirmation (the CallCard's
// order), so a lit pip says WHICH key has turned, not just how many. A count-only pip row read a
// confirmation-only (Watch) card as "conviction fired": the first pip lit either way.
function Readiness({ conviction, confirmation }: { conviction: boolean; confirmation: boolean }) {
  const keys = [
    { name: "Conviction", short: "Conv", on: conviction },
    { name: "Confirmation", short: "Conf", on: confirmation },
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
          <span key={k.name} className="pip-wrap">
            <span className={`pip ${k.on ? "on" : ""}`} />
            <span className={`pip-lbl${k.on ? " on" : ""}`} aria-hidden="true">
              {k.short}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
