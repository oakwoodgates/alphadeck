import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ThemeLeaderboard from "../ThemeLeaderboard";
import type { LeaderboardOut } from "../useThemeLeaderboard";
import { ASOF, CARD_A, CARD_B, MIXED, card, payload, sample } from "./fixtures";

// The Theme Leaderboard body with its one hook stubbed: the table in score order (the server's cards, the score
// recomputed among the cards shown), the filters that change it, every sort, the card names opening the Cockpit, the
// state chip as the page's one color, the quiet blanks, the work lines, the stated formula and the framing line, and
// the empty / slow / error states. The hook's request has its own test.

const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../useThemeLeaderboard", async (orig) => ({
  ...(await orig<typeof import("../useThemeLeaderboard")>()),
  useThemeLeaderboard: h.hook,
}));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(data: LeaderboardOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.hook.mockReturnValue(ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(<ThemeLeaderboard asof={ASOF} onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const lines = () => screen.queryAllByTestId("tl-row");
const names = () => lines().map((r) => r.querySelector(".tl-card")?.textContent);
const cells = (r: HTMLElement) => [...r.querySelectorAll("td")];
const header = (name: RegExp) => screen.getByRole("columnheader", { name });
const rowOf = (name: string) => lines().find((r) => r.querySelector(".tl-card")?.textContent === name) as HTMLElement;
const scoreOf = (name: string) => rowOf(name).querySelector(".tl-score-n")?.textContent;

beforeEach(() => h.hook.mockReset());

describe("ThemeLeaderboard — the rows", () => {
  it("shows every counted card in score order, the cards without a score last", () => {
    setup();
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(screen.getByTestId("tl-count")).toHaveTextContent("4 cards · the score is among the cards shown");
  });

  it("heads the columns in order", () => {
    setup();
    expect(
      screen.getAllByRole("columnheader").map((c) => c.querySelector("button")?.textContent?.replace(/[↑↓]/g, "").trim() ?? c.textContent),
    ).toEqual([
      "Card",
      "State",
      "Score (stated formula)",
      "30D vs IWM",
      "Above 50d",
      "Above 200d",
      "Keys",
      "Adoption",
      "Buyers 30d",
      "Names",
      "7D",
      "RS leaders",
      "nano",
      "micro",
      "small",
      "mid",
      "large",
      "Armed",
    ]);
    expect(header(/^Score/)).toHaveAttribute("aria-sort", "descending");
  });

  it("shows each card's five parts and the columns beside them", () => {
    setup();
    const r = cells(rowOf("Card A")).map((c) => c.textContent);
    expect(r[0]).toBe("Card A");
    expect(r[1]).toBe("Armed");
    expect(r[2]).toBe("100");
    expect(r[3]).toBe("+8.0 pp");
    expect(r[4]).toBe("8/10");
    expect(r[5]).toBe("4/8");
    expect(r[6]).toBe("2 of 2");
    expect(r[7]).toBe("+100%");
    expect(r[8]).toBe("5");
    expect(r[9]).toBe("11/12 1 flagged");
    expect(r[10]).toBe("+2.4%");
    expect(r[11]).toBe("3/9");
    expect(r.slice(12, 17)).toEqual(["1", "2", "4", "2", "0"]);
    expect(r[17]).toBe("2");
  });

  it("shows the score as a number with a gray bar and its five parts on hover", () => {
    setup();
    const score = rowOf("Card B").querySelector(".tl-score") as HTMLElement;
    expect(score.querySelector(".tl-score-bar")).not.toBeNull();
    expect(score.getAttribute("title")).toBe("tape 0.50 · breadth 0.33 · keys 0.50 · adoption 0.00 · insiders 0.00");
    expect(score.textContent).toBe("27");
  });

  it("names the key that is off when exactly one is turned, and nothing when both are", () => {
    setup();
    expect(rowOf("Card B").querySelector(".tl-note")?.textContent).toBe("confirmation off");
    expect(cells(rowOf("Card B"))[6].textContent).toBe("1 of 2 confirmation off");
    expect(cells(rowOf("Card A"))[6].querySelector(".tl-note")).toBeNull();
  });

  it("gives a card missing a part no score, a dash, no bar, and says which part in the hover", () => {
    setup();
    for (const name of ["Card C", "Card D"]) {
      const row = rowOf(name);
      expect(row.querySelector(".tl-score-n")?.textContent).toBe("—");
      expect(row.querySelector(".tl-score-bar")).toBeNull();
      expect(row.querySelector(".tl-score")?.getAttribute("title")).toMatch(/^No score: no reading for /);
    }
    expect(rowOf("Card C").querySelector(".tl-score")).toHaveAttribute("title", expect.stringContaining("no reading for adoption "));
  });

  it("reads 'not counted yet' for an adoption part without six closed months, and says what else is blank", () => {
    setup();
    const c = cells(rowOf("Card C"));
    expect(c[7].textContent).toBe("not counted yet");
    expect(c[7].querySelector(".tl-cell")).toHaveClass("gray");
    const d = cells(rowOf("Card D"));
    expect(d[1].textContent).toBe("No call");
    expect(d[3].textContent).toBe("—"); // thin tape
    expect(d[6].textContent).toBe("—"); // no recorded call: no keys
    expect(d[7].textContent).toBe("no terms");
    expect(d[8].textContent).toBe("—"); // no Form 4 reading
    expect(d[11].textContent).toBe("—");
    expect(d[17].textContent).toBe("—");
    expect(d[3].querySelector(".tl-cell")).toHaveAttribute("title", expect.stringContaining("fewer than 3"));
  });

  it("marks a state rebuilt by the backfill, and nothing else", () => {
    setup(payload({ cards: [{ ...sample()[0], reconstructed: true }, ...sample().slice(1)] }));
    expect(rowOf("Card A").querySelector(".tl-mark")?.textContent).toBe("R");
    expect(document.querySelectorAll(".tl-mark")).toHaveLength(1);
  });
});

describe("ThemeLeaderboard — loudness", () => {
  it("colors only the state chip (the Board's state colors) and the signed tape figures; no accent", () => {
    setup();
    expect(rowOf("Card A").querySelector(".tl-state")).toHaveClass("armed");
    expect(rowOf("Card B").querySelector(".tl-state")).toHaveClass("warming");
    expect(rowOf("Card C").querySelector(".tl-state")).toHaveClass("incubating");
    expect(rowOf("Card D").querySelector(".tl-state")).toHaveClass("none");
    expect(document.querySelector(".accent")).toBeNull();
    // the sign tone only on a signed tape figure: the 30D vs IWM and the 7D median
    expect(rowOf("Card C").querySelector(".tl-cell.pos, .tl-cell.neg")?.textContent).toBe("-2.0 pp");
    expect(rowOf("Card A").querySelector(".tl-cell.pos")?.textContent).toBe("+8.0 pp");
    const toned = [...document.querySelectorAll(".tl-cell.pos, .tl-cell.neg")].map((c) => c.closest("td")?.getAttribute("data-label"));
    expect(new Set(toned)).toEqual(new Set(["30D vs IWM", "7D"]));
  });

  it("frames the page as a rank of what the data shows, not a judgment of any thesis", () => {
    setup();
    expect(screen.getByTestId("tl-framing")).toHaveTextContent(
      "A rank of what the data shows about each card's names and record, by the formula below. It is not a judgment of any thesis.",
    );
  });

  it("labels each cell for the phone layout, where the table wraps under the card name", () => {
    setup();
    const labels = cells(rowOf("Card A")).map((c) => c.getAttribute("data-label"));
    expect(labels).toEqual([
      "Card",
      "State",
      "Score",
      "30D vs IWM",
      "Above 50d",
      "Above 200d",
      "Keys",
      "Adoption",
      "Buyers 30d",
      "Names",
      "7D",
      "RS leaders",
      "nano",
      "micro",
      "small",
      "mid",
      "large",
      "Armed",
    ]);
  });
});

describe("ThemeLeaderboard — the score is among the cards shown", () => {
  it("recomputes the score when a filter changes the set, and a lone card scores 50", async () => {
    const user = userEvent.setup();
    setup();
    expect(scoreOf("Card A")).toBe("100");
    expect(scoreOf("Card B")).toBe("27");

    const states = screen.getByRole("group", { name: "State" });
    await user.click(within(states).getByRole("button", { name: "Armed" }));
    await user.click(within(states).getByRole("button", { name: "Warming" }));
    expect(names()).toEqual(["Card A", "Card B"]);
    expect(scoreOf("Card A")).toBe("100");
    expect(scoreOf("Card B")).toBe("0");
    expect(screen.getByTestId("tl-count")).toHaveTextContent("2 cards of 4 after the filters");

    await user.click(within(states).getByRole("button", { name: "Warming" }));
    expect(names()).toEqual(["Card A"]);
    expect(scoreOf("Card A")).toBe("50"); // nothing to compare it with

    await user.click(screen.getByRole("button", { name: "clear filters" }));
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(scoreOf("Card B")).toBe("27");
  });

  it("filters to the cards with adoption months, and by a piece of the name", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("checkbox", { name: /has adoption months/ }));
    expect(names()).toEqual(["Card A", "Card B"]);
    await user.click(screen.getByRole("checkbox", { name: /has adoption months/ }));
    await user.type(screen.getByRole("searchbox", { name: "Search cards" }), "card c");
    expect(names()).toEqual(["Card C"]);
    await user.clear(screen.getByRole("searchbox", { name: "Search cards" }));
    await user.type(screen.getByRole("searchbox", { name: "Search cards" }), "zzz");
    expect(screen.getByText("No card passes the filters.")).toBeInTheDocument();
  });

  it("renders no control that cannot discriminate", () => {
    const same = [card({ state: "warming", thesis_name: "Card X" }), card({ state: "warming", thesis_name: "Card Y" })];
    setup(payload({ cards: same }));
    expect(screen.queryByRole("group", { name: "State" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /has adoption months/ })).toBeNull();
    expect(screen.getByRole("searchbox", { name: "Search cards" })).toBeInTheDocument();
  });
});

describe("ThemeLeaderboard — the sorts", () => {
  it("sorts a column biggest first, then flips, with a blank always last; 'back to score' returns", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(header(/30D vs IWM/).querySelector("button") as HTMLElement);
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(header(/30D vs IWM/)).toHaveAttribute("aria-sort", "descending");
    await user.click(header(/30D vs IWM/).querySelector("button") as HTMLElement);
    expect(names()).toEqual(["Card C", "Card B", "Card A", "Card D"]); // D has no tape: last either way
    expect(header(/30D vs IWM/)).toHaveAttribute("aria-sort", "ascending");
    await user.click(screen.getByRole("button", { name: "back to score" }));
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(header(/^Score/)).toHaveAttribute("aria-sort", "descending");
  });

  it("sorts the name A to Z first and the state in lifecycle order", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(header(/^Card$/).querySelector("button") as HTMLElement);
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    await user.click(header(/^Card$/).querySelector("button") as HTMLElement);
    expect(names()).toEqual(["Card D", "Card C", "Card B", "Card A"]);
    await user.click(header(/^State$/).querySelector("button") as HTMLElement);
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
  });

  it("sorts a cap band by its count", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(header(/^small$/).querySelector("button") as HTMLElement);
    expect(names()[0]).toBe("Card A"); // 4 small names, as every card but D (0)
    expect(names().at(-1)).toBe("Card D");
  });
});

