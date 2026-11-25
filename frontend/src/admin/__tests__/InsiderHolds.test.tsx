import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InsiderHolds } from "../InsiderHolds";

// The Held insider rows panel (bad dates step 3) over mocked hooks. The rows are the shapes GET
// /admin/insider-holds returns (backend/app/routers/admin_insider_holds.py) for the held filings the signed-off
// mockup shows. The gates: nothing fires on mount, LOUD only where a decision is owed, every bucket the API
// returns renders (an empty one does not), every decision has its Undo, and the server's refusal shows on ITS row.

const base = {
  security_id: "00000000-0000-4000-8000-0000000000aa",
  company_name: null as string | null,
  txn_seq: 0,
  form: "4",
  reason: "after_filing",
  reason_words: "the date is after SEC accepted the filing",
  period_of_report_as_filed: null as string | null,
  signature_date_as_filed: null as string | null,
  filed: null as string | null,
  accepted: null as string | null,
  why: null as string | null,
  filing_night: null as string | null,
  suggested_date: null as string | null,
  agrees: false,
  // W3-1's suggestion fields: where the date is written, the API's words, the filing's own window, the copies
  suggestion_basis: null as string | null,
  suggestion_words: null as string | null,
  window_from: null as string | null,
  window_to: null as string | null,
  copies: [] as { hold_id: string; ticker?: string | null }[],
  market_date: null as string | null,
  owed_why: null as string | null,
  decision: null as unknown,
  history: [] as unknown[],
  filing_url: null as string | null,
};

const KIDS = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000001",
  ticker: "KIDS",
  company_name: "OrthoPediatrics Corp.",
  accession: "0000899243-21-012557",
  bucket: "owed",
  row: { insider_name: "Pritzker, Jennifer N.", insider_role: "10% owner", txn_code: "P", shares: 800, price: 48.31, usd: 38648 },
  date_as_filed: "2021-11-16",
  period_of_report_as_filed: "2020-11-16",
  signature_date_as_filed: "2021-03-18",
  filed: "2021-03-18",
  accepted: "2021-03-18T20:05:00+00:00",
  // the words and window are what `suggest` returns for this filing (the backend's late_row_0 shape)
  suggested_date: "2020-11-16",
  agrees: true,
  suggestion_basis: "period_of_report",
  suggestion_words: "2020-11-16: the filing's period of report",
  window_from: "2020-11-16",
  window_to: "2021-03-18",
  market_date: "2021-03-18",
  owed_why: "the filing was made 122 days after its period of report (2020-11-16), more than the on-time window of 5 days",
  filing_url: "https://www.sec.gov/Archives/edgar/data/1425450/000089924321012557/",
};

const BBAI = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000002",
  ticker: "BBAI",
  accession: "0001628280-24-010030",
  bucket: "owed",
  reason: "year_before_1934",
  reason_words: "the year is before 1934, when insider reporting began",
  row: { insider_name: "Peffer, Julie", insider_role: "director", txn_code: "A", shares: 87827, price: 0, usd: 0 },
  date_as_filed: "0023-03-23",
  period_of_report_as_filed: "0023-03-23",
  signature_date_as_filed: "2024-03-08",
  // W3-1 reads the 00YY year as 2023 and the date is written in the footnote: it agrees now (the backend's
  // leading_zero_year_footnote shape)
  suggested_date: "2023-03-23",
  agrees: true,
  suggestion_basis: "footnote",
  suggestion_words:
    "2023-03-23: written in a footnote this row cites and the filing's period of report (written 0023-03-23)",
  window_from: "2023-03-23",
  window_to: "2023-11-13",
  market_date: "2024-03-08",
  owed_why: "the period of report as filed (0023-03-23) fails the year rule (before 1934, or after the filing's year)",
};

// The other shapes `suggest` returns (strings copied from backend/domain/insider_holds.py, under made-up tickers):
// withheld with the filing's window, withheld by a balance chain, a period-of-report guess that does not agree, two
// written dates, and a row whose copy is held under another ticker.
const WITHHELD = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000011",
  ticker: "WTHP",
  accession: "0000000000-15-000011",
  bucket: "owed",
  row: { insider_name: "Doe, Jane", insider_role: "director", txn_code: "S", shares: 100, price: 10, usd: 1000 },
  date_as_filed: "2015-09-14",
  suggested_date: null,
  agrees: false,
  suggestion_words:
    "2014-09-14 is before the period of report (2015-08-27); the filing puts it between 2015-08-27 and 2015-09-11",
  window_from: "2015-08-27",
  window_to: "2015-09-11",
  market_date: "2015-09-11",
  owed_why: "the date is after SEC accepted the filing",
};

const WITHHELD_CHAIN = {
  ...WITHHELD,
  hold_id: "11111111-0000-4000-8000-000000000012",
  ticker: "WTHB",
  suggestion_words:
    "2008-07-17 is before the 2009-07-09 row this one follows in the filing's balances (110,000 -> 250,000 shares); the filing puts it between 2009-07-09 and 2009-07-13",
  window_from: "2009-07-09",
  window_to: "2009-07-13",
};

