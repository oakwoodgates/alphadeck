import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PeerTable from "../PeerTable";
import type { PeerScopesOut, PeerTableOut } from "../usePeerTable";
import { AAA, BBB, CCC, COVERAGE, DDD, GROUP, MIXED, payload, scopesPayload } from "./fixtures";

// The Peer Table body with its two hooks stubbed: the scope control, the grouped table in the server's order (one
// line per company, a header row per group carrying its figures and what they rest on), every sort (a blank last),
// the filters (and the ones that cannot discriminate left off), a flagged price history grayed with its reason, the
// chips opening the Cockpit, the work lines, the coverage, the unresolved rows, the limits, and the empty / slow /
// error states. The hooks' requests have their own test.

const h = vi.hoisted(() => ({ scopes: vi.fn(), table: vi.fn() }));
vi.mock("../usePeerTable", () => ({ usePeerScopes: h.scopes, usePeerTable: h.table }));

const ok = (data: unknown, extra: Record<string, unknown> = {}) => ({
  data,
  isLoading: false,
  isFetching: false,
  error: null,
  ...extra,
});

function setup(
  data: PeerTableOut | undefined = payload(),
  scopes: PeerScopesOut = scopesPayload(),
  extra: Record<string, unknown> = {},
) {
  h.scopes.mockReturnValue(ok(scopes));
  h.table.mockImplementation(() => ok(data, extra));
  const onSelectThesis = vi.fn();
  const view = render(<PeerTable asof="2026-10-28" onSelectThesis={onSelectThesis} />);
  return { onSelectThesis, ...view };
}

const lines = () => screen.queryAllByTestId("pt-row");
const keys = () => lines().map((r) => r.querySelector(".pt-chip")?.textContent);
const cells = (row: HTMLElement) => [...row.querySelectorAll("td")];
const header = (name: RegExp) => screen.getByRole("columnheader", { name });
const headerCoverage = (name: RegExp) => header(name).querySelector(".pt-cov")?.textContent;

beforeEach(() => {
  h.scopes.mockReset();
  h.table.mockReset();
});

