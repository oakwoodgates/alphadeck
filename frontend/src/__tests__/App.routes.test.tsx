import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { DashboardEntry, DashboardProps, RegisteredDashboard } from "../dashboards/registry";
import { todayISO } from "../util/format";

// The route-mapping suite. The pages are stubbed — each renders its `header` slot (the real shared
// AppHeader) plus its key props as text and its own onSelect button — so what's under test is App's
// URL ↔ props translation AND the shared top-nav wiring: paths render the right page, params reach
// props, and AppHeader's links move the URL (carrying ?asof=). Page internals have their own suites.
// The Board is the home dashboard: `/` and /dashboards/board render the REAL registry entry and its real
// wrapper (dashboards/board/BoardDashboard) inside the real shell; only the Board component under it is
// stubbed (its own suites in board/__tests__ cover it).

const h = vi.hoisted(() => ({
  theses: {
    isLoading: false,
    error: null as unknown,
    data: [{ id: "t-1", name: "Thesis One" }] as unknown,
  },
  // the dashboards registry, mutated IN PLACE per test (the mock holds this array's reference); reset to
  // the real Board entry before each test, like the real registry's first line
  dashboards: [] as unknown[],
  // HOME_DASHBOARD, read through a getter so a test can point Home at another slug
  home: "board",
}));

vi.mock("../api/hooks", () => ({
  useTheses: () => h.theses,
}));

vi.mock("../dashboards/registry", () => ({
  DASHBOARDS: h.dashboards,
  get HOME_DASHBOARD() {
    return h.home;
  },
}));

// The real registry, for its Board entry and its home slug (vi.importActual skips the mock above).
const REAL = await vi.importActual<typeof import("../dashboards/registry")>("../dashboards/registry");
const BOARD = REAL.DASHBOARDS.find((d) => d.slug === "board") as DashboardEntry;

// A fake dashboard body: its props as text and an onSelectThesis button — the shell + route wiring is what's
// under test, not any real dashboard.
function FakeBody(p: DashboardProps) {
  return (
    <div>
      <h2>FAKE BODY</h2>
      <span data-testid="dash-asof">{p.asof}</span>
      <button onClick={() => p.onSelectThesis("t-9", "HIMS")}>dash-select</button>
    </div>
  );
}

const FAKE: RegisteredDashboard = {
  slug: "fake-dash",
  order: 1000,
  label: "Fake Dash",
  description: "One fake dashboard for the route tests.",
  status: "experimental",
  asofAware: true,
  thumbnail: "/assets/fake-thumbnail.svg",
  component: lazy(() => Promise.resolve({ default: FakeBody })),
};

const FAKE_NOW: DashboardEntry = {
  slug: "fake-now",
  order: 1010,
  label: "Fake Now",
  description: "A fake now view.",
  status: "experimental",
  asofAware: false,
  component: lazy(() => Promise.resolve({ default: FakeBody })),
};

/* eslint-disable @typescript-eslint/no-explicit-any */
vi.mock("../board/Board", () => ({
  Board: (p: any) => (
    <div>
      {p.header}
      <h2>BOARD</h2>
      <span data-testid="board-asof">{p.asof}</span>
      <button onClick={() => p.onSelect("t-9")}>board-select</button>
      <button onClick={() => p.onSelect("t-9", "HIMS")}>board-select-name</button>
    </div>
  ),
}));

vi.mock("../radar/Radar", () => ({
  Radar: (p: any) => (
    <div>
      {p.header}
      <h1>RADAR</h1>
    </div>
  ),
}));

vi.mock("../admin/Admin", () => ({
  Admin: (p: any) => (
    <div>
      {p.header}
      <h1>ADMIN</h1>
    </div>
  ),
}));

vi.mock("../scoreboard/Scoreboard", () => ({
  Scoreboard: (p: any) => (
    <div>
      {p.header}
      <h1>SCOREBOARD</h1>
      <span data-testid="sb-asof">{p.asof}</span>
      <span data-testid="sb-edition">{String(p.edition)}</span>
      <button onClick={() => p.onEditionChange(1)}>sb-pick-edition-1</button>
      <button onClick={() => p.onEditionChange(0)}>sb-pick-edition-0</button>
      <button onClick={() => p.onSelect("t-9", "HIMS")}>sb-select</button>
      <button onClick={() => p.onSelect("t-9")}>sb-select-bare</button>
    </div>
  ),
}));

vi.mock("../workbench/Workbench", () => ({
  Workbench: (p: any) => (
    <div>
      {p.header}
      <h1>WORKBENCH</h1>
      <span data-testid="wb-asof">{p.asof}</span>
    </div>
  ),
}));

