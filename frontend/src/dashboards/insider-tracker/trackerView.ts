import { fmtDate } from "../../util/format";
import type {
  InsiderTrackerCapOut,
  InsiderTrackerClusterOut,
  InsiderTrackerDays,
  InsiderTrackerOut,
  InsiderTrackerRowOut,
  InsiderTrackerTriggerOut,
} from "./useInsiderTracker";

// Insider Buying's pure view logic (docs/dashboards/insider-tracker.md): the grouping (by filing week, by name or
// by card), the filters and their per-chip counts, and the words the page uses. Every row and count is computed
// server-side; this file only slices and words them. There is NO aggregate here and none may be added
// (docs/DASHBOARDS.md rule 6: a median move "after insider buys" is an event-outcome statistic, sliced by
// market-cap tier on the Track Record page; this page lists). The only numbers across rows are counts and the
// dollar total a name's group header states.

export type Row = InsiderTrackerRowOut;
export type RoleClass = Row["role_classes"][number];
export type GroupBy = "week" | "name" | "card";
export type SizeBand = "lt100k" | "100k" | "1m" | "10m" | "none";
export type TierKey = "nano" | "micro" | "small" | "mid" | "large" | "none";
/** The filter dimensions that are chips or a picker (the two toggles are not dimensions). */
export type Dim = "size" | "role" | "tier" | "card";

export const GROUP_BYS: readonly { key: GroupBy; label: string }[] = [
  { key: "week", label: "By filing week" },
  { key: "name", label: "By name" },
  { key: "card", label: "By card" },
];

export const DAYS_OPTIONS: readonly { value: InsiderTrackerDays; label: string }[] = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];
export const DEFAULT_DAYS: InsiderTrackerDays = 30;

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- size bands (PROPOSED: $100K, $1M, $10M; the operator confirms or moves them) ---------------------------------

/** The lower edges of the size bands, in dollars. PROPOSED. */
export const SIZE_FLOORS: readonly [number, number, number] = [100_000, 1_000_000, 10_000_000];
export const SIZE_ORDER: readonly SizeBand[] = ["lt100k", "100k", "1m", "10m", "none"];
export const SIZE_LABEL: Record<SizeBand, string> = {
  lt100k: "under $100K",
  "100k": "$100K to $1M",
  "1m": "$1M to $10M",
  "10m": "$10M and over",
  none: "size not stated",
};

export function sizeBand(usd: number | null): SizeBand {
  if (usd === null) return "none";
  if (usd >= SIZE_FLOORS[2]) return "10m";
  if (usd >= SIZE_FLOORS[1]) return "1m";
  if (usd >= SIZE_FLOORS[0]) return "100k";
  return "lt100k";
}

export const ROLE_ORDER: readonly RoleClass[] = ["officer", "director", "ten_percent_owner", "other"];
export const ROLE_LABEL: Record<RoleClass, string> = {
  officer: "officer",
  director: "director",
  ten_percent_owner: "10% owner",
  other: "other",
};
export const TIER_ORDER: readonly TierKey[] = ["nano", "micro", "small", "mid", "large", "none"];
export const TIER_LABEL: Record<TierKey, string> = {
  nano: "nano",
  micro: "micro",
  small: "small",
  mid: "mid",
  large: "large",
  none: "no cap",
};

// --- filters -----------------------------------------------------------------------------------------------------

export type Filters = {
  sizes: ReadonlySet<SizeBand>;
  roles: ReadonlySet<RoleClass>;
  tiers: ReadonlySet<TierKey>;
  /** one card's thesis_id, "" = every card */
  card: string;
  clustersOnly: boolean;
  triggersOnly: boolean;
};

export const DEFAULT_FILTERS: Filters = {
  sizes: new Set(),
  roles: new Set(),
  tiers: new Set(),
  card: "",
  clustersOnly: false,
  triggersOnly: false,
};

export const tierKey = (r: Row): TierKey => (r.cap.tier as TierKey | null) ?? "none";

