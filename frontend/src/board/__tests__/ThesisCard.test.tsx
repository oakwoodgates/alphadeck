import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CallCardResponse, ThesisSummary } from "../../api/hooks";
import { ThesisCard } from "../ThesisCard";

// The readiness pips on a not-yet card are the two keys, one pip each — conviction left, confirmation right.
// They used to be a bare count (pip i lit when i < N), so a card whose ONLY turned key was confirmation (the
// Watch tier: moving, no conviction) lit the FIRST pip and read as "conviction fired". A lit pip must say
// WHICH key turned.

const thesis = {
  id: "t-a",
  name: "Alpha thesis",
  ticker: null,
  basket_size: 4,
  narrative: "n",
  archived: false,
} as unknown as ThesisSummary;

const call = (state: string, conviction: boolean, confirmation: boolean) =>
  ({
    thesis_id: "t-a",
    state,
    verdict: "not_yet",
    conviction_grade: null,
    entry_grade: null,
    key_conviction: { turned: conviction, detail: "" },
    key_confirmation: { turned: confirmation, detail: "" },
    armed_members: [],
    watch_members: [],
  }) as unknown as CallCardResponse;

const pips = (container: HTMLElement) =>
  [...container.querySelectorAll(".pip")].map((p) => p.classList.contains("on"));

const renderCard = (c: CallCardResponse) =>
  render(<ThesisCard thesis={thesis} call={c} onSelect={() => {}} />);

describe("ThesisCard — the readiness pips are the two KEYS", () => {
  it("lights the first pip only for conviction alone", () => {
    const { container } = renderCard(call("warming", true, false));
    expect(pips(container)).toEqual([true, false]);
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "Conviction ✓ · Confirmation ○");
  });

  it("lights the SECOND pip for confirmation alone — never the first (the misread this fixes)", () => {
    const { container } = renderCard(call("incubating", false, true));
    expect(pips(container)).toEqual([false, true]);
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "Conviction ○ · Confirmation ✓");
  });

  it("lights both when both turned, neither when neither — the count text is unchanged", () => {
    const both = renderCard(call("warming", true, true));
    expect(pips(both.container)).toEqual([true, true]);
    expect(screen.getByText("2/2")).toBeInTheDocument();
    both.unmount();

    const none = renderCard(call("incubating", false, false));
    expect(pips(none.container)).toEqual([false, false]);
    expect(screen.getByText("0/2")).toBeInTheDocument();
  });

  it("carries the same words on the hover as on the accessible label", () => {
    const { container } = renderCard(call("warming", true, false));
    expect(container.querySelector(".readiness")?.getAttribute("title")).toBe(
      "Conviction ✓ · Confirmation ○",
    );
  });

  it("draws no pips on an Armed or Managing card (they lead with the verdict, not readiness)", () => {
    const armed = renderCard({ ...call("armed", true, true), verdict: "starter_entry" } as never);
    expect(armed.container.querySelector(".readiness")).toBeNull();
    armed.unmount();
    const managing = renderCard({ ...call("managing", true, true), verdict: "managing" } as never);
    expect(managing.container.querySelector(".readiness")).toBeNull();
  });
});
