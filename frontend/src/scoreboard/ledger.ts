import type { DisplaySignal, MemberDisplaySignalsOut, ScoredMemberOut } from "../api/hooks";
import { businessTypeLabel } from "../util/format";
import { formatMarketCap } from "../workbench/format";
import {
  familyCls,
  type LifecycleKind,
  type OverlayEvent,
  overlayTooltip,
  type ProvenanceLink,
  triggerLinks,
} from "./overlay";
import { closeReasonLabel } from "./rows";

// Pure formatters for the drawer's per-episode event LEDGER (Slice B) — the tabular companion to the chart.
// The ledger lists the SAME numbered events the chart draws (row #N ↔ chip #N); it shares the ONE `events`
// array built ONCE in EpisodeScorecard, so the numbering is never re-derived here (the reuse invariant).
// Every row traces to a recorded fact (#6 — nothing invented); a missing identity field reads "—" (never a
// guess); only signals that carry a headline surface (honest loudness #7). Canvas/coordinate math lives in
// the chart — these are the jsdom-testable strings the reviewer can trust without an eye.

export interface LedgerRow {
  n: number; // the STABLE chronological id — identical to the chip's number (built in overlay.ts)
  cls: string; // the family hue class, same as the chip: ov-insider / ov-trigger / ov-lifecycle / …
  date: string; // raw ISO — the component formats via fmtDate
  type: string; // the human family/kind label
  detail: string; // the family specifics (reuses overlayTooltip's per-family lines where they read well)
  /** The row's clickable provenance — the filing behind the fact, where the server resolved one. The
   *  refs ALSO ride as text in `detail`, so an unresolvable source is never lost; this only adds the
   *  jump (#6, explainability: the table can reach the document). Absent on rows with no linkable
   *  source — the table stays quiet rather than rendering an empty affordance. */
  links?: ProvenanceLink[];
}

// A lifecycle row names its specific kind in the type cell — the number's tint already carries the family,
// so "armed" reads clearer than "lifecycle · armed".
const LIFECYCLE_TYPE: Record<LifecycleKind, string> = {
  warmed: "warmed",
  armed: "armed",
  dearmed: "de-armed",
  exit_by: "exit-by",
};

/** One overlay event → its ledger row. The `detail` reuses the chart tooltip's per-family lines where they
 *  read as a table cell (insider: who + fill + disclosure lag + market-price context; trigger: label +
 *  source), and stays quiet where the tooltip would only echo another column (a lifecycle row's single
 *  tooltip line IS its date). The insider "transacted <date>" echo is dropped — the date is its own column. */
export function ledgerRow(e: OverlayEvent): LedgerRow {
  const base = { n: e.n, cls: familyCls(e.family), date: e.date };
  if (e.family === "insider") {
    const tip = overlayTooltip(e);
    const lines = tip.lines.filter((l) => !l.startsWith("transacted "));
    return { ...base, type: "insider buy", detail: [tip.title, ...lines].join(" · ") };
  }
  if (e.family === "sell") {
    // Slice B: the sell mirror of the insider-buy row — who + fill + the two clocks + the character
    // line (why it did/didn't count toward the cluster); the date echo drops (its own column).
    const tip = overlayTooltip(e);
    const lines = tip.lines.filter((l) => !l.startsWith("transacted "));
    return { ...base, type: "insider sell", detail: [tip.title, ...lines].join(" · ") };
  }
  if (e.family === "filing") {
    // Slice B: the type cell carries the verbatim form ("8-K" / "8-K/A" — the tooltip title), so the
    // detail is the lines alone: items (or "items unresolved") + the ingest lag; the "filed <date>"
    // echo drops (the date is its own column). The EDGAR index URL rides as the row's jump (#6).
    const tip = overlayTooltip(e);
    const lines = tip.lines.filter((l) => !l.startsWith("filed "));
    return {
      ...base,
      type: e.event.form,
      detail: lines.join(" · "),
      links: [{ label: "filing index", url: e.event.url }],
    };
  }
  if (e.family === "activist") {
    // Slice B: type = the verbatim form, both naming eras (a 13G names itself — the grey row + the
    // form say "passive family" together); detail = filer (or "filer unresolved") + pct + ingest lag.
    const tip = overlayTooltip(e);
    const lines = tip.lines.filter((l) => !l.startsWith("filed "));
    return {
      ...base,
      type: e.stake.form,
      detail: lines.join(" · "),
      links: [{ label: "filing index", url: e.stake.url }],
    };
  }
  if (e.family === "trigger") {
    // title = the trigger label; lines = kind · ticker, grade, the arm linkage (only when the chip's
    // date differs from the arm), then the capped per-source provenance refs
    const tip = overlayTooltip(e);
    const links = triggerLinks(e.trigger);
    return {
      ...base,
      type: "arm trigger",
      detail: [tip.title, ...tip.lines].join(" · "),
      ...(links.length > 0 ? { links } : {}),
    };
  }
  if (e.family === "operator") {
    // the type cell already says "operator" (= the tooltip's title), so only the LINES join: the
    // action word leads them by construction — "took @ $X (logged fill) · running +8.0% · <reason>"
    return { ...base, type: "operator", detail: overlayTooltip(e).lines.join(" · ") };
  }
  if (e.family === "signal") {
    // A3: the tape read. The type cell names the family ("tape signal"), so the detail leads with the
    // event's own LABEL ("golden cross: 50d crossed above 200d") and carries the epistemics lines
    // verbatim — the as-of it was derived under, plus the latest-flip-only caveat where it applies.
    // The caveat must ride HERE too, not only on the hover: the ledger is the scannable surface, and a
    // reader who never hovers would otherwise read one cross as the name's whole cross history (#6).
    const tip = overlayTooltip(e);
    return { ...base, type: "tape signal", detail: [tip.title, ...tip.lines].join(" · ") };
  }
  const detail =
    e.kind === "dearmed"
      ? e.closeReason
        // English, not the wire token — the raw token stays reachable on the same drawer, in the
        // scorecard header's `title=` (the drawer never shows the label without the token behind it)
        ? closeReasonLabel(e.closeReason)
        : "—"
      : e.kind === "exit_by"
        ? "signal-validity horizon"
        : "—"; // warmed / armed: the WHY rides the separate numbered trigger rows
  return { ...base, type: LIFECYCLE_TYPE[e.kind], detail };
}

