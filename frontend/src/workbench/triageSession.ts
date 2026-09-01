// The TRIAGE prune session — serialize the editor's FULL working state to one opaque JSON blob and rehydrate
// it. The backend (workbench/triage_store.py) is a dumb blob store: it never interprets `state`; this module
// owns and shapes it. The whole feature's correctness lives here — a field that fails to round-trip is a
// silently-lost exclusion/decision on the operator's next open (invariant obligation #3).
//
// Working state is split across two components — the `useChainDraft` hook (the structural prune: draft +
// excluded Set + reasons Map) and `ChainEditor` (the expensive draft-run buckets + term/set-aside decisions).
// This module carries BOTH. Sets serialize to arrays and the one Map to a Record; `deserialize` reverses it
// so the hydrate initializers get ready-to-use Set/Map values.

import type { BasketMember, DraftReportOut, ResolvedPlacement, TermSetEntry } from "../api/hooks";
import type { DraftCounts } from "./DraftStatusStrip";
import type { ChainDraft } from "./useChainDraft";

// Bump ONLY on a BREAKING shape change (a removed/renamed/re-typed field). An ADDITIVE change (a new optional
// field) must NOT bump — `deserialize` defaults missing fields, so old blobs keep restoring. A breaking bump
// sends older blobs to `status: "incompatible"`, which the editor surfaces (notice + keep-fresh-or-discard),
// NEVER a silent seed-fresh over a real prune.
export const SCHEMA_VERSION = 1;

/** The live hook working state (Set/Map form) — what `serialize` reads and `deserialize` reconstructs into. */
export interface HookRuntime {
  draft: ChainDraft;
  excluded: Set<string>;
  reasons: Map<string, string>;
  reasonsDirty: boolean;
}

// `origin` (the derived where-from chip) is optional — an older restored blob deserializes without it and the
// chip simply abstains (graceful-degrade; no SCHEMA_VERSION bump needed for an additive optional field).
type Identity = {
  sector?: string | null;
  exchange?: string | null;
  category?: string | null;
  origin?: string | null;
};
type DraftStatus = { counts: DraftCounts; report: DraftReportOut } | null;

/** The live ChainEditor working state (Set form for the six Sets). The expensive draft-run output that can't
 *  be re-derived without a fresh Opus draft, plus the term-set + set-aside decisions. */
export interface EditorRuntime {
  ambiguous: ResolvedPlacement[];
  verify: ResolvedPlacement[];
  absent: ResolvedPlacement[];
  verifyOrigin: Record<string, ResolvedPlacement>;
  matched: Record<string, string[]>;
  offUniverse: Set<string>;
  offThesisSet: Set<string>;
  identity: Record<string, Identity>;
  names: Record<string, string>;
  draftStatus: DraftStatus;
  cappedTerms: Set<string>;
  emptyTerms: Set<string>;
  draftEmpty: boolean;
  termSet: TermSetEntry[];
  recs: Record<string, { tier: string; reason: string }>;
  adopted: Set<string>;
  setAside: Set<string>;
  // CHERRY-PICK (pick-mode) — ADDITIVE working state (optional so pre-existing constructors and old blobs
  // stay valid; NO SCHEMA_VERSION bump — `deserialize` defaults them): the Recommended pile (genuinely-new
  // PLACED placements a pick-mode draft diverted for check-to-add instead of auto-loading; flat like
  // `verify` — a multi-link name carries N entries), the origin stash powering a picked name's send-back
  // (sid → the pile placements it left; the array-valued twin of `verifyOrigin`), and the operator's
  // explicit load-mode choice (null = untouched → the lane decides: ⚡ quick ⇒ pick, ✦ full ⇒ auto-load).
  recommended?: ResolvedPlacement[];
  recommendedOrigin?: Record<string, ResolvedPlacement[]>;
  pickPref?: boolean | null;
}

/** The serialized (JSON-clean) blob — the opaque `state` the backend stores verbatim. Sets → arrays, the one
 *  Map → Record; everything else is already JSON-native. */
