// The words a PLAIN USER reads about a shared value (a company's share count and cash/burn): V2 W5e. The server owns
// the state's own words (`label`, `annual_line`, the pending and awaiting lines: `platform_values/words.py`); this
// module only assembles what a screen prints around them: the figure line, the filing phrase, the days of a flag.
// It reuses the steward page's figure rules (`admin/factWords`: the money, the integer, the pace line) and the one
// currency rule (`components/currency`), so no rule has a second copy. Every sentence is American English.

import type { MyFlagOut, UserValueOut } from "../api/hooks";
import { FACT_TITLE, fmtInt, fmtMoney, paceWords, valueWords } from "../admin/factWords";
import { storedCurrency } from "./currency";

export const FACT_HEADING = FACT_TITLE;

/** A day as a person reads it: "Sep 30, 2026" (the server's `day_words`), from an ISO date or timestamp's date part. */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

const MARKET_ZONE = "America/New_York";

/** The market-zone calendar day (YYYY-MM-DD) an instant falls on: the zone the allowance resets in. */
export function marketDay(instant: string): string {
  const d = new Date(instant);
  if (Number.isNaN(d.getTime())) return instant.slice(0, 10);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: MARKET_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** An instant's market-zone day without the year, as the flag lines say it: "Nov 16". */
export function shortDay(instant: string): string {
  const day = marketDay(instant);
  const t = Date.parse(`${day}T00:00:00Z`);
  if (Number.isNaN(t)) return day;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export const lowerFirst = (s: string): string => s.charAt(0).toLowerCase() + s.slice(1);

/** A sentence from a clause: one full stop at the end, never two. */
export const sentence = (s: string): string => {
  const t = s.trim();
  return /[.!?]$/.test(t) ? t : `${t}.`;
};

/** The form a value's filing was: "10-Q", "10-K", "20-F", "40-F"; the annual paths say "20-F or 40-F"; None when the
 *  source names no form (a V1 row). */
export function filingForm(source: string | null | undefined): string | null {
  if (!source) return null;
  const m = /^(10-q|10-k|20-f|40-f|8-k|6-k)(?![a-z0-9])/i.exec(source);
  if (m) return m[1].toUpperCase();
  if (source.startsWith("annual-")) return "20-F or 40-F";
  return null;
}

/** "10-Q dated Sep 30, 2026" (or "filing dated ..." when the form is unknown; "filing" with no date). */
export function filingPhrase(source: string | null | undefined, day: string | null | undefined): string {
  const form = filingForm(source) ?? "filing";
  return day ? `${form} dated ${fmtDay(day)}` : form;
}

/** What follows "No value yet:" in a no-value label, for the Cockpit's short line ("no 10-K, 10-Q, 20-F or 40-F on
 *  EDGAR"). The not-extracted reason is shortened for that line; the default reads as the server's own words. */
export function reasonClause(v: Pick<UserValueOut, "reason" | "label">): string {
  if (v.reason === "not-extracted" || !v.reason) return "the platform has not read the filings";
  const i = v.label.indexOf(":");
  return i >= 0 ? lowerFirst(v.label.slice(i + 1).trim()) : lowerFirst(v.label);
}

/** The value shown, in words: "52,113,000 shares" or, for cash and burn in the value's own currency (never
 *  converted), "$890,000,000 cash and short-term investments (of which short-term investments $120,000,000) ·
 *  quarterly burn $31,400,000". Null with nothing shown. */
export function figureWords(v: UserValueOut): string | null {
  const val = v.value;
  if (!val) return null;
  if (v.fact_type === "shares_outstanding") return val.shares == null ? null : valueWords("shares_outstanding", val);
  const cur = storedCurrency(val);
  const parts: string[] = [];
  if (val.cash_usd != null) {
    const split =
      val.investments_usd != null
        ? ` and short-term investments (of which short-term investments ${fmtMoney(val.investments_usd, cur)})`
        : "";
    parts.push(`${fmtMoney(val.cash_usd, cur)} cash${split}`);
  }
  if (val.quarterly_burn_usd != null) {
    parts.push(
      val.quarterly_burn_usd > 0
        ? `quarterly burn ${fmtMoney(val.quarterly_burn_usd, cur)}`
        : "cash-generative last quarter",
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

/** The year-to-date pace line, capitalized ("Year-to-date pace: quarterly burn $29,000,000 (9.2 months of
 *  runway)"), in the value's own currency; null with no pace. */
export function paceLine(v: UserValueOut): string | null {
  const val = v.value;
  if (!val?.pace) return null;
  const line = paceWords(val.pace, storedCurrency(val));
  return line ? line.charAt(0).toUpperCase() + line.slice(1) : null;
}

/** An integer with thousands separators (re-exported so a screen need not reach into the steward's module). */
export const intWords = fmtInt;

// --- the user's own flag --------------------------------------------------------------------------------------------

/** How long a steward's outcome stays loud after the close. Computed from `closed_at`, never stored. */
export const OUTCOME_LOUD_DAYS = 14;

const dayNumber = (isoDay: string): number => Date.parse(`${isoDay}T00:00:00Z`) / 86_400_000;

/** Whole days from the day the flag closed (market zone) to `today` (YYYY-MM-DD). */
export function daysSinceClose(closedAt: string, today: string): number {
  return Math.round(dayNumber(today) - dayNumber(marketDay(closedAt)));
}

/** The outcome is loud for 14 days after the close (the close day counts as day 0), then quiet. */
export function outcomeIsLoud(f: Pick<MyFlagOut, "closed_at">, today: string): boolean {
  return f.closed_at != null && daysSinceClose(f.closed_at, today) <= OUTCOME_LOUD_DAYS;
}

/** The steward's outcome in words: "The steward fixed this on Nov 18: corrected to the 10-Q cover total." / "The
 *  steward kept this value on Nov 18: ...". Null for an open flag, a withdrawal, or an outcome not yet known. */
export function outcomeWords(f: MyFlagOut): string | null {
  if (f.closed_at == null || (f.outcome !== "fixed" && f.outcome !== "kept")) return null;
  const head = `The steward ${f.outcome === "fixed" ? "fixed this" : "kept this value"} on ${shortDay(f.closed_at)}`;
  const note = f.outcome_note?.trim();
  return note ? `${head}: ${sentence(note)}` : `${head}.`;
}
