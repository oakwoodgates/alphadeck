import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WhatChanged from "../WhatChanged";
import type { WhatChangedItemOut, WhatChangedOut } from "../useWhatChanged";

// The What Changed body with its one hook stubbed: both groupings, every line's wording, the since selector
// asking the hook for each baseline, the quiet-night and error states, and a line opening the Cockpit. The
// hook's own request shape has its own test (useWhatChanged.test.tsx).

const h = vi.hoisted(() => ({
  useWhatChanged: vi.fn(),
}));
vi.mock("../useWhatChanged", () => ({ useWhatChanged: h.useWhatChanged }));

const GRID = { thesis_id: "t-grid", thesis_name: "Grid" };
const COAL = { thesis_id: "t-coal", thesis_name: "coal" };
const NEW = { thesis_id: "t-new", thesis_name: "Aardvark" };

const item = (
  card: { thesis_id: string; thesis_name: string },
  section: WhatChangedItemOut["section"],
  change: WhatChangedItemOut["change"],
  over: Partial<WhatChangedItemOut> = {},
): WhatChangedItemOut => ({
  ...card,
  section,
  change,
  reconstructed: false,
  security_id: null,
  ticker: null,
  from_state: null,
  to_state: null,
  label: null,
  signal_kind: null,
  on: null,
  days: null,
  ...over,
});

// in the server's order: by section, then card name
const ITEMS: WhatChangedItemOut[] = [
  item(COAL, "column_moves", "state", { from_state: "armed", to_state: "warming" }),
  item(GRID, "column_moves", "state", { from_state: "warming", to_state: "armed" }),
  item(GRID, "keys", "key_on", { label: "Conviction" }),
  item(COAL, "keys", "key_off", { label: "Confirmation" }),
  item(GRID, "names", "armed", { security_id: "s-aaa", ticker: "AAA" }),
  item(COAL, "names", "no_longer_armed", { security_id: "s-bbb", ticker: "BBB" }),
  item(GRID, "names", "new_watch", { security_id: "s-zzz", ticker: null }),
  item(GRID, "triggers", "trigger_new", {
    security_id: "s-aaa",
    ticker: "AAA",
    label: "8-day breakout",
    signal_kind: "technical_breakout",
    on: "2026-10-28",
  }),
  item(COAL, "triggers", "trigger_gone", {
    security_id: "s-bbb",
    ticker: "BBB",
    label: "Insider cluster buy",
    signal_kind: "insider",
    on: "2026-10-01",
  }),
  item(COAL, "triggers", "risk_new", {
    security_id: "s-bbb",
    ticker: "BBB",
    label: "Convertible overhang",
    signal_kind: "dilution_risk",
    on: "2026-10-27",
  }),
  item(GRID, "clocks", "entry_window", { on: "2026-10-31", days: 3 }),
  item(COAL, "clocks", "exit_by", { on: "2026-10-28" }),
  item(NEW, "new_cards", "new_card", { to_state: "warming", reconstructed: true }),
];

const DATA: WhatChangedOut = {
  since: "last_night",
  since_date: null,
  first_recorded: "2026-09-01",
  latest: "2026-10-28",
  baseline: "2026-10-27",
  live_cards: 62,
  cards_with_change: 3,
  unrecorded: [],
  items: ITEMS,
};

