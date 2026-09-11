import { describe, expect, it } from "vitest";

import type {
  ScoreboardEpisodeOut,
  ScoreboardMetricOut,
  ScoreboardSummaryOut,
  ScoreboardThesisOut,
} from "../../api/hooks";
import {
  awaitingForwardBar,
  closeReasonBadge,
  closeReasonLabel,
  closeReasonLine,
  episodeBadges,
  excursionTitle,
  fmtPastPeak,
  fmtReturn,
  gateMetrics,
  groupCount,
  groupHint,
  groupToneClass,
  ingestProvenanceLine,
  ledgerColCount,
  maturityHorizon,
  metricHeadline,
  operatorLine,
  pathTitle,
  returnLabel,
  triggerChips,
} from "../rows";

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
    conviction_grade: "core",
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

function metric(over: Partial<ScoreboardMetricOut> = {}): ScoreboardMetricOut {
  return {
    name: "arm_timing_forward_return",
    claim: "timing",
    n: 0,
    insufficient_n: true,
    summary: {},
    detail: [],
    note: "",
    ...over,
  } as ScoreboardMetricOut;
}

describe("fmtReturn", () => {
  it("signs and tones both directions, dash when unknowable", () => {
    expect(fmtReturn(0.052)).toEqual({ text: "+5.2%", cls: "pos" });
    expect(fmtReturn(-0.021)).toEqual({ text: "-2.1%", cls: "neg" });
    expect(fmtReturn(0)).toEqual({ text: "0.0%", cls: "" });
    expect(fmtReturn(null)).toEqual({ text: "—", cls: "" });
  });
});

describe("fmtPastPeak — the Timing view's past-peak gap cell (Slice 2)", () => {
  it("renders the day gap as Nd; a real 0d (exited AT the peak) is kept, dash when unknowable", () => {
    expect(fmtPastPeak(7)).toBe("7d");
    expect(fmtPastPeak(0)).toBe("0d"); // exited at the peak — meaningful, not zero-filled away
    expect(fmtPastPeak(null)).toBe("—");
    expect(fmtPastPeak(undefined)).toBe("—");
  });
});

describe("ledgerColCount — the group-row colSpan tracks the view (Slice 2)", () => {
  it("Summary spans 9 columns, Timing 11", () => {
    // Timing went 6 -> 8 (Path + Worst) -> 11: De-armed split out of Armed, and the excursion pair
    // was promoted out of the hovers into four columns. Summary went 8 -> 9 with De-armed alone.
    // The whole-table span test in Scoreboard.test.tsx is what catches a column added here but not
    // in all four places; this only pins the number.
    expect(ledgerColCount("summary")).toBe(9);
    expect(ledgerColCount("timing")).toBe(11);
  });
});

