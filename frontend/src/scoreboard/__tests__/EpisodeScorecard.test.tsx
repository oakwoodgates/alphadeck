import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScoreboardEpisodeOut } from "../../api/hooks";
import { EpisodeScorecard } from "../EpisodeScorecard";

// The scorecard content: the four timing lenses render for a populated matured episode; honest
// loudness hides/quiets the lenses with no data. Pure component — no hooks, so no mock needed.
// Number-bearing lenses are scoped to their section (the same return string can appear in two).

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
    render(<EpisodeScorecard ep={MATURED} thesisName="HIMS" />);
    expect(screen.getByText("DEVCO")).toBeInTheDocument();
    expect(screen.getByText("· HIMS")).toBeInTheDocument();
    expect(screen.getByText(/Jul 10 → Nov 22/)).toBeInTheDocument();
    expect(screen.getByText("MATURED")).toBeInTheDocument();
    expect(screen.getByText(/closed · window_end/)).toBeInTheDocument();
  });

  it("Lens 1 — the move: prices, the realized return, its label", () => {
    render(<EpisodeScorecard ep={MATURED} />);
    const t = lens("The move").textContent ?? "";
    expect(t).toContain("$100.00 @ Jul 10");
    expect(t).toContain("$112.00 @ Nov 22");
    expect(t).toContain("+12.0%");
    expect(t).toContain("realized");
  });

  it("Lens 2 — horizon calibration: the peak, its timing, the giveback", () => {
    render(<EpisodeScorecard ep={MATURED} />);
    const t = lens("Horizon calibration").textContent ?? "";
    expect(t).toContain("+18.0%");
    expect(t).toContain("peak @ Aug 1");
    expect(t).toContain("horizon closed 12d after the peak");
    expect(t).toContain("gave back from +18.0% to +12.0%");
  });

  it("Lens 3 — edge preservation: armed late, with both priced legs", () => {
    render(<EpisodeScorecard ep={MATURED} />);
    const t = lens("Edge preservation").textContent ?? "";
    expect(t).toContain("much of the move happened during warming — armed late");
    expect(t).toContain("warm-up +20.0% (from Jun 20)");
    expect(t).toContain("armed +12.0% (from Jul 10)");
  });

  it("Lens 4 — entry window + setup: the checkpoint return, grades, setup strength", () => {
    render(<EpisodeScorecard ep={MATURED} />);
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
    const { container } = render(<EpisodeScorecard ep={ep({ insufficient_prices: true })} />);
    expect(
      screen.getByText("awaiting the first forward bar — nothing to score yet"),
    ).toBeInTheDocument();
    expect(screen.getByText("awaiting first bar")).toBeInTheDocument(); // returnLabel
    expect(screen.queryByText(/@ Jul 10/)).toBeNull(); // no half-empty price flow ("— @ …")
    expect(container.textContent).not.toMatch(/NaN|null/);
  });

  it("hides the horizon lens entirely when there's no realized peak", () => {
    render(<EpisodeScorecard ep={ep({ peak_return: null, peak_date: null })} />);
    expect(screen.queryByText("Horizon calibration")).toBeNull();
  });

  it("no warm_date → the one quiet edge line, not an empty comparison", () => {
    const { container } = render(<EpisodeScorecard ep={ep({ warm_date: null })} />);
    expect(
      screen.getByText("armed without a visible warm-up (conviction + confirmation co-fired)"),
    ).toBeInTheDocument();
    expect(container.querySelector(".sc-cmp")).toBeNull(); // no two-legged comparison block
  });

  it("still armed reads 'still armed', not a dangling arrow", () => {
    render(<EpisodeScorecard ep={ep({ dearm_date: null })} />);
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
    const { container } = render(<EpisodeScorecard ep={JUST_ARMED} />);
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
    render(<EpisodeScorecard ep={JUST_ARMED} />);
    expect(screen.queryByText("Horizon calibration")).toBeNull();
  });

  it("keeps the setup grades + strength but suppresses the degenerate arm_until return", () => {
    render(<EpisodeScorecard ep={JUST_ARMED} />);
    const setup = lens("Entry window + setup");
    expect(setup.textContent).toContain("FLIP");
    expect(setup.textContent).toContain("setup strength 60%");
    expect(setup.textContent).not.toContain("entry-window close"); // the arm_until row is gone
  });

  it("a warm-up present + no bar → the honest 'not scorable' line, not a 0.0% comparison", () => {
    const { container } = render(
      <EpisodeScorecard ep={ep({ ...JUST_ARMED, warm_date: "2026-07-20", warm_return: 0 })} />,
    );
    expect(screen.getByText("not scorable until a forward bar lands")).toBeInTheDocument();
    expect(container.querySelector(".sc-cmp")).toBeNull(); // no two-legged comparison
    expect(container.textContent).not.toContain("0.0%");
  });
});
