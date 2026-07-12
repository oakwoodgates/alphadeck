import type { ScoreboardEpisodeOut } from "../api/hooks";
import { fmtDate } from "../util/format";
import { episodeBadges, fmtReturn, operatorLine, returnLabel } from "./rows";

// One episode ledger row — shared by the live record table and the historical (replayed) panel.
// `historical` swaps the operator cell: history predates decision capture, so it says so
// (structurally absent) instead of faking a "no decision logged" capture gap.

export function EpisodeRow({
  ep,
  thesisId,
  onSelect,
  historical = false,
}: {
  ep: ScoreboardEpisodeOut;
  thesisId: string;
  onSelect: (id: string) => void;
  historical?: boolean;
}) {
  const ret = fmtReturn(ep.forward_return);
  const op = operatorLine(ep);
  return (
    <tr className="sb-row" onClick={() => onSelect(thesisId)} tabIndex={0}>
      <td className="tk">{ep.ticker ?? "—"}</td>
      <td className="sb-armed">
        {fmtDate(ep.arm_date)}
        {ep.censored_start && (
          <span className="sb-cen" title="the record began mid-arm — true arm date unknowable">
            *
          </span>
        )}
        {ep.dearm_date && <span className="sb-dearm"> → {fmtDate(ep.dearm_date)}</span>}
      </td>
      <td className="sb-why">
        {ep.triggers_at_arm.length ? (
          ep.triggers_at_arm.map((t, i) => (
            <span key={i} className="sb-trig" title={t.label}>
              {t.kind}
            </span>
          ))
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="exitby">{fmtDate(ep.exit_by)}</td>
      <td className="sb-status">
        {episodeBadges(ep).map((b) => (
          <span key={b.label} className={`sb-badge ${b.cls}`} title={b.title}>
            {b.label}
          </span>
        ))}
        {ep.status === "closed" && <span className="sb-reason">{ep.close_reason}</span>}
      </td>
      <td className="sb-ret">
        <span className={`ret ${ret.cls}`}>{ret.text}</span>
        <span className="sb-retlabel"> {returnLabel(ep)}</span>
      </td>
      {historical ? (
        <td className="sb-op sb-op-none">— predates decision capture</td>
      ) : (
        <td className={`sb-op sb-op-${op.kind}`}>
          {op.text}
          {op.ret && <span className={`ret ${op.ret.cls}`}> {op.ret.text}</span>}
          {op.inferred && (
            <span className="sb-inf" title="no fill price logged — the close stands in">
              ≈
            </span>
          )}
          {ep.operator?.reason && <span className="sb-reason"> · {ep.operator.reason}</span>}
        </td>
      )}
    </tr>
  );
}
