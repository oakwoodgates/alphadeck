import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompanyFacts } from "../CompanyFacts";

// The steward's company page over mocked hooks. The shapes are what GET /admin/platform-values/{security_id}
// returns (CompanyValuesOut). The gates: every state of the Q7 table says its words, loud only where the
// steward owes a decision, every filing link goes through ExternalLink, each action sends the right body and has
// its way back on the same page, a refusal shows the server's sentence inside the block that made the call, and
// nothing fires on mount.

const SEC = "00000000-0000-4000-8000-0000000000aa";
const FILING = "https://www.sec.gov/Archives/edgar/data/1/acme-10q.htm";
const STEWARD = "steward@example.com";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = vi.hoisted(() => ({
  q: {} as Record<string, any>,
  approve: {} as Record<string, any>,
  correct: {} as Record<string, any>,
  dismiss: {} as Record<string, any>,
  undo: {} as Record<string, any>,
  pin: {} as Record<string, any>,
  unpin: {} as Record<string, any>,
  extract: {} as Record<string, any>,
  explain: {} as Record<string, any>,
  explainRefetch: vi.fn(),
  explainArgs: [] as any[],
}));

vi.mock("../../api/hooks", () => ({
  useCompanyValues: () => h.q,
  useApproveCandidate: () => h.approve,
  useCorrectValue: () => h.correct,
  useDismissCandidate: () => h.dismiss,
  useUndoDecision: () => h.undo,
  usePinValue: () => h.pin,
  useUnpinValue: () => h.unpin,
  useExtractNow: () => h.extract,
  useExplainFlag: (c: unknown) => {
    h.explainArgs.push(c);
    return { ...h.explain, refetch: h.explainRefetch };
  },
  useResolveSecurities: () => ({ data: undefined, isFetching: false }),
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null, variables: undefined });
const mutate = (x: Record<string, any>) => x.mutate as ReturnType<typeof vi.fn>;

const passage = {
  kind: "cover",
  source_ref: `${FILING}#cover`,
  anchor: "cover page",
  excerpt: "12,345,678 shares of common stock outstanding",
};

const cand = (o: Record<string, any> = {}) => ({
  id: "c0000000-0000-4000-8000-000000000001",
  security_id: SEC,
  fact_type: "shares_outstanding",
  outcome: "flag",
  reason: null,
  flags: ["stale-cover"],
  source: "10-q-cover",
  source_ref: FILING,
  event_date: "2026-09-30",
  shares: 12345678,
  cash_usd: null,
  quarterly_burn_usd: null,
  statement_currency: null,
  ads_ratio: null,
  ads_ratio_status: null,
  filing_accession: null,
  filing_accepted: null,
  note: "",
  located_passages: [passage],
  passages_unreadable: 0,
  recorded_at: "2026-11-10T14:00:00+00:00",
  decision: null,
  ...o,
});

const val = (o: Record<string, any> = {}) => ({
  id: "f0000000-0000-4000-8000-000000000001",
  source: "10-q-cover",
  source_ref: FILING,
  valid_from: "2026-06-30",
  recorded_at: "2026-08-04T10:00:00+00:00",
  ratified_by: "auto",
  vouched: null,
  note: null,
  shares: 52113000,
  ads_ratio: null,
  ads_ratio_status: null,
  cash_usd: null,
  quarterly_burn_usd: null,
  ...o,
});

const dec = (o: Record<string, any> = {}) => ({
  id: "d0000000-0000-4000-8000-000000000001",
  action: "approve",
  candidate_id: "c0000000-0000-4000-8000-000000000001",
  fact_id: "f0000000-0000-4000-8000-000000000001",
  revokes: null,
  reason: null,
  decided_by: STEWARD,
  recorded_at: "2026-11-14T15:00:00+00:00",
  undone: false,
  ...o,
});

const pv = (o: Record<string, any> = {}) => ({
  fact_type: "shares_outstanding",
  state: "no_value",
  reason: "not-extracted",
  ratifier: null,
  pinned: false,
  value: null,
  awaiting: null,
  pending: null,
  versions: [],
  candidates: [],
  decisions: [],
  ...o,
});

const cashPv = (o: Record<string, any> = {}) => pv({ fact_type: "cash_burn", ...o });

const company = (shares: Record<string, any> = {}, cash: Record<string, any> = {}) => ({
  security_id: SEC,
  ticker: "ACME",
  name: "Acme Corp",
  shares: pv(shares),
  cash_burn: cashPv(cash),
});

function serve(data: Record<string, any>) {
  h.q = { data, isLoading: false, isError: false, error: null };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CompanyFacts securityId={SEC} asof={null} />
    </MemoryRouter>,
  );
}

const sharesBlock = () => screen.getByTestId("cf-shares");
const cashBlock = () => screen.getByTestId("cf-cash_burn");
const status = (b: HTMLElement) => within(b).getByTestId("cf-status");

beforeEach(() => {
  h.approve = m();
  h.correct = m();
  h.dismiss = m();
  h.undo = m();
  h.pin = m();
  h.unpin = m();
  h.extract = { ...m(), data: undefined };
  h.explain = { data: undefined, error: null, isError: false, isFetching: false };
  h.explainRefetch = vi.fn();
  h.explainArgs = [];
  serve(company());
});

