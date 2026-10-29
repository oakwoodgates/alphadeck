import { businessTypeLabel, supersectorLabel } from "../../util/format";
import { formatMarketCap } from "../../workbench/format";
import type {
  CapTier,
  ColumnKey,
  Gap,
  PeerCapOut,
  PeerCardChipOut,
  PeerCompanyOut,
  PeerCoverageOut,
  PeerGroupOut,
  PeerGroupStatsOut,
  PeerMedianOut,
  PeerScopesOut,
  PeerTableOut,
  ScopeKind,
} from "./usePeerTable";

// The Peer Table's pure view helpers (docs/dashboards/peer-table.md): the scope pickers, the sort, the filters, the
// cells' words, the group rows and the "show the work" lines. Every cell, group figure and coverage count is computed
// server-side; this only picks, filters, orders and words them. A blank is never zero: a blank sorts last in either
// direction, and the words say why it is blank.

// --- small formatting ------------------------------------------------------------------------------------------

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A signed number to one decimal ("+4.8", "-7.1", "0.0"); a blank is "—". */
export function fmtSigned(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
}

export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)}%`);
export const fmtPp = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)} pp`);

/** A dollar amount, compact: "$13.5M", "$1.20B", "$412K", "$950". A blank is "—". */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${Math.round(a / 1e3).toLocaleString("en-US")}K`;
  return `${sign}$${Math.round(a)}`;
}

/** A share count, compact: "10.0M", "1.20B". */
export function fmtShares(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3).toLocaleString("en-US")}K`;
  return String(Math.round(v));
}

/** The sign of a number for its quiet tint: up, down, or none (a flat 0 or a blank). */
export function tone(v: number | null | undefined): "pos" | "neg" | null {
  if (v == null || v === 0) return null;
  return v > 0 ? "pos" : "neg";
}

// --- the scope pickers -----------------------------------------------------------------------------------------

export const SCOPE_LABEL: Record<ScopeKind, string> = {
  business_type: "Business type",
  supersector: "Supersector",
  card: "Card",
  all: "All live cards",
};

export const SCOPE_ORDER: readonly ScopeKind[] = ["business_type", "supersector", "card", "all"];

export type Scope = { kind: ScopeKind; key: string | null };

export type ScopeOption = { key: string; label: string; companies: number; hint?: string };

const UNCLASSIFIED = "unclassified";

/** A business-type leaf's words ("biotech/pharma"; "Unclassified" for none). */
export const leafLabel = (key: string) => (key === UNCLASSIFIED ? "Unclassified" : businessTypeLabel(key));

/** A supersector's words ("Energy & Utilities"; "Unclassified" for none). */
export const sectorLabel = (key: string) => (key === UNCLASSIFIED ? "Unclassified" : supersectorLabel(key));

/** The picks of one scope kind at the as-of, in the server's order (biggest first for the types; cards A to Z). */
export function optionsFor(kind: ScopeKind, scopes: PeerScopesOut): ScopeOption[] {
  if (kind === "business_type") {
    return scopes.business_types.map((o) => ({ key: o.key, label: leafLabel(o.key), companies: o.companies }));
  }
  if (kind === "supersector") {
    return scopes.supersectors.map((o) => ({ key: o.key, label: sectorLabel(o.key), companies: o.companies }));
  }
  if (kind === "card") {
    return scopes.cards.map((c) => ({
      key: c.thesis_id,
      label: c.name,
      companies: c.companies,
      hint: `${plural(c.links, "link", "links")} today`,
    }));
  }
  return [];
}

/** The pick a scope kind opens on: the server's default leaf for business type; else the first option. */
export function firstKey(kind: ScopeKind, scopes: PeerScopesOut): string | null {
  if (kind === "all") return null;
  if (kind === "business_type" && scopes.default_scope === "business_type" && scopes.default_key) {
    return scopes.default_key;
  }
  return optionsFor(kind, scopes)[0]?.key ?? null;
}

