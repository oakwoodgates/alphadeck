import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FormingQuietly from "../FormingQuietly";
import { FRAMING } from "../quietView";
import type { FormingQuietlyOut } from "../useFormingQuietly";
import { ASOF, CARD_A, CARD_C, MIXED, payload, quiet, row, rows } from "./fixtures";

// Forming Quietly's body with its one hook stubbed: the rows in the server's order, the Incubating toggle, the filters
// and the sorts, the recorded call's own words, the chips opening the Cockpit, the quiet signals, the adoption slope
// with the sign tone as the page's only tone, the work lines, the framing sentence above them, and the empty / slow /
// error states. The page's loudness (invariant #7) is pinned here: no accent in the stylesheet or the markup. The
// hook's request has its own test.

const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../useFormingQuietly", async (orig) => ({ ...(await orig<typeof import("../useFormingQuietly")>()), useFormingQuietly: h.hook }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(data: FormingQuietlyOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.hook.mockReturnValue(ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(<FormingQuietly asof={ASOF} onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const lines = () => screen.queryAllByTestId("fq-row");
const cardsOnShow = () => lines().map((r) => r.querySelector(".fq-card")?.textContent);
const rowOf = (name: string) => lines().find((r) => r.querySelector(".fq-card")?.textContent === name) as HTMLElement;
const cellOf = (r: HTMLElement, label: string) => r.querySelector(`td[data-label="${label}"]`) as HTMLElement;

beforeEach(() => h.hook.mockReset());

