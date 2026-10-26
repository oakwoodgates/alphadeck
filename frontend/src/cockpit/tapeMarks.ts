import type { DisplaySignal } from "../api/hooks";
import { fmtDate, daysFrom } from "../util/format";
import type { DerivedCell } from "./dataFlags";

/** The Cockpit's `Tape` column: which names are printing an EXCEPTIONAL tape reading, off display members the
 *  panel already renders. Read-only context (#4/#6) — it states what the tape did, never what it will do, and
 *  nothing here can fire, arm, veto or grade a call. Pure + testable (the `groupAggregate.ts` precedent): the
 *  Cockpit hands in each row's already-bridged members, so this module never touches a hook.
 *
 *  Honest loudness (#7 / interaction principle #3): a mark exists ONLY for the exception, so the column reads
 *  a muted "—" on most rows. Three marks, each a member's own verdict — nothing is derived here but the
 *  recency of a cross:
 *    coil      — `vcp`'s headline `coiling` (a tightening base above a rising 50d)
 *    RS lead   — `relative_strength`'s headline `leading` (RS at a 13-week high vs SPY or IWM)
 *    golden / death — the `sma_position` member's last 50d/200d cross, when it printed within
 *                     `TAPE_CROSS_DAYS` of the as-of (the member keeps the LAST cross whenever it was) */

/** How long a golden / death cross stays a tape mark, in calendar days after the bar it printed on. A DISPLAY
 *  dial (never a call input): a cross older than this is history, not an exception. */
export const TAPE_CROSS_DAYS = 30;

export type TapeMarkKey = "coiling" | "rs_leading" | "golden_cross" | "death_cross";

export interface TapeMark {
  key: TapeMarkKey;
  /** The short chip text; the full statement rides `title`. */
  label: string;
  /** The direction glyph (the shared `.dirg` tint rule: rising-family positive, falling negative). */
  glyph: "up" | "down" | "flat";
  title: string;
  /** The flag-reach proxy for the bars this mark reads (`DERIVED_CELL_REACH`) — a coil / RS read spans at
   *  most ~65 bars, a cross reads back through the 200d line, so they reach different named cells. */
  reach: DerivedCell;
}

/** One row's already-bridged display members (each null when the name carries none). */
export interface TapeInputs {
  sma: DisplaySignal | null;
  vcp: DisplaySignal | null;
  rs: DisplaySignal | null;
}

function headlineTitle(sig: DisplaySignal): string {
  const h = sig.headline;
  return h ? `${h.label}${h.detail ? ` — ${h.detail}` : ""}` : sig.label;
}

/** The tape marks for ONE row at `asof`, in a fixed order (coil, RS lead, then the cross). Empty = the quiet
 *  default. */
export function tapeMarks(inputs: TapeInputs, asof: string): TapeMark[] {
  const marks: TapeMark[] = [];
  if (inputs.vcp?.headline?.key === "coiling") {
    marks.push({
      key: "coiling",
      label: "coil",
      glyph: "flat", // a coil is sideways; its direction is unknown until it breaks (never forecast, #4)
      title: headlineTitle(inputs.vcp),
      reach: "tapeShort",
    });
  }
  if (inputs.rs?.headline?.key === "leading") {
    marks.push({
      key: "rs_leading",
      label: "RS lead",
      glyph: "up",
      title: headlineTitle(inputs.rs),
      reach: "tapeShort",
    });
  }
  const cross = (inputs.sma?.events ?? []).find(
    (e) => e.key === "golden_cross" || e.key === "death_cross",
  );
  if (cross) {
    const age = daysFrom(cross.date, asof); // days from the bar to the as-of
    if (age !== null && age >= 0 && age <= TAPE_CROSS_DAYS) {
      const golden = cross.key === "golden_cross";
      marks.push({
        key: golden ? "golden_cross" : "death_cross",
        label: golden ? "golden" : "death",
        glyph: golden ? "up" : "down",
        title: `${cross.label} on ${fmtDate(cross.date)} (${age === 0 ? "today" : `${age}d ago`})`,
        reach: "tapeLong",
      });
    }
  }
  return marks;
}
