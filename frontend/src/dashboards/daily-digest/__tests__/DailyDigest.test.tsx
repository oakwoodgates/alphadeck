import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { todayISO } from "../../../util/format";
import DailyDigest from "../DailyDigest";
import { FRAMING, LIMITS } from "../digestView";
import type { DailyDigestOut } from "../useDailyDigest";
import { ASOF, CARD_A, CARD_B, CARD_C, CARD_D, CARD_E, emptyPayload, payload, position } from "./fixtures";

// The Daily Digest's body with its one hook stubbed: the six sections in order, each header opening its full page with
// ?asof=, the cards in the server's Armed-first order with Incubating last, the page's one loud rule (the accent on
// three kinds of line, the warm tone on two) read off the stylesheet AND the markup, the positions with the start of
// each return said, the this-week lines with the "today" mark, the insider buys, the tape's cut and its toggle, the
// forming line, every empty state, and the cards / names opening the Cockpit. The hook's request has its own test.

const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../useDailyDigest", async (orig) => ({ ...(await orig<typeof import("../useDailyDigest")>()), useDailyDigest: h.hook }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(data: DailyDigestOut | undefined = payload(), extra: Record<string, unknown> = {}, asof = ASOF) {
  h.hook.mockReturnValue(ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(
    <MemoryRouter>
      <DailyDigest asof={asof} onSelectThesis={onSelectThesis} />
    </MemoryRouter>,
  );
  return { onSelectThesis, ...view };
}

const sec = (label: string) => screen.getByRole("region", { name: label });
const cards = () => screen.getAllByTestId("dd-card");
const cardOf = (name: string) => cards().find((c) => c.querySelector(".dd-cardname")?.textContent === name) as HTMLElement;

beforeEach(() => h.hook.mockReset());