describe("excursionTitle — four columns, four hovers, none restating its neighbour", () => {
  const full = ep({
    peak_return: 0.204,
    peak_date: "2026-08-10",
    trough_return: -0.056,
    trough_date: "2026-07-28",
    intraday_high_return: 0.231,
    intraday_high_date: "2026-08-11",
    intraday_low_return: -0.084,
    intraday_low_date: "2026-07-27",
  });

  it("a CLOSE hover names its excursion and its own date — and nothing about the wick", () => {
    const peak = excursionTitle(full, "peak", "close");
    expect(peak).toContain("maximum favorable excursion (MFE)");
    expect(peak).toContain("on Aug 10");
    const worst = excursionTitle(full, "worst", "close");
    expect(worst).toContain("maximum adverse excursion (MAE)");
    expect(worst).toContain("on Jul 28");
    // the repetition the columns were promoted to remove: the wick has its own cell now, so the
    // close cell must not restate it
    for (const t of [peak, worst]) {
      expect(t).not.toContain("intraday");
      expect(t).not.toContain("+23.1%");
      expect(t).not.toContain("-8.4%");
    }
  });

  it("a WICK hover names the traded extreme and its own date — and not the close", () => {
    const peak = excursionTitle(full, "peak", "wick");
    expect(peak).toContain("intraday high");
    expect(peak).toContain("on Aug 11");
    const worst = excursionTitle(full, "worst", "wick");
    expect(worst).toContain("intraday low");
    expect(worst).toContain("on Jul 27");
    // each side reads its OWN date — a crossed pair would be silently wrong on every row
    expect(peak).not.toContain("Jul 27");
    expect(worst).not.toContain("Aug 11");
    // no MFE/MAE restatement, and no close figure leaking into the wick cell
    for (const t of [peak, worst]) {
      expect(t).not.toContain("excursion");
      expect(t).not.toContain("+20.4%");
      expect(t).not.toContain("-5.6%");
    }
  });

  it("says the intraday figure is unavailable rather than omitting the line", () => {
    // the close is NEVER substituted for a missing wick, and an absent line would read as "the
    // intraday extreme equals the close" — the one thing that is certainly not true
    const noWick = ep({ peak_return: 0.204, intraday_high_return: null, intraday_high_date: null });
    const t = excursionTitle(noWick, "peak", "wick");
    expect(t).toContain("intraday high unavailable");
    expect(t).not.toContain("+20.4%");
  });

  it("an EMPTY scored window says so on BOTH bases, instead of blaming a missing wick", () => {
    // the two real episodes whose arm and exit_by both land on the same Sunday: no bar exists in
    // [arm_date, exit_by] at all. "not every bar carries a wick" would be a vacuous truth pointing
    // at the wrong cause — there are no bars to carry anything. The wick columns ask the CLOSE
    // excursion first, precisely so they degrade to the right reason.
    const empty = ep({ peak_return: null, trough_return: null, path: [] });
    for (const side of ["peak", "worst"] as const) {
      for (const basis of ["close", "wick"] as const) {
        const t = excursionTitle(empty, side, basis);
        expect(t).toContain("no bars in the scored window");
        expect(t).not.toContain("unavailable");
      }
    }
  });
});

describe("pathTitle — the span, the axis caveat, and where the de-arm actually is", () => {
  const base = { arm_date: "2026-08-03", exit_date: "2026-08-21" } as Partial<ScoreboardEpisodeOut>;

  it("states the bar count, the span and the non-comparable axis", () => {
    const t = pathTitle(ep({ ...base, path: [1, 2, 3] }));
    expect(t).toContain("3 bars · Aug 3 → Aug 21");
    expect(t).toContain("not comparable between rows");
  });

  it("a de-arm ON the path is marked", () => {
    const t = pathTitle(ep({ ...base, path: [1, 2, 3], dearm_date: "2026-08-14", dearm_index: 1 }));
    expect(t).toContain("de-armed Aug 14 (marked)");
  });

  it("a de-arm PAST the scored window says so — never silence that reads as 'never de-armed'", () => {
    // the real 8-episode case: the horizon elapsed while the record kept the member armed
    const t = pathTitle(ep({ ...base, path: [1, 2, 3], dearm_date: "2026-09-02", dearm_index: null }));
    expect(t).toContain("after the scored window");
    expect(t).toContain("Sep 2");
  });

  it("an open episode says nothing about a de-arm, and a short path says why it can't draw", () => {
    expect(pathTitle(ep({ ...base, path: [1, 2, 3] }))).not.toContain("de-armed");
    expect(pathTitle(ep({ ...base, path: [1] }))).toContain("a point is not a path");
    expect(pathTitle(ep({ ...base, path: [] }))).toContain("no bars in the scored window");
  });
});

describe("returnLabel — a return is labeled for what it IS", () => {
  it("realized only once closed AND matured", () => {
    expect(returnLabel(ep({ status: "closed", matured: true }))).toBe("realized");
  });
  it("running while open or immature", () => {
    expect(returnLabel(ep({ status: "open", matured: false }))).toBe("running");
    expect(returnLabel(ep({ status: "closed", matured: false }))).toBe("running");
  });
  it("a day-1 arm with no bar yet says so", () => {
    expect(returnLabel(ep({ insufficient_prices: true }))).toBe("awaiting first bar");
  });
  it("a single-bar arm (only the arm-day bar) awaits a forward bar, not a flat 0.0%", () => {
    // exit_date === arm_date: the last bar ≤ asof IS the arm bar → one bar, no forward move yet
    expect(returnLabel(ep({ status: "open", exit_date: "2026-07-10" }))).toBe(
      "awaiting forward bar",
    );
  });
  it("once a forward bar lands (exit_date > arm_date) it is a real running return", () => {
    expect(returnLabel(ep({ status: "open", exit_date: "2026-07-13" }))).toBe("running");
  });
  it("a matured single-bar episode stays realized (the check runs AFTER realized)", () => {
    expect(
      returnLabel(ep({ status: "closed", matured: true, exit_date: "2026-07-10" })),
    ).toBe("realized");
  });
});

