import { useState } from "react";

import type { BasketMember, ChainDraftOut, Segment, ThesisDetail } from "../api/hooks";

// A member is keyed by its resolved security_id (always present for seeded + resolver-added names),
// falling back to the ticker — so place / move / remove address the right row.
export const memberKey = (m: { security_id?: string | null; ticker: string }): string =>
  m.security_id ?? m.ticker;

// The reconciler's catch-all segment (backend `_DISCOVERED_LABEL`): names discovered but not arranged into a
// real value-chain link. A re-draft parks a superseded drafted name here (an honest holding pen) rather than
// leaving it in a stale segment. One home for the label (ChainEditor imports it).
export const DISCOVERED = "Discovered";

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
  // Bulk-exclude a specific set of names — the group-level "exclude all" on a display lens (the acronym-
  // collision cluster). Same contract as excludeUnaccepted: ADDITIVE, exclude-only, never touches authorship;
  // every row stays visible (greyed) and re-includable in one click (#9).
  const excludeKeys = (keys: string[]) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      for (const k of keys) next.add(k);
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

  // Load a drafted chain — a RE-ROLL, not a blind merge. Its one narrow job: KEEP operator-authored names
  // exactly (operator_set / operator_edited — never clobbered, absolute), RE-ROLL every system_drafted name
  // (fresh segment + prose from the new decomposition), and SURFACE genuinely new placed names. A
  // system_drafted name the new draft no longer places is parked in "Discovered" (a stale segment is a lie;
  // Discovered is honest — #9, still visible, re-sortable), NOT left in its superseded segment. AMBIGUOUS /
  // ABSENT names are NOT added here (the editor surfaces those for an explicit pick / shown-not-placed).
  const loadDraft = (chain: ChainDraftOut) =>
    setDraft((d) => {
      // the new draft's PLACED names, by security_id → their fresh segment/prose
      const placed = new Map<string, { segment: string | null; prose: string | null }>();
      for (const p of chain.placements) {
        if (p.status === "placed" && p.security_id) {
          placed.set(p.security_id, { segment: p.segment, prose: p.prose || null });
        }
      }
      let orphaned = false; // did any drafted name fall out of the new draft → reset to Discovered?
      const basket = d.basket.map((m) => {
        // operator-authored → untouched (never clobber operator work)
        if (m.authored_by === "operator_set" || m.authored_by === "operator_edited") return m;
        // system_drafted: re-roll if still placed, else park in Discovered (drop the stale segment)
        const fresh = m.security_id ? placed.get(m.security_id) : undefined;
        if (fresh) return { ...m, segment: fresh.segment, thesis_fit: fresh.prose };
        orphaned = true;
        return { ...m, segment: DISCOVERED };
      });
      // append genuinely NEW placed names (not already in the basket), deduped by security_id
      const have = new Set(d.basket.map(memberKey));
      const additions: BasketMember[] = chain.placements
        .filter((p) => p.status === "placed" && p.security_id && !have.has(p.security_id))
        .map((p) => ({
          ticker: p.ticker || p.name,
          role: "—",
          archetype: null, // un-decided (item F) — the finalize rail sets it; never a placement default
          security_id: p.security_id,
          segment: p.segment,
          thesis_fit: p.prose || null,
          conviction: null, // the drafter never weights — the operator sets conviction in the row
          authored_by: "system_drafted",
        }));
      // append NEW segments; ensure Discovered exists if we parked an orphan there (else Save orphans it)
      const haveSeg = new Set(d.segments.map((s) => s.label));
      const segments = [
        ...d.segments,
        ...chain.segments
          .filter((s) => !haveSeg.has(s.label))
          .map((s) => ({ label: s.label, descriptor: s.descriptor ?? null })),
      ];
      if (orphaned && !segments.some((s) => s.label === DISCOVERED)) {
        segments.push({ label: DISCOVERED, descriptor: null });
      }
      return { segments, basket: [...basket, ...additions] };
    });

  // Accept ⇄ un-accept (reversibility, principle #1) — a TOGGLE. Accept ratifies a drafted placement
  // (system_drafted → operator_set, the operator owns it now). Un-accept is the visible inverse: it flips
  // authorship back to system_drafted and KEEPS every field value (segment / prose / conviction / archetype)
  // untouched — "I don't vouch anymore, let it re-roll next draft", NOT "undo my edits". Uniform across all
  // three states (operator_set / operator_edited both → system_drafted, edits intact). Composes with a
  // re-draft (loadDraft): a name back at system_drafted is re-rolled, which is the whole point.
  const toggleAccept = (key: string) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) =>
        memberKey(m) === key
          ? { ...m, authored_by: m.authored_by === "system_drafted" ? "operator_set" : "system_drafted" }
          : m,
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

  // (item F: there is NO editArchetype here — the archetype is decided ONCE, on the finalize rail
  // (DDRail hint → apply/override through the promote writer); the editor never sets or defaults it.)

  // The operator's per-name conviction/size (1–5; null = unset). ORTHOGONAL to authorship — unlike archetype/
  // prose (drafted CONTENT the operator overrides), conviction is a fresh operator axis the drafter never sets,
  // so weighting a still-drafted name does NOT consume its "accept" (same orthogonality as include). Stored
  // metadata: it never feeds the meters/verdict/grade (#4).
  const editConviction = (key: string, conviction: number | null) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) => (memberKey(m) === key ? { ...m, conviction } : m)),
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
    toggleAccept,
    editProse,
    editConviction,
    // TRIAGE (the prune): include-state + the included subset Save persists
    excluded,
    isIncluded,
    includedBasket,
    toggleInclude,
    includeAll,
    excludeAll,
    excludeUnaccepted,
    excludeKeys,
  };
}
