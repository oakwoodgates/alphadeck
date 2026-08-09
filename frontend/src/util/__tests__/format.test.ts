import { afterEach, describe, expect, it, vi } from "vitest";

import { businessTypeLabel, supersectorLabel, todayISO } from "../format";

describe("util/format", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("todayISO() returns the LOCAL date as YYYY-MM-DD", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 20, 12, 0, 0)); // local noon, 20 Jun 2026 — TZ-safe (no midnight flip)
    expect(todayISO()).toBe("2026-06-20");
  });

  it("businessTypeLabel / supersectorLabel map the taxonomy (raw-key fallback, honest '—')", () => {
    expect(businessTypeLabel("oil_gas")).toBe("oil & gas");
    expect(businessTypeLabel("software_it")).toBe("software/IT");
    expect(businessTypeLabel("some_future_leaf")).toBe("some_future_leaf"); // unknown -> raw-key fallback
    expect(businessTypeLabel(null)).toBe("—"); // unclassified -> the honest dash, never a guess
    expect(supersectorLabel("energy_utilities")).toBe("Energy & Utilities");
    expect(supersectorLabel(null)).toBe("Unclassified"); // the visible tail group's label
  });
});
