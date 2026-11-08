import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { api } from "../api/client";
import { ME_KEY } from "../api/hooks";
import { createQueryClient } from "../api/queryClient";

// The login door in the browser (docs/AUTH.md): the REAL App, hooks, openapi-fetch client and its 401
// middleware, over a faked fetch. Only the heavy pages are stubbed (each renders the real shared header slot).

// openapi-fetch builds `new Request("/api/...")` with the Request it captured when api/client.ts created the
// client; Node's Request needs an absolute URL (a browser resolves it against the page). Hoisted above the
// imports, so the client captures this one, which resolves a path against a fake origin as a browser would.
vi.hoisted(() => {
  const RealRequest = globalThis.Request;
  class PageRequest extends RealRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" && input.startsWith("/") ? `http://localhost${input}` : input, init);
    }
  }
  globalThis.Request = PageRequest as typeof Request;
});

/* eslint-disable @typescript-eslint/no-explicit-any */
vi.mock("../dashboards/DashboardShell", () => ({
  DashboardShell: () => <h1>HOME PAGE</h1>,
}));
vi.mock("../admin/Admin", () => ({
  Admin: (p: any) => (
    <div>
      {p.header}
      <h1>ADMIN PAGE</h1>
    </div>
  ),
}));
vi.mock("../radar/Radar", () => ({
  Radar: (p: any) => (
    <div>
      {p.header}
      <h1>RADAR PAGE</h1>
    </div>
  ),
}));
/* eslint-enable @typescript-eslint/no-explicit-any */

const USER = {
  id: "u-2",
  email: "plain@example.test",
  display_name: "Plain User",
  role: "user",
  workspace_id: "00000000-0000-0000-0000-000000000001",
};
const STEWARD = { ...USER, id: "u-1", email: "steward@example.test", display_name: "The Steward", role: "super_admin" };
const CLOCK = {
  current_night: "2026-10-26",
  action_day: "2026-10-27",
  market_today: "2026-10-27",
  market_tz: "America/New_York",
};

type Reply = { status: number; body?: unknown };
// per-route replies, keyed "METHOD /path" (the path after the /api proxy prefix)
let routes: Record<string, Reply | (() => Reply)>;
let calls: string[];

function json(r: Reply): Response {
  if (r.status === 204) return new Response(null, { status: 204 });
  return new Response(JSON.stringify(r.body ?? {}), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  routes = {
    "GET /auth/me": { status: 401, body: { detail: "sign in required" } },
    "GET /theses": { status: 200, body: [{ id: "t-1", name: "Thesis One" }] },
    "GET /clock": { status: 200, body: CLOCK },
    "POST /auth/logout": { status: 204 },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const req = input instanceof Request ? input : new Request(input, init);
      const path = new URL(req.url, "http://localhost").pathname.replace(/^\/api/, "");
      const key = `${req.method} ${path}`;
      calls.push(key);
      const r = routes[key];
      if (!r) return json({ status: 404, body: { detail: `no fake for ${key}` } });
      return json(typeof r === "function" ? r() : r);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname}</span>;
}

function renderAt(url: string, qc: QueryClient = createQueryClient()) {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <App />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

const where = () => screen.getByTestId("where").textContent;

describe("the login door", () => {
  it("a signed-out visit lands on /login; a good sign-in lands on Home", async () => {
    routes["POST /auth/login"] = { status: 200, body: STEWARD };
    renderAt("/");
    await waitFor(() => expect(where()).toBe("/login"));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "steward@example.test");
    await user.type(screen.getByLabelText("Password"), "a long passphrase");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument();
    expect(where()).toBe("/");
    expect(calls).toContain("POST /auth/login");
  });

  it("a wrong password shows the one error line and stays on /login", async () => {
    routes["POST /auth/login"] = { status: 401, body: { detail: "wrong email or password" } };
    renderAt("/login");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "steward@example.test");
    await user.type(screen.getByLabelText("Password"), "not the password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong email or password");
    expect(where()).toBe("/login");
    // a failed sign-in is the form's error, never the 401 redirect (no /auth/me fired on /login)
    expect(calls).not.toContain("GET /auth/me");
  });

  it("any 401 clears the query cache and lands on /login", async () => {
    routes["GET /auth/me"] = { status: 200, body: STEWARD };
    const qc = renderAt("/radar");
    expect(await screen.findByText("RADAR PAGE")).toBeInTheDocument();
    expect(qc.getQueryData(ME_KEY)).toEqual(STEWARD);
    routes["GET /theses"] = { status: 401, body: { detail: "sign in required" } };
    await api.GET("/theses"); // any read that meets an expired session
    await waitFor(() => expect(where()).toBe("/login"));
    expect(qc.getQueryData(ME_KEY)).toBeUndefined();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("a plain user sees no Admin or Backtest tab, and /admin says stewards only", async () => {
    routes["GET /auth/me"] = { status: 200, body: USER };
    renderAt("/admin");
    expect(await screen.findByText("This page is for stewards only.")).toBeInTheDocument();
    expect(screen.queryByText("ADMIN PAGE")).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation");
    expect(nav).not.toHaveTextContent("Admin");
    expect(nav).not.toHaveTextContent("Backtest");
    expect(screen.getByText("Plain User")).toBeInTheDocument();
  });

  it("a super admin sees the Admin and Backtest tabs and the Admin page", async () => {
    routes["GET /auth/me"] = { status: 200, body: STEWARD };
    renderAt("/admin");
    expect(await screen.findByText("ADMIN PAGE")).toBeInTheDocument();
    const nav = screen.getByRole("navigation");
    expect(nav).toHaveTextContent("Admin");
    expect(nav).toHaveTextContent("Backtest");
  });

  it("Sign out revokes the session, clears the cache and lands on /login", async () => {
    routes["GET /auth/me"] = { status: 200, body: STEWARD };
    const qc = renderAt("/radar");
    expect(await screen.findByText("RADAR PAGE")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(where()).toBe("/login"));
    expect(calls).toContain("POST /auth/logout");
    expect(qc.getQueryData(ME_KEY)).toBeUndefined();
    expect(qc.getQueryData(["clock"])).toBeUndefined();
  });

  it("Settings opens /settings/profile with the change-password form", async () => {
    routes["GET /auth/me"] = { status: 200, body: STEWARD };
    routes["POST /auth/change-password"] = { status: 204 };
    renderAt("/radar");
    expect(await screen.findByText("RADAR PAGE")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByText("Settings"));
    expect(where()).toBe("/settings/profile");
    await user.type(screen.getByLabelText("Current password"), "the old passphrase");
    await user.type(screen.getByLabelText("New password"), "a new passphrase!");
    await user.type(screen.getByLabelText("New password again"), "a new passphrase!");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed");
    expect(calls).toContain("POST /auth/change-password");
  });
});