/** The scope the page reads: the operator's pick when it is still pickable at this as-of, else the default (the
 *  first leaf with two or more companies). `null` = nothing to scope to (no company on any counted card). */
export function resolveScope(picked: Scope | null, scopes: PeerScopesOut): Scope | null {
  if (picked) {
    if (picked.kind === "all") return picked;
    if (picked.key != null && optionsFor(picked.kind, scopes).some((o) => o.key === picked.key)) return picked;
  }
  if (scopes.default_scope && scopes.default_key) return { kind: scopes.default_scope, key: scopes.default_key };
  if (scopes.companies > 0) return { kind: "all", key: null };
  return null;
}

/** The scope in words ("Business type: miner", "Card: Grid", "All live cards"). */
export function scopeWords(d: Pick<PeerTableOut, "scope" | "key" | "scope_name">): string {
  if (d.scope === "all") return SCOPE_LABEL.all;
  if (d.scope === "card") return `Card: ${d.scope_name ?? d.key ?? ""}`;
  const pick = d.key ?? "";
  return `${SCOPE_LABEL[d.scope]}: ${d.scope === "business_type" ? leafLabel(pick) : sectorLabel(pick)}`;
}

// --- the cells' words ------------------------------------------------------------------------------------------

/** Why a cell is blank, in full (the hover). */
export const GAP_WORDS: Record<Gap, string> = {
  flagged: "its data flags reach its price readings, so the Cockpit grays this cell too",
  no_price: "no price history is stored for this company at the as-of",
  no_reading: "the reading needs more history than is stored",
  no_shares: "awaiting shares: no shares-outstanding fact is on file (mark the name for data)",
  ads_unread: "ADS ratio unread: the cap is withheld rather than guessed at one ordinary share per ADS",
  no_cap: "the share count or the close is not positive",
  no_revenue: "no revenue facts are stored for this company",
  no_prior_year: "no row for the same period a year earlier in the stored series",
  no_burn: "no cash and burn fact is on file (mark the name for data)",
  no_convert: "no live convertible-note overhang is on file",
  no_mix: "no revenue-mix fact is on file (mark the name for data)",
  no_form4: "no Form 4 is ingested for this company",
  no_spy: "SPY has no price history at the as-of, so there is nothing to subtract",
  ends_differ: "its last close is not SPY's last close, so the two returns would end on different days",
};

/** The same, short (the coverage lines and the cap cell). */
export const GAP_SHORT: Record<Gap, string> = {
  flagged: "flagged",
  no_price: "awaiting price",
  no_reading: "short history",
  no_shares: "awaiting shares",
  ads_unread: "ADS ratio unread",
  no_cap: "not positive",
  no_revenue: "no revenue",
  no_prior_year: "no prior year",
  no_burn: "no cash/burn fact",
  no_convert: "no live convert",
  no_mix: "no revenue-mix fact",
  no_form4: "no Form 4",
  no_spy: "no SPY history",
  ends_differ: "last close differs from SPY's",
};

/** A cell's text, quiet tint and hover. A blank says why in the hover; a flagged price history grays it. */
export type Cell = { text: string; tone: "pos" | "neg" | null; gray: boolean; title: string };

const blank = (gap: Gap, note: string | null | undefined, text = "—"): Cell => ({
  text: gap === "flagged" ? "flagged" : text,
  tone: null,
  gray: gap === "flagged",
  title:
    gap === "flagged"
      ? `Withheld: ${note ?? "a flagged price history"}. ${GAP_WORDS.flagged}.`
      : `Blank: ${GAP_WORDS[gap]}${note ? ` (${note})` : ""}.`,
});

const plain = (text: string, title: string, t: "pos" | "neg" | null = null): Cell => ({
  text,
  tone: t,
  gray: false,
  title,
});

