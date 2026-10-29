import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppHeader, type NavKey } from "../AppHeader";
import type { DashboardEntry } from "../dashboards/registry";

// The Dashboards ▾ menu in the shared top bar (docs/DASHBOARDS.md). The registry is mocked and mutated IN
// PLACE per test (empty by default, exactly like the real registry today); a probe prints the router's
// location so a navigation is asserted on the URL itself.

const h = vi.hoisted(() => ({ dashboards: [] as unknown[] }));

vi.mock("../dashboards/registry", () => ({ DASHBOARDS: h.dashboards }));

const FAKE: DashboardEntry = {
  slug: "fake-dash",
  label: "Fake Dash",
  description: "One fake dashboard for the menu tests.",
  status: "experimental",
  asofAware: true,
  component: lazy(() => Promise.resolve({ default: () => <div>FAKE BODY</div> })),
};

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderAt(url: string, current: NavKey = "board") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <AppHeader current={current} />
      <Where />
    </MemoryRouter>,
  );
}

const trigger = () => screen.getByRole("button", { name: "Dashboards" });

beforeEach(() => {
  h.dashboards.length = 0;
});

describe("AppHeader — the Dashboards menu", () => {
  it("is a closed menu button placed right after Board", () => {
    renderAt("/");
    expect(trigger()).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    const order = Array.from(screen.getByRole("navigation").children).map((c) => c.textContent);
    expect(order.slice(0, 3)).toEqual(["Board", "Dashboards ▾", "Workbench"]);
  });

  it("opens on click and lists All dashboards first, then each registry entry with its description", async () => {
    const user = userEvent.setup();
    h.dashboards.push(FAKE);
    renderAt("/");
    await user.click(trigger());
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "All dashboards",
      "Fake DashOne fake dashboard for the menu tests.",
    ]);
    expect(screen.queryByText("None yet")).not.toBeInTheDocument();
  });

  it("an empty registry shows All dashboards and a muted None yet", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(trigger());
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["All dashboards"]);
    expect(screen.getByText("None yet")).toHaveClass("nav-menu-empty");
  });

  it("choosing a dashboard navigates with ?asof= carried and closes the menu", async () => {
    const user = userEvent.setup();
    h.dashboards.push(FAKE);
    renderAt("/?asof=2026-06-01");
    await user.click(trigger());
    await user.click(screen.getByRole("menuitem", { name: /Fake Dash/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/dashboards/fake-dash?asof=2026-06-01");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
  });

  it("All dashboards navigates to the index with ?asof= carried", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01", "scoreboard");
    await user.click(trigger());
    await user.click(screen.getByRole("menuitem", { name: "All dashboards" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/dashboards?asof=2026-06-01");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("with no ?asof= (or a malformed one) the paths stay clean", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=junk");
    await user.click(trigger());
    await user.click(screen.getByRole("menuitem", { name: "All dashboards" }));
    expect(screen.getByTestId("where").textContent).toBe("/dashboards");
  });

  it("Escape closes the menu and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(trigger());
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
    expect(screen.getByTestId("where").textContent).toBe("/"); // closing never navigates
  });

  it("a click outside closes the menu; clicking the trigger again toggles it shut", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(trigger());
    await user.click(screen.getByTestId("where"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await user.click(trigger());
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(trigger());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("the trigger wears the on style only when a dashboards page is current", () => {
    renderAt("/dashboards", "dashboards");
    expect(trigger()).toHaveClass("on");
    expect(screen.getByText("Board")).not.toHaveClass("on");
  });

  it("on any other page the trigger is not on", () => {
    renderAt("/");
    expect(trigger()).not.toHaveClass("on");
  });
});
