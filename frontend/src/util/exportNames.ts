/** Client-side export of kept/included name lists — outbound-only, never reloaded. */

export type ExportedName = {
  ticker: string;
  name: string | null;
};

export type ExportStage = "triage" | "shortlist" | "board" | "all";

/** One named group in a segmented export (a value-chain link, the Discovered pen, or a To-Review bucket). */
export type ExportGroup = {
  label: string;
  rows: ExportedName[];
};

export function toExportedName(row: {
  ticker?: string | null;
  name?: string | null;
}): ExportedName {
  return {
    ticker: row.ticker ?? "",
    name: row.name ?? null,
  };
}

export function slugForFilename(value: string): string {
  const slug = value
    .trim()
    .replace(/[^\w.-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "thesis";
}

export function exportFilename(
  thesisName: string,
  stage: ExportStage,
  asof: string,
): string {
  return `${slugForFilename(thesisName)}-${stage}-${asof}.json`;
}

export function downloadJson(filename: string, data: unknown): void {
  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Order rows alphabetically by ticker for a STABLE, diff-friendly export (the operator diffs successive
 *  exports of the same list). Sorts a copy — never mutates the caller's array — and breaks ticker ties by
 *  name so the order is fully deterministic. `localeCompare` gives case-insensitive alphabetical. */
export function sortByTicker(rows: ExportedName[]): ExportedName[] {
  return [...rows].sort(
    (a, b) =>
      a.ticker.localeCompare(b.ticker, undefined, { sensitivity: "base" }) ||
      (a.name ?? "").localeCompare(b.name ?? "", undefined, { sensitivity: "base" }),
  );
}

export function exportKeptNames(opts: {
  thesisName: string;
  stage: ExportStage;
  asof: string;
  rows: ExportedName[];
}): void {
  downloadJson(
    exportFilename(opts.thesisName, opts.stage, opts.asof),
    sortByTicker(opts.rows),
  );
}

/** Export a SEGMENTED name list — a JSON object keyed by group label (a value-chain link, the Discovered
 *  pen, or a To-Review bucket), each group's rows sorted alphabetically by ticker for a stable diff. Group
 *  ORDER is preserved as passed (the caller orders links by the chain, buckets last); empty groups are
 *  dropped so a group only appears when it has names. Keys are de-duplicated defensively (a repeated label
 *  merges its rows) so the object never silently loses a group. */
export function exportSegmentedNames(opts: {
  thesisName: string;
  stage: ExportStage;
  asof: string;
  groups: ExportGroup[];
}): void {
  const out: Record<string, ExportedName[]> = {};
  for (const g of opts.groups) {
    if (g.rows.length === 0) continue;
    const merged = out[g.label] ? [...out[g.label], ...g.rows] : g.rows;
    out[g.label] = sortByTicker(merged);
  }
  downloadJson(exportFilename(opts.thesisName, opts.stage, opts.asof), out);
}

// --- TradingView watchlist export ------------------------------------------------------------------
// A SECOND export shape beside the JSON dump: a TradingView-importable symbol list (.txt). TradingView's
// watchlist import reads a comma-separated stream where `###Name` opens a named section and each symbol is
// ideally `EXCHANGE:TICKER` (a bare TICKER also resolves, but the prefix pins the US listing over a foreign
// one). We PREFIX only the exchanges we can map with confidence and fall back to a BARE ticker for anything
// else — a WRONG prefix fails to resolve, strictly worse than bare (which TradingView resolves to the primary
// listing). The confident map was measured against the live security-master vocabulary (Nasdaq / NYSE / OTC
// dominate; CBOE / null are the tail → bare).

export type WatchlistRow = {
  ticker: string;
  /** The security-master exchange string (EDGAR's `submissions.exchanges[0]` vocabulary), or null. */
  exchange?: string | null;
};

/** Map a security-master exchange string to a TradingView exchange code — CONFIDENT cases only. Returns null
 *  → the caller emits a bare ticker (which TradingView still resolves). Case/space-insensitive so "Nasdaq",
 *  "NASDAQ", "NasdaqGS" all land. Deliberately omits CBOE / BATS / IEX and anything unrecognized (→ bare): a
 *  wrong prefix is worse than none. NYSE Arca maps to AMEX — where TradingView files US-listed ETFs (AMEX:SPY). */
export function tvExchangePrefix(exchange: string | null | undefined): string | null {
  const key = (exchange ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  if (!key) return null;
  if (key.startsWith("nasdaq")) return "NASDAQ";
  if (key === "nyse" || key === "new york stock exchange") return "NYSE";
  if (key.startsWith("nyse american") || key === "nyseamerican" || key === "nyse mkt" || key === "amex")
    return "AMEX";
  if (key.startsWith("nyse arca") || key === "nysearca" || key === "arca") return "AMEX";
  if (key.startsWith("otc") || key.startsWith("pink")) return "OTC";
  return null; // CBOE / BATS / IEX / unknown / null → bare ticker
}

/** The TradingView symbol for a row: `EXCHANGE:TICKER` when the exchange maps confidently, else the bare
 *  (uppercased) ticker. An empty ticker → "" (the caller drops it). */
export function tvSymbol(row: WatchlistRow): string {
  const ticker = row.ticker.trim().toUpperCase();
  if (!ticker) return "";
  const prefix = tvExchangePrefix(row.exchange);
  return prefix ? `${prefix}:${ticker}` : ticker;
}

/** Build the TradingView import payload: one `###<section>` header then the comma-joined symbols, sorted by
 *  ticker (stable + diff-friendly, the JSON export's idiom) and de-duplicated by emitted symbol. Ticker-less
 *  rows are dropped. The section label is comma-stripped (comma is the format's delimiter). */
export function buildWatchlistTxt(section: string, rows: WatchlistRow[]): string {
  const seen = new Set<string>();
  const symbols: string[] = [];
  const ordered = [...rows].sort((a, b) =>
    a.ticker.localeCompare(b.ticker, undefined, { sensitivity: "base" }),
  );
  for (const row of ordered) {
    const sym = tvSymbol(row);
    if (!sym) continue;
    const dedupeKey = sym.toUpperCase();
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    symbols.push(sym);
  }
  const header = section.replace(/,/g, " ").replace(/\s+/g, " ").trim() || "Watchlist";
  return [`###${header}`, ...symbols].join(",");
}

export function watchlistFilename(thesisName: string, asof: string): string {
  return `${slugForFilename(thesisName)}-watchlist-${asof}.txt`;
}

export function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Download the thesis basket as a TradingView-importable watchlist (.txt) — the thesis name is the section. */
export function exportWatchlist(opts: {
  thesisName: string;
  asof: string;
  rows: WatchlistRow[];
}): void {
  downloadText(
    watchlistFilename(opts.thesisName, opts.asof),
    buildWatchlistTxt(opts.thesisName, opts.rows),
  );
}