describe("PeerTable — the table", () => {
  it("draws a header row for the group and one line per company in the server's order", () => {
    setup();
    expect(keys()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    const groups = screen.getAllByTestId("pt-group");
    expect(groups).toHaveLength(1);
    expect(groups[0].querySelector(".pt-group-label")?.textContent).toBe("miner");
    expect(groups[0].textContent).toContain("4 names");
    // the group row precedes its companies
    const order = [...document.querySelectorAll("tbody tr")].map((r) => r.getAttribute("data-testid"));
    expect(order).toEqual(["pt-group", "pt-row", "pt-row", "pt-row", "pt-row"]);
  });

  it("heads the columns in order, each carrying its coverage", () => {
    setup();
    expect(
      screen.getAllByRole("columnheader").map((c) => c.querySelector("button")?.textContent?.replace(/[↑↓]/g, "").trim()),
    ).toEqual([
      "Company",
      "Cards",
      "Business type",
      "Cap",
      "Revenue",
      "YoY",
      "Runway",
      "Overhang",
      "Purity",
      "Off 52w high",
      "50d",
      "200d",
      "RVOL 20",
      "ADV 20d",
      "30D vs SPY",
      "Insider buys 90d",
    ]);
    expect(headerCoverage(/^Cap/)).toBe("2 of 4");
    expect(headerCoverage(/^Revenue/)).toBe("4 of 4");
    expect(headerCoverage(/^Purity/)).toBe("1 of 4");
    expect(header(/^Company/).querySelector(".pt-cov")).toBeNull(); // the pinned column carries none
    expect(header(/^Cap/).querySelector(".pt-cov")).toHaveAttribute(
      "title",
      "2 of 4 names state this. Blank: 1 flagged, 1 awaiting shares.",
    );
    expect(header(/^Company/)).toHaveAttribute("aria-sort", "none");
  });

  it("shows the company's ticker chip and master name, its business type and the royalty overlay", () => {
    setup();
    const r = lines()[0];
    expect(within(r).getByText("AAA")).toHaveClass("pt-chip");
    expect(within(r).getByText("Acme Mining")).toBeInTheDocument();
    expect(cells(r)[2].textContent).toBe("miner");
    expect(cells(lines()[1])[2].textContent).toBe("miner");
  });

  it("marks a royalty house quietly and leaves an unclassified company's type blank", () => {
    setup(
      payload({
        companies: [
          { ...AAA, royalty: true },
          { ...BBB, business_type: null },
        ],
        groups: [{ ...GROUP, members: [AAA.key, BBB.key] }],
      }),
    );
    expect(cells(lines()[0])[2].textContent).toBe("miner royalty");
    expect(cells(lines()[1])[2].textContent).toBe("");
  });

  it("shows the approximate cap as its tier and figure, and says in the cell why one is blank", () => {
    setup();
    const [aaa, bbb, ccc, ddd] = lines().map((r) => cells(r)[3].querySelector(".pt-cell") as HTMLElement);
    expect(aaa.textContent).toBe("micro · $250M");
    expect(aaa.getAttribute("title")).toContain("latest close $25.00");
    expect(bbb.textContent).toBe("small · $1.0B");
    expect(ccc.textContent).toBe("awaiting shares");
    expect(ddd.textContent).toBe("flagged");
    expect(ddd).toHaveClass("gray");
    expect(ddd.getAttribute("title")).toContain(MIXED);
  });

  it("shows revenue, YoY, runway, overhang and purity with their hovers, and a dash with its reason when blank", () => {
    setup();
    const a = cells(lines()[0]);
    expect(a[4].textContent).toBe("$120.0M");
    expect(a[5].textContent).toBe("+20.0%");
    expect(a[5].querySelector(".pt-cell")).toHaveClass("pos");
    expect(a[6].textContent).toBe("12.0 mo");
    expect(a[7].textContent).toBe("6.0%");
    expect(a[8].textContent).toBe("62%");
    expect(a[8].querySelector(".pt-cell")?.getAttribute("title")).toContain("nuclear");
    const b = cells(lines()[1]);
    expect(b[5].textContent).toBe("—");
    expect(b[5].querySelector(".pt-cell")?.getAttribute("title")).toContain("no row for the same period a year earlier");
    expect(b[6].textContent).toBe("cash-positive");
    expect(cells(lines()[2])[6].textContent).toBe("—"); // no cash/burn fact
    expect(cells(lines()[2])[7].querySelector(".pt-cell")?.getAttribute("title")).toContain("no live convertible-note overhang");
  });

  it("shows the tape: the 52w high, two marks for the averages, RVOL, dollar volume and vs SPY", () => {
    setup();
    const a = cells(lines()[0]);
    expect(a[9].textContent).toBe("-10.5%");
    expect([a[10].textContent, a[11].textContent]).toEqual(["▲", "▲"]);
    expect(a[10].querySelector(".pt-cell")).toHaveClass("pos");
    expect(a[12].textContent).toBe("1.4×");
    expect(a[13].textContent).toBe("$1.2M");
    expect(a[14].textContent).toBe("+6.0");
    expect(a[14].querySelector(".pt-cell")?.getAttribute("title")).toContain("minus SPY +4.0%");
    const b = cells(lines()[1]);
    expect(b[10].textContent).toBe("▼");
    expect(b[10].querySelector(".pt-cell")).toHaveClass("neg");
    expect(b[11].textContent).toBe("—"); // short history: blank with the member's own reason
    expect(b[11].querySelector(".pt-cell")?.getAttribute("title")).toContain("n/a: 120/200 bars");
  });

  it("grays every price-derived cell of a flagged company with the reason and keeps the cells that read no price", () => {
    setup();
    const d = cells(lines()[3]);
    for (const i of [3, 9, 10, 11, 12, 13, 14]) {
      const c = d[i].querySelector(".pt-cell") as HTMLElement;
      expect(c.textContent, `column ${i}`).toBe("flagged");
      expect(c).toHaveClass("gray");
      expect(c.getAttribute("title")).toContain(MIXED);
    }
    expect(d[4].textContent).toBe("$150.0M");
    expect(d[5].textContent).toBe("+50.0%");
    expect(d[6].textContent).toBe("6.0 mo");
    expect(within(lines()[3]).getByText("DDD")).toBeInTheDocument(); // the row stays
  });

  it("shows insider buys as a count and dollars, a stated zero quietly, a dash when no Form 4 is ingested", () => {
    setup();
    expect(cells(lines()[0])[15].textContent).toBe("3 · $1.3M");
    const zero = cells(lines()[1])[15].querySelector(".pt-cell") as HTMLElement;
    expect([zero.textContent, zero.classList.contains("gray")]).toEqual(["0", true]);
    expect(cells(lines()[2])[15].textContent).toBe("—");
  });

  it("uses no badge and no color class by tier or role", () => {
    const { container } = setup();
    expect(container.querySelectorAll("[class*='badge']")).toHaveLength(0);
    for (const r of lines()) expect(r.className).toBe("");
  });
});

describe("PeerTable — the company cell", () => {
  it("opens the ticker chip on the Cockpit for that name on its first card", async () => {
    const { onSelectThesis } = setup();
    await userEvent.click(within(lines()[0]).getByTitle("Open Grid on AAA"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "AAA");
    // BBB's first card (by name) is Batteries, where it is a payer
    await userEvent.click(within(lines()[1]).getByTitle("Open Batteries on BBB"));
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-batt", "BBB");
  });

  it("shows the card count with the chips on hover, and opens the chips on a click", async () => {
    const { onSelectThesis } = setup();
    const btn = within(lines()[1]).getByRole("button", { name: "2 cards" });
    expect(btn).toHaveAttribute("title", "Batteries · Cells; Grid · Turbines, Cables");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(lines()[1].querySelector(".pt-chips")).toBeNull();
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    const chips = [...lines()[1].querySelectorAll(".pt-card")];
    expect(chips.map((c) => c.textContent)).toEqual(["Batteries · Cells", "Grid · Turbines, Cables"]);
    await userEvent.click(chips[1]);
    expect(onSelectThesis).toHaveBeenLastCalledWith("t-grid", "BBB");
    await userEvent.click(btn);
    expect(lines()[1].querySelector(".pt-chips")).toBeNull();
    expect(within(lines()[0]).getByRole("button", { name: "1 card" })).toBeInTheDocument();
  });

  it("tags a role as quiet lowercase text and a silent-only company as silent, and nothing otherwise", () => {
    setup();
    expect(lines()[1].querySelector(".pt-tag")?.textContent).toBe("payer");
    const silent = lines()[3].querySelector(".pt-tag");
    expect(silent?.textContent).toBe("silent on 1 card");
    expect(silent).toHaveClass("silent");
    expect(lines()[0].querySelector(".pt-tag")).toBeNull();
  });
});

describe("PeerTable — the group rows", () => {
  it("carries the group's figures under their columns, each saying what it rests on", () => {
    setup();
    const g = cells(screen.getByTestId("pt-group"));
    // [cards, business type, cap, revenue, yoy, runway, overhang, purity, off high, 50d, 200d, rvol, adv, vs spy, insider]
    const text = (i: number) => g[i].textContent;
    expect(text(2)).toBe("$625Mmedian of 2 priced"); // cap
    expect(text(4)).toBe("+20.0%median of 3 with a prior year"); // yoy
    expect(text(5)).toBe("3with a figure"); // runway
    expect(text(9)).toBe("2 of 3priced"); // 50d
    expect(text(13)).toBe("+1.0median of 3 priced"); // vs SPY
    expect(g[2].querySelector(".pt-fig")).toHaveAttribute(
      "title",
      "Median approximate market cap of 2 names (priced), equal weight",
    );
  });

  it("shows one priced name's value with '1 name', never a median label", () => {
    setup(
      payload({
        groups: [
          {
            ...GROUP,
            stats: { ...GROUP.stats, cap: { value: 250_000_000, n: 1 }, yoy: { value: 20, n: 1 }, vs_spy: { value: 6, n: 1 } },
          },
        ],
      }),
    );
    const g = screen.getByTestId("pt-group");
    expect(g.textContent).toContain("$250M1 name");
    expect(g.textContent).not.toContain("median of 1");
  });

  it("leaves a figure empty when no name stands behind it", () => {
    setup(
      payload({
        groups: [
          {
            ...GROUP,
            stats: {
              names: 2,
              cap: { value: null, n: 0 },
              above_50d: 0,
              priced_50d: 0,
              vs_spy: { value: null, n: 0 },
              yoy: { value: null, n: 0 },
              runway: 0,
            },
          },
        ],
      }),
    );
    expect(screen.getByTestId("pt-group").querySelectorAll(".pt-fig")).toHaveLength(0);
  });

  it("nests a leaf under its supersector in the all-cards scope and labels the old link of a card", () => {
    const sector = { ...GROUP, key: "sector:materials", label: "materials", kind: "supersector" as const, members: [] };
    const leaf = { ...GROUP, key: "type:miner", depth: 1 };
    const old = { ...GROUP, key: "old_link:X", label: "Retired", kind: "old_link" as const, members: [] };
    setup(payload({ scope: "all", key: null, groups: [sector, leaf, old] }));
    const groups = screen.getAllByTestId("pt-group");
    expect(groups.map((g) => g.querySelector(".pt-group-label")?.textContent)).toEqual(["Materials", "miner", "Retired"]);
    expect(groups[1]).toHaveClass("depth-1");
    expect(groups[0]).toHaveClass("depth-0");
    expect(groups[2].textContent).toContain("not on the card now");
    expect(keys()).toEqual(["AAA", "BBB", "CCC", "DDD"]); // the supersector header lists none: its leaf does
  });

  it("lists a group with no names as a header alone", () => {
    setup(payload({ groups: [GROUP, { ...GROUP, key: "link:Empty", label: "Empty", kind: "link", members: [], stats: { ...GROUP.stats, names: 0 } }] }));
    const groups = screen.getAllByTestId("pt-group");
    expect(groups).toHaveLength(2);
    expect(groups[1].textContent).toContain("0 names");
  });
});

describe("PeerTable — sorting", () => {
  it("sorts a column on a click, flips it on a second, and puts a blank last in either direction", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /^Cap/ }));
    expect(keys()).toEqual(["BBB", "AAA", "CCC", "DDD"]); // $1B, $250M, then the two blanks
    expect(header(/^Cap/)).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(screen.getByRole("button", { name: /^Cap/ }));
    expect(keys()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    expect(header(/^Cap/)).toHaveAttribute("aria-sort", "ascending");
  });

  it("sorts within each group and keeps the groups in the server's order", async () => {
    const second = { ...GROUP, key: "type:bank", label: "bank", members: ["ccc2", "ddd2"] };
    const first = { ...GROUP, members: ["aaa", "bbb"] };
    setup(
      payload({
        groups: [first, second],
        companies: [AAA, BBB, { ...CCC, key: "ccc2", ticker: "CCC2", cap: { ...AAA.cap, value: 5e6, tier: "nano" } }, { ...DDD, key: "ddd2", ticker: "DDD2", cap: { ...AAA.cap, value: 9e6, tier: "nano" } }],
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^Cap/ }));
    expect(keys()).toEqual(["BBB", "AAA", "DDD2", "CCC2"]);
    expect(screen.getAllByTestId("pt-group")).toHaveLength(2);
  });

  it("sorts the vs-SPY column largest first, the blanks last, and returns to the group order", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /^30D vs SPY/ }));
    expect(keys()).toEqual(["AAA", "CCC", "BBB", "DDD"]); // +6.0, +1.0, -6.0, then the flagged blank
    await userEvent.click(screen.getByRole("button", { name: "back to group order" }));
    expect(keys()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    expect(screen.queryByRole("button", { name: "back to group order" })).toBeNull();
  });

  it("sorts the company column by ticker and the runway shortest first", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Company" }));
    expect(keys()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
    await userEvent.click(screen.getByRole("button", { name: /^Company/ }));
    expect(keys()).toEqual(["DDD", "CCC", "BBB", "AAA"]);
    await userEvent.click(screen.getByRole("button", { name: /^Runway/ }));
    expect(keys()).toEqual(["DDD", "AAA", "BBB", "CCC"]); // 6.0, 12.0, cash-positive, then the blank
  });
});

