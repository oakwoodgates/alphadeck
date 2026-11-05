import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// "Add a name" on a FUND: the typeahead's match carries the master row's instrument_kind, so a picked fund
// lands in the fixed, silent ETFs link (where every fund sits and never counts toward the call) instead of
// starting unplaced — where it would vote and the promote would refuse it. A company still starts unplaced.
const h = vi.hoisted(() => ({
  matches: [
    { security_id: "s-fnd", ticker: "FNDA", name: "A Theme Fund", cik: null, instrument_kind: "etf" },
    { security_id: "s-co", ticker: "FNDCO", name: "Fund Co Inc.", cik: "0009990009", instrument_kind: "equity" },
  ],
}));

vi.mock("../../api/hooks", () => ({
  useResolveSecurities: (q: string) => ({ data: q?.trim() ? h.matches : [], isFetching: false }),
}));

import { AddName } from "../AddName";

async function pickAndAdd(ticker: RegExp) {
  const user = userEvent.setup();
  const onAdd = vi.fn();
  render(<AddName existingKeys={new Set()} onAdd={onAdd} />);
  await user.type(screen.getByLabelText("search securities"), "fnd");
  await user.click(await screen.findByRole("button", { name: ticker }));
  await user.click(screen.getByRole("button", { name: "add to basket" }));
  expect(onAdd).toHaveBeenCalledTimes(1);
  return onAdd.mock.calls[0][0];
}

describe("AddName — a fund match lands in ETFs", () => {
  it("a picked FUND is added in the ETFs link", async () => {
    const m = await pickAndAdd(/^FNDA/);
    expect(m.security_id).toBe("s-fnd");
    expect(m.segment).toBe("ETFs");
    expect(m.signed_off).toBe(true); // a hand-add is the endorsement, fund or not
  });

  it("a picked COMPANY still starts unplaced", async () => {
    const m = await pickAndAdd(/^FNDCO/);
    expect(m.security_id).toBe("s-co");
    expect(m.segment).toBeNull();
  });
});
