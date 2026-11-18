import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the user's read of the shared values and the two flag mutations. The block, the
// Flag control, the outcome line and the words are the REAL components under test.
const h = vi.hoisted(() => ({
  company: { data: undefined as unknown, isLoading: false, error: null as unknown },
  raise: vi.fn(),
  withdraw: vi.fn(),
  raiseState: { isPending: false, isError: false, error: null as unknown },
  withdrawState: { isPending: false, isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useUserCompanyValues: () => h.company,
  useRaiseFlag: () => ({ mutate: h.raise, reset: vi.fn(), ...h.raiseState }),
  useWithdrawFlag: () => ({ mutate: h.withdraw, reset: vi.fn(), ...h.withdrawState }),
}));

import { MeProvider } from "../../auth/me";
import { ClockProvider } from "../../clock";
import { SharedValues } from "../SharedValues";
import {
  SID,
  cashAwaiting,
  cashVerified,
  company,
  noValue,
  openFlag,
  sharesAnnual,
  sharesFromFiling,
} from "./userValues.fixtures";

const me = (role: "user" | "super_admin") => ({
  id: "u-1",
  email: "someone@example.test",
  display_name: "Someone Test",
  role,
  workspace_id: "00000000-0000-0000-0000-0000000000a1",
});

const clock = (marketToday = "2026-11-17") => ({
  currentNight: marketToday,
  marketToday,
  actionDay: null,
  failed: false,
});

function renderBlock(
  opts: { role?: "user" | "super_admin"; today?: string; asof?: string } = {},
) {
  const today = opts.today ?? "2026-11-17";
  return render(
    <MemoryRouter>
      <ClockProvider value={clock(today)}>
        <MeProvider value={me(opts.role ?? "user")}>
          <SharedValues securityId={SID} asof={opts.asof ?? today} />
        </MeProvider>
      </ClockProvider>
    </MemoryRouter>,
  );
}

const set = (shares: unknown, cash: unknown) => {
  h.company.data = company(shares as Record<string, unknown>, cash as Record<string, unknown>);
};
const shares = () => screen.getByTestId("sv-shares_outstanding");
const cash = () => screen.getByTestId("sv-cash_burn");

beforeEach(() => {
  h.raise.mockReset();
  h.withdraw.mockReset();
  h.company = { data: undefined, isLoading: false, error: null };
  h.raiseState = { isPending: false, isError: false, error: null };
  h.withdrawState = { isPending: false, isError: false, error: null };
});

describe("SharedValues — the words of each state", () => {
  it("a value from the filing: the count, its filing and date, as a link", () => {
    set(sharesFromFiling(), noValue("cash_burn"));
    renderBlock();
    expect(screen.getByText("Shared values")).toBeInTheDocument();
    expect(shares()).toHaveTextContent("Share count");
    expect(shares()).toHaveTextContent("52,113,000 shares");
    expect(shares()).toHaveTextContent("from the 10-Q dated Sep 30, 2026");
    const link = within(shares()).getByRole("link", { name: /10-Q dated Sep 30, 2026/ });
    expect(link).toHaveAttribute("href", expect.stringContaining("sec.gov"));
  });

  it("an annual figure says so, with its cover date", () => {
    set(sharesAnnual(), noValue("cash_burn"));
    renderBlock();
    expect(shares()).toHaveTextContent("143,310,556 shares");
    expect(shares()).toHaveTextContent("annual figure from the 20-F or 40-F cover, as of Mar 31, 2026");
  });

  it("verified cash: the composition, the burn, who verified it, the pace line", () => {
    set(sharesFromFiling(), cashVerified());
    renderBlock();
    expect(cash()).toHaveTextContent(
      "$890,000,000 cash and short-term investments (of which short-term investments $120,000,000)",
    );
    expect(cash()).toHaveTextContent("quarterly burn $31,400,000");
    expect(cash()).toHaveTextContent("verified by the steward");
    expect(cash()).toHaveTextContent("10-Q dated Sep 30, 2026");
    expect(within(cash()).getByTestId("sv-pace")).toHaveTextContent(
      "Year-to-date pace: quarterly burn $29,000,000 (9.2 months of runway)",
    );
  });

  it("a pace caveat shows only when the server sent one", () => {
    const withCaveat = cashVerified();
    (withCaveat.value as { pace: { caveat: string | null } }).pace.caveat =
      "Last quarter's burn is far off the year-to-date pace.";
    set(sharesFromFiling(), withCaveat);
    renderBlock();
    expect(within(cash()).getByTestId("sv-pace-caveat")).toHaveTextContent("far off the year-to-date pace");
  });

  it("a non-dollar value is labeled in its own currency (the one rule), never as dollars", () => {
    const v = cashVerified();
    Object.assign(v.value, { currency: "CNY", currency_symbol: "CN¥", pace: null });
    set(sharesFromFiling(), v);
    renderBlock();
    expect(cash()).toHaveTextContent("CN¥890,000,000 cash");
    expect(cash()).not.toHaveTextContent("$890");
  });

  it("a value carried over from V1 says so", () => {
    set(
      sharesFromFiling({ state: "verified", label: "Verified (carried over from V1)", set_by: "carried" }),
      noValue("cash_burn"),
    );
    renderBlock();
    expect(shares()).toHaveTextContent("verified (carried over from V1)");
    expect(shares()).toHaveTextContent("52,113,000 shares");
  });

  it("awaiting the steward: no number, the filing link and date, the passage behind a toggle", async () => {
    const user = userEvent.setup();
    set(sharesFromFiling(), cashAwaiting());
    renderBlock();
    expect(cash()).toHaveTextContent("Awaiting the steward");
    expect(cash()).toHaveTextContent("Runway is not shown until the steward checks it.");
    expect(cash()).toHaveTextContent("filing dated Jul 26, 2026");
    expect(within(cash()).getByRole("link", { name: /Jul 26, 2026/ })).toHaveAttribute(
      "href",
      expect.stringContaining("sec.gov"),
    );
    // NO figure of any kind from the unverified candidate, and the passage stays collapsed until asked
    expect(cash().textContent).not.toMatch(/\d{1,3}(,\d{3}){2,}/);
    expect(within(cash()).queryByText(/Cash and short-term investments 69,343/)).not.toBeInTheDocument();
    await user.click(within(cash()).getByRole("button", { name: "Show the filing passage" }));
    expect(within(cash()).getByText(/Cash and short-term investments 69,343/)).toBeInTheDocument();
    await user.click(within(cash()).getByRole("button", { name: "Hide the filing passage" }));
    expect(within(cash()).queryByText(/Cash and short-term investments 69,343/)).not.toBeInTheDocument();
  });

  it("no value yet: the reason in words, quiet (not an alert, not a warning)", () => {
    set(noValue("shares_outstanding"), noValue("cash_burn", { reason: "no-annual-filing", label: "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR" }));
    renderBlock();
    expect(shares()).toHaveTextContent("No value yet: the platform has not read this company's filings yet.");
    expect(cash()).toHaveTextContent("No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(shares().className).not.toMatch(/loud|err|warn/);
  });

  it("an applied value with a newer filing waiting KEEPS the value and adds the gray line", () => {
    set(
      sharesFromFiling({
        pending: {
          event_date: "2026-12-31",
          source_url: "https://www.sec.gov/Archives/edgar/data/1/000000000027000001/q4.htm",
          line: "A newer filing (dated Dec 31, 2026) is awaiting the steward",
        },
      }),
      noValue("cash_burn"),
    );
    renderBlock();
    expect(shares()).toHaveTextContent("52,113,000 shares");
    expect(shares()).toHaveTextContent("A newer filing (dated Dec 31, 2026) is awaiting the steward.");
    expect(shares()).not.toHaveTextContent("Awaiting the steward");
  });

  it("the block is labeled current; a dated view says it shows today's state", () => {
    set(sharesFromFiling(), noValue("cash_burn"));
    const { unmount } = renderBlock();
    expect(screen.getByText(/current/i)).toBeInTheDocument();
    expect(screen.queryByText(/not the state on/i)).not.toBeInTheDocument();
    unmount();
    renderBlock({ today: "2026-11-17", asof: "2026-06-01" });
    expect(screen.getByText(/the current state, not the state on Jun 1/i)).toBeInTheDocument();
  });

  it("reads quietly while it loads and when it cannot be read", () => {
    h.company = { data: undefined, isLoading: true, error: null };
    const { unmount } = renderBlock();
    expect(screen.queryByTestId("sv-shares_outstanding")).not.toBeInTheDocument();
    unmount();
    h.company = { data: undefined, isLoading: false, error: { detail: "There is no such company." } };
    renderBlock();
    expect(screen.getByText("There is no such company.")).toBeInTheDocument();
  });
});

describe("SharedValues — the Flag", () => {
  it("opens a one-line box with Send and Cancel and the gray note; Cancel closes it (the inverse)", async () => {
    const user = userEvent.setup();
    set(sharesFromFiling(), noValue("cash_burn"));
    renderBlock();
    expect(screen.queryByPlaceholderText("What looks wrong? (optional)")).not.toBeInTheDocument();
    await user.click(within(shares()).getByRole("button", { name: "Flag" }));
    expect(within(shares()).getByPlaceholderText("What looks wrong? (optional)")).toBeInTheDocument();
    expect(within(shares()).getByText("Only you and the steward see this. It does not change the value.")).toBeInTheDocument();
    await user.click(within(shares()).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByPlaceholderText("What looks wrong? (optional)")).not.toBeInTheDocument();
    expect(h.raise).not.toHaveBeenCalled();
  });

  it("Send posts the company, the value type and the trimmed note", async () => {
    const user = userEvent.setup();
    set(sharesFromFiling(), noValue("cash_burn"));
    renderBlock();
    await user.click(within(shares()).getByRole("button", { name: "Flag" }));
    await user.type(within(shares()).getByPlaceholderText("What looks wrong? (optional)"), "  two classes on the cover ");
    await user.click(within(shares()).getByRole("button", { name: "Send to the steward" }));
    expect(h.raise).toHaveBeenCalledTimes(1);
    expect(h.raise.mock.calls[0][0]).toEqual({
      security_id: SID,
      value_type: "shares_outstanding",
      note: "two classes on the cover",
    });
  });

  it("a flag with no note sends none; a 'no value yet' value can be flagged too", async () => {
    const user = userEvent.setup();
    set(noValue("shares_outstanding"), noValue("cash_burn"));
    renderBlock();
    await user.click(within(cash()).getByRole("button", { name: "Flag" }));
    await user.click(within(cash()).getByRole("button", { name: "Send to the steward" }));
    expect(h.raise.mock.calls[0][0]).toEqual({ security_id: SID, value_type: "cash_burn" });
  });

  it("after flagging: the dated line and Withdraw; Withdraw posts the flag's id", async () => {
    const user = userEvent.setup();
    set(sharesFromFiling({ my_flag: openFlag({ id: "f-123", note: "two classes" }) }), noValue("cash_burn"));
    renderBlock();
    expect(shares()).toHaveTextContent("You flagged this on Nov 16. The steward will look at it.");
    expect(within(shares()).queryByRole("button", { name: "Flag" })).not.toBeInTheDocument();
    await user.click(within(shares()).getByRole("button", { name: "Withdraw" }));
    expect(h.withdraw).toHaveBeenCalledTimes(1);
    expect(h.withdraw.mock.calls[0][0]).toEqual({ flagId: "f-123" });
  });

  it("after a withdrawal: 'Withdrawn. You can flag it again.' and the Flag link is back", () => {
    set(
      sharesFromFiling({
        my_flag: openFlag({ closed_at: "2026-11-17T01:00:00Z", outcome: "withdrawn" }),
      }),
      noValue("cash_burn"),
    );
    renderBlock();
    expect(shares()).toHaveTextContent("Withdrawn. You can flag it again.");
    expect(within(shares()).getByRole("button", { name: "Flag" })).toBeInTheDocument();
  });

  it.each([
    ["404", "This company is not on any of your cards. Add it to a card first."],
    ["409", "You already have an open flag on this value. The steward will look at it."],
    ["429", "Daily allowance reached: 50 flags a day. It resets at midnight America/New_York time."],
    ["403", "The steward corrects values on the company page. A flag is for users."],
  ])("a %s refusal shows the server's own sentence", async (_status, sentence) => {
    const user = userEvent.setup();
    set(sharesFromFiling(), noValue("cash_burn"));
    h.raiseState = { isPending: false, isError: true, error: { detail: sentence } };
    renderBlock();
    await user.click(within(shares()).getByRole("button", { name: "Flag" }));
    expect(within(shares()).getByText(sentence)).toBeInTheDocument();
  });

  it.each([
    ["404", "There is no such flag."],
    ["409", "This flag is already closed."],
  ])("a withdraw %s refusal shows the server's own sentence", (_status, sentence) => {
    set(sharesFromFiling({ my_flag: openFlag() }), noValue("cash_burn"));
    h.withdrawState = { isPending: false, isError: true, error: { detail: sentence } };
    renderBlock();
    expect(within(shares()).getByText(sentence)).toBeInTheDocument();
  });
});

describe("SharedValues — the steward's outcome", () => {
  const closed = (outcome: string, note: string | null, closedAt = "2026-11-18T15:00:00Z") =>
    sharesFromFiling({
      my_flag: openFlag({ closed_at: closedAt, outcome, outcome_note: note }),
    });

  it("fixed: loud for 14 days after the close", () => {
    set(closed("fixed", "corrected to the 10-Q cover total"), noValue("cash_burn"));
    renderBlock({ today: "2026-12-02" }); // 14 days after Nov 18
    const o = within(shares()).getByTestId("sv-outcome");
    expect(o).toHaveTextContent("The steward fixed this on Nov 18: corrected to the 10-Q cover total.");
    expect(o).toHaveAttribute("data-loud", "true");
  });

  it("kept: the same words, then quiet from day 15 (computed from closed_at, nothing stored)", () => {
    set(closed("kept", "the filing states two classes; this is the total"), noValue("cash_burn"));
    renderBlock({ today: "2026-12-03" });
    const o = within(shares()).getByTestId("sv-outcome");
    expect(o).toHaveTextContent(
      "The steward kept this value on Nov 18: the filing states two classes; this is the total.",
    );
    expect(o).toHaveAttribute("data-loud", "false");
  });

  it("a closed flag leaves the Flag link available (a new flag can be raised)", () => {
    set(closed("kept", "fine as is"), noValue("cash_burn"));
    renderBlock();
    expect(within(shares()).getByRole("button", { name: "Flag" })).toBeInTheDocument();
  });
});

describe("SharedValues — notes are plain text", () => {
  const HOSTILE =
    '<img src=x onerror="alert(1)"> <a href="http://evil.test/x">click me</a> [md](http://evil.test/y) **bold** javascript:alert(1)';

  const noMarkup = (container: HTMLElement) => {
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href*="evil.test"]')).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
  };

  it("the user's own note on an open flag renders as text, never as markup or links", () => {
    set(sharesFromFiling({ my_flag: openFlag({ note: HOSTILE }) }), noValue("cash_burn"));
    const { container } = renderBlock();
    noMarkup(container);
    expect(screen.getByText(/<img src=x onerror="alert\(1\)">/)).toBeInTheDocument();
    expect(screen.getByText(/\[md\]\(http:\/\/evil\.test\/y\)/)).toBeInTheDocument();
  });

  it("the steward's outcome note renders as text, never as markup or links", () => {
    set(
      sharesFromFiling({
        my_flag: openFlag({ closed_at: "2026-11-18T15:00:00Z", outcome: "kept", outcome_note: HOSTILE }),
      }),
      noValue("cash_burn"),
    );
    const { container } = renderBlock();
    noMarkup(container);
    expect(screen.getByTestId("sv-outcome")).toHaveTextContent(`The steward kept this value on Nov 18: ${HOSTILE}.`);
  });
});

describe("SharedValues — the steward", () => {
  it("sees the same read-only block and the link to the steward's page: no Flag, no form", () => {
    set(sharesFromFiling(), cashAwaiting());
    renderBlock({ role: "super_admin" });
    expect(shares()).toHaveTextContent("52,113,000 shares");
    expect(screen.queryByRole("button", { name: "Flag" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open on the steward's page/ });
    expect(link).toHaveAttribute("href", `/admin/facts/${SID}`);
  });

  it("a plain user sees the Flag on each block and no steward link", () => {
    set(sharesFromFiling(), noValue("cash_burn"));
    renderBlock({ role: "user" });
    expect(screen.getAllByRole("button", { name: "Flag" })).toHaveLength(2);
    expect(screen.queryByRole("link", { name: /Open on the steward's page/ })).not.toBeInTheDocument();
  });
});
