import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NarrativeAdoption from "../NarrativeAdoption";
import type { AdoptionOut } from "../useNarrativeAdoption";
import { AXIS, CARDS, MONTHS, absent, counted, counts, overlay, payload, term } from "./fixtures";

// The Narrative Adoption body with its two hooks stubbed: the three panels on one axis (stacked bars drawn only for
// counted months, a baseline tick for a stored zero, the filer lines, the tape), one readout fed by hover and the
// arrow keys, the term table ("not counted yet", never zero), the quiet lines, the picker, and the wording (no
// "early", no "crowded"). The hooks' requests have their own test.

const h = vi.hoisted(() => ({ useNarrativeAdoption: vi.fn(), useAdoptionCards: vi.fn() }));
vi.mock("../useNarrativeAdoption", () => ({
  useNarrativeAdoption: h.useNarrativeAdoption,
  useAdoptionCards: h.useAdoptionCards,
}));

function setup(data: AdoptionOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useAdoptionCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useNarrativeAdoption.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<NarrativeAdoption asof="2026-09-30" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const panel = (id: "docs" | "filers" | "index") => screen.getByTestId(`na-panel-${id}`);
const readout = () => screen.getByTestId("na-readout");
const hitAt = (id: "docs" | "filers" | "index", month: string) =>
  within(panel(id)).getAllByTestId("na-hit")[AXIS.indexOf(month)];
const IDX_SEP = AXIS.indexOf("2026-09-01");

beforeEach(() => {
  h.useNarrativeAdoption.mockReset();
  h.useAdoptionCards.mockReset();
});

describe("NarrativeAdoption", () => {
  it("draws three panels, each with a hit slot for every month of the one shared axis", () => {
    setup();
    for (const id of ["docs", "filers", "index"] as const) {
      expect(within(panel(id)).getAllByTestId("na-hit")).toHaveLength(36);
    }
    expect(screen.getByRole("heading", { name: "Documents per month" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Filers per month" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The tape" })).toBeInTheDocument();
  });

  it("draws a stacked bar only for a counted month, a baseline tick for a stored zero and nothing for a gap", () => {
    setup();
    const bars = within(panel("docs")).getAllByTestId("na-bar");
    expect(bars.map((b) => Number(b.getAttribute("data-index")))).toEqual([
      AXIS.indexOf("2026-06-01"),
      AXIS.indexOf("2026-07-01"),
      IDX_SEP,
    ]);
    expect(within(panel("docs")).getAllByTestId("na-zero")).toHaveLength(1); // July: stored, nothing matched
    // August failed to read: no bar and no tick at its slot
    expect(bars.some((b) => b.getAttribute("data-index") === String(AXIS.indexOf("2026-08-01")))).toBe(false);
    // September stacks the four groups in order; June has no issuance, so three
    const sep = bars[2].querySelectorAll(".na-seg");
    expect([...sep].map((r) => r.getAttribute("class"))).toEqual([
      "na-seg na-g-reports",
      "na-seg na-g-events",
      "na-seg na-g-issuance",
      "na-seg na-g-other",
    ]);
    expect(bars[0].querySelectorAll(".na-seg")).toHaveLength(3);
  });

  it("legends the four muted fills and states the double count only when more than one term was summed", () => {
    setup();
    for (const label of ["Reports", "8-Ks", "Issuance", "Other"]) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByTestId("na-double-count")).toHaveTextContent("a filing that matches two terms counts twice");
    h.useNarrativeAdoption.mockReturnValue({
      data: payload({ months: MONTHS.map((m) => (m.counted ? { ...m, terms_counted: 1 } : m)) }),
      isLoading: false,
      isFetching: false,
      error: null,
    });
    render(<NarrativeAdoption asof="2026-09-30" onSelectThesis={vi.fn()} />);
    expect(screen.getAllByTestId("na-double-count")).toHaveLength(1); // the second render states none
  });

  it("draws the filer and new-filer lines, ringing only the 'at least' months", () => {
    setup();
    expect(screen.getByTestId("na-filers-line")).toBeInTheDocument();
    expect(screen.getByTestId("na-new-line")).toBeInTheDocument();
    expect(within(panel("filers")).getAllByTestId("na-ring")).toHaveLength(1); // September only
    expect(screen.getByText("at least")).toBeInTheDocument(); // the legend key exists because a ring does
    expect(screen.getByText(/A ringed month reads "at least"/)).toBeInTheDocument();
  });

  it("draws no ring when every counted month reads 'at least', and says so once instead", () => {
    setup(payload({ months: MONTHS.map((m) => (m.counted ? { ...m, filers_sampled: true } : m)) }));
    expect(within(panel("filers")).queryAllByTestId("na-ring")).toHaveLength(0);
    expect(screen.getByText(/Every month reads "at least"/)).toBeInTheDocument();
  });

  it("draws the card line and SPY, or says why one is missing", () => {
    setup();
    expect(screen.getByTestId("na-card-line")).toBeInTheDocument();
    expect(screen.getByTestId("na-spy-line")).toBeInTheDocument();
    expect(screen.queryByText(/No SPY line/)).toBeNull();
    setup(payload({ overlay: overlay({ spy: null, spy_reason: "no SPY tape at this as-of" }) }));
    expect(screen.getByText(/No SPY line: no SPY tape at this as-of\./)).toBeInTheDocument();
    expect(screen.getAllByTestId("na-spy-line")).toHaveLength(1); // only the first render drew it
  });

  it("names the companies left out of the tape for a flagged price history, quietly", () => {
    setup();
    const note = screen.getByText(/1 name left out for a flagged price history/);
    expect(note.closest("details")).not.toBeNull();
    expect(screen.getByText("BBB")).toBeInTheDocument();
  });
});

