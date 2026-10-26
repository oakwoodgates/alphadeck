import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CallCardResponse } from "../../api/hooks";
import { SETUP_STRENGTH_TOOLTIP, todayISO } from "../../util/format";
import { byWhenDateAsc, CallCard, counterCaseSummary } from "../CallCard";

// A minimal armed card: one graded trigger (shows its grade + source link, hit/◉) and one risk signal that
// CARRIES a grade which must NOT render (the TriggerRow showGrade=false path, warn/▲). conviction_grade is
// null so the only "CORE"/"FLIP" text in the DOM comes from the rows themselves.
const card = {
  thesis_id: "t1",
  asof: "2026-06-20",
  state: "armed",
  verdict: "core_entry",
  conviction_grade: null,
  confirmation_grade: null,
  entry_grade: null,
  armed_security_id: "s-smr",
  expression: "Buy the leader",
  exit_by: null,
  arm_until: null,
  catalyst_surface: [],
  confidence: null,
  key_conviction: { turned: true, detail: "conviction" },
  key_confirmation: { turned: false, detail: "confirmation" },
  triggers_fired: [
    {
      label: "Insider buy",
      kind: "insider",
      grade: "core",
      ticker: "SMR",
      sources: [{ source: "form4", ref: "0001-23-456789", url: "https://example.com/f", detail: {} }],
    },
  ],
  risk_signals: [
    { label: "Dilution risk", kind: "dilution_risk", grade: "flip", ticker: "SMR", sources: [] },
  ],
  missing: [],
  counter_case: "",
  safe_sleeve: null,
  armed_members: [],
  watch_members: [],
} as unknown as CallCardResponse;

describe("CallCard — TriggerRow (Tier-3 extraction)", () => {
  it("renders triggers with grade + source link (hit/◉) and risk signals without grade (warn/▲)", () => {
    const { container } = render(<CallCard card={card} />);

    // trigger row: label (regex — the label text node shares its span with the grade/link siblings), its
    // grade, and the resolved source link
    expect(screen.getByText(/Insider buy/)).toBeInTheDocument();
    expect(screen.getByText("CORE")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /source/i })).toHaveAttribute(
      "href",
      "https://example.com/f",
    );

    // risk-signal row: label renders, but its grade is suppressed (showGrade=false)
    expect(screen.getByText(/Dilution risk/)).toBeInTheDocument();
    expect(screen.queryByText("FLIP")).toBeNull();

    // variant + icon wiring (the props that differ between the two rows)
    expect(container.querySelector(".trg-item.hit .ic")?.textContent).toBe("◉");
    expect(container.querySelector(".trg-item.warn .ic")?.textContent).toBe("▲");
  });
});

describe("CallCard — trigger event dates", () => {
  // two triggers sent OLDER-first by the backend; the card must render them newest-first, each with its
  // own muted right-aligned date (fmtDate short style, e.g. "Jun 18").
  const dated = {
    ...card,
    conviction_grade: null,
    triggers_fired: [
      { label: "Older insider buy", kind: "insider", grade: "core", ticker: "AAA", event_date: "2026-06-05", sources: [] },
      { label: "Newer breakout", kind: "technical_breakout", grade: "flip", ticker: "AAA", event_date: "2026-06-18", sources: [] },
    ],
    risk_signals: [],
  } as unknown as CallCardResponse;

  it("renders each trigger's fire date muted and orders rows newest-first", () => {
    const { container } = render(<CallCard card={dated} />);
    // dates render in the muted .trg-date slot, newest-first
    const dates = [...container.querySelectorAll(".trg-date")].map((n) => n.textContent);
    expect(dates).toEqual(["Jun 18", "Jun 5"]);
    // the row order matches: the newer breakout leads the older insider (backend sent them reversed)
    const bodies = [...container.querySelectorAll(".trg-item.hit .trg-body")].map((n) => n.textContent);
    expect(bodies[0]).toMatch(/Newer breakout/);
    expect(bodies[1]).toMatch(/Older insider/);
  });

  it("omits the date entirely when a trigger has no event_date (nullable, graceful)", () => {
    const noDate = {
      ...card,
      triggers_fired: [{ label: "No date", kind: "insider", grade: "core", ticker: "AAA", sources: [] }],
      risk_signals: [],
    } as unknown as CallCardResponse;
    const { container } = render(<CallCard card={noDate} />);
    expect(container.querySelector(".trg-date")).toBeNull();
  });
});