/** The value(s) a row holds on a dimension: a row can hold several roles and sit on several cards. */
export function rowValues(r: Row, dim: Dim): string[] {
  switch (dim) {
    case "size":
      return [sizeBand(r.usd)];
    case "role":
      return [...r.role_classes];
    case "tier":
      return [tierKey(r)];
    case "card":
      return r.cards.map((c) => c.thesis_id);
  }
}

const chosen = (f: Filters, dim: Dim): ReadonlySet<string> | string =>
  dim === "size" ? f.sizes : dim === "role" ? f.roles : dim === "tier" ? f.tiers : f.card;

/** Does the row pass every filter except the one on `skip` (an empty chip set or "" = no filter on it)? */
function passes(r: Row, f: Filters, skip?: Dim): boolean {
  for (const dim of ["size", "role", "tier", "card"] as const) {
    if (dim === skip) continue;
    const c = chosen(f, dim);
    const have = rowValues(r, dim);
    if (typeof c === "string" ? c !== "" && !have.includes(c) : c.size > 0 && !have.some((v) => c.has(v))) {
      return false;
    }
  }
  if (f.clustersOnly && r.cluster === null) return false;
  if (f.triggersOnly && r.triggers.length === 0) return false;
  return true;
}

export const filterRows = (rows: readonly Row[], f: Filters): Row[] => rows.filter((r) => passes(r, f));

export function filtersActive(f: Filters): boolean {
  return f.sizes.size > 0 || f.roles.size > 0 || f.tiers.size > 0 || f.card !== "" || f.clustersOnly || f.triggersOnly;
}

/** How many rows each value of `dim` would show with every OTHER filter applied: the number a chip restates. A
 *  value no row has is absent (never a zero for a chip that cannot exist). */
export function facetCounts(rows: readonly Row[], f: Filters, dim: Dim): Map<string, number> {
  const out = new Map<string, number>();
  for (const r of rows) {
    const ok = passes(r, f, dim);
    for (const v of rowValues(r, dim)) {
      if (!out.has(v)) out.set(v, 0);
      if (ok) out.set(v, (out.get(v) ?? 0) + 1);
    }
  }
  return out;
}

export type Toggle = "clustersOnly" | "triggersOnly";

const toggleTest: Record<Toggle, (r: Row) => boolean> = {
  clustersOnly: (r) => r.cluster !== null,
  triggersOnly: (r) => r.triggers.length > 0,
};

/** How many rows a toggle would show with every OTHER filter applied (the number its chip restates). */
export function toggleCount(rows: readonly Row[], f: Filters, toggle: Toggle): number {
  const without: Filters = { ...f, [toggle]: false };
  return rows.filter((r) => passes(r, without) && toggleTest[toggle](r)).length;
}

/** A toggle is worth a control only when the payload holds rows on both sides of it (WB#3: a control that cannot
 *  discriminate does not render). */
export function toggleDiscriminates(rows: readonly Row[], toggle: Toggle): boolean {
  const hits = rows.filter(toggleTest[toggle]).length;
  return hits > 0 && hits < rows.length;
}

/** The values of `dim` present anywhere in the payload, in the page's order: sizes small to large then "not
 *  stated", roles officer · director · 10% owner · other, tiers nano to large then "no cap", cards A→Z by name. A
 *  dimension with fewer than two values renders no control (it cannot discriminate: WB#3). */
export function dimValues(rows: readonly Row[], dim: Dim): string[] {
  const seen = new Map<string, string>(); // value -> sort label
  for (const r of rows) {
    if (dim === "card") {
      for (const c of r.cards) if (!seen.has(c.thesis_id)) seen.set(c.thesis_id, c.thesis_name);
    } else {
      for (const v of rowValues(r, dim)) seen.set(v, v);
    }
  }
  const values = [...seen.keys()];
  const order = dim === "size" ? SIZE_ORDER : dim === "role" ? ROLE_ORDER : dim === "tier" ? TIER_ORDER : null;
  if (order) return values.sort((a, b) => order.indexOf(a as never) - order.indexOf(b as never));
  return values.sort(
    (a, b) =>
      (seen.get(a) ?? "").localeCompare(seen.get(b) ?? "", undefined, { sensitivity: "base" }) || a.localeCompare(b),
  );
}

