import type {
  EvidenceLedgerCardOut,
  EvidenceLedgerDayOut,
  EvidenceLedgerOut,
  EvidenceLedgerRowOut,
  EvidenceLedgerTalliesOut,
} from "./useEvidenceLedger";

// The Evidence Ledger's pure view helpers (docs/dashboards/evidence-ledger.md): the classes' and kinds' words, the
// client-side filters (class, kind, company, "only rows with an EDGAR link", the date window, "Counting names
// only"), the byline of an operator row and the "show the work" lines. Every row and every count is computed
// server-side; this only filters and words them. Nothing here hides a row from the counts: the tallies cover every
// row, whatever the filters leave.

export type Authority = EvidenceLedgerRowOut["authority"];
export type LedgerKind = EvidenceLedgerRowOut["kind"];

/** The classes in the server's order (`AUTHORITY_ORDER`): a person's rows first. */
export const AUTHORITY_ORDER: readonly Authority[] = ["operator", "system", "automatic", "card"];

/** The quiet word on a row. A label of WHO put the row there, never a judgment of it. */
export const AUTHORITY_TAG: Record<Authority, string> = {
  operator: "your",
  system: "computed",
  automatic: "stored",
  card: "card",
};

/** The class's name on its filter button. Exhaustive against the generated enum: a class added server-side fails
 *  the build here instead of rendering blank. */
export const AUTHORITY_LABEL: Record<Authority, string> = {
  operator: "Your",
  system: "Computed",
  automatic: "Stored",
  card: "Card",
};

/** What each class means (the plan's table), shown on hover and as the first line of an expanded row. */
export const AUTHORITY_MEANS: Record<Authority, string> = {
  operator: "a person ratified or set it",
  system: "a computation on public data (a trigger or a risk signal)",
  automatic: "an ingest leg stored it; no person ratified it",
  card: "an entry the operator maintains on the card",
};

/** The kinds in the server's order (`KIND_ORDER`). */
export const KIND_ORDER: readonly LedgerKind[] = [
  "trigger",
  "risk_signal",
  "ratified",
  "catalyst_fact",
  "card_catalyst",
  "kill",
  "evidence",
  "role",
];

export const KIND_LABEL: Record<LedgerKind, string> = {
  trigger: "Triggers",
  risk_signal: "Risk signals",
  ratified: "Ratified and stored facts",
  catalyst_fact: "Catalyst facts",
  card_catalyst: "Card catalysts",
  kill: "Kill criteria",
  evidence: "Evidence links",
  role: "Context roles",
};

export type WindowChoice = 30 | 90 | 365 | "all";
export const WINDOW_OPTIONS: readonly { value: WindowChoice; label: string }[] = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "365 days" },
  { value: "all", label: "All rows held" },
];

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

/** The first day (YYYY-MM-DD) a window of `days` days ending on `asof` holds. */
export function windowStart(asof: string, days: number): string {
  const t = Date.parse(`${asof}T00:00:00Z`);
  return new Date(t - days * 86_400_000).toISOString().slice(0, 10);
}

// --- the picker --------------------------------------------------------------------------------------------------

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly EvidenceLedgerCardOut[], query: string): EvidenceLedgerCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

// --- the filters (client side) -----------------------------------------------------------------------------------

export type Filters = {
  /** the classes shown (all by default) */
  authorities: ReadonlySet<Authority>;
  /** the kinds shown (all by default) */
  kinds: ReadonlySet<LedgerKind>;
  /** a ticker query; "" = every company. A row with no company (a card entry) does not match a query. */
  name: string;
  /** only a row with a source that links to EDGAR */
  edgarOnly: boolean;
  /** the dated rows within this many days of the as-of; "all" = every row held. Standing rows have no date and
   *  are never cut by it. */
  window: WindowChoice;
  /** leave the rows of Context and Bench companies out */
  countingOnly: boolean;
};

