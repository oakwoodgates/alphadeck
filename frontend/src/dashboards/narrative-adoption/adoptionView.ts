import { GROUPS, lastOnOrBefore, monthEnd, type Group } from "./adoptionLayout";
import type {
  AdoptionCardOut,
  AdoptionMonthOut,
  AdoptionOut,
  AdoptionOverlayOut,
  AdoptionRunOut,
  AdoptionTermOut,
} from "./useNarrativeAdoption";

// The Narrative Adoption's pure words (docs/dashboards/narrative-adoption.md): every string a person reads on the
// page that is built from the payload, neutral (what the data shows, never a verdict: no "early", no "crowded").
// Every number is computed server-side; this only words it.

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

const num = (n: number) => n.toLocaleString("en-US");

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A month ("Sep 2026") from its first day or a "2026-09" prefix. */
export function fmtMonth(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d.slice(0, 7)}-01T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly AdoptionCardOut[], query: string): AdoptionCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

// --- the form groups -----------------------------------------------------------------------------------------

export const GROUP_LABEL: Record<Group, string> = {
  reports: "Reports",
  events: "8-Ks",
  issuance: "Issuance",
  other: "Other",
};

/** What each group holds, for the legend's hover. */
export const GROUP_FORMS: Record<Group, string> = {
  reports: "annual and quarterly reports: 10-K, 10-Q, 20-F, 40-F, 6-K",
  events: "8-K current reports",
  issuance: "new-issuance paper: S-1, S-3, F-1, F-3, S-11, 424B prospectuses, FWP",
  other: "every other form",
};

/** A month's groups as words ("Reports 301 · 8-Ks 44 · Issuance 60 · Other 7"). */
export function groupWords(m: Pick<AdoptionMonthOut, Group>): string {
  return GROUPS.map((g) => `${GROUP_LABEL[g]} ${num(m[g] ?? 0)}`).join(" · ");
}

// --- the month readout ---------------------------------------------------------------------------------------

/** The overlay at a month: the last session on or before its end (the as-of caps it), as one line, or why there
 *  is none. */
export function overlayWords(overlay: AdoptionOverlayOut, monthFirst: string, asof: string): string {
  if (overlay.dates.length === 0) return "the tape: no line to read";
  const end = monthEnd(monthFirst) < asof ? monthEnd(monthFirst) : asof;
  const i = lastOnOrBefore(overlay.dates, end);
  if (i < 0) return `the tape: no session yet (the lines start ${fmtDay(overlay.dates[0])})`;
  const parts = [`the tape on ${fmtDay(overlay.dates[i])}`];
  parts.push(overlay.card ? `card line ${overlay.card[i].toFixed(1)}` : "no card line");
  parts.push(overlay.spy ? `SPY ${overlay.spy[i].toFixed(1)}` : "no SPY line");
  parts.push(`both 100 on ${fmtDay(overlay.dates[0])}`);
  return parts.join(" · ");
}

/** The readout for one month: its numbers, its top terms, the filers, and what limits them. `terms` = the card's
 *  SIGNAL term count (a month resting on fewer reads as a floor). */
export function monthReadout(m: AdoptionMonthOut, data: Pick<AdoptionOut, "asof" | "overlay" | "counts">): string[] {
  const title = fmtMonth(m.month);
  if (!m.counted) {
    return [
      m.closed
        ? `${title}: no count stored for this month (a failed or deferred month is left blank, never shown as zero)`
        : `${title}: the month had not closed at the as-of, so it is not counted yet`,
      overlayWords(data.overlay, m.month, data.asof),
    ];
  }
  const lines: string[] = [];
  lines.push(
    `${title}: ${plural(m.documents ?? 0, "document", "documents")} (${groupWords(m)}) · summed over ${plural(m.terms_counted, "term", "terms")}`,
  );
  if (m.top_terms.length > 0) {
    lines.push(`most documents: ${m.top_terms.map((t) => `${t.term} ${num(t.documents)}`).join(" · ")}`);
  }
  const filers =
    m.filers == null ? "—" : m.filers_sampled ? `at least ${num(m.filers)} (a term showed only part of its filers)` : num(m.filers);
  const fresh = m.new_filers == null ? "no earlier month to compare with" : `${num(m.new_filers)} new`;
  lines.push(`filers: ${filers} · ${fresh}`);
  const notes: string[] = [];
  if (m.terms_counted < data.counts.signal_terms) {
    notes.push(
      `only ${m.terms_counted} of ${plural(data.counts.signal_terms, "term", "terms")} stored for this month: the sums are a floor`,
    );
  }
  if (m.read_early) notes.push("counted before the month closed: partial until the next weekly run");
  if (notes.length > 0) lines.push(notes.join(" · "));
  lines.push(overlayWords(data.overlay, m.month, data.asof));
  return lines;
}