describe("CompanyFacts: header and loading", () => {
  it("shows the ticker and name, a way back to Admin, and fires no write on mount", () => {
    renderPage();
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to Admin/ })).toHaveAttribute("href", "/admin");
    for (const x of [h.approve, h.correct, h.dismiss, h.undo, h.pin, h.unpin]) {
      expect(mutate(x)).not.toHaveBeenCalled();
    }
    expect(h.explainRefetch).not.toHaveBeenCalled();
  });

  it("says it is loading, and says plainly when the read failed", () => {
    h.q = { data: undefined, isLoading: true, isError: false, error: null };
    const { unmount } = renderPage();
    expect(screen.getByText(/Loading/)).toBeInTheDocument();
    unmount();
    h.q = { data: undefined, isLoading: false, isError: true, error: { detail: "No such company." } };
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("No such company.");
  });
});

describe("CompanyFacts: the status words of every state", () => {
  const quiet: [string, Record<string, any>, string][] = [
    ["not extracted", { reason: "not-extracted" }, "No value yet: not extracted"],
    ["no filing", { reason: "no-annual-filing" }, "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR"],
    ["cover unread", { reason: "cover-not-located" }, "No value yet: the cover count could not be read"],
    ["no candidate", { reason: "no-candidate" }, "No value yet: the latest filing gave no value of this kind"],
    ["dismissed", { reason: "dismissed" }, "No value yet: you dismissed the last candidate"],
    ["exhibit not read", { reason: "financials-in-exhibit" }, "No value yet: the statements were in an exhibit we did not read"],
    ["two exhibits", { reason: "exhibit-ambiguous" }, "No value yet: the statements were in more than one exhibit"],
    ["not found", { reason: "statements-not-located" }, "No value yet: the statements were not found"],
  ];
  it.each(quiet)("no value, %s: the words, and not loud", (_n, over, words) => {
    serve(company(over));
    renderPage();
    expect(status(sharesBlock())).toHaveTextContent(words);
    expect(status(sharesBlock()).className).not.toContain("cf-loud");
  });

  it("cash-generative is a state, not a gap", () => {
    serve(company({}, { reason: "cash-generative" }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent("Runway does not apply: the company is cash-generative");
    expect(status(cashBlock()).className).not.toContain("cf-loud");
  });

  it("a decision with no value shown is loud and says to report it", () => {
    serve(company({ reason: "not-shown" }));
    renderPage();
    expect(status(sharesBlock())).toHaveTextContent("No value shown, though a decision stands. Report this.");
    expect(status(sharesBlock()).className).toContain("cf-loud");
  });

  it("awaiting the steward is loud", () => {
    serve(company({ state: "awaiting_steward", reason: null, awaiting: cand(), candidates: [cand()] }));
    renderPage();
    expect(status(sharesBlock())).toHaveTextContent("Awaiting you");
    expect(status(sharesBlock()).className).toContain("cf-loud");
    // the quiet block beside it stays quiet
    expect(status(cashBlock()).className).not.toContain("cf-loud");
  });

  it("from the filing: value, filing link and date, who and when", () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val() }));
    renderPage();
    const s = status(sharesBlock());
    expect(s).toHaveTextContent("From the filing");
    expect(s).toHaveTextContent("52,113,000");
    expect(s).toHaveTextContent("2026-06-30");
    expect(s).toHaveTextContent("applied by the machine on 2026-08-04");
    expect(s.className).not.toContain("cf-loud");
    const link = within(s).getByRole("link", { name: /filing/ });
    expect(link).toHaveAttribute("href", FILING);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("an annual count from the filing says it is an annual figure and its as-of date, quietly", () => {
    const annual = val({
      source: "annual-cover",
      valid_from: "2025-12-31",
      shares: 25932524521,
      ads_ratio: 5,
      ads_ratio_status: "known",
    });
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: annual }));
    renderPage();
    const s = status(sharesBlock());
    expect(s).toHaveTextContent("From the filing");
    expect(s).toHaveTextContent("25,932,524,521 shares, ADS ratio 5");
    expect(within(s).getByTestId("cf-annual")).toHaveTextContent(
      "annual figure from the 20-F or 40-F cover, as of 2025-12-31",
    );
    expect(s.className).not.toContain("cf-loud");
  });

  it("a quarterly count from the filing carries no annual words", () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val() }));
    renderPage();
    expect(within(status(sharesBlock())).queryByTestId("cf-annual")).toBeNull();
  });

  it("verified by the steward: the email and the time", () => {
    serve(
      company({
        state: "verified",
        reason: null,
        ratifier: "steward",
        value: val({ ratified_by: STEWARD, recorded_at: "2026-11-14T15:00:00+00:00" }),
      }),
    );
    renderPage();
    const s = status(sharesBlock());
    expect(s).toHaveTextContent("Verified");
    expect(s).toHaveTextContent(`by ${STEWARD} on 2026-11-14`);
  });

  it("verified, carried over from V1", () => {
    serve(company({ state: "verified", reason: null, ratifier: "carried", value: val({ ratified_by: "operator" }) }));
    renderPage();
    const s = status(sharesBlock());
    expect(s).toHaveTextContent("Verified (carried over from V1)");
    expect(s).toHaveTextContent("marked operator before V2");
  });

  it("a value with no usable filing link says so instead of dropping the line", () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val({ source_ref: null }) }));
    renderPage();
    expect(status(sharesBlock())).toHaveTextContent("no filing link");
    expect(within(status(sharesBlock())).queryByRole("link")).toBeNull();
  });

  it("cash and burn read as money, with the burn named quarterly", () => {
    serve(
      company(
        {},
        {
          state: "from_filing",
          reason: null,
          ratifier: "machine",
          value: val({ shares: null, cash_usd: 890000000, quarterly_burn_usd: 314678000 }),
        },
      ),
    );
    renderPage();
    expect(status(cashBlock())).toHaveTextContent("$890,000,000");
    expect(status(cashBlock())).toHaveTextContent("quarterly burn $314,678,000");
  });

  it("a stored foreign value shows its own currency and the short-term investment part", () => {
    const value = val({
      source: "annual-statements",
      shares: null,
      cash_usd: 7000000000,
      quarterly_burn_usd: -400000000,
      currency: "CNY",
      investments_usd: 2500000000,
    });
    serve(company({}, { state: "verified", reason: null, ratifier: "steward", value, versions: [value] }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent(
      "CN¥7,000,000,000 cash (of which short-term investments CN¥2,500,000,000), quarterly burn -CN¥400,000,000",
    );
  });

  it("an annual value stored without a currency shows '?', never '$', and no split", () => {
    const value = val({ source: "annual-statements", shares: null, cash_usd: 5000000, quarterly_burn_usd: 100000, currency: null });
    serve(company({}, { state: "verified", reason: null, ratifier: "steward", value }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent("?5,000,000 cash, quarterly burn ?100,000");
    expect(status(cashBlock())).not.toHaveTextContent("$");
    expect(status(cashBlock())).not.toHaveTextContent("of which");
  });

  it("a newer filing beside a value shown: the value's own line stays quiet, the pending one is loud", () => {
    const pending = cand({ event_date: "2026-09-30" });
    serve(
      company({
        state: "from_filing",
        reason: null,
        ratifier: "machine",
        value: val(),
        pending,
        candidates: [pending],
      }),
    );
    renderPage();
    const b = sharesBlock();
    expect(status(b).className).not.toContain("cf-loud");
    expect(status(b)).toHaveTextContent("From the filing");
    const loud = within(b).getByTestId("cf-pending");
    expect(loud.className).toContain("cf-loud");
    expect(loud).toHaveTextContent("A newer filing (2026-09-30) is awaiting you");
    expect(within(b).getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("pinned: the chip says new filings wait, and the pin's who and when show only when the server sends them", () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val(), pinned: true }));
    const { unmount } = renderPage();
    expect(within(sharesBlock()).getByTestId("cf-pin-chip")).toHaveTextContent("Pinned: new filings wait for you");
    expect(within(sharesBlock()).queryByTestId("cf-pin-who")).toBeNull();
    unmount();
    // the seam: once the company read carries the newest pin event, it is named
    serve(
      company({
        state: "from_filing",
        reason: null,
        ratifier: "machine",
        value: val(),
        pinned: true,
        pin: { action: "pin", decided_by: STEWARD, note: "restated twice", recorded_at: "2026-11-12T09:00:00+00:00" },
      }),
    );
    renderPage();
    expect(within(sharesBlock()).getByTestId("cf-pin-who")).toHaveTextContent(
      `by ${STEWARD} on 2026-11-12: restated twice`,
    );
  });

  it("a candidate held because the company is pinned says why", () => {
    const c = cand({ flags: ["pinned"] });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c], pinned: true }));
    renderPage();
    expect(sharesBlock()).toHaveTextContent(
      "Held because you pinned this company: a clean value from the filing",
    );
  });
});

