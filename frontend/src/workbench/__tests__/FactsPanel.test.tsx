import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the extract query + the ratify mutation. V2 W5e: the panel is PURITY ONLY, the user's
// own to extract, propose, confirm. A share count and cash are the platform's (the Shared values block, read-only);
// the panel offers no field, no Confirm and no note for them, for anyone.
const h = vi.hoisted(() => ({
  refetch: vi.fn(),
  extract: { data: undefined as unknown, error: null as unknown, isFetching: false },
  mutate: vi.fn(),
  ratify: { isPending: false, isError: false, isSuccess: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ ...h.extract, refetch: h.refetch }),
  useRatifyFact: () => ({ mutate: h.mutate, ...h.ratify }),
}));

import { MeProvider } from "../../auth/me";
import { FactsPanel } from "../FactsPanel";

const AUTO_SHARES = {
  fact_type: "shares_outstanding",
  tier: "auto",
  source: "10-q-cover",
  source_ref: "https://sec.gov/oklo-10q",
  event_date: "2026-03-31",
  note: "",
  value: 141000000,
  flags: [],
  located_passages: [],
};

const FLAG_BURN = {
  fact_type: "cash_burn",
  tier: "flag",
  source: "10-q-cashflow",
  source_ref: "https://sec.gov/smr-10q",
  event_date: "2026-03-31",
  note: "raw burn includes a one-time ENTRA1 partnership-milestone payment",
  cash_usd: 890000000,
  quarterly_burn_usd: 314678000,
  flags: ["one_time_in_burn"],
  located_passages: [
    {
      kind: "cash-flow-line",
      source_ref: "https://sec.gov/smr-10q#p1",
      anchor: "264,195",
      excerpt: "Partnership milestone payment of 264,195 (in thousands) to ENTRA1 ...",
    },
  ],
};

const HUMAN_PURITY = {
  fact_type: "revenue_mix",
  tier: "human",
  source: "10-k-business-description",
  source_ref: "https://sec.gov/smr-10k",
  event_date: "2025-12-31",
  note: "",
  flags: [],
  located_passages: [
    {
      kind: "item-1",
      source_ref: "https://sec.gov/smr-10k#item1",
      anchor: "Business",
      excerpt: "We are a pre-revenue nuclear technology company ...",
    },
  ],
};

const PURITY_EST = {
  fact_type: "revenue_mix",
  tier: "human",
  source: "10-k-segment",
  source_ref: "https://sec.gov/bwxt-10k#seg",
  event_date: "2025-12-31",
  note: "LLM-PROPOSED purity (UNVERIFIED — confirm or override): Commercial Operations $853,070 of $3,198,425 total. [on-thesis segment: Commercial Operations]. Grounded in the located segment passage.",
  value: 26.7,
  estimate_source: "llm_proposed",
  flags: [],
  located_passages: [
    {
      kind: "segment",
      source_ref: "https://sec.gov/bwxt-10k#seg",
      anchor: "reportable segment",
      excerpt: "… Commercial Operations 853,070 … $ 3,198,425 …",
    },
  ],
};

const SID = "00000000-0000-0000-0000-000000000abc";

// the wire shape is the ExtractionResult ENVELOPE: facts + the honest empty reason
const env = (facts: unknown[], empty_reason: string | null = null) => ({ facts, empty_reason });

const me = (role: "user" | "super_admin") => ({
  id: "u-1",
  email: "someone@example.test",
  display_name: "Someone Test",
  role,
  workspace_id: "00000000-0000-0000-0000-0000000000a1",
});

beforeEach(() => {
  h.refetch.mockReset();
  h.mutate.mockReset();
  h.extract = { data: undefined, error: null, isFetching: false };
  h.ratify = { isPending: false, isError: false, isSuccess: false, error: null };
});

