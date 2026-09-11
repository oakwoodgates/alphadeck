// THE PLACED MODE (Research ⇄ Pick) — the hook's mode-aware reads/writes, the two working-scoped resets,
// the arrival rules, pick-mode dirty, and the restore seam — run directly against useChainDraft (no UI).
// Research must stay byte-identical to today; pick must NEVER touch `excluded` (un-picked = available).
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BasketMember, ChainDraftOut, ThesisDetail } from "../../api/hooks";
import { useChainDraft } from "../useChainDraft";

const member = (over: Partial<BasketMember> & { ticker: string }): BasketMember => ({
  role: "r",
  security_id: `sid-${over.ticker.toLowerCase()}`,
  segment: null,
  thesis_fit: null,
  conviction: null,
  authored_by: "system_drafted",
  signed_off: false,
  ...over,
});

type Exclusion = { security_id: string; ticker: string | null; reason: string | null };
const no = (sid: string): Exclusion => ({ security_id: sid, ticker: null, reason: null });

const thesis = (
  basket: BasketMember[],
  exclusions: Exclusion[] = [],
  segments: { label: string; descriptor: string | null }[] = [],
): ThesisDetail =>
  ({
    id: "t1",
    name: "n",
    narrative: "x",
    ticker: null,
    basket,
    segments,
    term_set: [],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
    exclusions,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;

// A ChainDraftOut with the given PLACED placements (the bulk arrival).
const chain = (
  placements: { sid: string; ticker: string; segment: string; prose?: string }[],
): ChainDraftOut =>
  ({
    thesis_id: "t1",
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
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;

const pl = (sid: string, ticker: string) => ({ sid, ticker, segment: "reactors" });
const signedOf = (basket: BasketMember[], sid: string) =>
  basket.filter((m) => m.security_id === sid).map((m) => m.signed_off);

describe("useChainDraft — research (the default) is byte-identical", () => {
  it("mounts in research; toggleInclude drives `excluded` and leaves `selected` dormant", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([member({ ticker: "A" }), member({ ticker: "B" })])),
    );
    expect(result.current.placedMode).toBe("research");
    expect(result.current.isIncluded("sid-a")).toBe(true); // default-on (#9)
    expect(result.current.isCollapsed("sid-a")).toBe(false); // open

    act(() => result.current.toggleInclude("sid-a"));
    expect(result.current.excluded.has("sid-a")).toBe(true); // the durable-NO set, as today
    expect(result.current.selected.size).toBe(0); // the pick set never moves in research
    expect(result.current.isIncluded("sid-a")).toBe(false);
    expect(result.current.isCollapsed("sid-a")).toBe(true); // research collapses the decided-OUT row
    expect(result.current.includedBasket.map((m) => m.security_id)).toEqual(["sid-b"]);

    act(() => result.current.toggleInclude("sid-a")); // the visible inverse (#1)
    expect(result.current.excluded.has("sid-a")).toBe(false);
    expect(result.current.isIncluded("sid-a")).toBe(true);
  });

  it("isDurablyExcluded reads the PERSISTED set in both modes (display-only)", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([member({ ticker: "A" })], [no("sid-c")])),
    );
    expect(result.current.isDurablyExcluded("sid-c")).toBe(true);
    expect(result.current.isDurablyExcluded("sid-a")).toBe(false);
    act(() => result.current.setPlacedMode("pick"));
    expect(result.current.isDurablyExcluded("sid-c")).toBe(true);
    act(() => result.current.toggleInclude("sid-c")); // picking it changes NOTHING about the persisted read
    expect(result.current.isDurablyExcluded("sid-c")).toBe(true);
  });
});

