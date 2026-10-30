import type { TrackRecordOut, TrackRowOut, TrackSpanOut } from "./useTrackRecord";

// Pure helpers for the Track Record page (docs/dashboards/track-record.md). The server sends every arm episode's row
// with both entry rules' reads and all five horizons; the cap tier, the liquidity band, the filters, the entry
// toggle and the comparison are picked here, so a click re-tables the page with no request. The cell arithmetic
// MIRRORS the backend's (dashboards/track_record.py: median, linear-interpolated quartiles, a hit = strictly above
// zero, n under 10 too small to read); both test files pin the same hand-computed vectors. Display only: nothing
// here ranks an idea or says a result "works" (invariant #4): the wording states what happened, with the horizon,
// the comparison, n and how often it held.

export const MIN_N = 10;
export const UNPINNED = "unpinned";
export const POOLED = "pooled";
export const NONE_KEY = "none"; // the filter value of a null attribute (no Key-1 source, no entry grade)

export type Horizon = "h5" | "h10" | "h20" | "h60" | "exit_by";
export type Comparison = "iwm" | "spy" | "raw";
export type EntryRule = "arm_night" | "next_session";
export type SliceBy = "tier" | "liquidity";

export type HorizonDef = { key: Horizon; label: string; words: string };
export const HORIZONS: readonly HorizonDef[] = [
  { key: "h5", label: "5 sessions", words: "5 sessions" },
  { key: "h10", label: "10 sessions", words: "10 sessions" },
  { key: "h20", label: "20 sessions", words: "20 sessions" },
  { key: "h60", label: "60 sessions", words: "60 sessions" },
  { key: "exit_by", label: "to exit_by", words: "the span to each call's own exit_by" },
];

export const TIER_KEYS = ["nano", "micro", "small", "mid", "large"] as const;
export const LIQUIDITY_KEYS = ["under_1m", "1m_10m", "10m_plus"] as const;

/** The fixed market-cap bands (CLAUDE.md), and the PROPOSED liquidity bands, in words. */
const BAND_WORDS: Record<string, { label: string; band: string }> = {
  nano: { label: "nano", band: "under $50M" },
  micro: { label: "micro", band: "$50M to $300M" },
  small: { label: "small", band: "$300M to $2B" },
  mid: { label: "mid", band: "$2B to $10B" },
  large: { label: "large", band: "$10B and over" },
  under_1m: { label: "under $1M", band: "20-day avg $ volume" },
  "1m_10m": { label: "$1M to $10M", band: "20-day avg $ volume" },
  "10m_plus": { label: "$10M and over", band: "20-day avg $ volume" },
};

export const COMPARISON_WORDS: Record<Comparison, string> = {
  iwm: "IWM",
  spy: "SPY",
  raw: "raw return",
};
export const ENTRY_WORDS: Record<EntryRule, string> = {
  arm_night: "arm-night close",
  next_session: "next-session close",
};

export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- the rows in a slice -----------------------------------------------------------------------------------

/** A row's place on the slice dimension: its cap tier or liquidity band, `unpinned` when it has none. */
export function sliceKey(row: TrackRowOut, slice: SliceBy): string {
  if (slice === "tier") return row.cap?.tier ?? UNPINNED;
  return row.liquidity ?? UNPINNED;
}

export function sliceKeys(slice: SliceBy): readonly string[] {
  return slice === "tier" ? TIER_KEYS : LIQUIDITY_KEYS;
}

/** The label of a table row (and the band's words under it). The pooled row says it is pooled. */
export function rowLabel(key: string, slice: SliceBy): { label: string; band: string | null } {
  if (key === POOLED) {
    return { label: slice === "tier" ? "all tiers (pooled)" : "all liquidity bands (pooled)", band: null };
  }
  if (key === UNPINNED) {
    return { label: "unpinned", band: slice === "tier" ? "no cap could be pinned" : "under 20 volume sessions before" };
  }
  const w = BAND_WORDS[key];
  return w ? { label: w.label, band: w.band } : { label: key, band: null };
}

// --- the filters (chips): they narrow every cell, they never cross-tabulate --------------------------------------

