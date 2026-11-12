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
  suggested_date: "2020-11-16",
  agrees: true,
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
  suggested_date: "2023-03-23",
  agrees: false,
  market_date: "2024-03-08",
  owed_why: "the period of report as filed (0023-03-23) fails the year rule (before 1934, or after the filing's year)",
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
      "Suggested date 2020-11-16 · on or after the filing's period of report",
    );
    // an agreeing suggestion is offered as the primary button
    expect(within(kids).getByRole("button", { name: "Use 2020-11-16" })).toHaveClass("primary");
    // why the rule did not place it, in plain view (the API's words)
    expect(within(kids).getByTestId("adm-hold-owed-why")).toHaveTextContent(
      "Why the rule did not place it: the filing was made 122 days after its period of report (2020-11-16), more than the on-time window of 5 days.",
    );
    // a weak suggestion is text only: never a one-click button
    const bbai = rowOf("BBAI");
    expect(within(bbai).getByTestId("adm-hold-trade")).toHaveTextContent("award 87,827 @ 0");
    expect(within(bbai).getByTestId("adm-hold-sugg")).toHaveTextContent("Suggested date 2023-03-23 · weak");
    expect(within(bbai).queryByRole("button", { name: /^Use / })).toBeNull();
    expect(within(bbai).getByTestId("adm-hold-owed-why")).toHaveTextContent("fails the year rule");
    // no suggestion: no Use button
    const asns = rowOf("ASNS", screen.getByTestId("adm-holds-owed"));
    expect(within(asns).queryByRole("button", { name: /^Use / })).toBeNull();
    expect(asns).toHaveTextContent("No date can be suggested from the filing.");
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

  it("Set another date never prefills a weak suggestion", async () => {
    render(<InsiderHolds />);
    const bbai = rowOf("BBAI");
    await userEvent.click(within(bbai).getByRole("button", { name: "Set another date" }));
    expect(within(bbai).getByLabelText("Trade date")).toHaveValue("");
    expect(within(bbai).getByLabelText("Trade date")).toHaveAttribute("max", "2024-03-08");
    expect(within(bbai).getByRole("button", { name: "Save date" })).toBeDisabled();
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

  it("a weak suggestion on a rule-placed row is text only, with no Use button", () => {
    serve({
      ...FULL,
      placed_by_rule: [{ ...TMUS, suggested_date: "2023-05-24", agrees: false }],
    });
    render(<InsiderHolds />);
    const tmus = rowOf("TMUS");
    expect(within(tmus).queryByRole("button", { name: /^Use / })).toBeNull();
    expect(within(tmus).getByTestId("adm-hold-sugg")).toHaveTextContent("Suggested date 2023-05-24 · weak");
    expect(within(tmus).getByRole("button", { name: "Set the trade date" })).toBeInTheDocument();
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
