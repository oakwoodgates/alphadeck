import { describe, expect, it } from "vitest";

import {
  clearedRestore,
  deserialize,
  SCHEMA_VERSION,
  serialize,
  type HookRuntime,
} from "../triageSession";

// THE PLACED MODE (Research ⇄ Pick) — the two ADDITIVE hook fields: the pick selection + the mode. Both must
// round-trip losslessly WITHOUT a SCHEMA_VERSION bump (the `pickPref` precedent), and an old blob (written
// before the fields existed) must keep restoring byte-identical to today: research, empty selection.

const hook = (over: Partial<HookRuntime> = {}): HookRuntime => ({
  draft: { segments: [], basket: [] },
  excluded: new Set(),
  reasons: new Map(),
  reasonsDirty: false,
  ...over,
});

// through the wire (PUT → GET), exactly as the app round-trips
const wire = (h: HookRuntime) => JSON.parse(JSON.stringify(serialize(h, clearedRestore([]).editor)));
const restore = (state: unknown) => {
  const r = deserialize({ schema_version: SCHEMA_VERSION, state });
  if (r.status !== "ok") throw new Error("expected ok");
  return r.hook;
};

describe("triageSession — the placed-mode fields (additive; NO SCHEMA_VERSION bump)", () => {
  it("SCHEMA_VERSION is UNCHANGED (still 1) — the fields are additive by contract", () => {
    expect(SCHEMA_VERSION).toBe(1);
  });

  it("selected + placedMode survive serialize → JSON → deserialize (pick)", () => {
    const h = restore(wire(hook({ selected: new Set(["sid-a", "sid-b"]), placedMode: "pick" })));
    expect(h.selected).toEqual(new Set(["sid-a", "sid-b"])); // Set → array → Set
    expect(h.placedMode).toBe("pick");
  });

  it("…and research with an empty selection round-trips as itself (never collapsed into pick)", () => {
    const h = restore(wire(hook({ selected: new Set(), placedMode: "research" })));
    expect(h.selected).toEqual(new Set());
    expect(h.placedMode).toBe("research");
  });

  it("a hook WITHOUT the fields (a pre-existing constructor) serializes the defaults — research, []", () => {
    const state = wire(hook());
    expect(state.hook.selected).toEqual([]);
    expect(state.hook.placedMode).toBe("research");
  });

  it("an OLD blob (fields absent) restores as research + an empty selection — byte-identical to today", () => {
    const state = wire(hook({ excluded: new Set(["sid-x"]) }));
    delete state.hook.selected;
    delete state.hook.placedMode;
    const h = restore(state);
    expect(h.selected).toEqual(new Set()); // defaulted, never invented
    expect(h.placedMode).toBe("research");
    expect(h.excluded).toEqual(new Set(["sid-x"])); // the prune untouched
  });

  it("a malformed value never invents a pick session: anything but the literal 'pick' is research; a non-array selection is ∅", () => {
    for (const bogus of ["bogus", "PICK", 42, null, true, {}]) {
      const state = wire(hook());
      state.hook.placedMode = bogus;
      expect(restore(state).placedMode).toBe("research");
    }
    const state = wire(hook());
    state.hook.selected = "sid-a";
    expect(restore(state).selected).toEqual(new Set());
  });

  it("clearedRestore (the Clear action) resets to research with an empty selection", () => {
    const r = clearedRestore([]);
    expect(r.hook.selected).toEqual(new Set());
    expect(r.hook.placedMode).toBe("research");
  });
});
