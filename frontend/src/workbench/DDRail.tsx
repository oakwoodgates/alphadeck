import type { ScoredFigureOut, ScoredMemberOut } from "../api/hooks";
import { FactsPanel } from "./FactsPanel";
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
  // #10 apply: confirm the derived archetype recommendation -> persists as operator_edited (the operator
  // decides). Omitted (read-only) when there's no write path. The recommendation shows pending regardless.
  onApplyArchetype?: (
    securityId: string,
    archetype: NonNullable<ScoredMemberOut["archetype_hint"]>,
  ) => void;
  applying?: boolean;
  // the active thesis — passed to the facts panel so the extract can request the GROUNDED purity estimate
  // (SURFACE 1b; purity's on-thesis segment depends on the narrative). Optional: no thesis -> no purity estimate.
  thesisId?: string;
}

/** The DD rail — "behind the scores" for the selected name. Deterministic provenance ONLY: every chip
 *  traces to a fact or a computation, and the notes (the recurring-vs-one-time burn composition, the
 *  cash-runway basis) are the payoff — the operator seeing WHY the number is what it is. The facts panel
 *  closes the extract → ratify → re-score loop in place of the old "stored company facts" marker. Only the
 *  auto-drafted thesis-fit prose stays deferred — marked, not faked (the LLM drafter, S5). */
export function DDRail({ member, onApplyArchetype, applying, thesisId }: Props) {
  if (!member) {
    return (
      <div className="ddcard">
        <div className="dd-body">
          <p className="muted">Select a name to see the evidence behind its scores.</p>
        </div>
      </div>
    );
  }
  // The #10 recommendation: a derived default (market cap + purity) that DIFFERS from the current archetype.
  // Pending + display-only — the operator confirms it (apply -> operator_edited) or ignores it. No chip when
  // the rule abstains (hint null) or already agrees (quiet agreement) — only loud disagreement shows.
  const hint = member.archetype_hint;
  const recommends = hint != null && hint !== member.archetype;
  return (
    <div className="ddcard">
      <div className="dd-head">
        <span className="tk">{member.ticker ?? "◇"}</span>
        <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
        {recommends && (
          <span
            className="arch-rec"
            title="derived from the figures (market cap + purity) — a recommendation, not a verdict; you decide"
          >
            ✦ suggests {archLabel(hint)}
            {onApplyArchetype && (
              <button
                type="button"
                className="arch-apply"
                disabled={applying}
                aria-label={`apply ${archLabel(hint)} to ${member.ticker ?? "this name"}`}
                onClick={() => onApplyArchetype(member.security_id, hint)}
              >
                {applying ? "applying…" : "apply"}
              </button>
            )}
          </span>
        )}
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

        <div className="dd-sub">Extract &amp; ratify the facts</div>
        <FactsPanel securityId={member.security_id} thesisId={thesisId} />

        <div className="dd-sub deferred">
          Thesis fit <em>· auto-drafted prose — Slice 5 (the LLM drafter)</em>
        </div>
      </div>
    </div>
  );
}
