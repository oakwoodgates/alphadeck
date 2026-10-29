import type {
  FilingStreamCardOut,
  FilingStreamDayOut,
  FilingStreamLinkOut,
  FilingStreamOut,
  FilingStreamRowOut,
  StreamDays,
} from "./useFilingStream";

// The Filing Stream's pure view helpers (docs/dashboards/filing-stream.md): the kinds' words, the client-side
// filters (kind, link, name, role, "Counting names only"), the fold of a day's routine Form 4s, the day header and
// the "show the work" lines. Every row and every count is computed server-side; this only filters, folds and words
// them. Nothing here hides a row from the counts: the work line counts every row, folded or not.

export type StreamKind = FilingStreamRowOut["kind"];

/** The kinds in a day's order (the server's `KIND_ORDER`): 8-K, 13D/G, Form 4, 4/A, revenue, other forms from the
 *  EDGAR index, split, dilution, catalyst, ratified or stored fact. */
export const KIND_ORDER: readonly StreamKind[] = [
  "8k",
  "stake",
  "form4",
  "form4a",
  "revenue",
  "filing",
  "split",
  "dilution",
  "catalyst",
  "ratified",
];

/** The kind's short label on its filter chip. Exhaustive against the generated enum, so a kind added server-side
 *  fails the build here instead of rendering a blank. */
export const KIND_LABEL: Record<StreamKind, string> = {
  "8k": "8-K",
  stake: "13D/G",
  form4: "Form 4",
  form4a: "4/A",
  revenue: "Revenue",
  filing: "Other forms",
  split: "Split",
  dilution: "Dilution",
  catalyst: "Catalyst",
  ratified: "Ratified and stored facts",
};

/** The kind's words for the work line: [one, many]. */
const KIND_WORDS: Record<StreamKind, readonly [string, string]> = {
  "8k": ["8-K", "8-Ks"],
  stake: ["13D/G stake", "13D/G stakes"],
  form4: ["Form 4", "Form 4s"],
  form4a: ["4/A", "4/As"],
  revenue: ["revenue print", "revenue prints"],
  filing: ["other form", "other forms"],
  split: ["split", "splits"],
  dilution: ["dilution row", "dilution rows"],
  catalyst: ["catalyst", "catalysts"],
  ratified: ["ratified or stored fact", "ratified or stored facts"],
};

export const DAYS_OPTIONS: readonly { value: StreamDays; label: string }[] = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "365 days" },
];
export const DEFAULT_DAYS: StreamDays = 90;

/** The silent links' labels (domain/thesis.py's Context and Bench). */
export const SILENT_LABELS: readonly string[] = ["Context", "Bench"];

export const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

// --- dates and money ---------------------------------------------------------------------------------------------

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

// the units of a compact figure: its suffix, its size and the decimals shown
const UNITS = [
  { suffix: "", size: 1, decimals: 0 },
  { suffix: "K", size: 1e3, decimals: 1 },
  { suffix: "M", size: 1e6, decimals: 1 },
  { suffix: "B", size: 1e9, decimals: 1 },
] as const;

/** A dollar figure in one short piece ("$49.2K", "$350.0M"). THE ROLLOVER RULE (the server's `fmt_compact`): the
 *  figure is rounded to what is shown (a whole number below a thousand, one decimal above) and one that rounds up
 *  to 1,000 moves to the next unit, so 999,949 is "$999.9K" and 999,950 is "$1.0M", never "$1000.0K". */
export function compactUsd(v: number): string {
  const a = Math.abs(v);
  let i = 0;
  while (i < UNITS.length - 1 && a >= UNITS[i + 1].size) i++;
  while (i < UNITS.length - 1 && Number((a / UNITS[i].size).toFixed(UNITS[i].decimals)) >= 1000) i++;
  const u = UNITS[i];
  return `${v < 0 ? "-" : ""}$${(a / u.size).toFixed(u.decimals)}${u.suffix}`;
}

/** The row's dollar size for the compact hint at the line's end: a Form 4's purchases minus sales (signed) and a
 *  dilution's principal; nothing for any other row. */
export function usdHint(r: Pick<FilingStreamRowOut, "kind" | "usd">): string | null {
  if (r.usd == null) return null;
  if (r.kind === "form4" || r.kind === "form4a") return `${r.usd > 0 ? "+" : ""}${compactUsd(r.usd)} net`;
  if (r.kind === "dilution") return compactUsd(r.usd);
  return null;
}

