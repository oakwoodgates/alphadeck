import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ScoredMemberOut } from "../../api/hooks";
import { DDRail } from "../DDRail";

// The #4 value-chain mover on the DD rail (chain-editing Phase 1): a flat multi-select of the real links
// that REPLACES the old read-only segment cell. One checkbox per link, `checked` reflects the name's live
// memberships, toggling hands the NEXT label set to the parent's immediate-promote. Discovered is the
// parent's automatic floor (excluded from `options`); no links yet → a quiet hint, in no link → the
// "Unsorted (Discovered)" line. Read-only (no prop) renders nothing new.

// the hook-using child panels aren't under test here — stub them so this stays a focused unit
vi.mock("../CatalystFactForm", () => ({ CatalystFactForm: () => null }));
vi.mock("../FactsPanel", () => ({ FactsPanel: () => null }));

const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
const member = {
  security_id: "s-x",
  ticker: "HIMS",
  name: "Hims & Hers",
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
  instrument_kind: "equity",
  segment: "reactors",
  purity: fig(null, null),
  runway: fig(null, null),
  catalysts: fig(0, 0),
  dilution: fig(null, null),
  market_cap: fig(null, null),
  fit: "unrated",
  unconfirmed_estimates: 0,
  thin_price_history: false,
} as unknown as ScoredMemberOut;

const ctl = (over: Partial<Parameters<typeof DDRail>[0]["segmentControl"]> = {}) => ({
  options: ["reactors", "fuel"],
  current: ["reactors"],
  onChange: vi.fn(),
  pending: false,
  ...over,
});

describe("DDRail — the value-chain segment control (#4)", () => {
  it("renders one checkbox per real link; `checked` reflects the current memberships", () => {
    render(<DDRail member={member} segmentControl={ctl({ current: ["reactors"] })} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2); // one per option — Discovered is the parent's floor, not an option
    expect(screen.getByRole("checkbox", { name: "reactors" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "fuel" })).not.toBeChecked();
  });

  it("toggling a link ON hands onChange the union (multi-membership)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["reactors"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "fuel" }));
    expect(onChange).toHaveBeenCalledWith(["reactors", "fuel"]);
  });

  it("toggling the last link OFF hands onChange the empty set (→ the reconcile floors to Discovered)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["reactors"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "reactors" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("empty options → a quiet hint, NOT an empty control", () => {
    render(<DDRail member={member} segmentControl={ctl({ options: [], current: [] })} />);
    expect(screen.getByText(/no links yet/i)).toBeInTheDocument();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("empty current → the 'Unsorted (Discovered)' line, no box checked", () => {
    render(<DDRail member={member} segmentControl={ctl({ current: [] })} />);
    expect(screen.getByText(/unsorted \(discovered\)/i)).toBeInTheDocument();
    screen.getAllByRole("checkbox").forEach((b) => expect(b).not.toBeChecked());
  });

  it("disables the checkboxes while a promote is pending", () => {
    render(<DDRail member={member} segmentControl={ctl({ pending: true })} />);
    screen.getAllByRole("checkbox").forEach((b) => expect(b).toBeDisabled());
  });

  it("read-only (no segmentControl) renders no control and no old segment cell", () => {
    render(<DDRail member={member} />);
    expect(screen.queryByText(/value-chain links/i)).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByText("segment")).toBeNull(); // the read-only cell is retired
  });
});