export type Filters = {
  /** the call's headline name only (the card's `armed_security_id`), against every armed member */
  headline: boolean;
  key1: readonly string[];
  grade: readonly string[];
  coArm: readonly string[];
};
export const DEFAULT_FILTERS: Filters = { headline: false, key1: [], grade: [], coArm: [] };

export const filtersActive = (f: Filters) =>
  f.headline || f.key1.length > 0 || f.grade.length > 0 || f.coArm.length > 0;

/** Does a row pass the filters? Within one chip group any picked value passes; across groups all must. */
export function rowMatches(r: TrackRowOut, f: Filters): boolean {
  if (f.headline && !r.is_headline) return false;
  if (f.key1.length > 0 && !f.key1.includes(r.key1_source ?? NONE_KEY)) return false;
  if (f.grade.length > 0 && !f.grade.includes(r.entry_grade ?? NONE_KEY)) return false;
  if (f.coArm.length > 0 && !f.coArm.includes(r.co_arm_bucket)) return false;
  return true;
}

const KEY1_ORDER = ["insider", "activist", "ratified_catalyst", "corporate_catalyst", "revenue_accel", "theme"];
const GRADE_ORDER = ["flip", "core"];
const CO_ARM_ORDER = ["alone", "2-3", "4-7", "8+"];

const KEY1_WORDS: Record<string, string> = {
  insider: "insider",
  activist: "activist",
  ratified_catalyst: "ratified catalyst",
  corporate_catalyst: "corporate catalyst",
  revenue_accel: "revenue acceleration",
  theme: "theme",
  [NONE_KEY]: "none stated",
};
const CO_ARM_WORDS: Record<string, string> = {
  alone: "armed alone",
  "2-3": "group of 2-3",
  "4-7": "group of 4-7",
  "8+": "group of 8+",
};

export type ChipOption = { value: string; label: string; n: number };
export type ChipGroups = { key1: ChipOption[]; grade: ChipOption[]; coArm: ChipOption[] };

function options(
  rows: readonly TrackRowOut[],
  pick: (r: TrackRowOut) => string,
  order: readonly string[],
  words: (v: string) => string,
): ChipOption[] {
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(pick(r), (counts.get(pick(r)) ?? 0) + 1);
  const rank = (v: string) => (v === NONE_KEY ? order.length + 1 : order.includes(v) ? order.indexOf(v) : order.length);
  return [...counts.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || (a[0] < b[0] ? -1 : 1))
    .map(([value, n]) => ({ value, label: words(value), n }));
}

/** The chip values present among the counted rows, each with how many rows carry it. A value no row carries has no
 *  chip (a control that cannot discriminate does not render: WB#3). */
export function chipGroups(rows: readonly TrackRowOut[]): ChipGroups {
  return {
    key1: options(rows, (r) => r.key1_source ?? NONE_KEY, KEY1_ORDER, (v) => KEY1_WORDS[v] ?? v.replace(/_/g, " ")),
    grade: options(rows, (r) => r.entry_grade ?? NONE_KEY, GRADE_ORDER, (v) => (v === NONE_KEY ? "none stated" : v)),
    coArm: options(rows, (r) => r.co_arm_bucket, CO_ARM_ORDER, (v) => CO_ARM_WORDS[v] ?? v),
  };
}

// --- the cell statistics -----------------------------------------------------------------------------------------

/** The `p` quantile of ascending values by linear interpolation between order statistics (the median at 0.5). */
export function quantile(sorted: readonly number[], p: number): number {
  const n = sorted.length;
  if (n === 0) throw new Error("quantile of nothing");
  const h = (n - 1) * p;
  const lo = Math.floor(h);
  const hi = Math.min(lo + 1, n - 1);
  return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
}

/** One counted episode at one horizon: the name's return and the comparison's (null under raw, or when the benchmark
 *  had no bar on one of the dates). */
export type Obs = { ret: number; bench: number | null };

export type Cell = {
  n: number;
  held: number;
  hitRate: number | null;
  median: number | null;
  q1: number | null;
  q3: number | null;
  /** the median of the names' raw returns over the same episodes */
  nameMedian: number | null;
  /** the median of the comparison's raw returns over the same episodes (null under raw) */
  benchMedian: number | null;
  /** counted spans left out because the comparison had no bar */
  noComparison: number;
  /** 0 < n < MIN_N: the page prints n only */
  tooSmall: boolean;
};

