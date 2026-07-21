import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Admin } from "../Admin";

// The Admin page over mocked hooks. The two invariant gates live here: the run trigger NEVER fires on
// mount (operator-initiated only — the mutate spy stays uncalled until the click), and loudness marks
// the exception (stale/unhealthy loud; current / never-begun / never-ran quiet).

const OK_RUN = {
  ran_at: "2026-07-17T22:30:01+00:00",
  finished_at: "2026-07-17T23:35:12+00:00",
  duration_s: 3911.0,
  asof: "2026-07-17",
  mode: "live",
  theses: 2,
  appended: 1,
  unchanged: 1,
  withheld: 0,
  errored: 0,
  transitions: 0,
  edgar_fetches: 88,
  healthy: true,
  problems: [] as string[],
};

const FROZEN_RUN = {
  ...OK_RUN,
  ran_at: "2026-07-20T22:30:00+00:00",
  asof: "2026-07-20",
  appended: 0,
  unchanged: 2,
  edgar_fetches: 0,
  healthy: false,
  problems: ["FROZEN — 0 EDGAR fetches across 2 theses (the cache never refreshed)"],
};

const STATUS_CURRENT = {
  record: {
    edge: "2026-07-17",
    today: "2026-07-20",
    expected_asof: "2026-07-17",
    days_behind: 0,
    stale: false,
    reason: "current — no scheduled run is missing",
  },
  last_run: OK_RUN,
  cron: { status: "healthy", detail: "last run asof 2026-07-17 (live)" },
};

const STATUS_STALE = {
  record: {
    edge: "2026-07-17",
    today: "2026-07-21",
    expected_asof: "2026-07-21",
    days_behind: 2,
    stale: true,
    reason: "2 expected run(s) behind — last expected as-of 2026-07-21",
  },
  last_run: OK_RUN,
  cron: { status: "stale", detail: "record edge 2026-07-17 is 2 expected run(s) behind" },
};

const STATUS_NEVER = {
  record: {
    edge: null,
    today: "2026-07-20",
    expected_asof: "2026-07-20",
    days_behind: null,
    stale: false,
    reason: "the record has never begun — no call-of-record logged yet",
  },
  last_run: null,
  cron: { status: "never_ran", detail: "no daily run has been recorded yet" },
};

const h = vi.hoisted(() => ({
  status: {} as Record<string, unknown>,
  runs: {} as Record<string, unknown>,
  start: {} as Record<string, unknown>,
  job: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useAdminStatus: () => h.status,
  useAdminRuns: () => h.runs,
  useStartDailyRun: () => h.start,
  useDailyRunJob: () => h.job,
}));

beforeEach(() => {
  h.status = {
    data: STATUS_CURRENT,
    isLoading: false,
    error: null,
    refetch: vi.fn(() => Promise.resolve()),
  };
  h.runs = {
    data: { runs: [OK_RUN] },
    isLoading: false,
    error: null,
    refetch: vi.fn(() => Promise.resolve()),
  };
  h.start = { mutate: vi.fn(), isPending: false, isError: false, error: null };
  h.job = { data: undefined, isError: false };
});

function renderAdmin() {
  const onBack = vi.fn();
  const onOpenWorkbench = vi.fn();
  const onOpenScoreboard = vi.fn();
  render(
    <Admin onBack={onBack} onOpenWorkbench={onOpenWorkbench} onOpenScoreboard={onOpenScoreboard} />,
  );
  return { onBack, onOpenWorkbench, onOpenScoreboard };
}

describe("Admin — loading + guards", () => {
  it("shows the loading note while the status loads", () => {
    h.status = { data: undefined, isLoading: true, error: null, refetch: vi.fn() };
    renderAdmin();
    expect(screen.getByText("Reading the run record…")).toBeInTheDocument();
  });

  it("shows the unreachable note on a status error", () => {
    h.status = { data: undefined, isLoading: false, error: new Error("boom"), refetch: vi.fn() };
    renderAdmin();
    expect(screen.getByText(/Admin status unavailable/)).toBeInTheDocument();
  });
});

describe("Admin — honest loudness on the freshness widget", () => {
  it("a CURRENT record is quiet (no stale styling, a plain 'current')", () => {
    renderAdmin();
    const fresh = screen.getByTestId("adm-fresh");
    expect(fresh.className).not.toMatch(/stale/);
    expect(fresh.textContent).toContain("current");
    expect(fresh.textContent).toContain("2026-07-17");
  });

  it("a STALE record is loud (stale styling + days behind)", () => {
    h.status = { ...h.status, data: STATUS_STALE };
    renderAdmin();
    const fresh = screen.getByTestId("adm-fresh");
    expect(fresh.className).toMatch(/stale/);
    expect(fresh.textContent).toContain("2");
    expect(fresh.textContent).toContain("expected run(s) behind");
  });

  it("a never-begun record is the QUIET fresh-install state, not an alarm", () => {
    h.status = { ...h.status, data: STATUS_NEVER };
    renderAdmin();
    const fresh = screen.getByTestId("adm-fresh");
    expect(fresh.className).not.toMatch(/stale/);
    expect(fresh.textContent).toContain("never begun");
    expect(screen.getByTestId("adm-cron").textContent).toContain("never ran");
  });
});

