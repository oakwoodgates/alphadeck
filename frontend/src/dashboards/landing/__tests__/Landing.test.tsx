import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Landing from "../Landing";
import type {
  LandingGroupOut,
  LandingGroupWindowOut,
  LandingNameOut,
  LandingNameWindowOut,
  LandingOut,
  LandingSplitOut,
} from "../useLanding";

// The Price & Volume Landing body with its one hook stubbed: the group-by selector and the Context/Bench toggle
// threading their state, the sorts (a window's median position or its accumulation count) with "—" last both
// ways, the quadrant split rendered as counts with only accumulation / distribution toned, a row expanding
// into its names, and a name's card opening the Cockpit. The hook's request has its own test.

const h = vi.hoisted(() => ({ useLanding: vi.fn() }));
vi.mock("../useLanding", () => ({ useLanding: h.useLanding }));

const KEYS = ["1d", "7d", "30d", "90d"] as const;
type Key = (typeof KEYS)[number];

const split = (over: Partial<LandingSplitOut> = {}): LandingSplitOut => ({
  accumulation: 0,
  thin_rally: 0,
  distribution: 0,
  rest: 0,
  flat: 0,
  unread: 0,
  ...over,
});

const gwins = (
  pos7: number | null,
  over: Partial<Record<Key, Partial<LandingGroupWindowOut>>> = {},
): LandingGroupWindowOut[] =>
  KEYS.map((key) => ({
    key,
    range_median: key === "7d" ? pos7 : 50.0,
    range_n: 4,
    split: split({ accumulation: 1, rest: 3 }),
    up_down_median: 1.1,
    up_down_n: 4,
    ...over[key],
  }));

const nwins = (pos7: number | null, over: Partial<Record<Key, Partial<LandingNameWindowOut>>> = {}) =>
  KEYS.map((key) => ({
    key,
    ret: 1.0,
    range_pos: key === "7d" ? pos7 : 40.0,
    vol_ratio: 1.3,
    quadrant: "accumulation" as const,
    up_down: 1.5,
    ...over[key],
  }));

const name = (ticker: string, pos7: number | null, over: Partial<LandingNameOut> = {}): LandingNameOut => ({
  security_id: `s-${ticker}`,
  ticker,
  cards: [{ thesis_id: "t-grid", thesis_name: "Grid", ticker, links: ["Turbines"] }],
  priced: true,
  not_priced: null,
  last_bar: "2026-10-28",
  windows: nwins(pos7),
  ...over,
});

const group = (key: string, link: string, pos7: number | null, over: Partial<LandingGroupOut> = {}): LandingGroupOut => ({
  key,
  link,
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 4,
  priced: 4,
  windows: gwins(pos7),
  members: [],
  ...over,
});

const GROUPS: LandingGroupOut[] = [
  group("g-high", "Turbines", 88.0, {
    windows: gwins(88.0, { "7d": { split: split({ accumulation: 3, thin_rally: 1, rest: 1 }) } }),
    members: [
      name("LOW", 12.0, {
        windows: nwins(12.0, { "7d": { quadrant: "distribution", vol_ratio: 1.6 } }),
      }),
      name("TOP", 95.0),
      name("OFF", null, {
        priced: false,
        not_priced: "stale_tape",
        last_bar: "2026-09-30",
        windows: KEYS.map((key) => ({
          key,
          ret: null,
          range_pos: null,
          vol_ratio: null,
          quadrant: null,
          up_down: null,
        })),
      }),
    ],
  }),
  group("g-mid", "Cables", 55.0, {
    windows: gwins(55.0, { "7d": { split: split({ accumulation: 5, distribution: 2 }) } }),
  }),
  group("g-low", "Fuel", 20.0, {
    windows: gwins(20.0, { "7d": { split: split({ distribution: 4 }) } }),
  }),
  group("g-thin", "Miners", null, {
    names: 2,
    windows: gwins(null, { "7d": { range_n: 2, split: split({ unread: 2 }) } }),
  }),
];

const DATA: LandingOut = {
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
  no_tape_companies: 0,
  stale_tape_companies: 0,
  groups: GROUPS,
  windows: [
    { key: "1d", bars: 1 },
    { key: "7d", bars: 7 },
    { key: "30d", bars: 30 },
    { key: "90d", bars: 90 },
  ],
  min_priced: 3,
  rising_mult: 1.2,
  stale_gap_days: 10,
  cache: "hit",
  compute_ms: 41000,
};