describe("PeerTable — the filters", () => {
  it("finds a ticker or a name and says what is in view", async () => {
    setup();
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a ticker or company" }), "bol");
    expect(keys()).toEqual(["BBB"]);
    expect(screen.getByText("showing 1 of 4 companies after the filters")).toBeInTheDocument();
    expect(screen.getByTestId("pt-group").textContent).toContain("1 shown");
    await userEvent.click(screen.getByRole("button", { name: "clear filters" }));
    expect(keys()).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "clear filters" })).toBeNull();
    expect(screen.getByText("4 companies")).toBeInTheDocument();
  });

  it("narrows to cap tiers (several at once) and to the companies with no cap", async () => {
    setup();
    const group = screen.getByRole("group", { name: "Cap tier" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["micro", "small", "no cap"]);
    await userEvent.click(within(group).getByRole("button", { name: "micro" }));
    expect(keys()).toEqual(["AAA"]);
    expect(within(group).getByRole("button", { name: "micro" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(group).getByRole("button", { name: "small" }));
    expect(keys()).toEqual(["AAA", "BBB"]);
    await userEvent.click(within(group).getByRole("button", { name: "micro" }));
    await userEvent.click(within(group).getByRole("button", { name: "small" }));
    await userEvent.click(within(group).getByRole("button", { name: "no cap" }));
    expect(keys()).toEqual(["CCC", "DDD"]);
  });

  it("leaves out the names that never count, and counts the figures over the names that do", async () => {
    setup();
    await userEvent.click(screen.getByRole("checkbox", { name: "Counting names only" }));
    expect(keys()).toEqual(["AAA", "BBB", "CCC"]);
    // the group's figures and the columns' coverage follow the toggle (the server sends both readings)
    const g = screen.getByTestId("pt-group");
    expect(g.textContent).toContain("3 names");
    expect(g.textContent).toContain("+5.0%median of 2 with a prior year");
    expect(headerCoverage(/^Cap/)).toBe("2 of 3");
    expect(header(/^Cap/).querySelector(".pt-cov")).toHaveAttribute("title", "2 of 3 names state this. Blank: 1 awaiting shares.");
    expect(screen.getByText(/count the names that count/)).toBeInTheDocument();
  });

  it("keeps only the names with a Workbench fact", async () => {
    setup();
    await userEvent.click(screen.getByRole("checkbox", { name: "Only names with a Workbench fact" }));
    expect(keys()).toEqual(["AAA", "BBB"]);
  });

  it("keeps only the names with revenue when some have none", async () => {
    const bare = { ...CCC, key: "nrv", ticker: "NRV", revenue: { value: null, period_end: null, gap: "no_revenue" as const } };
    setup(payload({ companies: [AAA, bare], groups: [{ ...GROUP, members: [AAA.key, bare.key] }] }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Only names with revenue" }));
    expect(keys()).toEqual(["AAA"]);
  });

  it("says so when no company passes", async () => {
    setup();
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a ticker or company" }), "nonesuch");
    expect(screen.getByText("No company passes the filters.")).toBeInTheDocument();
    expect(lines()).toHaveLength(0);
    expect(screen.getByTestId("pt-group")).toBeInTheDocument(); // the group header stays: nothing vanishes
  });

  it("leaves off every control that cannot tell the companies apart", () => {
    setup(payload({ companies: [AAA], groups: [{ ...GROUP, members: [AAA.key] }], silent_only: 0 }));
    expect(screen.queryByRole("searchbox")).toBeNull(); // one company
    expect(screen.queryByRole("group", { name: "Cap tier" })).toBeNull(); // one tier
    expect(screen.queryByRole("checkbox", { name: "Counting names only" })).toBeNull(); // none is silent-only
    expect(screen.queryByRole("checkbox", { name: "Only names with revenue" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Only names with a Workbench fact" })).toBeNull();
  });
});

describe("PeerTable — the scope control", () => {
  it("opens on the server's default leaf with every scope offered and the picks counted", () => {
    setup();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Business type",
      "Supersector",
      "Card",
      "All live cards",
    ]);
    expect(screen.getByRole("tab", { name: "Business type" })).toHaveAttribute("aria-selected", "true");
    const select = screen.getByRole("combobox", { name: "Business type" });
    expect(select).toHaveValue("miner");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "miner · 4",
      "bank · 1",
      "Unclassified · 1",
    ]);
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "business_type", "miner");
    expect(screen.getByText("6 companies on 2 counted cards")).toBeInTheDocument();
  });

  it("reads another business type, a supersector and a card when picked, and every card for All live cards", async () => {
    setup();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Business type" }), "bank");
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "business_type", "bank");
    await userEvent.click(screen.getByRole("tab", { name: "Supersector" }));
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "supersector", "materials");
    expect([...screen.getByRole("combobox", { name: "Supersector" }).querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Materials · 4",
      "Financials · 1",
      "Unclassified · 1",
    ]);
    await userEvent.click(screen.getByRole("tab", { name: "Card" }));
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "card", "t-batt");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Card" }), "t-grid");
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "card", "t-grid");
    await userEvent.click(screen.getByRole("tab", { name: "All live cards" }));
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "all", null);
    expect(screen.queryByRole("combobox", { name: /Business type|Supersector|Card/ })).toBeNull(); // nothing to pick
    expect(screen.getByRole("tab", { name: "All live cards" })).toHaveAttribute("aria-selected", "true");
  });

  it("falls back to the default when the picked leaf is not on the rosters at this as-of", () => {
    h.scopes.mockReturnValue(ok(scopesPayload({ business_types: [{ key: "bank", companies: 3 }], default_key: "bank" })));
    h.table.mockImplementation(() => ok(payload({ key: "bank" })));
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(h.table).toHaveBeenLastCalledWith("2026-10-28", "business_type", "bank");
  });

  it("grays a scope with nothing to pick, still there, and cannot be opened", () => {
    setup(payload(), scopesPayload({ cards: [] }));
    const card = screen.getByRole("tab", { name: "Card" });
    expect(card).toBeDisabled();
    expect(card).toHaveClass("zero");
  });

  it("groups a card by link and shows the links in place of the card count", () => {
    const grid = payload({
      scope: "card",
      key: "t-grid",
      scope_name: "Grid",
      groups: [
        { ...GROUP, key: "link:Turbines", label: "Turbines", kind: "link", members: ["aaa"] },
        { ...GROUP, key: "link:Cables", label: "Cables", kind: "link", members: ["bbb"] },
        { ...GROUP, key: "silent:Context", label: "Context", kind: "silent", members: ["ddd"] },
      ],
      companies: [AAA, BBB, DDD],
    });
    h.scopes.mockReturnValue(ok(scopesPayload()));
    h.table.mockImplementation((_asof: string, kind: string) => ok(kind === "card" ? grid : payload()));
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    return userEvent.click(screen.getByRole("tab", { name: "Card" })).then(() => {
      expect(screen.getAllByTestId("pt-group").map((g) => g.querySelector(".pt-group-label")?.textContent)).toEqual([
        "Turbines",
        "Cables",
        "Context",
      ]);
      expect(screen.getByRole("columnheader", { name: /^Links/ })).toBeInTheDocument();
      expect(screen.queryByRole("columnheader", { name: /^Cards/ })).toBeNull();
      expect(lines()[0].querySelector(".pt-links")?.textContent).toBe("Turbines");
      expect(within(lines()[0]).queryByRole("button", { name: /card/ })).toBeNull();
      expect(screen.getByText(/^Card: Grid · /)).toBeInTheDocument();
    });
  });
});

