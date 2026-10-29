import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvidenceLedger from "../EvidenceLedger";
import type {
  EvidenceLedgerCardsOut,
  EvidenceLedgerOut,
  EvidenceLedgerRowOut,
} from "../useEvidenceLedger";

// The Evidence Ledger body with its two hooks stubbed: a standing section above days newest first, a row per line
// (chip, form, label, the quiet class word, the source link), a row opening to its class meaning, byline, inputs and
// sources, every filter (class, kind, company, EDGAR link, window, "Counting names only") with the controls that
// cannot discriminate left off, the picker, the work lines and the limits, and the empty / slow states. The hooks'
// requests have their own test.

const h = vi.hoisted(() => ({ useEvidenceLedger: vi.fn(), useEvidenceLedgerCards: vi.fn() }));
vi.mock("../useEvidenceLedger", () => ({
  useEvidenceLedger: h.useEvidenceLedger,
  useEvidenceLedgerCards: h.useEvidenceLedgerCards,
}));

const EDGAR = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000010/";
const EDGAR_8K = "https://www.sec.gov/Archives/edgar/data/876543/000087654326000020/";

const row = (key: string, over: Partial<EvidenceLedgerRowOut> = {}): EvidenceLedgerRowOut => ({
  key,
  authority: "system",
  kind: "trigger",
  label: "3 insiders bought",
  date: "2026-10-20",
  security_id: "s-OKLO",
  ticker: "OKLO",
  form: "insider",
  silent: null,
  role: null,
  by: null,
  on: null,
  sources: [
    {
      source: "form4",
      ref: "0000876543-26-000010",
      url: EDGAR,
      detail: ["net_usd: 16000.0", "shares: 4000"],
    },
  ],
  detail: ["Signal kind: insider", "Grade: core", "Event date: 2026-10-20", "Recorded in the call for 2026-10-27"],
  edgar: true,
  reconstructed: false,
  ...over,
});

const TRIGGER = row("d3");
const PRICE = row("d1", {
  label: "8-day breakout",
  date: "2026-10-27",
  form: "technical breakout",
  sources: [{ source: "price", ref: "price:s-OKLO:2026-10-27", url: null, detail: ["close: 12.5", "rvol: 2.1"] }],
  detail: ["Signal kind: technical_breakout", "Grade: flip"],
  edgar: false,
});
const RISK = row("d2", {
  kind: "risk_signal",
  label: "convertible overhang",
  ticker: "SMR",
  security_id: "s-SMR",
  date: "2026-10-22",
  form: "dilution risk",
  sources: [{ source: "8-k", ref: "0000876543-26-000020", url: EDGAR_8K, detail: [] }],
  detail: ["Risk signal (feeds the counter-case, never turns a key)"],
});
const FACT = row("d4", {
  authority: "operator",
  kind: "ratified",
  label: "cash $41.2M · burn $8.1M/qtr",
  form: "ratified fact",
  by: "operator",
  on: "2026-10-20",
  sources: [{ source: "ratified fact", ref: "0000876543-26-000010", url: EDGAR, detail: [] }],
  detail: ["Period or cover date: 2026-06-30", "Source: ratified, ratified by operator"],
});
const STORED = row("d5", {
  authority: "automatic",
  kind: "ratified",
  label: "10-q-cover: shares outstanding 540.6M",
  ticker: "MSFT",
  security_id: "s-MSFT",
  date: "2026-08-01",
  form: "stored fact",
  silent: "Context",
  sources: [],
  detail: ["Stored on 2026-10-27", "Applied automatically, not operator-ratified"],
  edgar: false,
});
const VOTE = row("d0", {
  authority: "card",
  kind: "card_catalyst",
  label: "next vote",
  date: "2026-11-20",
  ticker: null,
  security_id: null,
  form: "regulatory",
  sources: [],
  detail: ["Dated 2026-11-20, after the as-of", "When: mid-Nov"],
  edgar: false,
});
const KILL = row("s0", {
  authority: "card",
  kind: "kill",
  label: "loses the DOE contract",
  date: null,
  ticker: null,
  security_id: null,
  form: "kill criterion",
  sources: [],
  detail: [],
  edgar: false,
});
const SOMEDAY = row("s1", {
  authority: "card",
  kind: "card_catalyst",
  label: "someday readout",
  date: null,
  ticker: null,
  security_id: null,
  form: null,
  sources: [],
  detail: ["No date stated"],
  edgar: false,
});
const LINK = row("s2", {
  authority: "card",
  kind: "evidence",
  label: "DOE award notice",
  date: null,
  ticker: null,
  security_id: null,
  form: "8-K",
  sources: [{ source: "8-K", ref: "https://doe.test/award", url: "https://doe.test/award", detail: [] }],
  detail: ["Dated: Oct 2026"],
  edgar: false,
});
const ROLE = row("s3", {
  authority: "operator",
  kind: "role",
  label: "OKLO is tagged payer on this card",
  date: null,
  form: "context role",
  role: "payer",
  by: "operator",
  on: null,
  sources: [],
  detail: ["No roster snapshot dates this role"],
  edgar: false,
});

