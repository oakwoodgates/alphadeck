import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ThesisSummary } from "../../api/hooks";
import { ThesisSwitcher } from "../ThesisSwitcher";

const makeThesis = (id: string, name: string, over: Partial<ThesisSummary> = {}): ThesisSummary =>
  ({
    id,
    name,
    ticker: null,
    basket_size: 8,
    archived: false,
    meta_index: false,
    ...over,
  }) as ThesisSummary;

const THESES: ThesisSummary[] = [
  makeThesis("t-1", "Small-scale nuclear", { basket_size: 12 }),
  makeThesis("t-2", "Hims & Hers", { ticker: "HIMS", basket_size: 1 }),
  makeThesis("t-3", "Grid Storage", { basket_size: 15, meta_index: true }),
];

describe("ThesisSwitcher — top-bar thesis dropdown and steppers", () => {
  it("opens the thesis list on click, displays cached call states, and switches on selection", async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const qc = new QueryClient();
    qc.setQueryData(["call", "t-2", "2026-07-11"], { state: "armed" });

    render(
      <QueryClientProvider client={qc}>
        <ThesisSwitcher
          thesisId="t-1"
          currentName="Small-scale nuclear"
          currentBasketSize={12}
          stateClass="warm"
          asof="2026-07-11"
          theses={THESES}
          onSwitch={onSwitch}
        />
      </QueryClientProvider>,
    );

    const trigger = screen.getByRole("button", { name: /Switch thesis: Small-scale nuclear/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);

    expect(screen.getByRole("listbox", { name: "Theses" })).toBeInTheDocument();
    const himsOpt = screen.getByRole("option", { name: /Hims & Hers/ });
    expect(himsOpt).toHaveTextContent("Armed");
    expect(himsOpt).toHaveTextContent("HIMS");
    expect(screen.getByRole("option", { name: /Grid Storage/ })).toHaveTextContent("meta");

    await user.click(himsOpt);
    expect(onSwitch).toHaveBeenCalledWith("t-2");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("steps cleanly between theses with the prev/next buttons", async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();

    render(
      <ThesisSwitcher
        thesisId="t-1"
        currentName="Small-scale nuclear"
        currentBasketSize={12}
        stateClass="incub"
        asof="2026-07-11"
        theses={THESES}
        onSwitch={onSwitch}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Next thesis: Hims & Hers/ }));
    expect(onSwitch).toHaveBeenCalledWith("t-2");

    await user.click(screen.getByRole("button", { name: /Previous thesis: Grid Storage/ }));
    expect(onSwitch).toHaveBeenCalledWith("t-3");
  });

  it("shows a search box when there are 6+ theses and filters options by name or ticker", async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const many = [
      ...THESES,
      makeThesis("t-4", "Copper Supply", { basket_size: 9 }),
      makeThesis("t-5", "Defense Drones", { basket_size: 7 }),
      makeThesis("t-6", "Biotech GLP-1", { ticker: "VKTX", basket_size: 1 }),
    ];

    render(
      <ThesisSwitcher
        thesisId="t-1"
        currentName="Small-scale nuclear"
        currentBasketSize={12}
        stateClass="incub"
        asof="2026-07-11"
        theses={many}
        onSwitch={onSwitch}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Switch thesis/ }));
    const search = screen.getByRole("searchbox", { name: "Filter theses" });
    await user.type(search, "vktx");

    const opts = screen.getAllByRole("option");
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveTextContent("Biotech GLP-1");

    await user.keyboard("{Enter}");
    expect(onSwitch).toHaveBeenCalledWith("t-6");
  });
});
