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

/** The cash/burn LABEL currency (Retrieval Slice A follow-up). The IFRS annual-statements runway path
 *  reports in the filer's NATIVE currency (TWD/EUR/CAD/…), so the raw value is New Taiwan Dollars, not
 *  USD — labeling it a bare `$` read `$2.77T` for what is really ~US$88B. The value is NEVER converted
 *  (runway is a ratio — the months are currency-correct as-is); only the LABEL names the currency. A
 *  small static ISO→symbol map, falling back to the raw ISO code (honest, never an invented symbol). */
const CURRENCY_SYMBOL: Record<string, string> = {
  TWD: "NT$",
  EUR: "€",
  CAD: "C$",
  CHF: "CHF",
  GBP: "£",
  INR: "₹",
  // PR 4 (annual statements): the currencies the annual path now reads. Currencies that share the "$" glyph
  // carry their prefix (S$, HK$, A$, R$) — never a bare "$" for a non-USD value.
  CNY: "CN¥",
  JPY: "¥",
  SGD: "S$",
  HKD: "HK$",
  KRW: "₩",
  AUD: "A$",
  ZAR: "R",
  BRL: "R$",
  ILS: "₪",
};

/** The annual path's flag for a currency it could NOT determine (a bare "$", or headers that disagree). */
export const CURRENCY_UNREAD_FLAG = "currency-unread";

/** The currency a candidate's figures are labeled in: its own ISO code, or "?" when the extractor flagged the
 *  currency unread — an unread currency never renders as "$" (PR 4). Null otherwise (a domestic 10-Q/10-K name). */
export function labelCurrency(
  currency: string | null | undefined,
  flags: readonly string[] | null | undefined,
): string | null {
  if (currency) return currency;
  return (flags ?? []).includes(CURRENCY_UNREAD_FLAG) ? "?" : null;
}

/** `$` for a domestic/us-gaap filer (statement_currency null or "USD" — the periodic path never sets it);
 *  the native symbol (or raw ISO fallback) for an annual filer; "?" passed through for an unread currency. */
export function cashSymbol(currency: string | null | undefined): string {
  if (!currency || currency === "USD") return "$";
  return CURRENCY_SYMBOL[currency] ?? currency;
}

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