const ONE_ROW = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000013",
  ticker: "ONER",
  accession: "0000000000-22-000013",
  bucket: "owed",
  row: { insider_name: "Roe, Richard", insider_role: "officer", txn_code: "P", shares: 50, price: 20, usd: 1000 },
  date_as_filed: "2022-07-30",
  suggested_date: "2022-06-30",
  agrees: false,
  suggestion_basis: "period_of_report_one_row",
  suggestion_words:
    "2022-06-30: the period of report of a one-row filing; the row's written date (2022-07-30) fits neither it nor the filing, so it does not agree on its own",
  window_from: "2022-06-30",
  window_to: "2022-07-15",
  market_date: "2022-07-15",
};

const TWO_DATES = {
  ...ONE_ROW,
  hold_id: "11111111-0000-4000-8000-000000000014",
  ticker: "TWOD",
  suggestion_basis: "footnote",
  suggestion_words:
    "the filing writes 2020-03-05 and 2021-03-05 with this row's month and day; the latest is shown, and it does not agree on its own",
  suggested_date: "2021-03-05",
};

const COPY_A = {
  ...KIDS,
  hold_id: "11111111-0000-4000-8000-000000000015",
  ticker: "GSCA",
  accession: "0000000000-25-000015",
  suggested_date: "2025-07-25",
  suggestion_basis: "sibling_row",
  suggestion_words:
    "2025-07-25: the date of rows 0, 2 and 3 of the same filing and the filing's period of report (written 0025-07-25)",
  window_from: "2025-07-25",
  window_to: "2025-07-25",
  copies: [{ hold_id: "11111111-0000-4000-8000-000000000016", ticker: "GSCB" }],
};

const ASNS = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000003",
  ticker: "ASNS",
  accession: "0001213900-23-096094",
  bucket: "owed",
  row: { insider_name: "Barlev, Tuvia", insider_role: "officer", txn_code: "S", shares: 0, price: 0, usd: 0 },
  date_as_filed: "2024-04-01",
  suggested_date: null,
  suggestion_words: "no acceptance and no filing date are known, so no date can be checked",
  owed_why: "no acceptance time is known for the filing",
};

const TMUS = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000004",
  ticker: "TMUS",
  accession: "0001140361-24-027755",
  bucket: "placed_by_rule",
  reason: "year_after_filing_year",
  reason_words: "the year is after the year the filing was made",
  row: { insider_name: "Deutsche Telekom AG", insider_role: "10% owner", txn_code: "S", shares: 140035, price: 165.53, usd: null },
  date_as_filed: "2028-05-24",
  period_of_report_as_filed: "2024-05-24",
  filing_night: "2024-05-28",
  suggested_date: "2024-05-24",
  agrees: true,
};

const CRDO = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000005",
  ticker: "CRDO",
  accession: "0001628280-23-039999",
  bucket: "filer_corrected",
  reason: "year_after_filing_year",
  reason_words: "the year is after the year the filing was made",
  row: { insider_name: "Brennan, William Joseph", insider_role: "officer", txn_code: "S", shares: 15000, price: 17.71, usd: null },
  date_as_filed: "2027-11-17",
  why: "the filer's own amendment 0001628280-23-040161 restates the same trade",
};

const STEWARD = { user_id: "22222222-0000-4000-8000-000000000001", display_name: "Steward One" };
const OII_APPROVE = {
  id: "33333333-0000-4000-8000-000000000001",
  action: "approve",
  ratified_date: "2017-09-13",
  reason: "same day a year earlier, matches the plan purchases",
  recorded_at: "2026-11-12T15:00:00+00:00",
  basis: "steward:approve",
  revokes: null,
  decided_by: STEWARD,
};
const OII_DISMISS = {
  id: "33333333-0000-4000-8000-000000000000",
  action: "dismiss",
  ratified_date: null,
  reason: "first look",
  recorded_at: "2026-11-11T15:00:00+00:00",
  basis: "steward:dismiss",
  revokes: null,
  decided_by: STEWARD,
};
const OII_REVOKE = {
  id: "33333333-0000-4000-8000-00000000000f",
  action: "revoke",
  ratified_date: null,
  reason: "revoked",
  recorded_at: "2026-11-11T16:00:00+00:00",
  basis: "steward:revoke",
  revokes: OII_DISMISS.id,
  decided_by: STEWARD,
};

const OII = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000006",
  ticker: "OII",
  accession: "0000073756-18-000050",
  bucket: "decided",
  row: { insider_name: "Silva, Eric A", insider_role: "director", txn_code: "P", shares: 3, price: 25.95, usd: null },
  date_as_filed: "2018-09-13",
  decision: OII_APPROVE,
  history: [OII_DISMISS, OII_REVOKE, OII_APPROVE],
};

