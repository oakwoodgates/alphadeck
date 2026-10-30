import { verdictLabel } from "../../util/format";
import type { Heat } from "./heat";
import type { CapTier, HotBoardOut, HotRowOut, HotTriggerOut, RowKind } from "./useHotBoard";

// The Hot Board's pure view helpers (docs/dashboards/hot-board.md): which rows the toggles and filters show, the
// sorts, and the words and numbers of every cell. Every figure (the call's fields, the clocks, the return since the
// arm night, the tape, the cap) is computed server-side; the heat is recomputed from the rows shown (heat.ts). A
// blank is never zero: it reads "—" and sorts last in either direction. Kept in this folder rather than shared with
// the other dashboards', so each page deletes cleanly (docs/DASHBOARDS.md rule 8).

// --- small formatting --------------------------------------------------------------------------------------------

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A signed number to one decimal ("+4.8", "-7.1", "0.0"); a blank is "—". */
export function fmtSigned(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)}%`);
export const fmtPp = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)} pp`);
export const fmtRatio = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}×`);

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

/** "Oct 27" for a YYYY-MM-DD date; a blank is "—". */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** The sign tone of a signed return: only a signed return takes one (the sign tone, in the page's own CSS). */
export const signTone = (v: number | null | undefined): "pos" | "neg" | "" => (v == null || v === 0 ? "" : v > 0 ? "pos" : "neg");

// --- the cells ---------------------------------------------------------------------------------------------------

export type Cell = {
  text: string;
  title?: string;
  tone?: "pos" | "neg" | "";
  /** The page's ONE accent: an entry window with at most two sessions left. */
  accent?: boolean;
  /** A reason in place of a figure: drawn quiet. */
  gray?: boolean;
};

const BLANK = "—";

/** A tape number: the figure, or "—" with the reason in its hover (the member's note, the flag's label). */
function tapeCell(
  cell: { value: number | null; note: string | null },
  fmt: (v: number) => string,
  title: (v: number) => string,
  flag: string | null,
  signed = false,
): Cell {
  if (flag !== null) return { text: "flagged", title: `flagged: ${flag}`, gray: true };
  if (cell.value === null) return { text: BLANK, title: cell.note ?? "no reading", gray: true };
  return { text: fmt(cell.value), title: title(cell.value), tone: signed ? signTone(cell.value) : "" };
}

export const tapeCells = (r: HotRowOut) => {
  const f = r.tape.flag;
  return {
    rvol: tapeCell(r.tape.rvol20, fmtRatio, (v) => `The as-of bar's volume is ${v.toFixed(2)}× the mean of the 20 bars before it`, f),
    vs50: tapeCell(r.tape.vs_50d, fmtPct, (v) => `${fmtPct(v)} against the 50-day average`, f, true),
    offHigh: tapeCell(r.tape.off_high, fmtPct, (v) => `${fmtPct(v)} against the 52-week closing high`, f, true),
    vsIwm: tapeCell(
      r.tape.vs_iwm,
      fmtPp,
      (v) => `The 30-trading-day return minus IWM's: ${fmtPp(v)}`,
      f,
      true,
    ),
    ret7: tapeCell(r.tape.ret_7d, fmtPct, (v) => `${fmtPct(v)} over the last 7 trading days`, f, true),
    ret30: tapeCell(r.tape.ret_30d, fmtPct, (v) => `${fmtPct(v)} over the last 30 trading days`, f, true),
    adv: tapeCell(r.tape.adv_usd_20d, fmtUsd, (v) => `Average dollar volume over the last 20 bars: ${fmtUsd(v)}`, f),
  };
};

/** Insider buys over the last 30 days as "buys/buyers" ("2/2"); a blank when no Form 4 is ingested, a stated
 *  "0/0" when some is and none was a buy. */
export function insiderCell(r: HotRowOut): Cell {
  const { buys_30d: buys, buyers_30d: buyers } = r.tape;
  if (buys === null || buyers === null) return { text: BLANK, title: "No Form 4 ingested for this name", gray: true };
  return {
    text: `${buys}/${buyers}`,
    title: `${plural(buys, "open-market buy", "open-market buys")} by ${plural(buyers, "distinct buyer", "distinct buyers")} in the last 30 days`,
  };
}