export interface SerializedSession {
  hook: {
    draft: ChainDraft;
    excluded: string[];
    reasons: Record<string, string>;
    reasonsDirty: boolean;
  };
  editor: {
    ambiguous: ResolvedPlacement[];
    verify: ResolvedPlacement[];
    absent: ResolvedPlacement[];
    verifyOrigin: Record<string, ResolvedPlacement>;
    matched: Record<string, string[]>;
    offUniverse: string[];
    offThesisSet: string[];
    identity: Record<string, Identity>;
    names: Record<string, string>;
    draftStatus: DraftStatus;
    cappedTerms: string[];
    emptyTerms: string[];
    draftEmpty: boolean;
    termSet: TermSetEntry[];
    recs: Record<string, { tier: string; reason: string }>;
    adopted: string[];
    setAside: string[];
    // the cherry-pick fields (all JSON-native already) — optional in the blob: an old blob simply lacks them
    recommended?: ResolvedPlacement[];
    recommendedOrigin?: Record<string, ResolvedPlacement[]>;
    pickPref?: boolean | null;
  };
}

/** The restore result — a DISCRIMINATED union so a genuinely-absent session (the caller already knows from a
 *  null envelope) is NEVER conflated with an unreadable one. `incompatible` routes to the error-like path
 *  (notice + choice), not the empty path. */
export type DeserializeResult =
  | { status: "ok"; hook: HookRuntime; editor: EditorRuntime }
  | { status: "incompatible"; version: number };

/** A synthetic "cleared" restore for the Clear action: an EMPTY value chain + companies + draft-run buckets,
 *  but the term-set SEEDS are kept. Seeding the editor from this (instead of from the thesis's persisted
 *  basket) gives a blank canvas to re-draft, without losing the operator's SIGNAL/BROAD seeds. */
export function clearedRestore(
  termSet: TermSetEntry[],
): DeserializeResult & { status: "ok" } {
  return {
    status: "ok",
    hook: {
      draft: { segments: [], basket: [] },
      excluded: new Set(),
      reasons: new Map(),
      reasonsDirty: false,
    },
    editor: {
      ambiguous: [],
      verify: [],
      absent: [],
      verifyOrigin: {},
      matched: {},
      offUniverse: new Set(),
      offThesisSet: new Set(),
      identity: {},
      names: {},
      draftStatus: null,
      cappedTerms: new Set(),
      emptyTerms: new Set(),
      draftEmpty: false,
      termSet, // KEEP the seeds
      recs: {},
      adopted: new Set(),
      setAside: new Set(),
      recommended: [],
      recommendedOrigin: {},
      pickPref: null, // Clear resets working state — the load mode returns to the lane default
    },
  };
}

export function serialize(hook: HookRuntime, editor: EditorRuntime): SerializedSession {
  return {
    hook: {
      draft: hook.draft,
      excluded: [...hook.excluded],
      reasons: Object.fromEntries(hook.reasons),
      reasonsDirty: hook.reasonsDirty,
    },
    editor: {
      ambiguous: editor.ambiguous,
      verify: editor.verify,
      absent: editor.absent,
      verifyOrigin: editor.verifyOrigin,
      matched: editor.matched,
      offUniverse: [...editor.offUniverse],
      offThesisSet: [...editor.offThesisSet],
      identity: editor.identity,
      names: editor.names,
      draftStatus: editor.draftStatus,
      cappedTerms: [...editor.cappedTerms],
      emptyTerms: [...editor.emptyTerms],
      draftEmpty: editor.draftEmpty,
      termSet: editor.termSet,
      recs: editor.recs,
      adopted: [...editor.adopted],
      setAside: [...editor.setAside],
      recommended: editor.recommended ?? [],
      recommendedOrigin: editor.recommendedOrigin ?? {},
      pickPref: editor.pickPref ?? null,
    },
  };
}

// Defensive readers so a malformed blob defaults rather than throws (additive tolerance + resilience to a
// hand-corrupted file). A structurally-wrong `state` (not an object) falls to `incompatible` in `deserialize`.
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const rec = <T>(v: unknown): Record<string, T> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, T>) : {};
const strSet = (v: unknown): Set<string> => new Set(arr<string>(v));

// THE SESSION-BLOB RESURRECTION GUARD (S1 legacy normalize): a PRE-change autosaved blob carries basket
// members with the RETIRED `operator_set` (the old accept/hand-add authorship) and no `signed_off` field —
// restored verbatim, the next Save would write the retired value straight back over the 0035 reset. So a
// restore normalizes every member to the post-S1 shape: `operator_set` → `system_drafted` + `signed_off:
// true` (the 0035 rule — old accept meant ENDORSED, and the text stays the model's); any member missing
// `signed_off` defaults false. Additive tolerance (no SCHEMA_VERSION bump) — old blobs keep restoring.
// The promote-side legacy translation backstops whatever slips past this.
const normalizeMember = (m: BasketMember): BasketMember =>
  (m.authored_by as string) === "operator_set"
    ? { ...m, authored_by: "system_drafted", signed_off: true }
    : { ...m, signed_off: m.signed_off ?? false };

