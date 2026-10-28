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
  setMemberContextRole,
  toggleMemberLink,
} from "../chainOps";
import { BENCH, CONTEXT, DISCOVERED, SILENT_DESCRIPTORS, withSilentLinks } from "../useChainDraft";

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
    // nothing healed → the chain is untouched except that it now ends with the fixed silent pair
    expect(segments).toEqual(withSilentLinks(CHAIN));
  });

  it("is a no-op when every segment is valid", () => {
    const basket = [
      mem({ security_id: "s-a", segment: "reactors" }),
      mem({ security_id: "s-b", segment: "fuel" }),
    ];
    const { basket: out, segments } = sanitizeBasketForPromote(basket, CHAIN);
    expect(out).toEqual(basket);
    expect(segments).toEqual(withSilentLinks(CHAIN)); // only the silent pair is added
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


// --- the two SILENT links (Context / Bench): fixed, last, protected — and a company is silent XOR voting ----
// Every guard here is the chainOps half of the rule; `useChainDraft.silent.test.ts` pins the hook's mirror.
describe("silent links — Context and Bench stay fixed, last and untouchable", () => {
  const labelsOf = (segs: Segment[]) => segs.map((x) => x.label);
  // the thesis as it now always arrives: the real links, then the fixed pair
  const SC = withSilentLinks(CHAIN);

  it("the pair carries the operator-approved descriptors, verbatim", () => {
    expect(SILENT_DESCRIPTORS[CONTEXT]).toBe(
      "Watched for context: buyers, counter-case names, giants where the theme is a slice. Never counts toward the call.",
    );
    expect(SILENT_DESCRIPTORS[BENCH]).toBe(
      "Speculative names: pivots, distressed or thinly traded. Never counts toward the call.",
    );
    expect(SC.slice(-2)).toEqual([
      { label: "Context", descriptor: SILENT_DESCRIPTORS.Context },
      { label: "Bench", descriptor: SILENT_DESCRIPTORS.Bench },
    ]);
  });

  describe("addLink", () => {
    it("lands BEFORE Context / Bench (the pinned pair stays last)", () => {
      const { segments } = addLink([], SC, "supply");
      expect(labelsOf(segments)).toEqual(["reactors", "fuel", "supply", "Context", "Bench"]);
    });
    it("a new link lands after Discovered too — Discovered is NOT pinned", () => {
      const withPen = [seg("reactors"), seg(DISCOVERED), ...SC.slice(-2)];
      expect(labelsOf(addLink([], withPen, "supply").segments)).toEqual([
        "reactors",
        DISCOVERED,
        "supply",
        "Context",
        "Bench",
      ]);
    });
    it("refuses a reserved name in ANY case (and padded) — no-op, same reference", () => {
      for (const name of ["Context", "context", "BENCH", "  bench  ", "CoNtExT"]) {
        expect(addLink([], SC, name).segments).toBe(SC);
      }
    });
    it("still appends at the end when the list carries neither silent link", () => {
      expect(labelsOf(addLink([], CHAIN, "supply").segments)).toEqual(["reactors", "fuel", "supply"]);
    });
  });

  describe("renameLink", () => {
    it("refuses a silent SOURCE (Context / Bench can't be renamed)", () => {
      const basket = [mem({ security_id: "s-a", segment: CONTEXT })];
      expect(renameLink(basket, SC, CONTEXT, "Giants").segments).toBe(SC);
      expect(renameLink(basket, SC, BENCH, "Reserve").segments).toBe(SC);
      expect(renameLink(basket, SC, CONTEXT, "Giants").basket).toBe(basket); // no cascade either
    });
    it("refuses a reserved TARGET (no link may be renamed INTO Context / Bench, any case)", () => {
      const basket = [mem({ security_id: "s-a", segment: "fuel" })];
      for (const name of ["Context", "context", " BENCH "]) {
        const out = renameLink(basket, SC, "fuel", name);
        expect(out.segments).toBe(SC);
        expect(out.basket).toBe(basket);
      }
    });
    it("a normal rename keeps the pair last and untouched", () => {
      const { segments } = renameLink([], SC, "fuel", "fuels");
      expect(labelsOf(segments)).toEqual(["reactors", "fuels", "Context", "Bench"]);
    });
  });

  describe("reorderLink", () => {
    it("never moves a silent link — either direction, either link", () => {
      expect(reorderLink([], SC, CONTEXT, -1).segments).toBe(SC);
      expect(reorderLink([], SC, CONTEXT, 1).segments).toBe(SC);
      expect(reorderLink([], SC, BENCH, -1).segments).toBe(SC);
      expect(reorderLink([], SC, BENCH, 1).segments).toBe(SC);
    });
    it("never swaps a real link past or with one (the last real link can't go right)", () => {
      expect(reorderLink([], SC, "fuel", 1).segments).toBe(SC);
    });
    it("real links still reorder among themselves, the pair staying last", () => {
      expect(labelsOf(reorderLink([], SC, "fuel", -1).segments)).toEqual([
        "fuel",
        "reactors",
        "Context",
        "Bench",
      ]);
    });
  });

  describe("removeLink", () => {
    it("is a no-op on a silent link — the link AND its names stay (nothing deleted, WB#2)", () => {
      const basket = [mem({ security_id: "s-a", segment: CONTEXT })];
      for (const l of [CONTEXT, BENCH]) {
        const out = removeLink(basket, SC, l);
        expect(out.segments).toBe(SC);
        expect(out.basket).toBe(basket);
      }
    });
    it("a LAST placement still floors to Discovered — inserted BEFORE Context, never to Context/Bench", () => {
      const basket = [
        mem({ security_id: "s-y", ticker: "Y", segment: "fuel" }), // ONLY in fuel
        mem({ security_id: "s-c", ticker: "C", segment: CONTEXT }),
      ];
      const { basket: out, segments } = removeLink(basket, SC, "fuel");
      expect(out.find((m) => m.security_id === "s-y")?.segment).toBe(DISCOVERED);
      expect(out.find((m) => m.security_id === "s-c")?.segment).toBe(CONTEXT); // untouched
      expect(labelsOf(segments)).toEqual(["reactors", DISCOVERED, "Context", "Bench"]);
    });
  });

  describe("sanitizeBasketForPromote", () => {
    it("never rewrites a silent label — a Context / Bench row is always valid", () => {
      const basket = [
        mem({ security_id: "s-a", segment: CONTEXT }),
        mem({ security_id: "s-b", segment: BENCH }),
      ];
      // even from a chain that LACKED the pair (the payload gets it back)
      const { basket: out, segments } = sanitizeBasketForPromote(basket, CHAIN);
      expect(out.map((m) => m.segment)).toEqual([CONTEXT, BENCH]);
      expect(labelsOf(segments)).toEqual(["reactors", "fuel", "Context", "Bench"]);
    });
    it("ensures the pair is present and last (idempotent — an already-normal chain is the same reference)", () => {
      const once = sanitizeBasketForPromote([], CHAIN).segments;
      expect(labelsOf(once).slice(-2)).toEqual(["Context", "Bench"]);
      expect(sanitizeBasketForPromote([], SC).segments).toBe(SC);
    });
    it("heals an orphan into Discovered BEFORE the pair", () => {
      const { basket: out, segments } = sanitizeBasketForPromote(
        [mem({ security_id: "s-b", segment: "old-link" })],
        SC,
      );
      expect(out[0].segment).toBe(DISCOVERED);
      expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "Bench"]);
    });
    it("a case-variant label (\"context\") is not the silent link: its rows heal to Discovered, never lost", () => {
      const { basket: out } = sanitizeBasketForPromote(
        [mem({ security_id: "s-b", segment: "context" })],
        [seg("context"), ...CHAIN],
      );
      expect(out[0].segment).toBe(DISCOVERED);
    });
  });

  describe("effectiveSegment", () => {
    it("treats the silent labels as always known — even from a chain that lacks them", () => {
      expect(effectiveSegment({ segment: CONTEXT }, CHAIN)).toBe(CONTEXT);
      expect(effectiveSegment({ segment: BENCH }, CHAIN)).toBe(BENCH);
      expect(effectiveSegment({ segment: CONTEXT }, SC)).toBe(CONTEXT);
    });
    it("a lowercase look-alike is NOT a silent link (exact match) → the Discovered floor", () => {
      expect(effectiveSegment({ segment: "context" }, CHAIN)).toBe(DISCOVERED);
    });
  });

  describe("reconcileMemberSegments", () => {
    const multi = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "fuel" }),
      mem({ security_id: "s-a", ticker: "A", segment: "fuel" }),
    ];
    it("a silent label yields exactly ONE row (that link); other names are untouched", () => {
      const { basket: out } = reconcileMemberSegments(multi, SC, "s-x", [CONTEXT]);
      const xs = out.filter((m) => m.security_id === "s-x");
      expect(xs).toHaveLength(1);
      expect(xs[0].segment).toBe(CONTEXT);
      expect(out.find((m) => m.security_id === "s-a")).toBe(multi[2]);
    });
    it("a silent label beside real ones still collapses to the ONE silent row (defensive)", () => {
      const { basket: out } = reconcileMemberSegments(multi, SC, "s-x", ["reactors", BENCH]);
      expect(out.filter((m) => m.security_id === "s-x").map((m) => m.segment)).toEqual([BENCH]);
    });
    it("carries every per-name field into the silent row", () => {
      const rep = mem({ security_id: "s-x", segment: "fuel", conviction: 4, signed_off: true });
      const { basket: out } = reconcileMemberSegments([rep], SC, "s-x", [CONTEXT]);
      expect(out[0]).toEqual({ ...rep, segment: CONTEXT });
    });
    it("ensures the pair exists when the chain lacked it; leaves a chain that has it untouched", () => {
      const { segments } = reconcileMemberSegments(multi, CHAIN, "s-x", [CONTEXT]);
      expect(labelsOf(segments)).toEqual(["reactors", "fuel", "Context", "Bench"]);
      expect(reconcileMemberSegments(multi, SC, "s-x", [CONTEXT]).segments).toBe(SC);
    });
    it("the Discovered floor lands BEFORE the pair", () => {
      const { segments } = reconcileMemberSegments(multi, SC, "s-x", []);
      expect(labelsOf(segments)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "Bench"]);
    });
  });
});

