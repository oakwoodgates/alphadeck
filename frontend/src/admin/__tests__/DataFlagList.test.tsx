import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// S3 — the Admin "Data flags" panel: the full current list across the active baskets — the names whose
// flags reach a Cockpit number first, the ones no number reads in a quiet collapsed list, and (data hygiene)
// the names with only a quiet marker last, apart, never counted as flagged. Read-only.
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
          reason: "Split needs review: a 1-for-100 reverse split listed for 2026-10-13 (only Massive.com lists it).",
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
      {
        // data hygiene: SEC's data behind a filed 10-Q — a flag no Cockpit number reads
        security_id: "s-pypl", ticker: "PYPL", marked: false,
        theses: [{ id: "t3", name: "Payments" }],
        flags: [{
          kind: "fundamentals_not_current", label: "fundamentals not current", level: "in_doubt",
          reason: "Fundamentals end at 2026-03-31; the 10-Q filed 2026-07-28 isn't in SEC's data yet.",
          clears: "Clears on its own when SEC's data carries the 10-Q.", since: "2026-07-28",
          affects: [], columns: [], dates: [], evidence: [], call_affected: false,
        }],
        splits: [],
        notes: [],
      },
      {
        // a quiet marker only: information, not a defect — never counted as flagged
        security_id: "s-mplt", ticker: "MPLT", marked: false,
        theses: [{ id: "t4", name: "New listings" }],
        flags: [],
        splits: [],
        notes: [{
          kind: "short_price_history", label: "short price history",
          reason: "Price history starts 2025-10-27; the 52-week checks can't run before 2026-10-27.",
          since: "2025-10-27", until: "2026-10-27",
        }],
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
    expect(within(quiet).getAllByText(/no Cockpit number affected/)).toHaveLength(2);
  });

  it("folds the fundamentals flag with the flags no number reads", () => {
    render(<DataFlagList />);
    const quiet = within(screen.getByTestId("adm-flags-quiet"));
    expect(quiet.getByText("PYPL")).toBeInTheDocument();
    expect(quiet.getByText(/isn't in SEC's data yet/)).toBeInTheDocument();
    expect(screen.getByTestId("adm-flags").textContent).toContain(
      "2 more with flags no Cockpit number reads",
    );
  });

  it("lists a marker-only name last and apart, never as flagged", () => {
    render(<DataFlagList />);
    const noted = screen.getByTestId("adm-flags-noted");
    expect(noted.tagName).toBe("DETAILS");
    expect(noted.textContent).toContain("1 with only a quiet marker (short price history)");
    expect(within(noted).getByText("MPLT")).toBeInTheDocument();
    expect(within(noted).getByText(/52-week checks can't run before 2026-10-27/)).toBeInTheDocument();
    expect(within(screen.getByTestId("adm-flags-quiet")).queryByText("MPLT")).toBeNull();
    expect(within(screen.getByTestId("adm-flags-marked")).queryByText("MPLT")).toBeNull();
  });
});
