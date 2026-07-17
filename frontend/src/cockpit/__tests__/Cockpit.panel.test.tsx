import { fireEvent, render, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

// The per-name panel (C2): row click → a read-only, non-modal slide-over with THAT name's call,
// its own triggers/risks, and the free identity fields — the table underneath never unmounts.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  return {
  thesis: {
    id: "t-nuke",
    name: "Small-scale nuclear",
    narrative: "n",
    ticker: null,
    segments: [],
    basket: [
      // quiet, fully-authored row: decided archetype, size weight, detail, fit — the panel shows them
      { ticker: "URA", role: "the fund", archetype: "fund", security_id: "s-ura", detail: "the ETF sleeve", segment: "Safe exposure", thesis_fit: "The low-torque sleeve of the theme.", conviction: 4, authored_by: "operator_set" },
      { ticker: "J", role: "core", archetype: null, security_id: "s-j", detail: null, segment: "EPC & services", thesis_fit: "Nuclear-adjacent EPC backlog.", conviction: null, authored_by: "system_drafted" },
      { ticker: "XE", role: "core", archetype: null, security_id: "s-xe", detail: null, authored_by: "operator_set" },
      { ticker: "ZTEK", role: "core", archetype: null, security_id: "s-ztek", detail: null, authored_by: "operator_set" },
    ],
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    // the open position, ATTRIBUTED to J (post-#155 Position.security_id) — the panel joins on it
    position: { entry_price: 125, current_price: null, opened_on: "2026-07-11", security_id: "s-j" },
  },
  decisions: [
    { id: "d1", action: "take", decision_date: "2026-07-11", security_id: "s-j", shares: 10, price: 125, reason: "test", voids: null, call_state: "armed", call_verdict: "starter_entry", recorded_at: "2026-07-11T12:00:00Z", voided: false },
    { id: "d2", action: "take", decision_date: "2026-07-10", security_id: "s-ztek", shares: null, price: null, reason: "other name", voids: null, call_state: null, call_verdict: null, recorded_at: "2026-07-10T12:00:00Z", voided: false },
    { id: "d3", action: "pass", decision_date: "2026-07-09", security_id: null, shares: null, price: null, reason: "thesis-level", voids: null, call_state: null, call_verdict: null, recorded_at: "2026-07-09T12:00:00Z", voided: false },
  ],
  call: {
    thesis_id: "t-nuke",
    asof: "2026-07-11",
    state: "armed",
    verdict: "starter_entry",
    conviction_grade: "core",
    confirmation_grade: "flip",
    entry_grade: "flip",
    expression: "STARTER",
    exit_by: "2026-11-11",
    arm_until: "2026-07-17",
    catalyst_surface: [],
    confidence: 0.54,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [
      { label: "insider cluster on J", kind: "insider", grade: "core", ticker: "J", sources: [] },
      { label: "insider cluster on XE", kind: "insider", grade: "core", ticker: "XE", sources: [] },
    ],
    risk_signals: [
      { label: "dilution risk on J", kind: "dilution_risk", grade: null, ticker: "J", sources: [] },
      { label: "dilution risk on ZTEK", kind: "dilution_risk", grade: null, ticker: "ZTEK", sources: [] },
    ],
    missing: [],
    counter_case: "",
    armed_members: [
      {
        security_id: "s-j", ticker: "J", verdict: "starter_entry", conviction_grade: "core",
        confirmation_grade: "flip", entry_grade: "flip", confidence: 0.54, exit_by: "2026-11-11",
        arm_until: "2026-07-17", lapsing: false, theme_armed: false,
        triggers: [
          { label: "insider cluster on J", kind: "insider", grade: "core", ticker: "J", sources: [{ source: "form4", ref: "acc-1", url: "https://sec.gov/x" }] },
          { label: "momentum breakout on J", kind: "technical_breakout", grade: "flip", ticker: "J", sources: [] },
        ],
      },
    ],
    watch_members: [
      {
        security_id: "s-ztek", ticker: "ZTEK", verdict: null, conviction_grade: null,
        confirmation_grade: "core", entry_grade: null, confidence: null, exit_by: null,
        arm_until: "2026-07-21", lapsing: false, theme_armed: false,
        triggers: [{ label: "volume breakout on ZTEK", kind: "technical_breakout", grade: "core", ticker: "ZTEK", sources: [] }],
      },
    ],
  },
  scored: {
    members: [
      {
        security_id: "s-j", name: "Jacobs Solutions Inc.", sector: "Engineering & construction",
        exchange: "NYSE", category: "operating co", archetype: null, archetype_hint: "shovel",
        purity: fig(2, 38), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(0, null),
        market_cap: fig(null, 16.4e9), fit: "fits", unconfirmed_estimates: 1,
      },
    ],
  },
  // read-only indicators — only J has a row, so the join-by-security_id and the honest-empty
  // degrade (ZTEK) are both exercised; sma200 is a thin-history gap with its why
  display: {
    thesis_id: "t-nuke",
    asof: "2026-07-11",
    members: [
      {
        security_id: "s-j",
        ticker: "J",
        signals: [
          {
            kind: "sma_position",
            label: "SMA position (50/200d)",
            metrics: [
              { key: "close", label: "close", value: 132.4, unit: "price", note: null },
              { key: "sma50", label: "50d SMA", value: 120.1, unit: "price", note: null },
              { key: "sma200", label: "200d SMA", value: null, unit: "price", note: "n/a: 140/200 bars" },
              { key: "pct_vs_sma50", label: "vs 50d", value: 10.24, unit: "pct", note: null },
              { key: "pct_vs_sma200", label: "vs 200d", value: null, unit: "pct", note: "n/a: 140/200 bars" },
            ],
            events: [
              { key: "cross_sma50", label: "price crossed above 50d SMA", date: "2026-06-20", direction: "up" },
            ],
            basis: {
              source: "fact_price_eod",
              params: { fast: 50, slow: 200, lookback_days: 600 },
              bars_used: 140, window_start: "2026-01-02", window_end: "2026-07-11", note: null,
            },
          },
        ],
      },
    ],
  },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: fx.call, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  useDecisions: () => ({ data: fx.decisions, isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

// In the app the selection key is URL-owned (?name=, App's CockpitRoute); the harness plays that
// controlled-prop role so every open/switch/close path below exercises the real lift.
function Harness() {
  const [name, setName] = useState<string | null>(null);
  return (
    <Cockpit
      thesisId="t-nuke"
      asof="2026-07-11"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={name}
      onSelectName={setName}
    />
  );
}

function renderCockpit() {
  return render(<Harness />);
}

const row = (container: HTMLElement, cls: string) =>
  container.querySelector(`tr.bkt.${cls}`) as HTMLElement;
const panel = (container: HTMLElement) => container.querySelector(".npanel") as HTMLElement | null;

describe("Cockpit — the per-name panel", () => {
  it("opens on row click with THAT name's call and only its OWN triggers", () => {
    const { container } = renderCockpit();
    expect(panel(container)).toBeNull(); // closed by default
    fireEvent.click(row(container, "bkt-armed")); // J
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText("STARTER entry")).toBeInTheDocument();
    expect(p.getByText("54%")).toBeInTheDocument(); // its own confidence, not the thesis bar
    expect(p.getByText(/insider cluster on J/)).toBeInTheDocument();
    expect(p.getByText(/momentum breakout on J/)).toBeInTheDocument();
    expect(p.queryByText(/insider cluster on XE/)).toBeNull(); // another name's trigger never leaks
    expect(p.getByText("↗ source")).toBeInTheDocument(); // provenance rides the row (#6)
  });

  it("filters risk signals to this name's ticker", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-armed")); // J
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText(/dilution risk on J/)).toBeInTheDocument();
    expect(p.queryByText(/dilution risk on ZTEK/)).toBeNull();
  });

  it("swaps in place when another row is clicked — the table element never unmounts", () => {
    const { container } = renderCockpit();
    const table = container.querySelector("table.basket");
    fireEvent.click(row(container, "bkt-armed")); // J
    fireEvent.click(row(container, "bkt-warming")); // XE — one click, no close needed (non-modal)
    expect(container.querySelectorAll(".npanel")).toHaveLength(1);
    const p = within(panel(container) as HTMLElement);
    // XE has no member call: the honest degrade line + its trigger from the thesis list
    expect(p.getByText("Conviction fired — awaiting market confirmation.")).toBeInTheDocument();
    expect(p.getByText(/insider cluster on XE/)).toBeInTheDocument();
    expect(p.queryByText(/on J/)).toBeNull();
    expect(container.querySelector("table.basket")).toBe(table); // identity, not a remount
  });

  it("closes via Esc, ✕, and re-clicking the row — the table survives every exit", () => {
    const { container } = renderCockpit();
    const table = container.querySelector("table.basket");
    const jRow = row(container, "bkt-armed");

    fireEvent.click(jRow);
    expect(jRow.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(panel(container)).toBeNull();
    expect(jRow.getAttribute("aria-selected")).toBe("false");

    fireEvent.click(jRow);
    fireEvent.click(within(panel(container) as HTMLElement).getByTitle("close (Esc)"));
    expect(panel(container)).toBeNull();

    fireEvent.click(jRow);
    fireEvent.click(jRow); // toggle
    expect(panel(container)).toBeNull();

    expect(container.querySelector("table.basket")).toBe(table);
  });

  it("shows Indicators · this name — readings joined by security_id, gaps and empties said", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-armed")); // J — the one name with a display row
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText("Indicators · this name")).toBeInTheDocument();
    expect(p.getByText("SMA position (50/200d)")).toBeInTheDocument();
    expect(p.getByText("+10.2%")).toBeInTheDocument(); // pct renders signed
    expect(p.getByText("132.40")).toBeInTheDocument(); // price renders 2dp
    expect(p.getAllByText("n/a: 140/200 bars")).toHaveLength(2); // the honest gap says WHY (#6/#7)
    expect(p.getByText(/price crossed above 50d SMA/)).toBeInTheDocument();
    expect(p.getByText(/140 bars · through/)).toBeInTheDocument(); // the show-the-work basis line

    fireEvent.click(row(container, "bkt-watch")); // ZTEK — no row in the response
    const p2 = within(panel(container) as HTMLElement);
    expect(p2.getByText("No indicator data at this as-of.")).toBeInTheDocument();
    expect(p2.queryByText(/price crossed/)).toBeNull(); // another name's tape never leaks
  });

  it("degrades honestly on the verdict-less buckets: watch keeps its confirmation clock, quiet says so", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-watch")); // ZTEK
    let p = within(panel(container) as HTMLElement);
    expect(p.getByText("Moving, no conviction yet — confirmation only.")).toBeInTheDocument();
    expect(p.getByText(/Confirmation clock/)).toBeInTheDocument(); // arm_until, "decays in"
    expect(p.getByText(/volume breakout on ZTEK/)).toBeInTheDocument();

    fireEvent.click(row(container, "bkt-quiet")); // URA
    p = within(panel(container) as HTMLElement);
    expect(p.getByText("No live signals at this as-of.")).toBeInTheDocument();
    expect(p.getByText("None live.")).toBeInTheDocument(); // no triggers to show — said, not blank
    expect(p.getByText("None active.")).toBeInTheDocument(); // no risks either
  });

  it("surfaces the free identity fields — and the authored role/detail the table no longer carries", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-quiet")); // URA — the fully-authored row
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText("ETF sleeve")).toBeInTheDocument(); // archetype, via the shared label
    expect(p.getByText("Safe exposure")).toBeInTheDocument(); // segment
    expect(p.getByText("the ETF sleeve")).toBeInTheDocument(); // detail — preserved off the table
    expect(p.getByText("the fund")).toBeInTheDocument(); // role — likewise
    expect(p.getByText("●●●●○ 4")).toBeInTheDocument(); // operator size weight, labeled as yours
    expect(p.getByText("Size weight (yours)")).toBeInTheDocument();
    expect(p.getByText("The low-torque sleeve of the theme.")).toBeInTheDocument(); // thesis fit
    expect(p.getByText("yours")).toBeInTheDocument(); // authorship tag (operator_set)
  });

  it("renders '—' for unset weight, the archetype suggestion quietly, and the scoring snapshot", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-armed")); // J — unset weight/archetype, scored row
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText("Size weight (yours)").nextElementSibling?.textContent).toBe("—"); // NULL ≠ 0
    expect(p.queryByText("Detail")).toBeNull(); // null detail renders NO row, not an empty one
    expect(p.getByText(/figures suggest shovel — decide in the Workbench/)).toBeInTheDocument();
    expect(p.getByText("Jacobs Solutions Inc.")).toBeInTheDocument();
    expect(p.getByText("NYSE")).toBeInTheDocument();
    expect(p.getByText("Purity")).toBeInTheDocument(); // the four meters ride along (already fetched)
    expect(p.getByText("38%")).toBeInTheDocument();
    expect(p.getByText("cash-generative")).toBeInTheDocument(); // runway null value, honest label
    expect(p.getByText("1 unconfirmed estimate(s)")).toBeInTheDocument();
    expect(p.getByText("drafted")).toBeInTheDocument(); // system_drafted fit tag
  });

  it("shows the position and the decision rows logged ON this name — other names' never leak", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-armed")); // J — the held, decided-on name
    const p = within(panel(container) as HTMLElement);
    expect(p.getByText(/Position open — entered Jul 11 @ \$125/)).toBeInTheDocument();
    expect(p.getByText("Decision log · this name")).toBeInTheDocument();
    expect(p.getByText("take")).toBeInTheDocument();
    expect(p.getByText("10 sh · @ $125 · test · platform: starter-entry")).toBeInTheDocument();
    expect(p.queryByText(/other name/)).toBeNull(); // ZTEK's row never leaks into J's panel
    expect(p.queryByText(/thesis-level/)).toBeNull(); // unattributed rows stay on the rail's log

    fireEvent.click(row(container, "bkt-quiet")); // URA — flat, nothing logged on it
    const q = within(panel(container) as HTMLElement);
    expect(q.queryByText(/Position open/)).toBeNull();
    // no rows on this name → the whole section stays off (loudness marks the exception)
    expect(q.queryByText("Decision log · this name")).toBeNull();
  });

  it("keeps the panel read-only — no buttons beyond close, nothing to mutate", () => {
    const { container } = renderCockpit();
    fireEvent.click(row(container, "bkt-armed"));
    const buttons = (panel(container) as HTMLElement).querySelectorAll("button");
    expect(buttons).toHaveLength(1); // the ✕ only
  });
});
