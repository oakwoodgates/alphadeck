import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked. Real component under test: the Decision Queue's deep-link (#4 —
// the armed headline name rides ?name= into the Cockpit) and the errored-call surfacing (#2 — a
// thesis whose /call errored must stay VISIBLE, never silently vanish, and the DQ must not sound a
// fake all-clear while a call didn't compute).
const h = vi.hoisted(() => ({
  theses: [] as unknown[],
  // per-thesis-id call result: { data?, isLoading?, isError? } — the useCalls result shape
  calls: {} as Record<string, { data?: unknown; isLoading?: boolean; isError?: boolean }>,
  setArchived: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: h.theses, isLoading: false, error: null }),
  // useCalls now takes the thesis SUMMARIES (it needs basket_size to order the paint) and returns
  // results ALIGNED to that input order — the Board pairs callResults[i] with theses[i].
  useDataFlags: () => ({ data: undefined }),
  useCalls: (subjects: { id: string }[]) =>
    subjects.map((s) => h.calls[s.id] ?? { data: undefined, isLoading: false, isError: false }),
  useSetArchived: () => ({ mutate: h.setArchived, isPending: false }),
}));

import { Board } from "../Board";

const noop = () => {};

const boardEl = (onSelect: (id: string, name?: string) => void = noop) => (
  <Board
    asof="2026-07-21"
    onAsofChange={noop}
    onSelect={onSelect}
    onOpenWorkbench={noop}
    onOpenScoreboard={noop}
    onOpenAdmin={noop}
  />
);

const renderBoard = (onSelect: (id: string, name?: string) => void = noop) => render(boardEl(onSelect));

const armedCall = (
  members: { ticker: string | null; security_id: string; entry_window_closing?: boolean }[],
  extra: Record<string, unknown> = {},
) => ({
  thesis_id: "x",
  state: "armed",
  verdict: "starter_entry",
  conviction_grade: "core",
  entry_grade: "flip",
  key_conviction: { turned: true, detail: "" },
  key_confirmation: { turned: true, detail: "" },
  armed_members: members,
  watch_members: [],
  ...extra,
});

const incubCall = () => ({
  thesis_id: "x",
  state: "incubating",
  verdict: "not_yet",
  conviction_grade: null,
  entry_grade: null,
  key_conviction: { turned: false, detail: "" },
  key_confirmation: { turned: false, detail: "" },
  armed_members: [],
  watch_members: [],
});

const dq = () => screen.getByText("Decision Queue").closest(".dq") as HTMLElement;

beforeEach(() => {
  h.setArchived.mockReset();
  h.theses = [
    { id: "t-a", name: "Alpha thesis", ticker: null, basket_size: 4, narrative: "n", archived: false },
    { id: "t-b", name: "Broken thesis", ticker: "BRK", basket_size: 1, narrative: "n", archived: false },
  ];
  h.calls = {};
});

