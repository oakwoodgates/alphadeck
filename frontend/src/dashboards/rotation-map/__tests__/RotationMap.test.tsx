import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RotationMap from "../RotationMap";
import type {
  RotationGroupOut,
  RotationMapOut,
  RotationNameOut,
  RotationWindowOut,
} from "../useRotationMap";

// The Rotation Map body with its one hook stubbed: the group-by selector and the two toggles threading their
// state, the column sort with "—" last both ways, the server's tones becoming the only colored cells, a row
// expanding into its names, and a name's card opening the Cockpit. The hook's request has its own test.

const h = vi.hoisted(() => ({ useRotationMap: vi.fn() }));
vi.mock("../useRotationMap", () => ({ useRotationMap: h.useRotationMap }));

const KEYS = ["ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y"] as const;

const wins = (
  r7: number | null,
  over: Partial<Record<(typeof KEYS)[number], Partial<RotationWindowOut>>> = {},
): RotationWindowOut[] =>
  KEYS.map((key) => ({
    key,
    median: key === "ret_7d" ? r7 : 1.0,
    n: 4,
    up: 3,
    above_spy: 2,
    tone: null,
    ...over[key],
  }));

const name = (ticker: string, over: Partial<RotationNameOut> = {}): RotationNameOut => ({
  security_id: `s-${ticker}`,
  ticker,
  cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker, links: ["Turbines"] }],
  returns: { ret_1d: 0.5, ret_7d: 2.0, ret_30d: null, ret_90d: null, ret_1y: null },
  above_50d: true,
  rvol20: 1.2,
  cross: null,
  cross_date: null,
  rs_leading: null,
  ...over,
});

const group = (key: string, link: string, r7: number | null, over: Partial<RotationGroupOut> = {}): RotationGroupOut => ({
  key,
  link,
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 4,
  windows: wins(r7),
  above_50d: 2,
  above_50d_n: 4,
  rvol20_median: 1.1,
  rvol20_n: 4,
  golden: 0,
  death: 0,
  rs_leaders: 0,
  rs_n: 4,
  members: [],
  ...over,
});

const GROUPS: RotationGroupOut[] = [
  group("g-hot", "Turbines", 9.0, {
    windows: wins(9.0, { ret_7d: { tone: "pos" } }),
    golden: 2,
    members: [
      name("LOW", { returns: { ret_1d: 0.1, ret_7d: 1.0, ret_30d: null, ret_90d: null, ret_1y: null } }),
      name("HIGH", {
        returns: { ret_1d: 0.1, ret_7d: 12.0, ret_30d: null, ret_90d: null, ret_1y: null },
        cross: "golden",
        cross_date: "2026-05-20",
      }),
    ],
  }),
  group("g-mid", "Cables", 3.0, { death: 1, names: 3 }),
  group("g-cold", "Fuel", -4.0, { windows: wins(-4.0, { ret_7d: { tone: "neg" } }) }),
  group("g-thin", "Miners", null, {
    windows: wins(null, { ret_7d: { n: 2, up: 1 } }),
    names: 2,
  }),
];

const DATA: RotationMapOut = {
  asof: "2026-10-28",
  group_by: "link",
  include_silent: false,
  live_cards: 62,
  cards_counted: 62,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  basket_rows: 1600,
  silent_rows_left_out: 40,
  unresolved_rows: 3,
  companies: 1480,
  priced_companies: 1402,
  spy: { ret_1d: 0.2, ret_7d: 1.0, ret_30d: 2.0, ret_90d: 3.0, ret_1y: 10.0 },
  groups: GROUPS,
  min_priced: 3,
  cross_days: 30,
  cache: "hit",
  compute_ms: 41000,
};

