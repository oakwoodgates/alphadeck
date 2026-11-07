import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked. The meta index switch beside the archive ✕, the quiet tag on the card,
// and the one-click reversibility (WB#1) are the real component under test.
const h = vi.hoisted(() => ({
  theses: [] as unknown[],
  setMetaIndex: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: h.theses, isLoading: false, error: null }),
  useDataFlags: () => ({ data: undefined }),
  useCalls: (subjects: { id: string }[]) =>
    subjects.map(({ id }) => ({
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
  useSetArchived: () => ({ mutate: vi.fn(), isPending: false }),
  useSetMetaIndex: () => ({ mutate: h.setMetaIndex, isPending: false }),
}));

import { Board } from "../Board";

const noop = () => {};

beforeEach(() => {
  h.setMetaIndex.mockReset();
  h.theses = [
    { id: "t-norm", name: "normal one", ticker: null, basket_size: 3, narrative: "n", archived: false, meta_index: false },
    { id: "t-meta", name: "sector map", ticker: null, basket_size: 40, narrative: "n", archived: false, meta_index: true },
  ];
});

const cardOf = (name: string) =>
  [...document.querySelectorAll(".card-wrap")].find((w) => w.textContent?.includes(name)) as HTMLElement;

describe("Board — the meta index switch", () => {
  it("shows the quiet lowercase tag only on a meta index card (WB#3: nothing when off)", () => {
    render(<Board asof="2026-07-11" onSelect={noop} />);
    expect(cardOf("sector map").querySelector(".card .meta-tag")?.textContent).toBe("meta index");
    expect(cardOf("normal one").querySelector(".meta-tag")).toBeNull();
    expect(document.querySelectorAll(".meta-tag")).toHaveLength(1);
  });

  it("the switch sits beside the archive ✕ and says its state", () => {
    render(<Board asof="2026-07-11" onSelect={noop} />);
    const off = screen.getByLabelText("mark normal one as a meta index card");
    const on = screen.getByLabelText("make sector map a normal card again");
    expect(off).toHaveAttribute("aria-pressed", "false");
    expect(on).toHaveAttribute("aria-pressed", "true");
    expect(off.getAttribute("title")).toContain("its names never count toward the call");
    // a sibling of the card (never nested in the card's own <button>), beside the archive ✕
    expect(off.parentElement).toBe(screen.getByLabelText("archive normal one").parentElement);
    expect(off.closest(".card")).toBeNull();
  });

  it("one click marks a card, the same control unmarks it — both through the mutation", async () => {
    const user = userEvent.setup();
    render(<Board asof="2026-07-11" onSelect={noop} />);

    await user.click(screen.getByLabelText("mark normal one as a meta index card"));
    expect(h.setMetaIndex).toHaveBeenCalledWith({ thesisId: "t-norm", metaIndex: true });

    await user.click(screen.getByLabelText("make sector map a normal card again"));
    expect(h.setMetaIndex).toHaveBeenCalledWith({ thesisId: "t-meta", metaIndex: false });
  });

  it("toggling never opens the Cockpit (the switch is not the card)", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Board asof="2026-07-11" onSelect={onSelect} />);
    await user.click(screen.getByLabelText("mark normal one as a meta index card"));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
