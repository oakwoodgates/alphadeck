import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The Cockpit's catalyst calendar reads in DATE order — soonest first, a fuzzy "~Q3" last — with a date
// already past shown as "Nd ago" and grayed (it stays on the calendar, never vanishes). The order is a DISPLAY
// sort of a copy: the editor still opens on the operator's AUTHORING order, which is what a save stores.
const fx = vi.hoisted(() => {
  const cat = (id: string, label: string, when_date: string | null, when_label: string | null = null) => ({
    id, label, kind: null, when_date, when_label,
  });
  return {
    thesis: {
      id: "t-cal", name: "Calendar", narrative: "n", ticker: null, segments: [], basket: [],
      evidence: [], kill_criteria: [], position: null,
      // AUTHORING order — deliberately not chronological
      catalysts: [
        cat("c1", "Investor day", null, "~Q1"),
        cat("c2", "Phase 3 readout", "2026-12-15"),
        cat("c3", "Competitor halted", "2026-10-01"),
        cat("c4", "FDA decision", "2026-10-30"),
        cat("c5", "Earnings", "2026-10-22"),
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: { members: [] }, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: undefined, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { type AppClock, ClockProvider } from "../../clock";
import { Cockpit } from "../Cockpit";

const renderCockpit = (server?: AppClock) => {
  const cockpit = (
    <Cockpit
      thesisId="t-cal"
      asof="2026-10-22"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />
  );
  return render(server ? <ClockProvider value={server}>{cockpit}</ClockProvider> : cockpit);
};

const rows = () => [...document.querySelectorAll(".vt-cats .cat")] as HTMLElement[];

describe("Cockpit — the catalyst calendar", () => {
  it("lists the events soonest first, an undated one last", () => {
    renderCockpit();
    expect(rows().map((r) => r.querySelector(".lbl")?.textContent)).toEqual([
      "Competitor halted",
      "Earnings",
      "FDA decision",
      "Phase 3 readout",
      "Investor day",
    ]);
  });

  it("reads a past date as 'Nd ago', today as 'today', ahead as 'Nd', and a fuzzy one as its own label", () => {
    renderCockpit();
    // a PAST view (the browser's date, with no server clock around, is later than Oct 22): counted from the as-of
    expect(rows().map((r) => r.querySelector(".when")?.textContent)).toEqual([
      "Oct 1 · 21d ago",
      "Oct 22 · today",
      "Oct 30 · 8d",
      "Dec 15 · 54d",
      "~Q1",
    ]);
  });

  it("counts a LIVE view from the action day: the night's own date has passed, the action day reads today (F2)", () => {
    // Oct 22 is the current night, read live on Oct 23 morning, the action day
    const live = { currentNight: "2026-10-22", marketToday: "2026-10-23", actionDay: "2026-10-23", failed: false };
    renderCockpit(live);
    expect(rows().map((r) => r.querySelector(".when")?.textContent)).toEqual([
      "Oct 1 · 22d ago",
      "Oct 22 · 1d ago",
      "Oct 30 · 7d",
      "Dec 15 · 53d",
      "~Q1",
    ]);
    cleanup();
    renderCockpit({ ...live, actionDay: "2026-10-22" }); // when the action day IS Oct 22, that catalyst reads today
    expect(rows().map((r) => r.querySelector(".when")?.textContent)[1]).toBe("Oct 22 · today");
  });

  it("grays a past date (never hides it) and accents only what is within 21 days ahead", () => {
    renderCockpit();
    const [past, today, fda, far, fuzzy] = rows();
    expect(past.className).toContain("passed");
    expect(past.className).not.toContain("soon");
    expect(today.className).toContain("soon");
    expect(fda.className).toContain("soon");
    for (const r of [far, fuzzy]) {
      expect(r.className).not.toContain("soon");
      expect(r.className).not.toContain("passed");
    }
    expect(rows()).toHaveLength(5); // nothing dropped
  });

  it("opens the editor on the AUTHORING order — the display sort never reorders what a save stores", () => {
    renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: /edit the calendar/ }));
    const labels = [1, 2, 3, 4, 5].map(
      (i) => (screen.getByLabelText(`catalyst label ${i}`) as HTMLInputElement).value,
    );
    expect(labels).toEqual([
      "Investor day",
      "Phase 3 readout",
      "Competitor halted",
      "FDA decision",
      "Earnings",
    ]);
  });
});
