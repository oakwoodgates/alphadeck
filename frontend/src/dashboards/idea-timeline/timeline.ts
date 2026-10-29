import { STATE_CLASS, STATE_LABEL, verdictLabel } from "../../util/format";
import type {
  IdeaTimelineCatalystOut,
  IdeaTimelineDecisionOut,
  IdeaTimelineHatchOut,
  IdeaTimelineOut,
  IdeaTimelineRowOut,
  IdeaTimelineSegmentOut,
} from "./useIdeaTimeline";

// The Idea Timeline's pure helpers (docs/dashboards/idea-timeline.md): day arithmetic on ISO dates, the zoom
// window, the sorts and the state filter, and every hover's wording. Every rule about the RECORD (segments,
// the last-row-wins pick, the active excludes, the axis cap) is computed server-side; these only lay the
// payload out on screen.

const DAY_MS = 86_400_000;

/** An ISO date (YYYY-MM-DD) as a whole UTC day number, so day arithmetic never meets a timezone. */
export const dayNum = (iso: string): number => Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY_MS);

/** "Oct 7, 2026": the hovers' date (a year on every one, so an old run never reads as this year's). */
export function fmtDay(iso: string): string {
  return new Date(dayNum(iso) * DAY_MS).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// --- the zoom window ---------------------------------------------------------------------------------------

export type Zoom = "30" | "90" | "all";
export const ZOOMS: readonly { key: Zoom; label: string; days: number | null }[] = [
  { key: "30", label: "30 days", days: 30 },
  { key: "90", label: "90 days", days: 90 },
  { key: "all", label: "All", days: null },
];

/** The visible window, as day numbers, `end` exclusive. "All" is the server's axis; a preset of N days is
 *  today − N to today + N (the future tail also capped by the server's axis end). One day of room past the
 *  last date keeps a run that started tonight (zero days long) on screen at the today line. */
export type View = { start: number; end: number; today: number };

export function viewFor(zoom: Zoom, data: Pick<IdeaTimelineOut, "today" | "axis_start" | "axis_end">): View {
  const today = dayNum(data.today);
  const axisEnd = Math.max(dayNum(data.axis_end), today);
  const days = ZOOMS.find((z) => z.key === zoom)?.days ?? null;
  const start = days === null ? Math.min(dayNum(data.axis_start), today) : today - days;
  const last = days === null ? axisEnd : Math.min(axisEnd, today + days);
  return { start, end: Math.max(last, start) + 1, today };
}

/** A day's position on the view, as a percent of the track. */
export const pct = (v: View, day: number): number => ((day - v.start) / (v.end - v.start)) * 100;

export const inView = (v: View, day: number): boolean => day >= v.start && day < v.end;

/** A `[from, to)` span clipped to the view as CSS left/width, or null when it lies outside it. */
export function spanStyle(v: View, fromIso: string, toIso: string): { left: string; width: string } | null {
  const from = Math.max(dayNum(fromIso), v.start);
  const to = Math.min(dayNum(toIso), v.end);
  if (from > to || from >= v.end) return null;
  return { left: `${pct(v, from)}%`, width: `${pct(v, to) - pct(v, from)}%` };
}

export type Tick = { day: number; left: number; label: string };

/** A tick on the first of every month inside the view; January carries its year. */
export function monthTicks(v: View): Tick[] {
  const out: Tick[] = [];
  const first = new Date(v.start * DAY_MS);
  let y = first.getUTCFullYear();
  let m = first.getUTCMonth() + 1; // the first month boundary after the view's start
  for (;;) {
    if (m > 11) {
      y += 1;
      m = 0;
    }
    const day = Math.round(Date.UTC(y, m, 1) / DAY_MS);
    if (day >= v.end) break;
    const month = new Date(day * DAY_MS).toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
    out.push({ day, left: pct(v, day), label: m === 0 ? `${month} ${y}` : month });
    m += 1;
  }
  return out;
}

/** The markers on a row whose date falls past the visible end (counted on the row, never dropped). */
export function markersLater(row: IdeaTimelineRowOut, v: View): number {
  const dates = [
    ...row.catalysts.map((c) => c.when_date),
    ...row.decisions.map((d) => d.decision_date),
    ...[row.exit_by, row.arm_until].filter((d): d is string => d !== null),
  ];
  return dates.filter((d) => dayNum(d) >= v.end).length;
}

// --- states, the filter and the sorts -----------------------------------------------------------------------

/** The four states in the default sort's order (armed first), with the Board's classes and labels, plus
 *  "no call yet" for a card the nightly has not recorded. */
const ORDER = ["armed", "warming", "incubating", "managing"] as const;
export const STATES: readonly { key: string; cls: string; label: string }[] = [
  ...ORDER.map((key) => ({ key, cls: STATE_CLASS[key], label: STATE_LABEL[key] })),
  { key: "none", cls: "none", label: "No call yet" },
];

export const stateKey = (state: string | null): string => state ?? "none";
export const stateCls = (state: string | null): string =>
  STATES.find((s) => s.key === stateKey(state))?.cls ?? "other";
export const stateLabel = (state: string | null): string =>
  STATES.find((s) => s.key === stateKey(state))?.label ?? (state as string);

export type SortKey = "state" | "days_in_state" | "days_armed" | "first_recorded" | "name";
export const SORTS: readonly { key: SortKey; label: string }[] = [
  { key: "state", label: "State, then days in state" },
  { key: "days_in_state", label: "Days in state" },
  { key: "days_armed", label: "Days armed" },
  { key: "first_recorded", label: "First recorded" },
  { key: "name", label: "Name" },
];

const rank = (state: string | null): number => {
  const i = STATES.findIndex((s) => s.key === stateKey(state));
  return i === -1 ? STATES.length - 1 : i; // an unknown state sorts with "no call yet"
};
const byName = (a: IdeaTimelineRowOut, b: IdeaTimelineRowOut) =>
  a.thesis_name.localeCompare(b.thesis_name, undefined, { sensitivity: "base" });
/** Longest / most first, a missing value last. */
const desc = (a: number | null, b: number | null) =>
  a === b ? 0 : a === null ? 1 : b === null ? -1 : b - a;

export function sortRows(rows: readonly IdeaTimelineRowOut[], key: SortKey): IdeaTimelineRowOut[] {
  const cmp: Record<SortKey, (a: IdeaTimelineRowOut, b: IdeaTimelineRowOut) => number> = {
    state: (a, b) =>
      rank(a.current_state) - rank(b.current_state) || desc(a.days_in_state, b.days_in_state),
    days_in_state: (a, b) => desc(a.days_in_state, b.days_in_state),
    days_armed: (a, b) => desc(a.total_days_armed, b.total_days_armed),
    first_recorded: (a, b) =>
      a.first_recorded === b.first_recorded
        ? 0
        : a.first_recorded === null
          ? 1
          : b.first_recorded === null
            ? -1
            : a.first_recorded < b.first_recorded
              ? -1
              : 1,
    name: () => 0,
  };
  return [...rows].sort((a, b) => cmp[key](a, b) || byName(a, b));
}

/** Only the cards in the chosen states; none chosen = every card. */
export function filterRows(rows: readonly IdeaTimelineRowOut[], on: ReadonlySet<string>) {
  return on.size === 0 ? [...rows] : rows.filter((r) => on.has(stateKey(r.current_state)));
}

// --- the hovers ---------------------------------------------------------------------------------------------

const MAX_LISTED = 6;

function hatchLine(h: IdeaTimelineHatchOut): string {
  const when = `${fmtDay(h.start)} → ${fmtDay(h.end)} (${plural(h.nights, "night", "nights")})`;
  return h.kind === "reconstructed"
    ? `${when}: reconstructed: reported, never scored`
    : `${when}: set aside on the Scoreboard: ${h.reason ?? "no reason recorded"}`;
}

export function segmentTitle(s: IdeaTimelineSegmentOut): string {
  const lines = [
    `${stateLabel(s.state)} · verdict at start: ${verdictLabel(s.verdict)}`,
    `${fmtDay(s.start)} → ${s.current ? `today (${fmtDay(s.end)})` : fmtDay(s.end)} · ${plural(
      s.days,
      "day",
      "days",
    )} · ${plural(s.nights, "night", "nights")} recorded`,
  ];
  for (const m of s.multi_row_nights.slice(0, MAX_LISTED)) {
    lines.push(`${fmtDay(m.asof)}: ${m.rows} rows that night (the last one is read)`);
  }
  if (s.multi_row_nights.length > MAX_LISTED) {
    lines.push(`+${s.multi_row_nights.length - MAX_LISTED} more nights with several rows`);
  }
  lines.push(...s.hatches.map(hatchLine));
  return lines.join("\n");
}

export function catalystTitle(c: IdeaTimelineCatalystOut): string {
  const when = c.when_label ? `${fmtDay(c.when_date)} (${c.when_label})` : fmtDay(c.when_date);
  return [`Catalyst: ${c.label}`, when, c.kind, c.past ? "past" : null].filter(Boolean).join(" · ");
}

export const DECISION_GLYPH: Record<IdeaTimelineDecisionOut["action"], string> = {
  take: "▲",
  close: "▼",
  pass: "◇",
};

export function decisionTitle(d: IdeaTimelineDecisionOut): string {
  const name = d.ticker ?? (d.security_id ? "a name no longer on the card" : "the card");
  return [
    `Decision: ${d.action} · ${name} · ${fmtDay(d.decision_date)}`,
    d.reason,
    d.voided ? "voided (a later void row names it)" : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export const exitTitle = (iso: string) =>
  `Exit-by ${fmtDay(iso)} · the signal-validity horizon on the latest call (not a trade exit)`;

export const entryTitle = (iso: string) => `Entry window through ${fmtDay(iso)} · from the latest call`;

/** The quiet line that shows the work, every number off the payload. */
export function workLine(d: IdeaTimelineOut): string {
  const range =
    d.first_recorded && d.last_recorded
      ? `${fmtDay(d.first_recorded)} → ${fmtDay(d.last_recorded)}`
      : "no recorded nights yet";
  return [
    plural(d.live_cards, "live card", "live cards") +
      (d.cards_without_calls > 0 ? ` (${d.cards_without_calls} with no recorded call yet)` : ""),
    `${plural(d.recorded_calls, "recorded call", "recorded calls")} on ${plural(
      d.nights,
      "card-night",
      "card-nights",
    )}`,
    range,
    "a state holds from its night until the card's next row",
    `a night with several rows reads its last one (${plural(d.multi_row_nights, "night", "nights")})`,
    `hatched = reconstructed, reported, never scored (${plural(d.reconstructed_nights, "night", "nights")})`,
    `cross-hatched = set aside on the Scoreboard (${plural(d.excluded_nights, "night", "nights")})`,
    "the original record, not edition re-runs",
  ].join(" · ");
}
