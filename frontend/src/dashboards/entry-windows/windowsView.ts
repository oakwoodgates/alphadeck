import { leftOutWords } from "../FlaggedNote";
import type {
  EntryWindowsCatalystOut,
  EntryWindowsMemberClockOut,
  EntryWindowsMemberOut,
  EntryWindowsOut,
  EntryWindowsRowOut,
  EntryWindowsSinceOut,
  EntryWindowsTriggerOut,
} from "./useEntryWindows";

// The Entry Windows' words and orders (docs/dashboards/entry-windows.md): PURE, so the wording is testable without
// a DOM. Every number is computed server-side; this only phrases them, in neutral terms: it shows where the as-of
// sits and what the closes did, and never says "early", "late" or "chase".

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** "Oct 28" (a date without its year: the page's axis and rows sit within one run of weeks or months). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** A signed percent with one decimal ("+3.2%", "-1.4%"); a value that rounds to zero reads "0.0%". */
export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  const r = Math.round(v * 10) / 10;
  return r === 0 ? "0.0%" : `${r > 0 ? "+" : ""}${r.toFixed(1)}%`;
}

/** The sign tone of a return, from the value as shown. */
export function tone(v: number | null | undefined): "pos" | "neg" | "" {
  if (v == null) return "";
  const r = Math.round(v * 10) / 10;
  return r > 0 ? "pos" : r < 0 ? "neg" : "";
}

// --- the figures -------------------------------------------------------------------------------------------------

/** Sessions into the window: the IWM sessions after the arm night through the as-of. */
export const intoWords = (row: EntryWindowsRowOut): string =>
  row.sessions_into == null ? "—" : row.sessions_into.toLocaleString("en-US");

/** One entry clock in words: sessions left while it is open, "closed" once no session of it is left, then "closed N
 *  sessions ago". F3: a window is open only while the session after the as-of is inside it, so an open one has at
 *  least one session left, and its last night names that session: "1 session left: Oct 28" (`lastSession`). */
function clockWords(
  armUntil: string | null,
  left: number | null,
  closedAgo: number | null,
  lastSession: string | null,
): string {
  if (armUntil == null) return "no entry clock";
  if (left === 1 && lastSession != null) return `1 session left: ${fmtDay(lastSession)}`;
  if (left != null) return plural(left, "session left", "sessions left");
  if (closedAgo == null) return "—";
  if (closedAgo === 0) return "entry window closed";
  return `entry window closed ${plural(closedAgo, "session", "sessions")} ago`;
}

/** The entry window's cell: the CARD's clock (the headline name's `arm_until`). */
export const leftWords = (row: EntryWindowsRowOut): string =>
  clockWords(row.arm_until, row.sessions_left, row.closed_ago, row.last_session);

const tickerOf = (c: EntryWindowsMemberClockOut) => c.ticker ?? "?";

/** The quiet lines under the entry figure: one per armed name whose own entry window is closing and whose clock is
 *  not the card's own (the same `arm_until` is the figure above, already). "BBB · 2 sessions left · to Oct 29"; on the
 *  last night the session is named instead of the window's end: "BBB · 1 session left: Oct 28". */
export function memberEntryLines(row: EntryWindowsRowOut): string[] {
  return row.member_clocks
    .filter((c) => c.entry_window_closing && c.arm_until !== row.arm_until)
    .map((c) => {
      const words = clockWords(c.arm_until, c.sessions_left, c.closed_ago, c.last_session);
      const named = c.sessions_left === 1 && c.last_session != null;
      return `${tickerOf(c)} · ${words}${c.arm_until && !named ? ` · to ${fmtDay(c.arm_until)}` : ""}`;
    });
}

/** Does the card's own hold clock (the headline name's) say "hold clock closing"? */
export const cardHoldClosing = (row: EntryWindowsRowOut): boolean => row.member_clocks.some((c) => c.headline && c.lapsing);

/** The quiet lines under the hold figure: one per armed name whose own hold clock is closing, unless the card's
 *  figure already says so. "BBB · hold clock closing". */
export function memberHoldLines(row: EntryWindowsRowOut): string[] {
  if (cardHoldClosing(row)) return [];
  return row.member_clocks.filter((c) => c.lapsing).map((c) => `${tickerOf(c)} · ${HOLD_TAG}`);
}

