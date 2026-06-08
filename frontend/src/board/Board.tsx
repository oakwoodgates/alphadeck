import type { CallCardResponse, ThesisSummary } from "../api/hooks";
import { useCalls, useTheses } from "../api/hooks";
import { tickerLabel, verdictLabel } from "../util/format";
import { ThesisCard } from "./ThesisCard";

const COLUMNS = [
  { state: "incubating", cls: "incub", label: "Incubating", hint: "quiet · do not act" },
  { state: "warming", cls: "warm", label: "Warming", hint: "stirring" },
  { state: "armed", cls: "armed", label: "Armed", hint: "act now" },
  { state: "managing", cls: "manage", label: "Managing", hint: "in position" },
] as const;

interface Props {
  asof: string;
  onAsofChange: (asof: string) => void;
  onSelect: (thesisId: string) => void;
}

interface Row {
  thesis: ThesisSummary;
  call: CallCardResponse;
}

export function Board({ asof, onAsofChange, onSelect }: Props) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];
  const callResults = useCalls(
    theses.map((t) => t.id),
    asof,
  );

  // pair each thesis with its resolved call — a card appears once its call computes
  const rows: Row[] = theses
    .map((thesis, i) => ({ thesis, call: callResults[i]?.data }))
    .filter((r): r is Row => Boolean(r.call));
  const armedRows = rows.filter((r) => r.call.state === "armed");
  const computing = callResults.some((r) => r.isLoading);

  return (
    <div className="board-shell">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <nav className="nav">
          <a className="on">Board</a>
          <a className="stub">Workbench</a>
          <a className="stub">Scoreboard</a>
        </nav>
        <div className="spacer" />
        <label className="asof">
          as-of
          <input type="date" value={asof} onChange={(e) => onAsofChange(e.target.value)} />
        </label>
      </header>

      {/* Decision Queue — the loud, armed-only anti-forgetting strip */}
      <div className="dq">
        <div className="dq-label">
          <span className="pulse" />
          Decision Queue
        </div>
        <div className="dq-items">
          {armedRows.length > 0 ? (
            armedRows.map(({ thesis, call }) => (
              <button
                type="button"
                className="dq-item"
                key={thesis.id}
                onClick={() => onSelect(thesis.id)}
              >
                {/* a theme shows its single TOP-RANKED actionable name (anti-flooding), with a quiet
                    "+N" hint that a ranked menu sits behind it — never every member in the queue */}
                <b>{call.armed_members[0]?.ticker ?? tickerLabel(thesis.ticker, thesis.basket_size)}</b>
                {call.armed_members.length > 1 && (
                  <span className="dq-more">+{call.armed_members.length - 1}</span>
                )}
                {call.conviction_grade && (
                  <span className={`grade ${call.conviction_grade}`}>
                    {call.conviction_grade.toUpperCase()}
                  </span>
                )}
                <span>
                  {verdictLabel(call.verdict)} · {thesis.name}
                </span>
              </button>
            ))
          ) : (
            <span className="dq-empty">
              {computing ? "Computing…" : "Nothing armed. Nothing to do. ✓"}
            </span>
          )}
        </div>
      </div>

      <div className="board">
        {COLUMNS.map((col) => {
          const colRows = rows.filter((r) => r.call.state === col.state);
          return (
            <section className={`col ${col.cls}`} key={col.state}>
              <div className="col-head">
                <span className="swatch" />
                <h2>{col.label}</h2>
                <span className="hint">{col.hint}</span>
                <span className="n">{colRows.length}</span>
              </div>
              <div className="col-body">
                {colRows.map(({ thesis, call }) => (
                  <ThesisCard key={thesis.id} thesis={thesis} call={call} onSelect={onSelect} />
                ))}
                {colRows.length === 0 && <div className="col-empty">{computing ? "…" : "—"}</div>}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
