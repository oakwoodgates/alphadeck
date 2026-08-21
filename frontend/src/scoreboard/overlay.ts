import type {
  ActivistStakeOut,
  CorporateEventOut,
  DisplayEvent,
  DisplaySignal,
  EpisodeOperatorOut,
  InsiderBuyOut,
  InsiderSellOut,
  MemberDisplaySignalsOut,
  PriceBar,
  ProvenanceOut,
  ScoreboardEpisodeOut,
  TriggerRefOut,
} from "../api/hooks";
import { closeReasonLabel, fmtReturn } from "./rows";
// overlay → scorecard → rows is acyclic (scorecard imports only ../api/hooks + ./rows — checked); if
// scorecard ever came to need overlay, move these two guards into rows.ts instead of closing the loop.
import { fmtPrice, noForwardBar } from "./scorecard";

// Pure overlay logic for the drawer chart (Slice A, Revision 1): the default visible range, the STABLE
// chronological 1..N numbering across the recorded event families (insider buy / arm trigger / lifecycle /
// operator — A2) plus the one COMPUTED family (tape signal — A3: display-only, re-derived per read, and
// labeled as such) over the WHOLE loaded universe (the backend floors it to max(created_at−365d, first_bar)),
// the hover tooltip content (with the honest disclosure lag + the market-price context), and the
// collision-stacking. All unit-tested here; the component (PriceSparkline) owns only the imperative chart,
// coordinate positioning, and pan/zoom, which a canvas can't run in jsdom → the reviewer live-verifies THAT
// by eye. Every chip traces to a real recorded row — or, for the A3 tape family, to a deterministic
// computation over the SAME price facts, labeled as computed — nothing here invents an event (#6); the
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

/** The nearest loaded bar DATE to `iso` (either side) — the chip-positioning anchor. Binary search
 *  over the ascending ISO `d` strings (lexicographic = chronological, the codebase's standing idiom),
 *  then only the two neighbor candidates are compared by calendar distance; a tie prefers the EARLIER
 *  bar (behavior-identical to the linear scan it replaced, whose strict-< first-min kept the earlier
 *  date). Null when no bars are loaded. O(log B) matters here: the chart repositions every chip on
 *  every pan/zoom frame, and Slice B triples the event count. */
export function nearestBarDate(bars: { d: string }[], iso: string): string | null {
  if (bars.length === 0) return null;
  let lo = 0;
  let hi = bars.length; // lower bound: the first index with d >= iso
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].d < iso) lo = mid + 1;
    else hi = mid;
  }
  if (lo === 0) return bars[0].d; // before (or at) the first bar
  if (lo === bars.length) return bars[bars.length - 1].d; // after the last bar
  const prev = bars[lo - 1].d;
  const next = bars[lo].d;
  const t = Date.parse(iso);
  return t - Date.parse(prev) <= Date.parse(next) - t ? prev : next; // <= → the earlier bar wins ties
}

/** The latest bar's DATE with `d <= iso` — the honest x for a bar-anchored marker (a weekend/holiday
 *  date maps to the prior trading day, exactly `closeOnDate`'s convention). Null when the date precedes
 *  the first loaded bar: no honest x → the marker is dropped, never clamped (the A1 trigger-chip rule). */
function barDateOnOrBefore(bars: Bar[], iso: string): string | null {
  let d: string | null = null;
  for (const b of bars) {
    if (b.d <= iso) d = b.d;
    else break;
  }
  return d;
}

// -------- Slice A2: un-numbered OUTCOME markers (entry / exit / peak) ------------------------------
// DERIVED outcome points, not recorded events — so they ride lightweight-charts' setMarkers on the
// close series (bar-anchored: the y IS the close that day), never the numbered chip universe. Each
// traces to a wire field (#6): entry_close@arm_date, exit_close@exit_date, peak_date. Exit + peak are
// gated on the same degenerate-forward-bar guard the lenses use (`noForwardBar`): before a forward bar
// lands, exit_date === arm_date (a false round-trip) and the peak is a degenerate single-bar 0.0%.

export type PriceMarkerKind = "entry" | "exit" | "peak";
export interface PriceMarker {
  kind: PriceMarkerKind;
  time: string; // snapped to the latest bar ≤ the wire date; no bar → the marker is dropped
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string; // the tiny label; exit reads "last bar" on a truncated episode (the honest anchor)
}