describe("CallCard — foreign-filer conviction annotation (single-name)", () => {
  // a not-yet card missing BOTH keys. The conviction "missing" line is the assembler's literal — the one
  // that bundles insider AND structural catalyst; the annotation must scope to the INSIDER half only.
  const notYet = {
    ...card,
    state: "warming",
    verdict: "not_yet",
    triggers_fired: [],
    risk_signals: [],
    key_conviction: { turned: false, detail: "conviction" },
    missing: [
      "Conviction trigger (e.g. insider cluster / structural catalyst)",
      "Volume-confirmed breakout (the confirmation key)",
    ],
  } as unknown as CallCardResponse;

  it("scopes a sub-note UNDER the conviction line (only) when foreignFiler is set", () => {
    const { container } = render(<CallCard card={notYet} foreignFiler={{ form: "40-F" }} />);
    const note = container.querySelector(".miss-note");
    expect(note).not.toBeNull();
    // scoped to the INSIDER ingredient; carries the form; must NOT claim the key is dead — a catalyst still arms
    expect(note?.textContent).toMatch(/insider-cluster conviction is structurally unavailable/);
    expect(note?.textContent).toMatch(/40-F, no Form 4/);
    expect(note?.textContent).toMatch(/structural or theme catalyst can still arm/);
    // the note sits under the CONVICTION line, never the confirmation line
    const items = [...container.querySelectorAll(".trg-item.miss")];
    const conv = items.find((n) => n.textContent?.includes("Conviction trigger"));
    const conf = items.find((n) => n.textContent?.includes("Volume-confirmed"));
    expect(conv?.querySelector(".miss-note")).not.toBeNull();
    expect(conf?.querySelector(".miss-note")).toBeNull();
  });

  it("renders NO sub-note when the prop is absent (preview / byte-parity)", () => {
    const { container } = render(<CallCard card={notYet} />);
    expect(container.querySelector(".miss-note")).toBeNull();
    // the missing lines themselves are unchanged
    expect(screen.getByText(/Conviction trigger/)).toBeInTheDocument();
  });
});

// --- setup strength -------------------------------------------------------------------------------
// The wire field `confidence` is SETUP STRENGTH in the product: an experimental relative indicator, never a
// probability. The Armed rail's bar says so — label and hover — and never reads "Confidence".
describe("CallCard — the setup-strength bar", () => {
  it("is labeled 'Setup strength' with the not-a-probability hover", () => {
    const { container } = render(<CallCard card={{ ...card, confidence: 0.72 }} />);
    const row = container.querySelector(".conf-row") as HTMLElement;
    expect(row.textContent).toBe("Setup strength72%");
    expect(row.querySelector("span")?.getAttribute("title")).toBe(SETUP_STRENGTH_TOOLTIP);
    expect(container.textContent).not.toMatch(/confidence/i);
  });

  it("draws no bar at all when the card carries no setup strength (a not-yet card)", () => {
    const { container } = render(<CallCard card={{ ...card, confidence: null }} />);
    expect(container.querySelector(".conf")).toBeNull();
  });
});

// --- the catalyst surface ------------------------------------------------------------------------
// The assembler ships the dated events inside the exit-by window (`catalyst_surface`); the rail renders
// them soonest-first beside the clocks they sit between. Ambient — an empty surface draws no panel at all,
// and a date already past grays (it stays visible) instead of vanishing.
describe("CallCard — the catalyst surface", () => {
  const withSurface = (surface: unknown[]) =>
    ({ ...card, catalyst_surface: surface }) as unknown as CallCardResponse;
  const surface = [
    { id: "c2", label: "FDA decision", kind: "regulatory", when_date: "2026-07-10", when_label: null },
    { id: "c3", label: "Investor day", kind: null, when_date: null, when_label: "~Q3" },
    { id: "c0", label: "Competitor readout", kind: "clinical_readout", when_date: "2026-06-10", when_label: null },
    { id: "c1", label: "Earnings", kind: null, when_date: "2026-06-25", when_label: null },
  ];

  it("lists the events soonest-first with their day count, a past date marked, an undated one last", () => {
    const { container } = render(<CallCard card={withSurface(surface)} />);
    const rows = [...container.querySelectorAll(".trg-item.cal")] as HTMLElement[];
    expect(rows.map((r) => r.querySelector(".trg-body")?.textContent)).toEqual([
      "Competitor readoutclinical_readout",
      "Earnings",
      "FDA decisionregulatory",
      "Investor day",
    ]);
    expect(rows.map((r) => r.querySelector(".trg-date")?.textContent)).toEqual([
      "Jun 10 · passed",
      "Jun 25 · 5d",
      "Jul 10 · 20d",
      "~Q3",
    ]);
    // the past one grays, never vanishes
    expect(rows[0].className).toContain("passed");
    expect(rows[1].className).not.toContain("passed");
    // the header carries the count
    const head = screen.getByText("Catalysts through exit-by").closest(".rpanel-h") as HTMLElement;
    expect(head.querySelector(".ct")?.textContent).toBe("4");
  });

  it("draws NO panel when the surface is empty", () => {
    const { container } = render(<CallCard card={withSurface([])} />);
    expect(screen.queryByText("Catalysts through exit-by")).toBeNull();
    expect(container.querySelector(".trg-item.cal")).toBeNull();
  });

  it("sorts by date alone: equal dates keep the authoring order, an undated entry sinks", () => {
    const e = (id: string, when_date: string | null) => ({ id, label: id, when_date }) as never;
    const sorted = [e("u", null), e("b", "2026-07-01"), e("a", "2026-07-01"), e("z", "2026-06-01")].sort(
      byWhenDateAsc,
    );
    expect(sorted.map((c) => c.id)).toEqual(["z", "b", "a", "u"]);
  });
});

