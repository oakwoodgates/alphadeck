import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RotationQuadrant from "../RotationQuadrant";
import type { QuadrantGroupOut, QuadrantTailPointOut, RotationQuadrantOut } from "../useRotationQuadrant";

// The Rotation Quadrant body with its one hook stubbed: the plot's dots and tails (only plotted groups), the hover
// readout, a dot's click selecting its row, the table's default order and a column sort, the changed-quadrant
// filter, a row expanding into its names (a name's card opening the Cockpit) with the flagged-name note, the
// group-by and Context/Bench toggle threading their state, and the slow first-load message. The hook's request
// has its own test.

const h = vi.hoisted(() => ({ useRotationQuadrant: vi.fn() }));
vi.mock("../useRotationQuadrant", () => ({ useRotationQuadrant: h.useRotationQuadrant }));

const SESSIONS = [
  "2026-08-31",
  "2026-09-08",
  "2026-09-15",
  "2026-09-22",
  "2026-09-29",
  "2026-10-06",
  "2026-10-13",
  "2026-10-20",
  "2026-10-28",
];

const trail = (xs: number[], ys: number[]): QuadrantTailPointOut[] =>
  SESSIONS.map((session, i) => ({ session, x: xs[i], y: ys[i], quadrant: null }));

const group = (key: string, link: string, over: Partial<QuadrantGroupOut> = {}): QuadrantGroupOut => ({
  key,
  link,
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 5,
  priced: 4,
  status: "plotted",
  x: 100,
  y: 100,
  quadrant: "lagging",
  quadrant_ago: "lagging",
  tail: [],
  sessions_flat: 0,
  members: [],
  flagged: [],
  ...over,
});

const GROUPS: QuadrantGroupOut[] = [
  group("g-turb", "Turbines", {
    x: 99.2,
    y: 101.3,
    quadrant: "improving",
    quadrant_ago: "lagging",
    tail: trail([97, 97.5, 98, 98.2, 98.5, 98.8, 99, 99.1, 99.2], [98, 98.5, 99, 99.5, 100, 100.4, 100.8, 101, 101.3]),
    members: [
      {
        security_id: "s-a",
        ticker: "AAA",
        cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker: "AAA", links: ["Turbines"] }],
        priced: true,
        flag: null,
      },
      {
        security_id: "s-r",
        ticker: "RBOT",
        cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker: "RBOT", links: ["Turbines"] }],
        priced: false,
        flag: "mixed-scale price history",
      },
    ],
    flagged: [{ ticker: "RBOT", reason: "mixed-scale price history" }],
  }),
  group("g-cab", "Cables", {
    x: 103.4,
    y: 101.0,
    quadrant: "leading",
    quadrant_ago: "leading",
    tail: trail([101, 101.5, 102, 102.4, 102.8, 103, 103.1, 103.3, 103.4], [100.5, 100.6, 100.7, 100.8, 100.9, 101, 101, 101, 101]),
  }),
  group("g-fuel", "Fuel", {
    x: 101.5,
    y: 98.9,
    quadrant: "weakening",
    quadrant_ago: "leading",
    tail: trail([102, 102.2, 102.4, 102.3, 102.1, 101.9, 101.8, 101.6, 101.5], [101, 100.6, 100.2, 99.9, 99.6, 99.4, 99.2, 99, 98.9]),
  }),
  group("g-thin", "Thin", { status: "too_small", names: 2, priced: 2, x: null, y: null, quadrant: null, quadrant_ago: null }),
  group("g-new", "New", { status: "no_history", x: 100.7, y: null, quadrant: null, quadrant_ago: null }),
];

const payload = (over: Partial<RotationQuadrantOut> = {}): RotationQuadrantOut => ({
  asof: "2026-10-28",
  group_by: "link",
  include_silent: false,
  live_cards: 3,
  cards_counted: 3,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  basket_rows: 20,
  silent_rows_left_out: 4,
  unresolved_rows: 1,
  sessions: 252,
  first_session: "2025-10-27",
  last_session: "2026-10-28",
  session_source: "benchmarks",
  spy_available: true,
  companies: 14,
  priced_companies: 12,
  flagged_companies: 1,
  groups_plotted: 3,
  groups_too_small: 1,
  groups_no_history: 1,
  groups: GROUPS,
  level_sessions: 65,
  momentum_sessions: 20,
  tail_step: 5,
  tail_points: 9,
  ago_sessions: 20,
  priced_sessions: 65,
  min_priced: 3,
  cache: "hit",
  compute_ms: 900,
  ...over,
});

