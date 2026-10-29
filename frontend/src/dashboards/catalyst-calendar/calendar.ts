import { STATE_CLASS, STATE_LABEL } from "../../util/format";
import type {
  CatalystCalendarItemOut,
  CatalystCalendarOut,
  CatalystCalendarUndatedGroupOut,
} from "./useCatalystCalendar";

// The Catalyst Calendar's pure helpers (docs/dashboards/catalyst-calendar.md): the filters, the chips they are
// built from, and every date's wording. Every rule about the CALENDAR (the buckets, the week boundary, what
// counts as passed, the order) is computed server-side; these only filter and word what is on screen.

type Item = CatalystCalendarItemOut;
type Group = CatalystCalendarUndatedGroupOut;

const DAY_MS = 86_400_000;

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// --- dates --------------------------------------------------------------------------------------------------

const yearOf = (iso: string) => iso.slice(0, 4);

/** "Thu, Oct 29": a line's date, with its year when it is not today's year. */
export function fmtDay(iso: string, today: string): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`)).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(yearOf(iso) !== yearOf(today) ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
}

/** "Oct 29": a bucket's range, with its year when it is not today's year. */
export function fmtShort(iso: string, today: string): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(yearOf(iso) !== yearOf(today) ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
}

/** "Oct 29, 2026": today's date on the work line. */
export function fmtLong(iso: string): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** The day before an ISO date (a "later" bucket reads "after" the previous bucket's last day). */
export function dayBefore(iso: string): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) - DAY_MS).toISOString().slice(0, 10);
}

/** `days` = calendar days from today: "today", "3 days"; a passed one "4 days ago". */
export function daysAway(days: number): string {
  if (days === 0) return "today";
  return days > 0 ? plural(days, "day", "days") : `${plural(-days, "day", "days")} ago`;
}

// --- states and kinds ---------------------------------------------------------------------------------------

/** The four states in the Board's order, with its classes and labels, plus "no call yet" (shown "—") for a
 *  card the nightly has not recorded. */
const ORDER = ["armed", "warming", "incubating", "managing"] as const;
export const STATES: readonly { key: string; cls: string; label: string }[] = [
  ...ORDER.map((key) => ({ key, cls: STATE_CLASS[key], label: STATE_LABEL[key] })),
  { key: "none", cls: "none", label: "No call yet" },
];

export const stateKey = (state: string | null): string => state ?? "none";
export const stateCls = (state: string | null): string =>
  STATES.find((s) => s.key === stateKey(state))?.cls ?? "other";

/** A catalyst's kind as its filter key ("" = no kind). */
export const kindKey = (kind: string | null): string => kind ?? "";
export const kindLabel = (key: string): string => (key ? key.replace(/_/g, " ") : "no kind");

export type Chip = { key: string; label: string; count: number };

/** Every catalyst's kind on the page (upcoming, passed and undated): the deadlines are not catalysts. */
function allKinds(data: CatalystCalendarOut): (string | null)[] {
  return [
    ...data.buckets.flatMap((b) => b.items.filter((i) => i.item === "catalyst").map((i) => i.kind)),
    ...data.passed.map((i) => i.kind),
    ...data.undated_groups.flatMap((g) => g.catalysts.map((c) => c.kind)),
  ];
}

/** One chip per kind present, with how many catalysts carry it, most first ("no kind" last). */
export function kindChips(data: CatalystCalendarOut): Chip[] {
  const counts = new Map<string, number>();
  for (const kind of allKinds(data)) counts.set(kindKey(kind), (counts.get(kindKey(kind)) ?? 0) + 1);
  return [...counts.entries()]
    .map(([key, count]) => ({ key, label: kindLabel(key), count }))
    .sort(
      (a, b) =>
        Number(a.key === "") - Number(b.key === "") ||
        b.count - a.count ||
        a.label.localeCompare(b.label),
    );
}

/** One chip per state some card on the page holds, with how many such cards, in the Board's order. */
export function stateChips(data: CatalystCalendarOut): (Chip & { cls: string })[] {
  const cards = new Map<string, string>();
  for (const i of [...data.buckets.flatMap((b) => b.items), ...data.passed]) {
    cards.set(i.thesis_id, stateKey(i.state));
  }
  for (const g of data.undated_groups) cards.set(g.thesis_id, stateKey(g.state));
  const counts = new Map<string, number>();
  for (const key of cards.values()) counts.set(key, (counts.get(key) ?? 0) + 1);
  return STATES.filter((s) => counts.has(s.key)).map((s) => ({
    key: s.key,
    label: s.label,
    cls: s.cls,
    count: counts.get(s.key) ?? 0,
  }));
}

// --- the filters --------------------------------------------------------------------------------------------

export type Filters = {
  /** chosen kind keys; empty = every kind. Narrows catalysts only: the deadlines follow their own toggle. */
  kinds: ReadonlySet<string>;
  /** chosen state keys; empty = every state */
  states: ReadonlySet<string>;
  /** a case-insensitive match on the card name or the label */
  text: string;
  showDeadlines: boolean;
};

export const NO_FILTERS: Filters = {
  kinds: new Set(),
  states: new Set(),
  text: "",
  showDeadlines: true,
};

export const isFiltered = (f: Filters): boolean =>
  f.kinds.size > 0 || f.states.size > 0 || f.text.trim() !== "";

const matches = (needle: string, ...hay: string[]) =>
  hay.some((h) => h.toLowerCase().includes(needle));

/** Whether one dated line passes the filters. */
export function keepItem(i: Item, f: Filters): boolean {
  if (i.item !== "catalyst" && !f.showDeadlines) return false;
  if (i.item === "catalyst" && f.kinds.size > 0 && !f.kinds.has(kindKey(i.kind))) return false;
  if (f.states.size > 0 && !f.states.has(stateKey(i.state))) return false;
  const needle = f.text.trim().toLowerCase();
  return !needle || matches(needle, i.thesis_name, i.label);
}

/** One card's undated catalysts after the filters (the card name matching keeps every label), or null when
 *  none is left. */
export function keepGroup(g: Group, f: Filters): Group | null {
  if (f.states.size > 0 && !f.states.has(stateKey(g.state))) return null;
  const needle = f.text.trim().toLowerCase();
  const nameHit = needle !== "" && matches(needle, g.thesis_name);
  const catalysts = g.catalysts.filter(
    (c) =>
      (f.kinds.size === 0 || f.kinds.has(kindKey(c.kind))) &&
      (!needle || nameHit || matches(needle, c.label)),
  );
  return catalysts.length ? { ...g, count: catalysts.length, catalysts } : null;
}

/** The work line: the counts off the payload (never the filtered view), and today's date. */
export function workLine(data: CatalystCalendarOut, showDeadlines: boolean): string {
  const parts = [
    plural(data.live_cards, "live card", "live cards"),
    `${plural(data.catalysts, "catalyst", "catalysts")}: ${data.dated} dated ` +
      `(${data.upcoming_catalysts} upcoming, ${data.passed_catalysts} passed), ${data.undated} undated`,
    `${plural(data.deadlines, "card deadline", "card deadlines")} ahead${showDeadlines ? "" : " (hidden)"}`,
  ];
  if (data.cards_without_calls > 0) {
    parts.push(
      plural(data.cards_without_calls, "card with no recorded call", "cards with no recorded call"),
    );
  }
  if (data.cards_without_catalysts > 0) {
    parts.push(
      plural(data.cards_without_catalysts, "card with no catalysts", "cards with no catalysts"),
    );
  }
  parts.push(`today ${fmtLong(data.today)}`);
  return parts.join(" · ");
}