/** The relative-strength mark: "leading" when the RS prints a fresh 13-week high, nothing otherwise (the mark
 *  marks the exception, not the rule). */
export function leadingMark(r: HotRowOut): string | null {
  if (r.tape.rs_leading !== true) return null;
  return `Relative strength is at a 13-week high against ${r.tape.rs_leading_vs.join(" and ") || "the benchmarks"}`;
}

const TIER_LABEL: Record<CapTier, string> = { nano: "nano", micro: "micro", small: "small", mid: "mid", large: "large" };
export const TIER_ORDER: readonly CapTier[] = ["nano", "micro", "small", "mid", "large"];
export type TierPick = CapTier | "none";
export const tierLabel = (t: TierPick) => (t === "none" ? "no cap" : TIER_LABEL[t]);

/** The approximate cap with its tier ("$1.20B small"), labeled approximate in its hover; blank with the reason. */
export function capCell(r: HotRowOut): Cell {
  const c = r.cap;
  if (c.gap !== null || c.value === null) {
    const why: Record<string, string> = {
      flagged: `flagged: ${c.note ?? r.tape.flag ?? "price history"}`,
      no_price: "awaiting price",
      no_shares: "awaiting shares",
      ads_unread: "the ADS ratio could not be read",
      no_cap: c.note ?? "the share count or the close is not positive",
    };
    return { text: BLANK, title: `Approximate cap: ${why[c.gap ?? ""] ?? c.note ?? "not pinned down"}`, gray: true };
  }
  const parts = [
    `Approximate: the ${c.close_date ?? "latest"} close ${c.close ?? "?"} × ${c.shares != null ? c.shares.toLocaleString("en-US") : "?"} shares`,
    c.ads_ratio ? `÷ ${c.ads_ratio} per ADS` : "",
    c.split_factor && c.split_factor !== 1 ? `× ${c.split_factor} for a split in force` : "",
  ].filter(Boolean);
  return { text: `${fmtUsd(c.value)}${c.tier ? ` ${tierLabel(c.tier)}` : ""}`, title: parts.join(" ") };
}

// --- the call ----------------------------------------------------------------------------------------------------

/** The short word of a trigger kind, for the compact list. */
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
};
export const kindWord = (kind: string) => KIND_WORD[kind] ?? kind.replace(/_/g, " ");

/** The keys that fired, compactly ("insider · breakout"), each kind once, in the order they fired. */
export function keysWords(r: HotRowOut): string {
  const kinds = [...new Set(r.triggers.map((t) => t.kind))];
  return kinds.length ? kinds.map(kindWord).join(" · ") : BLANK;
}

/** One trigger's hover line: its label, kind, grade and fire date (the market event, not the recorded night). */
export function triggerLine(t: HotTriggerOut): string {
  return `${t.label} · ${kindWord(t.kind)} · ${t.grade ?? "no grade"} · fired ${t.event_date ?? "no date stated"}`;
}

export const KEY_WORD = { conviction: "conviction", confirmation: "confirmation" } as const;

/** The verdict cell: an Armed or Managing member's own verdict (the Board's labels); a Warming name says which
 *  key its trigger turned; a watch member says it is confirmation only. */
export function verdictWords(r: HotRowOut): string {
  if (r.kind === "warming") return r.keys.length ? `${r.keys.map((k) => KEY_WORD[k]).join(" + ")} key` : "one key";
  if (r.kind === "watch") return "confirmation only";
  return r.verdict ? verdictLabel(r.verdict) : BLANK;
}

export const KIND_TAG: Record<RowKind, string> = { armed: "", warming: "warming", watch: "watch", managing: "managing" };

/** Setup strength, a relative indicator (the wire field is `confidence`), never a probability. */
export const strengthWords = (r: HotRowOut) => (r.confidence == null ? BLANK : r.confidence.toFixed(2));

export function riskCell(r: HotRowOut): Cell {
  const n = r.risk_kinds.length;
  if (n === 0) return { text: BLANK, title: "No risk signal on the card names this security", gray: true };
  return { text: String(n), title: `${plural(n, "risk signal", "risk signals")} on the card name this security: ${[...new Set(r.risk_kinds)].map(kindWord).join(", ")}` };
}

// --- the clocks --------------------------------------------------------------------------------------------------

