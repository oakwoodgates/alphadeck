import type {
  InsiderBuyOut,
  PriceBar,
  ScoreboardEpisodeOut,
  TriggerRefOut,
} from "../api/hooks";

// Pure overlay logic for the drawer chart (Slice A, Revision 1): the default visible range, the STABLE
// chronological 1..N numbering across the three recorded event families (insider buy / arm trigger /
// lifecycle) over the WHOLE loaded universe (the backend floors it to max(created_at−365d, first_bar)),
// the hover tooltip content (with the honest disclosure lag + the market-price context), and the
// collision-stacking. All unit-tested here; the component (PriceSparkline) owns only the imperative chart,
// coordinate positioning, and pan/zoom, which a canvas can't run in jsdom → the reviewer live-verifies THAT
// by eye. Every chip traces to a real recorded row — nothing here invents an event (invariant #6); the
// number is a STABLE per-event identity (same event → same number in the compact view, the expanded view,
// and the future ledger — NEVER renumbered per visible window).

// The DEFAULT visible range on open: the recent episode. The universe is fully loaded; the user pans/zooms
// to reach earlier dots (no "+N earlier" affordance — the dots are real and reachable).
export const DEFAULT_VISIBLE_TRADING_DAYS = 130;

type Bar = Pick<PriceBar, "d" | "close">;

/** The default visible range = `[arm − 130 trading days, last bar]`, in bar-index terms (trading days,
 *  not calendar). Null when there are no bars. The chart sets this once via `setVisibleRange` (not
 *  fitContent-to-all) so a quick glance stays episode-focused while earlier dots sit loaded, left of view. */
export function defaultVisibleRange(bars: Bar[], armDate: string): { from: string; to: string } | null {
  if (bars.length === 0) return null;
  let armIdx = bars.length - 1;
  for (let i = 0; i < bars.length; i++) {
    if (bars[i].d >= armDate) {
      armIdx = i;
      break;
    }
  }
  const fromIdx = Math.max(0, armIdx - DEFAULT_VISIBLE_TRADING_DAYS);
  return { from: bars[fromIdx].d, to: bars[bars.length - 1].d };
}

/** The market close on/around a date — the latest bar with `d <= iso` (a weekend/holiday transaction maps
 *  to the prior trading day's close). Null when the date precedes the first loaded bar. */
export function closeOnDate(bars: Bar[], iso: string): number | null {
  let c: number | null = null;
  for (const b of bars) {
    if (b.d <= iso) c = b.close;
    else break;
  }
  return c;
}

export type OverlayFamily = "insider" | "trigger" | "lifecycle";
export type LifecycleKind = "warmed" | "armed" | "dearmed" | "exit_by";

interface ChipBase {
  n: number; // chronological 1..N across ALL families — a STABLE per-event identity (never per-window)
  family: OverlayFamily;
  date: string; // the chip's x anchor (the transaction / arm / lifecycle date)
  closeThatDay: number | null; // market close on/around that date (guide-line anchor + tooltip context)
}
export interface InsiderChipEvent extends ChipBase {
  family: "insider";
  buy: InsiderBuyOut;
  pctVsNow: number | null; // return from closeThatDay to the last drawn bar (the "−12% vs now" context)
}
export interface TriggerChipEvent extends ChipBase {
  family: "trigger";
  trigger: TriggerRefOut;
}
export interface LifecycleChipEvent extends ChipBase {
  family: "lifecycle";
  kind: LifecycleKind;
  closeReason?: string | null;
}
export type OverlayEvent = InsiderChipEvent | TriggerChipEvent | LifecycleChipEvent;

type RawEvent =
  | Omit<InsiderChipEvent, "n">
  | Omit<TriggerChipEvent, "n">
  | Omit<LifecycleChipEvent, "n">;

