import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SharedFacts } from "../SharedFacts";

// The Shared facts card over a mocked queue read: quiet when nothing waits (one calm line, no list), loud only for
// the companies that need the steward, Discovered-pen-only rows folded behind a count (and not even asked for until
// opened), the server's order kept, fifty to a page, a row opens the company page, and nothing on the card writes.

const h = vi.hoisted(() => ({
  calls: [] as { offset: number; pen: string; enabled: boolean }[],
  pages: (() => ({ data: undefined })) as (offset: number, pen: string) => Record<string, unknown>,
  writes: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  usePlatformQueue: (offset: number, pen: string, enabled = true) => {
    h.calls.push({ offset, pen, enabled });
    return enabled ? h.pages(offset, pen) : { data: undefined, isLoading: false, isError: false, error: null };
  },
  useResolveSecurities: () => ({ data: undefined, isFetching: false, isError: false, error: null }),
  useApproveCandidate: h.writes,
  useCorrectCompanyValue: h.writes,
  useDismissCandidate: h.writes,
  useUndoDecision: h.writes,
  usePinCompany: h.writes,
  useUnpinCompany: h.writes,
}));

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

const cand = (over: Record<string, unknown>) => ({
  id: ID(900),
  security_id: ID(1),
  fact_type: "shares_outstanding",
  outcome: "flag",
  flags: [] as string[],
  passages_unreadable: 0,
  recorded_at: "2026-11-01T00:00:00Z",
  ...over,
});

const row = (n: number, over: Record<string, unknown> = {}) => ({
  security_id: ID(n),
  ticker: `T${n}`,
  name: `Company ${n}`,
  cards: 1,
  pen_only: false,
  waiting_since: "2026-11-12T00:00:00Z",
  shares: {
    state: "awaiting_steward",
    candidate: cand({ shares: 12345678, event_date: "2026-09-30", flags: ["stale-cover"] }),
    value: null,
  },
  cash_burn: null,
  ...over,
});

const page = (items: unknown[], total: number, penOnly: number) => ({
  data: { items, total, pen_only_total: penOnly },
  isLoading: false,
  isError: false,
  isFetching: false,
  error: null,
});

