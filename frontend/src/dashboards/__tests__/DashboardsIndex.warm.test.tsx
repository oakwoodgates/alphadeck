import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardsIndex } from "../DashboardsIndex";
import type { WarmStatusOut } from "../useDashboardWarm";

// openapi-fetch builds `new Request("/api/...")` with the Request it captured when api/client.ts created the
// client; Node's Request needs an absolute URL (a browser resolves it against the page).
vi.hoisted(() => {
  const RealRequest = globalThis.Request;
  class PageRequest extends RealRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" && input.startsWith("/") ? `http://localhost${input}` : input, init);
    }
  }
  globalThis.Request = PageRequest as typeof Request;
});

vi.mock("../../AppHeader", () => ({
  AppHeader: () => <header data-testid="app-header" />,
}));

function makeStatus(overrides: Partial<WarmStatusOut> = {}): WarmStatusOut {
  return {
    asof: "2026-11-24",
    total: 15,
    warm: 15,
    calls_total: 10,
    calls_warm: 10,
    running: false,
    current_page: null,
    last_started_at: null,
    last_finished_at: "2026-11-24T12:00:00Z",
    last_wall_s: 3.4,
    last_reason: "startup",
    error: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderIndex(asofParam: string | null = null) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DashboardsIndex asofParam={asofParam} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardsIndex warmth control", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("renders quiet Warm · 25/25 indicator without a Warm now button when all dashboards are warm", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse(makeStatus()));
    renderIndex();

    expect(await screen.findByText("Warm · 25/25")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Warm now/i })).not.toBeInTheDocument();
  });

  it("renders 10/25 warm and a Warm now button when partially cold, and POSTs /api/dashboards/warm on click", async () => {
    const coldStatus = makeStatus({ warm: 6, calls_warm: 4 });
    const startedStatus = makeStatus({ warm: 6, calls_warm: 4, running: true, current_page: "board" });

    fetchSpy
      .mockResolvedValueOnce(jsonResponse(coldStatus))
      .mockResolvedValueOnce(jsonResponse(startedStatus, 202));

    renderIndex(null);

    expect(await screen.findByText("10/25 warm")).toBeInTheDocument();
    const warmBtn = screen.getByRole("button", { name: "Warm now" });
    fireEvent.click(warmBtn);

    expect(await screen.findByText("Warming 10/25…")).toBeInTheDocument();

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    const postReq = fetchSpy.mock.calls[1][0] as Request;
    expect(postReq.method).toBe("POST");
    const postUrl = new URL(postReq.url, "http://localhost");
    expect(postUrl.pathname).toBe("/api/dashboards/warm");
    expect(postUrl.search).toBe("");
  });

  it("includes ?asof=2026-06-01 on GET status and POST warm when asofParam is set", async () => {
    const coldStatus = makeStatus({ asof: "2026-06-01", warm: 6, calls_warm: 4 });
    const startedStatus = makeStatus({ asof: "2026-06-01", warm: 6, calls_warm: 4, running: true });

    fetchSpy
      .mockResolvedValueOnce(jsonResponse(coldStatus))
      .mockResolvedValueOnce(jsonResponse(startedStatus, 202));

    renderIndex("2026-06-01");

    const warmBtn = await screen.findByRole("button", { name: "Warm now" });
    fireEvent.click(warmBtn);

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));

    const getReq = fetchSpy.mock.calls[0][0] as Request;
    const getUrl = new URL(getReq.url, "http://localhost");
    expect(getReq.method).toBe("GET");
    expect(getUrl.pathname).toBe("/api/dashboards/warm/status");
    expect(getUrl.searchParams.get("asof")).toBe("2026-06-01");

    const postReq = fetchSpy.mock.calls[1][0] as Request;
    const postUrl = new URL(postReq.url, "http://localhost");
    expect(postReq.method).toBe("POST");
    expect(postUrl.pathname).toBe("/api/dashboards/warm");
    expect(postUrl.searchParams.get("asof")).toBe("2026-06-01");
  });

  it("renders Warming 8/25… with current page tooltip when a warm pass is running", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse(makeStatus({ warm: 5, calls_warm: 3, running: true, current_page: "leaders" })),
    );
    renderIndex();

    const pill = await screen.findByText("Warming 8/25…");
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveAttribute("title", "Warming leaders");
    expect(screen.queryByRole("button", { name: /Warm now/i })).not.toBeInTheDocument();
  });

  it("fails open and renders the index cleanly without intrusive chrome when status fails", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));
    renderIndex();

    expect(screen.getByRole("heading", { name: "Dashboards" })).toBeInTheDocument();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/warm/i)).not.toBeInTheDocument();
  });
});
