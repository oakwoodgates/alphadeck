import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// #1–3 (chain-editing Phase 2) — value-chain TOPOLOGY on the SCORE view, end to end through the Workbench:
// the ✎ edit links reveal exposes per-link rename / reorder / remove + the `+ add link` bootstrap, each an
// immediate-promote built FROM THE SPINE (thesis.basket/segments). Rename cascades onto members; reorder
// swaps; add appends; remove is multi-safe and routes a last placement to the Discovered floor. Plus the
// two-screen relabel (the button reads "Edit the basket").

const mk = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, segment: string | null) => ({
    security_id: sid, ticker, name: `${ticker} Inc`, sector: null, exchange: null, category: null,
    origin: null, foreign_filer_form: null, price_symbol: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity", segment,
    purity: fig(null, null), runway: fig(null, null), catalysts: fig(0, 0),
    dilution: fig(null, null), market_cap: fig(null, null), fit: "unrated",
    unconfirmed_estimates: 0, thin_price_history: false,
  });
  const bm = (ticker: string, sid: string, segment: string | null) => ({
    ticker, role: "core", security_id: sid, detail: null, segment,
    thesis_fit: null, conviction: null, surfaced_terms: [] as string[],
    authored_by: "operator_set" as const, signed_off: false,
  });
  const wrap = (
    segments: { label: string; descriptor: string | null }[],
    basket: ReturnType<typeof bm>[],
    members: ReturnType<typeof scoredRow>[],
  ) => ({
    thesis: {
      id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments, basket,
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { thesis_id: "t-nuke", asof: "2026-06-08", segments, members },
  });
  // grouped: OKLO in reactors AND fuel (multi-membership); LEU only in fuel
  const grouped = () =>
    wrap(
      [{ label: "reactors", descriptor: null }, { label: "fuel", descriptor: null }],
      [bm("OKLO", "s-oklo", "reactors"), bm("OKLO", "s-oklo", "fuel"), bm("LEU", "s-leu", "fuel")],
      [scoredRow("s-oklo", "OKLO", "reactors"), scoredRow("s-leu", "LEU", "fuel")],
    );
  // flat: no links yet (the pre-decompose basket) — the + add link bootstrap builds the first
  const flat = () =>
    wrap([], [bm("OKLO", "s-oklo", null), bm("LEU", "s-leu", null)], [
      scoredRow("s-oklo", "OKLO", null),
      scoredRow("s-leu", "LEU", null),
    ]);
  // The two SILENT links as they now always arrive on the wire — fixed descriptors, verbatim.
  const CTX = {
    label: "Context",
    descriptor:
      "Watched for context: buyers, counter-case names, giants where the theme is a slice. Never counts toward the call.",
  };
  const BNC = {
    label: "Bench",
    descriptor: "Speculative names: pivots, distressed or thinly traded. Never counts toward the call.",
  };
  // silent: the chain + the pair; OKLO in reactors AND fuel (multi-membership), LEU only in fuel, KO in Context
  const silent = () =>
    wrap(
      [{ label: "reactors", descriptor: null }, { label: "fuel", descriptor: null }, CTX, BNC],
      [
        bm("OKLO", "s-oklo", "reactors"),
        bm("OKLO", "s-oklo", "fuel"),
        bm("LEU", "s-leu", "fuel"),
        bm("KO", "s-ko", "Context"),
      ],
      [
        scoredRow("s-oklo", "OKLO", "reactors"),
        scoredRow("s-leu", "LEU", "fuel"),
        scoredRow("s-ko", "KO", "Context"),
      ],
    );
  // the pair is on the wire but NO name sits in either: both silent tabs must stay hidden
  const pairEmpty = () =>
    wrap(
      [{ label: "reactors", descriptor: null }, { label: "fuel", descriptor: null }, CTX, BNC],
      [bm("OKLO", "s-oklo", "reactors"), bm("LEU", "s-leu", "fuel")],
      [scoredRow("s-oklo", "OKLO", "reactors"), scoredRow("s-leu", "LEU", "fuel")],
    );
  // flat (no real link, no placement) but the pair rides the wire — it must NOT read as a decomposed chain
  const flatWithPair = () =>
    wrap([CTX, BNC], [bm("OKLO", "s-oklo", null), bm("LEU", "s-leu", null)], [
      scoredRow("s-oklo", "OKLO", null),
      scoredRow("s-leu", "LEU", null),
    ]);
  // multi: the REAL scored shape — one scored row per basket ROW (a name in 2 links is 2 scored rows), so a
  // multi-link name shows under EACH of its links' tabs. OKLO in reactors AND fuel, LEU in fuel.
  const PAIR = [CTX, BNC];
  const multi = (oklo: string[]) =>
    wrap(
      [{ label: "reactors", descriptor: null }, { label: "fuel", descriptor: null }, ...PAIR],
      [...oklo.map((l) => bm("OKLO", "s-oklo", l)), bm("LEU", "s-leu", "fuel")],
      [...oklo.map((l) => scoredRow("s-oklo", "OKLO", l)), scoredRow("s-leu", "LEU", "fuel")],
    );
  return { grouped, flat, silent, pairEmpty, flatWithPair, multi };
});

