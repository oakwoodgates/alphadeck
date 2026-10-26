import { describe, expect, it } from "vitest";

import type {
  BasketMember,
  CallCardResponse,
  MemberCallOut,
  Segment,
  TriggerRefOut,
} from "../../api/hooks";
import {
  BUCKETS,
  dedupeBySecurityId,
  groupBasket,
  groupBySegment,
  nameKeyFor,
  resolveNameKey,
  UNSEGMENTED_KEY,
  type SegmentGroup,
} from "../buckets";

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

describe("dedupeBySecurityId — one row per name for the NON-segment lenses", () => {
  const rows = (groups: ReturnType<typeof groupBasket>) => groups.flatMap((g) => g.rows);

  it("collapses a multi-segment name (same security_id, N rows) to a single first-occurrence row", () => {
    // the Rainbow Rush shape: KTTA sits in two value-chain links -> two identical BasketMember rows
    const basket = [
      member("KTTA", "sk", { segment: "Ketamine Clinics & Therapy Delivery" }),
      member("KTTA", "sk", { segment: "Psychedelic & Ketamine Drug Developers" }),
      member("SOLO", "ss"),
    ];
    const out = rows(dedupeBySecurityId(groupBasket(basket, undefined, [])));
    expect(out.map((r) => [r.member.ticker, r.member.security_id])).toEqual([
      ["KTTA", "sk"],
      ["SOLO", "ss"],
    ]);
    expect(out[0].ordinal).toBe(0); // the FIRST occurrence (lowest ordinal) survives
  });

  it("keeps a duplicate TICKER with DISTINCT security_ids separate (different companies)", () => {
    const basket = [member("DUP", "s1"), member("DUP", "s2")];
    expect(rows(dedupeBySecurityId(groupBasket(basket, undefined, []))).map((r) => r.member.security_id)).toEqual([
      "s1",
      "s2",
    ]);
  });

  it("NEVER dedupes rows with no security_id — recall-safe over-include", () => {
    const basket = [member("GHOST", null), member("GHOST", null)];
    expect(rows(dedupeBySecurityId(groupBasket(basket, undefined, [])))).toHaveLength(2);
  });

  it("is a no-op when every name is unique (the common thesis)", () => {
    const basket = [member("A", "sa"), member("B", "sb"), member("C", null)];
    expect(rows(dedupeBySecurityId(groupBasket(basket, undefined, [])))).toHaveLength(3);
  });
});

