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

const BACKUP = {
  name: "alphadeck-20260721T143000.sql",
  bytes: 2048,
  created_at: "2026-07-21T14:30:00+00:00",
  labeled: false,
};
const BACKUP_LABELED = {
  name: "alphadeck-20260717T025757-pre-shares-backfill.sql",
  bytes: 5_242_880,
  created_at: "2026-07-17T02:57:57+00:00",
  labeled: true,
};

const h = vi.hoisted(() => ({
  status: {} as Record<string, unknown>,
  runs: {} as Record<string, unknown>,
  start: {} as Record<string, unknown>,
  job: {} as Record<string, unknown>,
  backups: {} as Record<string, unknown>,
  createBackup: {} as Record<string, unknown>,
  backupJob: {} as Record<string, unknown>,
}));

vi.mock("../../api/hooks", () => ({
  useAdminStatus: () => h.status,
  useAdminRuns: () => h.runs,
  useStartDailyRun: () => h.start,
  useDailyRunJob: () => h.job,
  useBackups: () => h.backups,
  useCreateBackup: () => h.createBackup,
  useBackupJob: () => h.backupJob,
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
  h.backups = {
    data: { backups: [] },
    isLoading: false,
    error: null,
    refetch: vi.fn(() => Promise.resolve()),
  };
  h.createBackup = { mutate: vi.fn(), isPending: false, isError: false, error: null };
  h.backupJob = { data: undefined, isError: false };
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

describe("Admin — backups (create + list, operator-initiated)", () => {
  it("NEVER creates a snapshot on mount/render — the invariant test", () => {
    renderAdmin();
    expect((h.createBackup.mutate as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it("the null last_backup state prompts to create one; a set one shows an age", () => {
    renderAdmin();
    expect(screen.getByTestId("adm-backups").textContent).toContain("no snapshots yet — create one");

    h.status = { ...h.status, data: { ...STATUS_CURRENT, last_backup: BACKUP } };
    renderAdmin();
    expect(screen.getAllByTestId("adm-backups")[1].textContent).toContain("last snapshot");
    expect(screen.getAllByTestId("adm-backups")[1].textContent).toContain("ago");
  });

  it("the click fires the create mutation; while it runs the button disables and progress shows", async () => {
    const user = userEvent.setup();
    const mutate = vi.fn(
      (_vars: unknown, opts?: { onSuccess?: (ref: { job_id: string }) => void }) =>
        opts?.onSuccess?.({ job_id: "b1" }),
    );
    h.createBackup = { mutate, isPending: false, isError: false, error: null };
    h.backupJob = {
      data: { job_id: "b1", status: "running", result: null, error: null },
      isError: false,
    };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const btn = screen.getByRole("button", { name: "Creating…" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/running pg_dump/)).toBeInTheDocument();
  });

  it("renders the snapshot name + size when the job lands done", async () => {
    const user = userEvent.setup();
    h.createBackup = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "b1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.backupJob = {
      data: { job_id: "b1", status: "done", result: BACKUP, error: null },
      isError: false,
    };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    const result = screen.getByTestId("adm-backup-result");
    expect(result.textContent).toContain("done");
    expect(result.textContent).toContain(BACKUP.name);
    expect(result.textContent).toContain("2.0 KB"); // fmtBytes(2048)
    // the read refresh fired once the snapshot landed (a READ, not a re-kick)
    expect((h.backups.refetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it("renders a loud error when the snapshot fails", async () => {
    const user = userEvent.setup();
    h.createBackup = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "b1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.backupJob = {
      data: { job_id: "b1", status: "failed", result: null, error: "disk full" },
      isError: false,
    };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(screen.getByText(/snapshot failed: disk full/)).toBeInTheDocument();
  });

  it("a lost snapshot job (poll 404) is a visible message, never a spinner", async () => {
    const user = userEvent.setup();
    h.createBackup = {
      mutate: vi.fn((_v: unknown, o?: { onSuccess?: (r: { job_id: string }) => void }) =>
        o?.onSuccess?.({ job_id: "b1" }),
      ),
      isPending: false,
      isError: false,
      error: null,
    };
    h.backupJob = { data: undefined, isError: true };
    renderAdmin();
    await user.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(screen.getByText(/lost from view/)).toBeInTheDocument();
  });

  it("a rejected create (409) surfaces the server detail", () => {
    h.createBackup = {
      mutate: vi.fn(),
      isPending: false,
      isError: true,
      error: { detail: "a snapshot is already in progress" },
    };
    renderAdmin();
    expect(screen.getByText("a snapshot is already in progress")).toBeInTheDocument();
  });

  it("renders the list newest-first with the labeled badge and sizes", () => {
    h.backups = { ...h.backups, data: { backups: [BACKUP, BACKUP_LABELED] } };
    renderAdmin();
    const sec = screen.getByTestId("adm-backups");
    expect(sec.textContent).toContain(BACKUP.name);
    expect(sec.textContent).toContain(BACKUP_LABELED.name);
    expect(sec.textContent).toContain("2.0 KB"); // fmtBytes(2048)
    expect(sec.textContent).toContain("5.0 MB"); // fmtBytes(5 MiB)
    expect(screen.getByText("labeled")).toBeInTheDocument(); // the prune-exempt marker
  });

  it("an empty snapshot list is an honest quiet line", () => {
    h.backups = { ...h.backups, data: { backups: [] } };
    renderAdmin();
    expect(screen.getByTestId("adm-backups").textContent).toContain("no snapshots yet");
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
