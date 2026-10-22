import { Fragment, useState, type ReactNode } from "react";

import type {
  OperatorSpanOut,
  ScoreboardEditionOut,
  ScoreboardEpisodeOut,
  ScoreboardThesisOut,
} from "../api/hooks";
import { useScoreboard, useScoreboardEditions } from "../api/hooks";
import { Drawer } from "../components/Drawer";
import { fmtDate } from "../util/format";
import { annotatedNightsLine, annotatedNightsTitle, nextAnnotatedOverride } from "./annotated";
import { editionLabel, editionNote, editionTitle, isRerun } from "./editions";
import { lateNightsLine, lateNightsTitle, nextLateOverride } from "./late";
import { EpisodeRow } from "./EpisodeRow";
import { EpisodeScorecard } from "./EpisodeScorecard";
import {
  filterEpisodes,
  filterMatchCounts,
  filterSpans,
  groupCountLabel,
  LEDGER_FILTERS,
  ledgerTally,
  toggleLedgerFilter,
  type LedgerFilterId,
} from "./filters";
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
  /** The EDITION asked for (?edition=, editions S4): null = the server's default; 0 = the original record. */
  edition?: number | null;
  /** Picks an edition — the route writes it to ?edition= (a view dial: it REPLACES history). */
  onEditionChange?: (edition: number) => void;
};

// Beyond this many editions the segmented control would crowd the header — a plain list picker takes over.
const SEGMENTED_MAX = 5;

/** The header's quiet edition label + switcher (S4). Renders only when there is something to switch
 *  between (a one-option control does not discriminate, #7): with nothing published the page looks exactly
 *  as it did before editions. The SERVED edition is the selected one, so a default-edition read marks the
 *  right button with no ?edition= in the URL. */
function EditionSwitcher({
  editions,
  served,
  onPick,
}: {
  editions: readonly ScoreboardEditionOut[];
  served: number;
  onPick?: (edition: number) => void;
}) {
  if (editions.length < 2) return null;
  if (editions.length > SEGMENTED_MAX) {
    return (
      <select
        className="sb-editionpick"
        aria-label="edition"
        value={served}
        onChange={(e) => onPick?.(Number(e.target.value))}
      >
        {editions.map((e) => (
          <option key={e.number} value={e.number} title={editionTitle(e)}>
            {editionLabel(e)}
          </option>
        ))}
      </select>
    );
  }
  return (
    <div className="sb-viewtoggle sb-editions" role="group" aria-label="edition">
      {editions.map((e) => (
        <button
          key={e.number}
          type="button"
          className={e.number === served ? "on" : ""}
          aria-pressed={e.number === served}
          title={editionTitle(e)}
          onClick={() => onPick?.(e.number)}
        >
          {editionLabel(e)}
        </button>
      ))}
    </div>
  );
}