// --- grouping ----------------------------------------------------------------------------------------------------

export type Group = {
  key: string;
  label: string;
  /** set for a card group: its header opens the card */
  thesisId: string | null;
  rows: Row[];
  names: number;
  /** distinct buyers by the filer's name; a filing that names no filer is no buyer here */
  buyers: number;
  /** the dollars the group's purchases state, added; null when none states one */
  usd: number | null;
  usdFrom: number;
  /** the most buyers any row of the group has in its cluster; null when none is in one */
  clusterBuyers: number | null;
};

/** "Week of Oct 26": the Monday the server grouped on. */
export const weekLabel = (week: string) => `Week of ${fmtDate(week)}`;

/** The rows grouped, each group's rows in the order given (the server's: filing date newest first, then dollar
 *  size, then ticker). Weeks newest first; names by their newest filing, then ticker; cards A→Z. A name on two
 *  cards is listed under both. */
export function groupRows(rows: readonly Row[], by: GroupBy): Group[] {
  const groups = new Map<string, Group & { newest: string }>();
  const add = (key: string, label: string, thesisId: string | null, r: Row) => {
    let g = groups.get(key);
    if (!g) {
      g = { key, label, thesisId, rows: [], names: 0, buyers: 0, usd: null, usdFrom: 0, clusterBuyers: null, newest: "" };
      groups.set(key, g);
    }
    g.rows.push(r);
    if (r.filed > g.newest) g.newest = r.filed;
  };
  for (const r of rows) {
    if (by === "week") add(r.week, weekLabel(r.week), null, r);
    else if (by === "name") add(r.security_id, r.ticker, null, r);
    else for (const c of r.cards) add(c.thesis_id, c.thesis_name, c.thesis_id, r);
  }
  for (const g of groups.values()) {
    g.names = new Set(g.rows.map((r) => r.security_id)).size;
    g.buyers = new Set(g.rows.filter((r) => r.filer).map((r) => (r.filer as string).toLowerCase().replace(/\s+/g, " ").trim())).size;
    const stated = g.rows.filter((r) => r.usd !== null);
    g.usdFrom = stated.length;
    g.usd = stated.length > 0 ? stated.reduce((s, r) => s + (r.usd as number), 0) : null;
    const buyers = g.rows.map((r) => r.cluster?.buyers ?? 0);
    g.clusterBuyers = Math.max(0, ...buyers) > 0 ? Math.max(...buyers) : null;
  }
  const out = [...groups.values()];
  const byLabel = (a: Group, b: Group) =>
    a.label.localeCompare(b.label, undefined, { sensitivity: "base" }) || a.key.localeCompare(b.key);
  if (by === "week") return out.sort((a, b) => (a.key < b.key ? 1 : a.key > b.key ? -1 : 0));
  if (by === "name") return out.sort((a, b) => (a.newest < b.newest ? 1 : a.newest > b.newest ? -1 : byLabel(a, b)));
  return out.sort(byLabel);
}

/** The group header's counts, in words: purchases and names for a week or a card; buyers and the dollars stated for
 *  a name. */
export function groupFacts(g: Group, by: GroupBy): string[] {
  if (by === "name") {
    return [
      g.buyers > 0 ? plural(g.buyers, "buyer", "buyers") : "buyer not named",
      g.usd !== null ? `${usdWords(g.usd)} total` : "size not stated",
    ];
  }
  return [plural(g.rows.length, "purchase", "purchases"), plural(g.names, "name", "names")];
}

/** The hover on a name group's dollar total: how many of its purchases state a size. */
export const usdFromTitle = (g: Group) =>
  `from ${g.usdFrom} of ${plural(g.rows.length, "purchase", "purchases")} that state a size`;

// --- cell words --------------------------------------------------------------------------------------------------

/** A dollar figure: whole dollars below a million, then millions and billions with one decimal; a figure that
 *  rounds up to 1,000 of its unit moves to the next ("$999.9M", then "$1.0B"). */
