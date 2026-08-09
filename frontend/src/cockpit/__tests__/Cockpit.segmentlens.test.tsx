import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The value-chain (segment) LENS: a THIRD basket toggle grouping by `basket_member.segment` (the
// chain drafter's value-chain link). The load-bearing checks: the third toggle appears only when the
// basket is decomposed; a multi-segment name (the Rainbow Rush shape: KTTA in 2 links = 2 rows, same
// security_id) shows ONCE in call-state (deduped) but under EACH link in the value-chain lens; the
// null-segment name lands in a keep-visible "Unsegmented" group; the toggle is reversible.
const fx = vi.hoisted(() => {
  const m = (ticker: string, sid: string, segment: string | null) => ({
    ticker, role: "core", security_id: sid, segment, detail: null, thesis_fit: null,
    authored_by: "operator_set",
  });
  const base = { narrative: "n", ticker: null, evidence: [], catalysts: [], kill_criteria: [], position: null };
  const withSeg = {
    ...base,
    id: "t-seg", name: "Rainbow Rush",
    segments: [
      { label: "Ketamine Clinics & Therapy Delivery", descriptor: "catalyst-rich" },
      { label: "Psychedelic & Ketamine Drug Developers", descriptor: null },
    ],
    basket: [
      m("KTTA", "s-ktta", "Ketamine Clinics & Therapy Delivery"),
      m("KTTA", "s-ktta", "Psychedelic & Ketamine Drug Developers"), // same name, second link
      m("ATAI", "s-atai", "Psychedelic & Ketamine Drug Developers"),
      m("LONE", "s-lone", null), // unsegmented — must stay visible
    ],
  };
  const noSeg = { ...base, id: "t-noseg", name: "HIMS", segments: [], basket: [m("HIMS", "s-hims", null)] };
  return { withSeg, noSeg, thesis: withSeg as unknown, scored: { members: [] as unknown[] } };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t"
      asof="2026-08-09"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
    />,
  );
}

const headerLabels = (c: HTMLElement) => [...c.querySelectorAll(".grp-h .lbl")].map((e) => e.textContent);

describe("Cockpit — the value-chain (segment) lens", () => {
  beforeEach(() => {
    fx.thesis = fx.withSeg;
  });

  it("adds a 'value chain' toggle; groups by link with a multi-segment name under EACH", () => {
    const { container } = renderCockpit();
    expect(screen.getByRole("button", { name: "call state" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "business type" })).toBeInTheDocument();
    const vc = screen.getByRole("button", { name: "value chain" });

    // default (call-state) lens: KTTA's two link-rows collapse to ONE (the dedupe from Slice 1)
    expect(screen.getAllByText("KTTA")).toHaveLength(1);

    fireEvent.click(vc);
    // the links become the headers — authored order, "Unsegmented" last (keep-visible tail)
    expect(headerLabels(container)).toEqual([
      "Ketamine Clinics & Therapy Delivery",
      "Psychedelic & Ketamine Drug Developers",
      "Unsegmented",
    ]);
    expect(screen.getAllByText("KTTA")).toHaveLength(2); // once per link — show them all
    expect(screen.getByText("· catalyst-rich")).toBeInTheDocument(); // the descriptor rides the hint
    expect(screen.getByText("LONE")).toBeInTheDocument(); // unsegmented, never dropped
  });

  it("is reversible — back to call state re-collapses the multi-segment name", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(screen.getAllByText("KTTA")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "call state" }));
    expect(headerLabels(container)).toEqual(["Quiet"]); // card undefined -> everything Quiet
    expect(screen.getAllByText("KTTA")).toHaveLength(1);
  });

  it("HIDES the value-chain toggle when the thesis has no decomposed chain (gating)", () => {
    fx.thesis = fx.noSeg;
    renderCockpit();
    expect(screen.queryByRole("button", { name: "value chain" })).toBeNull();
    expect(screen.getByRole("button", { name: "call state" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "business type" })).toBeInTheDocument();
  });
});
