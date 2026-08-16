import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DisplaySignal } from "../../api/hooks";
import { RotationStrip } from "../RotationStrip";

// The thesis-level rotation readings, shaped exactly as the backend producers emit them
// (signals/display/theme_breadth.py + signals/display/relative_strength.py): the SAME
// /display-signals payload the basket cells read, carried on its TOP-LEVEL breadth / sector_rs fields.
// Load-bearing checks: a breadth `thrust` reads LOUD with its % chip; a breadth `quiet` reads muted but
// still SHOWS; a sector_rs `leading` reads loud and NAMES the leaders; a null field renders nothing for
// it; both-null renders nothing at all (no empty shell); loudness is per-reading (#7). Display-only —
// it must never look like a trigger (#4/#7).

const breadthThrust = {
  kind: "theme_breadth",
  label: "Theme breadth (50d)",
  headline: {
    key: "thrust",
    label: "Breadth thrust — 80% above 50d, +40pts",
    glyph: "up",
    detail: "≥50% above the line and +40pts vs 20d — the theme is turning up together",
  },
  metrics: [
    { key: "breadth", label: "above 50d SMA", value: 80.0, unit: "pct", note: null },
    { key: "breadth_prior", label: "20d ago", value: 40.0, unit: "pct", note: null },
    { key: "breadth_delta", label: "Δ vs 20d", value: 40.0, unit: "pct", tone: "pos", note: null },
    { key: "members_counted", label: "members counted", value: 5.0, unit: "count", note: null },
    { key: "members_thin", label: "thin history", value: 0, unit: "count", note: null },
  ],
  basis: { source: "fact_price_eod", params: {} },
} as unknown as DisplaySignal;

const breadthQuiet = {
  kind: "theme_breadth",
  label: "Theme breadth (50d)",
  headline: {
    key: "quiet",
    label: "45% above 50d",
    glyph: null,
    detail: "Δ +5pts vs 20d — no thrust",
  },
  metrics: [
    { key: "breadth", label: "above 50d SMA", value: 45.0, unit: "pct", note: null },
    { key: "breadth_delta", label: "Δ vs 20d", value: 5.0, unit: "pct", tone: "pos", note: null },
  ],
  basis: { source: "fact_price_eod", params: {} },
} as unknown as DisplaySignal;

const breadthUnknown = {
  kind: "theme_breadth",
  label: "Theme breadth (50d)",
  headline: { key: "unknown", label: "breadth n/a — thin history", glyph: null },
  metrics: [
    {
      key: "breadth",
      label: "above 50d SMA",
      value: null,
      unit: "pct",
      note: "n/a: 0/3 members have 70+ bars",
    },
    { key: "members_counted", label: "members counted", value: 0, unit: "count", note: null },
    { key: "members_thin", label: "thin history", value: 3.0, unit: "count", note: null },
  ],
  basis: { source: "fact_price_eod", params: {} },
} as unknown as DisplaySignal;

const sectorLeading = {
  kind: "sector_rs",
  label: "Sector RS leadership (vs SPY)",
  headline: {
    key: "leading",
    label: "Leading: energy utilities, materials",
    glyph: "up",
    detail: "3 member(s) at a 13-week RS high vs the market",
  },
  metrics: [
    {
      key: "rs_lead_energy_utilities",
      label: "energy utilities",
      value: 2.0,
      unit: "count",
      note: "2/3 leading, 1 thin",
    },
    { key: "rs_lead_materials", label: "materials", value: 1.0, unit: "count", note: "1/2 leading" },
    { key: "rs_lead_technology", label: "technology", value: 0.0, unit: "count", note: "0/1 leading" },
  ],
  basis: { source: "fact_price_eod", params: {} },
} as unknown as DisplaySignal;