export function capTitle(c: PeerCapOut): string {
  if (c.value == null) return "";
  const parts = [`approximate: latest close ${c.close == null ? "?" : `$${c.close.toFixed(2)}`} (${fmtDay(c.close_date)})`];
  const count = c.shares == null ? "?" : fmtShares(c.shares);
  parts.push(`× ${count} shares on file (as of ${fmtDay(c.shares_date)})`);
  if (c.ads_ratio != null) parts.push(`÷ ${c.ads_ratio} ordinary shares per ADS`);
  if (c.split_factor != null) {
    parts.push(`× ${Number(c.split_factor.toPrecision(6))} to put the count on the close's split scale`);
  }
  return `${parts.join(" ")} = ${formatMarketCap(c.value)}`;
}

export function capCell(c: PeerCapOut): Cell {
  if (c.gap) {
    // the cap's blank reasons are the operator's to-do list ("awaiting shares"), so they ride in the cell
    return { ...blank(c.gap, c.note, GAP_SHORT[c.gap]), gray: c.gap === "flagged" };
  }
  return plain(`${c.tier ?? ""} · ${formatMarketCap(c.value)}`, capTitle(c));
}

export function revenueCell(r: { value: number | null; period_end: string | null; gap: Gap | null }): Cell {
  if (r.gap || r.value == null) return blank(r.gap ?? "no_revenue", null);
  return plain(fmtUsd(r.value), `Revenue for the period ended ${fmtDay(r.period_end)} (the XBRL series)`);
}

export function yoyCell(y: {
  value: number | null;
  prior_value: number | null;
  prior_period_end: string | null;
  gap: Gap | null;
}): Cell {
  if (y.gap || y.value == null) return blank(y.gap ?? "no_prior_year", null);
  return plain(
    fmtPct(y.value),
    `${fmtPct(y.value)} against ${fmtUsd(y.prior_value)} for the period ended ${fmtDay(y.prior_period_end)}`,
    tone(y.value),
  );
}

export function runwayCell(r: {
  months: number | null;
  cash_positive: boolean;
  cash_usd: number | null;
  burn_usd: number | null;
  as_of: string | null;
  gap: Gap | null;
}): Cell {
  if (r.gap) return blank(r.gap, null);
  const fact = `cash ${fmtUsd(r.cash_usd)}, burn ${fmtUsd(r.burn_usd)} per quarter, from the fact dated ${fmtDay(r.as_of)}`;
  if (r.cash_positive) return plain("cash-positive", `The burn is zero or negative: no finite runway (${fact})`);
  return plain(`${(r.months ?? 0).toFixed(1)} mo`, `Months of cash at the current burn: ${fact}`);
}

export function numberCell(
  c: { value: number | null; gap: Gap | null; note: string | null },
  text: (v: number) => string,
  title: (v: number) => string,
  signed = false,
): Cell {
  if (c.gap || c.value == null) return blank(c.gap ?? "no_reading", c.note);
  return plain(text(c.value), title(c.value), signed ? tone(c.value) : null);
}

/** The 50d / 200d mark: above, below or at the average, with the percent on hover. */
export function sideCell(c: { value: number | null; gap: Gap | null; note: string | null }, days: 50 | 200): Cell {
  if (c.gap || c.value == null) return blank(c.gap ?? "no_reading", c.note);
  const side = c.value > 0 ? "above" : c.value < 0 ? "below" : "at";
  return plain(
    side === "above" ? "▲" : side === "below" ? "▼" : "●",
    `${fmtPct(c.value)} ${side} the ${days}-day average`,
    side === "above" ? "pos" : side === "below" ? "neg" : null,
  );
}

export function spyCell(s: {
  value: number | null;
  ret_pct: number | null;
  spy_pct: number | null;
  last_close: string | null;
  gap: Gap | null;
  note: string | null;
}): Cell {
  if (s.gap || s.value == null) {
    const own = s.ret_pct != null ? ` Its own 30-trading-day return is ${fmtPct(s.ret_pct)}.` : "";
    const c = blank(s.gap ?? "no_reading", s.note);
    return { ...c, title: `${c.title}${c.gray ? "" : own}` };
  }
  return plain(
    fmtSigned(s.value),
    `30 trading days to ${fmtDay(s.last_close)}: the company ${fmtPct(s.ret_pct)} minus SPY ${fmtPct(s.spy_pct)} = ${fmtPp(s.value)}`,
    tone(s.value),
  );
}

