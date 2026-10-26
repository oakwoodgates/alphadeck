import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

const renderBoard = (onSelect: (id: string, name?: string) => void = noop) =>
  render(
    <Board
      asof="2026-07-21"
      onAsofChange={noop}
      onSelect={onSelect}
      onOpenWorkbench={noop}
      onOpenScoreboard={noop}
      onOpenAdmin={noop}
    />,
  );

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
