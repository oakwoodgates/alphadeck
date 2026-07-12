import { describe, expect, it } from "vitest";

import type { BasketMember, CallCardResponse, MemberCallOut, TriggerRefOut } from "../../api/hooks";
import { groupBasket, nameKeyFor, resolveNameKey } from "../buckets";

// --- fixture builders (loose partials cast to the wire types — test-only) ----------------------
function member(ticker: string, sid: string | null, over: Partial<BasketMember> = {}): BasketMember {
  return { ticker, role: "core", authored_by: "operator_set", security_id: sid, ...over } as BasketMember;
}

function mcall(sid: string, over: Partial<MemberCallOut> = {}): MemberCallOut {
  return { security_id: sid, ticker: null, lapsing: false, theme_armed: false, triggers: [], ...over } as MemberCallOut;
}

function trigger(ticker: string): TriggerRefOut {
  return { label: `fired on ${ticker}`, kind: "insider", ticker, sources: [] } as unknown as TriggerRefOut;
}

function card(over: Partial<CallCardResponse> = {}): CallCardResponse {
  return {
    thesis_id: "t1",
    asof: "2026-07-11",
    state: "armed",
    verdict: "core_entry",
    expression: "",
    catalyst_surface: [],
    key_conviction: { turned: true, label: "Conviction", detail: "" },
    key_confirmation: { turned: true, label: "Confirmation", detail: "" },
    triggers_fired: [],
    risk_signals: [],
    missing: [],
    counter_case: "",
    armed_members: [],
    watch_members: [],
    ...over,
  } as CallCardResponse;
}

const keysOf = (groups: ReturnType<typeof groupBasket>) => groups.map((g) => g.def.key);
const tickersIn = (groups: ReturnType<typeof groupBasket>, key: string) =>
  groups.find((g) => g.def.key === key)?.rows.map((r) => r.member.ticker) ?? [];

describe("groupBasket — the per-name bucket derivation", () => {
  it("partitions the armed family by flag precedence: lapsing beats theme_armed, plain armed is neither", () => {
    const basket = [member("A", "sa"), member("B", "sb"), member("C", "sc"), member("D", "sd")];
    const c = card({
      armed_members: [
        mcall("sa", { verdict: "core_entry" }),
        mcall("sb", { verdict: "starter_entry", lapsing: true }),
        mcall("sc", { verdict: "starter_entry", theme_armed: true }),
        // BOTH flags true → the clock is the urgent fact: Lapsing wins the bucket
        mcall("sd", { verdict: "starter_entry", lapsing: true, theme_armed: true }),
      ],
    });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["armed", "lapsing", "theme_armed"]);
    expect(tickersIn(groups, "armed")).toEqual(["A"]);
    expect(tickersIn(groups, "lapsing")).toEqual(["B", "D"]);
    expect(tickersIn(groups, "theme_armed")).toEqual(["C"]);
  });

  it("files a member whose verdict reads managing under Managing (render-if-present)", () => {
    const basket = [member("HELD", "sh"), member("A", "sa")];
    const c = card({
      armed_members: [mcall("sa", { verdict: "core_entry" }), mcall("sh", { verdict: "managing" })],
    });
    const groups = groupBasket(basket, c, []);
    // Managing displays first (strongest → weakest), regardless of wire rank
    expect(keysOf(groups)).toEqual(["managing", "armed"]);
    expect(tickersIn(groups, "managing")).toEqual(["HELD"]);
  });

  it("keeps a watch member in Watch even though its breakout is itself in triggers_fired", () => {
    const basket = [member("W", "sw")];
    const c = card({
      watch_members: [mcall("sw")],
      triggers_fired: [trigger("W")], // the confirmation firing rides the thesis trigger list too
    });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["watch"]);
  });

  it("warms a conviction-only name via the triggers_fired ticker join (in neither member list)", () => {
    // XE fired a conviction trigger but has no confirmation → the wire puts it in NEITHER
    // armed_members NOR watch_members; without this bucket it would misfile as "no signals".
    const basket = [member("XE", "sx"), member("XE", null), member("Q", "sq")];
    const c = card({ triggers_fired: [trigger("XE")] });
    const groups = groupBasket(basket, c, []);
    // the ticker join is deliberate over-inclusion: BOTH XE rows light (visible, never silent)
    expect(tickersIn(groups, "warming")).toEqual(["XE", "XE"]);
    expect(tickersIn(groups, "quiet")).toEqual(["Q"]);
  });

  it("lands every basket row somewhere — the remainder (incl. unresolved rows) is Quiet", () => {
    const basket = [
      member("A", "sa"),
      member("Eagle Nuclear Energy Corp.", null), // unresolved: no security_id
      member("B", null),
    ];
    const c = card({ armed_members: [mcall("sa", { verdict: "core_entry" })] });
    const groups = groupBasket(basket, c, []);
    const total = groups.reduce((n, g) => n + g.rows.length, 0);
    expect(total).toBe(basket.length);
    expect(tickersIn(groups, "quiet")).toEqual(["Eagle Nuclear Energy Corp.", "B"]);
  });

  it("preserves the wire's own ranking within armed buckets and basket order within Quiet", () => {
    // the call machinery already ranked armed_members (freshness band, grade within) — never re-rank
    const basket = [member("A", "sa"), member("B", "sb"), member("Z2", null), member("Z1", null)];
    const c = card({
      armed_members: [mcall("sb", { verdict: "core_entry" }), mcall("sa", { verdict: "core_entry" })],
    });
    const groups = groupBasket(basket, c, []);
    expect(tickersIn(groups, "armed")).toEqual(["B", "A"]); // wire rank, not basket order
    expect(tickersIn(groups, "quiet")).toEqual(["Z2", "Z1"]); // authored basket order
  });

  it("joins duplicate security_id rows to the same member call — both render, ordinals distinct", () => {
    const basket = [member("LTBR", "sl"), member("LTBR", "sl")];
    const c = card({ armed_members: [mcall("sl", { verdict: "starter_entry", exit_by: "2026-11-11" })] });
    const groups = groupBasket(basket, c, []);
    const rows = groups.find((g) => g.def.key === "armed")?.rows ?? [];
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.ordinal)).toEqual([0, 1]);
    expect(rows.every((r) => r.call?.exit_by === "2026-11-11")).toBe(true);
  });

  it("reads everything as Quiet while there is no card (loading / error) — honestly, not loudly", () => {
    const basket = [member("A", "sa"), member("B", null)];
    const groups = groupBasket(basket, undefined, undefined);
    expect(keysOf(groups)).toEqual(["quiet"]);
    expect(groups[0].rows).toHaveLength(2);
  });

  it("bridges the scored member (name / market cap) by security_id onto the row", () => {
    const basket = [member("A", "sa"), member("B", null)];
    const scored = [
      { security_id: "sa", name: "Alpha Corp.", market_cap: { value: 3.2e9, provenance: [] } },
    ] as never[];
    const groups = groupBasket(basket, undefined, scored);
    const rows = groups[0].rows;
    expect(rows[0].scored?.name).toBe("Alpha Corp.");
    expect(rows[1].scored).toBeNull(); // no security_id → no bridge, never a guess
  });

  it("omits empty buckets entirely — a header over nothing is noise", () => {
    const basket = [member("A", "sa")];
    const c = card({ armed_members: [mcall("sa", { verdict: "core_entry" })] });
    expect(keysOf(groupBasket(basket, c, []))).toEqual(["armed"]);
  });
});

