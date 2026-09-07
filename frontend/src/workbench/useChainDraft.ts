import { useState } from "react";

import type { BasketMember, ChainDraftOut, Segment, ThesisDetail } from "../api/hooks";

// A member is keyed by its resolved security_id (always present for seeded + resolver-added names),
// falling back to the ticker. A NAME may hold N basket rows (one per LLM-recommended segment — the
// multi-membership placements, S1), all sharing this key — so every per-name action (sign-off /
// include / description-edit / remove) co-mutates ALL of a name's rows via `.map` over the key.
export const memberKey = (m: { security_id?: string | null; ticker: string }): string =>
  m.security_id ?? m.ticker;

// The reconciler's catch-all segment (backend `_DISCOVERED_LABEL`): names discovered but not arranged into a
// real value-chain link. A re-draft parks a superseded drafted name here (an honest holding pen) rather than
// leaving it in a stale segment. One home for the label (ChainEditor imports it).
export const DISCOVERED = "Discovered";

// HONEST AUTHORSHIP (S1): `authored_by` tracks WHO WROTE the description — "model draft"
// (system_drafted) until the operator EDITS the prose → operator_edited ("your words"). Editing is the
// ONLY flip; sign-off / include / conviction never touch it. (`operator_set` is retired for members —
// a legacy blob's value is normalized at session-restore and translated at promote.)
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

// THE PLACED MODE (Research ⇄ Pick) — HOW the operator decides the PLACED section: one of two gestures over
// the SAME working list.
//   research (today's prune, the default): every name starts checked + open; unchecking is the DURABLE NO
//     (`excluded`, persisted on Save — a rejected name returns pre-greyed next time).
//   pick (the additive SELECT): every name starts un-picked + open/available; checking PICKS it into the
//     basket (`selected`) and collapses it. Pick NEVER writes a durable exclusion — an un-picked name is
//     AVAILABLE, not rejected (silence is never a NO).
// Two sets, one live per mode, the other DORMANT: `excluded` is research's, `selected` is pick's. The reads
// keep their names (`isIncluded` / `includedBasket`) so every consumer stays mode-blind; `isCollapsed` is
// the polarity flip (research collapses the decided-OUT row, pick the decided-IN row).
export type PlacedMode = "research" | "pick";

/** The hook portion of a restored triage session (see `triageSession.ts`). When present, `draft`/`excluded`/
 *  `reasons`/`reasonsDirty` SEED from the blob instead of the thesis — resuming the operator's prune. `base`/
 *  `baseExcluded` deliberately still seed from the THESIS (the persisted spine), so `dirty` stays honest: a
 *  restored-but-unsaved prune correctly reads as dirty (it differs from what's persisted). */
export interface RestoredChainState {
  draft: ChainDraft;
  excluded: Set<string>;
  reasons: Map<string, string>;
  reasonsDirty: boolean;
  // PLACED MODE (additive, optional — an old blob restores without them: research + ∅, byte-identical to
  // before). A restore SEEDS the mode + the pick selection exactly like `excluded` — a restore is NOT a reset.
  selected?: Set<string>;
  placedMode?: PlacedMode;
}

const setsEqual = (a: Set<string>, b: Set<string>) =>
  a.size === b.size && [...a].every((x) => b.has(x));
const withToggled = (prev: Set<string>, key: string): Set<string> => {
  const next = new Set(prev);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
};
// add keys to a Set, returning the SAME instance when nothing changes (no spurious re-render / autosave)
const withAdded = (prev: Set<string>, keys: Iterable<string>): Set<string> => {
  let next: Set<string> | null = null;
  for (const k of keys) {
    if (prev.has(k)) continue;
    next ??= new Set(prev);
    next.add(k);
  }
  return next ?? prev;
};
const withDeleted = (prev: Set<string>, key: string): Set<string> => {
  if (!prev.has(key)) return prev;
  const next = new Set(prev);
  next.delete(key);
  return next;
};