export const armNightCell = (r: HotRowOut): Cell => {
  const c = r.clocks;
  if (c.arm_night === null) return { text: BLANK, title: "Not an armed row: no arm night", gray: true };
  return {
    text: `${fmtDay(c.arm_night)}${c.arm_reconstructed ? " R" : ""}`,
    title: `Armed on the recorded night ${c.arm_night}${c.arm_reconstructed ? " (a night rebuilt by the backfill: reported, never scored)" : ""}. The trigger fire dates are in the keys' hover.`,
  };
};

export const sinceCell = (r: HotRowOut): Cell =>
  r.clocks.sessions_since === null
    ? { text: BLANK, title: "No arm night, or no session count for it", gray: true }
    : { text: String(r.clocks.sessions_since), title: "Sessions after the arm night through the as-of, on IWM's bar calendar" };

/** Sessions left in the entry window ("closed N ago" past it). The ONE accent: the card's own
 *  `entry_window_closing` (at most two sessions left). */
export function leftCell(r: HotRowOut): Cell {
  const c = r.clocks;
  if (c.arm_until === null) return { text: BLANK, title: "No entry clock on this call", gray: true };
  if (c.sessions_left !== null) {
    return {
      text: String(c.sessions_left),
      title: `${plural(c.sessions_left, "session", "sessions")} left after the as-of through ${c.arm_until} (Mon to Fri; a market holiday counts)${r.entry_window_closing ? ". Entry window closing." : ""}`,
      accent: r.entry_window_closing,
    };
  }
  const ago = c.closed_ago ?? 0;
  return { text: ago > 0 ? `closed ${ago} ago` : "closed", title: `The entry window ended ${c.arm_until}`, gray: true };
}

export function exitCell(r: HotRowOut): Cell {
  const c = r.clocks;
  if (c.exit_by === null || c.days_to_exit === null) return { text: BLANK, title: "No hold clock on this call", gray: true };
  if (c.days_to_exit < 0) return { text: `passed ${-c.days_to_exit}d`, title: `The hold horizon ended ${c.exit_by}`, gray: true };
  return {
    text: `${c.days_to_exit}d`,
    title: `${plural(c.days_to_exit, "day", "days")} to the hold horizon ${c.exit_by}${r.lapsing ? ". Hold clock closing." : ""}`,
  };
}

/** The return since the arm night and IWM's over the same dates. */
export function sinceArmCells(r: HotRowOut): { ret: Cell; iwm: Cell } {
  const s = r.since_arm;
  const why: Record<string, string> = {
    no_arm_night: "Not an armed row: no arm night",
    flagged: `flagged: ${r.tape.flag ?? "price history"}`,
    before_window: "The arm night is before the earliest bar the view reads",
    no_bar: "No close on or after the arm night",
  };
  if (s.ret_pct === null) return { ret: { text: BLANK, title: why[s.gap ?? ""] ?? "no reading", gray: true }, iwm: { text: BLANK, gray: true } };
  const span = `${s.entry_date} close ${s.entry_close} to ${s.last_date} close ${s.last_close}`;
  return {
    ret: { text: fmtPct(s.ret_pct), title: `As-traded, from the first close on or after the arm night: ${span}`, tone: signTone(s.ret_pct) },
    iwm:
      s.iwm_pct === null
        ? { text: BLANK, title: "IWM has no bar on or after the arm night", gray: true }
        : { text: fmtPct(s.iwm_pct), title: "IWM over the same dates", tone: signTone(s.iwm_pct) },
  };
}

// --- the rows shown ----------------------------------------------------------------------------------------------

export type Toggles = { warming: boolean; watch: boolean; managing: boolean };
export const DEFAULT_TOGGLES: Toggles = { warming: false, watch: false, managing: false };
export const TOGGLE_KINDS = ["warming", "watch", "managing"] as const;

/** How many rows each toggle adds (shown on the toggle). */
export function toggleCounts(rows: readonly HotRowOut[]): Record<(typeof TOGGLE_KINDS)[number], number> {
  const n = { warming: 0, watch: 0, managing: 0 };
  for (const r of rows) if (r.kind !== "armed") n[r.kind] += 1;
  return n;
}

