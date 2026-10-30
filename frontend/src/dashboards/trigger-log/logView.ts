import { fmtDate } from "../../util/format";
import type {
  TriggerLogDays,
  TriggerLogOut,
  TriggerLogRole,
  TriggerLogRowOut,
  TriggerLogSourceOut,
} from "./useTriggerLog";

// The Trigger Log's pure view logic (docs/dashboards/trigger-log.md): the grouping (by week, kind or card), the
// filters and their per-chip counts, and the words the page uses. Every row and count is computed server-side;
// this file only slices and words them. There is NO aggregate here and none may be added (docs/DASHBOARDS.md
// rule 6: a median move "after insider triggers" is an event-outcome statistic, sliced by market-cap tier on the
// Track Record page; this page lists).

export type Row = TriggerLogRowOut;
export type Role = Row["role"];
export type GroupBy = "week" | "kind" | "card";
export type GradeKey = "flip" | "core" | "none";
/** The filter dimensions that are chips or a picker (the two toggles are not dimensions). */
export type Dim = "role" | "kind" | "grade" | "card";

export const GROUP_BYS: readonly { key: GroupBy; label: string }[] = [
  { key: "week", label: "By week" },
  { key: "kind", label: "By kind" },
  { key: "card", label: "By card" },
];

export const DAYS_OPTIONS: readonly { value: TriggerLogDays; label: string }[] = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 180, label: "180 days" },
];
export const DEFAULT_DAYS: TriggerLogDays = 90;

/** Which role(s) one read loads. Risk signals are the bulk of the record, so each choice is its own read and the
 *  entry triggers load first. */
export const ROLE_OPTIONS: readonly { value: TriggerLogRole; label: string; title: string }[] = [
  { value: "entry", label: "Entry triggers", title: "Load the entry triggers: the default" },
  { value: "risk", label: "Risk signals", title: "Load the risk signals: the bulk of the record, so they load on request" },
  { value: "both", label: "Both", title: "Load both roles together: the larger read" },
];
export const DEFAULT_ROLE: TriggerLogRole = "entry";

export const ROLE_LABEL: Record<Role, string> = { entry_trigger: "entry trigger", risk_signal: "risk signal" };
/** The role is a glyph, not a color: a filled diamond for an entry trigger, an open one for a risk signal. */
export const ROLE_GLYPH: Record<Role, string> = { entry_trigger: "◆", risk_signal: "◇" };
export const GRADE_LABEL: Record<GradeKey, string> = { flip: "flip", core: "core", none: "no grade" };
const GRADE_ORDER: readonly GradeKey[] = ["flip", "core", "none"];

export const RECONSTRUCTED_TITLE =
  "recorded on a reconstructed night: written by pipeline.backfill to fill a missed night, reported but never scored";

/** The fixed line under the work line: why no number summarizes these rows. */
export const NO_AGGREGATE = "no aggregate here: medians by tier are on the Track Record page";

export const LIMITS: readonly string[] = [
  "The record shows live triggers only: a trigger that fired and lapsed between two recorded nights never reached the log and is not here.",
  "The recorded night is when the system saw it, which can trail the fire date (the note says by how much).",
  "A trigger first recorded before the window is not listed, however live.",
  "One row per trigger: the same security, kind and fire date is one row however its label changed (hover a label to see how many times); the row shows the first night's label.",
  "As-traded closes, no dividends.",
];

export const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- filters -----------------------------------------------------------------------------------------------------

export type Filters = {
  roles: ReadonlySet<Role>;
  kinds: ReadonlySet<string>;
  grades: ReadonlySet<GradeKey>;
  /** one card's thesis_id, "" = every card */
  card: string;
  liveOnly: boolean;
  changedOnly: boolean;
};

export const DEFAULT_FILTERS: Filters = {
  roles: new Set(),
  kinds: new Set(),
  grades: new Set(),
  card: "",
  liveOnly: false,
  changedOnly: false,
};

