import { useState } from "react";

import type { components } from "../api/types.gen";
import { ExternalLink } from "./ExternalLink";

// The located-passage display, shared by the Workbench's Facts panel and the steward's company page. It was the
// block inside FactsPanel's RatifyRow, moved here with its two small helpers; the markup and behavior are unchanged.
// A located passage is a deterministically retrieved filing excerpt (never a model's reading): the evidence behind
// a value, shown readable inline because reading it IS the decision.

type LocatedPassage = components["schemas"]["LocatedPassage"];

/** Missing-data flags — "can't compute" is a gray AUTHORING state (the value is None by design; the
 *  operator writes it from the located statements), not a warm alarm. The loud ⚠ is reserved for the
 *  judgment exceptions (one-time, stale, raw-YTD, dual-class) — honest loudness: a flag that alarms
 *  on a mere data gap drowns the flags that mark real composition risk. */
export const MISSING_FLAGS = new Set(["no-companyfacts", "no-cashflow-column", "no-cash-instant"]);

/** The passages themselves, clamped to a scannable window (a raw segment table arrives as a wall of collapsed
 *  text) with one expand for the long ones, the filing link on each. `show` false renders nothing but keeps the
 *  expand state, so a parent that toggles the block (the Workbench's AUTO source toggle) loses nothing. */
export function LocatedPassages({ passages, show = true }: { passages: LocatedPassage[]; show?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const showPassages = passages.length > 0 && show;
  return (
    <>
      {showPassages &&
        passages.map((p, i) => (
          <div className="located" key={i}>
            <ExternalLink className="chip" href={p.source_ref} title={p.source_ref}>
              {p.kind} · {p.anchor} ↗
            </ExternalLink>
            {/* clamp only what's actually long — the fade must never eat a short passage */}
            <div className={`excerpt${!expanded && p.excerpt.length > 420 ? " clamped" : ""}`}>
              {p.excerpt}
            </div>
          </div>
        ))}
      {showPassages && passages.some((p) => p.excerpt.length > 420) && (
        <button type="button" className="wb-mini ghost" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "− collapse the passage" : "+ show the full passage"}
        </button>
      )}
    </>
  );
}
