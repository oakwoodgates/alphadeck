import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as rtlRender, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  InsiderBuyOut,
  MemberDisplaySignalsOut,
  PriceBar,
  ScoredMemberOut,
  ScoreboardEpisodeOut,
} from "../../api/hooks";
import { EpisodeScorecard } from "../EpisodeScorecard";
import { buildOverlayEvents } from "../overlay";

// The scorecard content: the four timing lenses render for a populated matured episode; honest loudness
// hides/quiets the lenses with no data. Slice B lifted the price-window + Cockpit reads into this component,
// so it now holds hooks — every render is wrapped in a QueryClientProvider (the hooks disable themselves
// without an asof, so the pure-lens assertions stay green). The lazy PriceSparkline is stubbed (its canvas
// is PriceSparkline.test's job); the ledger + strip are driven by SEEDED query data. Number-bearing lenses
// are scoped to their section (the same return string can appear in two).

// The chart is lazy + canvas-bound — stub it so the drawer's ledger/strip can be asserted without jsdom canvas.
vi.mock("../PriceSparkline", () => ({ PriceSparkline: () => null }));

/** Every render goes through a fresh QueryClient (staleTime ∞ so a seeded key never triggers a real fetch;
 *  an unseeded enabled query with no data would hit the network — so asof tests seed all three keys). */
function renderCard(ui: ReactElement, seed?: (qc: QueryClient) => void) {
  const qc = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, gcTime: Infinity, retry: false } },
  });
  seed?.(qc);
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    ticker: "DEVCO",
    is_headline: true,
    theme_armed: false,
    arm_date: "2026-07-10",
    dearm_date: null,
    close_reason: "window_end",
    status: "open",
    matured: false,
    censored_start: false,
    arm_ingest_fresh: null,
    freeze_era: false,
    thaw_lag_days: null,
    ingest_flagged: false,
    ingest_note: null,
    verdict: "core_entry",
    entry_grade: "core",
    conviction_grade: "flip",
    confidence: 0.9,
    exit_by: "2026-11-22",
    arm_until: null,
    warm_date: null,
    triggers_at_arm: [],
    entry_close: null,
    exit_close: null,
    exit_date: null,
    forward_return: null,
    arm_until_return: null,
    warm_return: null,
    peak_return: null,
    peak_date: null,
    exit_vs_peak_days: null,
    truncated: false,
    insufficient_prices: false,
    operator: null,
    ...over,
  } as ScoreboardEpisodeOut;
}

function bar(d: string, close: number): Pick<PriceBar, "d" | "close"> {
  return { d, close };
}

const MATURED = ep({
  status: "closed",
  matured: true,
  dearm_date: "2026-11-22",
  close_reason: "window_end",
  entry_close: 100,
  exit_close: 112,
  exit_date: "2026-11-22",
  forward_return: 0.12,
  peak_return: 0.18,
  peak_date: "2026-08-01",
  exit_vs_peak_days: 12,
  warm_date: "2026-06-20",
  warm_return: 0.2,
  arm_until: "2026-07-17",
  arm_until_return: 0.05,
});

/** The `<section>` for a lens, addressed by its header text. */
const lens = (name: string) => screen.getByText(name).closest("section") as HTMLElement;

describe("EpisodeScorecard — a populated matured episode", () => {
  it("heads with the identity, the arm→dearm span, and the MATURED mark", () => {
    renderCard(<EpisodeScorecard ep={MATURED} thesisName="HIMS" />);
    expect(screen.getByText("DEVCO")).toBeInTheDocument();
    expect(screen.getByText("· HIMS")).toBeInTheDocument();
    expect(screen.getByText(/Jul 10 → Nov 22/)).toBeInTheDocument();
    expect(screen.getByText("MATURED")).toBeInTheDocument();
    expect(screen.getByText(/closed · window_end/)).toBeInTheDocument();
  });

  it("Lens 1 — the move: prices, the realized return, its label", () => {
    renderCard(<EpisodeScorecard ep={MATURED} />);
    const t = lens("The move").textContent ?? "";
    expect(t).toContain("$100.00 @ Jul 10");
    expect(t).toContain("$112.00 @ Nov 22");
    expect(t).toContain("+12.0%");
    expect(t).toContain("realized");
  });

  it("Lens 2 — horizon calibration: the peak, its timing, the giveback", () => {
    renderCard(<EpisodeScorecard ep={MATURED} />);
    const t = lens("Horizon calibration").textContent ?? "";
    expect(t).toContain("+18.0%");
    expect(t).toContain("peak @ Aug 1");
    expect(t).toContain("horizon closed 12d after the peak");
    expect(t).toContain("gave back from +18.0% to +12.0%");
  });

  it("Lens 3 — edge preservation: armed late, with both priced legs", () => {
    renderCard(<EpisodeScorecard ep={MATURED} />);
    const t = lens("Edge preservation").textContent ?? "";
    expect(t).toContain("much of the move happened during warming — armed late");
    expect(t).toContain("warm-up +20.0% (from Jun 20)");
    expect(t).toContain("armed +12.0% (from Jul 10)");
  });

  it("Lens 4 — entry window + setup: the checkpoint return, grades, setup strength", () => {
    renderCard(<EpisodeScorecard ep={MATURED} />);
    const t = lens("Entry window + setup").textContent ?? "";
    expect(t).toContain("+5.0%");
    expect(t).toContain("to entry-window close Jul 17");
    expect(t).toContain("CORE"); // entry grade
    expect(t).toContain("FLIP"); // conviction grade
    expect(t).toContain("setup strength 90%");
  });
});

