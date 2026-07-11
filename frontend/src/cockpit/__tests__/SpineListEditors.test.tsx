import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  putCats: vi.fn(),
  putKills: vi.fn(),
  state: { isPending: false, isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  usePutCatalysts: () => ({ mutate: h.putCats, ...h.state }),
  usePutKillCriteria: () => ({ mutate: h.putKills, ...h.state }),
}));

import { CatalystEditor, KillCriteriaEditor } from "../SpineListEditors";

beforeEach(() => {
  h.putCats.mockReset();
  h.putKills.mockReset();
});

describe("SpineListEditors — the Cockpit's authored lists", () => {
  it("offers the authoring entry point EVEN AT ZERO (empty sections used to vanish)", async () => {
    const user = userEvent.setup();
    render(<CatalystEditor thesisId="t1" catalysts={[]} />);
    await user.click(
      screen.getByRole("button", { name: /add catalysts \(the events you're watching\)/ }),
    );
    expect(screen.getByLabelText("catalyst label 1")).toBeInTheDocument(); // a blank starter row
  });

  it("saves the calendar: trimmed labels, blanks dropped, empty optionals as null", async () => {
    const user = userEvent.setup();
    render(<CatalystEditor thesisId="t1" catalysts={[]} />);
    await user.click(screen.getByRole("button", { name: /add catalysts/ }));
    await user.type(screen.getByLabelText("catalyst label 1"), "  MU FQ4 earnings  ");
    await user.type(screen.getByLabelText("catalyst date 1"), "2026-09-24");
    await user.click(screen.getByRole("button", { name: "+ row" })); // a second, left blank -> dropped
    await user.click(screen.getByRole("button", { name: "Save calendar" }));

    expect(h.putCats).toHaveBeenCalledTimes(1);
    expect(h.putCats.mock.calls[0][0]).toEqual([
      { label: "MU FQ4 earnings", kind: null, when_date: "2026-09-24", when_label: null },
    ]);
  });

  it("edits an existing calendar in place and removes a row reversibly-before-save", async () => {
    const user = userEvent.setup();
    render(
      <CatalystEditor
        thesisId="t1"
        catalysts={[
          { id: "c1", label: "NRC decision", kind: "regulatory", when_date: null, when_label: "~Q3" },
          { id: "c2", label: "stale event", kind: null, when_date: null, when_label: null },
        ]}
      />,
    );
    await user.click(screen.getByRole("button", { name: /edit the calendar/ }));
    expect(screen.getByLabelText("catalyst label 1")).toHaveValue("NRC decision");
    await user.click(screen.getByRole("button", { name: "remove catalyst 2" }));
    await user.click(screen.getByRole("button", { name: "Save calendar" }));
    expect(h.putCats.mock.calls[0][0]).toEqual([
      { label: "NRC decision", kind: "regulatory", when_date: null, when_label: "~Q3" },
    ]);
  });

  it("kill criteria: authors, trims, and drops blanks", async () => {
    const user = userEvent.setup();
    render(<KillCriteriaEditor thesisId="t1" kills={[]} />);
    await user.click(screen.getByRole("button", { name: /add kill criteria/ }));
    await user.type(
      screen.getByLabelText("kill criterion 1"),
      " DRAM contract prices roll over two consecutive quarters ",
    );
    await user.click(screen.getByRole("button", { name: "Save kill criteria" }));
    expect(h.putKills.mock.calls[0][0]).toEqual([
      { text: "DRAM contract prices roll over two consecutive quarters" },
    ]);
  });
});
