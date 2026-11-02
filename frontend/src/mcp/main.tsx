import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { clockFromServer, ClockProvider } from "../clock";
import { DashboardShell } from "../dashboards/DashboardShell";
import { DASHBOARDS } from "../dashboards/registry";
import type { ViewBridge } from "./contract";
import { frozenFetch } from "./frozenFetch";
import { DuplicateTargetError, observe } from "./observe";
import { CaptureViewContext } from "./viewContext";
import "../index.css";
import "./render.css";

const snapshot = window.__ALPHA_DECK_MCP_SNAPSHOT__;
const errors: string[] = [];
const bridge: ViewBridge = { status: "loading", snapshot_id: snapshot?.snapshot_id ?? "", settings: snapshot?.settings ?? {}, observation: null, targets: [] };
window.__ALPHA_DECK_MCP_VIEW__ = bridge;

class CaptureBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, _info: ErrorInfo) { errors.push(error.message); }
  render() { return this.state.failed ? <div data-mcp-ready="true" data-mcp-error="true">Could not render this dashboard.</div> : this.props.children; }
}

const entry = DASHBOARDS.find((d) => d.slug === snapshot?.dashboard);
if (!snapshot || !entry || !entry.capabilities?.observe) {
  bridge.status = "error";
  bridge.error = "A supported frozen dashboard snapshot is required.";
} else {
  // Install before React.lazy imports any query hooks: openapi-fetch captures this fetch at client creation.
  window.fetch = frozenFetch(snapshot.responses, (message) => errors.push(message));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  const host = document.getElementById("root")!;
  host.setAttribute("data-mcp-root", "");
  host.setAttribute("data-mcp-id", "dashboard");
  // Freeze all interactions while keeping the ordinary CSS and control values visible.
  host.setAttribute("inert", "");
  createRoot(host).render(
    <CaptureBoundary>
      <QueryClientProvider client={queryClient}>
        <ClockProvider value={clockFromServer(snapshot.clock)}>
          <CaptureViewContext.Provider value={{ settings: snapshot.settings, readOnly: true }}>
            <MemoryRouter initialEntries={[`/dashboards/${entry.slug}?asof=${snapshot.asof}`]}>
              <DashboardShell entry={entry} asof={snapshot.asof} onAsofChange={() => undefined} onSelectThesis={() => undefined} />
            </MemoryRouter>
          </CaptureViewContext.Provider>
        </ClockProvider>
      </QueryClientProvider>
    </CaptureBoundary>,
  );
  let previous = "";
  let stable = 0;
  const started = performance.now();
  function publishObservation() {
    try {
      Object.assign(bridge, observe(host, entry!.label));
      return true;
    } catch (error) {
      bridge.status = "error";
      bridge.error_code = error instanceof DuplicateTargetError ? "duplicate_target" : "observation_failed";
      bridge.error = "The captured view could not publish unambiguous observation targets.";
      return false;
    }
  }
  async function settle() {
    await document.fonts?.ready;
    const ready = host.querySelector('[data-mcp-ready="true"]') !== null;
    const pendingDetail = host.querySelector('[data-mcp-detail-ready="false"]') !== null;
    const pendingQueries = queryClient.getQueryCache().getAll().some((q) => q.state.fetchStatus === "fetching" || (q.getObserversCount() > 0 && q.state.status === "pending"));
    const signature = `${host.innerHTML}|${host.scrollWidth}|${host.scrollHeight}`;
    stable = signature === previous ? stable + 1 : 0;
    previous = signature;
    if (ready && !pendingDetail && !pendingQueries && stable >= 3) {
      if (!publishObservation()) return;
      const failed = errors.length || host.querySelector('[data-mcp-error="true"]') || queryClient.getQueryCache().getAll().some((q) => q.state.status === "error");
      bridge.status = failed ? "error" : "ready";
      const code = host.querySelector<HTMLElement>("[data-mcp-error-code]")?.dataset.mcpErrorCode;
      if (code) bridge.error_code = code;
      if (failed) bridge.error = [...new Set(errors)].join("; ") || "A dashboard read or render failed; see the visible view.";
      return;
    }
    if (performance.now() - started > 30_000) {
      if (!publishObservation()) return;
      bridge.status = "error";
      bridge.error = errors.join("; ") || "Dashboard did not reach data and layout readiness within 30 seconds.";
      return;
    }
    requestAnimationFrame(() => void settle());
  }
  requestAnimationFrame(() => void settle());
}
