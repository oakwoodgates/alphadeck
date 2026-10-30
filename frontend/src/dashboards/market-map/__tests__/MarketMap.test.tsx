import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MarketMap from "../MarketMap";
import type { MarketMapOut } from "../useMarketMap";
import { company, mem, payload, twoCards } from "./fixture";

// The Market Map body with its one hook stubbed and a fixed 1000 x 672 map: the controls threading their state
// (no refetch), the tiles, the group-header zoom with its breadcrumb and Escape, the hover and pinned info box,
// the click rule, the benchmark, the sizes, the Context/Bench toggle, the table with its sort, the fullscreen
// button, the quiet work line and the flagged note. The hook's request has its own test.

const h = vi.hoisted(() => ({ useMarketMap: vi.fn() }));
vi.mock("../useMarketMap", () => ({ useMarketMap: h.useMarketMap }));

const RECT = {
  x: 0,
  y: 0,
  left: 0,
  top: 0,
  width: 1000,
  height: 600,
  right: 1000,
  bottom: 600,
  toJSON: () => ({}),
} as DOMRect;

const serve = (data: MarketMapOut, extra: Record<string, unknown> = {}) =>
  h.useMarketMap.mockReturnValue({ data, isLoading: false, isFetching: false, error: null, ...extra });

const onSelect = vi.fn();
const renderMap = (data: MarketMapOut = twoCards()) => {
  serve(data);
  return render(<MarketMap asof="2026-06-01" onSelectThesis={onSelect} />);
};

const tile = (container: HTMLElement, key: string) => container.querySelector<HTMLElement>(`[data-k="${key}"]`)!;
const tiles = (container: HTMLElement) => container.querySelectorAll(".mm-tile");
const pick = (name: string) => userEvent.click(screen.getByRole("button", { name }));
const area = (el: HTMLElement) => parseFloat(el.style.width) * parseFloat(el.style.height);
/** A group's header strip, by its label. */
const head = (container: HTMLElement, label: string) =>
  Array.from(container.querySelectorAll<HTMLElement>(".mm-head")).find(
    (b) => b.querySelector(".mm-head-l")?.textContent === label,
  )!;

beforeEach(() => {
  onSelect.mockClear();
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(() => RECT);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (Element.prototype as unknown as Record<string, unknown>).requestFullscreen;
});

describe("the page states", () => {
  it("says it is computing on the first load and when the read failed", () => {
    h.useMarketMap.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    const { unmount } = render(<MarketMap asof="2026-06-01" onSelectThesis={onSelect} />);
    expect(screen.getByText(/Computing across all cards/)).toBeInTheDocument();
    unmount();
    h.useMarketMap.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<MarketMap asof="2026-06-01" onSelectThesis={onSelect} />);
    expect(screen.getByText("Could not load the Market Map.")).toBeInTheDocument();
  });
});

