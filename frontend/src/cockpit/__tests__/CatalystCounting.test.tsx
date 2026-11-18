import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  count: vi.fn(),
  putCats: vi.fn(),
  state: { isPending: false, isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useFundLaunches: () => ({ data: undefined, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: h.putCats, ...h.state }),
  usePutKillCriteria: () => ({ mutate: vi.fn(), ...h.state }),
  useCatalystCounting: () => ({ mutate: h.count, ...h.state }),
}));

import { CatalystCountingRow } from "../CatalystCounting";
import { CatalystEditor } from "../SpineListEditors";

const members = [
  { ticker: "OKLO", role: "core", security_id: "s-oklo", segment: "Reactors" },
  { ticker: "SMR", role: "core", security_id: "s-smr", segment: "Reactors" },
  { ticker: "BWXT", role: "core", security_id: "s-bwxt", segment: "Context" },
] as never[];

const counting = {
  entry_id: "e1",
  counts: true,
  grade: "core",
  label: "NRC decision",
  members: [
    { security_id: "s-oklo", ticker: "OKLO" },
    { security_id: "s-smr", ticker: "SMR" },
  ],
  citation: "https://sec.gov/x",
  relevant_until: null,
  counts_from: "2026-11-18",
  saved_at: "2026-11-18T22:00:00Z",
} as never;

function row(over: Record<string, unknown> = {}) {
  return render(
    <CatalystCountingRow
      thesisId="t1"
      entryId="e1"
      label="NRC decision"
      counting={undefined}
      members={members}
      metaIndex={false}
      {...(over as object)}
    />,
  );
}

beforeEach(() => {
  h.count.mockReset();
  h.putCats.mockReset();
  h.state.isError = false;
  h.state.error = null;
  h.state.isPending = false;
});

describe("the counting form on a saved calendar row", () => {
  it("opens from 'Counts toward the call…' and says once that it counts from the night it is saved", async () => {
    const user = userEvent.setup();
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    expect(screen.getByText("Count this toward the call")).toBeInTheDocument();
    expect(
      screen.getAllByText("It counts from tonight, not from the date on the calendar. Earlier nights stay as they were."),
    ).toHaveLength(1);
  });

  it("switches on with every field and sends exactly the CatalystCountingIn body", async () => {
    const user = userEvent.setup();
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    await user.click(screen.getByLabelText("OKLO"));
    await user.click(screen.getByLabelText(/^Core: binding/));
    await user.type(screen.getByLabelText("Citation"), "0001193125-26-000001");
    await user.type(screen.getByLabelText("Relevant until (optional)"), "2027-03-01");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.count).toHaveBeenCalledTimes(1);
    expect(h.count.mock.calls[0][0]).toEqual({
      entryId: "e1",
      body: {
        counts: true,
        grade: "core",
        security_ids: ["s-oklo"],
        citation: "0001193125-26-000001",
        relevant_until: "2027-03-01",
      },
    });
  });

  it("offers only Flip with no company, and resets Core to Flip with a visible note when the last name goes", async () => {
    const user = userEvent.setup();
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    expect(screen.queryByLabelText(/^Core: binding/)).toBeNull();
    expect(screen.getByLabelText(/^Flip: provisional/)).toBeInTheDocument();
    expect(screen.getByText("A whole-card entry counts as Flip.")).toBeInTheDocument();
    await user.click(screen.getByLabelText("OKLO"));
    await user.click(screen.getByLabelText(/^Core: binding/));
    await user.click(screen.getByLabelText("OKLO")); // none again
    expect(screen.queryByLabelText(/^Core: binding/)).toBeNull();
    expect(screen.getByLabelText(/^Flip: provisional/)).toBeChecked();
    expect(screen.getByText(/no company is picked.*Flip/i)).toBeInTheDocument();
  });

  it("shows a silent-link name disabled with the plan's note, and offers no vote to it", async () => {
    const user = userEvent.setup();
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    expect(screen.getByLabelText("BWXT")).toBeDisabled();
    expect(screen.getByText("BWXT is in Context, so it has no vote; this entry does nothing for it.")).toBeInTheDocument();
  });

  it("sends nothing when a client check fails (no grade, empty citation)", async () => {
    const user = userEvent.setup();
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.count).not.toHaveBeenCalled();
    expect(screen.getByText("Pick a grade.")).toBeInTheDocument();
    await user.click(screen.getByLabelText(/^Flip: provisional/));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.count).not.toHaveBeenCalled();
    expect(screen.getByText("Add a citation: a link (https://…) or an SEC accession number.")).toBeInTheDocument();
  });

  it.each([
    "That company is not on this card.",
    "A whole-card entry can only be Flip.",
    "'Relevant until' must be after 2026-11-18, the night it would count from.",
    "Give the entry a label before it counts toward the call.",
  ])("shows the server's sentence as is: %s", async (sentence) => {
    const user = userEvent.setup();
    h.state.isError = true;
    h.state.error = { detail: sentence };
    row();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    expect(screen.getByText(sentence)).toBeInTheDocument();
  });

  it("a meta index card says nothing on it counts, and offers no form", () => {
    row({ metaIndex: true });
    expect(screen.getByText("This card is a meta index: nothing on it counts toward the call.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Counts toward the call…/ })).toBeNull();
  });
});

