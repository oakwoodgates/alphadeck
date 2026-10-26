import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Keyboard stepping through the basket: ↑/↓ on a focused row moves to the previous / next VISIBLE row — in the
// rendered order (the active lens and sort count), skipping a folded group — and with the per-name panel open
// the panel follows. Focus always moves; Enter / Space and Esc keep their jobs. A name shown under several
// value-chain links resolves to its first row, so a later link-row takes focus and leaves the panel alone.
const fx = vi.hoisted(() => {
  const m = (ticker: string, segment: string | null = null) => ({
    ticker, role: "core", security_id: `s-${ticker.toLowerCase()}`, segment, detail: null,
    thesis_fit: null, authored_by: "operator_set",
  });
  const base = { narrative: "n", ticker: null, evidence: [], catalysts: [], kill_criteria: [], position: null };
  const member = (ticker: string) => ({
    security_id: `s-${ticker.toLowerCase()}`, ticker, verdict: null, conviction_grade: null,
    confirmation_grade: "core", entry_grade: null, confidence: null, exit_by: null, arm_until: "2026-07-21",
    lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
  });
  return {
    // call-state groups: Armed (A), Watch (B), Quiet (C, D) — the rendered order A, B, C, D
    flat: {
      ...base, id: "t-nav", name: "Keys", segments: [],
      basket: ["A", "B", "C", "D"].map((t) => m(t)),
    },
    // the value-chain lens: KTTA sits in TWO links (two rows, one security_id)
    chain: {
      ...base, id: "t-chain", name: "Chain",
      segments: [{ label: "Clinics", descriptor: null }, { label: "Developers", descriptor: null }],
      basket: [m("KTTA", "Clinics"), m("ATAI", "Clinics"), m("KTTA", "Developers"), m("LONE", "Developers")],
    },
    thesis: undefined as unknown,
    call: {
      thesis_id: "t-nav", asof: "2026-07-11", state: "armed", verdict: "starter_entry",
      conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", expression: "STARTER",
      exit_by: "2026-11-11", arm_until: "2026-07-17", catalyst_surface: [], confidence: 0.54,
      key_conviction: { turned: true, label: "Conviction", detail: "" },
      key_confirmation: { turned: true, label: "Confirmation", detail: "" },
      triggers_fired: [], risk_signals: [], missing: [], counter_case: "",
      armed_members: [{ ...member("A"), verdict: "starter_entry", conviction_grade: "core", entry_grade: "flip", confidence: 0.54, exit_by: "2026-11-11", arm_until: "2026-07-17" }],
      watch_members: [member("B")],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.thesis === fx.flat ? fx.call : undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: { members: [] }, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

// the URL owns the selection in the app; the harness plays that controlled-prop role
function Harness({ thesisId }: { thesisId: string }) {
  const [name, setName] = useState<string | null>(null);
  return (
    <Cockpit
      thesisId={thesisId}
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={name}
      onSelectName={setName}
      railOpen
      onRailChange={() => {}}
    />
  );
}

const rowOf = (tk: string, nth = 0) =>
  screen.getAllByText(tk, { selector: "td.tk" })[nth].closest("tr") as HTMLElement;
const panelTicker = (c: HTMLElement) => c.querySelector(".npanel .np-tk")?.textContent ?? null;
// fireEvent returns false when the handler called preventDefault
const key = (el: HTMLElement, k: string, mods: Record<string, boolean> = {}) =>
  fireEvent.keyDown(el, { key: k, ...mods });

beforeEach(() => {
  fx.thesis = fx.flat;
});

describe("Cockpit — arrow-key stepping through the basket", () => {
  it("moves FOCUS to the next / previous row with no panel open, stopping at both ends", () => {
    const { container } = render(<Harness thesisId="t-nav" />);
    rowOf("A").focus();
    expect(key(rowOf("A"), "ArrowDown")).toBe(false); // the row owns the arrow: the scroller must not also move
    expect(document.activeElement).toBe(rowOf("B"));
    key(rowOf("B"), "ArrowDown");
    key(rowOf("C"), "ArrowDown");
    expect(document.activeElement).toBe(rowOf("D"));
    key(rowOf("D"), "ArrowDown"); // the last row: nothing further
    expect(document.activeElement).toBe(rowOf("D"));
    key(rowOf("D"), "ArrowUp");
    expect(document.activeElement).toBe(rowOf("C"));
    // stepping alone never opens the panel — Enter does that
    expect(container.querySelector(".npanel")).toBeNull();
    rowOf("A").focus();
    key(rowOf("A"), "ArrowUp"); // the first row
    expect(document.activeElement).toBe(rowOf("A"));
  });

  it("with the panel open, the panel follows focus — and Enter / Esc keep their jobs on the new row", () => {
    const { container } = render(<Harness thesisId="t-nav" />);
    fireEvent.click(rowOf("A"));
    expect(panelTicker(container)).toBe("A");
    key(rowOf("A"), "ArrowDown");
    expect(panelTicker(container)).toBe("B");
    expect(rowOf("B").getAttribute("aria-selected")).toBe("true");
    expect(rowOf("A").getAttribute("aria-selected")).toBe("false");
    expect(document.activeElement).toBe(rowOf("B"));
    key(rowOf("B"), "ArrowDown");
    key(rowOf("C"), "ArrowUp");
    expect(panelTicker(container)).toBe("B");
    // Enter acts on the FOCUSED row — the one the panel now shows — so it closes it
    key(rowOf("B"), "Enter");
    expect(container.querySelector(".npanel")).toBeNull();
    // and stepping with the panel closed no longer opens one
    key(rowOf("B"), "ArrowDown");
    expect(container.querySelector(".npanel")).toBeNull();
  });

  it("skips a folded group — it stays mounted, but is not a stop", () => {
    render(<Harness thesisId="t-nav" />);
    const watch = [...document.querySelectorAll("tr.grp .grp-h")].find(
      (b) => b.querySelector(".lbl")?.textContent === "Watch",
    ) as HTMLElement;
    fireEvent.click(watch); // fold Watch (B)
    key(rowOf("A"), "ArrowDown");
    expect(document.activeElement).toBe(rowOf("C"));
    key(rowOf("C"), "ArrowUp");
    expect(document.activeElement).toBe(rowOf("A"));
  });

  it("steps in the RENDERED order — the active sort counts", () => {
    render(<Harness thesisId="t-nav" />);
    // Ticker, desc: within the Quiet group D now precedes C
    fireEvent.click(within(screen.getByRole("columnheader", { name: "Ticker" })).getByRole("button"));
    key(rowOf("B"), "ArrowDown");
    expect(document.activeElement).toBe(rowOf("D"));
    key(rowOf("D"), "ArrowDown");
    expect(document.activeElement).toBe(rowOf("C"));
  });

  it("ignores a modified arrow (shift / ctrl / alt / meta) and leaves the default to the browser", () => {
    render(<Harness thesisId="t-nav" />);
    rowOf("A").focus();
    for (const mods of [{ shiftKey: true }, { ctrlKey: true }, { altKey: true }, { metaKey: true }]) {
      expect(key(rowOf("A"), "ArrowDown", mods)).toBe(true); // not handled -> not default-prevented
      expect(document.activeElement).toBe(rowOf("A"));
    }
  });

  it("value-chain lens: a repeated name takes focus but leaves the panel on its first row", () => {
    fx.thesis = fx.chain;
    const { container } = render(<Harness thesisId="t-chain" />);
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    // rows in order: Clinics → KTTA, ATAI; Developers → KTTA (the repeat), LONE
    fireEvent.click(rowOf("KTTA", 0));
    expect(panelTicker(container)).toBe("KTTA");
    key(rowOf("KTTA", 0), "ArrowDown"); // ATAI — its own first row: the panel follows
    expect(panelTicker(container)).toBe("ATAI");
    key(rowOf("ATAI"), "ArrowDown"); // KTTA again, under Developers: focus moves…
    expect(document.activeElement).toBe(rowOf("KTTA", 1));
    expect(panelTicker(container)).toBe("ATAI"); // …the panel stays (KTTA resolves to its FIRST row)
    key(rowOf("KTTA", 1), "ArrowDown"); // LONE: the panel follows again
    expect(document.activeElement).toBe(rowOf("LONE"));
    expect(panelTicker(container)).toBe("LONE");
  });
});