describe("Admin — cron health", () => {
  it("an UNHEALTHY last run surfaces its problems loudly", () => {
    h.status = {
      ...h.status,
      data: {
        ...STATUS_CURRENT,
        last_run: FROZEN_RUN,
        cron: { status: "unhealthy", detail: "last run needs attention: FROZEN" },
      },
    };
    renderAdmin();
    const cron = screen.getByTestId("adm-cron");
    expect(cron.textContent).toContain("unhealthy");
    expect(cron.textContent).toContain("FROZEN");
  });
});

describe("Admin — the run trigger (operator-initiated ONLY)", () => {
  it("NEVER kicks off a run on mount/render — the invariant test", () => {
    renderAdmin();
    expect((h.start.mutate as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it("the click fires the mutation; while the job runs the button disables and progress shows", async () => {
    const user = userEvent.setup();
    const mutate = vi.fn(
      (_vars: unknown, opts?: { onSuccess?: (ref: { job_id: string }) => void }) =>
        opts?.onSuccess?.({ job_id: "j1" }),
    );
    h.start = { mutate, isPending: false, isError: false, error: null };
    h.job = { data: { job_id: "j1", status: "running", result: null, error: null }, isError: false };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Run daily now" }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const btn = screen.getByRole("button", { name: "Running…" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/running the full daily pass/)).toBeInTheDocument();
  });

  it("renders the result when the job lands done", async () => {
    const user = userEvent.setup();
    h.start = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "j1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.job = {
      data: { job_id: "j1", status: "done", result: OK_RUN, error: null },
      isError: false,
    };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Run daily now" }));
    const result = screen.getByTestId("adm-run-result");
    expect(result.textContent).toContain("done");
    expect(result.textContent).toContain("1 appended");
    expect(result.textContent).toContain("1 unchanged");
    // the read refresh fired once the run landed (a READ, not a re-kick)
    expect((h.status.refetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it("renders a loud error when the job fails", async () => {
    const user = userEvent.setup();
    h.start = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "j1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.job = {
      data: { job_id: "j1", status: "failed", result: null, error: "daily run failed: boom" },
      isError: false,
    };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Run daily now" }));
    expect(screen.getByText(/run failed: daily run failed: boom/)).toBeInTheDocument();
  });

  it("a lost job (poll 404) is a visible message, never a spinner", async () => {
    const user = userEvent.setup();
    h.start = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "j1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.job = { data: undefined, isError: true };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Run daily now" }));
    expect(screen.getByText(/lost from view/)).toBeInTheDocument();
  });

  it("a rejected kick-off (409) surfaces the server detail", () => {
    h.start = {
      mutate: vi.fn(),
      isPending: false,
      isError: true,
      error: { detail: "a daily run is already in progress" },
    };
    renderAdmin();
    expect(screen.getByText("a daily run is already in progress")).toBeInTheDocument();
  });
});

describe("Admin — run history", () => {
  it("renders a row per run with the artifact counts", () => {
    h.runs = {
      ...h.runs,
      data: { runs: [FROZEN_RUN, OK_RUN] },
    };
    renderAdmin();
    const hist = screen.getByTestId("adm-hist");
    expect(hist.textContent).toContain("2026-07-20");
    expect(hist.textContent).toContain("2026-07-17");
    expect(hist.textContent).toContain("FROZEN");
    expect(screen.getByText("ok")).toBeInTheDocument(); // the healthy row is quiet
  });

  it("an empty history is an honest quiet line", () => {
    h.runs = { ...h.runs, data: { runs: [] } };
    renderAdmin();
    expect(screen.getByText("no runs recorded yet")).toBeInTheDocument();
  });
});

describe("Admin — nav wiring", () => {
  it("the topbar links fire the page callbacks", async () => {
    const user = userEvent.setup();
    const { onBack, onOpenWorkbench, onOpenScoreboard } = renderAdmin();
    await user.click(screen.getByText("Board"));
    expect(onBack).toHaveBeenCalled();
    await user.click(screen.getByText("Workbench"));
    expect(onOpenWorkbench).toHaveBeenCalled();
    await user.click(screen.getByText("Scoreboard"));
    expect(onOpenScoreboard).toHaveBeenCalled();
  });
});
