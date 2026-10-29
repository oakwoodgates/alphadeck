import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CatalystCalendar from "../CatalystCalendar";
import type {
  CatalystCalendarBucketOut,
  CatalystCalendarItemOut,
  CatalystCalendarOut,
} from "../useCatalystCalendar";

// The Catalyst Calendar body with its one hook stubbed: the three sections, every line's wording, the kind /
// state / text filters, the deadlines toggle, the empty states and a line opening the Cockpit. The hook's own
// request shape has its own test (useCatalystCalendar.test.tsx); the filter helpers have calendar.test.ts.

const h = vi.hoisted(() => ({
  useCatalystCalendar: vi.fn(),
}));
vi.mock("../useCatalystCalendar", () => ({ useCatalystCalendar: h.useCatalystCalendar }));

const GRID = { thesis_id: "t-grid", thesis_name: "Grid", state: "armed" };
const COAL = { thesis_id: "t-coal", thesis_name: "coal", state: "warming" };
const FRESH = { thesis_id: "t-fresh", thesis_name: "Fresh", state: null };

let n = 0;
const item = (
  card: { thesis_id: string; thesis_name: string; state: string | null },
  on: string,
  days: number,
  label: string,
  over: Partial<CatalystCalendarItemOut> = {},
): CatalystCalendarItemOut => ({
  ...card,
  item: "catalyst",
  on,
  days,
  label,
  kind: "regulatory",
  when_label: null,
  catalyst_id: `c-${(n += 1)}`,
  ...over,
});

const deadline = (
  card: typeof GRID,
  which: "entry_window" | "exit_by",
  on: string,
  days: number,
): CatalystCalendarItemOut =>
  item(card, on, days, which === "entry_window" ? "entry window ends" : "exit-by", {
    item: which,
    kind: null,
    catalyst_id: null,
  });

const bucket = (
  key: CatalystCalendarBucketOut["key"],
  label: string,
  start: string,
  end: string | null,
  items: CatalystCalendarItemOut[] = [],
): CatalystCalendarBucketOut => ({ key, label, start, end, items });

const DATA: CatalystCalendarOut = {
  today: "2026-10-29",
  live_cards: 4,
  cards_without_calls: 1,
  cards_without_catalysts: 1,
  catalysts: 9,
  dated: 6,
  upcoming_catalysts: 4,
  passed_catalysts: 2,
  undated: 3,
  deadlines: 2,
  buckets: [
    bucket("this_week", "This week", "2026-10-29", "2026-11-01", [
      item(GRID, "2026-10-29", 0, "FERC vote", { when_label: "late Oct" }),
      deadline(GRID, "entry_window", "2026-10-30", 1),
    ]),
    bucket("next_week", "Next week", "2026-11-02", "2026-11-08", [
      item(COAL, "2026-11-04", 6, "Q3 earnings", { kind: "earnings" }),
    ]),
    bucket("next_30_days", "Rest of the next 30 days", "2026-11-09", "2026-11-28"),
    bucket("days_31_90", "31–90 days", "2026-11-29", "2027-01-27", [
      item(FRESH, "2026-12-15", 47, "Launch", { kind: null }),
      deadline(GRID, "exit_by", "2027-01-10", 73),
    ]),
    bucket("later", "Later", "2027-01-28", null, [
      item(COAL, "2027-06-01", 215, "Patent expiry"),
    ]),
  ],
  passed: [
    item(COAL, "2026-10-25", -4, "Pilot readout", { kind: "clinical_readout" }),
    item(GRID, "2026-09-01", -58, "Hearing"),
  ],
  undated_groups: [
    {
      ...COAL,
      count: 2,
      catalysts: [
        { catalyst_id: "u-1", label: "Phase 2 start", kind: "clinical_readout", when_label: "~Q3" },
        { catalyst_id: "u-2", label: "Index add", kind: null, when_label: null },
      ],
    },
    {
      ...FRESH,
      count: 1,
      catalysts: [{ catalyst_id: "u-3", label: "Partnership", kind: null, when_label: "2H27" }],
    },
  ],
};

