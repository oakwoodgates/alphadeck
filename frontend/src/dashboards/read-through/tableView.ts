import { businessTypeLabel } from "../../util/format";
import type {
  ReadThroughCardOut,
  ReadThroughCatalystOut,
  ReadThroughFilingOut,
  ReadThroughOut,
  ReadThroughRowOut,
  ReadThroughSpyOut,
  ReadThroughTapeOut,
  RoleKey,
} from "./useReadThrough";

// The Read-Throughs' pure view helpers (docs/dashboards/read-through.md): the tabs, the sorts, the filters, the
// cells' words and the "show the work" lines. Every row, count and number is computed server-side; this only
// filters, orders and words them. Nothing here hides a row from the counts: the work lines count every row.

// --- the roles and tabs ------------------------------------------------------------------------------------------

/** The roles in the Chain Wave's order (payers first): the server's `ROLE_ORDER`. */
export const ROLE_ORDER: readonly RoleKey[] = [
  "payer",
  "counter_case",
  "direct_read",
  "participant",
  "financier",
  "benchmark",
];

/** A role's tab label, as a group. Exhaustive against the generated enum, so a role added server-side fails the
 *  build here instead of rendering a blank tab. */
export const ROLE_TAB_LABEL: Record<RoleKey, string> = {
  payer: "Payers",
  counter_case: "Counter-cases",
  direct_read: "Direct reads",
  participant: "Participants",
  financier: "Financiers",
  benchmark: "Benchmarks",
};

export type TabKey = "all" | RoleKey;

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- dates and numbers -------------------------------------------------------------------------------------------

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A date without its year ("Oct 28"), for a column that already sits under the as-of. */
export function fmtShortDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** A signed number to one decimal ("+4.8", "-7.1", "0.0"); a blank is "—". */
export function fmtSigned(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
}

export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)}%`);
export const fmtPp = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)} pp`);

/** The sign of a vs-SPY number for its quiet tint: up, down, or none (a flat 0.0 or a blank). */
export function tone(v: number | null | undefined): "pos" | "neg" | null {
  if (v == null || v === 0) return null;
  return v > 0 ? "pos" : "neg";
}

// --- the cells ---------------------------------------------------------------------------------------------------

/** The links the company sits in on a card ("Turbines, Cables"; "No link" for none); empty with no link row. */
export function chipLinks(c: ReadThroughCardOut): string {
  return c.links.map((l) => l ?? "No link").join(", ");
}

/** A card chip's full words: the card, then the links the company sits in there ("Grid · Turbines, Cables"). The
 *  chip shows the name and the links as two separately truncated parts; this is the whole line, for its hover. */
export function chipWords(c: ReadThroughCardOut): string {
  const links = chipLinks(c);
  return links ? `${c.thesis_name} · ${links}` : c.thesis_name;
}

/** The last-filing cell: the date, the form and the headline on one line, and the full line for the hover. An 8-K's
 *  headline already opens with its form ("8-K · 2.02 …"), so the form is not said twice. */
export function filingWords(f: ReadThroughFilingOut): { short: string; full: string } {
  const ago = f.days_ago === 0 ? "on the as-of date" : `${plural(f.days_ago, "day", "days")} before the as-of`;
  const what = f.headline.startsWith(`${f.form} ·`) ? f.headline : `${f.form} · ${f.headline}`;
  return {
    short: `${fmtShortDay(f.date)} · ${what}`,
    full: `${fmtDay(f.date)} (${ago}) · ${what}`,
  };
}

/** "in 15 days", "in 1 day", "on the as-of date". */
export function daysWords(days: number): string {
  if (days === 0) return "on the as-of date";
  return `in ${plural(days, "day", "days")}`;
}

/** The next-catalyst cell: the date and days to go, the catalyst, its card; and the full line for the hover. */
export function catalystWords(c: ReadThroughCatalystOut): { short: string; full: string } {
  const when = c.when_label ? `${fmtDay(c.when_date)} (${c.when_label})` : fmtDay(c.when_date);
  return {
    short: `${fmtShortDay(c.when_date)} · ${daysWords(c.days)} · ${c.label} · ${c.thesis_name}`,
    full: `${when} · ${daysWords(c.days)} · ${c.label}${c.kind ? ` (${c.kind})` : ""} · on ${c.thesis_name}`,
  };
}

