import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HotBoard from "../HotBoard";
import type { HotBoardOut } from "../useHotBoard";
import { ASOF, CARD_A, CARD_B, MIXED, cell, clocks, payload, row, sample, tape } from "./fixtures";

// The Hot Board body with its one hook stubbed: the table in heat order (the server's rows, the heat recomputed
// among the rows shown), the toggles and filters that change it, every sort, the flagged name that keeps its row,
// the chips opening the Cockpit, the state chip and the one accent, the work lines, the stated formula, and the
// empty / slow / error states. The hook's request has its own test.

const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../useHotBoard", async (orig) => ({ ...(await orig<typeof import("../useHotBoard")>()), useHotBoard: h.hook }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(data: HotBoardOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.hook.mockReturnValue(ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(<HotBoard asof={ASOF} onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const lines = () => screen.queryAllByTestId("hb-row");
const tickers = () => lines().map((r) => r.querySelector(".hb-tkbtn")?.textContent);
const cells = (r: HTMLElement) => [...r.querySelectorAll("td")];
const header = (name: RegExp) => screen.getByRole("columnheader", { name });
const rowOf = (ticker: string) => lines().find((r) => r.querySelector(".hb-tkbtn")?.textContent === ticker) as HTMLElement;

beforeEach(() => h.hook.mockReset());

describe("HotBoard — the rows", () => {
  it("shows the armed names in heat order, an unscored row last, and nothing a toggle has not added", () => {
    setup();
    expect(tickers()).toEqual(["AAA", "CCC", "BBB", "FLG"]);
    expect(screen.getByTestId("hb-count")).toHaveTextContent("4 rows · heat is among the rows shown");
  });

  it("heads the columns in order", () => {
    setup();
    expect(
      screen.getAllByRole("columnheader").map((c) => c.querySelector("button")?.textContent?.replace(/[↑↓]/g, "").trim() ?? c.textContent),
    ).toEqual([
      "Ticker",
      "Card",
      "heat (stated formula)",
      "Call",
      "Grade",
      "Setup strength",
      "Keys",
      "Risk",
      "Arm night",
      "Sess. since",
      "Sess. left",
      "To exit-by",
      "Since arm",
      "IWM",
      "RVOL 20",
      "vs 50d",
      "Off 52w high",
      "vs IWM 30D",
      "7D",
      "30D",
      "Ins 30d",
      "$vol 20d",
      "Cap ≈",
      "90d",
    ]);
    expect(header(/^heat/)).toHaveAttribute("aria-sort", "descending");
  });

  it("shows each armed row's call, clocks, return, tape and cap", () => {
    setup();
    const r = cells(rowOf("AAA")).map((c) => c.textContent);
    expect(r[1]).toContain("Card A");
    expect(r[3]).toBe("CORE entry");
    expect(r[4]).toBe("core");
    expect(r[5]).toBe("0.62");
    expect(r[6]).toContain("insider");
    expect(r[8]).toBe("Oct 28");
    expect(r[9]).toBe("0");
    expect(r[10]).toBe("2");
    expect(r[11]).toBe("34d");
    expect(r[12]).toBe("0.0%");
    expect(r[14]).toBe("3.1×");
    expect(r[17]).toContain("+8.0 pp");
    expect(r[20]).toBe("2/2");
    expect(r[22]).toBe("$500.0M small");
  });

  it("shows heat as a number with a gray bar and its four parts on hover", () => {
    setup();
    const heatCell = rowOf("AAA").querySelector(".hb-heat") as HTMLElement;
    expect(heatCell.querySelector(".hb-heat-bar")).not.toBeNull();
    expect(heatCell.getAttribute("title")).toMatch(/^fresh \d\.\d\d · loud \d\.\d\d · leading \d\.\d\d · unextended \d\.\d\d$/);
    expect(Number(heatCell.textContent)).toBeGreaterThan(0);
  });

  it("keeps a flagged price history's row, grays its tape with the reason and gives it no heat", () => {
    setup();
    const flg = rowOf("FLG");
    expect(flg.querySelector(".hb-heat-n")?.textContent).toBe("—");
    expect(flg.querySelector(".hb-heat-bar")).toBeNull();
    const gray = [...flg.querySelectorAll(".hb-cell.gray")].map((c) => c.getAttribute("title"));
    expect(gray).toContain(`flagged: ${MIXED}`);
    expect(flg.querySelector(".hb-spark")).toBeNull();
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
  });

  it("puts the page's one accent on the entry window with at most two sessions left, and nowhere else", () => {
    setup();
    expect(document.querySelectorAll(".hb-cell.accent")).toHaveLength(1);
    expect(rowOf("AAA").querySelector(".hb-cell.accent")?.textContent).toBe("2");
    expect(rowOf("BBB").textContent).toContain("closed 2 ago");
  });

  it("tints only a signed return with its sign and draws a sparkline per row", () => {
    setup();
    expect(rowOf("BBB").querySelector(".hb-cell.neg")?.textContent).toBe("-4.5%");
    expect(rowOf("CCC").querySelector(".hb-cell.pos")?.textContent).toBe("+22.5%");
    expect(rowOf("AAA").querySelector(".hb-spark")).not.toBeNull();
  });

  it("marks a name also armed on another card and a relative-strength high", () => {
    setup();
    expect(rowOf("AAA").querySelector(".hb-also")).toHaveTextContent("also armed on 1 other card");
    expect(rowOf("AAA").querySelector(".hb-also")).toHaveAttribute("title", "Also armed on: Card B");
    expect(rowOf("AAA").querySelector(".hb-mark")?.textContent).toBe("RS");
    expect(rowOf("BBB").querySelector(".hb-mark")).toBeNull();
    expect(rowOf("BBB").querySelector(".hb-also")).toBeNull();
  });

  it("lists the trigger's detail with its source as a link in the keys' hover panel", () => {
    setup();
    const tip = rowOf("AAA").querySelector(".hb-tip") as HTMLElement;
    expect(tip).toHaveTextContent("Insider cluster buy · insider · core · fired 2026-10-20");
    const link = within(tip).getByRole("link");
    expect(link).toHaveAttribute("href", expect.stringContaining("sec.gov/Archives/edgar/data/9999999"));
    expect(link).toHaveTextContent("form4 0001234567-26-000001");
    // a trigger with no source says so
    expect(rowOf("BBB").querySelector(".hb-tip")).toHaveTextContent("no source stated");
  });
});

