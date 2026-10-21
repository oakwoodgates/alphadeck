import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InsiderAmendments } from "../InsiderAmendments";

// The Insider amendments panel (R8-A / R8-B) over mocked hooks. The rows are the shapes GET
// /admin/insider-amendments returns for the REAL filings the backend suite ingests
// (backend/tests/form4a_fixtures.py): the SKYT ambiguous pair (OWED — its sales sit inside the review horizon),
// the FLS cancellation the SYSTEM applied as the filer's correction, the AVAV grant-only void it applied with
// no signal effect, an AZTA keep the operator stacked over the system's application, the ELTP ambiguous 2012
// sale (unresolved — historical), and the RPAY clerical one. The gates: no decision fires on mount, LOUD only
// where a decision is owed, never "owed your decision" for a system application, and every action has its
// inverse beside it.

const FLS_ID = "e8ff1562-99a2-4402-a06c-994667aa6006";
const SKYT_ID = "531613b9-fe18-4850-a795-d256e12fbef4";

const base = {
  recorded_at: "2026-10-21T02:31:00+00:00",
  accepted: null,
  date_of_original_submission: null,
  remarks: null,
  footnotes: [] as string[],
  derivative_count: 0,
  verdict: null as string | null,
  why: "",
  original_accessions: [] as string[],
  voided_accessions: [] as string[],
  candidate_accessions: [] as string[],
  suppressing: [] as string[],
  amendment_rows: [] as unknown[],
  originals: [] as unknown[],
  decisions: [] as unknown[],
  undo_id: null as string | null,
  touches_buy: false,
  filing_url: null as string | null,
  signal_effect: true,
  auto_resolved: false,
  in_window: [] as string[],
  review: "owed",
  system_basis: null as string | null,
  in_review: false,
  unresolved: false,
  kept_rows: [] as string[],
};

const SKYT = {
  ...base,
  security_id: SKYT_ID,
  ticker: "SKYT",
  accession: "0001856680-26-000016",
  filed: "2026-03-23",
  owners: ["Manko Steve"],
  disposition: "ambiguous",
  method: "orig_date_owner",
  status: "pending",
  review: "owed",
  in_review: true,
  why: "2 different Form 4s by the owner were filed 2026-03-18 — which one this amends is the operator's call",
  candidate_accessions: ["0001856680-26-000010", "0001856680-26-000013"],
};

const FLS = {
  ...base,
  security_id: FLS_ID,
  ticker: "FLS",
  accession: "0001628280-26-037326",
  filed: "2026-05-21",
  owners: ["McMurray Michael C."],
  footnotes: [
    "The Reporting Person's previously reported sale on May 18, 2026 was cancelled by the Reporting Person's broker within the settlement window.",
  ],
  disposition: "void",
  method: "orig_date_owner",
  status: "applied",
  verdict: "apply",
  review: "auto",
  auto_resolved: true,
  system_basis: "system:filer-correction",
  original_accessions: ["0001628280-26-035913"],
  candidate_accessions: ["0001628280-26-035913"],
  suppressing: ["0001628280-26-035913"],
  originals: [
    {
      accession: "0001628280-26-035913",
      role: "original",
      rows: [
        {
          seq: 0,
          insider_name: "McMurray Michael C.",
          txn_code: "P",
          txn_date: "2026-05-18",
          shares: 2500,
          price: 65.71,
          usd: 164275,
        },
      ],
    },
  ],
  touches_buy: true,
  in_window: ["P"],
  filing_url: "https://www.sec.gov/Archives/edgar/data/30625/000162828026037326/",
};

const AVAV_AUTO = {
  ...base,
  security_id: "6f1c3a52-7d0e-4c3b-9d7a-2b8e5f4a1c90",
  ticker: "AVAV",
  accession: "0001538012-26-000006",
  filed: "2026-07-15",
  owners: ["Nawabi Wahid"],
  disposition: "void",
  method: "orig_date_owner",
  status: "applied",
  verdict: "apply",
  review: "auto",
  signal_effect: false,
  auto_resolved: true,
  system_basis: "system:no-signal-effect",
  original_accessions: ["0001538012-26-000004"],
  suppressing: ["0001538012-26-000004"],
};

