import type { CallCardResponse, ThesisSummary } from "../api/hooks";
import { useCalls, useSetArchived, useTheses } from "../api/hooks";
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
  /** Open the Cockpit for a thesis. `nameKey` (optional) deep-links a member's panel (?name=) —
   *  the Decision Queue passes the armed headline name so the Cockpit lands on it (same idiom as
   *  the Scoreboard's row click). A column card omits it (opens the thesis, no name pre-selected). */
  onSelect: (thesisId: string, nameKey?: string) => void;
  onOpenWorkbench: () => void;
  onOpenScoreboard: () => void;
  onOpenAdmin: () => void;
}

interface Row {
  thesis: ThesisSummary;
  call: CallCardResponse;
}

export function Board({
  asof,
  onAsofChange,
  onSelect,
  onOpenWorkbench,
  onOpenScoreboard,
  onOpenAdmin,
}: Props) {
  // the Board is the ONE consumer that asks for archived theses — they render in the collapsed
  // section below (visible + restorable, never vanished); their calls are NOT computed (no cost)
  const thesesQ = useTheses(true);
  const setArchived = useSetArchived();
  const all = thesesQ.data ?? [];
  const theses = all.filter((t) => !t.archived);
  const archived = all.filter((t) => t.archived);
  const callResults = useCalls(
    theses.map((t) => t.id),
    asof,
  );

  // pair each thesis with its resolved call — a card appears once its call computes
  const rows: Row[] = theses
    .map((thesis, i) => ({ thesis, call: callResults[i]?.data }))
    .filter((r): r is Row => Boolean(r.call));
  // keep-it-visible (#2 + BOARD.md "no silent loudness"): a thesis whose /call ERRORED (a 500, a
  // mid-flight delete) must NOT drop off the board with no trace — surface it as a placeholder with
  // an error affordance instead of filtering it into oblivion. Errored = isError with no data.
  const erroredRows = theses.filter((_t, i) => callResults[i]?.isError && !callResults[i]?.data);
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
          <a onClick={onOpenWorkbench}>Workbench</a>
          <a onClick={onOpenScoreboard}>Scoreboard</a>
          <a onClick={onOpenAdmin}>Admin</a>
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
            armedRows.map(({ thesis, call }) => {
              // deep-link the headline armed NAME (?name=) so the Cockpit opens on it — ticker
              // preferred, security_id as the precise fallback (the Scoreboard row idiom + what
              // resolveNameKey resolves against); a single-name thesis just carries its own ticker
              const headline = call.armed_members[0];
              const nameKey = headline?.ticker ?? headline?.security_id ?? undefined;
              return (
                <button
                  type="button"
                  className="dq-item"
                  key={thesis.id}
                  onClick={() => onSelect(thesis.id, nameKey)}
                >
                  {/* a theme shows its single TOP-RANKED actionable name (anti-flooding), with a quiet
                      "+N" hint that a ranked menu sits behind it — never every member in the queue */}
                  <b>{headline?.ticker ?? tickerLabel(thesis.ticker, thesis.basket_size)}</b>
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
              );
            })
          ) : (
            <span className="dq-empty">
              {computing
                ? "Computing…"
                : erroredRows.length > 0
                  ? // don't sound the all-clear when a call didn't compute — it might have been armed
                    "Some calls didn't compute — see below."
                  : "Nothing armed. Nothing to do. ✓"}
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
                  // the archive control is a SIBLING of the card (the card is itself a <button> —
                  // nesting one inside would be the nested-button trap), hover-quiet
                  <div className="card-wrap" key={thesis.id}>
                    <ThesisCard thesis={thesis} call={call} onSelect={onSelect} />
                    <button
                      type="button"
                      className="card-x"
                      title="archive — off the board and out of the nightly cron; restorable below, nothing deleted"
                      aria-label={`archive ${thesis.name}`}
                      disabled={setArchived.isPending}
                      onClick={() => setArchived.mutate({ thesisId: thesis.id, archived: true })}
                    >
                      ✕
                    </button>
                  </div>
                ))}
                {colRows.length === 0 && <div className="col-empty">{computing ? "…" : "—"}</div>}
              </div>
            </section>
          );
        })}
      </div>

      {/* errored calls — VISIBLE, never silently dropped (#2). An exception that wants the operator's
          eyes (honest loudness), so it renders loud, not tucked away. Each row opens the Cockpit,
          which mirrors the error on its rail (callQ.error) and refetches. */}
      {erroredRows.length > 0 && (
        <div className="board-errs" role="alert">
          <div className="be-label">⚠ Calls that didn&apos;t compute ({erroredRows.length})</div>
          <div className="be-items">
            {erroredRows.map((t) => (
              <button
                type="button"
                className="be-item"
                key={t.id}
                title="the call failed to compute — open the thesis to see the error / retry"
                onClick={() => onSelect(t.id)}
              >
                <b>{tickerLabel(t.ticker, t.basket_size)}</b>
                <span className="be-nm">{t.name}</span>
                <span className="be-tag">call failed to compute</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* archived — visible + restorable, never vanished (an explicit, reversible filter; their
          calls are not computed). Quiet by design: collapsed, grey, out of the columns. */}
      {archived.length > 0 && (
        <details className="arch-sect">
          <summary>Archived ({archived.length})</summary>
          {archived.map((t) => (
            <div className="arch-row" key={t.id}>
              <span className="arch-nm">{t.name}</span>
              <span className="arch-sz">{tickerLabel(t.ticker, t.basket_size)}</span>
              <button
                type="button"
                className="wb-mini ghost"
                disabled={setArchived.isPending}
                aria-label={`restore ${t.name}`}
                onClick={() => setArchived.mutate({ thesisId: t.id, archived: false })}
              >
                restore
              </button>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