const normalizeDraft = (d: ChainDraft): ChainDraft => ({
  segments: d.segments ?? [],
  basket: (d.basket ?? []).map(normalizeMember),
});

// THE PRE-SCOPE REPORT NORMALIZE (the draft-scope PR-2 gap): `DraftReportOut.scope` is NON-optional in the
// generated TS (a server-defaulted field, hardened by openapi-typescript), but a blob autosaved BEFORE the
// field existed restores a report genuinely missing it at runtime — a shape the type says can't exist,
// round-tripped verbatim by every autosave until a new draft replaces it. Same restore-seam discipline as
// `normalizeMember` above: default it to "full" — NOT an invention, every pre-scope blob is definitionally
// a FULL draft (`seeds_only` did not exist when it was saved) — so the type is true at runtime everywhere
// and the blob self-heals on the next autosave. Additive tolerance (no SCHEMA_VERSION bump); "full"
// renders no badge, so the restored strip looks exactly as it did.
const normalizeDraftStatus = (v: unknown): DraftStatus => {
  const ds = (v as DraftStatus) ?? null;
  if (!ds?.report) return ds; // null (no run) — or a malformed report, passed through as before
  // the type says scope is always present; a pre-scope blob's report proves it wrong at runtime — heal it
  return { ...ds, report: { ...ds.report, scope: ds.report.scope ?? "full" } };
};

/** Reconstruct live working state from a stored session envelope. `schema_version` mismatch (a breaking bump)
 *  → `incompatible` (surfaced, never silently discarded); a same-version blob reconstructs with per-field
 *  defaults (so an additive field added later still restores). */
export function deserialize(session: {
  schema_version: number;
  state: unknown;
}): DeserializeResult {
  if (session.schema_version !== SCHEMA_VERSION) {
    return { status: "incompatible", version: session.schema_version };
  }
  const s = session.state;
  if (!s || typeof s !== "object") {
    // a present-but-structurally-broken blob is treated like an incompatible one — surface, never seed-fresh
    return { status: "incompatible", version: session.schema_version };
  }
  const state = s as Partial<SerializedSession>;
  const h: Partial<SerializedSession["hook"]> = state.hook ?? {};
  const e: Partial<SerializedSession["editor"]> = state.editor ?? {};
  return {
    status: "ok",
    hook: {
      // the legacy normalize rides the restore seam itself, so no consumer can see a retired value
      draft: normalizeDraft((h.draft as ChainDraft) ?? { segments: [], basket: [] }),
      excluded: strSet(h.excluded),
      reasons: new Map(Object.entries(rec<string>(h.reasons))),
      reasonsDirty: Boolean(h.reasonsDirty),
    },
    editor: {
      ambiguous: arr<ResolvedPlacement>(e.ambiguous),
      verify: arr<ResolvedPlacement>(e.verify),
      absent: arr<ResolvedPlacement>(e.absent),
      verifyOrigin: rec<ResolvedPlacement>(e.verifyOrigin),
      matched: rec<string[]>(e.matched),
      offUniverse: strSet(e.offUniverse),
      offThesisSet: strSet(e.offThesisSet),
      identity: rec<Identity>(e.identity),
      names: rec<string>(e.names),
      draftStatus: normalizeDraftStatus(e.draftStatus),
      cappedTerms: strSet(e.cappedTerms),
      emptyTerms: strSet(e.emptyTerms),
      draftEmpty: Boolean(e.draftEmpty),
      termSet: arr<TermSetEntry>(e.termSet),
      recs: rec<{ tier: string; reason: string }>(e.recs),
      adopted: strSet(e.adopted),
      setAside: strSet(e.setAside),
      // cherry-pick (additive): an old blob restores with an EMPTY pile + no explicit mode (lane default)
      recommended: arr<ResolvedPlacement>(e.recommended),
      recommendedOrigin: rec<ResolvedPlacement[]>(e.recommendedOrigin),
      pickPref: typeof e.pickPref === "boolean" ? e.pickPref : null,
    },
  };
}
