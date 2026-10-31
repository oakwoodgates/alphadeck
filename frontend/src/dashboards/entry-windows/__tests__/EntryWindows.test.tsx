import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EntryWindows from "../EntryWindows";
import type { EntryWindowsOut } from "../useEntryWindows";
import { ASOF, CLOSED, CLOSING, MANAGING, NO_CLOCKS, PAIR, counts, payload, row } from "./fixtures";

// The Entry Windows body with its hook stubbed: the Armed rows with Managing behind a toggle that counts them,
// the default order (closing soonest first), the one accent on a card's own entry_window_closing flag, the
// window bars (none without a clock), the hollow catalyst ticks, the readout line, the fold with its linked
// sources, the roving keyboard stop, the Cockpit links, and the quiet lines. The hook's request has its own test.

const h = vi.hoisted(() => ({ useEntryWindows: vi.fn() }));
vi.mock("../useEntryWindows", () => ({ useEntryWindows: h.useEntryWindows }));

function setup(data: EntryWindowsOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useEntryWindows.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<EntryWindows asof={ASOF} onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const rowEls = () => screen.getAllByTestId("ew-row");
const names = () => rowEls().map((r) => within(r).getAllByRole("button")[0].textContent);
const rowOf = (name: string) =>
  rowEls().find((r) => within(r).queryByText(name, { selector: ".ew-card-name" }) !== null)!;

beforeEach(() => h.useEntryWindows.mockReset());

describe("EntryWindows — the rows", () => {
  it("shows the Armed cards and hides the Managing one behind a toggle that counts it", async () => {
    setup();
    expect(names()).toEqual(["Card B", "Card A", "Card C", "Card D"]);
    const toggle = screen.getByRole("checkbox", { name: /Show Managing \(1\)/ });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(names()).toContain("Card M");
    expect(within(rowOf("Card M")).getByText("Managing")).toHaveClass("managing");
    expect(within(rowOf("Card A")).getByText("Armed")).toHaveClass("armed");
  });

  it("renders no Managing toggle when no card is Managing, and no order control for fewer than two rows", () => {
    setup(payload({ rows: [row()], counts: counts({ armed: 1, managing: 0 }) }));
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("orders by sessions left by default: closing soonest, then closed windows, then no clock", () => {
    setup();
    expect(names()).toEqual(["Card B", "Card A", "Card C", "Card D"]);
    expect(screen.getByTestId("ew-work")).toHaveTextContent("4 Armed rows, 1 Managing hidden");
  });

  it("re-orders from the order control", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Order the rows" }), "armed");
    expect(names()).toEqual(["Card B", "Card A", "Card D", "Card C"]);
  });

  it("words each clock cell, including a closed window, a passed horizon and a missing clock", () => {
    setup();
    expect(within(rowOf("Card A")).getByTestId("ew-left")).toHaveTextContent("3 sessions left");
    expect(within(rowOf("Card A")).getByTestId("ew-hold-days")).toHaveTextContent("18 days");
    expect(within(rowOf("Card C")).getByTestId("ew-left")).toHaveTextContent("entry window closed 3 sessions ago");
    expect(within(rowOf("Card C")).getByTestId("ew-hold-days")).toHaveTextContent("hold horizon passed");
    expect(within(rowOf("Card D")).getByTestId("ew-left")).toHaveTextContent("no entry clock");
    expect(within(rowOf("Card D")).getByTestId("ew-hold-days")).toHaveTextContent("no hold clock");
  });

  it("shows the setup strength in text, the reconstructed mark and the hold-clock tag only where they are true", () => {
    setup();
    expect(within(rowOf("Card A")).getByText("setup strength 0.62")).toBeInTheDocument();
    expect(within(rowOf("Card D")).queryByText(/setup strength/)).toBeNull();
    expect(within(rowOf("Card C")).getByText("reconstructed")).toBeInTheDocument();
    expect(within(rowOf("Card A")).queryByText("reconstructed")).toBeNull();
    expect(within(rowOf("Card B")).getByText(/hold clock closing/)).toBeInTheDocument();
    expect(within(rowOf("Card A")).queryByText(/hold clock closing/)).toBeNull();
  });

  it("states the tape since the arm night: the median with its n, both benchmarks, the names above, the unpriced", () => {
    setup();
    const a = rowOf("Card A");
    expect(a).toHaveTextContent("+4.5%");
    expect(a).toHaveTextContent("median of 2");
    expect(a).toHaveTextContent("IWM +1.0% · SPY -2.0%");
    expect(a).toHaveTextContent("1 of 2 above the arm-night close");
    const c = rowOf("Card C");
    expect(c).toHaveTextContent("no priced name");
    expect(c).toHaveTextContent("1 flagged (of 1) out of the median");
  });
});

describe("EntryWindows — a second name's own clock", () => {
  it("keeps the card's figure and names the second name's window and hold clock under it", () => {
    setup(payload({ rows: [PAIR, row()], counts: counts({ armed: 2, managing: 0 }) }));
    const p = rowOf("Card P");
    // the card's own figure is unchanged and still carries the accent: the flag is any name's
    const left = within(p).getByTestId("ew-left");
    expect(left).toHaveTextContent("3 sessions left");
    expect(left).toHaveClass("closing");
    expect(left.getAttribute("title")).toContain("any armed name's entry window");
    expect(within(p).getByTestId("ew-member-entry")).toHaveTextContent("BBB · 1 session left: Oct 28");
    // the card's own hold clock is not closing, so the card says nothing; the second name's is named
    expect(within(p).getByTestId("ew-hold-days")).toHaveTextContent("18 days");
    expect(within(p).getByTestId("ew-member-hold")).toHaveTextContent("BBB · hold clock closing");
    expect(within(p).getAllByText(/hold clock closing/)).toHaveLength(1);
  });

  it("draws no name line when no armed name is closing, or when the closing window is the card's own", () => {
    setup();
    expect(screen.queryAllByTestId("ew-member-entry")).toHaveLength(0);
    expect(screen.queryAllByTestId("ew-member-hold")).toHaveLength(0);
    // Card B's own (headline) window is the one closing: its figure says so, with the card's hold tag and no repeat
    const b = rowOf("Card B");
    expect(within(b).getByTestId("ew-left")).toHaveClass("closing");
    expect(within(b).getByText(/hold clock closing/)).toBeInTheDocument();
    expect(within(b).queryByTestId("ew-member-entry")).toBeNull();
    expect(within(b).queryByTestId("ew-member-hold")).toBeNull();
    expect(within(rowOf("Card A")).queryByText(/hold clock closing/)).toBeNull();
  });
});

describe("EntryWindows — the picture", () => {
  it("carries the one accent on a card's own entry_window_closing flag, and on no other cell", () => {
    setup();
    const closing = screen.getAllByTestId("ew-left").filter((el) => el.classList.contains("closing"));
    expect(closing).toHaveLength(1);
    expect(closing[0]).toHaveTextContent("1 session left: Oct 28");
  });

  it("draws the as-of line through every row and a bar only for a clock the card has", () => {
    setup();
    expect(screen.getAllByTestId("ew-asof")).toHaveLength(4); // one through every row, all at one x
    const xs = new Set(screen.getAllByTestId("ew-asof").map((l) => l.getAttribute("x1")));
    expect(xs.size).toBe(1);
    expect(screen.getByRole("img", { name: "Date axis, Oct 27 marked" })).toHaveTextContent("as of Oct 27");
    const bars = (r: HTMLElement, id: string) => within(r).queryAllByTestId(id).length;
    expect(bars(rowOf("Card A"), "ew-entry")).toBe(1);
    expect(bars(rowOf("Card A"), "ew-hold")).toBe(1);
    expect(bars(rowOf("Card D"), "ew-entry")).toBe(0);
    expect(bars(rowOf("Card D"), "ew-hold")).toBe(0);
  });

  it("draws the catalysts as ticks, hollow once their date has passed", () => {
    setup();
    const ticks = within(rowOf("Card A")).getAllByTestId("ew-tick");
    expect(ticks).toHaveLength(2);
    expect(ticks.map((t) => t.classList.contains("past"))).toEqual([false, true]);
    expect(within(rowOf("Card B")).queryAllByTestId("ew-tick")).toHaveLength(0);
  });

  it("draws the sparkline against IWM only where the row has one", () => {
    setup();
    expect(within(rowOf("Card A")).getByTestId("ew-line")).toBeInTheDocument();
    expect(within(rowOf("Card A")).getByTestId("ew-iwm")).toBeInTheDocument();
    expect(within(rowOf("Card B")).queryByTestId("ew-line")).toBeNull();
  });

  it("fills the one readout line on hover and returns to the prompt on leave", async () => {
    setup();
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Hover a bar");
    const entry = within(rowOf("Card A")).getByTestId("ew-entry");
    await userEvent.hover(entry);
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Entry window: Oct 20 → Oct 30 · 3 sessions left");
    await userEvent.unhover(entry);
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Hover a bar");
    await userEvent.hover(within(rowOf("Card A")).getAllByTestId("ew-tick")[1]);
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Oct 22 · earnings · earnings");
  });
});

describe("EntryWindows — the Cockpit links", () => {
  it("opens the card from its name and a name from its chip", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(rowOf("Card A")).getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-a");
    await userEvent.click(within(rowOf("Card A")).getByRole("button", { name: "BBB" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-a", "BBB");
    // neither click opened the fold
    expect(screen.queryByTestId("ew-fold")).toBeNull();
  });

  it("grays a flagged name's chip and says why on hover", () => {
    setup();
    const chip = within(rowOf("Card C")).getByRole("button", { name: "DDD" });
    expect(chip).toHaveClass("flagged");
    expect(chip).toHaveAttribute("title", expect.stringContaining("left out: mixed-scale price history"));
  });
});

describe("EntryWindows — the fold", () => {
  it("opens on a click with each name's own arm night, return and linked sources, and closes on another", async () => {
    setup();
    const a = rowOf("Card A");
    expect(a).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(a);
    expect(a).toHaveAttribute("aria-expanded", "true");
    const fold = screen.getByTestId("ew-fold");
    const [aaa, bbb] = within(fold).getAllByTestId("ew-member");
    expect(aaa).toHaveTextContent("AAA");
    expect(aaa).toHaveTextContent("armed since Oct 20");
    expect(aaa).toHaveTextContent("+4.5% from the Oct 20 close · above its arm-night close");
    expect(aaa).toHaveTextContent("technical breakout · core · 8-day breakout · fired Oct 19");
    expect(bbb).toHaveTextContent("armed since Oct 23"); // joined the Armed card later
    expect(bbb).toHaveTextContent("no trigger recorded on this name");
    // the Form 4 accession links to EDGAR; the price reference stays text
    const link = within(aaa).getByRole("link", { name: "form4 0001234567-26-000123" });
    expect(link).toHaveAttribute("href", "https://www.sec.gov/Archives/edgar/data/123456/000123456726000123/");
    expect(within(aaa).getByText("price price:AAA:2026-10-19").closest("a")).toBeNull();
    // the catalysts as text, the twin of the dots
    expect(fold).toHaveTextContent("Oct 29 · readout · clinical_readout · still ahead");
    await userEvent.click(a);
    expect(screen.queryByTestId("ew-fold")).toBeNull();
  });

  it("says a flagged name is left out, never silently gone", async () => {
    setup();
    await userEvent.click(rowOf("Card C"));
    expect(screen.getByTestId("ew-fold")).toHaveTextContent("left out: mixed-scale price history");
  });
});

describe("EntryWindows — the keyboard", () => {
  it("is one tab stop: only the first row is tabbable", () => {
    setup();
    expect(rowEls().map((r) => r.tabIndex)).toEqual([0, -1, -1, -1]);
  });

  it("moves between rows with the arrow keys and shows the focused row's words", () => {
    setup();
    const [first, second] = rowEls();
    act(() => first.focus());
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Card B: Armed since Oct 23");
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    expect(screen.getByTestId("ew-readout")).toHaveTextContent("Card A: Armed since Oct 20");
    expect(second.tabIndex).toBe(0);
    expect(first.tabIndex).toBe(-1);
    fireEvent.keyDown(second, { key: "End" });
    expect(document.activeElement).toBe(rowEls()[3]);
    fireEvent.keyDown(rowEls()[3], { key: "Home" });
    expect(document.activeElement).toBe(rowEls()[0]);
  });

  it("opens and closes the fold with Enter and Space on the focused row", () => {
    setup();
    const first = rowEls()[0];
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "Enter" });
    expect(screen.getByTestId("ew-fold")).toBeInTheDocument();
    fireEvent.keyDown(first, { key: " " });
    expect(screen.queryByTestId("ew-fold")).toBeNull();
  });

  it("leaves a button inside the row its own keys", () => {
    setup();
    const btn = within(rowEls()[0]).getByRole("button", { name: "Card B" });
    fireEvent.keyDown(btn, { key: "Enter" });
    expect(screen.queryByTestId("ew-fold")).toBeNull();
  });
});

describe("EntryWindows — the quiet lines and the states", () => {
  it("names the flagged companies and states the limits", () => {
    setup();
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
    expect(screen.getByText(/The arm night is the recorded night/)).toBeInTheDocument();
    expect(screen.getByText(/The Track Record page opens on the next session's close/)).toBeInTheDocument();
  });

  it("says so when no card is Armed, still showing the work", () => {
    setup(payload({ rows: [], axis_start: null, axis_end: null, counts: counts({ armed: 0, managing: 0 }), flagged: [] }));
    expect(screen.getByTestId("ew-empty")).toHaveTextContent("No card is Armed at this as-of.");
    expect(screen.getByTestId("ew-work")).toHaveTextContent("0 Armed rows");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("points at the toggle when only Managing rows exist", () => {
    setup(payload({ rows: [MANAGING], counts: counts({ armed: 0, managing: 1 }), flagged: [] }));
    expect(screen.getByTestId("ew-empty")).toHaveTextContent("Managing rows are behind the toggle");
  });

  it("shows a loading note, an error note, and keeps the old view while a new as-of loads", () => {
    h.useEntryWindows.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    const { unmount } = render(<EntryWindows asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/Reading each card's recorded call/)).toBeInTheDocument();
    unmount();
    h.useEntryWindows.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    const failed = render(<EntryWindows asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Entry Windows.")).toBeInTheDocument();
    failed.unmount();
    h.useEntryWindows.mockReturnValue({ data: payload(), isLoading: false, isFetching: true, error: null });
    render(<EntryWindows asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("updating…")).toBeInTheDocument();
    expect(rowEls().length).toBe(4);
  });

  it("draws the rows of the CLOSING and CLOSED fixtures from the same payload shape (guards the fixtures)", () => {
    expect([CLOSING.state, CLOSED.state, NO_CLOCKS.state]).toEqual(["armed", "armed", "armed"]);
  });
});