describe("FactsPanel — purity only", () => {
  it("offers no shares or cash field and no 'Set by the steward' line, for a plain user or the steward", () => {
    for (const role of ["user", "super_admin"] as const) {
      h.extract.data = env([AUTO_SHARES, FLAG_BURN, HUMAN_PURITY]);
      const { unmount } = render(
        <MeProvider value={me(role)}>
          <FactsPanel securityId={SID} />
        </MeProvider>,
      );
      expect(screen.queryByLabelText("shares")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("cash")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("quarterly burn")).not.toBeInTheDocument();
      expect(screen.queryByText("Set by the steward.")).not.toBeInTheDocument();
      expect(screen.queryByText(/market cap · shares|runway · cash/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Partnership milestone payment/)).not.toBeInTheDocument();
      // the one row left is purity, with its own Confirm
      expect(screen.getAllByRole("button", { name: "Confirm" })).toHaveLength(1);
      expect(screen.getByLabelText("purity percent")).toBeInTheDocument();
      unmount();
    }
  });

  it("says nothing of the share-count and runway chores (annual-filer notes, auto-applied tag, cover reads)", () => {
    h.extract.data = env(
      [{ ...AUTO_SHARES, source: "annual-cover" }, { ...FLAG_BURN, source: "annual-statements" }],
      "cash-generative",
    );
    h.extract.data = { ...(h.extract.data as object), runway_empty_reason: "cash-generative" };
    render(<FactsPanel securityId={SID} />);
    expect(screen.queryByText(/Annual-filer coverage/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cash-generative/)).not.toBeInTheDocument();
    expect(screen.queryByText(/auto-applied/)).not.toBeInTheDocument();
  });

  it("the explainer's FLAG row is gone with the shares and cash rows", () => {
    h.extract.data = env([FLAG_BURN, HUMAN_PURITY]);
    render(<FactsPanel securityId={SID} />);
    expect(screen.queryByRole("button", { name: /Explain in plain English/ })).not.toBeInTheDocument();
  });

  it("extract fires on the explicit click, NOT on mount", async () => {
    render(<FactsPanel securityId={SID} />);
    expect(h.refetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Extract from filings/ }));
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("an EMPTY extract with nothing on EDGAR says so; an unread cover says nothing here (that was the count's)", () => {
    h.extract.data = env([], "no-annual-filing");
    const { unmount } = render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/Nothing on EDGAR the extractor can read/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    unmount();

    h.extract.data = env([], "cover-not-located");
    render(<FactsPanel securityId={SID} />);
    expect(screen.queryByText(/unread, not empty/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing on EDGAR/)).not.toBeInTheDocument();
  });

  it("an annual filer's purity-not-covered says purity is set by hand from its annual report (W7a)", () => {
    h.extract.data = env([], "purity-not-covered");
    render(<FactsPanel securityId={SID} />);
    expect(
      screen.getByText(
        "Purity is not read automatically for a company that files annual reports on Form 20-F or 40-F. " +
          "Set it by hand from its annual report.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Nothing on EDGAR/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});

describe("FactsPanel — purity (yours to set), unchanged", () => {
  it("a HUMAN purity confirm requires an operator-entered % (no pre-fill) and posts it", async () => {
    const user = userEvent.setup();
    h.extract.data = env([HUMAN_PURITY]);
    render(<FactsPanel securityId={SID} />);

    expect((screen.getByLabelText("segment") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("purity percent") as HTMLInputElement).value).toBe("");
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("segment"), "nuclear");
    expect(confirm).toBeDisabled(); // a segment alone is not enough
    await user.type(screen.getByLabelText("purity percent"), "100");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      fact_type: "revenue_mix",
      security_id: SID,
      segment_label: "nuclear",
      mix_pct: 100,
    });
  });

  it("the located passage is readable INLINE (reading it is the decision)", () => {
    h.extract.data = env([HUMAN_PURITY]);
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/pre-revenue nuclear technology company/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /item-1/ })).toHaveAttribute("href", "https://sec.gov/smr-10k#item1");
  });

  it("a LONG passage renders CLAMPED with an explicit expand; a SHORT one has no expand", async () => {
    const user = userEvent.setup();
    h.extract.data = env([
      {
        ...HUMAN_PURITY,
        located_passages: [
          {
            kind: "segment",
            source_ref: "https://sec.gov/mu-10k#seg",
            anchor: "segment",
            excerpt: "Revenue by segment " + "104 414 176 137 920 restructure ".repeat(30),
          },
        ],
      },
    ]);
    const { unmount } = render(<FactsPanel securityId={SID} />);
    const excerpt = screen.getByText(/Revenue by segment/);
    expect(excerpt).toHaveClass("clamped");
    await user.click(screen.getByRole("button", { name: /show the full passage/ }));
    expect(excerpt).not.toHaveClass("clamped");
    await user.click(screen.getByRole("button", { name: /collapse the passage/ }));
    expect(excerpt).toHaveClass("clamped");
    unmount();

    h.extract.data = env([HUMAN_PURITY]);
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/pre-revenue nuclear/)).not.toHaveClass("clamped");
    expect(screen.queryByRole("button", { name: /show the full passage/ })).not.toBeInTheDocument();
  });

  it("an empty purity names WHY it's empty: pre-revenue vs couldn't-ground (missing ≠ blank)", () => {
    h.extract.data = env([HUMAN_PURITY]); // source: 10-k-business-description
    const { unmount } = render(<FactsPanel securityId={SID} thesisId="t-1" />);
    expect(screen.getByText(/no revenue data on file — pre-revenue/)).toBeInTheDocument();
    unmount();

    h.extract.data = env([{ ...HUMAN_PURITY, source: "10-k-segment" }]);
    render(<FactsPanel securityId={SID} thesisId="t-1" />);
    expect(screen.getByText(/couldn't ground a purity estimate/)).toBeInTheDocument();
  });

  it("the note is a growable TEXTAREA seeded with the basis", async () => {
    const user = userEvent.setup();
    h.extract.data = env([{ ...HUMAN_PURITY, note: "basis: the business description" }]);
    render(<FactsPanel securityId={SID} />);
    const note = screen.getByLabelText("note") as HTMLTextAreaElement;
    expect(note.tagName).toBe("TEXTAREA");
    expect(note.value).toContain("the business description");
    await user.type(note, " — confirmed");
    expect(note.value).toContain("confirmed");
  });

  it("re-entry shows the RATIFIED purity, not the stale LLM rec, and tags it on file (the reversion bug)", () => {
    h.extract.data = env([
      {
        ...HUMAN_PURITY,
        value: 72,
        estimate_source: "llm_proposed",
        note: "LLM-PROPOSED purity (UNVERIFIED — confirm or override): x [on-thesis segment: HBM]. Grounded.",
      },
    ]);
    render(
      <FactsPanel
        securityId={SID}
        thesisId="t-1"
        onFile={{ revenue_mix: { mix_pct: 85, segment_label: "nuclear", note: "operator basis" } }}
      />,
    );
    expect(screen.getByLabelText("purity percent")).toHaveValue(85);
    expect(screen.getByLabelText("segment")).toHaveValue("nuclear");
    expect(screen.getByLabelText("note")).toHaveValue("operator basis");
    expect(screen.queryByText(/llm-proposed · unverified/)).not.toBeInTheDocument();
    expect(screen.getByText("✓ on file")).toBeInTheDocument();
  });

  it("re-seeds when the selected member changes (no cross-member stale value)", () => {
    const SID_A = "00000000-0000-0000-0000-00000000aaaa";
    const SID_B = "00000000-0000-0000-0000-00000000bbbb";
    h.extract.data = env([{ ...PURITY_EST, value: 26.7 }]);
    const { rerender } = render(<FactsPanel securityId={SID_A} thesisId="t" />);
    expect(screen.getByLabelText("purity percent")).toHaveValue(26.7);
    h.extract = { data: env([{ ...PURITY_EST, value: 61 }]), error: null, isFetching: false };
    rerender(<FactsPanel securityId={SID_B} thesisId="t" />);
    expect(screen.getByLabelText("purity percent")).toHaveValue(61);
  });

  it("the grounded estimate: tag, % + segment pre-filled, confirm as-is WITH the estimate", async () => {
    h.extract.data = env([PURITY_EST]);
    const user = userEvent.setup();
    render(<FactsPanel securityId={SID} thesisId="t-nuke" />);
    expect(screen.getByText(/estimate 26\.7% · llm-proposed · unverified/)).toBeInTheDocument();
    expect((screen.getByLabelText("purity percent") as HTMLInputElement).value).toBe("26.7");
    expect((screen.getByLabelText("segment") as HTMLInputElement).value).toBe("Commercial Operations");
    expect(screen.getByText(/Commercial Operations 853,070/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      fact_type: "revenue_mix",
      segment_label: "Commercial Operations",
      mix_pct: 26.7,
      estimate: 26.7,
    });
  });

  it("an override sends the shown estimate but a different % (→ vouched=overridden)", async () => {
    h.extract.data = env([PURITY_EST]);
    const user = userEvent.setup();
    render(<FactsPanel securityId={SID} thesisId="t-nuke" />);
    const pct = screen.getByLabelText("purity percent");
    await user.clear(pct);
    await user.type(pct, "100");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({ mix_pct: 100, estimate: 26.7 });
  });

  it("a ratified purity says so, and the meter re-derives", () => {
    h.extract.data = env([PURITY_EST]);
    h.ratify = { isPending: false, isError: false, isSuccess: true, error: null };
    render(<FactsPanel securityId={SID} thesisId="t-nuke" />);
    expect(screen.getByText(/purity ratified/)).toBeInTheDocument();
  });

  it("a plain user and the steward both keep purity's Confirm (it is every user's own)", () => {
    for (const role of ["user", "super_admin"] as const) {
      h.extract.data = env([HUMAN_PURITY]);
      const { unmount } = render(
        <MeProvider value={me(role)}>
          <FactsPanel securityId={SID} />
        </MeProvider>,
      );
      expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
      unmount();
    }
  });
});

// security hardening PR 1b: the API's fixed refusals on a fetch read on their own line, as the API words them
describe("FactsPanel — the fetch refusals", () => {
  it("shows the own-cards 404 without the couldn't-extract wrapper", () => {
    const msg = "This company is not on any of your cards. Add it to a card first.";
    h.extract = { data: undefined, error: { detail: msg }, isFetching: false };
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(msg)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't extract/)).not.toBeInTheDocument();
  });

  it("shows the daily-allowance 429 the same way", () => {
    const msg = "Daily allowance reached: 5,000 extracts a day. It resets at midnight America/New_York time.";
    h.extract = { data: undefined, error: { detail: msg }, isFetching: false };
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(msg)).toBeInTheDocument();
  });
});