const sortedAsc = (xs: number[]) => [...xs].sort((a, b) => a - b);

/** A cell from the observations that have a counted span at one horizon. Under IWM or SPY the value is the excess
 *  (the name's return minus the benchmark's) and an observation without that benchmark's return is left out and
 *  counted in `noComparison`; under raw it is the raw return. A hit is strictly above zero. */
export function cellStats(obs: readonly Obs[], comparison: Comparison, minN = MIN_N): Cell {
  const used =
    comparison === "raw"
      ? obs.map((o) => ({ ret: o.ret, bench: null as number | null, value: o.ret }))
      : obs.flatMap((o) => (o.bench == null ? [] : [{ ret: o.ret, bench: o.bench, value: o.ret - o.bench }]));
  const noComparison = comparison === "raw" ? 0 : obs.length - used.length;
  const n = used.length;
  if (n === 0) {
    return { n: 0, held: 0, hitRate: null, median: null, q1: null, q3: null, nameMedian: null, benchMedian: null, noComparison, tooSmall: false };
  }
  const values = sortedAsc(used.map((u) => u.value));
  const held = values.filter((v) => v > 0).length;
  const benches = sortedAsc(used.flatMap((u) => (u.bench == null ? [] : [u.bench])));
  return {
    n,
    held,
    hitRate: held / n,
    median: quantile(values, 0.5),
    q1: quantile(values, 0.25),
    q3: quantile(values, 0.75),
    nameMedian: quantile(sortedAsc(used.map((u) => u.ret)), 0.5),
    benchMedian: benches.length > 0 ? quantile(benches, 0.5) : null,
    noComparison,
    tooSmall: n < minN,
  };
}

/** The span of a row for one entry rule and horizon (a left-out row has none). */
export const spanOf = (r: TrackRowOut, entry: EntryRule, horizon: Horizon): TrackSpanOut | undefined =>
  r[entry]?.spans[horizon];

/** The comparison's return for a span, or null (raw, or a benchmark with no bar). */
export const benchOf = (s: TrackSpanOut, comparison: Comparison): number | null =>
  comparison === "iwm" ? s.iwm : comparison === "spy" ? s.spy : null;

/** The value a list cell prints: the excess under IWM or SPY, the raw return under raw; null when the span is not
 *  counted or the comparison has no return. */
export function spanValue(s: TrackSpanOut | undefined, comparison: Comparison): number | null {
  if (!s || s.status !== "counted" || s.ret == null) return null;
  if (comparison === "raw") return s.ret;
  const b = benchOf(s, comparison);
  return b == null ? null : s.ret - b;
}

/** The counted spans of `rows` at one horizon of one entry rule, with the comparison's return alongside. */
export function observations(
  rows: readonly TrackRowOut[],
  entry: EntryRule,
  horizon: Horizon,
  comparison: Comparison,
): Obs[] {
  const out: Obs[] = [];
  for (const r of rows) {
    const s = spanOf(r, entry, horizon);
    if (!s || s.status !== "counted" || s.ret == null) continue;
    out.push({ ret: s.ret, bench: benchOf(s, comparison) });
  }
  return out;
}

/** The rows a table cell rests on: those with a counted span at the horizon (and the comparison's return, unless
 *  raw). Clicking a cell lists exactly these. */
export function cellMembers(
  rows: readonly TrackRowOut[],
  entry: EntryRule,
  horizon: Horizon,
  comparison: Comparison,
): TrackRowOut[] {
  return rows.filter((r) => {
    const s = spanOf(r, entry, horizon);
    if (!s || s.status !== "counted" || s.ret == null) return false;
    return comparison === "raw" || benchOf(s, comparison) != null;
  });
}

// --- the table -----------------------------------------------------------------------------------------------------

export type TableRowView = {
  key: string;
  label: string;
  band: string | null;
  episodes: number;
  cells: Record<Horizon, Cell>;
  members: TrackRowOut[];
};

/** The table for the COUNTED rows: the slice's rows in size order, then `unpinned`, then `pooled` LAST (the whole
 *  counted set; never first). Every row is present, a row with no episode too. */