describe("the default map: Card > Link, 1d, market cap, companies", () => {
  it("draws a tile per company-and-link with its ticker and return, and a header per group", () => {
    const { container } = renderMap();
    expect(tiles(container)).toHaveLength(7); // AAA is drawn three times
    expect(within(tile(container, "/card:t-fuel/link:Core/co:ddd")).getByText("DDD")).toBeInTheDocument();
    expect(within(tile(container, "/card:t-fuel/link:Core/co:ddd")).getByText("+5.0%")).toBeInTheDocument();
    // the Context company is not drawn until asked
    expect(container.querySelector('[data-k$="co:ctx"]')).toBeNull();
    // Grid's median (1d: 1, 3, -2) is +1.0%; Fuel has two priced names of three: no median
    expect(head(container, "Grid")).toHaveAttribute("title", "median of 3 priced names · 3 names");
    expect(head(container, "Grid")).toHaveTextContent("+1.0%");
    expect(head(container, "Fuel")).toHaveAttribute("title", "no median: 2 priced names, fewer than 3 · 3 names");
    expect(head(container, "Fuel")).toHaveTextContent("—");
    expect(head(container, "Cables")).toHaveTextContent("—"); // one company: below the floor
  });

  it("colors by the fixed scale: a no-reading tile is hatched, a quiet move gray, a strong one at the end", () => {
    const { container } = renderMap();
    expect(tile(container, "/card:t-fuel/link:Core/co:eee")).toHaveClass("mm-c-none");
    expect(tile(container, "/card:t-fuel/link:Core/co:ddd")).toHaveClass("mm-c-p4"); // +5% is past the 3% end
    expect(tile(container, "/card:t-grid/link:Turbines/co:bbb")).toHaveClass("mm-c-p4"); // +3% is the end
    expect(tile(container, "/card:t-grid/link:Turbines/co:ccc")).toHaveClass("mm-c-n3"); // -2% is 2/3 of the end
    expect(tile(container, "/card:t-grid/link:Turbines/co:aaa")).toHaveClass("mm-c-p1"); // +1% is 1/3 of the end
  });

  it("shows the legend: nine steps with their values for the window, and the no-reading swatch", () => {
    renderMap();
    const legend = screen.getByLabelText("Color scale for 1d");
    expect(within(legend).getAllByText(/%$/)).toHaveLength(9);
    expect(within(legend).getByText("+0.75%")).toBeInTheDocument();
    expect(within(legend).getByText("no reading")).toBeInTheDocument();
  });

  it("draws a company with no cap dashed, and Equal makes every tile the same size and none dashed", async () => {
    const { container } = renderMap();
    expect(tile(container, "/card:t-fuel/link:Core/co:eee")).toHaveClass("dashed");
    expect(tile(container, "/card:t-fuel/link:Core/co:ddd")).not.toHaveClass("dashed");
    await pick("All companies");
    await pick("Equal");
    expect(container.querySelectorAll(".mm-tile.dashed")).toHaveLength(0);
    const a = area(container.querySelector<HTMLElement>(".mm-tile")!);
    for (const el of Array.from(tiles(container)) as HTMLElement[]) {
      expect(Math.abs(area(el) - a) / a).toBeLessThan(0.02);
    }
  });
});

