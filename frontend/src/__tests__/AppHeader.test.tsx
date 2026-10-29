import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppHeader, type NavKey } from "../AppHeader";
import type { DashboardEntry } from "../dashboards/registry";

// The Home tab and the Dashboards ▾ menu in the shared top bar (docs/DASHBOARDS.md). The registry is mocked
// and mutated IN PLACE per test (empty by default); a probe prints the router's location so a navigation is
// asserted on the URL itself.

const h = vi.hoisted(() => ({ dashboards: [] as unknown[] }));

vi.mock("../dashboards/registry", () => ({ DASHBOARDS: h.dashboards }));

// The real registry (vi.importActual skips the mock above), for the menu's real order.
const REAL = await vi.importActual<typeof import("../dashboards/registry")>("../dashboards/registry");

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

function renderAt(url: string, current: NavKey = "home") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <AppHeader current={current} />
      <Where />
    </MemoryRouter>,
  );
}

const trigger = () => screen.getByRole("button", { name: "Dashboards" });
const homeTab = () => screen.getByText("Home");

beforeEach(() => {
  h.dashboards.length = 0;
});

describe("AppHeader — the Home tab", () => {
  it("is the first tab, labeled Home (no Board tab: the Board is the home dashboard)", () => {
    renderAt("/");
    const order = Array.from(screen.getByRole("navigation").children).map((c) => c.textContent);
    expect(order[0]).toBe("Home");
    expect(order).not.toContain("Board");
  });

  it("is lit at / (current home) and nothing else is", () => {
    renderAt("/", "home");
    expect(homeTab()).toHaveClass("on");
    expect(trigger()).not.toHaveClass("on");
  });

  it("navigates to / with ?asof= carried from another page", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01", "scoreboard");
    expect(homeTab()).not.toHaveClass("on");
    await user.click(homeTab());
    expect(screen.getByTestId("where").textContent).toBe("/?asof=2026-06-01");
  });

  it("on a dashboard page, the Board's own included, the Dashboards menu is lit and Home is not", () => {
    renderAt("/dashboards/board", "dashboards");
    expect(trigger()).toHaveClass("on");
    expect(homeTab()).not.toHaveClass("on");
  });
});

describe("AppHeader — the Dashboards menu", () => {
  it("is a closed menu button placed right after Home", () => {
    renderAt("/");
    expect(trigger()).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    const order = Array.from(screen.getByRole("navigation").children).map((c) => c.textContent);
    expect(order.slice(0, 3)).toEqual(["Home", "Dashboards ▾", "Workbench"]);
  });

  it("with the real registry, lists the Board first, right after All dashboards", async () => {
    const user = userEvent.setup();
    h.dashboards.push(...REAL.DASHBOARDS);
    renderAt("/?asof=2026-06-01");
    await user.click(trigger());
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveTextContent("All dashboards");
    expect(items[1].textContent).toBe(
      "BoardEvery card in its lifecycle column, with the Decision Queue.",
    );
    expect(items).toHaveLength(REAL.DASHBOARDS.length + 1);
    await user.click(items[1]);
    expect(screen.getByTestId("where").textContent).toBe("/dashboards/board?asof=2026-06-01");
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
    expect(homeTab()).not.toHaveClass("on");
  });

  it("on any other page the trigger is not on", () => {
    renderAt("/");
    expect(trigger()).not.toHaveClass("on");
  });
});
