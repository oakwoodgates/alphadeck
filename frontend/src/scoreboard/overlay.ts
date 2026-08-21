import type {
  InsiderBuyOut,
  PriceBar,
  ProvenanceOut,
  ScoreboardEpisodeOut,
  TriggerRefOut,
} from "../api/hooks";
import { closeReasonLabel } from "./rows";

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
  /** The arm this trigger fed — carried so the tooltip can name the linkage when the chip's own date
   *  sits away from it (`date !== armDate`). The chip's x is VALID time; this is the call's time. */
  armDate: string;
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
  for (const t of ep.triggers_at_arm ?? []) {
    // The WHY, at its OWN fire date: `event_date` (the SignalEvent's valid time) anchors the chip
    // whenever the loaded window can actually show it; a fire that predates the first bar has no
    // honest x, so it falls back to the arm rather than clamping onto a bar that never saw it (#6).
    // VALID TIME positions the chip; the arm linkage rides the tooltip — so a trigger may now sit
    // LEFT of `armed` and take a lower number. That is fine: the numbering is a per-render identity
    // (chart ⇄ ledger, built once here), never persisted, so no stored reference can go stale.
    const d = t.event_date != null && t.event_date >= bars[0].d ? t.event_date : ep.arm_date;
    raw.push({ family: "trigger", date: d, trigger: t, armDate: ep.arm_date, closeThatDay: closeAt(d) });
  }
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

// S2c: the buy's server-classified CHARACTER, one terse line each (honest loudness: the exception is
// labeled; an open-market buy — the norm — stays unbadged). Wording is the operator-approved copy.
const CHARACTER_LINE: Partial<Record<InsiderBuyOut["character"], string>> = {
  self_filing: "issuer self-filing (not a personal buy)",
  primary_market: "primary-market (offer-price, set aside)",
  implausible: "implausible $ (bad source data, set aside)",
  // open_market: no line — "passed the available screens", the unbadged default
};

/** Is this buy SET ASIDE (excluded from the panel's open-market flow)? Only `primary_market` /
 *  `implausible` — a `self_filing` is labeled but still counted today (the net-flow re-base is
 *  deferred). Set-aside buys render greyed + labeled, never hidden (WB #2: pruning hides, it never
 *  vanishes) — the chip and its ledger row both read this one helper. */
export function insiderSetAside(b: InsiderBuyOut): boolean {
  return b.character === "primary_market" || b.character === "implausible";
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
  // an unsized set-aside/self-filing must not claim "open-market" — the neutral fallback is honest
  else bought = b.character === "open_market" ? "open-market buy" : "insider buy";
  const lines = [bought, `transacted ${b.d}`];
  // Two honest clocks (#6, the MRVL two-clock fix): `disclosed` = the real SEC acceptance date; `ingested`
  // = when our pipeline wrote the row, rendered as a SECOND line ONLY when it differs. When the acceptance
  // date is unknown (pre-backfill / unresolvable) the disclosed line is absent and only "ingested" rides
  // (#9 fallback) — honest at every rollout stage. Each line suppresses a 0-day lag (no "0d later").
  if (b.disclosed != null) {
    const lag = daysBetween(b.d, b.disclosed);
    if (lag >= 1) lines.push(`disclosed ${lag}d later (${b.disclosed})`);
    if (b.ingested !== b.disclosed) {
      const ilag = daysBetween(b.d, b.ingested);
      if (ilag >= 1) lines.push(`ingested ${ilag}d later (${b.ingested})`); // the re-ingest lag, honest
    }
  } else {
    const ilag = daysBetween(b.d, b.ingested); // no acceptance date -> the ingest line only (#9)
    if (ilag >= 1) lines.push(`ingested ${ilag}d later (${b.ingested})`);
  }
  // S2c: the character line — why this buy did/didn't count (#6); open_market stays unbadged (#7)
  const character = CHARACTER_LINE[b.character];
  if (character) lines.push(character);
  if (b.aff_10b5_1) lines.push("10b5-1 plan (pre-scheduled)"); // tri-state: renders ONLY on explicit true
  // R3: the DISTINCT market fact — the stock's close that day + the move since (the fill $ ≠ the price).
  if (e.closeThatDay != null) {
    const vsNow = e.pctVsNow != null ? ` · ${fmtPct(e.pctVsNow)} vs now` : "";
    lines.push(`stock ${fmtUsd(e.closeThatDay)} that day${vsNow}`);
  }
  return { title: who, lines };
}