describe("PeerTable — the work and the notes", () => {
  it("says what the table rests on, in quiet lines", () => {
    setup();
    expect(screen.getByText("as of Oct 28, 2026 · as it was public by Oct 28, 2026 · cached")).toBeInTheDocument();
    expect(
      screen.getByText("Business type: miner · 2 live cards counted · 1 not yet created · 2 cards hold a name in scope"),
    ).toBeInTheDocument();
    expect(screen.getByText("4 companies · 6 roster rows · 1 silent only (Context or Bench)")).toBeInTheDocument();
    expect(screen.getByText(/^30D vs SPY: 30 trading days of closes, the company minus SPY/)).toBeInTheDocument();
  });

  it("lists every column's coverage on expand", async () => {
    setup();
    await userEvent.click(screen.getByText("column coverage — every blank counted by reason"));
    expect(screen.getByText("Cap: 2 of 4 — blank: 1 flagged, 1 awaiting shares")).toBeInTheDocument();
    expect(screen.getAllByText(/^[A-Za-z0-9 ]+: \d+ of \d+/).length).toBeGreaterThanOrEqual(COVERAGE.length);
  });

  it("names the flagged price histories through the shared note", async () => {
    setup();
    const summary = screen.getByText("1 name left out for a flagged price history");
    await userEvent.click(summary);
    expect(screen.getByText(MIXED)).toBeInTheDocument();
  });

  it("counts the unresolved roster rows and lists them on expand, never as a row", async () => {
    setup(
      payload({
        unresolved: [{ ticker: "ZZZ", thesis_id: "t-grid", thesis_name: "Grid" }],
        unresolved_rows: 1,
        roster_fallback_cards: 2,
      }),
    );
    const summary = screen.getByText("1 roster row has no resolved security, so no reading can be attached and it is not a row");
    await userEvent.click(summary);
    expect(screen.getByText("ZZZ · Grid")).toBeInTheDocument();
    expect(keys()).not.toContain("ZZZ");
    expect(screen.getByText(/2 counted cards have no roster snapshot by this as-of/)).toBeInTheDocument();
  });

  it("leaves those notes off when they do not apply", () => {
    setup(payload({ flagged: [], unresolved: [], unresolved_rows: 0, roster_fallback_cards: 0 }));
    expect(screen.queryByText(/left out for a flagged price history/)).toBeNull();
    expect(screen.queryByText(/no resolved security/)).toBeNull();
    expect(screen.queryByText(/no roster snapshot/)).toBeNull();
  });

  it("states the known limits and the honest notes", () => {
    setup();
    expect(screen.getByText(/^Approximate market cap: the latest close × the latest shares fact/)).toBeInTheDocument();
    expect(screen.getByText(/the blanks are the to-do list, counted per column/)).toBeInTheDocument();
    expect(screen.getByText(/Short interest is not stored/)).toBeInTheDocument();
    expect(screen.getByText(/as the Cockpit's column screens them/)).toBeInTheDocument();
    expect(screen.getByText(/Group figures are medians with equal weight and no score/)).toBeInTheDocument();
  });

  it("counts the companies in view against the whole", () => {
    setup();
    expect(screen.getByText("4 companies")).toBeInTheDocument();
  });
});

describe("PeerTable — states", () => {
  it("says it is loading the scopes", () => {
    h.scopes.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    h.table.mockReturnValue(ok(undefined));
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Loading the scopes…")).toBeInTheDocument();
  });

  it("says so when it cannot load", () => {
    h.scopes.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    h.table.mockReturnValue(ok(undefined));
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load the Peer Table.")).toBeInTheDocument();
  });

  it("explains an as-of with no company on any counted card and draws no control or table", () => {
    h.scopes.mockReturnValue(
      ok(
        scopesPayload({
          companies: 0,
          business_types: [],
          supersectors: [],
          cards: [],
          default_scope: null,
          default_key: null,
          cards_counted: 1,
        }),
      ),
    );
    h.table.mockReturnValue(ok(undefined));
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/No company is on a card counted at this as-of \(1 card counted\)/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("says the first load is slow while the table loads, and that All live cards can take a minute", async () => {
    h.scopes.mockReturnValue(ok(scopesPayload()));
    h.table.mockReturnValue({ data: undefined, isLoading: true, isFetching: true, error: null });
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "All live cards" }));
    expect(screen.getByText(/All live cards reads every company on every counted card/)).toBeInTheDocument();
  });

  it("says so when one scope cannot load, with the scope control still there", () => {
    h.scopes.mockReturnValue(ok(scopesPayload()));
    h.table.mockReturnValue({ data: undefined, isLoading: false, isFetching: false, error: new Error("x") });
    render(<PeerTable asof="2026-10-28" onSelectThesis={vi.fn()} />);
    expect(screen.getByText("Could not load this scope.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Business type" })).toBeInTheDocument();
  });

  it("explains an empty scope and draws no table", () => {
    setup(payload({ companies: [], groups: [], coverage: [], coverage_counting: [], silent_only: 0, flagged: [] }));
    expect(screen.getByText(/No company in this scope/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("keeps the previous table on screen and says so while another scope or as-of loads", () => {
    setup(payload({ asof: "2026-10-21" }), scopesPayload(), { isFetching: true });
    expect(lines()).toHaveLength(4);
    expect(screen.getByText(/the first load after a run or a basket edit can take a moment/)).toBeInTheDocument();
  });

  it("says it is updating (not slow) when the same view refreshes", () => {
    setup(payload(), scopesPayload(), { isFetching: true });
    expect(screen.getByText("updating…")).toBeInTheDocument();
  });
});