function setup(data: RotationQuadrantOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useRotationQuadrant.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<RotationQuadrant asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const rowLabels = () =>
  screen
    .getAllByRole("row")
    .filter((r) => r.classList.contains("rq-group"))
    .map((r) => r.querySelector(".rq-label-text")?.textContent);

const row = (label: RegExp) =>
  screen.getAllByRole("row").find((r) => r.classList.contains("rq-group") && label.test(r.textContent ?? ""))!;

beforeEach(() => {
  h.useRotationQuadrant.mockReset();
});

describe("RotationQuadrant", () => {
  it("plots one dot and one tail per plotted group, and lists every group in the table", () => {
    setup();
    const dots = screen.getAllByTestId("rq-dot");
    expect(dots.map((d) => d.getAttribute("data-key")).sort()).toEqual(["g-cab", "g-fuel", "g-turb"]);
    const tails = screen.getAllByTestId("rq-tail");
    expect(tails).toHaveLength(3);
    const turb = tails.find((t) => t.getAttribute("data-key") === "g-turb")!;
    expect(turb.getAttribute("points")?.split(" ")).toHaveLength(9);
    expect(turb).toHaveClass("rq-tail", "improving");
    expect(screen.getByRole("button", { name: "Turbines · Grid: Improving, x 99.20, y 101.30" })).toBeInTheDocument();
    // the faint quadrant labels
    for (const q of ["Leading", "Weakening", "Lagging", "Improving"]) {
      expect(within(screen.getByRole("img")).getByText(q)).toBeInTheDocument();
    }
    // the groups with no dot are still rows, with "—" and why
    expect(within(row(/Thin/)).getByText("fewer than 3 priced names")).toBeInTheDocument();
    expect(within(row(/New/)).getByText("not enough history")).toBeInTheDocument();
    expect(within(row(/New/)).getByText("100.70")).toBeInTheDocument();
    expect(within(row(/Thin/)).getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  it("shows the measures and the work on its quiet lines", () => {
    setup(payload({ roster_fallback_cards: 2, asof: "2026-09-01" }));
    expect(screen.getByText(/x = RS level/)).toHaveTextContent(
      "x = RS level: 100 × the RS ratio ÷ its own 65-session average (100 = at it) · y = RS momentum: 100 × x ÷ x 20 sessions ago (100 = flat) · tail: 9 points, every 5 sessions",
    );
    const work = screen.getByText(/live cards counted/);
    expect(work).toHaveTextContent("5 groups: 3 plotted · 1 too small (fewer than 3 priced names) · 1 not enough history");
    expect(work).toHaveTextContent("names with a flagged price history are left out (1 company)");
    expect(work).toHaveTextContent("Context and Bench left out (4 basket rows)");
    expect(work).toHaveTextContent("cached");
    expect(work).toHaveTextContent("as of Sep 1, 2026");
    expect(screen.getByText(/Today's roster stands in for 2 cards/)).toHaveTextContent("Sep 15, 2026");
    expect(screen.getByText(/Our own simple measures, not a published methodology/)).toBeInTheDocument();
  });

  it("hovering a dot shows its group, x, y, n and the quadrant 4 weeks ago → now", async () => {
    setup();
    const readout = screen.getByTestId("rq-readout");
    expect(readout).toHaveTextContent("Hover a dot");
    await userEvent.hover(screen.getByRole("button", { name: /^Turbines · Grid/ }));
    expect(readout).toHaveTextContent("Turbines · Grid");
    expect(readout).toHaveTextContent("x 99.20");
    expect(readout).toHaveTextContent("y 101.30");
    expect(readout).toHaveTextContent("n 4 priced of 5");
    expect(readout).toHaveTextContent("Lagging → Improving");
    await userEvent.unhover(screen.getByRole("button", { name: /^Turbines · Grid/ }));
    expect(readout).toHaveTextContent("Hover a dot");
  });

  it("clicking a dot selects its row in the table", async () => {
    setup();
    expect(row(/Fuel/)).toHaveAttribute("aria-selected", "false");
    await userEvent.click(screen.getByRole("button", { name: /^Fuel · Grid/ }));
    expect(row(/Fuel/)).toHaveAttribute("aria-selected", "true");
    expect(row(/Fuel/)).toHaveClass("selected");
    expect(row(/Cables/)).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("button", { name: /^Fuel · Grid/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("rq-readout")).toHaveTextContent("Leading → Weakening");
  });

  it("orders the table by quadrant then x by default, and re-sorts on a column", async () => {
    setup();
    expect(rowLabels()).toEqual(["Turbines · Grid", "Cables · Grid", "Fuel · Grid", "New · Grid", "Thin · Grid"]);
    await userEvent.click(screen.getByRole("button", { name: "x" }));
    expect(rowLabels()).toEqual(["Cables · Grid", "Fuel · Grid", "New · Grid", "Turbines · Grid", "Thin · Grid"]);
    await userEvent.click(screen.getByRole("button", { name: /^x/ }));
    expect(rowLabels()).toEqual(["Turbines · Grid", "New · Grid", "Fuel · Grid", "Cables · Grid", "Thin · Grid"]);
    // 4 weeks ago: Cables and Fuel were leading (then x highest first), Turbines lagging; "—" last
    await userEvent.click(screen.getByRole("button", { name: "4 weeks ago" }));
    expect(rowLabels()).toEqual(["Cables · Grid", "Fuel · Grid", "Turbines · Grid", "New · Grid", "Thin · Grid"]);
  });

  it("filters to the groups that changed quadrant in the last 4 weeks, in the table and the plot", async () => {
    setup();
    await userEvent.click(screen.getByRole("checkbox", { name: /Changed quadrant in the last 4 weeks/ }));
    expect(rowLabels()).toEqual(["Turbines · Grid", "Fuel · Grid"]);
    expect(screen.getAllByTestId("rq-dot").map((d) => d.getAttribute("data-key")).sort()).toEqual(["g-fuel", "g-turb"]);
    expect(screen.getByText(/live cards counted/)).toHaveTextContent("2 shown by the filter");
    await userEvent.click(screen.getByRole("checkbox", { name: /Changed quadrant in the last 4 weeks/ }));
    expect(rowLabels()).toHaveLength(5);
  });

  it("expands a row into its names; a name's card opens the Cockpit on that name, with the flagged note", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(row(/Turbines/));
    expect(row(/Turbines/)).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("1 name left out: RBOT, mixed-scale price history")).toHaveClass("rq-note");
    expect(screen.getByText("left out: mixed-scale price history")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Open Grid on AAA"));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "AAA");
    await userEvent.click(screen.getByTitle("Open Grid on RBOT"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "RBOT");
    await userEvent.click(row(/Turbines/));
    expect(screen.queryByText(/name left out/)).toBeNull();
  });

  it("threads the grouping and the Context/Bench toggle into the read", async () => {
    setup();
    expect(h.useRotationQuadrant).toHaveBeenLastCalledWith("2026-10-28", "link", false);
    await userEvent.click(screen.getByRole("button", { name: "Sector" }));
    expect(h.useRotationQuadrant).toHaveBeenLastCalledWith("2026-10-28", "sector", false);
    expect(screen.getByRole("button", { name: "Sector" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("checkbox", { name: /Show Context and Bench/ }));
    expect(h.useRotationQuadrant).toHaveBeenLastCalledWith("2026-10-28", "sector", true);
  });

  it("says why nothing is plotted when no group can be", () => {
    setup(payload({ groups: [GROUPS[3], GROUPS[4]], groups_plotted: 0, spy_available: false }));
    expect(screen.queryAllByTestId("rq-dot")).toHaveLength(0);
    expect(screen.getByText("No group in this view can be plotted.")).toBeInTheDocument();
    expect(screen.getByText(/live cards counted/)).toHaveTextContent("no SPY tape at this as-of: nothing can be measured");
  });

  it("shows the slow first-load message while the first read computes", () => {
    setup(undefined, { isLoading: true });
    expect(
      screen.getByText(/Computing every group's line — the first load after a run or a basket edit can take a minute/),
    ).toBeInTheDocument();
  });
});
