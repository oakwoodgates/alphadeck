import type { components } from "../../api/types.gen";
import { businessTypeLabel } from "../../util/format";
import { CONTEXT_ROLE_DESCRIPTION, contextRoleTag } from "../../workbench/format";
import type { ChainMapCardOut, ChainMapNodeOut, ChainMapOut } from "./useChainMap";

// The Chain Map's pure words (docs/dashboards/chain-map.md): every string a person reads on the page that is built
// from the payload, neutral (what the data shows, never a verdict). Every number (the placement, the 50-day side,
// the filing age, the counts) is computed server-side; this only words it.

type Authorship = components["schemas"]["Authorship"];

/** Who wrote a description, the Cockpit's own words (`operator_set` is retired for basket members and degrades
 *  to the honest "model draft"). */
export const AUTHOR_TAG: Record<Authorship, string> = {
  system_drafted: "model draft",
  operator_set: "model draft",
  operator_edited: "your words",
};

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** A date with its year ("Oct 28, 2026"). */
export function fmtDay(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(`${d}T00:00:00Z`);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly ChainMapCardOut[], query: string): ChainMapCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

/** The links a company sits in on the card ("Turbines · Cables"; a `null` link reads "No link"). */
export const linkWords = (links: readonly (string | null)[]): string =>
  links.map((l) => l ?? "No link").join(" · ");

/** The business-type label a node shows ("" when the master has none: no placeholder, Workbench principle 3). */
export const typeLabel = (n: ChainMapNodeOut): string => (n.business_type ? businessTypeLabel(n.business_type) : "");

/** The age glyph on a node: whole days since the newest filing ("12d"), "—" when none. */
export const ageGlyph = (n: ChainMapNodeOut): string => (n.last_filing ? `${n.last_filing.age_days}d` : "—");

export function ageWords(days: number): string {
  return days === 0 ? "on the as-of day" : days === 1 ? "1 day before the as-of" : `${days} days before the as-of`;
}

/** Where a name sits against its 50-day average, in words. */
export function sideWords(n: ChainMapNodeOut): string {
  if (n.flag != null) return `50-day side left out: ${n.flag}`;
  if (n.security_id == null) return "no resolved security";
  const pct = n.pct_vs_50d == null ? "" : ` (${n.pct_vs_50d > 0 ? "+" : ""}${n.pct_vs_50d.toFixed(1)}%)`;
  switch (n.side_50d) {
    case "above":
      return `above its 50-day average${pct}`;
    case "below":
      return `below its 50-day average${pct}`;
    case "at":
      return "at its 50-day average";
    default:
      return "no 50-day reading (no price, or fewer than 50 bars)";
  }
}

/** The newest filing, in words ("none stored" is a statement, not a blank). */
export function filingWords(n: ChainMapNodeOut, filingDays = 365): string {
  if (n.security_id == null) return "no resolved security, so no filings are read";
  const f = n.last_filing;
  if (!f) return `no filing stored in the last ${filingDays === 365 ? "year" : `${filingDays} days`}`;
  return `last filing: ${f.form} · ${fmtDay(f.date)} (${ageWords(f.age_days)}) · ${f.headline}`;
}

/** A role's hover description (the operator-approved copy). */
export const roleWords = (n: ChainMapNodeOut): string | null =>
  n.role ? `${contextRoleTag(n.role)}: ${CONTEXT_ROLE_DESCRIPTION[n.role]}` : null;

/** One name's words, for its tooltip and its accessible name. */
export function nodeWords(n: ChainMapNodeOut): string {
  return [
    n.name ? `${n.ticker} · ${n.name}` : n.ticker,
    linkWords(n.links),
    roleWords(n),
    n.business_type ? typeLabel(n) : null,
    sideWords(n),
    filingWords(n),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The quiet column tag under a column's label. */
export function columnTag(kind: "link" | "old_link" | "no_link" | "silent", descriptor: string | null): string {
  switch (kind) {
    case "old_link":
      return "not a link today";
    case "no_link":
      return "rows with no link";
    case "silent":
      return "no role · not in the call";
    default:
      return descriptor ?? "";
  }
}

function cacheWords(d: ChainMapOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work. */
export function workLine(d: ChainMapOut, showUntagged: boolean): string {
  const c = d.counts;
  const roles = c.roles.map((r) => `${contextRoleTag(r.role)} ${r.names}`).join(", ");
  return [
    `${plural(c.links_today, "link", "links")} today${c.old_links > 0 ? ` and ${plural(c.old_links, "roster link", "roster links")} that ${c.old_links === 1 ? "is" : "are"} not on the card today` : ""}`,
    `${plural(c.roster_rows, "roster row", "roster rows")} (${c.resolved_rows.toLocaleString("en-US")} resolved, ${c.unresolved_rows.toLocaleString("en-US")} without a resolved security), ${plural(c.companies, "company", "companies")}`,
    c.roles.length > 0 ? `roles set: ${roles}` : "no context roles are set on this card",
    `${plural(c.on_rails, "company", "companies")} on the rails`,
    `a 50-day side for ${c.with_side.toLocaleString("en-US")} of ${plural(c.companies, "name", "names")} (${c.without_side.toLocaleString("en-US")} without)`,
    `a filing in the last ${d.filing_days} days for ${c.with_filing.toLocaleString("en-US")} (${c.without_filing.toLocaleString("en-US")} without)`,
    d.flagged.length > 0
      ? `${plural(d.flagged.length, "name", "names")} drawn grayed for a flagged price history`
      : "names with a flagged price history are drawn grayed",
    c.untagged_silent > 0
      ? `${plural(c.untagged_silent, "Context or Bench name", "Context and Bench names")} with no role ${showUntagged ? "shown" : "hidden"}`
      : null,
    `${d.clock === "public" ? "public clock" : "live read"}`,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}