export function usdWords(v: number): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a < 1e6) return `${sign}$${Math.round(a).toLocaleString("en-US")}`;
  if (a < 999_950_000) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  return `${sign}$${(a / 1e9).toFixed(1)}B`;
}

export const sharesWords = (n: number | null) =>
  n === null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: 2 });

export function priceWords(p: number | null): string {
  if (p === null) return "—";
  return Math.abs(p) >= 1 || p === 0 ? `$${p.toFixed(2)}` : `$${p.toFixed(4)}`;
}

/** "filed 3 days after the trade" when the filing is later than the trade; null when there is none to make. */
export function tradeNote(r: Row): string | null {
  const n = r.filed_days_after;
  return n === null ? null : `filed ${plural(n, "day", "days")} after the trade`;
}

/** The cluster word: "cluster · 3 buyers" (the row's own buyer counted). */
export const clusterWords = (c: InsiderTrackerClusterOut) => `cluster · ${plural(c.buyers, "buyer", "buyers")}`;

/** The hover on a cluster: who else filed within 30 days of this row, and when. */
export function clusterTitle(c: InsiderTrackerClusterOut): string {
  return `Also filed within 30 days: ${c.others.map((o) => `${o.name} (${fmtDate(o.filed)})`).join(", ")}`;
}

/** Why a row reads "not a trigger": the page says nothing about why beyond this (the trigger path owns that). */
export const NOT_A_TRIGGER_TITLE =
  "below the detector's bar or screened (a control holder, an internal transfer); the page does not re-derive the screens";

export function triggerTitle(h: InsiderTrackerTriggerOut): string {
  const state = h.live === true ? "still on the card" : h.live === false ? "no longer on the card at the as-of" : "the card in effect could not be read";
  return `First recorded ${fmtDate(h.recorded_on)} on ${h.thesis_name} · ${state}`;
}

