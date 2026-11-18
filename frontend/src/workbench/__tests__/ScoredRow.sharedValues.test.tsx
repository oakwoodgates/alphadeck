import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

// ScoredRow after V2 W5e: no get-data chore of any kind (a share count and cash are the platform's, shared), and the
// stale-shares badge follows the source: an annual figure inside its 400-day cap is quiet (its date rides the hover);
// a quarterly count over six months old keeps the badge. The row calls NO hook now, so none is mocked: a hook call
// here would need a QueryClient and throw.
import { ScoredRow } from "../ScoredRow";
import { cashAwaiting, company, noValue, sharesFromFiling } from "./userValues.fixtures";

const fig = (pips: number | null, value: number | null, provenance: unknown[] = []) => ({
  pips,
  value,
  provenance,
});

const cap = (source: string, sharesAsof: string, value: number | null = 8e9) =>
  fig(null, value, [
    { source: "price", ref: "price:2026-11-14", detail: { close: 10 } },
    { source, ref: "Q", detail: { shares: 1e6, shares_asof: sharesAsof } },
  ]);

function member(over: Record<string, unknown> = {}) {
  return {
    security_id: "s-acme",
    ticker: "ACME",
    name: "Acme Corp",
    instrument_kind: "equity",
    segment: null,
    purity: fig(null, null),
    runway: fig(null, null),
    catalysts: fig(null, null),
    dilution: fig(null, null),
    market_cap: fig(null, null),
    fit: "",
    unconfirmed_estimates: 0,
    ...over,
  };
}

const ASOF = "2026-11-14";

function renderRow(m: unknown, extra: Record<string, unknown> = {}) {
  return render(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <ScoredRow member={m as any} selected={false} onSelect={() => {}} asof={ASOF} {...(extra as any)} />,
  );
}

describe("ScoredRow — no get-data chore", () => {
  it("renders no 'get data', 'data ready' or 'retry get data' control, for a name with or without data", () => {
    for (const m of [member(), member({ market_cap: cap("10-q-cover", "2026-09-30") })]) {
      const { container, unmount } = renderRow(m);
      expect(screen.queryByRole("button", { name: /get data|data ready|retry/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/get data|data ready|retry get data|extracting/i)).not.toBeInTheDocument();
      expect(container.querySelector(".wb-getdata")).toBeNull();
      // the row is still selectable, and shows the cap and the four meters
      expect(screen.getByRole("button", { name: /ACME/ })).toBeInTheDocument();
      unmount();
    }
  });
});

describe("ScoredRow — the stale-shares badge follows the source", () => {
  it("an annual figure inside its 400-day cap shows no badge; its date is in the hover", () => {
    // 2026-03-31 -> 2026-11-14 is 228 days (7.5 months): old for a quarterly count, quiet for an annual figure
    const { container } = renderRow(member({ market_cap: cap("annual-cover", "2026-03-31") }));
    expect(screen.queryByText(/shares ~\d+mo old/)).not.toBeInTheDocument();
    const capEl = container.querySelector(".cap") as HTMLElement;
    expect(capEl.querySelector("[title]")?.getAttribute("title")).toBe("annual figure, as of Mar 31, 2026");
  });

  it("an annual figure past the 400-day cap keeps the badge", () => {
    renderRow(member({ market_cap: cap("annual-cover", "2025-09-30") })); // 410 days
    expect(screen.getByText(/shares ~13mo old/)).toBeInTheDocument();
  });

  it("a quarterly count at seven months keeps the badge, with its months", () => {
    renderRow(member({ market_cap: cap("10-q-cover", "2026-04-14") })); // 214 days = 7 months
    expect(screen.getByText("⚠ shares ~7mo old")).toBeInTheDocument();
  });

  it("a current quarterly count shows no badge", () => {
    renderRow(member({ market_cap: cap("10-q-cover", "2026-09-30") }));
    expect(screen.queryByText(/shares ~/)).not.toBeInTheDocument();
  });
});

describe("ScoredRow — a value awaiting the steward", () => {
  const values = (shares: unknown, cash: unknown) => company(shares as never, cash as never);

  it("a cap blank because the count awaits the steward shows '—' with the hover 'awaiting the steward'", () => {
    const awaitingShares = { ...cashAwaiting(), fact_type: "shares_outstanding" };
    const { container } = renderRow(member(), { values: values(awaitingShares, noValue("cash_burn")) });
    const capEl = container.querySelector(".cap") as HTMLElement;
    expect(capEl).toHaveTextContent("—");
    expect(capEl.querySelector('[title="awaiting the steward"]')).not.toBeNull();
  });

  it("a runway blank because cash awaits the steward carries the same hover", () => {
    renderRow(member(), { values: values(noValue("shares_outstanding"), cashAwaiting()) });
    expect(screen.getByTestId("wb-meter-runway")).toHaveAttribute("title", "awaiting the steward");
  });

  it("an applied value with a newer filing waiting KEEPS its cap (the pending line is the rail's, not the row's)", () => {
    const pending = sharesFromFiling({
      pending: { event_date: "2026-12-31", line: "A newer filing (dated Dec 31, 2026) is awaiting the steward" },
    });
    renderRow(member({ market_cap: cap("10-q-cover", "2026-09-30", 8e9) }), {
      values: values(pending, noValue("cash_burn")),
    });
    expect(screen.getByText("$8.0B")).toBeInTheDocument();
  });

  it("a name with no value yet reads like any blank: no hover, no badge, no mark", () => {
    const { container } = renderRow(member(), {
      values: values(noValue("shares_outstanding"), noValue("cash_burn")),
    });
    expect(container.querySelector('[title="awaiting the steward"]')).toBeNull();
    expect(screen.getByTestId("wb-meter-runway")).not.toHaveAttribute("title");
  });
});