describe("toggleMemberLink — the rail's one-row rule (silent XOR voting)", () => {
  it("checking a SILENT link on a multi-link name → exactly that one link", () => {
    expect(toggleMemberLink(["reactors", "fuel"], CONTEXT)).toEqual([CONTEXT]);
    expect(toggleMemberLink(["reactors", "fuel"], BENCH)).toEqual([BENCH]);
  });
  it("checking a silent link on a name in none → that one link", () => {
    expect(toggleMemberLink([], CONTEXT)).toEqual([CONTEXT]);
  });
  it("moving a silent name to the OTHER silent link replaces it (never both)", () => {
    expect(toggleMemberLink([CONTEXT], BENCH)).toEqual([BENCH]);
  });
  it("checking a REAL link while the name is silent → that one real link (it leaves Context)", () => {
    expect(toggleMemberLink([CONTEXT], "fuel")).toEqual(["fuel"]);
    expect(toggleMemberLink([BENCH], "reactors")).toEqual(["reactors"]);
  });
  it("checking a real link otherwise ADDS it (multi-membership as before)", () => {
    expect(toggleMemberLink(["reactors"], "fuel")).toEqual(["reactors", "fuel"]);
    expect(toggleMemberLink([], "fuel")).toEqual(["fuel"]);
  });
  it("UNCHECKING removes just that label — the empty set is the Discovered floor", () => {
    expect(toggleMemberLink(["reactors", "fuel"], "fuel")).toEqual(["reactors"]);
    expect(toggleMemberLink([CONTEXT], CONTEXT)).toEqual([]);
    expect(toggleMemberLink(["fuel"], "fuel")).toEqual([]);
  });
  it("does not mutate its input", () => {
    const cur = ["reactors"];
    toggleMemberLink(cur, "fuel");
    toggleMemberLink(cur, CONTEXT);
    expect(cur).toEqual(["reactors"]);
  });

  describe("end to end through reconcileMemberSegments (the rail's payload)", () => {
    const SC = withSilentLinks(CHAIN);
    const rowsFor = (basket: BasketMember[], sid: string) => basket.filter((m) => m.security_id === sid);
    const twoLink = [
      mem({ security_id: "s-x", ticker: "X", segment: "reactors" }),
      mem({ security_id: "s-x", ticker: "X", segment: "fuel" }),
    ];

    it("a 2-link name → Context = ONE row in Context", () => {
      const labels = toggleMemberLink(["reactors", "fuel"], CONTEXT);
      const { basket } = reconcileMemberSegments(twoLink, SC, "s-x", labels);
      expect(rowsFor(basket, "s-x").map((m) => m.segment)).toEqual([CONTEXT]);
    });
    it("a Context name → a real link = ONE row in that link", () => {
      const inCtx = [mem({ security_id: "s-x", ticker: "X", segment: CONTEXT })];
      const labels = toggleMemberLink([CONTEXT], "fuel");
      const { basket } = reconcileMemberSegments(inCtx, SC, "s-x", labels);
      expect(rowsFor(basket, "s-x").map((m) => m.segment)).toEqual(["fuel"]);
    });
    it("unchecking Context floors the name to Discovered (the visible inverse — nothing deleted)", () => {
      const inCtx = [mem({ security_id: "s-x", ticker: "X", segment: CONTEXT })];
      const labels = toggleMemberLink([CONTEXT], CONTEXT);
      const { basket, segments } = reconcileMemberSegments(inCtx, SC, "s-x", labels);
      expect(rowsFor(basket, "s-x").map((m) => m.segment)).toEqual([DISCOVERED]);
      expect(segments.map((x) => x.label)).toEqual(["reactors", "fuel", DISCOVERED, "Context", "Bench"]);
    });
    it("Context → check → uncheck round-trips the name back to a single row each way", () => {
      const start = twoLink;
      const toCtx = reconcileMemberSegments(start, SC, "s-x", toggleMemberLink(["reactors", "fuel"], CONTEXT));
      const back = reconcileMemberSegments(toCtx.basket, toCtx.segments, "s-x", toggleMemberLink([CONTEXT], "reactors"));
      expect(rowsFor(back.basket, "s-x").map((m) => m.segment)).toEqual(["reactors"]);
    });
  });
});


