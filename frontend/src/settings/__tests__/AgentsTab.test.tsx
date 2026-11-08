import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { createQueryClient } from "../../api/queryClient";
import { SETTINGS_TABS } from "../registry";

// The Agents tab (docs/AGENT_TOOLS.md §Hosted endpoint) in the REAL App over a faked fetch (the settings.test.tsx
// harness): the connect section hosted and not, the steward sentence, the one-time token reveal, revoke-in-place.

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

const ME = {
  id: "u-2",
  email: "ana@example.test",
  display_name: "Ana Test",
  role: "user",
  workspace_id: "00000000-0000-0000-0000-0000000000a2",
};
const CLOCK = {
  current_night: "2026-10-26",
  action_day: "2026-10-27",
  market_today: "2026-10-27",
  market_tz: "America/New_York",
};
const URL_ = "https://deck.example.com/mcp";
const UNHOSTED = { hosted: false, connector_url: null, steward: false, stdio_server: true };
const HOSTED = { hosted: true, connector_url: URL_, steward: false, stdio_server: true };
const LIVE = {
  id: "t-1",
  name: "claude-code laptop",
  created_at: "2026-11-07T14:00:00Z",
  last_used_at: null,
  revoked_at: null,
};
const OLD = {
  id: "t-0",
  name: "old phone",
  created_at: "2026-10-20T14:00:00Z",
  last_used_at: "2026-11-01T14:00:00Z",
  revoked_at: "2026-11-02T14:00:00Z",
};
const SECRET = "invented-token-value-for-tests-only";

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
    "GET /auth/me": { status: 200, body: ME },
    "GET /theses": { status: 200, body: [] },
    "GET /clock": { status: 200, body: CLOCK },
    "GET /account/agents": { status: 200, body: UNHOSTED },
    "GET /account/tokens": { status: 200, body: { tokens: [LIVE, OLD] } },
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
});

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname}</span>;
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

const count = (key: string) => calls.filter((c) => c.key === key).length;

