import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// S3 — the Admin "Data flags" panel: the full current list across the active baskets — the names whose
// flags reach a Cockpit number first, the history-only ones in a quiet collapsed list. Read-only.
const h = vi.hoisted(() => ({
  data: {
    thesis_id: null,
    asof: "2026-10-22",
    names: 688,
    members: [
      {
        security_id: "s-mvnc", ticker: "MVNC", marked: true,
        theses: [{ id: "t1", name: "The Everything Exchange" }],
        flags: [{
          kind: "split_needs_review", label: "split needs review", level: "in_doubt",
          reason: "Split needs review: a 1-for-100 reverse split listed for 2026-10-13 (only Polygon lists it).",
          clears: "Clears when the split is confirmed or rejected in Admin → Splits.", since: "2026-10-13",
          affects: ["price"], columns: ["ret_7d", "rvol8"], dates: [], evidence: [], call_affected: false,
        }],
        splits: [],
      },
      {
        security_id: "s-jagx", ticker: "JAGX", marked: false,
        theses: [{ id: "t2", name: "Rainbow Rush" }],
        flags: [{
          kind: "mixed_scale_history", label: "mixed-scale price history", level: "in_doubt",
          reason: "Mixed-scale price history: the prices change scale by about ×15 at 2024-09-17.",
          clears: "c", since: "2024-09-17", affects: ["price"], columns: [], dates: [], evidence: [],
          call_affected: false,
        }],
        splits: [],
      },
    ],
  } as unknown,
}));

vi.mock("../../api/hooks", () => ({
  useDataFlags: () => ({ data: h.data, isError: false }),
}));

import { DataFlagList } from "../DataFlagList";

describe("Admin — the Data flags panel (S3)", () => {
  it("leads with the names whose flags reach a number and folds the history-only ones", () => {
    render(<DataFlagList />);
    const panel = screen.getByTestId("adm-flags");
    expect(panel.textContent).toContain("1 of 688 names carry a flag that reaches a Cockpit number");
    const marked = within(screen.getByTestId("adm-flags-marked"));
    expect(marked.getByText("MVNC")).toBeInTheDocument();
    expect(marked.getByText(/affects 7d, RVOL 8D/)).toBeInTheDocument();
    expect(marked.getByText("The Everything Exchange")).toBeInTheDocument();
    const quiet = screen.getByTestId("adm-flags-quiet");
    expect(quiet.tagName).toBe("DETAILS");
    expect(within(quiet).getByText("JAGX")).toBeInTheDocument();
    expect(within(quiet).getByText(/no Cockpit number affected/)).toBeInTheDocument();
  });
});
