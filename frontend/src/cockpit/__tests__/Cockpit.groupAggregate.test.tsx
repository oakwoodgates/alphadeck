import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The group header's "is this group moving?" line — `<Group> · N names · 7d median ±X.X%` — on every
// grouped lens of the Cockpit basket table. A CLIENT-SIDE aggregate over the 7d trailing-return
// values already on the wire (the `trailing_returns` member's `ret_7d`, the same value the 7d
// column shows — zero wire, nothing re-derived). The load-bearing checks: the MEDIAN (never the
// mean — a single +40% blowup must not swing it), 7d only, N = the FULL group size (an unpriced
// name counts in N but not in the median), "—" under three priced names, computed per group in
// EACH lens (call-state / business-type / value-chain), muted (header-only, no per-row mark), and
// untouched by a fold or a sort (it changes no membership and no order — a display read, never a
// call input). Fixture shape mirrors Cockpit.typelens / Cockpit.trailingReturns.
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, over: Record<string, unknown>) => ({
    security_id: sid, ticker, name: `${ticker} Co`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
    ...over,
  });
  const miner = (sid: string, ticker: string) =>
    scoredRow(sid, ticker, { business_type: "miner", business_supersector: "materials" });
  const utility = (sid: string, ticker: string) =>
    scoredRow(sid, ticker, { business_type: "utilities", business_supersector: "energy_utilities" });
  const metric = (key: string, value: number | null, note: string | null = null) => ({
    key, label: key.replace("ret_", ""), value, unit: "pct",
    tone: value == null || value === 0 ? null : value > 0 ? "pos" : "neg", note,
  });
  // every priced name ALSO carries a +99 30d, so the header provably reads the 7d key, not
  // "whatever return is there"
  const trailSig = (ret7d: number | null, note: string | null = null) => ({
    kind: "trailing_returns", label: "Trailing returns",
    metrics: [metric("ret_7d", ret7d, note), metric("ret_30d", 99)],
    basis: { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null },
  });
  const member = (ticker: string, sid: string, segment: string) => ({
    ticker, role: "core", security_id: sid, segment, detail: null, thesis_fit: null, authored_by: "operator_set",
  });
  const armedMember = (sid: string, ticker: string) => ({
    security_id: sid, ticker, verdict: "starter_entry", conviction_grade: "core", confirmation_grade: null,
    entry_grade: null, confidence: null, exit_by: null, arm_until: null, lapsing: false, theme_armed: false,
    triggers: [],
  });
  return {
    thesis: {
      id: "t-agg", name: "Copper Squeeze", narrative: "n", ticker: null,
      segments: [{ label: "Miners", descriptor: null }, { label: "Utilities", descriptor: null }],
      basket: [
        member("AAA", "s-a", "Miners"), member("BBB", "s-b", "Miners"), member("CCC", "s-c", "Miners"),
        member("DDD", "s-d", "Miners"), member("EEE", "s-e", "Miners"), member("FFF", "s-f", "Miners"),
        member("GGG", "s-g", "Utilities"), member("HHH", "s-h", "Utilities"), member("III", "s-i", "Utilities"),
      ],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    // AAA / BBB / EEE are ARMED — three names (the median's floor), one of them the +40% blowup;
    // every other row reads Quiet
    call: {
      thesis_id: "t-agg", asof: "2026-09-08", state: "armed", verdict: "starter_entry",
      conviction_grade: "core", confirmation_grade: null, entry_grade: null, expression: "",
      exit_by: null, arm_until: null, catalyst_surface: [], confidence: null, actions: [],
      key_conviction: { turned: true, label: "Conviction", detail: "" },
      key_confirmation: { turned: false, label: "Confirmation", detail: "" },
      triggers_fired: [], missing: [], risk_signals: [], counter_case: [],
      armed_members: [armedMember("s-a", "AAA"), armedMember("s-b", "BBB"), armedMember("s-e", "EEE")],
      watch_members: [],
    },
    scored: {
      members: [
        miner("s-a", "AAA"), miner("s-b", "BBB"), miner("s-c", "CCC"),
        miner("s-d", "DDD"), miner("s-e", "EEE"), miner("s-f", "FFF"),
        utility("s-g", "GGG"), utility("s-h", "HHH"), utility("s-i", "III"),
      ],
    },
    display: {
      members: [
        { security_id: "s-a", ticker: "AAA", signals: [trailSig(-6.0)] },
        { security_id: "s-b", ticker: "BBB", signals: [trailSig(-1.0)] },
        { security_id: "s-c", ticker: "CCC", signals: [trailSig(2.0)] },
        { security_id: "s-d", ticker: "DDD", signals: [trailSig(3.5)] },
        { security_id: "s-e", ticker: "EEE", signals: [trailSig(40.0)] }, // the single blowup
        { security_id: "s-f", ticker: "FFF", signals: [trailSig(null, "n/a: 3/8 bars")] }, // thin history: unpriced
        { security_id: "s-g", ticker: "GGG", signals: [trailSig(1.0)] },
        { security_id: "s-h", ticker: "HHH", signals: [trailSig(-2.0)] },
        { security_id: "s-i", ticker: "III", signals: [] }, // no trailing member at all: unpriced
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
  // the armed card's decision rail (inert here — this test never acts on the call)
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
  usePostDecision: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-agg"
      asof="2026-09-08"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );
}

/** Every group header, in render order: label · the count span · the moving line (text, the value's
 *  tone class, the show-the-work hover). */
const headers = (c: HTMLElement) =>
  [...c.querySelectorAll(".grp-h")].map((h) => ({
    label: h.querySelector(".lbl")?.textContent,
    ct: h.querySelector(".ct")?.textContent,
    agg: h.querySelector(".agg")?.textContent,
    tone: h.querySelector(".aggv")?.className,
    title: h.querySelector(".agg")?.getAttribute("title"),
  }));

describe("Cockpit — the group header's moving line (7d median)", () => {
  it("call-state lens: N names + the MEDIAN 7d return per bucket — an outlier does not swing it", () => {
    const { container } = renderCockpit();
    expect(headers(container)).toEqual([
      // Armed = AAA −6 / BBB −1 / EEE +40: the median is −1.0 (a mean would read +11.0%)
      {
        label: "Armed", ct: "· 3 names", agg: "· 7d median -1.0%", tone: "aggv neg",
        title: "median 7d return over the 3 priced of 3 names",
      },
      // Quiet = CCC +2 / DDD +3.5 / FFF — / GGG +1 / HHH −2 / III —: 6 names, 4 priced (an EVEN
      // count → the mean of the two middles, 1 and 2 = +1.5; a mean would read +1.1%)
      {
        label: "Quiet", ct: "· 6 names", agg: "· 7d median +1.5%", tone: "aggv pos",
        title: "median 7d return over the 4 priced of 6 names",
      },
    ]);
    // the means are nowhere on the page — the header is a median, never a mean
    expect(screen.queryAllByText("+11.0%")).toHaveLength(0);
    expect(screen.queryAllByText("+1.1%")).toHaveLength(0);
  });

  it("business-type lens: per super-sector — and an honest '—' under three priced names", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    expect(headers(container)).toEqual([
      // GGG +1 / HHH −2 / III —: three names but only TWO priced → no median, and the why on hover
      {
        label: "Energy & Utilities", ct: "· 3 names", agg: "· 7d median —", tone: "aggv na",
        title: "no median below 3 priced names (2 of 3 priced)",
      },
      // AAA −6 / BBB −1 / CCC +2 / DDD +3.5 / EEE +40 / FFF —: 6 names, 5 priced → median +2.0
      // (a mean would read +7.7%)
      {
        label: "Materials", ct: "· 6 names", agg: "· 7d median +2.0%", tone: "aggv pos",
        title: "median 7d return over the 5 priced of 6 names",
      },
    ]);
    expect(screen.queryAllByText("+7.7%")).toHaveLength(0);
  });

  it("value-chain lens: per link — the same rules, the same values", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    expect(headers(container)).toEqual([
      {
        label: "Miners", ct: "· 6 names", agg: "· 7d median +2.0%", tone: "aggv pos",
        title: "median 7d return over the 5 priced of 6 names",
      },
      {
        label: "Utilities", ct: "· 3 names", agg: "· 7d median —", tone: "aggv na",
        title: "no median below 3 priced names (2 of 3 priced)",
      },
    ]);
  });

  it("counts an unpriced name in N but leaves it out of the median — the row's own 7d '—'", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "value chain" }));
    const miners = [...container.querySelectorAll(".grp-h")].find(
      (h) => h.querySelector(".lbl")?.textContent === "Miners",
    ) as HTMLElement;
    expect(miners.querySelector(".ct")?.textContent).toBe("· 6 names"); // FFF counted
    expect(miners.querySelector(".agg")?.getAttribute("title")).toBe(
      "median 7d return over the 5 priced of 6 names", // FFF excluded
    );
    // FFF's 7d cell is the SAME "—" (one predicate: absent-or-null) — and its +99 30d, which is on
    // the wire, is never read by the 7d-only header
    const fffCells = (screen.getByText("FFF").closest("tr") as HTMLElement).querySelectorAll("td.retc");
    expect(fffCells[1].textContent).toBe("—");
    expect(fffCells[2].textContent).toBe("+99.0%");
  });

  it("is muted context, not a badge: header-only, survives a fold, unmoved by a sort", () => {
    const { container } = renderCockpit();
    expect(container.querySelectorAll("tr.bkt .agg")).toHaveLength(0); // never a per-row mark
    const before = headers(container);
    // fold Quiet — the count AND the moving line stay on the closed header (nothing reads as dropped)
    fireEvent.click(container.querySelector("tr.grp.bkt-quiet .grp-h") as HTMLElement);
    expect(headers(container)).toEqual(before);
    // sort by 7d — a re-order WITHIN each group changes neither N nor the median
    fireEvent.click(within(screen.getByRole("columnheader", { name: "7d" })).getByRole("button"));
    expect(headers(container)).toEqual(before);
    expect(container.querySelectorAll("tr.bkt")).toHaveLength(9); // and drops nothing
  });
});