describe("the readout", () => {
  it("starts as a prompt and shows a month's counts on hover", async () => {
    setup();
    expect(readout()).toHaveTextContent("Hover a month for its counts");
    await userEvent.hover(hitAt("docs", "2026-09-01"));
    expect(readout()).toHaveTextContent("Sep 2026: 54 documents (Reports 30 · 8-Ks 8 · Issuance 12 · Other 4)");
    expect(readout()).toHaveTextContent("most documents: alpha 40 · beta 14");
    expect(readout()).toHaveTextContent("filers: at least 20");
    expect(readout()).toHaveTextContent("only 2 of 3 terms stored for this month: the sums are a floor");
    expect(readout()).toHaveTextContent("counted before the month closed");
    expect(readout()).toHaveTextContent("the tape on");
    expect(screen.getAllByTestId("na-cursor")).toHaveLength(3); // the column is drawn in every panel
  });

  it("follows the pointer from any panel to the same month", async () => {
    setup();
    await userEvent.hover(hitAt("index", "2026-06-01"));
    expect(readout()).toHaveTextContent("Jun 2026: 13 documents");
    await userEvent.hover(hitAt("filers", "2026-09-01"));
    expect(readout()).toHaveTextContent("Sep 2026: 54 documents");
    // every panel draws the cursor column
    expect(screen.getAllByTestId("na-cursor")).toHaveLength(3);
  });

  it("says a failed month is blank, never zero, and that the as-of's own month is not counted yet", async () => {
    setup();
    await userEvent.hover(hitAt("docs", "2026-08-01"));
    expect(readout()).toHaveTextContent("Aug 2026: no count stored for this month (a failed or deferred month is left blank, never shown as zero)");
    setup(payload({ months: [...MONTHS.slice(1), absent("2026-10-01", { closed: false })] }));
    const hit = within(screen.getAllByTestId("na-panel-docs")[1]).getAllByTestId("na-hit")[35];
    await userEvent.hover(hit);
    expect(screen.getAllByTestId("na-readout")[1]).toHaveTextContent("the month had not closed at the as-of");
  });

  it("steps through the months with the arrow keys from the newest counted one, and Escape clears", async () => {
    setup();
    const frame = screen.getByTestId("na-frame");
    act(() => frame.focus());
    expect(readout()).toHaveTextContent("Sep 2026: 54 documents"); // focus starts on the newest counted month
    fireEvent.keyDown(frame, { key: "ArrowLeft" });
    expect(readout()).toHaveTextContent("Aug 2026: no count stored");
    fireEvent.keyDown(frame, { key: "ArrowLeft" });
    expect(readout()).toHaveTextContent("Jul 2026: 0 documents");
    fireEvent.keyDown(frame, { key: "ArrowRight" });
    fireEvent.keyDown(frame, { key: "ArrowRight" });
    fireEvent.keyDown(frame, { key: "ArrowRight" }); // stops at the last month
    expect(readout()).toHaveTextContent("Sep 2026: 54 documents");
    fireEvent.keyDown(frame, { key: "Home" });
    expect(readout()).toHaveTextContent("Oct 2023: no count stored");
    fireEvent.keyDown(frame, { key: "End" });
    expect(readout()).toHaveTextContent("Sep 2026");
    fireEvent.keyDown(frame, { key: "Escape" });
    expect(readout()).toHaveTextContent("Hover a month for its counts");
  });

  it("has one tab stop for the three panels", () => {
    setup();
    expect(screen.getByTestId("na-frame")).toHaveAttribute("tabindex", "0");
    for (const id of ["docs", "filers", "index"] as const) expect(panel(id)).toHaveAttribute("aria-hidden", "true");
  });
});

