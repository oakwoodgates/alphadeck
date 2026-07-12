import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { todayISO } from "../util/format";

// The route-mapping suite. The four PAGES are stubbed — each renders its key props as text plus
// buttons that fire its navigation callbacks — so what's under test is exactly App's URL ↔ props
// translation: paths render the right page, params reach props, callbacks move the URL. Page
// internals have their own suites.

const h = vi.hoisted(() => ({
  theses: {
    isLoading: false,
    error: null as unknown,
    data: [{ id: "t-1", name: "Thesis One" }] as unknown,
  },
}));

vi.mock("../api/hooks", () => ({
  useTheses: () => h.theses,
}));

/* eslint-disable @typescript-eslint/no-explicit-any */
vi.mock("../board/Board", () => ({
  Board: (p: any) => (
    <div>
      <h1>BOARD</h1>
      <span data-testid="board-asof">{p.asof}</span>
      <button onClick={() => p.onSelect("t-9")}>board-select</button>
      <button onClick={() => p.onOpenScoreboard()}>board-to-scoreboard</button>
      <button onClick={() => p.onOpenWorkbench()}>board-to-workbench</button>
    </div>
  ),
}));

vi.mock("../scoreboard/Scoreboard", () => ({
  Scoreboard: (p: any) => (
    <div>
      <h1>SCOREBOARD</h1>
      <span data-testid="sb-asof">{p.asof}</span>
      <button onClick={() => p.onSelect("t-9", "HIMS")}>sb-select</button>
      <button onClick={() => p.onSelect("t-9")}>sb-select-bare</button>
      <button onClick={() => p.onBack()}>sb-back</button>
      <button onClick={() => p.onOpenWorkbench()}>sb-to-workbench</button>
    </div>
  ),
}));

vi.mock("../workbench/Workbench", () => ({
  Workbench: (p: any) => (
    <div>
      <h1>WORKBENCH</h1>
      <span data-testid="wb-asof">{p.asof}</span>
      <button onClick={() => p.onBack()}>wb-back</button>
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
      <button onClick={() => p.onBack()}>cp-back</button>
      <button onClick={() => p.onAsofChange("2026-06-01")}>cp-scrub</button>
      <button onClick={() => p.onSelectName("XE")}>cp-pick-xe</button>
      <button onClick={() => p.onSelectName(null)}>cp-clear-name</button>
    </div>
  ),
}));
/* eslint-enable @typescript-eslint/no-explicit-any */

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  h.theses = { isLoading: false, error: null, data: [{ id: "t-1", name: "Thesis One" }] };
});

describe("App routes — path → page", () => {
  it("/ renders the Board", () => {
    renderAt("/");
    expect(screen.getByText("BOARD")).toBeInTheDocument();
  });

  it("/scoreboard renders the Scoreboard", () => {
    renderAt("/scoreboard");
    expect(screen.getByText("SCOREBOARD")).toBeInTheDocument();
  });

  it("/workbench renders the Workbench", () => {
    renderAt("/workbench");
    expect(screen.getByText("WORKBENCH")).toBeInTheDocument();
  });

  it("/thesis/:thesisId renders the Cockpit with the id from the path", () => {
    renderAt("/thesis/t-42");
    expect(screen.getByText("COCKPIT")).toBeInTheDocument();
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-42");
  });

  it("an unknown path redirects to the Board", () => {
    renderAt("/no/such/place");
    expect(screen.getByText("BOARD")).toBeInTheDocument();
  });
});

describe("App routes — ?asof=", () => {
  it("reaches the page prop when well-formed", () => {
    renderAt("/scoreboard?asof=2026-06-01");
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
  });

  it("falls back to today when malformed", () => {
    renderAt("/?asof=junk");
    expect(screen.getByTestId("board-asof")).toHaveTextContent(todayISO());
  });

  it("defaults to today when absent", () => {
    renderAt("/");
    expect(screen.getByTestId("board-asof")).toHaveTextContent(todayISO());
  });

  it("is carried across a Board → Cockpit navigation", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByText("board-select"));
    expect(screen.getByTestId("cp-thesis")).toHaveTextContent("t-9");
    expect(screen.getByTestId("cp-asof")).toHaveTextContent("2026-06-01");
  });

  it("is carried across Board → Scoreboard → Workbench tab moves", async () => {
    const user = userEvent.setup();
    renderAt("/?asof=2026-06-01");
    await user.click(screen.getByText("board-to-scoreboard"));
    expect(screen.getByTestId("sb-asof")).toHaveTextContent("2026-06-01");
    await user.click(screen.getByText("sb-to-workbench"));
    expect(screen.getByTestId("wb-asof")).toHaveTextContent("2026-06-01");
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

  it("a fresh-tab Cockpit deep link (no state) Backs to the Board", async () => {
    const user = userEvent.setup();
    renderAt("/thesis/t-42?asof=2026-06-01");
    await user.click(screen.getByText("cp-back"));
    expect(screen.getByText("BOARD")).toBeInTheDocument();
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
