import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InsiderTracker from "../InsiderTracker";
import type { InsiderTrackerOut } from "../useInsiderTracker";
import { EDGAR, MIXED, R1, R2, R3, R4, ROWS, page, row } from "./fixtures";

// Insider Buying's body with its hook stubbed: a grouped tape (by filing week, name or card) with one row per Form 4
// holding a purchase, every cell (filing date linked to EDGAR, trade date and its "filed N days after the trade"
// note, ticker that opens the Cockpit, card chips, filer, role, shares, price, dollars, the cluster word, tags, the
// trigger chips or "not a trigger", the since-return against IWM, the cap and its tier), the one accent, the chips
// that restate their counts and the controls that cannot discriminate left off, the flagged / unpriced / no-size
// rows kept and marked, the work line, the fixed "no aggregate" line, the limits, and the empty / slow / error
// states. The hook's request has its own test.

const h = vi.hoisted(() => ({ useInsiderTracker: vi.fn() }));
vi.mock("../useInsiderTracker", () => ({ useInsiderTracker: h.useInsiderTracker }));

const ok = (data: InsiderTrackerOut, over: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...over,
});

const select = vi.fn();

function show(data: InsiderTrackerOut = page(), asof = "2026-10-28") {
  h.useInsiderTracker.mockReturnValue(ok(data));
  return render(<InsiderTracker asof={asof} onSelectThesis={select} />);
}

const rows = () => screen.getAllByTestId("ins-row");
const rowFor = (who: RegExp | string) => {
  const el = screen.getByText(who).closest("[data-testid=ins-row]");
  if (!el) throw new Error(`no row for ${String(who)}`);
  return el as HTMLElement;
};
const chip = (name: RegExp | string) => screen.getByRole("button", { name });

beforeEach(() => {
  h.useInsiderTracker.mockReset();
  select.mockReset();
});

