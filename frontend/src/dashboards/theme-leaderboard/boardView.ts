import { PARTS, type Part, type Score } from "./score";
import type { CardState, LeaderboardCardOut, LeaderboardOut } from "./useThemeLeaderboard";

// The Theme Leaderboard's pure view helpers (docs/dashboards/theme-leaderboard.md): which cards the filters show,
// the sorts, and the words and numbers of every cell. Every figure (the five parts, the columns beside them) is
// computed server-side from the display members, the calls log and the term-mention table; the percentiles and the
// score are recomputed from the cards shown (score.ts). A blank is never zero: it reads "—" (or says why) and sorts
// last in either direction. Kept in this folder rather than shared with the other dashboards', so each page deletes
// cleanly (docs/DASHBOARDS.md rule 8).

// --- small formatting --------------------------------------------------------------------------------------------

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A signed number to `digits` decimals ("+4.8", "-7.1", "0.0"); a blank is "—". */
export function fmtSigned(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}
export const fmtPct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${fmtSigned(v, digits)}%`);
export const fmtPp = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)} pp`);

/** "Jul 2026" for a YYYY-MM-DD month start; a blank is "—". */
export function fmtMonth(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** "Jul to Sep 2026" (the year once when both months share it, "Nov 2025 to Jan 2026" otherwise). */
export function fmtSpan(from: string | null | undefined, to: string | null | undefined): string {
  if (!from || !to) return "—";
  const a = fmtMonth(from);
  const b = fmtMonth(to);
  return from.slice(0, 4) === to.slice(0, 4) ? `${a.split(" ")[0]} to ${b}` : `${a} to ${b}`;
}

/** The sign tone of a signed tape figure: only a signed return takes one (the sign tone, in the page's own CSS). */
export const signTone = (v: number | null | undefined): "pos" | "neg" | "" => (v == null || v === 0 ? "" : v > 0 ? "pos" : "neg");

// --- the state ---------------------------------------------------------------------------------------------------

export type StateKey = CardState | "none";
/** The lifecycle order the state sort and the state chips use. */
export const STATE_ORDER: readonly StateKey[] = ["armed", "managing", "warming", "incubating", "none"];
export const STATE_LABEL: Record<StateKey, string> = {
  armed: "Armed",
  managing: "Managing",
  warming: "Warming",
  incubating: "Incubating",
  none: "No call",
};
export const stateOf = (c: LeaderboardCardOut): StateKey => c.state ?? "none";

// --- the cells ---------------------------------------------------------------------------------------------------

export type Cell = {
  text: string;
  title?: string;
  tone?: "pos" | "neg" | "";
  /** A reason in place of a figure: drawn quiet. */
  gray?: boolean;
  /** A short qualifier beside the figure (drawn quiet): which key is off, how many names are flagged. */
  note?: string;
};

export type Ctx = Pick<LeaderboardOut, "min_priced" | "iwm_ret_30d" | "benchmark" | "recent_months" | "prior_months">;

const BLANK = "—";
const blank = (title: string): Cell => ({ text: BLANK, title, gray: true });

/** The tape part: the median 30-day return of the priced counting names minus IWM's, in percentage points. */
export function tapeCell(c: LeaderboardCardOut, ctx: Ctx): Cell {
  const t = c.tape;
  if (t.value === null) {
    if (t.gap === "no_iwm") {
      return blank(`${ctx.benchmark} has no 30-day return at the as-of, so there is no figure (median of ${t.k} priced names: ${fmtPct(t.median)})`);
    }
    return blank(`Median of ${t.k} priced ${t.k === 1 ? "name" : "names"}: fewer than ${ctx.min_priced}, so no median and no tape part`);
  }
  return {
    text: fmtPp(t.value),
    title: `Median of ${t.k} priced names: ${fmtPct(t.median)} over 30 trading days, minus ${ctx.benchmark}'s ${fmtPct(ctx.iwm_ret_30d)}`,
    tone: signTone(t.value),
  };
}

/** The 7-day median return of the priced counting names (a column beside the parts, not one of them). */
export function ret7Cell(c: LeaderboardCardOut, ctx: Ctx): Cell {
  const r = c.ret_7d;
  if (r.median === null) return blank(`Median of ${r.k} priced ${r.k === 1 ? "name" : "names"}: fewer than ${ctx.min_priced}, so no median`);
  return { text: fmtPct(r.median), title: `Median of ${r.k} priced names over 7 trading days`, tone: signTone(r.median) };
}

/** The breadth part: the counting names above their 50-day average, "x/n". */
export function breadthCell(c: LeaderboardCardOut): Cell {
  const b = c.breadth;
  if (b.pct === null) {
    return blank(
      b.thin > 0
        ? `${plural(b.thin, "name", "names")} with too little history for the reading (shown, not counted)`
        : "No counting name with a price history",
    );
  }
  const thin = b.thin > 0 ? `; ${plural(b.thin, "name", "names")} with too little history, shown but not counted` : "";
  return {
    text: `${b.above}/${b.counted}`,
    title: `${b.above} of ${b.counted} counting names are above their 50-day average (${Math.round(b.pct)}%)${thin}`,
  };
}

/** The share above the 200-day average rides along beside the breadth part. */
export function above200Cell(c: LeaderboardCardOut): Cell {
  const b = c.breadth;
  if (b.n_200 === 0) return blank("No counting name has the 200 bars the reading needs");
  return {
    text: `${b.above_200}/${b.n_200}`,
    title: `${b.above_200} of ${b.n_200} counting names are above their 200-day average (${Math.round((b.above_200 / b.n_200) * 100)}%)`,
  };
}

/** The keys part: how many of the two keys the recorded call in effect has turned. */
export function keysCell(c: LeaderboardCardOut): Cell {
  const k = c.keys;
  if (k.turned === null) return blank("No recorded call by the as-of");
  const off = k.turned === 1 ? (k.conviction ? "confirmation off" : "conviction off") : null;
  const words =
    c.state === "managing"
      ? "Managing counts as 2 of 2"
      : k.turned === 2
        ? "both keys turned"
        : k.turned === 0
          ? "neither key turned"
          : `${k.conviction ? "conviction" : "confirmation"} turned, ${off}`;
  return {
    text: `${k.turned} of 2`,
    note: off ?? undefined,
    title: `keys ${k.turned} of 2 on the recorded call of ${c.recorded_asof ?? "the night in effect"}: ${words}`,
  };
}

/** The adoption part: the slope of the documents mentioning the card's SIGNAL terms, the recent months against the
 *  months before; or why the part has no reading. */
export function adoptionCell(c: LeaderboardCardOut, ctx: Ctx): Cell {
  const a = c.adoption;
  const recent = fmtSpan(a.recent_from, a.recent_to);
  const prior = fmtSpan(a.prior_from, a.prior_to);
  if (a.slope_pct !== null) {
    return {
      text: fmtPct(a.slope_pct, 0),
      title:
        `Documents mentioning the card's SIGNAL terms: ${a.recent_docs?.toLocaleString("en-US")} in ${recent} against ` +
        `${a.prior_docs?.toLocaleString("en-US")} in ${prior} (the last ${ctx.recent_months} closed months against the ${ctx.prior_months} before). ` +
        `${plural(a.terms_counted, "term", "terms")} of ${a.terms} counted; a document matching two terms counts twice`,
    };
  }
  if (a.gap === "no_base") {
    return {
      text: "no base",
      gray: true,
      title: `No documents mention the card's terms in ${prior}, so there is no percent to state (${a.recent_docs ?? 0} in ${recent})`,
    };
  }
  if (a.gap === "no_terms") return { text: "no terms", gray: true, title: "The card has no SIGNAL term to count" };
  return {
    text: "not counted yet",
    gray: true,
    title: `Fewer than six closed months of counts are stored for the card's terms (${a.terms_counted} of ${a.terms} terms have ${recent} and ${prior})`,
  };
}

/** The insiders part: distinct open-market buyers in 30 days, summed across the counting names. */
export function insidersCell(c: LeaderboardCardOut): Cell {
  const i = c.insiders;
  if (i.buyers === null) return blank("No counting name has a Form 4 reading");
  const who = i.names.length ? `: ${i.names.map((n) => `${n.name} ${n.buyers}`).join(", ")}` : "";
  return {
    text: String(i.buyers),
    title: `Distinct open-market buyers in the last 30 days, summed across the ${plural(i.read, "counting name", "counting names")} with a Form 4 reading${who}`,
  };
}

/** Names priced / counting, with the flagged ones as a quiet note. */
export function namesCell(c: LeaderboardCardOut): Cell {
  const flagged = c.flagged > 0 ? `${c.flagged} flagged` : undefined;
  return {
    text: `${c.priced}/${c.counting}`,
    note: flagged,
    title:
      `${c.priced} of ${plural(c.counting, "counting name", "counting names")} have a price at the as-of (Context and Bench never count)` +
      (c.flagged > 0 ? `; ${c.flagged} left out of every price reading for a flagged price history` : ""),
  };
}

/** Counting names whose relative strength is at a 13-week high, of those with a reading. */
export function rsCell(c: LeaderboardCardOut): Cell {
  if (c.rs_n === 0) return blank("No counting name has a 13-week relative-strength reading");
  return {
    text: `${c.rs_leaders}/${c.rs_n}`,
    title: `${c.rs_leaders} of ${c.rs_n} counting names have relative strength at a 13-week high`,
  };
}

export const CAP_TIERS = ["nano", "micro", "small", "mid", "large"] as const;
export type CapKey = (typeof CAP_TIERS)[number];

/** One cap band's count of counting names (approximate cap at the as-of), zero drawn quiet. */
export function capCell(c: LeaderboardCardOut, tier: CapKey): Cell {
  const n = c.cap_mix[tier];
  const rest = c.cap_mix.blank > 0 ? `; ${plural(c.cap_mix.blank, "counting name", "counting names")} with no cap` : "";
  return { text: String(n), gray: n === 0, title: `${plural(n, "counting name", "counting names")} in the ${tier} band (approximate cap)${rest}` };
}

export function armedCell(c: LeaderboardCardOut): Cell {
  if (c.armed_names === null) return blank("Not an Armed card");
  return { text: String(c.armed_names), title: `${plural(c.armed_names, "armed name", "armed names")} on the recorded call` };
}

// --- the score, in words -----------------------------------------------------------------------------------------

/** The score: 0 to 100, or "—" for a card missing a part. */
export const scoreText = (s: Score | undefined) => (s?.score == null ? BLANK : String(Math.round(s.score)));

const p2 = (v: number | null) => (v === null ? BLANK : v.toFixed(2));

/** The five parts for the hover, or why there is no score. */
export function scoreTitle(s: Score | undefined): string {
  if (!s) return "No score";
  const parts = PARTS.map((p) => `${p} ${p2(s[p])}`).join(" · ");
  if (s.score !== null) return parts;
  const missing = PARTS.filter((p) => s[p] === null);
  return `No score: no reading for ${missing.join(", ")} (${parts})`;
}

export const FRAMING = "A rank of what the data shows about each card's names and record, by the formula below. It is not a judgment of any thesis.";

export const FORMULA =
  "Score (stated formula): the mean of five percentiles, shown 0 to 100, each among the cards shown that have the part. " +
  "tape: a higher median 30-day return of the card's priced counting names, minus IWM's, ranks higher. " +
  "breadth: a higher share of the counting names above their 50-day average ranks higher. " +
  "keys: more keys turned on the recorded call ranks higher (Managing counts as 2). " +
  "adoption: a higher slope in the documents mentioning the card's SIGNAL terms, the last three closed months against the three before, ranks higher. " +
  "insiders: more distinct open-market buyers across the counting names in 30 days ranks higher. " +
  "Equal weights. A card missing a part reads — and sorts last. Ties share a percentile. The score compares the cards shown " +
  "with each other, so a filter changes it; it is not a score across days, and it feeds nothing.";

export const LIMITS: readonly string[] = [
  "The score is relative to the cards shown and means nothing across days.",
  "The parts are display readings and the record, never call inputs; a high score is not a call.",
  "The adoption part counts documents matching the card's current SIGNAL terms (a document matching two terms counts twice) on a weekly-refreshed table, and is missing for a card with fewer than six closed months or no documents in the earlier three.",
  "Medians are over priced counting names; a card with fewer priced names than the floor has no tape part.",
  "A name with a flagged price history is left out of every price reading; its Form 4 count is not one, and stays.",
];

// --- the filters -------------------------------------------------------------------------------------------------

export type Filters = {
  /** the states shown; empty = every state */
  states: readonly StateKey[];
  /** only cards whose adoption part has a counted term (six closed months stored) */
  adoptionOnly: boolean;
  /** a case-insensitive piece of the card's name */
  search: string;
};
export const DEFAULT_FILTERS: Filters = { states: [], adoptionOnly: false, search: "" };

export const filtersActive = (f: Filters) => f.states.length > 0 || f.adoptionOnly || f.search.trim() !== "";

/** The card has at least one SIGNAL term whose six closed months are all stored. */
export const hasAdoptionMonths = (c: LeaderboardCardOut) => c.adoption.terms_counted > 0;

export function cardMatches(c: LeaderboardCardOut, f: Filters): boolean {
  if (f.states.length && !f.states.includes(stateOf(c))) return false;
  if (f.adoptionOnly && !hasAdoptionMonths(c)) return false;
  const q = f.search.trim().toLocaleLowerCase();
  if (q && !c.thesis_name.toLocaleLowerCase().includes(q)) return false;
  return true;
}

/** The controls that can discriminate among the cards on offer (a chip every card shares carries no information and
 *  does not render). */
export function filterControls(cards: readonly LeaderboardCardOut[]) {
  const present = new Set(cards.map(stateOf));
  const states = STATE_ORDER.filter((s) => present.has(s));
  const withAdoption = cards.filter(hasAdoptionMonths).length;
  return {
    states: states.length > 1 ? states : [],
    adoption: withAdoption > 0 && withAdoption < cards.length,
    search: cards.length > 1,
  };
}

// --- the sorts ---------------------------------------------------------------------------------------------------

export type SortKey =
  | "card"
  | "state"
  | "score"
  | "tape"
  | "breadth"
  | "above200"
  | "keys"
  | "adoption"
  | "insiders"
  | "names"
  | "ret7"
  | "rs"
  | CapKey
  | "armed";
export type Sort = { key: SortKey; dir: "asc" | "desc" };
/** The page's default: score, descending. */
export const DEFAULT_SORT: Sort = { key: "score", dir: "desc" };

const TEXT_KEYS: readonly SortKey[] = ["card", "state"];
/** A column's first direction: text A to Z (a state in lifecycle order), a figure biggest first. */
export const firstDir = (key: SortKey): Sort["dir"] => (TEXT_KEYS.includes(key) ? "asc" : "desc");

/** A click on a column: the same column flips its direction, another starts in its own first direction. */
export function nextSort(cur: Sort, key: SortKey): Sort {
  if (cur.key === key) return { key, dir: cur.dir === "asc" ? "desc" : "asc" };
  return { key, dir: firstDir(key) };
}

/** A card's value in a column: a string or number to compare; `null` = blank (always last, whatever the
 *  direction: a blank never leads and is never read as zero). */
export function sortValue(c: LeaderboardCardOut, key: SortKey, score: Score | undefined): string | number | null {
  switch (key) {
    case "card":
      return c.thesis_name.toLocaleLowerCase();
    case "state": {
      const i = STATE_ORDER.indexOf(stateOf(c));
      return stateOf(c) === "none" ? null : i;
    }
    case "score":
      return score?.score ?? null;
    case "tape":
      return c.tape.value;
    case "breadth":
      return c.breadth.pct;
    case "above200":
      return c.breadth.n_200 > 0 ? c.breadth.above_200 / c.breadth.n_200 : null;
    case "keys":
      return c.keys.turned;
    case "adoption":
      return c.adoption.slope_pct;
    case "insiders":
      return c.insiders.buyers;
    case "names":
      return c.counting;
    case "ret7":
      return c.ret_7d.median;
    case "rs":
      return c.rs_n > 0 ? c.rs_leaders : null;
    case "nano":
    case "micro":
    case "small":
    case "mid":
    case "large":
      return c.cap_mix[key];
    case "armed":
      return c.armed_names;
  }
}

/** The cards in the sort's order; a blank last in either direction; ties by name, then id. */
export function sortCards(cards: readonly LeaderboardCardOut[], sort: Sort, scores: ReadonlyMap<string, Score>): LeaderboardCardOut[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  const val = (c: LeaderboardCardOut) => sortValue(c, sort.key, scores.get(c.thesis_id));
  return [...cards].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    if (x === null && y !== null) return 1;
    if (y === null && x !== null) return -1;
    if (x !== null && y !== null && x !== y) {
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      if (cmp !== 0) return sign * cmp;
    }
    return a.thesis_name.toLocaleLowerCase().localeCompare(b.thesis_name.toLocaleLowerCase()) || a.thesis_id.localeCompare(b.thesis_id);
  });
}

