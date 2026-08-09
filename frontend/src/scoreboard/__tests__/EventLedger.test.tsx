import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  DisplaySignal,
  InsiderBuyOut,
  PriceBar,
  ScoreboardEpisodeOut,
  TriggerRefOut,
} from "../../api/hooks";
import { EventLedger, type EventLedgerProps } from "../EventLedger";
import type { IdentityCell } from "../ledger";
import { buildOverlayEvents } from "../overlay";

// The event ledger: N rows == the shared `events` array (row #N ↔ chip #N — proven by feeding the SAME
// buildOverlayEvents output), the cross-highlight (a row hover reports up via onActivate + an active prop
// tints the matching row), the Cockpit identity line + present-only headlines, and the empty→no-table gate.
// Pure DOM — no canvas, no hooks (the chart is PriceSparkline's job, live-verified by eye).

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    arm_date: "2026-06-01",
    warm_date: null,
    dearm_date: null,
    exit_by: null,
    close_reason: "",
    triggers_at_arm: [],
    ...over,
  } as unknown as ScoreboardEpisodeOut;
}
function bar(d: string, close: number): Pick<PriceBar, "d" | "close"> {
  return { d, close };
}
function buy(over: Partial<InsiderBuyOut> = {}): InsiderBuyOut {
  return {
    d: "2026-06-10",
    insider_name: "Jane Doe",
    insider_role: "CEO",
    shares: 1000,
    usd: 50000,
    aff_10b5_1: false,
    disclosed: "2026-06-10",
    ...over,
  };
}
const TRIG = { label: "3 insiders bought", kind: "insider", ticker: "IBM" } as TriggerRefOut;
const BARS = [bar("2026-05-15", 100), bar("2026-06-01", 104), bar("2026-06-10", 107), bar("2026-06-30", 112)];

// warmed 05-20 (#1), armed 06-01 (#2), trigger 06-01 (#3), insider 06-10 (#4) — the SAME array the chart draws
const EVENTS = buildOverlayEvents(
  ep({ warm_date: "2026-05-20", arm_date: "2026-06-01", triggers_at_arm: [TRIG] }),
  [buy({ d: "2026-06-10" })],
  BARS as PriceBar[],
);

const IDENTITY: IdentityCell[] = [
  { label: "type", value: "software/IT" },
  { label: "sector", value: "Technology" },
  { label: "exchange", value: "NYSE" },
  { label: "market cap", value: "$8.0B" },
];

function sig(kind: string, label: string): DisplaySignal {
  return {
    kind,
    label: kind,
    headline: { key: `${kind}.key`, label, glyph: "up", detail: null },
    metrics: [],
    events: [],
    basis: { source: "test", params: {} },
  } as unknown as DisplaySignal;
}

function renderLedger(over: Partial<EventLedgerProps> = {}) {
  const onActivate = vi.fn();
  const utils = render(
    <EventLedger
      events={EVENTS}
      activeN={null}
      onActivate={onActivate}
      identity={IDENTITY}
      signals={[sig("sma_position", "above the 50d")]}
      {...over}
    />,
  );
  return { onActivate, ...utils };
}

const rows = (c: HTMLElement) => Array.from(c.querySelectorAll("tbody tr")) as HTMLElement[];
const rowFor = (c: HTMLElement, n: number) =>
  rows(c).find((r) => r.querySelector(".evled-n")?.textContent === String(n))!;

describe("EventLedger — rows are the shared numbered events (row #N ↔ chip #N)", () => {
  it("renders exactly one row per event, numbered 1..N in chronological order", () => {
    const { container } = renderLedger();
    expect(rows(container)).toHaveLength(EVENTS.length); // 4
    expect(rows(container).map((r) => r.querySelector(".evled-n")?.textContent)).toEqual(["1", "2", "3", "4"]);
  });

  it("names each family in the type cell, tinting the number by family", () => {
    const { container } = renderLedger();
    expect(within(rowFor(container, 1)).getByText("warmed")).toBeInTheDocument();
    expect(rowFor(container, 2).querySelector(".evled-n")).toHaveClass("ov-lifecycle"); // armed
    expect(within(rowFor(container, 3)).getByText("arm trigger")).toBeInTheDocument();
    expect(rowFor(container, 3).querySelector(".evled-n")).toHaveClass("ov-trigger");
    const insider = rowFor(container, 4);
    expect(within(insider).getByText("insider buy")).toBeInTheDocument();
    expect(insider.querySelector(".evled-n")).toHaveClass("ov-insider");
    expect(insider.textContent).toContain("Jane Doe (CEO)"); // detail reuses the tooltip's line
  });

  it("empty events → no table at all (mirrors the chart's empty state)", () => {
    const { container } = renderLedger({ events: [] });
    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelector(".sb-evledger")).toBeNull();
  });
});

describe("EventLedger — the cross-highlight (hover-only v1)", () => {
  it("a row hover reports its number up (onActivate), and clears on leave", () => {
    const { container, onActivate } = renderLedger();
    fireEvent.mouseEnter(rowFor(container, 3));
    expect(onActivate).toHaveBeenCalledWith(3);
    fireEvent.mouseLeave(rowFor(container, 3));
    expect(onActivate).toHaveBeenCalledWith(null);
  });

  it("the activeN prop (a chip hover on the chart) tints ONLY the matching row", () => {
    const { container } = renderLedger({ activeN: 2 });
    expect(rowFor(container, 2)).toHaveClass("active");
    expect(rowFor(container, 1)).not.toHaveClass("active");
    expect(rowFor(container, 4)).not.toHaveClass("active");
  });
});

describe("EventLedger — the Cockpit strip", () => {
  it("shows the identity line and the present signal headlines", () => {
    renderLedger({ signals: [sig("sma_position", "above the 50d"), sig("range_52w", "mid-range")] });
    expect(screen.getByText("software/IT")).toBeInTheDocument();
    expect(screen.getByText("Technology")).toBeInTheDocument();
    expect(screen.getByText("$8.0B")).toBeInTheDocument();
    expect(screen.getByText("above the 50d")).toBeInTheDocument();
    expect(screen.getByText("mid-range")).toBeInTheDocument();
  });

  it("renders no headline block when no signal has a headline (honest loudness #7)", () => {
    const { container } = renderLedger({ signals: [] });
    expect(container.querySelector(".evled-headlines")).toBeNull();
    // the identity line still renders (the "—" discipline lives in identityCells, tested there)
    expect(screen.getByText("software/IT")).toBeInTheDocument();
  });

  it("captions the strip 'current tape · as-of X' only when a closed/matured asof is passed", () => {
    const { rerender } = renderLedger({ tapeAsof: null });
    expect(screen.queryByText(/current tape/)).toBeNull();
    rerender(
      <EventLedger
        events={EVENTS}
        activeN={null}
        onActivate={() => {}}
        identity={IDENTITY}
        signals={[]}
        tapeAsof="2026-07-15"
      />,
    );
    expect(screen.getByText("current tape · as-of Jul 15")).toBeInTheDocument();
  });
});