describe("the Agents tab", () => {
  it("is the third registry tab and /settings/agents resolves to it", async () => {
    expect(SETTINGS_TABS.map((t) => t.slug)).toEqual(["profile", "notifications", "agents"]);
    renderAt("/settings/agents");
    const tablist = await screen.findByRole("tablist", { name: "Settings" });
    expect(within(tablist).getByRole("tab", { name: "Agents" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("where")).toHaveTextContent("/settings/agents");
    expect(await screen.findByRole("heading", { name: "Connect an AI client" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Access tokens" })).toBeInTheDocument();
  });

  it("not hosted: the quiet paragraph, no URL, no client blocks", async () => {
    renderAt("/settings/agents");
    expect(await screen.findByText(/needs Alpha Deck on a public address/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Connector URL")).toBeNull();
    expect(document.querySelectorAll("details")).toHaveLength(0);
  });

  it("hosted: the URL with Copy and the four client blocks, the command filled in; no steward sentence", async () => {
    routes["GET /account/agents"] = { status: 200, body: HOSTED };
    renderAt("/settings/agents");
    const field = (await screen.findByLabelText("Connector URL")) as HTMLInputElement;
    expect(field.value).toBe(URL_);
    expect(field).toHaveAttribute("readonly");
    const summaries = [...document.querySelectorAll("details summary")].map((s) => s.textContent);
    expect(summaries).toEqual(["Claude (claude.ai)", "Claude Code", "ChatGPT", "Grok"]);
    expect(screen.getByText(`claude mcp add --transport http alphadeck ${URL_} --header "Authorization: Bearer <your token>"`)).toBeInTheDocument();
    expect(screen.queryByText(/You are a steward/)).toBeNull();
    expect(screen.queryByText(/needs Alpha Deck on a public address/)).toBeNull();
    await userEvent.setup().click(within(field.parentElement as HTMLElement).getByRole("button", { name: "Copy" }));
    expect(within(field.parentElement as HTMLElement).getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("the steward sentence shows only for a super admin", async () => {
    routes["GET /auth/me"] = { status: 200, body: { ...ME, role: "super_admin" } };
    routes["GET /account/agents"] = { status: 200, body: { ...HOSTED, steward: true } };
    renderAt("/settings/agents");
    expect(await screen.findByText(/You are a steward/)).toBeInTheDocument();
  });

  it("lists live tokens, hides revoked ones until Show revoked tokens is ticked", async () => {
    renderAt("/settings/agents");
    const table = await screen.findByRole("table", { name: "Access tokens" });
    expect(within(table).getByText("claude-code laptop")).toBeInTheDocument();
    expect(within(table).getByText("never")).toBeInTheDocument();
    expect(within(table).queryByText("old phone")).toBeNull();
    await userEvent.setup().click(screen.getByLabelText("Show revoked tokens"));
    expect(within(table).getByText("old phone")).toBeInTheDocument();
    expect(within(table).getByText("old phone").closest("tr")).toHaveClass("revoked");
  });

  it("Create: the token is revealed once, the list refreshes, Done hides it for good", async () => {
    let tokens = [LIVE];
    routes["GET /account/tokens"] = () => ({ status: 200, body: { tokens } });
    routes["POST /account/tokens"] = (body) => {
      const made = { id: "t-2", name: (body as { name: string }).name, created_at: "2026-11-08T14:40:00Z" };
      tokens = [{ ...made, last_used_at: null, revoked_at: null }, ...tokens];
      return { status: 201, body: { ...made, token: SECRET } };
    };
    renderAt("/settings/agents");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("New token name"), "morning brief");
    await user.click(screen.getByRole("button", { name: "Create" }));
    const shown = (await screen.findByLabelText("The new token")) as HTMLInputElement;
    expect(shown.value).toBe(SECRET);
    expect(screen.getByText(/This is the only time it is shown/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /account/tokens")?.body).toEqual({ name: "morning brief" });
    const table = screen.getByRole("table", { name: "Access tokens" });
    await waitFor(() => expect(within(table).getByText("morning brief")).toBeInTheDocument());
    expect(count("GET /account/tokens")).toBeGreaterThanOrEqual(2);
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByLabelText("The new token")).toBeNull();
    expect(document.body.textContent).not.toContain(SECRET);
    expect(screen.getByRole("form", { name: "New token" })).toBeInTheDocument();
  });

  it("a refused create shows the API's detail", async () => {
    routes["POST /account/tokens"] = { status: 409, body: { detail: "too many tokens; revoke one first" } };
    renderAt("/settings/agents");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("New token name"), "one more");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too many tokens; revoke one first");
  });

  it("Revoke asks nothing; the row stays, grayed, with 'revoked just now' in place of the button", async () => {
    let tokens = [LIVE];
    routes["GET /account/tokens"] = () => ({ status: 200, body: { tokens } });
    routes["DELETE /account/tokens/t-1"] = () => {
      tokens = [{ ...LIVE, revoked_at: "2026-11-08T15:00:00Z" }];
      return { status: 204 };
    };
    renderAt("/settings/agents");
    const table = await screen.findByRole("table", { name: "Access tokens" });
    await userEvent.setup().click(within(table).getByRole("button", { name: "Revoke" }));
    expect(await within(table).findByText("revoked just now")).toBeInTheDocument();
    expect(within(table).getByText("claude-code laptop").closest("tr")).toHaveClass("revoked");
    expect(within(table).queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(count("DELETE /account/tokens/t-1")).toBe(1);
  });
});

// --- Connected apps (the OAuth sign-in, docs/CONNECTORS.md) -----------------------------------------------------

const CLAUDE_APP = {
  id: "g-1",
  client_name: "Claude",
  client_known: true,
  scopes: ["read"],
  created_at: "2026-11-07T14:00:00Z",
  last_used_at: "2026-11-07T15:00:00Z",
  revoked_at: null,
};
const SELF_REGISTERED = {
  id: "g-2",
  client_name: "Research Desk",
  client_known: false,
  scopes: ["read"],
  created_at: "2026-11-06T14:00:00Z",
  last_used_at: null,
  revoked_at: null,
};
const OLD_APP = {
  ...CLAUDE_APP,
  id: "g-0",
  created_at: "2026-11-01T14:00:00Z",
  revoked_at: "2026-11-02T14:00:00Z",
};

describe("Connected apps", () => {
  it("lists live apps with the unverified tag on a self-registered one; disconnected ones wait for the toggle", async () => {
    routes["GET /account/connections"] = {
      status: 200,
      body: { connections: [CLAUDE_APP, SELF_REGISTERED, OLD_APP] },
    };
    renderAt("/settings/agents");
    expect(await screen.findByRole("heading", { name: "Connected apps" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Connected apps" });
    const heads = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(heads).toEqual(["App", "Access", "Connected", "Last used", ""]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Claude");
    expect(within(rows[0]).queryByText("unverified")).toBeNull();
    expect(within(rows[1]).getByText("unverified")).toBeInTheDocument();
    expect(rows[1].textContent).toContain("“Research Desk”");
    expect(within(rows[1]).getByText("never")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByLabelText("Show disconnected apps"));
    const all = within(table).getAllByRole("row").slice(1);
    expect(all).toHaveLength(3);
    expect(all[2]).toHaveClass("revoked");
    expect(within(all[2]).getByText("disconnected")).toBeInTheDocument();
  });

  it("Disconnect asks nothing; the row stays, grayed, with 'disconnected just now'", async () => {
    let connections = [CLAUDE_APP];
    routes["GET /account/connections"] = () => ({ status: 200, body: { connections } });
    routes["DELETE /account/connections/g-1"] = () => {
      connections = [{ ...CLAUDE_APP, revoked_at: "2026-11-08T15:00:00Z" }];
      return { status: 204 };
    };
    renderAt("/settings/agents");
    const table = await screen.findByRole("table", { name: "Connected apps" });
    await userEvent.setup().click(within(table).getByRole("button", { name: "Disconnect" }));
    expect(await within(table).findByText("disconnected just now")).toBeInTheDocument();
    expect(within(table).getByText("Claude").closest("tr")).toHaveClass("revoked");
    expect(within(table).queryByRole("button", { name: "Disconnect" })).toBeNull();
    expect(count("DELETE /account/connections/g-1")).toBe(1);
  });

  it("empty: one quiet line, no table", async () => {
    routes["GET /account/connections"] = { status: 200, body: { connections: [] } };
    renderAt("/settings/agents");
    expect(await screen.findByText("No app is connected yet. Connect one with the URL above.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Connected apps" })).toBeNull();
    expect(screen.queryByLabelText("Show disconnected apps")).toBeNull();
  });

  it("with oauth off the Claude block keeps its Phase 2 line", async () => {
    routes["GET /account/connections"] = { status: 200, body: { connections: [] } };
    routes["GET /account/agents"] = { status: 200, body: { ...HOSTED, oauth: false } };
    renderAt("/settings/agents");
    await screen.findByLabelText("Connector URL");
    expect(screen.getByText(/Available once sign-in through Claude is enabled/)).toBeInTheDocument();
    expect(screen.queryByText("alphadeck-claude")).toBeNull();
  });

  it("with oauth on: the live steps and the client-ID fallback; ChatGPT and Grok keep their Phase 4 line", async () => {
    routes["GET /account/connections"] = { status: 200, body: { connections: [] } };
    routes["GET /account/agents"] = { status: 200, body: { ...HOSTED, oauth: true } };
    renderAt("/settings/agents");
    await screen.findByLabelText("Connector URL");
    expect(screen.getByText(/leave the client identity on its default/)).toBeInTheDocument();
    expect(screen.getByText(/on the page that opens/)).toBeInTheDocument();
    expect(screen.getByText("alphadeck-claude").tagName).toBe("CODE");
    expect(screen.queryByText(/Available once sign-in through Claude is enabled/)).toBeNull();
    expect(screen.getByText(/sign-in through ChatGPT is enabled on this install \(Phase 4\)/)).toBeInTheDocument();
    expect(screen.getByText(/Grok is enabled on this install \(Phase 4\)/)).toBeInTheDocument();
  });
});