describe("DailyDigest — the page", () => {
  it("renders the six sections in the plan's order", () => {
    const { container } = setup();
    expect([...container.querySelectorAll("section[aria-label]")].map((s) => s.getAttribute("aria-label"))).toEqual([
      "Last night",
      "Positions",
      "Due this week",
      "Insider buys",
      "The tape",
      "Forming",
    ]);
    expect(screen.getByTestId("dd-framing")).toHaveTextContent(FRAMING);
  });

  it("says which night against which, and says so when the as-of is past that night", () => {
    setup();
    expect(screen.getByTestId("dd-night")).toHaveTextContent("The night of Thu, Sep 17, against Wed, Sep 16 · 1 session between.");
    expect(screen.queryByTestId("dd-after")).toBeNull();
  });

  it("reads a Monday morning as Friday's night against Thursday's and says nothing newer is recorded", () => {
    const p = payload({
      asof: "2026-09-21",
      nights: { asof: "2026-09-21", latest: "2026-09-18", previous: "2026-09-17", sessions_between: 1, days_after_night: 3 },
    });
    setup(p, {}, "2026-09-21");
    expect(screen.getByTestId("dd-night")).toHaveTextContent("The night of Fri, Sep 18, against Thu, Sep 17");
    expect(screen.getByTestId("dd-after")).toHaveTextContent("Mon, Sep 21 is 3 days past that night. Nothing newer is recorded, so the page repeats it.");
  });

  it("says there is no earlier night to compare on the record's first night", () => {
    setup(
      payload({
        nights: { asof: ASOF, latest: ASOF, previous: null, sessions_between: null, days_after_night: 0 },
        last_night: { no_baseline: true, cards_with_change: 0, lines: 0, cards: [] },
        insider: { ...payload().insider, no_baseline: true, span_start: null, span_end: null, rows: [] },
      }),
    );
    expect(screen.getByTestId("dd-night")).toHaveTextContent("no earlier night to compare");
    expect(within(sec("Last night")).getByText("No earlier night to compare.")).toBeInTheDocument();
    expect(within(sec("Insider buys")).getByText("No earlier night to compare.")).toBeInTheDocument();
  });

  it("shows a loading note, the could-not-load note and keeps the stale page while the next as-of loads", () => {
    h.hook.mockReturnValue(ok(undefined, { isLoading: true }));
    const { unmount } = render(
      <MemoryRouter>
        <DailyDigest asof={ASOF} onSelectThesis={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Reading the last two recorded nights/)).toBeInTheDocument();
    unmount();
    h.hook.mockReturnValue(ok(undefined, { error: new Error("boom") }));
    render(
      <MemoryRouter>
        <DailyDigest asof={ASOF} onSelectThesis={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Could not load the Daily Digest.")).toBeInTheDocument();
  });

  it("keeps the previous page on screen, with a note, while a different as-of loads", () => {
    setup(payload(), { isFetching: true }, "2026-09-18");
    expect(screen.getAllByTestId("dd-card").length).toBeGreaterThan(0);
    expect(screen.getByText(/Reading the last two recorded nights/)).toBeInTheDocument();
  });
});

describe("DailyDigest — the headers open the full pages, carrying ?asof=", () => {
  it("links each section to its page with the as-of; Positions has no page to open", () => {
    setup();
    const href = (name: string | RegExp) => screen.getByRole("link", { name }).getAttribute("href");
    expect(href("Last night")).toBe("/dashboards/what-changed?asof=2026-09-17");
    expect(href("Due this week")).toBe("/dashboards/catalyst-calendar?asof=2026-09-17");
    expect(href("Insider buys")).toBe("/dashboards/insider-tracker?asof=2026-09-17");
    expect(href("The tape")).toBe("/dashboards/hot-board?asof=2026-09-17");
    expect(href("Entry Windows")).toBe("/dashboards/entry-windows?asof=2026-09-17");
    expect(href("Forming")).toBe("/dashboards/forming-quietly?asof=2026-09-17");
    expect(within(sec("Positions")).queryAllByRole("link")).toHaveLength(0);
  });

  it("keeps the URL clean on a live visit, where an absent ?asof= already reads today", () => {
    const today = todayISO();
    setup(payload({ asof: today }), {}, today);
    expect(screen.getByRole("link", { name: "Last night" }).getAttribute("href")).toBe("/dashboards/what-changed");
  });
});

describe("DailyDigest — last night", () => {
  it("lists the cards in the server's order: Armed-touching first, then Warming, Incubating last", () => {
    setup();
    expect(cards().map((c) => [c.querySelector(".dd-cardname")?.textContent, c.getAttribute("data-tier")])).toEqual([
      ["Card C", "armed"],
      ["Card D", "armed"],
      ["Card E", "armed"],
      ["Card B", "warming"],
      ["Card A", "incubating"],
    ]);
    expect(within(sec("Last night")).getByRole("heading", { level: 2 })).toHaveTextContent("5 cards, 9 lines");
  });

  it("words a card's state move as its headline and each other line in What Changed's words", () => {
    setup();
    expect(within(cardOf("Card C")).getByTestId("dd-move")).toHaveTextContent("Warming → Armed");
    expect(within(cardOf("Card D")).getByTestId("dd-move")).toHaveTextContent("Armed → Warming");
    expect(within(cardOf("Card E")).getByTestId("dd-move")).toHaveTextContent("Managing"); // no state move: the state it stands in
    expect(within(cardOf("Card A")).getByTestId("dd-move")).toHaveTextContent("Warming → Incubating");
    const lines = (name: string) => within(cardOf(name)).queryAllByTestId("dd-line").map((l) => l.textContent);
    expect(lines("Card C")).toEqual(["AAAnewly armed", "entry window closes in 2 days · Sep 19"]);
    expect(lines("Card D")).toEqual(["BBBno longer armed"]);
    expect(lines("Card E")).toEqual(["CCCnew risk signal · going-concern note · dilution risk · Sep 16", "exit-by reached · Sep 17"]);
    expect(lines("Card B")).toEqual(["Confirmation key turned on"]);
    expect(lines("Card A")).toEqual([]);
  });

  it("carries a trigger line's first source as a link, and a plain label when it has no link", () => {
    setup();
    const e = cardOf("Card E");
    const link = within(e).getByRole("link", { name: "form4 0000876543-26-000001" });
    expect(link).toHaveAttribute("href", "https://www.sec.gov/Archives/edgar/data/876543/000087654326000001/");
  });

  it("marks a rebuilt night R and a card whose run did not ingest cleanly, and only those", () => {
    setup();
    const a = cardOf("Card A");
    expect(a.querySelector(".dd-mark")?.textContent).toBe("R");
    expect(within(a).getByText("ingest not fresh")).toBeInTheDocument();
    expect(cardOf("Card C").querySelector(".dd-mark")).toBeNull();
  });

  it("opens the Cockpit on the card, and on the name for a name or trigger line", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(cardOf("Card C")).getByRole("button", { name: "Card C" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_C);
    await userEvent.click(within(cardOf("Card C")).getAllByTestId("dd-line")[0]);
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_C, "AAA");
    // a card-level line (no name) opens the card
    await userEvent.click(within(cardOf("Card B")).getByTestId("dd-line"));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_B);
  });

  it("says nothing changed in one quiet sentence, never a hidden section", () => {
    setup(emptyPayload());
    expect(within(sec("Last night")).getByText("Nothing changed last night.")).toBeInTheDocument();
  });
});

