// Silent links (Context + Bench) — the hook half. Every thesis carries both, ALWAYS the last two links, fixed
// (no rename / reorder / remove / reserved name); a draft NEVER files a name into one (the operator alone
// does); the `hasChain` trap is closed (the always-present pair must not read as "a chain already exists").
// The pure helpers (`withSilentLinks` & co.) are pinned here too — chainOps.test.ts pins the transforms.
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BasketMember, ChainDraftOut, Segment, ThesisDetail } from "../../api/hooks";
import {
  BENCH,
  CONTEXT,
  DISCOVERED,
  SILENT_DESCRIPTORS,
  SILENT_LINKS,
  insertBeforeSilent,
  isSilentLink,
  reservedLinkName,
  useChainDraft,
  withSilentLinks,
} from "../useChainDraft";

const seg = (label: string, descriptor: string | null = null): Segment => ({ label, descriptor });
const labelsOf = (segs: Segment[]) => segs.map((s) => s.label);
const PAIR = [seg(CONTEXT, SILENT_DESCRIPTORS[CONTEXT]), seg(BENCH, SILENT_DESCRIPTORS[BENCH])];

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

const thesis = (basket: BasketMember[], segments: Segment[] = []): ThesisDetail =>
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

// A ChainDraftOut with the given PLACED placements and (by default) the segments they name.
const chain = (
  placements: { sid: string; ticker: string; segment: string; prose?: string }[],
  segments?: Segment[],
): ChainDraftOut =>
  ({
    thesis_id: "t1",
    segments:
      segments ?? [...new Set(placements.map((p) => p.segment))].map((label) => seg(label)),
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

const rowsOf = (basket: BasketMember[], sid: string) => basket.filter((m) => m.security_id === sid);

describe("the pure helpers", () => {
  it("isSilentLink is an EXACT match (and tolerates a member's nullable segment)", () => {
    expect(isSilentLink("Context")).toBe(true);
    expect(isSilentLink("Bench")).toBe(true);
    expect(isSilentLink("context")).toBe(false);
    expect(isSilentLink(DISCOVERED)).toBe(false);
    expect(isSilentLink(null)).toBe(false);
    expect(isSilentLink(undefined)).toBe(false);
  });

  it("reservedLinkName is case-insensitive and trimmed, returning the canonical spelling", () => {
    expect(reservedLinkName("context")).toBe("Context");
    expect(reservedLinkName("  BENCH ")).toBe("Bench");
    expect(reservedLinkName("Contextual")).toBeNull();
    expect(reservedLinkName("")).toBeNull();
    expect(reservedLinkName("Discovered")).toBeNull();
  });

  it("SILENT_LINKS is Context then Bench (the pinned order)", () => {
    expect([...SILENT_LINKS]).toEqual(["Context", "Bench"]);
  });

  describe("withSilentLinks", () => {
    it("appends Context then Bench with the fixed descriptors; the real links keep their order", () => {
      expect(withSilentLinks([seg("b"), seg("a", "the a link")])).toEqual([
        seg("b"),
        seg("a", "the a link"),
        ...PAIR,
      ]);
    });
    it("an empty list becomes exactly the pair", () => {
      expect(withSilentLinks([])).toEqual(PAIR);
    });
    it("drops a silent link in ANY case (and any position) and re-appends the canonical pair last", () => {
      const out = withSilentLinks([seg("bench", "x"), seg("a"), seg("CONTEXT"), seg("b"), seg(CONTEXT, "stale")]);
      expect(out).toEqual([seg("a"), seg("b"), ...PAIR]);
    });
    it("replaces drifted silent descriptors with the fixed ones", () => {
      expect(withSilentLinks([seg("a"), seg(CONTEXT, null), seg(BENCH, "old")])).toEqual([seg("a"), ...PAIR]);
    });
    it("is idempotent — an already-normal list comes back as the SAME array", () => {
      const once = withSilentLinks([seg("a")]);
      expect(withSilentLinks(once)).toBe(once);
      expect(withSilentLinks(withSilentLinks(once))).toEqual(once);
    });
    it("leaves Discovered where it is (it is not pinned)", () => {
      expect(labelsOf(withSilentLinks([seg(DISCOVERED), seg("a")]))).toEqual([DISCOVERED, "a", "Context", "Bench"]);
    });
  });

  describe("insertBeforeSilent", () => {
    it("lands just before Context", () => {
      expect(labelsOf(insertBeforeSilent(withSilentLinks([seg("a")]), seg("z")))).toEqual(["a", "z", "Context", "Bench"]);
    });
    it("appends at the end when neither silent link is present", () => {
      expect(labelsOf(insertBeforeSilent([seg("a")], seg("z")))).toEqual(["a", "z"]);
    });
  });
});

describe("useChainDraft — the snapshot always carries both silent links, last", () => {
  it("a thesis with NO segments still shows Context + Bench (the editor always lists them)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([member({ ticker: "A" })])));
    expect(result.current.draft.segments).toEqual(PAIR);
  });

  it("real links keep their order and the pair follows — a payload that lacked it", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([], [seg("reactors", "the builders"), seg("fuel")])),
    );
    expect(result.current.draft.segments).toEqual([seg("reactors", "the builders"), seg("fuel"), ...PAIR]);
  });

  it("an out-of-order payload (silent links first, a case-variant) is normalized to real… Context, Bench", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([], [seg(BENCH), seg("reactors"), seg(CONTEXT), seg("fuel")])),
    );
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "Bench"]);
  });

  it("a clean load is NOT dirty (the normalized snapshot is the baseline)", () => {
    const { result } = renderHook(() => useChainDraft(thesis([], [seg("reactors")])));
    expect(result.current.dirty).toBe(false);
  });

  it("a RESTORED draft (an old autosave / a Clear's empty chain) is normalized the same way", () => {
    const restored = { draft: { segments: [], basket: [] }, excluded: new Set<string>(), reasons: new Map<string, string>(), reasonsDirty: false };
    const { result } = renderHook(() => useChainDraft(thesis([], [seg("reactors")]), restored));
    expect(result.current.draft.segments).toEqual(PAIR);
  });
});