export function insiderCell(i: {
  buys: number | null;
  usd: number | null;
  buyers: number | null;
  gap: Gap | null;
  note: string | null;
}): Cell {
  if (i.gap || i.buys == null) return blank(i.gap ?? "no_form4", i.note);
  if (i.buys === 0) {
    return {
      text: "0",
      tone: null,
      gray: true,
      title: "Form 4s are on file for this company; none was an open-market buy in the 90-day window (as the Cockpit screens them)",
    };
  }
  const who = i.buyers != null ? `, ${plural(i.buyers, "buyer", "buyers")}` : "";
  return plain(
    `${i.buys} · ${fmtUsd(i.usd)}`,
    `${plural(i.buys, "open-market buy", "open-market buys")} in the 90-day window${who}, ${fmtUsd(i.usd)}${i.note ? ` (${i.note})` : ""}; screened as the Cockpit's column screens them`,
  );
}

// --- the columns -----------------------------------------------------------------------------------------------

export type SortKey =
  | "company"
  | "cards"
  | "business_type"
  | "cap"
  | "revenue"
  | "yoy"
  | "runway"
  | "overhang"
  | "purity"
  | "off_high"
  | "sma50"
  | "sma200"
  | "rvol20"
  | "adv"
  | "vs_spy"
  | "insider";

export type ColumnDef = {
  key: SortKey;
  label: string;
  /** the hover on the header: what the column is and how it sorts */
  title: string;
  /** the coverage column this one counts (the first three carry none) */
  coverage?: ColumnKey;
  num?: boolean;
  /** the direction a first click sorts in */
  first: "asc" | "desc";
};

/** The data columns after the pinned Company column, in order. */
export const COLUMNS: readonly ColumnDef[] = [
  { key: "cards", label: "Cards", title: "Sort by the number of cards the company sits on", first: "desc" },
  { key: "business_type", label: "Business type", title: "Sort by business type, A to Z", first: "asc" },
  {
    key: "cap",
    label: "Cap",
    title:
      "Approximate market cap and its tier (nano < $50M · micro $50M-$300M · small $300M-$2B · mid $2B-$10B · large ≥ $10B): the latest close × the latest shares fact, the ADS ratio applied when known and withheld when unread. Sorts by the cap.",
    coverage: "cap",
    num: true,
    first: "desc",
  },
  {
    key: "revenue",
    label: "Revenue",
    title: "The latest period's revenue from the XBRL series (its period end on hover). Sorts by the amount.",
    coverage: "revenue",
    num: true,
    first: "desc",
  },
  {
    key: "yoy",
    label: "YoY",
    title: "Revenue against the same period a year earlier, when that row is in the same stored series. Sorts by the change.",
    coverage: "yoy",
    num: true,
    first: "desc",
  },
  {
    key: "runway",
    label: "Runway",
    title: "Months of cash at the current burn from the latest cash and burn fact; cash-positive when the burn is zero or negative. Sorts shortest first.",
    coverage: "runway",
    num: true,
    first: "asc",
  },
  {
    key: "overhang",
    label: "Overhang",
    title: "Convertible-note overhang as a percent of shares outstanding, the dilution clock's own number. Sorts largest first.",
    coverage: "overhang",
    num: true,
    first: "desc",
  },
  {
    key: "purity",
    label: "Purity",
    title: "The largest revenue-mix line as a percent of revenue (its segment on hover). Sorts largest first.",
    coverage: "purity",
    num: true,
    first: "desc",
  },
  {
    key: "off_high",
    label: "Off 52w high",
    title: "Percent off the 52-week closing high. Sorts nearest the high first.",
    coverage: "off_high",
    num: true,
    first: "desc",
  },
  {
    key: "sma50",
    label: "50d",
    title: "Above or below the 50-day average (the percent on hover). Sorts by the percent, highest first.",
    coverage: "sma50",
    first: "desc",
  },
  {
    key: "sma200",
    label: "200d",
    title: "Above or below the 200-day average (the percent on hover). Sorts by the percent, highest first.",
    coverage: "sma200",
    first: "desc",
  },
  {
    key: "rvol20",
    label: "RVOL 20",
    title: "The as-of bar's volume over the mean volume of the 20 bars before it. Sorts highest first.",
    coverage: "rvol20",
    num: true,
    first: "desc",
  },
  {
    key: "adv",
    label: "ADV 20d",
    title: "Average dollar volume over the last 20 bars, the liquidity cut that belongs beside a cap. Sorts largest first.",
    coverage: "adv",
    num: true,
    first: "desc",
  },
  {
    key: "vs_spy",
    label: "30D vs SPY",
    title: "The 30 trading-day return minus SPY's, in percentage points. Sorts largest first.",
    coverage: "vs_spy",
    num: true,
    first: "desc",
  },
  {
    key: "insider",
    label: "Insider buys 90d",
    title: "Open-market purchases in the 90-day window, count and dollars, as the Cockpit's column screens them. Sorts by the dollars.",
    coverage: "insider",
    num: true,
    first: "desc",
  },
];