const state = vi.hoisted(() => ({ thesis: undefined as unknown, scored: undefined as unknown }));
const h = vi.hoisted(() => ({ promote: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: vi.fn(), isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: state.thesis }),
  useWorkbenchScored: () => ({ data: state.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.promote, mutateAsync: vi.fn(), reset: vi.fn(),
    isPending: false, isError: false, isSuccess: false, error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () => render(<Workbench asof="2026-06-08" />);
const enterEditLinks = async (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole("button", { name: /edit links/i }));
const lastPayload = () => h.promote.mock.calls.at(-1)![0];
const labels = (segs: { label: string }[]) => segs.map((s) => s.label);
// every promote now carries the two FIXED silent links, always last (sanitizeBasketForPromote → withSilentLinks)
const SILENT = ["Context", "Bench"];
const DISCOVERED_LABEL = "Discovered";

beforeEach(() => {
  const g = mk.grouped();
  state.thesis = g.thesis;
  state.scored = g.scored;
  h.promote.mockClear();
});

describe("Workbench — value-chain topology on SCORE (#1–3, chain-editing Phase 2)", () => {
  it("the two-screen relabel: the button reads 'Edit the basket', not 'Edit the chain'", () => {
    renderWb();
    expect(screen.getByRole("button", { name: /edit the basket/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit the chain/i })).toBeNull();
  });

  it("#1 rename CASCADES the new label onto every placed member", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    const input = screen.getByLabelText("rename reactors");
    await user.clear(input);
    await user.type(input, "reactor builders{Enter}");

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["reactor builders", "fuel", ...SILENT]);
    // OKLO's reactors row cascaded; no member is left pointing at the old label (no orphan)
    expect(p.basket.some((b: { security_id: string; segment: string }) => b.security_id === "s-oklo" && b.segment === "reactor builders")).toBe(true);
    expect(p.basket.some((b: { segment: string }) => b.segment === "reactors")).toBe(false);
  });

  it("#2 reorder swaps and persists the link order", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("move fuel left"));
    expect(labels(lastPayload().segments)).toEqual(["fuel", "reactors", ...SILENT]);
  });

  it("#3 add appends a new link", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.type(screen.getByLabelText("new link name"), "supply");
    await user.click(screen.getByRole("button", { name: "+ add link" }));
    // the new link lands BEFORE Context / Bench — the pinned pair stays last
    expect(labels(lastPayload().segments)).toEqual(["reactors", "fuel", "supply", ...SILENT]);
  });

  it("#3 remove is multi-safe: a name kept elsewhere loses only its redundant row", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("remove reactors"));

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["fuel", ...SILENT]);
    const oklo = p.basket.filter((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo).toHaveLength(1);
    expect(oklo[0].segment).toBe("fuel"); // kept placement, not floored
    expect(p.basket.some((b: { segment: string }) => b.segment === "Discovered")).toBe(false);
  });

  it("#3 remove routes a LAST placement to the Discovered floor (never null / gone)", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("remove fuel"));

    const p = lastPayload();
    const leu = p.basket.find((b: { security_id: string }) => b.security_id === "s-leu");
    expect(leu.segment).toBe("Discovered"); // LEU was only in fuel → floored, not dropped (#9)
    expect(p.segments.some((s: { label: string }) => s.label === "Discovered")).toBe(true);
    expect(p.segments.some((s: { label: string }) => s.label === "fuel")).toBe(false);
  });

  it("bootstrap: + add link builds the FIRST link from the flat (pre-decompose) state", async () => {
    const user = userEvent.setup();
    const f = mk.flat();
    state.thesis = f.thesis;
    state.scored = f.scored;
    renderWb();
    // flat: no tab strip; the toggle is still there so the first link is buildable
    await enterEditLinks(user);
    expect(screen.getByText(/name your first one/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText("new link name"), "reactors");
    await user.click(screen.getByRole("button", { name: "+ add link" }));

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["reactors", ...SILENT]);
    // the flat basket rides through untouched (null segments stay valid — the validator only rejects orphans)
    expect(p.basket).toHaveLength(2);
    expect(p.basket.every((b: { segment: string | null }) => b.segment === null)).toBe(true);
  });
});

