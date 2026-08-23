import { describe, expect, it } from "vitest";

import type { BasketMember, TermSetEntry } from "../../api/hooks";
import {
  groupHasTerm,
  hasTermUniverse,
  termsInclude,
  termUniverse,
} from "../termFilter";

// The pure core of the TRIAGE find-bar TERM filter. Load-bearing: the dropdown universe is the deduped
// union of every PLACED name's persisted `surfaced_terms`, tier-split SEED vs BROAD off the term set (an
// unmatched term stays visible under BROAD — recall #9); the predicate selects EXACTLY the names carrying
// the pick; clearing restores every name; an off-universe name (no surfaced terms) never matches a pick.
// VIEW-only — these transforms read provenance + term set and return options/booleans, touching no fact.

// --- fixtures (mirror chainOps.test.ts) --------------------------------------------------------------
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
const term = (t: string, tier: TermSetEntry["tier"]): TermSetEntry => ({
  term: t,
  tier,
  authored_by: tier === "signal" ? "operator_set" : "system_drafted",
  source: tier === "signal" ? "operator" : "keyword_gen",
});

// A term set with two operator SEEDS and one keyword-gen BROAD term. "small modular reactor" is a seed the
// basket never surfaced (a dead seed) — it must NOT appear in the universe (no dead options).
const TERM_SET: TermSetEntry[] = [
  term("SMR", "signal"),
  term("HALEU", "signal"),
  term("nuclear", "broad"),
  term("small modular reactor", "signal"),
];

describe("termUniverse — the tier-grouped dropdown options", () => {
  it("is the deduped union of surfaced_terms, split SEED vs BROAD by the term set", () => {
    const basket = [
      mem({ security_id: "s-a", ticker: "A", surfaced_terms: ["SMR", "nuclear"] }),
      mem({ security_id: "s-b", ticker: "B", surfaced_terms: ["SMR", "HALEU"] }), // SMR repeats → deduped
    ];
    const u = termUniverse(basket, TERM_SET);
    expect(u.signal.map((o) => o.label)).toEqual(["HALEU", "SMR"]); // sorted, deduped, seeds only
    expect(u.broad.map((o) => o.label)).toEqual(["nuclear"]);
  });

  it("omits DEAD options — a term in the set that surfaced NO placed name never appears", () => {
    const basket = [mem({ security_id: "s-a", surfaced_terms: ["SMR"] })];
    const u = termUniverse(basket, TERM_SET);
    const all = [...u.signal, ...u.broad].map((o) => o.label);
    expect(all).toEqual(["SMR"]);
    expect(all).not.toContain("HALEU"); // in the set, but no name surfaced it
    expect(all).not.toContain("small modular reactor"); // the dead seed
  });

  it("a surfaced term ABSENT from the term set lands in BROAD (never dropped — recall #9)", () => {
    const basket = [mem({ security_id: "s-a", surfaced_terms: ["cask storage", "SMR"] })];
    const u = termUniverse(basket, TERM_SET);
    expect(u.signal.map((o) => o.label)).toEqual(["SMR"]);
    expect(u.broad.map((o) => o.label)).toEqual(["cask storage"]); // unmatched → BROAD, still offered
  });

  it("dedupes case-insensitively, keeping the FIRST-seen casing for display", () => {
    const basket = [
      mem({ security_id: "s-a", surfaced_terms: ["HALEU"] }),
      mem({ security_id: "s-b", surfaced_terms: ["haleu"] }), // same term, other casing
    ];
    const u = termUniverse(basket, TERM_SET);
    expect(u.signal).toHaveLength(1);
    expect(u.signal[0].label).toBe("HALEU"); // first-seen casing
    expect(u.signal[0].value).toBe("haleu"); // normalized key
  });

  it("ignores blank/whitespace terms and empty-surfaced names", () => {
    const basket = [
      mem({ security_id: "s-a", surfaced_terms: ["  ", ""] }),
      mem({ security_id: "s-b", surfaced_terms: [] }),
      mem({ security_id: "s-c", surfaced_terms: undefined as unknown as string[] }),
    ];
    const u = termUniverse(basket, TERM_SET);
    expect(hasTermUniverse(u)).toBe(false);
  });
});