describe("awaitingForwardBar — the single-bar signal", () => {
  it("true only when exit_date equals arm_date", () => {
    expect(awaitingForwardBar(ep({ exit_date: "2026-07-10" }))).toBe(true); // == arm_date
    expect(awaitingForwardBar(ep({ exit_date: "2026-07-13" }))).toBe(false); // a forward bar landed
    expect(awaitingForwardBar(ep({ exit_date: null }))).toBe(false); // no bar at all
  });
});

describe("closeReasonBadge — the row's short form, deferring nothing", () => {
  const ep = (over: Record<string, unknown>) =>
    ({ status: "closed", close_reason: "dearmed_other", dearm_detail: null, ...over }) as never;

  it("is null while the episode is still open", () => {
    // the reason only describes how an episode LEFT the armed set; an open one hasn't
    expect(closeReasonBadge(ep({ status: "open", close_reason: "window_end" }))).toBeNull();
  });

  it("gives each token a short scannable label", () => {
    const label = (t: string) => closeReasonBadge(ep({ close_reason: t }))!.label;
    expect(label("dearmed_other")).toBe("DE-ARMED");
    expect(label("arm_until_lapsed")).toBe("WINDOW LAPSED");
    expect(label("conviction_aged_out")).toBe("AGED OUT");
    expect(label("managing")).toBe("MANAGING");
  });

  it("surfaces the composed detail the row used to defer — the whole point of the change", () => {
    // "(see de-arm day)" was written when the row had no answer. The backend composes one now, and
    // it never reached this cell: the row called closeReasonLabel, so the placeholder always won.
    const b = closeReasonBadge(
      ep({ dearm_detail: "thesis fell back to Warming" }),
    )!;
    expect(b.label).toBe("DE-ARMED");
    expect(b.title).toContain("de-armed — thesis fell back to Warming");
    expect(b.title).not.toContain("see de-arm day"); // the deferral is gone, not relabelled
  });

  it("keeps the raw wire token reachable, translated or not", () => {
    // the closeReasonLabel discipline: the English must never hide what the record actually says
    expect(closeReasonBadge(ep({ close_reason: "arm_until_lapsed" }))!.title).toContain(
      "wire: arm_until_lapsed",
    );
    // an unknown future token renders RAW rather than "unknown", and still says so on the wire (#9)
    const future = closeReasonBadge(ep({ close_reason: "some_future_reason" }))!;
    expect(future.label).toBe("some_future_reason");
    expect(future.title).toContain("wire: some_future_reason");
  });

  it("stays muted — it marks the rule, not an exception", () => {
    // 208 of 252 rows are closed; an alert-toned chip there would make two thirds of the ledger shout
    expect(closeReasonBadge(ep({}))!.cls).toBe("b-dearm");
  });
});