// --- the picker --------------------------------------------------------------------------------------------------

/** The picker's filter: the cards whose name holds the query (any case), in the server's A→Z order. */
export function filterCards(cards: readonly FilingStreamCardOut[], query: string): FilingStreamCardOut[] {
  const q = query.trim().toLocaleLowerCase();
  return q ? cards.filter((c) => c.name.toLocaleLowerCase().includes(q)) : [...cards];
}

// --- the filters (client side) -----------------------------------------------------------------------------------

export type Filters = {
  /** the kinds shown (all by default) */
  kinds: ReadonlySet<StreamKind>;
  /** a link's key (`FilingStreamLinkOut.key`); "" = every link */
  link: string;
  /** a ticker query; "" = every name */
  name: string;
  /** a context role; "" = every role */
  role: string;
  /** leave the Context and Bench names out */
  countingOnly: boolean;
  /** show the routine Form 4s in place instead of folding them */
  showRoutine: boolean;
};

export const DEFAULT_FILTERS: Filters = {
  kinds: new Set(KIND_ORDER),
  link: "",
  name: "",
  role: "",
  countingOnly: false,
  showRoutine: false,
};

/** Is any row filter narrower than the default? (The fold toggle is not a filter: it hides nothing.) */
export function filtersActive(f: Filters): boolean {
  return f.kinds.size < KIND_ORDER.length || f.link !== "" || f.name.trim() !== "" || f.role !== "" || f.countingOnly;
}

/** Does the row pass every filter? A link filter matches a row whose company sits in that link on the card ("No
 *  link" matches a company in none). */
export function rowMatches(
  r: FilingStreamRowOut,
  f: Filters,
  links: readonly FilingStreamLinkOut[],
): boolean {
  if (!f.kinds.has(r.kind)) return false;
  if (f.countingOnly && r.silent) return false;
  if (f.role && r.role !== f.role) return false;
  const q = f.name.trim().toLocaleLowerCase();
  if (q && !r.ticker.toLocaleLowerCase().includes(q)) return false;
  if (f.link) {
    const link = links.find((l) => l.key === f.link);
    if (link) {
      const hit = link.kind === "no_link" ? r.links.includes(null) : r.links.includes(link.label);
      if (!hit) return false;
    }
  }
  return true;
}

/** The days with only the rows that pass the filters; a day left empty is dropped. */
export function filterDays(
  days: readonly FilingStreamDayOut[],
  f: Filters,
  links: readonly FilingStreamLinkOut[],
): FilingStreamDayOut[] {
  const out: FilingStreamDayOut[] = [];
  for (const d of days) {
    const rows = d.rows.filter((r) => rowMatches(r, f, links));
    if (rows.length > 0) out.push({ ...d, rows, routine: rows.filter((r) => r.routine).length });
  }
  return out;
}

export const countRows = (days: readonly FilingStreamDayOut[]) => days.reduce((n, d) => n + d.rows.length, 0);

// --- the fold ----------------------------------------------------------------------------------------------------

/** The server's fold groups (`filings.FOLD_GROUPS`): `form4` = a Form 4 holding no purchase (folded per day),
 *  `prospectus` = a prospectus supplement (424B, 424H, 424I, 497, FWP) and `ownership` = an insider ownership
 *  filing with no transaction rows stored (an index-only Form 3/4/5, a 144 notice), each folded per day AND
 *  company. */
export type FoldGroup = NonNullable<FilingStreamRowOut["fold"]>;

/** A routine row's group; a routine row the server sent without one is the day's Form 4 fold (today's rule). */
export const foldGroupOf = (r: Pick<FilingStreamRowOut, "fold">): FoldGroup => r.fold ?? "form4";

/** One line of a day: a row, or a fold line standing where its first row was. `key` is unique within the day:
 *  `form4` for the day's ONE Form 4 fold, `<group>:<security_id>` for a company's prospectus / ownership fold. */
export type Entry =
  | { type: "row"; row: FilingStreamRowOut }
  | { type: "fold"; group: FoldGroup; key: string; ticker: string; rows: FilingStreamRowOut[] };