const EMPTY_PEN = page([], 0, 0);

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderCard(props: { other?: { one: string; many: string; target: string; count: number }[] } = {}) {
  render(
    <MemoryRouter initialEntries={["/admin"]}>
      <SharedFacts {...props} />
      <Where />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.calls = [];
  h.writes.mockReset();
  h.pages = () => EMPTY_PEN;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-15T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("SharedFacts: nothing waiting", () => {
  it("shows the search and one calm line, no list and no toggle", () => {
    renderCard();
    expect(screen.getByTestId("adm-shared-facts")).toBeInTheDocument();
    expect(screen.getByLabelText("Find a company (ticker or name)")).toBeInTheDocument();
    const line = screen.getByText("Nothing awaits you");
    expect(line.className).not.toMatch(/loud/);
    expect(screen.queryByRole("list", { name: /await/i })).toBeNull();
    expect(screen.queryByTestId("sf-queue")).toBeNull();
    expect(screen.queryByTestId("sf-pen-toggle")).toBeNull();
  });

  it("asks for the non-pen queue only, and fires no write", () => {
    renderCard();
    expect(h.calls.every((c) => c.pen === "exclude" || !c.enabled)).toBe(true);
    expect(h.writes).not.toHaveBeenCalled();
  });
});

describe("SharedFacts: rows waiting", () => {
  beforeEach(() => {
    h.pages = (offset, pen) =>
      pen === "exclude"
        ? page(
            [
              row(2, { cards: 3 }),
              row(1, {
                cards: 0,
                shares: null,
                cash_burn: {
                  state: "from_filing",
                  candidate: cand({ fact_type: "cash_burn", event_date: "2026-09-30" }),
                  value: { cash_usd: 1000000, quarterly_burn_usd: 100000 },
                },
              }),
            ],
            5,
            3,
          )
        : EMPTY_PEN;
  });

  it("a loud count, then the rows in the server's order with ticker, name, cards, what waits and how long", () => {
    renderCard();
    const count = screen.getByTestId("sf-count");
    expect(count).toHaveTextContent("2 companies await you");
    expect(count.className).toMatch(/loud/);
    const rows = screen.getAllByTestId("sf-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("T2"),
      expect.stringContaining("T1"),
    ]);
    expect(rows[0]).toHaveTextContent("Company 2");
    expect(rows[0]).toHaveTextContent("on 3 cards");
    expect(rows[0]).toHaveTextContent("share count: 12,345,678 shares, dated 2026-09-30, flagged stale-cover");
    expect(rows[0]).toHaveTextContent("waiting 3 days");
    expect(rows[1]).toHaveTextContent("on 0 cards");
    expect(rows[1]).toHaveTextContent("cash and burn: a newer filing (2026-09-30) waits beside the value shown");
  });

  it("says 'on 1 card' and a single company in the singular", () => {
    h.pages = (o, pen) => (pen === "exclude" ? page([row(7)], 1, 0) : EMPTY_PEN);
    renderCard();
    expect(screen.getByTestId("sf-count")).toHaveTextContent("1 company awaits you");
    expect(screen.getByTestId("sf-row")).toHaveTextContent("on 1 card");
  });

  it("names both waiting values on one row", () => {
    h.pages = (o, pen) =>
      pen === "exclude"
        ? page(
            [
              row(3, {
                cash_burn: {
                  state: "awaiting_steward",
                  candidate: cand({ fact_type: "cash_burn", cash_usd: 2000000, quarterly_burn_usd: 500000, event_date: "2026-09-30" }),
                  value: null,
                },
              }),
            ],
            1,
            0,
          )
        : EMPTY_PEN;
    renderCard();
    const r = screen.getByTestId("sf-row");
    expect(r).toHaveTextContent("share count:");
    expect(r).toHaveTextContent("cash and burn: $2,000,000 cash, quarterly burn $500,000");
  });

  it("a row is a link to the company's page", async () => {
    renderCard();
    await userEvent.setup().click(within(screen.getAllByTestId("sf-row")[0]).getByRole("link"));
    expect(screen.getByTestId("where")).toHaveTextContent(`/admin/facts/${ID(2)}`);
  });

  it("the card carries the company search box too", () => {
    renderCard();
    expect(screen.getByLabelText("Find a company (ticker or name)")).toBeInTheDocument();
  });

  it("writes nothing", () => {
    renderCard();
    expect(h.writes).not.toHaveBeenCalled();
  });
});

describe("SharedFacts: Discovered-pen-only companies", () => {
  beforeEach(() => {
    h.pages = (offset, pen) => {
      if (pen === "exclude") return page([row(1)], 3, 2);
      if (pen === "only")
        return page(
          [row(8, { pen_only: true, cards: 0 }), row(9, { pen_only: true, cards: 0 })],
          3,
          2,
        );
      return EMPTY_PEN;
    };
  });

  it("are not counted as waiting and not asked for until the toggle opens", () => {
    renderCard();
    expect(screen.getByTestId("sf-count")).toHaveTextContent("1 company awaits you");
    expect(screen.getByTestId("sf-pen-toggle")).toHaveTextContent("2 more are only in a Discovered pen");
    expect(screen.getAllByTestId("sf-row")).toHaveLength(1);
    expect(h.calls.some((c) => c.pen === "only" && c.enabled)).toBe(false);
  });

  it("open below the rest, quiet, and close again in one click", async () => {
    renderCard();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("sf-pen-toggle"));
    const all = screen.getAllByTestId("sf-row");
    expect(all.map((r) => r.textContent)).toEqual([
      expect.stringContaining("T1"),
      expect.stringContaining("T8"),
      expect.stringContaining("T9"),
    ]);
    expect(screen.getAllByTestId("sf-row").filter((r) => r.dataset.pen === "true")).toHaveLength(2);
    expect(screen.getByTestId("sf-pen-toggle")).toHaveTextContent("hide");
    await user.click(screen.getByTestId("sf-pen-toggle"));
    expect(screen.getAllByTestId("sf-row")).toHaveLength(1);
  });

  it("only pen companies: the line stays calm and the toggle is the one thing to open", async () => {
    h.pages = (o, pen) => (pen === "only" ? page([row(8, { pen_only: true })], 1, 1) : page([], 1, 1));
    renderCard();
    const line = screen.getByText("Nothing awaits you");
    expect(line.className).not.toMatch(/loud/);
    expect(screen.queryByTestId("sf-queue")).toBeNull();
    await userEvent.setup().click(screen.getByTestId("sf-pen-toggle"));
    expect(screen.getAllByTestId("sf-row")).toHaveLength(1);
  });
});

