import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TrackRecord from "../TrackRecord";
import type { TrackRecordOut } from "../useTrackRecord";
import { SERVER, cap, entry, payload, row, span } from "./fixtures";

// The Track Record body with its one hook stubbed: the table (the tiers in size order, unpinned, the pooled row
// LAST), every cell's text and hover, a cell under ten episodes printing only its n, the slice / comparison / entry
// toggles re-tabling the page with no request, the chips narrowing every cell, a cell click filtering the list, the
// list's Cockpit links, the folded sections (unpinned, left out) each counted, the work lines, and the loading /
// empty / error states. The hook's request has its own test.

const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../useTrackRecord", () => ({ useTrackRecord: h.hook }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(data: TrackRecordOut | undefined = SERVER, extra: Record<string, unknown> = {}) {
  h.hook.mockReturnValue(ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(<TrackRecord asof="2026-07-24" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

beforeEach(() => h.hook.mockReset());

const tableKeys = () => screen.getAllByTestId("tr-table-row").map((r) => r.getAttribute("data-row"));
const cell = (row: string, horizon: string) => screen.getByTestId(`tr-cell-${row}-${horizon}`);
const cellText = (row: string, horizon: string) => cell(row, horizon).textContent;
const listTickers = () => screen.queryAllByTestId("tr-row").map((r) => r.querySelector(".tr-tk")?.textContent);
const click = (name: string | RegExp) => userEvent.click(screen.getByRole("button", { name }));
// the page opens on the next session's close; the hand-computed vectors below are the arm night's, one toggle away
const armNight = () => click("Arm night");

describe("TrackRecord — the table", () => {
  it("lists the tiers in size order, then unpinned, then the pooled row LAST and labeled pooled", () => {
    setup();
    expect(tableKeys()).toEqual(["nano", "micro", "small", "mid", "large", "unpinned", "pooled"]);
    const pooled = screen.getAllByTestId("tr-table-row").at(-1)!;
    expect(pooled).toHaveClass("tr-pooled");
    expect(pooled.querySelector("th")?.textContent).toBe("all tiers (pooled)");
    expect(screen.getAllByTestId("tr-table-row")[0]).not.toHaveClass("tr-pooled");
  });

  it("heads the columns with the five horizons", () => {
    setup();
    const heads = screen.getAllByRole("columnheader").map((c) => c.textContent);
    expect(heads).toEqual([
      "Cap tier at the event",
      "Episodes",
      "5 sessions",
      "10 sessions",
      "20 sessions",
      "60 sessions",
      "to exit_by",
    ]);
  });

  it("states each readable cell as the median excess and how many held, with the four things in its hover", async () => {
    setup();
    await armNight();
    expect(cellText("small", "h20")).toBe("+2.5%8/12");
    const btn = within(cell("small", "h20")).getByRole("button");
    for (const part of ["excess vs IWM", "20 sessions", "n = 12", "held 8 of 12 (67%)", "arm-night close entry"]) {
      expect(btn.getAttribute("title"), part).toContain(part);
    }
    expect(within(cell("small", "h20")).getByText("+2.5%")).toHaveClass("pos");
    expect(within(cell("small", "h5")).getByText("+2.0%")).toHaveClass("pos"); // the sign tone marks only the sign
  });

  it("prints only the n and 'too small to read' for a cell under ten episodes", () => {
    setup();
    expect(cellText("micro", "h20")).toBe("n 3too small to read");
    expect(cellText("unpinned", "h20")).toBe("n 1too small to read");
    expect(cellText("mid", "h5")).toBe("n 1too small to read");
    expect(within(cell("micro", "h20")).getByText("n 3")).toHaveClass("gray");
  });

  it("leaves an empty cell blank with 'no matured episodes' in its hover", () => {
    setup();
    expect(cellText("nano", "h20")).toBe("—");
    expect(within(cell("nano", "h20")).queryByRole("button")).toBeNull();
    expect(cell("nano", "h20").querySelector("[title]")?.getAttribute("title")).toContain("no matured episodes");
    expect(cellText("small", "h60")).toBe("—"); // nothing has reached 60 sessions
  });

  it("draws the thin cells' numbers, grayed, when asked", async () => {
    setup();
    await armNight();
    await userEvent.click(screen.getByLabelText("show thin cells"));
    expect(cellText("micro", "h20")).toBe("+1.0%2/3");
    expect(within(cell("micro", "h20")).getByText("+1.0%")).toHaveClass("gray");
    expect(within(cell("micro", "h20")).getByText("+1.0%")).not.toHaveClass("pos");
    expect(cellText("small", "h20")).toBe("+2.5%8/12"); // a readable cell is unchanged
  });

  it("counts the episodes of every row, left-out ones in none", () => {
    setup();
    const counts = Object.fromEntries(
      screen.getAllByTestId("tr-table-row").map((r) => [r.getAttribute("data-row"), r.querySelectorAll("td")[0].textContent]),
    );
    expect(counts).toEqual({ nano: "0", micro: "3", small: "12", mid: "1", large: "0", unpinned: "1", pooled: "17" });
  });
});

describe("TrackRecord — the toggles re-table the page with no request", () => {
  it("swaps the rows for the liquidity bands", async () => {
    setup();
    await click("Liquidity");
    expect(tableKeys()).toEqual(["under_1m", "1m_10m", "10m_plus", "unpinned", "pooled"]);
    expect(screen.getAllByRole("columnheader")[0].textContent).toBe("Liquidity at the event");
    expect(screen.getAllByTestId("tr-table-row").at(-1)?.querySelector("th")?.textContent).toBe("all liquidity bands (pooled)");
    expect(h.hook).toHaveBeenCalledWith("2026-07-24", 0); // the same read: the toggle is not a parameter
  });

  it("switches the comparison to SPY and to the raw return", async () => {
    setup();
    await armNight();
    await click("vs SPY");
    expect(cellText("small", "h20")).toBe("+17.5%12/12");
    expect(screen.getByText(/Each cell: median excess vs SPY/)).toBeInTheDocument();
    await click("raw");
    expect(cellText("small", "h20")).toBe("+27.5%12/12");
    expect(screen.getByText(/Each cell: median raw return/)).toBeInTheDocument();
    expect(screen.getByText(/Comparison: none shown \(the raw return\)/)).toBeInTheDocument();
  });

  it("opens on the next session's close and says so", () => {
    setup();
    expect(screen.getByRole("button", { name: "Next session" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Arm night" })).toHaveAttribute("aria-pressed", "false");
    expect(cellText("small", "h20")).toBe("−2.5%3/12");
    expect(screen.getByText(/by horizon \(next-session close entry\)/)).toBeInTheDocument();
    expect(screen.getByText(/Entry: the next session's close/)).toBeInTheDocument();
    expect(within(cell("small", "h20")).getByRole("button").getAttribute("title")).toContain("next-session close entry");
  });

  it("still reaches the arm night's figures, the Scoreboard's own entry, with the toggle", async () => {
    setup();
    await armNight();
    expect(screen.getByRole("button", { name: "Arm night" })).toHaveAttribute("aria-pressed", "true");
    expect(cellText("small", "h20")).toBe("+2.5%8/12");
    expect(screen.getByText(/by horizon \(arm-night close entry\)/)).toBeInTheDocument();
    expect(screen.getByText(/Entry: the arm night's close, the Scoreboard's own/)).toBeInTheDocument();
    await click("Next session");
    expect(cellText("small", "h20")).toBe("−2.5%3/12");
  });

  it("restates every cell under a chip filter and the line says how many are shown", async () => {
    setup();
    await armNight();
    await click(/^activist/);
    expect(cellText("small", "h20")).toMatch(/^n 5/); // five activist names left in the small tier
    expect(cellText("micro", "h20")).toBe("—"); // the micro names are all 'theme'
    expect(screen.getByText(/filters on: 5 counted episodes of 17 shown/)).toBeInTheDocument();
    await click("clear filters");
    expect(cellText("small", "h20")).toBe("+2.5%8/12");
    expect(screen.queryByText(/filters on:/)).toBeNull();
  });

  it("narrows to the headline names only", async () => {
    setup();
    await userEvent.click(screen.getByLabelText("headline names only"));
    const headline = SERVER.rows.filter((r) => r.left_out == null && r.is_headline && r.cap?.tier === "small").length;
    expect(screen.getAllByTestId("tr-table-row").find((r) => r.getAttribute("data-row") === "small")!.querySelectorAll("td")[0].textContent).toBe(
      String(headline),
    );
  });

  it("offers a chip group only when its values can tell the episodes apart", () => {
    const same = payload({ rows: [row(), row(), row()], counts: { ...SERVER.counts, episodes: 3, counted: 3 } });
    setup(same);
    expect(screen.queryByRole("group", { name: "Key 1" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Grade" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Armed with" })).toBeNull();
  });

  it("has the chip groups for the real payload", () => {
    setup();
    expect(screen.getByRole("group", { name: "Key 1" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Grade" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Armed with" })).toBeInTheDocument();
  });
});

describe("TrackRecord — the episode list", () => {
  it("lists the counted episodes with a pinned slice, newest arm first, the unpinned in their own fold", () => {
    setup();
    const tickers = listTickers().filter((t, i) => i < 16);
    expect(listTickers()).toHaveLength(16 + 1); // 16 pinned in the main list, 1 unpinned in the fold
    expect(tickers).not.toContain("UNP");
    expect(within(screen.getByTestId("tr-unpinned")).getAllByTestId("tr-row")).toHaveLength(1);
    expect(screen.getByTestId("tr-unpinned").textContent).toContain("1 counted episode with no pinned cap");
    expect(screen.getByTestId("tr-unpinned").textContent).toContain("UNP");
  });

  it("shows a name's cap tier and approximate cap, its liquidity band, its Key 1 source, grade and the five horizons", () => {
    setup(payload({ rows: [row({ ticker: "AAA", key1_source: "revenue_accel", entry_grade: "flip", next_session: entry({ h5: span(), h10: span({ status: "maturing" }), h20: span({ status: "path_ended" }) }) })] }));
    const r = screen.getByTestId("tr-row");
    expect(r.textContent).toContain("AAA");
    expect(r.textContent).toContain("small ~$576M");
    expect(r.textContent).toContain("$1M to $10M");
    expect(r.textContent).toContain("revenue accel.");
    expect(r.textContent).toContain("flip");
    const hz = [...r.querySelectorAll(".tr-hz")].map((x) => x.textContent);
    expect(hz).toEqual(["+6.0%", "maturing", "path ended", "maturing", "maturing"]);
  });

  it("opens the Cockpit from the ticker (on the name) and the card chip (on the card)", async () => {
    const { onSelectThesis } = setup(payload({ rows: [row({ ticker: "AAA" })] }));
    await userEvent.click(screen.getByRole("button", { name: "AAA" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("00000000-0000-0000-0000-0000000000a1", "AAA");
    await userEvent.click(screen.getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith("00000000-0000-0000-0000-0000000000a1");
  });

  it("filters the list to a cell's episodes on a click, and back on a second click", async () => {
    setup();
    await userEvent.click(within(cell("small", "h20")).getByRole("button"));
    expect(listTickers()).toHaveLength(12);
    expect(screen.getByText(/12 episodes in small/)).toBeInTheDocument();
    expect(within(cell("small", "h20")).getByRole("button")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(cell("small", "h20")).getByRole("button"));
    expect(listTickers()).toHaveLength(17);
    await userEvent.click(within(cell("pooled", "h20")).getByRole("button"));
    expect(listTickers()).toHaveLength(16); // the pooled cell: every counted episode matured at 20 sessions
    await userEvent.click(screen.getByRole("button", { name: "show every episode" }));
    expect(screen.queryByText(/episodes in all tiers/)).toBeNull();
  });

  it("clears a picked cell when a toggle changes the table under it", async () => {
    setup();
    await userEvent.click(within(cell("small", "h20")).getByRole("button"));
    expect(listTickers()).toHaveLength(12);
    await click("vs SPY");
    expect(listTickers()).toHaveLength(17);
  });

  it("pages a long list instead of drawing every row", async () => {
    const rows = Array.from({ length: 160 }, () => row());
    setup(payload({ rows, counts: { ...SERVER.counts, episodes: 160, counted: 160 } }));
    expect(screen.getAllByTestId("tr-row")).toHaveLength(150);
    await click(/show 10 more/);
    expect(screen.getAllByTestId("tr-row")).toHaveLength(160);
  });
});

describe("TrackRecord — what is left out is counted and kept visible", () => {
  it("folds each left-out reason into its own section with its count and its rows", () => {
    setup();
    for (const [reason, ticker, summary] of [
      ["flagged", "FLG", "1 episode left out: flagged price history"],
      ["censored_start", "CEN", "1 episode left out: censored start"],
      ["ingest_flagged", "ING", "1 episode left out: ingest flagged"],
    ]) {
      const fold = screen.getByTestId(`tr-out-${reason}`);
      expect(fold.querySelector("summary")?.textContent).toBe(summary);
      const rows = within(fold).getAllByTestId("tr-out-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain(ticker);
    }
    expect(screen.getByTestId("tr-out-censored_start").textContent).toContain("true arm date is unknown");
  });

  it("names the companies left out for a flagged price history in the shared quiet note", () => {
    setup();
    expect(screen.getByText("1 name left out for a flagged price history")).toBeInTheDocument();
  });

  it("draws a section only when something is in it", () => {
    setup(payload({ rows: [row()], flagged: [] }));
    expect(screen.queryByTestId("tr-out-flagged")).toBeNull();
    expect(screen.queryByTestId("tr-unpinned")).toBeNull();
    expect(screen.queryByText(/left out for a flagged price history/)).toBeNull();
  });

  it("keeps an unpinned-cap episode in the pooled row and out of the main list", () => {
    const unpinned = row({ ticker: "UNP", cap: cap({ value: null, tier: null, gap: "no_shares" }) });
    setup(payload({ rows: [row({ ticker: "PIN" }), unpinned] }));
    expect(tableKeys().at(-2)).toBe("unpinned");
    const counts = Object.fromEntries(
      screen.getAllByTestId("tr-table-row").map((r) => [r.getAttribute("data-row"), r.querySelectorAll("td")[0].textContent]),
    );
    expect(counts).toMatchObject({ small: "1", unpinned: "1", pooled: "2" });
    expect(listTickers()).toEqual(["PIN", "UNP"]);
  });
});

describe("TrackRecord — the edition", () => {
  const withNewest = () => payload({ newest_edition: { number: 1, label: "first re-run" } });

  it("reads the record by default and says why it is the default", () => {
    setup();
    expect(h.hook).toHaveBeenLastCalledWith("2026-07-24", 0);
    expect(screen.getByText(/edition 0: the original record \(the default; editions are paused\)/)).toBeInTheDocument();
  });

  it("draws no edition control while no edition is published (it could not discriminate)", () => {
    setup(); // the server fixture publishes none
    expect(screen.queryByRole("group", { name: "Edition" })).toBeNull();
  });

  it("offers the record and the newest published edition, and a pick refetches with that edition", async () => {
    setup(withNewest());
    const group = screen.getByRole("group", { name: "Edition" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "The record",
      "Edition 1 (newest published)",
    ]);
    expect(within(group).getByRole("button", { name: "The record" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(within(group).getByRole("button", { name: "Edition 1 (newest published)" }));
    expect(h.hook).toHaveBeenLastCalledWith("2026-07-24", 1);
    expect(within(group).getByRole("button", { name: "Edition 1 (newest published)" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "The record" })).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(within(group).getByRole("button", { name: "The record" }));
    expect(h.hook).toHaveBeenLastCalledWith("2026-07-24", 0);
  });

  it("states the edition in force on the work line when a stored edition is read", () => {
    setup(payload({ edition: { number: 1, label: "first re-run" }, newest_edition: { number: 1, label: "first re-run" } }));
    expect(screen.getByText(/edition 1: first re-run · annotated nights excluded/)).toBeInTheDocument();
    expect(screen.queryByText(/editions are paused/)).toBeNull();
  });

  it("clears a picked cell when the edition changes under it", async () => {
    setup(withNewest());
    await userEvent.click(within(cell("small", "h20")).getByRole("button"));
    expect(screen.getByRole("button", { name: "show every episode" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edition 1 (newest published)" }));
    expect(screen.queryByRole("button", { name: "show every episode" })).toBeNull();
  });
});

describe("TrackRecord — the quiet work lines and the states", () => {
  it("states the as-of, the clock, what the cells rest on, the entry rule and the comparison", () => {
    setup();
    for (const text of [
      /as of 2026-07-24 · public clock/,
      /edition 0: the original record \(the default; editions are paused\) · annotated nights excluded · late nights excluded/,
      /20 arm episodes on 2 cards · 17 in the cells · left out: 1 flagged price history, 1 censored start, 1 ingest flagged/,
      /1 with an unpinned cap · 1 with unpinned liquidity \(counted, never dropped\)/,
      /10 sessions 9 counted, 8 maturing/,
      /Entry: the next session's close, the first one after the arm night/,
      /Comparison: IWM over the same two dates/,
      /Market clock: SPY's bar dates/,
      /computed · 1,234 ms/,
    ]) {
      expect(screen.getByText(text), String(text)).toBeInTheDocument();
    }
  });

  it("states the fixed limits, including the too-small rule", () => {
    setup();
    expect(screen.getByText(/A cell under 10 episodes is too small to read: it prints its n only/)).toBeInTheDocument();
    expect(screen.getByText(/Horizons are sessions of the name's own bars, never calendar days/)).toBeInTheDocument();
    expect(screen.getByText(/Market cap is approximate and measured the session before the arm date/)).toBeInTheDocument();
    expect(screen.getByText(/The entry is a close, not a fill. The default is the next session's close/)).toBeInTheDocument();
  });

  it("never says a result works, wins or deserves anything", () => {
    const { container } = setup();
    expect(container.textContent).not.toMatch(/\bworks?\b|outperform|underperform|\bbeat\b|\bwinn|\bgood\b|\bbad\b|\bbuy\b|\bsell\b/i);
  });

  it("says so when the record has no episode yet", () => {
    setup(payload({ rows: [], flagged: [], opening: [], counts: { ...SERVER.counts, episodes: 0, counted: 0 } }));
    expect(screen.getByText("No arm episode is in the record at this as-of.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a slow first read, then an error", () => {
    h.hook.mockReturnValue(ok(undefined, { isLoading: true }));
    const view = render(<TrackRecord asof="2026-07-24" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/Reading the Scoreboard's episodes/)).toBeInTheDocument();
    view.unmount();
    h.hook.mockReturnValue(ok(undefined, { error: new Error("boom") }));
    render(<TrackRecord asof="2026-07-24" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Track Record.")).toBeInTheDocument();
  });

  it("keeps the previous view while the next as-of loads", () => {
    setup(SERVER, { isFetching: true });
    expect(screen.getByText("updating…")).toBeInTheDocument();
    expect(screen.getAllByTestId("tr-table-row")).toHaveLength(7);
  });
});
