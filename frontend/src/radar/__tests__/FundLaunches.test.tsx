import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FundLaunches } from "../FundLaunches";

// The fund tape with the API layer stubbed: the batch collapse, the two toggles (and the args they send), the
// quiet rendering of tier and name hits. Made-up funds, cards and trusts throughout.

const h = vi.hoisted(() => ({
  calls: [] as { includePrivate?: boolean; matchedOnly?: boolean }[],
  data: null as unknown,
}));

vi.mock("../../api/hooks", () => ({
  useRadarFunds: (opts: { includePrivate?: boolean; matchedOnly?: boolean }) => {
    h.calls.push(opts);
    return { isLoading: false, error: null, data: h.data };
  },
}));

const STAGE_FILED = { filed: "2026-11-03", effective: null, ticker: null, listed: null, current: "filed" };

const SINGLE = {
  accession: "A-1",
  form: "485APOS",
  trust: "Test Trust",
  public_date: "2026-11-03",
  url: "https://example.test/A-1",
  private_to_public: false,
  funds: [
    {
      key: "S1",
      series_id: "S1",
      name: "Test Uranium ETF",
      stage: { ...STAGE_FILED, effective: "2026-11-04", current: "effective" },
      symbols: [],
      in_etfs: false,
      matches: [
        {
          thesis_id: "t-1",
          thesis_name: "Test Card A",
          tier: "broad",
          signal_terms: [],
          broad_terms: ["nuclear"],
          name_hits: [],
        },
      ],
    },
  ],
};

const BATCH = {
  accession: "A-2",
  form: "485APOS",
  trust: "Other Trust",
  public_date: "2026-11-02",
  url: "https://example.test/A-2",
  private_to_public: false,
  funds: [
    {
      key: "S2",
      series_id: "S2",
      name: "Test 2X Long ZRBX Daily ETF",
      stage: STAGE_FILED,
      symbols: [],
      in_etfs: false,
      matches: [
        {
          thesis_id: "t-1",
          thesis_name: "Test Card A",
          tier: null,
          signal_terms: [],
          broad_terms: [],
          name_hits: [{ ticker: "ZRBX", link: "Core", token: "ZRBX", via: "ticker" }],
        },
      ],
    },
    {
      key: "S3",
      series_id: "S3",
      name: "Test 2X Long QPLX Daily ETF",
      stage: STAGE_FILED,
      symbols: [],
      in_etfs: false,
      matches: [],
    },
  ],
};

const COUNTS = {
  filings: 2,
  funds: 5,
  matched_funds: 2,
  unmatched_funds: 3,
  private_held_back_funds: 4,
  private_held_back_filings: 1,
};

beforeEach(() => {
  h.calls = [];
  h.data = {
    filings: [SINGLE, BATCH],
    counts: COUNTS,
    window_days: 90,
    include_private: false,
    matched_only: true,
  };
});