describe("RotationStrip — the thesis-level rotation strip (breadth + sector RS)", () => {
  it("breadth `thrust` renders LOUD with the % chip", () => {
    const { container } = render(<RotationStrip breadth={breadthThrust} sectorRs={null} />);
    const item = container.querySelector(".rot-item") as HTMLElement;
    expect(item.className).toContain("loud"); // the loud accent marks the exception (#7)
    expect(screen.getByText("Breadth")).toBeInTheDocument(); // the slot label
    // the breadth % rides a chip via fmtMetricValue (pct → signed, 1dp) — a real number, not a fake
    expect(screen.getByText("+80.0%")).toBeInTheDocument();
    // the loud headline states the thrust literally (glyph + label + detail, reused DisplayHeadlineRow)
    expect(screen.getByText("Breadth thrust — 80% above 50d, +40pts")).toBeInTheDocument();
    expect(container.querySelector(".np-ind-headline .g.dirg.up")?.textContent).toBe("↑");
  });

  it("breadth `quiet` renders MUTED — shown, not hidden, but no loud accent", () => {
    const { container } = render(<RotationStrip breadth={breadthQuiet} sectorRs={null} />);
    const item = container.querySelector(".rot-item") as HTMLElement;
    expect(item.className).not.toContain("loud"); // quiet == neutral (#7)
    // the reading still SHOWS (a lens re-orders/quiets, it never drops) — its % + quiet headline render
    expect(screen.getByText("45% above 50d")).toBeInTheDocument();
    expect(screen.getByText("+45.0%")).toBeInTheDocument();
  });

  it("an `unknown` (thin-history) breadth shows muted with an HONEST gap, never a fake % (#6/#7)", () => {
    const { container } = render(<RotationStrip breadth={breadthUnknown} sectorRs={null} />);
    expect((container.querySelector(".rot-item") as HTMLElement).className).not.toContain("loud");
    expect(screen.getByText("breadth n/a — thin history")).toBeInTheDocument();
    // the null-valued breadth reads an honest "—" with the WHY on hover — never a fabricated number
    const dash = screen.getByText("—");
    expect(dash.className).toContain("na");
    expect(dash.closest(".np-ind-chip")?.getAttribute("title")).toBe(
      "n/a: 0/3 members have 70+ bars",
    );
  });

  it("`sector_rs` `leading` renders LOUD and NAMES the leaders", () => {
    const { container } = render(<RotationStrip breadth={null} sectorRs={sectorLeading} />);
    const item = container.querySelector(".rot-item") as HTMLElement;
    expect(item.className).toContain("loud");
    expect(screen.getByText("Sector RS")).toBeInTheDocument();
    // the leading supersectors are named in the headline...
    expect(screen.getByText("Leading: energy utilities, materials")).toBeInTheDocument();
    // ...and ride per-supersector leader-count chips (fmtMetricValue count → integer)
    expect(screen.getByText("energy utilities")).toBeInTheDocument();
    expect(screen.getByText("materials")).toBeInTheDocument();
  });

  it("renders only the present reading when ONE field is null (no empty chip for the absent one, #7)", () => {
    const { container } = render(<RotationStrip breadth={breadthThrust} sectorRs={null} />);
    expect(container.querySelectorAll(".rot-item")).toHaveLength(1); // exactly one card
    expect(screen.getByText("Breadth")).toBeInTheDocument();
    expect(screen.queryByText("Sector RS")).toBeNull(); // the absent sector_rs contributes nothing
  });

  it("renders NOTHING at all when BOTH readings are absent (no empty shell, #7)", () => {
    const { container } = render(<RotationStrip breadth={null} sectorRs={null} />);
    expect(container.firstChild).toBeNull();
    expect(container.querySelector(".rot-strip")).toBeNull();
  });

  it("loudness is PER-READING: a quiet breadth beside a leading sector — each reads its OWN key (#7)", () => {
    const { container } = render(<RotationStrip breadth={breadthQuiet} sectorRs={sectorLeading} />);
    const items = container.querySelectorAll(".rot-item");
    expect(items).toHaveLength(2); // both present → two cards, breadth first (component order)
    expect(items[0].className).not.toContain("loud"); // Breadth — quiet
    expect(items[1].className).toContain("loud"); // Sector RS — leading
  });
});