/** A return fraction as a signed percent with one decimal ("+12.3%", "-4.0%", "0.0%"). */
export function pct(frac: number | null | undefined): string {
  if (frac == null) return "—";
  const v = Math.round(frac * 1000) / 10;
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export type Tone = "pos" | "neg" | "flat";
/** The sign tone, only for a signed since-value. */
export function tone(frac: number | null | undefined): Tone {
  if (frac == null) return "flat";
  const v = Math.round(frac * 1000) / 10;
  return v > 0 ? "pos" : v < 0 ? "neg" : "flat";
}

/** Why a market cap is blank (the Peer Table's gap codes). */
export const CAP_GAP_WORDS: Record<string, string> = {
  flagged: "withheld, a data flag reaches its price",
  no_price: "awaiting price",
  no_shares: "awaiting shares",
  ads_unread: "ADS ratio not read",
  no_cap: "share count or close not positive",
};

/** The approximate market cap in one line: "≈ $125.0M micro", or "—" when it is blank. Labeled approximate. */
export function capWords(cap: InsiderTrackerCapOut): string {
  if (cap.value === null) return "—";
  return `≈ ${usdWords(cap.value)}${cap.tier ? ` ${cap.tier}` : ""}`;
}

export function capTitle(cap: InsiderTrackerCapOut): string {
  if (cap.value === null) {
    const why = cap.gap ? (CAP_GAP_WORDS[cap.gap] ?? cap.gap) : "not pinned down";
    return `Approximate market cap blank: ${why}${cap.note ? ` (${cap.note})` : ""}`;
  }
  return "Approximate: the latest close at the as-of times the latest shares fact (the Peer Table's rule). A filter dimension only.";
}

// --- the quiet "show the work" line ---------------------------------------------------------------------------------

export function clockWords(data: InsiderTrackerOut): string {
  return data.clock === "public" ? "public clock: what was public by the end of that date" : "live read";
}

/** The fixed line under the work lines: what counts, and why no number summarizes these rows. */
export const NO_AGGREGATE =
  "open-market purchases (code P) only; a filing counts from the day it was filed; no aggregate here: outcomes by tier are on the Track Record page";

export const LIMITS: readonly string[] = [
  "Open-market purchases only (code P): option exercises, grants and conversions are not purchases here.",
  "The role is the filing's word, as the reader gives it; the role filter reads those words, so a title like \"Director of Sales\" reads as a director.",
  "Foreign private issuers file no Form 4, so ADR names are absent, not clean.",
  "\"Not a trigger\" says nothing about why: the detector's screens are not re-derived here.",
  "The dollars are the purchases' own, not net of any sale in the same filing; a filing that names several owners shows the first and counts the rest.",
  "A cluster counts distinct named buyers whose filings fall within 30 days of the row's, among the purchases read, including the 30 days before the window.",
  "As-traded closes, no dividends.",
  "No aggregate on this page.",
];

export function emptyWords(data: InsiderTrackerOut): string {
  return `No open-market purchase was filed by a counted name in the last ${data.window_days} days (see the limits below).`;
}

/** The work, one quiet line per thought: what was read, what is listed, what the tape rests on. `shown` is the rows
 *  after the filters. */
export function workLines(data: InsiderTrackerOut, shown: number, filtering: boolean): string[] {
  const c = data.counts;
  const lines: string[] = [];
  lines.push(
    `As of ${fmtDate(data.asof)} (${clockWords(data)}) · window ${fmtDate(data.window_start)} → ${fmtDate(data.asof)} (${data.window_days} days, by filing date)`,
  );
  lines.push(
    [
      `${plural(c.cards_counted, "card", "cards")} counted`,
      `${plural(c.names_read, "name", "names")} read`,
      data.include_silent
        ? "Context and Bench included"
        : c.silent_rows_left_out > 0
          ? `Context and Bench left out (${plural(c.silent_rows_left_out, "row", "rows")})`
          : "Context and Bench left out",
      c.unresolved_rows > 0 ? `${plural(c.unresolved_rows, "basket row", "basket rows")} with no resolved company` : null,
      c.not_yet_created > 0 ? `${plural(c.not_yet_created, "card", "cards")} created after this date left out` : null,
      c.roster_fallback > 0 ? `${plural(c.roster_fallback, "card", "cards")} on today's roster (no snapshot by then)` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  lines.push(
    [
      filtering
        ? `Showing ${shown.toLocaleString("en-US")} of ${plural(c.purchases, "purchase", "purchases")}`
        : `${plural(c.purchases, "purchase", "purchases")} listed`,
      `${plural(c.names_with_purchases, "name", "names")}`,
      `${plural(c.filings_read, "Form 4 filing", "Form 4 filings")} read`,
      `${plural(c.routine_folded, "filing", "filings")} with no purchase folded away`,
      c.amendments_no_purchase > 0
        ? `${plural(c.amendments_no_purchase, "4/A", "4/As")} with no purchase row`
        : null,
      c.filer_unresolved > 0 ? `${plural(c.filer_unresolved, "filing", "filings")} naming no filer` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  lines.push(
    [
      `${plural(c.cluster_rows, "row", "rows")} in a cluster (${plural(c.cluster_names, "name", "names")})`,
      `${plural(c.trigger_rows, "row is a recorded trigger", "rows are recorded triggers")}`,
      `${plural(c.nights_read, "recorded night", "recorded nights")} read`,
      c.nights_unreadable > 0 ? `${c.nights_unreadable} unreadable, not matched` : null,
      c.cap_blank > 0 ? `${plural(c.cap_blank, "row", "rows")} with a blank cap` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  lines.push(
    "Since = the name's as-traded return from the first close on or after the filing date to the last close on or before the as-of, with IWM on the same two dates. Cap ≈ the latest close times the latest shares fact, labeled approximate; its tier only filters.",
  );
  return lines;
}

/** The cache line: whether the read came from the shared dashboard cache and how long it took when computed. */
export const cacheWords = (data: InsiderTrackerOut) =>
  `${data.cache === "hit" ? "cache hit" : "computed"} · ${data.compute_ms.toLocaleString("en-US")} ms${data.cache === "hit" ? " when computed" : ""}`;
