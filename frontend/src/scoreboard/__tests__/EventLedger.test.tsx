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
    ingested: "2026-06-10", // == disclosed -> single "disclosed" line (the two-clock default)
    character: "open_market",
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

  // A1: a trigger row whose provenance resolved to a filing gets a quiet anchor beside the detail text.
  it("renders the trigger's resolved provenance as a new-tab anchor; link-less rows get none", () => {
    const linked = {
      label: "3 insiders bought",
      kind: "insider",
      ticker: "IBM",
      sources: [{ source: "form4", ref: "0001-a", url: "https://sec.gov/a-index.htm", detail: {} }],
    } as unknown as TriggerRefOut;
    const events = buildOverlayEvents(
      ep({ arm_date: "2026-06-01", triggers_at_arm: [linked] }),
      [],
      BARS as PriceBar[],
    );
    const { container } = renderLedger({ events });
    const a = container.querySelector("a.evled-link") as HTMLAnchorElement;
    expect(a).toHaveAttribute("href", "https://sec.gov/a-index.htm");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a).toHaveAttribute("rel", "noreferrer"); // never leaks the drawer's referrer
    expect(a.textContent).toContain("form4");
    // the ref is still legible as TEXT in the same cell — the link only adds the jump (#6)
    expect(a.closest("td")?.textContent).toContain("form4: 0001-a");
    // the default fixture's trigger carries no sources → no anchor at all (no empty affordance)
    expect(renderLedger().container.querySelector("a.evled-link")).toBeNull();
  });

  it("empty events → no table at all (mirrors the chart's empty state)", () => {
    const { container } = renderLedger({ events: [] });
    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelector(".sb-evledger")).toBeNull();
  });

  it("a risk row renders under its own family tint, distinct from the fact rows (Slice C)", () => {
    const risk = {
      label: "Share count creeping — +9.1% over 4 quarters",
      kind: "dilution_risk",
      event_date: "2026-06-10",
      ticker: "IBM",
    } as unknown as TriggerRefOut;
    const events = buildOverlayEvents(
      ep({ arm_date: "2026-06-01", risk_events: [risk] }),
      [],
      BARS as PriceBar[],
    );
    const { container } = renderLedger({ events });
    const riskRow = rowFor(container, 2); // armed #1, the risk #2 (2026-06-10)
    expect(within(riskRow).getByText("risk signal")).toBeInTheDocument();
    expect(riskRow.querySelector(".evled-n")).toHaveClass("ov-risk");
    expect(riskRow.textContent).toContain("Share count creeping");
    expect(riskRow).not.toHaveClass("evled-setaside"); // quiet family hue, not the excluded grey
  });

  it("a SET-ASIDE buy's row renders muted-but-present with its label; type stays 'insider buy' (S2c)", () => {
    // same shape as EVENTS but the insider buy is a primary-market set-aside — it must NOT vanish
    const events = buildOverlayEvents(
      ep({ warm_date: "2026-05-20", arm_date: "2026-06-01", triggers_at_arm: [TRIG] }),
      [buy({ d: "2026-06-10", character: "primary_market" })],
      BARS as PriceBar[],
    );
    const { container } = renderLedger({ events });
    expect(rows(container)).toHaveLength(events.length); // the set-aside row is present (WB #2)
    const setAsideRow = rowFor(container, 4);
    expect(setAsideRow).toHaveClass("evled-setaside"); // muted, never removed
    expect(within(setAsideRow).getByText("insider buy")).toBeInTheDocument(); // type unchanged
    expect(setAsideRow.textContent).toContain("primary-market (offer-price, set aside)");
    // the counted open-market row (the norm) carries NO muted class — loudness marks the exception
    const { container: normal } = renderLedger();
    expect(rowFor(normal, 4)).not.toHaveClass("evled-setaside");
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
