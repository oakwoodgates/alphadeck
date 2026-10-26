import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The `Last close` column: the newest END-OF-DAY close at or before the as-of, off the `sma_position` member's
// `close` metric ("last known price"). Load-bearing checks: where it sits and that it sorts, the price formats
// (a sub-dollar name keeps four decimals), a lagging bar — a halt, a thin name — says WHEN (relative to the
// basket's newest bar, so a weekend marks nothing), a name with no bars is an honest "—", and the data flags: a
// split flag on the newest bar grays or blanks it, but a STOPPED feed does not blank it — the last print is the
// point, so it shows with its date and the reason on hover.
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string) => ({
    security_id: sid, ticker, name: `${ticker} Inc.`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
  });
  const sma = (close: number | null, end: string | null, note: string | null = null) => ({
    kind: "sma_position", label: "SMA position", headline: null, events: [],
    metrics: [{ key: "close", label: "close", value: close, unit: "price", note: close == null ? "n/a" : null }],
    basis: { source: "fact_price_eod", params: {}, bars_used: 300, window_start: "2025-01-02", window_end: end, note },
  });
  const flag = (kind: string, level: string, columns: string[], reason: string) => ({
    kind, label: kind.replace(/_/g, " "), level, reason, clears: "c", since: "2026-09-01",
    affects: [], columns, dates: ["2026-09-01"], evidence: [], call_affected: false,
  });
  const tickers = ["FISN", "OKLO", "HALT", "BIG", "NONE", "SPLT", "STOP"];
  return {
    flags: undefined as unknown,
    thesis: {
      id: "t-last", name: "Last", narrative: "n", ticker: null, segments: [],
      basket: tickers.map((t) => ({
        ticker: t, role: "core", security_id: `s-${t.toLowerCase()}`, detail: null, authored_by: "operator_set",
      })),
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { members: tickers.map((t) => scoredRow(`s-${t.toLowerCase()}`, t)) },
    display: {
      members: [
        { security_id: "s-oklo", ticker: "OKLO", signals: [sma(27.71, "2026-10-22")] },
        { security_id: "s-fisn", ticker: "FISN", signals: [sma(0.0431, "2026-10-22")] },
        { security_id: "s-big", ticker: "BIG", signals: [sma(1234.5, "2026-10-22")] },
        // HALT: no print since Oct 14 — older than the basket's newest bar (Oct 22)
        { security_id: "s-halt", ticker: "HALT", signals: [sma(4.5, "2026-10-14")] },
        // NONE carries no member at all; SPLT: a normal bar with a split flag; STOP: tape ended Aug 24
        { security_id: "s-splt", ticker: "SPLT", signals: [sma(9.99, "2026-10-22")] },
        { security_id: "s-stop", ticker: "STOP", signals: [sma(2.25, "2026-08-24", "stale: last bar 59d before asof")] },
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
      thesisId="t-last"
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
const last = (tk: string) => rowOf(tk).querySelector("td.lastc") as HTMLElement;
const order = () =>
  [...document.querySelectorAll("tr.bkt td.tk")].map((td) => td.textContent?.replace("⚑", "").trim());
const member = (security_id: string, ticker: string, flags: unknown[]) => ({
  security_id, ticker, marked: true, theses: [], splits: [], flags,
});

beforeEach(() => {
  fx.flags = undefined;
});

describe("Cockpit — the Last close column", () => {
  it("sits right behind Type and sorts", () => {
    renderCockpit();
    const heads = [...document.querySelectorAll("thead th")].map((h) => h.textContent?.trim());
    expect(heads.indexOf("Last close")).toBe(heads.indexOf("Type") + 1);
    const th = screen.getByRole("columnheader", { name: "Last close" });
    expect(th).toHaveAttribute("aria-sort", "none");
    expect(th.querySelector("button.th-sort")).not.toBeNull();
  });

  it("prints the price: two decimals, thousands separated, four for a name under a dollar", () => {
    renderCockpit();
    expect(last("OKLO").textContent).toBe("$27.71");
    expect(last("BIG").textContent).toBe("$1,234.50");
    expect(last("FISN").textContent).toBe("$0.0431");
    expect(last("OKLO").querySelector(".last")?.getAttribute("title")).toBe(
      "last close $27.71 on Oct 22 (end of day, not a live quote)",
    );
  });

  it("says WHEN only for a bar older than the basket's newest — most rows carry no date", () => {
    renderCockpit();
    expect(last("OKLO").querySelector(".last-date")).toBeNull();
    expect(last("BIG").querySelector(".last-date")).toBeNull();
    expect(last("HALT").textContent).toBe("$4.50 · Oct 14");
    expect(last("HALT").querySelector(".last")?.className).toContain("old");
  });

  it("reads an honest em-dash for a name with no bars", () => {
    renderCockpit();
    expect(last("NONE").textContent).toBe("—");
    expect(last("NONE").querySelector(".muted")).not.toBeNull();
  });

  it("sorts by the price level — highest first on desc — a name with no bars last in BOTH directions", () => {
    renderCockpit();
    fireEvent.click(within(screen.getByRole("columnheader", { name: "Last close" })).getByRole("button"));
    expect(order()).toEqual(["BIG", "OKLO", "SPLT", "HALT", "STOP", "FISN", "NONE"]);
    fireEvent.click(within(screen.getByRole("columnheader", { name: "Last close" })).getByRole("button"));
    expect(order()).toEqual(["FISN", "STOP", "HALT", "SPLT", "OKLO", "BIG", "NONE"]);
  });

  it("grays the price when a flag names the newest bar (1d), blanks it when known wrong, ignores a 30d-only flag", () => {
    fx.flags = {
      thesis_id: "t-last", asof: "2026-10-22", names: 7,
      members: [
        member("s-splt", "SPLT", [fx.flag("split_needs_review", "in_doubt", ["ret_1d", "ret_7d"], "a 2-for-1 split on 2026-10-21")]),
        member("s-oklo", "OKLO", [fx.flag("split_needs_review", "in_doubt", ["ret_30d"], "an older split")]),
        member("s-big", "BIG", [fx.flag("split_hold", "known_wrong", ["ret_1d"], "a possible unconfirmed split")]),
      ],
    };
    renderCockpit();
    expect(last("SPLT").querySelector(".flag-doubt")?.textContent).toBe("$9.99");
    expect(last("SPLT").querySelector(".flag-doubt")?.getAttribute("title")).toBe("a 2-for-1 split on 2026-10-21");
    expect(last("OKLO").querySelector(".flag-doubt")).toBeNull(); // the 30d window is not the newest bar
    const wrong = last("BIG").querySelector(".flag-wrong") as HTMLElement;
    expect(wrong.textContent).toBe("—");
    expect(wrong.title).toBe("a possible unconfirmed split");
  });

  it("a STOPPED feed does not blank the last print: it shows with its date and the reason on hover", () => {
    fx.flags = {
      thesis_id: "t-last", asof: "2026-10-22", names: 7,
      members: [
        member("s-stop", "STOP", [
          fx.flag("price_feed_stopped", "known_wrong", ["sma", "ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y", "path"], "Stopped trading 2026-08-24: a delisting form was filed 2026-08-25."),
        ]),
      ],
    };
    renderCockpit();
    expect(last("STOP").querySelector(".flag-wrong")).toBeNull(); // not blanked…
    expect(last("STOP").textContent).toBe("$2.25 · Aug 24"); // …the last known price, dated
    const title = last("STOP").querySelector(".last")?.getAttribute("title") ?? "";
    expect(title).toContain("last close $2.25 on Aug 24");
    expect(title).toContain("Stopped trading 2026-08-24");
    // the RETURNS on the same row are blanked: the feed ending makes those wrong, not the last print
    expect(within(rowOf("STOP")).getAllByText("—").length).toBeGreaterThan(3);
  });
});
