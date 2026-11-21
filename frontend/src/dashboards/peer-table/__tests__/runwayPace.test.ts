import { describe, expect, it } from "vitest";

import { runwayCell } from "../tableView";
import { runway } from "./fixtures";

// V2 extractor correctness PR 3: the Peer Table's runway cell is the runway at last quarter's pace. Its hover says
// so and carries the year-to-date pace and the runway it implies; a quiet asterisk marks ONLY a row whose caveat
// applies (honest loudness), with the caveat sentence in the hover. The months and the sort never change.
const CAVEAT = "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads 9.1 months at last quarter's pace and 2.2 months at the year-to-date pace.";

describe("the runway cell's pace", () => {
  it("labels the months at last quarter's pace and carries the year-to-date pace and the caveat", () => {
    const c = runwayCell(
      runway({ months: 9.1, cash_usd: 51_811_000, burn_usd: 17_085_000, ytd_quarterly_burn_usd: 69_943_025, ytd_months: 2.2, pace_caveat: CAVEAT }),
    );
    expect(c.text).toBe("9.1 mo*");
    expect(c.title).toBe(
      "Months of cash at last quarter's pace: cash $51.8M, burn $17.1M per quarter, from the fact dated May 10, 2026. " +
        "Year-to-date pace: burn $69.9M per quarter, 2.2 months of runway. " +
        CAVEAT,
    );
  });

  it("has no mark for a steady quarter, and says the year to date generated cash when it did", () => {
    const steady = runwayCell(runway({ ytd_quarterly_burn_usd: 2_900_000, ytd_months: 12.4 }));
    expect(steady.text).toBe("12.0 mo");
    expect(steady.title).toContain("Year-to-date pace: burn $2.9M per quarter, 12.4 months of runway.");
    const flip = runwayCell(runway({ ytd_quarterly_burn_usd: -1_524_448_759, ytd_months: null, pace_caveat: CAVEAT }));
    expect(flip.text).toBe("12.0 mo*");
    expect(flip.title).toContain("Year-to-date pace: cash-generative, $1.52B per quarter.");
  });

  it("speaks the stored currency and the split for a yuan annual value (GAP 3)", () => {
    const c = runwayCell(
      runway({
        months: 72,
        cash_usd: 1_200_000_000,
        burn_usd: 50_000_000,
        currency: "CNY",
        source: "annual-statements",
        investments_usd: 300_000_000,
      }),
    );
    expect(c.title).toBe(
      "Months of cash at last quarter's pace: cash CN¥1.20B (of which short-term investments CN¥300.0M), " +
        "burn CN¥50.0M per quarter, from the fact dated May 10, 2026",
    );
    const usd = runwayCell(runway({ currency: "USD", source: "10-q" }));
    expect(usd.title).toContain("cash $12.0M, burn $3.0M per quarter");
    const unread = runwayCell(runway({ currency: null, source: "annual-statements" }));
    expect(unread.title).toContain("cash ?12.0M, burn ?3.0M per quarter");
    const older = runwayCell(runway({ currency: null, source: "10-q" }));
    expect(older.title).toContain("cash $12.0M, burn $3.0M per quarter");
  });

  it("reads as before for a value without a pace", () => {
    const c = runwayCell(runway());
    expect(c.text).toBe("12.0 mo");
    expect(c.title).toBe(
      "Months of cash at last quarter's pace: cash $12.0M, burn $3.0M per quarter, from the fact dated May 10, 2026",
    );
  });
});
