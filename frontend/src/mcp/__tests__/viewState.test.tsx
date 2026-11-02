import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import NarrativeIndex from "../../dashboards/narrative-index/NarrativeIndex";
import { CaptureViewContext } from "../viewContext";

vi.mock("../../dashboards/narrative-index/useNarrativeIndex", () => ({
  useNarrativeIndex: () => ({ data: { cards: [], dates: ["2026-10-30"] }, isLoading: false, isFetching: false }),
  useNarrativeCard: () => ({ data: undefined }),
}));

describe("frozen Narrative Index view validation", () => {
  it.each([{ open_thesis: "missing" }, { chart_date: "2026-10-29" }, { chart_date: "2026-10-30", open_thesis: null }])("rejects absent requested card or chart date: %j", (settings) => {
    const { container } = render(
      <CaptureViewContext.Provider value={{ readOnly: true, settings }}>
        <NarrativeIndex asof="2026-10-30" onSelectThesis={() => undefined} />
      </CaptureViewContext.Provider>,
    );
    expect(container.querySelector('[data-mcp-error-code="invalid_view_state"]')).not.toBeNull();
  });
});
