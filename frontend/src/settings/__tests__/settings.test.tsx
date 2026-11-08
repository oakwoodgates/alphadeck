import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import { createQueryClient } from "../../api/queryClient";
import { SETTINGS_TABS } from "../registry";

// The Settings page (docs/AUTH.md §Settings page) in the REAL App, hooks and openapi-fetch client over a faked
// fetch (the auth.test.tsx harness): the tablist from the registry, the Profile save reaching the header, and
// the Notifications tab's write-only webhook field, status line, Remove and test message.

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
const STORED_URL = "https://hooks.slack.com/services/TEST/TEST/invented-for-tests";

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
    "GET /account/notifications/slack-webhook": { status: 200, body: { set: false, updated_at: null } },
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
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
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

const keys = () => calls.map((c) => c.key);

describe("the Settings page", () => {
  it("the tablist renders every registry entry, in registry order, with the active tab selected", async () => {
    renderAt("/settings/notifications");
    const tablist = await screen.findByRole("tablist", { name: "Settings" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(SETTINGS_TABS.map((t) => t.label));
    expect(SETTINGS_TABS.map((t) => t.slug)).toEqual(["profile", "notifications"]);
    expect(within(tablist).getByRole("tab", { name: "Notifications" })).toHaveAttribute("aria-selected", "true");
    expect(within(tablist).getByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "false");
  });
});

describe("the Profile tab", () => {
  it("is pre-filled, submits, and the header shows the new name at once", async () => {
    routes["PUT /account/profile"] = (body) => ({
      status: 200,
      body: { ...ME, ...(body as object) },
    });
    renderAt("/settings/profile");
    const name = (await screen.findByLabelText("Display name")) as HTMLInputElement;
    const email = screen.getByLabelText("Email") as HTMLInputElement;
    expect(name.value).toBe("Ana Test");
    expect(email.value).toBe("ana@example.test");
    const user = userEvent.setup();
    await user.clear(name);
    await user.type(name, "Ana Reyes");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(document.querySelector(".user-name")).toHaveTextContent("Ana Reyes"));
    const put = calls.find((c) => c.key === "PUT /account/profile");
    expect(put?.body).toEqual({ display_name: "Ana Reyes", email: "ana@example.test" });
    expect(keys().filter((k) => k === "GET /auth/me")).toHaveLength(1); // no refetch: the reply is the cache
  });

  it("shows the API's detail on a refused save", async () => {
    routes["PUT /account/profile"] = { status: 409, body: { detail: "that email is already in use" } };
    renderAt("/settings/profile");
    const email = (await screen.findByLabelText("Email")) as HTMLInputElement;
    const user = userEvent.setup();
    await user.clear(email);
    await user.type(email, "bo@example.test");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("that email is already in use");
    expect(document.querySelector(".user-name")).toHaveTextContent("Ana Test");
  });

  it("carries the change-password form below", async () => {
    renderAt("/settings/profile");
    expect(await screen.findByRole("form", { name: "Change password" })).toBeInTheDocument();
  });
});

describe("the Notifications tab", () => {
  it("unset: the logged-only status line, an empty field, no Remove and no test message", async () => {
    renderAt("/settings/notifications");
    expect(
      await screen.findByText("No Slack webhook. Armed calls and the nightly digest for your cards are logged only."),
    ).toBeInTheDocument();
    expect((screen.getByLabelText("Slack webhook URL") as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send a test message" })).toBeNull();
  });

  it("set: the field stays empty (the URL never comes back), Remove and the test message show", async () => {
    routes["GET /account/notifications/slack-webhook"] = {
      status: 200,
      body: { set: true, updated_at: "2026-11-07T12:00:00Z" },
    };
    routes["POST /account/notifications/slack-webhook/test"] = {
      status: 200,
      body: { delivered: true, detail: "Slack answered 200" },
    };
    renderAt("/settings/notifications");
    expect(await screen.findByText(/^Slack webhook set on /)).toBeInTheDocument();
    const input = screen.getByLabelText("Replace the Slack webhook URL") as HTMLInputElement;
    expect(input.value).toBe("");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(document.body.textContent).not.toContain("hooks.slack.com/services/TEST");
    await userEvent.setup().click(screen.getByRole("button", { name: "Send a test message" }));
    expect(await screen.findByText("Test message sent (Slack answered 200).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("Save sends the URL, clears the field and re-reads the status; Remove clears it", async () => {
    let set = false;
    routes["GET /account/notifications/slack-webhook"] = () => ({
      status: 200,
      body: { set, updated_at: set ? "2026-11-07T12:00:00Z" : null },
    });
    routes["PUT /account/notifications/slack-webhook"] = () => {
      set = true;
      return { status: 204 };
    };
    routes["DELETE /account/notifications/slack-webhook"] = () => {
      set = false;
      return { status: 204 };
    };
    renderAt("/settings/notifications");
    const user = userEvent.setup();
    const input = (await screen.findByLabelText("Slack webhook URL")) as HTMLInputElement;
    await user.type(input, STORED_URL);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/^Slack webhook set on /)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "PUT /account/notifications/slack-webhook")?.body).toEqual({
      url: STORED_URL,
    });
    expect((screen.getByLabelText("Replace the Slack webhook URL") as HTMLInputElement).value).toBe("");
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(
      await screen.findByText("No Slack webhook. Armed calls and the nightly digest for your cards are logged only."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });
});
