// The context role and the first-screen editor — the WIPE-TRAP guard. The editor never authors a role (the DD
// rail does, by an immediate promote), but its Save full-replaces the basket from the working draft. A draft
// seeded from an autosaved triage-session blob written BEFORE a role was set carries no role; without the
// overlay in `useChainDraft`, that Save would silently clear the saved one. These pin: (1) an old blob — rows
// without the key AND rows with `context_role: null` — cannot clear a saved role on Save, (2) the saved spine
// wins even when it says "unset" (a role cleared after the blob must not come back), (3) the restore reads
// clean, not dirty, (4) an established member keeps its role through a re-draft (`loadDraft`).
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BasketMember, ChainDraftOut, ThesisDetail } from "../../api/hooks";
import { deserialize, SCHEMA_VERSION } from "../triageSession";
import { useChainDraft, type RestoredChainState } from "../useChainDraft";

// key order mirrors the server's (ticker first … context_role last), as the dirty compare is a JSON compare
const row = (over: Partial<BasketMember> & { ticker: string }): BasketMember => ({
  ticker: over.ticker,
  role: "r",
  security_id: `sid-${over.ticker.toLowerCase()}`,
  detail: null,
  segment: "reactors",
  thesis_fit: "why it sits here",
  conviction: null,
  surfaced_terms: [],
  authored_by: "system_drafted",
  signed_off: true,
  context_role: null,
  ...over,
});

