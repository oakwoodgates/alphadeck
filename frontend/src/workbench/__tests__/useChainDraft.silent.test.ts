// Silent links (Context + ETFs + Bench) — the hook half. Every thesis carries all three, ALWAYS last, fixed
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
  ETFS,
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
// the three fixed silent links, in order (the name predates ETFs: it was the Context + Bench pair)
const PAIR = [
  seg(CONTEXT, SILENT_DESCRIPTORS[CONTEXT]),
  seg(ETFS, SILENT_DESCRIPTORS[ETFS]),
  seg(BENCH, SILENT_DESCRIPTORS[BENCH]),
];

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
    expect(isSilentLink("ETFs")).toBe(true);
    expect(isSilentLink("Bench")).toBe(true);
    expect(isSilentLink("context")).toBe(false);
    expect(isSilentLink("etfs")).toBe(false);
    expect(isSilentLink(DISCOVERED)).toBe(false);
    expect(isSilentLink(null)).toBe(false);
    expect(isSilentLink(undefined)).toBe(false);
  });

  it("reservedLinkName is case-insensitive and trimmed, returning the canonical spelling", () => {
    expect(reservedLinkName("context")).toBe("Context");
    expect(reservedLinkName("  BENCH ")).toBe("Bench");
    expect(reservedLinkName(" etfs ")).toBe("ETFs");
    expect(reservedLinkName("ETFS")).toBe("ETFs");
    expect(reservedLinkName("Contextual")).toBeNull();
    expect(reservedLinkName("ETF")).toBeNull();
    expect(reservedLinkName("")).toBeNull();
    expect(reservedLinkName("Discovered")).toBeNull();
  });

  it("SILENT_LINKS is Context, ETFs then Bench (the pinned order — Bench last)", () => {
    expect([...SILENT_LINKS]).toEqual(["Context", "ETFs", "Bench"]);
    expect(SILENT_DESCRIPTORS[ETFS]).toBe(
      "Funds that track the theme: watched for price and fund flow. Never counts toward the call.",
    );
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
      expect(labelsOf(withSilentLinks([seg(DISCOVERED), seg("a")]))).toEqual([DISCOVERED, "a", "Context", "ETFs", "Bench"]);
    });
  });

  describe("insertBeforeSilent", () => {
    it("lands just before Context", () => {
      expect(labelsOf(insertBeforeSilent(withSilentLinks([seg("a")]), seg("z")))).toEqual(["a", "z", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "supply", "Context", "ETFs", "Bench"]);
  });

  it("addSegment refuses a reserved name in any case (and padded)", () => {
    const { result } = mount();
    for (const name of ["Context", "context", "BENCH", "  bench "]) act(() => result.current.addSegment(name));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "ETFs", "Bench"]);
  });

  it("renameSegment refuses a silent source and a reserved target; a normal rename still cascades", () => {
    const { result } = mount();
    act(() => result.current.renameSegment(CONTEXT, "Giants"));
    act(() => result.current.renameSegment("fuel", "context"));
    act(() => result.current.renameSegment("fuel", " Bench "));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "fuel", "Context", "ETFs", "Bench"]);
    act(() => result.current.renameSegment("reactors", "builders"));
    expect(labelsOf(result.current.draft.segments)).toEqual(["builders", "fuel", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(result.current.draft.segments)).toEqual(["fuel", "reactors", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(result.current.draft.segments)).toEqual(["fuel", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(result.current.draft.segments)).toEqual([DISCOVERED, "reactors", "fuel", "Context", "ETFs", "Bench"]);
    expect(rowsOf(result.current.draft.basket, "sid-smr")[0].segment).toBe("reactors");
    expect(rowsOf(result.current.draft.basket, "sid-leu")[0].segment).toBe("fuel");
  });

  it("a flat thesis (NO segments at all) also takes the first draft, pair last", () => {
    const { result } = renderHook(() => useChainDraft(thesis([])));
    act(() => result.current.loadDraft(chain([{ sid: "sid-smr", ticker: "SMR", segment: "reactors" }])));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "ETFs", "Bench"]);
    expect(segments.filter((s) => reservedLinkName(s.label)).length).toBe(3);
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
    expect(labelsOf(segments)).toEqual(["reactors", DISCOVERED, "Context", "ETFs", "Bench"]);
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
    expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "ETFs", "Bench"]);
  });

  it("an UNESTABLISHED name already in Context (a restored session) is never re-rolled or parked", () => {
    const t = thesis([member({ ticker: "OKLO", segment: "reactors" })], [seg("reactors")]);
    // KO is in the restored working draft but NOT in the saved spine → not established
    const restored = {
      draft: {
        segments: [seg("reactors")],
        basket: [member({ ticker: "OKLO", segment: "reactors" }), member({ ticker: "KO", segment: CONTEXT })],
      },
      excluded: new Set<string>(),
      reasons: new Map<string, string>(),
      reasonsDirty: false,
    };
    const { result } = renderHook(() => useChainDraft(t, restored));
    expect(result.current.isEstablished("sid-ko")).toBe(false);
    act(() => result.current.loadDraft(chain([{ sid: "sid-ko", ticker: "KO", segment: "reactors" }])));
    expect(rowsOf(result.current.draft.basket, "sid-ko").map((m) => m.segment)).toEqual([CONTEXT]);
  });
});