export const FIRST_DIR: Record<SortKey, "asc" | "desc"> = {
  company: "asc",
  ...Object.fromEntries(COLUMNS.map((c) => [c.key, c.first])),
} as Record<SortKey, "asc" | "desc">;

// --- the sort (client side) --------------------------------------------------------------------------------------

export type Sort = { key: SortKey; dir: "asc" | "desc" } | null;

/** The page's default: the group order with names A to Z inside (no column sort). */
export const DEFAULT_SORT: Sort = null;

/** A click on a column: the same column flips its direction, another starts in its own first direction. */
export function nextSort(cur: Sort, key: SortKey): Sort {
  if (cur && cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: FIRST_DIR[key] };
}

/** A company's value in a column: a string or number to compare, `null` = blank (always last, whatever the
 *  direction: a blank never leads and is never read as zero). Cash-positive runway is the longest. */
export function sortValue(c: PeerCompanyOut, key: SortKey, scope: ScopeKind): string | number | null {
  switch (key) {
    case "company":
      return c.ticker.toLocaleLowerCase();
    case "cards":
      return scope === "card"
        ? (c.cards[0]?.links.map((l) => l ?? "No link").join(", ").toLocaleLowerCase() ?? null)
        : c.cards.length;
    case "business_type":
      return c.business_type ? businessTypeLabel(c.business_type).toLocaleLowerCase() : null;
    case "cap":
      return c.cap.gap ? null : c.cap.value;
    case "revenue":
      return c.revenue.gap ? null : c.revenue.value;
    case "yoy":
      return c.yoy.gap ? null : c.yoy.value;
    case "runway":
      return c.runway.gap ? null : c.runway.cash_positive ? Number.POSITIVE_INFINITY : c.runway.months;
    case "overhang":
      return c.overhang.gap ? null : c.overhang.value;
    case "purity":
      return c.purity.gap ? null : c.purity.value;
    case "off_high":
      return c.off_high.gap ? null : c.off_high.value;
    case "sma50":
      return c.sma50.gap ? null : c.sma50.value;
    case "sma200":
      return c.sma200.gap ? null : c.sma200.value;
    case "rvol20":
      return c.rvol20.gap ? null : c.rvol20.value;
    case "adv":
      return c.adv.gap ? null : c.adv.value;
    case "vs_spy":
      return c.vs_spy.gap ? null : c.vs_spy.value;
    case "insider":
      return c.insider.gap ? null : (c.insider.usd ?? c.insider.buys);
  }
}

const cmp = (a: string | number, b: string | number) => (a < b ? -1 : a > b ? 1 : 0);

