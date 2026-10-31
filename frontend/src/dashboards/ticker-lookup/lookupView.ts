import { CONTEXT_ROLE_LABEL } from "../../workbench/format";
import { STATE_LABEL, verdictLabel } from "../../util/format";
import type {
  LookupCap,
  LookupCard,
  LookupFiling,
  LookupFilingDay,
  LookupFigure,
  LookupLink,
  LookupPlace,
  LookupSource,
  LookupSparkline,
  LookupTrigger,
  TickerLookupOut,
} from "./useTickerLookup";

// The Ticker Lookup's pure view helpers (docs/dashboards/ticker-lookup.md): the words and figures of each section,
// the filings window and the day fold, the sparkline paths and the "show the work" lines. Every number is computed
// server-side from readers the other surfaces already use; this only formats, folds and words them. Nothing here
// ranks or judges a company (#4).

/** The URL parameter that carries the looked-up ticker, beside the shell's `?asof=`. */
export const T_PARAM = "t";

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- dates -------------------------------------------------------------------------------------------------------

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A day header ("Wed, Oct 28, 2026"). */
export function fmtDayHeader(d: string): string {
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** The ISO date `n` calendar days before `d` (UTC arithmetic, so a DST change never moves it). */
export function daysBefore(d: string, n: number): string {
  const t = Date.parse(`${d}T00:00:00Z`);
  return Number.isNaN(t) ? d : new Date(t - n * 86_400_000).toISOString().slice(0, 10);
}

// --- figures -----------------------------------------------------------------------------------------------------

const UNITS = [
  { suffix: "", size: 1, decimals: 0 },
  { suffix: "K", size: 1e3, decimals: 1 },
  { suffix: "M", size: 1e6, decimals: 1 },
  { suffix: "B", size: 1e9, decimals: 1 },
] as const;

/** A dollar figure in one short piece ("$49.2K", "$350.0M"). The figure is rounded to what is shown and one that
 *  rounds up to 1,000 moves to the next unit, so 999,950 is "$1.0M", never "$1000.0K" (the server's `fmt_compact`). */
export function compactUsd(v: number): string {
  const a = Math.abs(v);
  let i = 0;
  while (i < UNITS.length - 1 && a >= UNITS[i + 1].size) i++;
  while (i < UNITS.length - 1 && Number((a / UNITS[i].size).toFixed(UNITS[i].decimals)) >= 1000) i++;
  const u = UNITS[i];
  return `${v < 0 ? "-" : ""}$${(a / u.size).toFixed(u.decimals)}${u.suffix}`;
}

/** A share price: cents above a dollar, four places below. */
export const fmtPrice = (v: number) => `$${v.toFixed(Math.abs(v) < 1 ? 4 : 2)}`;

/** A figure as the tape grid shows it: its unit, a sign on a toned figure, "n/a" when the member could not state
 *  it (the member's own note rides the cell's hover). */
export function fmtFigure(f: Pick<LookupFigure, "value" | "unit" | "tone" | "key">): string {
  const v = f.value;
  if (v == null) return "n/a";
  const sign = f.tone === "pos" ? "+" : "";
  switch (f.unit) {
    case "pct":
      return `${sign}${v.toFixed(1)}%`;
    case "usd":
      return `${sign}${compactUsd(v)}`;
    case "price":
      return fmtPrice(v);
    case "count":
      return String(Math.round(v));
    case "ratio":
      return f.key.startsWith("rs_") ? v.toFixed(4) : `${v.toFixed(2)}×`;
    default:
      return String(v);
  }
}

/** Why a market cap is blank (the Peer Table's gap codes). */
export const CAP_GAP_WORDS: Record<string, string> = {
  flagged: "withheld, a data flag reaches its price",
  no_price: "awaiting price",
  no_shares: "awaiting shares",
  ads_unread: "ADS ratio not read",
  no_cap: "share count or close not positive",
};

/** The approximate market cap in one line: "≈ $125.0M · micro", or why it is not stated. Labeled approximate
 *  everywhere. */
export function capWords(cap: LookupCap): string {
  if (cap.value == null) return `not stated: ${cap.gap ? (CAP_GAP_WORDS[cap.gap] ?? cap.gap) : "no value"}`;
  return `≈ ${compactUsd(cap.value)}${cap.tier ? ` · ${cap.tier}` : ""}`;
}

/** The cap's inputs for its hover: the close and the share count it multiplies, the ADS ratio and the split scale. */
export function capTitle(cap: LookupCap): string {
  const parts: string[] = ["Approximate: the latest close times the latest shares fact."];
  if (cap.close != null) parts.push(`close ${fmtPrice(cap.close)} (${fmtDay(cap.close_date)})`);
  if (cap.shares != null) parts.push(`${cap.shares.toLocaleString("en-US")} shares (${fmtDay(cap.shares_date)})`);
  if (cap.ads_ratio != null) parts.push(`÷ ${cap.ads_ratio} ordinary per ADS`);
  if (cap.split_factor != null) parts.push(`split scale ×${cap.split_factor}`);
  if (cap.note) parts.push(cap.note);
  return parts.join(" · ");
}

// --- the cards ---------------------------------------------------------------------------------------------------

/** The operator's weight, display only ("weight 3 of 5"; two different weights on one card read both). */
export function weightWords(links: readonly Pick<LookupLink, "weight">[]): string | null {
  const w = [...new Set(links.map((l) => l.weight).filter((x): x is number => x != null))].sort((a, b) => a - b);
  if (w.length === 0) return null;
  return `weight ${w.join(" and ")} of 5`;
}

/** Who wrote a thesis-fit line: the model's draft until the operator edits it. */
export function authorWords(authoredBy: string): string {
  if (authoredBy === "system_drafted") return "model draft";
  if (authoredBy === "operator_edited" || authoredBy === "operator_set") return "your words";
  return authoredBy;
}

/** A context role as its quiet lowercase tag ("payer", "counter-case"). */
export function roleTag(role: string): string {
  const label = (CONTEXT_ROLE_LABEL as Record<string, string>)[role];
  return (label ?? role).toLowerCase();
}

/** The card's recorded state in effect, in the app's own words ("Armed"), or the absence of a record. */
export function stateWords(row: Pick<LookupCard, "state" | "recorded_on" | "reconstructed">): string {
  if (row.state == null) return "no recorded call yet";
  const label = STATE_LABEL[row.state] ?? row.state;
  const when = row.recorded_on ? ` · ${fmtDay(row.recorded_on)}` : "";
  return `${label}${when}${row.reconstructed ? " (reconstructed)" : ""}`;
}

const KEY_WORDS: Record<string, string> = { conviction: "conviction", confirmation: "confirmation" };

/** The company's place on the card in effect, in words. `armed` marks the one accent of the page. */
export function placeWords(p: LookupPlace): { text: string; armed: boolean } {
  const risk = p.live_risks > 0 ? ` · ${plural(p.live_risks, "risk signal", "risk signals")}` : "";
  switch (p.kind) {
    case "armed": {
      const bits = [
        "armed",
        p.verdict ? verdictLabel(p.verdict) : null,
        p.grade,
        p.strength != null ? `setup strength ${Math.round(p.strength * 100)}%` : null,
      ].filter(Boolean);
      return { text: `${bits.join(" · ")}${risk}`, armed: true };
    }
    case "watch":
      return { text: `watch${risk}`, armed: false };
    case "triggers": {
      const keys = p.turns.map((k) => KEY_WORDS[k] ?? k);
      const turns = keys.length > 0 ? ` · turns ${keys.join(" and ")}` : "";
      return { text: `${plural(p.live_triggers, "live trigger", "live triggers")}${turns}${risk}`, armed: false };
    }
    case "none":
      return { text: `none${risk}`, armed: false };
    case "not_counted":
      return { text: "not counted", armed: false };
    case "no_record":
      return { text: "no recorded call yet", armed: false };
  }
}

// --- the triggers ------------------------------------------------------------------------------------------------

/** The role as a glyph, not a color: filled = an entry trigger, hollow = a risk signal. */
export const ROLE_GLYPH: Record<LookupTrigger["role"], { glyph: string; label: string }> = {
  entry_trigger: { glyph: "●", label: "Entry trigger" },
  risk_signal: { glyph: "○", label: "Risk signal" },
};

/** The key an entry trigger turns, or "risk" for a risk signal. */
export function keyWords(e: Pick<LookupTrigger, "role" | "turns">): string {
  if (e.role === "risk_signal") return "risk";
  return e.turns ?? "no key";
}

/** The fire date and, when the record saw it later, how much later. */
export function firedWords(e: Pick<LookupTrigger, "event_date" | "recorded_after_days">): string {
  if (!e.event_date) return "undated";
  const note =
    e.recorded_after_days != null ? ` · recorded ${plural(e.recorded_after_days, "day", "days")} after` : "";
  return `${fmtDay(e.event_date)}${note}`;
}

/** The hover on a label: the label itself (the first recorded night's), and how many distinct other labels the
 *  record showed for the trigger after it (only when it showed any). */
export function labelTitle(e: Pick<LookupTrigger, "label" | "label_changes">): string {
  return e.label_changes > 0 ? `${e.label}\nlabel changed ${plural(e.label_changes, "time", "times")}` : e.label;
}

/** A source in one piece: a link's label when it has a URL, the bar date for a `price:` reference, else its
 *  reference as stored. */
export function sourceLabel(s: Pick<LookupSource, "source" | "ref" | "url">): string {
  if (s.url) return s.url.includes("sec.gov") ? `${s.source} · EDGAR` : s.source;
  if (s.ref.startsWith("price:")) {
    const day = s.ref.split(":").at(-1) ?? "";
    return /^\d{4}-\d{2}-\d{2}$/.test(day) ? `price · ${day}` : s.ref;
  }
  return s.ref ? `${s.source} · ${s.ref}` : s.source;
}

export const triggerCounts = (events: readonly LookupTrigger[]) => ({
  entry: events.filter((e) => e.role === "entry_trigger").length,
  risk: events.filter((e) => e.role === "risk_signal").length,
  live: events.filter((e) => e.live).length,
});

// --- the tape ----------------------------------------------------------------------------------------------------

export type FigureGroup = LookupFigure["group"];

/** The grid's groups in order, each with its heading. The price-derived ones gray when a data flag reaches the
 *  price (the Cockpit's grayed cells); the insider and fund-flow groups read no price and stay. */
export const GROUPS: readonly { group: FigureGroup; label: string; price: boolean }[] = [
  { group: "price", label: "Price", price: true },
  { group: "returns", label: "Returns", price: true },
  { group: "range", label: "52-week range", price: true },
  { group: "volume", label: "Volume", price: true },
  { group: "strength", label: "Strength", price: true },
  { group: "insider", label: "Insiders", price: false },
  { group: "flow", label: "Fund flow", price: false },
];

export function figuresOf(figures: readonly LookupFigure[], group: FigureGroup): LookupFigure[] {
  return figures.filter((f) => f.group === group);
}

/** A figure's hover: its label, the exact value, the level and day it is measured from, and the member's note. */
export function figureTitle(f: LookupFigure): string {
  const bits = [f.label];
  if (f.value != null) bits.push(String(f.value));
  if (f.level != null) bits.push(`from ${fmtPrice(f.level)}${f.on ? ` on ${fmtDay(f.on)}` : ""}`);
  if (f.note) bits.push(f.note);
  return bits.join(" · ");
}

export type SparkPaths = { stock: string; iwm: string };

/** The sparkline as two SVG paths on one shared scale (the company and IWM, both indexed to 100): x by session,
 *  a missing bar breaks the line and is never interpolated, a lone point draws nothing. */
export function sparkPaths(s: LookupSparkline, width: number, height: number, pad = 2): SparkPaths {
  const all = [...s.stock, ...s.iwm].filter((v): v is number => v != null);
  if (all.length === 0) return { stock: "", iwm: "" };
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const n = Math.max(s.stock.length - 1, 1);
  const x = (i: number) => pad + (i / n) * (width - 2 * pad);
  const y = (v: number) => height - pad - ((v - lo) / span) * (height - 2 * pad);
  const path = (series: readonly (number | null)[]) => {
    const segments: string[] = [];
    let run: string[] = [];
    const flush = () => {
      if (run.length > 1) segments.push(run.join(" "));
      run = [];
    };
    series.forEach((v, i) => {
      if (v == null) flush();
      else run.push(`${run.length === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`);
    });
    flush();
    return segments.join(" ");
  };
  return { stock: path(s.stock), iwm: path(s.iwm) };
}

// --- the filings -------------------------------------------------------------------------------------------------

export const FILING_WINDOWS: readonly { value: number; label: string }[] = [
  { value: 90, label: "90 days" },
  { value: 365, label: "365 days" },
];
export const DEFAULT_FILING_DAYS = 90;

/** The filing days inside the window ending at the as-of (inclusive), newest first as the server sent them. */
export function windowDays(days: readonly LookupFilingDay[], asof: string, n: number): LookupFilingDay[] {
  const start = daysBefore(asof, n);
  return days.filter((d) => d.date >= start && d.date <= asof);
}

export const countFilings = (days: readonly LookupFilingDay[]) => days.reduce((n, d) => n + d.rows.length, 0);

export type FoldGroup = NonNullable<LookupFiling["fold"]>;
/** A routine row the server sent without a fold group is a Form 4 (the reader's own default). */
export const foldGroupOf = (r: Pick<LookupFiling, "fold">): FoldGroup => r.fold ?? "form4";

export type Entry =
  | { type: "row"; row: LookupFiling }
  | { type: "fold"; group: FoldGroup; rows: LookupFiling[] };

/** A day's lines in order. With `showRoutine` off, its routine rows collapse into ONE fold line per fold group
 *  (the honest-loudness rule: the common noise goes quiet, nothing is removed); on, every row is a line. */
export function foldEntries(rows: readonly LookupFiling[], showRoutine: boolean): Entry[] {
  if (showRoutine) return rows.map((row) => ({ type: "row", row }));
  const out: Entry[] = [];
  const folds = new Map<FoldGroup, Extract<Entry, { type: "fold" }>>();
  for (const row of rows) {
    if (!row.routine) {
      out.push({ type: "row", row });
      continue;
    }
    const group = foldGroupOf(row);
    const fold = folds.get(group);
    if (fold) fold.rows.push(row);
    else {
      const made: Extract<Entry, { type: "fold" }> = { type: "fold", group, rows: [row] };
      folds.set(group, made);
      out.push(made);
    }
  }
  return out;
}

const isForm144 = (form: string) => form.trim().toUpperCase().startsWith("144");

/** A fold line's words. */
export function foldWords(e: Extract<Entry, { type: "fold" }>): string {
  const n = e.rows.length;
  if (e.group === "form4")
    return `${plural(n, "routine Form 4 filing", "routine Form 4 filings")} (none holds a purchase: sales, grants, exercises, withholding, other codes)`;
  if (e.group === "prospectus")
    return `${plural(n, "prospectus supplement", "prospectus supplements")} (424B, FWP)`;
  const what = [
    e.rows.some((r) => !isForm144(r.form)) ? "Forms 3/4/5 with no transaction rows stored" : null,
    e.rows.some((r) => isForm144(r.form)) ? "144 notices" : null,
  ].filter(Boolean);
  return `${plural(n, "insider ownership filing", "insider ownership filings")} (${what.join(", ")})`;
}

/** The compact dollar hint at the end of a filing line: a Form 4's net purchases (signed), a dilution's principal. */
export function usdHint(r: Pick<LookupFiling, "kind" | "usd">): string | null {
  if (r.usd == null) return null;
  if (r.kind === "form4" || r.kind === "form4a") return `${r.usd > 0 ? "+" : ""}${compactUsd(r.usd)} net`;
  if (r.kind === "dilution") return compactUsd(r.usd);
  return null;
}

/** Who ratified a fact and when, when a person did. */
export function ratifiedWords(r: Pick<LookupFiling, "ratifier" | "ratified_on">): string | null {
  if (!r.ratifier) return null;
  return `ratified by ${r.ratifier}${r.ratified_on ? ` on ${fmtDay(r.ratified_on)}` : ""}`;
}

// --- the decisions -----------------------------------------------------------------------------------------------

/** The operator's own fill as logged ("100 sh @ $10.50"), or nothing logged. */
export function fillWords(d: { shares: number | null; price: number | null }): string {
  const parts = [
    d.shares != null ? `${d.shares.toLocaleString("en-US")} sh` : null,
    d.price != null ? `@ ${fmtPrice(d.price)}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : "—";
}

// --- the show-the-work lines -------------------------------------------------------------------------------------

function cacheWords(d: TickerLookupOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s, first load since the last run or basket edit`;
}

/** The quiet lines under the page: the as-of and clock, the cards, the triggers, the tape, the filings, the flags,
 *  the cache. `inWindow` = filings in the window shown, `windowN` = that window's days. */
export function workLines(d: TickerLookupOut, inWindow: number, windowN: number): string[] {
  const c = d.cards;
  const t = d.triggers;
  const tc = triggerCounts(t.events);
  const silent = c.rows.filter((r) => r.silent != null).length;
  const tape = d.tape;
  const lines = [
    [
      `as of ${fmtDay(d.asof)}`,
      d.clock === "record" ? "live read" : `as it was public by ${fmtDay(d.asof)}`,
      cacheWords(d),
    ].join(" · "),
    [
      `${plural(c.counted, "card", "cards")} counted (of ${c.live_cards} live; ${c.not_yet_created} created after the as-of)`,
      `on ${c.rows.length} (${silent} in Context or Bench)`,
      `${c.archived.length} archived`,
      `excluded from ${c.excluded.length}`,
      c.roster_fallback > 0 ? `${c.roster_fallback} with today's roster standing in` : null,
    ]
      .filter(Boolean)
      .join(" · "),
    [
      `${plural(t.events.length, "trigger", "triggers")} listed since ${fmtDay(t.window_start)} (${tc.entry} entry, ${tc.risk} risk, ${tc.live} live)`,
      `from ${plural(t.nights_read, "recorded night", "recorded nights")} on ${plural(t.cards_read, "card", "cards")}${t.reconstructed_nights > 0 ? ` (${t.reconstructed_nights} reconstructed)` : ""}`,
      t.before_window_live > 0
        ? `${plural(t.before_window_live, "live trigger", "live triggers")} first recorded before the window not listed`
        : null,
    ]
      .filter(Boolean)
      .join(" · "),
    [
      `${tape.members_read - tape.members_without} of ${tape.members_read} display members read`,
      `${plural(tape.figures_blank, "figure", "figures")} with no value`,
    ].join(" · "),
    [
      `${plural(inWindow, "filing", "filings")} in the last ${windowN} days`,
      `${d.filings.rows.toLocaleString("en-US")} in the ${d.filings.read_days}-day read`,
      `${plural(d.catalyst_facts.length, "catalyst fact", "catalyst facts")} under Catalysts`,
    ].join(" · "),
    d.identity.flag
      ? `data flag on its price readings: ${d.identity.flag}`
      : "no data flag reaches its price readings",
  ];
  return lines;
}

/** The routine rows the cap left out of the filings list, in words; `null` when none. */
export function notListedWords(f: Pick<TickerLookupOut["filings"], "not_listed">): string | null {
  const total = f.not_listed.reduce((n, x) => n + x.rows, 0);
  if (total === 0) return null;
  const parts = f.not_listed.map(
    (x) =>
      `${plural(x.rows, "row", "rows")} of ${x.fold ?? "routine"}${x.older_than ? ` on or before ${fmtDay(x.older_than)}` : ""}`,
  );
  return `${plural(total, "routine row", "routine rows")} not listed (over the per-kind cap): ${parts.join(", ")}`;
}

/** The page's known limits, stated on the page (docs/dashboards/ticker-lookup.md). */
export const LIMITS: readonly string[] = [
  "One company at a time. A company on no counted card shows its identity, tape and filings only.",
  "The record's triggers are the live ones the calls log kept: a trigger that lapsed between two recorded nights is not here. They are read from the cards the company sits on at the as-of, over the last 365 days; one first recorded before that window is counted, not listed. One row per trigger (role, kind and fire date): a label that changed night to night is still one row, showing the first night's label (hover it to see how many times it changed).",
  "The market cap is approximate: the latest close times the latest shares fact, with the ADS ratio and any split applied. A data flag on the price withholds it.",
  "Decisions are the operator's own fills, shown as logged; a decision counts from its date and, on a past view, only once it was logged by the end of that day.",
  "The catalysts of a card are the operator's list as stored today, not as they stood on the as-of; a date is marked passed when it falls before the day you act (the session after the as-of's night) on the current view, and before the as-of on a past one.",
];
