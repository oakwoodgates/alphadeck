import { describe, expect, it } from "vitest";

import type { DataFlagOut, DisplaySignal, MemberDataFlagsOut } from "../../api/hooks";
import type { BucketRow } from "../buckets";
import {
  cellFlag,
  derivedCellFlag,
  factorWords,
  markTitle,
  rowLevel,
  stoppedFeedReason,
} from "../dataFlags";
import { sparkTicks } from "../DisplaySignalsSection";
import { groupMoving } from "../groupAggregate";

const flag = (over: Partial<DataFlagOut>): DataFlagOut => ({
  kind: "split_needs_review",
  label: "split needs review",
  level: "in_doubt",
  reason: "r",
  clears: "c",
  since: null,
  affects: [],
  columns: [],
  dates: [],
  evidence: [],
  call_affected: false,
  ...over,
});
const member = (flags: DataFlagOut[]): MemberDataFlagsOut => ({
  security_id: "s",
  ticker: "X",
  marked: flags.some((f) => (f.columns ?? []).length > 0),
  flags,
  splits: [],
  theses: [],
});

describe("dataFlags — the cell and row reads", () => {
  it("a cell no flag names is untouched; known wrong beats in doubt and every reason rides", () => {
    const m = member([
      flag({ reason: "split", columns: ["ret_7d", "ret_30d"] }),
      flag({ kind: "price_feed_stopped", level: "known_wrong", reason: "stopped", columns: ["ret_7d"] }),
    ]);
    expect(cellFlag(m, "ret_1d")).toBeNull();
    expect(cellFlag(m, "ret_30d")).toEqual({ level: "in_doubt", reason: "split" });
    expect(cellFlag(m, "ret_7d")).toEqual({ level: "known_wrong", reason: "split\nstopped" });
    expect(cellFlag(null, "ret_7d")).toBeNull();
  });

  it("a flag that reaches no cell never marks the row (history only)", () => {
    const quiet = member([flag({ kind: "mixed_scale_history", columns: [] })]);
    expect(rowLevel(quiet)).toBeNull();
    expect(markTitle(quiet)).toBe("");
    const loud = member([flag({ reason: "a 1-for-100 split", columns: ["ret_7d", "rvol8"] })]);
    expect(rowLevel(loud)).toBe("in_doubt");
    expect(markTitle(loud)).toBe("split needs review (in doubt): a 1-for-100 split — affects 7d, RVOL 8D");
  });

  it("a split's factor reads in the operator's words", () => {
    expect(factorWords(16)).toBe("×16");
    expect(factorWords(0.5)).toBe("÷2");
    expect(factorWords(1 / 3)).toBe("÷3");
    expect(factorWords(1.0167)).toBe("×1.017");
  });
});

describe("groupMoving — a flagged 7d value is left out, and counted", () => {
  const row = (sid: string) => ({ row: { member: { security_id: sid } } as unknown as BucketRow });
  const sig = (v: number): DisplaySignal =>
    ({
      kind: "trailing_returns",
      label: "t",
      metrics: [{ key: "ret_7d", label: "7d", value: v, unit: "pct" }],
      basis: { source: "x" },
    }) as unknown as DisplaySignal;
  const values: Record<string, number> = { a: 61, b: 2, c: 4, d: 6 };
  const trailFor = (r: BucketRow) => sig(values[r.member.security_id as string]);

  it("the median skips the flagged rows and says how many", () => {
    const rows = ["a", "b", "c", "d"].map(row);
    expect(groupMoving(rows, trailFor)).toEqual({ n: 4, priced: 4, median: 5, leftOut: 0 });
    const flagged = (r: BucketRow) => r.member.security_id === "a";
    expect(groupMoving(rows, trailFor, flagged)).toEqual({ n: 4, priced: 3, median: 4, leftOut: 1 });
  });

  it("leaving values out can take the group under the three-priced floor (an honest dash)", () => {
    const rows = ["a", "b", "c"].map(row);
    expect(groupMoving(rows, trailFor, (r) => r.member.security_id === "a")).toEqual({
      n: 3,
      priced: 2,
      median: null,
      leftOut: 1,
    });
  });
});

describe("sparkTicks — a split inside the path's window, at most a tick", () => {
  it("places the first bar at the new price on its slot and drops a split older than the window", () => {
    // 90 slots over 72px: the newest slot (barsAgo 0) at x=72, the oldest (barsAgo 89) at x=0
    expect(sparkTicks([{ barsAgo: 0, label: "a" }], 90)).toEqual([{ x: 72, label: "a" }]);
    expect(sparkTicks([{ barsAgo: 89, label: "b" }], 90)).toEqual([{ x: 0, label: "b" }]);
    expect(sparkTicks([{ barsAgo: 90, label: "c" }], 90)).toEqual([]);
  });
});

