import { render, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CallCardResponse, MemberCallOut } from "../../api/hooks";

// Q14 (2026-10-18) — the two clock flags, labeled for what they are, on every surface that draws a
// member's clocks:
//   `lapsing`              = the HOLD clock (exit_by inside the freshness dial) → "hold clock closing"
//   `entry_window_closing` = the ENTRY window (≤ N trading sessions left through arm_until)
//                            → "entry window closing"
// The flags are the assembler's; these surfaces only READ them (no FE date rule), and the old
// "entry window closing" label must never again ride `lapsing`.

vi.mock("../../api/hooks", () => ({
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
}));

import { NamePanel } from "../../cockpit/NamePanel";
import { bucketDef } from "../../cockpit/buckets";
import { CallCard } from "../CallCard";
import { MemberMenu } from "../MemberMenu";

// The REAL record shape this mirrors: Modern Defense, asof Tue 2026-10-13, MTUS armed (core_entry)
// with arm_until Thu 2026-10-15 — two trading sessions left, so the assembler flags it; its exit_by
// (2027-01-31) is far, so `lapsing` is off. A second armed member carries the opposite pair.
function member(over: Partial<MemberCallOut>): MemberCallOut {
  return {
    security_id: "s-mtus",
    ticker: "MTUS",
    verdict: "core_entry",
    conviction_grade: "core",
    confirmation_grade: "core",
    entry_grade: "core",
    confidence: 0.7,
    exit_by: "2027-01-31",
    arm_until: "2026-10-15",
    lapsing: false,
    entry_window_closing: true,
    theme_armed: false,
    triggers: [],
    ...over,
  } as MemberCallOut;
}

const closing = member({});
const holdClosing = member({
  security_id: "s-alt",
  ticker: "ALT",
  exit_by: "2026-11-02",
  arm_until: "2026-10-22",
  lapsing: true,
  entry_window_closing: false,
});

function card(over: Partial<CallCardResponse> = {}): CallCardResponse {
  return {
    thesis_id: "t-def",
    asof: "2026-10-13",
    state: "armed",
    verdict: "core_entry",
    conviction_grade: "core",
    confirmation_grade: "core",
    entry_grade: "core",
    armed_security_id: "s-mtus",
    expression: "CORE setup",
    exit_by: "2027-01-31",
    arm_until: "2026-10-15",
    catalyst_surface: [],
    confidence: 0.7,
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [],
    risk_signals: [],
    missing: [],
    counter_case: "",
    safe_sleeve: null,
    armed_members: [closing, holdClosing],
    watch_members: [],
    ...over,
  } as unknown as CallCardResponse;
}

describe("CallCard clocks — the headline's entry-window flag", () => {
  it("labels the entry window 'entry window closing' when the headline member is flagged", () => {
    const { container } = render(<CallCard card={card()} />);
    const entry = container.querySelector(".clock-row.entry") as HTMLElement;
    expect(entry.className).toContain("closing");
    expect(entry.textContent).toContain("Entry window · confirmation clock");
    expect(entry.textContent).toContain("entry window closing");
    // the hold row never borrows the entry window's words
    expect(container.querySelector(".clock-row.hold")?.textContent).not.toContain("closing");
  });

  it("stays quiet when the headline member is not flagged", () => {
    const { container } = render(<CallCard card={card({ armed_members: [holdClosing, closing] })} />);
    const entry = container.querySelector(".clock-row.entry") as HTMLElement;
    expect(entry.className).not.toContain("closing");
    expect(entry.textContent).not.toContain("entry window closing");
  });

  it("never flags a not-yet card, whatever a member carries (inverse loudness, #7)", () => {
    const { container } = render(<CallCard card={card({ state: "warming", verdict: "not_yet" })} />);
    const entry = container.querySelector(".clock-row.entry") as HTMLElement;
    expect(entry.className).toContain("quiet");
    expect(entry.className).not.toContain("closing");
    expect(entry.textContent).not.toContain("entry window closing");
  });
});

describe("MemberMenu — both flags, each with its own words", () => {
  it("chips the entry window on the flagged row and says 'hold clock closing' for lapsing", () => {
    const { container } = render(<MemberMenu card={card()} />);
    const rows = [...container.querySelectorAll(".mm-row.armed")] as HTMLElement[];
    expect(rows).toHaveLength(2);
    const [mtus, alt] = rows;
    expect(within(mtus).getByText("entry window closing").className).toBe("mm-ewc");
    expect(mtus.querySelector(".mm-runway")?.textContent).toBe("runway to Jan 31");
    expect(alt.querySelector(".mm-ewc")).toBeNull();
    expect(alt.className).toContain("lapsing"); // the CSS hook keeps the wire name
    expect(alt.querySelector(".mm-runway")?.textContent).toBe("hold clock closing · Nov 2");
    expect(container.textContent).not.toMatch(/lapses/);
  });
});

describe("NamePanel clocks — the per-name panel", () => {
  function renderPanel(call: MemberCallOut) {
    return render(
      <NamePanel
        row={{
          member: { ticker: call.ticker, role: "core", security_id: call.security_id } as never,
          ordinal: 0,
          call,
          scored: null,
          bucket: call.lapsing ? "lapsing" : "armed",
        }}
        def={bucketDef(call.lapsing ? "lapsing" : "armed")}
        card={card()}
        thesisId="t-def"
        position={null}
        display={null}
        asof="2026-10-13"
        onClose={() => {}}
      />,
    );
  }

  it("flags the entry-window row on the flagged member — and not the hold row", () => {
    const { container } = renderPanel(closing);
    const entry = container.querySelector(".clock-row.entry") as HTMLElement;
    expect(entry.className).toContain("closing");
    expect(entry.textContent).toContain("act within 2d");
    expect(entry.textContent).toContain("entry window closing");
    const hold = container.querySelector(".clock-row.hold") as HTMLElement;
    expect(hold.className).not.toContain("lapse");
    expect(hold.textContent).not.toContain("closing");
  });

  it("reads the hold row 'hold clock closing' on a lapsing member — never 'entry window closing'", () => {
    const { container } = renderPanel(holdClosing);
    const hold = container.querySelector(".clock-row.hold") as HTMLElement;
    expect(hold.className).toContain("lapse");
    expect(hold.textContent).toContain("hold clock closing · 20d");
    expect(hold.textContent).not.toMatch(/lapses/);
    const entry = container.querySelector(".clock-row.entry") as HTMLElement;
    expect(entry.className).not.toContain("closing");
    expect(entry.textContent).not.toContain("entry window closing");
  });

  it("labels the hold-clock bucket for what it is", () => {
    const def = bucketDef("lapsing");
    expect(def.label).toBe("Hold clock closing");
    expect(def.hint).toBe("signal-validity horizon near");
    expect(`${def.label} ${def.hint}`).not.toMatch(/entry window/i);
  });
});