export type TapeWindow = "7d" | "30d";

/** A tape cell: its text, its quiet tint and its hover. A blank cell says why in the hover; a flagged price
 *  history grays it and names the reason (the row stays). */
export type TapeCell = { text: string; tone: "pos" | "neg" | null; gray: boolean; title: string };

const WINDOW_DAYS: Record<TapeWindow, number> = { "7d": 7, "30d": 30 };

export function tapeCell(tape: ReadThroughTapeOut, which: TapeWindow, spy: ReadThroughSpyOut | null): TapeCell {
  const days = WINDOW_DAYS[which];
  const own = which === "7d" ? tape.ret_7d : tape.ret_30d;
  const diff = which === "7d" ? tape.vs_spy_7d : tape.vs_spy_30d;
  const bench = spy ? (which === "7d" ? spy.ret_7d : spy.ret_30d) : null;
  if (tape.gap === "flagged") {
    return {
      text: "flagged",
      tone: null,
      gray: true,
      title: `Left out: ${tape.reason ?? "a flagged price history"}. The Cockpit grays this company's price cells for the same reason.`,
    };
  }
  if (tape.gap === "no_tape") {
    return { text: "—", tone: null, gray: false, title: "No price history is stored for this company at the as-of." };
  }
  if (tape.gap === "no_spy") {
    return {
      text: "—",
      tone: null,
      gray: false,
      title: `SPY has no price history at the as-of, so there is nothing to subtract. The company's own ${days}-trading-day return is ${fmtPct(own)}.`,
    };
  }
  if (tape.gap === "ends_differ") {
    return {
      text: "—",
      tone: null,
      gray: false,
      title: `The company's last close (${fmtDay(tape.last_close)}) is not SPY's (${fmtDay(spy?.end)}), so the two returns would end on different days and are not compared. Its own ${days}-trading-day return is ${fmtPct(own)}.`,
    };
  }
  if (diff == null) {
    return {
      text: "—",
      tone: null,
      gray: false,
      title: `Fewer than ${days + 1} closes are stored for ${own == null ? "this company" : "SPY"}, so there is no ${days}-trading-day return to compare yet.`,
    };
  }
  return {
    text: fmtSigned(diff),
    tone: tone(diff),
    gray: false,
    title: `${days} trading days to ${fmtDay(tape.last_close)}: the company ${fmtPct(own)} minus SPY ${fmtPct(bench)} = ${fmtPp(diff)}`,
  };
}

// --- the filters (client side) -----------------------------------------------------------------------------------

export type Filters = {
  /** a card-name query (a substring of any chip's card); "" = every card */
  card: string;
  /** a business-type key; "" = every type, `NO_TYPE` = the rows with none */
  businessType: string;
  /** keep the rows whose last filing is within this many days of the as-of; 0 = any row, filed or not */
  withinDays: number;
};

export const NO_TYPE = "__none__";

export const DEFAULT_FILTERS: Filters = { card: "", businessType: "", withinDays: 0 };

export const FILING_WINDOWS: readonly { value: number; label: string }[] = [
  { value: 0, label: "Any time" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "365 days" },
];

export function filtersActive(f: Filters): boolean {
  return f.card.trim() !== "" || f.businessType !== "" || f.withinDays !== 0;
}

export function rowMatches(r: ReadThroughRowOut, f: Filters): boolean {
  const q = f.card.trim().toLocaleLowerCase();
  if (q && !r.cards.some((c) => c.thesis_name.toLocaleLowerCase().includes(q))) return false;
  if (f.businessType) {
    const key = r.business_type ?? NO_TYPE;
    if (key !== f.businessType) return false;
  }
  if (f.withinDays > 0 && (r.last_filing == null || r.last_filing.days_ago > f.withinDays)) return false;
  return true;
}

export const filterRows = (rows: readonly ReadThroughRowOut[], f: Filters): ReadThroughRowOut[] =>
  rows.filter((r) => rowMatches(r, f));

