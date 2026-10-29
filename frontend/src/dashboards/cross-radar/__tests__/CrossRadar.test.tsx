import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CrossRadar from "../CrossRadar";
import type {
  CrossClusterOut,
  CrossFirstMoverOut,
  CrossGroupOut,
  CrossNearGroupOut,
  CrossRadarOut,
  CrossTimelineNameOut,
  CrossTimelineRowOut,
} from "../useCrossRadar";

// The Cross Radar body with its one hook stubbed: the four sections in order with their quiet empty lines, the
// group-by selector and the two toggles threading their state, a timeline row expanding into its names, and a
// name opening its card's Cockpit. The hook's request and the sorts have their own tests.

const h = vi.hoisted(() => ({ useCrossRadar: vi.fn() }));
vi.mock("../useCrossRadar", () => ({ useCrossRadar: h.useCrossRadar }));

const card = (ticker: string) => [{ thesis_id: "t-grid", thesis_name: "Grid", ticker, links: ["Turbines"] }];

const group = (link: string, over: Partial<CrossGroupOut> = {}): CrossGroupOut => ({
  key: `link:t-grid:${link}`,
  label: `${link} · Grid`,
  link,
  thesis_id: "t-grid",
  thesis_name: "Grid",
  business_type: null,
  supersector: null,
  names: 7,
  priced: 7,
  not_priced: 0,
  flagged: 0,
  ...over,
});

const CLUSTER: CrossClusterOut = {
  group: group("Turbines"),
  direction: "golden",
  count: 3,
  priced: 7,
  share: 3 / 7,
  within: 6,
  rs_leaders: 2,
  names: [
    { security_id: "s-aaa", ticker: "AAA", cards: card("AAA"), d: "2026-10-28", sessions_ago: 0, rs_leading: true },
    { security_id: "s-bbb", ticker: "BBB", cards: card("BBB"), d: "2026-10-21", sessions_ago: 5, rs_leading: true },
  ],
};

const MOVER: CrossFirstMoverOut = {
  group: group("Fuel", { names: 6, priced: 6 }),
  direction: "golden",
  leader: { security_id: "s-bwxt", ticker: "BWXT", cards: card("BWXT"), d: "2026-10-22", sessions_ago: 4, rs_leading: false },
  rest: 5,
  closing_in: 2,
  already: 1,
  not_yet: 2,
};

const ROLLOVER: CrossFirstMoverOut = {
  ...MOVER,
  group: group("Cables"),
  direction: "death",
  leader: { ...MOVER.leader, security_id: "s-ccc", ticker: "CCC", d: "2026-10-27", sessions_ago: 1 },
  rest: 3,
  closing_in: 0,
  already: 2,
  not_yet: 1,
};

const NEAR: CrossNearGroupOut = {
  group: group("Miners"),
  names: [
    {
      security_id: "s-mmm",
      ticker: "MMM",
      cards: card("MMM"),
      toward: "golden",
      gap_pct: -0.62,
      gap_pct_then: -1.03,
      change: 0.41,
    },
  ],
};

const tname = (ticker: string, over: Partial<CrossTimelineNameOut> = {}): CrossTimelineNameOut => ({
  security_id: `s-${ticker.toLowerCase()}`,
  ticker,
  cards: card(ticker),
  priced: true,
  not_priced: null,
  bars: 410,
  last_bar: "2026-10-28",
  readable_sessions: 90,
  crosses: [],
  price_200d: [],
  gap_pct: -3.0,
  posture: "below_rising",
  rs_leading: null,
  flag: null,
  ...over,
});

const ROW: CrossTimelineRowOut = {
  group: group("Turbines", { names: 3, priced: 2, not_priced: 1 }),
  golden: 1,
  death: 1,
  price_up: 0,
  price_down: 0,
  posture: { above_rising: 1, above_falling: 0, below_rising: 1, below_falling: 0, other: 1 },
  members: [
    tname("AAA", {
      crosses: [
        { d: "2026-10-28", direction: "golden", sessions_ago: 0, pos: 2 },
        { d: "2026-10-26", direction: "death", sessions_ago: 2, pos: 0 },
      ],
    }),
    tname("QQQ"),
    tname("THIN", { priced: false, not_priced: "short_tape", bars: 120, crosses: [], posture: "other" }),
  ],
};

const QUIET_ROW: CrossTimelineRowOut = {
  ...ROW,
  group: group("Spare", { names: 1, priced: 1 }),
  golden: 0,
  death: 0,
  members: [tname("ZZZ")],
};

