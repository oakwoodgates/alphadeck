import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Radar } from "../Radar";

// The page under test with the API layer stubbed — the tape render, the honest states, and the
// act-on wiring (attach ⇄ detach, the catalyst prefill) are what's asserted; hook internals have
// the api suite.

const h = vi.hoisted(() => ({
  q: { isLoading: false, error: null as unknown, data: null as unknown },
  attach: { isPending: false, error: null as unknown, mutate: vi.fn() },
  catalyst: { isPending: false, error: null as unknown, mutate: vi.fn() },
}));

vi.mock("../../api/hooks", () => ({
  useRadarSpac: () => h.q,
  useSpacAttach: () => h.attach,
  useSpacCatalyst: () => h.catalyst,
}));

const EVENT = {
  cik: "0000002222",
  ticker: "LIVE",
  company_name: "Live Shell Acquisition Corp",
  security_id: "sid-2",
  form: "425",
  items: null,
  filed: "2026-08-04",
  accession: "acc-2201",
  url: "https://www.sec.gov/x-index.htm",
  deal_state: "announced",
  in_basket_of: [],
  matches: [
    {
      thesis_id: "t-1",
      thesis_name: "Rainbow Rush",
      signal_terms: ["psilocybin"],
      broad_terms: ["real-world assets"],
      truncated: false,
    },
  ],
};

const NOMATCH_EVENT = {
  ...EVENT,
  cik: "0000001111",
  ticker: "DEAD",
  company_name: "Dead Shell Corp",
  security_id: "sid-1",
  form: "8-K",
  items: ["1.02"],
  accession: "acc-1102",
  deal_state: "terminated",
  matches: [],
};

const noop = () => {};
const renderPage = () =>
  render(
    <Radar onBack={noop} onOpenWorkbench={noop} onOpenScoreboard={noop} onOpenAdmin={noop} />,
  );

beforeEach(() => {
  h.q = {
    isLoading: false,
    error: null,
    data: { events: [EVENT, NOMATCH_EVENT], window_days: 90, shells_known: 12 },
  };
  h.attach.mutate.mockClear();
  h.catalyst.mutate.mockClear();
});

describe("Radar — the tape", () => {
  it("renders rows with state chips, form+items, and the filing link", () => {
    renderPage();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("deal announced")).toBeInTheDocument();
    // the Rev 2 pair made visible: the dead deal reads terminated, never announced
    expect(screen.getByText("deal terminated")).toBeInTheDocument();
    expect(screen.getByText("8-K · 1.02")).toBeInTheDocument();
    const links = screen.getAllByText("filing ↗");
    expect(links[0]).toHaveAttribute("href", "https://www.sec.gov/x-index.htm");
    expect(screen.getByText(/12 shells known/)).toBeInTheDocument();
  });

  it("controls render ONLY on matched rows (honest loudness) and attach fires with the pair", async () => {
    const user = userEvent.setup();
    renderPage();
    // the unmatched row carries no buttons at all
    expect(screen.getAllByRole("button", { name: /add to/ })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "+ add to Rainbow Rush" }));
    expect(h.attach.mutate).toHaveBeenCalledWith({
      thesis_id: "t-1",
      cik: "0000002222",
      detach: false,
    });
    await user.click(screen.getByRole("button", { name: "+ vote catalyst" }));
    expect(h.catalyst.mutate).toHaveBeenCalledWith({
      thesisId: "t-1",
      label: "LIVE combination vote",
    });
  });

  it("an in-basket match shows the reversible remove (detach) instead of add", async () => {
    const user = userEvent.setup();
    h.q.data = {
      events: [{ ...EVENT, in_basket_of: ["t-1"] }],
      window_days: 90,
      shells_known: 1,
    };
    renderPage();
    await user.click(screen.getByRole("button", { name: "✓ in basket — remove" }));
    expect(h.attach.mutate).toHaveBeenCalledWith({
      thesis_id: "t-1",
      cik: "0000002222",
      detach: true,
    });
  });

  it("renders the honest empty state", () => {
    h.q.data = { events: [], window_days: 90, shells_known: 0 };
    renderPage();
    expect(screen.getByText(/No transition filings in the window/)).toBeInTheDocument();
  });
});