// How many per-source provenance lines a trigger surfaces before it collapses the rest into a visible
// "+N more sources". A hover card is transient real estate; the cap keeps it readable — and the "+N"
// keeps the omission VISIBLE rather than a silent drop (#9, the same discipline as the chip overflow).
const PROVENANCE_LINES = 2;

/** A provenance ref's clickable target, or null when it has none. The server resolves `url` (the EDGAR
 *  index page) where it can and leaves it null otherwise — an unresolvable ref stays TEXT, never a
 *  fabricated href (#6). A `ref` that is itself an http(s) URL is honored as the fallback; anything
 *  else (an accession, a metric key) is not a link. */
function provenanceHref(p: ProvenanceOut): string | null {
  const u = p.url ?? p.ref;
  return typeof u === "string" && /^https?:\/\//i.test(u) ? u : null;
}

export interface ProvenanceLink {
  label: string;
  url: string;
}

/** The linkable subset of a trigger's provenance, in wire order, under the SAME cap the tooltip uses —
 *  so the ledger row's anchors and the hover card's lines never disagree about which sources surfaced.
 *  Text-only refs are absent here (they still ride as tooltip/detail TEXT — provenance is never hidden,
 *  it just isn't always clickable). */
export function triggerLinks(t: TriggerRefOut): ProvenanceLink[] {
  const out: ProvenanceLink[] = [];
  for (const p of (t.sources ?? []).slice(0, PROVENANCE_LINES)) {
    const url = provenanceHref(p);
    if (url) out.push({ label: p.source, url });
  }
  return out;
}

function triggerTooltip(e: TriggerChipEvent): TooltipContent {
  const t = e.trigger;
  const lines: string[] = [];
  const source = [t.kind, t.ticker].filter(Boolean).join(" · ");
  if (source) lines.push(source);
  if (t.grade) lines.push(`grade ${t.grade}`); // the call-strength CLASS (flip/core), never a size
  // The TRUE fire date, ONLY when the chip is NOT sitting on it — i.e. the fallback case, where the fire
  // predates the first loaded bar so `buildOverlayEvents` anchored the chip at the arm instead. There the
  // linkage line below stays silent (date === armDate) and the valid time would otherwise vanish from the
  // UI entirely; a recorded fact is named, never dropped (#6/#9). At its own date the line is redundant.
  if (t.event_date != null && t.event_date !== e.date)
    lines.push(`fired ${t.event_date} (before the loaded window)`);
  // The arm linkage — ONLY when the chip sits away from the arm. At the arm it would restate the chip's
  // own x on every trigger, and a line true of every row carries no information (#7, honest loudness).
  if (e.date !== e.armDate) lines.push(`→ fed the ${e.armDate} arm`);
  const sources = t.sources ?? [];
  for (const p of sources.slice(0, PROVENANCE_LINES)) lines.push(`${p.source}: ${p.ref}`);
  const hidden = sources.length - PROVENANCE_LINES;
  if (hidden > 0) lines.push(`+${hidden} more source${hidden === 1 ? "" : "s"}`); // visible, not dropped
  return { title: t.label, lines };
}

const LIFECYCLE_TITLE: Record<LifecycleKind, string> = {
  warmed: "warmed",
  armed: "armed",
  dearmed: "de-armed",
  exit_by: "exit-by (horizon)",
};

function lifecycleTooltip(e: LifecycleChipEvent): TooltipContent {
  // the de-arm reason reads as ENGLISH here (`closeReasonLabel`), not as the wire token — the raw token
  // stays reachable on the components that render it (a `title=`), so nothing is hidden, only translated
  const title =
    e.kind === "dearmed" && e.closeReason
      ? `de-armed (${closeReasonLabel(e.closeReason)})`
      : LIFECYCLE_TITLE[e.kind];
  return { title, lines: [e.date] };
}

/** The hover card for a chip — a pure builder (jsdom can't test coordinates, so test THIS). Always
 *  available: the number is never a dead reference (#6). */
export function overlayTooltip(e: OverlayEvent): TooltipContent {
  if (e.family === "insider") return insiderTooltip(e);
  if (e.family === "trigger") return triggerTooltip(e);
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
