import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CONTEXT_ROLE_DESCRIPTION } from "../../../workbench/format";
import ReadThrough from "../ReadThrough";
import type { ReadThroughOut } from "../useReadThrough";
import { EDGAR, MIXED, payload, row } from "./fixtures";

// The Read-Throughs body with its one hook stubbed: the table in the server's default order, one line per row
// (company chip, role tag, card chips, business type, last filing, next catalyst, the two tape cells), the tabs
// (a role with no rows grayed with 0, never hidden), every sort, the filters (and the ones that cannot
// discriminate left off), a flagged price history grayed with its reason, the chips opening the Cockpit, the
// work lines, the cards with no role, the limits, and the empty / slow / error states. The hook's request has
// its own test.

const h = vi.hoisted(() => ({ useReadThrough: vi.fn() }));
vi.mock("../useReadThrough", () => ({ useReadThrough: h.useReadThrough }));

function setup(data: ReadThroughOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useReadThrough.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<ReadThrough asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const lines = () => screen.queryAllByTestId("rt-row");
const keys = () =>
  lines().map((r) => `${r.querySelector(".rt-chip")?.textContent}:${r.querySelector(".rt-role")?.textContent}`);
const tab = (name: RegExp) => screen.getByRole("tab", { name });

beforeEach(() => h.useReadThrough.mockReset());

describe("ReadThrough — the table", () => {
  it("draws one line per (company, role) in the default order: newest last filing first, no filing last A to Z", () => {
    setup();
    expect(keys()).toEqual(["YYY:counter-case", "XXX:payer", "ZZZ:direct read", "AAA:financier", "YYY:payer"]);
    expect(screen.getAllByRole("columnheader").map((c) => c.textContent?.replace(/[↑↓]/g, "").trim())).toEqual([
      "Company",
      "Role",
      "Cards",
      "Business type",
      "Last filing",
      "Next dated catalyst",
      "7D vs SPY",
      "30D vs SPY",
    ]);
    expect(screen.getByRole("columnheader", { name: /Last filing/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("shows the company's ticker and master name, the role as quiet lowercase text with its hover, and the business type", () => {
    setup();
    const r = lines()[1]; // XXX
    expect(within(r).getByText("XXX")).toHaveClass("rt-chip");
    expect(within(r).getByText("Xylo Corp")).toBeInTheDocument();
    const role = within(r).getByText("payer");
    expect(role).toHaveAttribute("title", CONTEXT_ROLE_DESCRIPTION.payer);
    expect(within(r).getByText(/biotech\/pharma/)).toBeInTheDocument();
    // a company with no business type shows a blank cell, and one with no name shows no name line
    const zzz = lines()[2];
    expect(zzz.querySelector(".rt-bt")?.textContent).toBe("");
    expect(lines()[3].querySelector(".rt-name")).toBeNull();
  });

  it("shows one chip per card with the links there, Context / Bench quiet, and No link for none", () => {
    setup();
    const chips = [...lines()[1].querySelectorAll(".rt-card")];
    expect(chips.map((c) => c.textContent)).toEqual(["Batteries · Context", "Grid · Turbines, Cables"]);
    expect(chips[0]).toHaveClass("quiet");
    expect(chips[1]).not.toHaveClass("quiet");
    expect(lines()[2].querySelector(".rt-card")?.textContent).toBe("Grid · No link");
  });

  it("shows the last filing as date, form and headline with an EDGAR link, and says when none is stored", () => {
    setup();
    const xxx = lines()[1];
    expect(xxx.querySelector(".rt-fil-text")?.textContent).toBe(
      "Oct 20 · 8-K · 2.02 results of operations and financial condition",
    );
    expect(xxx.querySelector(".rt-fil-text")).toHaveAttribute(
      "title",
      "Oct 20, 2026 (8 days before the as-of) · 8-K · 2.02 results of operations and financial condition",
    );
    const link = within(xxx).getByRole("link", { name: "Open XXX 8-K on EDGAR" });
    expect(link).toHaveAttribute("href", EDGAR);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // a row with no stored link has no anchor; a company with no filing says so
    expect(within(lines()[2]).queryByRole("link")).toBeNull();
    expect(within(lines()[3]).getByText("none stored in the last year")).toBeInTheDocument();
  });

  it("shows the next dated catalyst with its card and days to go, and a dash when none", () => {
    setup();
    expect(lines()[1].querySelector(".rt-cat-text")?.textContent).toBe("Nov 12 · in 15 days · Q3 earnings · Grid");
    expect(lines()[0].querySelector(".rt-cat")?.textContent).toBe("—");
  });

  it("shows the two tape cells as signed numbers, blank with a reason when there is none, and grays a flagged one", () => {
    setup();
    const xxx = lines()[1].querySelectorAll(".rt-tape");
    expect([xxx[0].textContent, xxx[1].textContent]).toEqual(["+4.8", "-7.1"]);
    expect(xxx[0]).toHaveClass("pos");
    expect(xxx[1]).toHaveClass("neg");
    expect(xxx[0].getAttribute("title")).toContain("minus SPY +1.5%");
    // a short 7-day window is blank alone
    const counter = lines()[0].querySelectorAll(".rt-tape");
    expect([counter[0].textContent, counter[1].textContent]).toEqual(["—", "+1.5"]);
    // a last close that is not SPY's: blank, the reason in the hover
    const zzz = lines()[2].querySelectorAll(".rt-tape");
    expect(zzz[0].textContent).toBe("—");
    expect(zzz[0].getAttribute("title")).toContain("is not SPY's");
    // a flagged price history: both cells gray, the reason in the hover, the row stays
    const flagged = lines()[4];
    const cells = flagged.querySelectorAll(".rt-tape");
    expect([cells[0].textContent, cells[1].textContent]).toEqual(["flagged", "flagged"]);
    expect(cells[0]).toHaveClass("gray");
    expect(cells[0].getAttribute("title")).toContain(MIXED);
    expect(within(flagged).getByText("YYY")).toBeInTheDocument();
  });

  it("opens a ticker chip on the Cockpit for that name on its first card, and a card chip on that card", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(lines()[1]).getByTitle("Open Batteries on XXX"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-batt", "XXX");
    await userEvent.click(within(lines()[1]).getByRole("button", { name: "Grid · Turbines, Cables" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid");
  });

  it("uses no badge and no color class by role", () => {
    const { container } = setup();
    expect(container.querySelectorAll("[class*='badge']")).toHaveLength(0);
    for (const r of lines()) expect(r.className).toBe("");
  });
});

describe("ReadThrough — the tabs", () => {
  it("lists All roles and one tab per role in the Chain Wave's order, each with its count; a role with none is grayed, not hidden", () => {
    setup();
    const labels = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(labels).toEqual([
      "All roles 5",
      "Payers 2",
      "Counter-cases 1",
      "Direct reads 1",
      "Participants 0",
      "Financiers 1",
      "Benchmarks 0",
    ]);
    expect(tab(/Participants/)).toHaveClass("zero");
    expect(tab(/Benchmarks/)).toHaveClass("zero");
    expect(tab(/Payers/)).not.toHaveClass("zero");
    expect(tab(/All roles/)).toHaveAttribute("aria-selected", "true");
  });

  it("narrows the table to a role and back", async () => {
    setup();
    await userEvent.click(tab(/Payers/));
    expect(tab(/Payers/)).toHaveAttribute("aria-selected", "true");
    expect(keys()).toEqual(["XXX:payer", "YYY:payer"]);
    await userEvent.click(tab(/Financiers/));
    expect(keys()).toEqual(["AAA:financier"]);
    await userEvent.click(tab(/All roles/));
    expect(lines()).toHaveLength(5);
  });

  it("says so on an empty role tab, with the table gone and the tab still there", async () => {
    setup();
    await userEvent.click(tab(/Benchmarks/));
    expect(lines()).toHaveLength(0);
    expect(screen.getByText("No company holds the benchmark role on a card counted at this as-of.")).toBeInTheDocument();
    expect(tab(/Benchmarks/)).toBeInTheDocument();
  });
});

describe("ReadThrough — sorting", () => {
  it("sorts a column on a click and flips it on a second click", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Company" }));
    expect(keys()).toEqual(["AAA:financier", "XXX:payer", "YYY:payer", "YYY:counter-case", "ZZZ:direct read"]);
    expect(screen.getByRole("columnheader", { name: /Company/ })).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(screen.getByRole("button", { name: /Company/ }));
    expect(keys()[0]).toBe("ZZZ:direct read");
    expect(screen.getByRole("columnheader", { name: /Company/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("sorts the tape columns largest first with the blanks last", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "30D vs SPY" }));
    expect(keys().slice(0, 2)).toEqual(["YYY:counter-case", "XXX:payer"]);
    await userEvent.click(screen.getByRole("button", { name: /30D vs SPY/ }));
    expect(keys().slice(0, 2)).toEqual(["XXX:payer", "YYY:counter-case"]);
  });

  it("keeps the sort when the tab changes", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Company" }));
    await userEvent.click(tab(/Payers/));
    expect(keys()).toEqual(["XXX:payer", "YYY:payer"]);
  });
});

describe("ReadThrough — the filters", () => {
  it("filters by a card name (a type-ahead), and its tab counts follow", async () => {
    setup();
    await userEvent.type(screen.getByRole("combobox", { name: "Find a card" }), "batt");
    expect(keys().sort()).toEqual(["AAA:financier", "XXX:payer", "YYY:payer"]);
    expect(tab(/All roles/).textContent).toBe("All roles 3");
    expect(tab(/Counter-cases/).textContent).toBe("Counter-cases 0");
    expect(tab(/Counter-cases/)).toHaveClass("zero");
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(lines()).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "clear filters" })).toBeNull();
  });

  it("filters by business type, including the rows with none", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Business type" }), "utilities");
    expect(keys().sort()).toEqual(["YYY:counter-case", "YYY:payer"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Business type" }), "__none__");
    expect(keys()).toEqual(["ZZZ:direct read"]);
  });

  it("keeps only the rows filed within the last N days", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filed in the last" }), "30");
    expect(keys()).toEqual(["YYY:counter-case", "XXX:payer"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Filed in the last" }), "365");
    expect(keys()).toEqual(["YYY:counter-case", "XXX:payer", "ZZZ:direct read"]);
  });

  it("says so when no row passes, and counts what is in view", async () => {
    setup();
    await userEvent.type(screen.getByRole("combobox", { name: "Find a card" }), "nonesuch");
    expect(screen.getByText("No row passes the filters.")).toBeInTheDocument();
    expect(lines()).toHaveLength(0);
  });

  it("leaves a control off when it cannot discriminate", () => {
    const one = row("only", { business_type: "utilities" });
    setup(payload({ rows: [one], rows_with_filing: 0 }));
    expect(screen.queryByRole("combobox", { name: "Find a card" })).toBeNull(); // one card
    expect(screen.queryByRole("combobox", { name: "Business type" })).toBeNull(); // one type
    expect(screen.queryByRole("combobox", { name: "Filed in the last" })).toBeNull(); // no row has a filing
  });
});

describe("ReadThrough — the work and the notes", () => {
  it("says what the table rests on, in quiet lines", () => {
    setup();
    expect(screen.getByText("as of Oct 28, 2026 · as it was public by Oct 28, 2026 · cached")).toBeInTheDocument();
    expect(screen.getByText("3 cards counted · 1 not yet created · 2 with at least one role · 1 with none")).toBeInTheDocument();
    expect(screen.getByText("6 roles set · 4 companies · 5 rows (one per company and role)")).toBeInTheDocument();
    expect(screen.getByText(/^rows by role: Payers 2 · Counter-cases 1/)).toBeInTheDocument();
    expect(screen.getByText("3 of 5 rows have a filing-derived fact in the last 365 days")).toBeInTheDocument();
    expect(screen.getByText(/^tape: 7 and 30 trading days of closes, the company minus SPY/)).toBeInTheDocument();
    expect(screen.getByText("1 row has a last close that is not SPY's, so its tape is not compared")).toBeInTheDocument();
  });

  it("names the flagged price histories through the shared note", async () => {
    setup();
    const summary = screen.getByText("1 name left out for a flagged price history");
    await userEvent.click(summary);
    expect(screen.getByText(MIXED)).toBeInTheDocument();
  });

  it("lists the cards with no role, each opening its card, so the page is also the to-do list for roles", async () => {
    const { onSelectThesis } = setup();
    const summary = screen.getByText("1 card has no role set — roles are set on a card's basket");
    await userEvent.click(summary);
    await userEvent.click(screen.getByRole("button", { name: "Quiet" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-quiet");
  });

  it("lists the role rows with no resolved security and the roster fallback, only when there are any", () => {
    setup(payload({ unresolved: ["QQQ"], unresolved_rows: 1, roster_fallback_cards: 2 }));
    expect(
      screen.getByText("1 role row has no resolved security, so no filing or tape can be attached and it is not a row: QQQ."),
    ).toBeInTheDocument();
    expect(screen.getByText(/2 counted cards have no roster snapshot by this as-of/)).toBeInTheDocument();
  });

  it("leaves those notes off when they do not apply", () => {
    setup(payload({ cards_without_roles: [], flagged: [], tape_ends_differ: 0 }));
    expect(screen.queryByText(/has no role set/)).toBeNull();
    expect(screen.queryByText(/left out for a flagged price history/)).toBeNull();
    expect(screen.queryByText(/no resolved security/)).toBeNull();
    expect(screen.queryByText(/no roster snapshot/)).toBeNull();
  });

  it("states the known limits", () => {
    setup();
    expect(screen.getByText(/A role names a part, not a target/)).toBeInTheDocument();
    expect(screen.getByText(/Catalysts are today's list/)).toBeInTheDocument();
    expect(screen.getByText(/Last filing means ingested facts only/)).toBeInTheDocument();
    expect(screen.getByText(/each card's roster as of the as-of/)).toBeInTheDocument();
  });

  it("counts the rows in view against the whole", async () => {
    setup();
    expect(screen.getByText("5 rows")).toBeInTheDocument();
    await userEvent.click(tab(/Payers/));
    expect(screen.getByText("showing 2 of 5 rows")).toBeInTheDocument();
  });
});

describe("ReadThrough — states", () => {
  it("says the first load is slow while it loads", () => {
    h.useReadThrough.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    render(<ReadThrough asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
  });

  it("says so when it cannot load", () => {
    h.useReadThrough.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<ReadThrough asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Read-Throughs.")).toBeInTheDocument();
  });

  it("explains an empty table, still lists the cards with no role, and draws no table or tabs", () => {
    setup(
      payload({
        rows: [],
        rows_with_filing: 0,
        role_tags: 0,
        companies: 0,
        cards_with_roles: 0,
        cards_without_roles: [
          { thesis_id: "t-a", thesis_name: "A" },
          { thesis_id: "t-b", thesis_name: "B" },
        ],
        by_role: [],
        flagged: [],
        tape_ends_differ: 0,
      }),
    );
    expect(screen.getByText(/No company carries a context role on a card counted at this as-of/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByText("2 cards have no role set — roles are set on a card's basket")).toBeInTheDocument();
  });

  it("keeps the previous table on screen and says so while another as-of loads", () => {
    h.useReadThrough.mockReturnValue({
      data: payload({ asof: "2026-10-21" }),
      isLoading: false,
      isFetching: true,
      error: null,
    });
    render(<ReadThrough asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(lines()).toHaveLength(5);
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
  });
});