describe("CompanyFacts: the evidence behind a candidate", () => {
  it("shows the raw value, flags, filing link and date, who found it, and the located passage", () => {
    const c = cand({ ads_ratio: 2, ads_ratio_status: "known" });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    renderPage();
    const panel = within(sharesBlock()).getByTestId("cf-candidate");
    expect(panel).toHaveTextContent("12,345,678");
    expect(panel).toHaveTextContent("stale-cover");
    expect(panel).toHaveTextContent("2026-09-30");
    expect(panel).toHaveTextContent("10-q-cover");
    expect(panel).toHaveTextContent("ADS ratio 2");
    expect(within(panel).getByRole("link", { name: /filing/ })).toHaveAttribute("href", FILING);
    expect(within(panel).getByRole("link", { name: /cover page/ })).toHaveAttribute("href", `${FILING}#cover`);
    expect(panel).toHaveTextContent(passage.excerpt);
  });

  it("names a candidate's passages the server could not read", () => {
    const c = cand({ passages_unreadable: 2 });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    renderPage();
    expect(sharesBlock()).toHaveTextContent("2 stored passages could not be read");
  });

  it("a cash/burn candidate in a native currency names it", () => {
    const c = cand({
      fact_type: "cash_burn",
      shares: null,
      cash_usd: 2770000000,
      quarterly_burn_usd: 90000000,
      statement_currency: "TWD",
      flags: [],
    });
    serve(company({}, { state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    renderPage();
    const panel = within(cashBlock()).getByTestId("cf-candidate");
    expect(panel).toHaveTextContent("NT$2,770,000,000");
    expect(panel).toHaveTextContent("quarterly burn NT$90,000,000");
  });
});

describe("CompanyFacts: Explain", () => {
  const awaitingFlag = () => {
    const c = cand();
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
  };

  it("is offered on a flag candidate, with the stored candidate mapped to the extractor's shape", () => {
    awaitingFlag();
    renderPage();
    expect(screen.getByRole("button", { name: "Explain in plain English" })).toBeInTheDocument();
    expect(h.explainArgs[0]).toMatchObject({
      fact_type: "shares_outstanding",
      tier: "flag",
      source: "10-q-cover",
      source_ref: FILING,
      event_date: "2026-09-30",
      value: 12345678,
      flags: ["stale-cover"],
      located_passages: [passage],
    });
  });

  it("runs only on a click", async () => {
    awaitingFlag();
    renderPage();
    expect(h.explainRefetch).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: "Explain in plain English" }));
    expect(h.explainRefetch).toHaveBeenCalledTimes(1);
  });

  it("shows a grounded answer as drafted, and a not-grounded one as a read-the-passage line", () => {
    awaitingFlag();
    h.explain = { data: { grounded: true, explanation: "The cover count is older than the filing." }, isFetching: false, isError: false, error: null };
    const { unmount } = renderPage();
    expect(screen.getByText("drafted")).toBeInTheDocument();
    expect(screen.getByText("The cover count is older than the filing.")).toBeInTheDocument();
    unmount();
    h.explain = { data: { grounded: false, explanation: null }, isFetching: false, isError: false, error: null };
    renderPage();
    expect(screen.getByText(/No plain-English read grounded in the passage/)).toBeInTheDocument();
  });

  it("is not offered on a nothing candidate, a dismissed one, or a value with nothing waiting", () => {
    const nothing = cand({ outcome: "nothing", reason: "no-annual-filing", flags: [], located_passages: [], shares: null });
    const dismissed = cand({ id: "c0000000-0000-4000-8000-000000000002", decision: dec({ action: "dismiss", reason: "stale" }) });
    serve(company({ reason: "dismissed", candidates: [nothing, dismissed] }));
    renderPage();
    expect(screen.queryByRole("button", { name: /Explain/ })).toBeNull();
  });
});