describe("HotBoard — opening the Cockpit", () => {
  it("opens the card and the name from the ticker, and the card alone from the card chip", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(rowOf("BBB")).getByRole("button", { name: "BBB" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_B, "BBB");
    await userEvent.click(within(rowOf("BBB")).getByRole("button", { name: "Card B" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_B);
  });
});

describe("HotBoard — the toggles", () => {
  it("renders a toggle only when it adds rows, with its count", () => {
    setup(payload(Object.values(sample()).filter((r) => r.kind !== "watch")));
    expect(screen.getByLabelText(/Warming names/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Managing/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Watch names/)).toBeNull();
    expect(screen.getByLabelText(/Warming names/).closest("label")).toHaveTextContent("+1");
  });

  it("adds the rows of a toggle and recomputes the heat among the rows then shown", async () => {
    setup();
    const before = Number(rowOf("AAA").querySelector(".hb-heat-n")?.textContent);
    await userEvent.click(screen.getByLabelText(/Managing/));
    expect(tickers()).toContain("MNG");
    expect(screen.getByTestId("hb-count")).toHaveTextContent("5 rows");
    const after = Number(rowOf("AAA").querySelector(".hb-heat-n")?.textContent);
    expect(after).not.toBe(before); // MNG joined the pool of rows AAA is ranked against
    await userEvent.click(screen.getByLabelText(/Warming names/));
    const warming = rowOf("WRM");
    expect(warming).toHaveTextContent("conviction key");
    expect(warming.querySelector(".hb-kind.warming")?.textContent).toBe("warming");
    expect(warming.querySelector(".hb-heat-n")?.textContent).toBe("—"); // no arm night: no heat
    await userEvent.click(screen.getByLabelText(/Watch names/));
    expect(rowOf("WTC")).toHaveTextContent("confirmation only");
    expect(screen.getByText(/the armed names plus 1 managing and 1 warming and 1 watch|the armed names plus 1 warming and 1 watch and 1 managing/)).toBeInTheDocument();
  });

  it("shows no state chip on an armed row (true of every row, it would carry nothing)", () => {
    setup();
    expect(document.querySelectorAll(".hb-kind")).toHaveLength(0);
  });
});

describe("HotBoard — the filters", () => {
  it("offers only the controls that discriminate and restates the row count", async () => {
    setup();
    expect(screen.getByRole("group", { name: "Grade" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Card" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Cap tier" })).toBeInTheDocument();
    expect(screen.getByLabelText("Entry window open only")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("group", { name: "Grade" })).getByRole("button", { name: "flip" }));
    expect(tickers()).toEqual(["BBB"]);
    expect(screen.getByTestId("hb-count")).toHaveTextContent("1 row of 4 after the filters");
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(tickers()).toHaveLength(4);
  });

  it("filters by card, cap tier and an open entry window", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Card" }), CARD_B);
    expect(tickers().sort()).toEqual(["BBB", "CCC"]);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Cap tier" })).getByRole("button", { name: "large" }));
    expect(tickers()).toEqual(["CCC"]);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    await userEvent.click(screen.getByLabelText("Entry window open only"));
    expect(tickers().sort()).toEqual(["AAA", "FLG"]);
  });

  it("says so when no row passes the filters, and offers a way back", async () => {
    setup(payload([row({ thesis_id: CARD_A, entry_grade: "core" }), row({ thesis_id: CARD_B, thesis_name: "Card B", entry_grade: "flip" })]));
    await userEvent.click(within(screen.getByRole("group", { name: "Grade" })).getByRole("button", { name: "flip" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Card" }), CARD_A);
    expect(screen.getByText("No row passes the filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "clear filters" })).toBeInTheDocument();
  });

  it("renders no filter control that every row would share", () => {
    setup(payload([row(), row()]));
    expect(screen.queryByRole("group", { name: "Grade" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Card" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Cap tier" })).toBeNull();
    expect(screen.queryByLabelText("Entry window open only")).toBeNull();
  });
});

describe("HotBoard — the sorts", () => {
  it("sorts a column on a click: a figure biggest first, again to flip, a blank last", async () => {
    setup();
    await userEvent.click(within(header(/^RVOL 20/)).getByRole("button"));
    expect(tickers()).toEqual(["AAA", "CCC", "BBB", "FLG"]);
    expect(header(/^RVOL 20/)).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(within(header(/^RVOL 20/)).getByRole("button"));
    expect(tickers()).toEqual(["BBB", "CCC", "AAA", "FLG"]);
    expect(header(/^RVOL 20/)).toHaveAttribute("aria-sort", "ascending");
  });

  it("sorts the ticker A to Z and goes back to heat", async () => {
    setup();
    await userEvent.click(within(header(/^Ticker/)).getByRole("button"));
    expect(tickers()).toEqual(["AAA", "BBB", "CCC", "FLG"]);
    await userEvent.click(screen.getByRole("button", { name: "back to heat" }));
    expect(tickers()).toEqual(["AAA", "CCC", "BBB", "FLG"]);
    expect(screen.queryByRole("button", { name: "back to heat" })).toBeNull();
  });

  it("sorts every figure column", async () => {
    setup();
    for (const name of [/^Card/, /^Call/, /^Grade/, /^Setup/, /^Keys/, /^Risk/, /^Arm night/, /^Sess\. since/, /^Sess\. left/, /^To exit-by/, /^Since arm/, /^IWM/, /^vs 50d/, /^Off 52w/, /^vs IWM/, /^7D/, /^30D/, /^Ins 30d/, /^\$vol/, /^Cap/]) {
      await userEvent.click(within(header(name)).getByRole("button"));
      expect(tickers(), String(name)).toHaveLength(4);
    }
    // the sparkline column is a picture, not a figure: no sort button
    expect(within(header(/^90d/)).queryByRole("button")).toBeNull();
  });
});

describe("HotBoard — the work lines, the formula and the limits", () => {
  it("states the formula in force under the table", () => {
    setup();
    const f = screen.getByTestId("hb-formula");
    expect(f).toHaveTextContent("Heat (stated formula): the mean of four percentiles, shown 0 to 100");
    expect(f).toHaveTextContent("fresh: fewer sessions since the arm night ranks higher");
    expect(f).toHaveTextContent("A row missing a part reads — and sorts last");
  });

  it("shows the work: the as-of and clock, the cards by state, the rows, the calendar and the cache", () => {
    setup();
    expect(screen.getByText(`as of ${ASOF}, read on the public clock: what was public by the end of that date`)).toBeInTheDocument();
    expect(screen.getByText("4 cards counted: 2 armed · 1 warming · 1 managing · 0 incubating · 0 no call yet")).toBeInTheDocument();
    expect(screen.getByText(/sessions are IWM's bars/)).toBeInTheDocument();
    expect(screen.getByText(/computed · compute 1,234 ms/)).toBeInTheDocument();
    expect(screen.getByText(/A name on two cards is two rows/)).toBeInTheDocument();
  });

  it("opens the flagged note to the ticker and the reason", async () => {
    setup();
    await userEvent.click(screen.getByText(/1 name left out for a flagged price history/));
    const note = document.querySelector(".dash-flagged") as HTMLElement;
    expect(within(note).getByText("FLG")).toBeInTheDocument();
    expect(note).toHaveTextContent(MIXED);
  });
});

describe("HotBoard — the empty, slow and error states", () => {
  it("says so when no card's call shows an armed name, with the card counts", () => {
    setup(payload([], { cards_counted: 3, cards_no_call: 2, cards_armed: 0 }));
    expect(screen.getByText(/No counted card's recorded call in effect at this as-of shows an armed name \(3 cards counted, 2 with no call yet\)/)).toBeInTheDocument();
  });

  it("shows a slow first read and an error", () => {
    h.hook.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    const { unmount } = render(<HotBoard asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/Reading every counted card/)).toBeInTheDocument();
    unmount();
    h.hook.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<HotBoard asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Hot Board.")).toBeInTheDocument();
  });

  it("keeps the previous view on screen and says so while the next as-of loads", () => {
    setup(payload(), { isFetching: true });
    expect(tickers()).toHaveLength(4);
    expect(screen.getByText("updating…")).toBeInTheDocument();
  });

  it("asks the hook for the as-of it was handed", () => {
    setup();
    expect(h.hook).toHaveBeenCalledWith(ASOF);
  });
});

describe("HotBoard — a lone row", () => {
  it("scores a lone row at the middle (nothing to compare it with)", () => {
    setup(
      payload([
        row({ clocks: clocks({ sessions_since: 1 }), tape: tape({ rvol20: cell(1), vs_iwm: cell(1) }) }),
      ]),
    );
    expect(lines()[0].querySelector(".hb-heat-n")?.textContent).toBe("50");
  });
});
