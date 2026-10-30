import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TriggerLog from "../TriggerLog";
import type { TriggerLogOut } from "../useTriggerLog";
import { EDGAR, R1, R2, R3, R4, ROWS, page, row } from "./fixtures";

// The Trigger Log body with its hook stubbed: a grouped tape (by week, kind or card) with one row per trigger,
// every cell (recorded night, fire date and its "recorded N days after" note, ticker that opens the Cockpit, role
// glyph, kind, what it turns, grade, label, source, the state pair, live, sessions since, the since-return against
// IWM), the one accent, the chips that restate their counts and the controls that cannot discriminate left off,
// the flagged / unpriced / reconstructed rows kept and marked, the work line, the fixed "no aggregate" line, the
// limits, and the empty / slow / error states. The hook's request has its own test.

const h = vi.hoisted(() => ({ useTriggerLog: vi.fn() }));
vi.mock("../useTriggerLog", () => ({ useTriggerLog: h.useTriggerLog }));

const ok = (data: TriggerLogOut, over: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const select = vi.fn();

function show(data: TriggerLogOut = page(), asof = "2026-10-28") {
  h.useTriggerLog.mockReturnValue(ok(data));
  return render(<TriggerLog asof={asof} onSelectThesis={select} />);
}

const rows = () => screen.getAllByTestId("tl-row");
const rowFor = (label: RegExp | string) => {
  const el = screen.getByText(label).closest("[data-testid=tl-row]");
  if (!el) throw new Error(`no row for ${String(label)}`);
  return el as HTMLElement;
};
const chip = (name: RegExp | string) => screen.getByRole("button", { name });

beforeEach(() => {
  h.useTriggerLog.mockReset();
  select.mockReset();
});

describe("the tape", () => {
  it("lists one row per trigger under the week headers, newest week first, each header counting its rows by role", () => {
    show();
    expect(rows()).toHaveLength(4);
    const groups = screen.getAllByRole("rowgroup");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Week of Oct 26", "Week of Oct 19"]);
    expect(within(groups[0]).getByText("2 entry triggers")).toBeInTheDocument();
    expect(within(groups[1]).getByText("1 entry trigger · 1 risk signal")).toBeInTheDocument();
    expect(within(groups[1]).getAllByTestId("tl-row")).toHaveLength(2);
  });

  it("draws every column of a row", () => {
    show();
    const r = within(rowFor("Open-market buy, 1,000 sh"));
    expect(r.getByText("Oct 28")).toBeInTheDocument(); // recorded night
    expect(r.getByText("Oct 22")).toBeInTheDocument(); // fire date
    expect(r.getByText(/recorded 6 days after/)).toBeInTheDocument(); // its note
    expect(r.getByRole("button", { name: "AAA" })).toBeInTheDocument();
    expect(r.getByText("Card A")).toBeInTheDocument();
    expect(r.getByRole("img", { name: "entry trigger" })).toHaveTextContent("◆");
    expect(r.getByText(/insider/)).toBeInTheDocument();
    expect(r.getByText("conviction")).toBeInTheDocument(); // what it turns
    expect(r.getByText("core")).toBeInTheDocument(); // grade
    const edgar = r.getByRole("link", { name: /source 0000876543-26-000010/ });
    expect(edgar).toHaveAttribute("href", EDGAR);
    expect(edgar).toHaveAttribute("title", "form4 · 0000876543-26-000010\nshares: 1000");
    expect(r.getByLabelText("new card · warming")).toBeInTheDocument(); // state: the card's first row
    expect(r.getByText("live")).toBeInTheDocument();
    expect(r.getByText("0.0%", { selector: ".tl-ret" })).toBeInTheDocument(); // since
  });

  it("words the state pair: a move between the nights, an unchanged state as one state", () => {
    show();
    expect(within(rowFor(/Volume-backed breakout/)).getByLabelText("warming → armed")).toBeInTheDocument();
    expect(within(rowFor("Convertible note overhang")).getByLabelText("armed")).toBeInTheDocument();
  });

  it("shows the since-return with the sign tone, IWM beside it in gray, and the sessions", () => {
    show();
    const r = within(rowFor(/Volume-backed breakout/));
    const ret = r.getByText("-4.2%");
    expect(ret).toHaveClass("tl-ret", "neg");
    expect(r.getByText(/IWM \+1.0%/)).toBeInTheDocument();
    expect(r.getByText("1").closest(".tl-c-sess")).toBeInTheDocument();
  });

  it("keeps a flagged, an unpriced, a lapsed and an unreadable-card row and says why", () => {
    show();
    const flagged = within(rowFor("Convertible note overhang"));
    expect(flagged.getByText("flagged: mixed-scale price history")).toBeInTheDocument();
    expect(flagged.getByText("off the card")).toBeInTheDocument();
    expect(flagged.getByTitle("no source stated")).toBeInTheDocument();
    const unpriced = within(rowFor(/Laggard rotation/));
    expect(unpriced.getByText("unpriced")).toBeInTheDocument();
    expect(unpriced.getByText("undated")).toBeInTheDocument();
    expect(unpriced.getByText("bar Oct 19")).toHaveAttribute("title", expect.stringContaining("return: 0.04"));
    expect(unpriced.getByText("—", { selector: ".tl-c-live" })).toBeInTheDocument(); // liveness unknown
  });

  it("marks a reconstructed night with a dagger and its explanation", () => {
    show();
    const mark = within(rowFor("Convertible note overhang")).getByLabelText("reconstructed night");
    expect(mark).toHaveTextContent("†");
    expect(mark).toHaveAttribute("title", expect.stringContaining("pipeline.backfill"));
    expect(within(rowFor("Open-market buy, 1,000 sh")).queryByLabelText("reconstructed night")).toBeNull();
  });

  it("gives a risk signal the open glyph and 'risk' for what it turns, with no grade", () => {
    show();
    const r = within(rowFor("Convertible note overhang"));
    expect(r.getByRole("img", { name: "risk signal" })).toHaveTextContent("◇");
    expect(r.getByText("risk")).toBeInTheDocument();
  });

  it("draws the one accent on a row recorded on the as-of's own night, and nowhere else", () => {
    show();
    const marked = rows().filter((el) => el.classList.contains("newest"));
    expect(marked).toHaveLength(1);
    expect(within(marked[0]).getByText("Open-market buy, 1,000 sh")).toBeInTheDocument();
  });
});

describe("opening the Cockpit", () => {
  it("opens the card on the name's ticker", async () => {
    show();
    await userEvent.click(within(rowFor("Open-market buy, 1,000 sh")).getByRole("button", { name: "AAA" }));
    expect(select).toHaveBeenCalledWith("t-a", "AAA");
  });

  it("opens a name with no ticker by its security id", async () => {
    show();
    await userEvent.click(within(rowFor(/Laggard rotation/)).getByRole("button", { name: "—" }));
    expect(select).toHaveBeenCalledWith("t-b", "s-ddd");
  });

  it("opens the card from a card group's header", async () => {
    show();
    await userEvent.click(chip("By card"));
    await userEvent.click(screen.getByRole("button", { name: "Card B" }));
    expect(select).toHaveBeenCalledWith("t-b");
  });
});

describe("grouping", () => {
  it("groups by kind, then by card, and back to weeks", async () => {
    show();
    await userEvent.click(chip("By kind"));
    expect(screen.getAllByRole("rowgroup").map((g) => g.getAttribute("aria-label"))).toEqual([
      "dilution risk",
      "insider",
      "laggard",
      "technical breakout",
    ]);
    await userEvent.click(chip("By card"));
    expect(screen.getAllByRole("rowgroup").map((g) => g.getAttribute("aria-label"))).toEqual(["Card A", "Card B"]);
    expect(chip("By card")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip("By week"));
    expect(screen.getAllByRole("rowgroup")).toHaveLength(2);
    expect(rows()).toHaveLength(4); // grouping never drops a row
  });
});

describe("the filters", () => {
  it("restates each chip's count and filters by role", async () => {
    show();
    expect(chip(/^entry trigger 3$/)).toBeInTheDocument();
    expect(chip(/^risk signal 1$/)).toBeInTheDocument();
    await userEvent.click(chip(/^risk signal/));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("1 of 4")).toBeInTheDocument(); // the page restates the count
    expect(screen.getByText(/Showing 1 of 4 triggers/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(rows()).toHaveLength(4);
    expect(screen.queryByText("1 of 4")).toBeNull();
  });

  it("filters by kind, grade and card", async () => {
    show();
    await userEvent.click(chip(/^insider/));
    expect(rows()).toHaveLength(1);
    await userEvent.click(chip(/^laggard/)); // a chip set: either kind
    expect(rows()).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));

    await userEvent.click(chip(/^flip/));
    expect(rows()).toHaveLength(2);
    expect(chip(/^no grade/)).toHaveTextContent(/no grade\s*1/);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Card" }), "t-b");
    expect(rows()).toHaveLength(2);
    // the grade chip counts now follow the card filter (flip: the laggard only; core: none left on Card B)
    expect(chip(/^core/)).toHaveTextContent(/core\s*0/);
  });

  it("filters to live only and to state changed only, each restating its count", async () => {
    show();
    expect(chip(/^Live only 2$/)).toBeInTheDocument();
    expect(chip(/^State changed only 1$/)).toBeInTheDocument();
    await userEvent.click(chip(/^Live only/));
    expect(rows()).toHaveLength(2);
    await userEvent.click(chip(/^State changed only/));
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByLabelText("warming → armed")).toBeInTheDocument();
  });

  it("says when no row passes", async () => {
    show();
    await userEvent.click(chip(/^risk signal/));
    await userEvent.click(chip(/^Live only/)); // the one risk row lapsed
    expect(screen.queryAllByTestId("tl-row")).toHaveLength(0);
    expect(screen.getByText("No trigger passes the filters.")).toBeInTheDocument();
  });

  it("draws no control that cannot discriminate (WB#3)", () => {
    // one card, one role, one kind, one grade, every row live and no state moved: nothing to filter on
    const same = [
      row("a", { live: true, from_state: "armed", to_state: "armed" }),
      row("b", { label: "another buy", event_date: "2026-10-23", live: true, from_state: "armed", to_state: "armed" }),
    ];
    show(page(same));
    expect(screen.queryByRole("group", { name: "Role" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Kind" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Grade" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Card" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Live only/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /State changed only/ })).toBeNull();
    expect(rows()).toHaveLength(2);
  });
});

describe("the window", () => {
  it("threads the as-of and the window to the read, and switches the window", async () => {
    show(page(), "2026-10-28");
    expect(h.useTriggerLog).toHaveBeenLastCalledWith("2026-10-28", 90);
    expect(chip("90 days")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip("180 days"));
    expect(h.useTriggerLog).toHaveBeenLastCalledWith("2026-10-28", 180);
    await userEvent.click(chip("30 days"));
    expect(h.useTriggerLog).toHaveBeenLastCalledWith("2026-10-28", 30);
  });
});

describe("the work line, the notes and the limits", () => {
  it("prints the work, the cache state, the fixed no-aggregate line and the limits", () => {
    show();
    expect(screen.getByText(/As of Oct 28 \(public clock/)).toBeInTheDocument();
    expect(screen.getByText(/3 cards counted · 2 with a night in the window/)).toBeInTheDocument();
    expect(screen.getByText(/9 nights read · 1 reconstructed \(†\)/)).toBeInTheDocument();
    expect(screen.getByText(/2 live triggers first recorded before the window \(not listed\)/)).toBeInTheDocument();
    expect(screen.getByText("computed · 412 ms")).toBeInTheDocument();
    expect(screen.getByText("no aggregate here: medians by tier are on the Track Record page")).toBeInTheDocument();
    expect(screen.getByText(/The record shows live triggers only/)).toBeInTheDocument();
    expect(screen.getByText(/A trigger first recorded before the window is not listed, however live/)).toBeInTheDocument();
    expect(screen.getByText("As-traded closes, no dividends.")).toBeInTheDocument();
  });

  it("opens the unpriced and flagged names from a quiet fold", async () => {
    show();
    const fold = screen.getByRole("button", { name: /1 name unpriced.*1 name left unpriced for a flagged price history/ });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("CCC: flagged, mixed-scale price history")).toBeNull();
    await userEvent.click(fold);
    expect(screen.getByText("CCC: flagged, mixed-scale price history")).toBeInTheDocument();
    expect(screen.getByText("s-ddd: unpriced")).toBeInTheDocument(); // a name with no ticker, by its id
  });

  it("draws no fold when no name is set aside", () => {
    show(page(ROWS, { unpriced: [], flagged: [] }));
    expect(screen.queryByRole("button", { name: /unpriced/ })).toBeNull();
  });

  it("shows no aggregate anywhere on the page", () => {
    const { container } = show();
    const text = (container.textContent ?? "").replace("no aggregate here: medians by tier are on the Track Record page", "");
    expect(text).not.toMatch(/\b(median|mean|average|avg|win rate|hit rate|pooled)\b/i);
  });
});

describe("the empty, slow and failed states", () => {
  it("says so when no card existed by the as-of", () => {
    const empty = page([], { counts: { ...page().counts, cards_counted: 0 } });
    show(empty);
    expect(screen.getByText("No card existed by this as-of.")).toBeInTheDocument();
  });

  it("says so when nothing was first recorded in the window, still printing the work and limits", () => {
    show(page([], { window_days: 30 }));
    expect(screen.getByText(/No trigger was first recorded in the last 30 days/)).toBeInTheDocument();
    expect(screen.getByText(/The record shows live triggers only/)).toBeInTheDocument();
  });

  it("shows the slow-read note while the first read loads", () => {
    h.useTriggerLog.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    render(<TriggerLog asof="2026-10-28" onSelectThesis={select} />);
    expect(screen.getByText(/Reading the recorded calls/)).toBeInTheDocument();
  });

  it("says it could not load on an error", () => {
    h.useTriggerLog.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<TriggerLog asof="2026-10-28" onSelectThesis={select} />);
    expect(screen.getByText("Could not load the Trigger Log.")).toBeInTheDocument();
  });

  it("keeps the previous view up and says it is reading while another as-of loads", () => {
    h.useTriggerLog.mockReturnValue(ok(page(), { isFetching: true }));
    render(<TriggerLog asof="2026-10-21" onSelectThesis={select} />); // the data is for Oct 28
    expect(screen.getAllByTestId("tl-row")).toHaveLength(4);
    expect(screen.getByText(/Reading the recorded calls/)).toBeInTheDocument();
  });
});

describe("the sheet", () => {
  const HERE = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(join(HERE, "..", "trigger-log.css"), "utf8");

  it("sticks the column header and the group headers under it", () => {
    expect(css).toMatch(/\.tl-head\s*\{[^}]*position: sticky;[^}]*top: 0/);
    expect(css).toMatch(/\.tl-gh\s*\{[^}]*position: sticky;[^}]*top: 26px/);
  });

  it("tones only the signed since-return, with the app's sign tone mixed 55% into gray", () => {
    expect(css).toContain("--tl-pos: color-mix(in srgb, var(--pos) 55%, var(--txt-3))");
    expect(css).toContain("--tl-neg: color-mix(in srgb, var(--neg) 55%, var(--txt-3))");
    expect(css).toMatch(/\.tl-ret\.pos\s*\{\s*color: var\(--tl-pos\)/);
    // the sign tokens appear nowhere else
    expect(css.match(/--tl-pos|--tl-neg/g)?.length).toBe(4);
  });

  it("has ONE accent, on the as-of's own night", () => {
    expect(css.match(/--tl-accent/g)?.length).toBe(3); // its definition and its two uses
    expect(css).toMatch(/\.tl-row\.newest\s*\{[^}]*border-left-color: var\(--tl-accent\)/);
  });

  it("folds the source and state columns under the row on a phone", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 760px)"));
    expect(phone).toMatch(/grid-template-areas:[^;]*"src src state state"/);
    expect(phone).toContain(".tl-c-src { grid-area: src; }");
    expect(phone).toContain(".tl-c-state { grid-area: state; }");
  });

  it("uses the app's tokens only: no hard-coded color", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe("the fixtures", () => {
  it("are the four rows the tests above rest on", () => {
    expect(ROWS).toEqual([R1, R2, R3, R4]);
  });
});