describe("the controls", () => {
  it("change the window without a refetch: the colors, the legend and the work line follow", async () => {
    const { container } = renderMap();
    await pick("90d");
    expect(h.useMarketMap).toHaveBeenLastCalledWith("2026-06-01");
    // every company's 90d return is +4%, a quiet 1/6 of the 25% end
    expect(tile(container, "/card:t-fuel/link:Core/co:ddd")).toHaveClass("mm-c-p1");
    expect(within(tile(container, "/card:t-fuel/link:Core/co:ddd")).getByText("+4.0%")).toBeInTheDocument();
    expect(screen.getByLabelText("Color scale for 90d")).toBeInTheDocument();
    expect(screen.getByText(/priced for 90d/)).toBeInTheDocument();
  });

  it("Compare to subtracts the benchmark's own return for the window, and says so", async () => {
    const { container } = renderMap();
    expect(within(tile(container, "/card:t-grid/link:Turbines/co:bbb")).getByText("+3.0%")).toBeInTheDocument();
    await pick("SPY");
    expect(within(tile(container, "/card:t-grid/link:Turbines/co:bbb")).getByText("+2.5%")).toBeInTheDocument();
    expect(screen.getByText(/returns minus SPY's own/)).toBeInTheDocument();
    await pick("YTD"); // SPY's YTD is +4%; the company's is +5%
    expect(within(tile(container, "/card:t-grid/link:Turbines/co:bbb")).getByText("+1.0%")).toBeInTheDocument();
  });

  it("disables a benchmark with no tape, with the reason", () => {
    renderMap();
    const iwm = screen.getByRole("button", { name: "IWM" });
    expect(iwm).toBeDisabled();
    expect(iwm).toHaveAttribute("title", "no IWM tape at this as-of");
  });

  it("falls back to raw when the chosen benchmark has no reading for the next window", async () => {
    const data = twoCards();
    data.spy = { ...data.spy!, ret_1y: null };
    const { container } = renderMap(data);
    await pick("SPY");
    await pick("1Y");
    expect(screen.getByRole("button", { name: "SPY" })).toBeDisabled();
    expect(screen.getByText("SPY has no 1Y reading: showing raw returns.")).toBeInTheDocument();
    expect(within(tile(container, "/card:t-grid/link:Turbines/co:bbb")).getByText("+6.0%")).toBeInTheDocument();
    expect(screen.getByText(/returns raw/)).toBeInTheDocument();
  });

  it("Tiles: Groups draws one tile per link inside the card boxes", async () => {
    const { container } = renderMap();
    await pick("Groups");
    expect(tiles(container)).toHaveLength(3); // Grid/Turbines, Grid/Cables, Fuel/Core
    expect(within(tile(container, "/card:t-grid/link:Turbines")).getByText("Turbines")).toBeInTheDocument();
    expect(screen.getByText(/3 group tiles drawn/)).toBeInTheDocument();
  });

  it("Show Context and Bench adds their own link box", async () => {
    const { container } = renderMap();
    expect(screen.getByText(/Context and Bench left out \(1 basket row\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Show Context and Bench"));
    expect(tile(container, "/card:t-grid/link:Context/co:ctx")).toBeInTheDocument();
    expect(screen.getByText(/Context and Bench included/)).toBeInTheDocument();
  });
});

describe("zoom", () => {
  it("a group header zooms in, the breadcrumb steps back out and Escape steps out one level", async () => {
    const { container } = renderMap();
    await userEvent.click(head(container, "Grid"));
    const crumbs = screen.getByRole("navigation", { name: "Zoom" });
    expect(within(crumbs).getByText("Grid")).toHaveAttribute("aria-current", "location");
    expect(container.querySelector('[data-k$="co:ddd"]')).toBeNull(); // Fuel's tiles are gone
    expect(tiles(container)).toHaveLength(4); // AAA twice, BBB, CCC

    // one level deeper: the Turbines link
    await userEvent.click(head(container, "Turbines"));
    expect(within(screen.getByRole("navigation", { name: "Zoom" })).getByText("Turbines")).toHaveAttribute(
      "aria-current",
      "location",
    );
    expect(tiles(container)).toHaveLength(3);

    await userEvent.keyboard("{Escape}");
    expect(within(screen.getByRole("navigation", { name: "Zoom" })).getByText("Grid")).toHaveAttribute("aria-current", "location");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("navigation", { name: "Zoom" })).toBeNull();
    expect(tiles(container)).toHaveLength(7);
  });

  it("the breadcrumb's own buttons go back, and a change of grouping resets the zoom", async () => {
    const { container } = renderMap();
    await userEvent.click(head(container, "Grid"));
    await userEvent.click(screen.getByRole("button", { name: "← All" }));
    expect(tiles(container)).toHaveLength(7);
    await userEvent.click(head(container, "Grid"));
    await pick("Sector");
    expect(screen.queryByRole("navigation", { name: "Zoom" })).toBeNull();
  });
});

describe("hover, click and pin", () => {
  it("hover opens an info box with the readings, the cap with its source, the cards and the gaps", () => {
    const { container } = renderMap();
    fireEvent.mouseMove(tile(container, "/card:t-fuel/link:Core/co:eee"), { clientX: 100, clientY: 80 });
    const box = screen.getByRole("status");
    expect(box).toHaveTextContent("EEE");
    expect(box).toHaveTextContent("no 1d reading: no price bars at the as-of");
    expect(box).toHaveTextContent("no 90d reading: 61 of 91 bars");
    expect(box).toHaveTextContent("no market cap: drawn at the smallest size");
    expect(box).toHaveTextContent("awaiting a share count");
    expect(box).toHaveTextContent("Fuel · Core");
    fireEvent.mouseLeave(container.querySelector(".mm-map")!);
    expect(screen.queryByRole("status")).toBeNull();

    fireEvent.mouseMove(tile(container, "/card:t-grid/link:Turbines/co:bbb"), { clientX: 700, clientY: 500 });
    const b = screen.getByRole("status");
    expect(b).toHaveTextContent("BBB Inc");
    expect(b).toHaveTextContent("~$4.00B");
    expect(b).toHaveTextContent("share count ratified in the Workbench");
    expect(b).toHaveTextContent("Energy & Utilities · utilities");
  });

  it("names the share count's source when it came from the filing cover", () => {
    const data = twoCards();
    data.companies.find((c) => c.security_id === "bbb")!.cap_source = "cover";
    const { container } = renderMap(data);
    fireEvent.mouseMove(tile(container, "/card:t-grid/link:Turbines/co:bbb"), { clientX: 10, clientY: 10 });
    expect(screen.getByRole("status")).toHaveTextContent("share count from the filing cover (not ratified)");
  });

  it("with a benchmark set, the box shows the relative figure with the raw one beside it", async () => {
    const { container } = renderMap();
    await pick("SPY");
    fireEvent.mouseMove(tile(container, "/card:t-grid/link:Turbines/co:bbb"), { clientX: 10, clientY: 10 });
    expect(screen.getByRole("status")).toHaveTextContent("+2.5% vs SPY (raw +3.0%)");
  });

  it("a click in a card view opens the Cockpit on that card and the ticker it carries there", async () => {
    const { container } = renderMap();
    await userEvent.click(tile(container, "/card:t-fuel/link:Core/co:aaa"));
    expect(onSelect).toHaveBeenLastCalledWith("t-fuel", "AAA.F");
    await userEvent.click(tile(container, "/card:t-grid/link:Turbines/co:bbb"));
    expect(onSelect).toHaveBeenLastCalledWith("t-grid", "BBB");
  });

  it("in a view with no card, a company on one card opens it and one on several pins the box with Cockpit links", async () => {
    const { container } = renderMap();
    await pick("All companies");
    await userEvent.click(tile(container, "/co:bbb"));
    expect(onSelect).toHaveBeenLastCalledWith("t-grid", "BBB");

    onSelect.mockClear();
    await userEvent.click(tile(container, "/co:aaa"));
    expect(onSelect).not.toHaveBeenCalled();
    const box = screen.getByRole("status");
    expect(tile(container, "/co:aaa")).toHaveClass("pinned");
    await userEvent.click(within(box).getByRole("button", { name: /Fuel/ }));
    expect(onSelect).toHaveBeenLastCalledWith("t-fuel", "AAA.F");
    await userEvent.click(within(box).getByRole("button", { name: /Grid/ }));
    expect(onSelect).toHaveBeenLastCalledWith("t-grid", "AAA");
  });

  it("Escape unpins the box before it steps out of a zoom", async () => {
    const { container } = renderMap();
    await pick("All companies");
    await userEvent.click(tile(container, "/co:aaa"));
    expect(container.querySelector(".mm-info.pinned")).not.toBeNull();
    expect(tile(container, "/co:aaa")).toHaveClass("pinned");
    await userEvent.keyboard("{Escape}");
    expect(container.querySelector(".mm-info.pinned")).toBeNull();
    expect(tile(container, "/co:aaa")).not.toHaveClass("pinned");
  });
});

describe("the table", () => {
  it("lists one row per tile and sorts every column, no value last", async () => {
    renderMap();
    await pick("Table");
    const table = screen.getByRole("table");
    const firstLabels = () =>
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((r) => within(r).getAllByRole("cell")[0].querySelector(".mm-tk-t")!.textContent);
    expect(firstLabels()).toHaveLength(7);
    expect(firstLabels()[0]).toBe("DDD"); // the 1d return, highest first
    expect(firstLabels().at(-1)).toBe("EEE"); // no reading last

    const sortBy = async (name: RegExp) =>
      userEvent.click(within(within(table).getByRole("columnheader", { name })).getByRole("button"));
    await sortBy(/^1d/);
    expect(firstLabels()[0]).toBe("CCC");
    expect(firstLabels().at(-1)).toBe("EEE"); // still last when ascending
    expect(within(table).getByRole("columnheader", { name: /^1d/ })).toHaveAttribute("aria-sort", "ascending");

    await sortBy(/Market cap/);
    expect(firstLabels().slice(0, 3)).toEqual(["AAA", "AAA", "AAA"]);
    await sortBy(/Market cap/);
    expect(firstLabels().at(-1)).toBe("EEE"); // no cap stays last ascending too
    expect(firstLabels()[0]).toBe("DDD");
  });

  it("shows the cap with its band, the dollar volume, the type and the card links", async () => {
    renderMap();
    await pick("Table");
    const row = within(screen.getByRole("table"))
      .getAllByRole("row")
      .find((r) => r.textContent?.includes("BBB Inc"))!;
    expect(row).toHaveTextContent("$4.00B · small");
    expect(row).toHaveTextContent("$5.0M");
    expect(row).toHaveTextContent("utilities");
    await userEvent.click(within(row).getByRole("button", { name: /Grid/ }));
    expect(onSelect).toHaveBeenLastCalledWith("t-grid", "BBB");
  });

  it("is the default on a phone", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    renderMap();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByLabelText("Zoom")).toBeNull();
  });
});

describe("the page's quiet lines", () => {
  it("shows the work: as-of, cards, companies, tiles, priced, caps by source, scale, business type, cache", () => {
    renderMap();
    const line = screen.getByText(/priced for 1d/).textContent!;
    expect(line).toContain("as of Jun 1, 2026");
    expect(line).toContain("2 cards counted");
    expect(line).toContain("5 companies");
    expect(line).toContain("7 tiles drawn");
    expect(line).toContain("4 priced for 1d");
    expect(line).toContain("caps: 4 Workbench / 0 filing cover / 1 none");
    expect(line).toContain("1 company drawn dashed at the smallest size");
    expect(line).toContain("scale ±3% for 1d");
    expect(line).toContain("returns raw");
    expect(line).toContain("business type and sector are today's");
    expect(line).toContain("computed in 1.2 s");
    expect(screen.getByText(/drawn once for each, so the areas here show the cards' structure/)).toBeInTheDocument();
  });

  it("counts a card created after the as-of, a roster fallback and the unresolved rows", () => {
    renderMap(payload({ ...twoCards(), cards_not_yet_created: 2, roster_fallback_cards: 1, unresolved_rows: 3, cache: "hit" }));
    const line = screen.getByText(/priced for 1d/).textContent!;
    expect(line).toContain("2 cards counted (2 created after the as-of left out)");
    expect(line).toContain("3 basket rows without a resolved security not drawn");
    expect(line).toContain("cached");
    expect(screen.getByText(/Today's roster stands in for 1 card/)).toBeInTheDocument();
  });

  it("lists the names left out for a flagged price history, and hides a Context-only one until asked", async () => {
    const data = twoCards();
    data.flagged = [
      { security_id: "rbot", ticker: "RBOT", reason: "mixed-scale price history", silent_only: false },
      { security_id: "cflag", ticker: "CFLAG", reason: "price feed stopped", silent_only: true },
    ];
    renderMap(data);
    expect(screen.getByText("1 name left out for a flagged price history")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Show Context and Bench"));
    expect(screen.getByText("2 names left out for a flagged price history")).toBeInTheDocument();
  });

  it("says so when there is nothing to draw", async () => {
    renderMap(payload({ companies: [company("a", "A")], memberships: [mem("a", "t-grid", "X")] }));
    await userEvent.click(screen.getByLabelText("Show Context and Bench"));
    expect(screen.queryByText("No companies in this view.")).toBeNull();
    renderMap(payload());
    expect(screen.getAllByText("No companies in this view.").length).toBeGreaterThan(0);
  });
});

describe("fullscreen", () => {
  it("asks the browser to fullscreen the map's container", async () => {
    const request = vi.fn();
    (Element.prototype as unknown as Record<string, unknown>).requestFullscreen = request;
    renderMap();
    await userEvent.click(screen.getByRole("button", { name: "Fullscreen" }));
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("offers no button where the browser has no Fullscreen API", () => {
    renderMap();
    expect(screen.queryByRole("button", { name: "Fullscreen" })).toBeNull();
  });
});