// --- the term table ------------------------------------------------------------------------------------------

/** A window's documents ("412", or "412 (2 of 3 months)" when some of its months are not stored; "—" = none). */
export function windowWords(value: number | null, stored: number, width: number): string {
  if (value == null) return "—";
  return stored < width ? `${num(value)} (${stored} of ${width} months)` : num(value);
}

export function filersWords(t: Pick<AdoptionTermOut, "filers" | "filers_sampled">): string {
  if (t.filers == null) return "—";
  return t.filers_sampled ? `at least ${num(t.filers)}` : num(t.filers);
}

/** The month a term first had a document in the stored window; "or earlier" when that is its earliest stored month. */
export function firstWords(t: Pick<AdoptionTermOut, "first_month" | "first_at_start">): string {
  if (t.first_month == null) return "no document in the stored months";
  return t.first_at_start ? `${fmtMonth(t.first_month)} or earlier` : fmtMonth(t.first_month);
}

export const NOT_COUNTED = "not counted yet";

// --- the quiet lines -----------------------------------------------------------------------------------------

function cacheWords(d: AdoptionOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The job's newest run, as one phrase (this card's share of what it could not read beside the totals). */
export function runWords(r: AdoptionRunOut | null | undefined): string {
  if (!r) return "no run of the weekly count on record";
  const span = r.months_from === r.months_to ? fmtMonth(r.months_to) : `${fmtMonth(r.months_from)} to ${fmtMonth(r.months_to)}`;
  const head = `last count run ${r.finished_on ? fmtDay(r.finished_on) : "(unfinished)"} (${r.schedule}, ${r.status}): ${span}, ${plural(r.requests, "request", "requests")}`;
  const card =
    r.card_failed_requests > 0 || r.card_deferred_months > 0
      ? `this card: ${plural(r.card_failed_requests, "failed request", "failed requests")}, ${plural(r.card_deferred_months, "deferred month", "deferred months")}, left blank`
      : "this card: nothing failed or deferred";
  return `${head} · ${card}`;
}

/** The quiet line that shows the work, every number off the payload. */
export function workLine(d: AdoptionOut): string {
  const c = d.counts;
  const o = d.overlay;
  return [
    `${d.thesis_name} as of ${fmtDay(d.asof)}`,
    d.clock === "public" ? "public clock" : "live read",
    `${plural(c.terms_counted, "SIGNAL term", "SIGNAL terms")} counted of ${num(c.signal_terms)}${c.terms_not_counted > 0 ? `, ${num(c.terms_not_counted)} ${NOT_COUNTED}` : ""}`,
    `${plural(c.broad_terms_left_out, "BROAD term", "BROAD terms")} left out`,
    c.retired_terms > 0 ? `${plural(c.retired_terms, "stored term", "stored terms")} no longer in the card's term set, ignored` : null,
    `months stored ${num(c.months_counted)} of ${num(c.months_total)}${c.months_counted > 0 ? `, ${num(c.months_complete)} with every term` : ""}`,
    c.months_sampled > 0 ? `filers read "at least" in ${plural(c.months_sampled, "month", "months")}` : null,
    c.months_read_early > 0 ? `${plural(c.months_read_early, "month", "months")} counted before they closed` : null,
    runWords(d.last_run),
    `the tape: ${num(o.priced)} of ${plural(o.names, "name", "names")} priced${o.flagged.length > 0 ? `, ${num(o.flagged.length)} left out for a flagged price history` : ""}${o.silent_left_out > 0 ? `, ${plural(o.silent_left_out, "Context or Bench row", "Context or Bench rows")} left out` : ""}, equal weight, rebalanced daily`,
    o.session_source === "companies" ? "sessions from the names' bars (no SPY tape)" : null,
    cacheWords(d),
  ]
    .filter(Boolean)
    .join(" · ");
}
