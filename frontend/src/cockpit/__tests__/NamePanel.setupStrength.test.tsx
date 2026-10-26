import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MemberCallOut } from "../../api/hooks";
import { SETUP_STRENGTH_TOOLTIP } from "../../util/format";

// The wire field `confidence` is named SETUP STRENGTH in the product — an experimental relative indicator,
// never a probability — and the per-name panel says so, with the same hover the rail and the scorecard carry.

vi.mock("../../api/hooks", () => ({
  useDecisions: () => ({ data: [], isLoading: false, error: null }),
}));

import { bucketDef } from "../buckets";
import { NamePanel } from "../NamePanel";

const call = {
  security_id: "s-mtus",
  ticker: "MTUS",
  verdict: "core_entry",
  conviction_grade: "core",
  confirmation_grade: "core",
  entry_grade: "core",
  confidence: 0.7,
  exit_by: "2027-01-31",
  arm_until: "2026-10-15",
  lapsing: false,
  entry_window_closing: false,
  theme_armed: false,
  triggers: [],
} as unknown as MemberCallOut;

describe("NamePanel — the setup-strength bar", () => {
  it("is labeled 'Setup strength' with the not-a-probability hover, never 'Confidence'", () => {
    const { container } = render(
      <NamePanel
        row={{
          member: { ticker: "MTUS", role: "core", security_id: "s-mtus" } as never,
          ordinal: 0,
          call,
          scored: null,
          bucket: "armed",
        }}
        def={bucketDef("armed")}
        card={undefined}
        thesisId="t-def"
        position={null}
        display={null}
        asof="2026-10-13"
        onClose={() => {}}
      />,
    );
    const row = container.querySelector(".np-conf .row") as HTMLElement;
    expect(row.textContent).toBe("Setup strength70%");
    expect(row.querySelector("span")?.getAttribute("title")).toBe(SETUP_STRENGTH_TOOLTIP);
    expect(container.textContent).not.toMatch(/confidence/i);
  });
});