const thesisOf = (basket: BasketMember[]): ThesisDetail =>
  ({
    id: "t1",
    name: "nuclear",
    narrative: "smr",
    ticker: null,
    basket,
    segments: [
      { label: "reactors", descriptor: null },
      { label: "fuel", descriptor: null },
    ],
    term_set: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    exclusions: [],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;

// The SAVED spine: OKLO in two links carrying a role on both rows, LEU in one link with none.
const spine = () =>
  thesisOf([
    row({ ticker: "OKLO", segment: "reactors", context_role: "counter_case" }),
    row({ ticker: "OKLO", segment: "fuel", context_role: "counter_case" }),
    row({ ticker: "LEU", segment: "fuel" }),
  ]);

// An autosaved blob as the editor wrote it BEFORE the role existed, through the real serialize/deserialize seam.
function restoreFrom(basket: unknown[]): RestoredChainState {
  const res = deserialize({
    schema_version: SCHEMA_VERSION,
    state: {
      hook: {
        draft: {
          segments: [
            { label: "reactors", descriptor: null },
            { label: "fuel", descriptor: null },
          ],
          basket,
        },
        excluded: [],
        reasons: {},
        reasonsDirty: false,
      },
      editor: {},
    },
  });
  if (res.status !== "ok") throw new Error("fixture blob must restore");
  return res.hook;
}

// a blob row exactly as an old autosave stored it: no `context_role` key at all
const oldRow = (ticker: string, segment: string) => {
  const { context_role: _omit, ...rest } = row({ ticker, segment });
  void _omit;
  return rest;
};

// unset reads as null whether the row carries `null` or no key at all (a drafted arrival omits the field)
const rolesOf = (basket: BasketMember[], ticker: string) =>
  basket.filter((m) => m.ticker === ticker).map((m) => m.context_role ?? null);

describe("the restore overlay — a saved role survives an older autosaved blob", () => {
  it("rows WITHOUT the key: the working draft (and so Save's payload) carries the saved role", () => {
    const restored = restoreFrom([
      oldRow("OKLO", "reactors"),
      oldRow("OKLO", "fuel"),
      oldRow("LEU", "fuel"),
    ]);
    const { result } = renderHook(() => useChainDraft(spine(), restored));
    expect(rolesOf(result.current.draft.basket, "OKLO")).toEqual(["counter_case", "counter_case"]);
    // what Save sends is `includedBasket` — the same rows
    expect(rolesOf(result.current.includedBasket, "OKLO")).toEqual(["counter_case", "counter_case"]);
    expect(rolesOf(result.current.includedBasket, "LEU")).toEqual([null]); // unset stays unset
  });

  it("rows with `context_role: null`: still cannot clear the saved role on Save", () => {
    const restored = restoreFrom([
      row({ ticker: "OKLO", segment: "reactors", context_role: null }),
      row({ ticker: "OKLO", segment: "fuel", context_role: null }),
      row({ ticker: "LEU", segment: "fuel", context_role: null }),
    ]);
    const { result } = renderHook(() => useChainDraft(spine(), restored));
    expect(rolesOf(result.current.includedBasket, "OKLO")).toEqual(["counter_case", "counter_case"]);
  });

  it("the overlay is per COMPANY: a restored row in a different link set still gets the company's role", () => {
    // the blob dropped OKLO's fuel row and moved the survivor — the role follows the company, not the row
    const restored = restoreFrom([oldRow("OKLO", "fuel"), oldRow("LEU", "fuel")]);
    const { result } = renderHook(() => useChainDraft(spine(), restored));
    expect(rolesOf(result.current.includedBasket, "OKLO")).toEqual(["counter_case"]);
  });

  it("the SAVED spine wins even when it is unset: a role cleared after the blob was written does not come back", () => {
    const cleared = thesisOf([
      row({ ticker: "OKLO", segment: "reactors", context_role: null }),
      row({ ticker: "OKLO", segment: "fuel", context_role: null }),
    ]);
    // a blob autosaved while the role was still set
    const stale = restoreFrom([
      row({ ticker: "OKLO", segment: "reactors", context_role: "counter_case" }),
      row({ ticker: "OKLO", segment: "fuel", context_role: "counter_case" }),
    ]);
    const { result } = renderHook(() => useChainDraft(cleared, stale));
    expect(rolesOf(result.current.includedBasket, "OKLO")).toEqual([null, null]);
  });

  it("a restored name the saved spine does not hold is left exactly as the blob has it", () => {
    const restored = restoreFrom([oldRow("OKLO", "reactors"), oldRow("NEWCO", "fuel")]);
    const { result } = renderHook(() => useChainDraft(spine(), restored));
    const newco = result.current.draft.basket.find((m) => m.ticker === "NEWCO");
    expect(newco).toBeDefined();
    expect("context_role" in (newco as object)).toBe(false); // no key invented
  });

  it("an untouched restore reads CLEAN (the overlay adds no spurious dirty)", () => {
    // the blob IS the saved spine, written before the role key existed
    const restored = restoreFrom([
      oldRow("OKLO", "reactors"),
      oldRow("OKLO", "fuel"),
      oldRow("LEU", "fuel"),
    ]);
    const { result } = renderHook(() => useChainDraft(spine(), restored));
    expect(result.current.dirty).toBe(false);
  });

  it("with no restored session the draft seeds straight from the spine, roles included", () => {
    const { result } = renderHook(() => useChainDraft(spine()));
    expect(rolesOf(result.current.draft.basket, "OKLO")).toEqual(["counter_case", "counter_case"]);
    expect(result.current.dirty).toBe(false);
  });
});

describe("a re-draft (loadDraft) leaves an established member's role alone", () => {
  const redraft = (placements: { sid: string; ticker: string; segment: string }[]): ChainDraftOut =>
    ({
      thesis_id: "t1",
      segments: [
        { label: "reactors", descriptor: null },
        { label: "fuel", descriptor: null },
      ],
      placements: placements.map((p) => ({
        name: p.ticker,
        ticker: p.ticker,
        prose: "fresh prose",
        segment: p.segment,
        status: "placed" as const,
        security_id: p.sid,
        candidates: [],
        matched_terms: [],
        discovery_source: "edgar" as const,
        off_thesis: false,
      })),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any;

  it("an ESTABLISHED member keeps its role on every row, even when the draft places it elsewhere", () => {
    const { result } = renderHook(() => useChainDraft(spine()));
    act(() => {
      result.current.loadDraft(
        redraft([
          { sid: "sid-oklo", ticker: "OKLO", segment: "fuel" },
          { sid: "sid-leu", ticker: "LEU", segment: "reactors" },
        ]),
      );
    });
    expect(rolesOf(result.current.draft.basket, "OKLO")).toEqual(["counter_case", "counter_case"]);
    expect(rolesOf(result.current.draft.basket, "LEU")).toEqual([null]);
  });

  it("a name the re-draft adds arrives with no role (the drafter never authors one)", () => {
    const { result } = renderHook(() => useChainDraft(spine()));
    act(() => {
      result.current.loadDraft(
        redraft([
          { sid: "sid-oklo", ticker: "OKLO", segment: "reactors" },
          { sid: "sid-newco", ticker: "NEWCO", segment: "fuel" },
        ]),
      );
    });
    expect(rolesOf(result.current.draft.basket, "NEWCO")).toEqual([null]);
  });
});