describe("FundLaunches", () => {
  it("renders one row per filing: a single fund inline, with its stage chips, tier tag and filing link", () => {
    render(<FundLaunches />);
    expect(screen.getByText("Test Uranium ETF")).toBeInTheDocument();
    expect(screen.getByText("Test Trust")).toBeInTheDocument();
    expect(screen.getByText("effective")).toBeInTheDocument();
    expect(screen.getByText("filed")).toBeInTheDocument();
    expect(screen.getByText("nuclear")).toBeInTheDocument();
    expect(screen.getByText("broad")).toBeInTheDocument(); // the quiet tier tag
    const links = screen.getAllByText("filing ↗");
    expect(links[0]).toHaveAttribute("href", "https://example.test/A-1");
    expect(screen.getByText(/2 filings · 5 funds/)).toBeInTheDocument();
  });

  it("a batch filing is ONE collapsed row, opened on click, showing each fund's name hit", async () => {
    const user = userEvent.setup();
    render(<FundLaunches />);
    expect(screen.getByRole("button", { name: /2 funds/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Test 2X Long ZRBX Daily ETF")).not.toBeInTheDocument();
    expect(screen.getByText(/fits Test Card A/)).toBeInTheDocument(); // the collapsed summary

    await user.click(screen.getByRole("button", { name: /2 funds/ }));
    expect(screen.getByText("Test 2X Long ZRBX Daily ETF")).toBeInTheDocument();
    expect(screen.getByText("Test 2X Long QPLX Daily ETF")).toBeInTheDocument(); // unmatched fund of the batch
    expect(screen.getByText("ZRBX · Core")).toBeInTheDocument(); // the name hit: ticker and its link
    expect(screen.getByRole("button", { name: /2 funds/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("a collapsed batch lists at most 5 cards (theme tier first, then by fund hits) and counts the rest", async () => {
    const user = userEvent.setup();
    const mk = (i: number, tier: string | null, hits: number) =>
      Array.from({ length: hits }, (_, k) => ({
        key: `C${i}-${k}`,
        series_id: `C${i}-${k}`,
        name: `Test Wrapper ${i}-${k} ETF`,
        stage: STAGE_FILED,
        symbols: [],
        in_etfs: false,
        matches: [
          {
            thesis_id: `t-${i}`,
            thesis_name: `Card ${i}`,
            tier,
            signal_terms: tier === "signal" ? ["x"] : [],
            broad_terms: tier === "broad" ? ["y"] : [],
            name_hits: tier ? [] : [{ ticker: "ZZ", link: null, token: "ZZ", via: "ticker" }],
          },
        ],
      }));
    h.data = {
      filings: [
        {
          ...BATCH,
          // name-only cards 1 (1 fund) and 2 (3 funds); broad cards 3 and 4; signal card 5; two more name-only
          funds: [
            ...mk(1, null, 1),
            ...mk(2, null, 3),
            ...mk(3, "broad", 1),
            ...mk(4, "broad", 2),
            ...mk(5, "signal", 1),
            ...mk(6, null, 1),
            ...mk(7, null, 1),
          ],
        },
      ],
      counts: COUNTS,
      window_days: 90,
      include_private: false,
      matched_only: true,
    };
    render(<FundLaunches />);
    const fits = screen.getByText(/^fits /);
    // signal (5), then broad by hits (4, 3), then name-only by hits (2, then 1 by name); 6 and 7 are the rest
    expect(fits).toHaveTextContent("fits Card 5 · Card 4 · Card 3 · Card 2 · Card 1");
    expect(fits).toHaveTextContent("+2 more cards");
    await user.click(screen.getByRole("button", { name: /funds/ }));
    expect(screen.getByText("Test Wrapper 7-0 ETF")).toBeInTheDocument(); // opening shows every fund's own lines
  });

  it("defaults to matched only; the show-all toggle (with its unmatched count) flips the query", async () => {
    const user = userEvent.setup();
    render(<FundLaunches />);
    expect(h.calls.at(-1)).toMatchObject({ matchedOnly: true, includePrivate: false });
    await user.click(screen.getByLabelText(/show all \(3 unmatched\)/));
    expect(h.calls.at(-1)).toMatchObject({ matchedOnly: false, includePrivate: false });
  });

  it("the old-filings toggle carries its held-back count and flips includePrivate; hidden at zero", async () => {
    const user = userEvent.setup();
    render(<FundLaunches />);
    await user.click(screen.getByLabelText(/include old filings SEC released \(4\)/));
    expect(h.calls.at(-1)).toMatchObject({ includePrivate: true });
  });

  it("renders no old-filings toggle when none are held back (honest loudness)", () => {
    h.data = {
      filings: [SINGLE],
      counts: { ...COUNTS, private_held_back_funds: 0, private_held_back_filings: 0 },
      window_days: 90,
      include_private: false,
      matched_only: true,
    };
    render(<FundLaunches />);
    expect(screen.queryByLabelText(/include old filings/)).not.toBeInTheDocument();
  });

  it("says so honestly when nothing fits", () => {
    h.data = {
      filings: [],
      counts: { ...COUNTS, filings: 0, funds: 0, matched_funds: 0, unmatched_funds: 0 },
      window_days: 90,
      include_private: false,
      matched_only: true,
    };
    render(<FundLaunches />);
    expect(screen.getByText(/No filing in the window has a fund that fits/)).toBeInTheDocument();
  });
});