describe("a counting row", () => {
  it("shows its state in one quiet line", () => {
    row({ counting });
    expect(screen.getByText("Counts · Core · OKLO, SMR · since Nov 18")).toBeInTheDocument();
  });

  it("says 'whole card' for an entry naming none", () => {
    row({ counting: { ...(counting as object), grade: "flip", members: [] } });
    expect(screen.getByText("Counts · Flip · whole card · since Nov 18")).toBeInTheDocument();
  });

  it("Edit re-opens the form filled", async () => {
    const user = userEvent.setup();
    row({ counting });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("OKLO")).toBeChecked();
    expect(screen.getByLabelText("SMR")).toBeChecked();
    expect(screen.getByLabelText(/^Core: binding/)).toBeChecked();
    expect(screen.getByLabelText("Citation")).toHaveValue("https://sec.gov/x");
  });

  it("Stop counting sends counts:false with the last statement", async () => {
    const user = userEvent.setup();
    row({ counting });
    await user.click(screen.getByRole("button", { name: "Stop counting" }));
    expect(h.count.mock.calls[0][0]).toEqual({
      entryId: "e1",
      body: {
        counts: false,
        grade: "core",
        security_ids: ["s-oklo", "s-smr"],
        citation: "https://sec.gov/x",
        relevant_until: null,
      },
    });
  });

  it("once off it is an ordinary item again, and its form re-opens filled with the last statement", async () => {
    const user = userEvent.setup();
    row({ counting: { ...(counting as object), counts: false } });
    expect(screen.queryByText(/^Counts ·/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop counting" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Counts toward the call…/ }));
    expect(screen.getByLabelText("Citation")).toHaveValue("https://sec.gov/x");
    expect(screen.getByLabelText(/^Core: binding/)).toBeChecked();
  });

  it("a refusal of Stop counting shows the server's sentence on that row", () => {
    h.state.isError = true;
    h.state.error = { detail: "Give the entry a label before it counts toward the call." };
    row({ counting });
    expect(screen.getByText("Give the entry a label before it counts toward the call.")).toBeInTheDocument();
  });

  it("renders a label, citation and sentence as plain text: no markup appears", async () => {
    const user = userEvent.setup();
    const nasty = "<b>bold</b> [click](https://evil.example/x) <img src=x onerror=alert(1)>";
    h.state.isError = true;
    h.state.error = { detail: nasty };
    const { container } = row({
      label: nasty,
      counting: { ...(counting as object), label: nasty, citation: nasty },
    });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(container.querySelector("b, img, a")).toBeNull();
    expect(screen.getAllByText(nasty).length).toBeGreaterThan(0);
  });
});

describe("the calendar editor", () => {
  it("has no counting control on an unsaved row, and says to save first", async () => {
    const user = userEvent.setup();
    render(<CatalystEditor thesisId="t1" catalysts={[]} />);
    await user.click(screen.getByRole("button", { name: /add catalysts/ }));
    expect(screen.queryByRole("button", { name: /Counts toward the call/ })).toBeNull();
    expect(screen.getByText("Save the calendar to make an entry count toward the call.")).toBeInTheDocument();
  });

  it("keeps the user's draft when the server refuses a removal, and shows its sentence", async () => {
    const user = userEvent.setup();
    h.state.isError = true;
    h.state.error = { detail: "'NRC decision' counts toward the call. Turn that off before removing it." };
    render(
      <CatalystEditor
        thesisId="t1"
        catalysts={[{ id: "e1", label: "NRC decision", kind: null, when_date: null, when_label: null }] as never}
      />,
    );
    await user.click(screen.getByRole("button", { name: /edit the calendar/ }));
    await user.click(screen.getByLabelText("remove catalyst 1"));
    await user.click(screen.getByRole("button", { name: "+ row" }));
    await user.type(screen.getByLabelText("catalyst label 1"), "Another");
    await user.click(screen.getByRole("button", { name: "Save calendar" }));
    expect(h.putCats).toHaveBeenCalled();
    expect(screen.getByText(/Turn that off before removing it\./)).toBeInTheDocument();
    expect(screen.getByLabelText("catalyst label 1")).toHaveValue("Another"); // the draft is still open
  });
});
