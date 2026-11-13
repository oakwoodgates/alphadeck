import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FundLaunchesLine } from "../FundLaunchesLine";

// The Cockpit's quiet fund-launch line with the API layer stubbed: hidden at 0, the counts line, collapsed by
// default, and the reversible add ⇄ remove (add calls attach, then pulls prices for the returned security).
// Made-up funds and cards.

const h = vi.hoisted(() => ({
  q: { data: undefined as unknown },
  attach: { mutate: vi.fn(), isPending: false, error: null as unknown },
  ingest: { mutate: vi.fn() },
  args: [] as unknown[][],
}));

vi.mock("../../api/hooks", () => ({
  useFundLaunches: (...a: unknown[]) => {
    h.args.push(a);
    return h.q;
  },
  useFundAttach: () => h.attach,
  useIngestPrices: () => h.ingest,
}));

const fund = (over: Record<string, unknown> = {}) => ({
  key: "S1",
  series_id: "S1",
  name: "Test Uranium ETF",
  stage: { filed: "2026-10-01", effective: "2026-10-20", ticker: "2026-10-22", listed: null, current: "ticker" },
  symbols: ["TSTX"],
  in_etfs: false,
  matches: [
    {
      thesis_id: "t-1",
      thesis_name: "Test Card A",
      tier: "signal",
      signal_terms: ["uranium"],
      broad_terms: [],
      name_hits: [],
    },
  ],
  ...over,
});

const data = (funds: unknown[]) => ({
  thesis_id: "t-1",
  asof: "2026-11-04",
  window_days: 90,
  counts: { filings: 3, sponsors: 2, funds: 4, signal_filings: 1, broad_filings: 1, name_filings: 1 },
  filings: [
    {
      accession: "A-1",
      form: "485APOS",
      trust: "Test Trust",
      public_date: "2026-10-01",
      url: "https://example.test/A-1",
      private_to_public: false,
      funds,
    },
  ],
});

beforeEach(() => {
  h.args = [];
  h.attach.mutate.mockReset();
  h.ingest.mutate.mockReset();
  h.q = { data: data([fund()]) };
});

describe("FundLaunchesLine", () => {
  it("renders nothing when there are no matching filings (honest loudness)", () => {
    h.q = { data: { ...data([]), counts: { ...data([]).counts, filings: 0 } } };
    const { container } = render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing while loading", () => {
    h.q = { data: undefined };
    const { container } = render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("reads at the Cockpit's asof and shows the counts line, collapsed by default", () => {
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    expect(h.args[0]).toEqual(["t-1", "2026-11-04"]);
    const head = screen.getByRole("button", { name: /fund launches · 3 filings from 2 sponsors/ });
    expect(head).toHaveTextContent("in 90 days (signal 1 · broad 1 · name 1)");
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Test Uranium ETF")).not.toBeInTheDocument();
  });

  it("says quietly, in its title, since when tickers and listings are tracked", () => {
    h.q = { data: { ...data([fund()]), symbol_history_since: "2026-11-05" } };
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    expect(screen.getByRole("button", { name: /fund launches/ })).toHaveAttribute(
      "title",
      expect.stringContaining("tickers and listings tracked since 2026-11-05"),
    );
  });

  it("expands to the list with stage chips, the card's terms and the filing link", async () => {
    const user = userEvent.setup();
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    await user.click(screen.getByRole("button", { name: /fund launches/ }));
    expect(screen.getByText("Test Uranium ETF")).toBeInTheDocument();
    expect(screen.getByText("uranium")).toBeInTheDocument();
    expect(screen.getByText("ticker")).toBeInTheDocument();
    expect(screen.getByText("filing ↗")).toHaveAttribute("href", "https://example.test/A-1");
  });

  it("offers + add to ETFs only at the ticker stage or later, and pulls prices after a successful add", async () => {
    const user = userEvent.setup();
    h.q = {
      data: data([
        fund(),
        fund({
          key: "S2",
          name: "Test Early ETF",
          symbols: [],
          stage: { filed: "2026-10-01", effective: null, ticker: null, listed: null, current: "filed" },
        }),
      ]),
    };
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    await user.click(screen.getByRole("button", { name: /fund launches/ }));
    expect(screen.getAllByRole("button", { name: "+ add to ETFs" })).toHaveLength(1); // not on the filed-only fund

    h.attach.mutate.mockImplementation((_v, opts) =>
      opts?.onSuccess?.({ added: true, security_id: "sid-9" }),
    );
    await user.click(screen.getByRole("button", { name: "+ add to ETFs" }));
    expect(h.attach.mutate.mock.calls[0][0]).toEqual({ thesis_id: "t-1", symbol: "TSTX" });
    expect(h.ingest.mutate).toHaveBeenCalledWith("sid-9");
  });

  it("an already-added fund shows the reversible remove, which detaches (and pulls no prices)", async () => {
    const user = userEvent.setup();
    h.q = { data: data([fund({ in_etfs: true })]) };
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    await user.click(screen.getByRole("button", { name: /fund launches/ }));
    expect(screen.queryByRole("button", { name: "+ add to ETFs" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "✓ in ETFs — remove" }));
    expect(h.attach.mutate).toHaveBeenCalledWith({ thesis_id: "t-1", symbol: "TSTX", detach: true });
    expect(h.ingest.mutate).not.toHaveBeenCalled();
  });

  it("an idempotent attach (already in) pulls no prices", async () => {
    const user = userEvent.setup();
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
    await user.click(screen.getByRole("button", { name: /fund launches/ }));
    h.attach.mutate.mockImplementation((_v, opts) =>
      opts?.onSuccess?.({ already: true, added: false, security_id: "sid-9" }),
    );
    await user.click(screen.getByRole("button", { name: "+ add to ETFs" }));
    expect(h.ingest.mutate).not.toHaveBeenCalled();
  });

  it("shows the not-in-the-company-list answer plainly, as the API words it", () => {
    const msg = "This fund is not in the company list yet. A steward can add it.";
    h.attach = { ...h.attach, error: { detail: msg } };
    try {
      render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
      expect(screen.getByText(msg)).toBeInTheDocument(); // the whole line is the message, no "failed:" prefix
      expect(screen.queryByText(/add\/remove failed/)).not.toBeInTheDocument();
    } finally {
      h.attach = { ...h.attach, error: null };
    }
  });

  it("shows a refused price pull (the daily allowance) plainly, as the API words it", () => {
    const msg =
      "Daily allowance reached: 5,000 price pulls a day. It resets at midnight America/New_York time.";
    const saved = h.ingest;
    h.ingest = { ...h.ingest, error: { detail: msg } } as typeof h.ingest;
    try {
      render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" />);
      expect(screen.getByText(msg)).toBeInTheDocument();
    } finally {
      h.ingest = saved;
    }
  });

  it("the buttons are disabled on a read-only (captured) view", async () => {
    const user = userEvent.setup();
    render(<FundLaunchesLine thesisId="t-1" asof="2026-11-04" readOnly />);
    await user.click(screen.getByRole("button", { name: /fund launches/ }));
    expect(screen.getByRole("button", { name: "+ add to ETFs" })).toBeDisabled();
  });
});
