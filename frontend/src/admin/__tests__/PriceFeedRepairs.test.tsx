import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminPriceRepairsOut } from "../../api/hooks";
import { PriceFeedRepairs, repairHandledIds, repairVerb } from "../PriceFeedRepairs";
import { massiveClause } from "../priceFeedWords";

// The "Price feed repairs" panel over mocked mutation hooks, on the measured prod night (2026-10-16):
// HLLK renamed (SDCO splice-verified), BRR renamed (SVIA verified, its warrants SVIAW not), WONDF never
// priced, GTLS closed. The gates: nothing fires on mount (every write is the operator's click), every
// action has its inverse beside it, and loudness marks only what needs a decision.

const h = vi.hoisted(() => ({
  adopt: {} as Record<string, unknown>,
  set: {} as Record<string, unknown>,
  undo: {} as Record<string, unknown>,
  ack: {} as Record<string, unknown>,
  revoke: {} as Record<string, unknown>,
  adoptCik: {} as Record<string, unknown>,
  undoCik: {} as Record<string, unknown>,
  markLimited: {} as Record<string, unknown>,
  undoLimited: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useAdoptPriceSymbol: () => h.adopt,
  useSetPriceSymbol: () => h.set,
  useUndoPriceSymbolChange: () => h.undo,
  useAcknowledgeUnpriceable: () => h.ack,
  useRevokeAcknowledgment: () => h.revoke,
  useAdoptSuccessorCik: () => h.adoptCik,
  useUndoCikChange: () => h.undoCik,
  useMarkLimitedHistory: () => h.markLimited,
  useUndoLimitedHistory: () => h.undoLimited,
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null });

beforeEach(() => {
  h.adopt = m();
  h.set = m();
  h.undo = m();
  h.ack = m();
  h.revoke = m();
  h.adoptCik = m();
  h.undoCik = m();
  h.markLimited = m();
  h.undoLimited = m();
});

const HLLK_ID = "1ab2376c-df06-4928-9d6a-868a49eb936b";
const BRR_ID = "c8f7df60-247d-4a02-ade1-476ced15626d";
const WONDF_ID = "d326ed68-1228-4224-980b-06b3bc64bfdf";
const GTLS_ID = "3b36a89a-d286-4d1e-aa44-c8ccfbe5c126";

const HLLK = {
  security_id: HLLK_ID,
  ticker: "HLLK",
  edge: "2026-09-24",
  theses: [{ id: "md", name: "Modern Defense" }],
  edgar_tickers: ["SDCO"],
  edgar_name: "SDR Drone, Inc.",
  former_names: [{ name: "HALLMARK VENTURE GROUP, INC.", since: "2011-02-04", until: "2026-06-16" }],
  name_change_filings: [{ filed: "2026-07-16", accession: "0001493152-26-033465" }],
  candidates: [
    { symbol: "SDCO", verified: true, overlap: 30, ratio: 1.0, bars: 48, last_bar: "2026-10-15" },
  ],
  recommended: "SDCO",
  probed: true,
  price_symbol: null,
  change: null,
};

const BRR = {
  ...HLLK,
  security_id: BRR_ID,
  ticker: "BRR",
  edge: "2026-09-28",
  theses: [{ id: "ee", name: "The Everything Exchange" }],
  edgar_tickers: ["SVIA", "SVIAW"],
  edgar_name: "Silvia, Inc.",
  former_names: [],
  name_change_filings: [],
  candidates: [
    { symbol: "SVIA", verified: true, overlap: 30, ratio: 1.0, bars: 45, last_bar: "2026-10-16" },
    { symbol: "SVIAW", verified: false, overlap: 30, ratio: 0.12075, bars: 45, last_bar: "2026-10-16" },
  ],
  recommended: "SVIA",
};

const WONDF = {
  security_id: WONDF_ID,
  ticker: "WONDF",
  theses: [{ id: "ee", name: "The Everything Exchange" }],
  edgar_tickers: [],
  vendor_error:
    "Client error '404 Not Found' for url 'https://query1.finance.yahoo.com/v8/finance/chart/WONDF?interval=1d&range=2y'",
  price_symbol: null,
  change: null,
};

const GTLS = {
  security_id: GTLS_ID,
  ticker: "GTLS",
  edge: "2026-07-16",
  thesis: "Nuclear Buildout",
  kind: "price",
  closed_at: "2026-07-16",
};

const CHANGE = {
  id: "c1",
  security_id: HLLK_ID,
  ticker: "HLLK",
  action: "adopt",
  prior_symbol: null,
  new_symbol: "SDCO",
  name_changed: true,
  prior_name: "HALLMARK VENTURE GROUP, INC.",
  new_name: "SDR Drone, Inc.",
  basis: "operator:adopt",
  created_at: "2026-10-17T14:00:00+00:00",
  undoable: true,
  current_symbol: "SDCO",
};

const EMPTY = {
  asof: "2026-10-16",
  ran_at: "2026-10-17T02:30:01+00:00",
  probe_evaluated: true,
  probe_error: null,
  renamed: [],
  unpriceable: [],
  acknowledged: [],
  closed: [],
  applied: [],
  expected_misses: 0,
};

const NIGHT = {
  ...EMPTY,
  renamed: [HLLK, BRR],
  unpriceable: [WONDF],
  closed: [GTLS],
  expected_misses: 1,
};

const show = (r: Record<string, unknown>) =>
  render(<PriceFeedRepairs repairs={r as unknown as AdminPriceRepairsOut} />);

describe("PriceFeedRepairs — what it shows", () => {
  it("renders NOTHING when there is nothing to show (honest loudness)", () => {
    show(EMPTY);
    expect(screen.queryByTestId("adm-price-repairs")).toBeNull();
  });

  it("a renamed name shows its splice verdicts and the EDGAR evidence; the warrants are not verified", () => {
    show(NIGHT);
    const block = screen.getByTestId("adm-renamed-repairs");
    expect(block.querySelector(".adm-loud")).not.toBeNull(); // a decision is owed: loud
    const rows = screen.getAllByTestId("adm-renamed-row");
    expect(rows[0].textContent).toContain("HLLK");
    expect(rows[0].textContent).toContain("EDGAR now lists SDCO (SDR Drone, Inc.; formerly HALLMARK VENTURE GROUP, INC.)");
    expect(rows[0].textContent).toContain("8-K item 5.03 filed 2026-07-16");
    expect(rows[0].textContent).toContain("splice-verified (30 shared bars, ×1.000, bars to 2026-10-15)");
    expect(rows[1].textContent).toContain("SVIAW not verified (30 shared bars, ×0.121");
    // the verified candidate's Adopt is the primary action; the mismatched one stays secondary
    const adopts = screen.getAllByRole("button", { name: /^Adopt / });
    expect(adopts.map((b) => [b.textContent, b.className.includes("primary")])).toEqual([
      ["Adopt SDCO", true],
      ["Adopt SVIA", true],
      ["Adopt SVIAW", false],
    ]);
  });

  it("an unpriceable name names the vendor 404 and EDGAR's empty list; closed names are a QUIET count", () => {
    show(NIGHT);
    const u = screen.getByTestId("adm-unpriceable-row");
    expect(u.textContent).toContain("WONDF");
    expect(u.textContent).toContain("vendor 404 Not Found");
    expect(u.textContent).toContain("EDGAR lists no symbol");
    const closed = screen.getByTestId("adm-repairs-closed");
    expect(closed.tagName).toBe("DETAILS"); // a collapsed count that opens to its names (never dropped)
    expect(closed.textContent).toContain("1 closed");
    expect(closed.textContent).toContain("GTLS — stopped trading 2026-07-16");
    expect(closed.querySelector(".adm-loud")).toBeNull();
  });

  it("a failed probe is a loud line, and the renamed row stays adoptable from EDGAR's list", () => {
    show({
      ...EMPTY,
      probe_error: "OperationalError: boom",
      renamed: [{ ...HLLK, candidates: [], probed: false }],
    });
    expect(screen.getByTestId("adm-probe-error").textContent).toContain("OperationalError: boom");
    expect(screen.getByTestId("adm-renamed-row").textContent).toContain("not probed on this pass");
    expect(screen.getByRole("button", { name: "Adopt SDCO" })).toBeTruthy();
  });
});

describe("PriceFeedRepairs — every action is the operator's click, with its inverse", () => {
  it("NOTHING fires on mount", () => {
    show({ ...NIGHT, acknowledged: [], applied: [CHANGE] });
    for (const hook of [h.adopt, h.set, h.undo, h.ack, h.revoke]) {
      expect(hook.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
    }
  });

  it("Adopt fires the adopt write for THAT name and symbol", async () => {
    show(NIGHT);
    await userEvent.click(screen.getByRole("button", { name: "Adopt SDCO" }));
    expect(h.adopt.mutate).toHaveBeenCalledWith({ securityId: HLLK_ID, symbol: "SDCO" });
  });

  it("an adopted row shows what was applied and its Undo (disabled when the master moved)", async () => {
    show({ ...EMPTY, renamed: [{ ...HLLK, change: CHANGE, price_symbol: "SDCO" }] });
    const line = screen.getByTestId("adm-applied-line");
    expect(line.textContent).toContain("adopted SDCO (name → SDR Drone, Inc.)");
    expect(screen.queryByRole("button", { name: /^Adopt / })).toBeNull(); // no second adopt
    await userEvent.click(screen.getByRole("button", { name: "Undo adopt" }));
    expect(h.undo.mutate).toHaveBeenCalledWith({ changeId: "c1" });

    show({
      ...EMPTY,
      renamed: [{ ...HLLK, change: { ...CHANGE, undoable: false, current_symbol: "SDCOX" } }],
    });
    expect(screen.getAllByRole("button", { name: "Undo adopt" })[1]).toBeDisabled();
    expect(screen.getByText(/the master changed since \(now SDCOX\)/)).toBeTruthy();
  });

  it("Set price symbol needs a symbol, then fires with it trimmed", async () => {
    show(NIGHT);
    const set = screen.getByRole("button", { name: "Set price symbol" });
    expect(set).toBeDisabled();
    await userEvent.type(screen.getByLabelText("vendor symbol for WONDF"), "  WNDR.TO ");
    await userEvent.click(set);
    expect(h.set.mutate).toHaveBeenCalledWith({ securityId: WONDF_ID, symbol: "WNDR.TO" });
  });

  it("Acknowledge fires with the reason; Remove is only a link to the basket editor", async () => {
    show(NIGHT);
    await userEvent.type(screen.getByLabelText("reason to keep WONDF unpriced"), "TSX-only filer");
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge — keep unpriced" }));
    expect(h.ack.mutate).toHaveBeenCalledWith({ securityId: WONDF_ID, reason: "TSX-only filer" });
    const remove = screen.getByRole("link", { name: "Remove from basket →" });
    expect(remove.getAttribute("href")).toBe("/workbench"); // no delete path of its own
  });

  it("an acknowledged name is a QUIET count with its Revoke", async () => {
    show({
      ...EMPTY,
      acknowledged: [
        {
          id: "a1",
          security_id: WONDF_ID,
          ticker: "WONDF",
          action: "acknowledge",
          reason: "TSX-only filer",
          basis: "operator:acknowledge",
          created_at: "2026-10-17T14:00:00+00:00",
          still_unpriceable: true,
        },
      ],
    });
    const acked = screen.getByTestId("adm-acknowledged");
    expect(acked.tagName).toBe("DETAILS");
    expect(acked.querySelector(".adm-loud")).toBeNull();
    expect(acked.textContent).toContain("1 acknowledged — kept unpriced by the operator");
    expect(acked.textContent).toContain("WONDF — “TSX-only filer”");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(h.revoke.mutate).toHaveBeenCalledWith({ ackId: "a1" });
  });

  it("a repair whose name healed and left the inventory keeps its Undo in the applied list (WB#1)", async () => {
    show({ ...EMPTY, applied: [CHANGE] });
    const applied = screen.getByTestId("adm-applied");
    expect(applied.textContent).toContain("HLLK — adopted SDCO");
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(h.undo.mutate).toHaveBeenCalledWith({ changeId: "c1" });
  });

  it("a rejected write shows the server's reason", () => {
    h.adopt = { ...m(), isError: true, error: { detail: "HLLK is already priced under SDCO" } };
    show(NIGHT);
    expect(screen.getByText("HLLK is already priced under SDCO")).toBeTruthy();
  });
});

// T7 — the successor-CIK block, on the measured 2026-10-17 prod finding: UROY's stored CIK 0001711570
// ("Uranium Royalty Corp. /CA", 15-12G 2026-07-31) lists no symbol; SEC assigns UROY to 0002143673
// (8-K12B 2026-07-28, incorporated DE, files 10-K / 10-Q).
const UROY_ID = "c61a00a6-ff88-43c3-8c3e-479e4f900e14";
const UROY = {
  security_id: UROY_ID,
  ticker: "UROY",
  theses: [{ id: "nb", name: "Nuclear Buildout" }],
  stored_cik: "0001711570",
  master_name: "Uranium Royalty Corp.",
  old_entity_name: "Uranium Royalty Corp. /CA",
  old_former_names: [{ name: "Uranium Royalty Corp.", since: "2018-07-16", until: "2026-07-20" }],
  deregistrations: [{ form: "15-12G", filed: "2026-07-31", accession: "0001493152-26-035517", items: [] }],
  candidates: [
    {
      cik: "0002143673",
      matched_on: ["UROY"],
      entity_name: "Uranium Royalty Corp.",
      edgar_tickers: ["UROY"],
      former_names: [],
      succession_filings: [
        { form: "8-K12B", filed: "2026-07-28", accession: "0001493152-26-034947", items: ["5.03"] },
      ],
      identity_read: true,
      recent_foreign_form: null,
      files_domestic_forms: true,
      incorporation: "DE",
      error: null,
    },
  ],
  recommended: "0002143673",
  current_cik: "0001711570",
  change: null,
};
const CIK_CHANGE = {
  id: "k1",
  security_id: UROY_ID,
  ticker: "UROY",
  action: "adopt",
  prior_cik: "0001711570",
  new_cik: "0002143673",
  prior_name: "Uranium Royalty Corp.",
  new_name: "Uranium Royalty Corp.",
  basis: "operator:adopt-cik",
  created_at: "2026-10-17T15:00:00+00:00",
  undoable: true,
  current_cik: "0002143673",
};

describe("PriceFeedRepairs — the successor CIK (T7)", () => {
  it("a member filing under a NEW CIK is LOUD with its evidence and ONE Adopt for the recommended CIK", async () => {
    show({ ...EMPTY, successor: [UROY], successor_asof: "2026-10-16" });
    const block = screen.getByTestId("adm-successor-cik");
    expect(block.querySelector(".adm-loud")).not.toBeNull();
    const row = screen.getByTestId("adm-successor-row");
    expect(row.textContent).toContain(
      "UROY — stored CIK 0001711570 (Uranium Royalty Corp. /CA) lists no symbol · 15-12G filed 2026-07-31",
    );
    expect(screen.getByTestId("adm-successor-candidate").textContent).toContain(
      "SEC now assigns UROY to CIK 0002143673 (Uranium Royalty Corp.) · 8-K12B filed 2026-07-28",
    );
    expect(row.textContent).toContain("incorporated DE · files 10-K / 10-Q (insider Form 4s apply)");
    for (const hook of [h.adoptCik, h.undoCik]) {
      expect(hook.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled(); // nothing on mount
    }
    await userEvent.click(screen.getByRole("button", { name: "Adopt CIK 0002143673" }));
    expect(h.adoptCik.mutate).toHaveBeenCalledWith({ securityId: UROY_ID, cik: "0002143673" });
  });

  it("an adopted row shows the re-point and its Undo; an unread successor cannot be adopted", async () => {
    show({ ...EMPTY, successor: [{ ...UROY, change: CIK_CHANGE, current_cik: "0002143673" }] });
    expect(screen.getByTestId("adm-applied-cik-line").textContent).toContain("adopted CIK 0002143673");
    expect(screen.queryByRole("button", { name: /^Adopt CIK/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Undo adopt" }));
    expect(h.undoCik.mutate).toHaveBeenCalledWith({ changeId: "k1" });

    const unread = { ...UROY.candidates[0], identity_read: false, error: "OSError: timed out" };
    show({ ...EMPTY, successor: [{ ...UROY, candidates: [unread] }] });
    expect(screen.getByRole("button", { name: "Adopt CIK 0002143673" })).toBeDisabled();
    expect(screen.getByText(/could not be read on that pass \(OSError: timed out\)/)).toBeTruthy();
  });

  it("a re-point that left the inventory keeps its Undo in a QUIET list (WB#1); a failed check is loud", async () => {
    show({ ...EMPTY, cik_applied: [CIK_CHANGE], successor_error: "OSError: map fetch failed" });
    const applied = screen.getByTestId("adm-cik-applied");
    expect(applied.tagName).toBe("DETAILS");
    expect(applied.querySelector(".adm-loud")).toBeNull();
    expect(applied.textContent).toContain("UROY — CIK 0001711570 → 0002143673");
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(h.undoCik.mutate).toHaveBeenCalledWith({ changeId: "k1" });
    expect(screen.getByTestId("adm-successor-error").textContent).toContain("OSError: map fetch failed");
  });
});

// 0056 — the known feed limitations, on the real case: SCE-PG (a Southern California Edison preferred) whose
// vendor serves ONE bar under every range (MEASURED 2026-10-21); the mark's evidence is that one-bar pull.
const SCE_ID = "219ae6ad-605a-484a-a9fb-9e6ae5dcfb16";
const SCE_WHY = "Yahoo serves no history for SCE-PG: one bar (the current day) under every range";
const SCE_MARK = {
  id: "l1",
  security_id: SCE_ID,
  ticker: "SCE-PG",
  reason: SCE_WHY,
  basis: "operator:limited-history",
  created_at: "2026-10-22T14:00:00+00:00",
  active: true,
  revoked_at: null,
  vendor_symbol: "SCE-PG",
  vendor_range: "2y",
  vendor_bars: 1,
  vendor_last_bar: "2026-10-21",
  tape_stopped: false,
  tape_edge: null,
};

describe("PriceFeedRepairs — the known feed limitations (0056)", () => {
  it("a marked feed is a QUIET list row with the reason, the vendor evidence and its Undo", async () => {
    show({ ...EMPTY, limitations: [SCE_MARK] });
    const list = screen.getByTestId("adm-limitations");
    expect(list.tagName).toBe("DETAILS"); // collapsed, never paged
    expect(list.querySelector(".adm-loud")).toBeNull();
    expect(list.textContent).toContain("1 known feed limitation(s) — the vendor serves no history");
    const row = screen.getByTestId("adm-limitation-row");
    expect(row.textContent).toContain("SCE-PG — the vendor serves no history");
    expect(row.textContent).toContain(`“${SCE_WHY}” · marked 2026-10-22`);
    expect(row.textContent).toContain("(vendor: 1 bar at 2y)");
    expect(h.undoLimited.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled(); // not on mount
    expect(h.markLimited.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(h.undoLimited.mutate).toHaveBeenCalledWith({ markId: "l1" });
  });

  it("an UNDONE mark stays listed, grayed, with Mark again (the inverse never vanishes)", async () => {
    const undone = { ...SCE_MARK, active: false, revoked_at: "2026-10-23T09:00:00+00:00" };
    show({ ...EMPTY, limitations: [undone] });
    const row = screen.getByTestId("adm-limitation-row");
    expect(row.className).toBe("adm-grayed");
    expect(row.textContent).toContain("undone 2026-10-23");
    expect(screen.getByTestId("adm-limitations").textContent).toContain(
      "0 known feed limitation(s) — the vendor serves no history",
    );
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Mark again" }));
    expect(h.markLimited.mutate).toHaveBeenCalledWith({ securityId: SCE_ID, reason: SCE_WHY });
  });

  it("a marked name whose tape STOPPED says so loudly — the mark never excuses a real gap", () => {
    show({ ...EMPTY, limitations: [{ ...SCE_MARK, tape_stopped: true, tape_edge: "2026-08-31" }] });
    expect(screen.getByTestId("adm-limitation-stopped").textContent).toContain(
      "its tape has STOPPED (last bar 2026-08-31)",
    );
  });

  it("a limitation is NOT a handled id: a marked name that stopped stays a stale feed on the freshness card", () => {
    const ids = repairHandledIds({ ...EMPTY, limitations: [SCE_MARK] } as unknown as AdminPriceRepairsOut);
    expect(ids.has(SCE_ID)).toBe(false);
  });
});

// W4-3 — a NEVER-PRICED name's probed candidates and its deregistration evidence. TKAAY is the backend test's
// ADR member (SYNTHETIC METALS PLC/ADR): EDGAR lists its ordinary line TKAAF (480 bars, a cross-class listing),
// TKAAN (30 bars, class unknown) and TKAAX (its fetch 404ed). TKBK is a company that filed a Form 15 with
// bankruptcy / control-change 8-Ks around it, and lists no symbol.
const TKAAY_ID = "5a1c0f0e-5a2b-4a0e-9b6e-0f6f2d5a0c01";
const TKCC_ID = "5a1c0f0e-5a2b-4a0e-9b6e-0f6f2d5a0c02";
const TKBK_ID = "5a1c0f0e-5a2b-4a0e-9b6e-0f6f2d5a0c03";
const VENDOR_404 =
  "Client error '404 Not Found' for url 'https://query1.finance.yahoo.com/v8/finance/chart/TKAAY?interval=1d&range=2y'";
const ONLY_SYMBOLS = ["TKAAY", "TKAAF", "TKAAN", "TKAAX"];

const TKAAY = {
  security_id: TKAAY_ID,
  ticker: "TKAAY",
  theses: [{ id: "sm", name: "Synthetic Minerals" }],
  edgar_tickers: ONLY_SYMBOLS,
  vendor_error: VENDOR_404,
  price_symbol: null,
  change: null,
  member_class: "adr",
  candidates: [
    { symbol: "TKAAF", verified: false, overlap: 0, bars: 480, last_bar: "2026-10-15", listing_class: "ordinary" },
    { symbol: "TKAAN", verified: false, overlap: 0, bars: 30, last_bar: "2026-10-15", listing_class: null },
    {
      symbol: "TKAAX",
      verified: false,
      overlap: 0,
      bars: 0,
      error: "HTTPStatusError: Client error '404 Not Found' for url 'https://x.test/TKAAX'",
      listing_class: null,
    },
  ],
  recommended: null,
  probed: true,
  deregistered: null,
};

// the recommended shape: a candidate of the member's own class, with bars to the last pass
const TKCC = {
  ...TKAAY,
  security_id: TKCC_ID,
  ticker: "TKCCY",
  edgar_tickers: ["TKCCY", "TKCCX"],
  candidates: [
    { symbol: "TKCCX", verified: false, overlap: 0, bars: 250, last_bar: "2026-10-16", listing_class: "adr" },
  ],
  recommended: "TKCCX",
};

const TKBK = {
  security_id: TKBK_ID,
  ticker: "TKBK",
  theses: [{ id: "sc", name: "Synthetic Care" }],
  edgar_tickers: [],
  vendor_error: VENDOR_404,
  price_symbol: null,
  change: null,
  member_class: null,
  candidates: [],
  recommended: null,
  probed: false,
  deregistered: {
    form: "15-12G",
    filed: "2026-05-20",
    accession: "0009999831-26-000004",
    filing_url: "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000004/",
    events: [
      {
        form: "8-K15D5",
        filed: "2026-05-20",
        accession: "0009999831-26-000003",
        items: ["1.03", "3.03", "5.01"],
        item_titles: [
          "Bankruptcy or Receivership",
          "Material Modification to Rights of Security Holders",
          "Changes in Control of Registrant",
        ],
        filing_url: "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000003/",
      },
      {
        form: "8-K",
        filed: "2026-05-07",
        accession: "0009999831-26-000002",
        items: ["1.03"],
        item_titles: ["Bankruptcy or Receivership"],
        filing_url: "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000002/",
      },
    ],
  },
};

const unpriced = (...rows: Record<string, unknown>[]) => show({ ...EMPTY, unpriceable: rows });
const cand = (symbol: string) =>
  screen.getAllByTestId("adm-unpriced-candidate").find((el) => el.textContent?.startsWith(symbol)) as HTMLElement;

describe("PriceFeedRepairs — a never-priced name's candidates (W4-3)", () => {
  it("each candidate reads symbol · bars · class mark, with the member's class when they differ", () => {
    unpriced(TKAAY);
    expect(screen.getAllByTestId("adm-unpriced-candidate")).toHaveLength(3);
    expect(cand("TKAAF").textContent).toContain(
      "TKAAF · 480 bars to 2026-10-15 · foreign ordinary by the OTC naming convention; this member is an ADR",
    );
    // class unknown: nothing about a class; a failed fetch says why, plainly
    expect(cand("TKAAN").textContent).toContain("TKAAN · 30 bars to 2026-10-15");
    expect(cand("TKAAN").textContent).not.toContain("naming convention");
    expect(cand("TKAAX").textContent).toContain("TKAAX · fetch failed (404 Not Found)");
    expect(screen.getByRole("button", { name: "Adopt TKAAF" })).toBeTruthy();
  });

  it("the row header does not list again the symbols its candidate lines carry", () => {
    unpriced(TKAAY);
    const head = screen.getByTestId("adm-unpriceable-head").textContent ?? "";
    expect(head).toContain("TKAAY — no stored bar · vendor 404 Not Found");
    expect(head).toContain("Synthetic Minerals");
    expect(head).not.toContain("EDGAR");
    expect(head).not.toContain("TKAAF");
    // a symbol EDGAR lists that has no candidate line (not probed) is still named
    unpriced({ ...TKAAY, edgar_tickers: [...ONLY_SYMBOLS, "TKAAZ"] });
    expect(screen.getAllByTestId("adm-unpriceable-head")[1].textContent).toContain("EDGAR also lists TKAAZ");
  });

  it("a class-matching candidate with bars to the last pass is the recommended one, marked once", () => {
    unpriced(TKCC);
    const line = cand("TKCCX");
    expect(line.textContent).toContain(
      "TKCCX · 250 bars to 2026-10-16 · ADR by the OTC naming convention, the member's own class",
    );
    expect(within(line).getAllByTestId("adm-recommended")).toHaveLength(1);
    expect(screen.getAllByTestId("adm-recommended")).toHaveLength(1);
    const adopt = screen.getByRole("button", { name: "Adopt TKCCX" });
    expect(adopt.className).toContain("primary");
    expect(adopt).toBeEnabled(); // same class: no box, nothing to confirm
    expect(within(line).queryByRole("checkbox")).toBeNull();
  });

  it("no candidate is marked recommended when the server recommends none (the cross-class line never is)", () => {
    unpriced(TKAAY);
    expect(screen.queryByTestId("adm-recommended")).toBeNull();
    for (const b of screen.getAllByRole("button", { name: /^Adopt TKAA/ })) {
      expect(b.className).not.toContain("primary");
    }
  });

  it("an Adopt with no class conflict sends today's request, and nothing fires on mount", async () => {
    unpriced(TKAAY, TKCC);
    expect(h.adopt.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Adopt TKAAN" }));
    const calls = (h.adopt.mutate as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toStrictEqual({ securityId: TKAAY_ID, symbol: "TKAAN" }); // no confirmClass key
    await userEvent.click(screen.getByRole("button", { name: "Adopt TKCCX" }));
    expect(calls[1][0]).toStrictEqual({ securityId: TKCC_ID, symbol: "TKCCX" });
  });

  it("an Adopt with no bars or a failed fetch is disabled: the server would refuse it", () => {
    unpriced(TKAAY);
    expect(screen.getByRole("button", { name: "Adopt TKAAX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Adopt TKAAN" })).toBeEnabled();
  });

  it("a cross-class Adopt stays disabled until 'I checked the price basis' is ticked, then sends confirmClass", async () => {
    unpriced(TKAAY);
    const adopt = screen.getByRole("button", { name: "Adopt TKAAF" });
    const box = within(cand("TKAAF")).getByRole("checkbox", { name: "I checked the price basis" });
    expect(adopt).toBeDisabled();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1); // only the cross-class line asks
    await userEvent.click(adopt); // a disabled button fires nothing
    expect(h.adopt.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();

    await userEvent.click(box);
    expect(adopt).toBeEnabled();
    await userEvent.click(box); // un-ticking closes it again
    expect(adopt).toBeDisabled();
    await userEvent.click(box);
    await userEvent.click(adopt);
    const calls = (h.adopt.mutate as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toStrictEqual({ securityId: TKAAY_ID, symbol: "TKAAF", confirmClass: true });
  });

  it("the box belongs to its candidate: ticking one line opens no other", async () => {
    const twoOrdinary = {
      ...TKAAY,
      candidates: [
        TKAAY.candidates[0],
        { ...TKAAY.candidates[0], symbol: "TKAAG", bars: 12 },
      ],
    };
    unpriced(twoOrdinary);
    await userEvent.click(within(cand("TKAAF")).getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Adopt TKAAF" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Adopt TKAAG" })).toBeDisabled();
  });

  it("an unknown class on either side asks for no box (the server's rule: both known and different)", () => {
    unpriced({ ...TKAAY, member_class: null });
    expect(within(cand("TKAAF")).queryByRole("checkbox")).toBeNull();
    expect(cand("TKAAF").textContent).toContain("TKAAF · 480 bars to 2026-10-15 · foreign ordinary by the OTC naming convention");
    expect(cand("TKAAF").textContent).not.toContain("this member");
    expect(screen.getByRole("button", { name: "Adopt TKAAF" })).toBeEnabled();
  });

  it("a refused adopt (409) shows the server's own message, once", () => {
    const why =
      "TKAAF is a foreign ordinary share and TKAAY is an ADR (class marks by convention: EDGAR's /ADR name, the OTC fifth letter): pricing one class on the other puts the cap off by the ADS ratio — check the price basis and send confirm_class to adopt it";
    h.adopt = { ...m(), isError: true, error: { detail: why } };
    unpriced(TKAAY);
    expect(screen.getAllByText(why)).toHaveLength(1);
  });

  it("an adopted row shows what was applied and offers no candidate to adopt again", () => {
    unpriced({ ...TKAAY, change: { ...CHANGE, security_id: TKAAY_ID, ticker: "TKAAY", new_symbol: "TKAAF", name_changed: false } });
    expect(screen.getByTestId("adm-applied-line").textContent).toContain("set to TKAAF");
    expect(screen.queryByTestId("adm-unpriced-candidate")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Adopt / })).toBeNull();
  });

  it("a row from an older server (none of the new fields) keeps today's display", () => {
    unpriced(WONDF);
    const row = screen.getByTestId("adm-unpriceable-row");
    expect(screen.getByTestId("adm-unpriceable-head").textContent).toBe(
      "WONDF — no stored bar · vendor 404 Not Found · EDGAR lists no symbol · The Everything Exchange",
    );
    expect(screen.queryByTestId("adm-unpriced-candidate")).toBeNull();
    expect(screen.queryByTestId("adm-recommended")).toBeNull();
    expect(screen.queryByTestId("adm-deregistered")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    // the actions in today's order, the removal link last and not labeled as a recommendation
    const acts = within(screen.getByTestId("adm-unpriceable-actions"));
    const order = [...row.querySelectorAll("a, button, input")].map((el) => el.getAttribute("aria-label") ?? el.textContent);
    expect(order).toEqual([
      "vendor symbol for WONDF",
      "Set price symbol",
      "reason to keep WONDF unpriced",
      "Acknowledge — keep unpriced",
      "Remove from basket →",
    ]);
    expect(acts.getByRole("link", { name: "Remove from basket →" }).getAttribute("href")).toBe("/workbench");
  });
});

describe("PriceFeedRepairs — a deregistered never-priced name (W4-3)", () => {
  it("shows the Form 15, its filed date, each event's SEC item titles and a link to every filing", () => {
    unpriced(TKBK);
    const ev = screen.getByTestId("adm-deregistered");
    expect(ev.textContent).toContain("Deregistered");
    expect(ev.textContent).toContain("15-12G filed 2026-05-20");
    const events = within(ev).getAllByTestId("adm-dereg-event");
    expect(events.map((e) => e.textContent)).toEqual([
      "8-K15D5 filed 2026-05-20 — Bankruptcy or Receivership; Material Modification to Rights of Security Holders; Changes in Control of Registrant",
      "8-K filed 2026-05-07 — Bankruptcy or Receivership",
    ]);
    const hrefs = Object.fromEntries(
      within(ev)
        .getAllByRole("link")
        .map((a) => [a.textContent, a.getAttribute("href")]),
    );
    expect(hrefs["15-12G"]).toBe("https://www.sec.gov/Archives/edgar/data/9999831/000999983126000004/");
    expect(within(events[0]).getByRole("link", { name: "8-K15D5" }).getAttribute("href")).toBe(
      "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000003/",
    );
    expect(within(events[1]).getByRole("link", { name: "8-K" }).getAttribute("href")).toBe(
      "https://www.sec.gov/Archives/edgar/data/9999831/000999983126000002/",
    );
  });

  it("puts 'Remove from basket →' first among the actions, labeled as the recommended one", async () => {
    unpriced(TKBK);
    const acts = screen.getByTestId("adm-unpriceable-actions");
    const first = acts.querySelector("a, button, input") as HTMLElement;
    expect(first.textContent).toBe("Remove from basket →");
    expect(first.getAttribute("href")).toBe("/workbench"); // the basket editor the panel already links
    expect(within(acts).getByTestId("adm-recommended").textContent).toBe("recommended");
    expect(screen.getAllByTestId("adm-recommended")).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Remove from basket →" })).toHaveLength(1);
    // the other two choices stay on the row, after it
    expect(within(acts).getByRole("button", { name: "Acknowledge — keep unpriced" })).toBeTruthy();
    expect(within(acts).getByRole("button", { name: "Set price symbol" })).toBeTruthy();
    expect(h.ack.mutate as ReturnType<typeof vi.fn>).not.toHaveBeenCalled();
  });

  it("says nothing twice: the header carries no EDGAR clause (the evidence says it) and no candidate lines show", () => {
    unpriced(TKBK);
    expect(screen.getByTestId("adm-unpriceable-head").textContent).toBe(
      "TKBK — no stored bar · vendor 404 Not Found · Synthetic Care",
    );
    expect(screen.queryByTestId("adm-unpriced-candidate")).toBeNull();
  });

  it("an event with no evidence items is just its form and date; a filing with no link is plain words", () => {
    const bare = {
      ...TKBK,
      deregistered: {
        ...TKBK.deregistered,
        filing_url: null,
        events: [{ form: "8-K15D5", filed: "2026-05-20", accession: "0009999831-26-000003", items: [], item_titles: [], filing_url: null }],
      },
    };
    unpriced(bare);
    const ev = screen.getByTestId("adm-deregistered");
    expect(within(ev).getByTestId("adm-dereg-event").textContent).toBe("8-K15D5 filed 2026-05-20");
    expect(ev.textContent).toContain("15-12G filed 2026-05-20");
    expect(within(ev).queryAllByRole("link")).toHaveLength(0);
  });

  it("a row without the evidence is not marked recommended, whatever else it shows", () => {
    unpriced(TKAAY, TKBK);
    const [aay, bk] = screen.getAllByTestId("adm-unpriceable-row");
    expect(within(aay).queryByTestId("adm-recommended")).toBeNull();
    expect(within(bk).getByTestId("adm-recommended")).toBeTruthy();
    expect(within(aay).getByRole("link", { name: "Remove from basket →" })).toBeTruthy(); // still offered, unlabeled
  });
});

// W6-4 — Massive.com's reference evidence on a row (the probe's measured shapes, 2026-11-25): INDV renamed on EDGAR and
// delisted on Massive.com; TMRC never priced and unknown to Massive.com (404); a warrant that traded tonight.
const REF = {
  symbol: "INDV",
  read_on: "2026-11-25",
  listed: true,
  active: false,
  type: "CS",
  delisted_utc: "2026-11-03",
  traded_on: null,
};

describe("PriceFeedRepairs — Massive.com's reference evidence (W6-4)", () => {
  it("a row says in one line what Massive.com lists: its type, whether active, the delisting, tonight's trade", () => {
    show({
      ...NIGHT,
      renamed: [{ ...HLLK, massive_ref: REF }, BRR],
      unpriceable: [{ ...WONDF, massive_ref: { ...REF, symbol: "WONDF", listed: false, active: null, type: null, delisted_utc: null } }],
    });
    const [hllk, brr] = screen.getAllByTestId("adm-renamed-row");
    expect(within(hllk).getByTestId("adm-massive-ref").textContent).toBe("Massive.com: CS, inactive, delisted 2026-11-03");
    expect(within(brr).queryByTestId("adm-massive-ref")).toBeNull(); // no evidence, no line
    const u = screen.getByTestId("adm-unpriceable-row");
    expect(within(u).getByTestId("adm-massive-ref").textContent).toBe("Massive.com lists no ticker WONDF");
    expect(within(u).getByTestId("adm-massive-ref").className).toContain("adm-sub"); // evidence, never loud
  });

  it("words a warrant that traded tonight, and an overview not read yet says only the trade", () => {
    expect(massiveClause({ ...REF, symbol: "ACLEW", active: true, type: "WARRANT", delisted_utc: null, traded_on: "2026-11-25" })).toBe(
      "Massive.com: WARRANT, active, traded 2026-11-25",
    );
    expect(massiveClause({ symbol: "SNDK", read_on: null, listed: null, active: null, type: null, delisted_utc: null, traded_on: "2026-11-25" })).toBe(
      "Massive.com: traded 2026-11-25",
    );
    expect(massiveClause({ symbol: "SNDK", read_on: null, listed: null, active: null, type: null, delisted_utc: null, traded_on: null })).toBe("");
    expect(massiveClause(null)).toBe("");
    expect(massiveClause(undefined)).toBe("");
  });

  it("a row from an older server (no massive_ref) shows no line", () => {
    show(NIGHT);
    expect(screen.queryByTestId("adm-massive-ref")).toBeNull();
  });
});

describe("repairHandledIds", () => {
  it("is every security the panel renders a row for, and empty without a panel", () => {
    const ids = repairHandledIds(NIGHT as unknown as AdminPriceRepairsOut);
    expect([...ids].sort()).toEqual([BRR_ID, GTLS_ID, HLLK_ID, WONDF_ID].sort());
    expect(repairHandledIds(undefined).size).toBe(0);
  });
});

describe("repairVerb — the two SYSTEM authors read plainly", () => {
  it("names the refresh's pin and the probe's automatic adopt; the operator's own verbs are unchanged", () => {
    expect(repairVerb("set", "system:rename-pin")).toBe("pinned automatically (company renamed) to");
    expect(repairVerb("adopt", "system:auto-adopt")).toBe("adopted automatically");
    expect(repairVerb("adopt", "operator:adopt")).toBe("adopted");
    expect(repairVerb("set", "operator:set")).toBe("set to");
  });
});