// --- the work lines ----------------------------------------------------------------------------------------------

/** How many of the cards shown have no reading for each part. */
export function missingParts(cards: readonly LeaderboardCardOut[], scores: ReadonlyMap<string, Score>): Record<Part, number> {
  const n: Record<Part, number> = { tape: 0, breadth: 0, keys: 0, adoption: 0, insiders: 0 };
  for (const c of cards) {
    const s = scores.get(c.thesis_id);
    if (!s) continue;
    for (const p of PARTS) if (s[p] === null) n[p] += 1;
  }
  return n;
}

/** The "show the work" lines: the as-of and clock, the cards counted / scored / missing a part, the names read and
 *  flagged, the adoption coverage, the formula's dials and the cache. */
export function workLines(data: LeaderboardOut, shown: readonly LeaderboardCardOut[], scores: ReadonlyMap<string, Score>): string[] {
  const clock = data.clock === "public" ? "the public clock: what was public by the end of that date" : "the live read";
  const scored = shown.filter((c) => scores.get(c.thesis_id)?.score != null).length;
  const missing = missingParts(shown, scores);
  const gaps = PARTS.filter((p) => missing[p] > 0).map((p) => `${p} ${missing[p]}`);
  const cards =
    `${plural(data.cards_counted, "card", "cards")} counted (${data.live_cards} live` +
    (data.cards_not_yet_created ? `; ${data.cards_not_yet_created} created after the as-of left out` : "") +
    `) · ${shown.length} shown · ${scored} scored · ${shown.length - scored} without a score` +
    (gaps.length ? ` (a part has no reading: ${gaps.join(" · ")})` : "");
  const read = data.names - data.names_flagged;
  const names =
    `${plural(data.names, "counting name", "counting names")} across the cards · ${read} read for price · ${data.names_flagged} flagged` +
    ` · Context and Bench never counted (${plural(data.silent_rows_left_out, "row", "rows")} left out)` +
    (data.unresolved_rows ? ` · ${plural(data.unresolved_rows, "row", "rows")} with no resolved security, not read` : "");
  const withTerms = shown.filter(hasAdoptionMonths).length;
  const gap = (g: string) => shown.filter((c) => !hasAdoptionMonths(c) && c.adoption.gap === g).length;
  const adoption =
    `adoption: ${withTerms} ${withTerms === 1 ? "card" : "cards"} with six closed months of term counts, ${shown.length - withTerms} without ` +
    `(not counted yet ${gap("not_counted_yet")} · no signal terms ${gap("no_terms")})`;
  const recon = shown.filter((c) => c.reconstructed).length;
  const lines = [
    `as of ${data.asof}, read on ${clock}`,
    cards,
    names,
    adoption,
    recon ? `${plural(recon, "card", "cards")} whose recorded state was rebuilt by the backfill (R): reported, never scored` : null,
    data.roster_fallback_cards
      ? `${plural(data.roster_fallback_cards, "card", "cards")} with no roster snapshot by then: today's roster stands in`
      : null,
    `${data.benchmark} 30-day return ${fmtPct(data.iwm_ret_30d)} · a median needs ${data.min_priced} priced names · adoption: the last ${data.recent_months} closed months against the ${data.prior_months} before · equal weights`,
    `${data.cache === "hit" ? "served from the cache" : "computed"} · compute ${data.compute_ms.toLocaleString("en-US")} ms`,
  ];
  return lines.filter((l): l is string => l !== null);
}