describe("the tape", () => {
  it("lists one row per purchase under the filing-week headers, newest week first, each header counting its rows", () => {
    show();
    expect(rows()).toHaveLength(4);
    const groups = screen.getAllByRole("rowgroup");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Week of Oct 26", "Week of Oct 19"]);
    expect(within(groups[0]).getByText("2 purchases")).toBeInTheDocument();
    expect(within(groups[0]).getByText("2 names")).toBeInTheDocument();
    expect(within(groups[1]).getAllByTestId("ins-row")).toHaveLength(2);
  });

  it("draws every column of a row", () => {
    show();
    const r = within(rowFor("Jane Roe"));
    const filed = r.getByRole("link", { name: "Oct 27" });
    expect(filed).toHaveAttribute("href", EDGAR);
    expect(filed).toHaveAttribute("title", "Open the filing on EDGAR (0000876543-26-000010)");
    expect(r.getByText("Oct 22")).toBeInTheDocument(); // the trade date
    expect(r.getByText("filed 5 days after the trade")).toBeInTheDocument();
    expect(r.getByRole("button", { name: "AAA" })).toBeInTheDocument();
    expect(r.getByRole("button", { name: "Card A" })).toBeInTheDocument();
    expect(r.getByText("CEO")).toBeInTheDocument();
    expect(r.getByText("12,000")).toBeInTheDocument();
    expect(r.getByText("$4.10")).toBeInTheDocument();
    expect(r.getByText("$49,200")).toBeInTheDocument();
    expect(r.getByText("cluster · 2 buyers")).toBeInTheDocument();
    expect(r.getByText("trigger on Card A")).toBeInTheDocument();
    expect(r.getByText("+10.0%", { selector: ".ins-ret" })).toBeInTheDocument(); // since
    expect(r.getByText("+1.0%", { selector: ".ins-bench" })).toBeInTheDocument(); // IWM
    expect(r.getByText("≈ $250.0M small")).toBeInTheDocument();
  });

  it("shows the since-return with the sign tone and states the size as dollars", () => {
    show();
    const r = within(rowFor("Sam Poe"));
    expect(r.getByText("-4.2%")).toHaveClass("ins-ret", "neg");
    expect(r.getByText("$1.5M")).toBeInTheDocument();
    expect(r.getByText("+2.0%", { selector: ".ins-bench" })).toBeInTheDocument();
    expect(r.getByText("filed 1 day after the trade")).toBeInTheDocument();
  });

  it("hovers who else is in a cluster, and gives a lone buy no cluster word", () => {
    show();
    expect(within(rowFor("Jane Roe")).getByText("cluster · 2 buyers")).toHaveAttribute(
      "title",
      "Also filed within 30 days: Sam Poe (Oct 20)",
    );
    expect(within(rowFor("Kim Lee")).queryByText(/cluster/)).toBeNull();
  });

  it("draws the one accent on the cluster word, and nowhere else", () => {
    const { container } = show();
    const marked = [...container.querySelectorAll(".ins-cluster")];
    expect(marked).toHaveLength(2);
    expect(marked.every((el) => el.closest("[data-testid=ins-row]") !== null)).toBe(true);
    expect(rows().filter((el) => el.getAttribute("data-cluster") === "yes")).toHaveLength(2);
  });

  it("reads 'trigger on <card>' only where a recorded trigger carries the filing, else 'not a trigger' with its hover", () => {
    show();
    const none = within(rowFor("Sam Poe")).getByText("not a trigger");
    expect(none).toHaveAttribute(
      "title",
      expect.stringContaining("below the detector's bar or screened (a control holder, an internal transfer)"),
    );
    expect(none).toHaveAttribute("title", expect.stringContaining("does not re-derive the screens"));
    const hit = within(rowFor("Jane Roe")).getByText("trigger on Card A");
    expect(hit).toHaveAttribute("title", "First recorded Oct 27 on Card A · still on the card");
    // a trigger that has left the card still reads as a trigger, and says so
    const off = within(rowFor("Cy Dunn"));
    expect(off.getByText("trigger on Card A")).toHaveAttribute(
      "title",
      "First recorded Oct 21 on Card A · no longer on the card at the as-of",
    );
    expect(off.getByText(/off the card/)).toBeInTheDocument();
  });

  it("carries the reader's tags as gray words, and tags nothing else", () => {
    show();
    expect(within(rowFor("Kim Lee")).getByText("10b5-1 plan")).toBeInTheDocument();
    const cy = within(rowFor("Cy Dunn"));
    expect(cy.getByText("applied 4/A")).toBeInTheDocument();
    expect(cy.getByText("internal transfer")).toBeInTheDocument();
    expect(within(rowFor("Jane Roe")).queryByText("10b5-1 plan")).toBeNull();
  });

  it("keeps a flagged, an unpriced and a no-size row and says why", () => {
    show();
    const flagged = within(rowFor("Kim Lee"));
    expect(flagged.getByText(`flagged: ${MIXED}`)).toBeInTheDocument();
    expect(flagged.getByText("$12.0M")).toBeInTheDocument();
    const cap = flagged.getAllByText("—").find((el) => el.closest(".ins-c-cap"));
    expect(cap?.closest(".ins-c-cap")).toHaveAttribute("title", expect.stringContaining("withheld, a data flag reaches its price"));

    const cy = within(rowFor("Cy Dunn"));
    expect(cy.getByText("unpriced")).toBeInTheDocument();
    expect(cy.getByText("4/A")).toBeInTheDocument(); // the amendment's form
    expect(cy.getByText(/and 2 more/)).toBeInTheDocument();
    expect(cy.getByText("not stated")).toBeInTheDocument(); // the role
    expect(cy.getByText("· Context")).toBeInTheDocument(); // the silent tag on its card chip
    const usd = cy.getAllByText("—").find((el) => el.closest(".ins-c-usd"));
    expect(usd).toBeTruthy();
    const capCell = cy.getAllByText("—").find((el) => el.closest(".ins-c-cap"))?.closest(".ins-c-cap");
    expect(capCell).toHaveAttribute("title", expect.stringContaining("awaiting shares"));
  });

  it("says when a filing names no filer", () => {
    show(page([row("n", { filer: null, co_filers: 0 })]));
    expect(screen.getByText("filer not named")).toBeInTheDocument();
  });

  it("opens a row to the reader's own words and closes it again", async () => {
    show();
    const r = within(rowFor("Jane Roe"));
    const open = r.getByRole("button", { name: /Show the filing's words for AAA filed 2026-10-27/ });
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("ins-more")).toBeNull();
    await userEvent.click(open);
    const more = within(screen.getByTestId("ins-more"));
    expect(more.getByText(R1.headline)).toBeInTheDocument();
    expect(more.getByText(R1.detail[1])).toBeInTheDocument();
    expect(more.getByText("accession 0000876543-26-000010")).toBeInTheDocument();
    await userEvent.click(r.getByRole("button", { name: /Hide the filing's words/ }));
    expect(screen.queryByTestId("ins-more")).toBeNull();
  });
});

describe("opening the Cockpit", () => {
  it("opens the first card on the name's ticker", async () => {
    show();
    await userEvent.click(within(rowFor("Jane Roe")).getByRole("button", { name: "AAA" }));
    expect(select).toHaveBeenCalledWith("t-a", "AAA");
  });

  it("opens a card from its chip on the ticker that card uses", async () => {
    show();
    await userEvent.click(within(rowFor("Cy Dunn")).getByRole("button", { name: /Card A/ }));
    expect(select).toHaveBeenCalledWith("t-a", "CCC");
  });

  it("opens the card from a card group's header, and a row's ticker there opens that group's card", async () => {
    const two = row("two", {
      filer: "Two Cards",
      cards: [
        { thesis_id: "t-a", thesis_name: "Card A", ticker: "AAA", silent: null },
        { thesis_id: "t-b", thesis_name: "Card B", ticker: "AAB", silent: null },
      ],
    });
    show(page([two, R3]));
    await userEvent.click(chip("By card"));
    await userEvent.click(screen.getByTitle("Open Card B in the Cockpit"));
    expect(select).toHaveBeenCalledWith("t-b");
    const groupB = screen.getByRole("rowgroup", { name: "Card B" });
    await userEvent.click(within(groupB).getAllByRole("button", { name: /^AA[AB]$/ })[0]);
    expect(select).toHaveBeenLastCalledWith("t-b", "AAB");
  });
});

describe("grouping", () => {
  it("groups by name, newest filing first, with the buyers, the dollars stated and the cluster word", async () => {
    show();
    await userEvent.click(chip("By name"));
    const groups = screen.getAllByRole("rowgroup");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["AAA", "BBB", "CCC"]);
    const aaa = within(groups[0]);
    expect(aaa.getByText("2 buyers")).toBeInTheDocument();
    expect(aaa.getByText("$1.5M total")).toHaveAttribute("title", "from 2 of 2 purchases that state a size");
    expect(aaa.getAllByText("cluster · 2 buyers").length).toBeGreaterThanOrEqual(1);
    expect(within(groups[2]).getByText("size not stated")).toBeInTheDocument();
    expect(within(groups[2]).queryByText(/cluster/)).toBeNull();
    expect(rows()).toHaveLength(4); // grouping never drops a row
  });

  it("groups by card, A→Z, and back to weeks", async () => {
    show();
    await userEvent.click(chip("By card"));
    expect(screen.getAllByRole("rowgroup").map((g) => g.getAttribute("aria-label"))).toEqual(["Card A", "Card B"]);
    expect(chip("By card")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip("By filing week"));
    expect(screen.getAllByRole("rowgroup")).toHaveLength(2);
  });
});

describe("the filters", () => {
  it("restates each chip's count and filters by size, with the page restating the count", async () => {
    show();
    expect(chip(/^under \$100K 1$/)).toBeInTheDocument();
    expect(chip(/^\$1M to \$10M 1$/)).toBeInTheDocument();
    expect(chip(/^\$10M and over 1$/)).toBeInTheDocument();
    expect(chip(/^size not stated 1$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^\$100K to \$1M/ })).toBeNull(); // no row there: no chip
    await userEvent.click(chip(/^\$10M and over/));
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("1 of 4")).toBeInTheDocument();
    expect(screen.getByText(/Showing 1 of 4 purchases/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(rows()).toHaveLength(4);
    expect(screen.queryByText("1 of 4")).toBeNull();
  });

  it("filters by role and the other chips follow it", async () => {
    show();
    expect(chip(/^officer 1$/)).toBeInTheDocument();
    expect(chip(/^director 2$/)).toBeInTheDocument();
    expect(chip(/^10% owner 1$/)).toBeInTheDocument();
    await userEvent.click(chip(/^director/));
    expect(rows()).toHaveLength(2);
    // the size chips now count the director rows only
    expect(chip(/^\$1M to \$10M/)).toHaveTextContent(/\$1M to \$10M\s*1/);
    expect(chip(/^under \$100K/)).toHaveTextContent(/under \$100K\s*0/);
  });

  it("filters by cap tier and by card", async () => {
    show();
    expect(chip(/^small 2$/)).toBeInTheDocument();
    expect(chip(/^no cap 2$/)).toBeInTheDocument();
    await userEvent.click(chip(/^no cap/));
    expect(rows()).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Card" }), "t-b");
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("Kim Lee")).toBeInTheDocument();
  });

  it("filters to clusters and to triggers, each restating its count", async () => {
    show();
    expect(chip(/^Clusters only 2$/)).toBeInTheDocument();
    expect(chip(/^Triggers only 2$/)).toBeInTheDocument();
    await userEvent.click(chip(/^Clusters only/));
    expect(rows()).toHaveLength(2);
    await userEvent.click(chip(/^Triggers only/));
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("Jane Roe")).toBeInTheDocument();
  });

  it("says when no row passes", async () => {
    show();
    await userEvent.click(chip(/^\$10M and over/));
    await userEvent.click(chip(/^Clusters only/));
    expect(screen.queryAllByTestId("ins-row")).toHaveLength(0);
    expect(screen.getByText("No purchase passes the filters.")).toBeInTheDocument();
  });

  it("draws no control that cannot discriminate (WB#3)", () => {
    // one card, one role, one size, one tier, every row a cluster and a trigger: nothing to filter on
    const same = [
      row("a", { cluster: R1.cluster, triggers: R1.triggers }),
      row("b", { filer: "Another Buyer", cluster: R1.cluster, triggers: R1.triggers }),
    ];
    show(page(same));
    for (const name of ["Size", "Role", "Cap"]) expect(screen.queryByRole("group", { name })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Card" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Clusters only/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Triggers only/ })).toBeNull();
    expect(rows()).toHaveLength(2);
  });
});

describe("the window and the Context-and-Bench toggle", () => {
  it("threads the as-of, the window and the toggle to the read", async () => {
    show(page(), "2026-10-28");
    expect(h.useInsiderTracker).toHaveBeenLastCalledWith("2026-10-28", 30, false);
    expect(chip("30 days")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip("90 days"));
    expect(h.useInsiderTracker).toHaveBeenLastCalledWith("2026-10-28", 90, false);
    const toggle = chip("Include Context and Bench");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    expect(h.useInsiderTracker).toHaveBeenLastCalledWith("2026-10-28", 90, true);
    expect(chip("Include Context and Bench")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip("30 days"));
    expect(h.useInsiderTracker).toHaveBeenLastCalledWith("2026-10-28", 30, true);
  });
});

describe("the work line, the notes and the limits", () => {
  it("prints the work, the cache state, the fixed no-aggregate line and the limits", () => {
    show();
    expect(screen.getByText(/As of Oct 28 \(public clock/)).toBeInTheDocument();
    expect(screen.getByText(/2 cards counted · 7 names read · Context and Bench left out \(2 rows\)/)).toBeInTheDocument();
    expect(screen.getByText(/4 purchases listed · 3 names · 11 Form 4 filings read · 6 filings with no purchase folded away/)).toBeInTheDocument();
    expect(screen.getByText(/2 rows in a cluster \(1 name\) · 2 rows are recorded triggers · 14 recorded nights read/)).toBeInTheDocument();
    expect(screen.getByText("computed · 412 ms")).toBeInTheDocument();
    expect(
      screen.getByText(
        "open-market purchases (code P) only; a filing counts from the day it was filed; no aggregate here: outcomes by tier are on the Track Record page",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Open-market purchases only \(code P\)/)).toBeInTheDocument();
    expect(screen.getByText(/The role is the filing's word, as the reader gives it/)).toBeInTheDocument();
    expect(screen.getByText(/Foreign private issuers file no Form 4, so ADR names are absent, not clean/)).toBeInTheDocument();
    expect(screen.getByText(/"Not a trigger" says nothing about why/)).toBeInTheDocument();
    expect(screen.getByText("No aggregate on this page.")).toBeInTheDocument();
  });

  it("says a cache hit when it is one", () => {
    show(page(ROWS, { cache: "hit", compute_ms: 9 }));
    expect(screen.getByText("cache hit · 9 ms when computed")).toBeInTheDocument();
  });

  it("opens the flagged names from a quiet fold", async () => {
    show();
    const fold = screen.getByRole("button", { name: /1 name left unpriced for a flagged price history/ });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(`BBB: flagged, ${MIXED}`)).toBeNull();
    await userEvent.click(fold);
    expect(screen.getByText(`BBB: flagged, ${MIXED}`)).toBeInTheDocument();
  });

  it("draws no fold when no name is flagged", () => {
    show(page(ROWS, { flagged: [] }));
    expect(screen.queryByRole("button", { name: /flagged price history/ })).toBeNull();
  });

  it("shows no aggregate anywhere on the page", () => {
    const { container } = show();
    const fixed = "no aggregate here: outcomes by tier are on the Track Record page";
    const text = (container.textContent ?? "").replace(fixed, "").replace("No aggregate on this page.", "");
    expect(text).not.toMatch(/\b(median|mean|average|avg|win rate|hit rate|pooled)\b/i);
  });
});

describe("the empty, slow and failed states", () => {
  it("says so when no card existed by the as-of", () => {
    show(page([], { counts: { ...page().counts, cards_counted: 0 } }));
    expect(screen.getByText("No card existed by this as-of.")).toBeInTheDocument();
  });

  it("says so when nothing was filed in the window, still printing the work and limits", () => {
    show(page([], { window_days: 30 }));
    expect(screen.getByText(/No open-market purchase was filed by a counted name in the last 30 days/)).toBeInTheDocument();
    expect(screen.getByText(/Open-market purchases only \(code P\)/)).toBeInTheDocument();
  });

  it("shows the slow-read note while the first read loads", () => {
    h.useInsiderTracker.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    render(<InsiderTracker asof="2026-10-28" onSelectThesis={select} />);
    expect(screen.getByText(/Reading every counted name's filings/)).toBeInTheDocument();
  });

  it("says it could not load on an error", () => {
    h.useInsiderTracker.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<InsiderTracker asof="2026-10-28" onSelectThesis={select} />);
    expect(screen.getByText("Could not load Insider Buying.")).toBeInTheDocument();
  });

  it("keeps the previous view up and says it is reading while another as-of loads", () => {
    h.useInsiderTracker.mockReturnValue(ok(page(), { isFetching: true }));
    render(<InsiderTracker asof="2026-10-21" onSelectThesis={select} />); // the data is for Oct 28
    expect(screen.getAllByTestId("ins-row")).toHaveLength(4);
    expect(screen.getByText(/Reading every counted name's filings/)).toBeInTheDocument();
  });
});

describe("the sheet", () => {
  const HERE = dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(join(HERE, "..", "insider-tracker.css"), "utf8");

  it("sticks the column header and the group headers under it", () => {
    expect(css).toMatch(/\.ins-head\s*\{[^}]*position: sticky;[^}]*top: 0/);
    expect(css).toMatch(/\.ins-gh\s*\{[^}]*position: sticky;[^}]*top: 26px/);
  });

  it("tones only the signed since-return, with the app's sign tone mixed 55% into gray", () => {
    expect(css).toContain("--ins-pos: color-mix(in srgb, var(--pos) 55%, var(--txt-3))");
    expect(css).toContain("--ins-neg: color-mix(in srgb, var(--neg) 55%, var(--txt-3))");
    expect(css).toMatch(/\.ins-ret\.pos\s*\{\s*color: var\(--ins-pos\)/);
    expect(css.match(/--ins-pos|--ins-neg/g)?.length).toBe(4); // a definition and a use each
  });

  it("has ONE accent, the cluster word, in the Leaders page's tone", () => {
    expect(css).toContain("--ins-accent: var(--leader)");
    expect(css.match(/--ins-accent/g)?.length).toBe(2); // its definition and its one use
    expect(css).toMatch(/\.ins-cluster\s*\{[^}]*color: var\(--ins-accent\)/);
  });

  it("folds the filer, the trigger and the tags under the row on a phone", () => {
    const phone = css.slice(css.indexOf("@media (max-width: 760px)"));
    expect(phone).toMatch(/grid-template-areas:[^;]*"filer filer role role"/);
    expect(phone).toMatch(/"trig trig tags tags"/);
    expect(phone).toContain(".ins-c-trig { grid-area: trig; }");
  });

  it("uses the app's tokens only: no hard-coded color", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe("the fixtures", () => {
  it("are the four rows the tests above rest on", () => {
    expect(ROWS).toEqual([R1, R3, R2, R4]);
  });
});
