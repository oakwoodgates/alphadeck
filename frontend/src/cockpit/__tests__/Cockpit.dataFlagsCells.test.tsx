import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// S3 fix — ONLY the cells a flag names change, and only once the reads it qualifies have landed. Real shapes
// and values from the Everything Exchange read on a fresh copy of prod (2026-10-22): SNEX (one history-only
// flag, columns [], and three that reach SMA / 90d / 1Y / Path / Mkt cap), a history-only-only row (SNEX's
// 2025-03 flag alone), OPEN (one flag, ["ret_1y"]), RMAX (stopped trading: known wrong on every price cell).
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, cap: number) => ({
    security_id: sid, ticker, name: `${ticker} Inc.`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(cap), fit: "", unconfirmed_estimates: 0,
  });
  const basis = { source: "fact_price_eod", params: { loud_mult: 1.5, loud_mult_20: 1.5 }, bars_used: null, window_start: null, window_end: null, note: null };
  const m = (key: string, value: number | null) => ({
    key, label: key, value, unit: key.startsWith("rvol") ? "ratio" : "pct",
    tone: value == null ? null : value > 0 ? "pos" : value < 0 ? "neg" : null, note: null,
  });
  const display = (sid: string, ticker: string, rets: (number | null)[], rv: [number, number] | null) => ({
    security_id: sid, ticker,
    signals: [
      { kind: "trailing_returns", label: "t", basis,
        metrics: ["ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y"].map((k, i) => m(k, rets[i])) },
      ...(rv ? [{ kind: "rvol", label: "r", basis, metrics: [m("rvol", rv[0]), m("rvol20", rv[1])] }] : []),
    ],
  });
  const flag = (kind: string, level: string, columns: string[], since: string, reason: string) => ({
    kind, label: kind.replace(/_/g, " "), level, reason, clears: "c", since, affects: [], columns,
    dates: [since], evidence: [], call_affected: false,
  });
  const historyOnly = flag("split_needs_review", "in_doubt", [], "2025-03-24",
    "Split needs review: a 3-for-2 forward split listed for 2025-03-24 (only Massive.com lists it). No column on this row reads across it (history only).");
  return {
    displayLoading: false,
    displayPlaceholder: false,
    thesis: {
      id: "t-ee", name: "The Everything Exchange", narrative: "n", ticker: null, segments: [],
      basket: ["SNEX", "HIST", "OPEN", "RMAX", "GME"].map((t) => ({
        ticker: t, role: "x", security_id: `s-${t.toLowerCase()}`, detail: null, authored_by: "system_drafted",
      })),
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: {
      members: [
        scoredRow("s-snex", "SNEX", 4.6e9), scoredRow("s-hist", "HIST", 4.6e9),
        scoredRow("s-open", "OPEN", 1.9e9), scoredRow("s-rmax", "RMAX", 3.0e8), scoredRow("s-gme", "GME", 1.18e10),
      ],
    },
    display: {
      members: [
        display("s-snex", "SNEX", [0.56, -4.89, -17.18, -34.08, 28.18], [0.76, 0.75]),
        display("s-hist", "HIST", [0.56, -4.89, -17.18, -34.08, 28.18], [0.76, 0.75]),
        display("s-open", "OPEN", [-5.34, -6.7, -35.0, -56.08, -73.54], [0.68, 0.8]),
        display("s-rmax", "RMAX", [-9.25, -0.56, 16.6, 109.49, null], null),
        display("s-gme", "GME", [-0.7, -3.5, 32.2, 20.8, 12.5], [0.54, 0.56]),
      ],
    },
    flags: {
      thesis_id: "t-ee", asof: "2026-10-22", names: 5,
      members: [
        {
          security_id: "s-snex", ticker: "SNEX", marked: true, theses: [], splits: [],
          flags: [
            flag("split_needs_review", "in_doubt", ["sma", "ret_1y"], "2026-03-23", "a 3-for-2 forward split listed for 2026-03-23"),
            flag("split_needs_review", "in_doubt", ["sma", "ret_90d", "ret_1y", "path"], "2026-07-20", "a 3-for-2 forward split listed for 2026-07-20"),
            flag("market_cap_share_count", "in_doubt", ["mktcap"], "2026-07-20", "Market cap in doubt"),
            historyOnly,
          ],
        },
        // SNEX's 2025-03 flag ALONE: a real flag, but no cell reads across it -> no mark, every cell normal
        { security_id: "s-hist", ticker: "HIST", marked: false, theses: [], splits: [], flags: [historyOnly] },
        {
          security_id: "s-open", ticker: "OPEN", marked: true, theses: [], splits: [],
          flags: [flag("split_needs_review", "in_doubt", ["ret_1y"], "2025-11-18", "a 31-for-30 forward split listed for 2025-11-18")],
        },
        {
          security_id: "s-rmax", ticker: "RMAX", marked: true, theses: [], splits: [],
          flags: [flag("price_feed_stopped", "known_wrong",
            ["sma", "ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y", "path", "rvol8", "rvol20", "mktcap"],
            "2026-08-24", "Stopped trading 2026-08-24: a delisting form was filed 2026-08-25.")],
        },
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () =>
    fx.displayLoading
      ? { data: undefined, isLoading: true, isPending: true, error: null }
      : { data: fx.display, isLoading: false, error: null, isPlaceholderData: fx.displayPlaceholder },
  useThesisDataFlags: () => ({ data: fx.flags, isLoading: false, error: null }),
  useDecisions: () => ({ data: [] }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

const renderCockpit = () =>
  render(
    <Cockpit
      thesisId="t-ee"
      asof="2026-10-22"
      onAsofChange={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );

const row = (tk: string) => screen.getByText(tk).closest("tr") as HTMLElement;
const rets = (tk: string) => [...row(tk).querySelectorAll("td.retc")] as HTMLElement[];
const grayed = (tk: string) => [...row(tk).querySelectorAll(".flag-doubt")].map((e) => e.textContent);

beforeEach(() => {
  fx.displayLoading = false;
  fx.displayPlaceholder = false;
});

describe("Cockpit — only the cells a flag names change (S3 fix)", () => {
  it("SNEX: the reaching flags gray SMA, 90d, 1Y, Path and Mkt cap; every other cell is its number", () => {
    renderCockpit();
    expect(row("SNEX").querySelector(".flag-mark")).toBeTruthy();
    expect(rets("SNEX").map((td) => td.textContent)).toEqual(["+0.6%", "-4.9%", "-17.2%", "-34.1%", "+28.2%"]);
    // 1d / 7d / 30d untouched; 90d and 1Y grayed (shown, never blanked)
    expect(rets("SNEX").map((td) => td.querySelector(".flag-doubt") != null)).toEqual([false, false, false, true, true]);
    expect(grayed("SNEX")).toContain("$4.6B");
    expect(row("SNEX").querySelector(".flag-wrong")).toBeNull();
    // the RVOL cells are not in any flag's columns: shown normally
    expect(row("SNEX").textContent).toContain("0.76×");
    expect(row("SNEX").textContent).toContain("0.75×");
  });

  it("a row whose only flag reaches no cell (columns []) gets no mark and no change at all", () => {
    renderCockpit();
    expect(row("HIST").querySelector(".flag-mark")).toBeNull();
    expect(row("HIST").querySelector(".flag-doubt, .flag-wrong")).toBeNull();
    expect(rets("HIST").map((td) => td.textContent)).toEqual(["+0.6%", "-4.9%", "-17.2%", "-34.1%", "+28.2%"]);
  });

  it("OPEN: one column (1Y) grays; the rest of the row renders normally", () => {
    renderCockpit();
    expect(row("OPEN").querySelector(".flag-mark")).toBeTruthy();
    expect(grayed("OPEN")).toEqual(["-73.5%"]);
    expect(rets("OPEN").slice(0, 4).map((td) => td.textContent)).toEqual(["-5.3%", "-6.7%", "-35.0%", "-56.1%"]);
    expect(row("OPEN").textContent).toContain("$1.9B");
  });

  it("RMAX (known wrong): its named cells are '—' with the reason; nothing it does not name is touched", () => {
    renderCockpit();
    const wrong = [...row("RMAX").querySelectorAll(".flag-wrong")] as HTMLElement[];
    expect(wrong.length).toBe(5 + 1 + 2 + 1 + 1); // five returns, SMA, two RVOL, Path, Mkt cap
    expect(wrong.every((e) => e.textContent === "—" && e.title.startsWith("Stopped trading"))).toBe(true);
    expect(row("RMAX").textContent).not.toContain("+109.5%");
  });

  it("no flag is applied while the display read is still loading (the flags arrive first)", () => {
    fx.displayLoading = true;
    renderCockpit();
    expect(document.querySelectorAll(".flag-mark, .flag-doubt, .flag-wrong")).toHaveLength(0);
  });

  it("no flag is applied over another view's numbers (a placeholder while the new read computes)", () => {
    fx.displayPlaceholder = true;
    renderCockpit();
    expect(document.querySelectorAll(".flag-mark, .flag-doubt, .flag-wrong")).toHaveLength(0);
  });
});