describe("SharedFacts: paging by fifty", () => {
  const many = (from: number, n: number) => Array.from({ length: n }, (_, i) => row(from + i));
  beforeEach(() => {
    h.pages = (offset, pen) =>
      pen === "exclude" ? page(many(offset + 1, Math.min(50, 120 - offset)), 120, 0) : EMPTY_PEN;
  });

  it("Next and Previous move the offset by 50", async () => {
    renderCard();
    const user = userEvent.setup();
    expect(screen.getByTestId("sf-range")).toHaveTextContent("1–50 of 120");
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(h.calls.some((c) => c.offset === 50 && c.pen === "exclude")).toBe(true);
    expect(screen.getByTestId("sf-range")).toHaveTextContent("51–100 of 120");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByTestId("sf-range")).toHaveTextContent("101–120 of 120");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByTestId("sf-range")).toHaveTextContent("51–100 of 120");
  });

  it("no pager when one page holds everything", () => {
    h.pages = (o, pen) => (pen === "exclude" ? page([row(1)], 1, 0) : EMPTY_PEN);
    renderCard();
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  });
});

describe("SharedFacts: loading and error", () => {
  it("says it is reading while loading, and the search is still there", () => {
    h.pages = () => ({ data: undefined, isLoading: true, isError: false, error: null });
    renderCard();
    expect(screen.getByText("Reading the queue…")).toBeInTheDocument();
    expect(screen.queryByText("Nothing awaits you")).toBeNull();
    expect(screen.getByLabelText("Find a company (ticker or name)")).toBeInTheDocument();
  });

  it("shows the failure in place and keeps the search", () => {
    h.pages = () => ({ data: undefined, isLoading: false, isError: true, error: new Error("boom") });
    renderCard();
    expect(screen.getByTestId("sf-error")).toBeInTheDocument();
    expect(screen.queryByText("Nothing awaits you")).toBeNull();
    expect(screen.getByLabelText("Find a company (ticker or name)")).toBeInTheDocument();
  });
});

describe("SharedFacts: the one line of other work", () => {
  it("names only the counts above zero, each a button", () => {
    renderCard({
      other: [
        { one: "split to review", many: "splits to review", target: "adm-splits", count: 2 },
        { one: "held insider row", many: "held insider rows", target: "adm-insider-holds", count: 0 },
        { one: "insider amendment owed", many: "insider amendments owed", target: "adm-insider-amendments", count: 1 },
      ],
    });
    const line = screen.getByTestId("sf-other");
    expect(line).toHaveTextContent("Other work below: 2 splits to review · 1 insider amendment owed");
    expect(within(line).getAllByRole("button")).toHaveLength(2);
  });

  it("is absent when there is none", () => {
    renderCard({ other: [{ one: "split to review", many: "splits to review", target: "adm-splits", count: 0 }] });
    expect(screen.queryByTestId("sf-other")).toBeNull();
  });
});