describe("dataFlags — derived cells (52w, ADV 20d) borrow the reach of the named cells that read their bars", () => {
  it("52w follows 1Y alone; ADV follows 30d and RVOL 20D; a cell no proxy names is untouched", () => {
    const oneYear = member([flag({ reason: "split", columns: ["ret_1y"] })]);
    expect(derivedCellFlag(oneYear, "off52w")).toEqual({ level: "in_doubt", reason: "split" });
    expect(derivedCellFlag(oneYear, "adv")).toBeNull();

    const thirty = member([flag({ reason: "split", columns: ["ret_30d"] })]);
    expect(derivedCellFlag(thirty, "adv")).toEqual({ level: "in_doubt", reason: "split" });
    expect(derivedCellFlag(thirty, "off52w")).toBeNull();

    const volume = member([flag({ reason: "volume scale", columns: ["rvol20"] })]);
    expect(derivedCellFlag(volume, "adv")).toEqual({ level: "in_doubt", reason: "volume scale" });

    // the Tape marks: a coil / RS read sits inside 90d's window, a cross reads back through 1Y's
    expect(derivedCellFlag(member([flag({ reason: "r", columns: ["ret_90d"] })]), "tapeShort")).not.toBeNull();
    expect(derivedCellFlag(member([flag({ reason: "r", columns: ["ret_90d"] })]), "tapeLong")).toBeNull();
    expect(derivedCellFlag(member([flag({ reason: "r", columns: ["ret_1y"] })]), "tapeLong")).not.toBeNull();
    expect(derivedCellFlag(member([flag({ reason: "r", columns: ["ret_1y"] })]), "tapeShort")).toBeNull();

    // a flag that names only the short windows never reaches either cell
    const short = member([flag({ reason: "island bar", columns: ["ret_1d", "ret_7d", "rvol8"] })]);
    expect(derivedCellFlag(short, "off52w")).toBeNull();
    expect(derivedCellFlag(short, "adv")).toBeNull();
    expect(derivedCellFlag(null, "adv")).toBeNull();
  });

  it("known wrong beats in doubt across the proxies, and each distinct reason rides once", () => {
    const m = member([
      flag({ reason: "split", columns: ["ret_30d", "rvol20"] }),
      flag({ kind: "price_feed_stopped", level: "known_wrong", reason: "stopped", columns: ["rvol20"] }),
    ]);
    // ret_30d -> "split"; rvol20 -> "split\nstopped": the merged hover names "split" once
    expect(derivedCellFlag(m, "adv")).toEqual({ level: "known_wrong", reason: "split\nstopped" });
  });
});

describe("dataFlags — the last close reads the newest bar, and a stopped feed does not blank it", () => {
  it("follows 1d alone, and inherits every kind EXCEPT a stopped price feed", () => {
    const split = member([flag({ reason: "split", columns: ["ret_1d"] })]);
    expect(derivedCellFlag(split, "last")).toEqual({ level: "in_doubt", reason: "split" });
    // a flag that reaches only the older windows never touches the newest bar
    expect(derivedCellFlag(member([flag({ reason: "r", columns: ["ret_7d", "ret_30d"] })]), "last")).toBeNull();

    const stopped = member([
      flag({ kind: "price_feed_stopped", level: "known_wrong", reason: "stopped", columns: ["ret_1d", "ret_7d"] }),
    ]);
    // the returns on the row are blanked by it…
    expect(cellFlag(stopped, "ret_1d")).toEqual({ level: "known_wrong", reason: "stopped" });
    // …but the last real print is not
    expect(derivedCellFlag(stopped, "last")).toBeNull();
    expect(stoppedFeedReason(stopped)).toBe("stopped");

    // another kind on the same row still counts
    const both = member([
      flag({ kind: "price_feed_stopped", level: "known_wrong", reason: "stopped", columns: ["ret_1d"] }),
      flag({ reason: "split", columns: ["ret_1d"] }),
    ]);
    expect(derivedCellFlag(both, "last")).toEqual({ level: "in_doubt", reason: "split" });
  });

  it("stoppedFeedReason is null for a name with no stopped-feed flag", () => {
    expect(stoppedFeedReason(null)).toBeNull();
    expect(stoppedFeedReason(member([flag({ columns: ["ret_1d"] })]))).toBeNull();
  });
});