/** The companies sorted by a column. A blank is last in either direction; ties break by ticker A to Z, then the key
 *  (a stable, repeatable order). No sort (`null`) keeps the order given (the server's group order, names A to Z). */
export function sortCompanies(rows: readonly PeerCompanyOut[], sort: Sort, scope: ScopeKind): PeerCompanyOut[] {
  if (!sort) return [...rows];
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = sortValue(a, sort.key, scope);
    const vb = sortValue(b, sort.key, scope);
    if (va == null && vb != null) return 1;
    if (vb == null && va != null) return -1;
    if (va != null && vb != null) {
      const c = cmp(va, vb);
      if (c !== 0) return c * sign;
    }
    return cmp(a.ticker.toLocaleLowerCase(), b.ticker.toLocaleLowerCase()) || cmp(a.key, b.key);
  });
}

// --- the filters (client side) -----------------------------------------------------------------------------------

/** A cap-tier filter value: a tier, or `none` for the companies with no cap (so the blanks can be found). */
export type TierPick = CapTier | "none";

export const TIER_ORDER: readonly CapTier[] = ["nano", "micro", "small", "mid", "large"];

export type Filters = {
  tiers: readonly TierPick[];
  /** leave out the companies whose every row is Context or Bench (they never count toward a call) */
  countingOnly: boolean;
  withRevenue: boolean;
  withFact: boolean;
  query: string;
};

export const DEFAULT_FILTERS: Filters = { tiers: [], countingOnly: false, withRevenue: false, withFact: false, query: "" };

export function filtersActive(f: Filters): boolean {
  return f.tiers.length > 0 || f.countingOnly || f.withRevenue || f.withFact || f.query.trim() !== "";
}

export function companyMatches(c: PeerCompanyOut, f: Filters): boolean {
  if (f.tiers.length > 0 && !f.tiers.includes(c.cap.tier ?? "none")) return false;
  if (f.countingOnly && c.silent_only) return false;
  if (f.withRevenue && c.revenue.gap) return false;
  if (f.withFact && !c.workbench_facts) return false;
  const q = f.query.trim().toLocaleLowerCase();
  if (q && !c.ticker.toLocaleLowerCase().includes(q) && !(c.name ?? "").toLocaleLowerCase().includes(q)) return false;
  return true;
}

/** Which controls can tell the companies apart: a control that cannot discriminate does not render (WB#3). */
export type Controls = { tiers: TierPick[]; countingOnly: boolean; withRevenue: boolean; withFact: boolean; query: boolean };

export function controlsFor(companies: readonly PeerCompanyOut[]): Controls {
  const present = new Set<TierPick>(companies.map((c) => c.cap.tier ?? "none"));
  const some = (p: (c: PeerCompanyOut) => boolean) => companies.some(p) && !companies.every(p);
  return {
    tiers: [...TIER_ORDER, "none" as const].filter((t) => present.has(t)),
    countingOnly: companies.some((c) => c.silent_only),
    withRevenue: some((c) => !c.revenue.gap),
    withFact: some((c) => c.workbench_facts),
    query: companies.length > 1,
  };
}

/** The pick words for a tier chip. */
export const tierLabel = (t: TierPick) => (t === "none" ? "no cap" : t);

// --- the company cell --------------------------------------------------------------------------------------------

/** A card chip's words: the card, then the links the company sits in there ("Grid · Turbines, Cables"). */
export function chipWords(c: PeerCardChipOut): string {
  const links = chipLinks(c);
  return links ? `${c.thesis_name} · ${links}` : c.thesis_name;
}

/** The links the company sits in on a card ("Turbines, Cables"; "No link" for none). */
export function chipLinks(c: Pick<PeerCardChipOut, "links">): string {
  return c.links.map((l) => l ?? "No link").join(", ");
}

/** The quiet tag after a company's cards: "silent on 2 cards" when every row is Context or Bench, else its context
 *  roles as lowercase words, else nothing. */
