// The flagged-name note says each page's REAL rule (operator decision 2026-11-04): a page that keeps the row never
// claims the name is "left out of every number on this page".
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FlaggedNote, flaggedRule } from "../FlaggedNote";

const flagged = [{ ticker: "RBOT", reason: "mixed-scale price history" }];
const hover = () =>
  screen
    .getByText(/left out for a flagged price history/)
    .getAttribute("title");

describe("FlaggedNote wording", () => {
  it("keeps the every-number sentence by default", () => {
    render(<FlaggedNote flagged={flagged} />);
    expect(hover()).toContain("left out of every number on this page");
  });

  it("states the page's own rule when one is given, and drops the every-number claim", () => {
    render(
      <FlaggedNote
        flagged={flagged}
        title={flaggedRule("both cells read flagged. The row stays.")}
      />,
    );
    expect(hover()).toContain("Their data flags reach their price readings");
    expect(hover()).toContain("The row stays.");
    expect(hover()).not.toContain("every number");
  });

  it("renders nothing when no name is flagged", () => {
    const { container } = render(<FlaggedNote flagged={[]} title="x" />);
    expect(container).toBeEmptyDOMElement();
  });
});

// vitest runs from frontend/, so the page sources are read relative to it
const src = (rel: string) => readFileSync(resolve("src/dashboards", rel), "utf8");

// page -> what its note must say ("default" = the page really leaves the name out of every number)
const KEEPS_THE_ROW: Record<string, string> = {
  "peer-table/PeerTable.tsx": "price-derived cells",
  "hot-board/HotBoard.tsx": "They keep their row and their insider counts",
  "read-through/ReadThrough.tsx": "The row stays",
  "daily-digest/DailyDigest.tsx": "They keep their lines",
  "chain-map/ChainMap.tsx": "They stay on the map",
  "chain-wave/ChainWave.tsx": "They still count in their rows' names",
  "entry-windows/EntryWindows.tsx": "They keep their place",
  "forming-quietly/FormingQuietly.tsx": "tape and insider reading",
  "theme-leaderboard/ThemeLeaderboard.tsx": "Their Form 4 count stays",
  "narrative-adoption/NarrativeAdoption.tsx": "price line",
};
const LEFT_OUT_OF_EVERYTHING = [
  "cross-radar/CrossRadar.tsx",
  "landing/Landing.tsx",
  "leaders/Leaders.tsx",
  "market-map/MarketMap.tsx",
  "rotation-map/RotationMap.tsx",
];

describe("each page's flagged note", () => {
  for (const [file, phrase] of Object.entries(KEEPS_THE_ROW)) {
    it(`${file} says its real rule`, () => {
      const s = src(file);
      expect(s).toMatch(/<FlaggedNote [^>]*title=\{flaggedRule\(/);
      expect(s).toContain(phrase);
    });
  }
  for (const file of LEFT_OUT_OF_EVERYTHING) {
    it(`${file} keeps the default every-number sentence`, () => {
      const s = src(file);
      expect(s).toMatch(/<FlaggedNote flagged=\{[^}]+\} \/>/);
      expect(s).not.toContain("flaggedRule");
    });
  }
});
