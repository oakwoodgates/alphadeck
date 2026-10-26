import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The `Tape` column on the Cockpit basket table: the EXCEPTIONAL tape readings only (a coiling base, RS at a
// 13-week high, a recent golden / death cross), off members the panel already renders, bridged by security_id.
// Load-bearing checks: the column sits behind `Off 52w high`, a name with nothing exceptional reads a muted
// "—" (the common case — a mark is the exception, #7), a stale cross is no mark, the column is NOT sortable,
// and a data flag grays a mark only where the bars it reads are reached (the server's vocabulary does not name
// this cell, so each chip borrows the named cells whose window contains its own).
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string) => ({
    security_id: sid, ticker, name: `${ticker} Inc.`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
  });
  const basis = { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null };
  const sma = (key: string, date: string) => ({
    kind: "sma_position", label: "SMA position", basis, metrics: [],
    events: [{
      key,
      label: key === "golden_cross" ? "golden cross: 50d crossed above 200d" : "death cross: 50d crossed below 200d",
      date, direction: key === "golden_cross" ? "up" : "down",
    }],
  });
  const vcp = (coiling: boolean) => ({
    kind: "vcp", label: "VCP", basis, metrics: [],
    headline: coiling
      ? { key: "coiling", label: "Base coiling — 3 tightening contractions above a rising 50d", glyph: "flat", detail: null }
      : null,
  });
  const rs = (leading: boolean) => ({
    kind: "relative_strength", label: "RS", basis, metrics: [],
    headline: leading
      ? { key: "leading", label: "Leading — RS at a 13-week high vs SPY", glyph: "up", detail: null }
      : { key: "inline", label: "Not at a 13-week RS high", glyph: null, detail: null },
  });
  const flag = (level: string, columns: string[], reason: string) => ({
    kind: "split_needs_review", label: "split needs review", level, reason, clears: "c", since: "2026-09-01",
    affects: [], columns, dates: ["2026-09-01"], evidence: [], call_affected: false,
  });
  return {
    flags: undefined as unknown,
    thesis: {
      id: "t-tape", name: "Tape", narrative: "n", ticker: null, segments: [],
      basket: ["OKLO", "FISN", "MNMD", "NONE"].map((t) => ({
        ticker: t, role: "core", security_id: `s-${t.toLowerCase()}`, detail: null, authored_by: "operator_set",
      })),
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { members: ["OKLO", "FISN", "MNMD", "NONE"].map((t) => scoredRow(`s-${t.toLowerCase()}`, t)) },
    display: {
      members: [
        // OKLO: all three marks (a coil, RS at a high, a golden cross 5 days ago)
        { security_id: "s-oklo", ticker: "OKLO", signals: [sma("golden_cross", "2026-10-17"), vcp(true), rs(true)] },
        // FISN: only a death cross, 12 days ago
        { security_id: "s-fisn", ticker: "FISN", signals: [sma("death_cross", "2026-10-10"), vcp(false), rs(false)] },
        // MNMD: carries every member, none exceptional — the golden cross is 90 days old
        { security_id: "s-mnmd", ticker: "MNMD", signals: [sma("golden_cross", "2026-07-24"), vcp(false), rs(false)] },
      ],
    },
    flag,
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: fx.flags, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

const renderCockpit = () =>
  render(
    <Cockpit
      thesisId="t-tape"
      asof="2026-10-22"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );

const rowOf = (tk: string) => screen.getByText(tk).closest("tr") as HTMLElement;
const cell = (tk: string) => rowOf(tk).querySelector("td.tapec") as HTMLElement;
const chips = (tk: string) => [...cell(tk).querySelectorAll(".tape-chip")] as HTMLElement[];
const flagsFor = (columns: string[], level = "in_doubt") => ({
  thesis_id: "t-tape", asof: "2026-10-22", names: 4,
  members: [{ security_id: "s-oklo", ticker: "OKLO", marked: true, theses: [], splits: [], flags: [fx.flag(level, columns, "a 3-for-2 split on 2026-09-01")] }],
});

beforeEach(() => {
  fx.flags = undefined;
});

describe("Cockpit — the Tape column", () => {
  it("sits right behind Off 52w high, and is a plain NON-sortable header like Path", () => {
    renderCockpit();
    const heads = [...document.querySelectorAll("thead th")].map((h) => h.textContent?.trim());
    expect(heads.indexOf("Tape")).toBe(heads.indexOf("Off 52w high") + 1);
    const th = screen.getByRole("columnheader", { name: "Tape" });
    expect(th.querySelector("button")).toBeNull();
    expect(th).not.toHaveAttribute("aria-sort");
  });

  it("draws one chip per exception, in a fixed order, each with the member's own words on hover", () => {
    renderCockpit();
    expect(chips("OKLO").map((c) => c.textContent)).toEqual(["→coil", "↑RS lead", "↑golden"]);
    expect(chips("OKLO").map((c) => c.getAttribute("title"))).toEqual([
      "Base coiling — 3 tightening contractions above a rising 50d",
      "Leading — RS at a 13-week high vs SPY",
      "golden cross: 50d crossed above 200d on Oct 17 (5d ago)",
    ]);
    // only the glyph carries a direction tint (the shared .dirg rule): flat · up · up
    expect(chips("OKLO").map((c) => c.querySelector(".dirg")?.className)).toEqual([
      "g dirg flat", "g dirg up", "g dirg up",
    ]);
    expect(chips("FISN").map((c) => c.textContent)).toEqual(["↓death"]);
    expect(chips("FISN")[0].querySelector(".dirg.down")).not.toBeNull();
  });

  it("reads a muted '—' for a name with nothing exceptional — a stale cross is no mark — and for a bare name", () => {
    renderCockpit();
    for (const tk of ["MNMD", "NONE"]) {
      expect(chips(tk)).toHaveLength(0);
      expect(cell(tk).textContent).toBe("—");
      expect(cell(tk).querySelector(".muted")).not.toBeNull();
    }
  });

  it("grays a mark only where the bars it reads are reached: a 90d flag the coil + RS, a 1Y-only flag the cross", () => {
    fx.flags = flagsFor(["ret_90d"]);
    const first = renderCockpit();
    expect(chips("OKLO").map((c) => c.closest(".flag-doubt") != null)).toEqual([true, true, false]);
    first.unmount();

    fx.flags = flagsFor(["ret_1y"]);
    renderCockpit();
    expect(chips("OKLO").map((c) => c.closest(".flag-doubt") != null)).toEqual([false, false, true]);
    // the reason rides the grayed chip's wrapper
    expect(chips("OKLO")[2].closest(".flag-doubt")?.getAttribute("title")).toBe("a 3-for-2 split on 2026-09-01");
  });

  it("a known-wrong flag replaces each mark it reaches with '—' and the reason", () => {
    fx.flags = flagsFor(["ret_90d", "ret_1y"], "known_wrong");
    renderCockpit();
    expect(chips("OKLO")).toHaveLength(0);
    const wrong = [...cell("OKLO").querySelectorAll(".flag-wrong")] as HTMLElement[];
    expect(wrong).toHaveLength(3);
    expect(wrong.every((w) => w.textContent === "—" && w.title === "a 3-for-2 split on 2026-09-01")).toBe(true);
  });
});