const query = (data: CatalystCalendarOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () =>
  render(<CatalystCalendar asof="2026-10-29" onSelectThesis={onSelectThesis} />);

const region = (name: string) => screen.getByRole("region", { name });
const texts = (el: HTMLElement) =>
  within(el)
    .queryAllByRole("listitem")
    .map((li) => li.textContent);

/** Each upcoming bucket's heading and its lines, top to bottom. */
const buckets = () =>
  within(region("Upcoming"))
    .queryAllByRole("group")
    .map((g) => ({ heading: within(g).getByRole("heading").textContent, lines: texts(g) }));

const ALL_BUCKETS = [
  {
    heading: "This week · Oct 29 – Nov 1 · 2",
    lines: [
      "Thu, Oct 29todayFERC voteregulatoryGridArmed",
      "Fri, Oct 301 dayentry window endsdeadlineGridArmed",
    ],
  },
  {
    heading: "Next week · Nov 2 – Nov 8 · 1",
    lines: ["Wed, Nov 46 daysQ3 earningsearningscoalWarming"],
  },
  {
    heading: "31–90 days · Nov 29 – Jan 27, 2027 · 2",
    lines: [
      "Tue, Dec 1547 daysLaunch—Fresh—",
      "Sun, Jan 10, 202773 daysexit-bydeadlineGridArmed",
    ],
  },
  {
    heading: "Later · after Jan 27, 2027 · 1",
    lines: ["Tue, Jun 1, 2027215 daysPatent expiryregulatorycoalWarming"],
  },
];

beforeEach(() => {
  h.useCatalystCalendar.mockReset();
  h.useCatalystCalendar.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("Catalyst Calendar — the sections", () => {
  it("renders Upcoming, Passed: prune? and Undated, in that order, with their counts", () => {
    renderPage();
    expect(
      screen.getAllByRole("region").map((r) => within(r).getAllByRole("heading")[0].textContent),
    ).toEqual(["Upcoming · 6", "Passed: prune? · 2", "Undated · 3"]);
  });

  it("buckets the upcoming lines, renders only the buckets with lines, and words each line", () => {
    renderPage();
    expect(buckets()).toEqual(ALL_BUCKETS);
  });

  it("colors each state chip with its Board column's class; a card with no call reads —", () => {
    renderPage();
    const [vote, entry] = within(region("Upcoming")).getAllByRole("listitem");
    expect(within(vote).getByText("Armed")).toHaveClass("ccal-st", "armed");
    const launch = within(region("Upcoming")).getAllByRole("listitem")[3];
    const dashes = within(launch).getAllByText("—");
    expect(dashes.at(-1)).toHaveClass("ccal-st", "none");
    expect(dashes.at(-1)).toHaveAttribute("title", "No recorded call yet");
    // a deadline wears its tag and its own class, never a catalyst kind
    expect(within(entry).getByText("deadline")).toHaveClass("ccal-tag");
    expect(within(entry).getByRole("button")).toHaveClass("ccal-line", "ccal-deadline");
    expect(within(vote).getByRole("button")).not.toHaveClass("ccal-deadline");
  });

  it("lists the passed catalysts newest first, quietly, with the prune note", () => {
    renderPage();
    const passed = region("Passed: prune?");
    expect(texts(passed)).toEqual([
      "Sun, Oct 254 days agoPilot readoutclinical readoutcoalWarming",
      "Tue, Sep 158 days agoHearingregulatoryGridArmed",
    ]);
    expect(
      within(passed).getByText(
        "The Board keeps showing a passed catalyst until it's removed in the card's editor.",
      ),
    ).toBeInTheDocument();
    expect(within(passed).getAllByRole("button")[0]).toHaveClass("ccal-passed");
  });

  it("groups the undated per card, most first, and expands a card to its labels and when-labels", async () => {
    const user = userEvent.setup();
    renderPage();
    const undated = region("Undated");
    expect(texts(undated)).toEqual(["2coalWarmingshow", "1Fresh—show"]);

    const show = within(undated).getByRole("button", { name: "Show coal's undated catalysts" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    await user.click(show);
    const hide = within(undated).getByRole("button", { name: "Hide coal's undated catalysts" });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    expect(texts(undated)).toEqual([
      "2coalWarminghidePhase 2 start~Q3clinical readoutIndex addno date—",
      "Phase 2 start~Q3clinical readout",
      "Index addno date—",
      "1Fresh—show",
    ]);

    // and back: its inverse is the same button
    await user.click(hide);
    expect(within(undated).queryByText("Phase 2 start")).toBeNull();
  });

  it("shows the work in one quiet line: the cards, the catalysts, the deadlines and today", () => {
    renderPage();
    expect(
      screen.getByText(
        "4 live cards · 9 catalysts: 6 dated (4 upcoming, 2 passed), 3 undated · 2 card deadlines ahead · " +
          "1 card with no recorded call · 1 card with no catalysts · today Oct 29, 2026",
      ),
    ).toBeInTheDocument();
  });
});

describe("Catalyst Calendar — opening the Cockpit", () => {
  it("a catalyst line, a deadline, a passed line and an undated line each open their card alone", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Q3 earnings/ }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-coal");
    expect(onSelectThesis.mock.calls[0]).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /exit-by/ }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid");

    await user.click(screen.getByRole("button", { name: /Pilot readout/ }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-coal");

    await user.click(within(region("Undated")).getByText("Fresh"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-fresh");

    await user.click(screen.getByRole("button", { name: "Show coal's undated catalysts" }));
    await user.click(screen.getByRole("button", { name: /Index add/ }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-coal");
  });
});

describe("Catalyst Calendar — the filters", () => {
  it("builds the kind chips from the kinds present, most first, no kind last", () => {
    renderPage();
    const kinds = screen.getByRole("group", { name: "Kinds" });
    expect(within(kinds).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "regulatory · 3",
      "clinical readout · 2",
      "earnings · 1",
      "no kind · 3",
    ]);
  });

  it("a kind chip narrows the catalysts in every section; the deadlines stay with their toggle", async () => {
    const user = userEvent.setup();
    renderPage();
    const chip = screen.getByRole("button", { name: "clinical readout · 2" });
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");

    expect(buckets().map((b) => b.lines)).toEqual([
      ["Fri, Oct 301 dayentry window endsdeadlineGridArmed"],
      ["Sun, Jan 10, 202773 daysexit-bydeadlineGridArmed"],
    ]);
    expect(texts(region("Passed: prune?"))).toEqual([
      "Sun, Oct 254 days agoPilot readoutclinical readoutcoalWarming",
    ]);
    expect(texts(region("Undated"))).toEqual(["1coalWarmingshow"]);

    // its inverse: the same chip
    await user.click(chip);
    expect(buckets()).toEqual(ALL_BUCKETS);
  });

  it("a state chip keeps only the cards in that state, and the no-call chip finds a card with none", async () => {
    const user = userEvent.setup();
    renderPage();
    const states = screen.getByRole("group", { name: "States" });
    expect(within(states).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Armed · 1",
      "Warming · 1",
      "No call yet · 1",
    ]);
    expect(within(states).getByRole("button", { name: "Armed · 1" })).toHaveClass("armed");

    await user.click(within(states).getByRole("button", { name: "No call yet · 1" }));
    expect(buckets().map((b) => b.lines)).toEqual([["Tue, Dec 1547 daysLaunch—Fresh—"]]);
    expect(
      within(region("Passed: prune?")).getByText("Nothing here matches these filters."),
    ).toBeInTheDocument();
    expect(texts(region("Undated"))).toEqual(["1Fresh—show"]);
  });

  it("the text filter matches a card name or a label, and Clear filters undoes every filter", async () => {
    const user = userEvent.setup();
    renderPage();
    const box = screen.getByRole("searchbox", { name: "Filter by card or label" });

    fireEvent.change(box, { target: { value: "COAL" } });
    expect(buckets().map((b) => b.heading)).toEqual([
      "Next week · Nov 2 – Nov 8 · 1",
      "Later · after Jan 27, 2027 · 1",
    ]);
    expect(texts(region("Undated"))).toEqual(["2coalWarmingshow"]);

    fireEvent.change(box, { target: { value: "vote" } });
    expect(buckets().map((b) => b.lines)).toEqual([
      ["Thu, Oct 29todayFERC voteregulatoryGridArmed"],
    ]);
    expect(texts(region("Undated"))).toEqual([]);
    expect(
      within(region("Undated")).getByText("Nothing here matches these filters."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "earnings · 1" }));
    expect(
      within(region("Upcoming")).getByText("Nothing here matches these filters."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(box).toHaveValue("");
    expect(buckets()).toEqual(ALL_BUCKETS);
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
  });

  it("the deadlines toggle hides the entry-window and exit-by lines, and says so on the work line", async () => {
    const user = userEvent.setup();
    renderPage();
    const toggle = screen.getByRole("checkbox", { name: "Show card deadlines" });
    expect(toggle).toBeChecked();

    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    const lines = buckets().flatMap((b) => b.lines);
    expect(lines).toHaveLength(4);
    expect(lines.join(" ")).not.toMatch(/deadline|entry window|exit-by/);
    expect(screen.getByText(/2 card deadlines ahead \(hidden\)/)).toBeInTheDocument();
    // the toggle is not a filter: there is nothing to clear
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();

    await user.click(toggle);
    expect(buckets()).toEqual(ALL_BUCKETS);
  });

  it("a filter with only one option does not render", () => {
    h.useCatalystCalendar.mockReturnValue(
      query({
        ...DATA,
        buckets: DATA.buckets.map((b) => ({
          ...b,
          items: b.items.filter((i) => i.thesis_id === "t-grid"),
        })),
        passed: [],
        undated_groups: [],
      }),
    );
    renderPage();
    // Grid alone: one state; its one catalyst kind
    expect(screen.queryByRole("group", { name: "States" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Kinds" })).toBeNull();
  });
});

describe("Catalyst Calendar — the empty states", () => {
  it("nothing dated ahead, nothing passed, every catalyst dated", () => {
    h.useCatalystCalendar.mockReturnValue(
      query({
        ...DATA,
        buckets: DATA.buckets.map((b) => ({ ...b, items: [] })),
        passed: [],
        undated_groups: [],
      }),
    );
    renderPage();
    expect(within(region("Upcoming")).getByText("Nothing dated ahead.")).toBeInTheDocument();
    expect(within(region("Passed: prune?")).getByText("No passed catalysts.")).toBeInTheDocument();
    expect(within(region("Undated")).getByText("Every catalyst has a date.")).toBeInTheDocument();
    // the prune note explains a list; with no list it does not render
    expect(screen.queryByText(/The Board keeps showing/)).toBeNull();
  });

  it("no live cards", () => {
    h.useCatalystCalendar.mockReturnValue(
      query({
        ...DATA,
        live_cards: 0,
        cards_without_calls: 0,
        cards_without_catalysts: 0,
        catalysts: 0,
        dated: 0,
        upcoming_catalysts: 0,
        passed_catalysts: 0,
        undated: 0,
        deadlines: 0,
        buckets: DATA.buckets.map((b) => ({ ...b, items: [] })),
        passed: [],
        undated_groups: [],
      }),
    );
    renderPage();
    expect(screen.getByText("No live cards.")).toBeInTheDocument();
    expect(screen.queryAllByRole("region")).toEqual([]);
  });

  it("while loading, and on an error", () => {
    h.useCatalystCalendar.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    unmount();

    h.useCatalystCalendar.mockReturnValue(query(undefined, { error: new Error("boom") }));
    renderPage();
    expect(screen.getByText("Could not load the Catalyst Calendar.")).toBeInTheDocument();
  });
});
