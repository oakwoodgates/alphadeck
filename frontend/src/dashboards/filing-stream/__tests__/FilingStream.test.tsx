import { render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FilingStream from "../FilingStream";
import type {
  FilingStreamCardsOut,
  FilingStreamDayOut,
  FilingStreamOut,
  FilingStreamRowOut,
} from "../useFilingStream";

// The Filing Stream body with its two hooks stubbed: days newest first, a row per line (chip, form, headline, quiet
// tags, dollar hint, source link), a day's routine Form 4s folded into ONE line that expands in place and a toggle
// that shows them, a row opening to its detail (a 4/A to the Admin page), every filter (kind, link, name, role,
// "Counting names only") with the controls that cannot discriminate left off, the picker, the window threading,
// the work lines and the limits, and the empty / slow states. The hooks' requests have their own test.

const h = vi.hoisted(() => ({ useFilingStream: vi.fn(), useFilingStreamCards: vi.fn() }));
vi.mock("../useFilingStream", () => ({
  useFilingStream: h.useFilingStream,
  useFilingStreamCards: h.useFilingStreamCards,
}));

const EDGAR = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/";

const row = (key: string, over: Partial<FilingStreamRowOut> = {}): FilingStreamRowOut => ({
  key,
  security_id: "s-OKLO",
  ticker: "OKLO",
  kind: "8k",
  form: "8-K",
  date: "2026-10-28",
  headline: "8-K · 5.02 officer/director departure or appointment · 9.01",
  detail: ["Item 5.02 officer/director departure or appointment", "Item 9.01"],
  url: EDGAR,
  accession: "0000876543-26-000010",
  tags: [],
  routine: false,
  usd: null,
  links: ["Reactors"],
  role: null,
  silent: false,
  ...over,
});

const routine = (key: string, ticker: string, over: Partial<FilingStreamRowOut> = {}) =>
  row(key, {
    ticker,
    security_id: `s-${ticker}`,
    kind: "form4",
    form: "4",
    headline: `Jane Roe (CEO): S sale 3,000 sh @ $5.00 · $15,000 (${ticker})`,
    detail: ["2026-10-26 · S · 3,000 sh @ $5.00 · $15,000"],
    routine: true,
    usd: -15000,
    ...over,
  });

const D1: FilingStreamDayOut = {
  date: "2026-10-28",
  routine: 2,
  rows: [
    row("1"),
    routine("2", "OKLO"),
    routine("3", "SMR", { links: ["Fuel"] }),
    row("4", {
      ticker: "MSFT",
      security_id: "s-MSFT",
      kind: "stake",
      form: "SC 13D",
      headline: "SC 13D · Big Fund LP · 7.2%",
      detail: ["Filer CIK 0000555555"],
      links: ["Context"],
      role: "payer",
      silent: true,
    }),
  ],
};
const D2: FilingStreamDayOut = {
  date: "2026-10-27",
  routine: 0,
  rows: [
    row("5", {
      kind: "form4",
      form: "4",
      headline: "Jane Roe (CEO): P purchase 12,000 sh @ $4.10 · $49,200",
      detail: ["2026-10-24 · P · 12,000 sh @ $4.10 · $49,200"],
      tags: ["issuer self-filing", "10b5-1 plan"],
      usd: 49200,
    }),
    row("6", {
      kind: "form4a",
      form: "4/A",
      headline: "4/A by Jane Roe amends the Form 4 filed 2026-10-02",
      detail: ["Filer's remarks: Corrects the price in row 2.", "1 transaction row as filed"],
      url: null,
    }),
  ],
};
const D3: FilingStreamDayOut = {
  date: "2026-10-20",
  routine: 0,
  rows: [
    row("7", {
      ticker: "SMR",
      security_id: "s-SMR",
      kind: "dilution",
      form: "dilution",
      headline: "convertible notes: $350.0M principal · 4.4% of shares outstanding as converted",
      detail: [],
      usd: 350000000,
      links: ["Fuel"],
    }),
  ],
};

const COUNTS = {
  rows: 7,
  by_kind: [
    { kind: "8k", rows: 1 },
    { kind: "stake", rows: 1 },
    { kind: "form4", rows: 3 },
    { kind: "form4a", rows: 1 },
    { kind: "revenue", rows: 0 },
    { kind: "split", rows: 0 },
    { kind: "dilution", rows: 1 },
    { kind: "catalyst", rows: 0 },
    { kind: "ratified", rows: 0 },
  ],
  form4_purchases: 1,
  routine: 2,
} as FilingStreamOut["counts"];

const payload = (over: Partial<FilingStreamOut> = {}): FilingStreamOut => ({
  asof: "2026-10-28",
  thesis_id: "t-nuke",
  thesis_name: "Nuclear",
  days: 90,
  window_start: "2026-07-30",
  clock: "public",
  roster_fallback: false,
  links_today: 2,
  basket_rows: 5,
  silent_rows: 1,
  unresolved: [],
  links: [
    { key: "link:Reactors", label: "Reactors", kind: "link", names: 1 },
    { key: "link:Fuel", label: "Fuel", kind: "link", names: 1 },
    { key: "silent:Context", label: "Context", kind: "silent", names: 1 },
  ],
  roles: ["payer"],
  names: [
    { key: "s-OKLO", ticker: "OKLO", security_id: "s-OKLO", links: ["Reactors"], role: null, silent: false, rows: 3 },
    { key: "s-SMR", ticker: "SMR", security_id: "s-SMR", links: ["Fuel"], role: null, silent: false, rows: 2 },
    { key: "s-MSFT", ticker: "MSFT", security_id: "s-MSFT", links: ["Context"], role: "payer", silent: true, rows: 1 },
  ],
  groups: [D1, D2, D3],
  counts: COUNTS,
  windows: [30, 90, 365],
  cache: "hit",
  compute_ms: 800,
  ...over,
});

const CARDS: FilingStreamCardsOut = {
  asof: "2026-10-28",
  live_cards: 3,
  not_yet_created: 0,
  cards: [
    { thesis_id: "t-copper", name: "Copper", links: 4 },
    { thesis_id: "t-grid", name: "Grid", links: 5 },
    { thesis_id: "t-nuke", name: "Nuclear", links: 2 },
  ],
  cache: "hit",
  compute_ms: 10,
};

function setup(data: FilingStreamOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useFilingStreamCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useFilingStream.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(
    <MemoryRouter>
      <FilingStream asof="2026-10-28" onSelectThesis={onSelectThesis} />
    </MemoryRouter>,
  );
  return { onSelectThesis, ...view };
}

const headlines = () => screen.queryAllByTestId("fs-row").map((r) => r.querySelector(".fs-head")?.textContent?.replace(/^[▸▾]/, ""));
const dayHeads = () => screen.getAllByRole("heading", { level: 2 }).map((e) => e.textContent);

beforeEach(() => {
  h.useFilingStream.mockReset();
  h.useFilingStreamCards.mockReset();
});

describe("FilingStream", () => {
  it("draws the days newest first, each a header over one line per row", () => {
    setup();
    expect(dayHeads()).toEqual([
      "Wed, Oct 28, 2026 4 rows",
      "Tue, Oct 27, 2026 2 rows",
      "Tue, Oct 20, 2026 1 row",
    ]);
    // D1's two routine Form 4s are folded: 3 lines (the 8-K, the fold, the stake)
    const first = screen.getByRole("region", { name: "Wed, Oct 28, 2026" });
    expect(within(first).getAllByTestId("fs-row")).toHaveLength(2);
    expect(within(first).getByRole("button", { name: /2 routine Form 4 filings/ })).toBeInTheDocument();
    expect(headlines()).toContain("SC 13D · Big Fund LP · 7.2%");
  });

  it("shows a row's form, quiet tags (its own, its role, Context / Bench), dollar hint and source link", () => {
    setup();
    const purchase = screen.getAllByTestId("fs-row").find((r) => r.getAttribute("data-kind") === "form4")!;
    expect(within(purchase).getByText("4")).toHaveClass("fs-form");
    expect(within(purchase).getByText("issuer self-filing")).toBeInTheDocument();
    expect(within(purchase).getByText("10b5-1 plan")).toBeInTheDocument();
    expect(within(purchase).getByText("+$49.2K net")).toBeInTheDocument();
    const link = within(purchase).getByRole("link", { name: "Open OKLO 4 on EDGAR" });
    expect(link).toHaveAttribute("href", EDGAR);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // a silent payer carries both quiet tags; an unlinked 4/A carries no anchor at all
    const stake = screen.getAllByTestId("fs-row").find((r) => r.getAttribute("data-kind") === "stake")!;
    expect(within(stake).getByText("payer")).toBeInTheDocument();
    expect(within(stake).getByText("Context")).toBeInTheDocument();
    const amend = screen.getAllByTestId("fs-row").find((r) => r.getAttribute("data-kind") === "form4a")!;
    expect(within(amend).queryByRole("link")).toBeNull();
    // no tag on an ordinary row, and no color by kind (no kind class at all)
    const ev = screen.getAllByTestId("fs-row").find((r) => r.getAttribute("data-kind") === "8k")!;
    expect(ev.querySelectorAll(".fs-tag")).toHaveLength(0);
  });

  it("opens a ticker chip on the Cockpit for that name, and the card name on the card", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(screen.getAllByTitle("Open Nuclear on OKLO")[0]);
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "OKLO");
    await userEvent.click(screen.getByTitle("Open Nuclear on SMR"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "SMR");
    await userEvent.click(screen.getByTitle("Open Nuclear in the Cockpit"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke");
  });

  it("opens a row to its detail lines, and a 4/A to the Admin page for its standing", async () => {
    setup();
    expect(screen.queryByTestId("fs-detail")).toBeNull();
    const btn = screen.getByRole("button", { name: /5\.02 officer\/director/ });
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    const detail = screen.getByTestId("fs-detail");
    expect(within(detail).getByText("Item 9.01")).toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.queryByTestId("fs-detail")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /4\/A by Jane Roe/ }));
    const admin = screen.getByRole("link", { name: "Admin › Insider amendments" });
    expect(admin).toHaveAttribute("href", "/admin");
    expect(screen.getByText("Filer's remarks: Corrects the price in row 2.")).toBeInTheDocument();
    // a row with no detail beyond its headline is plain text, not a button
    expect(screen.queryByRole("button", { name: /convertible notes/ })).toBeNull();
    expect(screen.getByText(/convertible notes: \$350\.0M principal/)).toBeInTheDocument();
  });

  it("folds a day's routine Form 4s into one line that expands in place, and a toggle shows them all", async () => {
    setup();
    expect(headlines()).not.toContain(expect.stringContaining("S sale"));
    const fold = screen.getByRole("button", { name: /2 routine Form 4 filings \(none holds a purchase/ });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(fold);
    expect(fold).toHaveAttribute("aria-expanded", "true");
    expect(headlines().filter((t) => t?.includes("S sale"))).toHaveLength(2);
    await userEvent.click(fold);
    expect(headlines().filter((t) => t?.includes("S sale"))).toHaveLength(0);

    await userEvent.click(screen.getByRole("checkbox", { name: "Show routine Form 4s" }));
    expect(screen.queryByRole("button", { name: /routine Form 4 filings/ })).toBeNull();
    expect(headlines().filter((t) => t?.includes("S sale"))).toHaveLength(2);
    // the work line counted every row all along, folded or not
    expect(screen.getByText(/7 rows in the window:.*3 Form 4s \(1 hold a purchase\)/)).toBeInTheDocument();
  });

  it("filters by kind, offering only the kinds the window holds", async () => {
    setup();
    const kinds = screen.getByRole("group", { name: "Kinds" });
    expect(within(kinds).getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "8-K 1",
      "13D/G 1",
      "Form 4 3",
      "4/A 1",
      "Dilution 1",
    ]);
    await userEvent.click(within(kinds).getByRole("button", { name: /^Form 4/ }));
    expect(within(kinds).getByRole("button", { name: /^Form 4/ })).toHaveAttribute("aria-pressed", "false");
    expect(headlines().some((t) => t?.includes("P purchase"))).toBe(false);
    expect(headlines().some((t) => t?.includes("convertible notes"))).toBe(true);
    // the fold line goes with its rows; the counts line says what the filters left
    expect(screen.queryByRole("button", { name: /routine Form 4 filings/ })).toBeNull();
    expect(screen.getByText(/shown after the filters/)).toBeInTheDocument();
    await userEvent.click(within(kinds).getByRole("button", { name: /^Form 4/ }));
    expect(headlines().some((t) => t?.includes("P purchase"))).toBe(true);
  });

  it("filters by link, name, role and 'Counting names only'", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Link" }), "link:Fuel");
    expect(screen.getAllByTestId("fs-row").map((r) => r.querySelector(".fs-chip")?.textContent)).toEqual(["SMR"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Link" }), "");

    await userEvent.type(screen.getByRole("combobox", { name: "Find a name" }), "msf");
    expect(screen.getAllByTestId("fs-row").map((r) => r.querySelector(".fs-chip")?.textContent)).toEqual(["MSFT"]);
    await userEvent.clear(screen.getByRole("combobox", { name: "Find a name" }));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Role" }), "payer");
    expect(screen.getAllByTestId("fs-row")).toHaveLength(1);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Role" }), "");

    await userEvent.click(screen.getByRole("checkbox", { name: "Counting names only" }));
    expect(screen.getAllByTestId("fs-row").map((r) => r.querySelector(".fs-chip")?.textContent)).not.toContain("MSFT");
    // a filter that leaves nothing says so, the rows still exist behind it
    await userEvent.type(screen.getByRole("combobox", { name: "Find a name" }), "zzzz");
    expect(screen.getByText("No row passes the filters.")).toBeInTheDocument();
  });

  it("offers the name type-ahead over the roster, a name with no rows included", () => {
    const { container } = setup();
    const list = container.querySelector("datalist#fs-names")!;
    expect([...list.querySelectorAll("option")].map((o) => o.getAttribute("value"))).toEqual(["OKLO", "SMR", "MSFT"]);
  });

  it("renders only the controls that can discriminate", () => {
    const quiet = payload({
      links: [{ key: "link:Reactors", label: "Reactors", kind: "link", names: 1 }],
      roles: [],
      names: [payload().names[0]],
      counts: { ...COUNTS, rows: 1, by_kind: COUNTS.by_kind.map((c) => ({ ...c, rows: c.kind === "8k" ? 1 : 0 })), routine: 0 },
      groups: [{ date: "2026-10-28", routine: 0, rows: [row("1")] }],
    });
    setup(quiet);
    expect(screen.queryByRole("combobox", { name: "Link" })).toBeNull(); // one link
    expect(screen.queryByRole("combobox", { name: "Role" })).toBeNull(); // no roles set
    expect(screen.queryByRole("combobox", { name: "Find a name" })).toBeNull(); // one name
    expect(screen.queryByRole("group", { name: "Kinds" })).toBeNull(); // one kind
    expect(screen.queryByRole("checkbox", { name: "Counting names only" })).toBeNull(); // no silent name
    expect(screen.queryByRole("checkbox", { name: "Show routine Form 4s" })).toBeNull(); // nothing to fold
  });

  it("picks the card by search and select, the first A→Z by default, and threads the window", async () => {
    setup();
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(select).toHaveValue("t-copper");
    expect(h.useFilingStream).toHaveBeenLastCalledWith("2026-10-28", "t-copper", 90);
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Grid · 5 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a card" }), "nuc");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Copper · 4 links",
      "Nuclear · 2 links",
    ]);
    await userEvent.selectOptions(select, "t-nuke");
    expect(h.useFilingStream).toHaveBeenLastCalledWith("2026-10-28", "t-nuke", 90);

    const window = screen.getByRole("group", { name: "Window" });
    expect(within(window).getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(window).getByRole("button", { name: "30 days" }));
    expect(h.useFilingStream).toHaveBeenLastCalledWith("2026-10-28", "t-nuke", 30);
    await userEvent.click(within(window).getByRole("button", { name: "365 days" }));
    expect(h.useFilingStream).toHaveBeenLastCalledWith("2026-10-28", "t-nuke", 365);
  });

  it("states the work, the roster note and the limits", () => {
    setup(payload({ unresolved: ["ZZZ"], roster_fallback: true }));
    expect(screen.getByText(/as of Oct 28, 2026 · window Jul 30, 2026 – Oct 28, 2026 \(the last 90 days\) · as it was public by Oct 28, 2026 · cached/)).toBeInTheDocument();
    expect(screen.getByText(/5 roster rows · 3 companies resolved · 1 without a resolved security · 1 in Context or Bench/)).toBeInTheDocument();
    expect(screen.getByText("1 roster name has no resolved security, so no filing can be attached to it: ZZZ.")).toBeInTheDocument();
    expect(screen.getByText(/Today's roster stands in for this card: rosters are point-in-time only from Sep 15, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Ingested facts only: forms other than 8-K, Form 4 and 4\/A/)).toBeInTheDocument();
    expect(screen.getByText(/day-low and implausible-data insider screens are not applied here/)).toBeInTheDocument();
    expect(screen.getByText(/its standing \(applied, kept, pending\) is on Admin › Insider amendments/)).toBeInTheDocument();
  });

  it("says so when the window holds nothing, and while the first load runs", () => {
    const { unmount } = setup(
      payload({
        groups: [],
        counts: { ...COUNTS, rows: 0, by_kind: COUNTS.by_kind.map((c) => ({ ...c, rows: 0 })), routine: 0, form4_purchases: 0 },
      }),
    );
    expect(screen.getByText(/No stored filing-derived fact for this card's names in the last 90 days/)).toBeInTheDocument();
    unmount();
    setup(payload(), { data: undefined, isLoading: true, isFetching: true });
    expect(screen.getByText(/Reading this card's filings/)).toBeInTheDocument();
  });

  it("marks the open row with a neutral class, and its stylesheet uses no state color for hover or open", async () => {
    setup();
    const btn = screen.getByRole("button", { name: /5\.02 officer\/director/ });
    const li = btn.closest("li")!;
    expect(li).not.toHaveClass("open");
    await userEvent.click(btn);
    expect(li).toHaveClass("open");
    await userEvent.click(btn);
    expect(li).not.toHaveClass("open");
    // loudness marks the exception, never an interaction state: no lifecycle / sign color in the sheet's rules
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "filing-stream.css"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).toContain(".fs-head:hover");
    expect(css).not.toMatch(/--(pos|neg|armed|warm|manage|incub|leader|activist)\b/);
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|\bred\b/i);
  });

  it("keeps the previous view up while a new one loads, saying so", () => {
    setup(payload({ thesis_id: "t-other" }), { isFetching: true });
    expect(screen.getByText(/Reading this card's filings/)).toBeInTheDocument();
    expect(screen.getAllByTestId("fs-row").length).toBeGreaterThan(0);
  });
});
