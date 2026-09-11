import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScoreboardEpisodeOut } from "../../api/hooks";
import { dearmX, EpisodeSparkline } from "../EpisodeSparkline";

// The Timing view's Path cell. The GEOMETRY is the Cockpit's own `sparkGeometry` (tested there); what
// is this component's to get right is the floor, the de-arm mark's placement, and never drawing a mark
// for a de-arm that has no place on the path.

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    ticker: "DEVCO",
    arm_date: "2026-08-03",
    exit_date: "2026-08-21",
    dearm_date: null,
    dearm_index: null,
    close_reason: "window_end",
    status: "open",
    matured: false,
    censored_start: false,
    path: [],
    ...over,
  } as ScoreboardEpisodeOut;
}

describe("dearmX — the mark lands on its bar, not near it", () => {
  it("uses the same i/(n-1)*w mapping the geometry uses for its slots", () => {
    expect(dearmX(5, 0)).toBe(0); // first slot -> left edge
    expect(dearmX(5, 4)).toBe(72); // last slot -> SPARK_W
    expect(dearmX(5, 2)).toBe(36); // midpoint
  });
  it("is null wherever there is no place for it", () => {
    expect(dearmX(5, null)).toBeNull();
    expect(dearmX(5, undefined)).toBeNull();
    expect(dearmX(1, 0)).toBeNull(); // a point is not a path
    expect(dearmX(3, 3)).toBeNull(); // out of range — never clamped onto the final slot
    expect(dearmX(3, -1)).toBeNull();
  });
});

describe("EpisodeSparkline", () => {
  it("draws the path, and carries the span + axis caveat on the hover", () => {
    const { container } = render(<EpisodeSparkline ep={ep({ path: [10, 12, 11, 14] })} />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelectorAll("path").length).toBeGreaterThan(0);
    expect(container.querySelector(".spark")?.getAttribute("title")).toContain("4 bars");
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toContain("4 bars");
  });

  it("keeps the Cockpit's floor: below two closes the cell reads '—' with the why on hover", () => {
    // forking the floor between the two sparklines in one application would be a worse inconsistency
    // than the 13 two-bar episodes that draw a single straight segment
    const { container } = render(<EpisodeSparkline ep={ep({ path: [10] })} />);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toBe("—");
    expect(container.querySelector(".muted")?.getAttribute("title")).toContain(
      "a point is not a path",
    );
  });

  it("a two-bar episode still draws — it is literally the two closes that exist", () => {
    const { container } = render(<EpisodeSparkline ep={ep({ path: [10, 12] })} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("marks a de-arm that lands on the path", () => {
    const { container } = render(
      <EpisodeSparkline ep={ep({ path: [10, 12, 11, 14], dearm_date: "2026-08-12", dearm_index: 1 })} />,
    );
    const tick = container.querySelector("line.sb-spark-dearm");
    expect(tick).not.toBeNull();
    expect(tick?.getAttribute("x1")).toBe("24"); // 1/(4-1) * 72
  });

  it("draws NO mark when the de-arm fell after the scored window — and says so on the hover", () => {
    // the real 8-episode case. A silently absent tick would read as "never de-armed"; the hover is
    // what keeps the missing mark from being a claim.
    const { container } = render(
      <EpisodeSparkline ep={ep({ path: [10, 12, 11, 14], dearm_date: "2026-09-02", dearm_index: null })} />,
    );
    expect(container.querySelector("line.sb-spark-dearm")).toBeNull();
    expect(container.querySelector(".spark")?.getAttribute("title")).toContain(
      "after the scored window",
    );
  });
});
