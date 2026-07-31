import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChainDraftOut, ThesisDetail } from "../../api/hooks";
import {
  clearedRestore,
  deserialize,
  SCHEMA_VERSION,
  serialize,
  type EditorRuntime,
} from "../triageSession";
import { useChainDraft } from "../useChainDraft";

// A minimal persisted thesis: ONE member (OKLO), no exclusions — the "last saved spine".
function thesis(): ThesisDetail {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "nuclear",
    narrative: "smr",
    basket: [
      {
        ticker: "OKLO",
        role: "leader",
        security_id: "sid-oklo",
        segment: "Enrichment",
        authored_by: "operator_set",
      },
    ],
    segments: [{ label: "Enrichment", descriptor: null }],
    term_set: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    exclusions: [],
  };
}

const emptyEditor = (): EditorRuntime => ({
  ambiguous: [],
  verify: [],
  absent: [],
  verifyOrigin: {},
  matched: {},
  offUniverse: new Set(),
  offThesisSet: new Set(),
  identity: {},
  names: {},
  draftStatus: null,
  cappedTerms: new Set(),
  draftEmpty: false,
  termSet: [],
  recs: {},
  adopted: new Set(),
  setAside: new Set(),
});

// Round-trip a hook state through the wire so a test exercises the real restore path, not a hand-built object.
function roundTrip(hook: Parameters<typeof serialize>[0]) {
  const state = JSON.parse(JSON.stringify(serialize(hook, emptyEditor())));
  const result = deserialize({ schema_version: SCHEMA_VERSION, state });
  if (result.status !== "ok") throw new Error("expected ok");
  return result.hook;
}

// A restored prune that DIVERGES from the spine: a second name added + one excluded with a reason.
function restoredHook() {
  const t = thesis();
  return roundTrip({
    draft: {
      segments: t.segments,
      basket: [
        ...t.basket,
        {
          ticker: "SMR",
          role: "high_beta",
          security_id: "sid-smr",
          segment: "Enrichment",
          authored_by: "system_drafted" as const,
        },
      ],
    },
    excluded: new Set(["sid-smr"]),
    reasons: new Map([["sid-smr", "too speculative"]]),
    reasonsDirty: true,
  });
}

// The Basket-freeze worst case, restored from a blob: the SPINE name (OKLO) rides the blob DEMOTED
// (excluded) AND un-accepted (system_drafted) — nothing but `established` protects it from a re-roll.
function restoredDemotedSpine() {
  const t = thesis();
  return roundTrip({
    draft: {
      segments: t.segments,
      basket: [
        { ...t.basket[0], authored_by: "system_drafted" as const }, // un-accepted in-session
        {
          ticker: "SMR",
          role: "high_beta",
          security_id: "sid-smr",
          segment: "Enrichment",
          authored_by: "system_drafted" as const,
        },
      ],
    },
    excluded: new Set(["sid-oklo"]), // the spine name, demoted
    reasons: new Map<string, string>(),
    reasonsDirty: false,
  });
}

// A minimal ChainDraftOut placing the given names (status "placed", resolved by security_id).
function chain(
  placements: { sid: string; ticker: string; segment: string; prose?: string }[],
): ChainDraftOut {
  return {
    thesis_id: "11111111-1111-1111-1111-111111111111",
    segments: [...new Set(placements.map((p) => p.segment))].map((label) => ({
      label,
      descriptor: null,
    })),
    placements: placements.map((p) => ({
      name: p.ticker,
      ticker: p.ticker,
      prose: p.prose ?? "",
      segment: p.segment,
      status: "placed" as const,
      security_id: p.sid,
      candidates: [],
      matched_terms: [],
      discovery_source: "edgar" as const,
      off_thesis: false,
    })),
  };
}

