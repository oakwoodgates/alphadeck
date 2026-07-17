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
