import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The grouped basket (per-name buckets, C1): a thesis whose card exercises every populated bucket —
// armed / lapsing / theme-armed / warming (conviction-only, via the triggers_fired ticker join) /
// watch / quiet. Managing stays absent (the wire never emits a per-member managing verdict today).
const fx = vi.hoisted(() => ({
  thesis: {
    id: "t-nuke",
    name: "Small-scale nuclear",
    narrative: "n",
    ticker: null,
    segments: [],
    basket: [
      { ticker: "URA", role: "core", archetype: "fund", security_id: "s-ura", detail: null, authored_by: "operator_set" },
      { ticker: "J", role: "core", archetype: null, security_id: "s-j", detail: null, authored_by: "operator_set" },
      { ticker: "UUUU", role: "core", archetype: null, security_id: "s-uuuu", detail: null, authored_by: "operator_set" },
      { ticker: "NNE", role: "core", archetype: null, security_id: "s-nne", detail: null, authored_by: "operator_set" },
      { ticker: "XE", role: "core", archetype: null, security_id: "s-xe", detail: null, authored_by: "operator_set" },
      { ticker: "ZTEK", role: "core", archetype: null, security_id: "s-ztek", detail: null, authored_by: "operator_set" },
    ],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  },
  call: {
    thesis_id: "t-nuke",
    asof: "2026-07-11",
    state: "armed",
    verdict: "starter_entry",
    conviction_grade: "core",
    confirmation_grade: "flip",
    entry_grade: "flip",
    expression: "STARTER",
    exit_by: "2026-11-11",
    arm_until: "2026-07-17",
    catalyst_surface: [],
    confidence: 0.54,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [
      { label: "insider cluster on J", kind: "insider", grade: "core", ticker: "J", sources: [] },
      { label: "insider cluster on XE", kind: "insider", grade: "core", ticker: "XE", sources: [] },
      { label: "breakout on ZTEK", kind: "technical_breakout", grade: "core", ticker: "ZTEK", sources: [] },
    ],
    risk_signals: [],
    missing: [],
    counter_case: "",
    armed_members: [
      { security_id: "s-j", ticker: "J", verdict: "starter_entry", conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", confidence: 0.54, exit_by: "2026-11-11", arm_until: "2026-07-17", lapsing: false, theme_armed: false, triggers: [] },
      { security_id: "s-uuuu", ticker: "UUUU", verdict: "starter_entry", conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", confidence: 0.41, exit_by: "2026-07-15", arm_until: "2026-07-13", lapsing: true, theme_armed: false, triggers: [] },
      { security_id: "s-nne", ticker: "NNE", verdict: "starter_entry", conviction_grade: "flip", confirmation_grade: "flip", entry_grade: "flip", confidence: 0.33, exit_by: "2026-09-30", arm_until: "2026-07-19", lapsing: false, theme_armed: true, triggers: [] },
    ],
    watch_members: [
      { security_id: "s-ztek", ticker: "ZTEK", verdict: null, conviction_grade: null, confirmation_grade: "core", entry_grade: null, confidence: null, exit_by: null, arm_until: "2026-07-21", lapsing: false, theme_armed: false, triggers: [] },
    ],
  },
  scored: {
    members: [
      { security_id: "s-j", name: "Jacobs Solutions Inc.", market_cap: { value: 16.4e9 } },
      { security_id: "s-ura", name: "Global X Uranium ETF", market_cap: { value: 3.2e9 } },
    ],
  },
}));

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.call, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  // the spine-list editors + the CallCard's decision row render inside the Cockpit — inert here
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-nuke"
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
    />,
  );
}