/** Days to the hold horizon, or why there is none. A COUNTDOWN: the server counts it from the action day on a live view
 *  and from the as-of on a past one (F2), so a horizon that ends on the origin day reads "today". The horizon's date
 *  rides under the figure on every view (`to Dec 3`), so a past view needs no date added. */
export function holdWords(row: EntryWindowsRowOut): string {
  if (row.exit_by == null) return "no hold clock";
  if (row.hold_days == null) return "—";
  if (row.hold_days < 0) return "hold horizon passed";
  if (row.hold_days === 0) return "today";
  return plural(row.hold_days, "day", "days");
}

export const HOLD_TAG = "hold clock closing";
export const CLOSING_TITLE =
  "The accent marks any armed name's entry window at 2 trading sessions or fewer (Mon-Fri days; a market holiday is not a session). A name whose window differs from the card's is named under the figure";

export type SinceCell = {
  /** The median over the priced names, "—" with none. */
  main: string;
  tone: "pos" | "neg" | "";
  /** "median of 3" */
  basis: string;
  /** "2 of 3 above the arm-night close" */
  above: string | null;
  iwm: string;
  spy: string;
  /** "1 flagged, 1 without a bar" — the names counted out of the median. */
  unpriced: string | null;
};

export function sinceCell(s: EntryWindowsSinceOut): SinceCell {
  const unpriced = [
    s.flagged > 0 ? `${s.flagged} flagged` : null,
    s.no_bar > 0 ? `${s.no_bar} without a bar` : null,
  ].filter(Boolean);
  return {
    main: fmtPct(s.median),
    tone: tone(s.median),
    basis: s.priced === 0 ? "no priced name" : `median of ${s.priced}`,
    above: s.priced === 0 ? null : `${s.above} of ${s.priced} above the arm-night close`,
    iwm: `IWM ${fmtPct(s.iwm)}`,
    spy: `SPY ${fmtPct(s.spy)}`,
    unpriced: unpriced.length > 0 ? `${unpriced.join(", ")} (of ${s.names})` : null,
  };
}

// --- the fold and the hovers -------------------------------------------------------------------------------------

export const kindWords = (kind: string) => kind.replace(/_/g, " ");

/** "technical breakout · core · 8-day breakout · fired Oct 20" */
export function triggerWords(t: EntryWindowsTriggerOut): string {
  return [
    kindWords(t.kind),
    t.grade,
    t.label,
    t.event_date ? `fired ${fmtDay(t.event_date)}` : "no fire date stated",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** A member's return since its own arm night, or why there is none. */
export function memberSinceWords(m: EntryWindowsMemberOut): string {
  if (m.flag) return leftOutWords(m.flag);
  if (m.since == null) return "no bar since its arm night";
  const side = m.above ? "above" : "not above";
  return `${fmtPct(m.since)} from the ${fmtDay(m.entry_on)} close · ${side} its arm-night close`;
}

export function catalystWords(c: EntryWindowsCatalystOut): string {
  return [fmtDay(c.when_date), c.label, c.kind, c.past ? null : "still ahead"].filter(Boolean).join(" · ");
}

/** The one readout line for a row: where the as-of sits in its two clocks, in words. */
export function rowWords(row: EntryWindowsRowOut): string {
  const arm = `${row.thesis_name}: ${row.state === "managing" ? "Managing; armed" : "Armed"} since ${fmtDay(row.arm_night)}${row.reconstructed ? " (a reconstructed night)" : ""}`;
  const entry =
    row.arm_until == null ? "no entry clock" : `entry window to ${fmtDay(row.arm_until)}: ${leftWords(row)}`;
  const hold = row.exit_by == null ? "no hold clock" : `hold horizon to ${fmtDay(row.exit_by)}: ${holdWords(row)}`;
  const into = row.sessions_into == null ? null : `${plural(row.sessions_into, "session", "sessions")} in`;
  return [arm, into, entry, hold].filter(Boolean).join(" · ");
}

// --- the order ---------------------------------------------------------------------------------------------------

export type SortKey = "left" | "armed" | "hold" | "since";

export const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "left", label: "Sessions left to arm_until" },
  { key: "armed", label: "Arm night, newest first" },
  { key: "hold", label: "Days to exit_by" },
  { key: "since", label: "Median since the arm night" },
];
export const DEFAULT_SORT: SortKey = "left";