const query = (data: RotationMapOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () => render(<RotationMap asof="2026-10-28" onSelectThesis={onSelectThesis} />);

/** The group rows' label cells, top to bottom. */
const labels = () =>
  screen
    .getAllByRole("row")
    .filter((r) => r.classList.contains("rm-group"))
    .map((r) => within(r).getAllByRole("cell")[0].textContent?.replace(/^[▸▾]/, ""));
const groupRow = (label: string) => {
  const row = screen
    .getAllByRole("row")
    .find((r) => r.classList.contains("rm-group") && r.textContent?.includes(label));
  if (!row) throw new Error(`no row ${label}`);
  return row;
};
const sevenDay = (label: string) => within(groupRow(label)).getAllByRole("cell")[3];

beforeEach(() => {
  h.useRotationMap.mockReset();
  h.useRotationMap.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Rotation Map — the table", () => {
  it("renders one row per group in the default 7d-median order, the thin group last", () => {
    renderPage();
    expect(labels()).toEqual(["Turbines · Grid", "Cables · Grid", "Fuel · Grid", "Miners · Grid"]);
    expect(screen.getByRole("columnheader", { name: /7d median/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(within(sevenDay("Miners")).getByText("—")).toBeInTheDocument();
    expect(within(sevenDay("Miners")).getByText("1/2 up")).toBeInTheDocument();
    expect(within(sevenDay("Turbines")).getByText("+9.0%")).toBeInTheDocument();
    expect(within(sevenDay("Turbines")).getByText("3/4 up")).toBeInTheDocument();
  });

  it("colors only the cells the server toned; everything else and every — stays gray", () => {
    renderPage();
    expect(within(sevenDay("Turbines")).getByText("+9.0%")).toHaveClass("rm-ret", "pos");
    expect(within(sevenDay("Fuel")).getByText("-4.0%")).toHaveClass("rm-ret", "neg");
    const mid = within(sevenDay("Cables")).getByText("+3.0%");
    expect(mid).not.toHaveClass("pos");
    expect(mid).not.toHaveClass("neg");
    expect(within(sevenDay("Miners")).getByText("—")).not.toHaveClass("pos");
    expect(document.querySelectorAll(".rm-ret.pos, .rm-ret.neg")).toHaveLength(2);
    expect(screen.getByText(/top \/ bottom 10% of the groups shown/)).toBeInTheDocument();
  });

  it("shows the work on one quiet line", () => {
    renderPage();
    expect(
      screen.getByText(
        "62 live cards counted · 1,480 companies · 4 groups · 1,402 priced · Context and Bench left out " +
          "(40 basket rows) · 3 basket rows without a resolved security not counted · returns raw · cached · " +
          "as of Oct 28, 2026",
      ),
    ).toBeInTheDocument();
  });

  it("names a fresh compute and the cards left out, and notes today's roster standing in", () => {
    h.useRotationMap.mockReturnValue(
      query({
        ...DATA,
        cache: "computed",
        cards_counted: 60,
        cards_not_yet_created: 2,
        roster_fallback_cards: 60,
      }),
    );
    renderPage();
    expect(screen.getByText(/60 live cards counted \(2 created after the as-of left out\)/)).toBeInTheDocument();
    expect(screen.getByText(/computed in 41 s — first load since the last run or basket edit/)).toBeInTheDocument();
    expect(screen.getByText(/Today's roster stands in for 60 cards/)).toBeInTheDocument();
  });

  it("says so while the first load computes, and on an error", () => {
    h.useRotationMap.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(
      screen.getByText("Computing across all cards — the first load after a run can take a minute."),
    ).toBeInTheDocument();
    unmount();
    h.useRotationMap.mockReturnValue(query(undefined, { error: new Error("boom") }));
    renderPage();
    expect(screen.getByText("Could not load the Rotation Map.")).toBeInTheDocument();
  });

  it("keeps the last view on screen while a new as-of computes, and says so", () => {
    h.useRotationMap.mockReturnValue(query(DATA, { isFetching: true }));
    render(<RotationMap asof="2026-06-01" onSelectThesis={onSelectThesis} />);
    expect(labels()).toHaveLength(4);
    expect(screen.getByText(/Computing across all cards at the new as-of/)).toBeInTheDocument();
  });
});

describe("Rotation Map — sorting", () => {
  it("a header click flips the order and keeps — last in both directions", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "7d median" }));
    expect(labels()).toEqual(["Fuel · Grid", "Cables · Grid", "Turbines · Grid", "Miners · Grid"]);
    expect(screen.getByRole("columnheader", { name: /7d median/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("another column sorts highest first; counts and names sort too", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Golden 30d" }));
    expect(labels()[0]).toBe("Turbines · Grid");
    await user.click(screen.getByRole("button", { name: "Death 30d" }));
    expect(labels()[0]).toBe("Cables · Grid");
    await user.click(within(screen.getByRole("table")).getByRole("button", { name: "Link" }));
    expect(labels()).toEqual(["Cables · Grid", "Fuel · Grid", "Miners · Grid", "Turbines · Grid"]);
  });
});

describe("Rotation Map — group by and toggles", () => {
  it("the selector asks the hook for that grouping", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "link", false);
    expect(screen.getByRole("button", { name: "Link", pressed: true })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sector" }));
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "sector", false);
    await user.click(screen.getByRole("button", { name: "Business type" }));
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "business_type", false);
    await user.click(screen.getByRole("button", { name: "Card" }));
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "card", false);
  });

  it("labels the rows per view, Unclassified for a group without a type or sector", () => {
    h.useRotationMap.mockReturnValue(
      query({
        ...DATA,
        group_by: "sector",
        groups: [
          group("sector:energy_utilities", "", 2.0, { link: null, thesis_name: null, supersector: "energy_utilities" }),
          group("sector:", "", 1.0, { link: null, thesis_name: null, supersector: null }),
        ],
      }),
    );
    renderPage();
    expect(labels()).toEqual(["Energy & Utilities", "Unclassified"]);
  });

  it("Show Context and Bench threads include_silent, and back", async () => {
    const user = userEvent.setup();
    renderPage();
    const box = screen.getByRole("checkbox", { name: "Show Context and Bench" });
    await user.click(box);
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "link", true);
    await user.click(box);
    expect(h.useRotationMap).toHaveBeenLastCalledWith("2026-10-28", "link", false);
  });

  it("vs SPY subtracts SPY's own return per window and reads x/n above SPY", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "vs SPY" }));
    expect(within(sevenDay("Turbines")).getByText("+8.0%")).toHaveClass("pos"); // 9.0 − SPY 1.0
    expect(within(sevenDay("Turbines")).getByText("2/4 > SPY")).toBeInTheDocument();
    expect(within(sevenDay("Miners")).getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/returns vs SPY/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Raw" }));
    expect(within(sevenDay("Turbines")).getByText("+9.0%")).toBeInTheDocument();
  });

  it("vs SPY is disabled with a quiet reason when there is no SPY tape", () => {
    h.useRotationMap.mockReturnValue(query({ ...DATA, spy: null }));
    renderPage();
    expect(screen.getByRole("button", { name: "vs SPY" })).toBeDisabled();
    expect(screen.getByText("no SPY tape at this as-of")).toBeInTheDocument();
  });
});

