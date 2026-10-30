import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TickerLookup from "../TickerLookup";
import type { TickerLookupOut } from "../useTickerLookup";
import { CARD_A, CARD_B, SID, payload, silentRow, trigger } from "./fixtures";

// The Ticker Lookup body with its reads stubbed: the search box (suggestions as you type, Enter takes an exact
// ticker first then the first suggestion, `/` focuses it, the ticker rides the URL beside ?asof=), the not-found
// view with its suggestions and the former-ticker search, and the seven sections each stating its n: the cards table
// (merged links, the Context tag and role, the weight, authorship, the place on the card with the one accent, the
// archived and excluded folds, the chip opening the Cockpit), the triggers, the tape (signs, gaps, the flagged
// graying, the sparkline), the filings (the window, the fold, a detail), the catalysts, the decisions (voided struck
// through), the work lines and the limits. The hooks' requests have their own test.

const h = vi.hoisted(() => ({
  resolve: vi.fn(),
  lookup: vi.fn(),
  fetchResolve: vi.fn(),
  suggest: vi.fn(),
}));
vi.mock("../useTickerLookup", () => ({
  useResolveTicker: h.resolve,
  useTickerLookup: h.lookup,
  fetchResolve: h.fetchResolve,
}));
vi.mock("../../../api/hooks", () => ({ useResolveSecurities: h.suggest }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

const COMPANY = { security_id: SID, ticker: "TLA", name: "Tape A Corp", matched: "ticker" };

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.search}</div>;
}

function setup(
  data: TickerLookupOut | undefined = payload(),
  { url = "/dashboards/ticker-lookup?asof=2026-10-28&t=TLA", extra = {} as Record<string, unknown> } = {},
) {
  h.lookup.mockImplementation((_asof: string, id: string | null) => ok(id ? data : undefined, extra));
  const onSelectThesis = vi.fn();
  const view = render(
    <MemoryRouter initialEntries={[url]}>
      <TickerLookup asof="2026-10-28" onSelectThesis={onSelectThesis} />
      <Where />
    </MemoryRouter>,
  );
  return { onSelectThesis, ...view };
}

const where = () => screen.getByTestId("where").textContent;
const box = () => screen.getByRole("combobox", { name: /ticker or company name/i });

beforeEach(() => {
  h.resolve.mockReset();
  h.lookup.mockReset();
  h.fetchResolve.mockReset();
  h.suggest.mockReset();
  h.resolve.mockImplementation((t: string | null) => ok(t === "TLA" ? COMPANY : t ? null : undefined));
  h.suggest.mockReturnValue(ok([]));
});