export function tagWords(c: PeerCompanyOut, roleTag: (r: PeerCompanyOut["roles"][number]) => string): string {
  if (c.silent_only) return `silent on ${plural(c.cards.length, "card", "cards")}`;
  return c.roles.map(roleTag).join(", ");
}

// --- the group rows -----------------------------------------------------------------------------------------------

/** A group's words: the leaf or supersector label, the link, "No link", Context or Bench. */
export function groupLabel(g: Pick<PeerGroupOut, "kind" | "label">): string {
  if (g.kind === "business_type") return leafLabel(g.label);
  if (g.kind === "supersector") return sectorLabel(g.label);
  return g.label;
}

/** A group figure's cell: the value, what it rests on, and the hover. One priced name shows its value with "1 name",
 *  never a median label (a median of one is not a median). `null` = nothing to say (no priced name). */
export type GroupFigure = { text: string; sub: string; title: string };

function medianFigure(m: PeerMedianOut, fmt: (v: number) => string, what: string, qualifier: string): GroupFigure | null {
  if (m.value == null || m.n === 0) return null;
  if (m.n === 1) {
    return { text: fmt(m.value), sub: "1 name", title: `One name (${qualifier}): its ${what}, not a median` };
  }
  const n = m.n.toLocaleString("en-US");
  return {
    text: fmt(m.value),
    sub: `median of ${n} ${qualifier}`,
    title: `Median ${what} of ${n} names (${qualifier}), equal weight`,
  };
}

/** The figures a group row carries under the columns they belong to. */
export function groupFigures(s: PeerGroupStatsOut): Partial<Record<SortKey, GroupFigure>> {
  const out: Partial<Record<SortKey, GroupFigure>> = {};
  const cap = medianFigure(s.cap, formatMarketCap, "approximate market cap", "priced");
  if (cap) out.cap = cap;
  if (s.priced_50d > 0) {
    out.sma50 = {
      text: `${s.above_50d} of ${s.priced_50d}`,
      sub: "priced",
      title: `${plural(s.above_50d, "name is", "names are")} above the 50-day average, of the ${plural(s.priced_50d, "name", "names")} with a 50-day reading`,
    };
  }
  const yoy = medianFigure(s.yoy, fmtPct, "year-over-year revenue change", "with a prior year");
  if (yoy) out.yoy = yoy;
  const spy = medianFigure(s.vs_spy, fmtSigned, "30-day return minus SPY's, in percentage points", "priced");
  if (spy) out.vs_spy = spy;
  if (s.runway > 0) {
    out.runway = {
      text: String(s.runway),
      sub: "with a figure",
      title: `${plural(s.runway, "name has", "names have")} a runway figure (months, or cash-positive)`,
    };
  }
  return out;
}

// --- the coverage -----------------------------------------------------------------------------------------------

/** "41 of 58". */
export const coverageWords = (c: Pick<PeerCoverageOut, "stated" | "total">) =>
  `${c.stated.toLocaleString("en-US")} of ${c.total.toLocaleString("en-US")}`;

/** A column's coverage in full: how many names state it and the blanks by reason (the to-do list). */
export function coverageTitle(c: PeerCoverageOut): string {
  const head = `${coverageWords(c)} names state this`;
  if (c.blanks.length === 0) return `${head}.`;
  return `${head}. Blank: ${c.blanks.map((b) => `${b.n.toLocaleString("en-US")} ${GAP_SHORT[b.gap]}`).join(", ")}.`;
}

export const coverageOf = (rows: readonly PeerCoverageOut[], column: ColumnKey) => rows.find((c) => c.column === column);

/** The columns' coverage lines for the expandable "show the work" block: "Cap: 41 of 58 — 9 awaiting shares, ...". */
export function coverageLines(rows: readonly PeerCoverageOut[]): string[] {
  return rows.map((c) => {
    const label = COLUMNS.find((d) => d.coverage === c.column)?.label ?? c.column;
    const blanks = c.blanks.length ? ` — blank: ${c.blanks.map((b) => `${b.n.toLocaleString("en-US")} ${GAP_SHORT[b.gap]}`).join(", ")}` : "";
    return `${label}: ${coverageWords(c)}${blanks}`;
  });
}

