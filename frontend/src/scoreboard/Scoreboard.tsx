import { Fragment, useState } from "react";

import type { ScoreboardThesisOut } from "../api/hooks";
import { useScoreboard } from "../api/hooks";
import { fmtDate } from "../util/format";
import { EpisodeRow } from "./EpisodeRow";
import { MetricsStrip } from "./MetricsStrip";
import { ReplayPanel } from "./ReplayPanel";
import { fmtReturn, groupCount, groupHint, groupToneClass } from "./rows";

// The Scoreboard (SCORE) — the episode ledger over the forward record: what the platform said,
// what the operator did, what happened. Ledger-first (the aggregate strip stays quiet until n
// accrues past the gate); archived groups fold closed but are never dropped; every mark on a row
// is an exception, not a constant. Read-only: the write surface stays the Cockpit's rail.

type Props = {
  asof: string;
  onAsofChange: (v: string) => void;
  onBack: () => void;
  onOpenWorkbench: () => void;
  onSelect: (thesisId: string) => void;
};

function SpanRow({
  t,
  onSelect,
}: {
  t: ScoreboardThesisOut;
  onSelect: (id: string) => void;
}) {
  // off-record spans (overrides live here) — rendered per span under the thesis group
  return (
    <>
      {t.operator_spans.map((s) => {
        const ret = fmtReturn(s.operator_return);
        return (
          <tr key={s.take_id} className="sb-row sb-span" onClick={() => onSelect(t.thesis_id)}>
            <td className="tk">{s.ticker ?? (s.thesis_level ? "◇" : "—")}</td>
            <td className="sb-armed">{fmtDate(s.take_date)}</td>
            <td className="sb-why">
              <span className="sb-stance">
                platform said {s.call_verdict_at_take ?? s.call_state_at_take ?? "—"}
              </span>
            </td>
            <td className="exitby">—</td>
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
          </tr>
        );
      })}
    </>
  );
}

export function Scoreboard({ asof, onAsofChange, onBack, onOpenWorkbench, onSelect }: Props) {
  const { data, isLoading, error } = useScoreboard(asof);
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
          <div className="sb-counts">
            <span>{summary.n_episodes} episodes</span>
            <span>{summary.n_open} open</span>
            <span>{summary.n_matured} matured</span>
            <span>{summary.n_censored} censored</span>
            <span className="sb-sep">·</span>
            <span>{summary.n_takes} takes</span>
            <span>{summary.n_passes} passes</span>
            <span>{summary.n_overrides} overrides</span>
            {summary.n_voided > 0 && <span>{summary.n_voided} voided</span>}
          </div>

          <MetricsStrip metrics={summary.metrics} minN={summary.min_n} />

          {summary.n_episodes === 0 && summary.n_takes === 0 && (
            <div className="sb-empty">
              No arm episodes on the record yet
              {summary.record_began
                ? ` — it began ${fmtDate(summary.record_began)} and accrues forward (no backfill).`
                : " — the record starts with the first daily call-of-record."}
            </div>
          )}

          <table className="basket sb-ledger">
            <colgroup>
              <col className="c-tk" />
              <col className="c-armed" />
              <col className="c-why" />
              <col className="c-exit" />
              <col className="c-status" />
              <col className="c-ret" />
              <col className="c-op" />
            </colgroup>
            <thead>
              <tr>
                <th>Name</th>
                <th>Armed</th>
                <th>Why</th>
                <th>Exit-by</th>
                <th>Status</th>
                <th>Record return</th>
                <th>Operator</th>
              </tr>
            </thead>
            <tbody>
              {data.theses.map((t) => (
                <Fragment key={t.thesis_id}>
                  <tr className={`grp ${groupToneClass(t)}`}>
                    <td colSpan={7}>
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
                      <td colSpan={7} className="sb-error">
                        record error: {t.record_error}
                      </td>
                    </tr>
                  )}
                  {t.decision_anomaly && isOpen(t) && (
                    <tr className="sb-note-row">
                      <td colSpan={7} className="sb-anomaly">
                        decision log anomaly: {t.decision_anomaly}
                      </td>
                    </tr>
                  )}
                  {isOpen(t) &&
                    t.episodes.map((ep, i) => (
                      <EpisodeRow key={i} ep={ep} thesisId={t.thesis_id} onSelect={onSelect} />
                    ))}
                  {isOpen(t) && <SpanRow t={t} onSelect={onSelect} />}
                  {isOpen(t) && !groupCount(t) && !t.record_error && (
                    <tr className="sb-note-row">
                      <td colSpan={7} className="sb-quietline">
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

          <ReplayPanel onSelect={onSelect} />
        </div>
      )}
    </div>
  );
}
