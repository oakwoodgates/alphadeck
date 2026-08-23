import type { BasketMember, TermSetEntry } from "../api/hooks";

// The term filter's PURE core (the TRIAGE find bar's discovery-term dropdown) — the counterpart to the
// COUNTRY / EXCHANGE / TYPE identity filters, but keyed on the name's DISCOVERY provenance instead of its
// identity. No React, no side effects: `(basket, termSet) -> { signal, broad }` for the dropdown universe,
// and small predicates for the row filter. VIEW-only — like every find-bar control it narrows what RENDERS,
// never what Save persists (#9); the derivation reads `surfaced_terms` (persisted provenance) + the working
// `term_set`, and returns options/booleans, touching neither.
//
// Source of truth = `BasketMember.surfaced_terms` (persisted, frozen at promote AND captured at draft entry),
// NOT the ephemeral draft-run `matched` map (empty on a cold re-entry) — so the filter works whenever the
// thesis is opened, not only right after a draft.

// Normalize a discovery term for case-insensitive dedup / join / compare — the SAME rule ChainEditor's `norm`
// uses for its term-set lookups (recs / capped / empty markers all key on it), so the term filter joins and
// matches terms exactly the way the rest of the editor does.
const normTerm = (t: string): string => t.trim().toLowerCase();

// One dropdown entry: `value` is the normalized key (what the <select> stores + the predicate compares),
// `label` is the first-seen original casing (what the operator reads — the select renders it text-transform:none).
export type TermOption = { value: string; label: string };
// The tier-grouped universe: `signal` -> the "Seed terms" optgroup, `broad` -> the "Broad terms" optgroup.
export type TermUniverse = { signal: TermOption[]; broad: TermOption[] };

/** Derive the term-filter dropdown universe from the placed basket + the thesis term set.
 *
 *  Universe = the deduped union of every `surfaced_terms` entry that actually surfaced at least one PLACED
 *  name — NO dead options (a term nobody placed never appears). Each term is tier-grouped by joining
 *  (case-insensitively) to the term set: a `signal` term -> the SEED group; a `broad` term -> the BROAD
 *  group. A surfaced term with NO term-set match lands in BROAD (never dropped — it really surfaced a name;
 *  recall is sacred, #9). First-seen original casing is kept for display; options sort alphabetically within
 *  each group so the operator scans a stable list. Reading the persisted `surfaced_terms` (not the draft-run
 *  matches) is deliberate — the universe is the same whenever the thesis is opened. */
export function termUniverse(basket: BasketMember[], termSet: TermSetEntry[]): TermUniverse {
  const tierByTerm = new Map<string, TermSetEntry["tier"]>();
  for (const e of termSet) tierByTerm.set(normTerm(e.term), e.tier);

  const signal = new Map<string, string>(); // normalized key -> first-seen display casing
  const broad = new Map<string, string>();
  for (const m of basket) {
    for (const raw of m.surfaced_terms ?? []) {
      const key = normTerm(raw);
      if (!key) continue; // a blank/whitespace term is not a real option
      // no term-set match -> BROAD (an operator seed is the only thing that earns the SEED group)
      const bucket = tierByTerm.get(key) === "signal" ? signal : broad;
      if (!bucket.has(key)) bucket.set(key, raw.trim());
    }
  }
  const toOptions = (m: Map<string, string>): TermOption[] =>
    [...m.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  return { signal: toOptions(signal), broad: toOptions(broad) };
}

/** True when the term universe can DISCRIMINATE — i.e. at least one placed name carries a surfaced term.
 *  A fully off-universe basket (zero surfaced terms) yields an empty universe -> the caller renders NO term
 *  control (honest loudness #3 — a filter that can't discriminate is noise). */
export function hasTermUniverse(u: TermUniverse): boolean {
  return u.signal.length > 0 || u.broad.length > 0;
}

/** Does this term list carry the selected (already-normalized) term? Case-insensitive, mirroring the
 *  universe's normalized keys. An empty pick (`""`, the cleared filter) passes everything. An empty/absent
 *  term list (an off-universe / hand-added name) never matches a real pick — correct: it surfaced under no
 *  term. Works on a placed member's `surfaced_terms` AND a To-Review candidate's `matched_terms`. */
export function termsInclude(
  terms: string[] | null | undefined,
  normalizedTerm: string,
): boolean {
  if (!normalizedTerm) return true;
  return (terms ?? []).some((t) => normTerm(t) === normalizedTerm);
}

/** Group-level predicate: does ANY of a name's membership rows carry the term? `surfaced_terms` is per-CIK
 *  provenance (uniform across a name's rows, copied when a name is split into N link rows), so a union
 *  equals the representative row's terms — but a union is recall-safe (#9): it can only ADD a match, never
 *  drop one. An empty pick passes every group. */
export function groupHasTerm(
  rows: { surfaced_terms?: string[] | null }[],
  normalizedTerm: string,
): boolean {
  if (!normalizedTerm) return true;
  return rows.some((r) => termsInclude(r.surfaced_terms, normalizedTerm));
}