/** The outcome markers for an episode, sorted ascending by time (a setMarkers requirement). Pure —
 *  the canvas application (color, size, the setMarkers call) lives in PriceSparkline. On a truncated
 *  episode exit_date is the measurement edge, not an exit — the label says "last bar", mirroring
 *  `peakTimingPhrase`'s anchor idiom (scorecard.ts). peak_date === exit_date keeps both (v4 stacks). */
export function episodeMarkers(ep: ScoreboardEpisodeOut, bars: Bar[]): PriceMarker[] {
  const out: PriceMarker[] = [];
  if (ep.entry_close != null) {
    const t = barDateOnOrBefore(bars, ep.arm_date);
    if (t != null) out.push({ kind: "entry", time: t, position: "belowBar", shape: "arrowUp", text: "entry" });
  }
  if (!noForwardBar(ep)) {
    if (ep.exit_close != null && ep.exit_date != null) {
      const t = barDateOnOrBefore(bars, ep.exit_date);
      if (t != null)
        out.push({
          kind: "exit",
          time: t,
          position: "aboveBar",
          shape: "arrowDown",
          text: ep.truncated ? "last bar" : "exit",
        });
    }
    if (ep.peak_date != null) {
      const t = barDateOnOrBefore(bars, ep.peak_date);
      if (t != null) out.push({ kind: "peak", time: t, position: "aboveBar", shape: "circle", text: "peak" });
    }
  }
  out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  return out;
}

/** The volume histogram's data (Slice A2): null-volume bars are SKIPPED, never zero-invented (#6) — a
 *  close-only free-EOD bar simply leaves a gap. Pure; the series creation/visibility is the component's. */
export function volumeData(bars: Pick<PriceBar, "d" | "volume">[]): { time: string; value: number }[] {
  return bars.filter((b) => b.volume != null).map((b) => ({ time: b.d, value: b.volume as number }));
}

export type OverlayFamily =
  | "insider"
  | "sell"
  | "trigger"
  | "risk"
  | "activist"
  | "filing"
  | "lifecycle"
  | "operator"
  | "signal";
export type LifecycleKind = "warmed" | "armed" | "dearmed" | "exit_by";

// -------- Slice A3: the DISPLAY-SIGNAL kinds whose events earn a chip -------------------------------
// A display signal is a READ-ONLY tape read (docs/DISPLAY_SIGNALS.md) — structurally not a SignalEvent,
// never an input to the call. Only the two kinds whose `events[]` are DATED tape facts qualify as chips:
// `sma_position` (the 50/200 crosses) and `relative_strength` (the RS-high print). Everything else is
// EXCLUDED — `insider_flow_90d` explicitly so: its last_buy/last_sell events would duplicate the insider
// chip family off a second source, and two chips for one Form 4 is a lie about how much the tape said.
// An allow-list, not a deny-list: a NEW display member's events stay off the chart until someone decides
// they belong (a chip is a claim; silence is the safe default, #7).
const TAPE_EVENT_KINDS = ["sma_position", "relative_strength"] as const;

/** The member's display signals that may contribute chips — the allow-list above, applied to the joined
 *  `useDisplaySignals` member. Pure + exported so the filter itself is testable (the drawer composes it
 *  and hands the result to `buildOverlayEvents`). A null member (no display read yet) → no chips. */
export function tapeSignals(member: MemberDisplaySignalsOut | null | undefined): DisplaySignal[] {
  const allowed: readonly string[] = TAPE_EVENT_KINDS;
  return (member?.signals ?? []).filter((s) => allowed.includes(s.kind));
}

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
/** Slice B: a code-S insider sale — the recorded mirror of the buy chip. `character` (server-side,
 *  the CALL screen's bucket wire-mapped) drives the set-aside grey: everything except `kept` is
 *  screened out of the detector's cluster and renders greyed + labeled, never hidden (WB #2). */
export interface SellChipEvent extends ChipBase {
  family: "sell";
  sell: InsiderSellOut;
  pctVsNow: number | null; // same market-context read as the buy chip
}
/** Slice B: one stored 8-K (every one in-window rides — the server never item-cuts; loudness is
 *  this layer's concern, expressed as the family's quiet grey, never as an omission). */