describe("triggerChips — a kind is named once and counted, never repeated", () => {
  const t = (kind: string, label: string) => ({ kind, label });

  it("collapses repeats of a kind into one counted chip", () => {
    // the real FLR arm: 6 fires, 2 kinds — it rendered 6 chips saying 2 things
    const chips = triggerChips([
      t("catalyst", "a"),
      t("catalyst", "b"),
      t("technical_breakout", "c"),
      t("technical_breakout", "d"),
      t("technical_breakout", "e"),
      t("technical_breakout", "f"),
    ]);
    expect(chips).toEqual([
      { kind: "catalyst", n: 2, labels: ["a", "b"] },
      { kind: "technical_breakout", n: 4, labels: ["c", "d", "e", "f"] },
    ]);
  });

  it("keeps the record's own order and never re-sorts by count", () => {
    // technical_breakout is the more frequent kind but insider fired FIRST — order is the record's
    const chips = triggerChips([
      t("insider", "i"),
      t("technical_breakout", "x"),
      t("technical_breakout", "y"),
      t("insider", "j"),
    ]);
    expect(chips.map((c) => c.kind)).toEqual(["insider", "technical_breakout"]);
    expect(chips.map((c) => c.n)).toEqual([2, 2]);
    // a kind seen again after another kind joins its FIRST chip — it does not open a second one
    expect(chips).toHaveLength(2);
    expect(chips[0].labels).toEqual(["i", "j"]);
  });

  it("leaves a single fire uncounted, and an empty list empty", () => {
    // n===1 is what the render gates the "xN" suffix on: one fire must not wear a count
    expect(triggerChips([t("laggard", "only")])).toEqual([
      { kind: "laggard", n: 1, labels: ["only"] },
    ]);
    expect(triggerChips([])).toEqual([]);
  });

  it("loses no label — every fire is still reachable through the chip it collapsed into", () => {
    const fires = [t("catalyst", "one"), t("catalyst", "two"), t("insider", "three")];
    const chips = triggerChips(fires);
    expect(chips.flatMap((c) => c.labels).sort()).toEqual(["one", "three", "two"]);
    expect(chips.reduce((n, c) => n + c.n, 0)).toBe(fires.length); // counts sum to the fires
  });
});

describe("episodeBadges — marks are exceptions, not constants", () => {
  it("open + censored episode carries both marks", () => {
    const labels = episodeBadges(ep({ status: "open", censored_start: true })).map((b) => b.label);
    expect(labels).toContain("OPEN");
    expect(labels).toContain("CENSORED");
    expect(labels).not.toContain("MATURED");
  });
  it("a closed matured un-censored episode carries only MATURED", () => {
    const labels = episodeBadges(
      ep({ status: "closed", matured: true, censored_start: false }),
    ).map((b) => b.label);
    expect(labels).toEqual(["MATURED"]);
  });
  it("INGEST rides iff flagged — the backend note becomes the title with the excluded suffix", () => {
    const badge = episodeBadges(
      ep({ ingest_flagged: true, ingest_note: "armed inside the 2026-07 EDGAR freeze window" }),
    ).find((b) => b.label === "INGEST");
    expect(badge?.cls).toBe("b-ing");
    expect(badge?.title).toBe(
      "armed inside the 2026-07 EDGAR freeze window — excluded from metrics",
    );
    expect(episodeBadges(ep()).map((b) => b.label)).not.toContain("INGEST");
  });
  it("INGEST falls back to the generic title when no note rides", () => {
    const badge = episodeBadges(ep({ ingest_flagged: true })).find((b) => b.label === "INGEST");
    expect(badge?.title).toBe(
      "the arm rested on partial or late-ingested data — excluded from metrics",
    );
  });
});

// A1: the de-arm tokens replay stamps (backend/replay/episodes.py::_close_reason) → the operator's English.
describe("closeReasonLabel — every de-arm token in English, unknown tokens raw", () => {
  it("translates each token replay can stamp", () => {
    expect(closeReasonLabel("arm_until_lapsed")).toBe("entry window lapsed");
    expect(closeReasonLabel("conviction_aged_out")).toBe("conviction aged out (past exit-by)");
    expect(closeReasonLabel("managing")).toBe("position taken — managing");
    expect(closeReasonLabel("window_end")).toBe("still armed at the record edge");
    expect(closeReasonLabel("dearmed_other")).toBe("de-armed (see de-arm day)");
  });
  it("an unknown token returns ITSELF — a new backend reason surfaces, never blanks out (#9)", () => {
    expect(closeReasonLabel("some_future_reason")).toBe("some_future_reason");
    expect(closeReasonLabel("")).toBe("");
  });
});

// Slice C: the composed dearm_detail turns the "(see de-arm day)" deferral into the actual answer.
describe("closeReasonLine — a composed detail answers dearmed_other; everything else is unchanged", () => {
  it("dearmed_other WITH a backend-composed detail reads the answer", () => {
    expect(
      closeReasonLine(
        "dearmed_other",
        "now missing: Volume-confirmed breakout (the confirmation key)",
      ),
    ).toBe("de-armed — now missing: Volume-confirmed breakout (the confirmation key)");
  });
  it("falls through to closeReasonLabel without a detail, and on every other token", () => {
    expect(closeReasonLine("dearmed_other", null)).toBe("de-armed (see de-arm day)");
    expect(closeReasonLine("dearmed_other", undefined)).toBe("de-armed (see de-arm day)");
    // the detail composes ONLY for the one opaque token — a self-explaining close never grows one
    expect(closeReasonLine("arm_until_lapsed", "spurious")).toBe("entry window lapsed");
    expect(closeReasonLine("some_future_reason", null)).toBe("some_future_reason");
  });
});