/** The rows the toggles show: every armed row, plus the kinds switched on. */
export const rowsOnShow = (rows: readonly HotRowOut[], t: Toggles): HotRowOut[] =>
  rows.filter((r) => r.kind === "armed" || t[r.kind]);

export type GradePick = "flip" | "core";
export type Filters = {
  grades: readonly GradePick[];
  triggerKinds: readonly string[];
  /** a card's thesis_id, or null for every card */
  card: string | null;
  tiers: readonly TierPick[];
  /** only rows whose entry window is still open at the as-of */
  openOnly: boolean;
};
export const DEFAULT_FILTERS: Filters = { grades: [], triggerKinds: [], card: null, tiers: [], openOnly: false };

export const filtersActive = (f: Filters) =>
  f.grades.length > 0 || f.triggerKinds.length > 0 || f.card !== null || f.tiers.length > 0 || f.openOnly;

export const tierOf = (r: HotRowOut): TierPick => r.cap.tier ?? "none";
export const windowOpen = (r: HotRowOut) => r.clocks.sessions_left !== null;

export function rowMatches(r: HotRowOut, f: Filters): boolean {
  if (f.grades.length && !(r.entry_grade && f.grades.includes(r.entry_grade))) return false;
  if (f.triggerKinds.length && !r.triggers.some((t) => f.triggerKinds.includes(t.kind))) return false;
  if (f.card !== null && r.thesis_id !== f.card) return false;
  if (f.tiers.length && !f.tiers.includes(tierOf(r))) return false;
  if (f.openOnly && !windowOpen(r)) return false;
  return true;
}

/** The controls that can discriminate among the rows on offer (a chip every row shares carries no information and
 *  does not render). */
export function filterControls(rows: readonly HotRowOut[]) {
  const grades = (["flip", "core"] as const).filter((g) => rows.some((r) => r.entry_grade === g));
  const kinds = [...new Set(rows.flatMap((r) => r.triggers.map((t) => t.kind)))].sort();
  const cards = [...new Map(rows.map((r) => [r.thesis_id, r.thesis_name])).entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  const present = new Set(rows.map(tierOf));
  const tiers = [...TIER_ORDER, "none" as const].filter((t) => present.has(t));
  const open = rows.filter(windowOpen).length;
  return {
    grades: grades.length > 1 ? grades : [],
    triggerKinds: kinds.length > 1 ? kinds : [],
    cards: cards.length > 1 ? cards : [],
    tiers: tiers.length > 1 ? tiers : [],
    openOnly: open > 0 && open < rows.length,
  };
}

// --- the sorts ---------------------------------------------------------------------------------------------------

export type SortKey =
  | "ticker"
  | "card"
  | "heat"
  | "verdict"
  | "grade"
  | "strength"
  | "keys"
  | "risk"
  | "arm"
  | "since"
  | "left"
  | "exit"
  | "ret"
  | "iwm"
  | "rvol"
  | "vs50"
  | "offHigh"
  | "vsIwm"
  | "ret7"
  | "ret30"
  | "insider"
  | "adv"
  | "cap";
export type Sort = { key: SortKey; dir: "asc" | "desc" };
/** The page's default: heat, descending. */
export const DEFAULT_SORT: Sort = { key: "heat", dir: "desc" };

const TEXT_KEYS: readonly SortKey[] = ["ticker", "card", "verdict", "grade", "keys"];
/** A column's first direction: text A to Z, a figure biggest first, a date newest first. */
export const firstDir = (key: SortKey): Sort["dir"] => (TEXT_KEYS.includes(key) ? "asc" : "desc");

/** A click on a column: the same column flips its direction, another starts in its own first direction. */
export function nextSort(cur: Sort, key: SortKey): Sort {
  if (cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: firstDir(key) };
}

/** A row's value in a column: a string or number to compare; `null` = blank (always last, whatever the
 *  direction: a blank never leads and is never read as zero). A closed entry window sorts below every open one
 *  (more sessions since it ended = lower). */
export function sortValue(r: HotRowOut, key: SortKey, heat: Heat | undefined): string | number | null {
  const c = r.clocks;
  switch (key) {
    case "ticker":
      return r.ticker.toLocaleLowerCase();
    case "card":
      return r.thesis_name.toLocaleLowerCase();
    case "heat":
      return heat?.score ?? null;
    case "verdict":
      return verdictWords(r).toLocaleLowerCase();
    case "grade":
      return r.entry_grade;
    case "strength":
      return r.confidence;
    case "keys":
      return r.triggers.length ? keysWords(r).toLocaleLowerCase() : null;
    case "risk":
      return r.risk_kinds.length;
    case "arm":
      return c.arm_night;
    case "since":
      return c.sessions_since;
    case "left":
      return c.sessions_left ?? (c.closed_ago !== null ? -c.closed_ago : null);
    case "exit":
      return c.days_to_exit;
    case "ret":
      return r.since_arm.ret_pct;
    case "iwm":
      return r.since_arm.iwm_pct;
    case "rvol":
      return r.tape.rvol20.value;
    case "vs50":
      return r.tape.vs_50d.value;
    case "offHigh":
      return r.tape.off_high.value;
    case "vsIwm":
      return r.tape.vs_iwm.value;
    case "ret7":
      return r.tape.ret_7d.value;
    case "ret30":
      return r.tape.ret_30d.value;
    case "insider":
      return r.tape.buys_30d;
    case "adv":
      return r.tape.adv_usd_20d.value;
    case "cap":
      return r.cap.value;
  }
}

/** The rows in the sort's order; a blank last in either direction; ties by ticker, then card, then key. */
export function sortRows(rows: readonly HotRowOut[], sort: Sort, heats: ReadonlyMap<string, Heat>): HotRowOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  const val = (r: HotRowOut) => sortValue(r, sort.key, heats.get(r.key));
  return [...rows].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    if (x === null && y !== null) return 1;
    if (y === null && x !== null) return -1;
    if (x !== null && y !== null && x !== y) {
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      if (cmp !== 0) return sign * cmp;
    }
    return (
      a.ticker.toLocaleLowerCase().localeCompare(b.ticker.toLocaleLowerCase()) ||
      a.thesis_name.localeCompare(b.thesis_name) ||
      a.key.localeCompare(b.key)
    );
  });
}

