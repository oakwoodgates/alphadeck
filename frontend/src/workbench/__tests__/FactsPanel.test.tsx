import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the extract query + the ratify mutation. The per-tier RatifyRow logic
// (editability, the located-passage inline render, the purity gate) is the REAL component under test.
const h = vi.hoisted(() => ({
  refetch: vi.fn(),
  extract: { data: undefined as unknown, error: null as unknown, isFetching: false },
  mutate: vi.fn(),
  ratify: { isPending: false, isError: false, isSuccess: false, error: null as unknown },
  explainRefetch: vi.fn(),
  explain: { data: undefined as unknown, error: null as unknown, isFetching: false },
}));

vi.mock("../../api/hooks", () => ({
  useExtract: () => ({ ...h.extract, refetch: h.refetch }),
  useRatifyFact: () => ({ mutate: h.mutate, ...h.ratify }),
  useExplainFlag: () => ({ ...h.explain, refetch: h.explainRefetch }),
}));

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

const SID = "00000000-0000-0000-0000-000000000abc";

beforeEach(() => {
  h.refetch.mockReset();
  h.mutate.mockReset();
  h.explainRefetch.mockReset();
  h.extract = { data: undefined, error: null, isFetching: false };
  h.ratify = { isPending: false, isError: false, isSuccess: false, error: null };
  h.explain = { data: undefined, error: null, isFetching: false };
});

describe("FactsPanel — extract → ratify", () => {
  it("renders a candidate per tier: AUTO value read-only, located excerpt inline, purity gated", () => {
    h.extract.data = [AUTO_SHARES, FLAG_BURN, HUMAN_PURITY];
    render(<FactsPanel securityId={SID} />);

    // AUTO — the value is shown but read-only (confirm-as-is; the operator doesn't retype it)
    const shares = screen.getByLabelText("shares") as HTMLInputElement;
    expect(shares.value).toBe("141000000");
    expect(shares.readOnly).toBe(true);

    // FLAG — the raw burn is editable, and the located passage is readable INLINE (not a tooltip)
    const burn = screen.getByLabelText("quarterly burn") as HTMLInputElement;
    expect(burn.value).toBe("314678000");
    expect(burn.readOnly).toBe(false);
    expect(screen.getByText(/Partnership milestone payment of 264,195/)).toBeInTheDocument();
    const chip = screen.getByRole("link", { name: /cash-flow-line/ });
    expect(chip).toHaveAttribute("href", "https://sec.gov/smr-10q#p1");

    // HUMAN purity — empty, never pre-filled; its Confirm is disabled until authored
    expect((screen.getByLabelText("segment") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("purity percent") as HTMLInputElement).value).toBe("");
    const confirms = screen.getAllByRole("button", { name: "Confirm" });
    expect(confirms[2]).toBeDisabled(); // the purity row (third candidate)
  });

  it("extract fires on the explicit click, NOT on mount", async () => {
    render(<FactsPanel securityId={SID} />);
    expect(h.refetch).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Extract from filings/ }));
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("a candidate whose fact is already ON FILE is tagged (a re-confirm appends, never 'never saved')", () => {
    h.extract.data = [AUTO_SHARES, FLAG_BURN];
    render(
      <FactsPanel securityId={SID} onFile={{ shares_outstanding: true, cash_burn: false }} />,
    );
    const tags = screen.getAllByText("✓ on file");
    expect(tags).toHaveLength(1); // ONLY the shares row — the tag must discriminate
    expect(tags[0].closest(".ratify-row")).toHaveTextContent(/market cap · shares/);
  });

  it("a LONG located passage renders CLAMPED with an explicit expand (evidence, not a wall)", async () => {
    const user = userEvent.setup();
    const LONG = {
      ...HUMAN_PURITY,
      located_passages: [
        {
          kind: "segment",
          source_ref: "https://sec.gov/mu-10k#seg",
          anchor: "segment",
          excerpt: "Revenue by segment " + "104 414 176 137 920 restructure ".repeat(30),
        },
      ],
    };
    h.extract.data = [LONG];
    render(<FactsPanel securityId={SID} />);
    const excerpt = screen.getByText(/Revenue by segment/);
    expect(excerpt).toHaveClass("clamped");
    await user.click(screen.getByRole("button", { name: /show the full passage/ }));
    expect(excerpt).not.toHaveClass("clamped");
    await user.click(screen.getByRole("button", { name: /collapse the passage/ }));
    expect(excerpt).toHaveClass("clamped");
  });

  it("a SHORT passage renders unclamped with no expand control (the clamp marks the exception)", () => {
    h.extract.data = [FLAG_BURN]; // its excerpt is well under the clamp threshold
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/Partnership milestone payment/)).not.toHaveClass("clamped");
    expect(screen.queryByRole("button", { name: /show the full passage/ })).not.toBeInTheDocument();
  });

  it("the note is a growable TEXTAREA — a truncated single line hid the basis being ratified", async () => {
    const user = userEvent.setup();
    h.extract.data = [FLAG_BURN]; // FLAG rows carry a pre-filled composition note
    render(<FactsPanel securityId={SID} />);
    const note = screen.getByLabelText("note") as HTMLTextAreaElement;
    expect(note.tagName).toBe("TEXTAREA");
    expect(note.value).toContain("one-time ENTRA1"); // the pre-filled basis rides in, fully visible
    await user.type(note, " — confirmed against the cash-flow statement");
    expect(note.value).toContain("confirmed against"); // still editable
  });

  it("a FLAG confirm posts the EDITED recurring burn, not the raw value", async () => {
    const user = userEvent.setup();
    h.extract.data = [FLAG_BURN];
    render(<FactsPanel securityId={SID} />);

    const burn = screen.getByLabelText("quarterly burn");
    await user.clear(burn);
    await user.type(burn, "50483000"); // the operator strips the one-time payment

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      fact_type: "cash_burn",
      security_id: SID,
      source: "10-q-cashflow", // the candidate's BASIS, carried through (not retyped)
      cash_usd: 890000000,
      quarterly_burn_usd: 50483000,
    });
  });

  it("a HUMAN purity confirm requires an operator-entered % (no pre-fill)", async () => {
    const user = userEvent.setup();
    h.extract.data = [HUMAN_PURITY];
    render(<FactsPanel securityId={SID} />);

    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();

    // a segment alone is not enough — the % is the operator's judgment and is still required
    await user.type(screen.getByLabelText("segment"), "nuclear");
    expect(confirm).toBeDisabled();

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
});

