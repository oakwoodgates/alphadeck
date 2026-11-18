import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SharedFacts } from "../SharedFacts";

// W5f: the queue row for a company with open user flags says how many, and only how many (never whose or what
// note); a company with a flag and no waiting candidate is a normal row that opens its company page.

const h = vi.hoisted(() => ({ items: [] as unknown[] }));

vi.mock("../../api/hooks", () => ({
  usePlatformQueue: (_o: number, pen: string, enabled = true) =>
    enabled
      ? {
          data: { items: pen === "only" ? [] : h.items, total: h.items.length, pen_only_total: 0 },
          isLoading: false,
          isError: false,
          error: null,
        }
      : { data: undefined, isLoading: false, isError: false, error: null },
  useResolveSecurities: () => ({ data: undefined, isFetching: false, isError: false, error: null }),
}));

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

const row = (n: number, over: Record<string, unknown> = {}) => ({
  security_id: ID(n),
  ticker: `T${n}`,
  name: `Company ${n}`,
  cards: 1,
  pen_only: false,
  waiting_since: "2026-11-12T00:00:00Z",
  shares: null,
  cash_burn: null,
  open_flags: 0,
  ...over,
});

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname}</span>;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-15T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

function renderCard() {
  render(
    <MemoryRouter initialEntries={["/admin"]}>
      <SharedFacts />
      <Where />
    </MemoryRouter>,
  );
}

describe("the queue card: open user flags", () => {
  it("says '1 flag from a user' and plural for more, as a count only", () => {
    h.items = [
      row(1, { open_flags: 1 }),
      row(2, {
        open_flags: 3,
        shares: {
          state: "awaiting_steward",
          candidate: {
            id: ID(900),
            security_id: ID(2),
            fact_type: "shares_outstanding",
            outcome: "flag",
            flags: [],
            passages_unreadable: 0,
            recorded_at: "2026-11-01T00:00:00Z",
            shares: 1000,
            event_date: "2026-09-30",
          },
          value: null,
        },
      }),
    ];
    renderCard();
    const [a, b] = screen.getAllByTestId("sf-row");
    expect(a).toHaveTextContent("1 flag from a user");
    expect(b).toHaveTextContent("3 flags from users");
    expect(b).toHaveTextContent("1,000 shares");
  });

  it("shows no flag words on a row without an open flag", () => {
    h.items = [row(1, { open_flags: 0, cash_burn: undefined })];
    renderCard();
    expect(screen.getByTestId("sf-row")).not.toHaveTextContent(/flag/i);
  });

  it("renders a flag-only company (both values null) as a normal row that opens its company page", async () => {
    h.items = [row(7, { open_flags: 2 })];
    renderCard();
    const r = screen.getByTestId("sf-row");
    expect(within(r).getByText("T7")).toBeInTheDocument();
    expect(r).toHaveTextContent("2 flags from users");
    expect(screen.getByTestId("sf-count")).toHaveTextContent("1 company awaits you");
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(within(r).getByRole("link"));
    expect(screen.getByTestId("where")).toHaveTextContent(`/admin/facts/${ID(7)}`);
  });
});
