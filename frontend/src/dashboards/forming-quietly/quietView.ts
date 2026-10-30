import type {
  FormingAdoptionOut,
  FormingKeyOut,
  FormingQuietlyOut,
  FormingRowOut,
  FormingTriggerOut,
  RowState,
} from "./useFormingQuietly";

// Forming Quietly's pure view helpers (docs/dashboards/forming-quietly.md): which rows the Incubating toggle and the
// filters show, the sorts, and the words of every cell. Every figure (the recorded keys and missing lines, the
// quiet signals, the adoption slope, the run's first night) is computed server-side; this page picks the rows, sorts
// them and words them. A blank is never zero: it reads "—" and sorts last in either direction. Nothing here ranks a
// card by promise (invariant #4) or names a timing: the words are neutral and gray (invariant #7). Kept in this
// folder rather than shared with the other dashboards', so each page deletes cleanly (docs/DASHBOARDS.md rule 8).

// --- the page's own sentences ------------------------------------------------------------------------------------

/** The page's one sentence of framing, above the work line. */
export const FRAMING =
  "Nothing on this page is Armed. It lists what the record shows forming and what each card still needs.";

/** What the page does not claim (stated on the page too). */
export const LIMITS: readonly string[] = [
  "Quiet signals are display readings of the tape and the Form 4 filings (insider buying, coiling, leading, breadth). They never feed a call: a card can be Incubating with every one of them lit and that means nothing to the call.",
  "Adoption counts the EDGAR documents that match the card's SIGNAL terms (a document that matches two terms counts twice), on a weekly-refreshed table, closed months only: the last three against the three before.",
  "An Incubating card shows here only when a quiet signal is lit (insider buying, coiling, leading or a breadth thrust). The breadth count and the adoption slope appear wherever they exist and never light a card alone.",
  "Context and Bench names never count here. A name with a flagged price history is counted and left out of every reading.",
];

// --- small formatting --------------------------------------------------------------------------------------------

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

const BLANK = "—";

