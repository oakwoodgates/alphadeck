import { describe, expect, it } from "vitest";

import type { DisplaySignal } from "../../api/hooks";
import { TAPE_CROSS_DAYS, tapeMarks } from "../tapeMarks";

// The Tape column's marks: only the EXCEPTION, each a display member's own verdict — nothing is derived here
// except how recent a cross is. The default is no mark at all (a muted "—" in the table).

const ASOF = "2026-10-22";
const basis = { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null };
const sig = (kind: string, over: Partial<DisplaySignal> = {}): DisplaySignal =>
  ({ kind, label: kind, metrics: [], events: [], basis, ...over }) as DisplaySignal;

const coiling = sig("vcp", {
  headline: { key: "coiling", label: "Base coiling — 3 tightening contractions above a rising 50d", glyph: "flat", detail: "range narrowed to 41% of the base start" },
});
const leading = sig("relative_strength", {
  headline: { key: "leading", label: "Leading — RS at a 13-week high vs SPY", glyph: "up", detail: "the name is pulling ahead of the market" },
});
const cross = (key: string, date: string) =>
  sig("sma_position", {
    events: [{ key, label: key === "golden_cross" ? "golden cross: 50d crossed above 200d" : "death cross: 50d crossed below 200d", date, direction: key === "golden_cross" ? "up" : "down" }],
  });
const none = { sma: null, vcp: null, rs: null };

describe("tapeMarks — the exception only", () => {
  it("a name with no members, or none of the exceptions, has no marks", () => {
    expect(tapeMarks(none, ASOF)).toEqual([]);
    // vcp without its coiling headline, RS merely inline, and a cross long past: all quiet
    expect(
      tapeMarks(
        {
          sma: cross("golden_cross", "2026-07-01"),
          vcp: sig("vcp", { headline: null }),
          rs: sig("relative_strength", { headline: { key: "inline", label: "Not at a 13-week RS high", glyph: null, detail: "moving with the market" } }),
        },
        ASOF,
      ),
    ).toEqual([]);
  });

  it("a coil reads the member's own words and carries no direction (a coil is sideways)", () => {
    const [m] = tapeMarks({ ...none, vcp: coiling }, ASOF);
    expect(m).toMatchObject({ key: "coiling", label: "coil", glyph: "flat", reach: "tapeShort" });
    expect(m.title).toBe(
      "Base coiling — 3 tightening contractions above a rising 50d — range narrowed to 41% of the base start",
    );
  });

  it("RS at a 13-week high is a mark only on the leading headline", () => {
    const [m] = tapeMarks({ ...none, rs: leading }, ASOF);
    expect(m).toMatchObject({ key: "rs_leading", label: "RS lead", glyph: "up", reach: "tapeShort" });
    expect(m.title).toContain("Leading — RS at a 13-week high vs SPY");
  });

  it("a golden / death cross is a mark for TAPE_CROSS_DAYS after its bar — day 30 in, day 31 out", () => {
    expect(TAPE_CROSS_DAYS).toBe(30);
    const golden = tapeMarks({ ...none, sma: cross("golden_cross", "2026-09-22") }, ASOF); // 30 days
    expect(golden).toHaveLength(1);
    expect(golden[0]).toMatchObject({ key: "golden_cross", label: "golden", glyph: "up", reach: "tapeLong" });
    expect(golden[0].title).toBe("golden cross: 50d crossed above 200d on Sep 22 (30d ago)");
    expect(tapeMarks({ ...none, sma: cross("golden_cross", "2026-09-21") }, ASOF)).toEqual([]); // 31 days

    const death = tapeMarks({ ...none, sma: cross("death_cross", "2026-10-22") }, ASOF);
    expect(death[0]).toMatchObject({ key: "death_cross", label: "death", glyph: "down" });
    expect(death[0].title).toBe("death cross: 50d crossed below 200d on Oct 22 (today)");
  });

  it("never marks a cross dated after the as-of (no lookahead), nor a price-vs-SMA cross", () => {
    expect(tapeMarks({ ...none, sma: cross("golden_cross", "2026-10-23") }, ASOF)).toEqual([]);
    const priceCross = sig("sma_position", {
      events: [{ key: "cross_sma50", label: "price crossed above 50d SMA", date: "2026-10-20", direction: "up" }],
    });
    expect(tapeMarks({ ...none, sma: priceCross }, ASOF)).toEqual([]);
  });

  it("orders the marks coil, RS lead, then the cross", () => {
    const marks = tapeMarks({ sma: cross("death_cross", "2026-10-15"), vcp: coiling, rs: leading }, ASOF);
    expect(marks.map((m) => m.key)).toEqual(["coiling", "rs_leading", "death_cross"]);
  });
});