// A1: the drawer's one ingest line — the healthy arm renders NOTHING (loudness marks the exception, #7).
describe("ingestProvenanceLine — silence when healthy, the composed why when flagged", () => {
  it("is null on a clean arm, and on an UNKNOWN (null) freshness stamp — unknown is not a judgement", () => {
    expect(ingestProvenanceLine(ep())).toBeNull();
    expect(ingestProvenanceLine(ep({ arm_ingest_fresh: null }))).toBeNull();
    expect(ingestProvenanceLine(ep({ arm_ingest_fresh: true, thaw_lag_days: 2 }))).toBeNull();
  });

  it("fires on ANY of the three wire signals (the rollup, the freeze era, an explicitly stale run)", () => {
    expect(ingestProvenanceLine(ep({ ingest_flagged: true }))).not.toBeNull();
    expect(ingestProvenanceLine(ep({ freeze_era: true }))).not.toBeNull();
    expect(ingestProvenanceLine(ep({ arm_ingest_fresh: false }))).not.toBeNull();
  });

  it("uses the server's composed note verbatim and appends the measured thaw lag (#6)", () => {
    expect(
      ingestProvenanceLine(
        ep({
          ingest_flagged: true,
          ingest_note: "armed inside the 2026-07 EDGAR freeze window",
          freeze_era: true,
          thaw_lag_days: 11,
        }),
      ),
    ).toBe(
      "ingest provenance: armed inside the 2026-07 EDGAR freeze window · worst source lag 11d",
    );
  });

  it("falls back to the generic why when no note rides, and omits the lag when it is unknown", () => {
    expect(ingestProvenanceLine(ep({ ingest_flagged: true, thaw_lag_days: null }))).toBe(
      "ingest provenance: the arm rested on partial or late-ingested data",
    );
  });

  it("keeps a real 0-day lag (a measured 0 is information, unlike an unknown)", () => {
    expect(ingestProvenanceLine(ep({ freeze_era: true, thaw_lag_days: 0 }))).toContain(
      "worst source lag 0d",
    );
  });
});

describe("operatorLine", () => {
  it("no decision logged is the honest gap, not an error", () => {
    expect(operatorLine(ep()).kind).toBe("none");
    expect(operatorLine(ep()).text).toBe("no decision logged");
  });
  it("a took row carries the return and the inferred flag", () => {
    const line = operatorLine(
      ep({
        operator: {
          action: "took",
          decision_id: "d1",
          decision_date: "2026-07-11",
          reason: null,
          thesis_level: false,
          entry_price: 100,
          entry_inferred: false,
          exit_price: 108,
          exit_inferred: true,
          exit_date: null,
          running: true,
          operator_return: 0.08,
        },
      }),
    );
    expect(line.kind).toBe("took");
    expect(line.text).toContain("took 2026-07-11 @ 100");
    expect(line.text).toContain("running");
    expect(line.ret?.text).toBe("+8.0%");
    expect(line.inferred).toBe(true);
  });
  it("a pass carries no prices", () => {
    const line = operatorLine(
      ep({
        operator: {
          action: "passed",
          decision_id: "d2",
          decision_date: "2026-07-11",
          reason: "too extended",
          thesis_level: false,
          entry_price: null,
          entry_inferred: false,
          exit_price: null,
          exit_inferred: false,
          exit_date: null,
          running: false,
          operator_return: null,
        },
      }),
    );
    expect(line.kind).toBe("passed");
    expect(line.ret).toBeNull();
  });
});