const AZTA_KEPT = {
  ...base,
  security_id: "d3fe8a2f-1cca-41af-a07b-fd7ae45fbaad",
  ticker: "AZTA",
  accession: "0001193125-25-282945",
  filed: "2025-11-14",
  owners: ["Lin"],
  disposition: "void",
  method: "orig_date_owner",
  status: "kept",
  verdict: "keep",
  review: "decided",
  undo_id: "9a1d2c3e-0000-4000-8000-000000000001",
  decisions: [
    {
      id: "9a1d2c3e-0000-4000-8000-000000000000",
      action: "apply",
      reason: "the filer's own amendment",
      basis: "system:filer-correction",
      recorded_at: "2026-10-21T02:31:00+00:00",
      standing: true,
    },
    {
      id: "9a1d2c3e-0000-4000-8000-000000000001",
      action: "keep",
      reason: "only attaches a power of attorney",
      basis: "operator:keep",
      recorded_at: "2026-10-21T14:00:00+00:00",
      standing: true,
    },
  ],
};

const ELTP = {
  ...base,
  security_id: "0b8e7a51-3e1d-4c6f-9a2b-5d7c8e9f0a1b",
  ticker: "ELTP",
  accession: "0001465825-12-000008",
  filed: "2012-02-08",
  owners: ["POTTI RAM"],
  disposition: "ambiguous",
  method: "orig_date_owner",
  status: "pending",
  review: "historical",
  candidate_accessions: ["0001465825-12-000004", "0001465825-12-000005"],
};

const RPAY = {
  ...base,
  security_id: "28d0e407-b6b3-4630-997c-64d2c734c243",
  ticker: "RPAY",
  accession: "0001104659-26-041960",
  filed: "2026-04-10",
  owners: ["Forager Fund, L.P."],
  disposition: "clerical",
  method: "orig_date_owner",
  status: "inert",
  review: "inert",
  signal_effect: false,
};

