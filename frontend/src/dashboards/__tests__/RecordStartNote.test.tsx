import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DASHBOARDS } from "../registry";
import { RecordStartNote } from "../RecordStartNote";

// The quiet "the record starts on ..." note (W8c): one hook, one component, used by every dashboard that reads the
// calls history. It says why a history page is empty, never anything loud: nothing while loading or on an error.
const h = vi.hoisted(() => ({ hook: vi.fn() }));
vi.mock("../../api/hooks", () => ({ useRecordStart: h.hook }));

afterEach(() => h.hook.mockReset());

const note = () => screen.queryByTestId("record-start-note");

describe("RecordStartNote", () => {
  it("dates the record when the page is empty", () => {
    h.hook.mockReturnValue({ data: "2026-12-03", isLoading: false, isError: false });
    render(<RecordStartNote empty />);
    expect(note()).toHaveTextContent("The record starts on Dec 3, 2026. Nothing before that night is on it.");
  });

  it("says the record starts with the first nightly call when none is recorded", () => {
    h.hook.mockReturnValue({ data: null, isLoading: false, isError: false });
    render(<RecordStartNote empty />);
    expect(note()).toHaveTextContent("The record starts with the first nightly call.");
    expect(note()).not.toHaveTextContent("Nothing before");
  });

  it("says nothing on a page that has history rows (a dated record)", () => {
    h.hook.mockReturnValue({ data: "2026-12-03", isLoading: false, isError: false });
    render(<RecordStartNote empty={false} />);
    expect(note()).toBeNull();
  });

  it("renders nothing while loading and on an error", () => {
    h.hook.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const { container, rerender } = render(<RecordStartNote empty />);
    expect(container).toBeEmptyDOMElement();
    h.hook.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    rerender(<RecordStartNote empty />);
    expect(container).toBeEmptyDOMElement();
  });

  it("reuses the muted note class (no new color)", () => {
    h.hook.mockReturnValue({ data: "2026-12-03", isLoading: false, isError: false });
    render(<RecordStartNote empty />);
    expect(note()).toHaveClass("dash-note");
  });
});

// The sweep: the dashboards that read the calls history, by slug. Each is flagged `readsRecord` in its entry and
// its page renders the shared note, so a new history dashboard that forgets it is caught here.
const READS_RECORD = [
  "catalyst-calendar",
  "daily-digest",
  "entry-windows",
  "evidence-ledger",
  "forming-quietly",
  "hot-board",
  "idea-timeline",
  "insider-tracker",
  "narrative-index",
  "theme-leaderboard",
  "ticker-lookup",
  "trigger-log",
  "what-changed",
  "track-record",
];

describe("the history dashboards carry the note", () => {
  it("flags exactly the history dashboards as readsRecord", () => {
    expect(DASHBOARDS.filter((d) => d.readsRecord).map((d) => d.slug).sort()).toEqual([...READS_RECORD].sort());
  });

  it("renders RecordStartNote in the page of every flagged dashboard", () => {
    for (const d of DASHBOARDS.filter((x) => x.readsRecord)) {
      const src = readFileSync(resolve("src/dashboards", d.slug, pageFile(d.slug)), "utf8");
      expect(src, d.slug).toMatch(/<RecordStartNote\b/);
    }
  });
});

// <slug> -> its page file (the PascalCase component beside entry.ts)
function pageFile(slug: string): string {
  const pascal = slug.split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join("");
  return `${pascal}.tsx`;
}
