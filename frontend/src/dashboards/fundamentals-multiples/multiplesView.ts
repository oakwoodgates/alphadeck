import type {
  MultiplesBarOut,
  MultiplesCardOut,
  MultiplesCompanyOut,
  MultiplesLastPrintOut,
  MultiplesMedianPointOut,
  MultiplesOut,
} from "./useFundamentalsMultiples";

// The Fundamentals Multiples' pure words (docs/dashboards/fundamentals-multiples.md): every string a person reads
// on the page that is built from the payload, neutral (what the data shows, never a verdict). Every number (the
// window, the dots, the medians, the counts) is computed server-side; this only words it.

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** A date without its year when it falls in the as-of's year ("Aug 7"), with it otherwise ("Aug 7, 2025"). */
export function fmtShort(d: string, asof: string): string {
  if (d.slice(0, 4) !== asof.slice(0, 4)) return fmtDay(d);
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** A calendar quarter from the wire ("2026-Q2") as people say it ("Q2 2026"). */
export function quarterWords(q: string): string {
  const m = /^(\d{4})-Q([1-4])$/.exec(q);
  return m ? `Q${m[2]} ${m[1]}` : q;
}

const UNITS = [
  { s: "B", n: 1e9, d: 1 },
  { s: "M", n: 1e6, d: 1 },
  { s: "K", n: 1e3, d: 0 },
  { s: "", n: 1, d: 0 },
] as const;

/** A dollar figure in one compact number ("$1.6B", "$41.2M", "$845K"). The rollover rule: a figure that rounds up
 *  to 1,000 of its unit moves to the next unit, so 999,950 reads "$1.0M", never "$1000K". */
export function compactUsd(v: number): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  let i = UNITS.findIndex((u) => a >= u.n);
  if (i < 0) i = UNITS.length - 1;
  let text = (a / UNITS[i].n).toFixed(UNITS[i].d);
  if (parseFloat(text) >= 1000 && i > 0) {
    i -= 1;
    text = (a / UNITS[i].n).toFixed(UNITS[i].d);
  }
  return `${sign}$${text}${UNITS[i].s}`;
}

/** A year-over-year change in percent ("+34%", "+3.4%", "-40%"): a whole number from ten percent up, one
 *  decimal below it, the sign always stated. */
export function pctWords(p: number): string {
  const text = Math.abs(p) >= 10 ? Math.abs(p).toFixed(0) : Math.abs(p).toFixed(1);
  if (parseFloat(text) === 0) return "0.0%";
  return `${p > 0 ? "+" : "-"}${text}%`;
}

export function ageWords(days: number): string {
  return days === 0 ? "on the as-of day" : days === 1 ? "1 day before the as-of" : `${days.toLocaleString("en-US")} days before the as-of`;
}

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly MultiplesCardOut[], query: string): MultiplesCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

/** The basis in words: a derived quarter says how it was made. */
export function basisWords(basis: string): string {
  return basis === "derived" ? "derived (the fiscal year minus nine months)" : basis;
}

/** One bar's first line: its calendar quarter, period end, value, basis and change on the year before. */
export function barWords(b: MultiplesBarOut): string {
  const yoy =
    b.yoy != null
      ? `${pctWords(b.yoy)} on the same period a year earlier (ending ${fmtDay(b.prior_period_end)})`
      : "no row a year earlier in the read, so no change";
  return [`${quarterWords(b.quarter)}: period ending ${fmtDay(b.period_end)}`, compactUsd(b.value), basisWords(b.basis), yoy].join(
    " · ",
  );
}

/** One bar's filing line: the date that made it public, its accession and, for a re-report, the earlier date it was
 *  first knowable. The accession's EDGAR link is the page's (`b.url`). */
export function filingLine(b: MultiplesBarOut): string {
  const parts = [`dated by its filing: ${fmtDay(b.filed)}`];
  if (b.accession) parts.push(`accession ${b.accession}`);
  if (b.first_knowable)
    parts.push(
      `first knowable ${fmtDay(b.first_knowable)}${b.first_knowable_accession ? ` in accession ${b.first_knowable_accession}` : ""} (re-reported)`,
    );
  return parts.join(" · ");
}

