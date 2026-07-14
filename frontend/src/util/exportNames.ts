/** Client-side export of kept/included name lists — outbound-only, never reloaded. */

export type ExportedName = {
  ticker: string;
  name: string | null;
};

export type ExportStage = "triage" | "shortlist" | "board";

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

export function exportKeptNames(opts: {
  thesisName: string;
  stage: ExportStage;
  asof: string;
  rows: ExportedName[];
}): void {
  downloadJson(
    exportFilename(opts.thesisName, opts.stage, opts.asof),
    opts.rows,
  );
}