const DATA: CrossRadarOut = {
  asof: "2026-10-28",
  group_by: "link",
  include_silent: false,
  price_200d: false,
  live_cards: 62,
  cards_counted: 62,
  cards_not_yet_created: 0,
  roster_fallback_cards: 0,
  basket_rows: 1600,
  silent_rows_left_out: 40,
  unresolved_rows: 3,
  companies: 1480,
  priced_companies: 1402,
  short_tape_companies: 70,
  stale_tape_companies: 8,
  sessions: ["2026-10-26", "2026-10-27", "2026-10-28"],
  clusters: [CLUSTER],
  first_movers: [MOVER, ROLLOVER],
  near_cross: [NEAR],
  timeline: [QUIET_ROW, ROW],
  flagged: [],
  recent_sessions: 10,
  history_sessions: 90,
  cluster_min_names: 2,
  cluster_min_share_pct: 40,
  first_mover_min_priced: 2,
  near_band_pct: 2,
  near_lookback: 5,
  stale_gap_days: 10,
  cache: "hit",
  compute_ms: 16000,
};

const EMPTY: CrossRadarOut = { ...DATA, clusters: [], first_movers: [], near_cross: [], timeline: [] };

const query = (data: CrossRadarOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () => render(<CrossRadar asof="2026-10-28" onSelectThesis={onSelectThesis} />);
const section = (name: string) => screen.getByRole("region", { name });
const timelineRows = () =>
  screen
    .getAllByRole("row")
    .filter((r) => r.classList.contains("xr-tl"))
    .map((r) => within(r).getAllByRole("cell")[0].textContent?.replace(/^[▸▾]/, ""));

beforeEach(() => {
  h.useCrossRadar.mockReset();
  h.useCrossRadar.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Cross Radar — the sections", () => {
  it("renders the four sections loudest first", () => {
    renderPage();
    const heads = screen.getAllByRole("heading", { level: 2 }).map((e) => e.textContent);
    expect(heads).toEqual(["Clusters", "First movers", "Near-cross watch", "Cross timeline"]);
  });

  it("a cluster row shows its pieces, with no score", () => {
    renderPage();
    const row = within(section("Clusters")).getAllByRole("row")[1];
    expect(row).toHaveClass("xr-cluster", "golden");
    expect(within(row).getByText("Turbines · Grid")).toBeInTheDocument();
    expect(within(row).getByText("3 of 7 golden ↑")).toBeInTheDocument();
    expect(within(row).getByText("43%")).toBeInTheDocument();
    expect(within(row).getByText("within 6 sessions")).toBeInTheDocument();
    expect(within(row).getByText("2 are RS leaders")).toBeInTheDocument();
    expect(within(row).getByText("Oct 28")).toBeInTheDocument();
  });

  it("first movers name the leader and count the rest; the death mirror says first to roll over", () => {
    renderPage();
    const rows = within(section("First movers")).getAllByRole("row").slice(1);
    // newest cross first: the Oct 27 roll-over, then the Oct 22 golden
    expect(within(rows[0]).getByText("CCC")).toBeInTheDocument();
    expect(within(rows[0]).getByText("first to roll over")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2 already below")).toBeInTheDocument();
    expect(within(rows[1]).getByText("BWXT")).toBeInTheDocument();
    expect(within(rows[1]).getByText("4 sessions ago")).toBeInTheDocument();
    expect(within(rows[1]).getByText("2 of 5 closing in")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1 already above")).toBeInTheDocument();
    expect(within(rows[1]).getByText("2 not yet")).toBeInTheDocument();
  });

  it("the near-cross watch lists each name under its group with its gap and 5-session change", () => {
    renderPage();
    const near = section("Near-cross watch");
    expect(within(near).getByText("Miners · Grid")).toBeInTheDocument();
    expect(within(near).getByText("approaching golden")).toBeInTheDocument();
    expect(within(near).getByText("-0.62%")).toBeInTheDocument();
    expect(within(near).getByText("+0.41 pts")).toBeInTheDocument();
    expect(within(near).queryByText(/days|forecast in/)).toBeNull();
  });

  it("the timeline sorts by total crosses and draws one tick per cross beside a posture bar", () => {
    renderPage();
    expect(timelineRows()[0]).toMatch(/^Turbines · Grid/);
    expect(timelineRows()[1]).toMatch(/^Spare · Grid/);
    const tl = section("Cross timeline");
    const strip = within(tl).getByRole("img", { name: "Turbines · Grid: 1 golden, 1 death" });
    expect(strip.querySelectorAll("line.xr-tick.golden")).toHaveLength(1);
    expect(strip.querySelectorAll("line.xr-tick.death")).toHaveLength(1);
    expect(strip.querySelectorAll("line.xr-tick.layer")).toHaveLength(0);
    expect(tl.querySelectorAll(".xr-posture")).toHaveLength(2);
    expect(within(tl).getAllByText("1 not priced", { exact: false }).length).toBeGreaterThan(0);
  });

  it("shows the work on one quiet line", () => {
    renderPage();
    expect(
      screen.getByText(
        "62 live cards counted · 1,480 companies · 1,402 priced · 78 not priced (70 too few bars, 8 stale tape) · " +
          "2 groups · Context and Bench left out (40 basket rows) · 3 basket rows without a resolved security not " +
          "counted · price × 200d ticks off · cluster: ≥ 2 names and ≥ 40% of the priced names within 10 sessions · " +
          "near cross: within ±2% and narrowing over 5 sessions · cached · as of Oct 28, 2026",
      ),
    ).toBeInTheDocument();
  });

  it("says so quietly when a section is empty", () => {
    h.useCrossRadar.mockReturnValue(query(EMPTY));
    renderPage();
    expect(screen.getByText(/No group has 2\+ names and 40%\+ of its priced names/)).toBeInTheDocument();
    expect(screen.getByText("No group has exactly one name crossing within 10 sessions.")).toBeInTheDocument();
    expect(screen.getByText("No name is within ±2% of a cross and narrowing.")).toBeInTheDocument();
    expect(screen.getByText("No companies in this view.")).toBeInTheDocument();
    expect(screen.queryAllByRole("table")).toHaveLength(0);
  });

  it("says so while the first load computes, and on an error, and notes today's roster standing in", () => {
    h.useCrossRadar.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(
      screen.getByText("Computing across all cards — the first load after a run can take a minute."),
    ).toBeInTheDocument();
    unmount();
    h.useCrossRadar.mockReturnValue(query(undefined, { error: new Error("boom") }));
    const again = renderPage();
    expect(screen.getByText("Could not load the Cross Radar.")).toBeInTheDocument();
    again.unmount();
    h.useCrossRadar.mockReturnValue(query({ ...DATA, roster_fallback_cards: 60, cache: "computed" }));
    renderPage();
    expect(screen.getByText(/Today's roster stands in for 60 cards/)).toBeInTheDocument();
    expect(screen.getByText(/computed in 16 s — first load since the last run or basket edit/)).toBeInTheDocument();
  });
});

describe("Cross Radar — group by and toggles", () => {
  it("the selector asks the hook for that grouping", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "link", false, false);
    await user.click(screen.getByRole("button", { name: "Sector" }));
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "sector", false, false);
    await user.click(screen.getByRole("button", { name: "Business type" }));
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "business_type", false, false);
    await user.click(screen.getByRole("button", { name: "Card" }));
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "card", false, false);
  });

  it("Show Context and Bench and the price × 200d ticks thread their params, and back", async () => {
    const user = userEvent.setup();
    renderPage();
    const silent = screen.getByRole("checkbox", { name: "Show Context and Bench" });
    const price = screen.getByRole("checkbox", { name: "Price × 200d ticks" });
    expect(price).not.toBeChecked();
    await user.click(silent);
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "link", true, false);
    await user.click(price);
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "link", true, true);
    await user.click(silent);
    await user.click(price);
    expect(h.useCrossRadar).toHaveBeenLastCalledWith("2026-10-28", "link", false, false);
  });

  it("with the price layer on, its ticks draw lighter on the timeline only", () => {
    const layered: CrossTimelineRowOut = {
      ...ROW,
      price_up: 1,
      members: [
        tname("AAA", {
          crosses: ROW.members[0].crosses,
          price_200d: [{ d: "2026-10-27", direction: "up", sessions_ago: 1, pos: 1 }],
        }),
      ],
    };
    h.useCrossRadar.mockReturnValue(query({ ...DATA, price_200d: true, timeline: [layered] }));
    renderPage();
    const strip = within(section("Cross timeline")).getByRole("img", { name: /Turbines · Grid/ });
    expect(strip.querySelectorAll("line.xr-tick.layer.up")).toHaveLength(1);
    expect(within(section("Clusters")).getByText("3 of 7 golden ↑")).toBeInTheDocument();
    expect(screen.getByText(/price × 200d ticks on/)).toBeInTheDocument();
  });
});

