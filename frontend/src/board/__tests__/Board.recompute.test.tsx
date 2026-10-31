import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// F11 — the quiet "recomputed, not the recorded call" line: every column on the Board shares the
// ONE as-of dial, so it renders ONCE for the whole board (never per-card — a badge true of every
// row is noise, WB#3) and ONLY when that shared as-of is a PAST date; absent on the live/today
// board (honest loudness #7).
const h = vi.hoisted(() => ({
  theses: [] as unknown[],
  calls: {} as Record<string, { data?: unknown; isLoading?: boolean; isError?: boolean }>,
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: h.theses, isLoading: false, error: null }),
  useDataFlags: () => ({ data: undefined }),
  useCalls: (subjects: { id: string }[]) =>
    subjects.map((s) => h.calls[s.id] ?? { data: undefined, isLoading: false, isError: false }),
  useSetArchived: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { ClockProvider } from "../../clock";
import { todayISO } from "../../util/format";
import { Board } from "../Board";

const noop = () => {};

const incubCall = () => ({
  thesis_id: "t-a",
  state: "incubating",
  verdict: "not_yet",
  conviction_grade: null,
  entry_grade: null,
  key_conviction: { turned: false, detail: "" },
  key_confirmation: { turned: false, detail: "" },
  armed_members: [],
  watch_members: [],
});

beforeEach(() => {
  h.theses = [
    { id: "t-a", name: "Alpha thesis", ticker: "ALFA", basket_size: 1, narrative: "n", archived: false },
  ];
  h.calls = { "t-a": { data: incubCall() } };
});

describe("Board — F11 past-asof recompute note", () => {
  it("shows the quiet board-wide line when scrubbed to a PAST date", () => {
    const { container } = render(<Board asof="2026-06-01" onSelect={noop} />);
    expect(
      screen.getByText("Recomputed as of Jun 1 — today's rules on what was public then, not the recorded call"),
    ).toBeInTheDocument();
    expect(container.querySelector(".board-recompute")).not.toBeNull();
  });

  it("is ABSENT on the live/today board", () => {
    const { container } = render(<Board asof={todayISO()} onSelect={noop} />);
    expect(screen.queryByText(/not the recorded call/)).toBeNull();
    expect(container.querySelector(".board-recompute")).toBeNull();
  });

  // F1 — the default view is the server's CURRENT NIGHT (read live by the server), so it is never labeled a
  // recompute, though it is before today; only a date before the night is.
  it("is ABSENT on the server's current night (the default view) and present before it", () => {
    const server = { currentNight: "2026-10-26", marketToday: "2026-10-27", actionDay: "2026-10-27", failed: false };
    const { container, rerender } = render(
      <ClockProvider value={server}>
        <Board asof="2026-10-26" onSelect={noop} />
      </ClockProvider>,
    );
    expect(container.querySelector(".board-recompute")).toBeNull();
    rerender(
      <ClockProvider value={server}>
        <Board asof="2026-10-23" onSelect={noop} />
      </ClockProvider>,
    );
    expect(container.querySelector(".board-recompute")).not.toBeNull();
  });
});
