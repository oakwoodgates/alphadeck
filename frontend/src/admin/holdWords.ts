// The Held insider rows panel's suggestion lines in PLAIN ENGLISH: where the suggested date is written, the
// filing's own date window for a withheld one, and the copy of a row held under another ticker. Pure words over
// GET /admin/insider-holds — the backend decides every fact (`suggest` in domain/insider_holds.py: the date, whether
// it agrees, its basis, its words, the window; the copies from the repository); this module only phrases the pieces
// the API sends as codes or bare dates. Nothing here decides anything.
import type { AdminInsiderHoldOut } from "../api/hooks";

type Copy = NonNullable<AdminInsiderHoldOut["copies"]>[number];

/** The short label for where a suggestion comes from (`suggestion_basis`; the API's own words say the rest). */
const BASIS: Record<string, string> = {
  footnote: "from a footnote",
  sibling_row: "from another row of the filing",
  period_of_report: "from the period of report",
  arithmetic: "from the written month and day",
  period_of_report_one_row: "from a one-row filing's period of report",
};

/** The label for a basis code; `null` for none (an older server) or one this build does not know. */
export const basisLabel = (basis: string | null | undefined): string | null =>
  basis ? (BASIS[basis] ?? null) : null;

/** The API's words for a shown date without their leading "<date>: " (the line already names the date). */
export function wordsAfterDate(words: string, day: string): string {
  const lead = `${day}: `;
  return words.startsWith(lead) ? words.slice(lead.length) : words;
}

/** The filing's own date window for a row, as a sentence; `null` when the filing gives neither end. */
export function windowLine(from: string | null | undefined, to: string | null | undefined): string | null {
  const put = "The filing's own dates put it";
  if (from && to) {
    if (from > to) return `The filing's own dates leave no window (${from} is after ${to}).`;
    return from === to ? `${put} on ${from}.` : `${put} between ${from} and ${to}.`;
  }
  if (to) return `${put} on or before ${to}.`;
  if (from) return `${put} on or after ${from}.`;
  return null;
}

/** "Also held: a copy of the same filing row under GSCE. Each is decided on its own."; `null` with no copy. */
export function copiesLine(copies: readonly Copy[] | null | undefined): string | null {
  if (!copies?.length) return null;
  const each = copies.map((c) => `a copy of the same filing row under ${c.ticker || "another security"}`);
  const named = each.length === 1 ? each[0] : `${each.slice(0, -1).join(", ")} and ${each[each.length - 1]}`;
  return `Also held: ${named}. Each is decided on its own.`;
}