export const DEFAULT_FILTERS: Filters = {
  authorities: new Set(AUTHORITY_ORDER),
  kinds: new Set(KIND_ORDER),
  name: "",
  edgarOnly: false,
  window: "all",
  countingOnly: false,
};

/** Is any filter narrower than the default? */
export function filtersActive(f: Filters): boolean {
  return (
    f.authorities.size < AUTHORITY_ORDER.length ||
    f.kinds.size < KIND_ORDER.length ||
    f.name.trim() !== "" ||
    f.edgarOnly ||
    f.window !== "all" ||
    f.countingOnly
  );
}

/** Does the row pass every filter? A dated row older than the window is out; a future-dated one and a standing
 *  one are never cut by it. */
export function rowMatches(r: EvidenceLedgerRowOut, f: Filters, asof: string): boolean {
  if (!f.authorities.has(r.authority) || !f.kinds.has(r.kind)) return false;
  if (f.countingOnly && r.silent) return false;
  if (f.edgarOnly && !r.edgar) return false;
  const q = f.name.trim().toLocaleLowerCase();
  if (q && !(r.ticker ?? "").toLocaleLowerCase().includes(q)) return false;
  if (f.window !== "all" && r.date != null && r.date < windowStart(asof, f.window)) return false;
  return true;
}

/** The days with only the rows that pass the filters; a day left empty is dropped. */
export function filterDays(
  days: readonly EvidenceLedgerDayOut[],
  f: Filters,
  asof: string,
): EvidenceLedgerDayOut[] {
  const out: EvidenceLedgerDayOut[] = [];
  for (const d of days) {
    const rows = d.rows.filter((r) => rowMatches(r, f, asof));
    if (rows.length > 0) out.push({ ...d, rows });
  }
  return out;
}

export const filterStanding = (rows: readonly EvidenceLedgerRowOut[], f: Filters, asof: string) =>
  rows.filter((r) => rowMatches(r, f, asof));

export const countRows = (days: readonly EvidenceLedgerDayOut[]) => days.reduce((n, d) => n + d.rows.length, 0);

// --- words -------------------------------------------------------------------------------------------------------

/** The count for a key in a tally list (a class or a kind); 0 when absent. */
export const countOf = (list: readonly { key: string; rows: number }[], key: string) =>
  list.find((c) => c.key === key)?.rows ?? 0;

/** An operator row's byline: who and when ("ratified by operator · Oct 20, 2026"; a role is "set by"). A row
 *  with no recorded day says so rather than leaving it out. */
export function byWords(r: Pick<EvidenceLedgerRowOut, "kind" | "by" | "on">): string | null {
  if (!r.by) return null;
  const verb = r.kind === "role" ? "set by" : "ratified by";
  return r.on ? `${verb} ${r.by} · ${fmtDay(r.on)}` : `${verb} ${r.by} · day not recorded`;
}

/** How the fired signals trace, for the header: an accession, a price reference only, another source, none. */
export function traceWords(t: EvidenceLedgerTalliesOut): string | null {
  if (t.signals === 0) return null;
  const parts = [
    `${t.trace_accession} of ${plural(t.signals, "fired signal", "fired signals")} trace${t.signals === 1 ? "s" : ""} to an EDGAR accession`,
  ];
  if (t.trace_price > 0) parts.push(`${t.trace_price} to a price reference only`);
  if (t.trace_other > 0) parts.push(`${t.trace_other} to another source`);
  if (t.trace_none > 0) parts.push(`${t.trace_none} state${t.trace_none === 1 ? "s" : ""} no source`);
  return parts.join(" · ");
}