vi.mock("../cockpit/Cockpit", () => ({
  Cockpit: (p: any) => (
    <div>
      <h1>COCKPIT</h1>
      <span data-testid="cp-thesis">{p.thesisId}</span>
      <span data-testid="cp-asof">{p.asof}</span>
      <span data-testid="cp-name">{p.selectedName ?? ""}</span>
      <span data-testid="cp-rail">{String(p.railOpen)}</span>
      <button onClick={() => p.onBack()}>cp-back</button>
      <button onClick={() => p.onAsofChange("2026-06-01")}>cp-scrub</button>
      <button onClick={() => p.onSelectName("XE")}>cp-pick-xe</button>
      <button onClick={() => p.onSelectName(null)}>cp-clear-name</button>
      <button onClick={() => p.onRailChange(false)}>cp-rail-close</button>
      <button onClick={() => p.onRailChange(true)}>cp-rail-open</button>
    </div>
  ),
}));
/* eslint-enable @typescript-eslint/no-explicit-any */

// prints the router's location, so a navigation is asserted on the URL itself
function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <App />
      <Where />
    </MemoryRouter>,
  );
}

const where = () => screen.getByTestId("where").textContent;
const homeTab = () => screen.getByText("Home");
const dashboardsMenu = () => screen.getByRole("button", { name: "Dashboards" });

beforeEach(() => {
  h.theses = { isLoading: false, error: null, data: [{ id: "t-1", name: "Thesis One" }] };
  h.dashboards.length = 0;
  h.dashboards.push(BOARD);
  h.home = REAL.HOME_DASHBOARD;
});

describe("App routes — path → page", () => {
  it("/ renders the Board", async () => {
    renderAt("/");
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
  });

  it("/scoreboard renders the Scoreboard", () => {
    renderAt("/scoreboard");
    expect(screen.getByText("SCOREBOARD")).toBeInTheDocument();
  });

  it("/workbench renders the Workbench", () => {
    renderAt("/workbench");
    expect(screen.getByText("WORKBENCH")).toBeInTheDocument();
  });

  it("/admin renders the Admin", () => {
    renderAt("/admin");
    expect(screen.getByText("ADMIN")).toBeInTheDocument();
  });

  it("/radar renders the SPAC Radar", () => {
    renderAt("/radar");
    expect(screen.getByText("RADAR")).toBeInTheDocument();
  });

  it("/thesis/:thesisId renders the Cockpit with the id from the path", () => {
    renderAt("/thesis/t-42");
    expect(screen.getByText("COCKPIT")).toBeInTheDocument();
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-42");
  });

  it("an unknown path redirects Home (the Board)", async () => {
    renderAt("/no/such/place");
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(where()).toBe("/");
  });
});

describe("App routes — ?asof=", () => {
  it("reaches the page prop when well-formed", () => {
    renderAt("/scoreboard?asof=2026-06-01");
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
  });

  it("falls back to today when malformed", async () => {
    renderAt("/?asof=junk");
    expect(await screen.findByTestId("board-asof")).toHaveTextContent(todayISO());
  });

  it("defaults to today when absent", async () => {
    renderAt("/");
    expect(await screen.findByTestId("board-asof")).toHaveTextContent(todayISO());
  });

  it("is carried across a Board → Cockpit navigation", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(await screen.findByText("board-select"));
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
  });

  it("is carried across Board → Scoreboard → Workbench tab moves", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByText("Scoreboard"));
    expect(screen.getByText("SCOREBOARD")).toBeInTheDocument();
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
    await user.click(screen.getByText("Workbench"));
    expect(screen.getByText("WORKBENCH")).toBeInTheDocument();
    expect(screen.getByTestId("wb-asof")).toHaveTextContent("2026-06-01");
  });
});

describe("App routes — the Admin tab", () => {
  it("Home → Admin → Home round-trips with asof intact", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByText("Admin"));
    expect(screen.getByText("ADMIN")).toBeInTheDocument();
    await user.click(homeTab());
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-06-01");
  });

  it("Admin → Workbench keeps the asof param riding", async () => {
    const user = userEvent.setup();
    renderAt("/admin?asof=2026-06-01");
    await user.click(screen.getByText("Workbench"));
    expect(screen.getByTestId("wb-asof")).toHaveTextContent("2026-06-01");
  });

  it("Home → Radar → Home round-trips with asof intact", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByText("Radar"));
    expect(screen.getByText("RADAR")).toBeInTheDocument();
    await user.click(homeTab());
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-06-01");
  });
});