describe("useChainDraft — the segment mutators protect the pair", () => {
  const mount = () =>
    renderHook(() => useChainDraft(thesis([member({ ticker: "A", segment: "reactors" })], [seg("reactors"), seg("fuel")])));

  it("addSegment lands BEFORE Context / Bench", () => {
    const { result } = mount();
    act(() => result.current.addSegment("supply"));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "supply", "Context", "Bench"]);
  });

  it("addSegment refuses a reserved name in any case (and padded)", () => {
    const { result } = mount();
    for (const name of ["Context", "context", "BENCH", "  bench "]) act(() => result.current.addSegment(name));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "Bench"]);
  });

  it("renameSegment refuses a silent source and a reserved target; a normal rename still cascades", () => {
    const { result } = mount();
    act(() => result.current.renameSegment(CONTEXT, "Giants"));
    act(() => result.current.renameSegment("fuel", "context"));
    act(() => result.current.renameSegment("fuel", " Bench "));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "Bench"]);
    act(() => result.current.renameSegment("reactors", "builders"));
    expect(labelsOf(result.current.draft.segments)).toEqual(["builders", "fuel", "Context", "Bench"]);
    expect(result.current.draft.basket[0].segment).toBe("builders");
  });

  it("moveSegment never moves a silent link and never swaps a link past or with one", () => {
    const { result } = mount();
    const before = result.current.draft.segments;
    act(() => result.current.moveSegment(CONTEXT, -1));
    act(() => result.current.moveSegment(BENCH, 1));
    act(() => result.current.moveSegment(BENCH, -1));
    act(() => result.current.moveSegment("fuel", 1)); // the last real link can't go right
    expect(result.current.draft.segments).toBe(before);
    act(() => result.current.moveSegment("fuel", -1)); // real links still reorder among themselves
    expect(labelsOf(result.current.draft.segments)).toEqual(["fuel", "reactors", "Context", "Bench"]);
  });

  it("removeSegment is a no-op on a silent link — it and its names stay", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([member({ ticker: "C", segment: CONTEXT })], [seg("reactors")])),
    );
    const before = result.current.draft;
    act(() => result.current.removeSegment(CONTEXT));
    act(() => result.current.removeSegment(BENCH));
    expect(result.current.draft).toBe(before);
  });

  it("removeSegment of a real link still un-places a last placement (null, never gone)", () => {
    const { result } = mount();
    act(() => result.current.removeSegment("reactors"));
    expect(result.current.draft.basket[0].segment).toBeNull();
    expect(labelsOf(result.current.draft.segments)).toEqual(["fuel", "Context", "Bench"]);
  });
});

