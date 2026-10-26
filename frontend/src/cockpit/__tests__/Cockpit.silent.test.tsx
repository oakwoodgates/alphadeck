import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The two SILENT value-chain links on the Cockpit (Context + Bench). A name the operator placed in either is
// on the card and visible but NEVER counts toward the call, so:
//   - call-state lens: two buckets AFTER Quiet (Context, Bench), hidden when empty — except a HELD
//     (`managing`) name, which stays at the top (a held position never leaves Managing);
//   - value-chain lens: the last two groups, even when a payload's segments came in another order;
//   - business-type lens: the name keeps a grayed row + a small lowercase "context" / "bench" tag.
// Low visual energy throughout (#7): muted rows, no badges.
const fx = vi.hoisted(() => {
  const m = (ticker: string, sid: string, segment: string | null) => ({
    ticker, role: "core", security_id: sid, segment, detail: null, thesis_fit: null,
    authored_by: "operator_set",
  });
  const mcall = (sid: string, ticker: string, verdict: string) => ({
    security_id: sid, ticker, verdict, conviction_grade: "core", confirmation_grade: "flip",
    entry_grade: "flip", confidence: 0.5, exit_by: "2026-11-11", arm_until: "2026-07-17",
    lapsing: false, entry_window_closing: false, theme_armed: false, triggers: [],
  });
  const base = { narrative: "n", ticker: null, evidence: [], catalysts: [], kill_criteria: [], position: null };
  // the payload order is deliberately SCRAMBLED: the pair first/in the middle
  const segments = [
    { label: "Context", descriptor: "stale" },
    { label: "Clinics", descriptor: null },
    { label: "Bench", descriptor: null },
    { label: "Developers", descriptor: null },
  ];
  const thesis = (basket: unknown[]) => ({ ...base, id: "t-sil", name: "Silent", segments, basket });
  const card = (armed: unknown[]) => ({
    thesis_id: "t-sil", asof: "2026-07-11", state: "armed", verdict: "starter_entry",
    conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", expression: "STARTER",
    exit_by: "2026-11-11", arm_until: "2026-07-17", catalyst_surface: [], confidence: 0.54,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [], risk_signals: [], missing: [], counter_case: "", armed_members: armed, watch_members: [],
  });
  const alpha = m("ALPHA", "s-alpha", "Clinics");
  const beta = m("BETA", "s-beta", "Developers");
  const ctx = m("CTX", "s-ctx", "Context");
  const bnc = m("BNC", "s-bnc", "Bench");
  const held = m("HELD", "s-held", "Context");
  return {
    alpha, beta, ctx, bnc, held, thesis, card, mcall,
    state: {
      thesis: thesis([alpha, beta, ctx, bnc]) as unknown,
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

function renderCockpit(selectedName: string | null = null) {
  return render(
    <Cockpit
      thesisId="t-sil"
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={selectedName}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );
}
const headers = (c: HTMLElement) => [...c.querySelectorAll("tr.grp .lbl")].map((e) => e.textContent);
const rowFor = (c: HTMLElement, ticker: string) =>
  [...c.querySelectorAll("tr.bkt")].find((tr) => tr.querySelector(".tk")?.textContent?.startsWith(ticker)) as
    | HTMLElement
    | undefined;

beforeEach(() => {
  fx.state.thesis = fx.thesis([fx.alpha, fx.beta, fx.ctx, fx.bnc]);
  fx.state.call = fx.card([fx.mcall("s-alpha", "ALPHA", "starter_entry")]);
});

describe("Cockpit — Context and Bench in the call-state lens", () => {
  it("files silent names in two buckets AFTER Quiet, with a quiet sub-note", () => {
    const { container } = renderCockpit();
    expect(headers(container)).toEqual(["Armed", "Quiet", "Context", "Bench"]);
    const grp = [...container.querySelectorAll("tr.grp")].map((tr) => ({
      label: tr.querySelector(".lbl")?.textContent,
      hint: tr.querySelector(".hint")?.textContent,
      ct: tr.querySelector(".ct")?.textContent,
    }));
    expect(grp.slice(2)).toEqual([
      { label: "Context", hint: "· never counts toward the call", ct: "· 1 name" },
      { label: "Bench", hint: "· never counts toward the call", ct: "· 1 name" },
    ]);
    // BETA is a plain Quiet name; the silent ones are NOT in Quiet
    expect(rowFor(container, "BETA")?.className).toContain("bkt-quiet");
    expect(rowFor(container, "CTX")?.className).toContain("bkt-context");
    expect(rowFor(container, "BNC")?.className).toContain("bkt-bench");
  });

  it("hides an empty silent bucket (honest loudness) — and both when nobody is silent", () => {
    fx.state.thesis = fx.thesis([fx.alpha, fx.beta, fx.ctx]);
    const only = renderCockpit();
    expect(headers(only.container)).toEqual(["Armed", "Quiet", "Context"]);
    only.unmount();

    fx.state.thesis = fx.thesis([fx.alpha, fx.beta]);
    const none = renderCockpit();
    expect(headers(none.container)).toEqual(["Armed", "Quiet"]);
  });

  it("a silent name never lands in a call bucket — even one the (stale) call lists as armed", () => {
    // the backend removes silent rows at the call seam; if one slipped through, it still files as Context
    fx.state.call = fx.card([fx.mcall("s-ctx", "CTX", "starter_entry")]);
    const { container } = renderCockpit();
    expect(headers(container)).toEqual(["Quiet", "Context", "Bench"]);
    expect(rowFor(container, "CTX")?.className).toContain("bkt-context");
  });

  it("a HELD (managing) name stays in Managing — a held position never leaves the top", () => {
    fx.state.thesis = fx.thesis([fx.alpha, fx.beta, fx.ctx, fx.held]);
    fx.state.call = fx.card([
      fx.mcall("s-alpha", "ALPHA", "starter_entry"),
      fx.mcall("s-held", "HELD", "managing"),
    ]);
    const { container } = renderCockpit();
    expect(headers(container)).toEqual(["Managing", "Armed", "Quiet", "Context"]);
    expect(rowFor(container, "HELD")?.className).toContain("bkt-managing");
    expect(rowFor(container, "CTX")?.className).toContain("bkt-context");
    // the Context header counts only the NON-held silent name
    expect(container.querySelectorAll("tr.grp .ct")[3].textContent).toBe("· 1 name");
  });

  it("the per-name panel says a silent name is not in the call (no invented verdict)", () => {
    renderCockpit("CTX");
    expect(screen.getByText("Watched for context — never counts toward the call.")).toBeInTheDocument();
  });

  it("the bucket header rows keep every silent name countable (a lens re-orders, never drops)", () => {
    const { container } = renderCockpit();
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(4); // ALPHA, BETA, CTX, BNC — all visible
  });
});

describe("Cockpit — Context and Bench in the value-chain lens", () => {
  it("are the LAST two groups, even when the payload's segments came in another order", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(headers(container)).toEqual(["Clinics", "Developers", "Context", "Bench"]);
  });

  it("carry the FIXED descriptor as the header hint (not whatever the payload said)", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    const ctx = [...container.querySelectorAll("tr.grp")].find(
      (tr) => tr.querySelector(".lbl")?.textContent === "Context",
    );
    expect(ctx?.querySelector(".hint")?.textContent).toBe(
      "· Watched for context: buyers, counter-case names, giants where the theme is a slice. Never counts toward the call.",
    );
    expect(screen.queryByText("· stale")).toBeNull();
  });

  it("an empty one is hidden (Bench here)", () => {
    fx.state.thesis = fx.thesis([fx.alpha, fx.beta, fx.ctx]);
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(headers(container)).toEqual(["Clinics", "Developers", "Context"]);
  });

  it("a held silent name still shows under its link here (the lens is by link; its row keeps the Managing dot)", () => {
    fx.state.thesis = fx.thesis([fx.alpha, fx.held]);
    fx.state.call = fx.card([fx.mcall("s-held", "HELD", "managing")]);
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(headers(container)).toEqual(["Clinics", "Context"]);
    expect(rowFor(container, "HELD")?.className).toContain("bkt-managing");
  });
});

describe("Cockpit — Context and Bench in the business-type lens", () => {
  it("a silent name keeps a grayed row and carries a small lowercase tag; real names carry none", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    const tag = (t: string) => rowFor(container, t)?.querySelector(".silent-tag")?.textContent ?? null;
    expect(tag("CTX")).toBe("context");
    expect(tag("BNC")).toBe("bench");
    expect(tag("ALPHA")).toBeNull();
    expect(tag("BETA")).toBeNull();
    // grayed = the silent bucket's muted row class rides the lens (no loud badge)
    expect(rowFor(container, "CTX")?.className).toContain("bkt-context");
  });

  it("the tag is the type lens's alone — the other lenses name the link in the header instead", () => {
    const { container } = renderCockpit();
    expect(container.querySelector(".silent-tag")).toBeNull(); // call-state lens
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(container.querySelector(".silent-tag")).toBeNull();
  });

  it("the display columns still render for a silent name (Last close, SMA… are computed for every member)", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // the row carries the full set of cells — same width as a voting name's row
    expect(rowFor(container, "CTX")?.querySelectorAll("td").length).toBe(
      rowFor(container, "ALPHA")?.querySelectorAll("td").length,
    );
  });
});
