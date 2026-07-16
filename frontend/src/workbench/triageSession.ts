// The TRIAGE prune session — serialize the editor's FULL working state to one opaque JSON blob and rehydrate
// it. The backend (workbench/triage_store.py) is a dumb blob store: it never interprets `state`; this module
// owns and shapes it. The whole feature's correctness lives here — a field that fails to round-trip is a
// silently-lost exclusion/decision on the operator's next open (invariant obligation #3).
//
// Working state is split across two components — the `useChainDraft` hook (the structural prune: draft +
// excluded Set + reasons Map) and `ChainEditor` (the expensive draft-run buckets + term/set-aside decisions).
// This module carries BOTH. Sets serialize to arrays and the one Map to a Record; `deserialize` reverses it
// so the hydrate initializers get ready-to-use Set/Map values.

import type { DraftReportOut, ResolvedPlacement, TermSetEntry } from "../api/hooks";
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

type Identity = { sector?: string | null; exchange?: string | null; category?: string | null };
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
  draftEmpty: boolean;
  termSet: TermSetEntry[];
  recs: Record<string, { tier: string; reason: string }>;
  adopted: Set<string>;
  setAside: Set<string>;
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
    draftEmpty: boolean;
    termSet: TermSetEntry[];
    recs: Record<string, { tier: string; reason: string }>;
    adopted: string[];
    setAside: string[];
  };
}

/** The restore result — a DISCRIMINATED union so a genuinely-absent session (the caller already knows from a
 *  null envelope) is NEVER conflated with an unreadable one. `incompatible` routes to the error-like path
 *  (notice + choice), not the empty path. */
export type DeserializeResult =
  | { status: "ok"; hook: HookRuntime; editor: EditorRuntime }
  | { status: "incompatible"; version: number };

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
      draftEmpty: editor.draftEmpty,
      termSet: editor.termSet,
      recs: editor.recs,
      adopted: [...editor.adopted],
      setAside: [...editor.setAside],
    },
  };
}

// Defensive readers so a malformed blob defaults rather than throws (additive tolerance + resilience to a
// hand-corrupted file). A structurally-wrong `state` (not an object) falls to `incompatible` in `deserialize`.
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const rec = <T>(v: unknown): Record<string, T> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, T>) : {};
const strSet = (v: unknown): Set<string> => new Set(arr<string>(v));

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
      draft: (h.draft as ChainDraft) ?? { segments: [], basket: [] },
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
      draftStatus: (e.draftStatus as DraftStatus) ?? null,
      cappedTerms: strSet(e.cappedTerms),
      draftEmpty: Boolean(e.draftEmpty),
      termSet: arr<TermSetEntry>(e.termSet),
      recs: rec<{ tier: string; reason: string }>(e.recs),
      adopted: strSet(e.adopted),
      setAside: strSet(e.setAside),
    },
  };
}
