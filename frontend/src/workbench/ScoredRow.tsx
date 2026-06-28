import type { ScoredMemberOut } from "../api/hooks";
import { Meter } from "./Meter";
import { archLabel, formatMarketCap } from "./format";

interface Props {
  member: ScoredMemberOut;
  selected: boolean;
  onSelect: () => void;
}

/** One scored basket member: ticker + archetype + market-cap figure, then the four meters. purity /
 *  runway / catalysts cluster as the "goodness" group; dilution sits after a faint separator as the
 *  ember RISK axis (the pressure meter), so it can't be misread as a fourth goodness meter. */
export function ScoredRow({ member, selected, onSelect }: Props) {
  return (
    <button type="button" className={`nmrow${selected ? " sel" : ""}`} onClick={onSelect}>
      <div className="top">
        <span className="tk">{member.ticker ?? "◇"}</span>
        <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
        {member.archetype_hint && member.archetype_hint !== member.archetype && (
          <span
            className="arch-rec-dot"
            title={`figures suggest ${archLabel(member.archetype_hint)} — open the name to apply`}
          >
            ✦
          </span>
        )}
        <span className="cap">
          <small>mkt cap</small>
          {formatMarketCap(member.market_cap.value)}
        </span>
      </div>
      <div className="wb-meters">
        <Meter label="purity" figure={member.purity} />
        <Meter label="runway" figure={member.runway} />
        <Meter label="catalysts" figure={member.catalysts} />
        <span className="wb-meter-sep" aria-hidden="true" />
        <Meter label="dilution" figure={member.dilution} risk />
        <span className="fit">{member.fit}</span>
      </div>
    </button>
  );
}
