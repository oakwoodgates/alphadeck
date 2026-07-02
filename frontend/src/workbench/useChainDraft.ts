import { useState } from "react";

import type { BasketMember, ChainDraftOut, Segment, ThesisDetail } from "../api/hooks";

// A member is keyed by its resolved security_id (always present for seeded + resolver-added names),
// falling back to the ticker — so place / move / remove address the right row.
export const memberKey = (m: { security_id?: string | null; ticker: string }): string =>
  m.security_id ?? m.ticker;

// Editing a DRAFTED placement (system_drafted) is the operator taking it over → operator_edited; the
// operator's own placement (operator_set / operator_edited) is unchanged by a further edit.
const touched = (m: BasketMember): BasketMember["authored_by"] =>
  m.authored_by === "system_drafted" ? "operator_edited" : m.authored_by;

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

  // TRIAGE (the prune) — include is a NEW, FE-only concept ORTHOGONAL to accept/authorship. A member starts
  // INCLUDED (#9: nothing silently dropped — the operator UNCHECKS to exclude); `excluded` holds the keys the
  // operator has chosen to leave OUT of the saved basket. It is never persisted — Save sends `includedBasket`,
  // and the promote full-replace simply doesn't receive the excluded names (the draft is reproducible by
  // re-drafting). Include NEVER touches `authored_by` — a name can be accepted-but-excluded or the reverse.
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  // An exclusion is an unsaved intent even when the draft structure is untouched (Save would persist fewer names).
  const dirty = JSON.stringify(draft) !== JSON.stringify(base) || excluded.size > 0;

  const isIncluded = (key: string) => !excluded.has(key);
  const includedBasket = draft.basket.filter((m) => !excluded.has(memberKey(m)));

  const toggleInclude = (key: string) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const includeAll = () => setExcluded(new Set());
  const excludeAll = () => setExcluded(new Set(draft.basket.map(memberKey)));
  // "Clear un-accepted" — exclude every still-drafted (system_drafted) name, the fast path to just-my-vouched
  // names. ADDITIVE (union) so a manually-excluded accepted name stays excluded; sets exclude only, never
  // touches authorship (accept stays the separate act).
  const excludeUnaccepted = () =>
    setExcluded((prev) => {
      const next = new Set(prev);
      for (const m of draft.basket) {
        if (m.authored_by === "system_drafted") next.add(memberKey(m));
      }
      return next;
    });

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
      basket: d.basket.map((m) =>
        memberKey(m) === key ? { ...m, segment, authored_by: touched(m) } : m,
      ),
    }));

  const addMember = (m: BasketMember) =>
    setDraft((d) =>
      d.basket.some((x) => memberKey(x) === memberKey(m)) ? d : { ...d, basket: [...d.basket, m] },
    );

  const removeMember = (key: string) => {
    setDraft((d) => ({ ...d, basket: d.basket.filter((m) => memberKey(m) !== key) }));
    // a hard-removed name leaves the draft entirely — drop any stale exclude marker so `dirty` doesn't linger.
    setExcluded((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  };

  // --- S5 5c: draft from the narrative, then ratify per member ---

  // MERGE a drafted chain in (never replace): append any NEW segments, and add the PLACED names as
  // `system_drafted` placements, deduped by security_id (a name already in the basket is skipped — the
  // operator's existing work is never clobbered). AMBIGUOUS / ABSENT names are NOT added here: the operator
  // picks an AMBIGUOUS one explicitly (the editor surfaces the pick list), an ABSENT one is shown-not-placed.
  const loadDraft = (chain: ChainDraftOut) =>
    setDraft((d) => {
      const haveSeg = new Set(d.segments.map((s) => s.label));
      const segments = [
        ...d.segments,
        ...chain.segments
          .filter((s) => !haveSeg.has(s.label))
          .map((s) => ({ label: s.label, descriptor: s.descriptor ?? null })),
      ];
      const have = new Set(d.basket.map(memberKey));
      const additions: BasketMember[] = chain.placements
        .filter((p) => p.status === "placed" && p.security_id && !have.has(p.security_id))
        .map((p) => ({
          ticker: p.ticker || p.name,
          role: "—",
          archetype: "high_beta",
          security_id: p.security_id,
          segment: p.segment,
          thesis_fit: p.prose || null,
          authored_by: "system_drafted",
        }));
      return { segments, basket: [...d.basket, ...additions] };
    });

  // Ratify a drafted placement as-is — the operator owns it now.
  const acceptMember = (key: string) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) =>
        memberKey(m) === key ? { ...m, authored_by: "operator_set" } : m,
      ),
    }));

  // Edit the thesis-fit prose; editing a drafted member takes it over (→ operator_edited).
  const editProse = (key: string, text: string) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) =>
        memberKey(m) === key ? { ...m, thesis_fit: text, authored_by: touched(m) } : m,
      ),
    }));

  // Re-classify the basket role; editing a drafted member takes it over (→ operator_edited).
  const editArchetype = (key: string, archetype: BasketMember["archetype"]) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) =>
        memberKey(m) === key ? { ...m, archetype, authored_by: touched(m) } : m,
      ),
    }));

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
    loadDraft,
    acceptMember,
    editProse,
    editArchetype,
    // TRIAGE (the prune): include-state + the included subset Save persists
    excluded,
    isIncluded,
    includedBasket,
    toggleInclude,
    includeAll,
    excludeAll,
    excludeUnaccepted,
  };
}
