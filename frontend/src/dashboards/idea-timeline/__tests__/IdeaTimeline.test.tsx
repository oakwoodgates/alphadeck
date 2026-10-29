import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IdeaTimeline from "../IdeaTimeline";
import type {
  IdeaTimelineOut,
  IdeaTimelineRowOut,
  IdeaTimelineSegmentOut,
} from "../useIdeaTimeline";

// The Idea Timeline body with its one hook stubbed: segments in the Board's state classes with their hovers,
// the zoom presets, the state filter chips, the sorts, the markers (catalysts, decisions, clocks) with their
// hovers, and the card name opening the Cockpit. The hook's request has its own test (useIdeaTimeline.test.tsx).

const h = vi.hoisted(() => ({ useIdeaTimeline: vi.fn() }));
vi.mock("../useIdeaTimeline", () => ({ useIdeaTimeline: h.useIdeaTimeline }));

const seg = (over: Partial<IdeaTimelineSegmentOut>): IdeaTimelineSegmentOut => ({
  state: "incubating",
  verdict: "watching",
  start: "2026-07-10",
  end: "2026-09-01",
  days: 53,
  current: false,
  nights: 30,
  hatches: [],
  multi_row_nights: [],
  ...over,
});

const row = (over: Partial<IdeaTimelineRowOut>): IdeaTimelineRowOut => ({
  thesis_id: "t-x",
  thesis_name: "X",
  current_state: null,
  current_verdict: null,
  days_in_state: null,
  total_days_armed: 0,
  times_armed: 0,
  first_recorded: null,
  last_recorded: null,
  recorded_calls: 0,
  nights: 0,
  segments: [],
  catalysts: [],
  undated_catalysts: 0,
  decisions: [],
  exit_by: null,
  arm_until: null,
  ...over,
});

const GRID = row({
  thesis_id: "t-grid",
  thesis_name: "Grid",
  current_state: "armed",
  current_verdict: "starter_entry",
  days_in_state: 22,
  total_days_armed: 22,
  times_armed: 1,
  first_recorded: "2026-07-10",
  last_recorded: "2026-10-28",
  recorded_calls: 60,
  nights: 58,
  segments: [
    seg({}),
    seg({
      state: "warming",
      verdict: "not_yet",
      start: "2026-09-01",
      end: "2026-10-07",
      days: 36,
      nights: 20,
      hatches: [
        { kind: "reconstructed", start: "2026-09-08", end: "2026-09-10", nights: 2, reason: null },
      ],
    }),
    seg({
      state: "armed",
      verdict: "starter_entry",
      start: "2026-10-07",
      end: "2026-10-29",
      days: 22,
      current: true,
      nights: 8,
      hatches: [
        {
          kind: "excluded",
          start: "2026-10-07",
          end: "2026-10-08",
          nights: 1,
          reason: "stale_comparative_revenue",
        },
      ],
      multi_row_nights: [{ asof: "2026-10-09", rows: 3 }],
    }),
  ],
  catalysts: [
    { label: "Q2 print", kind: "earnings", when_date: "2026-08-01", when_label: null, past: true },
    { label: "PDUFA", kind: "regulatory", when_date: "2026-11-10", when_label: "~2wk", past: false },
    { label: "Readout", kind: null, when_date: "2026-12-15", when_label: null, past: false },
  ],
  undated_catalysts: 2,
  decisions: [
    {
      id: "d-pass",
      action: "pass",
      decision_date: "2026-09-02",
      security_id: null,
      ticker: null,
      reason: "too early",
      voided: false,
    },
    {
      id: "d-take",
      action: "take",
      decision_date: "2026-10-08",
      security_id: "s-hims",
      ticker: "HIMS",
      reason: null,
      voided: false,
    },
    {
      id: "d-close",
      action: "close",
      decision_date: "2026-10-09",
      security_id: "s-hims",
      ticker: "HIMS",
      reason: null,
      voided: true,
    },
  ],
  exit_by: "2026-11-29",
  arm_until: "2026-11-05",
});

