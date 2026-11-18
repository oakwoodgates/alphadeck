import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompanyFacts } from "../CompanyFacts";

// W5f: the user flags on the steward's company page. An open flag is loud and carries its two closing actions;
// "fixed" needs the standing approve or correct of the value (prefilled from the page's history) and is disabled
// with a quiet reason when there is none; "kept" needs a note the user reads; every refusal is the SERVER's
// sentence; a closed flag sits gray under History with its outcome, its Reopen (or the server's reason it cannot
// be), and pages older ones in; and every user-written string is plain text.

const SEC = "00000000-0000-4000-8000-0000000000aa";
const FILING = "https://www.sec.gov/Archives/edgar/data/1/acme-10q.htm";
const STEWARD = "steward@example.com";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = vi.hoisted(() => ({
  q: {} as Record<string, any>,
  close: {} as Record<string, any>,
  reopen: {} as Record<string, any>,
  older: { rows: [], isFetching: false, isError: false, error: null } as Record<string, any>,
  olderArgs: [] as any[],
}));

const inert = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null, variables: undefined });

vi.mock("../../api/hooks", () => ({
  useCompanyValues: () => h.q,
  useApproveCandidate: () => inert(),
  useCorrectValue: () => inert(),
  useDismissCandidate: () => inert(),
  useUndoDecision: () => inert(),
  usePinValue: () => inert(),
  useUnpinValue: () => inert(),
  useExtractNow: () => ({ ...inert(), data: undefined }),
  useExplainFlag: () => ({ data: undefined, error: null, isError: false, isFetching: false, refetch: vi.fn() }),
  useResolveSecurities: () => ({ data: undefined, isFetching: false }),
  useCloseFlag: () => h.close,
  useReopenFlag: () => h.reopen,
  useClosedFlagPages: (securityId: string, extraPages: number, limit: number) => {
    h.olderArgs.push({ securityId, extraPages, limit });
    return h.older;
  },
}));

const FID = (n: number) => `a0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const DID = (n: number) => `d0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

const version = (o: Record<string, any> = {}) => ({
  id: "f0000000-0000-4000-8000-000000000001",
  source: "10-q-cover",
  source_ref: FILING,
  valid_from: "2026-06-30",
  recorded_at: "2026-08-04T10:00:00+00:00",
  ratified_by: "auto",
  vouched: null,
  note: null,
  set_aside: false,
  shares: 52113000,
  ads_ratio: null,
  ads_ratio_status: null,
  cash_usd: null,
  quarterly_burn_usd: null,
  ...o,
});

const dec = (o: Record<string, any> = {}) => ({
  id: DID(1),
  action: "correct",
  candidate_id: null,
  fact_id: "f0000000-0000-4000-8000-000000000001",
  revokes: null,
  reason: null,
  decided_by: STEWARD,
  recorded_at: "2026-11-14T15:00:00+00:00",
  undone: false,
  ...o,
});

const flag = (o: Record<string, any> = {}) => ({
  id: FID(1),
  value_type: "shares_outstanding",
  user_email: "pat@example.com",
  note: "This looks too low",
  opened_at: "2026-11-12T09:00:00+00:00",
  current_when_flagged: "value",
  seen_value: version(),
  seen_candidate: null,
  reopens: null,
  reopened_by: null,
  reopened_as: null,
  closed_at: null,
  closed_by: null,
  outcome: null,
  outcome_note: null,
  fixed_decision: null,
  fixed_decision_undone: false,
  can_reopen: false,
  reopen_refusal: null,
  ...o,
});

const closedFlag = (n: number, o: Record<string, any> = {}) =>
  flag({
    id: FID(n),
    closed_at: "2026-11-15T10:00:00+00:00",
    closed_by: STEWARD,
    outcome: "kept",
    outcome_note: "The filing states two classes; this is the total.",
    can_reopen: true,
    ...o,
  });

