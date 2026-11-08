import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// A brand-new workspace has zero cards: GET /theses answers 200 with []. The Board must say so quietly and
// point at the Workbench's "+ New thesis", never read as an error.
const h = vi.hoisted(() => ({
  thesesQ: { data: [] as unknown[], isLoading: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => h.thesesQ,
  useDataFlags: () => ({ data: undefined }),
  useCalls: () => [],
  useSetArchived: () => ({ mutate: vi.fn(), isPending: false }),
  useSetMetaIndex: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { Board } from "../Board";

const noop = () => {};

beforeEach(() => {
  h.thesesQ = { data: [], isLoading: false, error: null };
});

describe("Board — an empty workspace", () => {
  it("shows one quiet note linking the Workbench, and no error", () => {
    render(<Board asof="2026-07-11" onSelect={noop} />);
    expect(screen.getByText(/No cards yet\./)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Workbench/ });
    expect(link).toHaveAttribute("href", "/workbench");
    expect(screen.queryByText(/API not reachable/)).toBeNull();
    expect(screen.getByText("Decision Queue")).toBeInTheDocument();
  });

  it("does not show the note while the list is still loading", () => {
    h.thesesQ = { data: [], isLoading: true, error: null };
    render(<Board asof="2026-07-11" onSelect={noop} />);
    expect(screen.queryByText(/No cards yet\./)).toBeNull();
  });

  it("does not show the note when cards exist", () => {
    h.thesesQ = {
      data: [{ id: "t-1", name: "one", ticker: null, basket_size: 1, narrative: "n", archived: false, meta_index: false }],
      isLoading: false,
      error: null,
    };
    render(<Board asof="2026-07-11" onSelect={noop} />);
    expect(screen.queryByText(/No cards yet\./)).toBeNull();
  });
});