describe("useChainDraft — pick (the additive select)", () => {
  it("toggleInclude drives `selected`, never `excluded`; the polarity flips (checked ⇒ collapsed)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.loadDraft(chain([pl("sid-a", "A"), pl("sid-b", "B")])));
    act(() => result.current.setPlacedMode("pick"));

    expect(result.current.placedMode).toBe("pick");
    // a bulk arrival is UNDECIDED: un-picked + open (available), never excluded
    expect(result.current.isIncluded("sid-a")).toBe(false);
    expect(result.current.isCollapsed("sid-a")).toBe(false);
    expect(result.current.includedBasket).toEqual([]);
    expect(result.current.excluded.size).toBe(0);

    act(() => result.current.toggleInclude("sid-a")); // PICK it
    expect(result.current.selected.has("sid-a")).toBe(true);
    expect(result.current.isIncluded("sid-a")).toBe(true);
    expect(result.current.isCollapsed("sid-a")).toBe(true); // pick collapses the decided-IN row
    expect(result.current.includedBasket.map((m) => m.security_id)).toEqual(["sid-a"]);
    expect(result.current.excluded.size).toBe(0); // NEVER written by pick

    act(() => result.current.toggleInclude("sid-a")); // un-pick → available again, still no NO
    expect(result.current.selected.has("sid-a")).toBe(false);
    expect(result.current.isIncluded("sid-a")).toBe(false);
    expect(result.current.excluded.size).toBe(0);
  });

  it("the bulk prune primitives stay research-only writers: in pick they move `excluded` (dormant), not the selection", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.loadDraft(chain([pl("sid-a", "A")])));
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.toggleInclude("sid-a"));
    act(() => result.current.excludeAll());
    expect(result.current.excluded.has("sid-a")).toBe(true); // written…
    expect(result.current.isIncluded("sid-a")).toBe(true); // …but dormant: the pick still reads included
  });
});

describe("useChainDraft — the toggle is a WORKING-SCOPED RESET (restore ≠ reset; sign-off untouched)", () => {
  // established A + B (the saved spine), a persisted NO on C (which the draft re-surfaces), new D + E
  const setup = () => {
    const hook = renderHook(() =>
      useChainDraft(thesis([member({ ticker: "A" }), member({ ticker: "B" })], [no("sid-c")])),
    );
    act(() =>
      hook.result.current.loadDraft(chain([pl("sid-c", "C"), pl("sid-d", "D"), pl("sid-e", "E")])),
    );
    act(() => hook.result.current.toggleSignOff("sid-e")); // endorse E (research: stays dormant for pick)
    act(() => hook.result.current.toggleInclude("sid-b")); // demote the established B ("sent down")
    return hook;
  };

  it("entering pick: kept established + signed-off checked; demoted-established, persisted-NO and undecided names un-picked; `excluded` + `signed_off` untouched", () => {
    const { result } = setup();
    expect([...result.current.excluded].sort()).toEqual(["sid-b", "sid-c"]); // the research state
    // only D would flip (A kept, B demoted→un-picked, C excluded→un-picked, E signed→checked: unchanged)
    expect(result.current.placedModeResetCount("pick")).toBe(1);

    act(() => result.current.setPlacedMode("pick"));
    expect([...result.current.selected].sort()).toEqual(["sid-a", "sid-e"]);
    expect(result.current.isIncluded("sid-a")).toBe(true); // the saved Basket's kept name — never emptied
    expect(result.current.isIncluded("sid-b")).toBe(false); // the demoted established name stays down
    expect(result.current.isIncluded("sid-c")).toBe(false); // the persisted NO → available, not pre-grayed
    expect(result.current.isIncluded("sid-d")).toBe(false); // undecided
    expect(result.current.isIncluded("sid-e")).toBe(true); // signed off ⇒ checked
    // dormant, untouched — it comes back on research entry
    expect([...result.current.excluded].sort()).toEqual(["sid-b", "sid-c"]);
    // sign-off rides `draft`, which the reset never mutates
    expect(signedOf(result.current.draft.basket, "sid-e")).toEqual([true]);
    expect(result.current.draft.basket.filter((m) => m.security_id !== "sid-e").every((m) => !m.signed_off)).toBe(true);
  });

  it("entering research: excluded ← persisted NOs − signed-off (a fresh mount, NOT an all-check); a picked persisted-NO returns grayed; `selected` untouched", () => {
    const { result } = setup();
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.toggleInclude("sid-c")); // pick the persisted-NO name
    act(() => result.current.toggleInclude("sid-d")); // and an undecided one
    act(() => result.current.toggleInclude("sid-a")); // un-pick an established name (RULING 1 — allowed)
    expect([...result.current.selected].sort()).toEqual(["sid-c", "sid-d", "sid-e"]);
    // A (un-picked → included), B (the in-session demote → included again) and C (picked → grayed)
    // would flip; D (picked → included) and E (signed → included) read the same either way
    expect(result.current.placedModeResetCount("research")).toBe(3);

    act(() => result.current.setPlacedMode("research"));
    expect([...result.current.excluded]).toEqual(["sid-c"]); // the persisted NO restored — never withdrawn silently
    expect(result.current.isIncluded("sid-a")).toBe(true);
    expect(result.current.isIncluded("sid-b")).toBe(true); // the in-session demote does NOT survive the reset
    expect(result.current.isIncluded("sid-c")).toBe(false); // pre-grayed, one click back (#9)
    expect(result.current.isIncluded("sid-d")).toBe(true);
    expect(result.current.isIncluded("sid-e")).toBe(true);
    expect([...result.current.selected].sort()).toEqual(["sid-c", "sid-d", "sid-e"]); // dormant, untouched
    expect(signedOf(result.current.draft.basket, "sid-e")).toEqual([true]);
  });

  it("a SIGNED-OFF persisted-NO name is checked on research entry regardless (endorsed ⇒ in)", () => {
    const { result } = setup();
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.toggleSignOff("sid-c")); // endorse the persisted-NO name (in pick ⇒ also picked)
    expect(result.current.isIncluded("sid-c")).toBe(true);
    act(() => result.current.setPlacedMode("research"));
    expect(result.current.excluded.has("sid-c")).toBe(false); // baseExcluded − signedOff
    expect(result.current.isIncluded("sid-c")).toBe(true);
  });

  it("the active mode is a no-op (no reset, same Set instances); its reset count is 0", () => {
    const { result } = setup();
    const excludedBefore = result.current.excluded;
    const selectedBefore = result.current.selected;
    expect(result.current.placedModeResetCount("research")).toBe(0);
    act(() => result.current.setPlacedMode("research"));
    expect(result.current.placedMode).toBe("research");
    expect(result.current.excluded).toBe(excludedBefore);
    expect(result.current.selected).toBe(selectedBefore);
  });
});

