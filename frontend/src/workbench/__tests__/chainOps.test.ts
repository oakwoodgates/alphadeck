import { describe, expect, it } from "vitest";

import type { BasketMember, Segment } from "../../api/hooks";
import {
  addLink,
  effectiveSegment,
  reconcileMemberSegments,
  removeLink,
  renameLink,
  reorderLink,
  sanitizeBasketForPromote,
} from "../chainOps";
import { DISCOVERED } from "../useChainDraft";

// The pure value-chain transforms behind the SCORE-view #4 mover (chain-editing Phase 1). Load-bearing:
// moving ONE name leaves every OTHER name's rows byte-identical; clearing floors to exactly one Discovered
// row (never null, never gone); a NULL and an ORPHAN segment each resolve to Discovered on read AND heal on
// write; the whole thing is idempotent. `segment` is display/structure — these transforms never read or
// touch a call input (#4).

// --- fixture builder: a full BasketMember with distinctive per-name fields ----------------------
function mem(over: Partial<BasketMember> & { security_id: string }): BasketMember {
  return {
    ticker: over.ticker ?? "TKR",
    role: "core",
    security_id: over.security_id,
    detail: null,
    segment: null,
    thesis_fit: null,
    conviction: null,
    surfaced_terms: [],
    authored_by: "operator_set",
    signed_off: false,
    ...over,
  };
}
const seg = (label: string, descriptor: string | null = null): Segment => ({ label, descriptor });
const CHAIN = [seg("reactors", "the builders"), seg("fuel")];

describe("reconcileMemberSegments — the #4 mover", () => {
  it("moves a name from one link to another, rebuilding just its row", () => {
    const basket = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
    ];
    const { basket: out, segments } = reconcileMemberSegments(basket, CHAIN, "s-x", ["fuel"]);
    expect(out).toHaveLength(1);
    expect(out[0].segment).toBe("fuel");
    expect(segments).toBe(CHAIN); // no floor used → segments untouched (same ref)
  });

  it("leaves every OTHER name's rows byte-identical (count + fields + reference)", () => {
    const a = mem({ security_id: "s-a", ticker: "A", segment: "reactors" });
    const x1 = mem({ security_id: "s-x", ticker: "X", segment: "reactors" });
    const x2 = mem({ security_id: "s-x", ticker: "X", segment: "fuel" });
    const b = mem({ security_id: "s-b", ticker: "B", segment: "fuel" });
    const basket = [a, x1, b, x2];

    const { basket: out } = reconcileMemberSegments(basket, CHAIN, "s-x", ["fuel"]);

    // X collapses from 2 rows to 1 (["fuel"]); A and B are untouched.
    const others = out.filter((m) => m.security_id !== "s-x");
    expect(others).toHaveLength(2);
    // byte-identical: deep-equal AND the very same object references (nothing rebuilt for others)
    expect(others).toEqual([a, b]);
    expect(others[0]).toBe(a);
    expect(others[1]).toBe(b);
    // the moved name: exactly one row, now in fuel
    const xs = out.filter((m) => m.security_id === "s-x");
    expect(xs).toHaveLength(1);
    expect(xs[0].segment).toBe("fuel");
  });

  it("carries EVERY per-name field forward (only segment changes)", () => {
    const rep = mem({
      security_id: "s-x",
      ticker: "HIMS",
      role: "leader",
      detail: "met the breakout",
      segment: "reactors",
      thesis_fit: "why it fits",
      conviction: 4,
      surfaced_terms: ["telehealth", "glp-1"],
      authored_by: "operator_edited",
      signed_off: true,
    });
    const { basket: out } = reconcileMemberSegments([rep], CHAIN, "s-x", ["fuel"]);
    expect(out[0]).toEqual({ ...rep, segment: "fuel" });
    // spot-check the fields the plan enumerates (nothing wiped — mappers.py resave wipe-trap)
    expect(out[0]).toMatchObject({
      ticker: "HIMS",
      role: "leader",
      detail: "met the breakout",
      thesis_fit: "why it fits",
      conviction: 4,
      surfaced_terms: ["telehealth", "glp-1"],
      authored_by: "operator_edited",
      signed_off: true,
    });
  });

  it("places a name into MULTIPLE links (N rows, one per checked label)", () => {
    const rep = mem({ security_id: "s-x", ticker: "X", segment: "reactors" });
    const { basket: out } = reconcileMemberSegments([rep], CHAIN, "s-x", ["reactors", "fuel"]);
    expect(out).toHaveLength(2);
    expect(out.map((m) => m.segment).sort()).toEqual(["fuel", "reactors"]);
    // both rows carry the same per-name identity
    expect(out.every((m) => m.security_id === "s-x" && m.ticker === "X")).toBe(true);
  });

  it("dedupes a doubled label (can't emit two identical rows)", () => {
    const rep = mem({ security_id: "s-x", segment: "reactors" });
    const { basket: out } = reconcileMemberSegments([rep], CHAIN, "s-x", ["fuel", "fuel"]);
    expect(out).toHaveLength(1);
    expect(out[0].segment).toBe("fuel");
  });

  it("clear (empty labels) → exactly one Discovered row + the Discovered segment appended", () => {
    const rep = mem({ security_id: "s-x", segment: "reactors" });
    const { basket: out, segments } = reconcileMemberSegments([rep], CHAIN, "s-x", []);
    const xs = out.filter((m) => m.security_id === "s-x");
    expect(xs).toHaveLength(1);
    expect(xs[0].segment).toBe(DISCOVERED); // the floor — never null (a null vanishes from every tab)
    // the Discovered Segment is ensured so the promote's consistency validator can't 422
    expect(segments.some((s) => s.label === DISCOVERED)).toBe(true);
    expect(segments).not.toBe(CHAIN); // a new array (Discovered appended)
  });

  it("does NOT re-append Discovered when the chain already has it", () => {
    const withDisc = [...CHAIN, seg(DISCOVERED)];
    const rep = mem({ security_id: "s-x", segment: "reactors" });
    const { segments } = reconcileMemberSegments([rep], withDisc, "s-x", []);
    expect(segments.filter((s) => s.label === DISCOVERED)).toHaveLength(1);
  });

  it("is idempotent — reconciling the same set again yields an identical basket", () => {
    const basket = [
      mem({ security_id: "s-a", ticker: "A", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "fuel" }),
    ];
    const once = reconcileMemberSegments(basket, CHAIN, "s-x", ["fuel"]);
    const twice = reconcileMemberSegments(once.basket, once.segments, "s-x", ["fuel"]);
    expect(twice.basket).toEqual(once.basket);
    expect(twice.segments).toEqual(once.segments);
  });

  it("is a no-op for an unknown name (nothing to rebuild)", () => {
    const basket = [mem({ security_id: "s-a", segment: "reactors" })];
    const out = reconcileMemberSegments(basket, CHAIN, "s-nope", ["fuel"]);
    expect(out.basket).toBe(basket);
    expect(out.segments).toBe(CHAIN);
  });
});