const NUCLEAR = row({
  thesis_id: "t-nuc",
  thesis_name: "Nuclear",
  current_state: "warming",
  current_verdict: "not_yet",
  days_in_state: 40,
  total_days_armed: 30,
  times_armed: 2,
  first_recorded: "2026-08-01",
  last_recorded: "2026-10-28",
  segments: [
    seg({ state: "armed", start: "2026-08-01", end: "2026-09-19", days: 49 }),
    seg({ state: "warming", start: "2026-09-19", end: "2026-10-29", days: 40, current: true }),
  ],
});

const URANIUM = row({
  thesis_id: "t-ura",
  thesis_name: "Uranium",
  current_state: "incubating",
  current_verdict: "watching",
  days_in_state: 100,
  first_recorded: "2026-07-21",
  last_recorded: "2026-10-28",
  segments: [seg({ start: "2026-07-21", end: "2026-10-29", days: 100, current: true })],
});

const BIOTECH = row({
  thesis_id: "t-bio",
  thesis_name: "biotech",
  current_state: "armed",
  current_verdict: "core_entry",
  days_in_state: 5,
  total_days_armed: 5,
  times_armed: 1,
  first_recorded: "2026-10-24",
  last_recorded: "2026-10-28",
  segments: [
    seg({ state: "armed", start: "2026-10-24", end: "2026-10-29", days: 5, current: true }),
  ],
});

const NEW = row({ thesis_id: "t-new", thesis_name: "Brand New" });

const DATA: IdeaTimelineOut = {
  today: "2026-10-29",
  axis_start: "2026-07-10",
  axis_end: "2026-11-30",
  future_cap: "2027-01-27",
  live_cards: 5,
  cards_without_calls: 1,
  recorded_calls: 994,
  nights: 880,
  multi_row_nights: 12,
  reconstructed_nights: 8,
  excluded_nights: 3,
  first_recorded: "2026-07-10",
  last_recorded: "2026-10-28",
  // the payload's order (by name); the page sorts
  rows: [BIOTECH, NEW, GRID, NUCLEAR, URANIUM],
};

const query = (data: IdeaTimelineOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () =>
  render(<IdeaTimeline asof="2026-10-29" onSelectThesis={onSelectThesis} />);

const table = () => screen.getByRole("table");
const names = () =>
  within(table())
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("button")[0].textContent);
const rowOf = (name: string) => {
  const r = within(table())
    .getAllByRole("row")
    .find((tr) => within(tr).queryByRole("button", { name })?.textContent === name);
  if (!r) throw new Error(`no row for ${name}`);
  return r;
};

beforeEach(() => {
  h.useIdeaTimeline.mockReset();
  h.useIdeaTimeline.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Idea Timeline — rows and segments", () => {
  it("draws one segment per run in the Board's state classes, with the run's hover", () => {
    renderPage();
    const grid = rowOf("Grid");
    const segs = grid.querySelectorAll(".it-seg");
    expect([...segs].map((s) => s.className)).toEqual([
      "it-seg st-incub",
      "it-seg st-warm",
      "it-seg st-armed current",
    ]);

    const armed = within(grid).getByTitle(/^Armed · verdict at start: STARTER entry/);
    expect(armed.getAttribute("title")).toContain("Oct 7, 2026 → today (Oct 29, 2026) · 22 days");
    expect(armed.getAttribute("title")).toContain("8 nights recorded");
    expect(armed.getAttribute("title")).toContain(
      "Oct 9, 2026: 3 rows that night (the last one is read)",
    );
    expect(armed.getAttribute("title")).toContain(
      "set aside on the Scoreboard: stale_comparative_revenue",
    );
    const warming = within(grid).getByTitle(/^Warming · verdict at start: Not yet/);
    expect(warming.getAttribute("title")).toContain("Sep 1, 2026 → Oct 7, 2026 · 36 days");
    expect(warming.getAttribute("title")).toContain("reconstructed: reported, never scored");

    // the hatches lie over their nights: reconstructed and set-aside are distinct classes
    expect(grid.querySelectorAll(".it-hatch.reconstructed")).toHaveLength(1);
    expect(grid.querySelectorAll(".it-hatch.excluded")).toHaveLength(1);
  });

  it("fills the left columns from the payload", () => {
    renderPage();
    const cells = within(rowOf("Grid"))
      .getAllByRole("cell")
      .slice(1, 6)
      .map((c) => c.textContent);
    expect(cells).toEqual(["Armed", "22", "22", "1", "Jul 10"]);
    expect(within(rowOf("Grid")).getByText("Armed")).toHaveClass("it-state", "st-armed");
  });

  it("keeps a card with no recorded call as a row with no segments", () => {
    renderPage();
    const r = rowOf("Brand New");
    expect(r.querySelectorAll(".it-seg")).toHaveLength(0);
    expect(within(r).getByText("No call yet")).toBeInTheDocument();
    expect(within(r).getAllByText("—")).toHaveLength(2); // days in state, first recorded
  });

  it("opens the card's Cockpit from its name", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Nuclear" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-nuc");
    expect(onSelectThesis.mock.calls[0]).toHaveLength(1);
  });

  it("states the work and the reading rules on the quiet line", () => {
    renderPage();
    const line = screen.getByText(/live cards/);
    expect(line.textContent).toContain("5 live cards (1 with no recorded call yet)");
    expect(line.textContent).toContain("994 recorded calls on 880 card-nights");
    expect(line.textContent).toContain("Jul 10, 2026 → Oct 28, 2026");
    expect(line.textContent).toContain("a state holds from its night until the card's next row");
    expect(line.textContent).toContain("a night with several rows reads its last one (12 nights)");
    expect(line.textContent).toContain("reconstructed, reported, never scored (8 nights)");
    expect(line.textContent).toContain("set aside on the Scoreboard (3 nights)");
  });
});

