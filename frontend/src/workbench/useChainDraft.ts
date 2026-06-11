import { useState } from "react";

import type { BasketMember, Segment, ThesisDetail } from "../api/hooks";

// A member is keyed by its resolved security_id (always present for seeded + resolver-added names),
// falling back to the ticker — so place / move / remove address the right row.
export const memberKey = (m: { security_id?: string | null; ticker: string }): string =>
  m.security_id ?? m.ticker;

export interface ChainDraft {
  segments: Segment[];
  basket: BasketMember[];
}

function snapshot(thesis: ThesisDetail): ChainDraft {
  return {
    segments: thesis.segments.map((s) => ({ ...s })),
    basket: thesis.basket.map((m) => ({ ...m })),
  };
}

/** Local, editable draft of a thesis's value chain (segments + placements). All edits are LOCAL until
 *  the caller saves the whole draft through the full-replace `POST /workbench/theses` — so the mutators
 *  build the complete intended state, never a diff. Segment edits cascade to placements so the chain
 *  stays consistent (the server's orphan validator never trips). The draft is snapshotted at mount; the
 *  editor remounts (and re-snapshots) after a save, so there is no in-hook re-sync. */
export function useChainDraft(thesis: ThesisDetail) {
  const [base] = useState<ChainDraft>(() => snapshot(thesis));
  const [draft, setDraft] = useState<ChainDraft>(base);

  const dirty = JSON.stringify(draft) !== JSON.stringify(base);

  const addSegment = (label: string, descriptor?: string) =>
    setDraft((d) => {
      const l = label.trim();
      if (!l || d.segments.some((s) => s.label === l)) return d; // no blank, no duplicate
      return {
        ...d,
        segments: [...d.segments, { label: l, descriptor: descriptor?.trim() || null }],
      };
    });

  const renameSegment = (oldLabel: string, newLabel: string) =>
    setDraft((d) => {
      const l = newLabel.trim();
      if (!l || d.segments.some((s) => s.label === l)) return d;
      return {
        // cascade the rename to placements so a placed member never orphans
        segments: d.segments.map((s) => (s.label === oldLabel ? { ...s, label: l } : s)),
        basket: d.basket.map((m) => (m.segment === oldLabel ? { ...m, segment: l } : m)),
      };
    });

  const moveSegment = (label: string, dir: -1 | 1) =>
    setDraft((d) => {
      const i = d.segments.findIndex((s) => s.label === label);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= d.segments.length) return d;
      const segments = [...d.segments];
      [segments[i], segments[j]] = [segments[j], segments[i]];
      return { ...d, segments };
    });

  const removeSegment = (label: string) =>
    setDraft((d) => ({
      segments: d.segments.filter((s) => s.label !== label),
      // un-place its members (keep the names; don't orphan them)
      basket: d.basket.map((m) => (m.segment === label ? { ...m, segment: null } : m)),
    }));

  const placeMember = (key: string, segment: string | null) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) => (memberKey(m) === key ? { ...m, segment } : m)),
    }));

  const addMember = (m: BasketMember) =>
    setDraft((d) =>
      d.basket.some((x) => memberKey(x) === memberKey(m)) ? d : { ...d, basket: [...d.basket, m] },
    );

  const removeMember = (key: string) =>
    setDraft((d) => ({ ...d, basket: d.basket.filter((m) => memberKey(m) !== key) }));

  return {
    draft,
    dirty,
    addSegment,
    renameSegment,
    moveSegment,
    removeSegment,
    placeMember,
    addMember,
    removeMember,
  };
}