export const gradeKey = (r: Row): GradeKey => r.grade ?? "none";

/** The card's recorded state moved on that night (against the previous recorded night's). A new card began; an
 *  unreadable night before says nothing: neither is a change. */
export const stateChanged = (r: Row): boolean => r.from_state != null && r.from_state !== r.to_state;

const dimValue: Record<Dim, (r: Row) => string> = {
  role: (r) => r.role,
  kind: (r) => r.kind,
  grade: gradeKey,
  card: (r) => r.thesis_id,
};

const chosen = (f: Filters, dim: Dim): ReadonlySet<string> | string =>
  dim === "role" ? f.roles : dim === "kind" ? f.kinds : dim === "grade" ? f.grades : f.card;

/** Does the row pass every filter except the one on `skip` (an empty chip set or "" = no filter on it)? */
function passes(r: Row, f: Filters, skip?: Dim): boolean {
  for (const dim of ["role", "kind", "grade", "card"] as const) {
    if (dim === skip) continue;
    const c = chosen(f, dim);
    if (typeof c === "string" ? c !== "" && c !== dimValue[dim](r) : c.size > 0 && !c.has(dimValue[dim](r))) {
      return false;
    }
  }
  if (f.liveOnly && r.live !== true) return false;
  if (f.changedOnly && !stateChanged(r)) return false;
  return true;
}

export const filterRows = (rows: readonly Row[], f: Filters): Row[] => rows.filter((r) => passes(r, f));

export function filtersActive(f: Filters): boolean {
  return f.roles.size > 0 || f.kinds.size > 0 || f.grades.size > 0 || f.card !== "" || f.liveOnly || f.changedOnly;
}

/** How many rows each value of `dim` would show with every OTHER filter applied: the number a chip restates. A
 *  value no row has is absent (never a zero for a chip that cannot exist). */
export function facetCounts(rows: readonly Row[], f: Filters, dim: Dim): Map<string, number> {
  const out = new Map<string, number>();
  for (const r of rows) {
    const v = dimValue[dim](r);
    if (!out.has(v)) out.set(v, 0);
    if (passes(r, f, dim)) out.set(v, (out.get(v) ?? 0) + 1);
  }
  return out;
}

export type Toggle = "liveOnly" | "changedOnly";

const toggleTest: Record<Toggle, (r: Row) => boolean> = {
  liveOnly: (r) => r.live === true,
  changedOnly: stateChanged,
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

/** The values of `dim` present anywhere in the payload, in the page's order: roles entry first, kinds A→Z,
 *  grades flip · core · none, cards A→Z by name. A dimension with fewer than two values renders no control
 *  (it cannot discriminate: WB#3). */
export function dimValues(rows: readonly Row[], dim: Dim): string[] {
  const seen = new Map<string, string>(); // value -> sort label
  for (const r of rows) {
    const v = dimValue[dim](r);
    if (!seen.has(v)) seen.set(v, dim === "card" ? r.thesis_name : v);
  }
  const values = [...seen.keys()];
  if (dim === "role") return values.sort((a, b) => (a === "entry_trigger" ? -1 : b === "entry_trigger" ? 1 : 0));
  if (dim === "grade") return values.sort((a, b) => GRADE_ORDER.indexOf(a as GradeKey) - GRADE_ORDER.indexOf(b as GradeKey));
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
  entry: number;
  risk: number;
};

export const kindWords = (kind: string) => kind.replace(/_/g, " ");

/** The hover on a label: the label itself, and how many times the record changed it after the first night (only
 *  when it did). */
export function labelTitle(r: Row): string {
  return r.label_changes > 0 ? `${r.label}\nlabel changed ${plural(r.label_changes, "time", "times")}` : r.label;
}

/** "Week of Oct 26": the Monday the server grouped on. */
export const weekLabel = (week: string) => `Week of ${fmtDate(week)}`;

/** The group's header counts, by role: "3 entry triggers · 1 risk signal" (a zero is left out). */
export function roleWords(entry: number, risk: number): string {
  return [
    entry > 0 ? plural(entry, "entry trigger", "entry triggers") : null,
    risk > 0 ? plural(risk, "risk signal", "risk signals") : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The rows grouped, each group's rows in the order given (the server's: the recorded night newest first, then
 *  ticker). Groups: weeks newest first, kinds and cards A→Z. */
export function groupRows(rows: readonly Row[], by: GroupBy): Group[] {
  const groups = new Map<string, Group>();
  for (const r of rows) {
    const key = by === "week" ? r.week : by === "kind" ? r.kind : r.thesis_id;
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        label: by === "week" ? weekLabel(r.week) : by === "kind" ? kindWords(r.kind) : r.thesis_name,
        thesisId: by === "card" ? r.thesis_id : null,
        rows: [],
        entry: 0,
        risk: 0,
      };
      groups.set(key, g);
    }
    g.rows.push(r);
    if (r.role === "entry_trigger") g.entry += 1;
    else g.risk += 1;
  }
  const out = [...groups.values()];
  if (by === "week") return out.sort((a, b) => (a.key < b.key ? 1 : a.key > b.key ? -1 : 0));
  return out.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }) || a.key.localeCompare(b.key));
}

