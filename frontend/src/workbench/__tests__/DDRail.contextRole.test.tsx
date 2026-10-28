import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ScoredMemberOut } from "../../api/hooks";
import { DDRail } from "../DDRail";
import { CONTEXT_ROLES, CONTEXT_ROLE_DESCRIPTION, CONTEXT_ROLE_LABEL } from "../format";

// The CONTEXT ROLE control on the DD rail: ONE select per company — "— none" plus the six roles, each option's
// hover its description. A change hands the parent (role | null) at once; "— none" clears (the visible inverse,
// WB#1). Read-only (no writer): a set role is a text label, an unset one renders nothing (WB#3). Operator-
// authored display metadata — the rail never touches the call.

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

describe("DDRail — the context role control", () => {
  it("is one select labeled 'Context role' offering '— none' plus the six roles in order", () => {
    render(<DDRail member={member} contextRole={null} onContextRole={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Context role" });
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "— none",
      "Payer",
      "Counter-case",
      "Direct read",
      "Participant",
      "Benchmark",
      "Financier",
    ]);
    expect(options.map((o) => (o as HTMLOptionElement).value)).toEqual([
      "",
      "payer",
      "counter_case",
      "direct_read",
      "participant",
      "benchmark",
      "financier",
    ]);
    // each role's hover is its description, verbatim
    for (const r of CONTEXT_ROLES) {
      const opt = within(select).getByRole("option", { name: CONTEXT_ROLE_LABEL[r] });
      expect(opt).toHaveAttribute("title", CONTEXT_ROLE_DESCRIPTION[r]);
    }
    expect(CONTEXT_ROLE_DESCRIPTION.direct_read).toBe(
      "Not in the business, but its numbers forecast what the voting names report.",
    );
  });

  it("reads the company's current role from the spine (the parent passes it)", () => {
    render(<DDRail member={member} contextRole="benchmark" onContextRole={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Context role" })).toHaveValue("benchmark");
  });

  it("an unset role reads '— none' (never a default role)", () => {
    render(<DDRail member={member} onContextRole={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Context role" })).toHaveValue("");
  });

  it("choosing a role calls the writer with the company's id and the role", async () => {
    const user = userEvent.setup();
    const onContextRole = vi.fn();
    render(<DDRail member={member} contextRole={null} onContextRole={onContextRole} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Context role" }), "counter_case");
    expect(onContextRole).toHaveBeenCalledTimes(1);
    expect(onContextRole).toHaveBeenCalledWith("s-x", "counter_case");
  });

  it("choosing '— none' clears: the writer gets null (the visible inverse)", async () => {
    const user = userEvent.setup();
    const onContextRole = vi.fn();
    render(<DDRail member={member} contextRole="payer" onContextRole={onContextRole} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Context role" }), "— none");
    expect(onContextRole).toHaveBeenCalledTimes(1);
    expect(onContextRole).toHaveBeenCalledWith("s-x", null);
  });

  it("disables the select while a promote is in flight", () => {
    render(
      <DDRail member={member} contextRole="payer" onContextRole={vi.fn()} contextRolePending />,
    );
    expect(screen.getByRole("combobox", { name: "Context role" })).toBeDisabled();
  });

  it("read-only (no writer): a set role is quiet text with its hover, and no select renders", () => {
    render(<DDRail member={member} contextRole="financier" />);
    expect(screen.queryByRole("combobox", { name: "Context role" })).toBeNull();
    const label = screen.getByText("Financier");
    expect(label).toHaveAttribute("title", CONTEXT_ROLE_DESCRIPTION.financier);
  });

  it("read-only with no role renders nothing at all (no '—' placeholder, WB#3)", () => {
    const { container } = render(<DDRail member={member} contextRole={null} />);
    expect(container.querySelector(".dd-role")).toBeNull();
    expect(screen.queryByText(/Context role/)).toBeNull();
  });

  it("sits beside the value-chain links without disturbing them (the segment control still renders)", () => {
    render(
      <DDRail
        member={member}
        contextRole={null}
        onContextRole={vi.fn()}
        segmentControl={{ options: ["reactors"], current: ["reactors"], onChange: vi.fn(), pending: false }}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "reactors" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Context role" })).toBeInTheDocument();
  });
});