/** The overlay universe, numbered chronologically 1..N across every family (sorted by date; ties keep a
 *  stable insertion order → armed before its triggers before same-day buys). Built ONCE over the whole
 *  loaded window (`bars` = the backend's [floor, end], already relevance-floored + open-market-screened),
 *  so the numbers are stable regardless of the visible range (the R1 bug was per-window renumbering). The
 *  component renders whichever fall in the visible range; the numbers never change. An event past the last
 *  drawn bar (a still-future exit-by horizon) is dropped — it hasn't printed on the tape. */
export function buildOverlayEvents(
  ep: ScoreboardEpisodeOut,
  insiderBuys: InsiderBuyOut[],
  bars: Bar[],
): OverlayEvent[] {
  if (bars.length === 0) return [];
  const last = bars[bars.length - 1].d;
  const lastClose = bars[bars.length - 1].close;
  const closeAt = (d: string) => closeOnDate(bars, d);
  const raw: RawEvent[] = [];
  if (ep.warm_date)
    raw.push({ family: "lifecycle", date: ep.warm_date, kind: "warmed", closeThatDay: closeAt(ep.warm_date) });
  raw.push({ family: "lifecycle", date: ep.arm_date, kind: "armed", closeThatDay: closeAt(ep.arm_date) });
  for (const t of ep.triggers_at_arm ?? [])
    raw.push({ family: "trigger", date: ep.arm_date, trigger: t, closeThatDay: closeAt(ep.arm_date) }); // the WHY, at the arm
  for (const b of insiderBuys) {
    const c = closeAt(b.d);
    raw.push({
      family: "insider",
      date: b.d,
      buy: b,
      closeThatDay: c,
      pctVsNow: c != null && c !== 0 ? (lastClose - c) / c : null,
    });
  }
  if (ep.dearm_date)
    raw.push({
      family: "lifecycle",
      date: ep.dearm_date,
      kind: "dearmed",
      closeReason: ep.close_reason,
      closeThatDay: closeAt(ep.dearm_date),
    });
  if (ep.exit_by)
    raw.push({ family: "lifecycle", date: ep.exit_by, kind: "exit_by", closeThatDay: closeAt(ep.exit_by) });

  const visible = raw.filter((e) => e.date <= last); // nothing sits past the last drawn bar
  visible.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)); // stable → insertion breaks ties
  return visible.map((e, i) => ({ ...e, n: i + 1 }) as OverlayEvent);
}

export interface TooltipContent {
  title: string;
  lines: string[];
}

function fmtShares(n: number): string {
  return `${Math.round(n).toLocaleString("en-US")} sh`;
}