/** A bar's tooltip and accessible name. */
export const barTitle = (b: MultiplesBarOut): string => `${barWords(b)} · ${filingLine(b)}`;

/** One median point's words ("Q2 2026: median +35% of 2 names"; a quarter not drawn says why). */
export function pointWords(p: MultiplesMedianPointOut, counting: number): string {
  if (p.median != null)
    return p.n === 1 && counting === 1
      ? `${quarterWords(p.quarter)}: ${pctWords(p.median)} (1 name)`
      : `${quarterWords(p.quarter)}: median ${pctWords(p.median)} (median of ${plural(p.n, "name", "names")})`;
  return `${quarterWords(p.quarter)}: not drawn (${plural(p.n, "name has", "names have")} a change that quarter; a median needs two)`;
}

/** Under a chart: the last print as words ("Q2 2026 · filed Aug 7 · 83 days before the as-of"). */
export function lastPrintWords(lp: MultiplesLastPrintOut, asof: string): string {
  return `${quarterWords(lp.quarter)} · filed ${fmtShort(lp.filed, asof)} · ${ageWords(lp.days_before)}`;
}

/** The last print's change on the year before, as text. */
export function lastYoyWords(lp: MultiplesLastPrintOut): string {
  return lp.yoy != null ? `${pctWords(lp.yoy)} on the year before` : "no change shown (no row a year earlier)";
}

export function setAsideWords(n: number): string {
  return `${plural(n, "period", "periods")} set aside`;
}

/** What an empty frame says. */
export function emptyWords(c: MultiplesCompanyOut): string {
  switch (c.state) {
    case "unresolved":
      return "no resolved security";
    case "none":
      return "no revenue series stored";
    case "stale":
      return "no revenue in these quarters";
    default:
      return "";
  }
}

/** A chart's accessible name. */
export function companyTitle(c: MultiplesCompanyOut, asof: string): string {
  const parts = [c.name ? `${c.ticker} · ${c.name}` : c.ticker];
  if (c.bars.length > 0) {
    parts.push(plural(c.bars.length, "quarter", "quarters"));
    if (c.state === "thin") parts.push("thin: fewer than five quarters");
  } else {
    parts.push(emptyWords(c));
  }
  if (c.last_print) parts.push(`last print ${lastPrintWords(c.last_print, asof)}`);
  if (c.set_aside > 0) parts.push(setAsideWords(c.set_aside));
  return parts.join(" · ");
}

function cacheWords(d: MultiplesOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work. */
export function workLine(d: MultiplesOut): string {
  const c = d.counts;
  const n = (v: number) => v.toLocaleString("en-US");
  return [
    `${d.thesis_name} as of ${fmtDay(d.asof)}`,
    d.clock === "public" ? "public clock" : "live read",
    `${plural(c.roster_rows, "roster row", "roster rows")} (${n(c.resolved_rows)} resolved, ${n(c.unresolved_rows)} without a resolved security, ${n(c.silent_rows)} in Context or Bench), ${plural(c.companies, "company", "companies")}`,
    `a revenue series for ${n(c.with_series)} of ${plural(c.companies, "name", "names")} (${n(c.without_series)} without)`,
    `${plural(c.thin, "name", "names")} thin (fewer than ${d.thin_below} quarters)`,
    c.stale > 0 ? `${plural(c.stale, "name", "names")} whose series ends before these quarters` : null,
    c.set_aside > 0
      ? `${plural(c.set_aside, "revenue period", "revenue periods")} set aside on this card's names`
      : "no revenue period set aside on this card's names",
    d.axis.length > 0 ? `quarters drawn: ${quarterWords(d.axis[0])} to ${quarterWords(d.axis[d.axis.length - 1])}` : "no quarter to draw",
    c.silent_companies > 0 ? `${plural(c.silent_companies, "Context or Bench name", "Context and Bench names")} never in a median` : null,
    cacheWords(d),
  ]
    .filter(Boolean)
    .join(" · ");
}