const pv = (o: Record<string, any> = {}) => ({
  fact_type: "shares_outstanding",
  state: "from_filing",
  reason: null,
  ratifier: "auto",
  pinned: false,
  value: version(),
  awaiting: null,
  pending: null,
  versions: [version()],
  candidates: [],
  decisions: [],
  ...o,
});

const company = (
  flags: { open?: any[]; closed?: any[]; closed_total?: number; closed_limit?: number } = {},
  shares: Record<string, any> = {},
  cash: Record<string, any> = {},
) => ({
  security_id: SEC,
  ticker: "ACME",
  name: "Acme Corp",
  shares: pv(shares),
  cash_burn: pv({ fact_type: "cash_burn", value: null, versions: [], state: "no_value", reason: "not-extracted", ...cash }),
  attempts: [],
  flags: {
    open: flags.open ?? [],
    closed: flags.closed ?? [],
    closed_total: flags.closed_total ?? (flags.closed ?? []).length,
    closed_offset: 0,
    closed_limit: flags.closed_limit ?? 20,
  },
});

const serve = (data: Record<string, any>) => {
  h.q = { data, isLoading: false, isError: false, error: null };
};

function renderPage() {
  return render(
    <MemoryRouter>
      <CompanyFacts securityId={SEC} asof={null} />
    </MemoryRouter>,
  );
}

const sharesBlock = () => screen.getByTestId("cf-shares");
const mutate = (x: Record<string, any>) => x.mutate as ReturnType<typeof vi.fn>;

beforeEach(() => {
  h.close = inert();
  h.reopen = inert();
  h.older = { rows: [], isFetching: false, isError: false, error: null };
  h.olderArgs = [];
  serve(company());
});

describe("flags from users: nothing to say when none is open", () => {
  it("renders no 'Flags from users' block and no flag control when no flag is open", () => {
    serve(company({ open: [] }));
    renderPage();
    expect(screen.queryByText("Flags from users")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Close as/ })).not.toBeInTheDocument();
  });
});

describe("flags from users: an open flag", () => {
  it("is loud and shows the user, the date, what was current, and the note, under the status line", () => {
    serve(company({ open: [flag()] }, { decisions: [dec()] }));
    renderPage();
    const block = within(sharesBlock()).getByTestId("cf-flags");
    expect(block).toHaveTextContent("Flags from users");
    expect(block.querySelector(".cf-loud")).not.toBeNull();
    const row = within(block).getByTestId("cf-flag-open");
    expect(row).toHaveTextContent("pat@example.com");
    expect(row).toHaveTextContent("2026-11-12");
    expect(row).toHaveTextContent("Current when flagged: 52,113,000 shares, dated 2026-06-30");
    expect(row).toHaveTextContent("This looks too low");
    const html = sharesBlock().innerHTML;
    expect(html.indexOf('data-testid="cf-status"')).toBeLessThan(html.indexOf('data-testid="cf-flags"'));
  });

  it("says what the candidate was when the flag recorded an awaiting candidate, and 'no value yet' for none", () => {
    serve(
      company({
        open: [
          flag({
            id: FID(2),
            current_when_flagged: "awaiting",
            seen_value: null,
            seen_candidate: {
              id: "c0000000-0000-4000-8000-000000000001",
              security_id: SEC,
              fact_type: "shares_outstanding",
              outcome: "flag",
              flags: ["stale-cover"],
              passages_unreadable: 0,
              shares: 12345678,
              event_date: "2026-09-30",
              recorded_at: "2026-11-10T14:00:00+00:00",
            },
          }),
          flag({ id: FID(3), current_when_flagged: "none", seen_value: null }),
        ],
      }),
    );
    renderPage();
    const rows = within(sharesBlock()).getAllByTestId("cf-flag-open");
    expect(rows[0]).toHaveTextContent("Current when flagged: awaiting you, 12,345,678 shares, dated 2026-09-30");
    expect(rows[1]).toHaveTextContent("Current when flagged: no value yet");
  });

  it("shows a flag under its own value only", () => {
    serve(company({ open: [flag({ value_type: "cash_burn", note: "burn is wrong" })] }));
    renderPage();
    expect(within(screen.getByTestId("cf-cash_burn")).getByText("burn is wrong")).toBeInTheDocument();
    expect(within(sharesBlock()).queryByText("burn is wrong")).not.toBeInTheDocument();
  });

  it("offers both actions and fires nothing on mount", () => {
    serve(company({ open: [flag()] }, { decisions: [dec()] }));
    renderPage();
    expect(within(sharesBlock()).getByRole("button", { name: "Close as fixed…" })).toBeEnabled();
    expect(within(sharesBlock()).getByRole("button", { name: "Close as kept…" })).toBeEnabled();
    expect(mutate(h.close)).not.toHaveBeenCalled();
    expect(mutate(h.reopen)).not.toHaveBeenCalled();
  });
});