describe("Rotation Map — a row's names", () => {
  it("a click expands the row into its names (7d highest first) and a second click folds it", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = groupRow("Turbines");
    expect(row).toHaveAttribute("aria-expanded", "false");
    await user.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    const names = document.querySelectorAll("tr.rm-name .rm-tk");
    expect([...names].map((n) => n.textContent)).toEqual(["HIGH", "LOW"]);
    expect(screen.getByTitle(/golden cross: 50d crossed above 200d on/)).toHaveTextContent("↑");
    await user.click(row);
    expect(document.querySelectorAll("tr.rm-name")).toHaveLength(0);
  });

  it("a name's card opens that card's Cockpit on the name", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(groupRow("Turbines"));
    const high = [...document.querySelectorAll("tr.rm-name")].find((r) =>
      r.textContent?.startsWith("HIGH"),
    ) as HTMLElement;
    await user.click(within(high).getByRole("button", { name: "Grid" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "HIGH");
  });

  it("outside the Link view a name's card shows its links", async () => {
    const user = userEvent.setup();
    h.useRotationMap.mockReturnValue(
      query({
        ...DATA,
        group_by: "business_type",
        groups: [
          group("type:utilities", "", 2.0, {
            link: null,
            thesis_name: null,
            business_type: "utilities",
            members: [
              name("AAA", {
                cards: [
                  { thesis_id: "t-grid", thesis_name: "Grid", ticker: "AAA", links: ["Turbines", null] },
                  { thesis_id: "t-nuc", thesis_name: "Nuclear", ticker: "AAA", links: ["Fuel"] },
                ],
              }),
            ],
          }),
        ],
      }),
    );
    renderPage();
    await user.click(groupRow("utilities"));
    expect(screen.getByRole("button", { name: "Grid · Turbines, Unsegmented" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Nuclear · Fuel" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-nuc", "AAA");
  });
});