describe("DailyDigest — positions", () => {
  it("shows the held name, the fill, the two returns with the start said, the clock, the risks and the kill criteria", () => {
    setup();
    const row = screen.getByTestId("dd-pos-row");
    const cell = (label: string) => row.querySelector(`td[data-label="${label}"]`) as HTMLElement;
    expect(cell("Card")).toHaveTextContent("Card E");
    expect(cell("Name")).toHaveTextContent("HHH");
    expect(cell("Fill")).toHaveTextContent("filled Sep 11 at $48.00");
    expect(cell("Fill")).toHaveTextContent("logged fill 100 sh × $48.00 = $4,800");
    expect(cell("Since fill")).toHaveTextContent("+37.50%");
    expect(cell("Since fill")).toHaveTextContent("from the logged price");
    expect(cell("Since fill").querySelector(".dd-fig")).toHaveAttribute("title", expect.stringContaining("$48.00 (the logged fill price) to $66.00"));
    expect(cell("Since fill").querySelector(".dd-fig")).toHaveAttribute("title", expect.stringContaining("IWM +3.96% over the same dates"));
    expect(cell("Last session")).toHaveTextContent("+10.00%");
    expect(cell("Last session")).toHaveTextContent("IWM +0.96%");
    expect(cell("Hold clock")).toHaveTextContent("exit-by Sep 22 · 3 sessions left");
    expect(cell("Risk")).toHaveTextContent("2 risk signals · 1 new last night");
    expect(cell("Kill criteria")).toHaveTextContent("2 kill criteria");
    // the operator's own words, verbatim, in the hover
    expect(cell("Kill criteria").querySelector("[title]")).toHaveAttribute("title", "the license is lost\na second going-concern note");
  });

  it("says the return started at the first close when no price was logged, and the sign tone is only on a return", () => {
    const p = position({
      logged_price: null,
      shares: null,
      fill_usd: null,
      since_fill: { status: "priced", basis: "first_close", basis_price: 52, basis_date: "2026-09-11", last_date: ASOF, last_close: 66, ret_pct: -26.92, bench_pct: null, reason: null, note: null },
    });
    setup(payload({ positions: [p] }));
    const row = screen.getByTestId("dd-pos-row");
    expect(row).toHaveTextContent("from the first close Sep 11");
    expect(row).toHaveTextContent("filled Sep 11 · no price logged");
    expect(row).not.toHaveTextContent("logged fill");
    const fig = row.querySelector('td[data-label="Since fill"] .dd-fig') as HTMLElement;
    expect(fig).toHaveClass("dd-neg");
  });

  it("keeps a unattributed position's line, a no-position line and a flagged name's reason", () => {
    setup(
      payload({
        positions: [
          position({ security_id: null, ticker: null, since_fill: { status: "no_name", basis: null, basis_price: null, basis_date: null, last_date: null, last_close: null, ret_pct: null, bench_pct: null, reason: "the position names no company", note: null }, last_session: { status: "unpriced", reason: "the position names no company", session: null, previous_session: null, ret_pct: null, bench_pct: null } }),
          position({ thesis_id: CARD_D, thesis_name: "Card D2", has_position: false, security_id: null, ticker: null, opened_on: null, logged_price: null, shares: null, fill_usd: null, since_fill: { status: "no_fill", basis: null, basis_price: null, basis_date: null, last_date: null, last_close: null, ret_pct: null, bench_pct: null, reason: "no position logged as of this date", note: null } }),
          position({ thesis_id: CARD_A, thesis_name: "Card A2", since_fill: { status: "flagged", basis: null, basis_price: null, basis_date: null, last_date: null, last_close: null, ret_pct: null, bench_pct: null, reason: "mixed-scale price history", note: null }, last_session: { status: "flagged", reason: "mixed-scale price history", session: null, previous_session: null, ret_pct: null, bench_pct: null } }),
        ],
      }),
    );
    const rows = screen.getAllByTestId("dd-pos-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("unattributed");
    expect(rows[0]).toHaveTextContent("the position names no company");
    expect(rows[1]).toHaveTextContent("no position logged as of this date");
    expect(rows[2]).toHaveTextContent("left out: mixed-scale price history");
  });

  it("opens the Cockpit on the card and on the held name", async () => {
    const { onSelectThesis } = setup();
    const row = screen.getByTestId("dd-pos-row");
    await userEvent.click(within(row).getByRole("button", { name: "HHH" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_E, "HHH");
    await userEvent.click(within(row).getByRole("button", { name: "Card E" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_E);
  });

  it("says no position is logged in one quiet sentence", () => {
    setup(emptyPayload());
    expect(within(sec("Positions")).getByText("No position logged.")).toBeInTheDocument();
  });
});

describe("DailyDigest — due this week", () => {
  it("lists the bucket's lines with the 'today' mark and the quiet passed count", () => {
    setup();
    const rows = screen.getAllByTestId("dd-due-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "todayCard CReadout · regulatory",
      "Fri, Sep 18 · in 1 dayCard Dentry window ends",
      "Sun, Sep 20 · in 3 daysCard BVote",
    ]);
    expect(rows[0]).toHaveClass("dd-today");
    expect(rows[1]).not.toHaveClass("dd-today");
    expect(screen.getByTestId("dd-passed")).toHaveTextContent("1 catalyst passed since Wed, Sep 16 and is not yet pruned (3 passed in all).");
  });

  it("opens the card from a line", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(screen.getAllByTestId("dd-due-row")[1]);
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_D);
  });

  it("says nothing is due in one quiet sentence", () => {
    setup(emptyPayload());
    expect(within(sec("Due this week")).getByText("Nothing due this week.")).toBeInTheDocument();
  });
});

describe("DailyDigest — insider buys", () => {
  it("words a purchase: the buyer, the size, when it was filed, the cluster and whether it became a trigger", () => {
    setup();
    const [a, b] = screen.getAllByTestId("dd-buy-row");
    expect(a).toHaveTextContent("AAA");
    expect(a).toHaveTextContent("Jane Roe (CEO)");
    expect(a).toHaveTextContent("1,000 sh @ $4.00 = $4,000");
    expect(a).toHaveTextContent("filed Sep 17 · traded Sep 16, filed 1 day after");
    expect(a).toHaveTextContent("cluster · 2 buyers");
    expect(within(a).getByText("cluster · 2 buyers")).toHaveAttribute("title", "Sam Poe, filed Sep 16");
    expect(a).toHaveTextContent("became a trigger on Card C");
    expect(within(a).getByRole("link", { name: "4 0000876543-26-000001" })).toHaveAttribute("href", expect.stringContaining("sec.gov"));
    expect(b).toHaveTextContent("Kim Lee");
    expect(b).toHaveTextContent("size not stated");
    expect(b).toHaveTextContent("not a trigger");
    expect(b).not.toHaveTextContent("cluster");
    // no "since" column: the move after a buy is the Track Record's statistic
    expect(sec("Insider buys")).not.toHaveTextContent(/since the filing|since filing/i);
  });

  it("heads the section with the span and the count, and opens the Cockpit on the name", async () => {
    const { onSelectThesis } = setup();
    expect(within(sec("Insider buys")).getByRole("heading", { level: 2 })).toHaveTextContent("filed since Wed, Sep 16 · 2 purchases");
    await userEvent.click(within(screen.getAllByTestId("dd-buy-row")[0]).getByRole("button", { name: "AAA" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_C, "AAA");
  });

  it("says no insider buys were filed in one quiet sentence", () => {
    setup(emptyPayload());
    expect(within(sec("Insider buys")).getByText("No insider buys filed since Wed, Sep 16.")).toBeInTheDocument();
  });
});

describe("DailyDigest — the tape", () => {
  const names = () => screen.queryAllByTestId("dd-tape-row").map((r) => r.querySelector(".dd-tk")?.textContent);

  it("shows the largest rises and falls the server flagged, in its order, and states n", () => {
    setup();
    expect(names()).toEqual(["RS1", "RS2", "FL1"]);
    expect(screen.getByTestId("dd-tape-note")).toHaveTextContent("Largest last-session rises and falls among 4 priced names · Wed, Sep 16 to Thu, Sep 17 · IWM +0.96%");
  });

  it("adds every armed name, in the same order, behind a toggle that names what it adds", async () => {
    setup();
    const toggle = screen.getByLabelText(/Show every armed name/);
    expect(toggle).not.toBeChecked();
    expect(toggle.closest("label")).toHaveTextContent("+2");
    await userEvent.click(toggle);
    expect(names()).toEqual(["RS1", "RS2", "MID", "FL1", "UNP"]);
    // an unpriced name keeps its row and says why
    const unp = screen.getAllByTestId("dd-tape-row")[4];
    expect(unp).toHaveTextContent("no close on 2026-09-17");
    expect(unp).toHaveTextContent("no close since the arm night");
    // the toggle stays to turn it back
    expect(screen.getByLabelText(/Show every armed name/)).toBeChecked();
    await userEvent.click(screen.getByLabelText(/Show every armed name/));
    expect(names()).toEqual(["RS1", "RS2", "FL1"]);
  });

  it("does not render a toggle that could not tell the rows apart (every armed name is already shown)", () => {
    const rows = [payload().tape.rows[0], payload().tape.rows[3]];
    setup(payload({ tape: { ...payload().tape, rows } }));
    expect(screen.queryByLabelText(/Show every armed name/)).toBeNull();
  });

  it("words the two clocks and the move since the arm night against IWM", () => {
    setup();
    const row = screen.getAllByTestId("dd-tape-row")[0];
    expect(row.querySelector('td[data-label="Last session"]')).toHaveTextContent("+9.00%");
    expect(row.querySelector('td[data-label="Since arm"]')).toHaveTextContent("+6.12%");
    expect(row.querySelector('td[data-label="Since arm"]')).toHaveTextContent("IWM +1.94%");
    expect(row.querySelector('td[data-label="Armed"]')).toHaveTextContent("Sep 15");
    expect(row.querySelector('td[data-label="Armed"]')).toHaveTextContent("2 in · 3 left");
  });

  it("opens the Cockpit on the name from its ticker and on the card from its name", async () => {
    const { onSelectThesis } = setup();
    const row = screen.getAllByTestId("dd-tape-row")[0];
    await userEvent.click(within(row).getByRole("button", { name: "RS1" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_C, "RS1");
    await userEvent.click(within(row).getByRole("button", { name: "Card C" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_C);
  });

  it("says no armed name in one quiet sentence", () => {
    setup(emptyPayload());
    expect(within(sec("The tape")).getByText("No armed name.")).toBeInTheDocument();
  });

  it("never states an average move across the names (rule 6)", () => {
    setup();
    expect(sec("The tape")).not.toHaveTextContent(/average|median|mean move/i);
  });
});

describe("DailyDigest — forming and the work", () => {
  it("is one line: the Warming count, the cards whose key turned last night by name, the Incubating count", async () => {
    const { onSelectThesis } = setup();
    const f = screen.getByTestId("dd-forming");
    expect(f).toHaveTextContent("2 Warming cards · key turned last night: Card B (Confirmation) · 40 Incubating cards");
    await userEvent.click(within(f).getByRole("button", { name: "Card B" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_B);
  });

  it("still says its counts when nothing is forming", () => {
    setup(emptyPayload());
    expect(screen.getByTestId("dd-forming")).toHaveTextContent("0 Warming cards · 0 Incubating cards");
  });

  it("shows the work: the clock, the night pair, the cards counted and their states, the tape and the span, the cache", () => {
    setup();
    const text = screen.getAllByTestId("dd-work").map((w) => w.textContent);
    expect(text[0]).toContain("As of Thu, Sep 17 · the public clock: what was public by the end of that date");
    expect(text.join("\n")).toContain("48 cards counted of 50 live · 2 created after the as-of");
    expect(text.join("\n")).toContain("45 recorded on the night · 2 carried from an earlier night · 1 with no call yet");
    expect(text.join("\n")).toContain("3 Armed · 2 Warming · 1 Managing · 40 Incubating");
    expect(text.join("\n")).toContain("1 ingest not fresh · 1 reconstructed");
    expect(text.join("\n")).toContain("tape: 5 armed rows on 5 names · 4 priced · 1 unpriced · 0 flagged");
    expect(text.join("\n")).toContain("insider buys: 4 Form 4 filings filed in the span (Wed, Sep 16 to Thu, Sep 17) on 12 names");
    expect(text.join("\n")).toContain("3 Context and Bench roster rows left out");
    expect(text[text.length - 1]).toBe("computed · 1,234 ms");
  });

  it("opens the flagged names under the page's note and states the limits", async () => {
    setup();
    const note = screen.getByText(/1 name left out for a flagged price history/);
    await userEvent.click(note);
    expect(screen.getByText("mixed-scale price history", { exact: false })).toBeInTheDocument();
    for (const l of LIMITS) expect(screen.getByText(l)).toBeInTheDocument();
  });
});

describe("DailyDigest — loudness (invariant #7)", () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "daily-digest.css"), "utf-8");
  // the rules without the comments that precede them (a comment is never part of a selector)
  const rulesOf = () =>
    [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim(), body: m[2] }));

  it("names the Board's Armed color once and the warm tone once, and no other state color or hex in its stylesheet", () => {
    expect(css.match(/--armed/g)).toHaveLength(1);
    expect(css.match(/--warm/g)).toHaveLength(1);
    expect(css).not.toMatch(/--manage|--incub|--leader|--activist|--accent|#[0-9a-f]{3,8}\b/i);
  });

  it("applies each tone through its own class only", () => {
    const rules = rulesOf();
    const using = (token: string) => rules.filter((r) => r.body.includes(`var(${token})`));
    // the accent token feeds only .dd-tone-accent rules; the warm token only .dd-tone-warm rules (and the variable itself)
    for (const r of using("--dd-accent")) for (const sel of r.selector.split(",")) expect(sel).toContain("dd-tone-accent");
    for (const r of using("--dd-warm")) for (const sel of r.selector.split(",")) expect(sel).toContain("dd-tone-warm");
    expect(using("--dd-accent").length).toBeGreaterThan(0);
    expect(using("--dd-warm").length).toBeGreaterThan(0);
  });

  it("sets the sign tone under .dd so the figure's own gray can never outrank it, and uses it for nothing else", () => {
    const rules = rulesOf();
    const tone = rules.filter((r) => r.body.includes("var(--dd-pos)") || r.body.includes("var(--dd-neg)"));
    expect(tone.map((r) => r.selector).sort()).toEqual([".dd .dd-neg", ".dd .dd-pos"]);
    const defs = rules.filter((r) => r.body.includes("--dd-pos:") || r.body.includes("--dd-neg:"));
    expect(defs.map((r) => r.selector)).toEqual([".dd"]); // the two tones are mixed from the app's own --pos / --neg
    expect(css).toMatch(/--dd-pos:\s*color-mix\(in srgb, var\(--pos\) 55%, var\(--txt-3\)\)/);
  });

  it("puts the accent on exactly the lines the server marked, and nowhere else in the markup", () => {
    const { container } = setup();
    const accent = [...container.querySelectorAll(".dd-tone-accent")];
    const warm = [...container.querySelectorAll(".dd-tone-warm")];
    // Card C's move, its newly armed name and its closing entry window; Card D's move and Card E's exit-by
    expect(accent).toHaveLength(3);
    expect(warm).toHaveLength(2);
    expect(accent.every((e) => e.getAttribute("data-tone") === "accent")).toBe(true);
    expect(warm.every((e) => e.getAttribute("data-tone") === "warm")).toBe(true);
    for (const e of accent) expect(cardOf("Card C").contains(e)).toBe(true);
    // nothing in Warming, Incubating, the due list, the buys, the tape or the forming line carries a tone
    for (const name of ["Card B", "Card A"]) expect(cardOf(name).querySelector(".dd-tone-accent, .dd-tone-warm")).toBeNull();
    for (const label of ["Due this week", "Insider buys", "The tape", "Forming", "Positions"]) {
      expect(sec(label).querySelector(".dd-tone-accent, .dd-tone-warm")).toBeNull();
    }
  });

  it("sets Incubating in the grays, last, and uses no badge, toast or count in the menu entry", () => {
    setup();
    const last = cards()[cards().length - 1];
    expect(last).toHaveClass("tier-incubating");
    expect(css).toMatch(/tier-incubating[^{]*\{[^}]*var\(--txt-3\)/);
    expect(document.querySelector('[role="alert"], [role="status"], .badge, .toast')).toBeNull();
  });

  it("never says a winner, a leader or a best idea", () => {
    const { container } = setup();
    expect(container.textContent).not.toMatch(/winner|leader|best idea|must watch|top pick|strongest/i);
  });

  it("puts the sign tone only on a signed return", () => {
    const { container } = setup();
    for (const e of container.querySelectorAll(".dd-pos, .dd-neg")) expect(e.textContent).toMatch(/^[+-]\d/);
    expect(container.querySelectorAll(".dd-pos").length).toBeGreaterThan(0);
  });
});