describe("groupBySegment — the value-chain lens", () => {
  const seg = (label: string, descriptor: string | null = null): Segment => ({ label, descriptor });
  const shape = (out: SegmentGroup[]) =>
    out.map((g) => [g.key, g.rows.map((r) => r.row.member.ticker)] as const);

  it("places a multi-segment name under EACH of its links (show them all — NOT deduped)", () => {
    const basket = [
      member("KTTA", "sk", { segment: "Clinics" }),
      member("KTTA", "sk", { segment: "Developers" }),
      member("ATAI", "sa", { segment: "Developers" }),
    ];
    const out = groupBySegment(groupBasket(basket, undefined, []), [seg("Clinics"), seg("Developers")]);
    expect(shape(out)).toEqual([
      ["Clinics", ["KTTA"]],
      ["Developers", ["KTTA", "ATAI"]],
    ]);
  });

  it("orders groups by the thesis's authored segment order and OMITS empty links", () => {
    const basket = [member("A", "sa", { segment: "Third" }), member("B", "sb", { segment: "First" })];
    const out = groupBySegment(groupBasket(basket, undefined, []), [
      seg("First"),
      seg("Second"), // no members -> omitted (a header over nothing is noise)
      seg("Third"),
    ]);
    expect(out.map((g) => g.key)).toEqual(["First", "Third"]);
  });

  it("surfaces the segment descriptor as the header hint", () => {
    const out = groupBySegment(
      groupBasket([member("A", "sa", { segment: "Clinics" })], undefined, []),
      [seg("Clinics", "catalyst-rich")],
    );
    expect(out[0].descriptor).toBe("catalyst-rich");
  });

  it("collects null AND empty-string segments into a keep-visible 'Unsegmented' group, LAST", () => {
    const basket = [
      member("A", "sa", { segment: "Clinics" }),
      member("B", "sb"), // null segment
      member("C", "sc", { segment: "" }), // empty string -> unsegmented too
    ];
    const out = groupBySegment(groupBasket(basket, undefined, []), [seg("Clinics")]);
    expect(out.map((g) => [g.key, g.label])).toEqual([
      ["Clinics", "Clinics"],
      [UNSEGMENTED_KEY, "Unsegmented"],
    ]);
    expect(out[1].rows.map((r) => r.row.member.ticker)).toEqual(["B", "C"]);
  });

  it("preserves each row's call-state def so the state dot survives the lens", () => {
    const c = card({ armed_members: [mcall("sa", { verdict: "core_entry" })] });
    const out = groupBySegment(
      groupBasket([member("A", "sa", { segment: "Clinics" })], c, []),
      [seg("Clinics")],
    );
    expect(out[0].rows[0].def.key).toBe("armed");
  });

  it("renders a label present on a row but ABSENT from the list after the knowns (never dropped)", () => {
    const basket = [member("A", "sa", { segment: "Known" }), member("B", "sb", { segment: "Rogue" })];
    const out = groupBySegment(groupBasket(basket, undefined, []), [seg("Known")]);
    expect(out.map((g) => g.key)).toEqual(["Known", "Rogue"]); // "Rogue" over-included, never lost
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

// --- the two SILENT links (Context / Bench): never a call bucket ----------------------------------------
describe("groupBasket — Context and Bench buckets", () => {
  it("the buckets run … Quiet, Context, Bench — the silent pair after the quietest call bucket", () => {
    expect(BUCKETS.map((b) => b.key)).toEqual([
      "managing", "armed", "lapsing", "theme_armed", "warming", "watch", "quiet", "context", "bench",
    ]);
    expect(BUCKETS.slice(-2).map((b) => [b.label, b.hint])).toEqual([
      ["Context", "never counts toward the call"],
      ["Bench", "never counts toward the call"],
    ]);
  });

  it("files a silent-link name under its link's bucket, after Quiet, instead of a call-derived one", () => {
    const basket = [
      member("A", "sa"),
      member("C", "sc", { segment: "Context" }),
      member("B", "sb", { segment: "Bench" }),
      member("Q", "sq", { segment: "Clinics" }),
    ];
    const c = card({ armed_members: [mcall("sa", { verdict: "core_entry" })] });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["armed", "quiet", "context", "bench"]);
    expect(tickersIn(groups, "context")).toEqual(["C"]);
    expect(tickersIn(groups, "bench")).toEqual(["B"]);
    expect(tickersIn(groups, "quiet")).toEqual(["Q"]);
  });

  it("omits an empty silent bucket (a header over nothing is noise)", () => {
    const basket = [member("A", "sa"), member("C", "sc", { segment: "Context" })];
    expect(keysOf(groupBasket(basket, card(), []))).toEqual(["quiet", "context"]);
    expect(keysOf(groupBasket([member("A", "sa")], card(), []))).toEqual(["quiet"]);
  });

  // C1 (#6): the roster is TODAY's, but the card can be a history recompute at a PAST as-of. A name that voted
  // then and sits in Context now must still read as the voter the headline names — its call bucket, WITH its call.
  it("a Context name the card has an ARMED hit for lands in Armed WITH its call (scrub-back to when it voted)", () => {
    const basket = [member("C", "sc", { segment: "Context" }), member("B", "sb", { segment: "Bench" })];
    const c = card({ armed_members: [mcall("sc", { verdict: "starter_entry" }), mcall("sb", { verdict: "starter_entry", lapsing: true })] });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["armed", "lapsing"]);
    expect(groups[0].rows[0].member.ticker).toBe("C");
    expect(groups[0].rows[0].call?.verdict).toBe("starter_entry"); // the call is kept, not nulled
    expect(tickersIn(groups, "lapsing")).toEqual(["B"]); // the armed family's flags still split it
  });

  it("a silent name with a WATCH hit lands in Watch; one with only a fired trigger lands in Warming", () => {
    const basket = [member("W", "sw", { segment: "Context" }), member("T", "st", { segment: "Bench" })];
    const c = card({
      watch_members: [mcall("sw", { verdict: null })],
      triggers_fired: [trigger("T")],
    });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["warming", "watch"]);
    expect(groups.find((g) => g.def.key === "watch")?.rows[0].call).not.toBeNull();
  });

  it("a silent name the card has NOTHING on (no hit, no fired trigger) lands in its silent bucket with no call", () => {
    const basket = [
      member("C", "sc", { segment: "Context" }),
      member("B", "sb", { segment: "Bench" }),
      member("V", "sv"), // a voter, armed — the card is NOT empty
    ];
    const c = card({ armed_members: [mcall("sv", { verdict: "core_entry" })], triggers_fired: [trigger("V")] });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["armed", "context", "bench"]);
    expect(groups.flatMap((g) => g.rows).filter((r) => r.bucket === "context" || r.bucket === "bench").every((r) => r.call === null)).toBe(true);
  });

  it("a HELD (managing) silent name stays in Managing — it never leaves the top", () => {
    const basket = [member("HELD", "sh", { segment: "Bench" }), member("C", "sc", { segment: "Context" })];
    const c = card({ armed_members: [mcall("sh", { verdict: "managing" })] });
    const groups = groupBasket(basket, c, []);
    expect(keysOf(groups)).toEqual(["managing", "context"]);
    expect(tickersIn(groups, "managing")).toEqual(["HELD"]);
    expect(groups[0].rows[0].call?.verdict).toBe("managing"); // its (held) call is kept
  });

  it("keeps authored basket order within a silent bucket", () => {
    const basket = [
      member("C2", "s2", { segment: "Context" }),
      member("C1", "s1", { segment: "Context" }),
    ];
    expect(tickersIn(groupBasket(basket, card(), []), "context")).toEqual(["C2", "C1"]);
  });

  it("matches the link EXACTLY: a lowercase look-alike is an ordinary (Quiet) name", () => {
    const groups = groupBasket([member("X", "sx", { segment: "context" })], card(), []);
    expect(keysOf(groups)).toEqual(["quiet"]);
  });

  it("dedupes by security_id like any bucket (one silent row per name by construction)", () => {
    const basket = [member("C", "sc", { segment: "Context" })];
    const out = dedupeBySecurityId(groupBasket(basket, card(), []));
    expect(out.flatMap((g) => g.rows)).toHaveLength(1);
  });
});

