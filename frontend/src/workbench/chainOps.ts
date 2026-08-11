import type { BasketMember, Segment } from "../api/hooks";
import { DISCOVERED } from "./useChainDraft";

// Pure value-chain transforms for the SCORE / triage screen — the immediate-promote counterpart to
// `useChainDraft`'s LOCAL mutators. This module MIRRORS the hook's algorithm (it does NOT import or edit
// it) so the SCORE view can rebuild a name's placements and hand the whole intended `{ segments, basket }`
// to the full-replace promote. No React, no side effects: `(segments, basket, …) → { segments, basket }`,
// fully unit-testable. `segment` is display / structure only — never a call input (#4).
//
// Phase 1 scope: the #4 per-name mover (`reconcileMemberSegments`) + the defensive `Discovered`
// normalization (`effectiveSegment` on read, `sanitizeBasketForPromote` on write). The #1–3 topology
// transforms (rename / reorder / add / remove links) are Phase 2 and are NOT built here.

/** The display segment for a member (read-only, no write). A member is "not in a real link" when its
 *  `segment` is NULL **or** an ORPHAN (a non-null label not present in the current chain) — either way it
 *  normalizes to `Discovered` (the visible floor), so a NULL/orphan name surfaces under a Discovered tab
 *  instead of vanishing from every tab (#9 / WB#2). Structural typing: accepts a `ScoredMemberOut` or a
 *  `BasketMember` (both carry `segment?: string | null`). */
export function effectiveSegment(member: { segment?: string | null }, segments: Segment[]): string {
  const labels = new Set(segments.map((s) => s.label));
  return member.segment != null && labels.has(member.segment) ? member.segment : DISCOVERED;
}

/** The #4 mover — rebuild ONLY `securityId`'s rows into the checked `labels`, immediate-promote-ready.
 *  Every OTHER name's rows come out byte-identical (same references, untouched). A name in N links is N
 *  rows sharing one `security_id`; this replaces that whole row-set from a representative row (`rows[0]`),
 *  copying EVERY per-name field (spread `rep` → ticker, role, security_id, detail, thesis_fit, conviction,
 *  surfaced_terms, authored_by, signed_off) so nothing is wiped (mirrors `useChainDraft.ts:263–276`), only
 *  `segment` differs. Clearing every real link (empty `labels`) parks the name in `Discovered` — the floor,
 *  NEVER `null` (a null-segment name vanishes from every tab, making a just-cleared name unreachable to
 *  re-select — WB#1/#2). Ensures the `Discovered` `Segment` exists when the floor is used, so the promote's
 *  segment-consistency validator (`thesis.py`) never trips. */
export function reconcileMemberSegments(
  basket: BasketMember[],
  segments: Segment[],
  securityId: string,
  labels: string[],
  discovered: string = DISCOVERED,
): { basket: BasketMember[]; segments: Segment[] } {
  const rows = basket.filter((b) => b.security_id === securityId);
  const others = basket.filter((b) => b.security_id !== securityId);
  const rep = rows[0];
  if (!rep) return { basket, segments }; // unknown name — nothing to rebuild (a harmless no-op)

  // the floor: an empty set parks the name in Discovered, never leaves it with no row (#9 / WB#2).
  const segs = labels.length ? labels : [discovered];
  // one row per chosen label, deduped (a doubled label can't emit two identical rows); copy every
  // per-name field forward, only the segment differs.
  const seen = new Set<string>();
  const newRows: BasketMember[] = [];
  for (const seg of segs) {
    if (seen.has(seg)) continue;
    seen.add(seg);
    newRows.push({ ...rep, segment: seg });
  }

  // ensure the Discovered link exists if the floor was used (real links already come from the live chain).
  const nextSegments =
    segs.includes(discovered) && !segments.some((s) => s.label === discovered)
      ? [...segments, { label: discovered, descriptor: null }]
      : segments;

  return { basket: [...others, ...newRows], segments: nextSegments };
}

/** The write-path self-heal — before a SCORE-view promote, rewrite any PRE-EXISTING orphan (a non-null
 *  segment not in the chain) → `Discovered`, so a stale label riding another name's row can't 422 the
 *  segment-consistency validator (`thesis.py`). NULL stays NULL where it is valid (the flat pre-decompose
 *  basket) — the validator only rejects a non-null orphan, and a NULL name display-normalizes to Discovered
 *  only when the chain is grouped. Appends the `Discovered` `Segment` when it heals one into it. */
export function sanitizeBasketForPromote(
  basket: BasketMember[],
  segments: Segment[],
  discovered: string = DISCOVERED,
): { basket: BasketMember[]; segments: Segment[] } {
  const labels = new Set(segments.map((s) => s.label));
  let healed = false;
  const nextBasket = basket.map((m) => {
    if (m.segment != null && !labels.has(m.segment)) {
      healed = true;
      return { ...m, segment: discovered };
    }
    return m;
  });
  const nextSegments =
    healed && !labels.has(discovered)
      ? [...segments, { label: discovered, descriptor: null }]
      : segments;
  return { basket: nextBasket, segments: nextSegments };
}
