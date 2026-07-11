import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  ratify: { isPending: false, isError: false, isSuccess: false, error: null as unknown, data: undefined as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useRatifyFact: () => ({ mutate: h.mutate, ...h.ratify }),
}));

import { CatalystFactForm } from "../CatalystFactForm";

beforeEach(() => {
  h.mutate.mockReset();
  h.ratify = { isPending: false, isError: false, isSuccess: false, error: null, data: undefined };
});

describe("CatalystFactForm — the hand-authored conviction fact (Key-1)", () => {
  it("collapsed by default; the submit gates on label AND citation (an uncited catalyst is a bare claim)", async () => {
    const user = userEvent.setup();
    render(<CatalystFactForm securityId="s-1" />);
    expect(screen.queryByLabelText("catalyst label")).toBeNull(); // quiet until opened

    await user.click(screen.getByRole("button", { name: /log a catalyst/ }));
    const submit = screen.getByRole("button", { name: "Log the catalyst" });
    expect(submit).toBeDisabled(); // nothing authored yet

    await user.type(screen.getByLabelText("catalyst label"), "10-year offtake signed");
    expect(submit).toBeDisabled(); // label alone is NOT enough — the citation is the provenance
    await user.type(screen.getByLabelText("catalyst citation"), "https://ex.com/pr");
    expect(submit).toBeEnabled();
  });

  it("submits the ratify union's catalyst variant with source='ratified'", async () => {
    const user = userEvent.setup();
    render(<CatalystFactForm securityId="s-1" />);
    await user.click(screen.getByRole("button", { name: /log a catalyst/ }));
    await user.selectOptions(screen.getByLabelText("catalyst type"), "gov_funding");
    await user.selectOptions(screen.getByLabelText("catalyst grade"), "flip");
    await user.type(screen.getByLabelText("catalyst label"), "DOE award");
    await user.type(screen.getByLabelText("catalyst citation"), "https://ex.com/doe");
    await user.click(screen.getByRole("button", { name: "Log the catalyst" }));

    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      fact_type: "catalyst",
      security_id: "s-1",
      catalyst_type: "gov_funding",
      grade: "flip",
      label: "DOE award",
      source: "ratified",
      source_ref: "https://ex.com/doe",
      horizon_end: null,
    });
  });

  it("shows the done state after a successful catalyst ratify", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<CatalystFactForm securityId="s-1" />);
    await user.click(screen.getByRole("button", { name: /log a catalyst/ }));
    h.ratify = { ...h.ratify, isSuccess: true, data: { fact_id: "f1", fact_type: "catalyst" } };
    rerender(<CatalystFactForm securityId="s-1" />);
    expect(screen.getByText(/catalyst logged — a Key-1 conviction fact/)).toBeInTheDocument();
  });
});
