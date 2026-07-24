import { Fragment, useState } from "react";

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

// The Scoreboard (SCORE) — the episode ledger over the forward record: what the platform said,
// what the operator did, what happened. Ledger-first (the aggregate strip stays quiet until n
// accrues past the gate); archived groups fold closed but are never dropped; every mark on a row
// is an exception, not a constant. Read-only: the write surface stays the Cockpit's rail.

type Props = {
  asof: string;
  onAsofChange: (v: string) => void;
  onBack: () => void;
  onOpenWorkbench: () => void;
  onOpenAdmin: () => void;
  /** nameKey (when the row has a name) deep-links that member's panel in the Cockpit (?name=). */
  onSelect: (thesisId: string, nameKey?: string) => void;
};

function SpanRow({
  t,
  onSelect,
  view,
}: {
  t: ScoreboardThesisOut;
  onSelect: (id: string, nameKey?: string) => void;
  view: LedgerView;
}) {
  // off-record spans (overrides live here) — rendered per span under the thesis group
  return (
    <>
      {t.operator_spans.map((s) => {
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
            {view === "timing" ? (
              <>
                {/* an operator span carries NO platform timing lens (forward / peak / past-peak are
                    episode-level, not a logged take) — dash the timing columns, keep the row visible
                    (interaction principle #2 — pruning hides, it never vanishes). */}
                <td className="sb-ret">
                  <span className="ret">—</span>
                </td>
                <td className="sb-ret">
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

export function Scoreboard({
  asof,
  onAsofChange,
  onBack,
  onOpenWorkbench,
  onOpenAdmin,
  onSelect,
}: Props) {
  const { data, isLoading, error } = useScoreboard(asof);
  // the episode-scorecard drawer's open episode — local state only (no URL param this slice); a
  // click opens it, the drawer's ✕/backdrop/Esc close it, and the ledger underneath never rerenders.
  const [openEp, setOpenEp] = useState<ScoreboardEpisodeOut | null>(null);
  // the Summary | Timing ledger view (Slice 2) — local component state, no URL param this slice; a
  // pure VIEW control that swaps the ledger's middle columns (never the rows or the data).
  const [view, setView] = useState<LedgerView>("summary");
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
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <nav className="nav">
          <a onClick={onBack}>Board</a>
          <a onClick={onOpenWorkbench}>Workbench</a>
          <a className="on">Scoreboard</a>
          <a onClick={onOpenAdmin}>Admin</a>
        </nav>
        <div className="spacer" />
        <label className="asof">
          as-of
          <input type="date" value={asof} onChange={(e) => onAsofChange(e.target.value)} />
        </label>
      </header>

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

          <table className="basket sb-ledger">
            <LedgerHead view={view} returnHeader="Record return" />
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
                        <span className="chev">▾</span>
                        <span className="lbl">{t.name}</span>
                        {t.archived && <span className="sb-badge b-arch">ARCHIVED</span>}
                        <em className="hint">· {groupHint(t)}</em>
                        <span className="ct">· {groupCount(t)}</span>
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
                  {isOpen(t) &&
                    t.episodes.map((ep, i) => (
                      <EpisodeRow
                        key={i}
                        ep={ep}
                        thesisId={t.thesis_id}
                        onSelect={onSelect}
                        onOpenScorecard={setOpenEp}
                        view={view}
                      />
                    ))}
                  {isOpen(t) && <SpanRow t={t} onSelect={onSelect} view={view} />}
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
          />
        )}
      </Drawer>
    </div>
  );
}