/** The business types the rows hold (and "Unclassified" when some have none), A to Z, each with its row count. */
export function businessTypeOptions(rows: readonly ReadThroughRowOut[]): { value: string; label: string; rows: number }[] {
  const tally = new Map<string, number>();
  for (const r of rows) {
    const key = r.business_type ?? NO_TYPE;
    tally.set(key, (tally.get(key) ?? 0) + 1);
  }
  const out = [...tally].map(([value, n]) => ({
    value,
    label: value === NO_TYPE ? "Unclassified" : businessTypeLabel(value),
    rows: n,
  }));
  // "Unclassified" last, the rest A to Z
  out.sort((a, b) => (a.value === NO_TYPE ? 1 : b.value === NO_TYPE ? -1 : a.label.localeCompare(b.label)));
  return out;
}

/** The card names the rows sit on, A to Z, each once (the type-ahead's suggestions). */
export function cardNames(rows: readonly ReadThroughRowOut[]): string[] {
  const names = new Set<string>();
  for (const r of rows) for (const c of r.cards) names.add(c.thesis_name);
  return [...names].sort((a, b) => a.localeCompare(b));
}

/** The rows per tab after the filters: "All roles" and one per role (a role with none is 0, never missing). */
export function tabCounts(rows: readonly ReadThroughRowOut[]): Record<TabKey, number> {
  const out = { all: rows.length } as Record<TabKey, number>;
  for (const role of ROLE_ORDER) out[role] = 0;
  for (const r of rows) out[r.role] += 1;
  return out;
}

export const inTab = (rows: readonly ReadThroughRowOut[], tab: TabKey): ReadThroughRowOut[] =>
  tab === "all" ? [...rows] : rows.filter((r) => r.role === tab);

// --- the sorts (client side) -------------------------------------------------------------------------------------

export type SortKey =
  | "company"
  | "role"
  | "cards"
  | "business_type"
  | "last_filing"
  | "next_catalyst"
  | "vs_spy_7d"
  | "vs_spy_30d";

export type Sort = { key: SortKey; dir: "asc" | "desc" };

/** The page's default: newest last filing first (rows with no filing last, A to Z by ticker). */
export const DEFAULT_SORT: Sort = { key: "last_filing", dir: "desc" };

/** The direction a column starts in when first clicked: text A to Z, the filing newest first, the catalyst
 *  nearest first, the tape largest first. */
const FIRST_DIR: Record<SortKey, "asc" | "desc"> = {
  company: "asc",
  role: "asc",
  cards: "asc",
  business_type: "asc",
  last_filing: "desc",
  next_catalyst: "asc",
  vs_spy_7d: "desc",
  vs_spy_30d: "desc",
};

/** A click on a column: the same column flips its direction, another starts in its own first direction. */
export function nextSort(cur: Sort, key: SortKey): Sort {
  if (cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: FIRST_DIR[key] };
}

const ROLE_RANK = new Map<string, number>(ROLE_ORDER.map((r, i) => [r, i]));

/** A row's value in a column: a string or number to compare, `null` = none (always last, whatever the direction:
 *  a missing value never leads). */
function sortValue(r: ReadThroughRowOut, key: SortKey): string | number | null {
  switch (key) {
    case "company":
      return r.ticker.toLocaleLowerCase();
    case "role":
      return ROLE_RANK.get(r.role) ?? ROLE_ORDER.length;
    case "cards":
      return r.cards[0]?.thesis_name.toLocaleLowerCase() ?? null;
    case "business_type":
      return r.business_type ? businessTypeLabel(r.business_type).toLocaleLowerCase() : null;
    case "last_filing":
      return r.last_filing?.date ?? null;
    case "next_catalyst":
      return r.next_catalyst?.when_date ?? null;
    case "vs_spy_7d":
      return r.tape.vs_spy_7d;
    case "vs_spy_30d":
      return r.tape.vs_spy_30d;
  }
}

const cmp = (a: string | number, b: string | number) => (a < b ? -1 : a > b ? 1 : 0);

/** The rows sorted. A blank value is last in either direction; ties break by ticker A to Z, then the role order,
 *  then the key (a stable, repeatable order). */
export function sortRows(rows: readonly ReadThroughRowOut[], sort: Sort): ReadThroughRowOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    if (va == null && vb != null) return 1;
    if (vb == null && va != null) return -1;
    if (va != null && vb != null) {
      const c = cmp(va, vb);
      if (c !== 0) return c * sign;
    }
    return (
      cmp(a.ticker.toLocaleLowerCase(), b.ticker.toLocaleLowerCase()) ||
      cmp(ROLE_RANK.get(a.role) ?? ROLE_ORDER.length, ROLE_RANK.get(b.role) ?? ROLE_ORDER.length) ||
      cmp(a.key, b.key)
    );
  });
}