export function tableRows(
  rows: readonly TrackRowOut[],
  slice: SliceBy,
  entry: EntryRule,
  comparison: Comparison,
  minN = MIN_N,
): TableRowView[] {
  const counted = rows.filter((r) => r.left_out == null);
  const keys = [...sliceKeys(slice), UNPINNED];
  const groups = new Map<string, TrackRowOut[]>(keys.map((k) => [k, []]));
  for (const r of counted) groups.get(sliceKey(r, slice))?.push(r);
  groups.set(POOLED, counted);
  return [...groups.entries()].map(([key, members]) => {
    const cells = {} as Record<Horizon, Cell>;
    for (const h of HORIZONS) cells[h.key] = cellStats(observations(members, entry, h.key, comparison), comparison, minN);
    return { key, ...rowLabel(key, slice), episodes: members.length, cells, members };
  });
}

// --- the words -----------------------------------------------------------------------------------------------------

const MINUS = "−";

/** A signed percent from a fraction: +6.0%, −1.2%. */
export function fmtRet(x: number | null | undefined, digits = 1): string {
  if (x == null) return "";
  const s = (Math.abs(x) * 100).toFixed(digits);
  if (Number(s) === 0) return `${(0).toFixed(digits)}%`; // a move that rounds to nothing carries no sign
  return `${x > 0 ? "+" : MINUS}${s}%`;
}

export const fmtRate = (x: number | null) => (x == null ? "" : `${Math.round(x * 100)}%`);

/** USD, compact: $48M, $1.2B, $350K. */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e3) return `$${Math.round(v / 1e3)}K`;
  return `$${Math.round(v)}`;
}

export type Tone = "pos" | "neg" | "";
export const toneOf = (x: number | null | undefined): Tone => (x == null || x === 0 ? "" : x > 0 ? "pos" : "neg");

/** The comparison as a phrase: "excess vs IWM", "raw return". */
export const measureWords = (c: Comparison) => (c === "raw" ? "raw return" : `excess vs ${COMPARISON_WORDS[c]}`);

export type CellView = {
  text: string;
  sub: string | null;
  tone: Tone;
  gray: boolean;
  title: string;
  /** a cell with a number worth listing: its episodes open on a click */
  clickable: boolean;
};

/** What one table cell prints and says on hover. The hover always states the four things the rule asks for (the
 *  horizon, the comparison, n and how often it held), with the medians of the names' and the benchmark's raw
 *  returns. n under 10 prints the n only unless the thin cells are shown (then grayed). */
export function cellView(
  cell: Cell,
  horizon: HorizonDef,
  comparison: Comparison,
  entry: EntryRule,
  showThin: boolean,
): CellView {
  const basis = `${measureWords(comparison)} over ${horizon.words}, ${ENTRY_WORDS[entry]} entry`;
  const gaps = cell.noComparison > 0 ? ` ${cell.noComparison} more had no ${COMPARISON_WORDS[comparison]} bar on one of the dates.` : "";
  if (cell.n === 0) {
    return { text: "—", sub: null, tone: "", gray: true, title: `${basis}: no matured episodes.${gaps}`, clickable: false };
  }
  const hit = `held ${cell.held} of ${cell.n} (${fmtRate(cell.hitRate)})`;
  const iqr = `interquartile range ${fmtRet(cell.q1)} to ${fmtRet(cell.q3)}`;
  const medians =
    comparison === "raw"
      ? `the names' median raw return ${fmtRet(cell.nameMedian)}`
      : `the names' median raw return ${fmtRet(cell.nameMedian)}, ${COMPARISON_WORDS[comparison]}'s ${fmtRet(cell.benchMedian)}`;
  const full = `${basis}: median ${fmtRet(cell.median)} · n = ${cell.n} · ${hit} · ${iqr} · ${medians}.${gaps}`;
  if (cell.tooSmall) {
    const small = `n = ${cell.n} is under ${MIN_N}: too small to read.`;
    return showThin
      ? { text: fmtRet(cell.median), sub: `${cell.held}/${cell.n}`, tone: "", gray: true, title: `${small} ${full}`, clickable: true }
      : { text: `n ${cell.n}`, sub: "too small to read", tone: "", gray: true, title: `${small} ${basis}.${gaps}`, clickable: true };
  }
  return { text: fmtRet(cell.median), sub: `${cell.held}/${cell.n}`, tone: toneOf(cell.median), gray: false, title: full, clickable: true };
}