const query = (data: WhatChangedOut | undefined, over: object = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const onSelectThesis = vi.fn();
const renderPage = () =>
  render(<WhatChanged asof="2026-10-29" onSelectThesis={onSelectThesis} />);

/** Each section's heading and its lines' text, top to bottom. */
const sections = () =>
  screen.getAllByRole("region").map((s) => ({
    heading: within(s).getByRole("heading").textContent,
    lines: within(s)
      .queryAllByRole("listitem")
      .map((li) => li.textContent),
  }));

beforeEach(() => {
  h.useWhatChanged.mockReset();
  h.useWhatChanged.mockReturnValue(query(DATA));
  onSelectThesis.mockClear();
});

describe("What Changed — group by kind (the default)", () => {
  it("renders each section with its count, its lines one per item, in the server's order", () => {
    renderPage();
    expect(sections()).toEqual([
      { heading: "Column moves · 2", lines: ["coalArmed → Warming", "GridWarming → Armed"] },
      {
        heading: "Keys turned · 2",
        lines: ["GridConviction key turned on", "coalConfirmation key turned off"],
      },
      {
        heading: "Names · 3",
        lines: ["GridAAAnewly armed", "coalBBBno longer armed", "Grid—new on watch"],
      },
      {
        heading: "Triggers · 3",
        lines: [
          "GridAAAfired · 8-day breakout · technical breakout · Oct 28",
          "coalBBBno longer firing · Insider cluster buy · insider · Oct 1",
          "coalBBBnew risk signal · Convertible overhang · dilution risk · Oct 27",
        ],
      },
      {
        heading: "Clocks · 2",
        lines: ["Gridentry window closes in 3 days · Oct 31", "coalexit-by reached · Oct 28"],
      },
      { heading: "New cards · 1", lines: ["Aardvarknew card · Warmingreconstructed"] },
    ]);
  });

  it("colors each state word with its Board column's class, and nothing else", () => {
    renderPage();
    const move = within(screen.getByRole("region", { name: "Column moves" }))
      .getAllByRole("listitem")[1];
    expect(within(move).getByText("Warming")).toHaveClass("wc-st", "warm");
    expect(within(move).getByText("Armed")).toHaveClass("wc-st", "armed");
  });

  it("never words a stopped trigger as expired or failed", () => {
    renderPage();
    const text = screen.getByRole("region", { name: "Triggers" }).textContent ?? "";
    expect(text).toContain("no longer firing");
    expect(text).not.toMatch(/expired|failed/i);
  });

  it("an entry window closing on the newest night says so", () => {
    h.useWhatChanged.mockReturnValue(
      query({ ...DATA, items: [item(GRID, "clocks", "entry_window", { on: "2026-10-28", days: 0 })] }),
    );
    renderPage();
    expect(
      screen.getByText("entry window closes on the newest night · Oct 28"),
    ).toBeInTheDocument();
  });

  it("shows the work in one quiet line: the two nights, the counts and the rule", () => {
    h.useWhatChanged.mockReturnValue(
      query({ ...DATA, unrecorded: [{ thesis_id: "t-x", thesis_name: "Fresh" }] }),
    );
    renderPage();
    const line = screen.getByText(/^Oct 27 → Oct 28/);
    expect(line).toHaveTextContent(
      "Oct 27 → Oct 28 · 62 live cards · 3 cards with a change · the log records only on change, " +
        "so a card with no new row is unchanged · 1 live card with no recorded call yet",
    );
    expect(within(line).getByTitle("Fresh")).toBeInTheDocument();
  });
});

describe("What Changed — group by card", () => {
  it("the toggle puts each card's lines together, cards by name, with the reconstructed tag on the card", async () => {
    const user = userEvent.setup();
    renderPage();
    const byCard = screen.getByRole("button", { name: "By card" });
    expect(byCard).toHaveAttribute("aria-pressed", "false");
    await user.click(byCard);
    expect(byCard).toHaveAttribute("aria-pressed", "true");

    expect(sections()).toEqual([
      { heading: "Aardvark · 1reconstructed", lines: ["new card · Warming"] },
      {
        heading: "coal · 6",
        lines: [
          "Armed → Warming",
          "Confirmation key turned off",
          "BBBno longer armed",
          "BBBno longer firing · Insider cluster buy · insider · Oct 1",
          "BBBnew risk signal · Convertible overhang · dilution risk · Oct 27",
          "exit-by reached · Oct 28",
        ],
      },
      {
        heading: "Grid · 6",
        lines: [
          "Warming → Armed",
          "Conviction key turned on",
          "AAAnewly armed",
          "—new on watch",
          "AAAfired · 8-day breakout · technical breakout · Oct 28",
          "entry window closes in 3 days · Oct 31",
        ],
      },
    ]);

    // and back: its inverse is the other button
    await user.click(screen.getByRole("button", { name: "By kind" }));
    expect(screen.getByRole("region", { name: "Column moves" })).toBeInTheDocument();
  });

  it("a card's heading opens that card", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "By card" }));
    await user.click(screen.getByRole("button", { name: "Grid" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid");
  });
});

