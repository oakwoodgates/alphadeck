import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MemberDataFlagsOut } from "../../api/hooks";
import { DataFlagMark, DataFlagsSection } from "../DataFlagMark";

// Data hygiene (2026-10-24): the "fundamentals not current" flag and the short-history quiet MARKER in the
// NamePanel's "Data · this name" section. The flag reaches no cell (no column reads fundamentals), so the row
// carries no mark; the marker is information, not a defect — one muted line, last, never a mark.
const fundamentals = {
  kind: "fundamentals_not_current",
  label: "fundamentals not current",
  level: "in_doubt" as const,
  reason: "Fundamentals end at 2026-03-31; the 10-Q filed 2026-07-28 isn't in SEC's data yet.",
  clears: "Clears on its own when SEC's data carries the 10-Q for the period ending 2026-06-30.",
  since: "2026-07-28",
  affects: [],
  columns: [],
  dates: [],
  evidence: [
    { label: "SEC 10-Q filed 2026-07-28", url: "https://www.sec.gov/Archives/edgar/data/1633917/x/" },
  ],
  call_affected: false,
};
const shortHistory = {
  kind: "short_price_history",
  label: "short price history",
  reason: "Price history starts 2026-02-11; the 52-week checks can't run before 2027-02-11.",
  since: "2026-02-11",
  until: "2027-02-11",
};

function member(over: Partial<MemberDataFlagsOut>): MemberDataFlagsOut {
  return { security_id: "s1", ticker: "PYPL", marked: false, flags: [], splits: [], notes: [], theses: [], ...over };
}

describe("NamePanel data section — fundamentals flag and short-history marker", () => {
  it("shows the fundamentals flag in full, touching no number, and the marker last", () => {
    render(<DataFlagsSection member={member({ flags: [fundamentals], notes: [shortHistory] })} />);
    expect(screen.getByText("fundamentals not current")).toBeInTheDocument();
    expect(screen.getByText(/isn't in SEC's data yet/)).toBeInTheDocument();
    expect(screen.getByText("no number on the row is affected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SEC 10-Q filed 2026-07-28" })).toBeInTheDocument();
    const note = screen.getByTestId("np-note");
    expect(note.textContent).toBe(shortHistory.reason);
    expect(note.className).toBe("np-note"); // muted: no level badge, no flag border
  });

  it("a marker alone still renders the section, quietly", () => {
    render(<DataFlagsSection member={member({ notes: [shortHistory] })} />);
    expect(screen.getByText("Data · this name")).toBeInTheDocument();
    expect(screen.getByTestId("np-note")).toBeInTheDocument();
    expect(screen.queryByText("in doubt")).toBeNull();
  });

  it("neither the flag nor the marker marks the row", () => {
    const { container } = render(
      <DataFlagMark member={member({ flags: [fundamentals], notes: [shortHistory] })} />,
    );
    expect(container.textContent).toBe("");
  });
});