describe("EpisodeScorecard — honest loudness on thin data", () => {
  it("a running episode with no bar yet: the awaiting note, no price flow, no NaN/null", () => {
    const { container } = renderCard(<EpisodeScorecard ep={ep({ insufficient_prices: true })} />);
    expect(
      screen.getByText("awaiting the first forward bar — nothing to score yet"),
    ).toBeInTheDocument();
    expect(screen.getByText("awaiting first bar")).toBeInTheDocument(); // returnLabel
    expect(screen.queryByText(/@ Jul 10/)).toBeNull(); // no half-empty price flow ("— @ …")
    expect(container.textContent).not.toMatch(/NaN|null/);
  });

  it("hides the horizon lens entirely when there's no realized peak", () => {
    renderCard(<EpisodeScorecard ep={ep({ peak_return: null, peak_date: null })} />);
    expect(screen.queryByText("Horizon calibration")).toBeNull();
  });

  it("no warm_date → the one quiet edge line, not an empty comparison", () => {
    const { container } = renderCard(<EpisodeScorecard ep={ep({ warm_date: null })} />);
    expect(
      screen.getByText("armed without a visible warm-up (conviction + confirmation co-fired)"),
    ).toBeInTheDocument();
    expect(container.querySelector(".sc-cmp")).toBeNull(); // no two-legged comparison block
  });

  it("still armed reads 'still armed', not a dangling arrow", () => {
    renderCard(<EpisodeScorecard ep={ep({ dearm_date: null })} />);
    expect(screen.getByText(/Jul 10 → still armed/)).toBeInTheDocument();
  });
});

// The just-armed case (PBLS: armed with only the arm-day bar) — every forward return is a
// degenerate 0.0% over a single bar; the scorecard must mirror the ledger row's "—", not fake flats.
const JUST_ARMED = ep({
  ticker: "PBLS",
  arm_date: "2026-07-23",
  entry_close: 32.81,
  exit_close: 32.81,
  exit_date: "2026-07-23", // === arm_date → the single-bar / no-forward-bar case
  forward_return: 0,
  peak_return: 0,
  peak_date: "2026-07-23",
  exit_vs_peak_days: 0,
  arm_until: "2026-07-30",
  arm_until_return: 0,
  warm_date: null, // PBLS hits the no-warm branch
  entry_grade: "flip",
  conviction_grade: "flip",
  confidence: 0.6,
});

describe("EpisodeScorecard — a just-armed episode, the false-flat 0.0% guard", () => {
  it("the move shows the ENTRY only and a '—' return, never a flat 0.0%", () => {
    const { container } = renderCard(<EpisodeScorecard ep={JUST_ARMED} />);
    const move = lens("The move");
    expect(move.textContent).toContain("$32.81 @ Jul 23");
    expect(move.querySelector(".sc-arrow")).toBeNull(); // no arrow → no round-trip to the same bar
    expect(move.textContent).toContain("—"); // the return reads "—", mirroring the row
    expect(container.textContent).not.toContain("0.0%"); // nowhere on the card
    expect(
      screen.getByText("awaiting the first forward bar — nothing to score yet"),
    ).toBeInTheDocument();
  });

  it("hides the horizon lens (no real peak to judge yet)", () => {
    renderCard(<EpisodeScorecard ep={JUST_ARMED} />);
    expect(screen.queryByText("Horizon calibration")).toBeNull();
  });

  it("keeps the setup grades + strength but suppresses the degenerate arm_until return", () => {
    renderCard(<EpisodeScorecard ep={JUST_ARMED} />);
    const setup = lens("Entry window + setup");
    expect(setup.textContent).toContain("FLIP");
    expect(setup.textContent).toContain("setup strength 60%");
    expect(setup.textContent).not.toContain("entry-window close"); // the arm_until row is gone
  });

  it("a warm-up present + no bar → the honest 'not scorable' line, not a 0.0% comparison", () => {
    const { container } = renderCard(
      <EpisodeScorecard ep={ep({ ...JUST_ARMED, warm_date: "2026-07-20", warm_return: 0 })} />,
    );
    expect(screen.getByText("not scorable until a forward bar lands")).toBeInTheDocument();
    expect(container.querySelector(".sc-cmp")).toBeNull(); // no two-legged comparison
    expect(container.textContent).not.toContain("0.0%");
  });
});

