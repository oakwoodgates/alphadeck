import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CallCardResponse } from "../../api/hooks";
import { CallCard } from "../CallCard";

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