// --- the show-the-work lines ------------------------------------------------------------------------------------

function cacheWords(d: PeerTableOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet lines under the table. `shown` = the companies in view after the filters. */
export function workLines(d: PeerTableOut, shown: number, countingOnly: boolean): string[] {
  const lines = [
    [
      `as of ${fmtDay(d.asof)}`,
      d.clock === "record" ? "live read" : `as it was public by ${fmtDay(d.asof)}`,
      cacheWords(d),
    ].join(" · "),
    [
      scopeWords(d),
      `${plural(d.cards_counted, "live card", "live cards")} counted`,
      d.cards_not_yet_created > 0 ? `${d.cards_not_yet_created.toLocaleString("en-US")} not yet created` : null,
      d.scope === "card" ? null : `${plural(d.scope_cards, "card holds", "cards hold")} a name in scope`,
    ]
      .filter(Boolean)
      .join(" · "),
    [
      plural(d.companies.length, "company", "companies"),
      `${plural(d.rows, "roster row", "roster rows")}`,
      `${d.silent_only.toLocaleString("en-US")} silent only (Context or Bench)`,
      shown !== d.companies.length ? `${shown.toLocaleString("en-US")} in view` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  ];
  if (countingOnly) lines.push("group figures and column coverage count the names that count (silent-only names left out)");
  if (d.spy) {
    lines.push(
      `30D vs SPY: 30 trading days of closes, the company minus ${d.benchmark} (${d.benchmark} ${fmtPct(d.spy.ret_30d)}, last close ${fmtDay(d.spy.end)})`,
    );
  } else {
    lines.push(`30D vs SPY: ${d.benchmark} has no price history at this as-of, so no name can be compared`);
  }
  return lines;
}

/** Roster rows with no resolved security can carry no reading: counted here, listed on expand, never a row
 *  (invariant #9). */
export function unresolvedSummary(d: Pick<PeerTableOut, "unresolved_rows">): string | null {
  if (d.unresolved_rows === 0) return null;
  return `${plural(d.unresolved_rows, "roster row has", "roster rows have")} no resolved security, so no reading can be attached and it is not a row`;
}

/** Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in. */
export const ROSTER_PIT_FROM = "2026-09-15";

export function rosterWords(n: number): string | null {
  if (n === 0) return null;
  return `${plural(n, "counted card has", "counted cards have")} no roster snapshot by this as-of, so today's roster stands in for ${n === 1 ? "it" : "them"}: rosters are point-in-time only from ${fmtDay(ROSTER_PIT_FROM)}.`;
}

/** The page's honest notes and known limits, stated on the page (docs/dashboards/peer-table.md). */
export const LIMITS: readonly string[] = [
  "Approximate market cap: the latest close × the latest shares fact (the ADS ratio applied when known and the cap withheld when unread; a split in force between the count and the close puts the count on the close's scale). An ADR with an unread ratio shows no cap by design.",
  "Workbench facts (shares, cash and burn, revenue mix, convertible notes) exist only where a name was marked for data or an automated leg reached it; the blanks are the to-do list, counted per column.",
  "Revenue is the XBRL series only (companyfacts); a name with no revenue facts shows blank, not zero. Short interest is not stored, so there is no column for it.",
  "Insider buys are shown as the Cockpit's column screens them: open-market purchases only, offer-price subscriptions and transfers between linked filers set aside.",
  "A company whose data flags reach its price readings keeps its row; its price-derived cells (cap, 52w, 50d, 200d, RVOL, dollar volume, vs SPY) are blank with the reason and it is named above.",
  "The roster is each card's roster as of the as-of; business types and links are today's. Group figures are medians with equal weight and no score; each says how many names it rests on.",
];