/** A day's lines in order. With `showRoutine` off, its routine Form 4 rows collapse into ONE fold line, and each
 *  company's routine prospectus / ownership rows into one fold line per company and group (the honest-loudness
 *  rule: the common noise goes quiet, nothing is removed); on, every row is a line. */
export function foldEntries(rows: readonly FilingStreamRowOut[], showRoutine: boolean): Entry[] {
  if (showRoutine) return rows.map((row) => ({ type: "row", row }));
  const out: Entry[] = [];
  const folds = new Map<string, Extract<Entry, { type: "fold" }>>();
  for (const row of rows) {
    if (!row.routine) {
      out.push({ type: "row", row });
      continue;
    }
    const group = foldGroupOf(row);
    const key = group === "form4" ? "form4" : `${group}:${row.security_id}`;
    const fold = folds.get(key);
    if (fold) {
      fold.rows.push(row);
    } else {
      const made: Extract<Entry, { type: "fold" }> = { type: "fold", group, key, ticker: row.ticker, rows: [row] };
      folds.set(key, made);
      out.push(made);
    }
  }
  return out;
}

/** The Form 4 fold line's words: no purchase in any of them, said so. */
export const foldWords = (n: number) =>
  `${plural(n, "routine Form 4 filing", "routine Form 4 filings")} (none holds a purchase: sales, grants, exercises, withholding, other codes)`;

const isForm144 = (form: string) => form.trim().toUpperCase().startsWith("144");

/** Any fold line's words: the day's Form 4s as before; a company's prospectus supplements ("JPM · 87 prospectus
 *  supplements (424B, FWP)"); a company's insider ownership filings, naming only what the line holds ("META · 5
 *  insider ownership filings (Forms 3/4/5 with no transaction rows stored, 144 notices)"). */
export function foldEntryWords(e: Extract<Entry, { type: "fold" }>): string {
  const n = e.rows.length;
  if (e.group === "form4") return foldWords(n);
  if (e.group === "prospectus") {
    return `${e.ticker} · ${plural(n, "prospectus supplement", "prospectus supplements")} (424B, FWP)`;
  }
  const what = [
    e.rows.some((r) => !isForm144(r.form)) ? "Forms 3/4/5 with no transaction rows stored" : null,
    e.rows.some((r) => isForm144(r.form)) ? "144 notices" : null,
  ].filter(Boolean);
  return `${e.ticker} · ${plural(n, "insider ownership filing", "insider ownership filings")} (${what.join(", ")})`;
}

// --- the show-the-work lines -------------------------------------------------------------------------------------

