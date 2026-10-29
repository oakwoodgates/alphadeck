import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { lazy } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { DashboardShell } from "../dashboards/DashboardShell";
import type { DashboardEntry } from "../dashboards/registry";

// The shared dashboard frame's two layouts (docs/DASHBOARDS.md). jsdom does no layout, so the "fill" body's
// height is pinned two ways: the classes the shell renders, and the CSS rules those classes carry in
// index.css (a fixed-height 100vh column, the body flex: 1 / min-height: 0 / unpadded, the Board filling the
// body instead of pinning itself to 100vh).

vi.mock("../dashboards/registry", () => ({ DASHBOARDS: [] }));

// read off disk: vitest hands a CSS import (even ?raw) back empty
const css = readFileSync(resolve(__dirname, "../index.css"), "utf8");

const body = lazy(() => Promise.resolve({ default: () => <div>THE BODY</div> }));

const FILL: DashboardEntry = {
  slug: "fill-dash",
  order: 0,
  label: "Fill Dash",
  description: "A fill-layout dashboard.",
  status: "core",
  asofAware: true,
  layout: "fill",
  component: body,
};

const PAGE: DashboardEntry = {
  slug: "page-dash",
  order: 10,
  label: "Page Dash",
  description: "A page-layout dashboard (no layout set).",
  status: "experimental",
  asofAware: true,
  component: body,
};

function renderShell(entry: DashboardEntry, navKey?: "home" | "dashboards") {
  return render(
    <MemoryRouter>
      <DashboardShell
        entry={entry}
        navKey={navKey}
        asof="2026-06-01"
        onAsofChange={() => {}}
        onSelectThesis={() => {}}
      />
    </MemoryRouter>,
  );
}

/** The declarations of one top-level rule in index.css, exactly as written (selector match is exact). */
function decls(selector: string): Record<string, string> {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = css.match(new RegExp(`(?:^|\\n)${esc}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`no rule for ${selector} in index.css`);
  return Object.fromEntries(
    m[1]
      .split(";")
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => {
        const i = d.indexOf(":");
        return [d.slice(0, i).trim(), d.slice(i + 1).trim()];
      }),
  );
}

describe("DashboardShell — layout: fill", () => {
  it("renders no title row: no heading, no status tag, no description", async () => {
    const { container } = renderShell(FILL);
    expect(await screen.findByText("THE BODY")).toBeInTheDocument();
    expect(container.querySelector(".dash-title")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Fill Dash" })).not.toBeInTheDocument();
    expect(screen.queryByText("core")).not.toBeInTheDocument();
    expect(screen.queryByText("A fill-layout dashboard.")).not.toBeInTheDocument();
  });

  it("puts the body straight under the top bar in the fill classes", async () => {
    const { container } = renderShell(FILL);
    await screen.findByText("THE BODY");
    const shell = container.querySelector(".dash-shell") as HTMLElement;
    expect(shell).toHaveClass("fill");
    expect(Array.from(shell.children).map((c) => c.tagName)).toEqual(["HEADER", "MAIN"]);
    const main = shell.querySelector("main") as HTMLElement;
    expect(main).toHaveClass("dash-body", "fill");
    expect(main).toHaveTextContent("THE BODY");
  });

  it("the CSS gives the body the rest of a 100vh column, unpadded, and the Board fills the body", () => {
    expect(decls(".dash-shell")).toMatchObject({ display: "flex", "flex-direction": "column" });
    expect(decls(".dash-shell.fill")).toMatchObject({ height: "100vh" });
    expect(decls(".dash-body")).toMatchObject({ flex: "1" });
    expect(decls(".dash-body.fill")).toMatchObject({
      "min-height": "0",
      padding: "0",
      display: "flex",
      "flex-direction": "column",
    });
    const board = decls(".board-shell");
    expect(board).toMatchObject({ flex: "1", "min-height": "0", "flex-direction": "column" });
    expect(board.height).toBeUndefined(); // no longer pinned to 100vh itself
    // the top bar never shrinks in that column (it predates this layout; the fill body relies on it)
    expect(decls(".topbar")).toMatchObject({ "flex-shrink": "0" });
  });
});

describe("DashboardShell — the default page layout is unchanged", () => {
  it("renders the title row (label, status, description) over a padded body, no fill classes", async () => {
    const { container } = renderShell(PAGE);
    expect(await screen.findByText("THE BODY")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Page Dash" })).toBeInTheDocument();
    expect(screen.getByText("experimental")).toHaveClass("dash-status");
    expect(screen.getByText("A page-layout dashboard (no layout set).")).toBeInTheDocument();
    expect(container.querySelector(".dash-shell")).not.toHaveClass("fill");
    expect(container.querySelector("main")).not.toHaveClass("fill");
    expect(decls(".dash-shell")).toMatchObject({ "min-height": "100vh" });
    expect(decls(".dash-body")).toMatchObject({ padding: "16px 18px 28px" });
  });

  it("shows a core status as the same quiet tag", async () => {
    renderShell({ ...PAGE, status: "core" });
    await screen.findByText("THE BODY");
    expect(screen.getByText("core")).toHaveClass("dash-status");
  });
});

describe("DashboardShell — navKey", () => {
  it("lights the Dashboards menu by default", () => {
    renderShell(FILL);
    expect(screen.getByRole("button", { name: "Dashboards" })).toHaveClass("on");
    expect(screen.getByText("Home")).not.toHaveClass("on");
  });

  it("lights Home when it is the home dashboard at /", () => {
    renderShell(FILL, "home");
    expect(screen.getByText("Home")).toHaveClass("on");
    expect(screen.getByRole("button", { name: "Dashboards" })).not.toHaveClass("on");
  });
});