describe("CompanyFacts: the actions and their way back", () => {
  const awaiting = () => {
    const c = cand();
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    return c;
  };

  it("Approve sends the candidate id, and the note only when one is typed", async () => {
    const c = awaiting();
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Approve" }));
    expect(mutate(h.approve)).toHaveBeenLastCalledWith({
      securityId: SEC,
      factType: "shares_outstanding",
      candidateId: c.id,
    });
    await user.type(within(sharesBlock()).getByLabelText("Approve note (optional)"), "checked the cover");
    await user.click(within(sharesBlock()).getByRole("button", { name: "Approve" }));
    expect(mutate(h.approve)).toHaveBeenLastCalledWith({
      securityId: SEC,
      factType: "shares_outstanding",
      candidateId: c.id,
      note: "checked the cover",
    });
  });

  it("Dismiss is disabled until a reason is typed, then sends it trimmed", async () => {
    const c = awaiting();
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Dismiss…" }));
    const go = within(sharesBlock()).getByRole("button", { name: "Dismiss" });
    expect(go).toBeDisabled();
    await user.type(within(sharesBlock()).getByLabelText("Reason for dismissing"), "  a notice, not a count  ");
    expect(go).toBeEnabled();
    await user.click(go);
    expect(mutate(h.dismiss)).toHaveBeenCalledWith({
      securityId: SEC,
      factType: "shares_outstanding",
      candidateId: c.id,
      reason: "a notice, not a count",
    });
  });

  it("Correct opens a form prefilled from the candidate and sends every field", async () => {
    const c = cand({ ads_ratio: 2, ads_ratio_status: "known" });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Correct…" }));
    const b = sharesBlock();
    expect(within(b).getByLabelText("Share count")).toHaveValue(12345678);
    expect(within(b).getByLabelText("Filing URL")).toHaveValue(FILING);
    expect(within(b).getByLabelText("Filing date")).toHaveValue("2026-09-30");
    expect(within(b).getByLabelText("ADS ratio status")).toHaveValue("known");
    expect(within(b).getByLabelText("ADS ratio")).toHaveValue(2);
    fireEvent.change(within(b).getByLabelText("Share count"), { target: { value: "12400000" } });
    await user.type(within(b).getByLabelText("Note (optional)"), "from the cover");
    await user.click(within(b).getByRole("button", { name: "Save correction" }));
    expect(mutate(h.correct)).toHaveBeenCalledWith({
      securityId: SEC,
      body: {
        fact_type: "shares_outstanding",
        shares: 12400000,
        ads_ratio: 2,
        ads_ratio_status: "known",
        source_ref: FILING,
        event_date: "2026-09-30",
        note: "from the cover",
        candidate_id: c.id,
      },
    });
  });

  it("Correct on a company with no value starts blank and refuses a blank value or filing URL", async () => {
    serve(company({ reason: "not-extracted" }));
    renderPage();
    const user = userEvent.setup();
    const b = sharesBlock();
    await user.click(within(b).getByRole("button", { name: "Correct…" }));
    const save = within(b).getByRole("button", { name: "Save correction" });
    expect(save).toBeDisabled();
    fireEvent.change(within(b).getByLabelText("Filing date"), { target: { value: "2026-09-30" } });
    fireEvent.change(within(b).getByLabelText("Share count"), { target: { value: "5000000" } });
    expect(save).toBeDisabled(); // no filing URL yet
    fireEvent.change(within(b).getByLabelText("Filing URL"), { target: { value: "   " } });
    expect(save).toBeDisabled(); // a blank URL is still blank
    fireEvent.change(within(b).getByLabelText("Filing URL"), { target: { value: FILING } });
    expect(save).toBeEnabled();
    fireEvent.change(within(b).getByLabelText("Share count"), { target: { value: "" } });
    expect(save).toBeDisabled(); // no value
    await user.click(save);
    expect(mutate(h.correct)).not.toHaveBeenCalled();
  });

  it("Correct on a value shown prefills from it, with no candidate id", async () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val() }));
    renderPage();
    const user = userEvent.setup();
    const b = sharesBlock();
    await user.click(within(b).getByRole("button", { name: "Correct…" }));
    expect(within(b).getByLabelText("Share count")).toHaveValue(52113000);
    expect(within(b).getByLabelText("Filing date")).toHaveValue("2026-06-30");
    await user.click(within(b).getByRole("button", { name: "Save correction" }));
    const body = mutate(h.correct).mock.calls[0][0].body;
    expect(body).toEqual({
      fact_type: "shares_outstanding",
      shares: 52113000,
      source_ref: FILING,
      event_date: "2026-06-30",
    });
  });

  it("Correct for cash/burn sends both numbers", async () => {
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: val({ shares: null, cash_usd: 890000000, quarterly_burn_usd: 314678000 }) }));
    renderPage();
    const user = userEvent.setup();
    const b = cashBlock();
    await user.click(within(b).getByRole("button", { name: "Correct…" }));
    fireEvent.change(within(b).getByLabelText(/^Quarterly burn/), { target: { value: "0" } }); // zero is a real answer
    await user.click(within(b).getByRole("button", { name: "Save correction" }));
    expect(mutate(h.correct).mock.calls[0][0].body).toEqual({
      fact_type: "cash_burn",
      cash_usd: 890000000,
      quarterly_burn_usd: 0,
      source_ref: FILING,
      event_date: "2026-06-30",
    });
  });

  it("Correct for cash/burn prefills the candidate's currency and investment part and sends them", async () => {
    const c = cand({
      fact_type: "cash_burn",
      source: "annual-statements",
      shares: null,
      cash_usd: 9000000,
      quarterly_burn_usd: 1500000,
      statement_currency: "JPY",
      investments_usd: 2000000,
      flags: ["annual-statements"],
    });
    serve(company({}, { state: "awaiting_steward", reason: null, awaiting: c, candidates: [c] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(cashBlock()).getByRole("button", { name: "Correct…" }));
    const b = cashBlock();
    expect(within(b).getByLabelText("Currency (ISO code)")).toHaveValue("JPY");
    expect(within(b).getByLabelText(/^Of which short-term investments/)).toHaveValue(2000000);
    expect(within(b).getByLabelText(/^Cash/)).toBeInTheDocument();
    expect(b).toHaveTextContent("Cash (¥)");
    await user.click(within(b).getByRole("button", { name: "Save correction" }));
    expect(mutate(h.correct).mock.calls[0][0].body).toEqual({
      fact_type: "cash_burn",
      cash_usd: 9000000,
      quarterly_burn_usd: 1500000,
      currency: "JPY",
      investments_usd: 2000000,
      source_ref: FILING,
      event_date: "2026-09-30",
      candidate_id: c.id,
    });
  });

  it("Correct for cash/burn takes a typed currency in capitals and refuses a part above the cash", async () => {
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: val({ shares: null, cash_usd: 1000, quarterly_burn_usd: 10 }) }));
    renderPage();
    const user = userEvent.setup();
    const b = cashBlock();
    await user.click(within(b).getByRole("button", { name: "Correct…" }));
    expect(within(b).getByLabelText("Currency (ISO code)")).toHaveValue("");
    await user.type(within(b).getByLabelText("Currency (ISO code)"), "cad");
    expect(within(b).getByLabelText("Currency (ISO code)")).toHaveValue("CAD");
    fireEvent.change(within(b).getByLabelText(/^Of which short-term investments/), { target: { value: "1001" } });
    expect(within(b).getByRole("button", { name: "Save correction" })).toBeDisabled();
    fireEvent.change(within(b).getByLabelText(/^Of which short-term investments/), { target: { value: "1000" } });
    await user.click(within(b).getByRole("button", { name: "Save correction" }));
    expect(mutate(h.correct).mock.calls[0][0].body).toMatchObject({ currency: "CAD", investments_usd: 1000 });
  });

  it("the newest standing decision shows right away with its Undo, and Undo sends the decision id", async () => {
    const d = dec();
    serve(
      company({
        state: "verified",
        reason: null,
        ratifier: "steward",
        value: val({ ratified_by: STEWARD }),
        decisions: [d],
      }),
    );
    renderPage();
    const last = within(sharesBlock()).getByTestId("cf-last-decision");
    expect(last).toHaveTextContent(`Approved by ${STEWARD} on 2026-11-14`);
    await userEvent.setup().click(within(last).getByRole("button", { name: "Undo" }));
    expect(mutate(h.undo)).toHaveBeenCalledWith({ securityId: SEC, factType: "shares_outstanding", decisionId: d.id });
  });

  it("every standing decision in the history has an Undo, an undone one is gray and has none", async () => {
    const approve = dec({ id: "d0000000-0000-4000-8000-000000000001" });
    const dismiss = dec({ id: "d0000000-0000-4000-8000-000000000002", action: "dismiss", fact_id: null, reason: "stale", recorded_at: "2026-11-13T10:00:00+00:00" });
    const undone = dec({ id: "d0000000-0000-4000-8000-000000000003", action: "dismiss", fact_id: null, undone: true, recorded_at: "2026-11-12T10:00:00+00:00" });
    const undo = dec({ id: "d0000000-0000-4000-8000-000000000004", action: "undo", candidate_id: null, fact_id: null, revokes: undone.id, decided_by: "other@example.com", recorded_at: "2026-11-12T11:00:00+00:00" });
    serve(company({ state: "verified", reason: null, ratifier: "steward", value: val({ ratified_by: STEWARD }), decisions: [approve, dismiss, undo, undone] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    const list = within(sharesBlock()).getByTestId("cf-decisions");
    expect(within(list).getAllByRole("button", { name: "Undo" })).toHaveLength(2);
    const grayRow = within(list).getAllByTestId("cf-decision-row").find((r) => r.className.includes("cf-undone"))!;
    expect(grayRow).toHaveTextContent("undone by other@example.com on 2026-11-12");
    expect(within(grayRow).queryByRole("button", { name: "Undo" })).toBeNull();
    // the undo is itself on the list, with no Undo of its own, and the page says what to do instead
    const undoRow = within(list).getAllByTestId("cf-decision-row").find((r) => r.textContent?.includes("Undid"))!;
    expect(within(undoRow).queryByRole("button", { name: "Undo" })).toBeNull();
    expect(list).toHaveTextContent("An undo cannot be undone. Make the decision again instead.");
  });

  it("an undone Correct offers Correct again, prefilled from the set-aside version", async () => {
    const v1 = { ...val({ id: "f0000000-0000-4000-8000-000000000009", ratified_by: STEWARD, shares: 7777777, valid_from: "2026-09-01" }), set_aside: true };
    const correct = dec({ id: "d0000000-0000-4000-8000-000000000005", action: "correct", fact_id: v1.id, undone: true, candidate_id: null });
    const undo = dec({ id: "d0000000-0000-4000-8000-000000000006", action: "undo", fact_id: null, candidate_id: null, revokes: correct.id, recorded_at: "2026-11-14T16:00:00+00:00" });
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val(), versions: [{ ...val(), set_aside: false }, v1], decisions: [undo, correct] }));
    renderPage();
    const user = userEvent.setup();
    // the way back is on the page without opening the history
    const note = within(sharesBlock()).getByTestId("cf-undone-note");
    expect(note).toHaveTextContent(`Corrected by ${STEWARD} on 2026-11-14 was undone`);
    await user.click(within(note).getByRole("button", { name: "Correct again" }));
    expect(within(sharesBlock()).getByLabelText("Share count")).toHaveValue(7777777);
    expect(within(sharesBlock()).getByLabelText("Filing date")).toHaveValue("2026-09-01");
    await user.click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    // the set-aside version is on the Versions list, gray and marked undone
    const versions = within(sharesBlock()).getByTestId("cf-versions");
    const gray = within(versions).getAllByTestId("cf-version-row").find((r) => r.className.includes("cf-undone"))!;
    expect(gray).toHaveTextContent("undone");
    expect(gray).toHaveTextContent("7,777,777");
  });

  it("Pin and Unpin swap, each with an optional note", async () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val() }));
    const { unmount } = renderPage();
    const user = userEvent.setup();
    expect(within(sharesBlock()).queryByRole("button", { name: "Unpin" })).toBeNull();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Pin" }));
    expect(mutate(h.pin)).toHaveBeenLastCalledWith({ securityId: SEC, factType: "shares_outstanding" });
    await user.type(within(sharesBlock()).getByLabelText("Pin note (optional)"), "restated twice");
    await user.click(within(sharesBlock()).getByRole("button", { name: "Pin" }));
    expect(mutate(h.pin)).toHaveBeenLastCalledWith({ securityId: SEC, factType: "shares_outstanding", note: "restated twice" });
    unmount();
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val(), pinned: true }));
    renderPage();
    expect(within(sharesBlock()).queryByRole("button", { name: "Pin" })).toBeNull();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Unpin" }));
    expect(mutate(h.unpin)).toHaveBeenCalledWith({ securityId: SEC, factType: "shares_outstanding" });
  });

  it("Unpin stays reachable while a candidate waits", () => {
    const c = cand({ flags: ["pinned"] });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: c, candidates: [c], pinned: true }));
    renderPage();
    expect(within(sharesBlock()).getByRole("button", { name: "Unpin" })).toBeInTheDocument();
  });
});

