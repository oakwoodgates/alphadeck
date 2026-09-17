import { Fragment, useState } from "react";

import type { BacktestLedgerOut, ScoreboardEpisodeOut } from "../api/hooks";
import { Drawer } from "../components/Drawer";
import { EpisodeRow } from "../scoreboard/EpisodeRow";
import { EpisodeScorecard } from "../scoreboard/EpisodeScorecard";
import { LedgerHead } from "../scoreboard/LedgerHead";
import { MetricsStrip } from "../scoreboard/MetricsStrip";
import { ledgerColCount, type LedgerView } from "../scoreboard/rows";
import {
  nextLedgerSort,
  sortEpisodes,
  type LedgerSort,
  type LedgerSortColId,
} from "../scoreboard/sortLedger";
import { breadthLine, episodeKey, indexBreadth, key1Line } from "./breadth";

// THE RUN'S LEDGER — the per-thesis drill-down, and the only place on this page where a thesis is
// named. It exists so the pooled panel above it is falsifiable: a reader who doubts a pooled number
// can open the rows behind it.
//
// It renders through the SCOREBOARD's own components — `LedgerHead`, `EpisodeRow`,
// `EpisodeScorecard`, `MetricsStrip` — because the rows genuinely are replayed, scored arm episodes
// and the backend serves them in that exact wire shape. Nothing here re-implements a cell. (The
// Scoreboard's `ReplayPanel` itself is not reused: it fetches `useScoreboardReplay` internally, so it
// is bound to that endpoint's data. Its CHILDREN are where the rendering lives, and those are shared.)
//
// COLLAPSED BY DEFAULT and below the pooled panel, in that order on purpose. Per-thesis outcomes are
// the leaderboard trap — the platform is opinionated about timing and deferential about the idea (#4)
// — so the algorithm-level view is what the page opens on, and the thesis rows are something the
// reader has to go and get. The theses are in NAME order, fixed by the writer.
//
// The scorecard drawer is mounted WITHOUT an `asof`, which is what keeps the live app out of a
// backtest: with one, `EpisodeScorecard` fetches the live price window, the live display signals and
// the live scored members, all capped at that live as-of. Those are today's data about a run that swept
// a frozen mirror, possibly on a different clock. The drawer therefore shows the episode's own scored
// lenses and no chart — the component's documented pure-render path.

export function RunLedger({
  ledger,
  episodes,
  onSelect,
}: {
  ledger: BacktestLedgerOut;
  /** The run's raw episodes — where the BREADTH fields ride (see breadth.ts). */
  episodes: readonly unknown[];
  /** The ↗ jump to the Cockpit for a name. */
  onSelect: (thesisId: string, nameKey?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<LedgerView>("summary");
  const [sort, setSort] = useState<LedgerSort | null>(null);
  const [openEp, setOpenEp] = useState<ScoreboardEpisodeOut | null>(null);
  const onSort = (col: LedgerSortColId) => setSort((cur) => nextLedgerSort(cur, col));
  const breadth = indexBreadth(episodes);
  const theses = ledger.theses ?? [];
  const openBreadth = openEp
    ? breadth.get(episodeKey(openEp.thesis_id, openEp.security_id, openEp.arm_date))
    : undefined;

  return (
    <section className="bt-ledger" aria-label="This run's episodes">
      <button
        type="button"
        className="grp-h rp-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="chev">▾</span>
        <span className="lbl">This run&apos;s episodes — by thesis</span>
        <em className="hint">
          · the drill-down behind the pooled view · {ledger.n_eligible} eligible of{" "}
          {ledger.n_episodes} · not a ranking
        </em>
        <span className="ct">· {ledger.n_theses}</span>
      </button>

      {open && (
        <>
          <div className="sb-banner">{ledger.banner}</div>
          <div className="sb-viewtoggle">
            <button
              type="button"
              className={view === "summary" ? "on" : ""}
              onClick={() => setView("summary")}
            >
              Summary
            </button>
            <button
              type="button"
              className={view === "timing" ? "on" : ""}
              onClick={() => setView("timing")}
            >
              Timing
            </button>
          </div>
          {/* The run's OWN metric set, over eligible episodes only — a different, smaller set than the
              pooled panel scores, which the banner above states. Same component as the Scoreboard's
              strips so the gate behaves identically. */}
          <MetricsStrip metrics={ledger.metrics ?? []} minN={ledger.min_n ?? 0} />
          <div className="sb-scroll">
            <table className="basket sb-ledger">
              <LedgerHead
                view={view}
                returnHeader="Replayed return"
                sort={sort}
                onSort={onSort}
              />
              <tbody>
                {theses.map((t) => (
                  <Fragment key={t.thesis_id}>
                    <tr className="grp rp-grp">
                      <td colSpan={ledgerColCount(view)}>
                        <div className="grp-h rp-grp-h">
                          <span className="grp-lbl">
                            <span className="lbl">{t.name}</span>
                            <em className="hint">· replayed</em>
                            <span className="ct">· {(t.episodes ?? []).length}</span>
                          </span>
                        </div>
                      </td>
                    </tr>
                    {/* A thesis that armed nothing keeps its heading and says so — an empty run is a
                        RESULT, and a vanished thesis would read as a missing one (#9). */}
                    {(t.episodes ?? []).length === 0 ? (
                      <tr className="sb-note-row">
                        <td colSpan={ledgerColCount(view)} className="sb-quietline">
                          no arm episode in this run&apos;s window
                        </td>
                      </tr>
                    ) : (
                      sortEpisodes(t.episodes ?? [], sort).map((ep) => (
                        <EpisodeRow
                          key={`${ep.security_id}-${ep.arm_date}-${ep.dearm_date ?? "open"}`}
                          ep={ep}
                          thesisId={t.thesis_id}
                          onSelect={onSelect}
                          onOpenScorecard={setOpenEp}
                          historical
                          view={view}
                        />
                      ))
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <Drawer
        open={openEp != null}
        onClose={() => setOpenEp(null)}
        title={
          openEp ? (
            <>
              {openEp.ticker ?? "—"} <span className="sc-sub">replayed scorecard</span>
            </>
          ) : undefined
        }
      >
        {openEp && (
          <>
            {/* The breadth fields, which the record's ledger has no concept of — so they render HERE,
                from this run's own payload, rather than being pushed into the shared episode model. */}
            {(breadthLine(openBreadth) || key1Line(openBreadth)) && (
              <div className="bt-breadth">
                {key1Line(openBreadth) && <div>{key1Line(openBreadth)}</div>}
                {breadthLine(openBreadth) && <div>{breadthLine(openBreadth)}</div>}
                {openBreadth?.confirmation_grade && (
                  <div>Confirmation at the arm: {openBreadth.confirmation_grade}</div>
                )}
              </div>
            )}
            {/* No `asof`: see the note at the top of this file. */}
            <EpisodeScorecard
              ep={openEp}
              thesisName={theses.find((t) => t.thesis_id === openEp.thesis_id)?.name}
            />
          </>
        )}
      </Drawer>
    </section>
  );
}
