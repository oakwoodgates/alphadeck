// Discovery cleanup S1 — the hook's confidence-ladder + multi-membership units, run directly against
// useChainDraft (no UI): sign-off never touches authorship, only a prose edit does; loadDraft preserves
// a name's N recommended links as N rows (the old Map squashed them); removeSegment is multi-safe;
// the clear-not-signed-off sweep keys on the flag.
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

const thesis = (
  basket: BasketMember[],
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
    exclusions: [],
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;

// A ChainDraftOut with the given PLACED placements (sid may repeat — the multi-link recommendation).
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

const rowsOf = (basket: BasketMember[], sid: string) =>
  basket.filter((m) => m.security_id === sid);

describe("useChainDraft — sign-off (the ladder's top rung)", () => {
  it("toggleSignOff flips ONLY signed_off — authorship is untouched, and it round-trips", () => {
    const { result } = renderHook(() => useChainDraft(thesis([member({ ticker: "SMR" })])));
    act(() => result.current.toggleSignOff("sid-smr"));
    let m = result.current.draft.basket[0];
    expect(m.signed_off).toBe(true);
    expect(m.authored_by).toBe("system_drafted"); // endorsing the NAME never claims the words

    act(() => result.current.toggleSignOff("sid-smr")); // the visible inverse (#1)
    m = result.current.draft.basket[0];
    expect(m.signed_off).toBe(false);
    expect(m.authored_by).toBe("system_drafted");
  });

  it("toggleSignOff co-mutates ALL of a name's multi-membership rows (per-NAME semantics)", () => {
    const t = thesis(
      [
        member({ ticker: "SMR", segment: "reactors" }),
        member({ ticker: "SMR", segment: "fuel" }),
      ],
      [
        { label: "reactors", descriptor: null },
        { label: "fuel", descriptor: null },
      ],
    );
    const { result } = renderHook(() => useChainDraft(t));
    act(() => result.current.toggleSignOff("sid-smr"));
    expect(result.current.draft.basket.map((m) => m.signed_off)).toEqual([true, true]);
  });

  it("editProse is the ONE authorship flip (→ operator_edited) and does NOT auto-sign-off", () => {
    const { result } = renderHook(() => useChainDraft(thesis([member({ ticker: "SMR" })])));
    act(() => result.current.editProse("sid-smr", "my own words"));
    const m = result.current.draft.basket[0];
    expect(m.authored_by).toBe("operator_edited"); // the operator changed the text → theirs
    expect(m.thesis_fit).toBe("my own words");
    expect(m.signed_off).toBe(false); // writing a note ≠ endorsing the name — separate acts
  });

  it("excludeNotSignedOff sweeps only un-endorsed WORKING names — signed-off and established kept", () => {
    // OKLO is ESTABLISHED (in the thesis at mount) and un-endorsed — never swept (working-scoped).
    const t = thesis([member({ ticker: "OKLO", signed_off: false })]);
    const { result } = renderHook(() => useChainDraft(t));
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-a", ticker: "AAA", segment: "reactors" },
          { sid: "sid-b", ticker: "BBB", segment: "reactors" },
        ]),
      ),
    );
    act(() => result.current.toggleSignOff("sid-a")); // endorse one of the two new names
    act(() => result.current.excludeNotSignedOff());
    expect(result.current.excluded.has("sid-b")).toBe(true); // un-endorsed new name → excluded
    expect(result.current.excluded.has("sid-a")).toBe(false); // endorsed → kept
    expect(result.current.excluded.has("sid-oklo")).toBe(false); // established → never swept
    // and the sweep touched NOTHING else: no authorship flip, no flag flip
    const bbb = result.current.draft.basket.find((m) => m.security_id === "sid-b")!;
    expect(bbb.authored_by).toBe("system_drafted");
    expect(bbb.signed_off).toBe(false);
  });
});