// --- the two SILENT links on the scored view: Context + Bench -------------------------------------------
// Fixed, last, protected. A company is silent XOR voting — the rail's mover must NEVER send a payload with a
// silent row beside another row; the checkboxes are the visible inverse (uncheck Context → Discovered, check a
// real link → out of Context); an empty silent tab stays hidden; the editor lists them as fixed rows.
describe("Workbench — Context and Bench (the silent links)", () => {
  const useFixture = (f: { thesis: unknown; scored: unknown }) => {
    state.thesis = f.thesis;
    state.scored = f.scored;
  };
  const tabs = (root: HTMLElement) =>
    Array.from(root.querySelectorAll(".chain .seg .sn")).map((e) => e.textContent);
  const tickers = (root: HTMLElement) =>
    Array.from(root.querySelectorAll(".nmrow .tk")).map((e) => e.textContent);
  const rowsFor = (p: { basket: { security_id: string; segment: string | null }[] }, sid: string) =>
    p.basket.filter((b) => b.security_id === sid);

  it("the rail lists the real links, then Context and Bench", () => {
    useFixture(mk.silent());
    renderWb(); // OKLO is the default selection (first name of the first tab)
    const order = screen.getAllByRole("checkbox").map((b) => b.closest("label")?.textContent);
    expect(order).toEqual(["reactors", "fuel", "Context", "Bench"]);
    expect(screen.getByRole("checkbox", { name: "reactors" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "fuel" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Context" })).not.toBeChecked();
  });

  it("checking Context on a 2-link name promotes ONE row in Context (never a silent row beside another)", async () => {
    const user = userEvent.setup();
    useFixture(mk.silent());
    renderWb();
    await user.click(screen.getByRole("checkbox", { name: "Context" }));

    expect(h.promote).toHaveBeenCalledTimes(1);
    const p = lastPayload();
    expect(rowsFor(p, "s-oklo").map((b) => b.segment)).toEqual(["Context"]);
    // everyone else rides through untouched
    expect(rowsFor(p, "s-leu").map((b) => b.segment)).toEqual(["fuel"]);
    expect(rowsFor(p, "s-ko").map((b) => b.segment)).toEqual(["Context"]);
    // the payload still ends with the fixed pair, in order
    expect(labels(p.segments).slice(-2)).toEqual(["Context", "Bench"]);
  });

  it("checking a real link on a Context name promotes ONE row in that link (it leaves Context)", async () => {
    const user = userEvent.setup();
    useFixture(mk.silent());
    renderWb();
    await user.click(screen.getByRole("button", { name: /^Context/ })); // the Context tab (KO)
    expect(screen.getByRole("checkbox", { name: "Context" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "fuel" }));

    const p = lastPayload();
    expect(rowsFor(p, "s-ko").map((b) => b.segment)).toEqual(["fuel"]);
    expect(rowsFor(p, "s-oklo")).toHaveLength(2); // the other names untouched
  });

  it("unchecking Context floors the name to a visible Discovered row, the pen BEFORE the pair — the inverse", async () => {
    const user = userEvent.setup();
    useFixture(mk.silent());
    renderWb();
    await user.click(screen.getByRole("button", { name: /^Context/ }));
    await user.click(screen.getByRole("checkbox", { name: "Context" }));

    const p = lastPayload();
    expect(rowsFor(p, "s-ko").map((b) => b.segment)).toEqual(["Discovered"]); // never null, never gone
    expect(labels(p.segments)).toEqual(["reactors", "fuel", "Discovered", "Context", "Bench"]);
  });

  it("a silent tab renders only when it has names; both are hidden when empty", () => {
    useFixture(mk.pairEmpty());
    const { container, unmount } = renderWb();
    expect(tabs(container)).toEqual(["reactors", "fuel"]);
    unmount();

    useFixture(mk.silent()); // KO is in Context, nobody is on the Bench
    const second = renderWb();
    expect(tabs(second.container)).toEqual(["reactors", "fuel", "Context"]);
  });

  // the scored header's "Basket — N names across M links" (the Basket section's own heading)
  const basketHeader = (root: HTMLElement) =>
    [...root.querySelectorAll(".sect-h")].find((e) => e.textContent?.startsWith("Basket"))?.textContent ?? "";

  it("the header's 'across M links' counts REAL links only — Context / Bench are not chain links", () => {
    useFixture(mk.silent()); // OKLO in reactors, LEU in fuel, KO in Context
    const { container } = renderWb();
    expect(basketHeader(container)).toContain("3 names across 2 links"); // reactors + fuel — Context excluded
  });

  it("a flat basket with a name moved into Context reads no link count at all", () => {
    const f = mk.flatWithPair();
    f.thesis.basket[0].segment = "Context";
    f.scored.members[0].segment = "Context";
    useFixture(f);
    const { container } = renderWb();
    expect(basketHeader(container)).toContain("2 names");
    expect(basketHeader(container)).not.toMatch(/across \d+ links?/); // was "across 1 link"
  });

  it("the Context tab lists its names and shows the fixed descriptor when active", async () => {
    const user = userEvent.setup();
    useFixture(mk.silent());
    const { container } = renderWb();
    expect(screen.queryByText(/watched for context/i)).toBeNull(); // not active → no descriptor line
    await user.click(screen.getByRole("button", { name: /^Context/ }));
    expect(tickers(container)).toEqual(["KO"]);
    expect(container.querySelector(".chain-desc")?.textContent).toContain(
      "Watched for context: buyers, counter-case names, giants where the theme is a slice. Never counts toward the call.",
    );
  });

  it("the pair never makes a flat basket read as decomposed — and a name can still be moved INTO Context", async () => {
    const user = userEvent.setup();
    useFixture(mk.flatWithPair());
    const { container } = renderWb();
    expect(screen.getByText(/no value chain yet/i)).toBeInTheDocument(); // still flat
    expect(tabs(container)).toEqual([]);
    await user.click(screen.getByRole("checkbox", { name: "Context" }));

    const p = lastPayload();
    expect(rowsFor(p, "s-oklo").map((b) => b.segment)).toEqual(["Context"]);
    expect(rowsFor(p, "s-leu").map((b) => b.segment)).toEqual([null]); // the flat name rides through
    expect(labels(p.segments)).toEqual(["Context", "Bench"]);
  });

  // B1 (WB#1) — after a rail move the VIEW follows the name: the tab switches to its new home and the rail
  // keeps showing the same company, so an immediate undo click acts on the name that was just moved. The
  // (mocked) refetch is simulated by swapping the fixture and re-rendering.
  describe("the selection follows a moved name", () => {
    const railTicker = (root: HTMLElement) => root.querySelector(".dd-head .tk")?.textContent;
    const activeTab = (root: HTMLElement) => root.querySelector(".chain .seg.on .sn")?.textContent;
    const clickTab = (name: RegExp) => screen.getByRole("button", { name });

    it("into Context: the rail still shows the SAME name, the active tab is Context, and unchecking acts on it", async () => {
      const user = userEvent.setup();
      useFixture(mk.multi(["reactors", "fuel"]));
      const { container, rerender } = renderWb(); // OKLO is the implicit default (first of the reactors tab)
      expect(railTicker(container)).toBe("OKLO");
      await user.click(screen.getByRole("checkbox", { name: "Context" }));
      expect(rowsFor(lastPayload(), "s-oklo").map((b) => b.segment)).toEqual(["Context"]); // ONE row

      useFixture(mk.multi(["Context"])); // the refetch lands: OKLO now lives only in Context
      rerender(<Workbench asof="2026-06-08" />);
      // not the next name in the old tab (LEU is not even in reactors; the old fallback would pick shownMembers[0])
      expect(railTicker(container)).toBe("OKLO");
      expect(activeTab(container)).toBe("Context");
      expect(screen.getByRole("checkbox", { name: "Context" })).toBeChecked();

      // the immediate undo acts on OKLO, not on whoever the view fell back to
      h.promote.mockClear();
      await user.click(screen.getByRole("checkbox", { name: "Context" }));
      expect(rowsFor(lastPayload(), "s-oklo").map((b) => b.segment)).toEqual(["Discovered"]);
      expect(rowsFor(lastPayload(), "s-leu").map((b) => b.segment)).toEqual(["fuel"]); // LEU untouched
    });

    it("unchecked to Discovered: the tab follows to the synthesized Discovered tab, same name selected", async () => {
      const user = userEvent.setup();
      useFixture(mk.multi(["Context"]));
      // pre-state: OKLO in Context, view on the Context tab
      const { container, rerender } = renderWb();
      await user.click(clickTab(/^Context/));
      expect(railTicker(container)).toBe("OKLO");
      await user.click(screen.getByRole("checkbox", { name: "Context" }));
      useFixture(mk.multi([DISCOVERED_LABEL]));
      rerender(<Workbench asof="2026-06-08" />);
      expect(activeTab(container)).toBe("Discovered");
      expect(railTicker(container)).toBe("OKLO");
    });

    it("a multi-link name losing the viewed link follows to its first REMAINING link", async () => {
      const user = userEvent.setup();
      useFixture(mk.multi(["reactors", "fuel"]));
      const { container, rerender } = renderWb();
      await user.click(clickTab(/^fuel/)); // the fuel tab: OKLO (also in reactors) + LEU
      await user.click(container.querySelector(".nmrow .tk")!.closest(".nmrow") as HTMLElement); // pick OKLO
      expect(railTicker(container)).toBe("OKLO");
      await user.click(screen.getByRole("checkbox", { name: "fuel" })); // uncheck fuel → [reactors]
      useFixture(mk.multi(["reactors"]));
      rerender(<Workbench asof="2026-06-08" />);
      expect(activeTab(container)).toBe("reactors");
      expect(railTicker(container)).toBe("OKLO");
    });

    it("does NOT jump a name that is still shown in the active tab (a multi-link name picked in its 2nd tab)", async () => {
      const user = userEvent.setup();
      useFixture(mk.multi(["reactors", "fuel"]));
      const { container } = renderWb();
      await user.click(clickTab(/^fuel/));
      await user.click(container.querySelector(".nmrow .tk")!.closest(".nmrow") as HTMLElement); // OKLO, fuel tab
      expect(activeTab(container)).toBe("fuel"); // its first scored row is reactors — the view must not snap there
      expect(railTicker(container)).toBe("OKLO");
    });

    it("a tab click still navigates (the pick is cleared, never overridden)", async () => {
      const user = userEvent.setup();
      useFixture(mk.multi(["reactors", "fuel"]));
      const { container } = renderWb();
      await user.click(screen.getByRole("checkbox", { name: "Context" })); // pins OKLO
      await user.click(clickTab(/^fuel/));
      expect(activeTab(container)).toBe("fuel");
    });
  });

  // C3 — the scored view's rename reverts a reserved name; it now SAYS why.
  it("renaming a link to a reserved name reverts AND shows the 'is a fixed link' note (cleared on the next edit)", async () => {
    const user = userEvent.setup();
    useFixture(mk.silent());
    renderWb();
    await enterEditLinks(user);
    const input = screen.getByLabelText("rename fuel");
    await user.clear(input);
    await user.type(input, "bench");
    await user.tab(); // blur = commit
    expect(input).toHaveValue("fuel");
    expect(screen.getByText(/Bench is a fixed link/)).toBeInTheDocument();
    expect(h.promote).not.toHaveBeenCalled();
    await user.click(input); // the next edit begins
    expect(screen.queryByText(/is a fixed link/)).toBeNull();
  });

  describe("✎ edit links", () => {
    it("lists Context and Bench as FIXED rows — no rename, no arrows, no remove", async () => {
      const user = userEvent.setup();
      useFixture(mk.silent());
      renderWb();
      await enterEditLinks(user);

      expect(screen.getByText(/^Context · 1/)).toBeInTheDocument(); // KO
      expect(screen.getByText(/^Bench · 0/)).toBeInTheDocument();
      for (const l of ["Context", "Bench"]) {
        expect(screen.queryByLabelText(`rename ${l}`)).toBeNull();
        expect(screen.queryByLabelText(`move ${l} left`)).toBeNull();
        expect(screen.queryByLabelText(`move ${l} right`)).toBeNull();
        expect(screen.queryByLabelText(`remove ${l}`)).toBeNull();
      }
      // …but the real links keep every control
      expect(screen.getByLabelText("rename reactors")).toBeInTheDocument();
      expect(screen.getByLabelText("remove fuel")).toBeInTheDocument();
    });

    it("shows both rows even when empty (the editors always list the pair)", async () => {
      const user = userEvent.setup();
      useFixture(mk.pairEmpty());
      renderWb();
      await enterEditLinks(user);
      expect(screen.getByText(/^Context · 0/)).toBeInTheDocument();
      expect(screen.getByText(/^Bench · 0/)).toBeInTheDocument();
    });

    it("the reorder bounds are the REAL links: the last real link can't move right (past the pair)", async () => {
      const user = userEvent.setup();
      useFixture(mk.silent());
      renderWb();
      await enterEditLinks(user);
      expect(screen.getByLabelText("move fuel right")).toBeDisabled();
      expect(screen.getByLabelText("move reactors left")).toBeDisabled();
      expect(screen.getByLabelText("move reactors right")).toBeEnabled();
    });

    it("+ add link lands BEFORE the pair and refuses a reserved name", async () => {
      const user = userEvent.setup();
      useFixture(mk.silent());
      renderWb();
      await enterEditLinks(user);

      await user.type(screen.getByLabelText("new link name"), "context");
      expect(screen.getByRole("button", { name: "+ add link" })).toBeDisabled();
      expect(screen.getByText(/is a fixed link/i)).toBeInTheDocument();
      await user.type(screen.getByLabelText("new link name"), "{Enter}"); // Enter can't sneak it through
      expect(h.promote).not.toHaveBeenCalled();

      await user.clear(screen.getByLabelText("new link name"));
      await user.type(screen.getByLabelText("new link name"), "supply");
      await user.click(screen.getByRole("button", { name: "+ add link" }));
      expect(labels(lastPayload().segments)).toEqual(["reactors", "fuel", "supply", "Context", "Bench"]);
    });

    it("renaming a link INTO a reserved name is refused (the input reverts, nothing is promoted)", async () => {
      const user = userEvent.setup();
      useFixture(mk.silent());
      renderWb();
      await enterEditLinks(user);
      const input = screen.getByLabelText("rename fuel");
      await user.clear(input);
      await user.type(input, "bench{Enter}");
      await user.tab(); // blur
      expect(h.promote).not.toHaveBeenCalled();
      expect(input).toHaveValue("fuel");
    });
  });
});
