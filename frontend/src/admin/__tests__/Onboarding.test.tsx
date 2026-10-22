import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OnboardingNameOut, OnboardingReadoutOut } from "../../api/hooks";
import { Onboarding } from "../Onboarding";

// The Admin "Onboarding" panel (X1) over mocked hooks, typed against the generated wire shape
// (OnboardingReadoutOut — the same object GET /admin/onboarding returns in
// backend/tests/app/test_onboarding_api.py). The gates: nothing at all for a queue that never held a name;
// QUIET (a summary line + collapsed lists) while names wait or load; LOUD only for a FAILED name (with its
// Retry) or a STALLED queue; a capped day reads as quiet "paused", never stalled; nothing fires on mount.

const name = (over: Partial<OnboardingNameOut>): OnboardingNameOut => ({
  security_id: "00000000-0000-0000-0000-00000000000a",
  ticker: "AAA",
  name: null,
  status: "queued",
  legs_done: [],
  attempts: 0,
  last_error: null,
  source: "promote",
  in_live_basket: true,
  enqueued_at: "2026-10-21T14:00:00Z",
  last_attempt_at: null,
  finished_at: null,
  ...over,
});

const FAILED = name({
  security_id: "00000000-0000-0000-0000-0000000000f1",
  ticker: "FAIL",
  status: "failed",
  attempts: 3,
  legs_done: ["form4"],
  last_error: "form8k: EDGAR said 503",
});
const LOADING = name({
  security_id: "00000000-0000-0000-0000-0000000000b2",
  ticker: "LOAD",
  status: "in_progress",
  legs_done: ["form4", "form8k"],
});
const PARKED = name({ security_id: "00000000-0000-0000-0000-0000000000c3", ticker: "PARK", in_live_basket: false });
const DONE = name({
  security_id: "00000000-0000-0000-0000-0000000000d4",
  ticker: "DONE",
  status: "done",
  legs_done: ["form4", "form8k", "schedule13", "price", "fund_shares", "fundamentals"],
  finished_at: "2026-10-21T15:00:00Z",
});

const readout = (over: Partial<OnboardingReadoutOut>): OnboardingReadoutOut => ({
  counts: { queued: 1, in_progress: 1, done: 1, failed: 0, parked: 1 },
  pending: [LOADING, PARKED],
  recent_done: [DONE],
  last_run: {
    started_at: "2026-10-21T14:30:00Z",
    finished_at: "2026-10-21T14:55:00Z",
    status: "finished",
    run_kind: "cron",
    stopped: "budget_time",
    detail: "budget_time: the run's 1500s budget is spent",
    counts: { done: 12, busy: 1 },
  },
  window: "21:30-23:30 EDT",
  stalled: null,
  paused: null,
  history_legs: ["form4", "form8k", "schedule13"],
  ...over,
});

const h = vi.hoisted(() => ({
  q: {} as Record<string, unknown>,
  retry: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useOnboarding: () => h.q,
  useRetryOnboarding: () => h.retry,
}));

const serve = (data: OnboardingReadoutOut) => {
  h.q = { data, isError: false, error: null };
};

describe("Onboarding (X1)", () => {
  beforeEach(() => {
    h.retry = { mutate: vi.fn(), isPending: false, isError: false, error: null };
    serve(readout({}));
  });

  it("renders NOTHING for a queue that has never held a name", () => {
    serve(
      readout({
        counts: { queued: 0, in_progress: 0, done: 0, failed: 0, parked: 0 },
        pending: [],
        recent_done: [],
        last_run: null,
      }),
    );
    const { container } = render(<Onboarding />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is QUIET while names wait or load: a summary, the last run, collapsed lists — nothing loud", () => {
    render(<Onboarding />);
    const panel = screen.getByTestId("adm-onboarding");
    expect(screen.getByTestId("adm-onboarding-summary")).toHaveTextContent(
      "1 queued · 1 loading · 1 loaded · 0 failed · 1 parked (in no live basket)",
    );
    expect(screen.getByTestId("adm-onboarding-summary")).toHaveTextContent("21:30-23:30 EDT");
    expect(screen.getByTestId("adm-onboarding-run")).toHaveTextContent("12 done, 1 busy");
    expect(screen.getByTestId("adm-onboarding-run")).toHaveTextContent("stopped: the run's time budget");
    expect(panel.querySelector(".adm-loud")).toBeNull();
    const rows = screen.getAllByTestId("adm-onboarding-waiting-row");
    expect(rows[0]).toHaveTextContent("LOAD — loading · 2 of 3 history legs loaded (Form 4, 8-K)");
    expect(rows[1]).toHaveClass("adm-grayed");
    expect(rows[1]).toHaveTextContent("parked: no live basket holds it");
    expect(fn(h.retry)).not.toHaveBeenCalled();
  });

  it("is LOUD for a FAILED name, with its Retry beside it", async () => {
    serve(
      readout({
        counts: { queued: 0, in_progress: 1, done: 1, failed: 1, parked: 0 },
        pending: [FAILED, LOADING],
      }),
    );
    render(<Onboarding />);
    const failed = screen.getByTestId("adm-onboarding-failed");
    expect(failed).toHaveTextContent("1 name(s) FAILED to load their history");
    const row = within(failed).getByTestId("adm-onboarding-failed-row");
    expect(row).toHaveTextContent("FAIL");
    expect(row).toHaveTextContent("form8k: EDGAR said 503");
    expect(row).toHaveTextContent("3 attempt(s)");
    await userEvent.click(within(row).getByRole("button", { name: "Retry" }));
    expect(fn(h.retry)).toHaveBeenCalledWith(FAILED.security_id);
  });

  it("is LOUD when the queue STALLED, and a capped day reads as quiet PAUSED instead", () => {
    serve(readout({ stalled: "2 name(s) waiting and no onboarding run has finished ever — check the cron" }));
    const { unmount } = render(<Onboarding />);
    expect(screen.getByTestId("adm-onboarding-stalled")).toHaveTextContent("STALLED — 2 name(s) waiting");
    unmount();

    serve(readout({ paused: "today's cap of 200 names is reached — loading resumes after midnight" }));
    render(<Onboarding />);
    expect(screen.queryByTestId("adm-onboarding-stalled")).toBeNull();
    expect(screen.getByTestId("adm-onboarding-paused")).toHaveTextContent("today's cap of 200 names");
  });

  it("shows a failed read as an error line, never a blank panel", () => {
    h.q = { data: undefined, isError: true, error: { detail: "relation security_onboarding does not exist" } };
    render(<Onboarding />);
    expect(screen.getByTestId("adm-onboarding")).toHaveTextContent("relation security_onboarding does not exist");
  });
});

const fn = (m: Record<string, unknown>) => m.mutate as ReturnType<typeof vi.fn>;
