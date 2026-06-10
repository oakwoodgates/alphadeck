import type { ScoredFigureOut } from "../api/hooks";

interface Props {
  label: string;
  figure: ScoredFigureOut;
  /** The risk axis (dilution): a PRESSURE meter — more pips = worse. Rendered ember + tinted label so
   *  a full dilution meter never reads at a glance like a full goodness meter. The fill stays honest
   *  left-to-right (we do NOT invert: an inverted severe meter would render empty, colliding with the
   *  "—" no-data state and lying about the pip count). */
  risk?: boolean;
}

const SLOTS = [0, 1, 2, 3];

/** A 0-4 pip meter. `pips == null` is NO DATA — a bare "—", structurally distinct (no pip track) from
 *  a measured zero (four empty slots). That distinction is the no-fake-zeros rule made visual. */
export function Meter({ label, figure, risk }: Props) {
  const pips = figure.pips;
  return (
    <div className={`wb-meter${risk ? " risk" : ""}`}>
      <span className="ml">{label}</span>
      {pips == null ? (
        <span className="wb-nodata" title="no data">
          —
        </span>
      ) : (
        <span className="wb-pips">
          {SLOTS.map((i) => (
            <span key={i} className={`wb-pip${i < pips ? " on" : ""}${risk ? " risk" : ""}`} />
          ))}
        </span>
      )}
    </div>
  );
}