// --- cell words --------------------------------------------------------------------------------------------------

/** What an entry trigger turns, in the cell's own words ("risk" for a risk signal). */
export function turnsWords(r: Row): string {
  if (r.role === "risk_signal") return "risk";
  return r.turns ?? "no key";
}

/** The note on a fire date that is earlier than the recorded night; null when there is none to make. */
export function fireNote(r: Row): string | null {
  const n = r.recorded_days_after;
  return n == null ? null : `recorded ${plural(n, "day", "days")} after`;
}

export type StatePair =
  | { kind: "new"; to: string }
  | { kind: "same"; to: string }
  | { kind: "moved"; from: string; to: string }
  | { kind: "unknown-before"; to: string };

/** The state cell: the card's recorded state that night beside the night before. "new card" when the recorded
 *  night is the card's first row; one state when it did not move; "unknown-before" when the row before could
 *  not be read (said, never guessed). */
export function statePair(r: Row): StatePair {
  if (r.new_card) return { kind: "new", to: r.to_state };
  if (r.from_state == null) return { kind: "unknown-before", to: r.to_state };
  if (r.from_state === r.to_state) return { kind: "same", to: r.to_state };
  return { kind: "moved", from: r.from_state, to: r.to_state };
}

export function statePairWords(p: StatePair): string {
  switch (p.kind) {
    case "new":
      return `new card · ${p.to}`;
    case "moved":
      return `${p.from} → ${p.to}`;
    default:
      return p.to;
  }
}

export const liveWords = (r: Row): string => (r.live === true ? "live" : r.live === false ? "off the card" : "—");

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

export type SourceView = {
  /** what the cell prints */
  text: string;
  /** a link, when one is known */
  href: string | null;
  /** the hover: the source, its reference and the computation's inputs, one line each */
  title: string;
};

/** One source as the cell shows it: an EDGAR index or a stored URL as a link, a price reference as the bar date in
 *  text, anything else as its kind; the detail lines on hover. */
export function sourceView(s: TriggerLogSourceOut): SourceView {
  const title = [`${s.source}${s.ref ? ` · ${s.ref}` : ""}`, ...s.detail].join("\n");
  if (s.url) return { text: s.url.includes("sec.gov") ? "EDGAR ↗" : "source ↗", href: s.url, title };
  if (s.bar_date) return { text: `bar ${fmtDate(s.bar_date)}`, href: null, title };
  return { text: s.source, href: null, title };
}

// --- the quiet "show the work" line ---------------------------------------------------------------------------

export function clockWords(data: TriggerLogOut): string {
  return data.clock === "public"
    ? "public clock: what was public by the end of that date"
    : "live read";
}