describe("FactsPanel — the FLAG-explanation drafter (the LLM seam)", () => {
  it("offers Explain on FLAG rows only — not AUTO, not HUMAN", () => {
    h.extract.data = [AUTO_SHARES, FLAG_BURN, HUMAN_PURITY];
    render(<FactsPanel securityId={SID} />);
    // exactly one Explain affordance — the FLAG (burn) row; AUTO is clean, HUMAN/purity is the operator's edge
    expect(screen.getAllByRole("button", { name: /Explain/ })).toHaveLength(1);
  });

  it("fires on the explicit click (not on render) and shows the model text, marked 'drafted'", async () => {
    h.explain.data = {
      explanation: "The cash use includes a one-time ~$264M ENTRA1 milestone; recurring is lower.",
      grounded: true,
    };
    h.extract.data = [FLAG_BURN];
    render(<FactsPanel securityId={SID} />);

    expect(h.explainRefetch).not.toHaveBeenCalled(); // never auto-fired on render
    await userEvent.click(screen.getByRole("button", { name: /Explain/ }));
    expect(h.explainRefetch).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/one-time ~\$264M ENTRA1 milestone/)).toBeInTheDocument();
    expect(screen.getByText("drafted")).toBeInTheDocument(); // marked model-drafted
  });

  it("THE BOUND: a grounded explanation never pre-fills the value field", () => {
    // the model's text even names a number; the burn input still shows the RAW value, untouched —
    // the ratified number is the operator's to type (the explanation rides no rail into the field)
    h.explain.data = { explanation: "Strip the 264,195 milestone and recurring is lower.", grounded: true };
    h.extract.data = [FLAG_BURN];
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/Strip the 264,195 milestone/)).toBeInTheDocument(); // the aid is shown
    const burn = screen.getByLabelText("quarterly burn") as HTMLInputElement;
    expect(burn.value).toBe("314678000"); // the operator's field is UNTOUCHED
  });

  it("grounded=false is a say-so, never a fabricated explanation", () => {
    h.explain.data = { explanation: "", grounded: false };
    h.extract.data = [FLAG_BURN];
    render(<FactsPanel securityId={SID} />);
    expect(screen.getByText(/No plain-English read grounded in the passage/)).toBeInTheDocument();
    expect(screen.queryByText("drafted")).not.toBeInTheDocument();
  });

  it("fail-open: an explain error leaves the raw passage + manual ratify fully working", async () => {
    h.explain = { data: undefined, error: new Error("LLM down"), isFetching: false };
    h.extract.data = [FLAG_BURN];
    render(<FactsPanel securityId={SID} />);
    // no drafted block, but the located passage is still readable and the ratify still posts
    expect(screen.queryByText("drafted")).not.toBeInTheDocument();
    expect(screen.getByText(/Partnership milestone payment of 264,195/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(h.mutate).toHaveBeenCalledTimes(1); // the panel works exactly as today
  });
});

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

describe("FactsPanel — the grounded purity estimate (SURFACE 1b)", () => {
  it("renders the estimate (unverified tag, % + segment pre-filled) and confirms it as-is WITH the estimate", async () => {
    h.extract.data = [PURITY_EST];
    const user = userEvent.setup();
    render(<FactsPanel securityId={SID} thesisId="t-nuke" />);

    expect(screen.getByText(/estimate 26\.7% · llm-proposed · unverified/)).toBeInTheDocument();
    expect((screen.getByLabelText("purity percent") as HTMLInputElement).value).toBe("26.7");
    expect((screen.getByLabelText("segment") as HTMLInputElement).value).toBe(
      "Commercial Operations",
    );
    // the grounded passage rides alongside (the operator eyeballs the $ figures)
    expect(screen.getByText(/Commercial Operations 853,070/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    // confirm-as-is: the shown estimate rides the body (→ vouched=confirmed) and matches the ratified %
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      fact_type: "revenue_mix",
      segment_label: "Commercial Operations",
      mix_pct: 26.7,
      estimate: 26.7,
    });
  });

  it("an override sends the shown estimate but a different % (→ vouched=overridden)", async () => {
    h.extract.data = [PURITY_EST];
    const user = userEvent.setup();
    render(<FactsPanel securityId={SID} thesisId="t-nuke" />);

    const pct = screen.getByLabelText("purity percent");
    await user.clear(pct);
    await user.type(pct, "100");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    // the estimate the operator was shown still rides the body; the server derives 'overridden' from the diff
    expect(h.mutate.mock.calls[0][0]).toMatchObject({ mix_pct: 100, estimate: 26.7 });
  });
});