function fmtUsd(v: number): string {
  const a = Math.abs(v);
  for (const [div, suffix] of [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ] as const) {
    if (a >= div) return `$${(v / div).toFixed(1).replace(/\.0$/, "")}${suffix}`;
  }
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

function fmtPct(x: number): string {
  return `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(0)}%`; // U+2212 minus, to match the deck
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
}

function insiderTooltip(e: InsiderChipEvent): TooltipContent {
  const b = e.buy;
  const who = b.insider_role
    ? `${b.insider_name ?? "insider"} (${b.insider_role})`
    : (b.insider_name ?? "insider");
  let bought: string;
  if (b.shares != null && b.usd != null) bought = `bought ${fmtShares(b.shares)} @ ${fmtUsd(b.usd)}`;
  else if (b.usd != null) bought = `bought ${fmtUsd(b.usd)}`;
  else if (b.shares != null) bought = `bought ${fmtShares(b.shares)}`;
  else bought = "open-market buy";
  const lines = [bought, `transacted ${b.d}`];
  const lag = daysBetween(b.d, b.disclosed);
  // the disclosure lag is the honest bit (#6): the IBM episode's own "ingested 166d after its event date"
  if (lag >= 1) lines.push(`disclosed ${lag}d later (${b.disclosed})`);
  if (b.aff_10b5_1) lines.push("10b5-1 plan (pre-scheduled)"); // not the same conviction signal
  // R3: the DISTINCT market fact — the stock's close that day + the move since (the fill $ ≠ the price).
  if (e.closeThatDay != null) {
    const vsNow = e.pctVsNow != null ? ` · ${fmtPct(e.pctVsNow)} vs now` : "";
    lines.push(`stock ${fmtUsd(e.closeThatDay)} that day${vsNow}`);
  }
  return { title: who, lines };
}

function triggerTooltip(t: TriggerRefOut): TooltipContent {
  const source = [t.kind, t.ticker].filter(Boolean).join(" · ");
  return { title: t.label, lines: source ? [source] : [] };
}

const LIFECYCLE_TITLE: Record<LifecycleKind, string> = {
  warmed: "warmed",
  armed: "armed",
  dearmed: "de-armed",
  exit_by: "exit-by (horizon)",
};

function lifecycleTooltip(e: LifecycleChipEvent): TooltipContent {
  const title =
    e.kind === "dearmed" && e.closeReason ? `de-armed (${e.closeReason})` : LIFECYCLE_TITLE[e.kind];
  return { title, lines: [e.date] };
}

/** The hover card for a chip — a pure builder (jsdom can't test coordinates, so test THIS). Always
 *  available: the number is never a dead reference (#6). */
export function overlayTooltip(e: OverlayEvent): TooltipContent {
  if (e.family === "insider") return insiderTooltip(e);
  if (e.family === "trigger") return triggerTooltip(e.trigger);
  return lifecycleTooltip(e);
}

const FAMILY_META: Record<OverlayFamily, { label: string; cls: string }> = {
  insider: { label: "insider buy", cls: "ov-insider" },
  trigger: { label: "arm trigger", cls: "ov-trigger" },
  lifecycle: { label: "lifecycle", cls: "ov-lifecycle" },
};
const FAMILY_ORDER: OverlayFamily[] = ["insider", "trigger", "lifecycle"];

/** The CSS class carrying a family's color (the DOM chip reads CSS vars → theme-consistent, unlike the
 *  canvas lines which must hard-code hex). */
export function familyCls(family: OverlayFamily): string {
  return FAMILY_META[family].cls;
}

/** The legend under the chart — ONLY families actually present (honest loudness / #7: a legend entry for
 *  a family with zero events is noise). Empty → the component renders no legend at all. */
export function legendEntries(
  events: OverlayEvent[],
): { family: OverlayFamily; label: string; cls: string }[] {
  const present = new Set(events.map((e) => e.family));
  return FAMILY_ORDER.filter((f) => present.has(f)).map((f) => ({ family: f, ...FAMILY_META[f] }));
}

export interface PositionedChip {
  n: number;
  x: number;
  family: OverlayFamily;
}
export interface PlacedChip extends PositionedChip {
  level: number; // vertical stack level (0 = baseline); the component maps level → y
}

/** Collision-stacking: chips whose x land within `chipW` share a column, so they stack to distinct
 *  `level`s instead of overlapping. Greedy over x (interval-graph coloring) → minimal stacking. A chip
 *  that would exceed `maxLevels` spills to `overflow` (the component renders a visible "+N", never a
 *  silent drop — #9 / interaction-principle #2). Zooming in (R4) spreads the x-axis, so a dense cluster
 *  restacks flatter and de-crowds. INVARIANT: placed + overflow === input (nothing lost). */
export function stackChips(
  items: PositionedChip[],
  opts: { chipW: number; maxLevels: number },
): { placed: PlacedChip[]; overflow: PositionedChip[] } {
  const sorted = [...items].sort((a, b) => a.x - b.x || a.n - b.n);
  const lastX: number[] = []; // lastX[level] = the x of the last chip placed at that level
  const placed: PlacedChip[] = [];
  const overflow: PositionedChip[] = [];
  for (const it of sorted) {
    let level = 0;
    while (level < opts.maxLevels && lastX[level] !== undefined && it.x - lastX[level] < opts.chipW)
      level++;
    if (level < opts.maxLevels) {
      lastX[level] = it.x;
      placed.push({ ...it, level });
    } else {
      overflow.push(it); // no room — surfaced as a visible "+N" cluster, never dropped
    }
  }
  return { placed, overflow };
}
