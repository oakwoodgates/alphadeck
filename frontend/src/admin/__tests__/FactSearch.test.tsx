import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FactSearch } from "../FactSearch";

// The company search box over a mocked read: typing asks the server, nothing is asked for an empty box, a match
// opens that company's page, and the box itself writes nothing.

const h = vi.hoisted(() => ({
  queries: [] as string[],
  data: undefined as unknown,
}));

vi.mock("../../api/hooks", () => ({
  useResolveSecurities: (q: string) => {
    h.queries.push(q);
    return { data: q.trim() ? h.data : undefined, isFetching: false, isError: false, error: null };
  },
}));

const ACME = { security_id: "00000000-0000-4000-8000-0000000000aa", ticker: "ACME", name: "Acme Corp", cik: null, instrument_kind: "equity" };
const SPYX = { security_id: "00000000-0000-4000-8000-0000000000bb", ticker: "SPYX", name: "Spy Index Fund", cik: null, instrument_kind: "etf" };

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderBox(asof: string | null = null) {
  render(
    <MemoryRouter>
      <FactSearch asof={asof} />
      <Where />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.queries = [];
  h.data = [ACME, SPYX];
});

describe("FactSearch", () => {
  it("asks for nothing and lists nothing while the box is empty", () => {
    renderBox();
    expect(screen.getByLabelText("Find a company (ticker or name)")).toHaveValue("");
    expect(h.queries.every((q) => q.trim() === "")).toBe(true);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("typing asks the server and lists the matches, a fund marked", async () => {
    renderBox();
    await userEvent.setup().type(screen.getByLabelText("Find a company (ticker or name)"), "ac");
    await waitFor(() => expect(h.queries).toContain("ac")); // asked once the typing settles
    expect(await screen.findByRole("button", { name: /ACME/ })).toHaveTextContent("Acme Corp");
    expect(screen.getByRole("button", { name: /SPYX/ })).toHaveTextContent("fund");
    expect(screen.getByRole("button", { name: /ACME/ })).not.toHaveTextContent("fund");
  });

  it("picking a match opens that company's page, keeping the as-of date", async () => {
    renderBox("2026-11-13");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Find a company (ticker or name)"), "ac");
    await user.click(await screen.findByRole("button", { name: /ACME/ }));
    expect(screen.getByTestId("where")).toHaveTextContent(`/admin/facts/${ACME.security_id}?asof=2026-11-13`);
  });

  it("says when nothing matches", async () => {
    h.data = [];
    renderBox();
    await userEvent.setup().type(screen.getByLabelText("Find a company (ticker or name)"), "zzzz");
    expect(await screen.findByText("No company matches.")).toBeInTheDocument();
  });
});
