import { describe, expect, it } from "vitest";

import type { DraftReportOut, ResolvedPlacement, TermSetEntry } from "../../api/hooks";
import {
  clearedRestore,
  deserialize,
  SCHEMA_VERSION,
  serialize,
  type EditorRuntime,
  type HookRuntime,
} from "../triageSession";

// A fully-populated working state — every Set and Map non-empty — so the round-trip proves EVERY field
// survives, not just the JSON-native ones. A dropped field here = a silently-lost decision on restore.
function fullHook(): HookRuntime {
  return {
    draft: {
      segments: [{ label: "Enrichment", descriptor: "SMR fuel" }],
      basket: [
        {
          ticker: "OKLO",
          role: "leader",
          archetype: "leader",
          security_id: "sid-1",
          detail: null,
          segment: "Enrichment",
          thesis_fit: "core name",
          conviction: 4,
          authored_by: "operator_set",
        },
      ],
    },
    excluded: new Set(["sid-2", "sid-3"]),
    reasons: new Map([
      ["sid-2", "off-thesis"],
      ["sid-3", "too small"],
    ]),
    reasonsDirty: true,
  };
}

function fullEditor(): EditorRuntime {
  const p = (name: string): ResolvedPlacement => ({ name, ticker: name }) as ResolvedPlacement;
  const term: TermSetEntry = { term: "nuclear", tier: "signal", authored_by: "operator_set" };
  const report: DraftReportOut = {
    coverage: { pages_ok: 4, pages_attempted: 4, failed_terms: [] },
    capped_terms: [],
    tail_sweep: "skipped",
    narration_needed: 0,
    narration_filled: 0,
  };
  return {
    ambiguous: [p("Amb Co")],
    verify: [p("Ghost Co")],
    absent: [p("Missing Co")],
    verifyOrigin: { "sid-9": p("Origin Co") },
    matched: { "sid-1": ["nuclear"] },
    offUniverse: new Set(["sid-5"]),
    offThesisSet: new Set(["sid-2"]),
    identity: { "sid-1": { sector: "Utilities", exchange: "NYSE", category: null } },
    names: { "sid-1": "Oklo Inc." },
    draftStatus: { counts: { placed: 1, verify: 1, ambiguous: 1, absent: 1 }, report },
    cappedTerms: new Set(["nuclear power"]),
    draftEmpty: false,
    termSet: [term],
    recs: { nuclear: { tier: "signal", reason: "core" } },
    adopted: new Set(["thorium"]),
    setAside: new Set(["sid-7"]),
  };
}

// Wrap the serialized state in a stored envelope + push it through JSON (the wire round-trip), exactly as a
// PUT → GET does.
function throughWire(hook: HookRuntime, editor: EditorRuntime) {
  const state = JSON.parse(JSON.stringify(serialize(hook, editor)));
  return deserialize({ schema_version: SCHEMA_VERSION, state });
}

describe("triageSession serialize/deserialize", () => {
  it("round-trips every working-state field losslessly (Sets and Map included)", () => {
    const hook = fullHook();
    const editor = fullEditor();
    const result = throughWire(hook, editor);

    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;

    // hook section — structural prune + the Set and the Map
    expect(result.hook.draft).toEqual(hook.draft);
    expect(result.hook.excluded).toEqual(hook.excluded); // Set → array → Set
    expect(result.hook.reasons).toEqual(hook.reasons); // Map → Record → Map
    expect(result.hook.reasonsDirty).toBe(true);

    // editor section — the six Sets reconstruct, the Records/arrays survive
    expect(result.editor.offUniverse).toEqual(editor.offUniverse);
    expect(result.editor.offThesisSet).toEqual(editor.offThesisSet);
    expect(result.editor.cappedTerms).toEqual(editor.cappedTerms);
    expect(result.editor.adopted).toEqual(editor.adopted);
    expect(result.editor.setAside).toEqual(editor.setAside);
    expect(result.editor.ambiguous).toEqual(editor.ambiguous);
    expect(result.editor.verify).toEqual(editor.verify);
    expect(result.editor.absent).toEqual(editor.absent);
    expect(result.editor.verifyOrigin).toEqual(editor.verifyOrigin);
    expect(result.editor.matched).toEqual(editor.matched);
    expect(result.editor.identity).toEqual(editor.identity);
    expect(result.editor.names).toEqual(editor.names);
    expect(result.editor.draftStatus).toEqual(editor.draftStatus);
    expect(result.editor.draftEmpty).toBe(false);
    expect(result.editor.termSet).toEqual(editor.termSet);
    expect(result.editor.recs).toEqual(editor.recs);
  });

  it("flags a breaking schema_version as incompatible (never a silent seed-fresh over a real prune)", () => {
    const state = JSON.parse(JSON.stringify(serialize(fullHook(), fullEditor())));
    const result = deserialize({ schema_version: SCHEMA_VERSION + 1, state });
    expect(result).toEqual({ status: "incompatible", version: SCHEMA_VERSION + 1 });
  });

  it("tolerates an additive-missing field within the same version (defaults, still ok)", () => {
    // simulate an OLD blob written before a field existed: drop editor.setAside entirely
    const state = JSON.parse(JSON.stringify(serialize(fullHook(), fullEditor())));
    delete state.editor.setAside;
    const result = deserialize({ schema_version: SCHEMA_VERSION, state });
    expect(result.status).toBe("ok");
    if (result.status !== "ok") return;
    expect(result.editor.setAside).toEqual(new Set()); // defaulted, not thrown
  });

  it("treats a structurally-broken state as incompatible, not empty", () => {
    expect(deserialize({ schema_version: SCHEMA_VERSION, state: null }).status).toBe("incompatible");
    expect(deserialize({ schema_version: SCHEMA_VERSION, state: 42 }).status).toBe("incompatible");
  });
});

describe("clearedRestore (the Clear action)", () => {
  it("empties the chain, companies and buckets but KEEPS the term-set seeds", () => {
    const seeds: TermSetEntry[] = [
      { term: "psilocybin", tier: "signal", authored_by: "operator_set" },
      { term: "ketamine", tier: "broad", authored_by: "operator_set" },
    ];
    const r = clearedRestore(seeds);
    expect(r.status).toBe("ok");
    // chain + companies empty
    expect(r.hook.draft).toEqual({ segments: [], basket: [] });
    expect(r.hook.excluded.size).toBe(0);
    expect(r.hook.reasons.size).toBe(0);
    // draft-run buckets empty
    expect(r.editor.verify).toEqual([]);
    expect(r.editor.ambiguous).toEqual([]);
    expect(r.editor.absent).toEqual([]);
    expect(r.editor.setAside.size).toBe(0);
    expect(r.editor.names).toEqual({});
    // the seeds survive
    expect(r.editor.termSet).toBe(seeds);
  });
});
