import type { DataFlagOut, DataFlagsResponse, MemberDataFlagsOut } from "../api/hooks";

/** The shared "is this real?" data flag (split plan S3) on the FE — pure helpers over the wire
 *  (`GET /theses/{id}/data-flags`, `GET /data-flags`). A flag is computed on read server-side and is
 *  display-only: it grays (in doubt) or blanks (known wrong) ONLY the cells it names, the row carries a
 *  quiet mark only when some cell is reached (WB#3), and nothing here can reach the call (#4/#7). */

export type FlagLevel = "in_doubt" | "known_wrong";

/** One cell's flag state: the worst level among the flags that reach it, and every such flag's reason. */
export interface CellFlag {
  level: FlagLevel;
  reason: string;
}

/** The Cockpit cell keys a flag can name (the server's `domain/data_flags.py` vocabulary). */
export const CELL_LABEL: Record<string, string> = {
  sma: "SMA",
  ret_1d: "1d",
  ret_7d: "7d",
  ret_30d: "30d",
  ret_90d: "90d",
  ret_1y: "1Y",
  path: "Path",
  rvol8: "RVOL 8D",
  rvol20: "RVOL 20D",
  ins_30d: "Ins 30d",
  ins_90d: "Ins 90d",
  mktcap: "Mkt cap",
};

export const LEVEL_WORD: Record<FlagLevel, string> = {
  in_doubt: "in doubt",
  known_wrong: "known wrong",
};

/** security_id → the member's flags (a member absent from the map has none). */
export function flagsBySid(
  resp: DataFlagsResponse | null | undefined,
): Map<string, MemberDataFlagsOut> {
  return new Map((resp?.members ?? []).map((m) => [m.security_id, m]));
}

function oneLine(f: DataFlagOut): string {
  return f.reason;
}

/** The flag state of ONE cell on a row: null when no flag reaches it. Known wrong beats in doubt (the
 *  number is then not shown at all); the hover carries every reaching flag's reason. */
export function cellFlag(
  member: MemberDataFlagsOut | null | undefined,
  cell: string,
): CellFlag | null {
  const hits = (member?.flags ?? []).filter((f) => (f.columns ?? []).includes(cell));
  if (hits.length === 0) return null;
  const level: FlagLevel = hits.some((f) => f.level === "known_wrong") ? "known_wrong" : "in_doubt";
  return { level, reason: hits.map(oneLine).join("\n") };
}

/** The flags that reach a cell on the row (the ones the row mark speaks for). */
export function rowFlags(member: MemberDataFlagsOut | null | undefined): DataFlagOut[] {
  return (member?.flags ?? []).filter((f) => (f.columns ?? []).length > 0);
}

/** The row mark's level: known wrong when any reaching flag is, else in doubt; null = no mark. */
export function rowLevel(member: MemberDataFlagsOut | null | undefined): FlagLevel | null {
  const fs = rowFlags(member);
  if (fs.length === 0) return null;
  return fs.some((f) => f.level === "known_wrong") ? "known_wrong" : "in_doubt";
}

/** The row mark's hover: one line per reaching flag — its label, level and the cells it touches. */
export function markTitle(member: MemberDataFlagsOut | null | undefined): string {
  return rowFlags(member)
    .map(
      (f) =>
        `${f.label} (${LEVEL_WORD[f.level]}): ${f.reason} — affects ${(f.columns ?? [])
          .map((c) => CELL_LABEL[c] ?? c)
          .join(", ")}`,
    )
    .join("\n");
}

/** A split's price factor in the operator's words: "×16" (reverse), "÷2" (a 2-for-1 forward split). */
export function factorWords(factor: number): string {
  if (!Number.isFinite(factor) || factor <= 0) return "";
  if (factor >= 1) return `×${fmtNum(factor)}`;
  return `÷${fmtNum(1 / factor)}`;
}

function fmtNum(x: number): string {
  return Number.isInteger(x) ? String(x) : String(Math.round(x * 1000) / 1000);
}