/** Which role the read holds and what the other role holds: "entry triggers loaded · 18,588 risk signals not
 *  loaded". With both loaded it is the rows by role. */
export function roleLoadedWords(data: TriggerLogOut): string[] {
  const c = data.counts;
  if (data.role === "both") return [roleWords(c.entry_rows, c.risk_rows)];
  const loaded = data.role === "entry" ? "entry triggers" : "risk signals";
  const [one, many] = data.role === "entry" ? ["risk signal", "risk signals"] : ["entry trigger", "entry triggers"];
  return [`${loaded} loaded`, c.other_role_rows > 0 ? `${plural(c.other_role_rows, one, many)} not loaded` : `no ${many} in the window`];
}

/** The empty state of a window that holds no row of the role loaded. */
export function emptyWords(data: TriggerLogOut): string {
  const what = data.role === "entry" ? "entry trigger" : data.role === "risk" ? "risk signal" : "trigger";
  return `No ${what} was first recorded in the last ${data.window_days} days (see the limits below).`;
}

/** The work, one quiet line per thought: what was read, what is listed, what the tape rests on. `shown` is the rows
 *  after the filters. */
export function workLines(data: TriggerLogOut, shown: number, filtering: boolean): string[] {
  const c = data.counts;
  const lines: string[] = [];
  lines.push(
    `As of ${fmtDate(data.asof)} (${clockWords(data)}) · window ${fmtDate(data.window_start)} → ${fmtDate(data.asof)} (${data.window_days} days)`,
  );
  lines.push(
    [
      `${plural(c.cards_counted, "card", "cards")} counted`,
      `${c.cards_with_nights} with a night in the window`,
      c.cards_quiet > 0 ? `${c.cards_quiet} quiet` : null,
      c.cards_no_call > 0 ? `${c.cards_no_call} with no call recorded yet` : null,
      c.not_yet_created > 0 ? `${plural(c.not_yet_created, "card", "cards")} created after this date left out` : null,
      c.roster_fallback > 0 ? `${plural(c.roster_fallback, "card", "cards")} on today's roster (no snapshot by then)` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  lines.push(
    [
      `${plural(c.nights_read, "night", "nights")} read`,
      c.nights_reconstructed > 0 ? `${c.nights_reconstructed} reconstructed (†)` : null,
      c.nights_unreadable > 0 ? `${c.nights_unreadable} unreadable, not listed` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  const total = data.rows.length;
  lines.push(
    [
      filtering ? `Showing ${shown.toLocaleString("en-US")} of ${plural(total, "trigger", "triggers")}` : `${plural(total, "trigger", "triggers")} listed`,
      ...roleLoadedWords(data),
      c.carried_live > 0
        ? `${plural(c.carried_live, "live trigger", "live triggers")} first recorded before the window (not listed)`
        : null,
      c.rows_no_ticker > 0 ? `${plural(c.rows_no_ticker, "row", "rows")} with no ticker` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  );
  const s = data.sessions;
  lines.push(
    [
      "Since = the name's as-traded return from the first close on or after the recorded night (the Scoreboard's entry rule) to the last close on or before the as-of, with IWM on the same two dates",
      s.count > 0 && s.first && s.last
        ? `sessions counted on ${s.source === "benchmarks" ? "IWM's" : `the ${s.source}'`} bar calendar (${plural(s.count, "session", "sessions")}, ${fmtDate(s.first)} → ${fmtDate(s.last)})`
        : "no session calendar: no bars at this as-of",
    ].join(" · "),
  );
  return lines;
}

/** The cache line: whether the read came from the shared dashboard cache and how long it took when computed. */
export const cacheWords = (data: TriggerLogOut) =>
  `${data.cache === "hit" ? "cache hit" : "computed"} · ${data.compute_ms.toLocaleString("en-US")} ms${data.cache === "hit" ? " when computed" : ""}`;
