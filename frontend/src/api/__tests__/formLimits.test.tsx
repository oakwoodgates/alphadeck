import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/hooks", () => ({
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
}));

import { CatalystEditor, KillCriteriaEditor } from "../../cockpit/SpineListEditors";
import { AutoTextarea } from "../../workbench/AutoTextarea";
import { ThesisFields } from "../../workbench/ThesisFields";
import { LIMITS } from "../limits";

// The API refuses a value over its input limit with a 422 (security hardening PR 3); the forms stop the user at the
// same limit before anything is sent.
const maxOf = (el: HTMLElement): number => (el as HTMLInputElement | HTMLTextAreaElement).maxLength;

describe("form inputs carry the API's limits", () => {
  it("the thesis name and narrative", () => {
    render(<ThesisFields name="" narrative="" onName={() => {}} onNarrative={() => {}} />);
    expect(maxOf(screen.getByLabelText("thesis name"))).toBe(LIMITS.line);
    expect(maxOf(screen.getByLabelText("thesis narrative"))).toBe(LIMITS.narrative);
  });

  it("the catalyst calendar's text fields and a kill criterion", async () => {
    const user = userEvent.setup();
    render(<CatalystEditor thesisId="t1" catalysts={[]} />);
    await user.click(screen.getByRole("button", { name: /add catalysts/ }));
    expect(maxOf(screen.getByLabelText("catalyst label 1"))).toBe(LIMITS.line);
    expect(maxOf(screen.getByLabelText("catalyst kind 1"))).toBe(LIMITS.line);
    expect(maxOf(screen.getByLabelText("catalyst when label 1"))).toBe(LIMITS.line);

    render(<KillCriteriaEditor thesisId="t1" kills={[]} />);
    await user.click(screen.getByRole("button", { name: /add kill criteria/ }));
    expect(maxOf(screen.getByLabelText("kill criterion 1"))).toBe(LIMITS.prose);
  });

  it("the growing textarea passes a limit through", () => {
    render(<AutoTextarea value="" onChange={() => {}} ariaLabel="note" maxLength={LIMITS.prose} />);
    expect(maxOf(screen.getByLabelText("note"))).toBe(LIMITS.prose);
  });
});