// --- heat, in words ----------------------------------------------------------------------------------------------

/** The heat number: 0 to 100, or "—" for a row missing a part. */
export const heatText = (h: Heat | undefined) => (h?.score == null ? BLANK : String(Math.round(h.score)));

const p2 = (v: number | null) => (v === null ? BLANK : v.toFixed(2));

/** The four parts for the hover, or why there is no heat. */
export function heatTitle(h: Heat | undefined): string {
  if (!h) return "No heat";
  const parts = `fresh ${p2(h.fresh)} · loud ${p2(h.loud)} · leading ${p2(h.leading)} · unextended ${p2(h.unextended)}`;
  return h.score === null ? `No heat: a part has no reading (${parts})` : parts;
}

export const FORMULA =
  "Heat (stated formula): the mean of four percentiles, shown 0 to 100, each among the rows shown that have the reading. " +
  "fresh: fewer sessions since the arm night ranks higher. loud: a higher RVOL 20 ranks higher. " +
  "leading: a higher 30-day return minus IWM's ranks higher. unextended: a smaller return since the arm night ranks higher. " +
  "Equal weights. A row missing a part reads — and sorts last. Ties share a percentile. Heat compares the rows shown " +
  "with each other, so a toggle or a filter changes it; it is not a score across days, and it feeds nothing.";

export const LIMITS: readonly string[] = [
  "Heat compares the rows shown with each other; it is not a score with a meaning across days.",
  "The arm night is the recorded night; the trigger fire dates are in the keys' hover.",
  "The tape is the display members' own reading (split-adjusted closes, no dividends).",
  "A name on two cards is two rows: the tape is the same, the call columns are each card's own.",
];

// --- the work lines ----------------------------------------------------------------------------------------------

/** How many of the rows have no reading in a tape column (flagged rows are counted apart). */
function blanks(rows: readonly HotRowOut[], read: (r: HotRowOut) => boolean) {
  return rows.filter((r) => r.tape.flag === null && read(r)).length;
}