describe("effectiveSegment — the read-side Discovered floor", () => {
  it("keeps a valid in-chain segment", () => {
    expect(effectiveSegment(mem({ security_id: "s", segment: "reactors" }), CHAIN)).toBe("reactors");
  });
  it("a NULL segment resolves to Discovered", () => {
    expect(effectiveSegment(mem({ security_id: "s", segment: null }), CHAIN)).toBe(DISCOVERED);
  });
  it("an ORPHAN label (not in the chain) resolves to Discovered", () => {
    expect(effectiveSegment(mem({ security_id: "s", segment: "old-link" }), CHAIN)).toBe(DISCOVERED);
  });
  it("a scored member (structural) resolves the same way", () => {
    expect(effectiveSegment({ segment: "fuel" }, CHAIN)).toBe("fuel");
    expect(effectiveSegment({ segment: undefined }, CHAIN)).toBe(DISCOVERED);
  });
});

describe("sanitizeBasketForPromote — the write-side orphan self-heal", () => {
  it("rewrites a non-null ORPHAN → Discovered and appends the Discovered segment", () => {
    const basket = [
      mem({ security_id: "s-a", segment: "reactors" }),
      mem({ security_id: "s-b", segment: "old-link" }), // pre-existing orphan
    ];
    const { basket: out, segments } = sanitizeBasketForPromote(basket, CHAIN);
    expect(out.find((m) => m.security_id === "s-b")?.segment).toBe(DISCOVERED);
    expect(out.find((m) => m.security_id === "s-a")?.segment).toBe("reactors"); // valid untouched
    expect(segments.some((s) => s.label === DISCOVERED)).toBe(true);
  });

  it("leaves a NULL segment as NULL (valid in the flat pre-decompose basket)", () => {
    const basket = [mem({ security_id: "s-a", segment: null })];
    const { basket: out, segments } = sanitizeBasketForPromote(basket, CHAIN);
    expect(out[0].segment).toBeNull();
    expect(segments).toBe(CHAIN); // nothing healed → segments untouched (same ref)
  });

  it("is a no-op when every segment is valid", () => {
    const basket = [
      mem({ security_id: "s-a", segment: "reactors" }),
      mem({ security_id: "s-b", segment: "fuel" }),
    ];
    const { basket: out, segments } = sanitizeBasketForPromote(basket, CHAIN);
    expect(out).toEqual(basket);
    expect(segments).toBe(CHAIN);
  });
});

