import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The network boundary, mocked: the runs LIST query + the on-demand LOAD mutation. A test drives the list
// (data / error) and the load result via `h`.
const h = vi.hoisted(() => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  runs: undefined as any,
  runsIsError: false,
  load: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  useThesisRuns: () => ({ data: h.runs, isError: h.runsIsError }),
  useLoadThesisRun: () => ({ mutateAsync: h.load, isPending: false, isError: false, error: null }),
}));

import { RunPicker } from "../RunPicker";

const RUN = {
  run_id: "20260706T120000Z-job1",
  written_at: "2026-07-06T12:00:00+00:00",
  job_id: "job1",
  placement_count: 3,
  segment_count: 2,
};

describe("RunPicker — load a saved draft run", () => {
  beforeEach(() => {
    h.runs = undefined;
    h.runsIsError = false;
    h.load = vi.fn();
  });

  it("renders NOTHING when the runs endpoint errors (the loader flag is off → 404)", () => {
    h.runsIsError = true;
    const { container } = render(<RunPicker thesisId="t1" onLoad={vi.fn()} />);
    expect(container).toBeEmptyDOMElement(); // the single-flag off-switch: no chrome, no dead control
  });

  it("renders NOTHING when the thesis has no saved runs", () => {
    h.runs = [];
    const { container } = render(<RunPicker thesisId="t1" onLoad={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists runs and, on Load, fetches the run and hands the draft to onLoad", async () => {
    const user = userEvent.setup();
    const draft = { thesis_id: "t1", segments: [], placements: [], report: null };
    h.runs = [RUN];
    h.load = vi.fn().mockResolvedValue(draft);
    const onLoad = vi.fn();
    render(<RunPicker thesisId="t1" onLoad={onLoad} />);

    // the run shows as a readable option (date + counts); no draft/API draft call is involved
    expect(screen.getByRole("option", { name: /3 placed · 2 links/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Load run/ }));

    expect(h.load).toHaveBeenCalledWith(RUN.run_id); // fetched THAT run by id
    await waitFor(() => expect(onLoad).toHaveBeenCalledWith(draft)); // seeds the editor with the loaded draft
  });
});