describe("Board — Decision Queue deep-link (#4)", () => {
  it("carries the armed HEADLINE name (?name=) on a DQ click — ticker preferred", async () => {
    const onSelect = vi.fn();
    h.calls = {
      "t-a": {
        data: armedCall([
          { ticker: "OKLO", security_id: "s-oklo" },
          { ticker: "SMR", security_id: "s-smr" },
        ]),
      },
      "t-b": { data: incubCall() },
    };
    renderBoard(onSelect);

    expect(within(dq()).getByText("+1")).toBeInTheDocument(); // the ranked-menu hint behind [0]
    await userEvent.setup().click(within(dq()).getByText("OKLO").closest("button") as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith("t-a", "OKLO"); // the armed name rides along
  });

  it("falls back to the security_id when the armed headline has no ticker", async () => {
    const onSelect = vi.fn();
    h.calls = {
      "t-a": { data: armedCall([{ ticker: null, security_id: "s-oklo" }]) },
      "t-b": { data: incubCall() },
    };
    renderBoard(onSelect);
    await userEvent.setup().click(within(dq()).getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("t-a", "s-oklo");
  });
});

// The act-by clock: the queue says WHAT is armed and now for how long. The text and the loud (warm) accent
// ride the assembler's own `entry_window_closing` flag on the headline name (Q14) — the FE holds no date rule
// of its own, and never reorders the queue by it.
describe("Board — Decision Queue act-by clock", () => {
  const clock = (container: HTMLElement) => container.querySelector(".dq-clock") as HTMLElement | null;
  const queue = (members: Parameters<typeof armedCall>[0], extra: Record<string, unknown>) => {
    h.calls = { "t-a": { data: armedCall(members, extra) }, "t-b": { data: incubCall() } };
    renderBoard();
    return dq();
  };

  it("states the days left on the entry window, quietly, when the call does not flag it", () => {
    const el = clock(
      queue([{ ticker: "OKLO", security_id: "s-oklo" }], { asof: "2026-10-13", arm_until: "2026-10-18" }),
    );
    expect(el?.textContent).toBe("act within 5d");
    expect(el?.className).not.toContain("closing");
    expect(el?.getAttribute("title")).toBe("the entry window — this entry signal is valid through Oct 18");
  });

  it("goes loud and says so only when the HEADLINE name's entry window is flagged closing", () => {
    const el = clock(
      queue([{ ticker: "OKLO", security_id: "s-oklo", entry_window_closing: true }], {
        asof: "2026-10-13",
        arm_until: "2026-10-15",
      }),
    );
    expect(el?.textContent).toBe("act within 2d · entry window closing");
    expect(el?.className).toContain("closing");
  });

  it("reads the headline's flag, not another armed member's", () => {
    const el = clock(
      queue(
        [
          { ticker: "OKLO", security_id: "s-oklo" },
          { ticker: "SMR", security_id: "s-smr", entry_window_closing: true },
        ],
        { asof: "2026-10-13", arm_until: "2026-10-15" },
      ),
    );
    expect(el?.className).not.toContain("closing");
    expect(el?.textContent).toBe("act within 2d");
  });

  it("says the window lapsed when its date is already behind the card's as-of", () => {
    const el = clock(
      queue([{ ticker: "OKLO", security_id: "s-oklo" }], { asof: "2026-10-13", arm_until: "2026-10-10" }),
    );
    expect(el?.textContent).toBe("entry window lapsed");
  });

  it("draws no clock at all when the card carries no entry window", () => {
    expect(clock(queue([{ ticker: "OKLO", security_id: "s-oklo" }], { asof: "2026-10-13" }))).toBeNull();
  });
});

// The queue wraps instead of clipping (BOARD.md "Known gaps", closed): past two rows it folds the rest behind an
// always-visible "+N more" that shows every item, and it never reorders or ranks the theses to do it (#4). jsdom
// does no layout, so these tests lay the strip out themselves — `perRow` items to a row — and the real wrap is
// checked by eye in the browser.
describe("Board — Decision Queue overflow: no armed item is ever hidden", () => {
  const ROW = 38; // a 28px item + the 10px row gap
  const ITEM = 28;
  const spies: { mockRestore: () => void }[] = [];
  const layout = (perRow: number) => {
    spies.splice(0).forEach((s) => s.mockRestore());
    spies.push(
      vi
        .spyOn(HTMLElement.prototype, "offsetTop", "get")
        .mockImplementation(function (this: HTMLElement) {
          const strip = this.parentElement;
          if (!strip?.classList.contains("dq-items")) return 0;
          return Math.floor(Array.prototype.indexOf.call(strip.children, this) / perRow) * ROW;
        }),
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(ITEM),
    );
  };
  afterEach(() => spies.splice(0).forEach((s) => s.mockRestore()));

  // n armed single-name cards; the LATER the card, the SOONER its act-by date — a queue sorted by deadline
  // would come back reversed, so an unchanged order is a real assertion, not an accident of the fixture
  const seedArmed = (n: number) => {
    const ids = Array.from({ length: n }, (_, i) => `t-${i}`);
    h.theses = ids.map((id, i) => ({
      id,
      name: `Thesis ${i}`,
      ticker: `T${i}`,
      basket_size: 1,
      narrative: "n",
      archived: false,
    }));
    h.calls = Object.fromEntries(
      ids.map((id, i) => [
        id,
        {
          data: armedCall([{ ticker: `T${i}`, security_id: `s-${i}` }], {
            asof: "2026-10-13",
            arm_until: `2026-10-${String(30 - i).padStart(2, "0")}`,
          }),
        },
      ]),
    );
  };
  const tickers = () =>
    [...dq().querySelectorAll(".dq-item b")].map((b) => b.textContent);
  const held = () => [...dq().querySelectorAll(".dq-item.held b")].map((b) => b.textContent);
  const inOrder = (n: number) => Array.from({ length: n }, (_, i) => `T${i}`);

  it("renders every armed item in the existing order, holds all past two rows behind a visible count", () => {
    seedArmed(12);
    layout(3); // four rows of three
    renderBoard();

    expect(tickers()).toEqual(inOrder(12)); // all twelve rendered, order unchanged
    expect(held()).toEqual(inOrder(12).slice(6)); // rows three and four are held
    const more = within(dq()).getByRole("button", { name: "+6 more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveAttribute("title", "12 armed — show every one");
    // held to exactly the two rows: row two's bottom edge (38 + 28)
    expect(dq().querySelector<HTMLElement>(".dq-items")?.style.maxHeight).toBe("66px");
  });

  it("'+N more' shows every item and puts the last one within reach; 'show fewer' folds it back", async () => {
    const onSelect = vi.fn();
    seedArmed(12);
    layout(3);
    renderBoard(onSelect);
    const user = userEvent.setup();

    await user.click(within(dq()).getByRole("button", { name: "+6 more" }));
    expect(held()).toEqual([]);
    expect(tickers()).toEqual(inOrder(12)); // opening reorders nothing
    expect(dq().querySelector<HTMLElement>(".dq-items")?.style.maxHeight).toBe("");
    await user.click(within(dq()).getByText("T11").closest("button") as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith("t-11", "T11"); // the last armed name is reachable

    await user.click(within(dq()).getByRole("button", { name: "show fewer" }));
    expect(held()).toEqual(inOrder(12).slice(6));
    expect(within(dq()).getByRole("button", { name: "+6 more" })).toBeInTheDocument();
  });

  it("draws no fold control when the whole queue fits in two rows", () => {
    seedArmed(12);
    layout(6); // two rows of six
    renderBoard();
    expect(tickers()).toEqual(inOrder(12));
    expect(held()).toEqual([]);
    expect(within(dq()).queryByRole("button", { name: /more|show fewer/ })).toBeNull();
    expect(dq().querySelector<HTMLElement>(".dq-items")?.style.maxHeight).toBe("");
  });

  it("re-counts when the strip re-flows (a window resize), not only on first paint", () => {
    seedArmed(12);
    layout(3);
    const { rerender } = renderBoard();
    expect(within(dq()).getByRole("button", { name: "+6 more" })).toBeInTheDocument();

    layout(4); // wider: three rows of four
    rerender(boardEl());
    expect(within(dq()).getByRole("button", { name: "+4 more" })).toBeInTheDocument();
    expect(held()).toEqual(inOrder(12).slice(8));

    layout(6); // wider still: it all fits
    rerender(boardEl());
    expect(held()).toEqual([]);
    expect(within(dq()).queryByRole("button", { name: /more/ })).toBeNull();
  });

  it("leaves the all-clear and the withheld all-clear alone (no fold control without items)", () => {
    layout(3);
    h.calls = { "t-a": { data: incubCall() }, "t-b": { data: undefined, isError: true } };
    renderBoard();
    expect(within(dq()).getByText(/didn't compute/)).toBeInTheDocument();
    expect(within(dq()).queryByRole("button")).toBeNull();
  });
});

describe("Board — errored /call stays visible (#2)", () => {
  it("surfaces an errored thesis with an error affordance — not filtered, not a fake all-clear", () => {
    h.calls = {
      "t-a": { data: incubCall() },
      "t-b": { data: undefined, isError: true }, // the /call failed (500 / mid-flight delete)
    };
    renderBoard();

    // keep-it-visible: the errored thesis is still on screen, with a clear error affordance
    expect(screen.getByText("Broken thesis")).toBeInTheDocument();
    expect(screen.getByText("call failed to compute")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // the DQ must NOT sound the triumphant all-clear while a call didn't compute
    expect(screen.queryByText(/Nothing to do/)).toBeNull();
    expect(within(dq()).getByText(/didn't compute/)).toBeInTheDocument();
  });

  it("a clean board (no errors, none armed) still shows the calm all-clear, no error strip", () => {
    h.calls = { "t-a": { data: incubCall() }, "t-b": { data: incubCall() } };
    renderBoard();
    expect(screen.getByText(/Nothing to do/)).toBeInTheDocument();
    expect(screen.queryByText("call failed to compute")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Board — a QUEUED call is still work in progress (C3)", () => {
  it("does not sound the all-clear while a call waits for a concurrency slot", () => {
    // Progressive paint means a query can be PENDING with fetchStatus idle — `isLoading` (v5 =
    // pending AND fetching) is FALSE for it. Keying the all-clear off isLoading alone would flash
    // "Nothing armed. Nothing to do. ✓" with calls still queued.
    h.calls = {
      "t-a": { data: incubCall() },
      "t-b": { data: undefined, isPending: true, isFetching: false, isLoading: false },
    };
    renderBoard();
    expect(within(dq()).getByText("Computing…")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing to do/)).toBeNull();
  });
});

describe("Board — a kept previous-as-of card (C2)", () => {
  const staleArmed = {
    data: { ...armedCall([{ ticker: "OKLO", security_id: "s-oklo" }]), asof: "2026-08-29" },
    isPlaceholderData: true,
    isFetching: true,
  };

  it("keeps the card visible, dimmed and DATED — the scrub never blanks the board", () => {
    h.calls = { "t-a": staleArmed, "t-b": { data: incubCall() } };
    const { container } = renderBoard();
    expect(screen.getByText("Alpha thesis")).toBeInTheDocument();
    expect(screen.getByText(/as of Aug 29/)).toBeInTheDocument();
    expect(container.querySelector(".card-wrap.recomputing")).not.toBeNull();
  });

  it("bars it from the Decision Queue — the one loud element never prompts on a previous date", () => {
    h.calls = { "t-a": staleArmed, "t-b": { data: incubCall() } };
    renderBoard();
    expect(within(dq()).queryByText("OKLO")).toBeNull();
    expect(within(dq()).getByText("Recomputing…")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing to do/)).toBeNull(); // and no fake all-clear either
  });
});