describe("App routes — Back returns to the originating view", () => {
  it("Scoreboard → Cockpit → Back lands on the Scoreboard with asof intact", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01");
    await user.click(screen.getByText("sb-select"));
    expect(screen.getByText("COCKPIT")).toBeInTheDocument();
    await user.click(screen.getByText("cp-back"));
    expect(screen.getByText("SCOREBOARD")).toBeInTheDocument();
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
  });

  it("a fresh-tab Cockpit deep link (no state) Backs to Home, `/`", async () => {
    const user = userEvent.setup();
    renderAt("/thesis/t-42?asof=2026-06-01");
    await user.click(screen.getByText("cp-back"));
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(where()).toBe("/?asof=2026-06-01");
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-06-01");
  });
});

describe("App routes — the ?name= deep link", () => {
  it("a scoreboard row click lands the Cockpit with the name key AND asof carried", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01");
    await user.click(screen.getByText("sb-select"));
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("HIMS");
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
  });

  it("?name= in a direct URL (the shared link) reaches the Cockpit prop", () => {
    renderAt("/thesis/t-42?name=OKLO");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("OKLO");
  });

  it("picking another name swaps the key; onSelectName(null) clears it", async () => {
    const user = userEvent.setup();
    renderAt("/thesis/t-42?name=OKLO");
    await user.click(screen.getByText("cp-pick-xe"));
    expect(screen.getByTestId("cp-name")).toHaveTextContent("XE");
    await user.click(screen.getByText("cp-clear-name"));
    expect(screen.getByTestId("cp-name").textContent).toBe("");
  });

  it("a name-less select (a thesis-level span) opens the bare Cockpit", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard");
    await user.click(screen.getByText("sb-select-bare"));
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-name").textContent).toBe("");
  });

  it("Back from a name-deep-linked Cockpit returns to the Scoreboard", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01");
    await user.click(screen.getByText("sb-select"));
    await user.click(screen.getByText("cp-back"));
    expect(screen.getByText("SCOREBOARD")).toBeInTheDocument();
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
  });
});

describe("App routes — the ?rail= dial", () => {
  it("defaults to open — an ordinary Cockpit URL carries no rail param", () => {
    renderAt("/thesis/t-42");
    expect(screen.getByTestId("cp-rail")).toHaveTextContent("true");
  });

  it("?rail=0 in a direct URL (a reload, a shared link) reaches the prop collapsed", () => {
    renderAt("/thesis/t-42?rail=0");
    expect(screen.getByTestId("cp-rail")).toHaveTextContent("false");
  });

  it("only the exact \"0\" collapses — junk reads as open, never hiding the call", () => {
    renderAt("/thesis/t-42?rail=nope");
    expect(screen.getByTestId("cp-rail")).toHaveTextContent("true");
  });

  it("collapsing round-trips through the URL and reopening drops the param", async () => {
    const user = userEvent.setup();
    renderAt("/thesis/t-42?asof=2026-06-01&name=OKLO");
    await user.click(screen.getByText("cp-rail-close"));
    expect(screen.getByTestId("cp-rail")).toHaveTextContent("false");
    // the dial rides ALONGSIDE the other params, never replacing them
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("OKLO");

    await user.click(screen.getByText("cp-rail-open"));
    expect(screen.getByTestId("cp-rail")).toHaveTextContent("true");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("OKLO");
  });
});

describe("App routes — the Scoreboard's ?edition= dial (editions S4)", () => {
  it("absent = null: the server's default edition (the param is not invented)", () => {
    renderAt("/scoreboard?asof=2026-06-01");
    expect(screen.getByTestId("sb-edition")).toHaveTextContent("null");
  });

  it("?edition=1 in a direct URL (a reload, a shared link) reaches the prop; junk reads as the default", () => {
    renderAt("/scoreboard?edition=1");
    expect(screen.getByTestId("sb-edition")).toHaveTextContent("1");
  });

  it("junk in ?edition= never reaches the page", () => {
    renderAt("/scoreboard?edition=latest");
    expect(screen.getByTestId("sb-edition")).toHaveTextContent("null");
  });

  it("picking an edition writes ?edition= beside the as-of, and Edition 0 is a real, named value", async () => {
    const user = userEvent.setup();
    renderAt("/scoreboard?asof=2026-06-01");
    await user.click(screen.getByText("sb-pick-edition-1"));
    expect(screen.getByTestId("sb-edition")).toHaveTextContent("1");
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01"); // the dial rides alongside
    await user.click(screen.getByText("sb-pick-edition-0"));
    expect(screen.getByTestId("sb-edition")).toHaveTextContent("0");
  });
});