describe("flags from users: Close as fixed", () => {
  it("prefills the newest standing approve or correct and sends it, with the optional note", async () => {
    const newer = dec({ id: DID(2), action: "approve", recorded_at: "2026-11-16T10:00:00+00:00" });
    const undone = dec({ id: DID(3), recorded_at: "2026-11-17T10:00:00+00:00", undone: true });
    const dismissal = dec({ id: DID(4), action: "dismiss", recorded_at: "2026-11-17T11:00:00+00:00" });
    serve(company({ open: [flag()] }, { decisions: [dismissal, undone, newer, dec()] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as fixed…" }));
    const form = within(sharesBlock()).getByTestId("cf-flag-close-form");
    expect(form).toHaveTextContent("Approved by steward@example.com on 2026-11-16");
    await user.type(within(form).getByLabelText("Note to the user (optional)"), "Corrected to the 10-Q cover.");
    await user.click(within(form).getByRole("button", { name: "Close as fixed" }));
    expect(mutate(h.close)).toHaveBeenCalledWith({
      securityId: SEC,
      flagId: FID(1),
      outcome: "fixed",
      decisionId: DID(2),
      note: "Corrected to the 10-Q cover.",
    });
  });

  it("sends no note when none is typed", async () => {
    serve(company({ open: [flag()] }, { decisions: [dec()] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as fixed…" }));
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as fixed" }));
    expect(mutate(h.close)).toHaveBeenCalledWith({
      securityId: SEC,
      flagId: FID(1),
      outcome: "fixed",
      decisionId: DID(1),
    });
  });

  it("is disabled with a quiet reason when the value has no standing approve or correct", () => {
    const onlyDismissOrUndone = [dec({ action: "dismiss" }), dec({ id: DID(5), undone: true })];
    serve(company({ open: [flag()] }, { decisions: onlyDismissOrUndone }));
    renderPage();
    const btn = within(sharesBlock()).getByRole("button", { name: "Close as fixed…" });
    expect(btn).toBeDisabled();
    expect(within(sharesBlock()).getByTestId("cf-flag-fixed-reason")).toHaveTextContent(
      "Correct the value first, then close the flag as fixed.",
    );
  });

  it("has a way back: Cancel closes the form and sends nothing", async () => {
    serve(company({ open: [flag()] }, { decisions: [dec()] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as fixed…" }));
    await user.click(within(sharesBlock()).getByRole("button", { name: "Cancel" }));
    expect(within(sharesBlock()).queryByTestId("cf-flag-close-form")).not.toBeInTheDocument();
    expect(mutate(h.close)).not.toHaveBeenCalled();
  });
});

describe("flags from users: Close as kept", () => {
  it("shows the hint, refuses a blank note on the client, and sends nothing", async () => {
    serve(company({ open: [flag()] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as kept…" }));
    const form = within(sharesBlock()).getByTestId("cf-flag-close-form");
    expect(form).toHaveTextContent("The user reads this note.");
    await user.type(within(form).getByLabelText("Note to the user (required)"), "   ");
    await user.click(within(form).getByRole("button", { name: "Close as kept" }));
    expect(mutate(h.close)).not.toHaveBeenCalled();
    expect(within(form).getByRole("alert")).toHaveTextContent("Closing a flag as kept needs a note for the user.");
  });

  it("sends the trimmed note and no decision", async () => {
    serve(company({ open: [flag()] }));
    renderPage();
    const user = userEvent.setup();
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as kept…" }));
    await user.type(
      within(sharesBlock()).getByLabelText("Note to the user (required)"),
      "  The filing states two classes.  ",
    );
    await user.click(within(sharesBlock()).getByRole("button", { name: "Close as kept" }));
    expect(mutate(h.close)).toHaveBeenCalledWith({
      securityId: SEC,
      flagId: FID(1),
      outcome: "kept",
      note: "The filing states two classes.",
    });
  });

  it("shows the server's own sentence when the server refuses the note", () => {
    const sentence = "Closing a flag as kept needs a note for the user. Nothing was saved.";
    h.close = { ...inert(), isError: true, error: { detail: sentence }, variables: { flagId: FID(1), securityId: SEC } };
    serve(company({ open: [flag()] }));
    renderPage();
    expect(within(sharesBlock()).getByTestId("cf-flag-open")).toHaveTextContent(sentence);
  });
});

describe("flags from users: every server refusal shows its own sentence, on the flag that made the call", () => {
  const closeSentences = [
    "There is no such flag.",
    "This flag is already closed.",
    "That decision is not a standing approve or correct of this company's value. Nothing was saved.",
    "A flag closed as kept names no decision. Nothing was saved.",
    "Closing a flag as fixed needs the decision that fixed it. Nothing was saved.",
  ];
  it.each(closeSentences)("close: %s", (sentence) => {
    h.close = { ...inert(), isError: true, error: { detail: sentence }, variables: { flagId: FID(2), securityId: SEC } };
    serve(company({ open: [flag({ id: FID(1) }), flag({ id: FID(2), note: "second" })] }));
    renderPage();
    const [first, second] = within(sharesBlock()).getAllByTestId("cf-flag-open");
    expect(second).toHaveTextContent(sentence);
    expect(first).not.toHaveTextContent(sentence);
  });

  const reopenSentences = [
    "This flag is still open, so there is nothing to reopen.",
    "This flag was already reopened. Reopen its newest closed flag instead.",
    "The user withdrew this flag. Only a flag you closed can be reopened.",
    "The user has a newer open flag on this value, so this one is not reopened.",
  ];
  it.each(reopenSentences)("reopen: %s", async (sentence) => {
    h.reopen = { ...inert(), isError: true, error: { detail: sentence }, variables: { flagId: FID(7), securityId: SEC } };
    serve(company({ closed: [closedFlag(7)] }));
    renderPage();
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    expect(within(sharesBlock()).getByTestId("cf-flag-closed")).toHaveTextContent(sentence);
  });

  it("shows the 409 company-busy sentence as given", () => {
    const busy = "Another writer is working on this company. Try again in a moment.";
    h.close = { ...inert(), isError: true, error: { detail: busy }, variables: { flagId: FID(1), securityId: SEC } };
    serve(company({ open: [flag()] }));
    renderPage();
    expect(within(sharesBlock()).getByRole("alert")).toHaveTextContent(busy);
  });
});

describe("flags from users: the page follows the server's answer", () => {
  it("a close answer that carries the flag as closed shows it gray under History and drops the loud block", async () => {
    serve(company({ open: [flag()] }));
    const { rerender } = renderPage();
    expect(within(sharesBlock()).getByTestId("cf-flags")).toBeInTheDocument();
    serve(company({ open: [], closed: [closedFlag(1)] }));
    rerender(
      <MemoryRouter>
        <CompanyFacts securityId={SEC} asof={null} />
      </MemoryRouter>,
    );
    expect(within(sharesBlock()).queryByTestId("cf-flags")).not.toBeInTheDocument();
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    const row = within(sharesBlock()).getByTestId("cf-flag-closed");
    expect(row).toHaveTextContent("Closed as kept by steward@example.com on 2026-11-15");
    expect(row).toHaveClass("cf-undone");
  });
});

describe("flags from users: closed flags under History", () => {
  const open = async () => {
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: /History/ }));
  };

  it("lists closed flags newest first as the server gave them, with outcome, note, who and when", async () => {
    serve(
      company({
        closed: [
          closedFlag(1, { closed_at: "2026-11-16T10:00:00+00:00", outcome_note: "newest note" }),
          closedFlag(2, { closed_at: "2026-11-10T10:00:00+00:00", outcome_note: "older note" }),
        ],
      }),
    );
    renderPage();
    expect(within(sharesBlock()).queryByTestId("cf-flag-closed")).not.toBeInTheDocument();
    await open();
    const rows = within(sharesBlock()).getAllByTestId("cf-flag-closed");
    expect(rows[0]).toHaveTextContent("newest note");
    expect(rows[1]).toHaveTextContent("older note");
    expect(rows[0]).toHaveTextContent("Closed as kept by steward@example.com on 2026-11-16");
    expect(rows[0]).toHaveTextContent("pat@example.com");
    expect(rows[0]).toHaveClass("cf-undone");
  });

  it("says fixed with the linked correction, and 'linked correction since undone' when it was", async () => {
    serve(
      company({
        closed: [
          closedFlag(1, { outcome: "fixed", outcome_note: null, fixed_decision: dec() }),
          closedFlag(2, { outcome: "fixed", outcome_note: null, fixed_decision: dec({ undone: true }), fixed_decision_undone: true }),
        ],
      }),
    );
    renderPage();
    await open();
    const [a, b] = within(sharesBlock()).getAllByTestId("cf-flag-closed");
    expect(a).toHaveTextContent("Closed as fixed by steward@example.com on 2026-11-15");
    expect(a).toHaveTextContent("Corrected by steward@example.com on 2026-11-14");
    expect(a).not.toHaveTextContent("linked correction since undone");
    expect(b).toHaveTextContent("linked correction since undone");
  });

  it("says a user's own withdrawal as such", async () => {
    serve(company({ closed: [closedFlag(1, { outcome: "withdrawn", closed_by: "user", outcome_note: null, can_reopen: false, reopen_refusal: "The user withdrew this flag. Only a flag you closed can be reopened." })] }));
    renderPage();
    await open();
    expect(within(sharesBlock()).getByTestId("cf-flag-closed")).toHaveTextContent("Withdrawn by the user on 2026-11-15");
  });

  it("renders Reopen when can_reopen, and posts it", async () => {
    serve(company({ closed: [closedFlag(1)] }));
    renderPage();
    await open();
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: "Reopen" }));
    expect(mutate(h.reopen)).toHaveBeenCalledWith({ securityId: SEC, flagId: FID(1) });
  });

  it("replaces Reopen with the server's reason as quiet text when it cannot", async () => {
    const why = "This flag was already reopened. Reopen its newest closed flag instead.";
    serve(company({ closed: [closedFlag(1, { can_reopen: false, reopen_refusal: why, reopened_as: FID(9) })] }));
    renderPage();
    await open();
    const row = within(sharesBlock()).getByTestId("cf-flag-closed");
    expect(within(row).queryByRole("button", { name: "Reopen" })).not.toBeInTheDocument();
    expect(within(row).getByText(why)).toHaveClass("cf-quiet");
  });

  it("disables Reopen while a write is pending", async () => {
    h.close = { ...inert(), isPending: true };
    serve(company({ closed: [closedFlag(1)] }));
    renderPage();
    await open();
    expect(within(sharesBlock()).getByRole("button", { name: "Reopen" })).toBeDisabled();
  });

  it("notes that a reopened open flag was reopened by the steward", () => {
    serve(company({ open: [flag({ id: FID(5), reopens: FID(1), reopened_by: STEWARD, opened_at: "2026-11-16T09:00:00+00:00" })] }));
    renderPage();
    expect(within(sharesBlock()).getByTestId("cf-flag-open")).toHaveTextContent(
      "Reopened by steward@example.com on 2026-11-16",
    );
  });
});

describe("flags from users: older closed flags page in", () => {
  const open = async () => {
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: /History/ }));
  };

  it("shows no 'Show older' when every closed flag is loaded", async () => {
    serve(company({ closed: [closedFlag(1)], closed_total: 1 }));
    renderPage();
    await open();
    expect(screen.queryByRole("button", { name: "Show older" })).not.toBeInTheDocument();
  });

  it("shows 'Show older' from closed_total, asks for the next page, and lists what arrives after the first page", async () => {
    serve(company({ closed: [closedFlag(1, { outcome_note: "first page" })], closed_total: 3, closed_limit: 1 }));
    renderPage();
    await open();
    expect(h.olderArgs.at(-1)).toMatchObject({ securityId: SEC, extraPages: 0, limit: 1 });
    expect(screen.getAllByText(/1 of 3 closed flags shown/).length).toBeGreaterThan(0);
    h.older = { rows: [closedFlag(2, { outcome_note: "second page" })], isFetching: false, isError: false, error: null };
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: "Show older" }));
    expect(h.olderArgs.at(-1)).toMatchObject({ extraPages: 1, limit: 1 });
    const rows = within(sharesBlock()).getAllByTestId("cf-flag-closed");
    expect(rows[0]).toHaveTextContent("first page");
    expect(rows[1]).toHaveTextContent("second page");
  });

  it("never lists a flag twice when a page overlaps", async () => {
    serve(company({ closed: [closedFlag(1)], closed_total: 2, closed_limit: 1 }));
    h.older = { rows: [closedFlag(1), closedFlag(2, { outcome_note: "other" })], isFetching: false, isError: false, error: null };
    renderPage();
    await open();
    expect(within(sharesBlock()).getAllByTestId("cf-flag-closed")).toHaveLength(2);
  });

  it("shows the server's sentence when a page cannot be read", async () => {
    serve(company({ closed: [closedFlag(1)], closed_total: 5, closed_limit: 1 }));
    h.older = { rows: [], isFetching: false, isError: true, error: { detail: "Could not read older flags." } };
    renderPage();
    await open();
    expect(within(sharesBlock()).getByText("Could not read older flags.")).toBeInTheDocument();
  });
});

describe("flags from users: user-written text is plain text", () => {
  const hostile = '<img src=x onerror="alert(1)"> <b>bold</b> [click](https://evil.example/x) https://evil.example/y';

  it("renders an email, a note and an outcome note as text, never as markup or links", async () => {
    serve(
      company({
        open: [flag({ user_email: "<i>p</i>@example.com", note: hostile })],
        closed: [closedFlag(2, { note: hostile, outcome_note: hostile })],
      }),
    );
    renderPage();
    await userEvent.setup().click(within(sharesBlock()).getByRole("button", { name: /History/ }));
    const block = sharesBlock();
    expect(block.querySelector("img")).toBeNull();
    expect(block.querySelector("b > b, i")).toBeNull();
    expect(within(block).getAllByText(hostile, { exact: false }).length).toBeGreaterThanOrEqual(3);
    expect(block).toHaveTextContent("<i>p</i>@example.com");
    for (const a of Array.from(block.querySelectorAll("a"))) {
      expect(a.getAttribute("href") ?? "").not.toContain("evil.example");
    }
    expect(block.querySelectorAll("a[href*='evil']").length).toBe(0);
  });
});
