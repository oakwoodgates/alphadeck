import { describe, expect, it, vi } from "vitest";
import { frozenFetch } from "../frozenFetch";

describe("frozen snapshot reads", () => {
  it("matches exact GET path and query independent of parameter order", async () => {
    const error = vi.fn();
    const fetch = frozenFetch([{ method: "GET", path: "/dashboards/rotation-map", query: { group_by: "link", asof: "2026-10-30" }, status: 200, body: { value: 2 } }], error);
    const response = await fetch("/api/dashboards/rotation-map?asof=2026-10-30&group_by=link");
    expect(await response.json()).toEqual({ value: 2 });
    expect(error).not.toHaveBeenCalled();
  });

  it("refuses writes, missing query values and uncaptured endpoints without falling through to network", async () => {
    const error = vi.fn();
    const fetch = frozenFetch([{ method: "GET", path: "/theses", query: { include_archived: "true" }, status: 200, body: [] }], error);
    await expect(fetch("/api/theses", { method: "POST" })).rejects.toThrow("refused POST");
    await expect(fetch("/api/theses")).rejects.toThrow("refused GET");
    await expect(fetch("/api/clock")).rejects.toThrow("refused GET");
    await expect(fetch("https://example.com/api/theses?include_archived=true")).rejects.toThrow("refused GET");
    expect(error).toHaveBeenCalledTimes(4);
  });
});
