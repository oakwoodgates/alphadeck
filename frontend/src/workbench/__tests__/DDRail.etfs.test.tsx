import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ScoredMemberOut } from "../../api/hooks";
import { DDRail } from "../DDRail";
import { ETFS, SILENT_DESCRIPTORS } from "../useChainDraft";

// ETFs on the DD rail — the funds-only rule on the OFFERING side (the promote enforces it on the write side).
// A COMPANY is never offered ETFs (only Context / Bench, under its real links) unless it already sits there
// (a state the server refuses), when ETFs stays listed so the operator can move it out (WB#1). A FUND is
// offered ONLY ETFs, and only when it is not already there (WB#3: an always-checked box says nothing) — a
// fund outside ETFs (saved before the link existed, or marked a fund after it was placed) gets one click to
// move it in; taking a fund off the card is the row's own remove.

// the hook-using panels aren't under test here; the fund branch's SleeveRail is stubbed to render only the
// controls the rail hands it (its holdings pull + display-signals reads are its own tests' business)
vi.mock("../CatalystFactForm", () => ({ CatalystFactForm: () => null }));
vi.mock("../FactsPanel", () => ({ FactsPanel: () => null }));
vi.mock("../SleeveRail", () => ({
  SleeveRail: ({ roleControl }: { roleControl?: ReactNode }) => (
    <div data-testid="sleeve-rail">{roleControl}</div>
  ),
}));

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const base = {
  name: null,
  sector: null,
  exchange: null,
  category: null,
  origin: null,
  foreign_filer_form: null,
  price_symbol: null,
  business_type: null,
  business_supersector: null,
  business_type_override: null,
  royalty: false,
  purity: fig(null, null),
  runway: fig(null, null),
  catalysts: fig(0, 0),
  dilution: fig(null, null),
  market_cap: fig(null, null),
  fit: "unrated",
  unconfirmed_estimates: 0,
  thin_price_history: false,
};
const company = {
  ...base,
  security_id: "s-co",
  ticker: "QZRA",
  instrument_kind: "equity",
  segment: "reactors",
} as unknown as ScoredMemberOut;
const fund = {
  ...base,
  security_id: "s-fund",
  ticker: "FNDA",
  instrument_kind: "etf",
  segment: ETFS,
} as unknown as ScoredMemberOut;

const ctl = (over: Partial<Parameters<typeof DDRail>[0]["segmentControl"]> = {}) => ({
  options: ["reactors", "fuel"],
  current: ["reactors"],
  onChange: vi.fn(),
  pending: false,
  ...over,
});

describe("DDRail — a company is never offered ETFs", () => {
  it("lists the real links, then Context and Bench — no ETFs box", () => {
    render(<DDRail member={company} segmentControl={ctl()} />);
    const order = screen.getAllByRole("checkbox").map((b) => b.closest("label")?.textContent);
    expect(order).toEqual(["reactors", "fuel", "Context", "Bench"]);
    expect(screen.queryByRole("checkbox", { name: ETFS })).toBeNull();
  });

  it("a company already sitting in ETFs sees it listed, checked, and unchecking it is the way out", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={company} segmentControl={ctl({ current: [ETFS], onChange })} />);
    const box = screen.getByRole("checkbox", { name: ETFS });
    expect(box).toBeChecked();
    expect(box.closest("label")).toHaveAttribute("title", SILENT_DESCRIPTORS[ETFS]);
    await user.click(box);
    expect(onChange).toHaveBeenCalledWith([]);
  });
});

describe("DDRail — a fund is offered only ETFs", () => {
  it("a fund in ETFs (the normal case) shows no link control at all", () => {
    render(<DDRail member={fund} segmentControl={ctl({ current: [ETFS] })} />);
    expect(screen.getByTestId("sleeve-rail")).toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("a fund outside ETFs is offered exactly one box — ETFs — and checking it moves the fund there", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={fund} segmentControl={ctl({ current: [], onChange })} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.map((b) => b.closest("label")?.textContent)).toEqual([ETFS]); // no real link, no Context/Bench
    expect(screen.getByText(/can't be saved until it moves there/i)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: ETFS }));
    expect(onChange).toHaveBeenCalledWith([ETFS]);
  });

  it("a fund sitting in a real link is offered ETFs too (one click collapses it to ONE ETFs row)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={fund} segmentControl={ctl({ current: ["reactors", "fuel"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: ETFS }));
    expect(onChange).toHaveBeenCalledWith([ETFS]);
  });

  it("the box is disabled while a promote is pending", () => {
    render(<DDRail member={fund} segmentControl={ctl({ current: [], pending: true })} />);
    expect(screen.getByRole("checkbox", { name: ETFS })).toBeDisabled();
  });

  it("read-only (no segmentControl) renders no link control for a fund", () => {
    render(<DDRail member={fund} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });
});
