// Junk-tell predicates for the Placed "low quality" display lens. Each tell is a pure check on draft-session
// provenance; the partition AND-gates them with the narrator's off_thesis flag (the recall guard).

import { isAcronymTerm } from "./format";

/** Everything a tell may read — built once per row at partition time. */
export interface JunkTellContext {
  matchedTerms: string[];
  companyName: string;
  signalAcronymTerms: ReadonlySet<string>;
}

export type JunkTell = (ctx: JunkTellContext) => boolean;

const norm = (t: string) => t.trim().toLowerCase();

/** Lowercase tokens from a company name (split on non-alphanumeric). */
export function tokenize(name: string): Set<string> {
  return new Set(
    name
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter(Boolean),
  );
}

/** Precompute SIGNAL acronym terms from the working term set (same source as the old collisionTerms set). */
export function signalAcronymTermsFrom(
  termSet: { term: string; tier: string }[],
): ReadonlySet<string> {
  return new Set(
    termSet
      .filter((e) => e.tier === "signal" && isAcronymTerm(e.term))
      .map((e) => norm(e.term)),
  );
}

/** Sole match on a collision-prone SIGNAL acronym term (HBM, DRAM, …). */
export const soleAcronymSignalMatch: JunkTell = (ctx) => {
  const mt = ctx.matchedTerms;
  return mt.length === 1 && ctx.signalAcronymTerms.has(norm(mt[0]));
};

/** Factory: every listed token must appear in the company name. */
export const nameTokensCooccur =
  (tokens: readonly string[]): JunkTell =>
  (ctx) => {
    if (!ctx.companyName) return false;
    const hay = tokenize(ctx.companyName);
    return tokens.every((t) => hay.has(t));
  };

const NAME_TOKEN_PAIRS: readonly (readonly string[])[] = [
  ["blackrock", "trust"],
  ["royce", "trust"],
];

/** The registry — append one entry to add a tell. */
export const JUNK_TELLS: readonly JunkTell[] = [
  soleAcronymSignalMatch,
  ...NAME_TOKEN_PAIRS.map(nameTokensCooccur),
];

export function matchesAnyJunkTell(ctx: JunkTellContext): boolean {
  return JUNK_TELLS.some((tell) => tell(ctx));
}