const ASNS_DISMISS = {
  id: "33333333-0000-4000-8000-000000000002",
  action: "dismiss",
  ratified_date: null,
  reason: "a notice of a trading plan, zero shares",
  recorded_at: "2026-11-12T16:00:00+00:00",
  basis: "steward:dismiss",
  revokes: null,
  decided_by: STEWARD,
};
const ASNS_DISMISSED = {
  ...ASNS,
  hold_id: "11111111-0000-4000-8000-000000000007",
  bucket: "dismissed",
  decision: ASNS_DISMISS,
  history: [ASNS_DISMISS],
};

const AMEND = {
  ...base,
  hold_id: "11111111-0000-4000-8000-000000000008",
  ticker: "PW",
  accession: "0001354488-15-000010",
  form: "4/A",
  bucket: "amendment_row",
  row: { insider_name: "Lesser, David H", insider_role: "officer", txn_code: "P", shares: 500, price: 8.37, usd: null },
  date_as_filed: "2015-12-31",
};

const h = vi.hoisted(() => ({
  q: {} as Record<string, unknown>,
  approve: {} as Record<string, unknown>,
  dismiss: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useInsiderHolds: () => h.q,
  useApproveHold: () => h.approve,
  useDismissHold: () => h.dismiss,
  useRevokeHoldDecision: () => h.revoke,
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null, variables: undefined });
const mutate = (x: Record<string, unknown>) => x.mutate as ReturnType<typeof vi.fn>;

const EMPTY = {
  owed: [],
  placed_by_rule: [],
  filer_corrected: [],
  decided: [],
  dismissed: [],
  amendment_row: [],
  counts: { owed: 0, placed_by_rule: 0, filer_corrected: 0, decided: 0, dismissed: 0, amendment_row: 0 },
  // the backend's BUCKET_WORDS (domain/insider_holds.py), as GET /admin/insider-holds returns them
  bucket_words: {
    owed: "owed your decision",
    placed_by_rule: "placed by rule (counted from the filing night)",
    filer_corrected: "corrected by the filer",
    decided: "decided",
    dismissed: "dismissed",
    amendment_row: "held in a Form 4/A (cannot be applied yet)",
  },
  total: 0,
};

function serve(data: Record<string, unknown>) {
  h.q = { data: { ...EMPTY, ...data }, isError: false, error: null };
}

const FULL = {
  owed: [KIDS, BBAI, ASNS],
  placed_by_rule: [TMUS],
  filer_corrected: [CRDO],
  decided: [OII],
  dismissed: [ASNS_DISMISSED],
  amendment_row: [AMEND],
  counts: { owed: 3, placed_by_rule: 24, filer_corrected: 9, decided: 1, dismissed: 1, amendment_row: 1 },
  total: 39,
};

const rowOf = (ticker: string, within_?: HTMLElement) =>
  (within_ ? within(within_).getAllByTestId("adm-hold-row") : screen.getAllByTestId("adm-hold-row")).find(
    (li) => li.textContent?.includes(ticker),
  )!;

beforeEach(() => {
  h.approve = m();
  h.dismiss = m();
  h.revoke = m();
  serve(FULL);
});

describe("InsiderHolds", () => {
  it("renders nothing when no row is held", () => {
    serve(EMPTY);
    const { container } = render(<InsiderHolds />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one section per non-empty bucket, owed loud and open, the rest collapsed, and fires nothing on mount", () => {
    render(<InsiderHolds />);
    const owed = screen.getByTestId("adm-holds-owed");
    expect(owed).toHaveTextContent("3 insider row(s) held and owed your decision");
    expect(within(owed).getAllByTestId("adm-hold-row")).toHaveLength(3);
    for (const id of ["adm-holds-placed", "adm-holds-filer", "adm-holds-decided", "adm-holds-dismissed", "adm-holds-amendment"]) {
      expect(screen.getByTestId(id)).not.toHaveAttribute("open");
    }
    // the section counts are the API's `counts`, not the list length
    expect(screen.getByTestId("adm-holds-placed")).toHaveTextContent("Counted from the filing night by rule · 24");
    expect(screen.getByTestId("adm-holds-filer")).toHaveTextContent("· 9");
    // the dismissed list stays visible, grayed
    expect(screen.getByTestId("adm-holds-dismissed")).toHaveClass("adm-grayed");
    // the Form 4/A rows: no action, the cannot-be-applied words
    const amend = screen.getByTestId("adm-holds-amendment");
    expect(amend).toHaveTextContent("Held in a Form 4/A (cannot be applied yet).");
    expect(within(amend).queryAllByRole("button")).toHaveLength(0);
    // the filer-corrected rows: no action, the API's why
    const filer = screen.getByTestId("adm-holds-filer");
    expect(filer).toHaveTextContent(CRDO.why);
    expect(within(filer).queryAllByRole("button")).toHaveLength(0);
    for (const x of [h.approve, h.dismiss, h.revoke]) expect(mutate(x)).not.toHaveBeenCalled();
  });

  it("does not render a bucket the API returns empty", () => {
    serve({ owed: [KIDS], counts: { ...EMPTY.counts, owed: 1 }, total: 1 });
    render(<InsiderHolds />);
    expect(screen.getByTestId("adm-holds-owed")).toBeInTheDocument();
    for (const id of ["adm-holds-placed", "adm-holds-filer", "adm-holds-decided", "adm-holds-dismissed", "adm-holds-amendment"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
  });

  it("shows the owed count in the header, loud, and none when nothing is owed", () => {
    const { unmount } = render(<InsiderHolds />);
    const n = screen.getByTestId("adm-holds-owed-count");
    expect(n).toHaveTextContent("3 owed");
    expect(n).toHaveClass("adm-loud");
    unmount();

    serve({ ...FULL, owed: [], counts: { ...FULL.counts, owed: 0 }, total: 36 });
    render(<InsiderHolds />);
    expect(screen.queryByTestId("adm-holds-owed-count")).toBeNull();
    expect(screen.queryByTestId("adm-holds-owed")).toBeNull();
    expect(screen.getByTestId("adm-holds-none-owed")).toHaveTextContent(
      "No held row needs a decision. 24 placed by rule (counted from the filing night) · 9 corrected by the filer · 1 decided · 1 dismissed · 1 held in a Form 4/A (cannot be applied yet).",
    );
  });

  it("shows each row's evidence: the trade in words, the filing's own dates, the reason and the suggestion", () => {
    render(<InsiderHolds />);
    const kids = rowOf("KIDS");
    expect(kids).toHaveTextContent("OrthoPediatrics Corp.");
    expect(kids).toHaveTextContent("Pritzker, Jennifer N.");
    expect(within(kids).getByTestId("adm-hold-trade")).toHaveTextContent("buy 800 @ 48.31");
    expect(kids).toHaveTextContent("The filing says the trade date is 2021-11-16. It cannot be true: the date is after SEC accepted the filing.");
    const ref = within(kids).getByTestId("adm-hold-ref");
    expect(ref).toHaveTextContent("accepted by SEC 2021-03-18 16:05 ET");
    expect(ref).toHaveTextContent("period of report as filed 2020-11-16");
    expect(ref).toHaveTextContent("signed as filed 2021-03-18");
    expect(within(kids).getByRole("link")).toHaveAttribute("href", KIDS.filing_url);
    expect(within(kids).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "Suggested date 2020-11-16 · the filing's period of report",
    );
    // the API's words are the one explanation: no second label beside them
    expect(within(kids).getByTestId("adm-hold-sugg")).not.toHaveTextContent("from the period of report");
    // an agreeing suggestion is offered as the primary button
    expect(within(kids).getByRole("button", { name: "Use 2020-11-16" })).toHaveClass("primary");
    // why the rule did not place it, in plain view (the API's words)
    expect(within(kids).getByTestId("adm-hold-owed-why")).toHaveTextContent(
      "Why the rule did not place it: the filing was made 122 days after its period of report (2020-11-16), more than the on-time window of 5 days.",
    );
    // an agreeing suggestion on a 00YY row (W3-1 reads it as 2023) is offered too, and says where it is written
    const bbai = rowOf("BBAI");
    expect(within(bbai).getByTestId("adm-hold-trade")).toHaveTextContent("award 87,827 @ 0");
    expect(within(bbai).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "Suggested date 2023-03-23 · written in a footnote this row cites and the filing's period of report (written 0023-03-23)",
    );
    expect(within(bbai).getByTestId("adm-hold-sugg")).not.toHaveTextContent("from a footnote");
    expect(within(bbai).getByRole("button", { name: "Use 2023-03-23" })).toBeInTheDocument();
    expect(within(bbai).getByTestId("adm-hold-owed-why")).toHaveTextContent("fails the year rule");
    // no suggestion: no Use button, and the API's words say why none is offered (no window is known here)
    const asns = rowOf("ASNS", screen.getByTestId("adm-holds-owed"));
    expect(within(asns).queryByRole("button", { name: /^Use / })).toBeNull();
    expect(within(asns).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "No date is suggested: no acceptance and no filing date are known, so no date can be checked",
    );
    expect(within(asns).queryByTestId("adm-hold-window")).toBeNull();
  });

  it("Use <date> approves the suggestion with no date sent", async () => {
    render(<InsiderHolds />);
    await userEvent.click(within(rowOf("KIDS")).getByRole("button", { name: "Use 2020-11-16" }));
    expect(mutate(h.approve)).toHaveBeenCalledWith({ holdId: KIDS.hold_id });
  });

  it("Set another date sends the date and reason, and shows the server's 422 on that row", async () => {
    const { rerender } = render(<InsiderHolds />);
    const asns = rowOf("ASNS", screen.getByTestId("adm-holds-owed"));
    await userEvent.click(within(asns).getByRole("button", { name: "Set another date" }));
    const save = within(asns).getByRole("button", { name: "Save date" });
    expect(save).toBeDisabled(); // no suggestion to prefill: nothing to send yet
    fireEvent.change(within(asns).getByLabelText("Trade date"), { target: { value: "2023-12-15" } });
    await userEvent.type(within(asns).getByLabelText("Why this date"), "the signature date");
    await userEvent.click(save);
    expect(mutate(h.approve)).toHaveBeenCalledWith({
      holdId: ASNS.hold_id,
      ratifiedDate: "2023-12-15",
      reason: "the signature date",
    });

    const detail = "2024-01-02 is after the filing's market date 2023-12-15";
    h.approve = { ...m(), isError: true, error: { detail }, variables: { holdId: ASNS.hold_id, ratifiedDate: "2024-01-02" } };
    rerender(<InsiderHolds />);
    const owed = screen.getByTestId("adm-holds-owed");
    expect(within(rowOf("ASNS", owed)).getByTestId("adm-hold-err")).toHaveTextContent(detail);
    expect(within(rowOf("KIDS")).queryByTestId("adm-hold-err")).toBeNull();
    // Cancel closes the edit without sending anything more
    await userEvent.click(within(rowOf("ASNS", owed)).getByRole("button", { name: "Cancel" }));
    expect(within(rowOf("ASNS", owed)).getByRole("button", { name: "Set another date" })).toBeInTheDocument();
    expect(mutate(h.approve)).not.toHaveBeenCalled();
  });

  it("Set another date prefills an agreeing suggestion, stops at the market date and says so", async () => {
    render(<InsiderHolds />);
    const kids = rowOf("KIDS");
    await userEvent.click(within(kids).getByRole("button", { name: "Set another date" }));
    const input = within(kids).getByLabelText("Trade date");
    expect(input).toHaveValue("2020-11-16");
    expect(input).toHaveAttribute("max", "2021-03-18");
    expect(within(kids).getByTestId("adm-hold-date-hint")).toHaveTextContent(
      "A date must be on or before 2021-03-18, the day the filing was public.",
    );
  });

  it("Set another date never prefills a suggestion that does not agree", async () => {
    serve({ owed: [ONE_ROW], counts: { ...EMPTY.counts, owed: 1 }, total: 1 });
    render(<InsiderHolds />);
    const row = rowOf("ONER");
    await userEvent.click(within(row).getByRole("button", { name: "Set another date" }));
    expect(within(row).getByLabelText("Trade date")).toHaveValue("");
    expect(within(row).getByLabelText("Trade date")).toHaveAttribute("max", "2022-07-15");
    expect(within(row).getByRole("button", { name: "Save date" })).toBeDisabled();
  });

  it("Set another date prefills an agreeing suggestion that comes from a 00YY period of report", async () => {
    render(<InsiderHolds />);
    const bbai = rowOf("BBAI");
    await userEvent.click(within(bbai).getByRole("button", { name: "Set another date" }));
    expect(within(bbai).getByLabelText("Trade date")).toHaveValue("2023-03-23");
    expect(within(bbai).getByLabelText("Trade date")).toHaveAttribute("max", "2024-03-08");
  });

  it("with no market date there is no max and the hint names no date", async () => {
    render(<InsiderHolds />);
    const asns = rowOf("ASNS", screen.getByTestId("adm-holds-owed"));
    await userEvent.click(within(asns).getByRole("button", { name: "Set another date" }));
    expect(within(asns).getByLabelText("Trade date")).not.toHaveAttribute("max");
    expect(within(asns).getByTestId("adm-hold-date-hint")).toHaveTextContent(
      "A date must be on or before the day the filing was public.",
    );
  });

  it("a suggestion that does not agree on a rule-placed row is the API's words only, with no Use button", () => {
    const words =
      "2024-05-24: the written month and day (2028-05-24) in the latest year on or before the filing's market date (2024-05-28); the period of report cannot be read, so it does not agree on its own";
    serve({
      ...FULL,
      placed_by_rule: [
        {
          ...TMUS,
          period_of_report_as_filed: null,
          agrees: false,
          suggestion_basis: "arithmetic",
          suggestion_words: words,
        },
      ],
    });
    render(<InsiderHolds />);
    const tmus = rowOf("TMUS");
    expect(within(tmus).queryByRole("button", { name: /^Use / })).toBeNull();
    expect(within(tmus).getByTestId("adm-hold-sugg")).toHaveTextContent(
      `Suggested date 2024-05-24 · ${words.slice("2024-05-24: ".length)}`,
    );
    expect(within(tmus).getByTestId("adm-hold-sugg")).not.toHaveTextContent("from the written month and day");
    expect(tmus).not.toHaveTextContent("weak");
    expect(within(tmus).getByRole("button", { name: "Set the trade date" })).toBeInTheDocument();
  });

  it.each([
    ["footnote", "from a footnote", "written in a footnote this row cites", "2017-11-21"],
    [
      "sibling_row",
      "from another row of the filing",
      "the date of rows 0, 2 and 3 of the same filing and the filing's period of report (written 0025-07-25)",
      "2025-07-25",
    ],
    ["period_of_report", "from the period of report", "the filing's period of report", "2020-11-16"],
    [
      "arithmetic",
      "from the written month and day",
      "the written month and day (2021-12-03) in the latest year on or before the filing's market date (2021-03-18)",
      "2020-12-03",
    ],
  ])("an agreeing suggestion from the %s says where the filing writes it once, in the API's words, and keeps its Use button", (basis, chip, words, day) => {
    serve({
      owed: [{ ...KIDS, suggested_date: day, suggestion_basis: basis, suggestion_words: `${day}: ${words}` }],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    const row = rowOf("KIDS");
    const sugg = within(row).getByTestId("adm-hold-sugg");
    expect(sugg).toHaveTextContent(`Suggested date ${day} · ${words}`);
    // the short label is only for a server that sends no words: here it would say the same thing twice
    expect(sugg).not.toHaveTextContent(chip);
    expect(within(sugg).queryByTestId("adm-hold-sugg-basis")).toBeNull();
    expect(sugg).toHaveAttribute("data-basis", basis);
    expect(within(sugg).getByText(day)).toHaveClass("adm-hold-ok");
    expect(within(row).getByRole("button", { name: `Use ${day}` })).toHaveClass("primary");
  });

  it("a suggestion that does not agree shows the API's words in place of the old 'weak' wording, never a Use button", () => {
    serve({ owed: [ONE_ROW, TWO_DATES], counts: { ...EMPTY.counts, owed: 2 }, total: 2 });
    render(<InsiderHolds />);
    const one = rowOf("ONER");
    expect(within(one).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "Suggested date 2022-06-30 · the period of report of a one-row filing; the row's written date (2022-07-30) fits neither it nor the filing, so it does not agree on its own",
    );
    expect(within(one).getByTestId("adm-hold-sugg")).not.toHaveTextContent("from a one-row filing's period of report");
    const two = rowOf("TWOD");
    expect(within(two).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "the filing writes 2020-03-05 and 2021-03-05 with this row's month and day; the latest is shown, and it does not agree on its own",
    );
    for (const row of [one, two]) {
      const sugg = within(row).getByTestId("adm-hold-sugg");
      expect(row).not.toHaveTextContent(/weak/i);
      expect(within(sugg).getByText(/^20\d\d-\d\d-\d\d$/, { selector: "b" })).not.toHaveClass("adm-hold-ok");
      expect(within(row).queryByRole("button", { name: /^Use / })).toBeNull();
      // the window is for a withheld suggestion; a shown date has its words
      expect(within(row).queryByTestId("adm-hold-window")).toBeNull();
    }
  });

  it("a withheld suggestion says why and shows the filing's own date window", () => {
    serve({ owed: [WITHHELD, WITHHELD_CHAIN], counts: { ...EMPTY.counts, owed: 2 }, total: 2 });
    render(<InsiderHolds />);
    const p = rowOf("WTHP");
    expect(within(p).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "No date is suggested: 2014-09-14 is before the period of report (2015-08-27); the filing puts it between 2015-08-27 and 2015-09-11",
    );
    const b = rowOf("WTHB");
    expect(within(b).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "2008-07-17 is before the 2009-07-09 row this one follows in the filing's balances (110,000 -> 250,000 shares); the filing puts it between 2009-07-09 and 2009-07-13",
    );
    for (const row of [p, b]) {
      // the API's words already give both ends of the window: the structured line would say it twice
      expect(within(row).queryByTestId("adm-hold-window")).toBeNull();
      expect(row).not.toHaveTextContent("The filing's own dates");
      expect(row).not.toHaveTextContent(/weak/i);
      expect(within(row).queryByRole("button", { name: /^Use / })).toBeNull();
      expect(within(row).getByRole("button", { name: "Set another date" })).toBeInTheDocument();
    }
  });

  it.each([
    ["both ends", "2015-08-27", "2015-09-11", "The filing's own dates put it between 2015-08-27 and 2015-09-11."],
    ["one bound only: the latest", null, "2015-09-11", "The filing's own dates put it on or before 2015-09-11."],
    ["one bound only: the earliest", "2015-08-27", null, "The filing's own dates put it on or after 2015-08-27."],
    ["a one-day window", "2025-07-25", "2025-07-25", "The filing's own dates put it on 2025-07-25."],
    [
      "bounds that cross",
      "2015-09-20",
      "2015-09-11",
      "The filing's own dates leave no window (2015-09-20 is after 2015-09-11).",
    ],
  ])("the window line is the fallback for words that name no window date: %s", (_label, from, to, line) => {
    // words that name neither end of the window (and an older server's missing words, below, the same)
    const words = "the date the filing writes for this row cannot be checked against its other rows";
    serve({
      owed: [{ ...WITHHELD, suggestion_words: words, window_from: from, window_to: to }],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    const row = rowOf("WTHP");
    expect(within(row).getByTestId("adm-hold-sugg")).toHaveTextContent(`No date is suggested: ${words}`);
    expect(within(row).getByTestId("adm-hold-window")).toHaveTextContent(line);
  });

  it("the window line shows when the API's words give only one of its two ends", () => {
    serve({
      owed: [{ ...WITHHELD, suggestion_words: "2014-09-14 is before the period of report (2015-08-27)" }],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    expect(within(rowOf("WTHP")).getByTestId("adm-hold-window")).toHaveTextContent(
      "The filing's own dates put it between 2015-08-27 and 2015-09-11.",
    );
  });

  it("the window line is not repeated when the words name the only end there is", () => {
    // the backend's wording for a window with no earliest end: "... the filing puts it on or before <date>"
    serve({
      owed: [
        {
          ...WITHHELD,
          suggestion_words: "2014-09-14 is before the period of report; the filing puts it on or before 2015-09-11",
          window_from: null,
        },
      ],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    expect(within(rowOf("WTHP")).queryByTestId("adm-hold-window")).toBeNull();
  });

  it("the window line shows with no words at all (an older server), after the plain line", () => {
    serve({
      owed: [{ ...WITHHELD, suggestion_words: null }],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    const row = rowOf("WTHP");
    expect(within(row).getByTestId("adm-hold-sugg")).toHaveTextContent("No date can be suggested from the filing.");
    expect(within(row).getByTestId("adm-hold-window")).toHaveTextContent(
      "The filing's own dates put it between 2015-08-27 and 2015-09-11.",
    );
  });

  it("a shown suggestion with a basis but no words (an older server) uses the short label", () => {
    serve({
      owed: [
        { ...KIDS, suggestion_words: null },
        { ...ONE_ROW, suggestion_words: null },
      ],
      counts: { ...EMPTY.counts, owed: 2 },
      total: 2,
    });
    render(<InsiderHolds />);
    const kids = within(rowOf("KIDS")).getByTestId("adm-hold-sugg");
    expect(kids).toHaveTextContent("Suggested date 2020-11-16 · from the period of report");
    expect(within(kids).getByTestId("adm-hold-sugg-basis")).toHaveTextContent("from the period of report");
    expect(within(kids).getByText("2020-11-16")).toHaveClass("adm-hold-ok");
    const one = within(rowOf("ONER")).getByTestId("adm-hold-sugg");
    expect(one).toHaveTextContent("Suggested date 2022-06-30 · from a one-row filing's period of report");
    expect(within(one).getByText("2022-06-30")).not.toHaveClass("adm-hold-ok");
  });

  it("a shown suggestion from an older server (no basis, no words) keeps the line it always had", () => {
    const old = { suggestion_basis: null, suggestion_words: null, window_from: null, window_to: null };
    serve({
      owed: [{ ...KIDS, ...old }, { ...ONE_ROW, ...old }],
      counts: { ...EMPTY.counts, owed: 2 },
      total: 2,
    });
    render(<InsiderHolds />);
    expect(within(rowOf("KIDS")).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "Suggested date 2020-11-16 · on or after the filing's period of report",
    );
    expect(within(rowOf("ONER")).getByTestId("adm-hold-sugg")).toHaveTextContent(
      "Suggested date 2022-06-30 · it does not agree with the filing, so it is not offered",
    );
    expect(within(rowOf("KIDS")).queryByTestId("adm-hold-sugg-basis")).toBeNull();
  });

  it("a withheld suggestion from an older server (no words, no window) keeps the plain line", () => {
    serve({
      owed: [{ ...WITHHELD, suggestion_words: null, window_from: null, window_to: null }],
      counts: { ...EMPTY.counts, owed: 1 },
      total: 1,
    });
    render(<InsiderHolds />);
    const row = rowOf("WTHP");
    expect(within(row).getByTestId("adm-hold-sugg")).toHaveTextContent("No date can be suggested from the filing.");
    expect(within(row).queryByTestId("adm-hold-window")).toBeNull();
  });

  it("says a copy of the same filing row is held under another ticker, to be decided on its own", () => {
    serve({ owed: [COPY_A, KIDS], counts: { ...EMPTY.counts, owed: 2 }, total: 2 });
    render(<InsiderHolds />);
    const a = rowOf("GSCA");
    expect(within(a).getByTestId("adm-hold-copies")).toHaveTextContent(
      "Also held: a copy of the same filing row under GSCB. Each is decided on its own.",
    );
    // a row with no copy says nothing about one
    expect(within(rowOf("KIDS")).queryByTestId("adm-hold-copies")).toBeNull();
  });

  it("names every copy, and a copy with no ticker by what it is", () => {
    const copies = [
      { hold_id: "11111111-0000-4000-8000-000000000021", ticker: "GSCB" },
      { hold_id: "11111111-0000-4000-8000-000000000022", ticker: null },
    ];
    serve({
      owed: [{ ...COPY_A, copies }],
      placed_by_rule: [{ ...TMUS, copies: [copies[0]] }],
      counts: { ...EMPTY.counts, owed: 1, placed_by_rule: 1 },
      total: 2,
    });
    render(<InsiderHolds />);
    expect(within(rowOf("GSCA")).getByTestId("adm-hold-copies")).toHaveTextContent(
      "Also held: a copy of the same filing row under GSCB and a copy of the same filing row under another security. Each is decided on its own.",
    );
    // a rule-placed row can be decided too, so it says the same
    expect(within(rowOf("TMUS")).getByTestId("adm-hold-copies")).toHaveTextContent(
      "Also held: a copy of the same filing row under GSCB. Each is decided on its own.",
    );
  });

  it("Dismiss is blocked without a reason and sends the reason", async () => {
    render(<InsiderHolds />);
    const kids = rowOf("KIDS");
    await userEvent.click(within(kids).getByRole("button", { name: "Dismiss: not a trade" }));
    const send = within(kids).getByRole("button", { name: "Dismiss" });
    expect(send).toBeDisabled();
    expect(kids).toHaveTextContent("A reason is required to dismiss.");
    await userEvent.type(within(kids).getByLabelText("Why it is not a trade"), "   ");
    expect(send).toBeDisabled();
    await userEvent.click(send);
    expect(mutate(h.dismiss)).not.toHaveBeenCalled();
    await userEvent.type(within(kids).getByLabelText("Why it is not a trade"), "a plan notice");
    expect(send).toBeEnabled();
    await userEvent.click(send);
    expect(mutate(h.dismiss)).toHaveBeenCalledWith({ holdId: KIDS.hold_id, reason: "a plan notice" });
  });

  it("a rule-placed row can still be decided", async () => {
    render(<InsiderHolds />);
    const tmus = rowOf("TMUS");
    expect(tmus).toHaveTextContent("counted from 2024-05-28, the night the filing was public");
    expect(within(tmus).queryByTestId("adm-hold-sugg")).toBeNull(); // an agreeing one rides its button
    await userEvent.click(within(tmus).getByRole("button", { name: "Use 2024-05-24" }));
    expect(mutate(h.approve)).toHaveBeenCalledWith({ holdId: TMUS.hold_id });
    expect(within(tmus).getByRole("button", { name: "Set the trade date" })).toBeInTheDocument();
    expect(within(tmus).getByRole("button", { name: "Dismiss: not a trade" })).toBeInTheDocument();
  });

  it("Undo revokes the standing decision on decided and dismissed rows", async () => {
    render(<InsiderHolds />);
    const oii = rowOf("OII");
    expect(oii).toHaveTextContent("Steward One set 2017-09-13 on 2026-11-12");
    expect(oii).toHaveTextContent("same day a year earlier");
    await userEvent.click(within(oii).getByRole("button", { name: "Undo" }));
    expect(mutate(h.revoke)).toHaveBeenCalledWith({ decisionId: OII_APPROVE.id, holdId: OII.hold_id });

    const dismissed = rowOf("ASNS", screen.getByTestId("adm-holds-dismissed"));
    expect(dismissed).toHaveTextContent("dismissed by Steward One on 2026-11-12");
    expect(dismissed).toHaveTextContent("a notice of a trading plan, zero shares");
    await userEvent.click(within(dismissed).getByRole("button", { name: "Undo" }));
    expect(mutate(h.revoke)).toHaveBeenLastCalledWith({ decisionId: ASNS_DISMISS.id, holdId: ASNS_DISMISSED.hold_id });
  });

  it("shows a 409 on its own row only", () => {
    const detail = "the decision was already undone";
    h.revoke = { ...m(), isError: true, error: { detail }, variables: { decisionId: OII_APPROVE.id, holdId: OII.hold_id } };
    render(<InsiderHolds />);
    expect(within(rowOf("OII")).getByTestId("adm-hold-err")).toHaveTextContent(detail);
    expect(screen.getAllByTestId("adm-hold-err")).toHaveLength(1);
  });

  it("expands a row's decision history, oldest first with who and when; no toggle on a row without one", async () => {
    render(<InsiderHolds />);
    const oii = rowOf("OII");
    expect(within(oii).queryByTestId("adm-hold-history")).toBeNull();
    await userEvent.click(within(oii).getByRole("button", { name: "Show decision history (3)" }));
    const items = within(within(oii).getByTestId("adm-hold-history")).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "2026-11-11 10:00 ET · Steward One dismissed it as not a trade · “first look”",
      "2026-11-11 11:00 ET · Steward One undid a decision · “revoked”",
      "2026-11-12 10:00 ET · Steward One set the trade date 2017-09-13 · “same day a year earlier, matches the plan purchases”",
    ]);
    expect(within(rowOf("KIDS")).queryByRole("button", { name: /decision history/ })).toBeNull();
  });

  it("shows a read error in the card", () => {
    h.q = { data: undefined, isError: true, error: { detail: "forbidden" } };
    render(<InsiderHolds />);
    expect(screen.getByTestId("adm-insider-holds")).toHaveTextContent("forbidden");
  });
});