function cacheWords(d: EvidenceLedgerOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** Which recorded night the triggers come from. */
export function nightWords(d: Pick<EvidenceLedgerOut, "night" | "night_unreadable">): string {
  if (d.night_unreadable) return "a call is recorded for this as-of but its card could not be read, so no trigger is listed";
  if (!d.night) return "no call recorded by this as-of, so no trigger is listed";
  return d.night.reconstructed
    ? `triggers from the call recorded for ${fmtDay(d.night.asof)} (a reconstructed night: reported, never scored)`
    : `triggers from the call recorded for ${fmtDay(d.night.asof)}`;
}

/** The quiet lines under the ledger: the card and the clock, the recorded call, the rows by class, the kinds the
 *  card holds, the roster. `shown` = rows left after the filters. */
export function workLines(d: EvidenceLedgerOut, shown: number, filtering: boolean): string[] {
  const t = d.tallies;
  const resolved = d.names.filter((n) => n.security_id != null).length;
  const byClass = AUTHORITY_ORDER.map((a) => `${countOf(t.by_authority, a).toLocaleString("en-US")} ${AUTHORITY_TAG[a]}`);
  const kinds = [
    `${plural(t.facts_ratified, "ratified fact", "ratified facts")} and ${plural(t.facts_stored, "stored fact", "stored facts")} (the last ${d.facts_days} days)`,
    `${plural(t.catalysts_dated, "catalyst", "catalysts")} dated, ${t.catalysts_undated} undated`,
    plural(countOf(t.by_kind, "kill"), "kill criterion", "kill criteria"),
    plural(countOf(t.by_kind, "evidence"), "evidence link", "evidence links"),
    `${plural(t.roles_dated + t.roles_undated, "context role", "context roles")}${t.roles_undated > 0 ? ` (${t.roles_undated} undated)` : ""}`,
  ];
  return [
    [
      `as of ${fmtDay(d.asof)}`,
      d.clock === "record" ? "live read" : `as it was public by ${fmtDay(d.asof)}`,
      nightWords(d),
      cacheWords(d),
    ].join(" · "),
    [
      `${plural(t.rows, "row", "rows")}: ${byClass.join(" · ")}`,
      filtering ? `${shown.toLocaleString("en-US")} shown after the filters` : null,
    ]
      .filter(Boolean)
      .join(" · "),
    kinds.join(" · "),
    [
      plural(d.basket_rows, "roster row", "roster rows"),
      `${plural(resolved, "company", "companies")} resolved`,
      `${d.unresolved.length} without a resolved security`,
      `${d.silent_rows} in Context or Bench`,
    ].join(" · "),
  ];
}

/** The roster note: names with no resolved security can carry no fact, so they are listed, never silently
 *  dropped (invariant #9). */
export function unresolvedWords(tickers: readonly string[]): string | null {
  if (tickers.length === 0) return null;
  return `${plural(tickers.length, "roster name has", "roster names have")} no resolved security, so no stored fact can be attached to ${tickers.length === 1 ? "it" : "them"}: ${tickers.join(", ")}.`;
}

/** Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in. */
export const ROSTER_PIT_FROM = "2026-09-15";

/** What the ledger does NOT hold, named on the page: none of these is evidence. */
export const NOT_EVIDENCE =
  "Not in the ledger, because none is evidence: display signals (tape context beside the call, never an input to it), thesis-fit prose (a drafted description, not a fact) and located passages (not stored).";

/** The page's known limits, stated on the page (docs/dashboards/evidence-ledger.md). */
export const LIMITS: readonly string[] = [
  "The card's own entries (catalysts, kill criteria, evidence links) load live: they are not snapshotted, so they show as they are today whatever the as-of. The triggers, the facts and the roster are as of the as-of.",
  "The evidence table is nearly unused today: the ledger's substance is the recorded call's triggers, the stored and ratified facts, and the card's catalysts and kill criteria.",
  "A trigger's inputs are the computation's own, shown as stored; the page does not re-derive them. Located passages are not stored, so a ratified FLAG links to its filing, not its passage.",
  "The class is a label of who put a row there, never a judgment of it. The stored and ratified facts are read for the last 365 days only; a catalyst fact counts under its event date, a ratified fact under the day it was ratified.",
];