describe("Cockpit — the grouped basket (per-name buckets)", () => {
  it("renders one header per populated bucket, with its count — and none for empty buckets", () => {
    const { container } = renderCockpit();
    const headers = [...container.querySelectorAll("tr.grp")].map((tr) => ({
      label: tr.querySelector(".lbl")?.textContent,
      hint: tr.querySelector(".hint")?.textContent,
      ct: tr.querySelector(".ct")?.textContent,
    }));
    expect(headers).toEqual([
      { label: "Armed", hint: "· act now", ct: "· 1" },
      { label: "Lapsing", hint: "· entry window closing", ct: "· 1" },
      { label: "Theme-armed", hint: "· theme fallback · starter cap", ct: "· 1" },
      { label: "Warming", hint: "· conviction in · awaiting confirmation", ct: "· 1" },
      { label: "Watch", hint: "· moving · no conviction yet", ct: "· 1" },
      { label: "Quiet", hint: "· no live signals", ct: "· 1" },
    ]);
    // no member call reads managing in this fixture → no header (render-if-present, not a stub)
    expect(screen.queryByText("Managing", { selector: ".lbl" })).toBeNull();
  });

  it("collapses a bucket on header click — open by default, count stays visible, reversible", () => {
    const { container } = renderCockpit();
    const warmingHeader = container.querySelector("tr.grp.bkt-warming .grp-h") as HTMLElement;
    const warmingRow = container.querySelector("tr.bkt.bkt-warming") as HTMLElement;
    expect(warmingHeader.getAttribute("aria-expanded")).toBe("true"); // open by default
    expect(warmingRow.className).not.toContain("folded");

    fireEvent.click(warmingHeader);
    expect(warmingHeader.getAttribute("aria-expanded")).toBe("false");
    // folded rows stay MOUNTED with visibility:collapse (a collapsed row still feeds the
    // column-width algorithm — the fold must never re-flow the columns), never unmounted
    expect(container.querySelector("tr.bkt.bkt-warming")).toBe(warmingRow);
    expect(warmingRow.className).toContain("folded");
    expect(warmingHeader.querySelector(".ct")?.textContent).toBe("· 1"); // the count never hides
    expect(container.querySelector("tr.bkt.bkt-watch")?.className).not.toContain("folded");

    fireEvent.click(warmingHeader); // one click back (reversibility)
    expect(warmingRow.className).not.toContain("folded");
  });

  it("swaps the dead Role/Detail columns for Name + Exit-by (Dot has no text header)", () => {
    renderCockpit();
    expect(screen.queryByText("Role")).toBeNull();
    expect(screen.queryByText("Detail")).toBeNull();
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Exit-by")).toBeInTheDocument();
    expect(screen.getByText("Mkt cap")).toBeInTheDocument(); // survives the swap
  });

  it("keys one status dot per row to its bucket class", () => {
    const { container } = renderCockpit();
    for (const cls of ["bkt-armed", "bkt-lapsing", "bkt-theme", "bkt-warming", "bkt-watch", "bkt-quiet"]) {
      expect(container.querySelectorAll(`tr.bkt.${cls} .rowdot`)).toHaveLength(1);
    }
  });

  it("renders the per-name exit-by — amber 'lapses <date>' on a lapsing row, '—' where no clock", () => {
    // scoped to the table: the rail (CallCard clocks, MemberMenu runway) repeats these strings
    const { container } = renderCockpit();
    const table = within(container.querySelector("table.basket") as HTMLElement);
    const lapse = table.getByText("lapses Jul 15");
    expect(lapse.closest("td")?.className).toContain("lapse");
    expect(table.getByText("Nov 11")).toBeInTheDocument(); // J's hold horizon, un-tinted
    // the watch row has no conviction clock → a quiet "—"
    const ztekRow = container.querySelector("tr.bkt.bkt-watch");
    expect(ztekRow?.lastElementChild?.textContent).toBe("—");
  });

  it("bridges the company name by security_id — '—' when the scored read has none", () => {
    const { container } = renderCockpit();
    expect(screen.getByText("Jacobs Solutions Inc.")).toBeInTheDocument();
    // XE is un-scored → its Name cell is a quiet "—", never a guess
    const xeRow = container.querySelector("tr.bkt.bkt-warming");
    expect(xeRow?.children[2]?.textContent).toBe("—");
  });

  it("files the conviction-only name under Warming, the confirmation-only one under Watch", () => {
    const { container } = renderCockpit();
    expect(container.querySelector("tr.bkt.bkt-warming .tk")?.textContent).toBe("XE");
    expect(container.querySelector("tr.bkt.bkt-watch .tk")?.textContent).toBe("ZTEK");
    expect(container.querySelector("tr.bkt.bkt-quiet .tk")?.textContent).toBe("URA");
  });
});
