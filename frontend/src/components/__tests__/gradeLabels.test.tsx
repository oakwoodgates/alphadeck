import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CallCardResponse, MemberCallOut, ThesisSummary } from "../../api/hooks";
import { ThesisCard } from "../../board/ThesisCard";
import { CallCard } from "../CallCard";
import { MemberMenu } from "../MemberMenu";

// The conviction grade (core / flip) rates the STRENGTH CLASS of the call's conviction signal — never the
// idea itself (invariant #4: opinionated on timing, deferential on thesis). Three surfaces printed it as
// "CORE THESIS" / "core thesis", which reads as a verdict on the thesis; BOARD.md has always said "core
// setup". Every surface says SETUP now, and this pins all three so the word cannot drift back.

const member = (over: Partial<MemberCallOut>): MemberCallOut =>
  ({
    security_id: "s-a", ticker: "AAA", verdict: "starter_entry", conviction_grade: "core",
    confirmation_grade: "flip", entry_grade: "flip", confidence: 0.5, exit_by: "2026-11-30",
    arm_until: "2026-10-20", lapsing: false, entry_window_closing: false, theme_armed: false,
    triggers: [], ...over,
  }) as MemberCallOut;

const card = (over: Partial<CallCardResponse> = {}): CallCardResponse =>
  ({
    thesis_id: "t", asof: "2026-10-13", state: "armed", verdict: "starter_entry",
    conviction_grade: "core", confirmation_grade: "flip", entry_grade: "flip", armed_security_id: "s-a",
    expression: "STARTER", exit_by: "2026-11-30", arm_until: "2026-10-20", catalyst_surface: [],
    confidence: 0.5, key_conviction: { turned: true, detail: "" }, key_confirmation: { turned: true, detail: "" },
    triggers_fired: [], risk_signals: [], missing: [], counter_case: "", safe_sleeve: null,
    armed_members: [member({}), member({ security_id: "s-b", ticker: "BBB", conviction_grade: "flip" })],
    watch_members: [], ...over,
  }) as unknown as CallCardResponse;

const thesis = {
  id: "t", name: "Alpha thesis", ticker: null, basket_size: 4, narrative: "n", archived: false,
} as unknown as ThesisSummary;

describe("the conviction grade reads as a SETUP, never a thesis", () => {
  it("the CallCard rail head: CORE SETUP", () => {
    const { container } = render(<CallCard card={card()} />);
    const chip = container.querySelector(".cc-head .grade") as HTMLElement;
    expect(chip.textContent).toBe("CORE SETUP");
    expect(container.querySelector(".cc-head")?.textContent).not.toMatch(/thesis/i);
  });

  it("the Board's armed card: 'core setup' beside the entry verdict", () => {
    const { container } = render(<ThesisCard thesis={thesis} call={card()} onSelect={() => {}} />);
    expect(container.querySelector(".foot .conv")?.textContent).toBe("core setup");
  });

  it("the ranked member menu: each armed row's grade reads 'setup'", () => {
    const { container } = render(<MemberMenu card={card()} />);
    expect([...container.querySelectorAll(".mm-conv")].map((n) => n.textContent)).toEqual([
      "core setup",
      "flip setup",
    ]);
  });
});
