import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminPriceRepairsOut } from "../../api/hooks";
import { PriceFeedRepairs, repairHandledIds } from "../PriceFeedRepairs";

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
}));

vi.mock("../../api/hooks", () => ({
  useAdoptPriceSymbol: () => h.adopt,
  useSetPriceSymbol: () => h.set,
  useUndoPriceSymbolChange: () => h.undo,
  useAcknowledgeUnpriceable: () => h.ack,
  useRevokeAcknowledgment: () => h.revoke,
}));

const m = () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null });

beforeEach(() => {
  h.adopt = m();
  h.set = m();
  h.undo = m();
  h.ack = m();
  h.revoke = m();
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

describe("repairHandledIds", () => {
  it("is every security the panel renders a row for, and empty without a panel", () => {
    const ids = repairHandledIds(NIGHT as unknown as AdminPriceRepairsOut);
    expect([...ids].sort()).toEqual([BRR_ID, GTLS_ID, HLLK_ID, WONDF_ID].sort());
    expect(repairHandledIds(undefined).size).toBe(0);
  });
});
