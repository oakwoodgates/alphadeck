import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The `Off 52w high` and `ADV 20d` columns on the Cockpit basket table: two members the panel already
// renders (`range_52w`, `volume_regime`) surfaced at table grain off the SAME display-signals query, bridged
// by security_id. Load-bearing checks: where the columns sit (52w behind the Path it follows, ADV beside the
// Mkt cap it qualifies), the numbers and their show-the-work hovers, honest gaps, the sort (nulls last in both
// directions, ranking exactly what the cell shows), and that a data flag reaches them through the named cells
// whose bars they read — the server's flag vocabulary does not name these two.
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string) => ({
    security_id: sid, ticker, name: `${ticker} Inc.`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
  });
  const basis = (note: string | null = null) => ({
    source: "fact_price_eod", params: {}, bars_used: 252, window_start: "2025-10-20", window_end: "2026-10-22", note,
  });
  const range = (off: number | null, high: number | null) => ({
    kind: "range_52w", label: "52-week range", basis: basis(),
    metrics: [
      { key: "pct_off_52w_high", label: "off 52w high", value: off, unit: "pct", note: off == null ? "n/a: no bars" : null },
      { key: "high_52w", label: "52w high", value: high, unit: "price", note: high == null ? null : "on 2026-08-01" },
    ],
  });
  const volume = (adv: number | null, note: string | null = null, basisNote: string | null = null) => ({
    kind: "volume_regime", label: "Volume regime", basis: basis(basisNote),
    metrics: [
      { key: "vol_ratio", label: "vol 20d/60d", value: 1.1, unit: "ratio", note: null },
      { key: "adv_usd_20d", label: "$vol 20d", value: adv, unit: "usd", note },
    ],
  });
  const flag = (level: string, columns: string[], reason: string) => ({
    kind: "split_needs_review", label: "split needs review", level, reason, clears: "c", since: "2026-09-01",
    affects: [], columns, dates: ["2026-09-01"], evidence: [], call_affected: false,
  });
  return {
    flags: undefined as unknown,
    thesis: {
      id: "t-r", name: "Range", narrative: "n", ticker: null, segments: [],
      // authored order FISN, OKLO, THIN, NONE — so a sort that does nothing is visible
      basket: ["FISN", "OKLO", "THIN", "NONE"].map((t) => ({
        ticker: t, role: "core", security_id: `s-${t.toLowerCase()}`, detail: null, authored_by: "operator_set",
      })),
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { members: ["FISN", "OKLO", "THIN", "NONE"].map((t) => scoredRow(`s-${t.toLowerCase()}`, t)) },
    display: {
      members: [
        // FISN: far off its high, the MOST liquid; OKLO: near its high, less liquid — opposite ranks
        { security_id: "s-fisn", ticker: "FISN", signals: [range(-61.4, 41.2), volume(4.1e6)] },
        { security_id: "s-oklo", ticker: "OKLO", signals: [range(-3.2, 88.0), volume(1.2e6, null, "2 bars without volume excluded")] },
        // THIN: a thin volume history (an honest gap with its why); NONE carries neither member
        { security_id: "s-thin", ticker: "THIN", signals: [range(null, null), volume(null, "n/a: 12/20 volume bars")] },
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
      thesisId="t-r"
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
const off52w = (tk: string) => rowOf(tk).querySelector("td.rngc") as HTMLElement;
const adv = (tk: string) => rowOf(tk).querySelector("td.advc") as HTMLElement;
const order = () =>
  [...document.querySelectorAll("tr.bkt td.tk")].map((td) => td.textContent?.replace("⚑", "").trim());
const sortBy = (name: string) =>
  fireEvent.click(within(screen.getByRole("columnheader", { name })).getByRole("button"));

beforeEach(() => {
  fx.flags = undefined;
});

describe("Cockpit — Off 52w high + ADV 20d columns", () => {
  it("places 52w right behind Path and ADV right behind Mkt cap; both sort", () => {
    renderCockpit();
    const heads = [...document.querySelectorAll("thead th")].map((h) => h.textContent?.trim());
    expect(heads.indexOf("Off 52w high")).toBe(heads.indexOf("Path") + 1);
    expect(heads.indexOf("ADV 20d")).toBe(heads.indexOf("Mkt cap") + 1);
    for (const name of ["Off 52w high", "ADV 20d"]) {
      const th = screen.getByRole("columnheader", { name });
      expect(th).toHaveAttribute("aria-sort", "none");
      expect(th.querySelector("button.th-sort")).not.toBeNull();
    }
  });

  it("shows the number and its work: the high and its bar date, the window, the dollar volume's basis", () => {
    renderCockpit();
    expect(off52w("OKLO").textContent).toBe("-3.2%");
    expect(off52w("OKLO").querySelector(".off52w")?.getAttribute("title")).toBe(
      "52-week closing high 88.00 on 2026-08-01 · 252 bars · through Oct 22",
    );
    expect(off52w("FISN").textContent).toBe("-61.4%");
    expect(adv("FISN").textContent).toBe("$4.1M");
    expect(adv("OKLO").textContent).toBe("$1.2M");
    expect(adv("OKLO").querySelector(".adv")?.getAttribute("title")).toBe(
      "average dollar volume (close × volume) over the last 20 bars · 2 bars without volume excluded",
    );
  });

  it("reads an honest em-dash with the why for a gap, and for a name that carries neither member", () => {
    renderCockpit();
    expect(off52w("THIN").textContent).toBe("—");
    expect(off52w("THIN").querySelector(".muted")?.getAttribute("title")).toBe("n/a: no bars");
    expect(adv("THIN").textContent).toBe("—");
    expect(adv("THIN").querySelector(".muted")?.getAttribute("title")).toBe("n/a: 12/20 volume bars");
    expect(off52w("NONE").textContent).toBe("—");
    expect(adv("NONE").textContent).toBe("—");
  });

  it("sorts within the group — nearest the high / most liquid first on desc — gaps last in BOTH directions", () => {
    renderCockpit();
    expect(order()).toEqual(["FISN", "OKLO", "THIN", "NONE"]); // authored order
    sortBy("Off 52w high"); // desc: closest to the high first
    expect(order()).toEqual(["OKLO", "FISN", "THIN", "NONE"]);
    sortBy("Off 52w high"); // asc: furthest below first — the absent ones still sink
    expect(order()).toEqual(["FISN", "OKLO", "THIN", "NONE"]);
    sortBy("Off 52w high"); // off: back to the authored order
    expect(order()).toEqual(["FISN", "OKLO", "THIN", "NONE"]);

    sortBy("ADV 20d"); // desc: most liquid first — a different ranking than 52w
    expect(order()).toEqual(["FISN", "OKLO", "THIN", "NONE"]);
    sortBy("ADV 20d"); // asc
    expect(order()).toEqual(["OKLO", "FISN", "THIN", "NONE"]);
  });

  it("a flag on 1Y grays the 52w cell, a flag on 30d grays ADV — each only where its bars are read", () => {
    fx.flags = {
      thesis_id: "t-r", asof: "2026-10-22", names: 4,
      members: [
        { security_id: "s-oklo", ticker: "OKLO", marked: true, theses: [], splits: [], flags: [fx.flag("in_doubt", ["ret_1y"], "a 3-for-2 split on 2026-03-23")] },
        { security_id: "s-fisn", ticker: "FISN", marked: true, theses: [], splits: [], flags: [fx.flag("in_doubt", ["ret_30d"], "a 2-for-1 split on 2026-10-01")] },
      ],
    };
    renderCockpit();
    // OKLO: 1Y named -> 52w grayed (the number kept, the reason on hover); ADV untouched
    expect(off52w("OKLO").querySelector(".flag-doubt")?.textContent).toBe("-3.2%");
    expect(off52w("OKLO").querySelector(".flag-doubt")?.getAttribute("title")).toBe("a 3-for-2 split on 2026-03-23");
    expect(adv("OKLO").querySelector(".flag-doubt")).toBeNull();
    // FISN: 30d named -> ADV grayed; 52w untouched (a 30-bar flag cannot reach a 252-bar window's high)
    expect(adv("FISN").querySelector(".flag-doubt")?.textContent).toBe("$4.1M");
    expect(off52w("FISN").querySelector(".flag-doubt")).toBeNull();
  });

  it("a known-wrong flag blanks both cells to '—' with the reason", () => {
    fx.flags = {
      thesis_id: "t-r", asof: "2026-10-22", names: 4,
      members: [
        { security_id: "s-oklo", ticker: "OKLO", marked: true, theses: [], splits: [], flags: [fx.flag("known_wrong", ["ret_1y", "ret_30d", "rvol20"], "Stopped trading 2026-08-24")] },
      ],
    };
    renderCockpit();
    for (const cell of [off52w("OKLO"), adv("OKLO")]) {
      const wrong = cell.querySelector(".flag-wrong") as HTMLElement;
      expect(wrong.textContent).toBe("—");
      expect(wrong.title).toBe("Stopped trading 2026-08-24");
    }
  });
});