const query = (data: LandingOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () => render(<Landing asof="2026-10-28" onSelectThesis={onSelectThesis} />);

/** The group rows' label cells, top to bottom. */
const labels = () =>
  screen
    .getAllByRole("row")
    .filter((r) => r.classList.contains("ld-group"))
    .map((r) => within(r).getAllByRole("cell")[0].textContent?.replace(/^[▸▾]/, ""));
const groupRow = (label: string) => {
  const row = screen
    .getAllByRole("row")
    .find((r) => r.classList.contains("ld-group") && r.textContent?.includes(label));
  if (!row) throw new Error(`no row ${label}`);
  return row;
};
/** A group row's 7D cell (label, names, 1D, 7D…). */
const sevenDay = (label: string) => within(groupRow(label)).getAllByRole("cell")[3];

beforeEach(() => {
  h.useLanding.mockReset();
  h.useLanding.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Price & Volume Landing — the table", () => {
  it("renders one row per group in the default 7D-position order, the thin group last", () => {
    renderPage();
    expect(labels()).toEqual(["Turbines · Grid", "Cables · Grid", "Fuel · Grid", "Miners · Grid"]);
    expect(screen.getByRole("columnheader", { name: /7D/ })).toHaveAttribute("aria-sort", "descending");
    expect(within(sevenDay("Turbines")).getByText("88%")).toBeInTheDocument();
    expect(within(sevenDay("Turbines")).getByText("n 4", { selector: ".ld-pos .ld-n" })).toBeInTheDocument();
    expect(within(sevenDay("Miners")).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(sevenDay("Miners")).getByText("n 2", { selector: ".ld-pos .ld-n" })).toBeInTheDocument();
  });

  it("draws the median as a thin bar and nothing for —", () => {
    renderPage();
    const fill = sevenDay("Turbines").querySelector(".ld-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("88%");
    expect(sevenDay("Miners").querySelector(".ld-bar-fill")).toBeNull();
  });

  it("renders the quadrant split as counts, zeros left out, only accum / dist toned", () => {
    renderPage();
    expect(sevenDay("Turbines").querySelector(".ld-split")).toHaveTextContent("3 accum · 1 thin · 1 rest");
    expect(within(sevenDay("Turbines")).getByText("3 accum")).toHaveClass("ld-q", "accumulation");
    expect(within(sevenDay("Turbines")).getByText("1 thin")).not.toHaveClass("thin_rally");
    expect(within(sevenDay("Turbines")).getByText("1 rest")).not.toHaveClass("rest");
    expect(within(sevenDay("Fuel")).getByText("4 dist")).toHaveClass("ld-q", "distribution");
    expect(within(sevenDay("Miners")).getByText("—", { selector: ".ld-split" })).toBeInTheDocument();
    // the full split, the unread count included, is the hover
    expect(within(sevenDay("Miners")).getByText("—", { selector: ".ld-split" })).toHaveAttribute(
      "title",
      expect.stringContaining("2 without a reading"),
    );
    expect(document.querySelectorAll(".ld-q.thin_rally, .ld-q.rest, .ld-q.flat")).toHaveLength(0);
  });

  it("shows the work on one quiet line", () => {
    renderPage();
    expect(
      screen.getByText(
        "62 live cards counted · 1,480 companies · 4 groups · 1,402 priced · Context and Bench left out " +
          "(40 basket rows) · 3 basket rows without a resolved security not counted · volume vs the prior " +
          "window of the same length; >1.2× = rising · windows in trading bars · cached · as of Oct 28, 2026",
      ),
    ).toBeInTheDocument();
  });

  it("names the not-priced companies, a fresh compute, the cards left out and today's roster standing in", () => {
    h.useLanding.mockReturnValue(
      query({
        ...DATA,
        cache: "computed",
        cards_counted: 60,
        cards_not_yet_created: 2,
        roster_fallback_cards: 60,
        no_tape_companies: 5,
        stale_tape_companies: 3,
      }),
    );
    renderPage();
    expect(screen.getByText(/60 live cards counted \(2 created after the as-of left out\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/1,402 priced \(5 without a bar, 3 stale: last bar over 10 days before the as-of\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/computed in 41 s — first load since the last run or basket edit/)).toBeInTheDocument();
    expect(screen.getByText(/Today's roster stands in for 60 cards/)).toBeInTheDocument();
  });

  it("says so while the first load computes, and on an error", () => {
    h.useLanding.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(
      screen.getByText("Computing across all cards — the first load after a run can take a minute."),
    ).toBeInTheDocument();
    unmount();
    h.useLanding.mockReturnValue(query(undefined, { error: new Error("boom") }));
    renderPage();
    expect(screen.getByText("Could not load the Price & Volume Landing.")).toBeInTheDocument();
  });

  it("keeps the last view on screen while a new as-of computes, and says so", () => {
    h.useLanding.mockReturnValue(query(DATA, { isFetching: true }));
    render(<Landing asof="2026-06-01" onSelectThesis={onSelectThesis} />);
    expect(labels()).toHaveLength(4);
    expect(screen.getByText(/Computing across all cards at the new as-of/)).toBeInTheDocument();
  });
});

describe("Price & Volume Landing — sorting", () => {
  it("a second click on the position sort flips it and keeps — last", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "7D position" }));
    expect(labels()).toEqual(["Fuel · Grid", "Cables · Grid", "Turbines · Grid", "Miners · Grid"]);
    expect(screen.getByRole("columnheader", { name: /7D/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("the accumulation sort orders by the window's count, a group with no reading last both ways", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "7D accumulation" }));
    // Cables 5 · Turbines 3 · Fuel 0 · Miners (no quadrant reading) last
    expect(labels()).toEqual(["Cables · Grid", "Turbines · Grid", "Fuel · Grid", "Miners · Grid"]);
    await user.click(screen.getByRole("button", { name: "7D accumulation" }));
    expect(labels()).toEqual(["Fuel · Grid", "Turbines · Grid", "Cables · Grid", "Miners · Grid"]);
  });

  it("another window's position sorts on its own medians; the label sorts A→Z", async () => {
    const user = userEvent.setup();
    h.useLanding.mockReturnValue(
      query({
        ...DATA,
        groups: [
          group("g-a", "Alpha", 10.0, { windows: gwins(10.0, { "90d": { range_median: 70.0 } }) }),
          group("g-b", "Beta", 90.0, { windows: gwins(90.0, { "90d": { range_median: null } }) }),
          group("g-c", "Charlie", 50.0, { windows: gwins(50.0, { "90d": { range_median: 30.0 } }) }),
        ],
      }),
    );
    renderPage();
    expect(labels()).toEqual(["Beta · Grid", "Charlie · Grid", "Alpha · Grid"]);
    await user.click(screen.getByRole("button", { name: "90D position" }));
    expect(labels()).toEqual(["Alpha · Grid", "Charlie · Grid", "Beta · Grid"]);
    await user.click(within(screen.getByRole("table")).getByRole("button", { name: "Link" }));
    expect(labels()).toEqual(["Alpha · Grid", "Beta · Grid", "Charlie · Grid"]);
  });
});

describe("Price & Volume Landing — group by and the toggle", () => {
  it("the selector asks the hook for that grouping", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "link", false);
    expect(screen.getByRole("button", { name: "Link", pressed: true })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sector" }));
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "sector", false);
    await user.click(screen.getByRole("button", { name: "Business type" }));
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "business_type", false);
    await user.click(screen.getByRole("button", { name: "Card" }));
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "card", false);
  });

  it("labels the rows per view, Unclassified for a group without a type or sector", () => {
    h.useLanding.mockReturnValue(
      query({
        ...DATA,
        group_by: "sector",
        groups: [
          group("sector:energy_utilities", "", 60.0, { link: null, thesis_name: null, supersector: "energy_utilities" }),
          group("sector:", "", 40.0, { link: null, thesis_name: null, supersector: null }),
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
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "link", true);
    await user.click(box);
    expect(h.useLanding).toHaveBeenLastCalledWith("2026-10-28", "link", false);
  });
});

describe("Price & Volume Landing — a row's names", () => {
  it("a click expands the row into its names (7D position highest first, — last) and a second click folds it", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = groupRow("Turbines");
    expect(row).toHaveAttribute("aria-expanded", "false");
    await user.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    const names = document.querySelectorAll("tr.ld-name .ld-tk");
    expect([...names].map((n) => n.textContent)).toEqual(["TOP", "LOW", "OFF"]);
    // every window's position, quadrant and volume ratio
    const low = [...document.querySelectorAll("tr.ld-name")].find((r) =>
      r.textContent?.startsWith("LOW"),
    ) as HTMLElement;
    const lowSeven = within(low).getAllByRole("cell")[3];
    expect(within(lowSeven).getByText("12%")).toBeInTheDocument();
    expect(within(lowSeven).getByText("dist")).toHaveClass("ld-chip-q", "distribution");
    expect(lowSeven).toHaveTextContent("vol 1.60×");
    expect(within(low).getAllByText("accum")).toHaveLength(3); // the other three windows
    expect(screen.getByText("stale tape · last bar Sep 30, 2026")).toBeInTheDocument();
    await user.click(row);
    expect(document.querySelectorAll("tr.ld-name")).toHaveLength(0);
  });

  it("a name's card opens that card's Cockpit on the name", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(groupRow("Turbines"));
    const top = [...document.querySelectorAll("tr.ld-name")].find((r) =>
      r.textContent?.startsWith("TOP"),
    ) as HTMLElement;
    await user.click(within(top).getByRole("button", { name: "Grid" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "TOP");
  });

  it("outside the Link view a name's card shows its links", async () => {
    const user = userEvent.setup();
    h.useLanding.mockReturnValue(
      query({
        ...DATA,
        group_by: "business_type",
        groups: [
          group("type:utilities", "", 60.0, {
            link: null,
            thesis_name: null,
            business_type: "utilities",
            members: [
              name("AAA", 60.0, {
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
