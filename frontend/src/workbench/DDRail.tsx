import type { ScoredFigureOut, ScoredMemberOut } from "../api/hooks";
import { CatalystFactForm } from "./CatalystFactForm";
import { FactsPanel } from "./FactsPanel";
import {
  ARCHETYPES,
  archLabel,
  formatMarketCap,
  meterValueLabel,
  onFileValues,
  provChip,
  provNotes,
} from "./format";

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
  // the member's persisted thesis-fit prose ("why this name sits in its link"), bridged from the
  // thesis basket by the parent — display of a stored field, never derived here
  thesisFit?: string | null;
  // The rail is the archetype's SINGLE home (item F): this callback persists a decision — confirming the
  // derived hint OR a manual pick from the select — as operator_edited (#10, the operator decides).
  // Omitted (read-only) when there's no write path. The recommendation shows pending regardless.
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
export function DDRail({ member, thesisFit, onApplyArchetype, applying, thesisId }: Props) {
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
  // the rule abstains (hint null) or already agrees (quiet agreement) — only pending disagreement shows.
  // With a NULL archetype (item F: placement never characterizes) any non-null hint is pending — the rail is
  // where the un-decided become decided, by the hint's apply or the manual set below.
  const hint = member.archetype_hint;
  const recommends = hint != null && hint !== member.archetype;
  return (
    <div className="ddcard">
      <div className="dd-head">
        <span className="tk">{member.ticker ?? "◇"}</span>
        {member.archetype ? (
          <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
        ) : (
          <span className="arch unset" title="not yet characterized — decide it here (the hint recommends; you pick)">
            unclassified
          </span>
        )}
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
        {/* the manual set/override — the SAME single decision point as the hint's apply (one home, one
            authorship: operator_edited). Offered whenever there's a write path; a pick is a decision even
            where the rule abstained (shovel / fund are relational calls the hint never guesses). */}
        {onApplyArchetype && (
          <select
            className="arch-set"
            value={member.archetype ?? ""}
            disabled={applying}
            aria-label={`set archetype for ${member.ticker ?? "this name"}`}
            onChange={(e) =>
              e.target.value &&
              onApplyArchetype(
                member.security_id,
                e.target.value as NonNullable<ScoredMemberOut["archetype_hint"]>,
              )
            }
          >
            {!member.archetype && <option value="">— set —</option>}
            {ARCHETYPES.map((a) => (
              <option key={a} value={a}>
                {archLabel(a)}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="dd-body">
        {/* WHO this is — the company name + the machine-parsed identity (master enrichment; display-only,
            never promoted, #2). The rail is where the operator decides, so it says who it's deciding about. */}
        {(member.name || member.sector || member.exchange || member.category) && (
          <div className="dd-ident">
            {member.name && <span className="dd-co">{member.name}</span>}
            {member.sector && <span className="idchip">{member.sector}</span>}
            {member.exchange && <span className="idchip">{member.exchange}</span>}
            {member.category && <span className="idchip">{member.category}</span>}
          </div>
        )}
        {/* the persisted thesis-fit prose — the draft-time "why this name sits in its link", ratified at
            TRIAGE and stored on the member. Absent on a hand-added name = honest blank, not filler. */}
        {thesisFit && <p className="dd-thesis-fit">{thesisFit}</p>}
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

        {/* the hand-authored conviction fact (Key-1) — the one meter input that has no extractor:
            the operator logs a CITED verifiable event; the catalysts meter + the call consume it */}
        <CatalystFactForm securityId={member.security_id} />

        <div className="dd-sub">Extract &amp; ratify the facts</div>
        <FactsPanel
          securityId={member.security_id}
          thesisId={thesisId}
          // which fact types already have a RATIFIED value on file — WITH the ratified values themselves,
          // recovered from the meters' provenance detail (price bars and the awaiting-note are not operator
          // facts). The panel shows THOSE values, not the stale extract candidate: without them a re-open
          // visibly "reverted" a saved purity to the original LLM rec (the save was fine; the DB-free
          // extract just can't know it happened).
          onFile={onFileValues(member)}
        />
      </div>
    </div>
  );
}