describe("hasTermUniverse — the honest-loudness render gate (#3)", () => {
  it("is false for a fully off-universe basket (no control)", () => {
    const basket = [mem({ security_id: "s-a", surfaced_terms: [] })];
    expect(hasTermUniverse(termUniverse(basket, TERM_SET))).toBe(false);
  });
  it("is true once any placed name carries a surfaced term", () => {
    const basket = [mem({ security_id: "s-a", surfaced_terms: ["SMR"] })];
    expect(hasTermUniverse(termUniverse(basket, TERM_SET))).toBe(true);
  });
});

describe("termsInclude — the row predicate (case-insensitive, cleared = pass-all)", () => {
  it("selecting a term yields EXACTLY the names whose surfaced_terms include it", () => {
    const basket = [
      mem({ security_id: "s-a", ticker: "A", surfaced_terms: ["SMR", "nuclear"] }),
      mem({ security_id: "s-b", ticker: "B", surfaced_terms: ["HALEU"] }),
      mem({ security_id: "s-c", ticker: "C", surfaced_terms: ["nuclear"] }),
    ];
    // pick the "nuclear" option exactly as the dropdown would hand it over (its normalized value)
    const u = termUniverse(basket, TERM_SET);
    const pick = u.broad.find((o) => o.label === "nuclear")!.value;
    const hit = basket.filter((m) => termsInclude(m.surfaced_terms, pick)).map((m) => m.ticker);
    expect(hit).toEqual(["A", "C"]); // B (HALEU only) is excluded
  });

  it("matches case-insensitively (a lowercased pick finds an upper-cased provenance)", () => {
    expect(termsInclude(["HALEU"], "haleu")).toBe(true);
    expect(termsInclude(["haleu"], "haleu")).toBe(true);
  });

  it("clearing (empty pick) passes EVERY name — the reversible restore", () => {
    const basket = [
      mem({ security_id: "s-a", surfaced_terms: ["SMR"] }),
      mem({ security_id: "s-b", surfaced_terms: [] }), // even the off-universe name returns
    ];
    expect(basket.every((m) => termsInclude(m.surfaced_terms, ""))).toBe(true);
  });

  it("a name with EMPTY surfaced_terms is excluded under any real pick", () => {
    expect(termsInclude([], "smr")).toBe(false);
    expect(termsInclude(undefined, "smr")).toBe(false);
    expect(termsInclude(null, "smr")).toBe(false);
  });

  it("a non-surfacing term picks nothing (no accidental match)", () => {
    expect(termsInclude(["SMR", "nuclear"], "haleu")).toBe(false);
  });
});

describe("groupHasTerm — the multi-membership (per-name) predicate", () => {
  it("matches when ANY of a name's rows carries the term (union across rows)", () => {
    // a name split into two link rows; surfaced_terms is per-CIK so the rows are uniform, but the union
    // is the recall-safe read — a match on either row counts.
    const rows = [
      mem({ security_id: "s-x", segment: "reactors", surfaced_terms: ["SMR"] }),
      mem({ security_id: "s-x", segment: "fuel", surfaced_terms: ["SMR"] }),
    ];
    expect(groupHasTerm(rows, "smr")).toBe(true);
    expect(groupHasTerm(rows, "haleu")).toBe(false);
  });

  it("an empty pick passes the group; an empty-provenance group never matches a pick", () => {
    const rows = [mem({ security_id: "s-y", surfaced_terms: [] })];
    expect(groupHasTerm(rows, "")).toBe(true);
    expect(groupHasTerm(rows, "smr")).toBe(false);
  });
});