describe("the term table", () => {
  it("lists each term in the server's order, a term with no stored month reading 'not counted yet'", () => {
    setup();
    const rows = screen.getAllByTestId("na-term");
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(["alpha", "beta", "gamma"]);
    expect(rows[2]).toHaveTextContent("not counted yet");
    expect(rows[2]).not.toHaveTextContent(/\b0\b/);
    expect(within(rows[2]).getAllByRole("cell")).toHaveLength(2); // the name and one spanning cell
  });

  it("shows the last month, the windows with their stored months, the filers and the first month", () => {
    setup();
    const [alpha, beta] = screen.getAllByTestId("na-term");
    const cells = (r: HTMLElement) => within(r).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(alpha)).toEqual(["alpha", "40", "49 (2 of 3 months)", "55 (3 of 12 months)", "at least 15", "Jun 2026 or earlier"]);
    expect(cells(beta)).toEqual(["beta", "14", "18", "18 (3 of 12 months)", "6", "Jul 2026"]);
    expect(screen.getByRole("columnheader", { name: "Last month (Sep 2026)" })).toBeInTheDocument();
  });

  it("shows a dash, never zero, for a counted term with nothing stored in the last month", () => {
    setup(payload({ terms: [term("delta", { last_month_documents: null, last_3_documents: 5, months_last_3: 1, filers: null })] }));
    expect(within(screen.getByTestId("na-term")).getAllByRole("cell")[1]).toHaveTextContent("—");
  });

  it("says each term is counted alone, so the rows do not add up to the panels", () => {
    setup();
    expect(screen.getByText(/Each term is counted alone, so the rows do not add up to the panels above/)).toBeInTheDocument();
  });

  it("says a card with no SIGNAL terms counts no filings", () => {
    setup(payload({ terms: [], months: AXIS.map((m) => absent(m)), counts: counts({ signal_terms: 0, terms_counted: 0, terms_not_counted: 0, months_counted: 0, last_counted_month: null }) }));
    expect(screen.getAllByText("This card has no SIGNAL terms, so no filings are counted for it.").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("na-terms")).toBeNull();
  });

  it("an empty curve says no month is counted yet in both panels and still draws the tape", () => {
    setup(payload({ months: AXIS.map((m) => absent(m)), counts: counts({ months_counted: 0, terms_counted: 0, terms_not_counted: 3, last_counted_month: null }) }));
    expect(screen.getAllByText("no month counted yet")).toHaveLength(2);
    expect(within(panel("docs")).queryAllByTestId("na-bar")).toHaveLength(0);
    expect(screen.getByTestId("na-card-line")).toBeInTheDocument();
  });
});

describe("the page", () => {
  it("shows the work on one quiet line and the limits", () => {
    setup();
    const work = screen.getByTestId("na-work");
    expect(work).toHaveTextContent("Card A as of Sep 30, 2026");
    expect(work).toHaveTextContent("2 SIGNAL terms counted of 3, 1 not counted yet");
    expect(work).toHaveTextContent("months stored 3 of 36");
    expect(work).toHaveTextContent("last count run Oct 24, 2026");
    expect(screen.getByText(/A blank is never a zero/)).toBeInTheDocument();
    expect(screen.getByText(/a term set has no history/)).toBeInTheDocument();
    expect(screen.queryByText(/Today's roster stands in/)).toBeNull();
  });

  it("says when today's roster stands in for the tape", () => {
    setup(payload({ roster_fallback: true }));
    expect(screen.getByText(/Today's roster stands in for this card's line/)).toBeInTheDocument();
  });

  it("opens the card in the Cockpit from its title", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenCalledWith("t-a");
  });

  it("picks a card from the dropdown with its link count, and the picked card is what the hook reads", async () => {
    setup();
    expect(h.useNarrativeAdoption).toHaveBeenLastCalledWith("2026-09-30", "t-a");
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Card A · 4 links",
      "Card B · 3 links",
      "Card C · 2 links",
    ]);
    await userEvent.selectOptions(select, "t-b");
    expect(h.useNarrativeAdoption).toHaveBeenLastCalledWith("2026-09-30", "t-b");
  });

  it("filters the picker by name and keeps the card on screen", async () => {
    setup();
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a card" }), "card c");
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Card A · 4 links", "Card C · 2 links"]);
    expect(screen.getByText("1 match of 3")).toBeInTheDocument();
  });

  it("says it is reading while the first load runs, and when a load fails or no card existed", () => {
    setup(undefined, { isLoading: true, isFetching: true });
    expect(screen.getByText(/Reading this card's counts/)).toBeInTheDocument();
    setup(undefined, { error: new Error("x") });
    expect(screen.getByText("Could not load the Narrative Adoption.")).toBeInTheDocument();
    h.useAdoptionCards.mockReturnValue({ data: { ...CARDS, cards: [] }, isLoading: false, error: null });
    render(<NarrativeAdoption asof="2026-09-30" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("No card existed by this as-of.")).toBeInTheDocument();
  });

  it("says nothing early or crowded anywhere on the page", async () => {
    const { container } = setup();
    await userEvent.hover(hitAt("docs", "2026-09-01"));
    expect(container.textContent ?? "").not.toMatch(/\b(early|crowded)\b/i);
    expect(counted("2026-09-01", {}).month).toBe("2026-09-01"); // the fixture builders stay in use
  });
});