function SpanRow({
  t,
  spans,
  onSelect,
  view,
  sort,
}: {
  t: ScoreboardThesisOut;
  /** the group's spans AFTER the chip filter — the host filters, then this sorts, so `sortSpans`
   *  keeps its "same length in, same length out" contract and the two steps stay separable. */
  spans: readonly OperatorSpanOut[];
  onSelect: (id: string, nameKey?: string) => void;
  view: LedgerView;
  /** the active column sort — spans re-rank among THEMSELVES, never interleaved with the episodes
   *  above them (a logged take is not an arm episode). See `sortLedger.ts`. */
  sort: LedgerSort | null;
}) {
  // off-record spans (overrides live here) — rendered per span under the thesis group
  return (
    <>
      {sortSpans(spans, sort).map((s) => {
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

export function Scoreboard({ header, asof, onSelect, edition = null, onEditionChange }: Props) {
  // D3b — the annotated-night READING for this view: null = the server's policy (the default), a bool =
  // the operator flipped it (include / set aside the phantom-arm nights). Local state like `view`/`sort`
  // below — no URL param, no browser storage — and one click back to the default (`annotated.ts`).
  const [annotatedOverride, setAnnotatedOverride] = useState<boolean | null>(null);
  // L1 — the late-night READING, the same tri-state (`late.ts`): rows recorded after their night's next
  // EDGAR open are set aside by default; one click scores them again for this view.
  const [lateOverride, setLateOverride] = useState<boolean | null>(null);
  const { data, isLoading, error } = useScoreboard(
    asof,
    true,
    annotatedOverride,
    lateOverride,
    edition,
  );
  // Editions S4 — the list for the switcher, and the edition this response actually SERVED (the server's
  // default when the URL names none). A re-run edition has no reconstructed / annotated / late rows and no
  // ingest history, so those lines, their toggles and the INGEST badge are hidden for it.
  const editionsQ = useScoreboardEditions();
  const servedEdition = data?.edition ?? null;
  const served = servedEdition?.number ?? 0;
  const rerun = isRerun(served);
  const editionLine = editionNote(servedEdition);
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
  // the status filter chips — the ledger's first true hide-from-view control, so it starts EMPTY
  // (never the default) and `Clear` is one click back to the whole record. Local state like `view`
  // and `sort` above: no URL param this slice, and no browser storage (repo convention). See
  // `filters.ts` for the predicates, the union rule, and why the LIVE ledger alone gets this.
  const [filters, setFilters] = useState<ReadonlySet<LedgerFilterId>>(() => new Set());
  const onFilter = (id: LedgerFilterId) => setFilters((cur) => toggleLedgerFilter(cur, id));
  const filtering = filters.size > 0;
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
  // both computed over the WHOLE record rather than the rendered subset: a chip's count must not
  // move when a group is folded or another chip is pressed, and the "showing X of Y" line has to
  // report what the FILTER hid (see `filters.ts`).
  const chipCounts = filterMatchCounts(data?.theses ?? []);
  const tally = ledgerTally(data?.theses ?? [], filters);

  return (
    <div className="board-shell sb-shell">
      {header}

      {isLoading && <div className="center-note">Scoring the record…</div>}
      {error != null && (
        <div className="center-note err">Scoreboard unavailable — is the backend on :8000?</div>
      )}

      {data && summary && (
        <div className="sb-body">
          {/* the edition label + switcher (S4) — quiet, and absent until a second edition exists */}
          <EditionSwitcher
            editions={editionsQ.data?.editions ?? []}
            served={served}
            onPick={onEditionChange}
          />
          {editionLine && (
            <div
              className="sb-freshline sb-fresh sb-editionline"
              title={servedEdition ? editionTitle(servedEdition) : undefined}
            >
              {editionLine}
            </div>
          )}
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
          {!rerun && summary.reconstructed_nights.length > 0 && (
            <div
              className="sb-freshline sb-fresh"
              title={`reconstructed by a backfill with the clock pinned, on today's basket — not scored: ${summary.reconstructed_nights.join(", ")}`}
            >
              {summary.reconstructed_nights.length}{" "}
              {summary.reconstructed_nights.length === 1 ? "night" : "nights"} reconstructed by a
              backfill · not scored
            </div>
          )}
          {/* the phantom-arm (annotated) nights line — D3b: rows annotated `exclude` (an arm that rested
              only on a stale-dated comparative revenue quarter) are set aside by the server's READ policy,
              exactly like reconstructed rows, and named once here. Rendered only when some night is
              annotated (a control that does not discriminate does not render), with the reading toggle:
              one click scores them again for this view, the next click returns to the default. Nothing is
              deleted either way — the line says which reading the numbers below came from. */}
          {!rerun && annotatedNightsLine(summary) && (
            <div className="sb-freshline sb-fresh sb-annotline" title={annotatedNightsTitle(summary)}>
              {annotatedNightsLine(summary)}{" "}
              <button
                type="button"
                className="sb-filterclear sb-annottoggle"
                aria-pressed={summary.annotated_policy === "included"}
                onClick={() =>
                  setAnnotatedOverride((cur) => nextAnnotatedOverride(cur, summary.annotated_policy))
                }
              >
                include annotated nights
              </button>
            </div>
          )}
          {/* the late-recorded nights line — L1: a row recorded after its night's next EDGAR open (a boot
              catch-up the next afternoon) is set aside by the server's READ policy, exactly like the two
              lines above, so an on-time row for the night wins and a night with none reads like a cron gap.
              Rendered only when some night was recorded late, with the same reading toggle; nothing is
              deleted either way. */}
          {!rerun && lateNightsLine(summary) && (
            <div className="sb-freshline sb-fresh sb-annotline" title={lateNightsTitle(summary)}>
              {lateNightsLine(summary)}{" "}
              <button
                type="button"
                className="sb-filterclear sb-annottoggle"
                aria-pressed={summary.late_policy === "included"}
                onClick={() => setLateOverride((cur) => nextLateOverride(cur, summary.late_policy))}
              >
                include late nights
              </button>
            </div>
          )}
          <div className="sb-counts">
            <span>{summary.n_episodes} episodes</span>
            <span>{summary.n_open} open</span>
            <span>{summary.n_matured} matured</span>
            <span>{summary.n_censored} censored</span>
            {!rerun && summary.n_ingest_flagged > 0 && (
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

          {/* the status filter chips — the one control on this page that actually REMOVES rows, so
              it carries the receipts interaction principle #2 demands: the count line below says
              what was hidden, every group keeps its heading, and `Clear` is one click back. The
              chips sit with the counts line / view toggle because they are read together ("1
              episodes, 1 open" → "show me the open one"). */}
          <div className="sb-filters">
            <div className="sb-filterchips" role="group" aria-label="ledger filters">
              {LEDGER_FILTERS.map((f) => {
                const on = filters.has(f.id);
                const n = chipCounts[f.id];
                return (
                  <button
                    key={f.id}
                    type="button"
                    className={on ? "on" : ""}
                    aria-pressed={on}
                    // a chip matching nothing goes dim and inert WITH its 0 rather than vanishing —
                    // "there are none" is an answer, and a control that disappeared would leave the
                    // operator wondering whether the question was ever asked. Never disabled while
                    // ACTIVE, though: the record refreshes underneath this view, and a chip that
                    // dropped to 0 mid-session must still be pressable to turn itself off (#1).
                    disabled={n === 0 && !on}
                    title={f.title}
                    onClick={() => onFilter(f.id)}
                  >
                    {f.label} <span className="ct">{n}</span>
                  </button>
                );
              })}
            </div>
            {/* both render only while something is filtered — an always-present "Clear" would imply
                a filter that is always on, and the count line has nothing to report at rest. */}
            {filtering && (
              <>
                <button type="button" className="sb-filterclear" onClick={() => setFilters(new Set())}>
                  Clear
                </button>
                <span className="sb-filterline">
                  showing {tally.showing} of {tally.total} rows · {tally.hidden} hidden by filter
                </span>
              </>
            )}
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
              {rerun
                ? "No arm episodes in this edition."
                : `No arm episodes on the record yet${
                    summary.record_began
                      ? ` — it began ${fmtDate(summary.record_began)} and accrues forward (no backfill).`
                      : " — the record starts with the first daily call-of-record."
                  }`}
            </div>
          )}

          {/* the ledger takes its natural width and scrolls sideways only when the window is
              narrower than that — it never wraps a row or truncates a cell to fit. No height cap:
              the page keeps the vertical scroll. See .sb-scroll. */}
          <div className="sb-scroll">
          <table className="basket sb-ledger">
            <LedgerHead
              view={view}
              returnHeader={rerun ? "Edition return" : "Record return"}
              sort={sort}
              onSort={onSort}
            />
            <tbody>
              {data.theses.map((t) => {
                // filter FIRST, then sort — two separable steps, so `sortEpisodes`/`sortSpans` keep
                // their "a re-order, never a filter" contract and this stays the only place a row
                // can leave the ledger.
                const eps = filterEpisodes(t.episodes, filters);
                const spans = filterSpans(t.operator_spans, filters);
                const filteredOut = groupCount(t) > 0 && eps.length + spans.length === 0;
                return (
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
                            {/* while a filter is on the heading reads "N of M" — a folded group with
                                matches has to be able to declare them, and no group is ever allowed
                                to look emptier than it is. Fold state itself is untouched by
                                filtering: it is the operator's own explicit act. */}
                            <span className="ct">· {groupCountLabel(t, filters)}</span>
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
                      sortEpisodes(eps, sort).map((ep) => (
                        <EpisodeRow
                          key={`${ep.security_id}-${ep.arm_date}-${ep.dearm_date ?? "open"}`}
                          ep={ep}
                          thesisId={t.thesis_id}
                          onSelect={onSelect}
                          onOpenScorecard={setOpenEp}
                          view={view}
                          rerun={rerun}
                        />
                      ))}
                    {isOpen(t) && (
                      <SpanRow t={t} spans={spans} onSelect={onSelect} view={view} sort={sort} />
                    )}
                    {/* a group the filter emptied keeps its heading and says so, with the number it is
                        holding back — a thesis that HAS a record must never read as one that doesn't
                        (#2). Distinct from the genuinely-empty case below, which is about the record. */}
                    {isOpen(t) && filteredOut && (
                      <tr className="sb-note-row">
                        <td colSpan={cols} className="sb-quietline">
                          no rows match the filter · {groupCount(t)} hidden
                        </td>
                      </tr>
                    )}
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
                );
              })}
            </tbody>
          </table>
          </div>

          {/* the historical (replayed) panel is deliberately OUTSIDE the filter chips: its rows
              predate decision capture, so "in position" and "needs an answer" could only ever
              answer the same way for every row there — a control that doesn't discriminate
              shouldn't render (#7). The chips filter the LIVE record ledger only. */}
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
            edition={served}
          />
        )}
      </Drawer>
    </div>
  );
}