export interface IdentityCell {
  label: string;
  value: string;
}

/** The Cockpit identity line for the drawer — type · sector · exchange · origin · market cap, off the
 *  scored member (found by security_id in the parent). A missing field reads "—" (NEVER a guess, #6) —
 *  type/origin included: both derive server-side (the SIC maps / the SEC's own locators) and abstain to
 *  null, so the "—" is the honest unknown. Deliberately NOT the scoring meters / fit / size-weight — that
 *  duplication is the NamePanel's job, which the operator asked the Scoreboard drawer to stay out of. */
export function identityCells(scored: ScoredMemberOut | null | undefined): IdentityCell[] {
  const cells: IdentityCell[] = [
    // the business-type LEAF (Business-Type M1) + the royalty overlay when it lights (never a bare
    // marker without its leaf); an ETF reads its instrument (a fund has no SIC)
    {
      label: "type",
      value:
        scored?.instrument_kind === "etf"
          ? "ETF sleeve"
          : scored?.business_type
            ? businessTypeLabel(scored.business_type) +
              (scored.royalty ? " · royalty/streaming" : "")
            : "—",
    },
    { label: "sector", value: scored?.sector ?? "—" },
    { label: "exchange", value: scored?.exchange ?? "—" },
    { label: "origin", value: scored?.origin ?? "—" },
    { label: "market cap", value: formatMarketCap(scored?.market_cap?.value) },
  ];
  // The resolved vendor price symbol — an EXCEPTION cell, appended ONLY when the name is priced under a
  // symbol other than its SEC ticker (honest loudness #7: the healthy common case has none, so a "—" here
  // would carry no signal). Off the scored wire (identity_for), never a call input — pure identity.
  if (scored?.price_symbol) cells.push({ label: "priced under", value: scored.price_symbol });
  return cells;
}

// The display-signal registry render order — MIRRORS the import order in backend/signals/display/__init__.py
// ("registration order is the panel's render order"), by each member's wire `kind`. The ledger owns the
// order so it is stable regardless of wire order; an unregistered new kind sorts last (renders, zero FE
// change), so this list drifting behind the registry costs placement, never a dropped signal.
const SIGNAL_ORDER = [
  "sma_position", // sma
  "trailing_returns", // trailing_returns
  "range_52w", // range52w
  "volume_regime", // volume_regime
  "rvol", // rvol
  "relative_strength", // relative_strength
  "insider_flow_90d", // insider_flow
  "etf_flow", // etf_flow
  "vcp", // vcp
];

/** The present-only display-signal headlines for the drawer strip, in registry order. Renders ONLY the
 *  signals that actually carry a headline (honest loudness #7 — a headline-less signal is silence, not a
 *  blank row). The component renders each via the shared Cockpit DisplayHeadlineRow. */
export function signalHeadlines(member: MemberDisplaySignalsOut | null | undefined): DisplaySignal[] {
  const withHeadline = (member?.signals ?? []).filter((s) => s.headline);
  const rank = (k: string) => {
    const i = SIGNAL_ORDER.indexOf(k);
    return i === -1 ? SIGNAL_ORDER.length : i;
  };
  return [...withHeadline].sort((a, b) => rank(a.kind) - rank(b.kind));
}
