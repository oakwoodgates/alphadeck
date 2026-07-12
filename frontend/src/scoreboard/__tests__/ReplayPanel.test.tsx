import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReplayPanel } from "../ReplayPanel";

// The historical (replayed) section: absent ENTIRELY without an artifact (no empty shell),
// collapsed by default with the window + count on the header, its own banner + metrics strip,
// and the operator cell saying history predates decision capture — never a fake capture gap.

const fx: { data: unknown; isLoading: boolean; error: unknown } = {
  data: null,
  isLoading: false,
  error: null,
};

vi.mock("../../api/hooks", () => ({
  useScoreboardReplay: () => fx,
}));

const EP = {
  thesis_id: "t-hims",
  security_id: "s1",
  ticker: "HIMS",
  is_headline: true,
  theme_armed: false,
  arm_date: "2026-06-01",
  dearm_date: "2026-06-29",
  close_reason: "arm_until_lapsed",
  status: "closed",
  matured: false,
  censored_start: false,
  verdict: "core_entry",
  entry_grade: "core",
  conviction_grade: "core",
  confidence: 0.9,
  exit_by: "2026-11-28",
  arm_until: "2026-06-11",
  warm_date: null,
  triggers_at_arm: [
    { label: "insider cluster", kind: "insider", grade: "core", ticker: "HIMS", sources: [] },
  ],
  entry_close: 38.2,
  exit_close: 48.78,
  exit_date: "2026-07-09",
  forward_return: 0.277,
  arm_until_return: null,
  warm_return: null,
  peak_return: 0.31,
  peak_date: "2026-07-02",
  exit_vs_peak_days: 7,
  truncated: true,
  insufficient_prices: false,
  operator: null,
};

const PAYLOAD = {
  available: true,
  generated_at: "2026-07-12T03:00:00+00:00",
  window_start: "2025-07-09",
  window_end: "2026-07-09",
  known_at_pin: "2026-07-12T03:00:00+00:00",
  record_began: "2026-07-10",
  window_overlaps_record: false,
  banner: "REPLAYED — today's code + dials over historical facts; NOT the record.",
  min_n: 5,
  n_theses: 2,
  n_episodes: 3,
  n_censored: 0,
  n_eligible: 2,
  metrics: [
    {
      name: "arm_timing_forward_return",
      claim: "timing",
      n: 10,
      insufficient_n: false,
      summary: { median: -0.1776 },
      detail: [],
      note: "",
    },
    { name: "name_selection_lift", claim: "selection", n: 1, insufficient_n: true, summary: {}, detail: [], note: "" },
  ],
  theses: [
    {
      thesis_id: "t-hims",
      name: "HIMS — insider conviction",
      ticker: "HIMS",
      basket_size: 1,
      episodes: [EP],
    },
    { thesis_id: "t-unh", name: "UNH — insider cluster", ticker: "UNH", basket_size: 1, episodes: [] },
  ],
};

function renderPanel(over: Partial<typeof fx> = {}) {
  Object.assign(fx, { data: PAYLOAD, isLoading: false, error: null }, over);
  const onSelect = vi.fn();
  const utils = render(<ReplayPanel onSelect={onSelect} />);
  return { onSelect, ...utils };
}

describe("ReplayPanel", () => {
  it("renders NOTHING at all without an artifact — absence, not an empty shell", () => {
    const { container } = renderPanel({ data: { available: false, theses: [], metrics: [] } });
    expect(container.innerHTML).toBe("");
    const { container: c2 } = renderPanel({ data: null });
    expect(c2.innerHTML).toBe("");
  });

  it("starts collapsed with the window + count on the header (closed ≠ absent)", () => {
    renderPanel();
    const head = screen.getByRole("button", { name: /Historical — replayed/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    // ISO dates: the window spans years, so month-day formatting would read zero-length
    expect(head.textContent).toContain("window 2025-07-09 → 2026-07-09");
    expect(head.textContent).toContain("not the record");
    expect(head.textContent).toContain("· 3");
    expect(screen.queryByText(/REPLAYED — today's code/)).not.toBeInTheDocument();
  });

  it("expands to the banner, its own gated metrics strip, and the replayed rows", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Historical — replayed/ }));
    expect(screen.getByText(/REPLAYED — today's code/)).toBeInTheDocument();
    expect(screen.getByText("arm timing forward return")).toBeInTheDocument();
    expect(screen.getByText(/median -17.8% · n=10/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 metrics await n ≥ 5/)).toBeInTheDocument();
    expect(screen.getByText("HIMS")).toBeInTheDocument();
    expect(screen.getByText("insider")).toBeInTheDocument(); // the WHY chip rides history too
    expect(screen.getByText("+27.7%")).toBeInTheDocument();
  });

  it("says history predates decision capture — never a fake capture gap", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Historical — replayed/ }));
    expect(screen.getByText("— predates decision capture")).toBeInTheDocument();
    expect(screen.queryByText("no decision logged")).not.toBeInTheDocument();
  });

  it("wears the OVERLAPS RECORD badge only when the window was pushed past the record", () => {
    renderPanel();
    expect(screen.queryByText("OVERLAPS RECORD")).not.toBeInTheDocument();
    renderPanel({ data: { ...PAYLOAD, window_overlaps_record: true } });
    expect(screen.getByText("OVERLAPS RECORD")).toBeInTheDocument();
  });

  it("drills into the Cockpit on row click — carrying the clicked NAME for the ?name= deep link", () => {
    const { onSelect } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Historical — replayed/ }));
    fireEvent.click(screen.getByText("+27.7%").closest("tr")!);
    expect(onSelect).toHaveBeenCalledWith("t-hims", "HIMS");
  });
});
