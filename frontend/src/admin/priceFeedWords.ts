// The Price feed repairs panel's never-priced rows in PLAIN ENGLISH: what a probed candidate is (bars, its class mark
// by the OTC naming convention, and how that sits against the member's own) and what a deregistered name's evidence
// says. Pure words over the GET /admin/price-feed payload (W4-1 / W4-2) — never a different decision, only how it is
// explained. A class mark is a naming convention, never proof, and the line says so. Backend:
// app/routers/admin_price_feed.py.
import type {
  AdminDeregistrationEventOut,
  AdminProbeCandidateOut,
  AdminUnpriceableRowOut,
} from "../api/hooks";

/** "Client error '404 Not Found' for url …" -> "404 Not Found"; anything else clipped. */
export const vendorShort = (err: string | null | undefined) => {
  if (!err) return null;
  const m = /'(\d{3} [^']+)'/.exec(err);
  return m ? m[1] : err.length > 80 ? `${err.slice(0, 79)}…` : err;
};

// the two class marks, as a noun ("foreign ordinary") and with its article ("an ADR")
const CLASS_NOUN: Record<string, string> = { adr: "ADR", ordinary: "foreign ordinary" };
const CLASS_ARTICLE: Record<string, string> = { adr: "an ADR", ordinary: "a foreign ordinary share" };

const noun = (mark: string) => CLASS_NOUN[mark] ?? mark;
const article = (mark: string) => CLASS_ARTICLE[mark] ?? mark;

/** The row's candidates; an older server sends none of the W4-1 fields. */
export const candidatesOf = (u: AdminUnpriceableRowOut): AdminProbeCandidateOut[] => u.candidates ?? [];

/** The classes are both known and differ — the server's own rule for asking the steward to confirm. */
export function isCrossClass(c: AdminProbeCandidateOut, memberClass: string | null | undefined): boolean {
  return Boolean(memberClass && c.listing_class && memberClass !== c.listing_class);
}

/** The vendor served bars under it and its fetch did not fail: the only candidate an Adopt accepts. */
export const adoptable = (c: AdminProbeCandidateOut): boolean => !c.error && c.bars > 0;

const barsPhrase = (c: AdminProbeCandidateOut) =>
  `${c.bars} bar${c.bars === 1 ? "" : "s"}${c.last_bar ? ` to ${c.last_bar}` : ""}`;

/** The class mark against the member's: "foreign ordinary by the OTC naming convention; this member is an ADR". */
export function classClause(c: AdminProbeCandidateOut, memberClass: string | null | undefined): string | null {
  const mark = c.listing_class;
  if (!mark) return null;
  const base = `${noun(mark)} by the OTC naming convention`;
  if (!memberClass) return base;
  return memberClass === mark ? `${base}, the member's own class` : `${base}; this member is ${article(memberClass)}`;
}

/** Everything after the symbol on a candidate's line: "480 bars to 2026-11-24 · foreign ordinary by …". A candidate
 *  the server would not adopt says why instead (the class mark matters only to an Adopt). */
export function candidateFacts(c: AdminProbeCandidateOut, memberClass: string | null | undefined): string {
  if (c.error) return `fetch failed (${vendorShort(c.error)})`;
  if (c.bars <= 0) return "the vendor serves no bars under it";
  return [barsPhrase(c), classClause(c, memberClass)].filter(Boolean).join(" · ");
}

/** The EDGAR clause of an unpriceable row's header. Today's wording for a row without candidates; once the
 *  candidates (or a deregistration's evidence) say what EDGAR lists, only a symbol they do not carry is named —
 *  never the row's own ticker, never one a candidate line already shows. */
export function edgarClause(u: AdminUnpriceableRowOut): string | null {
  const listed = u.edgar_tickers ?? [];
  const cands = candidatesOf(u);
  if (cands.length === 0 && !u.deregistered) return `EDGAR lists ${listed.length ? listed.join(", ") : "no symbol"}`;
  const shown = new Set(cands.map((c) => c.symbol));
  const rest = listed.filter((s) => s !== u.ticker && !shown.has(s));
  if (rest.length === 0) return null;
  return `EDGAR ${cands.length ? "also " : ""}lists ${rest.join(", ")}`;
}

/** "Bankruptcy or Receivership; Changes in Control of Registrant" — SEC's own item titles, in order; "" for none. */
export const eventTitles = (e: AdminDeregistrationEventOut): string => e.item_titles.join("; ");
