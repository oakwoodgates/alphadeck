import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NarrativeIndex from "../NarrativeIndex";
import type {
  NarrativeBenchmarkOut,
  NarrativeCardDetailOut,
  NarrativeCardOut,
  NarrativeIndexOut,
} from "../useNarrativeIndex";

// The Narrative Index body with its two hooks stubbed: the grid of minis with their numbers and state chips, the
// window switch and the Context/Bench toggle threading into the read, the sort options with "—" last, a mini
// enlarging in place and closing, the creation marker only inside the window, and a card's name opening the
// Cockpit. The hooks' requests have their own test.

const h = vi.hoisted(() => ({ useNarrativeIndex: vi.fn(), useNarrativeCard: vi.fn() }));
vi.mock("../useNarrativeIndex", () => ({
  useNarrativeIndex: h.useNarrativeIndex,
  useNarrativeCard: h.useNarrativeCard,
}));

const DATES = ["2026-10-26", "2026-10-27", "2026-10-28"];

const card = (id: string, name: string, over: Partial<NarrativeCardOut> = {}): NarrativeCardOut => ({
  thesis_id: id,
  thesis_name: name,
  state: "incubating",
  created: "2026-10-27",
  created_in_window: true,
  created_base: "2026-10-27",
  names: 5,
  priced: 4,
  unresolved: 1,
  silent_left_out: 0,
  ret: 2.0,
  vs_spy: 1.0,
  since_created: 0.5,
  sessions_flat: 0,
  points: [100, 101, 102],
  flagged: [],
  ...over,
});

const bench = (symbol: string, over: Partial<NarrativeBenchmarkOut> = {}): NarrativeBenchmarkOut => ({
  symbol,
  available: true,
  reason: null,
  ret: 1.0,
  points: [100, 100.5, 101],
  sessions_flat: 0,
  ...over,
});

const CARDS: NarrativeCardOut[] = [
  card("t-grid", "Grid", { state: "armed", ret: 9.0, vs_spy: 8.0, since_created: 3.0 }),
  card("t-alpha", "alpha uranium", {
    state: "warming",
    ret: -4.0,
    vs_spy: -5.0,
    created: "2026-01-02",
    created_in_window: false,
    created_base: null,
    since_created: null,
    sessions_flat: 2,
  }),
  card("t-mid", "Midstream", { state: null, ret: null, vs_spy: null, since_created: null, priced: 0 }),
];

const payload = (over: Partial<NarrativeIndexOut> = {}): NarrativeIndexOut => ({
  asof: "2026-10-28",
  window: "1y",
  include_silent: false,
  sessions: 2,
  dates: DATES,
  session_source: "benchmarks",
  live_cards: 3,
  cards_counted: 3,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  companies: 14,
  priced_companies: 12,
  silent_rows_left_out: 4,
  unresolved_rows: 1,
  flagged_companies: 0,
  benchmarks: [bench("SPY"), bench("IWM", { available: false, reason: "no IWM tape at this as-of", points: null, ret: null })],
  cards: CARDS,
  window_sessions: { "1y": 252, "90d": 90, "30d": 30 },
  cache: "hit",
  compute_ms: 1200,
  ...over,
});

const detail = (c: NarrativeCardOut): NarrativeCardDetailOut => ({
  asof: "2026-10-28",
  window: "1y",
  include_silent: false,
  sessions: 2,
  dates: DATES,
  card: c,
  benchmarks: payload().benchmarks,
  contributors: [0, 3, 4],
  members: [
    { ticker: "AAA", security_id: "s-a", sessions: 2, first: "2026-10-27", last: "2026-10-28", flag: null },
    { ticker: "ZZZ", security_id: null, sessions: 0, first: null, last: null, flag: null },
  ],
  cache: "hit",
  compute_ms: 0,
});