describe("useChainDraft hydrate seam", () => {
  it("seeds working state from the blob while base seeds from the thesis (so a restored prune reads dirty)", () => {
    const t = thesis();
    const { result } = renderHook(() => useChainDraft(t, restoredHook()));

    // (a) draft / excluded / reasons come from the BLOB
    expect(result.current.draft.basket.map((m) => m.security_id)).toEqual(["sid-oklo", "sid-smr"]);
    expect(result.current.excluded.has("sid-smr")).toBe(true);
    expect(result.current.reasons.get("sid-smr")).toBe("too speculative");
    // the excluded name is filtered out of the persist-set (basket − excluded)
    expect(result.current.includedBasket.map((m) => m.security_id)).toEqual(["sid-oklo"]);

    // (b)+(c) base/baseExcluded seed from the THESIS (one member, no exclusions), so the restored-but-unsaved
    // prune correctly reads DIRTY — the subtle blob-vs-thesis seeding interaction the byte round-trip can't cover.
    expect(result.current.dirty).toBe(true);
  });

  it("without a restored session, seeds from the thesis and reads clean", () => {
    const t = thesis();
    const { result } = renderHook(() => useChainDraft(t));

    expect(result.current.draft.basket.map((m) => m.security_id)).toEqual(["sid-oklo"]);
    expect(result.current.excluded.size).toBe(0);
    expect(result.current.dirty).toBe(false); // a clean load of the persisted spine is not dirty
  });

  it("BASKET FREEZE: established = base ∩ restored — a demoted spine name stays demoted, and loadDraft never re-rolls or parks it", () => {
    const t = thesis();
    const { result } = renderHook(() => useChainDraft(t, restoredDemotedSpine()));

    // established is the INTERSECTION: the spine name (in base AND the blob) — never the blob-only SMR
    expect([...result.current.establishedKeys]).toEqual(["sid-oklo"]);
    expect(result.current.isEstablished("sid-oklo")).toBe(true);
    expect(result.current.isEstablished("sid-smr")).toBe(false);
    // the demote rode the blob: the established name reconstructs EXCLUDED (demoted stays demoted)
    expect(result.current.excluded.has("sid-oklo")).toBe(true);

    // a re-draft that re-places BOTH names re-rolls only the un-established drafted one — the spine name
    // is frozen even though it is system_drafted (only `established` protects it here)
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-oklo", ticker: "OKLO", segment: "NewSeg", prose: "re-rolled?" },
          { sid: "sid-smr", ticker: "SMR", segment: "NewSeg", prose: "re-rolled" },
        ]),
      ),
    );
    const byId = (sid: string) =>
      result.current.draft.basket.find((m) => m.security_id === sid)!;
    expect(byId("sid-oklo").segment).toBe("Enrichment"); // FROZEN — never re-rolled
    // the blob-only drafted name IS re-rolled (not frozen) — its prose updates; and because "NewSeg" is a
    // fabricated link on the additive chain, it files into the Discovered pen instead of inventing the link
    expect(byId("sid-smr").thesis_fit).toBe("re-rolled");
    expect(byId("sid-smr").segment).toBe("Discovered");

    // a draft NOT placing the spine name does NOT park it in Discovered (the frozen basket never orphans)
    act(() =>
      result.current.loadDraft(chain([{ sid: "sid-smr", ticker: "SMR", segment: "Seg2" }])),
    );
    expect(byId("sid-oklo").segment).toBe("Enrichment");
  });

  it("BASKET FREEZE: after a Clear (empty restored basket) established is EMPTY — a re-discovered old-spine name re-rolls freely", () => {
    const t = thesis();
    const cleared = clearedRestore([]); // the Clear action's synthetic restore — empty chain, seeds kept
    const { result } = renderHook(() => useChainDraft(t, cleared.hook));

    expect(result.current.establishedKeys.size).toBe(0); // base ∩ ∅ = ∅ — nothing frozen

    // the next draft re-discovers the OLD spine name → it enters as a fresh system_drafted placement…
    act(() =>
      result.current.loadDraft(chain([{ sid: "sid-oklo", ticker: "OKLO", segment: "SegA" }])),
    );
    const oklo = () => result.current.draft.basket.find((m) => m.security_id === "sid-oklo")!;
    expect(oklo().segment).toBe("SegA");
    expect(oklo().authored_by).toBe("system_drafted");

    // …and a SECOND draft re-rolls it (the intersection kept it un-frozen) — its prose updates in place; a
    // frozen name would keep its mount state untouched. It stays in the existing link (SegA), not a new one.
    act(() =>
      result.current.loadDraft(
        chain([{ sid: "sid-oklo", ticker: "OKLO", segment: "SegA", prose: "re-rolled" }]),
      ),
    );
    expect(oklo().thesis_fit).toBe("re-rolled");
    expect(oklo().segment).toBe("SegA");
  });

  it("additive value chain: a re-draft over an existing chain adds NO new links — a new name lands in Discovered", () => {
    const t = thesis(); // basket = OKLO, value chain = ["Enrichment"]
    const { result } = renderHook(() => useChainDraft(t));
    // the drafter proposes a brand-new link with a new name in it
    act(() =>
      result.current.loadDraft(chain([{ sid: "sid-new", ticker: "NEW", segment: "A Fabricated Link" }])),
    );
    const labels = result.current.draft.segments.map((s) => s.label);
    expect(labels).toContain("Enrichment"); // the operator's chain is kept exactly
    expect(labels).not.toContain("A Fabricated Link"); // the drafter's new link is NOT appended (no dup pile-up)
    const nw = result.current.draft.basket.find((m) => m.security_id === "sid-new")!;
    expect(nw.segment).toBe("Discovered"); // the new name lands in the unsorted pen to file into an existing link
  });
});
