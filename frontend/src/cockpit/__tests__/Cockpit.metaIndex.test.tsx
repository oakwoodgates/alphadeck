import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The Cockpit header's quiet "meta index" tag: shown only when the card's switch is on (WB#3 — a tag true
// of every card would be noise), in the same lowercase style as the context-role tag.
const fx = vi.hoisted(() => ({
  thesis: {
    id: "t-map", name: "Sector map", narrative: "n", ticker: null, segments: [],
    basket: [{ ticker: "MIXA", role: "core", security_id: "s-mixa", detail: null, authored_by: "system_drafted" }],
    evidence: [], catalysts: [], kill_criteria: [], position: null, meta_index: true,
  } as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: { members: [] }, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: { members: [] }, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-map"
      asof="2026-09-08"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );
}

describe("Cockpit — the meta index tag", () => {
  it("shows the quiet lowercase tag in the header when the switch is on", () => {
    fx.thesis.meta_index = true;
    const { container } = renderCockpit();
    const tag = container.querySelector(".cp-title .meta-tag");
    expect(tag?.textContent).toBe("meta index");
    expect(tag?.getAttribute("title")).toContain("its names never count toward the call");
  });

  it("shows nothing when the switch is off", () => {
    fx.thesis.meta_index = false;
    const { container } = renderCockpit();
    expect(container.querySelector(".meta-tag")).toBeNull();
  });
});