describe("nameKeyFor — the ?name= URL key for a cockpit row", () => {
  const rowFor = (groups: ReturnType<typeof groupBasket>, ticker: string, sid: string | null) =>
    groups.flatMap((g) => g.rows).find((r) => r.member.ticker === ticker && r.member.security_id === sid)!;

  it("uses the ticker when it is unique in the basket (the readable common case)", () => {
    const basket = [member("A", "sa"), member("B", "sb")];
    const groups = groupBasket(basket, undefined, []);
    expect(nameKeyFor(rowFor(groups, "A", "sa"), basket)).toBe("A");
  });

  it("uses the security_id when the ticker duplicates (precise across the duplicates)", () => {
    const basket = [member("A", "sa1"), member("A", "sa2")];
    const groups = groupBasket(basket, undefined, []);
    expect(nameKeyFor(rowFor(groups, "A", "sa1"), basket)).toBe("sa1");
    expect(nameKeyFor(rowFor(groups, "A", "sa2"), basket)).toBe("sa2");
  });

  it("degrades to the ticker for a duplicate with NO security_id (first-match territory)", () => {
    const basket = [member("A", null), member("A", "sa2")];
    const groups = groupBasket(basket, undefined, []);
    expect(nameKeyFor(rowFor(groups, "A", null), basket)).toBe("A");
    expect(nameKeyFor(rowFor(groups, "A", "sa2"), basket)).toBe("sa2");
  });
});

describe("resolveNameKey — ?name= back to the display row", () => {
  it("matches security_id first, even when a ticker also matches the key text", () => {
    // pathological on purpose: a member whose TICKER equals another member's SID
    const basket = [member("sb", "sa"), member("B", "sb")];
    const groups = groupBasket(basket, undefined, []);
    expect(resolveNameKey(groups, "sb")?.row.member.security_id).toBe("sb");
  });

  it("matches the ticker case-insensitively", () => {
    const basket = [member("OKLO", "s1")];
    const groups = groupBasket(basket, undefined, []);
    expect(resolveNameKey(groups, "oklo")?.row.member.ticker).toBe("OKLO");
    expect(resolveNameKey(groups, "OKLO")?.row.member.ticker).toBe("OKLO");
  });

  it("breaks duplicate-ticker ties by the authored basket order (lowest ordinal)", () => {
    const basket = [member("A", null), member("A", null)];
    const groups = groupBasket(basket, undefined, []);
    expect(resolveNameKey(groups, "A")?.row.ordinal).toBe(0);
  });

  it("returns null for an unknown or absent key — the panel simply doesn't render", () => {
    const basket = [member("A", "sa")];
    const groups = groupBasket(basket, undefined, []);
    expect(resolveNameKey(groups, "NOSUCH")).toBeNull();
    expect(resolveNameKey(groups, null)).toBeNull();
    expect(resolveNameKey(groups, undefined)).toBeNull();
    expect(resolveNameKey(groups, "")).toBeNull();
  });

  it("carries the row's bucket def so the panel opens with the right accent", () => {
    const basket = [member("A", "sa")];
    const c = card({ armed_members: [mcall("sa", { verdict: "core_entry" })] });
    const groups = groupBasket(basket, c, []);
    expect(resolveNameKey(groups, "A")?.def.key).toBe("armed");
  });
});