describe("useChainDraft — sign-off ⇒ selected (pick only)", () => {
  it("in pick a flip-to-true selects the name; a withdraw never un-selects; in research the selection stays dormant", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.loadDraft(chain([pl("sid-a", "A"), pl("sid-b", "B")])));
    act(() => result.current.toggleSignOff("sid-a")); // research: the flag flips, the pick set stays dormant
    expect(signedOf(result.current.draft.basket, "sid-a")).toEqual([true]);
    expect(result.current.selected.size).toBe(0);

    act(() => result.current.setPlacedMode("pick")); // the reset checks the signed-off A
    expect([...result.current.selected]).toEqual(["sid-a"]);
    act(() => result.current.toggleSignOff("sid-b")); // pick: endorsing B decides it IN
    expect(signedOf(result.current.draft.basket, "sid-b")).toEqual([true]);
    expect(result.current.isIncluded("sid-b")).toBe(true);
    expect(result.current.isCollapsed("sid-b")).toBe(true);
    act(() => result.current.toggleSignOff("sid-b")); // withdraw → the name stays picked (#1: un-picking is the checkbox's job)
    expect(signedOf(result.current.draft.basket, "sid-b")).toEqual([false]);
    expect(result.current.isIncluded("sid-b")).toBe(true);
    // RULING 4: un-picking a signed-off name is allowed per-row — the flag stays, the name becomes available
    act(() => result.current.toggleInclude("sid-a"));
    expect(result.current.isIncluded("sid-a")).toBe(false);
    expect(signedOf(result.current.draft.basket, "sid-a")).toEqual([true]);
  });

  it("signOffKeys selects every swept name in pick (the bulk stamp is a flip-to-true)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() =>
      result.current.loadDraft(chain([pl("sid-a", "A"), pl("sid-b", "B"), pl("sid-c", "C")])),
    );
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.signOffKeys(["sid-a", "sid-c"]));
    expect([...result.current.selected].sort()).toEqual(["sid-a", "sid-c"]);
    expect(result.current.isIncluded("sid-b")).toBe(false);
    expect(result.current.draft.basket.map((m) => m.signed_off)).toEqual([true, false, true]);
  });
});