describe("gateMetrics — the gate itself is the information", () => {
  it("all-insufficient collapses to ONE quiet line", () => {
    const { shown, gatedLine } = gateMetrics(
      [metric({ n: 3 }), metric({ name: "false_arm_rate", n: 2 })],
      5,
    );
    expect(shown).toEqual([]);
    expect(gatedLine).toBe("2 of 2 metrics await n ≥ 5 (largest today: n=3)");
  });
  it("a sufficient metric renders; the rest stay gated", () => {
    const ok = metric({ n: 7, insufficient_n: false, summary: { median: 0.031 } });
    const { shown, gatedLine } = gateMetrics([ok, metric({ name: "x", n: 1 })], 5);
    expect(shown).toEqual([ok]);
    expect(gatedLine).toContain("1 of 2");
  });
  it("nothing gated → no line at all (a constant marker is noise)", () => {
    const ok = metric({ n: 7, insufficient_n: false });
    expect(gateMetrics([ok], 5).gatedLine).toBeNull();
  });
});

describe("metricHeadline", () => {
  it("prefers the median and formats it as a return", () => {
    expect(metricHeadline(metric({ n: 7, summary: { median: 0.031 } }))).toBe(
      "median +3.1% · n=7",
    );
  });
  it("falls back to n when the summary has no known key", () => {
    expect(metricHeadline(metric({ n: 4, summary: {} }))).toBe("n=4");
  });
});

function summ(over: Partial<ScoreboardSummaryOut> = {}): ScoreboardSummaryOut {
  return {
    min_n: 5,
    next_maturity: null,
    n_maturing_30d: 0,
    projected_min_n_date: null,
    ...over,
  } as ScoreboardSummaryOut;
}

describe("maturityHorizon — the countdown behind the mute gate (2e)", () => {
  it("renders all three fields when a projection exists", () => {
    expect(
      maturityHorizon(
        summ({
          next_maturity: "2026-07-18",
          n_maturing_30d: 5,
          projected_min_n_date: "2026-08-31",
        }),
      ),
    ).toBe(
      "next episode matures 2026-07-18 · 5 mature within 30d · first metric could clear n ≥ 5 around 2026-08-31",
    );
  });
  it("says honestly when n ≥ min_n is not reachable from current episodes", () => {
    expect(
      maturityHorizon(summ({ next_maturity: "2026-07-31", n_maturing_30d: 1 })),
    ).toBe(
      "next episode matures 2026-07-31 · 1 mature within 30d · n ≥ 5 not reachable from current episodes",
    );
  });
  it("no future maturity → no line at all (null, not an empty shell)", () => {
    expect(maturityHorizon(summ())).toBeNull();
  });
});

function thesis(over: Partial<ScoreboardThesisOut> = {}): ScoreboardThesisOut {
  return {
    thesis_id: "t1",
    name: "HIMS",
    ticker: "HIMS",
    basket_size: 1,
    archived: false,
    first_call_asof: "2026-07-10",
    last_call_asof: "2026-07-11",
    current_state: "armed",
    current_verdict: "core_entry",
    warming_since: null,
    episodes: [],
    operator_spans: [],
    decision_anomaly: null,
    record_error: null,
    ...over,
  } as ScoreboardThesisOut;
}

describe("groupHint / groupToneClass / groupCount", () => {
  it("shows the record span, and the warming accrual whenever a run is open (proposal ⑩)", () => {
    expect(groupHint(thesis())).toBe("record 2026-07-10 → 2026-07-11");
    expect(
      groupHint(thesis({ warming_since: "2026-07-10", first_call_asof: "2026-07-10", last_call_asof: "2026-07-10" })),
    ).toBe("record 2026-07-10 · warming since 2026-07-10");
    // an accruing withheld window shows even when episodes already exist (⑩ — operator-approved)
    expect(groupHint(thesis({ warming_since: "2026-08-25", episodes: [ep()] }))).toBe(
      "record 2026-07-10 → 2026-07-11 · warming since 2026-08-25",
    );
    expect(groupHint(thesis({ first_call_asof: null }))).toBe("no call-of-record yet");
  });
  it("tone: open episode → armed; warming edge → warm; else quiet", () => {
    expect(groupToneClass(thesis({ episodes: [ep()] }))).toBe("sbg-armed");
    expect(groupToneClass(thesis({ current_state: "warming" }))).toBe("sbg-warm");
    expect(groupToneClass(thesis())).toBe("sbg-quiet");
  });
  it("count = episodes + off-record spans", () => {
    expect(groupCount(thesis({ episodes: [ep(), ep()] }))).toBe(2);
  });
});