describe("Cross Radar — a timeline row's names", () => {
  it("a click expands the row into its names with their cross dates, and a second click folds it", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = screen.getAllByRole("row").find((r) => r.classList.contains("xr-tl") && r.textContent?.includes("Turbines"));
    if (!row) throw new Error("no Turbines row");
    expect(row).toHaveAttribute("aria-expanded", "false");
    await user.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    const names = [...document.querySelectorAll("tr.xr-tl-name .xr-tk")].map((n) => n.textContent);
    expect(names).toEqual(["AAA", "QQQ", "THIN"]);
    expect(screen.getByText("golden Oct 28 · death Oct 26")).toBeInTheDocument();
    expect(screen.getByText("no cross in 90 sessions")).toBeInTheDocument();
    expect(screen.getByText("not priced: 120 bars, a 200-day SMA needs 200")).toBeInTheDocument();
    await user.click(row);
    expect(document.querySelectorAll("tr.xr-tl-name")).toHaveLength(0);
  });

  it("a name opens its card's Cockpit on the name", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = screen.getAllByRole("row").find((r) => r.classList.contains("xr-tl") && r.textContent?.includes("Turbines"));
    await user.click(row as HTMLElement);
    const aaa = [...document.querySelectorAll("tr.xr-tl-name")].find((r) => r.textContent?.startsWith("AAA")) as HTMLElement;
    await user.click(within(aaa).getByRole("button", { name: "AAA" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "AAA");
  });

  it("cluster, first-mover and near-cross names open their cards too", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(within(section("Clusters")).getByRole("button", { name: "BBB" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "BBB");
    await user.click(within(section("First movers")).getByRole("button", { name: "BWXT" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "BWXT");
    await user.click(within(section("Near-cross watch")).getByRole("button", { name: "MMM" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "MMM");
  });
});

describe("Cross Radar — a flagged price history", () => {
  const MIXED = "mixed-scale price history";
  const FLAGGED: CrossRadarOut = {
    ...DATA,
    priced_companies: 1401,
    flagged: [{ security_id: "s-rbot", ticker: "RBOT", reason: MIXED }],
    timeline: [
      QUIET_ROW,
      {
        ...ROW,
        group: group("Turbines", { names: 4, priced: 2, not_priced: 1, flagged: 1 }),
        members: [
          ...ROW.members,
          tname("RBOT", { priced: false, not_priced: null, bars: 0, last_bar: null, gap_pct: null, posture: "other", flag: MIXED }),
        ],
      },
    ],
  };

  it("names the left-out companies on one quiet line that opens to each ticker and reason", async () => {
    const user = userEvent.setup();
    h.useCrossRadar.mockReturnValue(query(FLAGGED));
    renderPage();
    const note = screen.getByText("1 name left out for a flagged price history");
    expect(note.closest("details")).not.toHaveAttribute("open");
    await user.click(note);
    expect(note.closest("details")).toHaveAttribute("open");
    const item = screen.getByText("RBOT").closest("li") as HTMLElement;
    expect(item.textContent).toBe(`RBOT ${MIXED}`);
    // the work line's "not priced" never counts a left-out name: 1,480 - 1,401 priced - 1 left out
    expect(screen.getByText(/· 78 not priced \(70 too few bars, 8 stale tape\) ·/)).toBeInTheDocument();
  });

  it("an expanded group lists the flagged name grayed with its reason, never gone", async () => {
    const user = userEvent.setup();
    h.useCrossRadar.mockReturnValue(query(FLAGGED));
    renderPage();
    const row = screen.getAllByRole("row").find((r) => r.classList.contains("xr-tl") && r.textContent?.includes("Turbines"));
    if (!row) throw new Error("no Turbines row");
    expect(row.textContent).toContain("4 names · 1 not priced · 1 left out");
    await user.click(row);
    const rbot = [...document.querySelectorAll("tr.xr-tl-name")].find((r) => r.textContent?.startsWith("RBOT"));
    expect(rbot).toHaveClass("flagged");
    expect(within(rbot as HTMLElement).getByText(`left out: ${MIXED}`)).toBeInTheDocument();
    expect((rbot as HTMLElement).querySelector("svg")).toBeNull(); // no strip: no crosses drawn
  });

  it("shows no note when nothing is left out", () => {
    renderPage();
    expect(screen.queryByText(/left out for a flagged price history/)).toBeNull();
  });
});
