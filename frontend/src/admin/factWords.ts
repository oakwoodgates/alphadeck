// The steward's company page: its words and small pure helpers (the splitWords.ts idiom). Every sentence a person
// reads here is American English. Quiet unless a function says `loud`: loudness marks what the steward owes a
// decision on, never the rule.

import type {
  PlatformAppliedValueOut,
  PlatformCandidateOut,
  PlatformDecisionOut,
  PlatformExtractValueOut,
  PlatformExtractionAttemptOut,
  PlatformFactType,
  PlatformValueOut,
} from "../api/hooks";
import { cashSymbol } from "../components/currency";

export const FACT_TITLE: Record<PlatformFactType, string> = {
  shares_outstanding: "Share count",
  cash_burn: "Cash and burn",
};

/** A UTC instant or date as the day it falls on: "2026-08-04". */
export const day = (iso: string | null | undefined): string => (iso ? iso.slice(0, 10) : "");

export const fmtInt = (n: number): string => n.toLocaleString("en-US", { maximumFractionDigits: 0 });

/** Money in the statement's own currency symbol, never converted. */
export function fmtMoney(n: number, currency?: string | null): string {
  const sym = cashSymbol(currency);
  return n < 0 ? `-${sym}${fmtInt(-n)}` : `${sym}${fmtInt(n)}`;
}

type Valued = Pick<
  PlatformAppliedValueOut,
  "shares" | "ads_ratio" | "ads_ratio_status" | "cash_usd" | "quarterly_burn_usd" | "investments_usd"
>;

/** The value in words: "52,113,000 shares", "ADS ratio 2", "$890,000,000 cash, quarterly burn $314,678,000"; a
 *  stored investment part adds "(of which short-term investments $X)" after the cash (none recorded: no split). */
export function valueWords(
  factType: string,
  v: Valued,
  currency?: string | null,
): string {
  if (factType === "shares_outstanding") {
    if (v.shares == null) return "no share count";
    let out = `${fmtInt(v.shares)} shares`;
    if (v.ads_ratio_status === "known" && v.ads_ratio != null) out += `, ADS ratio ${v.ads_ratio}`;
    else if (v.ads_ratio_status === "unread") out += ", ADS ratio unread";
    return out;
  }
  const parts: string[] = [];
  if (v.cash_usd != null) {
    const split =
      v.investments_usd != null ? ` (of which short-term investments ${fmtMoney(v.investments_usd, currency)})` : "";
    parts.push(`${fmtMoney(v.cash_usd, currency)} cash${split}`);
  }
  if (v.quarterly_burn_usd != null) {
    parts.push(
      `quarterly burn ${fmtMoney(v.quarterly_burn_usd, currency)}${v.quarterly_burn_usd <= 0 ? " (cash-generative)" : ""}`,
    );
  }
  return parts.length ? parts.join(", ") : "no cash or burn value";
}

/** The source an annual foreign filer's cover count carries (a 20-F or 40-F; the machine applies one whose only
 *  flags are the annual ones, up to 400 days old). */
export const ANNUAL_COVER_SOURCE = "annual-cover";

/** An annual count's words: "annual figure from the 20-F or 40-F cover, as of 2025-12-31". A cover is dated at
 *  the fiscal year end, so the value can be most of a year old; this says so plainly (quiet, never a badge).
 *  `null` for any other source. */
export function annualWords(source: string | null | undefined, validFrom: string): string | null {
  return source === ANNUAL_COVER_SOURCE
    ? `annual figure from the 20-F or 40-F cover, as of ${day(validFrom)}`
    : null;
}

/** Why a value is missing, in words (the Q7 table). `null` reason reads as not extracted. */
const NO_VALUE_WORDS: Record<string, string> = {
  "not-extracted": "No value yet: not extracted",
  "no-annual-filing": "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR",
  "cover-not-located": "No value yet: the cover count could not be read",
  "no-candidate": "No value yet: the latest filing gave no value of this kind",
  dismissed: "No value yet: you dismissed the last candidate",
  "cash-generative": "Runway does not apply: the company is cash-generative",
  "financials-in-exhibit": "No value yet: the statements were in an exhibit we did not read",
  "exhibit-ambiguous": "No value yet: the statements were in more than one exhibit",
  "statements-not-located": "No value yet: the statements were not found",
  "not-shown": "No value shown, though a decision stands. Report this.",
};