describe("groupBySegment — Context and Bench are the last two groups", () => {
  const seg = (label: string, descriptor: string | null = null): Segment => ({ label, descriptor });
  const keys = (out: SegmentGroup[]) => out.map((g) => g.key);

  it("last even when the payload's segments came in another order (or without the pair at all)", () => {
    const basket = [
      member("B", "sb", { segment: "Bench" }),
      member("A", "sa", { segment: "Clinics" }),
      member("C", "sc", { segment: "Context" }),
    ];
    const groups = groupBasket(basket, undefined, []);
    expect(keys(groupBySegment(groups, [seg("Bench"), seg("Clinics"), seg("Context")]))).toEqual([
      "Clinics", "Context", "Bench",
    ]);
    expect(keys(groupBySegment(groups, [seg("Clinics")]))).toEqual(["Clinics", "Context", "Bench"]);
  });

  it("comes after the unknown labels AND the Unsegmented tail — literally the last two", () => {
    const basket = [
      member("C", "sc", { segment: "Context" }),
      member("R", "sr", { segment: "Rogue" }),
      member("U", "su"),
      member("A", "sa", { segment: "Clinics" }),
      member("B", "sb", { segment: "Bench" }),
    ];
    const out = groupBySegment(groupBasket(basket, undefined, []), [seg("Clinics")]);
    expect(keys(out)).toEqual(["Clinics", "Rogue", UNSEGMENTED_KEY, "Context", "Bench"]);
  });

  it("carries the FIXED descriptors as the header hint, whatever the payload said", () => {
    const basket = [member("C", "sc", { segment: "Context" }), member("B", "sb", { segment: "Bench" })];
    const out = groupBySegment(groupBasket(basket, undefined, []), [seg("Context", "stale"), seg("Bench")]);
    expect(out.map((g) => g.descriptor)).toEqual([
      "Watched for context: buyers, counter-case names, giants where the theme is a slice. Never counts toward the call.",
      "Speculative names: pivots, distressed or thinly traded. Never counts toward the call.",
    ]);
  });

  it("omits an empty silent group", () => {
    const basket = [member("C", "sc", { segment: "Context" }), member("A", "sa", { segment: "Clinics" })];
    expect(keys(groupBySegment(groupBasket(basket, undefined, []), [seg("Clinics")]))).toEqual([
      "Clinics", "Context",
    ]);
  });

  it("a case-variant label ('context') is not the silent link — it renders as an ordinary unknown label", () => {
    const basket = [member("X", "sx", { segment: "context" }), member("C", "sc", { segment: "Context" })];
    expect(keys(groupBySegment(groupBasket(basket, undefined, []), []))).toEqual(["context", "Context"]);
  });
});