// RULE 3 at the add seam: an old saved draft run / a pre-deploy session blob can hold a model-proposed
// "Context"/"bench" pile entry; the three ChainEditor add paths copy `p.segment` raw, so the hook's
// addMember / addMemberRows file a reserved label to the Discovered pen (it would otherwise DISPLAY as
// Discovered but SAVE silent). Nothing legitimate adds INTO a silent link through these.
describe("useChainDraft — the add paths never put a name in a silent link", () => {
  const mount = () => renderHook(() => useChainDraft(thesis([], [seg("reactors")])));

  it("addMember files a reserved segment (any case) to the Discovered pen — and creates the pen before the pair", () => {
    for (const reserved of ["Context", "bench", " CONTEXT "]) {
      const { result } = mount();
      act(() => result.current.addMember(member({ ticker: "NEW", segment: reserved })));
      expect(rowsOf(result.current.draft.basket, "sid-new").map((m) => m.segment)).toEqual([DISCOVERED]);
      expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", DISCOVERED, "Context", "ETFs", "Bench"]);
    }
  });

  it("addMember leaves a real link and a null segment exactly as they were (and adds no pen)", () => {
    const { result } = mount();
    act(() => result.current.addMember(member({ ticker: "A", segment: "reactors" })));
    act(() => result.current.addMember(member({ ticker: "B", segment: null })));
    expect(result.current.draft.basket.map((m) => m.segment)).toEqual(["reactors", null]);
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "Context", "ETFs", "Bench"]);
  });

  it("addMemberRows: a multi-link pick with a reserved label collapses the pen row to ONE, real links kept", () => {
    const { result } = mount();
    act(() =>
      result.current.addMemberRows([
        member({ ticker: "NEW", segment: "reactors" }),
        member({ ticker: "NEW", segment: "Context" }),
        member({ ticker: "NEW", segment: "bench" }),
      ]),
    );
    expect(rowsOf(result.current.draft.basket, "sid-new").map((m) => m.segment)).toEqual(["reactors", DISCOVERED]);
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", DISCOVERED, "Context", "ETFs", "Bench"]);
  });

  it("does not duplicate an existing pen", () => {
    const { result } = renderHook(() => useChainDraft(thesis([], [seg("reactors"), seg(DISCOVERED)])));
    act(() => result.current.addMember(member({ ticker: "NEW", segment: "Bench" })));
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", DISCOVERED, "Context", "ETFs", "Bench"]);
  });

  it("a model-proposed 'etfs' through addMember is filed to the pen too — only addFund files into ETFs", () => {
    const { result } = mount();
    act(() => result.current.addMember(member({ ticker: "NEW", segment: "etfs" })));
    act(() => result.current.addMember(member({ ticker: "NW2", segment: ETFS })));
    expect(rowsOf(result.current.draft.basket, "sid-new").map((m) => m.segment)).toEqual([DISCOVERED]);
    expect(rowsOf(result.current.draft.basket, "sid-nw2").map((m) => m.segment)).toEqual([DISCOVERED]);
  });
});