export interface StatusWords {
  /** The status line's first words. */
  label: string;
  /** True only where the steward owes a decision or something is wrong. */
  loud: boolean;
}

/** The status line of a value: its state in words. Loud: awaiting the steward, and the defensive not-shown. */
export function statusWords(v: PlatformValueOut): StatusWords {
  switch (v.state) {
    case "awaiting_steward":
      return { label: "Awaiting you", loud: true };
    case "from_filing":
      return { label: "From the filing", loud: false };
    case "verified":
      return {
        label: v.ratifier === "carried" ? "Verified (carried over from V1)" : "Verified",
        loud: false,
      };
    default: {
      const reason = v.reason ?? "not-extracted";
      return {
        label: NO_VALUE_WORDS[reason] ?? `No value yet: ${reason}`,
        loud: reason === "not-shown",
      };
    }
  }
}

/** Who set a stored value: the machine, a V1 carry-over, or a steward's email. */
export function whoWords(ratifiedBy: string | null | undefined): string {
  if (!ratifiedBy || ratifiedBy === "auto") return "applied by the machine";
  if (ratifiedBy === "operator") return "marked operator before V2";
  return `by ${ratifiedBy}`;
}

/** "applied by the machine on 2026-08-04" / "by steward@x on 2026-11-14" / "marked operator before V2". */
export function setWords(ratifiedBy: string | null | undefined, recordedAt: string): string {
  if (ratifiedBy === "operator") return "marked operator before V2";
  return `${whoWords(ratifiedBy)} on ${day(recordedAt)}`;
}

/** The flag that says a clean value was held back because the company is pinned. */
export const PINNED_FLAG = "pinned";
export const PINNED_HELD_WORDS =
  "Held because you pinned this company: a clean value from the filing";

// ---------------------------------------------------------------------------------------------------------------
// Who pinned a value, when and why: the company read's newest pin event (`pin`).
export type PlatformPinEvent = NonNullable<PlatformValueOut["pin"]>;

export function pinEventOf(v: PlatformValueOut): PlatformPinEvent | null {
  const pin = v.pin;
  return pin && pin.action === "pin" ? pin : null;
}

/** "by steward@x on 2026-11-12: restated twice" */
export function pinWords(p: PlatformPinEvent): string {
  return `by ${p.decided_by} on ${day(p.recorded_at)}${p.note?.trim() ? `: ${p.note.trim()}` : ""}`;
}
// ---------------------------------------------------------------------------------------------------------------

/** A standing decision is one no later undo names, and an undo itself never stands as something to undo. */
export const isStanding = (d: PlatformDecisionOut): boolean => d.action !== "undo" && !d.undone;

/** The undo that undid `d` (the one whose `revokes` names it), if the list carries it. */
export function undoOf(d: PlatformDecisionOut, all: PlatformDecisionOut[]): PlatformDecisionOut | undefined {
  return all.find((x) => x.action === "undo" && x.revokes === d.id);
}

const ACTION_PAST: Record<string, string> = {
  approve: "Approved",
  correct: "Corrected",
  dismiss: "Dismissed",
  undo: "Undid",
};

/** "Approved by steward@x on 2026-11-14" (+ ": reason"). */
export function decisionWords(d: PlatformDecisionOut): string {
  const what = d.action === "undo" ? "Undid a decision" : (ACTION_PAST[d.action] ?? d.action);
  const head = `${what} by ${d.decided_by} on ${day(d.recorded_at)}`;
  return d.reason?.trim() ? `${head}: ${d.reason.trim()}` : head;
}

export const lowerFirst = (s: string): string => s.charAt(0).toLowerCase() + s.slice(1);

export const UNDO_OF_UNDO_WORDS = "An undo cannot be undone. Make the decision again instead.";

/** A candidate is open when no decision closes it. */
export const isOpen = (c: PlatformCandidateOut): boolean => c.decision == null;

/** The candidate in the extractor's shape, for the explain route (which explains only a FLAG). `value` is the
 *  share count; the stored candidate's own basis rides as `source` ("extractor" when none was stored). */
