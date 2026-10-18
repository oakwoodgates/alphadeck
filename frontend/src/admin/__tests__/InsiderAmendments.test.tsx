import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InsiderAmendments } from "../InsiderAmendments";

// The Insider amendments panel (R8-A) over mocked hooks. The rows are the shapes GET
// /admin/insider-amendments returns for the REAL filings the backend suite ingests
// (backend/tests/form4a_fixtures.py): the FLS cancellation (a pending void), the SKYT ambiguous pair, an
// AZTA keep the operator decided, the ARBE auto-applied correction, the RPAY clerical one. The gates: no
// decision fires on mount, LOUD only where a decision is owed, and every action has its inverse beside it.

const FLS_ID = "e8ff1562-99a2-4402-a06c-994667aa6006";
const SKYT_ID = "531613b9-fe18-4850-a795-d256e12fbef4";

const base = {
  recorded_at: "2026-10-18T02:31:00+00:00",
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
  status: "pending",
  why: "the owner's only Form 4 filed 2026-05-18 (dateOfOriginalSubmission)",
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
  filing_url: "https://www.sec.gov/Archives/edgar/data/30625/000162828026037326/",
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
  candidate_accessions: ["0001856680-26-000010", "0001856680-26-000013"],
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
  undo_id: "9a1d2c3e-0000-4000-8000-000000000001",
};

const ARBE = {
  ...base,
  security_id: "b4adecfe-296d-4193-b6e1-48efd767ca33",
  ticker: "ARBE",
  accession: "0001213900-26-107326",
  filed: "2026-10-06",
  owners: ["CRIST EUGENE SCOTT"],
  disposition: "correction",
  method: "orig_date_owner",
  status: "applied",
  suppressing: ["0001213900-26-106000"],
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

function serve(data: Record<string, unknown>) {
  h.q = { data, isError: false, error: null };
}

beforeEach(() => {
  h.apply = m();
  h.keep = m();
  h.link = m();
  h.revoke = m();
  serve({
    pending: [FLS, SKYT],
    decided: [AZTA_KEPT],
    applied: [ARBE],
    inert: [RPAY],
    total: 5,
  });
});

describe("InsiderAmendments", () => {
  it("renders nothing when no amendment is stored", () => {
    serve({ pending: [], decided: [], applied: [], inert: [], total: 0 });
    const { container } = render(<InsiderAmendments />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is LOUD only for what is owed a decision, and fires nothing on mount", () => {
    render(<InsiderAmendments />);
    const pending = screen.getByTestId("adm-amend-pending");
    expect(pending.querySelector(".adm-loud")).toHaveTextContent("2 Form 4/A amendment(s) owed your decision");
    expect(within(pending).getAllByTestId("adm-amend-row")).toHaveLength(2);
    // the quiet buckets are collapsed lists, still present (#9)
    expect(screen.getByTestId("adm-amend-decided")).not.toHaveAttribute("open");
    expect(screen.getByTestId("adm-amend-applied")).toHaveTextContent("1 correction(s) applied automatically");
    expect(screen.getByTestId("adm-amend-inert")).toHaveTextContent("1 with nothing to apply");
    for (const mm of [h.apply, h.keep, h.link, h.revoke]) {
      expect((mm as { mutate: ReturnType<typeof vi.fn> }).mutate).not.toHaveBeenCalled();
    }
  });

  it("shows a pending void's evidence and what it suppresses, with Ratify void and Keep original", async () => {
    render(<InsiderAmendments />);
    const row = screen.getAllByTestId("adm-amend-row")[0];
    expect(row).toHaveTextContent("VOID");
    expect(row).toHaveTextContent("Suppressing 0001628280-26-035913 from every call until you decide.");
    expect(row).toHaveTextContent("cancelled by the Reporting Person's broker");
    expect(row).toHaveTextContent("P 2026-05-18 · 2,500 @ $65.71 = $164,275");
    expect(within(row).getByRole("link", { name: "0001628280-26-037326" })).toHaveAttribute(
      "href",
      FLS.filing_url,
    );
    await userEvent.click(within(row).getByRole("button", { name: "Ratify void" }));
    expect((h.apply as { mutate: ReturnType<typeof vi.fn> }).mutate).toHaveBeenCalledWith({
      securityId: FLS_ID,
      accession: FLS.accession,
    });
    await userEvent.click(within(row).getByRole("button", { name: "Keep original" }));
    expect((h.keep as { mutate: ReturnType<typeof vi.fn> }).mutate).toHaveBeenCalledWith({
      securityId: FLS_ID,
      accession: FLS.accession,
    });
  });

  it("asks the operator to pick the original of an ambiguous amendment — and suppresses nothing", async () => {
    render(<InsiderAmendments />);
    const row = screen.getAllByTestId("adm-amend-row")[1];
    expect(row).toHaveTextContent("Nothing is suppressed until you pick the original it amends.");
    expect(within(row).queryByRole("button", { name: "Ratify void" })).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Link to 0001856680-26-000013" }));
    expect((h.link as { mutate: ReturnType<typeof vi.fn> }).mutate).toHaveBeenCalledWith({
      securityId: SKYT_ID,
      accession: SKYT.accession,
      target: "0001856680-26-000013",
    });
  });

  it("keeps the inverse beside every decision: Undo on a decided one, Keep original on a correction", async () => {
    render(<InsiderAmendments />);
    const decided = screen.getByTestId("adm-amend-decided");
    expect(decided).toHaveTextContent("original kept");
    await userEvent.click(within(decided).getByRole("button", { name: "Undo" }));
    expect((h.revoke as { mutate: ReturnType<typeof vi.fn> }).mutate).toHaveBeenCalledWith({
      decisionId: AZTA_KEPT.undo_id,
    });
    const applied = screen.getByTestId("adm-amend-applied");
    await userEvent.click(within(applied).getByRole("button", { name: "Keep original" }));
    expect((h.keep as { mutate: ReturnType<typeof vi.fn> }).mutate).toHaveBeenCalledWith({
      securityId: ARBE.security_id,
      accession: ARBE.accession,
    });
  });
});