describe("Idea Timeline — markers", () => {
  it("draws catalysts as ticks, muted when past, with their label on hover", () => {
    renderPage();
    const grid = rowOf("Grid");
    const past = within(grid).getByTitle(/^Catalyst: Q2 print/);
    expect(past).toHaveClass("it-cat", "past");
    const next = within(grid).getByTitle("Catalyst: PDUFA · Nov 10, 2026 (~2wk) · regulatory");
    expect(next).toHaveClass("it-cat");
    expect(next).not.toHaveClass("past");
    // the Dec 15 readout is past the axis end (Nov 30): not drawn, counted on the row
    expect(within(grid).queryByTitle(/^Catalyst: Readout/)).toBeNull();
    expect(within(grid).getByText("+2 undated · +1 later")).toBeInTheDocument();
  });

  it("draws decisions as glyphs, a voided one grayed and still visible", () => {
    renderPage();
    const grid = rowOf("Grid");
    const take = within(grid).getByTitle("Decision: take · HIMS · Oct 8, 2026");
    expect(take).toHaveClass("it-dec", "take");
    expect(take).toHaveTextContent("▲");
    const close = within(grid).getByTitle(/^Decision: close · HIMS · Oct 9, 2026/);
    expect(close).toHaveClass("it-dec", "close", "voided");
    expect(close.getAttribute("title")).toContain("voided");
    expect(within(grid).getByTitle("Decision: pass · the card · Sep 2, 2026 · too early")).toHaveTextContent(
      "◇",
    );
    // no decisions logged: nothing drawn
    expect(rowOf("Nuclear").querySelectorAll(".it-dec")).toHaveLength(0);
  });

  it("draws the latest call's exit-by bracket and entry-window underline only when set", () => {
    renderPage();
    const grid = rowOf("Grid");
    expect(within(grid).getByTitle(/^Exit-by Nov 29, 2026/)).toHaveClass("it-exit");
    expect(within(grid).getByTitle(/^Entry window through Nov 5, 2026/)).toHaveClass("it-entry");
    const nuc = rowOf("Nuclear");
    expect(nuc.querySelectorAll(".it-exit, .it-entry")).toHaveLength(0);
  });

  it("draws the shared axis with month ticks and today's line on every track", () => {
    renderPage();
    const axis = table().querySelector(".it-axis") as HTMLElement;
    expect([...axis.querySelectorAll(".it-tick")].map((t) => t.textContent)).toEqual([
      "Aug",
      "Sep",
      "Oct",
      "today", // Nov 1 sits 3 days from today: its label would print over today's, so only its gridline shows
    ]);
    expect(rowOf("Grid").querySelectorAll(".it-grid")).toHaveLength(4); // Aug, Sep, Oct, Nov
    expect(table().querySelectorAll(".it-track .it-today")).toHaveLength(5);
  });
});

