import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked. The Board's partition (active columns vs the collapsed archived
// section), the sibling ✕, and the restore flow are the real component under test.
const h = vi.hoisted(() => ({
  theses: [] as unknown[],
  setArchived: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: h.theses, isLoading: false, error: null }),
  useCalls: (ids: string[]) =>
    ids.map((id) => ({
      data: {
        thesis_id: id,
        state: "incubating",
        verdict: "not_yet",
        key_conviction: { turned: false, detail: "" },
        key_confirmation: { turned: false, detail: "" },
        armed_members: [],
        watch_members: [],
      },
      isLoading: false,
    })),
  useSetArchived: () => ({ mutate: h.setArchived, isPending: false }),
}));

import { Board } from "../Board";

const noop = () => {};

beforeEach(() => {
  h.setArchived.mockReset();
  h.theses = [
    { id: "t-live", name: "live one", ticker: null, basket_size: 3, narrative: "n", archived: false },
    { id: "t-park", name: "parked one", ticker: null, basket_size: 9, narrative: "n", archived: true },
  ];
});

describe("Board — archive (hygiene, never delete)", () => {
  it("partitions: active in the columns, archived in the collapsed section (visible, not vanished)", () => {
    render(<Board asof="2026-07-11" onAsofChange={noop} onSelect={noop} onOpenWorkbench={noop} />);
    expect(screen.getByText("live one")).toBeInTheDocument(); // a column card
    expect(screen.getByText("Archived (1)")).toBeInTheDocument(); // the quiet section
    expect(screen.getByText("parked one")).toBeInTheDocument(); // greyed row inside it, NOT a card
    expect(screen.queryByLabelText("archive parked one")).toBeNull(); // no ✕ on an archived row
  });

  it("the sibling ✕ archives; restore un-archives — both through the mutation", async () => {
    const user = userEvent.setup();
    render(<Board asof="2026-07-11" onAsofChange={noop} onSelect={noop} onOpenWorkbench={noop} />);

    await user.click(screen.getByLabelText("archive live one"));
    expect(h.setArchived).toHaveBeenCalledWith({ thesisId: "t-live", archived: true });

    await user.click(screen.getByLabelText("restore parked one"));
    expect(h.setArchived).toHaveBeenCalledWith({ thesisId: "t-park", archived: false });
  });

  it("archived theses get NO call computation (their ids never reach useCalls)", () => {
    // useCalls above derives its return from the ids it receives: if the archived id were passed,
    // a second incubating card named "parked one" would render in the columns — assert it doesn't
    render(<Board asof="2026-07-11" onAsofChange={noop} onSelect={noop} onOpenWorkbench={noop} />);
    const cards = document.querySelectorAll(".card");
    expect(cards).toHaveLength(1); // only the live one computed into a column card
  });
});
