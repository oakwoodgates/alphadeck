import { afterEach, describe, expect, it, vi } from "vitest";

import { archLabel, todayISO } from "../format";

describe("util/format", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("todayISO() returns the LOCAL date as YYYY-MM-DD", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 20, 12, 0, 0)); // local noon, 20 Jun 2026 — TZ-safe (no midnight flip)
    expect(todayISO()).toBe("2026-06-20");
  });

  it("archLabel maps the full archetype set (the consolidated 6-key map)", () => {
    expect(archLabel("fund")).toBe("ETF sleeve");
    expect(archLabel("adjacent")).toBe("adjacent");
    expect(archLabel("leader")).toBe("leader");
    expect(archLabel("unknown")).toBe("unknown"); // unknown -> raw-key fallback
  });
});
