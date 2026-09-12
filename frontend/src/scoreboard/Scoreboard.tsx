import { Fragment, useState, type ReactNode } from "react";

import type { ScoreboardEpisodeOut, ScoreboardThesisOut } from "../api/hooks";
import { useScoreboard } from "../api/hooks";
import { Drawer } from "../components/Drawer";
import { fmtDate } from "../util/format";
import { EpisodeRow } from "./EpisodeRow";
import { EpisodeScorecard } from "./EpisodeScorecard";
import { LedgerHead } from "./LedgerHead";
import { MetricsStrip } from "./MetricsStrip";
import { ReplayPanel } from "./ReplayPanel";
import {
  fmtReturn,
  groupCount,
  groupHint,
  groupToneClass,
  ledgerColCount,
  maturityHorizon,
  type LedgerView,
} from "./rows";
import {
  nextLedgerSort,
  sortEpisodes,
  sortSpans,
  type LedgerSort,
  type LedgerSortColId,
} from "./sortLedger";

// The Scoreboard (SCORE) — the episode ledger over the forward record: what the platform said,
// what the operator did, what happened. Ledger-first (the aggregate strip stays quiet until n
// accrues past the gate); archived groups fold closed but are never dropped; every mark on a row
// is an exception, not a constant. Read-only: the write surface stays the Cockpit's rail.

type Props = {
  header?: ReactNode;
  asof: string;
  /** nameKey (when the row has a name) deep-links that member's panel in the Cockpit (?name=). */
  onSelect: (thesisId: string, nameKey?: string) => void;
};

function SpanRow({
  t,
  onSelect,
  view,
  sort,
}: {
  t: ScoreboardThesisOut;
  onSelect: (id: string, nameKey?: string) => void;
  view: LedgerView;
  /** the active column sort — spans re-rank among THEMSELVES, never interleaved with the episodes
   *  above them (a logged take is not an arm episode). See `sortLedger.ts`. */
  sort: LedgerSort | null;
}) {
  // off-record spans (overrides live here) — rendered per span under the thesis group
  return (
    <>
      {sortSpans(t.operator_spans, sort).map((s) => {
        const ret = fmtReturn(s.operator_return);
        // the OVERRIDE / THESIS-LEVEL marks are the span's status — identical in both views
        const statusCell = (
          <td className="sb-status">
            {s.override && (
              <span
                className="sb-badge b-ovr"
                title="entered while the platform withheld — the logged override, with its outcome"
              >
                OVERRIDE
              </span>
            )}
            {s.thesis_level && (
              <span className="sb-badge b-lvl" title="logged without a name — unpriced, never guessed">
                THESIS-LEVEL
              </span>
            )}
          </td>
        );
        return (
          <tr
            key={s.take_id}
            className="sb-row sb-span"
            // a thesis-level span has no name — the click opens the bare Cockpit
            onClick={() => onSelect(t.thesis_id, s.ticker ?? s.security_id ?? undefined)}
          >
            <td className="tk">{s.ticker ?? (s.thesis_level ? "◇" : "—")}</td>
            <td className="sb-armed">{fmtDate(s.take_date)}</td>
            {/* a span is a logged TAKE, not an arm — so it has no de-arm either. Dash it, the same
                way the platform-lens columns below are dashed, rather than leaving the cell short
                and shifting every following cell one column left of its header. */}
            <td className="sb-armed sb-dearm">—</td>
            {view === "timing" ? (
              <>
                {/* an operator span carries NO platform timing lens (path / forward / the four
                    excursions / past-peak are episode-level, not a logged take) — dash the timing
                    columns, keep the row visible (interaction principle #2 — pruning hides, it
                    never vanishes). This branch is the FOURTH place a Timing column has to land,
                    after LedgerHead, EpisodeRow and `ledgerColCount`; miss it and every span cell
                    sits one column left of its header, silently, because a short <tr> just renders
                    narrow. */}
                <td className="sb-path">—</td>
                <td className="sb-ret">
                  <span className="ret">—</span>
                </td>
                <td className="sb-ret">
                  <span className="ret">—</span>
                </td>
                <td className="sb-ret sb-wick">
                  <span className="ret">—</span>
                </td>
                <td className="sb-ret">
                  <span className="ret">—</span>
                </td>
                <td className="sb-ret sb-wick">
                  <span className="ret">—</span>
                </td>
                <td className="sb-pp">—</td>
                {statusCell}
              </>
            ) : (
              <>
                <td className="sb-why">
                  <span className="sb-stance">
                    platform said {s.call_verdict_at_take ?? s.call_state_at_take ?? "—"}
                  </span>
                </td>
                <td className="exitby">—</td>
                {statusCell}
                <td className="sb-ret">
                  <span className={`ret ${ret.cls}`}>{ret.text}</span>
                  {s.operator_return != null && (
                    <span className="sb-retlabel"> {s.running ? "running" : "realized"}</span>
                  )}
                </td>
                {/* Peak is an EPISODE lens — the platform's realized high over an arm window. A span
                    is a logged take, not an arm, so it has none: dash it, exactly as the Timing
                    branch above dashes the platform timing columns. It is also what keeps this row's
                    cell count equal to LedgerHead's (`ledgerColCount`) — the Peak column was added to
                    the Summary head and the episode row but not here, which shifted every span cell
                    one column left of its header. */}
                <td className="sb-ret">
                  <span className="ret">—</span>
                </td>
                <td className="sb-op sb-op-took">
                  took {s.take_date}
                  {s.entry_price != null && ` @ ${s.entry_price}`}
                  {(s.entry_inferred || s.exit_inferred) && (
                    <span className="sb-inf" title="no fill price logged — the close stands in">
                      ≈
                    </span>
                  )}
                  {s.reason && <span className="sb-reason"> · {s.reason}</span>}
                </td>
              </>
            )}
          </tr>
        );
      })}
    </>
  );
}