describe("What Changed — opening the Cockpit", () => {
  it("a name or trigger line opens the card on that ticker", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /newly armed/ }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "AAA");
  });

  it("a name with no ticker opens on its security id", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /new on watch/ }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid", "s-zzz");
  });

  it("a card-level line opens the card alone", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /Conviction key turned on/ }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-grid");
    expect(onSelectThesis.mock.calls[0]).toHaveLength(1);
  });
});

describe("What Changed — the since selector", () => {
  it("starts on last night and asks the hook for each choice", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("last_night");
    expect(screen.getByRole("button", { name: "Last night" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("7d");
    expect(screen.queryByLabelText("Since date")).toBeNull();

    // "Since a date" opens on the baseline in view, bounded by the record
    await user.click(screen.getByRole("button", { name: "Since a date" }));
    const picker = screen.getByLabelText("Since date");
    expect(picker).toHaveValue("2026-10-27");
    expect(picker).toHaveAttribute("min", "2026-09-01");
    expect(picker).toHaveAttribute("max", "2026-10-28");
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("2026-10-27");

    fireEvent.change(picker, { target: { value: "2026-10-14" } });
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("2026-10-14");

    // a cleared picker keeps the last date it had
    fireEvent.change(picker, { target: { value: "" } });
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("2026-10-14");

    await user.click(screen.getByRole("button", { name: "Last night" }));
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("last_night");
  });

  it("a 7-day baseline from before the record began opens the picker on the first recorded night", async () => {
    const user = userEvent.setup();
    h.useWhatChanged.mockReturnValue(
      query({ ...DATA, since: "7d", baseline: "2026-08-25", first_recorded: "2026-09-01" }),
    );
    renderPage();
    await user.click(screen.getByRole("button", { name: "Since a date" }));
    expect(screen.getByLabelText("Since date")).toHaveValue("2026-09-01");
    expect(h.useWhatChanged).toHaveBeenLastCalledWith("2026-09-01");
  });

  it("a picked date outside the record shows the server's reason, with the selector still there", () => {
    h.useWhatChanged.mockReturnValue(
      query(undefined, {
        error: { detail: "2026-10-29 is after the newest recorded night (2026-10-28)" },
      }),
    );
    renderPage();
    expect(
      screen.getByText("2026-10-29 is after the newest recorded night (2026-10-28)"),
    ).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Since" })).toBeInTheDocument();
  });

  it("an empty log disables the date choice", () => {
    h.useWhatChanged.mockReturnValue(
      query({
        ...DATA,
        first_recorded: null,
        latest: null,
        baseline: null,
        cards_with_change: 0,
        items: [],
      }),
    );
    renderPage();
    expect(screen.getByRole("button", { name: "Since a date" })).toBeDisabled();
    expect(screen.getByText("No recorded calls yet.")).toBeInTheDocument();
    expect(screen.getByText("62 live cards · no recorded call yet")).toBeInTheDocument();
  });
});

describe("What Changed — a quiet night", () => {
  it("says nothing moved since the baseline night, and renders no section", () => {
    h.useWhatChanged.mockReturnValue(query({ ...DATA, cards_with_change: 0, items: [] }));
    renderPage();
    expect(screen.getByText("No changes since Oct 27.")).toBeInTheDocument();
    expect(screen.queryAllByRole("region")).toEqual([]);
    expect(screen.getByText(/^Oct 27 → Oct 28 · 62 live cards · 0 cards with a change/)).toBeInTheDocument();
  });

  it("while loading, and on a generic error", () => {
    h.useWhatChanged.mockReturnValue(query(undefined, { isLoading: true }));
    const { unmount } = renderPage();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    unmount();

    h.useWhatChanged.mockReturnValue(query(undefined, { error: new Error("boom") }));
    renderPage();
    expect(screen.getByText("Could not load What Changed.")).toBeInTheDocument();
  });
});