describe("TickerLookup — the sections", () => {
  it("reads the ticker from ?t= and draws the seven sections, each with the n it rests on", () => {
    setup();
    expect(h.resolve).toHaveBeenCalledWith("TLA");
    expect(h.lookup).toHaveBeenCalledWith("2026-10-28", SID);
    for (const name of ["Identity", "Cards", "Triggers the record shows", "Tape", "Filings", "Catalysts", "Your decisions"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(within(screen.getByRole("region", { name: "Cards" })).getByText("on 2 of 4 counted cards")).toBeInTheDocument();
    expect(screen.getByText(/2 rows since Oct 28, 2025 · 1 live/)).toBeInTheDocument();
    expect(screen.getByText(/9 of 10 display members read · last bar Oct 28, 2026/)).toBeInTheDocument();
    expect(screen.getByText("3 rows in the last 90 days")).toBeInTheDocument();
    expect(screen.getByText("3 card catalysts · 1 company fact")).toBeInTheDocument();
    expect(screen.getByText("2 rows · 1 voided")).toBeInTheDocument();
  });

  it("shows the identity: the ticker, the EDGAR link, the business type, the approximate cap and the 20-day dollar volume", () => {
    setup();
    const id = screen.getByRole("region", { name: "Identity" });
    expect(within(id).getByText("TLA")).toBeInTheDocument();
    expect(within(id).getByText("Tape A Corp")).toBeInTheDocument();
    const cik = within(id).getByRole("link", { name: /0000876543/ });
    expect(cik).toHaveAttribute("href", expect.stringContaining("CIK=0000876543"));
    expect(within(id).getByText(/biotech\/pharma · Healthcare/)).toBeInTheDocument();
    expect(screen.getByTestId("tl-cap")).toHaveTextContent("≈ $125.0M · micro");
    expect(screen.getByTestId("tl-cap")).toHaveAttribute("title", expect.stringContaining("Approximate"));
    expect(within(id).getByText("$2.5M")).toBeInTheDocument();
    expect(screen.queryByTestId("tl-flag")).not.toBeInTheDocument();
    expect(within(id).queryByText("Priced under")).not.toBeInTheDocument();
  });

  it("names the vendor symbol and former tickers only when there are some, and a flag is never hidden", () => {
    const base = payload();
    setup(
      payload({
        identity: {
          ...base.identity,
          price_symbol: "TLAD",
          former_tickers: ["OLDA"],
          flag: "mixed-scale price history",
          cap: { ...base.identity.cap, value: null, tier: null, gap: "flagged", note: "mixed-scale price history" },
        },
      }),
    );
    const id = screen.getByRole("region", { name: "Identity" });
    expect(within(id).getByText("TLAD")).toBeInTheDocument();
    expect(within(id).getByText("OLDA")).toBeInTheDocument();
    expect(screen.getByTestId("tl-flag")).toHaveTextContent("mixed-scale price history");
    expect(screen.getByTestId("tl-cap")).toHaveTextContent("not stated: withheld, a data flag reaches its price");
    // the price-derived tape groups are grayed, the insider group is not
    expect(document.querySelector('[data-group="price"]')).toHaveClass("flagged");
    expect(document.querySelector('[data-group="insider"]')).not.toHaveClass("flagged");
  });

  it("lists one row per card with the merged links, the weight, the author and the place, armed the only accent", async () => {
    const { onSelectThesis } = setup();
    const rows = screen.getAllByTestId("tl-card");
    expect(rows).toHaveLength(2);
    const a = within(rows[0]);
    expect(a.getByText("Core · Rest")).toBeInTheDocument();
    expect(a.getByText("weight 3 of 5")).toBeInTheDocument();
    expect(a.queryByText("signed off")).not.toBeInTheDocument(); // only one of the two rows is signed off
    expect(a.getByText(/Core: Supplies the key part\./)).toBeInTheDocument();
    expect(a.getByText("model draft")).toBeInTheDocument();
    expect(a.getByText("your words")).toBeInTheDocument();
    expect(a.getByText("Armed · Oct 27, 2026")).toBeInTheDocument();
    // the accent is the word "armed" alone, never the rest of the cell
    expect(a.getByText("armed")).toHaveClass("tl-armed");
    expect(a.getByText(/^· CORE entry · core · setup strength 62%/)).not.toHaveClass("tl-armed");
    // the silent card: its tag and role, "not counted", no accent
    const b = within(rows[1]);
    expect(b.getByText("Context · payer")).toBeInTheDocument();
    expect(b.getByText("not counted").closest("[data-armed]")).toHaveAttribute("data-armed", "false");
    // the card chip opens the Cockpit on this name
    await userEvent.click(a.getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenCalledWith(CARD_A, "TLA");
    await userEvent.click(b.getByRole("button", { name: "Card B" }));
    expect(onSelectThesis).toHaveBeenLastCalledWith(CARD_B, "TLA");
  });

  it("folds the archived and the excluding cards under the table, and says so when the company is on no card", async () => {
    setup();
    expect(screen.getByText("also on 1 archived card")).toBeInTheDocument();
    expect(screen.getByText("excluded from 1 card")).toBeInTheDocument();
    expect(screen.getByText("Card X: a rival, not a fit")).toBeInTheDocument();
    expect(screen.getByText("Card Old")).toBeInTheDocument();
  });

  it("says a company on no counted card shows identity, tape and filings only", () => {
    const base = payload();
    setup(payload({ cards: { ...base.cards, rows: [], archived: [], excluded: [] } }));
    expect(screen.getByTestId("tl-no-cards")).toHaveTextContent("Not on any counted card at this as-of");
    expect(screen.queryByTestId("tl-archived")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tl-excluded")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Tape" })).toBeInTheDocument();
  });

  it("lists the record's triggers with their fire date, key, role glyph, live mark and sources", async () => {
    const { onSelectThesis } = setup();
    const rows = screen.getAllByTestId("tl-trigger");
    expect(rows).toHaveLength(2);
    const entry = within(rows[0]);
    expect(entry.getByText("Oct 26, 2026")).toBeInTheDocument();
    expect(entry.getByText("Oct 23, 2026 · recorded 3 days after")).toBeInTheDocument();
    expect(entry.getByLabelText("Entry trigger")).toHaveTextContent("●");
    expect(entry.getByText("CEO bought 12,000 shares")).toBeInTheDocument();
    expect(entry.getByText("conviction")).toBeInTheDocument();
    expect(entry.getByText("live")).toBeInTheDocument();
    const src = entry.getByRole("link", { name: /form4 · EDGAR/ });
    expect(src).toHaveAttribute("href", expect.stringContaining("sec.gov/Archives/edgar/data/876543"));
    const risk = within(rows[1]);
    expect(risk.getByLabelText("Risk signal")).toHaveTextContent("○");
    expect(risk.getByText("risk")).toBeInTheDocument();
    expect(risk.getByText("price · 2026-09-30")).toBeInTheDocument(); // a price reference is text, not a link
    expect(rows[1]).toHaveAttribute("data-live", "false");
    await userEvent.click(entry.getByRole("button", { name: "Card A" }));
    expect(onSelectThesis).toHaveBeenCalledWith(CARD_A, "TLA");
  });

  it("hovers a trigger's label with how many times the record changed it, only when it did", () => {
    const base = payload();
    setup(
      payload({
        triggers: {
          ...base.triggers,
          events: [
            trigger({ label: "Structural break: close 127.19", label_changes: 2, kind: "breakdown", role: "risk_signal" }),
            trigger({ label: "Breakout", label_changes: 1 }),
            trigger({ label: "CEO bought 12,000 shares", label_changes: 0 }),
          ],
        },
      }),
    );
    expect(screen.getByText("Structural break: close 127.19")).toHaveAttribute(
      "title",
      "Structural break: close 127.19\nlabel changed 2 times",
    );
    expect(screen.getByText("Breakout")).toHaveAttribute("title", "Breakout\nlabel changed 1 time");
    expect(screen.getByText("CEO bought 12,000 shares")).toHaveAttribute("title", "CEO bought 12,000 shares");
  });

  it("says when the record shows no trigger on the company", () => {
    const base = payload();
    setup(payload({ triggers: { ...base.triggers, events: [], before_window_live: 2 } }));
    expect(screen.getByTestId("tl-no-triggers")).toHaveTextContent("2 live triggers first recorded earlier are not listed");
  });

  it("draws the tape grid with the sign tone only on signed figures, the member's note on a gap and the sparkline", () => {
    setup();
    const fig = (key: string) => document.querySelector(`[data-key="${key}"]`) as HTMLElement;
    expect(fig("ret_1d")).toHaveClass("pos");
    expect(fig("ret_1d")).toHaveTextContent("+1.2%");
    expect(fig("ret_7d")).toHaveClass("neg");
    expect(fig("ret_7d")).toHaveTextContent("-3.4%");
    expect(fig("pct_off_52w_high")).not.toHaveClass("neg"); // not a signed return: no tone
    expect(fig("pct_off_52w_high")).toHaveTextContent("Sep 2, 2026");
    expect(fig("pct_vs_slow")).toHaveClass("na");
    expect(fig("pct_vs_slow")).toHaveTextContent("n/a");
    expect(fig("pct_vs_slow")).toHaveTextContent("n/a: 120/200 bars");
    expect(fig("rvol")).toHaveTextContent("1.82×");
    expect(fig("net_usd")).toHaveTextContent("+$55.0K");
    expect(screen.getByTestId("tl-gaps")).toHaveTextContent("No reading: Base (VCP) (too few price bars for a base)");
    expect(screen.getByTestId("tl-spark").querySelectorAll("path")).toHaveLength(2);
    expect(screen.getByText(/IWM has 2 of 3 bars/)).toBeInTheDocument();
    // not leading, not coiling: nothing shows
    expect(screen.queryByTestId("tl-leading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tl-coiling")).not.toBeInTheDocument();
  });

  it("marks a leading name and a coiling base only when the members say so", () => {
    const base = payload();
    setup(payload({ tape: { ...base.tape, leading: ["IWM"], coiling: true, coiling_detail: "range narrowed to 40%" } }));
    expect(screen.getByTestId("tl-leading")).toHaveTextContent("vs IWM");
    expect(screen.getByTestId("tl-coiling")).toHaveTextContent("range narrowed to 40%");
  });

  it("says when there is no tape", () => {
    const base = payload();
    setup(payload({ tape: { ...base.tape, figures: [], sparkline: null, gaps: [], members_without: 10 } }));
    expect(screen.getByTestId("tl-no-tape")).toBeInTheDocument();
  });

  it("shows the last 90 days of filings by default, folds the routine rows and flips to 365 days", async () => {
    setup();
    const filings = screen.getByRole("region", { name: "Filings" });
    expect(within(filings).getAllByTestId("tl-filing")).toHaveLength(1);
    expect(within(filings).getByRole("region", { name: "Tue, Oct 27, 2026" })).toBeInTheDocument();
    expect(within(filings).queryByRole("region", { name: /May 1, 2026/ })).not.toBeInTheDocument();
    // the day's two routine Form 4s are one folded line that opens in place
    const fold = within(filings).getByRole("button", { name: /2 routine Form 4 filings/ });
    await userEvent.click(fold);
    expect(within(filings).getAllByTestId("tl-filing")).toHaveLength(3);
    // 365 days brings the May filing in
    await userEvent.click(within(filings).getByRole("button", { name: "365 days" }));
    expect(within(filings).getByRole("region", { name: /May 1, 2026/ })).toBeInTheDocument();
    expect(screen.getByText("4 rows in the last 365 days")).toBeInTheDocument();
  });

  it("shows routine rows in place on request and opens a row to its detail with an EDGAR link", async () => {
    setup();
    const filings = screen.getByRole("region", { name: "Filings" });
    await userEvent.click(within(filings).getByRole("checkbox", { name: /show routine rows/i }));
    expect(within(filings).queryByRole("button", { name: /2 routine Form 4 filings/ })).not.toBeInTheDocument();
    expect(within(filings).getAllByTestId("tl-filing")).toHaveLength(3);
    await userEvent.click(within(filings).getByRole("button", { name: /5\.02 officer\/director/ }));
    expect(screen.getByTestId("tl-fdetail")).toHaveTextContent("Item 5.02 officer/director departure or appointment");
    expect(within(filings).getByRole("link", { name: /Open 8-K on EDGAR/ })).toHaveAttribute(
      "href",
      expect.stringContaining("sec.gov"),
    );
  });

  it("says when there is no filing in the window and when the cap left routine rows out", () => {
    const base = payload();
    setup(
      payload({
        filings: {
          ...base.filings,
          days: [],
          not_listed: [{ fold: "prospectus", rows: 40, older_than: "2026-09-01" }],
        },
      }),
    );
    expect(screen.getByTestId("tl-no-filings")).toHaveTextContent("last 90 days");
    expect(screen.getByText(/40 routine rows not listed/)).toBeInTheDocument();
  });

  it("lists the card catalysts in the server's order (passed ones marked) and the company's own catalyst facts", () => {
    setup();
    const items = screen.getAllByTestId("tl-catalyst");
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("Oct 1, 2026"),
      expect.stringContaining("Dec 1, 2026"),
      expect.stringContaining("~Q1"),
    ]);
    expect(items[0]).toHaveClass("passed");
    expect(items[0]).toHaveTextContent("passed");
    expect(items[1]).not.toHaveClass("passed");
    const fact = screen.getByTestId("tl-catalyst-fact");
    expect(fact).toHaveTextContent("catalyst: license decision expected");
    expect(fact).toHaveTextContent("ratified by operator on Oct 16, 2026");
  });

  it("lists the decisions with the operator's fills and a voided row struck through, never hidden", () => {
    setup();
    const rows = screen.getAllByTestId("tl-decision");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-voided", "false");
    expect(rows[0]).toHaveTextContent("void");
    expect(rows[1]).toHaveAttribute("data-voided", "true");
    expect(within(rows[1]).getByText("take")).toHaveClass("tl-struck");
    expect(within(rows[1]).getByText("100 sh @ $10.50")).toHaveClass("tl-struck");
    expect(rows[1]).toHaveTextContent("starter");
  });

  it("says when no decision is logged, and when there is nothing in a catalyst list", () => {
    setup(payload({ decisions: [], decisions_voided: 0, catalyst_facts: [], card_catalysts: [] }));
    expect(screen.getByTestId("tl-no-decisions")).toHaveTextContent("No decision logged on this company as of Oct 28, 2026");
    expect(screen.getByTestId("tl-no-card-catalysts")).toBeInTheDocument();
    expect(screen.getByTestId("tl-no-facts")).toBeInTheDocument();
  });

  it("ends with the work lines and the stated limits", () => {
    setup();
    expect(screen.getByText(/as it was public by Oct 28, 2026 · computed in 0\.9 s/)).toBeInTheDocument();
    expect(screen.getByText(/4 cards counted \(of 5 live; 1 created after the as-of\)/)).toBeInTheDocument();
    expect(screen.getByText("no data flag reaches its price readings")).toBeInTheDocument();
    expect(screen.getByText(/One company at a time/)).toBeInTheDocument();
  });
});

