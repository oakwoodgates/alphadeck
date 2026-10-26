import type { BasketMember, Segment } from "../api/hooks";
import {
  DISCOVERED,
  insertBeforeSilent,
  isSilentLink,
  memberKey,
  reservedLinkName,
  withSilentLinks,
} from "./useChainDraft";

// Pure value-chain transforms for the SCORE / triage screen — the immediate-promote counterpart to
// `useChainDraft`'s LOCAL mutators. This module MIRRORS the hook's algorithm (it does NOT import-and-mutate
// or edit it) so the SCORE view can rebuild a name's placements / edit the chain topology and hand the whole
// intended `{ segments, basket }` to the full-replace promote. No React, no side effects:
// `(basket, segments, …) → { basket, segments }`, fully unit-testable. `segment` is display / structure
// only — never a call input (#4).
//
// - Phase 1: the #4 per-name mover (`reconcileMemberSegments`) + the defensive `Discovered` normalization
//   (`effectiveSegment` on read, `sanitizeBasketForPromote` on write).
// - Phase 2: the #1–3 topology transforms — `addLink` / `renameLink` / `reorderLink` / `removeLink`,
//   mirroring `useChainDraft.ts:151–205`, with the ONE intentional delta that a removed link's last
//   placement routes to the `Discovered` FLOOR (not `null`).
// - Silent links (Context / Bench): the two FIXED, always-last links (`useChainDraft.ts`). Every transform
//   here keeps them last and untouchable — a new link / the Discovered floor lands BEFORE them, a silent link
//   is never renamed / reordered / removed, a reserved name is never taken — and `toggleMemberLink` is the
//   rail's one-row rule: a company is silent XOR voting, never both.

/** The display segment for a member (read-only, no write). A member is "not in a real link" when its
 *  `segment` is NULL **or** an ORPHAN (a non-null label not present in the current chain) — either way it
 *  normalizes to `Discovered` (the visible floor), so a NULL/orphan name surfaces under a Discovered tab
 *  instead of vanishing from every tab (#9 / WB#2). Structural typing: accepts a `ScoredMemberOut` or a
 *  `BasketMember` (both carry `segment?: string | null`). */
export function effectiveSegment(member: { segment?: string | null }, segments: Segment[]): string {
  const labels = new Set(segments.map((s) => s.label));
  // the silent links are ALWAYS known (every thesis carries them), even if a payload's list lacks them
  return member.segment != null && (labels.has(member.segment) || isSilentLink(member.segment))
    ? member.segment
    : DISCOVERED;
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

  // a company is silent XOR voting: a silent link among the checked labels IS the name's one row (a payload
  // that pairs it with another row would 422) — defensive here, `toggleMemberLink` is the real producer.
  const silent = labels.find((l) => isSilentLink(l));
  // the floor: an empty set parks the name in Discovered, never leaves it with no row (#9 / WB#2).
  const segs = silent ? [silent] : labels.length ? labels : [discovered];
  // one row per chosen label, deduped (a doubled label can't emit two identical rows); copy every
  // per-name field forward, only the segment differs.
  const seen = new Set<string>();
  const newRows: BasketMember[] = [];
  for (const seg of segs) {
    if (seen.has(seg)) continue;
    seen.add(seg);
    newRows.push({ ...rep, segment: seg });
  }

  // ensure the Discovered link exists if the floor was used (real links already come from the live chain) —
  // BEFORE Context/Bench — and the silent pair when the name was moved into one.
  let nextSegments =
    segs.includes(discovered) && !segments.some((s) => s.label === discovered)
      ? insertBeforeSilent(segments, { label: discovered, descriptor: null })
      : segments;
  if (silent && !nextSegments.some((s) => s.label === silent)) {
    nextSegments = withSilentLinks(nextSegments);
  }

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
  // The silent links are ensured FIRST (so a payload that lacked them, or carried a case-variant, goes out
  // normal) and their labels are never healed away: a "Context" row is always valid. A row riding a label
  // `withSilentLinks` dropped (a case-variant like "context") is an orphan now and heals → Discovered (#9).
  const chain = withSilentLinks(segments);
  const labels = new Set(chain.map((s) => s.label));
  let healed = false;
  const nextBasket = basket.map((m) => {
    if (m.segment != null && !isSilentLink(m.segment) && !labels.has(m.segment)) {
      healed = true;
      return { ...m, segment: discovered };
    }
    return m;
  });
  const nextSegments =
    healed && !labels.has(discovered)
      ? insertBeforeSilent(chain, { label: discovered, descriptor: null })
      : chain;
  return { basket: nextBasket, segments: nextSegments };
}

// --- #1–3 topology transforms (chain-editing Phase 2) — the immediate-promote counterparts to
// `useChainDraft`'s LOCAL segment mutators (`useChainDraft.ts:151–205`), MIRRORED here (never edited).
// Each returns the whole intended `{ basket, segments }` for the full-replace promote.

/** Append a link — no blank, no duplicate, no reserved name; it lands before Context/Bench (mirrors
 *  `useChainDraft.ts:151–159`). Basket unchanged. */