export interface FilingChipEvent extends ChipBase {
  family: "filing";
  event: CorporateEventOut;
}
/** Slice B: one stored 13D/G-family filing. The 13D family carries the family weight; the 13G
 *  family renders greyed-passive — mirroring the fire policy (13G fires nothing) as display weight,
 *  never as an omission. Unresolved filer identity reads "filer unresolved", never dropped (#9). */
export interface ActivistChipEvent extends ChipBase {
  family: "activist";
  stake: ActivistStakeOut;
}
export interface TriggerChipEvent extends ChipBase {
  family: "trigger";
  trigger: TriggerRefOut;
  /** The arm this trigger fed — carried so the tooltip can name the linkage when the chip's own date
   *  sits away from it (`date !== armDate`). The chip's x is VALID time; this is the call's time. */
  armDate: string;
}
/** Slice C: a fired RISK signal off the recorded daily cards (`ep.risk_events` — member-filtered,
 *  deduped server-side). The call's own risk tape, DISTINCT from the sell/filing FACT families: a
 *  fact is what the tape did; this is what the record made of it (a sell cluster appearing as both
 *  per-txn `sell` chips and one `risk` chip is by design — two different claims). Rides the same
 *  `TriggerRefOut` shape as the arm triggers, so grade/provenance/ticker come along for free. */
export interface RiskChipEvent extends ChipBase {
  family: "risk";
  trigger: TriggerRefOut;
}
export interface LifecycleChipEvent extends ChipBase {
  family: "lifecycle";
  kind: LifecycleKind;
  closeReason?: string | null;
  /** Slice C: the backend-composed WHY behind a `dearmed_other` close — rides the de-armed chip so
   *  its tooltip/ledger row can answer what "(see de-arm day)" used to defer. */
  dearmDetail?: string | null;
}
/** The operator's recorded decision (A2) — a numbered chip like any recorded row: one chip at
 *  `decision_date`, the took/passed detail (fill, inferred-close, running return, reason, the
 *  operator's own exit) riding its tooltip/ledger lines. */
export interface OperatorChipEvent extends ChipBase {
  family: "operator";
  op: EpisodeOperatorOut;
}
/** A3: a DISPLAY-signal event — the quietest family. Unlike every sibling it is NOT a recorded row: it is
 *  re-derived on the CURRENT read (compute-on-read, never persisted), so the `asof` it was derived under
 *  rides along and the tooltip says so. `signalKind` is its parent display member's registered kind — it
 *  drives the per-kind epistemics line, not any behavior. Display-only by construction (#3/#4): it cannot
 *  arm, veto, or grade anything; it only annotates the path the call already drew. */
export interface SignalChipEvent extends ChipBase {
  family: "signal";
  event: DisplayEvent;
  signalKind: string;
  asof: string;
}
export type OverlayEvent =
  | InsiderChipEvent
  | SellChipEvent
  | TriggerChipEvent
  | RiskChipEvent
  | ActivistChipEvent
  | FilingChipEvent
  | LifecycleChipEvent
  | OperatorChipEvent
  | SignalChipEvent;

type RawEvent =
  | Omit<InsiderChipEvent, "n">
  | Omit<SellChipEvent, "n">
  | Omit<TriggerChipEvent, "n">
  | Omit<RiskChipEvent, "n">
  | Omit<ActivistChipEvent, "n">
  | Omit<FilingChipEvent, "n">
  | Omit<LifecycleChipEvent, "n">
  | Omit<OperatorChipEvent, "n">
  | Omit<SignalChipEvent, "n">;

/** The overlay universe, numbered chronologically 1..N across every family (sorted by date; ties keep a
 *  stable insertion order → armed before its triggers before same-day buys). Built ONCE over the whole
 *  loaded window (`bars` = the backend's [floor, end], already relevance-floored + open-market-screened),
 *  so the numbers are stable regardless of the visible range (the R1 bug was per-window renumbering). The
 *  component renders whichever fall in the visible range; the numbers never change. An event past the last
 *  drawn bar (a still-future exit-by horizon) is dropped — it hasn't printed on the tape.
 *
 *  `tape` (A3, optional) adds the display-signal family: the already-kind-filtered signals (`tapeSignals`)
 *  plus the as-of they were derived under. OPTIONAL because it is pure garnish — a caller without the
 *  display read (the pure-render tests, a drawer whose signals query hasn't landed) still numbers the
 *  recorded families exactly as before; the tape chips only ever append to that universe.
 *
 *  `wire` (Slice B, optional) adds the three widened recorded families off the same price-window
 *  response — insider sells, 8-K filings, 13D/G stakes. Optional for the same reason as `tape`
 *  (existing callers/tests keep their exact universe); each list defaults empty. NB adding families
 *  interleaves new chips into the chronological numbering, shifting existing chip numbers — fine by
 *  design: the number is a per-render identity (chart ⇄ ledger, built once here), never persisted. */