export function Scoreboard({ header, asof, onSelect }: Props) {
  const { data, isLoading, error } = useScoreboard(asof);
  // the episode-scorecard drawer's open episode — local state only (no URL param this slice); a
  // click opens it, the drawer's ✕/backdrop/Esc close it, and the ledger underneath never rerenders.
  const [openEp, setOpenEp] = useState<ScoreboardEpisodeOut | null>(null);
  // the Summary | Timing ledger view (Slice 2) — local component state, no URL param this slice; a
  // pure VIEW control that swaps the ledger's middle columns (never the rows or the data).
  const [view, setView] = useState<LedgerView>("summary");
  // the ledger's column sort — null is the record's own order, and the 3-state header cycle
  // (desc → asc → off) always gets back to it. A WITHIN-GROUP re-order: the thesis groups never
  // move and no row is ever filtered out (see `sortLedger.ts`).
  const [sort, setSort] = useState<LedgerSort | null>(null);
  const onSort = (col: LedgerSortColId) => setSort((cur) => nextLedgerSort(cur, col));
  const cols = ledgerColCount(view); // the group/note-row colSpan tracks the rendered column count
  // fold state per thesis (archived groups START folded — present, quiet, never dropped)
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const isOpen = (t: ScoreboardThesisOut) => toggled.has(t.thesis_id) === t.archived;
  const toggle = (id: string) =>
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const summary = data?.summary;

  return (
    <div className="board-shell sb-shell">
      {header}

      {isLoading && <div className="center-note">Scoring the record…</div>}
      {error != null && (
        <div className="center-note err">Scoreboard unavailable — is the backend on :8000?</div>
      )}

      {data && summary && (
        <div className="sb-body">
          <div className="sb-banner">{summary.banner}</div>
          {/* the record-freshness marker (2a) — is the call-of-record current NOW? Shown ONLY on the
              live view (asof >= today): staleness answers "current now", not "as of a past date", so a
              scrubbed-back view suppresses it (decision #2). Loud only when stale; quiet when current
              or never-begun (honest loudness). Mirrors the Admin page's freshness copy. */}
          {asof >= summary.today &&
            (summary.record_edge == null ? (
              <div className="sb-freshline sb-fresh">record hasn&apos;t begun yet</div>
            ) : summary.stale ? (
              <div className="sb-freshline sb-stale">
                record last advanced <b>{summary.record_edge}</b> · <b>{summary.days_behind}</b>{" "}
                expected run(s) behind
              </div>
            ) : (
              <div className="sb-freshline sb-fresh">
                record current · last advanced <b>{summary.record_edge}</b>
              </div>
            ))}
          {/* the reconstructed-nights line: the record path scores honest (nightly) rows only — a row
              a backfill reconstructed never opens or closes an episode — so the nights it set aside
              are named ONCE, quietly, here; never as a per-row chip (with the filter in place a
              reconstructed row produces no ledger row). Asof-capped by the backend, so a scrubbed
              view names only nights it can see. Rendered only when non-empty (honest loudness). */}
          {summary.reconstructed_nights.length > 0 && (
            <div
              className="sb-freshline sb-fresh"
              title={`reconstructed by a backfill with the clock pinned, on today's basket — not scored: ${summary.reconstructed_nights.join(", ")}`}
            >
              {summary.reconstructed_nights.length}{" "}
              {summary.reconstructed_nights.length === 1 ? "night" : "nights"} reconstructed by a
              backfill · not scored
            </div>
          )}
          <div className="sb-counts">
            <span>{summary.n_episodes} episodes</span>
            <span>{summary.n_open} open</span>
            <span>{summary.n_matured} matured</span>
            <span>{summary.n_censored} censored</span>
            {summary.n_ingest_flagged > 0 && (
              <span>{summary.n_ingest_flagged} ingest-flagged</span>
            )}
            <span className="sb-sep">·</span>
            <span>{summary.n_takes} takes</span>
            <span>{summary.n_passes} passes</span>
            <span>{summary.n_overrides} overrides</span>
            {summary.n_voided > 0 && <span>{summary.n_voided} voided</span>}
          </div>

          {/* the Summary | Timing view toggle (Slice 2) — a VIEW control, so it renders ALWAYS (the
              honest-loudness "a control that doesn't discriminate shouldn't render" rule is about
              per-row badges, not a view switch). Flips which columns render; the rows never move. */}
          <div className="sb-viewtoggle" role="group" aria-label="ledger view">
            <button
              type="button"
              className={view === "summary" ? "on" : ""}
              aria-pressed={view === "summary"}
              onClick={() => setView("summary")}
            >
              Summary
            </button>
            <button
              type="button"
              className={view === "timing" ? "on" : ""}
              aria-pressed={view === "timing"}
              onClick={() => setView("timing")}
            >
              Timing
            </button>
          </div>

          <MetricsStrip metrics={summary.metrics} minN={summary.min_n} />

          {/* the maturity horizon (2e) — the countdown behind the mute gate. Asof-pure (a scrubbed
              view's countdown from that asof is coherent), so no today-gate — unlike the 2a
              staleness line above. Rendered only when something lies ahead (honest loudness). */}
          {maturityHorizon(summary) != null && (
            <div
              className="sb-horizon"
              title="a projection over currently-recorded episodes — new arms or de-arms shift it"
            >
              {maturityHorizon(summary)}
            </div>
          )}

          {summary.n_episodes === 0 && summary.n_takes === 0 && (
            <div className="sb-empty">
              No arm episodes on the record yet
              {summary.record_began
                ? ` — it began ${fmtDate(summary.record_began)} and accrues forward (no backfill).`
                : " — the record starts with the first daily call-of-record."}
            </div>
          )}

          {/* the ledger takes its natural width and scrolls sideways only when the window is
              narrower than that — it never wraps a row or truncates a cell to fit. No height cap:
              the page keeps the vertical scroll. See .sb-scroll. */}
          <div className="sb-scroll">
          <table className="basket sb-ledger">
            <LedgerHead view={view} returnHeader="Record return" sort={sort} onSort={onSort} />
            <tbody>
              {data.theses.map((t) => (
                <Fragment key={t.thesis_id}>
                  <tr className={`grp ${groupToneClass(t)}`}>
                    <td colSpan={cols}>
                      <button
                        type="button"
                        className="grp-h"
                        aria-expanded={isOpen(t)}
                        onClick={() => toggle(t.thesis_id)}
                      >
                        {/* the heading rides its own span so it can stick to the left of the
                            horizontal scroller — a group row spans the whole table, so the thesis
                            name would otherwise scroll out of view with the columns. */}
                        <span className="grp-lbl">
                          <span className="chev">▾</span>
                          <span className="lbl">{t.name}</span>
                          {t.archived && <span className="sb-badge b-arch">ARCHIVED</span>}
                          <em className="hint">· {groupHint(t)}</em>
                          <span className="ct">· {groupCount(t)}</span>
                        </span>
                      </button>
                    </td>
                  </tr>
                  {t.record_error && isOpen(t) && (
                    <tr className="sb-note-row">
                      <td colSpan={cols} className="sb-error">
                        record error: {t.record_error}
                      </td>
                    </tr>
                  )}
                  {t.decision_anomaly && isOpen(t) && (
                    <tr className="sb-note-row">
                      <td colSpan={cols} className="sb-anomaly">
                        decision log anomaly: {t.decision_anomaly}
                      </td>
                    </tr>
                  )}
                  {/* sorted WITHIN the group — the group is the ledger's spine, so the rows re-rank
                      under their own thesis heading and the headings never move. A re-order, never
                      a filter: `sortEpisodes` returns the same length it was given. The key is the
                      episode's own identity rather than its index, or a re-sort would hand React
                      the same key for a different episode. */}
                  {isOpen(t) &&
                    sortEpisodes(t.episodes, sort).map((ep) => (
                      <EpisodeRow
                        key={`${ep.security_id}-${ep.arm_date}-${ep.dearm_date ?? "open"}`}
                        ep={ep}
                        thesisId={t.thesis_id}
                        onSelect={onSelect}
                        onOpenScorecard={setOpenEp}
                        view={view}
                      />
                    ))}
                  {isOpen(t) && <SpanRow t={t} onSelect={onSelect} view={view} sort={sort} />}
                  {isOpen(t) && !groupCount(t) && !t.record_error && (
                    <tr className="sb-note-row">
                      <td colSpan={cols} className="sb-quietline">
                        {t.warming_since
                          ? `warming since ${fmtDate(t.warming_since)} — the withheld window is accruing`
                          : "no arm episodes on this record"}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          </div>

          <ReplayPanel onSelect={onSelect} onOpenScorecard={setOpenEp} view={view} />
        </div>
      )}

      {/* the episode scorecard, in a reusable slide-out. A sibling overlay (like the Cockpit's
          NamePanel): opening/closing never touches the ledger. Guarded content so a null episode
          is never constructed while the drawer is closed. */}
      <Drawer
        open={openEp != null}
        onClose={() => setOpenEp(null)}
        title={
          openEp ? (
            <>
              {openEp.ticker ?? "—"} <span className="sc-sub">scorecard</span>
            </>
          ) : undefined
        }
      >
        {openEp && (
          <EpisodeScorecard
            ep={openEp}
            thesisName={data?.theses.find((t) => t.thesis_id === openEp.thesis_id)?.name}
            asof={asof}
          />
        )}
      </Drawer>
    </div>
  );
}