const h = vi.hoisted(() => ({
  q: {} as Record<string, unknown>,
  apply: {} as Record<string, unknown>,
  keep: {} as Record<string, unknown>,
  link: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useInsiderAmendments: () => h.q,
  useApplyAmendment: () => h.apply,
  useKeepOriginal: () => h.keep,
  useLinkAmendment: () => h.link,
  useRevokeAmendmentDecision: () => h.revoke,
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null });
const mutate = (x: Record<string, unknown>) => x.mutate as ReturnType<typeof vi.fn>;

const EMPTY = { owed: [], auto: [], decided: [], historical: [], queued: [], inert: [], total: 0 };

function serve(data: Record<string, unknown>) {
  h.q = { data: { ...EMPTY, review_horizon_days: 940, ...data }, isError: false, error: null };
}

beforeEach(() => {
  h.apply = m();
  h.keep = m();
  h.link = m();
  h.revoke = m();
  serve({
    owed: [SKYT],
    auto: [FLS, AVAV_AUTO],
    decided: [AZTA_KEPT],
    historical: [ELTP],
    inert: [RPAY],
    total: 6,
  });
});

describe("InsiderAmendments", () => {
  it("renders nothing when no amendment is stored", () => {
    serve(EMPTY);
    const { container } = render(<InsiderAmendments />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is LOUD only for what is owed a decision, and fires nothing on mount", () => {
    render(<InsiderAmendments />);
    const owed = screen.getByTestId("adm-amend-owed");
    expect(owed.querySelector(".adm-loud")).toHaveTextContent("1 Form 4/A amendment(s) owed your decision");
    expect(within(owed).getAllByTestId("adm-amend-row")).toHaveLength(1);
    // the quiet buckets are collapsed lists, still present (#9)
    for (const id of ["adm-amend-auto", "adm-amend-decided", "adm-amend-historical", "adm-amend-inert"]) {
      expect(screen.getByTestId(id)).not.toHaveAttribute("open");
    }
    expect(screen.getByTestId("adm-amend-auto")).toHaveTextContent(
      "2 applied automatically — the filer's own correction",
    );
    expect(screen.getByTestId("adm-amend-inert")).toHaveTextContent("1 with nothing to apply");
    for (const mm of [h.apply, h.keep, h.link, h.revoke]) {
      expect(mutate(mm)).not.toHaveBeenCalled();
    }
  });

  it("never says 'owed your decision' about a system application", () => {
    render(<InsiderAmendments />);
    const auto = screen.getByTestId("adm-amend-auto");
    expect(auto).not.toHaveTextContent(/owed/i);
    expect(within(screen.getByTestId("adm-amend-owed")).queryByText(/FLS|AVAV/)).toBeNull();
  });

  it("asks the operator to pick the original of an ambiguous amendment — and changes nothing until then", async () => {
    render(<InsiderAmendments />);
    const row = screen.getAllByTestId("adm-amend-row")[0];
    expect(row).toHaveTextContent("Several Form 4s could be the one this amends");
    expect(row).toHaveTextContent("Nothing changes until you do.");
    expect(within(row).queryByRole("button", { name: "Ratify void" })).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Link to 0001856680-26-000013" }));
    expect(mutate(h.link)).toHaveBeenCalledWith({
      securityId: SKYT_ID,
      accession: SKYT.accession,
      target: "0001856680-26-000013",
    });
  });

  it("lists each system application with its basis, its evidence and Keep original as its Undo", async () => {
    render(<InsiderAmendments />);
    const [fls, avav] = within(screen.getByTestId("adm-amend-auto")).getAllByTestId("adm-amend-auto-row");
    expect(fls).toHaveTextContent("applied by the system — the filer's own amendment, resolved to the Form 4 it amends");
    expect(fls).toHaveTextContent("suppressing 0001628280-26-035913");
    // the evidence beside it: the filer's footnote and the original's rows
    expect(fls).toHaveTextContent("cancelled by the Reporting Person's broker");
    expect(fls).toHaveTextContent("P 2026-05-18 · 2,500 @ $65.71 = $164,275");
    expect(within(fls).getByRole("link", { name: "0001628280-26-037326" })).toHaveAttribute("href", FLS.filing_url);
    // a settled item in today's window is not loud (honest loudness)
    expect(within(fls).queryByTestId("adm-amend-in-window")).toBeNull();
    expect(avav).toHaveTextContent("applied by the system — it changes no buy or sale a call signal reads");
    await userEvent.click(within(fls).getByRole("button", { name: "Keep original" }));
    expect(mutate(h.keep)).toHaveBeenCalledWith({ securityId: FLS_ID, accession: FLS.accession });
    expect(mutate(h.revoke)).not.toHaveBeenCalled(); // the system's record is never revoked as "the Undo"
  });

  it("keeps the inverse beside a decision: Undo of a keep restores the filer's amendment", async () => {
    render(<InsiderAmendments />);
    const decided = screen.getByTestId("adm-amend-decided");
    expect(decided).toHaveTextContent("original kept by you — Undo restores the filer's amendment");
    await userEvent.click(within(decided).getByRole("button", { name: "Undo" }));
    expect(mutate(h.revoke)).toHaveBeenCalledWith({ decisionId: AZTA_KEPT.undo_id });
  });

  it("lists an old ambiguous amendment as unresolved — historical, grayed, and still linkable", async () => {
    render(<InsiderAmendments />);
    const hist = screen.getByTestId("adm-amend-historical");
    expect(hist).toHaveClass("adm-grayed");
    expect(hist).toHaveTextContent("1 unresolved — historical (no call reads these rows)");
    expect(hist).toHaveTextContent("940-day review horizon");
    const row = within(hist).getByTestId("adm-amend-historical-row");
    await userEvent.click(within(row).getByRole("button", { name: "Link to 0001465825-12-000005" }));
    expect(mutate(h.link)).toHaveBeenCalledWith({
      securityId: ELTP.security_id,
      accession: ELTP.accession,
      target: "0001465825-12-000005",
    });
  });

  it("tags an owed item inside today's window, and offers Apply / Keep on one the operator took back", async () => {
    const takenBack = {
      ...FLS,
      status: "pending",
      verdict: null,
      review: "owed",
      auto_resolved: false,
      system_basis: null,
    };
    serve({ owed: [takenBack, SKYT], total: 2 });
    render(<InsiderAmendments />);
    const [fls, skyt] = screen.getAllByTestId("adm-amend-row");
    expect(within(fls).getByTestId("adm-amend-in-window")).toHaveTextContent(
      "in today's P signal window — can move tonight's call",
    );
    expect(within(skyt).queryByTestId("adm-amend-in-window")).toBeNull();
    expect(fls).toHaveTextContent("Suppressing 0001628280-26-035913 from every call until you decide.");
    await userEvent.click(within(fls).getByRole("button", { name: "Ratify void" }));
    expect(mutate(h.apply)).toHaveBeenCalledWith({ securityId: FLS_ID, accession: FLS.accession });
  });

  it("marks the original rows an applied 4/A does not account for as standing (MRCY's sales)", () => {
    const MRCY = {
      ...AVAV_AUTO,
      ticker: "MRCY",
      accession: "0001049521-26-000071",
      disposition: "partial",
      original_accessions: ["0001049521-26-000054"],
      suppressing: [],
      kept_rows: ["0001049521-26-000054#0", "0001049521-26-000054#1"],
      amendment_rows: [{ seq: 0, txn_code: "A", txn_date: "2026-08-17", shares: 2882, price: 0, usd: 0 }],
      originals: [
        {
          accession: "0001049521-26-000054",
          role: "original",
          rows: [
            { seq: 0, txn_code: "S", txn_date: "2026-08-17", shares: 404, price: 109.95, usd: 44420 },
            { seq: 1, txn_code: "S", txn_date: "2026-08-18", shares: 494, price: 111.36, usd: 55010 },
          ],
        },
      ],
    };
    serve({ auto: [MRCY], total: 1 });
    render(<InsiderAmendments />);
    const row = within(screen.getByTestId("adm-amend-auto")).getByTestId("adm-amend-auto-row");
    expect(within(row).getAllByTestId("adm-amend-row-stands")).toHaveLength(2);
    expect(row).toHaveTextContent("stands (this 4/A does not account for it)");
  });

  it("asks about an unresolved row match: Apply if it restates, Keep if it does not", async () => {
    const MRAM = {
      ...base,
      security_id: "1a2b3c4d-0000-4000-8000-00000000aaaa",
      ticker: "MRAM",
      accession: "0001628280-26-047722",
      filed: "2026-07-08",
      owners: ["Aggarwal Sanjeev"],
      disposition: "restatement",
      method: "orig_date_owner",
      status: "pending",
      review: "owed",
      unresolved: true,
      in_review: true,
      original_accessions: ["0001628280-26-047404"],
      candidate_accessions: ["0001628280-26-047404"],
      kept_rows: ["0001628280-26-047404#0"],
    };
    serve({ owed: [MRAM], historical: [{ ...MRAM, accession: "0001628280-26-047720", review: "historical" }], total: 2 });
    render(<InsiderAmendments />);
    const row = screen.getAllByTestId("adm-amend-row")[0];
    expect(row).toHaveTextContent("date and share count both changed");
    expect(row).toHaveTextContent("The original stands until you decide.");
    expect(within(row).queryByRole("button", { name: /Link to/ })).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Apply amendment" }));
    expect(mutate(h.apply)).toHaveBeenCalledWith({ securityId: MRAM.security_id, accession: MRAM.accession });
    // a historical unresolved one offers Apply / Keep, never a Link to its own original
    const hist = within(screen.getByTestId("adm-amend-historical")).getByTestId("adm-amend-historical-row");
    expect(within(hist).queryByRole("button", { name: /Link to/ })).toBeNull();
    await userEvent.click(within(hist).getByRole("button", { name: "Keep original" }));
    expect(mutate(h.keep)).toHaveBeenCalledWith({ securityId: MRAM.security_id, accession: "0001628280-26-047720" });
  });

  it("shows a queued backlog quietly until the nightly sweep applies it", () => {
    serve({ queued: [{ ...FLS, status: "pending", verdict: null, review: "queued", auto_resolved: false }], total: 1 });
    render(<InsiderAmendments />);
    expect(screen.queryByTestId("adm-amend-owed")).toBeNull();
    expect(screen.getByTestId("adm-amend-queued")).toHaveTextContent(
      "1 awaiting the system's application at the next nightly pass",
    );
  });
});