/** What one horizon prints in the episode list: the excess (or raw return), or why there is no number. */
export function spanView(s: TrackSpanOut | undefined, comparison: Comparison): { text: string; tone: Tone; gray: boolean; title: string } {
  if (!s) return { text: "", tone: "", gray: true, title: "" };
  switch (s.status) {
    case "maturing":
      return { text: "maturing", tone: "", gray: true, title: "This horizon has not arrived by the as-of." };
    case "path_ended":
      return { text: "path ended", tone: "", gray: true, title: "The name's bars end before this horizon while the market's go on." };
    case "none":
      return { text: "—", tone: "", gray: true, title: "Nothing to measure: no exit_by, or no session between the entry and it." };
    default: {
      const v = spanValue(s, comparison);
      if (v == null) {
        return {
          text: "no bar",
          tone: "",
          gray: true,
          title: `${COMPARISON_WORDS[comparison]} has no bar on one of the dates; the name's own return was ${fmtRet(s.ret)}.`,
        };
      }
      const detail =
        comparison === "raw"
          ? `Return ${fmtRet(s.ret)} to ${s.end_date}.`
          : `The name ${fmtRet(s.ret)}, ${COMPARISON_WORDS[comparison]} ${fmtRet(benchOf(s, comparison))}, to ${s.end_date}.`;
      return { text: fmtRet(v), tone: toneOf(v), gray: false, title: detail };
    }
  }
}

/** The cap tier and approximate cap of a row, for the list ("small ~$576M"), or the reason it is unpinned. */
export function capWords(r: TrackRowOut): { text: string; title: string } {
  const cap = r.cap;
  if (!cap) return { text: "", title: "" };
  if (cap.value == null || cap.tier == null) {
    const why: Record<string, string> = {
      no_price: "no close before the arm date",
      no_shares: "no share count dated before the arm date",
      ads_unread: "an ADR whose ordinary-shares-per-ADS ratio is unread",
      no_cap: "a share count or close that is not positive",
    };
    return { text: UNPINNED, title: `Cap unpinned: ${(cap.gap && why[cap.gap]) || "it could not be measured"}.` };
  }
  const parts = [
    `Approximate: the as-traded close of ${cap.close_date} ($${cap.close?.toFixed(2)}) times the ${cap.shares?.toLocaleString("en-US")} shares dated ${cap.shares_date}`,
  ];
  if (cap.ads_ratio) parts.push(`divided by the ADS ratio ${cap.ads_ratio}`);
  if (cap.split_factor) parts.push(`the count put on the close's scale by a split (×${cap.split_factor})`);
  return { text: `${cap.tier} ~${fmtUsd(cap.value)}`, title: `${parts.join(", ")}.` };
}

export const LIQUIDITY_WORDS: Record<string, string> = {
  under_1m: "under $1M",
  "1m_10m": "$1M to $10M",
  "10m_plus": "$10M and over",
};

/** The liquidity band of a row for the list, with the figure behind it in the hover. */
export function liquidityWords(r: TrackRowOut): { text: string; title: string } {
  if (!r.liquidity) {
    return { text: UNPINNED, title: "Liquidity unpinned: fewer than 20 volume-bearing sessions precede the arm date." };
  }
  return {
    text: LIQUIDITY_WORDS[r.liquidity] ?? r.liquidity,
    title: `20-day average dollar volume before the arm date: ${fmtUsd(r.adv_usd)} a day.`,
  };
}

/** Which edition the read is, and for the record why it is the default: the editions nightly is paused, so a page
 *  that names no edition reads the calls log. */
export function editionWords(data: TrackRecordOut): string {
  const e = data.edition;
  return e.number === 0 ? `edition 0: ${e.label} (the default; editions are paused)` : `edition ${e.number}: ${e.label}`;
}

/** The quiet "show the work" lines above the table: the as-of, the clock, the edition and the honesty policies in
 *  force, what the cells rest on and what is left out, how each horizon reads for the entry rule in force, the
 *  entry rule, the comparison, the market clock and the cache. */