describe("ThemeLeaderboard — opening a card", () => {
  it("opens the Cockpit on the card's name", async () => {
    const user = userEvent.setup();
    const { onSelectThesis } = setup();
    await user.click(rowOf("Card B").querySelector(".tl-card") as HTMLElement);
    expect(onSelectThesis).toHaveBeenCalledWith(CARD_B);
    await user.click(rowOf("Card A").querySelector(".tl-card") as HTMLElement);
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A);
  });
});

describe("ThemeLeaderboard — the quiet lines", () => {
  it("prints the stated formula, the work lines, the flagged note and the limits", () => {
    setup();
    expect(screen.getByTestId("tl-formula")).toHaveTextContent("Score (stated formula): the mean of five percentiles");
    expect(screen.getByTestId("tl-formula")).toHaveTextContent("Equal weights.");
    expect(screen.getByText(/4 cards counted \(5 live; 1 created after the as-of left out\) · 4 shown · 2 scored · 2 without a score/)).toBeInTheDocument();
    expect(screen.getByText(/34 counting names across the cards · 33 read for price · 1 flagged/)).toBeInTheDocument();
    expect(screen.getByText(/adoption: 2 cards with six closed months of term counts, 2 without/)).toBeInTheDocument();
    expect(screen.getByText("computed · compute 41,250 ms")).toBeInTheDocument();
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
    expect(screen.getByText("The score is relative to the cards shown and means nothing across days.")).toBeInTheDocument();
    expect(screen.getByText(/never an input\.$/)).toBeInTheDocument();
  });

  it("names a flagged company by ticker and reason when the note is opened", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByText(/1 name left out for a flagged price history/));
    expect(screen.getByText("FLG")).toBeInTheDocument();
    expect(screen.getByText(MIXED)).toBeInTheDocument();
  });

  it("recounts the work lines for the cards shown", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(within(screen.getByRole("group", { name: "State" })).getByRole("button", { name: "Armed" }));
    expect(screen.getByText(/4 cards counted \(5 live; 1 created after the as-of left out\) · 1 shown · 1 scored · 0 without a score/)).toBeInTheDocument();
  });
});

describe("ThemeLeaderboard — the states of the read", () => {
  it("says it is reading while the first load is out", () => {
    h.hook.mockReturnValue(ok(undefined, { isLoading: true, isFetching: true }));
    render(<ThemeLeaderboard asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/one breadth read per card/)).toBeInTheDocument();
  });

  it("says it could not load", () => {
    h.hook.mockReturnValue(ok(undefined, { error: new Error("boom") }));
    render(<ThemeLeaderboard asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Theme Leaderboard.")).toBeInTheDocument();
  });

  it("keeps the previous view on screen with the slow note while the next as-of loads", () => {
    setup(payload({ asof: "2026-10-13" }), { isFetching: true });
    expect(names()).toEqual(["Card A", "Card B", "Card C", "Card D"]);
    expect(screen.getByText(/one breadth read per card/)).toBeInTheDocument();
  });

  it("says no card is counted when none is", () => {
    setup(payload({ cards: [], live_cards: 3, cards_counted: 0, cards_not_yet_created: 3 }));
    expect(screen.getByText("No card is counted at this as-of (3 live, 3 created after it).")).toBeInTheDocument();
    expect(lines()).toHaveLength(0);
  });
});