/** Local, editable draft of a thesis's value chain (segments + placements). All edits are LOCAL until
 *  the caller saves the whole draft through the full-replace `POST /workbench/theses` — so the mutators
 *  build the complete intended state, never a diff. Segment edits cascade to placements so the chain
 *  stays consistent (the server's orphan validator never trips). The draft is snapshotted at mount; the
 *  editor remounts (and re-snapshots) after a save, so there is no in-hook re-sync. `restored` (an
 *  autosaved triage session) seeds the working state at mount when present — same snapshot-at-mount
 *  discipline, blob instead of thesis. */
export function useChainDraft(thesis: ThesisDetail, restored?: RestoredChainState) {
  const [base] = useState<ChainDraft>(() => snapshot(thesis));
  const [draft, setDraft] = useState<ChainDraft>(() => restored?.draft ?? base);

  // THE BASKET FREEZE (the additive editor) — the ESTABLISHED keys, computed ONCE at mount: the members
  // present in BOTH the persisted spine (`base`) and the working draft we seeded from (`restored?.draft ??
  // base`). An INTERSECTION, deliberately not plain base keys: after a Clear (a restored EMPTY draft) the
  // set is empty, so a re-discovered old-spine name is a NEW drafted name again, never wrongly frozen.
  // An established member is FROZEN against the drafter: `loadDraft` never re-rolls it (or parks it in
  // Discovered), regardless of authorship — a draft over an established thesis only ADDS new names.
  const [establishedKeys] = useState<Set<string>>(() => {
    const draftKeys = new Set((restored?.draft ?? base).basket.map(memberKey));
    return new Set(base.basket.map(memberKey).filter((k) => draftKeys.has(k)));
  });
  const isEstablished = (key: string) => establishedKeys.has(key);

  // TRIAGE (the prune) — include is the CONFIDENCE LADDER's gate (Excluded → Included → Signed off;
  // excluded wins). A member starts INCLUDED (#9: nothing silently dropped — the operator UNCHECKS to
  // exclude); `excluded` holds the keys chosen to leave OUT of the saved basket. #7 made the NO durable:
  // the set SEEDS from the thesis's persisted exclusions (previously-rejected names arrive pre-greyed,
  // visible, one click back — never a filter), and Save persists the current set (with the optional
  // reasons) through PUT /theses/{id}/exclusions alongside the promote. Include NEVER touches
  // `authored_by` or `signed_off` — greying a name out neither un-endorses it nor un-writes anything.
  const [baseExcluded] = useState<Set<string>>(
    () => new Set((thesis.exclusions ?? []).map((e) => e.security_id)),
  );
  const [excluded, setExcluded] = useState<Set<string>>(() => restored?.excluded ?? baseExcluded);
  // the optional "rejected because X", keyed like `excluded`; seeded from the persisted rows (or the blob)
  const [reasons, setReasons] = useState<Map<string, string>>(
    () =>
      restored?.reasons ??
      new Map(
        (thesis.exclusions ?? [])
          .filter((e) => e.reason)
          .map((e) => [e.security_id, e.reason as string]),
      ),
  );
  const [reasonsDirty, setReasonsDirty] = useState(() => restored?.reasonsDirty ?? false);
  const editReason = (key: string, text: string) => {
    setReasons((prev) => new Map(prev).set(key, text));
    setReasonsDirty(true);
  };

  // THE PLACED MODE + the pick selection (see `PlacedMode`). Both SEED from the blob like `excluded` does
  // (a restore is not a reset); a fresh mount is research with an empty (dormant) selection. `selected`
  // is never touched by `loadDraft` — a bulk arrival is UNDECIDED by construction; only an explicit act
  // (a checkbox, a To-Review add, a pile pick, a hand-add, a sign-off) decides a name IN.
  const [placedMode, setPlacedModeState] = useState<PlacedMode>(
    () => restored?.placedMode ?? "research",
  );
  const [selected, setSelected] = useState<Set<string>>(() => restored?.selected ?? new Set());
  const pick = placedMode === "pick";

  // Mode-aware READS — the names stay so every consumer is mode-blind.
  //   research: included ⇔ not excluded (the default-on prune) · pick: included ⇔ selected (the additive pick)
  const isIncluded = (key: string) => (pick ? selected.has(key) : !excluded.has(key));
  // what Save persists in BOTH modes — the whole draft's included rows, regardless of any view filter (#9)
  const includedBasket = draft.basket.filter((m) => isIncluded(memberKey(m)));
  // the POLARITY FLIP: research collapses the decided-OUT row (the excluded stub), pick collapses the
  // decided-IN row (the picked stub); the open row is the same included row in both modes
  const isCollapsed = (key: string) => (pick ? isIncluded(key) : !isIncluded(key));
  // display-only: the name carries a PERSISTED research NO (the pick-mode "excluded in research" tag —
  // keep-visible #2; picking it withdraws the NO on Save)
  const isDurablyExcluded = (key: string) => baseExcluded.has(key);

  // an exclusion (or reason) edit is an unsaved intent even when the draft structure is untouched —
  // but the SEEDED baseline is not (a clean load must not read as dirty)
  const researchDirty =
    JSON.stringify(draft) !== JSON.stringify(base) ||
    !setsEqual(excluded, baseExcluded) ||
    reasonsDirty;
  // PICK: the raw draft-vs-base compare is meaningless (the AVAILABLE pool lives in the draft — an un-picked
  // arrival changes nothing Save would write), so dirty = "the rows a Save would persist differ from the
  // spine": the selected rows vs the saved basket, or the segments. Research's `dirty` is untouched.
  const pickDirty =
    JSON.stringify(includedBasket) !== JSON.stringify(base.basket) ||
    JSON.stringify(draft.segments) !== JSON.stringify(base.segments);
  const dirty = pick ? pickDirty : researchDirty;

  // Mode-aware WRITE: research toggles the durable-NO set (unchanged); pick toggles the selection and NEVER
  // touches `excluded` (un-picked = available, not rejected).
  const toggleInclude = (key: string) =>
    pick
      ? setSelected((prev) => withToggled(prev, key))
      : setExcluded((prev) => withToggled(prev, key));

  // --- the mode toggle: a WORKING-SCOPED RESET (never a restore; sign-off untouched by either) ---------
  // Entering PICK: the saved Basket's kept names (established ∧ included-in-research) stay checked — mode
  // entry NEVER empties the saved basket — and every signed-off name is checked; everything else in the
  // WORKING list becomes un-picked / open. `excluded` is left dormant (it comes back on research entry).
  const selectionForPick = (): Set<string> => {
    const next = new Set<string>();
    for (const m of draft.basket) {
      const k = memberKey(m);
      if ((establishedKeys.has(k) && !excluded.has(k)) || m.signed_off) next.add(k);
    }
    return next;
  };
  // Entering RESEARCH: the fresh-research-mount state — every working name checked EXCEPT the persisted
  // NOs, which return pre-greyed (NOT a literal all-check: that would silently withdraw every durable NO on
  // the next research Save); a signed-off name is checked regardless. `selected` is left dormant.
  const exclusionForResearch = (): Set<string> => {
    const next = new Set(baseExcluded);
    for (const m of draft.basket) if (m.signed_off) next.delete(memberKey(m));
    return next;
  };
  // How many NAMES would flip checked-state if the mode switched — the caller's conditional confirm
  // (a switch that changes nothing asks nothing). The active mode is always 0.
  const placedModeResetCount = (mode: PlacedMode): number => {
    if (mode === placedMode) return 0;
    const target = mode === "pick" ? selectionForPick() : exclusionForResearch();
    const includedAfter = (k: string) => (mode === "pick" ? target.has(k) : !target.has(k));
    let n = 0;
    for (const k of new Set(draft.basket.map(memberKey))) {
      if (includedAfter(k) !== isIncluded(k)) n += 1;
    }
    return n;
  };
  // Switch modes (the reset above). Clicking the already-active mode is a no-op. Sign-off (`signed_off` on
  // the member) rides `draft`, which neither reset mutates — the flag is NEVER touched by a toggle.
  const setPlacedMode = (mode: PlacedMode) => {
    if (mode === placedMode) return;
    if (mode === "pick") setSelected(selectionForPick());
    else setExcluded(exclusionForResearch());
    setPlacedModeState(mode);
  };
  const includeAll = () => setExcluded(new Set());
  const excludeAll = () => setExcluded(new Set(draft.basket.map(memberKey)));
  // "Clear not signed-off" — exclude every name the operator has NOT endorsed (the sign-off flag, never
  // authorship), the fast path to just-my-endorsed names. ADDITIVE (union) so a manually-excluded
  // signed-off name stays excluded; sets exclude only, never touches authorship or the flag itself.
  // WORKING-SCOPED: an ESTABLISHED member (the frozen Basket) is never swept, even if it rides the spine
  // un-endorsed — the bulk clear targets the new draft.
  const excludeNotSignedOff = () =>
    setExcluded((prev) => {
      const next = new Set(prev);
      for (const m of draft.basket) {
        if (!m.signed_off && !establishedKeys.has(memberKey(m))) {
          next.add(memberKey(m));
        }
      }
      return next;
    });
  // Bulk-exclude a specific set of names — the group-level "exclude all" on a display lens (the low-quality
  // cluster). Same contract as excludeNotSignedOff: ADDITIVE, exclude-only, never touches authorship or
  // the sign-off flag; every row stays visible (greyed) and re-includable in one click (#9).
  const excludeKeys = (keys: string[]) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      for (const k of keys) next.add(k);
      return next;
    });
  // The visible inverse of excludeKeys (reversibility #1) — bulk RE-include a specific set of names (the
  // working-scoped "include all new"). Same contract: include-state only, never touches authorship.
  const includeKeys = (keys: string[]) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      for (const k of keys) next.delete(k);
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
    setDraft((d) => {
      // MULTI-MEMBERSHIP-SAFE (S1): a name may hold N rows (one per recommended link). Removing a link
      // DROPS the name's row in it when the name keeps another placement (a now-redundant membership —
      // no duplicate unplaced row left behind) and NULLS the segment only on the name's LAST placement
      // (#9 — the NAME never leaves the basket; it just becomes unplaced, exactly as before).
      const keptElsewhere = new Map<string, number>(); // rows of each name OUTSIDE the removed link
      for (const m of d.basket) {
        if (m.segment !== label) {
          keptElsewhere.set(memberKey(m), (keptElsewhere.get(memberKey(m)) ?? 0) + 1);
        }
      }
      const nulledOnce = new Set<string>(); // a name with SEVERAL rows all in the removed link keeps ONE
      return {
        segments: d.segments.filter((s) => s.label !== label),
        basket: d.basket.flatMap((m) => {
          if (m.segment !== label) return [m];
          const k = memberKey(m);
          if ((keptElsewhere.get(k) ?? 0) > 0 || nulledOnce.has(k)) return []; // redundant row — drop
          nulledOnce.add(k);
          return [{ ...m, segment: null }]; // the name's last placement — un-place, never remove
        }),
      };
    });

  // An EXPLICIT add (hand-add / ETF sleeve / To-Review add / ambiguous pick) is a DECISION: the name is
  // always selected (checked ⇒ collapsed in pick; harmless in research, where the set is dormant).
  const addMember = (m: BasketMember) => {
    setDraft((d) =>
      d.basket.some((x) => memberKey(x) === memberKey(m)) ? d : { ...d, basket: [...d.basket, m] },
    );
    setSelected((prev) => withAdded(prev, [memberKey(m)]));
  };

  // CHERRY-PICK (pick-mode): append ALL of ONE name's membership rows in a single act — the Recommended
  // pile's check-to-add for a name the draft recommends into N links (N rows, same security_id — the S1
  // multi-membership shape loadDraft's additions branch would have appended). addMember dedups by NAME, so
  // rows 2..N of a multi-link pick would silently drop through it — this is its multi-row sibling with the
  // SAME one-name guard: a name already in the basket appends nothing. Rows arrive pre-shaped by the caller.
  // The pile pick is an explicit act too — the name is selected like any add.
  const addMemberRows = (rows: BasketMember[]) => {
    if (rows.length === 0) return;
    const key = memberKey(rows[0]);
    setDraft((d) => {
      if (d.basket.some((x) => memberKey(x) === key)) return d;
      return { ...d, basket: [...d.basket, ...rows] };
    });
    setSelected((prev) => withAdded(prev, [key]));
  };

  const removeMember = (key: string) => {
    setDraft((d) => ({ ...d, basket: d.basket.filter((m) => memberKey(m) !== key) }));
    // a hard-removed name leaves the draft entirely — drop any stale exclude marker so `dirty` doesn't linger.
    setExcluded((prev) => withDeleted(prev, key));
    setSelected((prev) => withDeleted(prev, key)); // …and its pick marker, for the same reason
  };

  // --- S5 5c: draft from the narrative, then ratify per member ---

  // Load a drafted chain — a RE-ROLL, not a blind merge. Its one narrow job: KEEP operator-EDITED names
  // exactly (an edited description pins — never clobbered), RE-ROLL every system_drafted name (fresh
  // segment(s) + prose from the new decomposition), and SURFACE genuinely new placed names. MULTI-
  // MEMBERSHIP (S1): a name the draft recommends into N links yields N rows (same security_id, one per
  // link) — the old Map-keyed-by-sid squashed these to last-wins, a real recall-of-structure bug. The
  // sign-off flag CARRIES across a re-roll (it endorses the NAME, not the words — locked decision 4), and
  // a signed-off name is never dropped (#9). A system_drafted name the new draft no longer places is
  // parked in "Discovered" as ONE row (a stale segment is a lie; Discovered is honest — #9, still
  // visible). AMBIGUOUS / ABSENT names are NOT added here (explicit pick / shown-not-placed).
  const loadDraft = (chain: ChainDraftOut) =>
    setDraft((d) => {
      // ALL of the new draft's PLACED placements per security_id — every recommended link, in draft order.
      const placed = new Map<string, { segment: string | null; prose: string | null }[]>();
      for (const p of chain.placements) {
        if (p.status === "placed" && p.security_id) {
          const list = placed.get(p.security_id) ?? [];
          list.push({ segment: p.segment, prose: p.prose || null });
          placed.set(p.security_id, list);
        }
      }
      // The value chain is ADDITIVE once it exists: a re-draft over an established chain never invents new
      // links. The drafter rephrases the chain every run ("Psychedelic & Ketamine Drug Developers" one run,
      // "Clinical-Stage Psychedelic Drug Developers" the next), so appending piles up near-duplicate links.
      // So: a FIRST draft (no chain yet) adopts the drafter's links; once you HAVE a chain, a re-draft keeps
      // it exactly and files new / re-rolled names into an EXISTING link (exact-label match) or the
      // "Discovered" unsorted pen — never a fabricated link. `fileSeg` routes any segment through that rule.
      const hasChain = d.segments.some((s) => s.label !== DISCOVERED);
      const haveSeg = new Set(d.segments.map((s) => s.label));
      const fileSeg = (seg: string | null): string | null =>
        hasChain && !(seg && haveSeg.has(seg)) ? DISCOVERED : seg;
      // The per-NAME description: ONE description per name (all its rows carry it) — the first non-empty
      // drafted prose across the name's placements (the chips carry the multi-link info; the description
      // stays a single, per-name field).
      const proseOf = (list: { prose: string | null }[]): string | null =>
        list.find((x) => x.prose)?.prose ?? null;
      // Expand a name into its FRESH row set: one row per recommended link (deduped by the FILED segment —
      // two placements filing into the same pen/link collapse to one row). `base` carries the name's kept
      // per-name fields (signed_off / conviction / role / surfaced_terms / ticker).
      const freshRows = (
        base: BasketMember,
        list: { segment: string | null; prose: string | null }[],
      ): BasketMember[] => {
        const out: BasketMember[] = [];
        const seen = new Set<string>();
        for (const f of list) {
          const seg = fileSeg(f.segment);
          if (seen.has(seg ?? "")) continue;
          seen.add(seg ?? "");
          out.push({ ...base, segment: seg, thesis_fit: proseOf(list) });
        }
        return out;
      };
      // Rebuild the basket NAME-BY-NAME. `handled` marks a name whose fresh rows were already emitted —
      // its remaining stale sibling rows (the pre-re-draft placements) are superseded, not kept.
      const handled = new Set<string>();
      const basket: BasketMember[] = [];
      for (const m of d.basket) {
        const key = memberKey(m);
        // ESTABLISHED (in the saved basket at mount) → untouched FIRST, regardless of authorship: the
        // frozen Basket is never re-rolled and never parked — a re-draft only surfaces NEW names.
        if (establishedKeys.has(key)) {
          basket.push(m);
          continue;
        }
        // operator-EDITED description → pinned (never clobber the operator's words). Legacy operator_set
        // is normalized away at session-restore; anything not system_drafted stays untouched here.
        if (m.authored_by !== "system_drafted") {
          basket.push(m);
          continue;
        }
        if (handled.has(key)) continue; // a stale sibling row of an already re-rolled/parked name
        handled.add(key);
        const fresh = m.security_id ? placed.get(m.security_id) : undefined;
        if (fresh && fresh.length > 0) {
          basket.push(...freshRows(m, fresh)); // re-rolled to the FULL fresh set; signed_off carries
        } else {
          basket.push({ ...m, segment: DISCOVERED }); // parked ONCE (sibling rows collapse) — never dropped
        }
      }
      // append genuinely NEW placed names (not already in the basket) — N rows for N recommended links
      const have = new Set(d.basket.map(memberKey));
      const additions: BasketMember[] = [];
      for (const [sid, list] of placed) {
        if (have.has(sid)) continue;
        const first = chain.placements.find((p) => p.security_id === sid && p.status === "placed");
        if (!first) continue; // unreachable — `placed` is built from exactly these
        additions.push(
          ...freshRows(
            {
              ticker: first.ticker || first.name,
              role: "—",
              security_id: sid,
              segment: null, // freshRows assigns the filed segment(s)
              thesis_fit: null,
              conviction: null, // the drafter never weights
              surfaced_terms: first.matched_terms, // capture at entry — frozen once the promote persists it
              authored_by: "system_drafted",
              signed_off: false, // system-recommended (included once kept); endorsement is the operator's act
            },
            list,
          ),
        );
      }
      // FIRST draft (no chain yet) adopts the drafter's links; an established chain keeps EXACTLY its links.
      const segments = hasChain
        ? [...d.segments]
        : [
            ...d.segments,
            ...chain.segments
              .filter((s) => !haveSeg.has(s.label))
              .map((s) => ({ label: s.label, descriptor: s.descriptor ?? null })),
          ];
      const merged = [...basket, ...additions];
      // ensure the unsorted pen exists if any name landed there (else Save orphans it)
      if (
        merged.some((m) => m.segment === DISCOVERED) &&
        !segments.some((s) => s.label === DISCOVERED)
      ) {
        segments.push({ label: DISCOVERED, descriptor: null });
      }
      return { segments, basket: merged };
    });

  // Sign off ⇄ withdraw (reversibility, principle #1) — a TOGGLE on the confidence ladder's top rung.
  // Sign-off ENDORSES the NAME (the company belongs in this thesis) — it NEVER touches `authored_by`
  // (the description honestly stays a model draft until edited), never gates Save, and never feeds the
  // call (#4). Per-NAME: the `.map` over memberKey co-mutates ALL of a name's multi-membership rows, and
  // the target state is computed once so a (theoretically) mixed name can't end up half-endorsed.
  // PICK: a flip-to-TRUE also SELECTS the name (endorsing it decides it IN ⇒ checked ⇒ collapsed); a
  // withdraw never un-selects (the name stays picked — un-picking is the checkbox's job, #1).
  const toggleSignOff = (key: string) => {
    setDraft((d) => {
      const cur = d.basket.find((m) => memberKey(m) === key);
      if (!cur) return d;
      const next = !cur.signed_off;
      return {
        ...d,
        basket: d.basket.map((m) => (memberKey(m) === key ? { ...m, signed_off: next } : m)),
      };
    });
    if (pick) {
      const cur = draft.basket.find((m) => memberKey(m) === key);
      if (cur && !cur.signed_off) setSelected((prev) => withAdded(prev, [key]));
    }
  };

  // Bulk "✓ sign off all picked" (the cherry-pick slice) — stamp the flag TRUE on a specific set of NAMES
  // (the origin-tracked picked set; the caller decides membership). Same per-name discipline as
  // toggleSignOff: the `.map` over memberKey co-mutates ALL of a name's multi-membership rows, and the
  // target (true) is fixed up front so a sweep can never half-flip a name. Stamps the FLAG only — never
  // authorship, never include-state — and stays reversible per-name via the existing toggle (#1).
  // (In pick the stamp is a flip-to-true, so the swept names are selected like a per-row sign-off.)
  const signOffKeys = (keys: string[]) => {
    setDraft((d) => {
      const set = new Set(keys);
      return {
        ...d,
        basket: d.basket.map((m) => (set.has(memberKey(m)) ? { ...m, signed_off: true } : m)),
      };
    });
    if (pick) setSelected((prev) => withAdded(prev, keys));
  };

  // Edit the per-NAME description (thesis_fit) — the ONE act that makes the words the operator's:
  // system_drafted → operator_edited ("your words"). Co-mutates all of a name's rows (per-name field).
  // Editing does NOT auto-sign-off — endorsing the name and writing its description are separate acts.
  const editProse = (key: string, text: string) =>
    setDraft((d) => ({
      ...d,
      basket: d.basket.map((m) =>
        memberKey(m) === key ? { ...m, thesis_fit: text, authored_by: touched(m) } : m,
      ),
    }));

  return {
    draft,
    dirty,
    addSegment,
    renameSegment,
    moveSegment,
    removeSegment,
    // NB (S1): `placeMember` and `editConviction` are GONE — per-name segment sorting and the
    // conviction weight live on the TRIAGE screen now; this surface renders the draft's placement
    // read-only. The `conviction` FIELD stays on the model/DB and rides Save untouched.
    addMember,
    addMemberRows,
    removeMember,
    loadDraft,
    toggleSignOff,
    signOffKeys,
    editProse,
    // THE BASKET FREEZE: the established (saved-spine) keys, frozen against the drafter
    establishedKeys,
    isEstablished,
    // TRIAGE (the prune): include-state + the included subset Save persists (mode-aware reads)
    excluded,
    isIncluded,
    includedBasket,
    isCollapsed,
    isDurablyExcluded,
    toggleInclude,
    // the bulk prune primitives — RESEARCH-ONLY (they write `excluded`, dormant in pick)
    includeAll,
    excludeAll,
    excludeNotSignedOff,
    excludeKeys,
    includeKeys,
    // THE PLACED MODE (Research ⇄ Pick) + the pick selection (dormant in research)
    placedMode,
    setPlacedMode,
    placedModeResetCount,
    selected,
    // #7: the optional rejection reasons, persisted with the exclusion set on Save
    reasons,
    editReason,
    // exposed for the triage-session snapshot (a reason-edit on an already-excluded name is dirty but touches
    // neither `draft` nor `excluded`, so the flag must ride the blob to survive a restore).
    reasonsDirty,
  };
}
