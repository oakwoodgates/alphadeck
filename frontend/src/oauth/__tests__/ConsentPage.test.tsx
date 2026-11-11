import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { createQueryClient } from "../../api/queryClient";
import { loginPath, safeNext } from "../../auth/next";
import { browser } from "../ConsentPage";

// The OAuth consent page (docs/CONNECTORS.md) in the REAL App over a faked fetch (the AgentsTab.test.tsx harness):
// its four states (loading, consent, done, gone), the scope lines, and the login page's `next` rule.

vi.hoisted(() => {
  const RealRequest = globalThis.Request;
  class PageRequest extends RealRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" && input.startsWith("/") ? `http://localhost${input}` : input, init);
    }
  }
  globalThis.Request = PageRequest as typeof Request;
});

vi.mock("../../dashboards/DashboardShell", () => ({
  DashboardShell: () => <h1>HOME PAGE</h1>,
}));

const ID = "c0ffee00-0000-4000-8000-000000000001";
const PAGE = `/oauth/consent/${ID}`;
const USER = {
  id: "u-1",
  email: "a@oauth.test",
  display_name: "A user",
  role: "user",
  workspace_id: "00000000-0000-0000-0000-0000000000a1",
};
const STEWARD = { ...USER, display_name: "Operator", role: "super_admin" };
const CLOCK = {
  current_night: "2026-10-26",
  action_day: "2026-10-27",
  market_today: "2026-10-27",
  market_tz: "America/New_York",
};
const READ = { name: "read", label: "Read your cards, calls, decisions and ratified facts", grantable: true };
const STEWARD_SCOPE = {
  name: "steward",
  label: "Read the Admin readouts (the split list, the status)",
  grantable: false,
};
const KNOWN = {
  client_name: "Claude Code",
  client_known: true,
  scopes: [READ],
  redirect_host: "localhost",
  expires_at: "2026-11-08T15:10:00Z",
};
const SENT = "http://localhost:54321/callback?code=invented-code&state=st&iss=x";

type Reply = { status: number; body?: unknown };
let routes: Record<string, Reply | ((body: unknown) => Reply)>;
let calls: { key: string; body: unknown }[];

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
    "GET /auth/me": { status: 200, body: USER },
    "GET /theses": { status: 200, body: [] },
    "GET /clock": { status: 200, body: CLOCK },
    [`GET ${PAGE}`]: { status: 200, body: KNOWN },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const req = input instanceof Request ? input : new Request(input, init);
      const path = new URL(req.url, "http://localhost").pathname.replace(/^\/api/, "");
      const key = `${req.method} ${path}`;
      const text = req.method === "GET" || req.method === "DELETE" ? "" : await req.text();
      const body = text ? JSON.parse(text) : undefined;
      calls.push({ key, body });
      const r = routes[key];
      if (!r) return json({ status: 404, body: { detail: `no fake for ${key}` } });
      return json(typeof r === "function" ? r(body) : r);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderAt(url: string) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <App />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const where = () => screen.getByTestId("where").textContent;

