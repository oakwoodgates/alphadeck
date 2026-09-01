import { describe, expect, it } from "vitest";

import type { ResolvedPlacement } from "../../api/hooks";
import { clearedRestore, deserialize, SCHEMA_VERSION, serialize } from "../triageSession";

// CHERRY-PICK (PR-3) — the three ADDITIVE session fields: the Recommended pile, the pick-origin map, and
// the explicit load-mode choice. All must round-trip losslessly WITHOUT a SCHEMA_VERSION bump, and an old
// blob (written before the fields existed) must keep restoring — with an empty pile, never an invented one.

const p = (name: string, sid: string, segment = "reactors"): ResolvedPlacement =>
  ({
    name,
    ticker: name,
    prose: `${name} prose`,
    segment,
    status: "placed",
    security_id: sid,
    candidates: [],
    matched_terms: ["psilocybin"],
  }) as unknown as ResolvedPlacement;

// a minimal populated state off the cleared baseline (the other fields have their own round-trip suite)
function stateWith(over: Record<string, unknown>) {
  const seeded = clearedRestore([]);
  Object.assign(seeded.editor, over);
  return JSON.parse(JSON.stringify(serialize(seeded.hook, seeded.editor)));
}

describe("triageSession — the cherry-pick fields (additive; NO SCHEMA_VERSION bump)", () => {
  it("SCHEMA_VERSION is UNCHANGED (still 1) — the fields are additive by contract", () => {
    expect(SCHEMA_VERSION).toBe(1);
  });

  it("pile + origin map + mode survive serialize → JSON → deserialize (a multi-link name keeps its N entries)", () => {
    const pile = [p("SMR", "sid-smr", "reactors"), p("SMR", "sid-smr", "fuel"), p("GEV", "sid-gev")];
    const origin = { "sid-ccj": [p("CCJ", "sid-ccj")] };
    const state = stateWith({ recommended: pile, recommendedOrigin: origin, pickPref: true });

    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.editor.recommended).toEqual(pile); // flat placements, order + duplicates-by-sid intact
    expect(result.editor.recommendedOrigin).toEqual(origin);
    expect(result.editor.pickPref).toBe(true);
  });

  it("an explicit 'auto-load all' (pickPref=false) round-trips as FALSE — never collapsed into the null lane-default", () => {
    const state = stateWith({ pickPref: false });
    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.editor.pickPref).toBe(false);
  });

  it("an OLD blob (fields absent) restores with an empty pile, an empty origin map, and no explicit mode", () => {
    const state = stateWith({});
    delete state.editor.recommended;
    delete state.editor.recommendedOrigin;
    delete state.editor.pickPref;

    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.editor.recommended).toEqual([]); // defaulted, not thrown — and never invented
    expect(result.editor.recommendedOrigin).toEqual({});
    expect(result.editor.pickPref).toBeNull(); // untouched ⇒ the lane decides
  });

  it("clearedRestore (the Clear action) empties the pile and resets the mode to the lane default", () => {
    const r = clearedRestore([]);
    expect(r.editor.recommended).toEqual([]);
    expect(r.editor.recommendedOrigin).toEqual({});
    expect(r.editor.pickPref).toBeNull();
  });
});
