import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ThesisDetail } from "../../api/hooks";
import { deserialize, SCHEMA_VERSION, serialize, type EditorRuntime } from "../triageSession";
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

// A restored prune that DIVERGES from the spine: a second name added + one excluded with a reason.
function restoredHook() {
  const t = thesis();
  const emptyEditor: EditorRuntime = {
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
  };
  const hook = {
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
  };
  // round-trip through the wire so the test exercises the real restore path, not a hand-built object
  const state = JSON.parse(JSON.stringify(serialize(hook, emptyEditor)));
  const result = deserialize({ schema_version: SCHEMA_VERSION, state });
  if (result.status !== "ok") throw new Error("expected ok");
  return result.hook;
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
});
