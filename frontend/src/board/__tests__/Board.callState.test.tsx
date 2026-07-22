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
  useCalls: (ids: string[]) =>
    ids.map((id) => h.calls[id] ?? { data: undefined, isLoading: false, isError: false }),
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

const armedCall = (members: { ticker: string | null; security_id: string }[]) => ({
  thesis_id: "x",
  state: "armed",
  verdict: "starter_entry",
  conviction_grade: "core",
  entry_grade: "flip",
  key_conviction: { turned: true, detail: "" },
  key_confirmation: { turned: true, detail: "" },
  armed_members: members,
  watch_members: [],
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
