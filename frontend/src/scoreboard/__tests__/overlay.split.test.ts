import { describe, expect, it } from "vitest";

import type { DataFlagOut, DataFlagSplitOut, ScoreboardEpisodeOut } from "../../api/hooks";
import { ledgerRow } from "../ledger";
import {
  buildOverlayEvents,
  eventSetAside,
  firstBarOnOrAfter,
  flagBands,
  legendEntries,
  overlayTooltip,
} from "../overlay";

// S3 (plan section 11): the "split" event family on the drawer chart — a chip on the FIRST BAR AT THE NEW
// PRICE, labeled "1-for-16 reverse split · SEC 8-K filed 2026-10-09 · prices before this day ×16" — and the
// shaded bands over a data flag's affected bars. BTOC's real split row (the prod list, 2026-10-22).

const ep = {
  thesis_id: "t1",
  security_id: "s1",
  arm_date: "2026-10-01",
  warm_date: null,
  dearm_date: null,
  exit_by: null,
  close_reason: "",
  triggers_at_arm: [],
} as unknown as ScoreboardEpisodeOut;

const bars = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"].map((d, i) => ({
  d,
  close: 10 + i,
}));

const btoc: DataFlagSplitOut = {
  key_date: "2026-10-15",
  effective_date: "2026-10-15",
  label: "1-for-16 reverse split",
  factor: 16,
  direction: "reverse",
  status: "confirmed",
  confirmed_via: "auto:sec+polygon",
  confirmed_by: "the SEC filing and Polygon agree",
  value_source: "both",
  sec_form: "8-K",
  sec_filed: "2026-10-09",
  sec_url: "https://www.sec.gov/Archives/edgar/data/x",
  polygon: true,
  note: null,
  took_effect: true,
  corrected_on: "2026-10-22",
  corrected_note:
    "the vendor had not adjusted 527 bar(s) before it; Alpha Deck re-scaled them from the listed ratio",
  bars_ago: 2,
};

describe("the split family", () => {
  it("sits on the first bar at the new price and reads the plan's label", () => {
    const evs = buildOverlayEvents(ep, [], bars, undefined, { splits: [btoc] });
    const s = evs.find((e) => e.family === "split")!;
    expect(s.date).toBe("2026-10-15");
    const tip = overlayTooltip(s);
    expect(tip.title).toBe("1-for-16 reverse split");
    expect(tip.lines).toEqual([
      "first day at the new price 2026-10-15",
      "SEC 8-K filed 2026-10-09",
      "prices before this day ×16",
      "confirmed: the SEC filing and Polygon agree",
      "history corrected 2026-10-22 — the vendor had not adjusted 527 bar(s) before it; Alpha Deck re-scaled them from the listed ratio",
    ]);
    expect(eventSetAside(s)).toBe(false);
    expect(legendEntries(evs).map((l) => l.label)).toContain("split");
    expect(ledgerRow(s).type).toBe("split");
    expect(ledgerRow(s).detail).toContain("prices before this day ×16");
  });

  it("a split on a day with no bar sits on the next loaded bar; one past the tape draws nothing", () => {
    const weekend = { ...btoc, effective_date: "2026-10-17", key_date: "2026-10-17" };
    const later = { ...btoc, effective_date: "2026-10-26", key_date: "2026-10-26", status: "announced" };
    const evs = buildOverlayEvents(ep, [], bars, undefined, { splits: [weekend, later] });
    const splits = evs.filter((e) => e.family === "split");
    expect(splits.map((e) => e.date)).toEqual(["2026-10-19"]);
    expect(overlayTooltip(splits[0]).lines[0]).toBe(
      "first session 2026-10-17 · first loaded bar 2026-10-19",
    );
    expect(firstBarOnOrAfter(bars, "2026-10-26")).toBeNull();
  });

  it("a split that needs review is listed grayed and says it is not applied", () => {
    const review = {
      ...btoc,
      status: "needs_review",
      confirmed_via: null,
      confirmed_by: null,
      sec_filed: null,
      sec_form: null,
      corrected_on: null,
      corrected_note: null,
    };
    const s = buildOverlayEvents(ep, [], bars, undefined, { splits: [review] }).find(
      (e) => e.family === "split",
    )!;
    expect(eventSetAside(s)).toBe(true);
    const lines = overlayTooltip(s).lines;
    expect(lines).toContain("listed by Polygon");
    expect(lines.some((l) => l.startsWith("needs review — not applied"))).toBe(true);
    expect(lines.some((l) => l.startsWith("prices before this day"))).toBe(false);
  });

  it("a forward split reads as a division", () => {
    const fwd = { ...btoc, label: "3-for-1 forward split", factor: 1 / 3, direction: "forward" };
    const s = buildOverlayEvents(ep, [], bars, undefined, { splits: [fwd] }).find(
      (e) => e.family === "split",
    )!;
    expect(overlayTooltip(s).lines).toContain("prices before this day ÷3");
  });
});

describe("flagBands — the shaded runs over a flag's affected bars", () => {
  const f = (dates: string[], level = "in_doubt"): DataFlagOut => ({
    kind: "mixed_scale_history",
    label: "mixed-scale price history",
    level: level as DataFlagOut["level"],
    reason: "why",
    clears: "c",
    since: null,
    affects: [],
    columns: [],
    dates,
    evidence: [],
    call_affected: false,
  });

  it("groups consecutive loaded bars into one band and skips dates the window does not load", () => {
    expect(flagBands([f(["2026-10-13", "2026-10-14", "2026-10-16", "2026-09-01"])], bars)).toEqual([
      { from: "2026-10-13", to: "2026-10-14", level: "in_doubt", reason: "why" },
      { from: "2026-10-16", to: "2026-10-16", level: "in_doubt", reason: "why" },
    ]);
    expect(flagBands([f([])], bars)).toEqual([]);
  });
});