// --- the show-the-work lines -------------------------------------------------------------------------------------

function cacheWords(d: ReadThroughOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet lines under the table. `shown` = the rows in view after the tab and the filters. */
export function workLines(d: ReadThroughOut, shown: number): string[] {
  const rolesBy = d.by_role.map((c) => `${ROLE_TAB_LABEL[c.role]} ${c.rows.toLocaleString("en-US")}`).join(" · ");
  const lines = [
    [
      `as of ${fmtDay(d.asof)}`,
      d.clock === "record" ? "live read" : `as it was public by ${fmtDay(d.asof)}`,
      cacheWords(d),
    ].join(" · "),
    [
      `${plural(d.cards_counted, "card", "cards")} counted`,
      d.cards_not_yet_created > 0 ? `${d.cards_not_yet_created.toLocaleString("en-US")} not yet created` : null,
      `${d.cards_with_roles.toLocaleString("en-US")} with at least one role`,
      `${d.cards_without_roles.length.toLocaleString("en-US")} with none`,
    ]
      .filter(Boolean)
      .join(" · "),
    [
      `${plural(d.role_tags, "role", "roles")} set`,
      plural(d.companies, "company", "companies"),
      `${plural(d.rows.length, "row", "rows")} (one per company and role)`,
      shown !== d.rows.length ? `${shown.toLocaleString("en-US")} in view` : null,
    ]
      .filter(Boolean)
      .join(" · "),
    `rows by role: ${rolesBy}`,
    `${d.rows_with_filing.toLocaleString("en-US")} of ${d.rows.length.toLocaleString("en-US")} rows have a filing in the last ${d.filing_days} days`,
  ];
  if (d.spy) {
    lines.push(
      `tape: ${d.tape_windows.join(" and ")} trading days of closes, the company minus ${d.benchmark} (${d.benchmark} ${fmtPct(d.spy.ret_7d)} and ${fmtPct(d.spy.ret_30d)}, last close ${fmtDay(d.spy.end)})`,
    );
  } else {
    lines.push(`tape: ${d.benchmark} has no price history at this as-of, so no row can be compared`);
  }
  if (d.tape_ends_differ > 0) {
    lines.push(
      `${plural(d.tape_ends_differ, "row has", "rows have")} a last close that is not ${d.benchmark}'s, so its tape is not compared`,
    );
  }
  return lines;
}

/** Role rows with no resolved security can carry no filing or tape: listed, never silently dropped (invariant #9). */
export function unresolvedWords(d: Pick<ReadThroughOut, "unresolved" | "unresolved_rows">): string | null {
  if (d.unresolved_rows === 0) return null;
  const rows = plural(d.unresolved_rows, "role row has", "role rows have");
  return `${rows} no resolved security, so no filing or tape can be attached and it is not a row: ${d.unresolved.join(", ")}.`;
}

/** Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in. */
export const ROSTER_PIT_FROM = "2026-09-15";

export function rosterWords(n: number): string | null {
  if (n === 0) return null;
  return `${plural(n, "counted card has", "counted cards have")} no roster snapshot by this as-of, so today's roster stands in for ${n === 1 ? "it" : "them"}: rosters are point-in-time only from ${fmtDay(ROSTER_PIT_FROM)}.`;
}

/** The page's known limits, stated on the page (docs/dashboards/read-through.md). */
export const LIMITS: readonly string[] = [
  "A role names a part, not a target: a row points at the card, never at specific names. Reading a role company's own disclosures against the names it relates to is a later step.",
  "Catalysts are today's list: card-level catalysts load live (they are not snapshotted), so a past as-of shows the catalysts as entered today.",
  "Last filing means ingested filings only: forms other than 8-K, Form 4 and 4/A, 13D/G and the 10-Q/10-K revenue series are not stored yet, and the Cockpit's insider price screens are not applied. A split, a catalyst or a ratified or stored fact is never counted as a filing.",
  "The roster is each card's roster as of the as-of; links and business types are today's.",
  "The tape counts trading days of closes (7 and 30), not calendar days. A flagged price history grays the tape cells and names the reason; the row stays.",
];