describe("App routes — the Dashboards (docs/DASHBOARDS.md)", () => {
  it("/dashboards renders the index's empty state while the registry is empty", () => {
    h.dashboards.length = 0;
    renderAt("/dashboards");
    expect(screen.getByText("No dashboards yet.")).toBeInTheDocument();
  });

  it("an unknown slug redirects to /dashboards", () => {
    renderAt("/dashboards/no-such-dashboard");
    expect(where()).toBe("/dashboards");
    expect(screen.getByRole("heading", { name: "Dashboards" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Board/ })).toHaveAttribute("href", "/dashboards/board");
  });

  it("the unknown-slug redirect keeps ?asof= (the index's links carry it)", () => {
    h.dashboards.push(FAKE);
    renderAt("/dashboards/no-such-dashboard?asof=2026-06-01");
    expect(screen.getByRole("link", { name: /Fake Dash/ })).toHaveAttribute(
      "href",
      "/dashboards/fake-dash?asof=2026-06-01",
    );
  });

  it("the index lists every registry entry (label, status, description), linking with ?asof= carried", () => {
    h.dashboards.push(FAKE);
    renderAt("/dashboards?asof=2026-06-01");
    const link = screen.getByRole("link", { name: /Fake Dash/ });
    expect(link).toHaveAttribute("href", "/dashboards/fake-dash?asof=2026-06-01");
    expect(link).toHaveTextContent("experimental");
    expect(link).toHaveTextContent("One fake dashboard for the route tests.");
    expect(screen.queryByText("No dashboards yet.")).not.toBeInTheDocument();
  });

  it("the index tile shows the entry's thumbnail on top: a decorative, lazy 320 x 200 image", () => {
    h.dashboards.push(FAKE);
    renderAt("/dashboards");
    const link = screen.getByRole("link", { name: /Fake Dash/ });
    const img = link.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/assets/fake-thumbnail.svg");
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("width", "320");
    expect(img).toHaveAttribute("height", "200");
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("decoding", "async");
    // the frame comes first in the tile, then the label row
    expect(link.firstElementChild).toHaveClass("dash-tile-thumb");
    expect(link.children[1]).toHaveClass("dash-tile-head");
  });

  it("/dashboards/<slug> renders the entry's body inside the shell, with the as-of dial for an as-of view", async () => {
    h.dashboards.push(FAKE);
    renderAt("/dashboards/fake-dash?asof=2026-06-01");
    expect(await screen.findByText("FAKE BODY")).toBeInTheDocument();
    expect(screen.getByTestId("dash-asof")).toHaveTextContent("2026-06-01");
    expect(screen.getByRole("heading", { name: "Fake Dash" })).toBeInTheDocument();
    expect(screen.getByText("One fake dashboard for the route tests.")).toBeInTheDocument();
    expect(screen.getByLabelText("as-of")).toHaveValue("2026-06-01");
    expect(screen.getByRole("button", { name: "Dashboards" })).toHaveClass("on");
  });

  it("a now dashboard (asofAware false) shows no dial and its body reads today, whatever ?asof= says", async () => {
    h.dashboards.push(FAKE_NOW);
    renderAt("/dashboards/fake-now?asof=2026-06-01");
    expect(await screen.findByText("FAKE BODY")).toBeInTheDocument();
    expect(screen.getByTestId("dash-asof")).toHaveTextContent(todayISO());
    expect(screen.queryByLabelText("as-of")).not.toBeInTheDocument();
  });

  it("onSelectThesis opens the Cockpit (asof + name carried) and Back returns to the dashboard", async () => {
    const user = userEvent.setup();
    h.dashboards.push(FAKE);
    renderAt("/dashboards/fake-dash?asof=2026-06-01");
    await user.click(await screen.findByText("dash-select"));
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("HIMS");
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
    await user.click(screen.getByText("cp-back"));
    expect(await screen.findByText("FAKE BODY")).toBeInTheDocument();
    expect(screen.getByTestId("dash-asof")).toHaveTextContent("2026-06-01");
  });

  it("the top nav's menu reaches a dashboard from the Board with ?asof= carried", async () => {
    const user = userEvent.setup();
    h.dashboards.push(FAKE);
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByRole("button", { name: "Dashboards" }));
    await user.click(screen.getByRole("menuitem", { name: /Fake Dash/ }));
    expect(await screen.findByText("FAKE BODY")).toBeInTheDocument();
    expect(screen.getByTestId("dash-asof")).toHaveTextContent("2026-06-01");
  });
});

