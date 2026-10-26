import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ScoredMemberOut } from "../../api/hooks";
import { DDRail } from "../DDRail";
import { SILENT_DESCRIPTORS } from "../useChainDraft";

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
    // one per option, plus the two silent links the rail always lists — Discovered is the parent's floor
    expect(boxes).toHaveLength(4);
    expect(screen.getByRole("checkbox", { name: "reactors" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "fuel" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Context" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Bench" })).not.toBeChecked();
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
    // no REAL link to check — only the two silent links (always listed) remain
    expect(screen.queryAllByRole("checkbox").map((b) => b.closest("label")?.textContent)).toEqual([
      "Context",
      "Bench",
    ]);
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

// The two SILENT links (Context / Bench): the rail always lists them under the real links, behind a quiet
// divider. A company is silent XOR voting, so a toggle hands the parent a payload with ONE row for a silent
// link — never a silent label beside another — and the checkboxes are the visible inverse (WB#1).
describe("DDRail — Context and Bench under the real links", () => {
  it("lists the real links, then a quiet divider, then Context and Bench (DOM order)", () => {
    render(<DDRail member={member} segmentControl={ctl()} />);
    const order = screen.getAllByRole("checkbox").map((b) => b.closest("label")?.textContent);
    expect(order).toEqual(["reactors", "fuel", "Context", "Bench"]);
    // the divider sits between the real links and the pair, and says what the pair is
    const divider = screen.getByText(/never counts toward the call/i).closest(".dd-seg-divider") as HTMLElement;
    const real = screen.getByRole("checkbox", { name: "fuel" });
    const ctx = screen.getByRole("checkbox", { name: "Context" });
    expect(real.compareDocumentPosition(divider) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(divider.compareDocumentPosition(ctx) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("each silent row carries its fixed descriptor as the hover", () => {
    render(<DDRail member={member} segmentControl={ctl()} />);
    expect(screen.getByRole("checkbox", { name: "Context" }).closest("label")).toHaveAttribute(
      "title",
      SILENT_DESCRIPTORS.Context,
    );
    expect(screen.getByRole("checkbox", { name: "Bench" }).closest("label")).toHaveAttribute(
      "title",
      SILENT_DESCRIPTORS.Bench,
    );
  });

  it("checking Context on a multi-link name hands onChange ONE label — [Context]", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["reactors", "fuel"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "Context" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(["Context"]);
  });

  it("checking Bench from Context swaps (never both)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["Context"], onChange })} />);
    expect(screen.getByRole("checkbox", { name: "Context" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Bench" }));
    expect(onChange).toHaveBeenCalledWith(["Bench"]);
  });

  it("checking a real link on a Context name hands onChange just that link (it leaves Context)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["Context"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "fuel" }));
    expect(onChange).toHaveBeenCalledWith(["fuel"]);
  });

  it("unchecking Context hands the empty set (→ the Discovered floor) — the visible inverse", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["Context"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "Context" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("real links keep OPTION order in the payload whichever was checked first", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DDRail member={member} segmentControl={ctl({ current: ["fuel"], onChange })} />);
    await user.click(screen.getByRole("checkbox", { name: "reactors" }));
    expect(onChange).toHaveBeenCalledWith(["reactors", "fuel"]);
  });

  it("a silent name reads as placed: no 'Unsorted (Discovered)' line", () => {
    render(<DDRail member={member} segmentControl={ctl({ current: ["Bench"] })} />);
    expect(screen.getByRole("checkbox", { name: "Bench" })).toBeChecked();
    expect(screen.queryByText(/unsorted \(discovered\)/i)).toBeNull();
  });

  it("with no real links the pair still lists (the hint stays, nothing else is faked)", () => {
    render(<DDRail member={member} segmentControl={ctl({ options: [], current: [] })} />);
    expect(screen.getByText(/no links yet/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Context" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Bench" })).toBeInTheDocument();
  });

  it("disables the silent checkboxes too while a promote is pending", () => {
    render(<DDRail member={member} segmentControl={ctl({ pending: true })} />);
    expect(screen.getByRole("checkbox", { name: "Context" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Bench" })).toBeDisabled();
  });
});