describe("FormingQuietly — the rows", () => {
  it("shows every Warming card and the Incubating cards with a quiet signal, in the server's order", () => {
    setup();
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C"]);
    expect(screen.getByTestId("fq-count")).toHaveTextContent("3 cards · in the order of the key most recently turned");
  });

  it("heads the columns in order", () => {
    setup();
    expect(
      screen.getAllByRole("columnheader").map((c) => c.querySelector("button")?.textContent?.replace(/[↑↓]/g, "").trim() ?? c.textContent),
    ).toEqual([
      "Card",
      "State",
      "Key that turned",
      "Still missing",
      "Risk",
      "Insider buying",
      "Coiling",
      "Leading",
      "Breadth",
      "Adoption",
    ]);
  });

  it("gives every cell its own label for the phone, where the columns stack under the card name", () => {
    setup();
    const labels = [...rowOf("Card A").querySelectorAll("td")].map((td) => td.getAttribute("data-label"));
    expect(labels).toEqual([
      "Card",
      "State",
      "Key that turned",
      "Still missing",
      "Risk",
      "Insider buying",
      "Coiling",
      "Leading",
      "Breadth",
      "Adoption",
    ]);
  });

  it("shows the state in gray words with the run's first night, the days in state and a rebuilt night marked", () => {
    setup(undefined);
    const a = rowOf("Card A");
    expect(within(a).getByText("Warming")).toBeInTheDocument();
    expect(a.querySelector(".fq-since")).toHaveTextContent("since Oct 25 · 3 days");
    expect(a.querySelector(".fq-mark")).toBeNull();
    // a rebuilt first night is marked once the toggle shows the card
    return userEvent.click(screen.getByLabelText(/Show every Incubating card/)).then(() => {
      const d = rowOf("Card D");
      expect(d.querySelector(".fq-mark")?.textContent).toBe(" R");
      expect(d.querySelector(".fq-mark")).toHaveAttribute("title", expect.stringContaining("rebuilt by the backfill"));
      expect(d.querySelector(".fq-since")).toHaveTextContent("since Sep 15 R · 43 days");
    });
  });

  it("quotes the key that turned and the missing lines as the call recorded them", () => {
    setup();
    const a = rowOf("Card A");
    const keys = cellOf(a, "Key that turned");
    expect(keys).toHaveTextContent("Conviction · A conviction entry trigger fired — the 'why now is real'.");
    expect(keys).not.toHaveTextContent("No confirmation yet.");
    const missing = cellOf(a, "Still missing");
    expect([...missing.querySelectorAll(".fq-line")].map((l) => l.textContent)).toEqual([
      "Volume-confirmed breakout (the confirmation key)",
      "Risk must clear before arming: dilution overhang",
    ]);
    // a confirmation-only name rides along, marked
    expect(within(missing).getByRole("button", { name: "BBB" })).toBeInTheDocument();
    expect(missing).toHaveTextContent("confirmation only");
    // a card whose confirmation key turned says so in the recorded words
    expect(cellOf(rowOf("Card B"), "Key that turned")).toHaveTextContent("Confirmation · A volume-confirmed breakout.");
  });

  it("says no key turned for an Incubating card and still lists what is missing", () => {
    setup();
    const c = rowOf("Card C");
    expect(cellOf(c, "Key that turned")).toHaveTextContent("no key turned");
    expect(cellOf(c, "Still missing")).toHaveTextContent("Conviction trigger (e.g. insider cluster / structural catalyst)");
    expect(cellOf(c, "Still missing")).toHaveTextContent("Volume-confirmed breakout (the confirmation key)");
  });

  it("shows the names behind the key with their triggers and source links on hover, and the risk count", () => {
    setup();
    const a = rowOf("Card A");
    const chip = cellOf(a, "Key that turned").querySelector(".fq-name") as HTMLElement;
    expect(chip.querySelector(".fq-tk")).toHaveTextContent("AAA");
    const tip = chip.querySelector(".fq-tip") as HTMLElement;
    expect(tip).toHaveTextContent("insider cluster · insider · core · fired 2026-10-22");
    expect(within(tip).getByRole("link", { name: "form4 0001234567-26-000001" })).toHaveAttribute(
      "href",
      "https://www.sec.gov/Archives/edgar/data/1/000123456726000001/",
    );
    expect(tip).toHaveTextContent("price price:AAA:2026-10-22"); // no link known: shown as text
    expect(within(tip).queryByRole("link", { name: /price:AAA/ })).toBeNull();
    const risk = cellOf(a, "Risk").querySelector(".fq-fig") as HTMLElement;
    expect(risk).toHaveTextContent("1");
    expect(risk).toHaveAttribute("title", expect.stringContaining("convertible overhang"));
    expect(cellOf(rowOf("Card B"), "Risk")).toHaveTextContent("—");
  });

  it("shows the quiet signals as short gray phrases with the names behind them and the cluster word", () => {
    setup();
    const a = rowOf("Card A");
    expect(cellOf(a, "Insider buying").querySelector(".fq-fig")).toHaveTextContent("2 of 11");
    expect(cellOf(a, "Insider buying")).toHaveTextContent("AAA");
    expect(cellOf(a, "Insider buying")).toHaveTextContent("cluster");
    expect(cellOf(a, "Insider buying")).toHaveTextContent("CCC");
    expect(within(cellOf(a, "Insider buying")).getAllByText("cluster")).toHaveLength(1); // CCC has one buyer
    expect(cellOf(a, "Coiling").querySelector(".fq-fig")).toHaveTextContent("1 of 11");
    expect(cellOf(a, "Leading").querySelector(".fq-fig")).toHaveTextContent("1 of 11");
    expect(cellOf(a, "Breadth")).toHaveTextContent("5 of 10above the 50-day");
    // nothing lit: a gray blank, the n in the hover
    const b = rowOf("Card B");
    expect(cellOf(b, "Insider buying").querySelector(".fq-fig.gray")).toHaveTextContent("—");
    expect(cellOf(b, "Coiling").querySelector(".fq-fig.gray")).toHaveAttribute("title", expect.stringContaining("among 11 names read"));
  });

  it("shows the adoption slope as a signed percent, 'not counted yet' and the counts under a blank percent", async () => {
    setup();
    expect(cellOf(rowOf("Card A"), "Adoption")).toHaveTextContent("+38%412 vs 298 docs");
    expect(cellOf(rowOf("Card C"), "Adoption")).toHaveTextContent("-12%87 vs 99 docs");
    expect(cellOf(rowOf("Card B"), "Adoption")).toHaveTextContent("not counted yet");
    await userEvent.click(screen.getByLabelText(/Show every Incubating card/));
    expect(cellOf(rowOf("Card D"), "Adoption")).toHaveTextContent("—12 vs 0 docs");
  });

  it("opens the Cockpit on a card, on a name behind the key and on a name in a quiet signal", async () => {
    const { onSelectThesis } = setup();
    const a = rowOf("Card A");
    await userEvent.click(within(a).getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, null);
    await userEvent.click(cellOf(a, "Key that turned").querySelector(".fq-tk") as HTMLElement);
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, "AAA");
    await userEvent.click(within(cellOf(a, "Insider buying")).getByRole("button", { name: "CCC" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, "CCC");
    await userEvent.click(within(cellOf(a, "Coiling")).getByRole("button", { name: "DDD" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, "DDD");
    await userEvent.click(within(cellOf(a, "Leading")).getByRole("button", { name: "EEE" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, "EEE");
    await userEvent.click(within(cellOf(a, "Still missing")).getByRole("button", { name: "BBB" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_A, "BBB");
  });

  it("caps a long list of names at three and counts the rest", () => {
    const many = Array.from({ length: 5 }, (_, i) => ({
      security_id: `20000000-0000-4000-8000-00000000000${i}`,
      ticker: `T${i}`,
      buys_30d: 1,
      buyers_30d: 1,
      cluster: false,
    }));
    setup(payload({ rows: [row({ quiet: quiet({ lit: true, insider: many }) })] }));
    const cell = cellOf(rowOf("Card A"), "Insider buying");
    expect(cell.querySelectorAll(".fq-tk")).toHaveLength(3);
    expect(cell.querySelector(".fq-more")).toHaveTextContent("+2");
    expect(cell.querySelector(".fq-fig")).toHaveTextContent("5 of 11");
  });
});

describe("FormingQuietly — the Incubating toggle, the filters and the sorts", () => {
  it("adds the Incubating cards with no quiet signal behind a toggle that counts them", async () => {
    setup();
    const toggle = screen.getByLabelText(/Show every Incubating card/);
    expect(toggle).not.toBeChecked();
    expect(screen.getByText("+1")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C", "Card D"]);
  });

  it("draws no toggle when there is nothing to add", () => {
    setup(payload({ rows: rows().slice(0, 3) }));
    expect(screen.queryByLabelText(/Show every Incubating card/)).toBeNull();
  });

  it("filters by state, insider buying, coiling and card, and clears them", async () => {
    setup();
    const group = screen.getByRole("group", { name: "State" });
    await userEvent.click(within(group).getByRole("button", { name: "Incubating" }));
    expect(cardsOnShow()).toEqual(["Card C"]);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C"]);
    await userEvent.click(screen.getByLabelText("Has insider buying"));
    expect(cardsOnShow()).toEqual(["Card A", "Card C"]);
    await userEvent.click(screen.getByLabelText("Has coiling"));
    expect(cardsOnShow()).toEqual(["Card A"]);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    await userEvent.selectOptions(screen.getByLabelText("Card"), CARD_C);
    expect(cardsOnShow()).toEqual(["Card C"]);
    expect(screen.getByTestId("fq-count")).toHaveTextContent("1 card of 3 after the filters");
  });

  it("says when no card passes the filters", async () => {
    setup();
    await userEvent.click(screen.getByLabelText("Has coiling"));
    await userEvent.selectOptions(screen.getByLabelText("Card"), CARD_C);
    expect(screen.getByText("No card passes the filters.")).toBeInTheDocument();
  });

  it("sorts by days in state and by adoption, flips the direction, and goes back to the record order", async () => {
    setup(payload({ rows: rows() }));
    await userEvent.click(screen.getByLabelText(/Show every Incubating card/));
    await userEvent.click(screen.getByRole("button", { name: /^State/ }));
    expect(cardsOnShow()).toEqual(["Card D", "Card C", "Card A", "Card B"]);
    expect(screen.getByRole("columnheader", { name: /State/ })).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(screen.getByRole("button", { name: /^State/ }));
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C", "Card D"]);
    await userEvent.click(screen.getByRole("button", { name: /^Adoption/ }));
    expect(cardsOnShow()).toEqual(["Card A", "Card C", "Card B", "Card D"]);
    await userEvent.click(screen.getByRole("button", { name: "back to the record order" }));
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C", "Card D"]);
    expect(screen.queryByRole("button", { name: "back to the record order" })).toBeNull();
  });

  it("sorts by the card's name and by distinct insider buyers", async () => {
    setup();
    await userEvent.click(within(screen.getByRole("columnheader", { name: "Card" })).getByRole("button"));
    expect(cardsOnShow()).toEqual(["Card A", "Card B", "Card C"]);
    await userEvent.click(screen.getByRole("button", { name: /^Insider buying/ }));
    expect(cardsOnShow()).toEqual(["Card A", "Card C", "Card B"]);
  });
});

describe("FormingQuietly — the page's loudness (invariant #7)", () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "forming-quietly.css"), "utf-8");

  it("uses no accent color in its stylesheet: not the Board's state colors, not the armed accent", () => {
    expect(css).not.toMatch(/--armed|--warm|--manage|--accent|#[0-9a-f]{3,8}\b/i);
    // the sign tone (the app's positive / negative colors mixed into gray) is the one tint, and only on the adoption percent
    const tinted = css.split("\n").filter((l) => /var\(--(pos|neg)\)/.test(l));
    expect(tinted.map((l) => l.trim().split(" ")[0])).toEqual(["--fq-pos:", "--fq-neg:"]);
    expect(css).toMatch(/\.fq-fig\.pos \{ color: var\(--fq-pos\)/);
    expect(css).toMatch(/\.fq-fig\.neg \{ color: var\(--fq-neg\)/);
  });

  it("puts the sign tone on the adoption percent alone and a class on no other element", () => {
    setup();
    const toned = [...document.querySelectorAll(".pos, .neg")];
    expect(toned.map((el) => el.textContent)).toEqual(["+38%", "-12%"]);
    for (const el of toned) expect(el.closest("td")?.getAttribute("data-label")).toBe("Adoption");
    expect(document.querySelectorAll(".accent, .armed, .warming-chip, .hb-kind")).toHaveLength(0);
  });

  it("draws the state chip as gray words with no state-colored class", () => {
    setup();
    const chip = rowOf("Card A").querySelector(".fq-state") as HTMLElement;
    expect(chip.className).toBe("fq-state");
    expect(rowOf("Card C").querySelector(".fq-state")?.className).toBe("fq-state");
  });

  it("draws no badge or count on its own and says none of the words the page never says", () => {
    const { container } = setup(payload(), {});
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/early|setting up|about to|best|strongest|promising|hot\b/i);
    expect(container.querySelectorAll("[class*='badge']")).toHaveLength(0);
  });
});

describe("FormingQuietly — the framing sentence and the work lines", () => {
  it("frames the page with its one sentence above the work lines", () => {
    const { container } = setup();
    const framing = screen.getByTestId("fq-framing");
    expect(framing).toHaveTextContent(FRAMING);
    const work = [...container.querySelectorAll(".fq-work")];
    expect(work.length).toBeGreaterThan(0);
    for (const w of work) {
      expect(framing.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("states the as-of, the clock, the counts, the names read, the term months and the members run", () => {
    setup();
    expect(screen.getByText(/as of 2026-10-28 · the public clock/)).toBeInTheDocument();
    expect(screen.getByText(/8 cards counted of 9 live/)).toHaveTextContent(
      "2 Warming · 1 Incubating with a quiet signal · 1 Incubating without (hidden) · 2 Armed · 1 Managing · 1 no call yet",
    );
    expect(screen.getByText(/40 names read/)).toHaveTextContent("adoption counted for 3 cards, no term months stored for 1");
    expect(screen.getByText(/members run: insider_flow_90d, vcp, relative_strength/)).toHaveTextContent("computed in 1234 ms");
  });

  it("names the companies left out for a flagged price history and states the known limits", () => {
    setup();
    expect(screen.getByText(/1 name left out for a flagged price history/)).toBeInTheDocument();
    expect(screen.getByText(/never feed a call/)).toBeInTheDocument();
    expect(screen.getByText(/Context and Bench names never count here/)).toBeInTheDocument();
  });
});

describe("FormingQuietly — empty, slow and error states", () => {
  it("says so when no counted card is Warming or Incubating", () => {
    setup(payload({ rows: [], cards_warming: 0, cards_incubating: 0, cards_incubating_lit: 0 }));
    expect(screen.getByText(/No counted card is recorded Warming or Incubating at this as-of \(8 cards counted, 1 with no call yet\)/)).toBeInTheDocument();
  });

  it("says so when only Incubating cards with no quiet signal exist, and offers them", async () => {
    setup(payload({ rows: [rows()[3]] }));
    expect(screen.getByText(/No Warming card and no Incubating card with a quiet signal at this as-of/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Show every Incubating card/));
    expect(cardsOnShow()).toEqual(["Card D"]);
  });

  it("shows the slow note while loading, the error when nothing loaded, and keeps the last view while updating", () => {
    h.hook.mockReturnValue(ok(undefined, { isLoading: true, isFetching: true }));
    const { rerender } = render(<FormingQuietly asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
    h.hook.mockReturnValue(ok(undefined, { error: new Error("boom") }));
    rerender(<FormingQuietly asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load Forming Quietly.")).toBeInTheDocument();
    h.hook.mockReturnValue(ok(payload(), { isFetching: true }));
    rerender(<FormingQuietly asof={ASOF} onSelectThesis={vi.fn()} />);
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C"]);
    expect(screen.getByText("updating…")).toBeInTheDocument();
  });

  it("notes that the view is a previous as-of's while the next one loads", () => {
    setup(payload({ asof: "2026-10-27" }), { isFetching: true });
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
    expect(cardsOnShow()).toEqual(["Card B", "Card A", "Card C"]);
  });

  it("draws the MIXED fixture's flagged ticker in the opened note", async () => {
    setup();
    await userEvent.click(screen.getByText(/1 name left out for a flagged price history/));
    expect(screen.getByText(MIXED)).toBeInTheDocument();
    expect(screen.getByText("FLG")).toBeInTheDocument();
  });
});