export function noReadingLine(rows: readonly HotRowOut[]): string | null {
  const parts: [string, number][] = [
    ["RVOL 20", blanks(rows, (r) => r.tape.rvol20.value === null)],
    ["vs 50d", blanks(rows, (r) => r.tape.vs_50d.value === null)],
    ["off 52w high", blanks(rows, (r) => r.tape.off_high.value === null)],
    ["vs IWM", blanks(rows, (r) => r.tape.vs_iwm.value === null)],
    ["7D", blanks(rows, (r) => r.tape.ret_7d.value === null)],
    ["30D", blanks(rows, (r) => r.tape.ret_30d.value === null)],
    ["insider", rows.filter((r) => r.tape.buys_30d === null).length],
    ["$vol", blanks(rows, (r) => r.tape.adv_usd_20d.value === null)],
    ["cap", rows.filter((r) => r.tape.flag === null && r.cap.value === null).length],
  ];
  const got = parts.filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`);
  return got.length ? `rows without a reading: ${got.join(" · ")}` : null;
}

/** The "show the work" lines: the as-of and clock, the cards by state, the rows and what the toggles add, the
 *  blanks, the calendar and the entry rule. */
export function workLines(
  data: HotBoardOut,
  shown: readonly HotRowOut[],
  counts: Record<(typeof TOGGLE_KINDS)[number], number>,
  toggles: Toggles,
  scored: number,
): string[] {
  const clock = data.clock === "public" ? "the public clock: what was public by the end of that date" : "the live read";
  const cards =
    `${plural(data.cards_counted, "card", "cards")} counted: ${data.cards_armed} armed · ${data.cards_warming} warming · ` +
    `${data.cards_managing} managing · ${data.cards_incubating} incubating · ${data.cards_no_call} no call yet`;
  const added = TOGGLE_KINDS.filter((k) => toggles[k]).map((k) => `${counts[k]} ${KIND_TAG[k]}`);
  const rowsLine =
    `${plural(shown.length, "row", "rows")} shown` +
    (added.length ? ` (the armed names plus ${added.join(" and ")})` : "") +
    `; ${scored} with a heat, ${shown.length - scored} without (a part has no reading)`;
  const flagged = shown.filter((r) => r.tape.flag !== null).length;
  const noBar = shown.filter((r) => r.since_arm.gap === "no_bar").length;
  const early = shown.filter((r) => r.since_arm.gap === "before_window").length;
  const recon = shown.filter((r) => r.clocks.arm_reconstructed).length;
  const lines = [
    `as of ${data.asof}, read on ${clock}`,
    cards,
    rowsLine,
    noReadingLine(shown),
    flagged ? `${plural(flagged, "row", "rows")} with a flagged price history: tape cells grayed, no heat` : null,
    noBar ? `${plural(noBar, "row", "rows")} with no close on or after the arm night` : null,
    early ? `${plural(early, "row", "rows")} with an arm night before the earliest price the view reads: no session count or return` : null,
    recon ? `${plural(recon, "arm night", "arm nights")} rebuilt by the backfill (R): reported, never scored` : null,
    data.no_ticker_rows ? `${plural(data.no_ticker_rows, "row", "rows")} whose security has no ticker, shown under its id` : null,
    data.roster_fallback_cards
      ? `${plural(data.roster_fallback_cards, "card", "cards")} with no roster snapshot by then: today's roster stands in`
      : null,
    `sessions are ${data.benchmark}'s bars: ${data.calendar_sessions.toLocaleString("en-US")} ${data.calendar_source === "benchmarks" ? "" : `(${data.calendar_source}) `}ending ${data.calendar_last ?? "—"}; entry rule: the first close on or after the arm night`,
    `${data.cache === "hit" ? "served from the cache" : "computed"} · compute ${data.compute_ms.toLocaleString("en-US")} ms`,
  ];
  return lines.filter((l): l is string => l !== null);
}

// --- the sparkline -----------------------------------------------------------------------------------------------

/** An SVG path for the last-90-closes sparkline in a w x h box, broken on a blank slot (never interpolated across
 *  it). `null` when there are fewer than two real closes (a point is not a path). */
export function sparkPath(values: ReadonlyArray<number | null>, w: number, h: number): string | null {
  const real = values.filter((v): v is number => v !== null);
  if (real.length < 2) return null;
  const lo = Math.min(...real);
  const hi = Math.max(...real);
  const span = hi - lo || 1;
  const n = values.length;
  const x = (i: number) => (n === 1 ? 0 : (i / (n - 1)) * w);
  const y = (v: number) => h - ((v - lo) / span) * (h - 2) - 1;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}
