import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OnboardingReadoutOut, PromoteOnboardingRef } from "../../api/hooks";
import { OnboardingNote } from "../OnboardingNote";

// X1 — the staged-onboarding note: a Save whose cold names were QUEUED (the promote response's `onboarding`
// ref — backend/tests/app/test_onboarding_api.py) polls the thesis's slice of the queue. QUIET while names
// wait or load and once all are loaded; LOUD only for a FAILED name or a lost poll.

const REF: PromoteOnboardingRef = {
  queued: 240,
  new_members: 243,
  reason:
    "240 new names is more than the 5 the fast lane loads at once — their history loads in batches outside the nightly window",
};

const readout = (over: Partial<OnboardingReadoutOut>): OnboardingReadoutOut => ({
  counts: { queued: 200, in_progress: 3, done: 37, failed: 0, parked: 0 },
  pending: [],
  recent_done: [],
  last_run: null,
  window: "21:30-23:30 EDT",
  stalled: null,
  paused: null,
  history_legs: ["form4", "form8k", "schedule13"],
  ...over,
});

const h = vi.hoisted(() => ({ q: {} as Record<string, unknown>, calls: [] as unknown[][] }));

vi.mock("../../api/hooks", () => ({
  useThesisOnboarding: (...args: unknown[]) => {
    h.calls.push(args);
    return h.q;
  },
}));

describe("OnboardingNote (X1)", () => {
  beforeEach(() => {
    h.calls = [];
    h.q = { data: readout({}), isError: false, error: null };
  });

  it("is QUIET while the queued names load, and says what the nightly does meanwhile", () => {
    render(<OnboardingNote thesisId="t-1" onboarding={REF} />);
    const note = screen.getByText(/Queued 240 new names to load their filing history/);
    expect(note).toHaveClass("muted");
    expect(note).toHaveTextContent("more than the 5 the fast lane loads at once");
    expect(note).toHaveTextContent("37 loaded, 203 waiting");
    expect(note).toHaveTextContent("the nightly reads only its recent filings");
    expect(h.calls[0]).toEqual(["t-1", true]);
  });

  it("resolves to a quiet done line once nothing is waiting", () => {
    h.q = { data: readout({ counts: { queued: 0, in_progress: 0, done: 240, failed: 0, parked: 0 } }) };
    render(<OnboardingNote thesisId="t-1" onboarding={REF} />);
    expect(screen.getByText("✓ History loaded for 240 new names.")).toHaveClass("muted");
  });

  it("is LOUD for a failed name, naming it and where to Retry", () => {
    h.q = {
      data: readout({
        counts: { queued: 10, in_progress: 0, done: 229, failed: 1, parked: 0 },
        pending: [
          {
            security_id: "00000000-0000-0000-0000-0000000000f1",
            ticker: "FAIL",
            name: null,
            status: "failed",
            legs_done: ["form4"],
            attempts: 3,
            last_error: "form8k: EDGAR said 503",
            source: "promote",
            in_live_basket: true,
            enqueued_at: "2026-10-21T14:00:00Z",
            last_attempt_at: "2026-10-21T16:00:00Z",
            finished_at: "2026-10-21T16:01:00Z",
          },
        ],
      }),
      isError: false,
      error: null,
    };
    render(<OnboardingNote thesisId="t-1" onboarding={REF} />);
    expect(screen.getByText(/History load FAILED for 1 name \(FAIL\)/)).toHaveTextContent("Retry on the Admin page");
  });

  it("says out loud when the poll is lost", () => {
    h.q = { data: undefined, isError: true, error: { detail: "thesis not found" } };
    render(<OnboardingNote thesisId="t-1" onboarding={REF} />);
    expect(screen.getByText(/Lost track of the history load for 240 new names/)).toHaveTextContent(
      "thesis not found",
    );
  });
});