function setup(data: NarrativeIndexOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useNarrativeIndex.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  h.useNarrativeCard.mockImplementation((_a: string, _w: string, _s: boolean, id: string | null) => ({
    data: id ? detail(CARDS.find((c) => c.thesis_id === id) ?? CARDS[0]) : undefined,
    isLoading: false,
    error: null,
  }));
  const onSelectThesis = vi.fn();
  const view = render(<NarrativeIndex asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const tileNames = () =>
  screen.getAllByRole("region").map((r) => r.getAttribute("aria-label")?.replace(/, enlarged$/, ""));

beforeEach(() => {
  h.useNarrativeIndex.mockReset();
  h.useNarrativeCard.mockReset();
});

describe("NarrativeIndex", () => {
  it("renders one mini per card, by name, with its numbers, its state chip and the quiet lines", () => {
    setup();
    expect(tileNames()).toEqual(["alpha uranium", "Grid", "Midstream"]);
    const grid = screen.getByRole("region", { name: "Grid" });
    expect(within(grid).getByText("1Y +9.0%")).toBeInTheDocument();
    expect(within(grid).getByText("vs SPY +8.0%")).toBeInTheDocument();
    expect(within(grid).getByText("since created +3.0%")).toBeInTheDocument();
    expect(within(grid).getByText("4/5 priced")).toBeInTheDocument();
    expect(within(grid).getByText("Armed")).toHaveClass("ni-st", "armed");
    expect(within(grid).queryByText(/flat/)).toBeNull(); // no flat session: nothing shown

    const alpha = screen.getByRole("region", { name: "alpha uranium" });
    expect(within(alpha).getByText("created before the window")).toBeInTheDocument();
    expect(within(alpha).getByText("2 sessions flat")).toBeInTheDocument();
    const mid = screen.getByRole("region", { name: "Midstream" });
    expect(within(mid).getByText("1Y —")).toBeInTheDocument();
    expect(within(mid).getByText("no recorded call")).toBeInTheDocument();

    expect(
      screen.getByText(
        "The card's basket as of Oct 28, 2026, looking back. Not the basket as it stood on each past day.",
      ),
    ).toBeInTheDocument();
    const work = screen.getByText(/live cards counted/);
    expect(work).toHaveTextContent("equal weight, rebalanced daily, from 100");
    expect(work).toHaveTextContent("Context and Bench left out (4 basket rows)");
    expect(work).toHaveTextContent("IWM line absent: no IWM tape at this as-of");
    expect(work).toHaveTextContent("1Y window: 2 sessions, Oct 26, 2026 → Oct 28, 2026");
    expect(work).toHaveTextContent("names with a flagged price history are left out ·");
    expect(work).toHaveTextContent("cached");
    expect(screen.queryByText(/left out:/)).toBeNull(); // no flagged name: no note on any tile
  });

  it("notes a card's names left out for a flagged price history, on the mini and the enlarged view", async () => {
    const robots = card("t-robots", "Robots", {
      names: 3,
      priced: 2,
      unresolved: 0,
      flagged: [{ ticker: "RBOT", reason: "mixed-scale price history" }],
    });
    const two = card("t-two", "Two flagged", {
      flagged: [
        { ticker: "AAA", reason: "price feed stopped" },
        { ticker: "BBB", reason: "mixed-scale price history" },
      ],
    });
    setup(payload({ cards: [robots, two, CARDS[0]], flagged_companies: 3 }));
    h.useNarrativeCard.mockImplementation((_a: string, _w: string, _s: boolean, id: string | null) => ({
      data: id
        ? {
            ...detail(robots),
            members: [
              { ticker: "RBOT", security_id: "s-r", sessions: 0, first: null, last: null, flag: "mixed-scale price history" },
              { ticker: "AAA", security_id: "s-a", sessions: 2, first: "2026-10-27", last: "2026-10-28", flag: null },
            ],
          }
        : undefined,
      isLoading: false,
      error: null,
    }));

    const tile = screen.getByRole("region", { name: "Robots" });
    expect(within(tile).getByText("1 name left out: RBOT, mixed-scale price history")).toHaveClass("ni-note");
    expect(within(tile).getByText("2/3 priced")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Two flagged" })).getByText(
        "2 names left out: AAA, price feed stopped · BBB, mixed-scale price history",
      ),
    ).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Grid" })).queryByText(/left out/)).toBeNull();
    expect(screen.getByText(/live cards counted/)).toHaveTextContent(
      "names with a flagged price history are left out (3 companies)",
    );

    await userEvent.click(screen.getByRole("button", { name: "Enlarge Robots" }));
    const big = screen.getByRole("region", { name: "Robots, enlarged" });
    expect(within(big).getByText("1 name left out: RBOT, mixed-scale price history")).toBeInTheDocument();
    expect(within(big).getByText("left out: mixed-scale price history")).toBeInTheDocument();
    expect(within(big).getByText("2/2")).toBeInTheDocument();
  });

  it("shows the slow first-load message while the first read computes", () => {
    setup(undefined, { isLoading: true });
    expect(screen.getByText(/the first load after a run or a basket edit can take a minute/)).toBeInTheDocument();
  });

  it("threads the window and the Context/Bench toggle into the read", async () => {
    setup();
    expect(h.useNarrativeIndex).toHaveBeenLastCalledWith("2026-10-28", "1y", false);
    await userEvent.click(screen.getByRole("button", { name: "90D" }));
    expect(h.useNarrativeIndex).toHaveBeenLastCalledWith("2026-10-28", "90d", false);
    expect(screen.getByRole("button", { name: "90D" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "30D" }));
    expect(h.useNarrativeIndex).toHaveBeenLastCalledWith("2026-10-28", "30d", false);
    await userEvent.click(screen.getByRole("checkbox", { name: /Show Context and Bench/ }));
    expect(h.useNarrativeIndex).toHaveBeenLastCalledWith("2026-10-28", "30d", true);
  });

  it("sorts by window return, vs SPY and since creation, highest first with — last, and flips on a second pick", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Window return" }));
    expect(tileNames()).toEqual(["Grid", "alpha uranium", "Midstream"]);
    await userEvent.click(screen.getByRole("button", { name: /Window return/ }));
    expect(tileNames()).toEqual(["alpha uranium", "Grid", "Midstream"]); // reversed, — still last
    await userEvent.click(screen.getByRole("button", { name: "Return vs SPY" }));
    expect(tileNames()).toEqual(["Grid", "alpha uranium", "Midstream"]);
    await userEvent.click(screen.getByRole("button", { name: "Since creation" }));
    expect(tileNames()).toEqual(["Grid", "alpha uranium", "Midstream"]);
    await userEvent.click(screen.getByRole("button", { name: /^Name/ }));
    expect(tileNames()).toEqual(["alpha uranium", "Grid", "Midstream"]);
  });

  it("marks the creation day only on a card created inside the window", () => {
    setup();
    expect(within(screen.getByRole("region", { name: "Grid" })).getByTestId("ni-created")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "alpha uranium" })).queryByTestId("ni-created")).toBeNull();
  });

  it("enlarges a mini in place with its names, and closes back to the mini", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Enlarge Grid" }));
    const big = screen.getByRole("region", { name: "Grid, enlarged" });
    expect(h.useNarrativeCard).toHaveBeenLastCalledWith("2026-10-28", "1y", false, "t-grid");
    expect(tileNames()).toEqual(["alpha uranium", "Grid", "Midstream"]); // in place: same position
    expect(within(big).getByTestId("ni-created-big")).toHaveTextContent("created Oct 27, 2026");
    expect(within(big).getByRole("img", { name: "names contributing each session" })).toBeInTheDocument();
    expect(within(big).getByText("AAA")).toBeInTheDocument();
    expect(within(big).getByText("2/2")).toBeInTheDocument();
    expect(within(big).getByText("no resolved security")).toBeInTheDocument();
    expect(within(big).getByText("4 of 5 names contributing")).toBeInTheDocument();

    await userEvent.click(within(big).getByRole("button", { name: "Close Grid" }));
    expect(screen.queryByRole("region", { name: "Grid, enlarged" })).toBeNull();
    expect(screen.getByRole("button", { name: "Enlarge Grid" })).toBeInTheDocument();
  });

  it("opens the card in the Cockpit from its name, on the mini and the enlarged view", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Grid" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid");
    await userEvent.click(screen.getByRole("button", { name: "Enlarge alpha uranium" }));
    const big = screen.getByRole("region", { name: "alpha uranium, enlarged" });
    await userEvent.click(within(big).getByRole("button", { name: "alpha uranium" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-alpha");
  });
});