describe("App routes — the Board as the home dashboard", () => {
  it("the real registry: the Board is the first entry, core, as-of aware, fill, and the home slug", () => {
    expect(REAL.DASHBOARDS[0]).toBe(BOARD);
    expect(BOARD).toMatchObject({
      slug: "board",
      order: 0,
      label: "Board",
      description: "Every card in its lifecycle column, with the Decision Queue.",
      status: "core",
      asofAware: true,
      layout: "fill",
    });
    expect(REAL.HOME_DASHBOARD).toBe("board");
  });

  it("/ renders the Board through the shell in place: no redirect, no title row, Home lit, the dial on", async () => {
    const { container } = renderAt("/?asof=2026-06-01");
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(where()).toBe("/?asof=2026-06-01");
    expect(container.querySelector(".dash-shell")).toHaveClass("fill");
    expect(container.querySelector(".dash-title")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Board" })).not.toBeInTheDocument();
    expect(homeTab()).toHaveClass("on");
    expect(dashboardsMenu()).not.toHaveClass("on");
    expect(screen.getByLabelText("as-of")).toHaveValue("2026-06-01");
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-06-01");
    // one top bar: the shell's; the Board is handed no header of its own
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });

  it("/dashboards/board renders the same Board, with the Dashboards menu lit instead of Home", async () => {
    const { container } = renderAt("/dashboards/board?asof=2026-06-01");
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(container.querySelector(".dash-title")).toBeNull();
    expect(dashboardsMenu()).toHaveClass("on");
    expect(homeTab()).not.toHaveClass("on");
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-06-01");
  });

  it("the as-of dial at / rewrites ?asof= in place and the Board follows", async () => {
    renderAt("/?asof=2026-06-01");
    await screen.findByText("BOARD");
    fireEvent.change(screen.getByLabelText("as-of"), { target: { value: "2026-05-15" } });
    expect(where()).toBe("/?asof=2026-05-15");
    expect(screen.getByTestId("board-asof")).toHaveTextContent("2026-05-15");
  });

  it("a card click at / opens the Cockpit with ?name= and ?asof=, and Back returns to state.from", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(await screen.findByText("board-select-name"));
    expect(where()).toBe("/thesis/t-9?asof=2026-06-01&name=HIMS");
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-name")).toHaveTextContent("HIMS");
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
    await user.click(screen.getByText("cp-back"));
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(where()).toBe("/?asof=2026-06-01");
  });

  it("a card click at /dashboards/board opens the Cockpit, and Back returns to /dashboards/board", async () => {
    const user = userEvent.setup();
    renderAt("/dashboards/board?asof=2026-06-01");
    await user.click(await screen.findByText("board-select-name"));
    expect(where()).toBe("/thesis/t-9?asof=2026-06-01&name=HIMS");
    await user.click(screen.getByText("cp-back"));
    expect(await screen.findByText("BOARD")).toBeInTheDocument();
    expect(where()).toBe("/dashboards/board?asof=2026-06-01");
  });

  it("a column card (no name) opens the bare Cockpit, and with no ?asof= none is invented", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByText("board-select"));
    expect(where()).toBe("/thesis/t-9");
    expect(screen.getByTestId("cp-name").textContent).toBe("");
    await user.click(screen.getByText("cp-back"));
    expect(where()).toBe("/");
  });

  it("with HOME_DASHBOARD pointed at another slug, / renders that dashboard (its own layout) with Home lit", async () => {
    h.home = "fake-dash";
    h.dashboards.push(FAKE);
    renderAt("/?asof=2026-06-01");
    expect(await screen.findByText("FAKE BODY")).toBeInTheDocument();
    expect(where()).toBe("/?asof=2026-06-01");
    expect(screen.queryByText("BOARD")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Fake Dash" })).toBeInTheDocument();
    expect(screen.getByTestId("dash-asof")).toHaveTextContent("2026-06-01");
    expect(homeTab()).toHaveClass("on");
    expect(dashboardsMenu()).not.toHaveClass("on");
  });

  it("a HOME_DASHBOARD the registry does not name lands on the index, as-of kept", () => {
    h.home = "no-such-dashboard";
    renderAt("/?asof=2026-06-01");
    expect(where()).toBe("/dashboards?asof=2026-06-01");
    expect(screen.getByRole("heading", { name: "Dashboards" })).toBeInTheDocument();
  });
});

describe("App routes — guards render on any URL", () => {
  it("loading", () => {
    h.theses = { isLoading: true, error: null, data: undefined };
    renderAt("/thesis/t-42?name=X");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("API unreachable / nothing seeded", () => {
    h.theses = { isLoading: false, error: new Error("boom"), data: undefined };
    renderAt("/scoreboard");
    expect(screen.getByText(/API not reachable or no thesis seeded/)).toBeInTheDocument();
  });
});