const STANDING = [KILL, SOMEDAY, LINK, ROLE];
const DAYS = [
  { date: "2026-11-20", rows: [VOTE] },
  { date: "2026-10-27", rows: [PRICE] },
  { date: "2026-10-22", rows: [RISK] },
  { date: "2026-10-20", rows: [TRIGGER, FACT] },
  { date: "2026-08-01", rows: [STORED] },
];

const TALLIES: EvidenceLedgerOut["tallies"] = {
  rows: 10,
  by_authority: [
    { key: "operator", rows: 2 },
    { key: "system", rows: 3 },
    { key: "automatic", rows: 1 },
    { key: "card", rows: 4 },
  ],
  by_kind: [
    { key: "trigger", rows: 2 },
    { key: "risk_signal", rows: 1 },
    { key: "ratified", rows: 2 },
    { key: "catalyst_fact", rows: 0 },
    { key: "card_catalyst", rows: 2 },
    { key: "kill", rows: 1 },
    { key: "evidence", rows: 1 },
    { key: "role", rows: 1 },
  ],
  signals: 3,
  trace_accession: 2,
  trace_price: 1,
  trace_other: 0,
  trace_none: 0,
  facts_ratified: 1,
  facts_stored: 1,
  catalysts_dated: 1,
  catalysts_undated: 1,
  roles_dated: 0,
  roles_undated: 1,
};

const payload = (over: Partial<EvidenceLedgerOut> = {}): EvidenceLedgerOut => ({
  asof: "2026-10-28",
  thesis_id: "t-nuke",
  thesis_name: "Nuclear",
  clock: "public",
  roster_fallback: false,
  night: { asof: "2026-10-27", reconstructed: false },
  night_unreadable: false,
  basket_rows: 4,
  silent_rows: 1,
  unresolved: [],
  facts_days: 365,
  names: [
    { key: "s-OKLO", ticker: "OKLO", security_id: "s-OKLO", role: "payer", silent: null, rows: 4 },
    { key: "s-SMR", ticker: "SMR", security_id: "s-SMR", role: null, silent: null, rows: 1 },
    { key: "s-MSFT", ticker: "MSFT", security_id: "s-MSFT", role: null, silent: "Context", rows: 1 },
    { key: "s-ZZZ", ticker: "ZZZ", security_id: "s-ZZZ", role: null, silent: null, rows: 0 },
  ],
  standing: STANDING,
  groups: DAYS,
  tallies: TALLIES,
  cache: "hit",
  compute_ms: 800,
  ...over,
});

