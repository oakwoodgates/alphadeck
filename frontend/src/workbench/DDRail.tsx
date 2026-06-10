import type { ScoredFigureOut, ScoredMemberOut } from "../api/hooks";
import { archLabel, formatMarketCap, meterValueLabel, provChip, provNotes } from "./format";

const METERS: { key: string; figure: (m: ScoredMemberOut) => ScoredFigureOut }[] = [
  { key: "purity", figure: (m) => m.purity },
  { key: "runway", figure: (m) => m.runway },
  { key: "catalysts", figure: (m) => m.catalysts },
  { key: "dilution", figure: (m) => m.dilution },
  { key: "market cap", figure: (m) => m.market_cap },
];

function MeterProvenance({ meter, figure }: { meter: string; figure: ScoredFigureOut }) {
  const notes = provNotes(figure.provenance);
  return (
    <div className="dd-meter">
      <div className="dd-meter-h">
        <span className="k">{meter}</span>
        <span className="v">{meterValueLabel(meter, figure)}</span>
      </div>
      {figure.provenance.length > 0 && (
        <div className="prov">
          {figure.provenance.map((p, i) => {
            const chip = provChip(p);
            return chip.url ? (
              <a
                key={i}
                className="chip"
                href={chip.url}
                target="_blank"
                rel="noreferrer"
                title={chip.title}
              >
                {chip.text} ↗
              </a>
            ) : (
              <span key={i} className="chip" title={chip.title}>
                {chip.text}
              </span>
            );
          })}
        </div>
      )}
      {notes.map((note, i) => (
        <div className="dd-why" key={i}>
          {note}
        </div>
      ))}
    </div>
  );
}

interface Props {
  member: ScoredMemberOut | null;
}

/** The DD rail — "behind the scores" for the selected name. Deterministic provenance ONLY: every chip
 *  traces to a fact or a computation, and the notes (the recurring-vs-one-time burn composition, the
 *  cash-runway basis) are the payoff — the operator seeing WHY the number is what it is. The
 *  company-reference Overview and the auto-drafted thesis-fit prose are NOT yet wire-backed; they're
 *  marked as deferred rather than faked (Overview → an ingest slice; the prose → the LLM drafter, S5). */
export function DDRail({ member }: Props) {
  if (!member) {
    return (
      <div className="ddcard">
        <div className="dd-body">
          <p className="muted">Select a name to see the evidence behind its scores.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="ddcard">
      <div className="dd-head">
        <span className="tk">{member.ticker ?? "◇"}</span>
        <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
      </div>
      <div className="dd-body">
        <div className="dd-facts">
          <span>
            <b>fit</b>
            {member.fit}
          </span>
          <span>
            <b>segment</b>
            {member.segment ?? "—"}
          </span>
          <span>
            <b>mkt cap</b>
            {formatMarketCap(member.market_cap.value)}
          </span>
        </div>

        <div className="dd-sub">Behind the scores</div>
        {METERS.map(({ key, figure }) => (
          <MeterProvenance key={key} meter={key} figure={figure(member)} />
        ))}

        <div className="dd-sub deferred">
          Overview <em>· stored company facts — with ingest</em>
        </div>
        <div className="dd-sub deferred">
          Thesis fit <em>· auto-drafted prose — Slice 5 (the LLM drafter)</em>
        </div>
      </div>
    </div>
  );
}
