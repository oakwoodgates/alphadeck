import { useState } from "react";
import type { ReactNode } from "react";

import type { CallCardResponse, ThesisSummary } from "../api/hooks";
import { useCalls, useDataFlags, useSetArchived, useTheses } from "../api/hooks";
import { countdownOrigin, useAppClock } from "../clock";
import { DataFlagMark } from "../cockpit/DataFlagMark";
import { flagsBySid } from "../cockpit/dataFlags";
import {
  daysFrom,
  fmtDate,
  isPastAsof,
  recomputeNote,
  RECOMPUTE_TOOLTIP,
  tickerLabel,
  verdictLabel,
} from "../util/format";
import { ThesisCard } from "./ThesisCard";
import { useRowCap } from "./useRowCap";

/** The Decision Queue wraps; collapsed it holds to this many rows and says how many it is holding back. */
const DQ_ROWS = 2;

const COLUMNS = [
  { state: "incubating", cls: "incub", label: "Incubating", hint: "quiet · do not act" },
  { state: "warming", cls: "warm", label: "Warming", hint: "stirring" },
  { state: "armed", cls: "armed", label: "Armed", hint: "act now" },
  { state: "managing", cls: "manage", label: "Managing", hint: "in position" },
] as const;

interface Props {
  header?: ReactNode;
  asof: string;
  /** Open the Cockpit for a thesis. `nameKey` (optional) deep-links a member's panel (?name=) —
   *  the Decision Queue passes the armed headline name so the Cockpit lands on it (same idiom as
   *  the Scoreboard's row click). A column card omits it (opens the thesis, no name pre-selected). */
  onSelect: (thesisId: string, nameKey?: string) => void;
}

interface Row {
  thesis: ThesisSummary;
  call: CallCardResponse;
  /** This card is the PREVIOUS as-of's call, kept on screen while the new one computes
   *  (`placeholderData: keepPreviousData`) — it renders dimmed and tagged, never as the answer for
   *  the date in the dial, and it is barred from the Decision Queue. */
  stale: boolean;
}