// --- #1–3 topology transforms (chain-editing Phase 2) ------------------------------------------------
describe("addLink", () => {
  it("appends a new link (basket untouched)", () => {
    const basket = [mem({ security_id: "s-a", segment: "reactors" })];
    const { basket: out, segments } = addLink(basket, CHAIN, "supply");
    expect(segments.map((s) => s.label)).toEqual(["reactors", "fuel", "supply"]);
    expect(out).toBe(basket); // add never touches the basket
  });
  it("rejects a blank or duplicate label (no-op)", () => {
    expect(addLink([], CHAIN, "   ").segments).toBe(CHAIN);
    expect(addLink([], CHAIN, "fuel").segments).toBe(CHAIN); // dup
  });
  it("trims the label and descriptor", () => {
    const { segments } = addLink([], CHAIN, "  supply  ", "  the feeders  ");
    expect(segments.at(-1)).toEqual({ label: "supply", descriptor: "the feeders" });
  });
});

describe("renameLink — cascades onto placed members", () => {
  it("renames the Segment AND every member in it (no orphan)", () => {
    const basket = [
      mem({ security_id: "s-a", segment: "reactors" }),
      mem({ security_id: "s-b", segment: "reactors" }),
      mem({ security_id: "s-c", segment: "fuel" }),
    ];
    const { basket: out, segments } = renameLink(basket, CHAIN, "reactors", "reactor builders");
    expect(segments.map((s) => s.label)).toEqual(["reactor builders", "fuel"]);
    expect(out.filter((m) => m.segment === "reactor builders")).toHaveLength(2);
    expect(out.find((m) => m.security_id === "s-c")?.segment).toBe("fuel"); // untouched
    expect(out.some((m) => m.segment === "reactors")).toBe(false); // nothing left orphaned
  });
  it("rejects a blank or duplicate rename (no-op)", () => {
    const basket = [mem({ security_id: "s-a", segment: "reactors" })];
    expect(renameLink(basket, CHAIN, "reactors", "  ").segments).toBe(CHAIN);
    expect(renameLink(basket, CHAIN, "reactors", "fuel").segments).toBe(CHAIN); // collides
  });
});

describe("reorderLink — swaps with the neighbor", () => {
  it("moves a link right / left and persists the order", () => {
    const right = reorderLink([], CHAIN, "reactors", 1);
    expect(right.segments.map((s) => s.label)).toEqual(["fuel", "reactors"]);
    const left = reorderLink([], CHAIN, "fuel", -1);
    expect(left.segments.map((s) => s.label)).toEqual(["fuel", "reactors"]);
  });
  it("a boundary move is a no-op", () => {
    expect(reorderLink([], CHAIN, "reactors", -1).segments).toBe(CHAIN); // already first
    expect(reorderLink([], CHAIN, "fuel", 1).segments).toBe(CHAIN); // already last
  });
});

describe("removeLink — multi-membership-safe, last placement → Discovered", () => {
  it("drops only the redundant row for a name kept in another link", () => {
    const basket = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "fuel" }), // kept elsewhere
      mem({ security_id: "s-y", ticker: "Y", segment: "fuel" }),
    ];
    const { basket: out, segments } = removeLink(basket, CHAIN, "reactors");
    expect(segments.map((s) => s.label)).toEqual(["fuel"]);
    const xs = out.filter((m) => m.security_id === "s-x");
    expect(xs).toHaveLength(1);
    expect(xs[0].segment).toBe("fuel"); // the kept placement — not floored
    expect(out.some((s) => s.segment === DISCOVERED)).toBe(false); // nobody needed the floor
  });

  it("routes a LAST placement to the Discovered floor (never null / gone) + ensures the segment", () => {
    const basket = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-y", ticker: "Y", segment: "fuel" }), // ONLY in fuel
    ];
    const { basket: out, segments } = removeLink(basket, CHAIN, "fuel");
    const y = out.find((m) => m.security_id === "s-y");
    expect(y?.segment).toBe(DISCOVERED); // last placement floored, not dropped (#9)
    expect(out).toHaveLength(2); // Y survives
    expect(segments.some((s) => s.label === DISCOVERED)).toBe(true); // ensured for the validator
    expect(segments.some((s) => s.label === "fuel")).toBe(false);
  });

  it("collapses several rows all in the removed link to ONE Discovered row", () => {
    const basket = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "fuel" }),
    ];
    // remove BOTH of X's links is two ops; here removing reactors keeps fuel, removing fuel then floors.
    const afterReactors = removeLink(basket, CHAIN, "reactors");
    const afterFuel = removeLink(afterReactors.basket, afterReactors.segments, "fuel");
    const xs = afterFuel.basket.filter((m) => m.security_id === "s-x");
    expect(xs).toHaveLength(1);
    expect(xs[0].segment).toBe(DISCOVERED);
  });
});
