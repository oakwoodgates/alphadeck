import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RotationMap from "../../dashboards/rotation-map/RotationMap";
import type { RotationMapOut } from "../../dashboards/rotation-map/useRotationMap";
import { DuplicateTargetError, observe } from "../observe";
import { targetId } from "../targetId";
import { CaptureViewContext } from "../viewContext";

const h = vi.hoisted(() => ({ data: null as unknown, useRotationMap: vi.fn() }));
vi.mock("../../dashboards/rotation-map/useRotationMap", () => ({
  useRotationMap: (...args: unknown[]) => { h.useRotationMap(...args); return { data: h.data, isLoading: false, isFetching: false }; },
}));

const group = (key: string, median: number) => ({
  key, link: key, thesis_name: "Basket", names: 1, flagged: 0,
  windows: [{ key: "ret_7d", median, n: 1, up: 1, above_spy: 1 }],
  above_50d: 1, above_50d_n: 1, rvol20_median: 1.2, rvol20_n: 1, golden: 0, death: 0, rs_n: 1, rs_leaders: 1,
  members: [{ security_id: `sid-${key}`, ticker: key, cards: [], returns: { ret_7d: median }, above_50d: true, rvol20: 1.2, cross: null, rs_leading: true, flag: null }],
});
const payload = (groups: ReturnType<typeof group>[]) => ({
  asof: "2026-10-30", group_by: "link", include_silent: true, min_priced: 1, cross_days: 7,
  cards_counted: 1, cards_not_yet_created: 0, companies: 2, priced_companies: 2, silent_rows_left_out: 0, unresolved_rows: 0,
  cache: "hit", roster_fallback_cards: 0, flagged: [], spy: { ret_7d: 1 }, groups,
}) as unknown as RotationMapOut;

describe("captured view parity", () => {
  it("rejects missing expanded group IDs instead of showing a collapsed view", () => {
    h.data = { groups: [] };
    const { container } = render(
      <CaptureViewContext.Provider value={{ readOnly: true, settings: { open_groups: ["missing"] } }}>
        <RotationMap asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    expect(container.querySelector('[data-mcp-error-code="invalid_view_state"]')).not.toBeNull();
  });
  it("rejects a vs-SPY capture when benchmark tape is unavailable", () => {
    h.data = { groups: [], spy: null };
    const { container } = render(
      <CaptureViewContext.Provider value={{ readOnly: true, settings: { vs_spy: true } }}>
        <RotationMap asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    expect(container.querySelector('[data-mcp-error-code="invalid_view_state"]')).not.toBeNull();
  });
  it("observes the actual formatted vs-SPY cells and expanded names, in the requested order", () => {
    h.data = payload([group("Alpha", 4.234), group("Beta", 8.1)]);
    const { container } = render(
      <CaptureViewContext.Provider value={{ readOnly: true, settings: { group_by: "link", include_silent: true, vs_spy: true, sort_col: "ret_7d", sort_dir: "desc", open_groups: ["Beta"] } }}>
        <RotationMap asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    expect(h.useRotationMap).toHaveBeenCalledWith("2026-10-30", "link", true);
    expect(screen.getAllByText("+7.1%")).toHaveLength(2);
    const result = observe(container, "Rotation Map");
    expect(result.observation.tables[0].rows[0][0]).toContain("Beta");
    expect(result.observation.tables[0].rows[0][3]).toContain("+7.1%");
    expect(result.targets.map((t) => t.id)).toEqual([targetId("group", "Beta"), targetId("group-member", "Beta", "sid-Beta"), targetId("group", "Alpha")]);
    expect(result.observation.text).toContain("returns vs SPY");
  });

  it("advertises bounded unique opaque IDs for groups and members with labels longer than 1000 characters", () => {
    const keys = ["synthetic-label-".repeat(100), `${"synthetic-label-".repeat(100)}next`];
    h.data = payload(keys.map((key, index) => group(key, index)));
    const { container, rerender } = render(
      <CaptureViewContext.Provider value={{ readOnly: true, settings: { open_groups: keys } }}>
        <RotationMap asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    const ids = observe(container, "Rotation Map").targets.map((target) => target.id);
    expect(ids).toHaveLength(4);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => id.length <= 256 && !id.includes("synthetic-label"))).toBe(true);
    rerender(
      <CaptureViewContext.Provider value={{ readOnly: true, settings: { open_groups: keys } }}>
        <RotationMap asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    expect(observe(container, "Rotation Map").targets.map((target) => target.id)).toEqual(ids);
  });

  it("refuses duplicate advertised targets instead of selecting an ambiguous element", () => {
    const { container } = render(<><div data-mcp-id="duplicate" /><div data-mcp-id="duplicate" /></>);
    expect(() => observe(container, "Synthetic view")).toThrow(DuplicateTargetError);
  });
});