const byName = (a: EntryWindowsRowOut, b: EntryWindowsRowOut) =>
  a.thesis_name.localeCompare(b.thesis_name, "en", { sensitivity: "base" }) ||
  (a.thesis_id < b.thesis_id ? -1 : a.thesis_id > b.thesis_id ? 1 : 0);

/** A clock's rank: a clock still running first (soonest end first), then a finished one (most recently ended
 *  first), then no clock at all. `open` = what remains while running, `shut` = how long since it ended (both
 *  non-negative); a null pair is no clock. */
function clockRank(open: number | null, shut: number | null): [number, number] {
  if (open != null) return [0, open];
  if (shut != null) return [1, shut];
  return [2, 0];
}

const entryRank = (r: EntryWindowsRowOut) => clockRank(r.sessions_left, r.closed_ago);
const holdRank = (r: EntryWindowsRowOut) =>
  clockRank(r.hold_days != null && r.hold_days >= 0 ? r.hold_days : null, r.hold_days != null && r.hold_days < 0 ? -r.hold_days : null);

/** The rows in the chosen order, a stable copy. Every order ends in the card's name. */
export function sortRows(rows: readonly EntryWindowsRowOut[], key: SortKey): EntryWindowsRowOut[] {
  const cmp = (a: EntryWindowsRowOut, b: EntryWindowsRowOut): number => {
    if (key === "armed") return (a.arm_night < b.arm_night ? 1 : a.arm_night > b.arm_night ? -1 : 0) || byName(a, b);
    if (key === "since") {
      const x = a.since.median;
      const y = b.since.median;
      if (x == null || y == null) return (x == null ? 1 : 0) - (y == null ? 1 : 0) || byName(a, b);
      return y - x || byName(a, b);
    }
    const [ra, rb] = key === "left" ? [entryRank(a), entryRank(b)] : [holdRank(a), holdRank(b)];
    return ra[0] - rb[0] || ra[1] - rb[1] || byName(a, b);
  };
  return [...rows].sort(cmp);
}

/** The rows on the page: every Armed row, and the Managing ones only behind the toggle. */
export const shownRows = (rows: readonly EntryWindowsRowOut[], showManaging: boolean) =>
  rows.filter((r) => r.state === "armed" || showManaging);

// --- the quiet "show the work" line ------------------------------------------------------------------------------

function cacheWords(d: EntryWindowsOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

const CALENDAR: Record<EntryWindowsOut["session_source"], string> = {
  benchmarks: "IWM's bars",
  companies: "the armed names' bars (IWM has none)",
  none: "no bars",
};

export function workLine(d: EntryWindowsOut, showManaging: boolean): string {
  const c = d.counts;
  const cards =
    c.cards_not_yet_created > 0
      ? `${plural(c.cards_counted, "card", "cards")} counted (${c.cards_not_yet_created} created after the as-of left out)`
      : `${plural(c.cards_counted, "card", "cards")} counted`;
  const states = [
    plural(c.armed, "Armed row", "Armed rows"),
    showManaging ? `${c.managing} Managing shown` : `${c.managing} Managing hidden`,
    ...(c.managing_without_arm_night > 0
      ? [`${c.managing_without_arm_night} Managing with no Armed night recorded (no window to draw)`]
      : []),
    `${c.warming} Warming`,
    `${c.incubating} Incubating`,
    `${c.no_call} no call yet`,
  ].join(", ");
  const names = `${plural(c.armed_names, "armed name", "armed names")}: ${c.priced_names} priced, ${c.unpriced_names} unpriced (flagged or no bar since the arm night, counted and left out of the medians)`;
  return [
    d.clock === "public"
      ? `As of ${fmtDay(d.asof)}: what was public by the end of that date`
      : `As of ${fmtDay(d.asof)}: the live read`,
    cards,
    states,
    names,
    ...(c.reconstructed_arm_nights > 0
      ? [`${plural(c.reconstructed_arm_nights, "arm night", "arm nights")} reconstructed (reported, never scored)`]
      : []),
    `sessions counted on ${CALENDAR[d.session_source]}${d.calendar_last ? ` to ${fmtDay(d.calendar_last)}` : ""}`,
    "entry rule: the first close on or after the arm night, as traded",
    cacheWords(d),
  ].join(" · ");
}