// --- F11: the past-as-of "recomputed, not the recorded call" line -----------------------------
// Derived purely from card.asof (no new prop): a scrubbed-past card says so, quietly; the live
// (today) card carries no caveat at all — honest loudness (#7 / WB#3), the note marks the
// exception, not the rule.
describe("CallCard — F11 past-asof recompute line", () => {
  it("renders the quiet line when card.asof is a PAST date", () => {
    const { container } = render(<CallCard card={{ ...card, asof: "2026-06-01" }} />);
    expect(
      screen.getByText("Recomputed as of Jun 1 — today's rules on what was public then, not the recorded call"),
    ).toBeInTheDocument();
    // the tooltip says what a past view IS: today's rules over what was PUBLIC by that date (the
    // public clock), never a claim that the roster is period-accurate — and not the record
    expect(container.querySelector(".cc-recompute")?.getAttribute("title")).toMatch(
      /Re-derived with today's rules from what was public by the end of this date/,
    );
  });

  it("is ABSENT when card.asof is TODAY (the live view)", () => {
    const { container } = render(<CallCard card={{ ...card, asof: todayISO() }} />);
    expect(screen.queryByText(/not the recorded call/)).toBeNull();
    expect(container.querySelector(".cc-recompute")).toBeNull();
  });
});

// --- the counter-case summary ------------------------------------------------------------------
// The block used to print the wire's `counter_case` string, which the assembler builds by joining
// the SAME `active_risk` list that becomes `card.risk_signals` (plus kill criteria and `missing`).
// Measured on the live theses it carried nothing the card was not already rendering — and it
// dropped what the rows keep (ticker, kind, date, sources) — at ~7,900px of rail. It is a COUNT of
// those sections now. The load-bearing test is the last one: the prose must never come back.
describe("counterCaseSummary — counts the sections, never restates them", () => {
  const risk = (ticker: string | null) =>
    ({ label: "x", kind: "breakdown", grade: null, ticker, sources: [] }) as never;

  it("counts signals and DISTINCT names, and the still-missing list", () => {
    expect(counterCaseSummary([risk("A"), risk("B"), risk("A")], ["m1", "m2"])).toBe(
      "3 active risk signals across 2 names · 2 still missing",
    );
  });

  it("says it in the singular when there is one of each", () => {
    expect(counterCaseSummary([risk("A")], ["m1"])).toBe(
      "1 active risk signal across 1 name · 1 still missing",
    );
  });

  it("drops the clause that has nothing in it, rather than printing a zero", () => {
    expect(counterCaseSummary([risk("A"), risk("B")], [])).toBe(
      "2 active risk signals across 2 names",
    );
    expect(counterCaseSummary([], ["m1"])).toBe("1 still missing");
  });

  it("omits the name clause when no signal carries a ticker (a thesis-level risk)", () => {
    expect(counterCaseSummary([risk(null), risk(null)], [])).toBe("2 active risk signals");
  });

  it("is honest when there is no counter-case at all", () => {
    expect(counterCaseSummary([], [])).toBe("No active risk signals, nothing still missing.");
  });

  it("renders the count and NOT the wire's prose, however long that prose is", () => {
    const prose =
      "Active risk signals: Dilution risk; Structural break: close 32.47 closed below the " +
      "200-day base 34.93 — the core hold's entry signal is no longer valid (a de-arm, not a sell).";
    render(<CallCard card={{ ...card, counter_case: prose, missing: ["Volume confirmation"] }} />);
    expect(screen.getByText("1 active risk signal across 1 name · 1 still missing")).toBeInTheDocument();
    expect(screen.queryByText(/Active risk signals:/)).toBeNull();
    expect(screen.queryByText(/200-day base/)).toBeNull();
  });
});