describe("TickerLookup — finding the company", () => {
  it("asks for a ticker when there is none in the URL and reads nothing", () => {
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    expect(screen.getByTestId("tl-empty")).toBeInTheDocument();
    expect(h.lookup).toHaveBeenCalledWith("2026-10-28", null);
  });

  it("upper-cases ?t= and reads a lower-case link", () => {
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28&t=tla" });
    expect(h.resolve).toHaveBeenCalledWith("TLA");
    expect(box()).toHaveValue("TLA");
  });

  it("suggests as you type through the shared company search and picks one into the URL beside ?asof=", async () => {
    h.suggest.mockImplementation((q: string) =>
      ok(q === "tl" ? [{ security_id: SID, ticker: "TLA", name: "Tape A Corp" }, { security_id: "s2", ticker: "TLB", name: "Tape B Corp" }] : []),
    );
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    await userEvent.type(box(), "tl");
    const list = await screen.findByRole("listbox");
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    await userEvent.click(within(list).getByText("TLB"));
    expect(where()).toBe("?asof=2026-10-28&t=TLB");
    expect(box()).toHaveValue("TLB");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("moves through the suggestions with the arrow keys and picks with Enter", async () => {
    h.suggest.mockImplementation((q: string) =>
      ok(q === "tl" ? [{ security_id: SID, ticker: "TLA", name: "A" }, { security_id: "s2", ticker: "TLB", name: "B" }] : []),
    );
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    await userEvent.type(box(), "tl");
    await screen.findByRole("listbox");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    await waitFor(() => expect(where()).toBe("?asof=2026-10-28&t=TLB"));
    expect(h.fetchResolve).not.toHaveBeenCalled();
  });

  it("takes an exact ticker first on Enter", async () => {
    h.fetchResolve.mockResolvedValue(COMPANY);
    h.suggest.mockImplementation((q: string) =>
      ok(q === "tla" ? [{ security_id: "s9", ticker: "TLAB", name: "Another" }] : []),
    );
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    await userEvent.type(box(), "tla{Enter}");
    await waitFor(() => expect(where()).toBe("?asof=2026-10-28&t=TLA"));
    expect(h.fetchResolve).toHaveBeenCalledWith("TLA");
  });

  it("falls back to the first suggestion on Enter when no ticker matches exactly", async () => {
    h.fetchResolve.mockResolvedValue(null);
    h.suggest.mockImplementation((q: string) =>
      ok(q === "tape" ? [{ security_id: SID, ticker: "TLA", name: "Tape A Corp" }, { security_id: "s2", ticker: "TLB", name: "Tape B" }] : []),
    );
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    await userEvent.type(box(), "tape");
    await screen.findByRole("listbox");
    // arrow-less Enter: the exact resolve misses, the first suggestion wins
    await userEvent.type(box(), "{Enter}");
    await waitFor(() => expect(where()).toBe("?asof=2026-10-28&t=TLA"));
    expect(h.fetchResolve).toHaveBeenCalledWith("TAPE");
  });

  it("puts the typed text in the URL when nothing matches, and the page says there is no such company", async () => {
    h.fetchResolve.mockResolvedValue(null);
    h.suggest.mockReturnValue(ok([]));
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28" });
    await userEvent.type(box(), "zzzz{Enter}");
    await waitFor(() => expect(where()).toBe("?asof=2026-10-28&t=ZZZZ"));
    expect(await screen.findByTestId("tl-none")).toHaveTextContent("No company with ticker ZZZZ.");
  });

  it("shows suggestions under a ticker with no company and searches former tickers on request", async () => {
    h.suggest.mockImplementation((q: string) =>
      ok(q === "OLDT" ? [{ security_id: "s2", ticker: "OLDTX", name: "Similar Corp" }] : []),
    );
    h.fetchResolve.mockResolvedValue({ security_id: SID, ticker: "TLA", name: "Tape A Corp", matched: "former" });
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28&t=OLDT" });
    const none = screen.getByTestId("tl-none");
    expect(none).toHaveTextContent("No company with ticker OLDT.");
    expect(within(none).getByRole("button", { name: "OLDTX" })).toBeInTheDocument();
    await userEvent.click(within(none).getByRole("button", { name: "search former tickers too" }));
    await waitFor(() => expect(where()).toBe("?asof=2026-10-28&t=TLA"));
    expect(h.fetchResolve).toHaveBeenCalledWith("OLDT", true);
    expect(await screen.findByText("OLDT is a former ticker of TLA.")).toBeInTheDocument();
  });

  it("says no company has had the ticker when the former search misses", async () => {
    h.fetchResolve.mockResolvedValue(null);
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-10-28&t=NOPE" });
    await userEvent.click(screen.getByRole("button", { name: "search former tickers too" }));
    expect(await screen.findByText("No company has had ticker NOPE, now or formerly.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "search former tickers too" })).not.toBeInTheDocument();
  });

  it("focuses the box on / unless a field already has the keys", async () => {
    setup();
    expect(box()).not.toHaveFocus();
    await userEvent.keyboard("/");
    expect(box()).toHaveFocus();
    expect(box()).toHaveValue("TLA"); // the slash is not typed into the box
    await userEvent.type(box(), "/");
    expect(box()).toHaveValue("TLA/"); // a field that has focus keeps the character
  });

  it("keeps the other URL parameters when the ticker changes", async () => {
    h.fetchResolve.mockResolvedValue(COMPANY);
    setup(payload(), { url: "/dashboards/ticker-lookup?asof=2026-06-05&x=1" });
    await userEvent.type(box(), "tla{Enter}");
    await waitFor(() => expect(where()).toBe("?asof=2026-06-05&x=1&t=TLA"));
  });
});

describe("TickerLookup — loading and failing", () => {
  it("says it is finding the company, then reading it", () => {
    h.resolve.mockImplementation(() => ok(undefined, { isLoading: true }));
    setup(undefined);
    expect(screen.getByText("Finding TLA…")).toBeInTheDocument();
  });

  it("says it is reading the company's page", () => {
    setup(undefined, { extra: { isLoading: true } });
    expect(screen.getByText(/Reading this company/)).toBeInTheDocument();
  });

  it("says when the company's page could not be read", () => {
    setup(undefined, { extra: { error: new Error("boom") } });
    expect(screen.getByText("Could not load the Ticker Lookup.")).toBeInTheDocument();
  });

  it("says when the ticker could not be looked up", () => {
    h.resolve.mockImplementation(() => ok(undefined, { error: new Error("boom") }));
    setup();
    expect(screen.getByText("Could not look up TLA.")).toBeInTheDocument();
  });

  it("keeps the previous company on screen, said, while the next one loads", () => {
    const other = payload({ identity: { ...payload().identity, security_id: "sid-other", ticker: "OLD" } });
    setup(other, { extra: { isFetching: true } });
    expect(screen.getByText(/Reading this company/)).toBeInTheDocument();
    expect(screen.getByText("OLD")).toBeInTheDocument();
  });

  it("lists a card that holds the company only in Context, as not counted", () => {
    const base = payload();
    setup(payload({ cards: { ...base.cards, rows: [silentRow()] } }));
    expect(screen.getAllByTestId("tl-card")).toHaveLength(1);
    expect(screen.getByText("not counted")).toBeInTheDocument();
  });
});
