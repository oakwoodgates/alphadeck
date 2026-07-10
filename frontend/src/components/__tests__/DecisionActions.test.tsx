import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the decisions query + the post mutation. The state-appropriate
// action row, the gate's friction copy, the forms, and the voided-row rendering are the REAL
// component under test.
const h = vi.hoisted(() => ({
  decisions: { data: undefined as unknown },
  mutate: vi.fn(),
  post: { isPending: false, isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useDecisions: () => h.decisions,
  usePostDecision: () => ({ mutate: h.mutate, ...h.post }),
}));

import type { CallCardResponse } from "../../api/hooks";
import { DecisionActions } from "../DecisionActions";

const TODAY = new Date().toISOString().slice(0, 10);

const base = {
  thesis_id: "t1",
  asof: TODAY,
  state: "warming",
  verdict: "not_yet",
  armed_security_id: null,
  armed_members: [],
  watch_members: [
    { security_id: "s-mu", ticker: "MU", triggers: [] },
    { security_id: "s-onto", ticker: "ONTO", triggers: [] },
  ],
} as unknown as CallCardResponse;

const armedCard = {
  ...base,
  state: "armed",
  verdict: "core_entry",
  armed_security_id: "s-mu",
  armed_members: [{ security_id: "s-mu", ticker: "MU", triggers: [] }],
} as unknown as CallCardResponse;

beforeEach(() => {
  h.mutate.mockReset();
  h.decisions = { data: undefined };
  h.post = { isPending: false, isError: false, error: null };
});

describe("DecisionActions — decision capture on the CallCard", () => {
  it("a not-yet state shows the GATE: friction copy + a logged override, and the take carries it", async () => {
    const user = userEvent.setup();
    render(<DecisionActions thesisId="t1" card={base} />);
    expect(screen.getByText(/withholding the go-signal/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Override — log an early entry/ }));
    // the v1 gate: the disagreement is named at the point of logging — display + record, no block
    expect(screen.getByText(/verdict is not-yet — logging this take as an override/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Log take" }));
    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate.mock.calls[0][0]).toMatchObject({ action: "take", decision_date: TODAY });
  });

  it("armed shows the loud act; the name select defaults to the platform's headline", async () => {
    const user = userEvent.setup();
    render(<DecisionActions thesisId="t1" card={armedCard} />);
    expect(screen.queryByText(/withholding the go-signal/)).toBeNull(); // no gate when armed

    await user.click(screen.getByRole("button", { name: /Act — log the fill/ }));
    expect(screen.queryByText(/logging this take as an override/)).toBeNull(); // acting WITH the call
    expect(screen.getByLabelText("decision name")).toHaveValue("s-mu"); // the headline pick

    await user.type(screen.getByLabelText("decision shares"), "100");
    await user.type(screen.getByLabelText("decision price"), "483");
    await user.click(screen.getByRole("button", { name: "Log take" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      action: "take",
      security_id: "s-mu",
      shares: 100,
      price: 483,
    });
  });

  it("managing offers Log exit → a close append", async () => {
    const user = userEvent.setup();
    render(
      <DecisionActions thesisId="t1" card={{ ...base, state: "managing" } as CallCardResponse} />,
    );
    await user.click(screen.getByRole("button", { name: "Log exit" }));
    await user.click(screen.getByRole("button", { name: "Log close" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({ action: "close" });
  });

  it("pass is quiet, available, and logs a reason", async () => {
    const user = userEvent.setup();
    render(<DecisionActions thesisId="t1" card={base} />);
    await user.click(screen.getByRole("button", { name: /Pass \(logged\)/ }));
    await user.type(screen.getByLabelText("decision reason"), "agreed, waiting for volume");
    await user.click(screen.getByRole("button", { name: "Log pass" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({
      action: "pass",
      reason: "agreed, waiting for volume",
    });
  });

  it("the log renders newest-first with the platform stance; voided rows grey but never vanish", () => {
    h.decisions = {
      data: [
        {
          id: "d2",
          action: "void",
          decision_date: TODAY,
          voids: "d1",
          voided: false,
          call_state: null,
          call_verdict: null,
        },
        {
          id: "d1",
          action: "take",
          decision_date: TODAY,
          shares: 10,
          price: 5,
          voided: true,
          call_state: "warming",
          call_verdict: "not_yet",
        },
      ],
    };
    render(<DecisionActions thesisId="t1" card={base} />);
    expect(screen.getByText("Decision log")).toBeInTheDocument();
    expect(screen.getByText("voided")).toBeInTheDocument(); // greyed tag — visible, not hidden
    expect(screen.getByText(/platform: not-yet/)).toBeInTheDocument(); // the gate's record, readable
    // the voided take offers no undo; the void row itself never does
    expect(screen.queryByRole("button", { name: /void this/ })).toBeNull();
  });

  it("undo appends a void pointing at the row (never a delete)", async () => {
    const user = userEvent.setup();
    h.decisions = {
      data: [
        {
          id: "d1",
          action: "take",
          decision_date: TODAY,
          voided: false,
          call_state: null,
          call_verdict: null,
        },
      ],
    };
    render(<DecisionActions thesisId="t1" card={base} />);
    await user.click(screen.getByRole("button", { name: "void this take" }));
    expect(h.mutate.mock.calls[0][0]).toMatchObject({ action: "void", voids: "d1" });
  });
});
