import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// S3 — the shared "is this real?" data flag on the Cockpit basket table. Three rows, the flag text the real
// prod read produced (2026-10-22, scratch copy): MVNC's needs-review 1-for-100 split reaches its 7d/30d cells
// (in doubt: grayed, the reason on hover), SKYT's stopped feed reaches every price cell (known wrong: "—"),
// and OKLO carries nothing. Only the cells a flag names change; the mark rides after the ticker; the group's
// 7d median leaves the flagged values out and says so.
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string) => ({
    security_id: sid, ticker, name: `${ticker} Inc.`, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(4.2e7), fit: "", unconfirmed_estimates: 0,
  });
  const metric = (key: string, value: number) => ({
    key, label: key, value, unit: "pct", tone: value > 0 ? "pos" : "neg", note: null,
  });
  const trail = (sid: string, ticker: string, r7: number) => ({
    security_id: sid, ticker,
    signals: [{
      kind: "trailing_returns", label: "Trailing returns",
      metrics: [metric("ret_1d", 1.1), metric("ret_7d", r7), metric("ret_30d", -3.3), metric("ret_90d", 4.4), metric("ret_1y", 5.5)],
      basis: { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null },
    }],
  });
  const PRICE = ["sma", "ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y", "path"];
  return {
    thesis: {
      id: "t-ex", name: "The Everything Exchange", narrative: "n", ticker: null, segments: [],
      basket: [
        { ticker: "MVNC", role: "x", security_id: "s-mvnc", detail: null, authored_by: "system_drafted" },
        { ticker: "SKYT", role: "x", security_id: "s-skyt", detail: null, authored_by: "system_drafted" },
        { ticker: "OKLO", role: "x", security_id: "s-oklo", detail: null, authored_by: "system_drafted" },
        { ticker: "FISN", role: "x", security_id: "s-fisn", detail: null, authored_by: "system_drafted" },
        { ticker: "ARTL", role: "x", security_id: "s-artl", detail: null, authored_by: "system_drafted" },
      ],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { members: ["MVNC", "SKYT", "OKLO", "FISN", "ARTL"].map((t) => scoredRow(`s-${t.toLowerCase()}`, t)) },
    display: {
      members: [
        trail("s-mvnc", "MVNC", 61.0),
        trail("s-skyt", "SKYT", -2.0),
        trail("s-oklo", "OKLO", 6.0),
        trail("s-fisn", "FISN", 2.0),
        trail("s-artl", "ARTL", 4.0),
      ],
    },
    flags: {
      thesis_id: "t-ex", asof: "2026-10-22", names: 5,
      members: [
        {
          security_id: "s-mvnc", ticker: "MVNC", marked: true, theses: [],
          flags: [{
            kind: "split_needs_review", label: "split needs review", level: "in_doubt",
            reason: "Split needs review: a 1-for-100 reverse split listed for 2026-10-13 (only Massive.com lists it). It is not applied until it is confirmed, so prices across that day may sit on two scales.",
            clears: "Clears when the split is confirmed or rejected in Admin → Splits.",
            since: "2026-10-13", affects: ["price", "volume", "insider"],
            columns: ["sma", "ret_7d", "ret_30d", "ret_90d", "ret_1y", "path", "rvol8", "rvol20", "ins_30d", "ins_90d"],
            dates: ["2026-10-13"], evidence: [{ label: "Massive.com split reference (first session 2026-10-13)", url: null }],
            call_affected: false,
          }],
          splits: [{
            key_date: "2026-10-13", effective_date: "2026-10-13", label: "1-for-100 reverse split", factor: 100,
            direction: "reverse", status: "needs_review", confirmed_via: null, confirmed_by: null,
            value_source: "polygon", sec_form: null, sec_filed: null, sec_url: null, polygon: true, note: null,
            took_effect: false, corrected_on: null, corrected_note: null, bars_ago: 6,
          }],
        },
        {
          security_id: "s-skyt", ticker: "SKYT", marked: true, theses: [],
          flags: [{
            kind: "price_feed_stopped", label: "price feed stopped", level: "known_wrong",
            reason: "Stopped trading 2026-08-07: a delisting form was filed 2026-08-10. The last prices are final, not today's.",
            clears: "Does not clear: the company stopped trading.", since: "2026-08-07",
            affects: ["price", "volume", "market_cap"], columns: [...PRICE, "rvol8", "rvol20", "mktcap"],
            dates: [], evidence: [], call_affected: false,
          }],
          splits: [],
        },
        {
          // a confirmed, corrected split and no flag: no mark, nothing grayed
          security_id: "s-artl", ticker: "ARTL", marked: false, theses: [], flags: [],
          splits: [{
            key_date: "2026-08-31", effective_date: "2026-08-31", label: "1-for-9 reverse split", factor: 9,
            direction: "reverse", status: "confirmed", confirmed_via: "auto:sec+polygon",
            confirmed_by: "the SEC filing and Massive.com agree", value_source: "both", sec_form: "8-K",
            sec_filed: "2026-08-27", sec_url: "https://www.sec.gov/x", polygon: true, note: null,
            took_effect: true, corrected_on: "2026-10-22",
            corrected_note: "the vendor had not adjusted 1 bar(s) before it; Alpha Deck re-scaled them from the listed ratio",
            bars_ago: 36,
          }],
        },
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  useThesisDataFlags: () => ({ data: fx.flags, isLoading: false, error: null }),
  useDecisions: () => ({ data: [] }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit(selectedName: string | null = null) {
  return render(
    <Cockpit
      thesisId="t-ex"
      asof="2026-10-22"
      onAsofChange={() => {}}
      selectedName={selectedName}
      onSelectName={() => {}}
      railOpen
      onRailChange={() => {}}
    />,
  );
}

const row = (ticker: string) => screen.getByText(ticker).closest("tr") as HTMLElement;

describe("Cockpit — the data flags (S3)", () => {
  it("marks only the flagged rows, after the ticker, with the reason on hover", () => {
    renderCockpit();
    const mvncMark = row("MVNC").querySelector(".flag-mark") as HTMLElement;
    expect(mvncMark).toBeTruthy();
    expect(mvncMark.className).toContain("doubt");
    expect(mvncMark.title).toContain("1-for-100 reverse split");
    expect(mvncMark.title).toContain("affects SMA, 7d, 30d");
    expect((row("SKYT").querySelector(".flag-mark") as HTMLElement).className).toContain("wrong");
    // a clean row and a row whose split is confirmed and applied carry no mark (WB#3)
    expect(row("OKLO").querySelector(".flag-mark")).toBeNull();
    expect(row("ARTL").querySelector(".flag-mark")).toBeNull();
  });

  it("grays only the in-doubt cells and blanks only the known-wrong ones", () => {
    renderCockpit();
    const mvnc = row("MVNC").querySelectorAll("td.retc");
    // 1d is not reached (the step is older than the 1d window) — it renders as normal
    expect(mvnc[0].querySelector(".flag-doubt")).toBeNull();
    expect(mvnc[0].textContent).toBe("+1.1%");
    // 7d is reached: the number stays, grayed, the reason on hover
    const r7 = mvnc[1].querySelector(".flag-doubt") as HTMLElement;
    expect(r7.textContent).toBe("+61.0%");
    expect(r7.title).toContain("not applied until it is confirmed");
    // SKYT's stopped feed: every return cell is "—" with the reason, never the stale number
    for (const td of row("SKYT").querySelectorAll("td.retc")) {
      const s = td.querySelector(".flag-wrong") as HTMLElement;
      expect(s.textContent).toBe("—");
      expect(s.title).toContain("Stopped trading 2026-08-07");
    }
    expect(row("SKYT").textContent).not.toContain("-2.0%");
    // OKLO untouched
    expect(row("OKLO").querySelector(".flag-doubt, .flag-wrong")).toBeNull();
  });

  it("leaves the flagged 7d values out of the group median and says so", () => {
    renderCockpit();
    // every row reads Quiet (no card): one group; priced 7d = 61, -2, 6, 2, 4 -> MVNC (in doubt) and SKYT
    // (known wrong) left out -> median of 6, 2, 4 = 4.0
    const header = screen.getByRole("button", { name: /7d median/ });
    expect(header.textContent).toContain("7d median +4.0%");
    expect(header.textContent).toContain("2 left out");
    expect((header.querySelector(".agg") as HTMLElement).title).toContain("2 flagged 7d values left out");
  });

  it("puts a split tick on the sparkline only for an applied split inside the window", () => {
    renderCockpit();
    // no price_path series in this fixture -> the cell is "—" either way; the tick math is unit-tested
    expect(row("ARTL").querySelector(".spark-tick")).toBeNull();
  });

  it("opens the NamePanel with the full reason, the evidence and the split's events", () => {
    renderCockpit("MVNC");
    const panel = screen.getByRole("complementary", { name: /MVNC/i }) as HTMLElement;
    expect(panel.textContent).toContain("Data · this name");
    expect(panel.textContent).toContain("Clears when the split is confirmed or rejected");
    expect(panel.textContent).toContain("Massive.com split reference");
    expect(panel.textContent).toContain("needs review — not applied until it is confirmed");
  });

  it("the NamePanel lists a clean name's applied split with its sources and the correction", () => {
    renderCockpit("ARTL");
    const panel = screen.getByRole("complementary", { name: /ARTL/i }) as HTMLElement;
    expect(panel.textContent).toContain("confirmed — the SEC filing and Massive.com agree");
    expect(panel.textContent).toContain("took effect 2026-08-31 (prices before it ×9)");
    expect(panel.textContent).toContain("history corrected 2026-10-22");
    fireEvent.keyDown(window, { key: "Escape" });
  });
});