describe("useChainDraft.loadDraft — the drafter never touches the pair", () => {
  it("the hasChain TRAP: a thesis of only Discovered + Context + Bench still adopts the drafter's links on its FIRST draft", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([], [seg(DISCOVERED), seg(CONTEXT), seg(BENCH)])),
    );
    act(() =>
      result.current.loadDraft(
        chain([
          { sid: "sid-smr", ticker: "SMR", segment: "reactors" },
          { sid: "sid-leu", ticker: "LEU", segment: "fuel" },
        ]),
      ),
    );
    // the drafter's links adopted (a first draft) and the pair re-pinned last; Discovered keeps its place
    expect(labelsOf(result.current.draft.segments)).toEqual([DISCOVERED, "reactors", "fuel", "Context", "Bench"]);
    expect(rowsOf(result.current.draft.basket, "sid-smr")[0].segment).toBe("reactors");
    expect(rowsOf(result.current.draft.basket, "sid-leu")[0].segment).toBe("fuel");
  });

  it("a flat thesis (NO segments at all) also takes the first draft, pair last", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.loadDraft(chain([{ sid: "sid-smr", ticker: "SMR", segment: "reactors" }])));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "Context", "Bench"]);
  });

  it("a re-draft over an ESTABLISHED chain keeps a Context name in Context and adds no reserved segment", () => {
    const t = thesis(
      [
        member({ ticker: "OKLO", segment: "reactors" }),
        member({ ticker: "KO", segment: CONTEXT }), // the operator's placement (a saved, established name)
      ],
      [seg("reactors"), seg("fuel"), ...PAIR],
    );
    const { result } = renderHook(() => useChainDraft(t));
    act(() =>
      result.current.loadDraft(
        chain(
          [
            // the model tries to re-place the Context name AND adds new names into a "Context"/"bench" link
            { sid: "sid-ko", ticker: "KO", segment: "reactors" },
            { sid: "sid-new", ticker: "NEW", segment: "Context" },
            { sid: "sid-nw2", ticker: "NW2", segment: "bench" },
          ],
          [seg("reactors"), seg("Context"), seg("bench")],
        ),
      ),
    );
    const { segments, basket } = result.current.draft;
    // KO: exactly one row, still in Context
    expect(rowsOf(basket, "sid-ko").map((m) => m.segment)).toEqual([CONTEXT]);
    // the model-proposed silent placements are rerouted to the Discovered pen (never into Context / Bench)
    expect(rowsOf(basket, "sid-new").map((m) => m.segment)).toEqual([DISCOVERED]);
    expect(rowsOf(basket, "sid-nw2").map((m) => m.segment)).toEqual([DISCOVERED]);
    // no reserved-labeled segment was added; Discovered lands BEFORE the pair
    expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "Bench"]);
    expect(segments.filter((s) => reservedLinkName(s.label)).length).toBe(2);
  });

  it("a FIRST draft never adopts a reserved-labeled drafter segment (any case) and reroutes its names to Discovered", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() =>
      result.current.loadDraft(
        chain(
          [
            { sid: "sid-smr", ticker: "SMR", segment: "reactors" },
            { sid: "sid-ctx", ticker: "CTX", segment: "context" },
          ],
          [seg("reactors"), seg("context"), seg("BENCH")],
        ),
      ),
    );
    const { segments, basket } = result.current.draft;
    expect(labelsOf(segments)).toEqual(["reactors", DISCOVERED, "Context", "Bench"]);
    expect(rowsOf(basket, "sid-ctx").map((m) => m.segment)).toEqual([DISCOVERED]);
    expect(segments.find((s) => s.label === CONTEXT)?.descriptor).toBe(SILENT_DESCRIPTORS[CONTEXT]);
  });

  it("the Discovered push lands BEFORE Context when a re-draft parks a superseded drafted name", () => {
    const t = thesis([member({ ticker: "OKLO", segment: "reactors" })], [seg("reactors"), seg("fuel")]);
    const { result } = renderHook(() => useChainDraft(t));
    // a drafted name added this session (NOT established), then superseded by a re-draft that no longer places it
    act(() => result.current.addMember(member({ ticker: "OLD", segment: "fuel" })));
    act(() => result.current.loadDraft(chain([{ sid: "sid-oklo", ticker: "OKLO", segment: "reactors" }])));
    const { segments, basket } = result.current.draft;
    expect(rowsOf(basket, "sid-old").map((m) => m.segment)).toEqual([DISCOVERED]);
    expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "Bench"]);
  });

  it("an UNESTABLISHED name already in Context (placed this session) is never re-rolled or parked", () => {
    const t = thesis([member({ ticker: "OKLO", segment: "reactors" })], [seg("reactors")]);
    const { result } = renderHook(() => useChainDraft(t));
    act(() => result.current.addMember(member({ ticker: "KO", segment: CONTEXT })));
    act(() => result.current.loadDraft(chain([{ sid: "sid-ko", ticker: "KO", segment: "reactors" }])));
    expect(rowsOf(result.current.draft.basket, "sid-ko").map((m) => m.segment)).toEqual([CONTEXT]);
  });
});