// -------- Slice B: the event ledger + Cockpit strip, driven by SEEDED query data (asof present) ----------
const ASOF = "2026-07-15";
const LBARS = [
  bar("2026-06-15", 100),
  bar("2026-07-10", 104),
  bar("2026-11-22", 112),
] as unknown as PriceBar[];
const LBUY: InsiderBuyOut = {
  d: "2026-08-01",
  insider_name: "Jane Doe",
  insider_role: "CEO",
  shares: 5000,
  usd: 500_000,
  aff_10b5_1: false,
  disclosed: "2026-08-05",
  character: "open_market",
};
const SCORED = {
  security_id: "s1",
  ticker: "DEVCO",
  name: "Dev Co",
  business_type: "software_it",
  royalty: false,
  instrument_kind: "equity",
  sector: "Technology",
  exchange: "NYSE",
  market_cap: { value: 8e9, provenance: [] },
} as unknown as ScoredMemberOut;
const DISPLAY = {
  security_id: "s1",
  ticker: "DEVCO",
  signals: [
    {
      kind: "sma_position",
      label: "SMA position",
      headline: { key: "sma_position.above", label: "above the 50d", glyph: "up", detail: null },
      metrics: [],
      events: [],
      basis: { source: "sma", params: {} },
    },
  ],
} as unknown as MemberDisplaySignalsOut;

function seedLedger(qc: QueryClient) {
  qc.setQueryData(["episode-price-window", "t1", "s1", MATURED.arm_date, ASOF], {
    bars: LBARS,
    insider_buys: [LBUY],
  });
  qc.setQueryData(["workbench-scored", "t1", ASOF], { members: [SCORED] });
  qc.setQueryData(["display-signals", "t1", ASOF], { members: [DISPLAY] });
}

describe("EpisodeScorecard — Slice B: the ledger shares the chart's numbered events", () => {
  it("renders one ledger row per event, numbered exactly as buildOverlayEvents (row #N ↔ chip #N)", async () => {
    renderCard(<EpisodeScorecard ep={MATURED} asof={ASOF} />, seedLedger);
    const expected = buildOverlayEvents(MATURED, [LBUY], LBARS);
    // await the lazy chart's Suspense resolving inside act (the ledger itself renders synchronously)
    const section = (await screen.findByText("Event ledger")).closest("section") as HTMLElement;
    const rows = Array.from(section.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(expected.length);
    expect(rows.map((r) => r.querySelector(".evled-n")?.textContent)).toEqual(
      expected.map((e) => String(e.n)),
    );
  });

  it("surfaces the Cockpit identity line + the present signal headline, joined by security_id", async () => {
    renderCard(<EpisodeScorecard ep={MATURED} asof={ASOF} />, seedLedger);
    const section = (await screen.findByText("Event ledger")).closest("section") as HTMLElement;
    expect(section).toHaveTextContent("software/IT"); // the business-type leaf via its label
    expect(section).toHaveTextContent("Technology"); // sector
    expect(section).toHaveTextContent("$8.0B"); // market cap
    expect(section).toHaveTextContent("above the 50d"); // the display headline
    // MATURED is closed → the current-tape caption warns the 90d figure is name-current, not the episode's
    expect(section).toHaveTextContent("current tape · as-of Jul 15");
  });

  it("a no-forward-bar episode WITH pre-arm price data renders the ledger + chart (not gated on the forward bar)", async () => {
    const noBarEp = ep({ insufficient_prices: true });
    renderCard(<EpisodeScorecard ep={noBarEp} asof={ASOF} />, (qc) => {
      // the price window is the name's full pre-arm history (not arm-relative) → it loads for a fresh arm too
      qc.setQueryData(["episode-price-window", "t1", "s1", noBarEp.arm_date, ASOF], {
        bars: LBARS,
        insider_buys: [LBUY],
      });
      qc.setQueryData(["workbench-scored", "t1", ASOF], { members: [] });
      qc.setQueryData(["display-signals", "t1", ASOF], { members: [] });
    });
    const section = (await screen.findByText("Event ledger")).closest("section") as HTMLElement;
    expect(section.querySelectorAll("tbody tr").length).toBeGreaterThan(0);
  });
});