describe("CompanyFacts: nothing vanishes", () => {
  it("lists a dismissed candidate, grayed, and an older reopened one as open and superseded, with Approve", async () => {
    const newest = cand({ id: "c0000000-0000-4000-8000-000000000003", event_date: "2026-09-30" });
    const older = cand({ id: "c0000000-0000-4000-8000-000000000002", event_date: "2026-06-30" });
    const dismissed = cand({
      id: "c0000000-0000-4000-8000-000000000001",
      event_date: "2026-03-31",
      decision: dec({ action: "dismiss", reason: "stale cover", candidate_id: "c0000000-0000-4000-8000-000000000001" }),
    });
    serve(company({ state: "awaiting_steward", reason: null, awaiting: newest, candidates: [newest, older, dismissed] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    const rows = within(within(sharesBlock()).getByTestId("cf-candidates")).getAllByTestId("cf-candidate-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("open");
    expect(rows[1]).toHaveTextContent("open, superseded by a newer candidate");
    expect(rows[2].className).toContain("cf-undone");
    expect(rows[2]).toHaveTextContent("dismissed by steward@example.com on 2026-11-14: stale cover");
    await user.click(within(rows[1]).getByRole("button", { name: "Approve" }));
    expect(mutate(h.approve)).toHaveBeenCalledWith({ securityId: SEC, factType: "shares_outstanding", candidateId: older.id });
  });

  it("the history is closed until asked for", () => {
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val(), versions: [{ ...val(), set_aside: false }] }));
    renderPage();
    expect(within(sharesBlock()).queryByTestId("cf-versions")).toBeNull();
  });
});

describe("CompanyFacts: refusals", () => {
  it("shows the server's sentence as given, inside the block that made the call and nowhere else", () => {
    const sentence = "That value is older than the one shown (2026-09-30, filing acme-10q).";
    h.correct = {
      ...m(),
      isError: true,
      error: { detail: sentence },
      variables: { securityId: SEC, body: { fact_type: "shares_outstanding" } },
    };
    serve(company({ state: "from_filing", reason: null, ratifier: "machine", value: val() }));
    renderPage();
    const alert = within(sharesBlock()).getByRole("alert");
    expect(alert.textContent).toBe(sentence);
    expect(within(cashBlock()).queryByRole("alert")).toBeNull();
  });

  it("adds no second period to a sentence that already ends with one", () => {
    h.approve = { ...m(), isError: true, error: { detail: "This candidate is already decided." }, variables: { securityId: SEC, factType: "cash_burn", candidateId: "x" } };
    serve(company());
    renderPage();
    expect(within(cashBlock()).getByRole("alert").textContent).toBe("This candidate is already decided.");
    expect(within(sharesBlock()).queryByRole("alert")).toBeNull();
  });

  it("shows a refusal of an undo or a pin in its own block", () => {
    h.undo = { ...m(), isError: true, error: { detail: "An undo cannot be undone. Make a new decision instead." }, variables: { securityId: SEC, factType: "shares_outstanding", decisionId: "d" } };
    h.pin = { ...m(), isError: true, error: { detail: "This company is busy. Try again in a moment." }, variables: { securityId: SEC, factType: "cash_burn" } };
    serve(company());
    renderPage();
    expect(within(sharesBlock()).getByRole("alert").textContent).toBe("An undo cannot be undone. Make a new decision instead.");
    expect(within(cashBlock()).getByRole("alert").textContent).toBe("This company is busy. Try again in a moment.");
  });
});

// --- Extract now (W4d) -----------------------------------------------------------------------------------------------

const outcome = (o: Record<string, any> = {}) => ({
  outcome: "applied",
  reason: null,
  written: true,
  fact_id: null,
  candidate_id: null,
  ...o,
});

const attempted = (o: Record<string, any> = {}) => ({
  attempt_id: "a0000000-0000-4000-8000-000000000001",
  status: "done",
  error: null,
  detail: null,
  live_fetches: 3,
  shares: outcome(),
  cash_burn: outcome(),
  ...o,
});

const attemptRow = (o: Record<string, any> = {}) => ({
  id: "a0000000-0000-4000-8000-000000000001",
  recorded_at: "2026-11-15T14:03:00+00:00",
  run_kind: "steward",
  status: "done",
  shares_reason: "applied",
  cash_reason: "nothing_found",
  error: null,
  detail: null,
  ...o,
});

const extractArea = () => screen.getByTestId("cf-extract");

describe("CompanyFacts: Extract now", () => {
  it("is offered quietly, fires only on a click, and says honestly what a click can change", async () => {
    renderPage();
    const button = within(extractArea()).getByRole("button", { name: "Extract now" });
    expect(mutate(h.extract)).not.toHaveBeenCalled();
    expect(extractArea()).toHaveTextContent("Reads this company's latest SEC filings");
    expect(extractArea()).toHaveTextContent(
      "A clean value from a newer filing replaces the one shown, unless that value is pinned",
    );
    expect(button.className).not.toContain("primary");
    await userEvent.click(button);
    expect(mutate(h.extract)).toHaveBeenCalledTimes(1);
    expect(mutate(h.extract).mock.calls[0][0]).toEqual({ securityId: SEC });
  });

  it("is disabled and says so while it runs, and holds the other actions too", () => {
    h.extract = { ...m(), isPending: true, data: undefined };
    serve(company({ state: "awaiting_steward", reason: null, awaiting: cand() }));
    renderPage();
    const button = within(extractArea()).getByRole("button", { name: /Extracting/ });
    expect(button).toBeDisabled();
    expect(within(sharesBlock()).getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("after a click, says what happened to each value in one plain sentence", () => {
    h.extract = {
      ...m(),
      isSuccess: true,
      data: {
        attempt: attempted({
          shares: outcome({ outcome: "flagged", written: true }),
          cash_burn: outcome({ outcome: "nothing_found", reason: "cash-generative", written: true }),
        }),
        company: company(),
      },
    };
    renderPage();
    const lines = within(extractArea()).getAllByTestId("cf-extract-line").map((x) => x.textContent);
    expect(lines).toEqual([
      "Share count: a flagged value from the filing now awaits you",
      "Cash and burn: the filings gave no value (cash-generative)",
    ]);
  });

  it.each([
    [{ outcome: "applied" }, "Share count: a newer value from the filing was applied"],
    [{ outcome: "flagged", written: false }, "Share count: the same flagged value already awaits you"],
    [{ outcome: "pinned", written: true }, "Share count: a clean value now awaits you because this value is pinned"],
    [{ outcome: "already_on_file", written: false }, "Share count: already on file, nothing changed"],
    [{ outcome: "older_than_on_file", written: false }, "Share count: the filing is not newer than the value shown, nothing changed"],
    [{ outcome: "after_today", written: false }, "Share count: the filing is dated after today, nothing written"],
  ])("words the outcome %o", (o, words) => {
    h.extract = { ...m(), isSuccess: true, data: { attempt: attempted({ shares: outcome(o) }), company: company() } };
    renderPage();
    expect(within(extractArea()).getAllByTestId("cf-extract-line")[0].textContent).toBe(words);
  });

  it("a failed extraction shows the server's reason as given, with no period added", () => {
    const failed = outcome({ outcome: "failed", reason: "no SEC User-Agent set", written: false });
    h.extract = {
      ...m(),
      isSuccess: true,
      data: {
        attempt: attempted({ status: "error", error: "no SEC User-Agent set", shares: failed, cash_burn: failed }),
        company: company(),
      },
    };
    renderPage();
    const alert = within(extractArea()).getByRole("alert");
    expect(alert.textContent).toBe("The extraction failed: no SEC User-Agent set");
    expect(within(extractArea()).queryAllByTestId("cf-extract-line")).toEqual([]);
  });

  it("a refusal (a busy company) shows the server's sentence in place, and only there", () => {
    const sentence = "Another writer is updating this company. Try again in a moment. Reference 1a2b3c4d.";
    h.extract = { ...m(), isError: true, error: { detail: sentence }, variables: { securityId: SEC }, data: undefined };
    renderPage();
    expect(within(extractArea()).getByRole("alert").textContent).toBe(sentence);
    expect(within(sharesBlock()).queryByRole("alert")).toBeNull();
    expect(within(cashBlock()).queryByRole("alert")).toBeNull();
  });

  it("lists the recent extraction attempts in each value's collapsed history", async () => {
    serve({
      ...company(),
      attempts: [
        attemptRow({ id: "a2", status: "error", shares_reason: null, cash_reason: null, error: "SEC timed out", run_kind: "nightly" }),
        attemptRow({ id: "a1" }),
        attemptRow({ id: "a0", status: "busy", shares_reason: null, cash_reason: null, detail: "company busy" }),
      ],
    });
    renderPage();
    expect(within(sharesBlock()).queryByTestId("cf-attempts")).toBeNull();
    await userEvent.click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    const rows = within(within(sharesBlock()).getByTestId("cf-attempts"))
      .getAllByTestId("cf-attempt-row")
      .map((x) => x.textContent);
    expect(rows).toEqual([
      "2026-11-15 · nightly run · failed: SEC timed out",
      "2026-11-15 · Extract now · a newer value from the filing was applied",
      "2026-11-15 · Extract now · busy: another writer held the company, nothing written",
    ]);
    await userEvent.click(within(cashBlock()).getByRole("button", { name: /History/ }));
    const cashRows = within(within(cashBlock()).getByTestId("cf-attempts")).getAllByTestId("cf-attempt-row");
    expect(cashRows[1].textContent).toBe("2026-11-15 · Extract now · the filings gave no value");
  });

  it("says so when no extraction was ever attempted", async () => {
    serve({ ...company(), attempts: [] });
    renderPage();
    fireEvent.click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    expect(within(sharesBlock()).getByTestId("cf-attempts")).toHaveTextContent("No extraction attempts yet.");
  });
});

describe("CompanyFacts: the burn's year-to-date pace (V2 extractor correctness PR 3)", () => {
  const CAVEAT = "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months at last quarter's pace and 2.2 months at the year-to-date pace.";
  const upValue = (pace: Record<string, any> | null) =>
    val({ source: "10-q", shares: null, cash_usd: 51811000, quarterly_burn_usd: 17085000, pace });

  it("shows the year-to-date pace beside an applied value, and its caveat quietly", () => {
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: upValue({ ytd_quarterly_burn_usd: 69943025, ytd_days: 272, ytd_runway_months: 2.2, caveat: CAVEAT }) }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent(
      "year-to-date pace: quarterly burn $69,943,025 (2.2 months of runway)",
    );
    const caveat = within(cashBlock()).getByTestId("cf-pace-caveat");
    expect(caveat).toHaveTextContent(CAVEAT);
    expect(caveat.className).toBe("cf-quiet");
    expect(status(cashBlock()).className).not.toContain("cf-loud"); // a caveat, never a loud state
  });

  it("shows a steady pace without a caveat, and nothing for a value without a pace", () => {
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: upValue({ ytd_quarterly_burn_usd: 88779250, ytd_days: 272, ytd_runway_months: 18.6, caveat: null }) }));
    const { unmount } = renderPage();
    expect(status(cashBlock())).toHaveTextContent("year-to-date pace: quarterly burn $88,779,250 (18.6 months of runway)");
    expect(within(cashBlock()).queryByTestId("cf-pace-caveat")).toBeNull();
    unmount();
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: upValue(null) }));
    renderPage();
    expect(status(cashBlock())).not.toHaveTextContent("year-to-date pace");
  });

  it("speaks a yuan value's pace in yuan (GAP 3)", () => {
    const yuan = val({
      source: "annual-statements",
      shares: null,
      cash_usd: 1200000000,
      quarterly_burn_usd: 50000000,
      currency: "CNY",
      pace: { ytd_quarterly_burn_usd: 40000000, ytd_days: 272, ytd_runway_months: 90, caveat: null },
    });
    serve(company({}, { state: "verified", reason: null, ratifier: "steward", value: yuan }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent("year-to-date pace: quarterly burn CN¥40,000,000 (90 months of runway)");
    expect(status(cashBlock())).not.toHaveTextContent("$");
  });

  it("says a cash-generating year to date in words", () => {
    serve(company({}, { state: "from_filing", reason: null, ratifier: "machine", value: upValue({ ytd_quarterly_burn_usd: -1524448759, ytd_days: 272, ytd_runway_months: null, caveat: CAVEAT }) }));
    renderPage();
    expect(status(cashBlock())).toHaveTextContent("year-to-date pace: cash-generative, $1,524,448,759 a quarter");
  });
});