describe("useChainDraft — loadDraft preserves N rows per name (the multi-membership fix)", () => {
  it("a name recommended into TWO links loads as TWO rows (same security_id, one per link)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-smr", ticker: "SMR", segment: "reactors", prose: "the designer" },
          { sid: "sid-smr", ticker: "SMR", segment: "fuel", prose: "" },
        ]),
      ),
    );
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows.map((r) => r.segment)).toEqual(["reactors", "fuel"]); // N rows — never last-wins
    // ONE description per NAME: every row carries the first non-empty drafted prose
    expect(rows.map((r) => r.thesis_fit)).toEqual(["the designer", "the designer"]);
    expect(rows.every((r) => r.authored_by === "system_drafted")).toBe(true);
    expect(rows.every((r) => !r.signed_off)).toBe(true);
  });

  it("a re-draft RE-ROLLS a drafted name to its fresh link set — and the sign-off flag CARRIES", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    // the FIRST draft adopts BOTH links (the additive-chain rule: a later draft never invents a link,
    // so the second recommendation must land in links the chain already has)
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-smr", ticker: "SMR", segment: "reactors" },
          { sid: "sid-other", ticker: "OTH", segment: "fuel" },
        ]),
      ),
    );
    act(() => result.current.toggleSignOff("sid-smr")); // endorse it, then re-draft
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-smr", ticker: "SMR", segment: "reactors", prose: "fresh" },
          { sid: "sid-smr", ticker: "SMR", segment: "fuel", prose: "" },
        ]),
      ),
    );
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows.map((r) => r.segment)).toEqual(["reactors", "fuel"]); // expanded to the fresh N
    expect(rows.every((r) => r.signed_off)).toBe(true); // decision 4: the flag rides the re-roll
    expect(rows.every((r) => r.thesis_fit === "fresh")).toBe(true);
  });

  it("a signed-off name the new draft no longer places PARKS in Discovered (one row) — never dropped (#9)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-smr", ticker: "SMR", segment: "reactors" },
          { sid: "sid-smr", ticker: "SMR", segment: "fuel" },
        ]),
      ),
    );
    act(() => result.current.toggleSignOff("sid-smr"));
    act(() =>
      result.current.loadDraft(chain([{ sid: "sid-new", ticker: "NEW", segment: "reactors" }])),
    );
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows).toHaveLength(1); // the stale sibling rows COLLAPSE to one parked row
    expect(rows[0].segment).toBe("Discovered"); // parked, visible — never silently dropped
    expect(rows[0].signed_off).toBe(true); // the endorsement survives the park
  });

  it("an operator-EDITED description PINS the name against the re-roll (all its rows)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() =>
      result.current.loadDraft(chain([{ sid: "sid-smr", ticker: "SMR", segment: "reactors", prose: "draft" }])),
    );
    act(() => result.current.editProse("sid-smr", "my words")); // → operator_edited
    act(() =>
      result.current.loadDraft(
        chain([{ sid: "sid-smr", ticker: "SMR", segment: "elsewhere", prose: "clobber?" }]),
      ),
    );
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows).toHaveLength(1);
    expect(rows[0].segment).toBe("reactors"); // pinned — the edited name never re-rolls
    expect(rows[0].thesis_fit).toBe("my words");
  });
});

describe("useChainDraft — removeSegment is multi-membership-safe", () => {
  const twoLinks = [
    { label: "reactors", descriptor: null },
    { label: "fuel", descriptor: null },
  ];

  it("drops the REDUNDANT row when the name keeps another placement", () => {
    const t = thesis(
      [
        member({ ticker: "SMR", segment: "reactors" }),
        member({ ticker: "SMR", segment: "fuel" }),
      ],
      twoLinks,
    );
    const { result } = renderHook(() => useChainDraft(t));
    act(() => result.current.removeSegment("reactors"));
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows).toHaveLength(1); // the redundant membership dropped — no stray unplaced twin
    expect(rows[0].segment).toBe("fuel"); // the surviving membership is untouched
  });

  it("NULLS the segment on the name's LAST placement — the name itself never leaves (#9)", () => {
    const t = thesis([member({ ticker: "SMR", segment: "reactors" })], twoLinks);
    const { result } = renderHook(() => useChainDraft(t));
    act(() => result.current.removeSegment("reactors"));
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows).toHaveLength(1);
    expect(rows[0].segment).toBeNull(); // un-placed, still in the basket
  });

  it("a name with SEVERAL rows all in the removed link collapses to ONE unplaced row (never zero)", () => {
    // dedup should prevent this shape arising, but the guard must not lose the name if it ever does
    const t = thesis(
      [
        member({ ticker: "SMR", segment: "reactors" }),
        member({ ticker: "SMR", segment: "reactors" }),
      ],
      twoLinks,
    );
    const { result } = renderHook(() => useChainDraft(t));
    act(() => result.current.removeSegment("reactors"));
    const rows = rowsOf(result.current.draft.basket, "sid-smr");
    expect(rows).toHaveLength(1); // collapsed, not dropped
    expect(rows[0].segment).toBeNull();
  });
});