describe("Idea Timeline — controls", () => {
  it("defaults to All and a 30-day zoom clips what falls before its window", async () => {
    renderPage();
    const zoom = screen.getByRole("group", { name: "Zoom" });
    expect(within(zoom).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(rowOf("Grid").querySelectorAll(".it-seg")).toHaveLength(3);

    await userEvent.click(within(zoom).getByRole("button", { name: "30 days" }));
    expect(within(zoom).getByRole("button", { name: "30 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // Sep 29 → Nov 28: the Jul–Sep incubating run is gone, the warming and armed runs remain
    expect(
      [...rowOf("Grid").querySelectorAll(".it-seg")].map((s) => s.className),
    ).toEqual(["it-seg st-warm", "it-seg st-armed current"]);
    const axis = table().querySelector(".it-axis") as HTMLElement;
    expect(within(axis).queryByText("Aug")).toBeNull();
    // the visible end is now Nov 28: the Nov 29 exit-by is past it too, counted rather than drawn
    expect(rowOf("Grid").querySelectorAll(".it-exit")).toHaveLength(0);
    expect(within(rowOf("Grid")).getByText("+2 undated · +2 later")).toBeInTheDocument();

    await userEvent.click(within(zoom).getByRole("button", { name: "90 days" }));
    expect(rowOf("Grid").querySelectorAll(".it-seg")).toHaveLength(3);
  });

  it("filters by state chips (only the states some card holds) and a second click clears", async () => {
    renderPage();
    const chips = screen.getByRole("group", { name: "Show states" });
    expect(within(chips).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Armed · 2",
      "Warming · 1",
      "Incubating · 1",
      "No call yet · 1",
    ]); // no card is managing, so that chip does not render

    await userEvent.click(within(chips).getByRole("button", { name: /^Armed/ }));
    expect(names()).toEqual(["Grid", "biotech"]);
    await userEvent.click(within(chips).getByRole("button", { name: /^Warming/ }));
    expect(names()).toEqual(["Grid", "biotech", "Nuclear"]);
    await userEvent.click(within(chips).getByRole("button", { name: /^Armed/ }));
    expect(names()).toEqual(["Nuclear"]);
    await userEvent.click(within(chips).getByRole("button", { name: /^Warming/ }));
    expect(names()).toHaveLength(5);
  });

  it("sorts by state then days in state by default, and by each other option", async () => {
    renderPage();
    expect(names()).toEqual(["Grid", "biotech", "Nuclear", "Uranium", "Brand New"]);

    const sort = screen.getByRole("combobox", { name: "Sort" });
    await userEvent.selectOptions(sort, "days_in_state");
    expect(names()).toEqual(["Uranium", "Nuclear", "Grid", "biotech", "Brand New"]);
    await userEvent.selectOptions(sort, "days_armed");
    expect(names()).toEqual(["Nuclear", "Grid", "biotech", "Brand New", "Uranium"]);
    await userEvent.selectOptions(sort, "first_recorded");
    expect(names()).toEqual(["Grid", "Uranium", "Nuclear", "biotech", "Brand New"]);
    await userEvent.selectOptions(sort, "name");
    expect(names()).toEqual(["biotech", "Brand New", "Grid", "Nuclear", "Uranium"]);
    expect(screen.getByRole("columnheader", { name: /Card ↓/ })).toBeInTheDocument();
  });
});

describe("Idea Timeline — states of the read", () => {
  it("shows loading, error and empty notes", () => {
    h.useIdeaTimeline.mockReturnValue(query(undefined, { isLoading: true }));
    const { rerender } = renderPage();
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    h.useIdeaTimeline.mockReturnValue(query(undefined, { error: new Error("boom") }));
    rerender(<IdeaTimeline asof="2026-10-29" onSelectThesis={onSelectThesis} />);
    expect(screen.getByText("Could not load the Idea Timeline.")).toBeInTheDocument();

    h.useIdeaTimeline.mockReturnValue(
      query({ ...DATA, rows: [], live_cards: 0, cards_without_calls: 0 }),
    );
    rerender(<IdeaTimeline asof="2026-10-29" onSelectThesis={onSelectThesis} />);
    expect(screen.getByText("No live cards.")).toBeInTheDocument();
  });
});
