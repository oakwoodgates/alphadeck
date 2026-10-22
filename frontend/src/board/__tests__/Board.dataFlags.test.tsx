import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// S3 — the Board's ticker marks: the Decision Queue's headline name and a single-name card carry the same
// quiet data-flag mark as their Cockpit row when a flag reaches a number on it (the union read), and only
// then (a history-only flag marks nothing).
const h = vi.hoisted(() => {
  const stopped = {
    kind: "price_feed_stopped", label: "price feed stopped", level: "known_wrong",
    reason: "Stopped trading 2026-08-07: a delisting form was filed 2026-08-10. The last prices are final, not today's.",
    clears: "Does not clear: the company stopped trading.", since: "2026-08-07",
    affects: ["price"], columns: ["ret_1d", "ret_7d"], dates: [], evidence: [], call_affected: false,
  };
  const tail = { ...stopped, kind: "mixed_scale_history", level: "in_doubt", columns: [] };
  return {
    theses: [
      { id: "t-a", name: "Alpha thesis", ticker: null, basket_size: 4, narrative: "n", archived: false },
      { id: "t-s", name: "Skyt thesis", ticker: "SKYT", basket_size: 1, narrative: "n", archived: false },
      { id: "t-j", name: "Jagx thesis", ticker: "JAGX", basket_size: 1, narrative: "n", archived: false },
    ],
    calls: {
      "t-a": {
        data: {
          thesis_id: "t-a", state: "armed", verdict: "starter_entry", conviction_grade: "core",
          entry_grade: "flip", key_conviction: { turned: true, detail: "" },
          key_confirmation: { turned: true, detail: "" },
          armed_members: [{ ticker: "MVNC", security_id: "s-mvnc" }], watch_members: [],
        },
      },
      "t-s": { data: { thesis_id: "t-s", state: "incubating", verdict: "not_yet", key_conviction: { turned: false }, key_confirmation: { turned: false }, armed_members: [], watch_members: [] } },
      "t-j": { data: { thesis_id: "t-j", state: "incubating", verdict: "not_yet", key_conviction: { turned: false }, key_confirmation: { turned: false }, armed_members: [], watch_members: [] } },
    } as Record<string, unknown>,
    flags: {
      thesis_id: null, asof: "2026-10-22", names: 3,
      members: [
        { security_id: "s-mvnc", ticker: "MVNC", marked: true, flags: [{ ...stopped, level: "in_doubt", kind: "split_needs_review", label: "split needs review" }], splits: [], theses: [] },
        { security_id: "s-skyt", ticker: "SKYT", marked: true, flags: [stopped], splits: [], theses: [] },
        { security_id: "s-jagx", ticker: "JAGX", marked: false, flags: [tail], splits: [], theses: [] },
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: h.theses, isLoading: false, error: null }),
  useDataFlags: () => ({ data: h.flags }),
  useCalls: (subjects: { id: string }[]) =>
    subjects.map((s) => h.calls[s.id] ?? { data: undefined, isLoading: false, isError: false }),
  useSetArchived: () => ({ mutate: () => {}, isPending: false }),
}));

import { Board } from "../Board";

describe("Board — the data-flag marks (S3)", () => {
  it("marks the Decision Queue's headline name and a flagged single-name card, nothing else", () => {
    render(<Board asof="2026-10-22" onSelect={() => {}} />);
    const dq = screen.getByText("Decision Queue").closest(".dq") as HTMLElement;
    const dqMark = within(dq).getByLabelText("data flag: in doubt");
    expect(dqMark.title).toContain("split needs review");
    const skyt = screen.getByText("Skyt thesis").closest(".card") as HTMLElement;
    expect(within(skyt).getByLabelText("data flag: known wrong").title).toContain("Stopped trading");
    // JAGX's flag reaches no number (history only): its card carries no mark
    const jagx = screen.getByText("Jagx thesis").closest(".card") as HTMLElement;
    expect(jagx.querySelector(".flag-mark")).toBeNull();
  });
});