export function Board({ header, asof, onSelect }: Props) {
  // the Board is the ONE consumer that asks for archived theses — they render in the collapsed
  // section below (visible + restorable, never vanished); their calls are NOT computed (no cost)
  const thesesQ = useTheses(true);
  const setArchived = useSetArchived();
  const all = thesesQ.data ?? [];
  const theses = all.filter((t) => !t.archived);
  const archived = all.filter((t) => t.archived);
  // the whole summary rides in (not just the ids) so the calls can fire smallest-basket-first —
  // progressive PAINT only: under one backend worker this orders what lands first, it does not
  // shorten the total (see useCalls)
  const callResults = useCalls(theses, asof);
  // F1 — "past" is before the server's current night (the default view, read live), never before the browser's date
  const appClock = useAppClock();
  const { currentNight } = appClock;
  // S3 — the data flags across the active baskets (compute-on-read, display-only): a ticker the Board shows
  // (the Decision Queue's headline name, a single-name card) carries the same quiet mark as its Cockpit row
  // when a flag reaches a number on it. A separate read: the cards never wait on it.
  const flagged = flagsBySid(useDataFlags(asof).data);
  const flaggedByTicker = new Map(
    [...flagged.values()].filter((m) => m.ticker).map((m) => [m.ticker as string, m]),
  );

  // pair each thesis with its resolved call — a card appears once its call computes
  const rows: Row[] = theses
    .map((thesis, i) => ({
      thesis,
      call: callResults[i]?.data,
      stale: Boolean(callResults[i]?.isPlaceholderData),
    }))
    .filter((r): r is Row => Boolean(r.call));
  // keep-it-visible (#2 + BOARD.md "no silent loudness"): a thesis whose /call ERRORED (a 500, a
  // mid-flight delete) must NOT drop off the board with no trace — surface it as a placeholder with
  // an error affordance instead of filtering it into oblivion. Errored = isError with no data.
  const erroredRows = theses.filter((_t, i) => callResults[i]?.isError && !callResults[i]?.data);
  // The Decision Queue is the ONE loud element (#7), so it must never prompt action on a previous
  // date: a placeholder (previous as-of) call is barred from it. Its dimmed card still shows in the
  // column — column = context, queue = the action prompt.
  const armedRows = rows.filter((r) => r.call.state === "armed" && !r.stale);
  // keep-it-visible (#2 + the #9 instinct): the strip wraps, and past DQ_ROWS rows it folds the rest behind a
  // "+N more" that is always on screen and one click from showing every item. The queue ORDER is never touched —
  // the fold is by position in the existing order, not by any ranking of the theses (#4).
  const [dqOpen, setDqOpen] = useState(false);
  const { ref: dqRef, cap: dqCap } = useRowCap<HTMLDivElement>(DQ_ROWS);
  const dqFolded = dqCap.height !== null && !dqOpen;
  const dqHeld = Math.max(0, armedRows.length - dqCap.firstClipped);
  // "Still working" must stay honest now that the calls are QUEUED (C3): a query waiting for a slot
  // is pending with fetchStatus idle, so `isLoading` (v5 = pending AND fetching) reads false for it
  // — keying the all-clear off isLoading alone would flash "Nothing to do ✓" with eight calls still
  // queued. Pending-or-fetching covers queued, in-flight, and recomputing-behind-a-placeholder.
  const computing = callResults.some((r) => r.isPending || r.isFetching);
  // any card on screen belongs to the previous as-of (the scrub is still computing)
  const recomputing = rows.some((r) => r.stale);
  // the queue's quiet line, in priority order: a previous-date board can NEVER read as an all-clear
  const dqNote = recomputing
    ? "Recomputing…"
    : computing
      ? "Computing…"
      : erroredRows.length > 0
        ? // don't sound the all-clear when a call didn't compute — it might have been armed
          "Some calls didn't compute — see below."
        : "Nothing armed. Nothing to do. ✓";

  return (
    <div className="board-shell">
      {header}

      {/* F11 — honest loudness (#7 / WB#3): every column shares the ONE as-of dial, so a past scrub
          makes EVERY card on screen a recompute, not the recorded call-of-record — a per-card badge
          would be true of every row and so be noise (WB#3); one quiet line for the whole board says
          it once. Absent on the live/today board. */}
      {isPastAsof(asof, currentNight) && (
        <div className="board-recompute" title={RECOMPUTE_TOOLTIP}>
          {recomputeNote(asof)}
        </div>
      )}

      {/* Decision Queue — the loud, armed-only anti-forgetting strip */}
      <div className="dq">
        <div className="dq-label">
          <span className="pulse" />
          Decision Queue
        </div>
        <div
          id="dq-items"
          ref={dqRef}
          className={`dq-items${dqOpen ? " open" : ""}`}
          style={dqFolded ? { maxHeight: dqCap.height ?? undefined } : undefined}
        >
          {armedRows.length > 0 ? (
            armedRows.map(({ thesis, call }, i) => {
              // deep-link the headline armed NAME (?name=) so the Cockpit opens on it — ticker
              // preferred, security_id as the precise fallback (the Scoreboard row idiom + what
              // resolveNameKey resolves against); a single-name thesis just carries its own ticker
              const headline = call.armed_members[0];
              const nameKey = headline?.ticker ?? headline?.security_id ?? undefined;
              // the act-by clock: on an Armed card `arm_until` IS the headline name's entry window, and
              // `entry_window_closing` is the assembler's own flag for it (Q14) — read, never re-derived
              // here, and loud (warm) only when set. The queue ORDER is unchanged: this states the clock,
              // it does not rank the theses by it.
              // F2 — a COUNTDOWN: from the action day on a live view (a window ending that day reads "act
              // today"), from the as-of on a past one, where the date rides with the count
              const armDays = daysFrom(countdownOrigin(call.asof, appClock), call.arm_until);
              const pastView = isPastAsof(call.asof, currentNight);
              const closing = Boolean(headline?.entry_window_closing);
              return (
                <button
                  type="button"
                  className={`dq-item${dqFolded && i >= dqCap.firstClipped ? " held" : ""}`}
                  key={thesis.id}
                  onClick={() => onSelect(thesis.id, nameKey)}
                >
                  {/* a theme shows its single TOP-RANKED actionable name (anti-flooding), with a quiet
                      "+N" hint that a ranked menu sits behind it — never every member in the queue */}
                  <b>{headline?.ticker ?? tickerLabel(thesis.ticker, thesis.basket_size)}</b>
                  {headline?.security_id && (
                    <DataFlagMark member={flagged.get(headline.security_id)} />
                  )}
                  {call.armed_members.length > 1 && (
                    <span className="dq-more">+{call.armed_members.length - 1}</span>
                  )}
                  {call.conviction_grade && (
                    <span className={`grade ${call.conviction_grade}`}>
                      {call.conviction_grade.toUpperCase()}
                    </span>
                  )}
                  <span className="dq-nm" title={thesis.name}>
                    {verdictLabel(call.verdict)} · {thesis.name}
                  </span>
                  {armDays !== null && (
                    <span
                      className={`dq-clock${closing ? " closing" : ""}`}
                      title={
                        closing
                          ? "entry window closing — the last trading sessions in which this entry signal is valid; it lapses after this date"
                          : `the entry window — this entry signal is valid through ${fmtDate(call.arm_until)}`
                      }
                    >
                      {armDays < 0
                        ? "entry window lapsed"
                        : armDays === 0
                          ? "act today"
                          : `act within ${armDays}d`}
                      {pastView && call.arm_until && ` · ${fmtDate(call.arm_until)}`}
                      {closing && " · entry window closing"}
                    </span>
                  )}
                </button>
              );
            })
          ) : (
            <span className="dq-empty">{dqNote}</span>
          )}
        </div>
        {dqCap.height !== null && (
          <button
            type="button"
            className="dq-toggle"
            aria-expanded={dqOpen}
            aria-controls="dq-items"
            title={
              dqOpen
                ? `fold the queue back to ${DQ_ROWS} rows`
                : `${armedRows.length} armed — show every one`
            }
            onClick={() => setDqOpen((o) => !o)}
          >
            {dqOpen ? "show fewer" : `+${dqHeld} more`}
          </button>
        )}
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
                {colRows.map(({ thesis, call, stale }) => (
                  // the archive control is a SIBLING of the card (the card is itself a <button> —
                  // nesting one inside would be the nested-button trap), hover-quiet
                  <div className={`card-wrap${stale ? " recomputing" : ""}`} key={thesis.id}>
                    <ThesisCard
                      thesis={thesis}
                      call={call}
                      onSelect={onSelect}
                      flags={
                        thesis.basket_size === 1 && thesis.ticker
                          ? (flaggedByTicker.get(thesis.ticker) ?? null)
                          : null
                      }
                    />
                    {/* C2 honest rendering: a kept previous-as-of card is dimmed AND says which
                        date it is for — the scrub keeps context instead of blanking the board, and
                        the card never passes itself off as the dial's answer. */}
                    {stale && (
                      <span className="stale-tag" title="the as-of you picked is still computing">
                        · as of {fmtDate(call.asof)}
                      </span>
                    )}
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
          calls are not computed). Quiet by design: collapsed, gray, out of the columns. */}
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