export function explainBody(c: PlatformCandidateOut) {
  return {
    fact_type: c.fact_type,
    tier: "flag" as const,
    source: c.source ?? "extractor",
    source_ref: c.source_ref ?? "",
    event_date: c.event_date ?? "",
    note: c.note ?? "",
    value: c.shares ?? null,
    cash_usd: c.cash_usd ?? null,
    quarterly_burn_usd: c.quarterly_burn_usd ?? null,
    statement_currency: c.statement_currency ?? null,
    flags: c.flags ?? [],
    located_passages: c.located_passages ?? [],
    ads_ratio: c.ads_ratio ?? null,
    ads_ratio_status: c.ads_ratio_status ?? null,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Extract now (W4d) and the recent extraction attempts.

/** The honest note beside the button: what a click reads and what it can change. */
export const EXTRACT_NOTE =
  "Reads this company's latest SEC filings. A clean value from a newer filing replaces the one shown, unless that " +
  "value is pinned; a flagged value waits for you; anything else changes nothing.";

/** What the machine writer did with one value type, in words (no closing period: a status line). Shared by the
 *  answer of a click (`outcome`, `written`, `reason`) and an attempt row (its stored reason only). */
export function machineWords(outcome: string, written = true, reason?: string | null): string {
  switch (outcome) {
    case "applied":
      return "a newer value from the filing was applied";
    case "flagged":
      return written ? "a flagged value from the filing now awaits you" : "the same flagged value already awaits you";
    case "pinned":
      return written
        ? "a clean value now awaits you because this value is pinned"
        : "the same held value already awaits you because this value is pinned";
    case "nothing_found":
      return reason ? `the filings gave no value (${reason})` : "the filings gave no value";
    case "already_on_file":
      return "already on file, nothing changed";
    case "older_than_on_file":
      return "the filing is not newer than the value shown, nothing changed";
    case "after_today":
      return "the filing is dated after today, nothing written";
    case "failed":
      return reason ? `failed: ${reason}` : "failed";
    case "skipped":
      return reason ? `skipped: ${reason}` : "skipped";
    default:
      return outcome;
  }
}

/** One value's line after a click: "Share count: a newer value from the filing was applied". */
export function extractLine(factType: PlatformFactType, v: PlatformExtractValueOut): string {
  return `${FACT_TITLE[factType]}: ${machineWords(v.outcome, v.written ?? true, v.reason)}`;
}

const RUN_KIND_WORDS: Record<string, string> = {
  nightly: "nightly run",
  onboarding: "onboarding",
  backfill: "backfill",
  steward: "Extract now",
  get_data: "get-data",
};

/** One attempt row for ONE value type: "2026-11-15 · nightly run · failed: SEC timed out". The server's error
 *  line is shown as given. */
export function attemptWords(a: PlatformExtractionAttemptOut, factType: PlatformFactType): string {
  const head = `${day(a.recorded_at)} · ${RUN_KIND_WORDS[a.run_kind] ?? a.run_kind}`;
  let what: string;
  if (a.status === "error") what = `failed: ${a.error ?? "no reason recorded"}`;
  else if (a.status === "busy") what = "busy: another writer held the company, nothing written";
  else if (a.status === "skipped") what = `skipped: ${a.detail ?? "nothing could be read"}`;
  else {
    const reason = factType === "shares_outstanding" ? a.shares_reason : a.cash_reason;
    what = reason ? machineWords(reason) : "no outcome recorded";
  }
  return `${head} · ${what}`;
}

/** A cash-and-burn value's year-to-date pace in words (V2 extractor correctness PR 3), from the steward read's
 *  `pace` (derived from the stored note): "year-to-date pace: quarterly burn $69,943,025 (2.2 months of runway)",
 *  or "year-to-date pace: cash-generative, $1,524,448,759 a quarter". `null` when the value carries no pace. The
 *  caveat sentence is the pace's own `caveat` (shown quietly, only when set). */
export function paceWords(pace: PlatformAppliedValueOut["pace"] | undefined): string | null {
  if (!pace) return null;
  const q = pace.ytd_quarterly_burn_usd;
  if (q <= 0) return `year-to-date pace: cash-generative, ${fmtMoney(-q)} a quarter`;
  const months = pace.ytd_runway_months != null ? ` (${pace.ytd_runway_months} months of runway)` : "";
  return `year-to-date pace: quarterly burn ${fmtMoney(q)}${months}`;
}
