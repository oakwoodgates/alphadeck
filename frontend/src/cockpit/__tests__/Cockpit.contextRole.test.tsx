import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The operator's CONTEXT ROLE on the Cockpit basket table: a quiet lowercase tag beside the ticker on ANY row
// whose member has a role — a voting name, a Context / Bench name — in every lens, and ONLY when one is set
// (never a "—" placeholder, WB#3). Operator-authored display metadata: it moves no row, no bucket, no call (#4);
// the hover is the role's description.
const fx = vi.hoisted(() => {
  const m = (ticker: string, sid: string, segment: string | null, role?: string | null) => ({
    ticker, role: "core", security_id: sid, segment, detail: null, thesis_fit: null,
    authored_by: "operator_set",
    ...(role === undefined ? {} : { context_role: role }),
  });
  const mcall = (sid: string, ticker: string, verdict: string) => ({
    security_id: sid, ticker, verdict, conviction_grade: "core", confirmation_grade: "flip",
    entry_grade: "flip", confidence: 0.5, exit_by: "2026-11-11", arm_until: "2026-07-17",
    lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
  });
  const base = { narrative: "n", ticker: null, evidence: [], catalysts: [], kill_criteria: [], position: null };
  const segments = [
    { label: "Clinics", descriptor: null },
    { label: "Developers", descriptor: null },
    { label: "Context", descriptor: null },
    { label: "Bench", descriptor: null },
  ];
  const thesis = (basket: unknown[]) => ({ ...base, id: "t-role", name: "Roles", segments, basket });
  const card = (armed: unknown[]) => ({
    thesis_id: "t-role", asof: "2026-07-11", state: "armed", verdict: "starter_entry",
    conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", expression: "STARTER",
    exit_by: "2026-11-11", arm_until: "2026-07-17", catalyst_surface: [], confidence: 0.54,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [], risk_signals: [], missing: [], counter_case: "", armed_members: armed, watch_members: [],
  });
  // ALPHA: a voting name WITH a role · BETA: voting, role null · GAMMA: voting, key absent (an old payload)
  // CTX: a Context name WITH a role (allowed on a silent row) · BNC: Bench, none
  const alpha = m("ALPHA", "s-alpha", "Clinics", "direct_read");
  const beta = m("BETA", "s-beta", "Developers", null);
  const gamma = m("GAMMA", "s-gamma", "Developers");
  const ctx = m("CTX", "s-ctx", "Context", "counter_case");
  const bnc = m("BNC", "s-bnc", "Bench", null);
  return {
    thesis, card, mcall, m,
    all: [alpha, beta, gamma, ctx, bnc],
    state: {
      thesis: thesis([alpha, beta, gamma, ctx, bnc]) as unknown,
      call: card([mcall("s-alpha", "ALPHA", "starter_entry")]) as unknown,
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.state.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.state.call, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: { members: [] }, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-role"
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );
}
const rowFor = (c: HTMLElement, ticker: string) =>
  [...c.querySelectorAll("tr.bkt")].find((tr) => tr.querySelector(".tk")?.textContent?.startsWith(ticker)) as
    | HTMLElement
    | undefined;
const roleTag = (c: HTMLElement, ticker: string) =>
  rowFor(c, ticker)?.querySelector(".role-tag") as HTMLElement | null | undefined;

beforeEach(() => {
  fx.state.thesis = fx.thesis(fx.all);
  fx.state.call = fx.card([fx.mcall("s-alpha", "ALPHA", "starter_entry")]);
});

describe("Cockpit — the context role tag", () => {
  it("renders a quiet lowercase tag beside the ticker on a row whose member has a role — voting or silent", () => {
    const { container } = renderCockpit();
    expect(roleTag(container, "ALPHA")?.textContent).toBe("direct read");
    expect(roleTag(container, "CTX")?.textContent).toBe("counter-case");
  });

  it("renders NOTHING (no '—' placeholder) on a row with no role: null, absent key, or Bench alike", () => {
    const { container } = renderCockpit();
    expect(roleTag(container, "BETA")).toBeNull();
    expect(roleTag(container, "GAMMA")).toBeNull();
    expect(roleTag(container, "BNC")).toBeNull();
    // exactly the two tagged rows carry one in the whole table
    expect(container.querySelectorAll("tr.bkt .role-tag")).toHaveLength(2);
  });

  it("the hover is the role's description", () => {
    const { container } = renderCockpit();
    expect(roleTag(container, "ALPHA")).toHaveAttribute(
      "title",
      "Not in the business, but its numbers forecast what the voting names report.",
    );
    expect(roleTag(container, "CTX")).toHaveAttribute(
      "title",
      "Loses if the thesis plays out, or wins if it fails.",
    );
  });

  it("is a tag, not a mover: the buckets and the call state are exactly what they are without roles", () => {
    const { container } = renderCockpit();
    expect(rowFor(container, "ALPHA")?.className).toContain("bkt-armed");
    expect(rowFor(container, "BETA")?.className).toContain("bkt-quiet");
    expect(rowFor(container, "CTX")?.className).toContain("bkt-context");
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(5); // nothing hidden, nothing added
  });

  it("rides every lens (the value-chain and business-type regroupings keep the tag)", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(roleTag(container, "ALPHA")?.textContent).toBe("direct read");
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    expect(roleTag(container, "ALPHA")?.textContent).toBe("direct read");
    // in the type lens a silent name keeps its own silent tag AND its role tag, side by side
    expect(rowFor(container, "CTX")?.querySelector(".silent-tag")?.textContent).toBe("context");
    expect(roleTag(container, "CTX")?.textContent).toBe("counter-case");
  });

  it("a company in two links shows the tag on each of its rows", () => {
    fx.state.thesis = fx.thesis([
      fx.m("ALPHA", "s-alpha", "Clinics", "payer"),
      fx.m("ALPHA", "s-alpha", "Developers", "payer"),
    ]);
    const { container } = renderCockpit();
    expect(container.querySelectorAll("tr.bkt .role-tag").length).toBeGreaterThanOrEqual(1);
    for (const tag of container.querySelectorAll("tr.bkt .role-tag")) {
      expect(tag.textContent).toBe("payer");
    }
  });
});