describe("the consent page", () => {
  it("a known client asking to read: the lines, the host, Allow and Deny", async () => {
    renderAt(PAGE);
    expect(
      await screen.findByRole("heading", { name: "Claude Code wants to read your Alpha Deck workspace." }),
    ).toBeInTheDocument();
    expect(screen.getByText("A known client.")).toBeInTheDocument();
    expect(screen.queryByText("unverified")).toBeNull();
    const lines = screen.getByRole("list", { name: "What it can read" });
    expect(lines.textContent).toBe("Read your cards, calls, decisions and ratified facts.");
    expect(screen.getByText(/It will not be able to change anything/)).toBeInTheDocument();
    expect(screen.getByText("localhost").tagName).toBe("CODE");
    expect(screen.getByRole("button", { name: "Allow" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
    expect(screen.getByText(/Signed in as A user\./)).toBeInTheDocument();
  });

  it("shows Loading… until the request answers", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((res) => (release = res));
    const realFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string, init?: RequestInit) => {
        const req = input instanceof Request ? input : new Request(input, init);
        if (new URL(req.url).pathname.endsWith(PAGE)) await gate;
        return realFetch(input, init);
      }),
    );
    renderAt(PAGE);
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    release();
    expect(await screen.findByRole("button", { name: "Allow" })).toBeInTheDocument();
  });

  it("a plain user asked for steward: the steward line is struck and labeled, Allow still works", async () => {
    routes[`GET ${PAGE}`] = {
      status: 200,
      body: { ...KNOWN, client_name: "Claude", redirect_host: "claude.ai", scopes: [READ, STEWARD_SCOPE] },
    };
    renderAt(PAGE);
    expect(await screen.findByRole("heading", { name: /^Claude wants to read/ })).toBeInTheDocument();
    const struck = screen.getByText("Read the Admin readouts").closest("li");
    expect(struck).toHaveClass("na");
    expect(struck?.textContent).toBe("Read the Admin readouts (not available to your account)");
    expect(screen.getByText("claude.ai")).toBeInTheDocument();
  });

  it("a steward and a self-registered app: the name quoted, the unverified tag, both lines grantable", async () => {
    routes["GET /auth/me"] = { status: 200, body: STEWARD };
    routes[`GET ${PAGE}`] = {
      status: 200,
      body: {
        ...KNOWN,
        client_name: "Research Desk",
        client_known: false,
        redirect_host: "research-desk.example.com",
        scopes: [READ, { ...STEWARD_SCOPE, grantable: true }],
      },
    };
    renderAt(PAGE);
    expect(
      await screen.findByRole("heading", { name: "“Research Desk” wants to read your Alpha Deck workspace." }),
    ).toBeInTheDocument();
    expect(screen.getByText(/This app registered itself under that name/)).toBeInTheDocument();
    expect(screen.getByText("unverified")).toBeInTheDocument();
    expect(
      screen.getByText("Read the Admin readouts (the split list, the status)."),
    ).not.toHaveClass("na");
    expect(screen.getByText(/Signed in as Operator \(steward\)\./)).toBeInTheDocument();
  });

  it("Allow posts the decision, says where it sends me, and sends the browser there", async () => {
    const assign = vi.spyOn(browser, "assign").mockImplementation(() => undefined);
    routes[`POST ${PAGE}`] = (body) => {
      expect(body).toEqual({ decision: "allow" });
      return { status: 200, body: { redirect_to: SENT } };
    };
    renderAt(PAGE);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Allow" }));
    expect(await screen.findByText("Sending you back to localhost…")).toBeInTheDocument();
    expect(assign).toHaveBeenCalledWith(SENT);
    expect(document.body.textContent).not.toContain("invented-code");
  });

  it("Deny also sends the browser back (the app shows its own message)", async () => {
    const assign = vi.spyOn(browser, "assign").mockImplementation(() => undefined);
    const denied = "http://localhost:54321/callback?error=access_denied&state=st";
    routes[`POST ${PAGE}`] = (body) => {
      expect(body).toEqual({ decision: "deny" });
      return { status: 200, body: { redirect_to: denied } };
    };
    renderAt(PAGE);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Deny" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(denied));
  });

  it.each([404, 410])("gone: a %i reads as expired or already answered", async (status) => {
    routes[`GET ${PAGE}`] = { status, body: { detail: "gone" } };
    renderAt(PAGE);
    expect(
      await screen.findByRole("heading", { name: "This sign-in request has expired or was already answered." }),
    ).toBeInTheDocument();
    expect(screen.getByText(/A request stays open for ten minutes/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
  });

  it("gone after a click: a 409 on the decision shows the same state", async () => {
    routes[`POST ${PAGE}`] = { status: 409, body: { detail: "this sign-in request was already answered" } };
    renderAt(PAGE);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Allow" }));
    expect(
      await screen.findByRole("heading", { name: "This sign-in request has expired or was already answered." }),
    ).toBeInTheDocument();
  });
});

describe("the login page's next rule", () => {
  it("only a consent-page path is honored", () => {
    expect(safeNext(PAGE)).toBe(PAGE);
    for (const bad of [
      null,
      "",
      "/",
      "/settings/agents",
      "https://evil.example/oauth/consent/x",
      "//evil.example/oauth/consent/x",
      "/oauth/consent/",
      "/oauth/consent/../../admin",
      "/oauth/consent/x?y=1",
    ]) {
      expect(safeNext(bad)).toBeNull();
    }
    expect(loginPath(PAGE)).toBe(`/login?next=${encodeURIComponent(PAGE)}`);
    expect(loginPath("/settings/agents")).toBe("/login");
  });

  it("a signed-out visit to a consent page goes through /login and comes back to it", async () => {
    let signedIn = false;
    routes["GET /auth/me"] = () => (signedIn ? { status: 200, body: USER } : { status: 401, body: {} });
    routes["POST /auth/login"] = () => {
      signedIn = true;
      return { status: 200, body: USER };
    };
    renderAt(PAGE);
    await waitFor(() => expect(where()).toBe(`/login?next=${encodeURIComponent(PAGE)}`));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "a@oauth.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(where()).toBe(PAGE));
    expect(await screen.findByRole("button", { name: "Allow" })).toBeInTheDocument();
  });

  it("any other next is ignored: sign-in lands on Home", async () => {
    routes["POST /auth/login"] = { status: 200, body: USER };
    renderAt("/login?next=https%3A%2F%2Fevil.example%2F");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "a@oauth.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(where()).toBe("/"));
  });

  it("a signed-out visit anywhere else carries no next", async () => {
    routes["GET /auth/me"] = { status: 401, body: {} };
    renderAt("/settings/agents");
    await waitFor(() => expect(where()).toBe("/login"));
  });
});