export function workLines(data: TrackRecordOut, entry: EntryRule, comparison: Comparison): string[] {
  const c = data.counts;
  const lines: string[] = [];
  const clock = data.clock === "public" ? "public clock: only what was public by the end of that date" : "live read";
  const policies =
    data.annotated_policy === "n/a" ? "" : ` · annotated nights ${data.annotated_policy} · late nights ${data.late_policy}`;
  lines.push(`as of ${data.asof} · ${clock} · ${editionWords(data)}${policies}`);

  const out = [
    c.left_out_flagged > 0 ? `${c.left_out_flagged.toLocaleString("en-US")} flagged price history` : "",
    c.left_out_censored > 0 ? `${c.left_out_censored.toLocaleString("en-US")} censored start` : "",
    c.left_out_ingest > 0 ? `${c.left_out_ingest.toLocaleString("en-US")} ingest flagged` : "",
  ].filter(Boolean);
  lines.push(
    `${plural(c.episodes, "arm episode")} on ${plural(data.cards, "card")} · ${c.counted.toLocaleString("en-US")} in the cells` +
      (out.length > 0 ? ` · left out: ${out.join(", ")}` : "") +
      (data.cards_unreadable > 0 ? ` · ${plural(data.cards_unreadable, "card")} unreadable` : ""),
  );

  const unpinned = [
    c.unpinned_cap > 0 ? `${c.unpinned_cap.toLocaleString("en-US")} with an unpinned cap` : "",
    c.unpinned_liquidity > 0 ? `${c.unpinned_liquidity.toLocaleString("en-US")} with unpinned liquidity` : "",
  ].filter(Boolean);
  if (unpinned.length > 0) lines.push(`${unpinned.join(" · ")} (counted, never dropped)`);

  const per = c[entry];
  lines.push(
    "by horizon (" +
      ENTRY_WORDS[entry] +
      " entry): " +
      HORIZONS.map((h) => {
        const k = per[h.key];
        const bits = [
          `${k.counted.toLocaleString("en-US")} counted`,
          k.maturing > 0 ? `${k.maturing.toLocaleString("en-US")} maturing` : "",
          k.path_ended > 0 ? `${k.path_ended.toLocaleString("en-US")} path ended` : "",
          k.none > 0 ? `${k.none.toLocaleString("en-US")} none to measure` : "",
        ].filter(Boolean);
        return `${h.label} ${bits.join(", ")}`;
      }).join(" · "),
  );

  lines.push(
    entry === "arm_night"
      ? "Entry: the arm night's close, the Scoreboard's own (a call recorded at 22:30 ET could not be acted on at it)."
      : "Entry: the next session's close, the first one after the arm night (the first a trader could act on).",
  );
  const missing = data.benchmarks_missing.length > 0 ? ` ${data.benchmarks_missing.join(" and ")} has no bar at this as-of, so its comparison is blank.` : "";
  lines.push(
    (comparison === "raw"
      ? "Comparison: none shown (the raw return)."
      : `Comparison: ${COMPARISON_WORDS[comparison]} over the same two dates (the name's return minus its).`) + missing,
  );
  lines.push(
    data.market_source
      ? `Market clock: ${data.market_source}'s bar dates; a name whose bars stop more than ${data.stale_days} days behind it has ended.`
      : "Market clock: no SPY or IWM bar at this as-of, so no path can read ended.",
  );
  lines.push(`${data.cache === "hit" ? "cached read" : "computed"} · ${data.compute_ms.toLocaleString("en-US")} ms`);
  return lines;
}

/** The fixed notes under the work lines (the plan's known limits): stated on the page, not buried. */
export const LIMITS: readonly string[] = [
  "Market cap is approximate and measured the session before the arm date: that session's as-traded close times the last share count dated before it. Liquidity is the 20-day average dollar volume before it.",
  "Horizons are sessions of the name's own bars, never calendar days. Returns are as-traded closes: no dividends and no survivorship fix, so a name whose bars end before a horizon is counted as path ended and is in no cell there.",
  "A cell under 10 episodes is too small to read: it prints its n only. The pooled row is every counted episode and is listed last.",
  "The record is young: most cards are weeks old, so the 60-session and exit_by columns are mostly maturing. That shows as n and as maturing, never as a result.",
  "No null comparison here: the timing null and the name null live in the backtest's pooled report. This page compares to IWM and SPY only.",
];