export function buildOverlayEvents(
  ep: ScoreboardEpisodeOut,
  insiderBuys: InsiderBuyOut[],
  bars: Bar[],
  tape?: { signals: DisplaySignal[]; asof: string },
  wire?: { sells?: InsiderSellOut[]; filings?: CorporateEventOut[]; stakes?: ActivistStakeOut[] },
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
  // Slice C: the run's fired RISK signals (member-filtered + deduped server-side), right after the
  // triggers so the call-record families tie together on a shared day. The anchor rule is the
  // trigger's exactly: the fact-anchored `event_date` where the loaded window can show it, else the
  // ARM as the honest fallback anchor (a risk that pre-dates the window — an old note issuance, say
  // — rode this run all the same); the tooltip names the true fire date in the fallback case (#6).
  // The backend stamps every event_date (a legacy None gets the first card-asof), so the null guard
  // is belt-and-suspenders, not a path.
  for (const t of ep.risk_events ?? []) {
    const d = t.event_date != null && t.event_date >= bars[0].d ? t.event_date : ep.arm_date;
    raw.push({ family: "risk", date: d, trigger: t, closeThatDay: closeAt(d) });
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
  // Slice B: the three widened recorded families, in insertion order sells → filings → stakes so a
  // same-day tie reads buys before sells before the corporate/ownership tape (all recorded SEC facts,
  // all ahead of the operator's decision). Every row the server sent gets a chip — screened sells and
  // the passive 13G family render greyed via `eventSetAside`, never hidden (WB #2).
  for (const s of wire?.sells ?? []) {
    const c = closeAt(s.d);
    raw.push({
      family: "sell",
      date: s.d,
      sell: s,
      closeThatDay: c,
      pctVsNow: c != null && c !== 0 ? (lastClose - c) / c : null,
    });
  }
  for (const f of wire?.filings ?? [])
    raw.push({ family: "filing", date: f.d, event: f, closeThatDay: closeAt(f.d) });
  for (const st of wire?.stakes ?? [])
    raw.push({ family: "activist", date: st.d, stake: st, closeThatDay: closeAt(st.d) });
  // A2: the operator's decision, at its decision_date. Same-day tie order (insertion): armed →
  // triggers → risks → buys → sells → filings → stakes → operator → dearmed. A decision dated past the last
  // bar loses its chip to the `date <= last` tape rule below — DELIBERATE (nothing sits past the
  // tape): it still rides the Lens-4 operator line + the episode row's operator cell, so the fact
  // never vanishes (WB #2).
  if (ep.operator)
    raw.push({
      family: "operator",
      date: ep.operator.decision_date,
      op: ep.operator,
      closeThatDay: closeAt(ep.operator.decision_date),
    });
  if (ep.dearm_date)
    raw.push({
      family: "lifecycle",
      date: ep.dearm_date,
      kind: "dearmed",
      closeReason: ep.close_reason,
      dearmDetail: ep.dearm_detail, // Slice C: the composed WHY rides the de-armed chip
      closeThatDay: closeAt(ep.dearm_date),
    });
  if (ep.exit_by)
    raw.push({ family: "lifecycle", date: ep.exit_by, kind: "exit_by", closeThatDay: closeAt(ep.exit_by) });
  // A3: the display-signal (tape) events, pushed LAST so a same-day tie sorts them behind every recorded
  // fact (#7 — a re-derived tape read never leads an arm or a filing it shares a date with).
  //
  // The left-edge rule INVERTS the trigger fallback above, deliberately. A trigger falls BACK to the arm
  // because it is call EVIDENCE — the arm is a real anchor that keeps the recorded fact on screen. A tape
  // read has no such anchor: it is a cross the price printed on ONE day and nothing else, so a cross that
  // predates the first loaded bar is DROPPED, never clamped onto a bar that never saw it (a "latest flip"
  // rendered at the window's left edge would read as a flip that just happened — the misleading case #6
  // forbids). It is not lost information: the same signal's HEADLINE still states the current position in
  // the Cockpit strip below (WB #2 — the fact stays visible, only the chip is withheld).
  for (const s of tape?.signals ?? []) {
    for (const ev of s.events ?? []) {
      if (ev.date < bars[0].d) continue; // pre-window: no honest x → drop, never clamp
      raw.push({
        family: "signal",
        date: ev.date,
        event: ev,
        signalKind: s.kind,
        asof: tape!.asof,
        closeThatDay: closeAt(ev.date),
      });
    }
  }

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

// Slice B: the sell's server-classified character, one terse line each. `kept` — the cluster-counted
// norm — stays unbadged (#7); every screened bucket names WHY it didn't count (#6). The 10b5-1 flag
// needs no separate line here: an explicit-True plan IS the `planned` character (the screen's rule),
// so the character line already carries it.
const SELL_CHARACTER_LINE: Partial<Record<InsiderSellOut["character"], string>> = {
  planned: "10b5-1 planned sale (near-noise, screened)",
  self_filing: "issuer self-filing (not personal insider supply, screened)",
  below_low: "below the day's low (discounted secondary, set aside)",
  implausible: "implausible $ (bad source data, set aside)",
  foreign_ordinary: "foreign ordinary line on the ADR tape (wrong instrument, screened)",
  // kept: no line — what the risk detector's cluster counts, the unbadged default
};

/** Loose display-grouping mirror of the backend's exact `_13D_FORMS` frozenset
 *  (backend/signals/activist_stake.py — both naming eras + amendments; re-sync by eye if the fire
 *  policy's set ever changes). Substring ON PURPOSE: this drives only the passive-grey display
 *  weight, so an unknown future form string fails toward QUIET (greyed), never toward loud — while
 *  the fire policy itself stays backend-exact. */
export function is13DFamily(form: string): boolean {
  return form.toUpperCase().includes("13D");
}

/** Is this chip SET ASIDE (greyed, never hidden — WB #2)? The ONE helper the chart chip and its
 *  ledger row both read, across every family that has a grey state: an insider buy via
 *  `insiderSetAside` (primary-market / implausible); a sell whose character is anything but `kept`
 *  (screened out of the detector's cluster); an activist filing outside the 13D family (passive —
 *  mirrors "13G fires nothing" as display weight). Everything else carries its family weight. */
export function eventSetAside(e: OverlayEvent): boolean {
  if (e.family === "insider") return insiderSetAside(e.buy);
  if (e.family === "sell") return e.sell.character !== "kept";
  if (e.family === "activist") return !is13DFamily(e.stake.form);
  return false;
}

function sellTooltip(e: SellChipEvent): TooltipContent {
  const s = e.sell;
  const who = s.insider_role
    ? `${s.insider_name ?? "insider"} (${s.insider_role})`
    : (s.insider_name ?? "insider");
  let sold: string;
  if (s.shares != null && s.usd != null) sold = `sold ${fmtShares(s.shares)} @ ${fmtUsd(s.usd)}`;
  else if (s.usd != null) sold = `sold ${fmtUsd(s.usd)}`;
  else if (s.shares != null) sold = `sold ${fmtShares(s.shares)}`;
  else sold = "insider sale";
  const lines = [sold, `transacted ${s.d}`];
  // the SAME two honest clocks as the buy tooltip (#6): disclosed = the SEC acceptance date;
  // ingested = our pipeline's write, a second line only when it differs; no acceptance date → the
  // ingest line alone (#9). Each suppresses a 0-day lag.
  if (s.disclosed != null) {
    const lag = daysBetween(s.d, s.disclosed);
    if (lag >= 1) lines.push(`disclosed ${lag}d later (${s.disclosed})`);
    if (s.ingested !== s.disclosed) {
      const ilag = daysBetween(s.d, s.ingested);
      if (ilag >= 1) lines.push(`ingested ${ilag}d later (${s.ingested})`);
    }
  } else {
    const ilag = daysBetween(s.d, s.ingested);
    if (ilag >= 1) lines.push(`ingested ${ilag}d later (${s.ingested})`);
  }
  const character = SELL_CHARACTER_LINE[s.character];
  if (character) lines.push(character); // why this sale did/didn't count (#6); kept stays unbadged
  if (e.closeThatDay != null) {
    const vsNow = e.pctVsNow != null ? ` · ${fmtPct(e.pctVsNow)} vs now` : "";
    lines.push(`stock ${fmtUsd(e.closeThatDay)} that day${vsNow}`);
  }
  return { title: who, lines };
}

function filingTooltip(e: FilingChipEvent): TooltipContent {
  const f = e.event;
  const lines = [
    // null = the submissions JSON has not resolved the codes yet — said plainly, never invented (#6)
    f.items != null ? `items ${f.items.join(", ")}` : "items unresolved",
    `filed ${f.d}`,
  ];
  const ilag = daysBetween(f.d, f.ingested);
  if (ilag >= 1) lines.push(`ingested ${ilag}d later (${f.ingested})`); // the honest ingest lag
  return { title: f.form, lines };
}

function activistTooltip(e: ActivistChipEvent): TooltipContent {
  const st = e.stake;
  // unresolved identity is SAID, never guessed and never a dropped row (#9/#6)
  const lines = [st.filer_name ?? "filer unresolved"];
  if (st.pct_owned != null) lines.push(`${st.pct_owned}% of class`);
  lines.push(`filed ${st.d}`);
  const ilag = daysBetween(st.d, st.ingested);
  if (ilag >= 1) lines.push(`ingested ${ilag}d later (${st.ingested})`);
  return { title: st.form, lines };
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

// Slice C: the risk chip's lines — the trigger tooltip's shape minus what a risk never has (a grade:
// risks are ungraded by construction — `grade=None` on every risk TriggerRef; an arm linkage: a risk
// rode the run, it didn't feed the arm). The true fire date is named ONLY in the fallback-anchor case
// (the same rule as the trigger chip — at its own date the line would restate the chip's x).
function riskTooltip(e: RiskChipEvent): TooltipContent {
  const t = e.trigger;
  const lines: string[] = [];
  const source = [t.kind, t.ticker].filter(Boolean).join(" · ");
  if (source) lines.push(source);
  if (t.event_date != null && t.event_date !== e.date)
    lines.push(`fired ${t.event_date} (before the loaded window)`);
  const sources = t.sources ?? [];
  for (const p of sources.slice(0, PROVENANCE_LINES)) lines.push(`${p.source}: ${p.ref}`);
  const hidden = sources.length - PROVENANCE_LINES;
  if (hidden > 0) lines.push(`+${hidden} more source${hidden === 1 ? "" : "s"}`); // visible, not dropped
  return { title: t.label, lines };
}

// A2: the operator decision's lines — the action word LEADS line 1 (not the title), so the ledger can
// join the lines verbatim under its "operator" type cell without restating the family. Every line is a
// wire field (#6): the fill vs inferred-close distinction is `entry_inferred`/`exit_inferred` (an
// inferred close is LABELED, never passed off as a logged fill), the return is `operator_return` with
// its `running` flag, the reason rides verbatim, and a thesis-level decision says so.
function operatorTooltip(e: OperatorChipEvent): TooltipContent {
  const op = e.op;
  const lines: string[] = [];
  if (op.action === "took") {
    const price =
      op.entry_price != null
        ? ` @ ${fmtPrice(op.entry_price)} (${op.entry_inferred ? "close, inferred" : "logged fill"})`
        : "";
    lines.push(`took${price}`);
    if (op.exit_price != null) {
      const when = op.exit_date != null ? `${op.exit_date} ` : "";
      lines.push(`exited ${when}@ ${fmtPrice(op.exit_price)}${op.exit_inferred ? " (close, inferred)" : ""}`);
    }
    if (op.operator_return != null)
      lines.push(`${op.running ? "running " : ""}${fmtReturn(op.operator_return).text}`);
  } else {
    lines.push("passed");
  }
  if (op.reason) lines.push(op.reason);
  if (op.thesis_level) lines.push("thesis-level decision"); // logged once for the thesis, not this name alone
  return { title: "operator", lines };
}

const LIFECYCLE_TITLE: Record<LifecycleKind, string> = {
  warmed: "warmed",
  armed: "armed",
  dearmed: "de-armed",
  exit_by: "exit-by (horizon)",
};

function lifecycleTooltip(e: LifecycleChipEvent): TooltipContent {
  // the de-arm reason reads as ENGLISH here (`closeReasonLabel`), not as the wire token — the raw token
  // stays reachable on the components that render it (a `title=`), so nothing is hidden, only translated.
  // Slice C: a composed dearm_detail ANSWERS the why on its own line, so the title drops the
  // "(see de-arm day)" deferral it would otherwise parenthesize — plain "de-armed", answer below.
  const hasDetail = e.kind === "dearmed" && Boolean(e.dearmDetail);
  const title =
    e.kind === "dearmed" && e.closeReason && !hasDetail
      ? `de-armed (${closeReasonLabel(e.closeReason)})`
      : LIFECYCLE_TITLE[e.kind];
  const lines = [e.date];
  if (hasDetail) lines.push(e.dearmDetail!);
  return { title, lines };
}

// A3: the tape chip's EPISTEMICS — the two lines that keep a computed indicator from being read as
// recorded call history. Line 1 always: these are re-derived at the drawer's as-of on every open (a
// different as-of can yield a different cross), so the read is dated by construction (#6, show the work).
// Line 2 on `sma_position` only: the backend emits the LATEST flip per key and nothing earlier
// (backend/signals/display/sma.py — `_last_flip`), so a chart showing one 50d cross across two years is
// the contract, not a dropped event; without the line a missing older cross reads as a bug (#9's spirit:
// an omission is stated, never silent). Relative-strength events aren't flips (an RS-high print dated at
// the last bar), so the line would be a lie there — hence per-kind, not blanket.
const TAPE_ONLY_LATEST_FLIP = "most recent flip only — earlier crosses not shown";

function signalTooltip(e: SignalChipEvent): TooltipContent {
  const lines = [`display-only tape read · derived as-of ${e.asof}`];
  if (e.signalKind === "sma_position") lines.push(TAPE_ONLY_LATEST_FLIP);
  return { title: e.event.label, lines };
}

/** The hover card for a chip — a pure builder (jsdom can't test coordinates, so test THIS). Always
 *  available: the number is never a dead reference (#6). */
export function overlayTooltip(e: OverlayEvent): TooltipContent {
  if (e.family === "insider") return insiderTooltip(e);
  if (e.family === "sell") return sellTooltip(e);
  if (e.family === "filing") return filingTooltip(e);
  if (e.family === "activist") return activistTooltip(e);
  if (e.family === "trigger") return triggerTooltip(e);
  if (e.family === "risk") return riskTooltip(e);
  if (e.family === "operator") return operatorTooltip(e);
  if (e.family === "signal") return signalTooltip(e);
  return lifecycleTooltip(e);
}

const FAMILY_META: Record<OverlayFamily, { label: string; cls: string }> = {
  insider: { label: "insider buy", cls: "ov-insider" },
  sell: { label: "insider sell", cls: "ov-sell" }, // Slice B — muted negative hue
  trigger: { label: "arm trigger", cls: "ov-trigger" },
  // Slice C — the call's own risk tape: muted negative like the sell family but HOLLOW (outline vs
  // fill), so "the record flagged risk" never blurs with "an insider sold" mid-scan (#7 quiet).
  risk: { label: "risk signal", cls: "ov-risk" },
  activist: { label: "activist stake", cls: "ov-activist" }, // Slice B — 13D at weight; 13G greys
  filing: { label: "8-K filing", cls: "ov-filing" }, // Slice B — grey, the common-tape family (#7)
  lifecycle: { label: "lifecycle", cls: "ov-lifecycle" },
  operator: { label: "operator", cls: "ov-operator" }, // A2 — muted --incub, the quiet family (#7)
  // A3 — the GREYEST family on the board, quieter even than the operator's muted --incub: a computed
  // tape read is context beside the call, never the call (#7's inverse loudness, read as hue).
  signal: { label: "tape signal", cls: "ov-signal" },
};
const FAMILY_ORDER: OverlayFamily[] = [
  "insider",
  "sell",
  "trigger",
  "risk",
  "activist",
  "filing",
  "lifecycle",
  "operator",
  "signal",
];

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