describe("setMemberContextRole — the per-company context-role mover", () => {
  it("sets the role on EVERY row of the company (a name in N links is N rows) and returns every other row by reference", () => {
    const a = mem({ security_id: "s-a", ticker: "A", segment: "reactors" });
    const x1 = mem({ security_id: "s-x", ticker: "X", segment: "reactors" });
    const x2 = mem({ security_id: "s-x", ticker: "X", segment: "fuel" });
    const b = mem({ security_id: "s-b", ticker: "B", segment: "fuel" });
    const basket = [a, x1, b, x2];

    const out = setMemberContextRole(basket, "s-x", "payer");

    expect(out).toHaveLength(4);
    expect(out.filter((m) => m.security_id === "s-x").map((m) => m.context_role)).toEqual([
      "payer",
      "payer",
    ]);
    // the other names' rows are the SAME objects (untouched), and the order is preserved
    expect(out[0]).toBe(a);
    expect(out[2]).toBe(b);
    expect(out.map((m) => m.ticker)).toEqual(["A", "X", "B", "X"]);
    // the input is not mutated
    expect(x1.context_role).toBeUndefined();
    expect(x2.context_role).toBeUndefined();
  });

  it("changes nothing else about a row (segment, authorship, sign-off, conviction, description)", () => {
    const x = mem({
      security_id: "s-x",
      ticker: "X",
      segment: "fuel",
      conviction: 4,
      signed_off: true,
      thesis_fit: "why it sits here",
      surfaced_terms: ["smr"],
      authored_by: "operator_edited",
    });
    const [out] = setMemberContextRole([x], "s-x", "financier");
    expect(out).toEqual({ ...x, context_role: "financier" });
  });

  it("replaces a different role on every row (a company never ends up with two)", () => {
    const rows = [
      mem({ security_id: "s-x", segment: "reactors", context_role: "payer" }),
      mem({ security_id: "s-x", segment: "fuel", context_role: "payer" }),
    ];
    const out = setMemberContextRole(rows, "s-x", "benchmark");
    expect(out.map((m) => m.context_role)).toEqual(["benchmark", "benchmark"]);
  });

  it("clears with null on EVERY row (the visible inverse), including rows that never had the key", () => {
    const rows = [
      mem({ security_id: "s-x", segment: "reactors", context_role: "counter_case" }),
      mem({ security_id: "s-x", segment: "fuel" }), // no key at all
    ];
    const out = setMemberContextRole(rows, "s-x", null);
    expect(out.map((m) => m.context_role)).toEqual([null, null]);
    // set → clear → set again is a clean round trip
    const again = setMemberContextRole(out, "s-x", "counter_case");
    expect(again.map((m) => m.context_role)).toEqual(["counter_case", "counter_case"]);
  });

  it("is allowed on a silent-link row and a Discovered row", () => {
    const ctx = mem({ security_id: "s-c", segment: CONTEXT });
    const dis = mem({ security_id: "s-d", segment: DISCOVERED });
    const out = setMemberContextRole(setMemberContextRole([ctx, dis], "s-c", "payer"), "s-d", "benchmark");
    expect(out.map((m) => [m.segment, m.context_role])).toEqual([
      [CONTEXT, "payer"],
      [DISCOVERED, "benchmark"],
    ]);
  });

  it("an unknown name is a harmless no-op (every row is the same reference)", () => {
    const rows = [mem({ security_id: "s-a" }), mem({ security_id: "s-b" })];
    const out = setMemberContextRole(rows, "s-nope", "payer");
    expect(out).toEqual(rows);
    out.forEach((m, i) => expect(m).toBe(rows[i]));
  });

  it("the other movers carry the role forward (reconcile, rename, remove-link): it rides every row", () => {
    const x = mem({ security_id: "s-x", ticker: "X", segment: "reactors", context_role: "participant" });
    // moving the name into a second link copies the representative row, role included
    const moved = reconcileMemberSegments([x], CHAIN, "s-x", ["reactors", "fuel"]).basket;
    expect(moved.map((m) => m.context_role)).toEqual(["participant", "participant"]);
    // a rename cascades onto the row without dropping the role
    expect(renameLink([x], CHAIN, "reactors", "builders").basket[0].context_role).toBe("participant");
    // removing the link floors the name to Discovered, role intact
    expect(removeLink([x], CHAIN, "reactors").basket[0]).toMatchObject({
      segment: DISCOVERED,
      context_role: "participant",
    });
  });
});