function cacheWords(d: FilingStreamOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** A fold group's rows in words: [one, many] (the folded line and the not-listed line). */
const GROUP_ROWS: Record<FoldGroup, readonly [string, string]> = {
  form4: ["routine Form 4 row", "routine Form 4 rows"],
  prospectus: ["prospectus supplement row", "prospectus supplement rows"],
  ownership: ["insider ownership row", "insider ownership rows"],
};
const GROUP_NOUN: Record<FoldGroup, readonly [string, string]> = {
  form4: ["routine Form 4", "routine Form 4s"],
  prospectus: ["prospectus supplement", "prospectus supplements"],
  ownership: ["insider ownership filing", "insider ownership filings"],
};
const FOLD_ORDER: readonly FoldGroup[] = ["form4", "prospectus", "ownership"];

/** The routine rows the filtered days hold, by fold group (what the fold covers). */
export function foldedByGroup(days: readonly FilingStreamDayOut[]): Partial<Record<FoldGroup, number>> {
  const out: Partial<Record<FoldGroup, number>> = {};
  for (const d of days) {
    for (const r of d.rows) {
      if (!r.routine) continue;
      const g = foldGroupOf(r);
      out[g] = (out[g] ?? 0) + 1;
    }
  }
  return out;
}

/** The cap's honest line: the routine rows the server did not list (over `routine_cap` of one kind for one
 *  company in this window), counted per company and group; `null` when none. */
export function notListedWords(d: Pick<FilingStreamOut, "not_listed" | "routine_cap">): string | null {
  const total = d.not_listed.reduce((n, x) => n + x.rows, 0);
  if (total === 0) return null;
  const who = d.not_listed.map((x) => {
    const [one, many] = x.fold ? GROUP_NOUN[x.fold] : ["routine row", "routine rows"];
    return `${x.ticker} ${plural(x.rows, one, many)}`;
  });
  return `${plural(total, "routine row", "routine rows")} not listed (over ${d.routine_cap} of one kind for one company in this window): ${who.join(", ")}`;
}

/** The quiet lines under the stream: the card and window, the clock, the roster, and the rows by kind (every
 *  row, folded or not, listed or not), then the cap's not-listed line when it cut anything. `shown` = rows left
 *  after the filters; `folded` = routine rows the fold is covering, by group. */
export function workLines(
  d: FilingStreamOut,
  shown: number,
  folded: Partial<Record<FoldGroup, number>>,
  filtering: boolean,
): string[] {
  const resolved = d.names.filter((n) => n.security_id != null).length;
  const kinds = KIND_ORDER.map((k) => {
    const n = d.counts.by_kind.find((c) => c.kind === k)?.rows ?? 0;
    const [one, many] = KIND_WORDS[k];
    const words = plural(n, one, many);
    return k === "form4" && n > 0 ? `${words} (${d.counts.form4_purchases} hold a purchase)` : words;
  });
  const foldedParts = FOLD_ORDER.filter((g) => (folded[g] ?? 0) > 0).map((g) =>
    plural(folded[g] ?? 0, ...GROUP_ROWS[g]),
  );
  const notListed = notListedWords(d);
  return [
    [
      `as of ${fmtDay(d.asof)}`,
      `window ${fmtDay(d.window_start)} – ${fmtDay(d.asof)} (the last ${d.days} days)`,
      d.clock === "record" ? "live read" : `as it was public by ${fmtDay(d.asof)}`,
      cacheWords(d),
    ].join(" · "),
    [
      plural(d.basket_rows, "roster row", "roster rows"),
      `${plural(resolved, "company", "companies")} resolved`,
      `${d.unresolved.length} without a resolved security`,
      `${d.silent_rows} in Context or Bench`,
    ].join(" · "),
    [
      `${plural(d.counts.rows, "row", "rows")} in the window: ${kinds.join(" · ")}`,
      filtering ? `${shown.toLocaleString("en-US")} shown after the filters` : null,
      foldedParts.length > 0 ? `${foldedParts.join(", ")} folded (counted above)` : null,
    ]
      .filter(Boolean)
      .join(" · "),
    ...(notListed ? [notListed] : []),
  ];
}

/** The roster note: names with no resolved security can carry no facts, so they are listed, never silently
 *  dropped (invariant #9). */
export function unresolvedWords(tickers: readonly string[]): string | null {
  if (tickers.length === 0) return null;
  return `${plural(tickers.length, "roster name has", "roster names have")} no resolved security, so no filing can be attached to ${tickers.length === 1 ? "it" : "them"}: ${tickers.join(", ")}.`;
}

/** Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in. */
export const ROSTER_PIT_FROM = "2026-09-15";

/** The page's known limits, stated on the page (docs/dashboards/filing-stream.md). */
export const LIMITS: readonly string[] = [
  "Every form in each company's EDGAR index is listed, from its recent window (at least a year, or the last 1,000 filings, whichever reaches further). A form the system does not read into (an S-1, a 424B5, a DEF 14A) shows as its form and primary document under Other forms, nothing quoted from it; each filing shows once, as its richest row.",
  "Routine rows fold into quiet lines: a day's Form 4s holding no purchase, and per company a day's prospectus supplements (424B, 424H, 424I, 497, FWP) and its insider ownership filings with no transaction rows stored (Forms 3/4/5, 144 notices). At most 200 routine rows of one kind per company are listed in a window, newest first; the rest are counted in the work line and in every count above, never listed. A row that is not routine is never cut.",
  "The Cockpit's day-low and implausible-data insider screens are not applied here: a purchase is shown as a purchase, nothing more. 13D/G stakes are listed as stored, without the call's control screens.",
  "A 4/A is shown as a filing; its standing (applied, kept, pending) is on Admin › Insider amendments. Form 4s come through the Cockpit's resolved read: an applied 4/A's rows replace the original's.",
  "A Form 4 is dated by its SEC acceptance in market time; a split by its own publication as the view knows it, its status as stored. The card-level catalysts are the Catalyst Calendar's.",
  "The roster is the card's roster as of the as-of, a look back with hindsight on who was in it; links are today's. Each row links to its filing; none quotes it.",
];