describe("useChainDraft — arrivals", () => {
  it("explicit adds SELECT (addMember / addMemberRows — a decision); loadDraft additions do NOT (undecided)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.addMember(member({ ticker: "H", signed_off: true }))); // the hand-add
    expect(result.current.selected.has("sid-h")).toBe(true);
    expect(result.current.isCollapsed("sid-h")).toBe(true);
    act(() =>
      result.current.addMemberRows([
        member({ ticker: "P", segment: "reactors" }),
        member({ ticker: "P", segment: "fuel" }),
      ]),
    ); // the pile pick (N rows, one name)
    expect(result.current.selected.has("sid-p")).toBe(true);
    expect(result.current.includedBasket.filter((m) => m.security_id === "sid-p")).toHaveLength(2);

    act(() => result.current.loadDraft(chain([pl("sid-a", "A"), pl("sid-b", "B")]))); // the bulk arrival
    expect(result.current.selected.has("sid-a")).toBe(false);
    expect(result.current.selected.has("sid-b")).toBe(false);
    expect(result.current.isIncluded("sid-a")).toBe(false); // available, open
    expect(result.current.isCollapsed("sid-a")).toBe(false);
    // the earlier decisions survive the load (loadDraft never touches the selection)
    expect([...result.current.selected].sort()).toEqual(["sid-h", "sid-p"]);
  });

  it("an explicit add in RESEARCH selects too (harmless — the set is dormant, and the pick reset recomputes it)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.addMember(member({ ticker: "H" })));
    expect(result.current.selected.has("sid-h")).toBe(true);
    expect(result.current.isIncluded("sid-h")).toBe(true); // research reads `excluded`, not the selection
    act(() => result.current.toggleInclude("sid-h"));
    expect(result.current.isIncluded("sid-h")).toBe(false); // …proving the read is research's
    expect(result.current.selected.has("sid-h")).toBe(true);
  });

  it("removeMember un-selects (no stale pick marker lingers)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.setPlacedMode("pick"));
    act(() => result.current.addMember(member({ ticker: "H" })));
    act(() => result.current.removeMember("sid-h"));
    expect(result.current.draft.basket).toEqual([]);
    expect(result.current.selected.size).toBe(0);
  });
});

describe("useChainDraft — pick-mode dirty", () => {
  it("clean when the selected rows == the saved basket; the available pool is not a change; a pick / un-pick / segment edit dirties", () => {
    const t = thesis([member({ ticker: "A", segment: "reactors" }), member({ ticker: "B", segment: "reactors" })], [], [
      { label: "reactors", descriptor: null },
    ]);
    const { result } = renderHook(() => useChainDraft(t));
    expect(result.current.dirty).toBe(false);
    act(() => result.current.setPlacedMode("pick")); // selected = the established, kept names
    expect(result.current.dirty).toBe(false);

    act(() => result.current.loadDraft(chain([pl("sid-c", "C")]))); // C lands in the existing link
    expect(result.current.dirty).toBe(false); // research would read dirty here; in pick an un-picked arrival changes nothing Save writes
    act(() => result.current.toggleInclude("sid-c"));
    expect(result.current.dirty).toBe(true);
    act(() => result.current.toggleInclude("sid-c"));
    expect(result.current.dirty).toBe(false);
    act(() => result.current.toggleInclude("sid-a")); // un-picking an established name WOULD drop it on Save
    expect(result.current.dirty).toBe(true);
    act(() => result.current.toggleInclude("sid-a"));
    expect(result.current.dirty).toBe(false);
    act(() => result.current.addSegment("fuel"));
    expect(result.current.dirty).toBe(true);
  });
});

describe("useChainDraft — the restore seam seeds, never resets", () => {
  const A = member({ ticker: "A" });
  const B = member({ ticker: "B", signed_off: true });
  const C = member({ ticker: "C" });

  it("a restored blob seeds placedMode + selected exactly — a signed-off-but-unselected name stays un-picked", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([A]), {
        draft: { segments: [], basket: [A, B, C] },
        excluded: new Set(),
        reasons: new Map(),
        reasonsDirty: false,
        selected: new Set(["sid-a", "sid-c"]),
        placedMode: "pick",
      }),
    );
    expect(result.current.placedMode).toBe("pick");
    expect([...result.current.establishedKeys]).toEqual(["sid-a"]);
    expect(result.current.isIncluded("sid-a")).toBe(true);
    expect(result.current.isIncluded("sid-b")).toBe(false); // signed off yet un-selected in the blob: a restore is NOT a reset
    expect(result.current.isIncluded("sid-c")).toBe(true);
    expect(result.current.dirty).toBe(true); // the selected rows (A, C) differ from the spine (A)
  });

  it("an old-shape restore (no placedMode / selected) mounts research with an empty selection — byte-identical to today", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([A]), {
        draft: { segments: [], basket: [A, C] },
        excluded: new Set(["sid-c"]),
        reasons: new Map(),
        reasonsDirty: false,
      }),
    );
    expect(result.current.placedMode).toBe("research");
    expect(result.current.selected.size).toBe(0);
    expect(result.current.isIncluded("sid-c")).toBe(false); // the restored prune, as ever
  });
});