export function addLink(
  basket: BasketMember[],
  segments: Segment[],
  label: string,
  descriptor?: string,
): { basket: BasketMember[]; segments: Segment[] } {
  const l = label.trim();
  // no blank, no duplicate, and never a RESERVED name (Context / Bench, any case — those two are fixed)
  if (!l || segments.some((s) => s.label === l) || reservedLinkName(l)) return { basket, segments };
  return {
    basket,
    // lands BEFORE Context/Bench — the pinned pair stays last
    segments: insertBeforeSilent(segments, { label: l, descriptor: descriptor?.trim() || null }),
  };
}

/** Rename a link and CASCADE the new label onto every placed member so none orphans (mirrors
 *  `useChainDraft.ts:161–170`). No blank, no duplicate (renaming to the current/an existing label is a
 *  no-op). */
export function renameLink(
  basket: BasketMember[],
  segments: Segment[],
  oldLabel: string,
  newLabel: string,
): { basket: BasketMember[]; segments: Segment[] } {
  const l = newLabel.trim();
  if (!l || segments.some((s) => s.label === l)) return { basket, segments };
  // a silent link can't be renamed, and no link can be renamed INTO a reserved name
  if (isSilentLink(oldLabel) || reservedLinkName(l)) return { basket, segments };
  return {
    segments: segments.map((s) => (s.label === oldLabel ? { ...s, label: l } : s)),
    basket: basket.map((m) => (m.segment === oldLabel ? { ...m, segment: l } : m)),
  };
}

/** Reorder a link by one slot, swapping it with its neighbor (mirrors `useChainDraft.ts:172–180`). A
 *  boundary move (past either end) is a no-op. Basket unchanged. */
export function reorderLink(
  basket: BasketMember[],
  segments: Segment[],
  label: string,
  dir: -1 | 1,
): { basket: BasketMember[]; segments: Segment[] } {
  if (isSilentLink(label)) return { basket, segments }; // Context / Bench are pinned last — they never move
  const i = segments.findIndex((s) => s.label === label);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= segments.length) return { basket, segments };
  if (isSilentLink(segments[j].label)) return { basket, segments }; // …and no link swaps past or with one
  const next = [...segments];
  [next[i], next[j]] = [next[j], next[i]];
  return { basket, segments: next };
}

/** Remove a link, MULTI-MEMBERSHIP-SAFE (mirrors `useChainDraft.ts:182–205`), with the ONE intentional
 *  delta for the SCORE view: a name's LAST placement routes to the `Discovered` FLOOR, not `null` (§4 — a
 *  null-segment name vanishes from every tab). A name kept in another link loses only its now-redundant row
 *  in the removed link; a name whose only placement was the removed link lands in `Discovered` (one row),
 *  never dropped (#9). Ensures the `Discovered` `Segment` exists when a placement floors to it. */
export function removeLink(
  basket: BasketMember[],
  segments: Segment[],
  label: string,
  discovered: string = DISCOVERED,
): { basket: BasketMember[]; segments: Segment[] } {
  if (isSilentLink(label)) return { basket, segments }; // Context / Bench are fixed — never removable
  const keptElsewhere = new Map<string, number>(); // rows of each name OUTSIDE the removed link
  for (const m of basket) {
    if (m.segment !== label) {
      keptElsewhere.set(memberKey(m), (keptElsewhere.get(memberKey(m)) ?? 0) + 1);
    }
  }
  const flooredOnce = new Set<string>(); // a name with several rows all in the removed link keeps ONE
  const nextBasket = basket.flatMap((m) => {
    if (m.segment !== label) return [m];
    const k = memberKey(m);
    if ((keptElsewhere.get(k) ?? 0) > 0 || flooredOnce.has(k)) return []; // redundant row — drop
    flooredOnce.add(k);
    return [{ ...m, segment: discovered }]; // the name's last placement → the Discovered floor
  });
  let nextSegments = segments.filter((s) => s.label !== label);
  if (flooredOnce.size > 0 && !nextSegments.some((s) => s.label === discovered)) {
    // the floor lands BEFORE Context/Bench — never after them, and a name never floors INTO one
    nextSegments = insertBeforeSilent(nextSegments, { label: discovered, descriptor: null });
  }
  return { basket: nextBasket, segments: nextSegments };
}

/** The rail's one-row rule (silent links) — the next checked-label set after the operator toggles `label`
 *  on a name currently in `current`. A company is silent XOR voting, so:
 *   - checking a SILENT link → `[label]` (all the name's other rows collapse to that one);
 *   - checking a REAL link while the name is silent → `[label]` (it leaves Context/Bench for that link);
 *   - checking a real link otherwise → `current + label` (multi-membership as before);
 *   - UNCHECKING → `current − label` (the empty set floors to Discovered via `reconcileMemberSegments`).
 *  The inverse of every check is its uncheck (WB#1); nothing is ever deleted (WB#2 / #9). Pure. */
export function toggleMemberLink(current: string[], label: string): string[] {
  if (current.includes(label)) return current.filter((l) => l !== label);
  if (isSilentLink(label)) return [label];
  if (current.some((l) => isSilentLink(l))) return [label];
  return [...current, label];
}