const CARDS: EvidenceLedgerCardsOut = {
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

function setup(data: EvidenceLedgerOut | undefined = payload(), extra: Record<string, unknown> = {}) {
  h.useEvidenceLedgerCards.mockReturnValue({ data: CARDS, isLoading: false, error: null });
  h.useEvidenceLedger.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });
  const onSelectThesis = vi.fn();
  const view = render(<EvidenceLedger asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const labels = () =>
  screen.queryAllByTestId("el-row").map((r) => r.querySelector(".el-head")?.textContent?.replace(/^[▸▾]/, ""));
const heads = () => screen.getAllByRole("heading", { level: 2 }).map((e) => e.textContent);
const rowOf = (text: string) =>
  screen.getAllByTestId("el-row").find((r) => (r.querySelector(".el-head")?.textContent ?? "").includes(text))!;
/** the row's headline button (the ticker chip is a button too) */
const headOf = (text: string) => rowOf(text).querySelector<HTMLButtonElement>("button.el-head")!;

beforeEach(() => {
  h.useEvidenceLedger.mockReset();
  h.useEvidenceLedgerCards.mockReset();
});

describe("EvidenceLedger", () => {
  it("draws the standing section above the days, newest first, each a header over one line per row", () => {
    setup();
    expect(heads()).toEqual([
      "Standing 4 rowsno date: the card's own entries and roles no roster snapshot dates",
      "Fri, Nov 20, 2026 1 row",
      "Tue, Oct 27, 2026 1 row",
      "Thu, Oct 22, 2026 1 row",
      "Tue, Oct 20, 2026 2 rows",
      "Sat, Aug 1, 2026 1 row",
    ]);
    const standing = screen.getByRole("region", { name: "Standing" });
    expect(within(standing).getAllByTestId("el-row")).toHaveLength(4);
    expect(labels()).toEqual([
      "loses the DOE contract",
      "someday readout",
      "DOE award notice",
      "OKLO is tagged payer on this card",
      "next vote",
      "8-day breakout",
      "convertible overhang",
      "3 insiders bought",
      "cash $41.2M · burn $8.1M/qtr",
      "10-q-cover: shares outstanding 540.6M",
    ]);
  });

  it("marks every row with the quiet word of who put it there, and Context / Bench when its company sits there", () => {
    setup();
    const tag = (r: HTMLElement) => [...r.querySelectorAll(".el-tag")].map((t) => t.textContent);
    expect(tag(rowOf("3 insiders bought"))).toEqual(["computed"]);
    expect(tag(rowOf("cash $41.2M"))).toEqual(["your"]);
    expect(tag(rowOf("shares outstanding 540.6M"))).toEqual(["stored", "Context"]);
    expect(tag(rowOf("next vote"))).toEqual(["card"]);
    expect(tag(rowOf("OKLO is tagged payer"))).toEqual(["your"]);
    // the word carries what it means on hover, as a label of who and never a judgment
    const word = rowOf("3 insiders bought").querySelector(".el-tag")!;
    expect(word).toHaveAttribute("title", "Computed: a computation on public data (a trigger or a risk signal)");
    expect(rowOf("cash $41.2M").querySelector(".el-tag")).toHaveAttribute("title", "Your: a person ratified or set it");
    // no color or badge by class: the rows carry the class as data, not as a style
    expect(rowOf("next vote")).toHaveAttribute("data-authority", "card");
  });

  it("links a row's first source: EDGAR for a filing, nothing for a price reference", () => {
    setup();
    const trigger = rowOf("3 insiders bought");
    const link = within(trigger).getByRole("link", { name: "Open OKLO insider on EDGAR" });
    expect(link).toHaveAttribute("href", EDGAR);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(rowOf("8-day breakout")).queryByRole("link")).toBeNull();
    // a stored URL that is not an SEC page is a source link, not an EDGAR one
    expect(within(rowOf("DOE award notice")).getByRole("link", { name: "Open card 8-K on its source" })).toHaveAttribute(
      "href",
      "https://doe.test/award",
    );
    expect(within(rowOf("loses the DOE contract")).queryByRole("link")).toBeNull();
  });

  it("opens a trigger to its class, its inputs and each source with its link", async () => {
    setup();
    const btn = headOf("3 insiders bought");
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    const detail = screen.getByTestId("el-detail");
    expect(within(detail).getByText("Computed: a computation on public data (a trigger or a risk signal)")).toBeInTheDocument();
    expect(within(detail).getByText("Grade: core")).toBeInTheDocument();
    expect(within(detail).getByText("Source: form4 · 0000876543-26-000010")).toBeInTheDocument();
    expect(within(detail).getByText("net_usd: 16000.0")).toBeInTheDocument();
    expect(within(detail).getByText("shares: 4000")).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: "EDGAR ↗" })).toHaveAttribute("href", EDGAR);
    await userEvent.click(btn);
    expect(screen.queryByTestId("el-detail")).toBeNull();
  });

  it("shows a price reference as text with its inputs, and no link", async () => {
    setup();
    await userEvent.click(headOf("8-day breakout"));
    const detail = screen.getByTestId("el-detail");
    expect(within(detail).getByText("Source: price · price:s-OKLO:2026-10-27")).toBeInTheDocument();
    expect(within(detail).getByText("close: 12.5")).toBeInTheDocument();
    expect(within(detail).queryByRole("link")).toBeNull();
  });

  it("opens an operator row to who and when, and a role to its approved description and the day it lacks", async () => {
    setup();
    await userEvent.click(headOf("cash $41.2M"));
    expect(within(rowOf("cash $41.2M")).getByText("ratified by operator · Oct 20, 2026")).toBeInTheDocument();
    await userEvent.click(headOf("OKLO is tagged payer"));
    const roleDetail = within(rowOf("OKLO is tagged payer")).getByTestId("el-detail");
    expect(within(roleDetail).getByText("set by operator · day not recorded")).toBeInTheDocument();
    expect(within(roleDetail).getByText("The demand side: buys the theme's product or pays its price.")).toBeInTheDocument();
    expect(within(roleDetail).getByText("No roster snapshot dates this role")).toBeInTheDocument();
  });

  it("makes a row with nothing to open plain text, not a button", () => {
    setup();
    expect(within(rowOf("loses the DOE contract")).queryByRole("button")).toBeNull();
    expect(within(rowOf("loses the DOE contract")).getByText("loses the DOE contract")).toHaveClass("plain");
  });

  it("opens a ticker chip on the Cockpit for that name, and the card name on the card; a row with no company has no chip", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(rowOf("3 insiders bought")).getByTitle("Open Nuclear on OKLO"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "OKLO");
    await userEvent.click(within(rowOf("convertible overhang")).getByTitle("Open Nuclear on SMR"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke", "SMR");
    await userEvent.click(screen.getByTitle("Open Nuclear in the Cockpit"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-nuke");
    expect(within(rowOf("next vote")).queryByRole("button", { name: /Open Nuclear on/ })).toBeNull();
  });

  it("filters by class, offering only the classes the ledger holds", async () => {
    setup();
    const classes = screen.getByRole("group", { name: "Classes" });
    expect(within(classes).getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "Your 2",
      "Computed 3",
      "Stored 1",
      "Card 4",
    ]);
    await userEvent.click(within(classes).getByRole("button", { name: /^Computed/ }));
    expect(within(classes).getByRole("button", { name: /^Computed/ })).toHaveAttribute("aria-pressed", "false");
    expect(labels()).not.toContain("3 insiders bought");
    expect(labels()).toContain("cash $41.2M · burn $8.1M/qtr");
    expect(screen.getByText(/shown after the filters/)).toBeInTheDocument();
    await userEvent.click(within(classes).getByRole("button", { name: /^Computed/ }));
    expect(labels()).toContain("3 insiders bought");
  });

  it("filters by kind, dropping a day it leaves empty", async () => {
    setup();
    const kinds = screen.getByRole("group", { name: "Kinds" });
    expect(within(kinds).getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "Triggers 2",
      "Risk signals 1",
      "Ratified and stored facts 2",
      "Card catalysts 2",
      "Kill criteria 1",
      "Evidence links 1",
      "Context roles 1",
    ]);
    for (const name of [/^Triggers/, /^Risk signals/, /^Card catalysts/, /^Kill criteria/, /^Evidence links/, /^Context roles/]) {
      await userEvent.click(within(kinds).getByRole("button", { name }));
    }
    expect(labels()).toEqual(["cash $41.2M · burn $8.1M/qtr", "10-q-cover: shares outstanding 540.6M"]);
    expect(heads()).toEqual(["Tue, Oct 20, 2026 1 row", "Sat, Aug 1, 2026 1 row"]); // the standing section and empty days are gone
  });

  it("filters by company, EDGAR link, window and 'Counting names only'", async () => {
    setup();
    await userEvent.type(screen.getByRole("combobox", { name: "Find a company" }), "smr");
    expect(labels()).toEqual(["convertible overhang"]);
    await userEvent.clear(screen.getByRole("combobox", { name: "Find a company" }));

    await userEvent.click(screen.getByRole("checkbox", { name: "Only rows with an EDGAR link" }));
    expect(labels()).toEqual(["convertible overhang", "3 insiders bought", "cash $41.2M · burn $8.1M/qtr"]);
    await userEvent.click(screen.getByRole("checkbox", { name: "Only rows with an EDGAR link" }));

    await userEvent.click(screen.getByRole("checkbox", { name: "Counting names only" }));
    expect(labels()).not.toContain("10-q-cover: shares outstanding 540.6M");
    await userEvent.click(screen.getByRole("checkbox", { name: "Counting names only" }));

    // the 30-day window (from Sep 28) cuts the stored fact of Aug 1, never the standing rows nor the future-dated vote
    const win = screen.getByRole("group", { name: "Window" });
    expect(within(win).getByRole("button", { name: "All rows held" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(win).getByRole("button", { name: "30 days" }));
    expect(labels()).not.toContain("10-q-cover: shares outstanding 540.6M");
    expect(labels()).toContain("next vote");
    expect(labels()).toContain("loses the DOE contract");

    // a filter that leaves nothing says so; the rows still exist behind it
    await userEvent.type(screen.getByRole("combobox", { name: "Find a company" }), "zzzz");
    expect(screen.getByText("No row passes the filters.")).toBeInTheDocument();
  });

  it("offers the company type-ahead over the roster, a company with no rows included", () => {
    const { container } = setup();
    const list = container.querySelector("datalist#el-names")!;
    expect([...list.querySelectorAll("option")].map((o) => o.getAttribute("value"))).toEqual(["OKLO", "SMR", "MSFT", "ZZZ"]);
  });

  it("renders only the controls that can discriminate", () => {
    const quiet = (rows: EvidenceLedgerRowOut[]) =>
      payload({
        names: [payload().names[0]],
        standing: [],
        groups: [{ date: "2026-10-20", rows }],
        tallies: {
          ...TALLIES,
          rows: rows.length,
          by_authority: TALLIES.by_authority.map((c) => ({ ...c, rows: c.key === "system" ? rows.length : 0 })),
          by_kind: TALLIES.by_kind.map((c) => ({ ...c, rows: c.key === "trigger" ? rows.length : 0 })),
        },
      });
    const { unmount } = setup(quiet([TRIGGER, { ...PRICE, date: "2026-10-20" }]));
    expect(screen.queryByRole("group", { name: "Classes" })).toBeNull(); // one class
    expect(screen.queryByRole("group", { name: "Kinds" })).toBeNull(); // one kind
    expect(screen.queryByRole("combobox", { name: "Find a company" })).toBeNull(); // one company
    expect(screen.queryByRole("checkbox", { name: "Counting names only" })).toBeNull(); // no silent company
    expect(screen.getByRole("checkbox", { name: "Only rows with an EDGAR link" })).toBeInTheDocument(); // one row links, one does not
    unmount();
    // with no row linking to EDGAR the check would cut nothing
    setup(quiet([{ ...PRICE, date: "2026-10-20" }]));
    expect(screen.queryByRole("checkbox", { name: "Only rows with an EDGAR link" })).toBeNull();
  });

  it("says the trace of the fired signals in the header, and the recorded night", () => {
    setup();
    expect(screen.getByText("2 of 3 fired signals trace to an EDGAR accession · 1 to a price reference only")).toBeInTheDocument();
    expect(screen.getByText("triggers from the call recorded for Oct 27, 2026", { selector: ".el-night" })).toBeInTheDocument();
  });

  it("says when the night is reconstructed, missing or unreadable", () => {
    const { unmount } = setup(payload({ night: { asof: "2026-10-27", reconstructed: true } }));
    expect(screen.getByText(/a reconstructed night: reported, never scored/, { selector: ".el-night" })).toBeInTheDocument();
    unmount();
    const second = setup(payload({ night: null }));
    expect(screen.getByText("no call recorded by this as-of, so no trigger is listed", { selector: ".el-night" })).toBeInTheDocument();
    second.unmount();
    setup(payload({ night: null, night_unreadable: true }));
    expect(screen.getByText(/could not be read, so no trigger is listed/, { selector: ".el-night" })).toBeInTheDocument();
  });

  it("picks the card by search and select, the first A→Z by default", async () => {
    setup();
    const select = screen.getByRole("combobox", { name: "Card" });
    expect(select).toHaveValue("t-copper");
    expect(h.useEvidenceLedger).toHaveBeenLastCalledWith("2026-10-28", "t-copper");
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
    expect(h.useEvidenceLedger).toHaveBeenLastCalledWith("2026-10-28", "t-nuke");
  });

  it("states the work, the roster note, what is not evidence and the limits", () => {
    setup(payload({ unresolved: ["ZZZ"], roster_fallback: true }));
    expect(screen.getByText(/as of Oct 28, 2026 · as it was public by Oct 28, 2026 · triggers from the call recorded for Oct 27, 2026 · cached/, { selector: ".el-work" })).toBeInTheDocument();
    expect(screen.getByText("10 rows: 2 your · 3 computed · 1 stored · 4 card")).toBeInTheDocument();
    expect(screen.getByText(/1 ratified fact and 1 stored fact \(the last 365 days\) · 1 catalyst dated, 1 undated · 1 kill criterion · 1 evidence link · 1 context role \(1 undated\)/)).toBeInTheDocument();
    expect(screen.getByText(/4 roster rows · 4 companies resolved · 1 without a resolved security · 1 in Context or Bench/)).toBeInTheDocument();
    expect(screen.getByText("1 roster name has no resolved security, so no stored fact can be attached to it: ZZZ.")).toBeInTheDocument();
    expect(screen.getByText(/Today's roster stands in for this card: rosters are point-in-time only from Sep 15, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Not in the ledger, because none is evidence: display signals/)).toBeInTheDocument();
    expect(screen.getByText(/thesis-fit prose \(a drafted description, not a fact\)/)).toBeInTheDocument();
    expect(screen.getByText(/The card's own entries \(catalysts, kill criteria, evidence links\) load live/)).toBeInTheDocument();
    expect(screen.getByText(/The evidence table is nearly unused today/)).toBeInTheDocument();
    expect(screen.getByText(/Located passages are not stored/)).toBeInTheDocument();
  });

  it("says so when nothing is recorded, and while the first load runs", () => {
    const { unmount } = setup(
      payload({
        standing: [],
        groups: [],
        night: null,
        tallies: {
          ...TALLIES,
          rows: 0,
          signals: 0,
          by_authority: TALLIES.by_authority.map((c) => ({ ...c, rows: 0 })),
          by_kind: TALLIES.by_kind.map((c) => ({ ...c, rows: 0 })),
        },
      }),
    );
    expect(screen.getByText(/Nothing is recorded for this card at this as-of/)).toBeInTheDocument();
    unmount();
    setup(payload(), { data: undefined, isLoading: true, isFetching: true });
    expect(screen.getByText(/Reading this card's record/)).toBeInTheDocument();
  });

  it("keeps the previous view up while a new one loads, saying so", () => {
    setup(payload({ thesis_id: "t-other" }), { isFetching: true });
    expect(screen.getByText(/Reading this card's record/)).toBeInTheDocument();
    expect(screen.getAllByTestId("el-row").length).toBeGreaterThan(0);
  });

  it("marks the open row with a neutral class, and its stylesheet uses no state color for class, hover or open", async () => {
    setup();
    const btn = headOf("3 insiders bought");
    const li = btn.closest("li")!;
    expect(li).not.toHaveClass("open");
    await userEvent.click(btn);
    expect(li).toHaveClass("open");
    await userEvent.click(btn);
    expect(li).not.toHaveClass("open");
    // loudness marks the exception, never a class or an interaction state: no lifecycle / sign color in the sheet
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "evidence-ledger.css"), "utf8").replace(
      /\/\*[\s\S]*?\*\//g,
      "",
    );
    expect(css).toContain(".el-head:hover");
    expect(css).not.toMatch(/data-authority|\.el-tag\.|\.el-row\[/);
    expect(css).not.toMatch(/--(pos|neg|armed|warm|manage|incub|leader|activist)\b/);
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|\bred\b/i);
  });
});
