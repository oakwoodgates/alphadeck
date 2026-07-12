import { Fragment, useState } from "react";

import { useScoreboardReplay } from "../api/hooks";
import { EpisodeRow } from "./EpisodeRow";
import { MetricsStrip } from "./MetricsStrip";

// The HISTORICAL (replayed) section — replayed history below the live ledger, clearly separated
// and QUIET: a RECOMPUTE (today's code + dials over historical facts), never the record. Collapsed
// by default (the header carries the window + count, so a closed panel never reads as absent);
// absent ENTIRELY when no artifact exists (no empty shell — run `python -m
// scoreboard.replay_snapshot` to create one). Its metrics strip is the SAME component as the live
// one but a different dataset — the two are never pooled. Platform track only: history predates
// decision capture, so the operator cell says so instead of faking a capture gap.

export function ReplayPanel({ onSelect }: { onSelect: (thesisId: string) => void }) {
  const { data } = useScoreboardReplay();
  const [open, setOpen] = useState(false); // collapsed by default: context, not action

  if (!data || !data.available) return null;

  return (
    <div className="rp-section">
      <button
        type="button"
        className="grp-h rp-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="chev">▾</span>
        <span className="lbl">Historical — replayed</span>
        {data.window_overlaps_record && (
          <span
            className="sb-badge b-ovr"
            title="the replay window was pushed past the forward record — arms may appear in both sections"
          >
            OVERLAPS RECORD
          </span>
        )}
        <em className="hint">
          {/* ISO dates, not fmtDate: the window spans years — "Jul 9 → Jul 9" reads zero-length */}
          · window {data.window_start} → {data.window_end} · generated{" "}
          {data.generated_at?.slice(0, 10)} · not the record
        </em>
        <span className="ct">· {data.n_episodes}</span>
      </button>

      {open && (
        <>
          <div className="sb-banner rp-banner">{data.banner}</div>
          <MetricsStrip metrics={data.metrics} minN={data.min_n} />
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
                <th>Replayed return</th>
                <th>Operator</th>
              </tr>
            </thead>
            <tbody>
              {data.theses.map((t) => (
                <Fragment key={t.thesis_id}>
                  <tr className="grp rp-grp">
                    <td colSpan={7}>
                      <div className="grp-h rp-grp-h">
                        <span className="lbl">{t.name}</span>
                        <em className="hint">· replayed</em>
                        <span className="ct">· {t.episodes.length}</span>
                      </div>
                    </td>
                  </tr>
                  {t.episodes.map((ep, i) => (
                    <EpisodeRow
                      key={i}
                      ep={ep}
                      thesisId={t.thesis_id}
                      onSelect={onSelect}
                      historical
                    />
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