// ETFs — the funds link. A fund's add (the "Surface an ETF" box, or a fund picked in "Add a name") is the one
// operator add INTO a silent link: `addFund` files it in ETFs, one row per name, selected like any add, and
// the row's own remove is its inverse (WB#1).
describe("useChainDraft.addFund — a fund lands in ETFs", () => {
  const mount = () => renderHook(() => useChainDraft(thesis([], [seg("reactors")])));

  it("files the fund in ETFs whatever segment it arrived with, and adds no pen", () => {
    const { result } = mount();
    act(() => result.current.addFund(member({ ticker: "FNDA", segment: null })));
    act(() => result.current.addFund(member({ ticker: "FNDB", segment: ETFS })));
    expect(result.current.draft.basket.map((m) => [m.ticker, m.segment])).toEqual([
      ["FNDA", ETFS],
      ["FNDB", ETFS],
    ]);
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "Context", "ETFs", "Bench"]);
    expect(result.current.isIncluded("sid-fnda")).toBe(true);
  });

  it("is one row per name — a fund already on the card is a no-op", () => {
    const { result } = mount();
    act(() => result.current.addFund(member({ ticker: "FNDA" })));
    const before = result.current.draft;
    act(() => result.current.addFund(member({ ticker: "FNDA" })));
    expect(result.current.draft).toBe(before);
  });

  it("removing the fund is its inverse — the row goes, ETFs stays", () => {
    const { result } = mount();
    act(() => result.current.addFund(member({ ticker: "FNDA" })));
    act(() => result.current.removeMember("sid-fnda"));
    expect(result.current.draft.basket).toEqual([]);
    expect(labelsOf(result.current.draft.segments)).toEqual(["reactors", "Context", "ETFs", "Bench"]);
  });

  it("ETFs is pinned like the other two: no rename, no move, no remove, no link may take its name", () => {
    const { result } = renderHook(() =>
      useChainDraft(thesis([member({ ticker: "FNDA", segment: ETFS })], [seg("reactors"), seg("fuel")])),
    );
    const before = result.current.draft;
    act(() => result.current.renameSegment(ETFS, "Funds"));
    act(() => result.current.moveSegment(ETFS, -1));
    act(() => result.current.removeSegment(ETFS));
    act(() => result.current.addSegment(" etfs "));
    act(() => result.current.renameSegment("fuel", "ETFS"));
    expect(result.current.draft).toBe(before);
  });

  it("a session blob saved BEFORE the ETFs link is filed back: a fund the spine holds in ETFs restores there", () => {
    const t = thesis(
      [
        member({ ticker: "ZZZA", segment: "reactors" }),
        member({ ticker: "FNDA", segment: ETFS, context_role: "benchmark" }), // the spine's role wins on restore
      ],
      [seg("reactors")],
    );
    const restored = {
      draft: {
        segments: [seg("reactors"), seg(DISCOVERED)],
        basket: [
          member({ ticker: "ZZZA", segment: "reactors" }),
          member({ ticker: "FNDA", segment: null, context_role: "benchmark" }), // the old no-link sleeve
          member({ ticker: "FNDA", segment: DISCOVERED, context_role: "benchmark" }), // a stray second row
          member({ ticker: "NEWCO", segment: null }), // not in the spine: never guessed at
        ],
      },
      excluded: new Set<string>(),
      reasons: new Map<string, string>(),
      reasonsDirty: false,
    };
    const { result } = renderHook(() => useChainDraft(t, restored));
    const { basket } = result.current.draft;
    expect(rowsOf(basket, "sid-fnda").map((m) => [m.segment, m.context_role])).toEqual([[ETFS, "benchmark"]]);
    expect(rowsOf(basket, "sid-zzza").map((m) => m.segment)).toEqual(["reactors"]);
    expect(rowsOf(basket, "sid-newco").map((m) => m.segment)).toEqual([null]);
  });

  it("a re-draft never re-rolls a fund out of ETFs and never adopts a drafter 'ETFs' segment", () => {
    const t = thesis(
      [member({ ticker: "ZZZA", segment: "reactors" }), member({ ticker: "FNDA", segment: ETFS })],
      [seg("reactors"), ...PAIR],
    );
    const { result } = renderHook(() => useChainDraft(t));
    act(() =>
      result.current.loadDraft(
        chain(
          [
            { sid: "sid-fnda", ticker: "FNDA", segment: "reactors" },
            { sid: "sid-new", ticker: "NEW", segment: "ETFs" },
          ],
          [seg("reactors"), seg("ETFs")],
        ),
      ),
    );
    const { segments, basket } = result.current.draft;
    expect(rowsOf(basket, "sid-fnda").map((m) => m.segment)).toEqual([ETFS]);
    expect(rowsOf(basket, "sid-new").map((m) => m.segment)).toEqual([DISCOVERED]);
    expect(segments.filter((s) => reservedLinkName(s.label)).length).toBe(3);
  });
});