/** "Oct 27" for a YYYY-MM-DD date; a blank is "—". */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return BLANK;
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** "Jul 2026" for a month's first day. */
export function fmtMonth(d: string | null | undefined): string {
  if (!d) return BLANK;
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** "Jul to Sep 2026" for a span of month first days (the year once when both months share it). */
export function fmtSpan(from: string | null, to: string | null): string {
  if (!from || !to) return BLANK;
  const sameYear = from.slice(0, 4) === to.slice(0, 4);
  const first = sameYear ? fmtMonth(from).replace(/ \d{4}$/, "") : fmtMonth(from);
  return from === to ? fmtMonth(from) : `${first} to ${fmtMonth(to)}`;
}

/** The sign tone of the adoption percent: the only figure on the page that takes one. */
export const signTone = (v: number | null | undefined): "pos" | "neg" | "" => (v == null || Math.round(v) === 0 ? "" : v > 0 ? "pos" : "neg");

const KIND_WORD: Record<string, string> = {
  insider: "insider",
  technical_breakout: "breakout",
  laggard: "laggard",
  catalyst: "catalyst",
  theme_conviction: "theme",
  activist_stake: "activist",
  squeeze: "squeeze",
  etf_launch: "ETF launch",
  etf_flow: "ETF flow",
  dilution_risk: "dilution",
};
export const kindWord = (kind: string) => KIND_WORD[kind] ?? kind.replace(/_/g, " ");

// --- the cells ---------------------------------------------------------------------------------------------------

export type Cell = {
  text: string;
  /** A second, smaller gray line under the text. */
  sub?: string;
  title?: string;
  /** The sign tone: only the adoption percent takes one. */
  tone?: "pos" | "neg" | "";
  /** A blank or a reason in place of a figure: drawn quieter. */
  gray?: boolean;
};

export const stateWord = (s: RowState) => (s === "warming" ? "Warming" : "Incubating");

/** "since Oct 27" for the current run's first night. */
export const sinceWords = (r: FormingRowOut) => (r.run_start === null ? "no recorded night" : `since ${fmtDay(r.run_start)}`);

/** "3 days" in the current state; "0 days" on the run's first night. */
export const daysWords = (r: FormingRowOut) => (r.days_in_state === null ? "" : plural(r.days_in_state, "day", "days"));

export const stateTitle = (r: FormingRowOut) =>
  `The first night of the card's current ${r.state} run on the record${r.run_nights > 0 ? ` (${plural(r.run_nights, "recorded night", "recorded nights")})` : ""}. The recorded call in effect at this as-of is the ${r.recorded_on} night.`;

export const RECONSTRUCTED_TITLE =
  "That night's call was rebuilt by the backfill from the facts public by then: reported, never scored.";

/** A key as the call recorded it: the assembler's own label and detail. */
export const keyLine = (k: FormingKeyOut) => (k.detail ? `${k.label} · ${k.detail}` : k.label);

/** The keys that are turned, in the recorded order (conviction, then confirmation). */
export const turnedKeys = (r: FormingRowOut): FormingKeyOut[] => [r.key_conviction, r.key_confirmation].filter((k) => k.turned);

/** One trigger's hover line: its label, kind, grade and fire date (the market event, not the recorded night). */
export function triggerLine(t: FormingTriggerOut): string {
  return `${t.label} · ${kindWord(t.kind)} · ${t.grade ?? "no grade"} · fired ${t.event_date ?? "no date stated"}`;
}

/** The recorded card's risk signals: the count with each one's label in the hover; never hidden. */
export function riskCell(r: FormingRowOut): Cell {
  const n = r.risks.length;
  if (n === 0) return { text: BLANK, title: "No risk signal on the recorded call", gray: true };
  return {
    text: String(n),
    title: `${plural(n, "risk signal", "risk signals")} on the recorded call: ${r.risks.map((x) => `${x.ticker} ${x.label} (${kindWord(x.kind)})`).join("; ")}`,
  };
}

const CLUSTER_TITLE = "a cluster: at least two distinct buyers";

/** Insider buying: how many of the names read had an open-market buy in the last 30 days; blank when none did. */
export function insiderCell(r: FormingRowOut): Cell {
  const q = r.quiet;
  const gaps = q.no_form4 > 0 ? ` ${plural(q.no_form4, "name has", "names have")} no Form 4 ingested.` : "";
  if (q.insider.length === 0) {
    return { text: BLANK, title: `No open-market buy in the last 30 days among ${plural(q.read, "name", "names")} read.${gaps}`, gray: true };
  }
  return {
    text: `${q.insider.length} of ${q.read}`,
    title: `${plural(q.insider.length, "name", "names")} of ${plural(q.read, "name", "names")} read had an open-market insider buy in the last 30 days.${gaps}`,
  };
}

/** One insider name's hover: its buys and distinct buyers, and the cluster word when it is one. */
export const insiderNameTitle = (n: { ticker: string; buys_30d: number; buyers_30d: number; cluster: boolean }) =>
  `${n.ticker}: ${plural(n.buys_30d, "open-market buy", "open-market buys")} by ${plural(n.buyers_30d, "distinct buyer", "distinct buyers")} in the last 30 days${n.cluster ? ` (${CLUSTER_TITLE})` : ""}`;

/** Coiling: how many of the names read have a base the volatility-contraction member calls coiling. */
export function coilingCell(r: FormingRowOut): Cell {
  const q = r.quiet;
  const gaps = q.coiling_no_reading > 0 ? ` ${plural(q.coiling_no_reading, "name has", "names have")} too little tape to read a base.` : "";
  if (q.coiling.length === 0) return { text: BLANK, title: `No base coiling among ${plural(q.read, "name", "names")} read.${gaps}`, gray: true };
  return {
    text: `${q.coiling.length} of ${q.read}`,
    title: `${plural(q.coiling.length, "name", "names")} of ${plural(q.read, "name", "names")} read ${q.coiling.length === 1 ? "has" : "have"} a base the volatility-contraction member calls coiling.${gaps}`,
  };
}

export const coilingNameTitle = (n: { ticker: string; contraction: number | null }) =>
  `${n.ticker}: ${n.contraction == null ? "base coiling" : `the newest segment's range is ${Math.round(n.contraction * 100)}% of the oldest's`}`;

/** Leading: how many of the names read have relative strength at a 13-week high. */
export function leadingCell(r: FormingRowOut): Cell {
  const q = r.quiet;
  const gaps = q.leading_no_reading > 0 ? ` ${plural(q.leading_no_reading, "name has", "names have")} no 13-week verdict yet.` : "";
  if (q.leading.length === 0) return { text: BLANK, title: `No relative strength at a 13-week high among ${plural(q.read, "name", "names")} read.${gaps}`, gray: true };
  return {
    text: `${q.leading.length} of ${q.read}`,
    title: `${plural(q.leading.length, "name", "names")} of ${plural(q.read, "name", "names")} read ${q.leading.length === 1 ? "has" : "have"} relative strength at a 13-week high.${gaps}`,
  };
}

export const leadingNameTitle = (n: { ticker: string; vs: string[] }) =>
  `${n.ticker}: relative strength at a 13-week high against ${n.vs.join(" and ") || "the benchmarks"}`;

/** Breadth: how many names are at or above their 50-day average ("12 of 30 above the 50-day"). */
export function breadthCell(r: FormingRowOut): Cell {
  const b = r.quiet.breadth;
  if (b === null) return { text: BLANK, title: "No name on the roster could be read", gray: true };
  if (b.counted === 0) {
    return { text: BLANK, title: `No name has the history for a 50-day breadth yet (${plural(b.thin, "name", "names")} thin).`, gray: true };
  }
  const delta = b.delta_pts == null ? "" : ` ${b.delta_pts > 0 ? "+" : ""}${b.delta_pts.toFixed(0)} points against 20 trading days ago.`;
  const thin = b.thin > 0 ? ` ${plural(b.thin, "name", "names")} with too little history ${b.thin === 1 ? "is" : "are"} not counted.` : "";
  return {
    text: `${b.above ?? 0} of ${b.counted}`,
    sub: b.thrust ? "above the 50-day, thrust" : "above the 50-day",
    title: `${b.above ?? 0} of ${plural(b.counted, "name", "names")} read close at or above their 50-day average.${delta}${thin}`,
  };
}

/** The adoption slope as a signed whole percent with the two document counts under it; "not counted yet" when the
 *  six closed months are not stored. */
export function adoptionCell(a: FormingAdoptionOut): Cell {
  if (!a.counted) {
    const have = a.months_stored > 0 ? ` ${a.months_stored} of the last 6 closed months are stored.` : "";
    return {
      text: "not counted yet",
      title: `The last six closed months of EDGAR term mentions are not all stored for any of the card's ${plural(a.terms_signal, "SIGNAL term", "SIGNAL terms")}.${have}`,
      gray: true,
    };
  }
  const spans = `${fmtSpan(a.recent_from, a.recent_to)}: ${a.recent} documents, against ${fmtSpan(a.prior_from, a.prior_to)}: ${a.prior}`;
  const left =
    a.terms_partial + a.terms_unstored > 0
      ? ` ${plural(a.terms_counted, "term", "terms")} of ${a.terms_signal} counted (a term needs all six closed months stored); ${a.terms_partial} partly stored and ${a.terms_unstored} not stored are left out.`
      : ` All ${plural(a.terms_signal, "SIGNAL term", "SIGNAL terms")} counted.`;
  const partial = a.latest_partial ? " The latest closed month was read before it ended, so its count may be low." : "";
  const sub = `${a.recent} vs ${a.prior} docs`;
  if (a.percent === null) {
    return { text: BLANK, sub, title: `No percent: the earlier three months hold no documents. ${spans}.${left}${partial}`, gray: true };
  }
  const rounded = Math.round(a.percent);
  const text = rounded === 0 ? "0%" : `${rounded > 0 ? "+" : ""}${rounded}%`;
  return { text, sub, tone: signTone(a.percent), title: `Documents that match the card's SIGNAL terms, summed. ${spans}.${left}${partial}` };
}

// --- which rows --------------------------------------------------------------------------------------------------

/** The Incubating cards the default view hides: no quiet signal lit. */
export const hiddenIncubating = (rows: readonly FormingRowOut[]) => rows.filter((r) => r.state === "incubating" && !r.quiet.lit).length;

/** The rows on show: every Warming card, and an Incubating card when a quiet signal is lit or the operator asked for
 *  every Incubating card. The server's order is kept. */
export const rowsOnShow = (rows: readonly FormingRowOut[], showEveryIncubating: boolean): FormingRowOut[] =>
  rows.filter((r) => r.state === "warming" || showEveryIncubating || r.quiet.lit);

export type Filters = { states: RowState[]; insider: boolean; coiling: boolean; card: string | null };
export const DEFAULT_FILTERS: Filters = { states: [], insider: false, coiling: false, card: null };
export const filtersActive = (f: Filters) => f.states.length > 0 || f.insider || f.coiling || f.card !== null;

export function rowMatches(r: FormingRowOut, f: Filters): boolean {
  if (f.states.length > 0 && !f.states.includes(r.state)) return false;
  if (f.insider && r.quiet.insider.length === 0) return false;
  if (f.coiling && r.quiet.coiling.length === 0) return false;
  if (f.card !== null && r.thesis_id !== f.card) return false;
  return true;
}

export type FilterControls = { states: RowState[]; insider: boolean; coiling: boolean; cards: { id: string; name: string }[] };

/** Only the controls that can discriminate among the rows on show (a control true of every row says nothing). */
export function filterControls(rows: readonly FormingRowOut[]): FilterControls {
  const states = (["warming", "incubating"] as const).filter((s) => rows.some((r) => r.state === s));
  const cards = rows
    .map((r) => ({ id: r.thesis_id, name: r.thesis_name }))
    .sort((a, b) => a.name.localeCompare(b.name, "en", { sensitivity: "base" }));
  return {
    states: states.length > 1 ? [...states] : [],
    insider: rows.some((r) => r.quiet.insider.length > 0),
    coiling: rows.some((r) => r.quiet.coiling.length > 0),
    cards: cards.length > 1 ? cards : [],
  };
}

// --- the sorts ---------------------------------------------------------------------------------------------------

/** `order` = the server's default order (the key most recently turned first); the rest are the alternatives. */
export type SortKey = "order" | "card" | "days" | "insider" | "adoption";
export type Sort = { key: SortKey; dir: "asc" | "desc" };
export const DEFAULT_SORT: Sort = { key: "order", dir: "asc" };

/** A column's first direction: the card A to Z, a figure biggest first. */
export const firstDir = (key: SortKey): Sort["dir"] => (key === "card" || key === "order" ? "asc" : "desc");

/** A click on a column: the same column flips its direction, another starts in its own first direction. */
export function nextSort(cur: Sort, key: SortKey): Sort {
  if (cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: firstDir(key) };
}

/** A row's value in a column; `null` = blank (always last, whatever the direction: a blank is never read as zero).
 *  Insider buying sorts on the distinct buyers summed over the names that had a buy. */
export function sortValue(r: FormingRowOut, key: SortKey): string | number | null {
  switch (key) {
    case "card":
      return r.thesis_name.toLocaleLowerCase();
    case "days":
      return r.days_in_state;
    case "insider":
      return r.quiet.insider.length === 0 ? null : r.quiet.insider.reduce((s, n) => s + n.buyers_30d, 0);
    case "adoption":
      return r.adoption.percent;
    default:
      return null;
  }
}

/** The rows in the chosen order. `order` keeps the server's; a tie keeps it too (a stable sort). */
export function sortRows(rows: readonly FormingRowOut[], sort: Sort): FormingRowOut[] {
  if (sort.key === "order") return [...rows];
  const keyed = rows.map((r, i) => ({ r, i, v: sortValue(r, sort.key) }));
  keyed.sort((a, b) => {
    if (a.v === null && b.v === null) return a.i - b.i;
    if (a.v === null) return 1;
    if (b.v === null) return -1;
    const c = typeof a.v === "string" ? a.v.localeCompare(b.v as string) : a.v - (b.v as number);
    return (sort.dir === "asc" ? c : -c) || a.i - b.i;
  });
  return keyed.map((k) => k.r);
}

// --- the quiet "show the work" lines -----------------------------------------------------------------------------

/** The work lines under the table: the as-of and the clock, the counts, the names read, the term months, the members
 *  run and the cache. `hidden` = the Incubating cards the view does not show right now. */
export function workLines(data: FormingQuietlyOut, hidden: number, showEveryIncubating: boolean): string[] {
  const without = data.cards_incubating - data.cards_incubating_lit;
  const clock =
    data.clock === "public"
      ? "the public clock: each fact counts from when it became public, up to the end of that day"
      : "the record clock: the live read";
  const left = [
    data.cards_not_yet_created > 0 ? `${plural(data.cards_not_yet_created, "card", "cards")} created after the as-of left out` : "",
    data.roster_fallback_cards > 0 ? `${plural(data.roster_fallback_cards, "card", "cards")} on today's roster (no snapshot by then)` : "",
  ]
    .filter(Boolean)
    .map((s) => ` · ${s}`)
    .join("");
  return [
    `as of ${data.asof} · ${clock}`,
    `${plural(data.cards_counted, "card", "cards")} counted of ${data.live_cards} live${left} · ${data.cards_warming} Warming · ${data.cards_incubating_lit} Incubating with a quiet signal · ${without} Incubating without (${showEveryIncubating || hidden === 0 ? "shown" : "hidden"}) · ${data.cards_armed} Armed · ${data.cards_managing} Managing · ${data.cards_no_call} no call yet`,
    `${plural(data.names_read, "name", "names")} read · ${data.names_flagged} left out for a flagged price history · adoption counted for ${plural(data.cards_with_term_months, "card", "cards")}, no term months stored for ${data.cards_without_term_months}`,
    `members run: ${data.members.join(", ")}, plus one theme-breadth read per card · ${data.cache === "hit" ? "from the cache" : `computed in ${data.compute_ms} ms`}`,
  ];
}
