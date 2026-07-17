import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  BasketMember,
  ChainDraftOut,
  DraftReportOut,
  ResolvedPlacement,
  ScoredMemberOut,
  SecurityCandidate,
  TermEdit,
  TermSetEntry,
  ThesisDetail,
  TriageSessionPut,
} from "../api/hooks";
import {
  useDraftJobStatus,
  useEditTerms,
  useProduceTerms,
  usePromoteThesis,
  usePutExclusions,
  usePutTriageSession,
  useRecommendTiers,
  useStartDraft,
} from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import {
  exportKeptNames,
  exportSegmentedNames,
  toExportedName,
  type ExportGroup,
} from "../util/exportNames";
import { useDebouncedCallback } from "../util/useDebouncedCallback";
import { AddName } from "./AddName";
import { AutoTextarea } from "./AutoTextarea";
import { DraftStatusStrip, type DraftCounts } from "./DraftStatusStrip";
import { archLabel, errText, memberHasFundamentals } from "./format";
import {
  matchesAnyJunkTell,
  signalAcronymTermsFrom,
  type JunkTellContext,
} from "./junkTells";
import { RunPicker } from "./RunPicker";
import {
  SCHEMA_VERSION,
  serialize,
  type DeserializeResult,
  type EditorRuntime,
} from "./triageSession";
import { DISCOVERED, memberKey, useChainDraft } from "./useChainDraft";

// Stop polling a draft after this long and show "timed out, try again". A real draft floor is the ~300s Opus
// tail-sweep + EDGAR discovery over the universe + decompose + narrate, so this is generous; it sits BELOW the
// 900s server-side running-job reaper, so the operator sees the timeout before the job is reaped (the backend
// job is left to the reaper — the FE only stops polling, never orphans it).
const DRAFT_POLL_TIMEOUT_MS = 600_000;

interface Props {
  thesis: ThesisDetail;
  asof: string;
  // Exit edit mode (the parent unmounts this, re-snapshotting on the next edit). `saved` = the exit
  // FOLLOWED a successful Save — it drives the parent's "your saved basket is editable on return" note (D).
  onDone: (saved: boolean) => void;
  // TRIAGE: the parent's scored members, keyed by security_id — a cheap read-time join (no fetch) that drives
  // the per-row "fundamentals loaded vs not" badge. Reflects the LAST SAVED state, so a freshly-drafted (unsaved)
  // name reads "needs SURFACE" — exactly the shortlist signal. Optional (an un-scored / test render omits it).
  scoredById?: Record<string, ScoredMemberOut>;
  // A restored triage session (the operator's autosaved prune, from `useTriageSession` → `deserialize`). When
  // present, the whole editor working state SEEDS from it at mount instead of from the thesis — resuming the
  // prune across a refresh. The parent gates the mount on the session GET, so this is settled before mount.
  restored?: DeserializeResult & { status: "ok" };
}

// "Fundamentals loaded" = the name carries a confirmed SURFACE-extractable scoring fact — the shared
// `memberHasFundamentals` rule (one rule across the badge, the scored row's get-data control, and the
// funnel line). This badge answers "does this survivor still need an extract → ratify?", nothing more.
const hasFundamentals = (
  sid: string | null | undefined,
  scoredById?: Record<string, ScoredMemberOut>,
): boolean => {
  if (!sid) return false;
  const sm = scoredById?.[sid];
  return sm ? memberHasFundamentals(sm) : false;
};

// A term's provenance: an operator seed vs an LLM-proposed (guard-tiered) term. The data already carries it.
const termAuthor = (a: string): string =>
  a === "operator_set" ? "seed" : a === "operator_edited" ? "edited" : "auto";

// The reconciler's catch-all segment (backend `_DISCOVERED_LABEL`, shared via useChainDraft): names
// EDGAR-discovered but NOT arranged into a real value-chain link. It's a SORTING QUEUE, not an economic link —
// the editor de-links it visually and the (wired) seg dropdown is how the operator sorts keepers OUT of it.

// The off-universe provenance pill (the dormant `.pill.sweep` slot, now data-backed): the name resolved OUTSIDE
// the EDGAR-discovered universe (via the sweep-augmented context). The label names the OBSERVATION ("off the
// deterministic universe"), never the mechanism — it is NOT a claim the tail-sweep's web-search sourced it.
const OffUniversePill = () => (
  <span
    className="pill sweep"
    title="off the deterministic universe — EDGAR term-search didn't surface it"
  >
    off-universe
  </span>
);

// Machine-parsed IDENTITY (Slice 2 enrichment) — quiet sector / exchange chips. Display-only (parsed from the
// name's EDGAR submissions onto the master), never promoted onto a BasketMember. Renders nothing when absent
// (an un-enriched / off-universe name — the honest fallback).
const IdentityChips = ({
  sector,
  exchange,
  category,
}: {
  sector?: string | null;
  exchange?: string | null;
  category?: string | null;
}) => (
  <>
    {sector && (
      <span className="idchip" title="sector (SEC SIC) — machine-parsed from EDGAR submissions">
        {sector}
      </span>
    )}
    {exchange && (
      <span className="idchip" title="exchange — machine-parsed from EDGAR submissions">
        {exchange}
      </span>
    )}
    {/* SEC filer category — a maturity/size tell. IDENTITY (sits with sector/exchange), NOT a re-classification
        of the archetype. Machine-parsed from EDGAR submissions, display-only. */}
    {category && (
      <span className="idchip" title="SEC filer category — a maturity/size tell (EDGAR submissions)">
        {category}
      </span>
    )}
  </>
);

// The hedged listing flag: the name's master row shows NO current SEC listing. A GUESS (a listing-presence
// heuristic), NEVER a "delisted" verdict — the name is still one pick away from placing (the frictionless
// rescue). Surfaced, never silently dropped (#9).
const NotListedFlag = () => (
  <div className="flag">
    ⚑ no current listing found in EDGAR — a guess, not a delisting; pick it to place anyway
  </div>
);

/** The authoring surface (Slice 4b + the S5 draft/ratify, 5c): build & edit the value chain by hand — or
 *  DRAFT it from the narrative (the narrative→chain drafter) and ratify per name. A drafted placement loads
 *  as `system_drafted` (badged, prunable); accepting it → `operator_set`, editing any field → `operator_edited`.
 *  A name the drafter couldn't resolve uniquely (AMBIGUOUS) enters the basket ONLY by an explicit operator
 *  pick (ticker + CIK disambiguate); one with no master row (ABSENT) is shown, never placed. A drafted name
 *  is UNSCORED until the operator extract→ratifies it. Nothing persists until SAVE (the full-replace promote,
 *  which honors each member's authorship and stores the thesis-fit prose). */
export function ChainEditor({ thesis, asof, onDone, scoredById, restored }: Props) {
  // The restored session seeds BOTH the hook (draft/excluded/reasons) and this component's own editor cells
  // (the draft-run buckets + term/set-aside decisions) at mount. `re` is the editor portion; `undefined` when
  // there's no session, so every initializer falls back to its thesis-derived / empty default.
  const re = restored?.editor;
  const d = useChainDraft(thesis, restored?.hook);
  const save = usePromoteThesis();
  const putExclusions = usePutExclusions(thesis.id); // #7: the durable NOs ride every Save
  // The draft is a KICK-OFF + POLL job now (it takes minutes; held open it 504'd). Start it, stash the job_id,
  // and poll until terminal. A poll-timeout (below) and a 404 (server restart) both surface as a visible failure
  // — never an infinite spinner.
  const startDraft = useStartDraft(thesis.id);
  const [jobId, setJobId] = useState<string | null>(null);
  const jobQ = useDraftJobStatus(thesis.id, jobId);
  const [draftError, setDraftError] = useState<string | null>(null);
  const pollTimeout = useRef<number | null>(null);
  const drafting = startDraft.isPending || !!jobId; // kicking off, or a job is running
  const produceTerms = useProduceTerms(thesis.id);
  const editTerms = useEditTerms(thesis.id);
  // The working term set. Seeded from what loaded; after produce OR a manual edit it ADOPTS the server's
  // RE-STAMPED set (never an optimistic copy — the next edit must diff against the server's authorship, not a
  // guessed one). Both writers update it via their per-call onSuccess below.
  const [termSet, setTermSet] = useState<TermSetEntry[]>(() => re?.termSet ?? thesis.term_set);
  const signalTerms = termSet.filter((e) => e.tier === "signal");
  const broadTerms = termSet.filter((e) => e.tier === "broad");
  const [termsOpen, setTermsOpen] = useState(true); // the term-set drawer — open by default
  const [newSeed, setNewSeed] = useState("");
  const [newSeg, setNewSeg] = useState("");

  // The tier RECOMMENDER (INVARIANT #10): the LLM recommends signal/broad + a reason per term; the operator
  // confirms via the EXISTING toggle. Display-only — `recs` is stashed (like `matched`), never persisted.
  const recommendTiers = useRecommendTiers(thesis.id);
  const [recs, setRecs] = useState<Record<string, { tier: string; reason: string }>>(
    () => re?.recs ?? {},
  );
  // OFFENSE adoptions (a BROAD term the model recommended SIGNAL, then confirmed): keep a "✦ adopted" trace in
  // v1 so the model's best contribution doesn't dissolve into an indistinguishable agreement while we judge it.
  const [adopted, setAdopted] = useState<Set<string>>(() => re?.adopted ?? new Set());
  const norm = (t: string) => t.trim().toLowerCase();

  const adopt = (t: ThesisDetail | undefined) => t && setTermSet(t.term_set);
  // Each edit op sends the FULL set (current working set + the one change) and adopts the re-stamped response.
  const toEdits = (ts: TermSetEntry[]): TermEdit[] => ts.map((e) => ({ term: e.term, tier: e.tier }));
  const saveEdits = (next: TermSetEntry[]) =>
    editTerms.mutate(toEdits(next), { onSuccess: adopt });
  // Produce/Regenerate replaces the set wholesale -> old recs are stale; clear recs + adopted (NOT on edits,
  // which the auto-flip + the adopted trace rely on `recs`/`adopted` surviving).
  const onProduce = () =>
    produceTerms.mutate(undefined, {
      onSuccess: (t) => {
        adopt(t);
        setRecs({});
        setAdopted(new Set());
      },
    });
  const onRecommend = () =>
    recommendTiers.mutate(undefined, {
      onSuccess: (rs) =>
        setRecs(
          Object.fromEntries(
            (rs ?? []).map((r) => [norm(r.term), { tier: r.recommended_tier, reason: r.reason }]),
          ),
        ),
    });

  const addSeed = () => {
    const term = newSeed.trim();
    if (!term) return;
    // a fresh seed lands SIGNAL; the server re-stamps authorship (operator_set). Tier here is just the request.
    saveEdits([...termSet, { term, tier: "signal", authored_by: "operator_set", source: "operator" }]);
    setNewSeed("");
  };
  const removeTerm = (term: string) => {
    const next = termSet.filter((e) => e.term !== term);
    if (next.length === 0 && termSet.length > 0) {
      // refinement 2 — clearing must be DELIBERATE (an empty set 503s the draft). Confirm before the wipe.
      const ok = window.confirm(
        "Remove the last term? This clears the set — the draft will return “term set is empty” until you produce or seed again.",
      );
      if (!ok) return;
    }
    saveEdits(next);
  };
  const toggleTier = (term: string) => {
    const entry = termSet.find((e) => e.term === term);
    const rec = recs[norm(term)];
    // OFFENSE adoption: a BROAD term the model recommended SIGNAL, toggled toward SIGNAL -> keep a v1 trace.
    if (entry && entry.tier === "broad" && rec?.tier === "signal") {
      setAdopted((prev) => new Set(prev).add(norm(term)));
    }
    saveEdits(
      termSet.map((e) =>
        e.term === term ? { ...e, tier: e.tier === "signal" ? "broad" : "signal" } : e,
      ),
    );
  };

  // The per-chip tier recommendation (INVARIANT #10) — DISPLAY-ONLY. Loud for a disagreement (DEFENSE: a SIGNAL
  // term recommended BROAD / OFFENSE: a BROAD term recommended SIGNAL — the existing toggle IS the confirm);
  // quiet-but-present for an agreement (a faint ✓, reason on hover), so v1 can judge the engine fired + concurred.
  // An adopted offense keeps a "✦ adopted" trace even after it auto-flips to agreement.
  const recTag = (e: TermSetEntry) => {
    const rec = recs[norm(e.term)];
    if (!rec) return null;
    if (rec.tier === e.tier) {
      const adoptedTrace = adopted.has(norm(e.term));
      return (
        <span
          className={`wb-rec wb-rec-agree${adoptedTrace ? " wb-rec-adopted" : ""}`}
          title={rec.reason}
        >
          {adoptedTrace ? "✦ adopted" : `✓ ${rec.tier}`}
        </span>
      );
    }
    const offense = rec.tier === "signal"; // current is broad -> recommend SIGNAL (the value cell)
    return (
      <span className={`wb-rec wb-rec-disagree wb-rec-${offense ? "offense" : "defense"}`}>
        {offense ? "↑ recommend SIGNAL" : "↓ recommend BROAD"} — {rec.reason}
      </span>
    );
  };

  // The ⚠ capped marker (#9 rule 4 made visible): on the LAST draft this term matched more filings than the
  // enumeration cap, so pages beyond the cap were not searched — deep hits for it may be missing. RUN state
  // from the draft report (display-only, cleared on re-draft) — never persisted onto the term set.
  const cappedTag = (e: TermSetEntry) =>
    cappedTerms.has(norm(e.term)) ? (
      <span
        className="wb-rec wb-capped"
        title="On the last draft this term matched more filings than the enumeration cap — pages beyond the cap were not searched, so names surfacing only that deep may be missing."
      >
        ⚠ capped
      </span>
    ) : null;
  const [ambiguous, setAmbiguous] = useState<ResolvedPlacement[]>(() => re?.ambiguous ?? []);
  const [verify, setVerify] = useState<ResolvedPlacement[]>(() => re?.verify ?? []);
  const [absent, setAbsent] = useState<ResolvedPlacement[]>(() => re?.absent ?? []);
  // The last draft's honesty report + bucket counts (the status strip's input) and the hit-capped terms (the
  // ⚠ marker on a term chip). RUN state from the LAST completed draft — cleared on a re-draft, absent until a
  // draft carries a report, never persisted (#9 rules 2/3 made visible; the strip is quiet at 100% healthy).
  const [draftStatus, setDraftStatus] = useState<{
    counts: DraftCounts;
    report: DraftReportOut;
  } | null>(() => re?.draftStatus ?? null);
  const [cappedTerms, setCappedTerms] = useState<Set<string>>(() => re?.cappedTerms ?? new Set());
  // Reversibility (principle #1): the origin placement of a name PULLED from To-Review into Placed, keyed by
  // security_id. It lets a Placed row that CAME from To-Review offer a "send back" — the visible inverse of add
  // (add ⇄ send-back). Only these names get the control (others were never in To-Review). Never persisted.
  const [verifyOrigin, setVerifyOrigin] = useState<Record<string, ResolvedPlacement>>(
    () => re?.verifyOrigin ?? {},
  );
  const [draftEmpty, setDraftEmpty] = useState(() => re?.draftEmpty ?? false);
  // Display-only provenance: security_id -> the discovery term(s) that surfaced it. Set on a draft, NOT a
  // field on BasketMember (it's draft-time discovery provenance, not a thesis fact — never promoted).
  const [matched, setMatched] = useState<Record<string, string[]>>(() => re?.matched ?? {});
  // Display-only provenance: the security_ids of PLACED names whose discovery_source is "off_universe" (resolved
  // outside the EDGAR-discovered universe, via the sweep-augmented context). The PLACED bucket renders
  // BasketMembers, not placements, so it bridges by security_id — same shape as `matched`. NEVER promoted.
  const [offUniverse, setOffUniverse] = useState<Set<string>>(() => re?.offUniverse ?? new Set());
  // Display-only OPINION: the security_ids of PLACED names the NARRATOR judged off-thesis (a boilerplate
  // term-collision). Same bridge-by-security_id shape as `offUniverse`. A RECOMMENDATION only (#10) — the name
  // STAYS placed (#9); the reason is its prose, shown in the thesis-fit note below. NEVER promoted.
  const [offThesisSet, setOffThesisSet] = useState<Set<string>>(
    () => re?.offThesisSet ?? new Set(),
  );
  // Display-only IDENTITY (Slice 2 enrichment): security_id -> sector / exchange / category (machine-parsed from
  // EDGAR submissions onto the master). Same bridge-by-security_id shape as `matched` for the PLACED bucket (which
  // renders BasketMembers); the other buckets read it off the placement directly. NEVER promoted.
  const [identity, setIdentity] = useState<
    Record<string, { sector?: string | null; exchange?: string | null; category?: string | null }>
  >(() => re?.identity ?? {});
  // Display-only: security_id -> the company NAME. The PLACED bucket renders BasketMembers (which carry no name),
  // so — like `matched`/`identity` — the name is bridged by security_id from the draft placements (and captured on
  // a manual add). NEVER promoted onto a BasketMember.
  const [names, setNames] = useState<Record<string, string>>(() => re?.names ?? {});

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));
  // Item 1 (inverse loudness): the per-row fundamentals badge only earns its place once it DISCRIMINATES — i.e.
  // once ≥1 name in the basket has confirmed fundamentals. Before any surfacing it's true of every row (pure
  // noise), so we show a single quiet header hint instead of stamping "needs SURFACE" on all of them.
  const anyFundamentals = d.draft.basket.some((m) => hasFundamentals(m.security_id, scoredById));
  // Item 6(c): how many placed names are still in the "Discovered" holding pen (unsorted into a real link).
  const discoveredCount = d.draft.basket.filter((m) => m.segment === DISCOVERED).length;
  const hasRealLink = segLabels.some((l) => l !== DISCOVERED);
  // The links editor separates the REAL value-chain links (reorderable) from the "Discovered" holding pen (not a
  // link — no reorder). Rendered as two distinct regions so the editor reads legibly (the arrows apply to links).
  const realLinks = d.draft.segments.filter((s) => s.label !== DISCOVERED);
  const discoveredSeg = d.draft.segments.find((s) => s.label === DISCOVERED);

  // --- post-draft results buckets (the IA reorg) ---
  const PLACED_PREVIEW = 12; // a large group (hundreds of names) collapses to a preview + "show more"
  // per-group "show more" state, keyed by group ("placed" | "flagged" | "low_quality"; flat mode uses "placed")
  const [showAllGroups, setShowAllGroups] = useState<Set<string>>(new Set());
  // The two big result sections collapse (open by default) — a long Placed list is a lot to scroll past to
  // reach To Review / Couldn't resolve, so the header is a click-to-collapse (the counts stay visible).
  const [placedOpen, setPlacedOpen] = useState(true);
  // C-B + G — the placed board's DISPLAY partitions (up to three groups of the ONE membership), each
  // independently collapsible. The two Placed groups start OPEN (nothing hidden by default); the acronym-
  // low-quality group starts COLLAPSED (a junk cluster to visit for a scan-and-clear pass, not a wall to
  // scroll past). Grouping only renders when it discriminates — see `groupingActive` below.
  const [cleanOpen, setCleanOpen] = useState(true);
  const [flaggedOpen, setFlaggedOpen] = useState(true);
  const [lowQualityOpen, setLowQualityOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true); // the master To-Review section (open by default)
  const [keepersOpen, setKeepersOpen] = useState(true); // the keepers sub-drawer (the signal — open)
  const [couldntOpen, setCouldntOpen] = useState(true); // the couldn't-resolve drawer (open by default)
  const [lowSignalOpen, setLowSignalOpen] = useState(false); // the low-signal noise sub-drawer (collapsed)
  const [noTickerOpen, setNoTickerOpen] = useState(false); // the ticker-less names sub-drawer (collapsed)
  const [pickOpen, setPickOpen] = useState<Set<string>>(new Set()); // which ambiguous rows show the CIK picker
  // Keeper set-aside (#1 reversible / #2 keep-it-visible): a keeper the operator waves off greys to a
  // stub and stays on screen, one ✕-click from restore. #7 made it durable for RESOLVED keepers: the
  // set seeds from the thesis's persisted exclusions (a rejected keeper arrives pre-greyed on the next
  // draft) and Save persists the UUID-keyed entries with the exclusion set. Ticker/name-keyed set-asides
  // (unresolved names) stay session-local — the flagged v1 scope cut.
  const [setAside, setSetAside] = useState<Set<string>>(
    () => re?.setAside ?? new Set((thesis.exclusions ?? []).map((e) => e.security_id)),
  );
  const toggleSetAside = (id: string) =>
    setSetAside((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // --- Autosave the prune session (the resumable working state; triageSession.ts + workbench/triage_store.py) ---
  // Serialize the WHOLE editor working state (this component's cells + the hook's draft/excluded/reasons) to one
  // opaque blob and debounced-PUT it on change. Accepted tradeoff (see the plan): every change re-PUTs the whole
  // blob (incl. the immutable draft output) — negligible at one thesis / single operator; NOT split, by design
  // (one self-contained blob). retry:2 in the hook rides out a transient blip; a sustained failure surfaces loud.
  const putSession = usePutTriageSession(thesis.id);
  const saveSession = useDebouncedCallback((state: ReturnType<typeof serialize>) => {
    // the wire `state` is an opaque Record (the backend never interprets it); our concrete SerializedSession
    // is the FE's private shape, so a cast bridges the two.
    putSession.mutate({ schema_version: SCHEMA_VERSION, state: state as unknown as TriageSessionPut["state"] });
  }, 1000);
  const editorRuntime: EditorRuntime = {
    ambiguous,
    verify,
    absent,
    verifyOrigin,
    matched,
    offUniverse,
    offThesisSet,
    identity,
    names,
    draftStatus,
    cappedTerms,
    draftEmpty,
    termSet,
    recs,
    adopted,
    setAside,
  };
  const sessionBlob = serialize(
    { draft: d.draft, excluded: d.excluded, reasons: d.reasons, reasonsDirty: d.reasonsDirty },
    editorRuntime,
  );
  const sessionKey = JSON.stringify(sessionBlob); // the change signal (referentially stable across no-op renders)
  const firstAutosave = useRef(true);
  useEffect(() => {
    // Hydration-race guard: the FIRST render carries the just-restored/seeded state — do NOT re-save it (that
    // would write back what we just read). Only genuine post-mount edits autosave. The `key={thesis.id}` remount
    // resets this ref per thesis, and the debounce timer clears on unmount, so a thesis switch never cross-saves.
    if (firstAutosave.current) {
      firstAutosave.current = false;
      return;
    }
    saveSession(sessionBlob);
    // sessionKey is the serialized change signal for sessionBlob; saveSession is stable (useRef).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);
  // The save-status indicator (a small, honest tri-state): Saving… / Saved / loud "Not saved" + Retry. Simple by
  // design — no escalation state machine (the plan): a transient blip self-heals on the next change (which re-PUTs
  // the whole blob), and nothing is destroyed in memory, so the worst case is a refresh during a sustained outage.
  const saveStatus: "idle" | "saving" | "saved" | "error" = putSession.isPending
    ? "saving"
    : putSession.isError
      ? "error"
      : putSession.isSuccess
        ? "saved"
        : "idle";

  // "Export all" (the top-of-editor button): EVERY name this narrative surfaced, grouped for a diff-friendly
  // dump — the whole basket (incl. excluded/set-aside — this is NOT the prune) by value-chain link in chain
  // order, then the Discovered pen, then the To-Review pile. Each group is sorted alphabetically by ticker in
  // exportSegmentedNames; empty groups are dropped there.
  const nameOf = (m: { security_id?: string | null; ticker: string }): string | null =>
    (m.security_id ? names[m.security_id] : undefined) ??
    (m.security_id ? scoredById?.[m.security_id]?.name : undefined) ??
    null;
  const buildExportAllGroups = (): ExportGroup[] => {
    const groups: ExportGroup[] = [];
    const linkLabels = new Set(d.draft.segments.map((s) => s.label));
    // real links, in chain order (a basket member's segment, or the Discovered pen when unset)
    for (const seg of d.draft.segments) {
      groups.push({
        label: seg.label,
        rows: d.draft.basket
          .filter((m) => (m.segment ?? DISCOVERED) === seg.label)
          .map((m) => toExportedName({ ticker: m.ticker, name: nameOf(m) })),
      });
    }
    // basket members whose segment is null or a stale label the chain no longer has → the Discovered pen
    const orphans = d.draft.basket
      .filter((m) => !linkLabels.has(m.segment ?? DISCOVERED))
      .map((m) => toExportedName({ ticker: m.ticker, name: nameOf(m) }));
    if (orphans.length) groups.push({ label: DISCOVERED, rows: orphans });
    // the To-Review pile — surfaced by the draft but never placed into a link
    const bucket = (arr: ResolvedPlacement[]): ExportGroup["rows"] =>
      arr.map((p) => toExportedName({ ticker: p.ticker, name: p.name }));
    groups.push({ label: "To Review", rows: bucket(verify) });
    groups.push({ label: "Ambiguous", rows: bucket(ambiguous) });
    groups.push({ label: "Couldn't resolve", rows: bucket(absent) });
    return groups;
  };
  const exportAllCount =
    d.draft.basket.length + verify.length + ambiguous.length + absent.length;

  // TRIAGE PR-2 (the find) — sort + filter the placed list so pruning ~90 names is fast. The VIEW only: it
  // reorders/hides rows, it NEVER changes what Save persists (Save is basket − excluded, computed over the whole
  // draft, not this view — the #9 spine, test-guarded). `compact` collapses the prose for a scannable, table-like
  // read without losing the inline editors.
  const [sortBy, setSortBy] = useState<"draft" | "name" | "archetype" | "segment" | "sector">("draft");
  const [fArch, setFArch] = useState("");
  const [fSeg, setFSeg] = useState("");
  const [fFund, setFFund] = useState<"" | "loaded" | "needs">("");
  const [fAuth, setFAuth] = useState<"" | "accepted" | "drafted">("");
  const [fInc, setFInc] = useState<"" | "included" | "excluded">("");
  const [fOffUniv, setFOffUniv] = useState(false);
  const [compact, setCompact] = useState(false);
  const filtersActive =
    sortBy !== "draft" || !!fArch || !!fSeg || !!fFund || !!fAuth || !!fInc || fOffUniv;
  const clearFilters = () => {
    setSortBy("draft");
    setFArch("");
    setFSeg("");
    setFFund("");
    setFAuth("");
    setFInc("");
    setFOffUniv(false);
  };
  const sec = (m: BasketMember) => (m.security_id ? identity[m.security_id]?.sector : null) ?? "";
  // the archetype filter offers the values PRESENT (+ "— unset —" for the un-characterized, item F)
  const archsPresent = Array.from(
    new Set(
      d.draft.basket
        .map((m) => m.archetype)
        .filter((a): a is NonNullable<BasketMember["archetype"]> => a != null),
    ),
  );
  const matchesFilters = (m: BasketMember): boolean => {
    const k = memberKey(m);
    const loaded = hasFundamentals(m.security_id, scoredById);
    if (fArch && (fArch === "__unset__" ? m.archetype != null : m.archetype !== fArch)) return false;
    if (fSeg && (fSeg === "__unplaced__" ? !!m.segment : m.segment !== fSeg)) return false;
    if (fFund && (fFund === "loaded" ? !loaded : loaded)) return false;
    if (fAuth === "accepted" && m.authored_by === "system_drafted") return false;
    if (fAuth === "drafted" && m.authored_by !== "system_drafted") return false;
    if (fInc === "included" && !d.isIncluded(k)) return false;
    if (fInc === "excluded" && d.isIncluded(k)) return false;
    if (fOffUniv && !(m.security_id && offUniverse.has(m.security_id))) return false;
    return true;
  };
  const verifyAsideId = (p: ResolvedPlacement, key?: string) =>
    p.security_id ?? p.ticker ?? p.name ?? key ?? "";
  const matchesVerifyInclude = (p: ResolvedPlacement): boolean => {
    if (!fInc) return true;
    const aside = setAside.has(verifyAsideId(p));
    if (fInc === "included") return !aside;
    if (fInc === "excluded") return aside;
    return true;
  };
  const sorted = (list: BasketMember[]): BasketMember[] => {
    if (sortBy === "draft") return list;
    const cmp = (a: BasketMember, b: BasketMember): number => {
      if (sortBy === "name") return (a.ticker || "").localeCompare(b.ticker || "");
      if (sortBy === "archetype")
        return (a.archetype ?? "￿").localeCompare(b.archetype ?? "￿"); // unset sorts last (item F)
      if (sortBy === "segment") return (a.segment || "￿").localeCompare(b.segment || "￿");
      return (sec(a) || "￿").localeCompare(sec(b) || "￿"); // sector; blanks sort last
    };
    return [...list].sort(cmp);
  };
  // filter → sort → partition → per-group preview-collapse (counts are of the FILTERED set)
  const triaged = sorted(d.draft.basket.filter(matchesFilters));

  // G — the low-quality lens (a cheap-cut accelerant): model-flagged off-thesis AND any registered junk-tell
  // (see junkTells.ts). The LLM flag is the recall guard — a loose tell can't demote a name the narrator
  // approved. A LENS, never a bucket: membership / include / Save are untouched (#9). Draft-session state.
  const signalAcronymTerms = signalAcronymTermsFrom(termSet);
  const junkTellCtx = (m: BasketMember): JunkTellContext | null => {
    if (!m.security_id) return null;
    return {
      matchedTerms: matched[m.security_id] ?? [],
      companyName: names[m.security_id] ?? "",
      signalAcronymTerms,
    };
  };
  const isLowQuality = (m: BasketMember): boolean => {
    const ctx = junkTellCtx(m);
    return (
      !!m.security_id &&
      offThesisSet.has(m.security_id) &&
      !!ctx &&
      matchesAnyJunkTell(ctx)
    );
  };
  // C-B + G — ONE membership in up to three DISPLAY partitions, precedence lowQuality > flagged > clean (the
  // To-Review precedence idiom). Grouping renders ONLY when it discriminates (everything in one group is
  // just today's flat list — a partition that doesn't discriminate is noise, honest-loudness #3).
  const gClean: BasketMember[] = [];
  const gFlagged: BasketMember[] = [];
  const gLowQuality: BasketMember[] = [];
  for (const m of triaged) {
    if (isLowQuality(m)) gLowQuality.push(m);
    else if (m.security_id && offThesisSet.has(m.security_id)) gFlagged.push(m);
    else gClean.push(m);
  }
  const groupingActive = gFlagged.length > 0 || gLowQuality.length > 0;
  const shownRows = (gkey: string, rows: BasketMember[]) =>
    showAllGroups.has(gkey) ? rows : rows.slice(0, PLACED_PREVIEW);
  const showMoreBtn = (gkey: string, rows: BasketMember[]) =>
    rows.length > PLACED_PREVIEW && !showAllGroups.has(gkey) ? (
      <div className="showmore">
        <button
          type="button"
          className="wb-mini"
          onClick={() => setShowAllGroups((prev) => new Set(prev).add(gkey))}
        >
          show {rows.length - PLACED_PREVIEW} more
        </button>
      </div>
    ) : null;
  const togglePick = (name: string) =>
    setPickOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  // The archetype palette (matches .arch.* / the lifecycle tokens) — the ticker tint for a SET archetype.
  // (No arch select here anymore: the archetype is decided on the finalize rail, item F.)
  const ARCH_COLOR: Record<string, string> = {
    leader: "var(--leader)",
    high_beta: "var(--armed)",
    lotto: "var(--warm)",
    shovel: "var(--manage)",
  };

  // Load a completed draft into the editor (MERGE, not replace). Fail-open: an empty draft (no key / the model
  // declined) loads nothing and shows the quiet "returned nothing" note.
  const applyDraft = (data: ChainDraftOut) => {
    d.loadDraft(data);
    setAmbiguous(data.placements.filter((p) => p.status === "ambiguous"));
    setVerify(data.placements.filter((p) => p.status === "verify"));
    setAbsent(data.placements.filter((p) => p.status === "absent"));
    setMatched(
      Object.fromEntries(
        data.placements
          .filter((p) => p.security_id)
          .map((p) => [p.security_id as string, p.matched_terms]),
      ),
    );
    setOffUniverse(
      new Set(
        data.placements
          .filter((p) => p.security_id && p.discovery_source === "off_universe")
          .map((p) => p.security_id as string),
      ),
    );
    setOffThesisSet(
      new Set(
        data.placements
          .filter((p) => p.security_id && p.off_thesis)
          .map((p) => p.security_id as string),
      ),
    );
    setIdentity(
      Object.fromEntries(
        data.placements
          .filter((p) => p.security_id)
          .map((p) => [
            p.security_id as string,
            { sector: p.sector, exchange: p.exchange, category: p.category },
          ]),
      ),
    );
    setNames((prev) => ({
      ...prev, // keep any names captured from manual adds
      ...Object.fromEntries(
        data.placements
          .filter((p) => p.security_id && p.name)
          .map((p) => [p.security_id as string, p.name]),
      ),
    }));
    setDraftEmpty(data.placements.length === 0 && data.segments.length === 0);
    // The run's honesty report -> the status strip + the ⚠ capped chip markers. A pre-slice result (no
    // report) renders no strip. Counts are client-derived from the placements' own statuses.
    const byStatus = (s: string) => data.placements.filter((p) => p.status === s).length;
    setDraftStatus(
      data.report
        ? {
            counts: {
              placed: byStatus("placed"),
              verify: byStatus("verify"),
              ambiguous: byStatus("ambiguous"),
              absent: byStatus("absent"),
            },
            report: data.report,
          }
        : null,
    );
    setCappedTerms(new Set((data.report?.capped_terms ?? []).map(norm)));
  };

  const clearPollTimeout = () => {
    if (pollTimeout.current) window.clearTimeout(pollTimeout.current);
    pollTimeout.current = null;
  };

  // Draft the chain from the narrative — an EXPLICIT operator action (never on render). KICK OFF the job and
  // start polling; arm a poll-timeout so the operator always reaches a terminal state.
  const onDraft = async () => {
    setDraftError(null);
    setDraftEmpty(false);
    setDraftStatus(null); // the strip + capped markers describe the LAST run — stale once a new one starts
    setCappedTerms(new Set());
    try {
      const ref = await startDraft.mutateAsync();
      setJobId(ref.job_id);
      clearPollTimeout();
      pollTimeout.current = window.setTimeout(() => {
        setJobId(null); // stop polling; the backend job is left to the server reaper, never orphaned
        setDraftError("Draft timed out — try again.");
      }, DRAFT_POLL_TIMEOUT_MS);
    } catch (e) {
      setDraftError(errText(e)); // a 409 ("already running") or a kick-off transport error
    }
  };

  // The poll's terminal transition: done → load the result; failed → show the operator-facing error; a 404
  // (unknown/expired/restart-wiped job) → a visible "draft was lost". In every case stop polling + disarm the
  // timeout. Keyed on the status/error edge so it fires once per terminal arrival.
  const jobStatus = jobQ.data?.status;
  useEffect(() => {
    if (!jobId) return;
    if (jobStatus === "done") {
      clearPollTimeout();
      if (jobQ.data?.result) applyDraft(jobQ.data.result);
      setJobId(null);
    } else if (jobStatus === "failed") {
      clearPollTimeout();
      setDraftError(jobQ.data?.error || "Draft failed.");
      setJobId(null);
    } else if (jobQ.isError) {
      clearPollTimeout();
      setDraftError("Draft was lost (the server may have restarted) — try again.");
      setJobId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus, jobQ.isError, jobId]);

  // An AMBIGUOUS name enters the basket ONLY here, by an explicit pick — the operator commits the exact
  // security_id (the membership decision, INVARIANT #2). It lands `system_drafted` (the prose is still
  // drafted) for the operator to accept / edit, like any drafted placement. Archetype stays UNSET (item F).
  const pickAmbiguous = (p: ResolvedPlacement, c: SecurityCandidate) => {
    d.addMember({
      ticker: c.ticker,
      role: "—",
      archetype: null,
      security_id: c.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      conviction: null,
      authored_by: "system_drafted",
    });
    setAmbiguous((prev) => prev.filter((x) => x !== p));
  };

  // A VERIFY name is already RESOLVED (in your universe by exact CIK) but matched on a single broad keyword,
  // so the deterministic discovery surfaces it LOWER-confidence and never auto-places it (the same discipline
  // as AMBIGUOUS — a single match is never auto-membership, INVARIANT #2). One explicit "add" commits its known
  // security_id; it lands `system_drafted` (still unscored) for the operator to accept / edit / drop.
  const addVerify = (p: ResolvedPlacement) => {
    if (!p.security_id) return;
    d.addMember({
      ticker: p.ticker || p.name,
      role: "—",
      archetype: null, // un-decided (item F) — the finalize rail sets it
      security_id: p.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      conviction: null,
      authored_by: "system_drafted",
    });
    // stash the origin so the Placed row can offer the inverse (send back to To-Review)
    setVerifyOrigin((prev) => ({ ...prev, [p.security_id as string]: p }));
    setVerify((prev) => prev.filter((x) => x !== p));
  };

  // The inverse of addVerify (reversibility): return a Placed name to the To-Review list exactly as it was,
  // and drop it from the basket. Offered ONLY on rows whose security_id is in `verifyOrigin` (i.e. names that
  // came from To-Review) — a draft-placed / hand-added name is reversed by exclude/remove, not this.
  const sendBackToVerify = (sid: string) => {
    const origin = verifyOrigin[sid];
    if (!origin) return;
    d.removeMember(sid); // memberKey === security_id for a resolved name
    setVerify((prev) => [...prev, origin]);
    setVerifyOrigin((prev) => {
      const next = { ...prev };
      delete next[sid];
      return next;
    });
  };

  // Save persists ONLY the INCLUDED subset (the prune) — the promote full-replaces, so excluded names simply
  // aren't sent. The current sort/filter VIEW never affects this: it's the whole basket minus `excluded`,
  // regardless of what's visible (#9 — the view hides, only include decides what persists).
  // #7: Save ALSO persists the exclusion set — the session's NOs (excluded members + UUID-keyed keeper
  // set-asides, each with its optional reason) ∪ the CARRIED-FORWARD prior exclusions this session never
  // re-surfaced (a name absent from today's draft must not lose its durable NO). A re-included name is
  // simply not in the payload — the NO is withdrawn.
  const isUuid = (s: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
  const onSave = async () => {
    const basket = d.includedBasket;
    if (basket.length === 0 && d.draft.basket.length > 0) {
      const ok = window.confirm(
        "Save an empty basket? Every name is excluded — the thesis will have no basket to score. Include at least one, or confirm the wipe.",
      );
      if (!ok) return;
    }
    const priorTicker = new Map((thesis.exclusions ?? []).map((e) => [e.security_id, e.ticker]));
    const priorReason = new Map((thesis.exclusions ?? []).map((e) => [e.security_id, e.reason]));
    const exclusions: { security_id: string; ticker: string | null; reason: string | null }[] = [];
    const seen = new Set<string>();
    for (const m of d.draft.basket) {
      if (!m.security_id) continue;
      seen.add(m.security_id);
      if (d.excluded.has(memberKey(m))) {
        exclusions.push({
          security_id: m.security_id,
          ticker: m.ticker,
          reason: d.reasons.get(memberKey(m)) ?? priorReason.get(m.security_id) ?? null,
        });
      }
    }
    for (const id of setAside) {
      if (!isUuid(id) || seen.has(id)) continue; // unresolved keys stay session-local (v1 cut)
      seen.add(id);
      exclusions.push({
        security_id: id,
        ticker: priorTicker.get(id) ?? null,
        reason: d.reasons.get(id) ?? priorReason.get(id) ?? null,
      });
    }
    for (const e of thesis.exclusions ?? []) {
      if (seen.has(e.security_id)) continue; // re-decided this session (kept or withdrawn above)
      exclusions.push({ security_id: e.security_id, ticker: e.ticker ?? null, reason: e.reason ?? null });
    }
    try {
      await putExclusions.mutateAsync(exclusions);
    } catch {
      window.alert("Couldn't persist the exclusion set — the basket was NOT saved. Retry Save.");
      return;
    }
    save.mutate(
      {
        id: thesis.id,
        name: thesis.name,
        narrative: thesis.narrative,
        ticker: thesis.ticker ?? null,
        basket,
        segments: d.draft.segments,
      },
      { onSuccess: () => onDone(true) },
    );
  };

  // Items 4 + 5 — the To-Review triage partition. Precedence off-thesis > ticker-less > keeper: the model's
  // off-thesis names are the majority NOISE (quiet, collapsed — never yellow-flagged, that's inverse loudness);
  // the ticker-less names are likely subs/holdcos (quiet, collapsed); what remains are the KEEPERS — the rare
  // signal, surfaced up top (the keepers block). Every group stays PROMOTABLE (#9 — nothing dropped). The
  // keeper vs noise distinction is carried STRUCTURALLY (keepers up top; the noise in labeled drawers), so no
  // per-row "recommend add" badge — it would be true of every visible keeper, which is noise (honest loudness #7).
  const verifyVisible = verify.filter(matchesVerifyInclude);
  const vOffThesis = verifyVisible.filter((p) => p.off_thesis);
  const vNoTicker = verifyVisible.filter((p) => !p.off_thesis && !p.ticker);
  const vKeepers = verifyVisible.filter((p) => !p.off_thesis && p.ticker);
  const verifyRow = (p: ResolvedPlacement, key: string) => {
    const inBasket = p.security_id ? keys.has(p.security_id) : false;
    // "add" is a checkbox styled affordance (model A): checking it promotes the candidate → the row MOVES up to
    // Placed (the basket's single home). That move IS the honest signal of the state change ("haven't decided" →
    // "in the basket"); the reverse is the Placed row's send-back / exclude (#121/#122). No "skip" — a candidate
    // is never discarded, only added or left in the queue. Disabled + explained for un-addable names (no filer
    // id, or no listed ticker → not directly investable; still reachable via the name search below).
    const canAdd = !inBasket && !!p.security_id && !!p.ticker;
    const addWhy = !p.security_id
      ? "can't resolve to a filer — not addable here"
      : !p.ticker
        ? "no listed ticker — not directly investable (add via the name search below if you need it)"
        : "check to add — moves it up to Placed (the basket)";
    // VIEW-only set-aside (#2 keep-it-visible): the ✕ greys the keeper to a stub, reversible in one click.
    const asideId = verifyAsideId(p, key);
    const aside = setAside.has(asideId);
    return (
      <div className={`nmrow${aside ? " excluded" : ""}`} key={key}>
        <div className="top">
          {/* the "add" checkbox sits LEFT of the name — the same spot as the Placed include checkbox (consistency).
              Checking it promotes the candidate → the row moves up to Placed. Disabled + titled for un-addable names. */}
          <input
            type="checkbox"
            className="wb-inc"
            checked={false}
            disabled={!canAdd || aside}
            aria-label={`add ${p.ticker || p.name}`}
            title={addWhy}
            onChange={() => canAdd && !aside && addVerify(p)}
          />
          <span className="tk">{p.ticker || "—"}</span>
          <span className="co">{p.name}</span>
          {/* set aside → a quiet stub (chips + prose hidden, the noise recedes); else the identity chips. */}
          {aside ? (
            <span className="wb-exc-tag" title="set aside — click the ✕ to restore">
              set aside
            </span>
          ) : (
            <>
              <IdentityChips sector={p.sector} exchange={p.exchange} category={p.category} />
              {p.discovery_source === "off_universe" && <OffUniversePill />}
            </>
          )}
          {/* the ✕ set-aside toggle, top-right (reversibility #1): click to grey the keeper out, click again to
              restore. Local view state only — nothing added, removed, or sent to the backend. */}
          <button
            type="button"
            className={`wb-setaside${aside ? " on" : ""}`}
            aria-pressed={aside}
            aria-label={`${aside ? "restore" : "set aside"} ${p.ticker || p.name}`}
            title={
              aside
                ? "restore — bring this keeper back"
                : "set aside — grey this keeper out (reversible; click again to restore)"
            }
            onClick={() => toggleSetAside(asideId)}
          >
            ✕
          </button>
        </div>
        {aside ? null : p.prose ? <div className="fit">{p.prose}</div> : null}
        {!aside &&
        (() => {
          // Only surface "recommend → {segment}" when it's a REAL link. "Discovered" is the unsorted holding pen
          // (not a link), so "recommend → Discovered" is a contradiction — it's exactly where the low-signal /
          // ticker-less names land, i.e. the system is NOT recommending a link. Keep the `matched …` provenance
          // (why it surfaced) either way.
          const recSeg = p.segment && p.segment !== DISCOVERED ? p.segment : null;
          const matched = p.matched_terms.length > 0 ? p.matched_terms.join(", ") : null;
          if (!recSeg && !matched) return null;
          return (
            <div className="prov lead">
              {recSeg ? `recommend → ${recSeg}` : null}
              {recSeg && matched ? " · " : null}
              {matched ? `matched ${matched}` : null}
            </div>
          );
        })()}
        {!aside && p.listing_status === "inactive" && <NotListedFlag />}
      </div>
    );
  };

  return (
    <div className="wb-editor">
      <div className="wb-editor-head">
        <div className="sect-h">
          Build the value chain <em>— decompose the basket into links</em>
        </div>
        <div className="wb-editor-actions">
          {/* Export ALL surfaced names (whole basket + To-Review pile), grouped by link, each alphabetical by
              ticker — a diff-friendly dump. DISTINCT from the "Export (N)" in Placed names, which is included-only. */}
          <button
            type="button"
            className="wb-mini ghost"
            disabled={exportAllCount === 0}
            title="Export EVERY name this narrative surfaced — the whole basket (including excluded) plus the To-Review pile — grouped by link, each alphabetical by ticker"
            aria-label={`export all ${exportAllCount} surfaced names, segmented by link`}
            onClick={() =>
              exportSegmentedNames({
                thesisName: thesis.name,
                stage: "all",
                asof,
                groups: buildExportAllGroups(),
              })
            }
          >
            Export all ({exportAllCount})
          </button>
          {/* Autosave status (the resumable prune) — DISTINCT from the promote "Save chain" below: this saves the
              working state so a refresh resumes; that writes the spine. Loud only on a sustained failure. */}
          {saveStatus === "saving" && (
            <span className="wb-autosave" title="autosaving your prune…">
              Saving…
            </span>
          )}
          {saveStatus === "saved" && (
            <span className="wb-autosave saved" title="your prune is saved — a refresh will resume it">
              ✓ Saved
            </span>
          )}
          {saveStatus === "error" && (
            <span className="wb-autosave err" role="status">
              ⚠ Not saved
              <button
                type="button"
                className="wb-mini ghost"
                onClick={() => saveSession(sessionBlob)}
              >
                Retry
              </button>
            </span>
          )}
          {d.dirty && <span className="wb-dirty">unsaved</span>}
          <button type="button" className="promote" disabled={save.isPending} onClick={onSave}>
            {save.isPending ? "Saving…" : "Save chain"}
          </button>
          <button type="button" className="wb-mini ghost" onClick={() => onDone(false)}>
            {d.dirty ? "Discard" : "Done"}
          </button>
        </div>
      </div>
      {save.isError && (
        <ErrorToast>Couldn't save — {errText(save.error)}. Nothing changed.</ErrorToast>
      )}

      <div className="wb-terms">
        <button
          type="button"
          className="wb-drawer-h"
          aria-expanded={termsOpen}
          onClick={() => setTermsOpen((o) => !o)}
        >
          <span className="chev">{termsOpen ? "▾" : "▸"}</span>
          <span className="dlabel">Term set</span>
          <span className="dmeta">
            {signalTerms.length} signal · {broadTerms.length} broad
          </span>
        </button>
        {termsOpen && (
          <>
        <div className="wb-draft-gap">
          <button
            type="button"
            className="wb-edit-btn"
            onClick={onProduce}
            disabled={produceTerms.isPending}
          >
            {produceTerms.isPending
              ? "Producing…"
              : termSet.length > 0
                ? "↻ Regenerate term set"
                : "⚙ Produce term set"}
          </button>
          {termSet.length > 0 && (
            <button
              type="button"
              className="wb-edit-btn"
              onClick={onRecommend}
              disabled={recommendTiers.isPending}
              title="Haiku recommends a tier + reason per term — you confirm via the ↑/↓ toggles (#10)"
            >
              {recommendTiers.isPending ? "Recommending…" : "✦ Recommend tiers"}
            </button>
          )}
          <span className="note">
            The discovery term set the draft reads — your <b>seeds</b> are the only <b>SIGNAL</b> (a hit
            PLACES); keyword-gen proposes the <b>BROAD</b> terms (corroboration → VERIFY). Seed and curate
            below; <b>Recommend tiers</b> has the model flag each term (you confirm via the ↑/↓ toggles).
          </span>
        </div>
        {produceTerms.isError && (
          <ErrorToast>Couldn't produce terms — {errText(produceTerms.error)}.</ErrorToast>
        )}
        {editTerms.isError && (
          <ErrorToast>Couldn't save the term edit — {errText(editTerms.error)}.</ErrorToast>
        )}
        {recommendTiers.isError && (
          <ErrorToast>Couldn't recommend tiers — {errText(recommendTiers.error)}.</ErrorToast>
        )}

        {/* Add a seed — works on an empty set (how a NEW thesis gets seeded). Lands SIGNAL / operator_set. */}
        <div className="wb-seed-add">
          <input
            type="text"
            className="wb-seed-input"
            placeholder="add a seed compound (SIGNAL — a hit places a name)…"
            value={newSeed}
            onChange={(ev) => setNewSeed(ev.target.value)}
            onKeyDown={(ev) => ev.key === "Enter" && addSeed()}
            disabled={editTerms.isPending}
          />
          <button
            type="button"
            className="wb-mini"
            onClick={addSeed}
            disabled={editTerms.isPending || !newSeed.trim()}
          >
            + Add seed
          </button>
        </div>

        {termSet.length > 0 ? (
          <div className="wb-terms-split">
            <div className="wb-terms-tier">
              <div className="wb-terms-tier-h">
                SIGNAL <small>· seeds — a hit PLACES</small>
              </div>
              <ul>
                {signalTerms.map((e, i) => (
                  <li key={i}>
                    <b>{e.term}</b>
                    <span className="wb-author">{termAuthor(e.authored_by)}</span>
                    <button
                      type="button"
                      className="wb-term-btn"
                      title="demote to BROAD (corroboration only — won't place alone)"
                      onClick={() => toggleTier(e.term)}
                      disabled={editTerms.isPending}
                    >
                      ↓ broad
                    </button>
                    <button
                      type="button"
                      className="wb-term-x"
                      title="remove this term"
                      onClick={() => removeTerm(e.term)}
                      disabled={editTerms.isPending}
                    >
                      ×
                    </button>
                    {recTag(e)}
                    {cappedTag(e)}
                  </li>
                ))}
                {signalTerms.length === 0 && (
                  <li className="muted">none — seed canonical compounds to place names</li>
                )}
              </ul>
            </div>
            <div className="wb-terms-tier">
              <div className="wb-terms-tier-h">
                BROAD <small>· corroboration — VERIFY only</small>
              </div>
              <ul>
                {broadTerms.map((e, i) => (
                  <li key={i}>
                    <b>{e.term}</b>
                    <span className="wb-author">{termAuthor(e.authored_by)}</span>
                    <button
                      type="button"
                      className="wb-term-btn"
                      title="promote to SIGNAL (a hit will place a name alone)"
                      onClick={() => toggleTier(e.term)}
                      disabled={editTerms.isPending}
                    >
                      ↑ signal
                    </button>
                    <button
                      type="button"
                      className="wb-term-x"
                      title="remove this term"
                      onClick={() => removeTerm(e.term)}
                      disabled={editTerms.isPending}
                    >
                      ×
                    </button>
                    {recTag(e)}
                    {cappedTag(e)}
                  </li>
                ))}
                {broadTerms.length === 0 && <li className="muted">none</li>}
              </ul>
            </div>
          </div>
        ) : (
          !produceTerms.isPending && (
            <div className="note">
              No term set yet — add a seed above (or Produce) before drafting; a draft without one returns
              “term set is empty”.
            </div>
          )
        )}
          </>
        )}
      </div>

      <div className="wb-draft-gap">
        <button type="button" className="wb-edit-btn" onClick={onDraft} disabled={drafting}>
          {drafting ? "Drafting… (can take a few minutes)" : "✦ Draft from narrative"}
        </button>
        <span className="note">
          Pre-fill the chain from your narrative — the drafter proposes the links, the names in each, and
          thesis-fit prose; you accept / edit / drop each. Names resolve against the master (exact membership
          decides); a placed name is <b>unscored</b> until you extract → ratify it. Nothing is sent until Save.
        </span>
      </div>
      {/* The run-loader picker (a dev/test cost-saver): load a SAVED draft run into this editor instead of
          paying for a fresh draft. Self-contained + self-hiding (absent when the loader flag is off or the
          thesis has no saved runs). onLoad clears the draft error/empty notes, then applyDraft reproduces the
          full workbench; disabled while a live draft is polling (no load-vs-poll race). */}
      <RunPicker
        thesisId={thesis.id}
        disabled={drafting}
        onLoad={(d) => {
          setDraftError(null);
          setDraftEmpty(false);
          applyDraft(d);
        }}
      />
      {draftError && <ErrorToast>Couldn't draft — {draftError}.</ErrorToast>}
      {draftEmpty && (
        <div className="note">
          The drafter returned nothing — no <code>ANTHROPIC_API_KEY</code> in the stack, or the model
          declined. Hand-authoring below is unaffected.
        </div>
      )}
      {draftStatus && (
        <DraftStatusStrip counts={draftStatus.counts} report={draftStatus.report} />
      )}

      {/* The value-chain LINKS editor — made self-describing (the operator couldn't tell the links, the
          "Discovered" holding pen, and the "add a link" box apart). Three labeled regions: real links
          (reorderable), the unsorted pen (NOT a link — no arrows), and add-a-link on its own row. */}
      <div className="wb-seg-edit">
        <div className="wb-seg-head">
          <div className="wb-seg-title">
            Value chain <em>· the links your basket decomposes into</em>
          </div>
          <div className="note wb-seg-desc">
            Each link is a stage in the theme's chain. Reorder with <b>← →</b>, rename inline, <b>×</b> removes
            it (its names return to the unsorted pen). Sort names into a link on the <b>Placed</b> rows below.
          </div>
        </div>

        {/* the real value-chain links — reorderable (← → operate among the links; the pen isn't one) */}
        <div className="wb-seg-links">
          {realLinks.map((s, i) => (
            <div className="wb-seg-chip" key={s.label}>
              <input
                className="wb-input"
                value={s.label}
                size={Math.max(s.label.length, 12)}
                aria-label={`link ${i + 1} label`}
                onChange={(e) => d.renameSegment(s.label, e.target.value)}
              />
              <button
                type="button"
                className="wb-mini"
                disabled={i === 0}
                aria-label={`move ${s.label} earlier`}
                title="move this link earlier in the chain"
                onClick={() => d.moveSegment(s.label, -1)}
              >
                ←
              </button>
              <button
                type="button"
                className="wb-mini"
                disabled={i === realLinks.length - 1}
                aria-label={`move ${s.label} later`}
                title="move this link later in the chain"
                onClick={() => d.moveSegment(s.label, 1)}
              >
                →
              </button>
              <button
                type="button"
                className="wb-mini ghost"
                aria-label={`remove ${s.label}`}
                title="remove this link — its names return to the unsorted pen"
                onClick={() => d.removeSegment(s.label)}
              >
                ×
              </button>
            </div>
          ))}
          {realLinks.length === 0 && (
            <span className="note">No links yet — add one below, or draft from the narrative.</span>
          )}
        </div>

        {/* the "Discovered" holding pen — a SORTING QUEUE, not a value-chain link (Item 6). De-linked (muted,
            dashed), the label is read-only (renaming it would silently turn the pen into a link), and there are
            NO reorder arrows (order is meaningless for a pen). Keepers get sorted OUT via the Placed rows' seg
            dropdown; × dismisses an emptied pen. */}
        {discoveredSeg && (
          <div className="wb-seg-pen">
            <span className="wb-seg-pen-lab">Unsorted</span>
            <div className="wb-seg-chip discovered">
              <span className="wb-seg-pen-name">{discoveredSeg.label}</span>
              <span className="seg-tag">not a link</span>
              <button
                type="button"
                className="wb-mini ghost"
                aria-label="remove the unsorted pen"
                title="dismiss the unsorted pen (its names become unplaced)"
                onClick={() => d.removeSegment(discoveredSeg.label)}
              >
                ×
              </button>
            </div>
            {discoveredCount > 0 && hasRealLink && (
              <span className="note wb-seg-pen-nudge">
                {discoveredCount} {discoveredCount === 1 ? "name is" : "names are"} still unsorted — sort
                keepers into a link with each row's <b>seg</b> dropdown below.
              </span>
            )}
          </div>
        )}

        {/* add a link — its own row, clearly an add affordance (not another link) */}
        <div className="wb-seg-add">
          <input
            className="wb-input"
            placeholder="add a link…"
            aria-label="new link label"
            value={newSeg}
            onChange={(e) => setNewSeg(e.target.value)}
          />
          <button
            type="button"
            className="wb-mini"
            onClick={() => {
              d.addSegment(newSeg);
              setNewSeg("");
            }}
          >
            + link
          </button>
        </div>
      </div>

      {/* ===== Results buckets (post-draft IA): PLACED · TO REVIEW · COULDN'T RESOLVE. Three distinct
              questions, never conflated (see docs/mockups/mockup_workbench_results.html). Scoped under
              .wb-results so the mock's class names don't collide with ScoredRow's .nmrow/.fit etc. ===== */}
      <div className="wb-results">
        {/* PLACED — the ONE basket, shown flat until a partition discriminates, then as up to three display
            groups (C-B: "Placed" / "Placed, flagged" by the narrator's off-thesis opinion; G: "Placed,
            low quality" when model-flagged AND a junk-tell matches). Groups are VIEWS — membership, include, and
            Save are computed over the whole draft regardless of grouping (#9, test-guarded). */}
        <div className="sect">
          <button
            type="button"
            className="sect-h wb-sect-toggle"
            aria-expanded={placedOpen}
            onClick={() => setPlacedOpen((o) => !o)}
          >
            <span className="chev">{placedOpen ? "▾" : "▸"}</span>
            Placed names <em>· segment drafted, overridable · archetype decided later, on the scored rail</em>
            {d.draft.basket.length > 0 && (
              <span className="ct">
                · {d.includedBasket.length} of {d.draft.basket.length} included
              </span>
            )}
          </button>
          {placedOpen && (
            <>
          {/* TRIAGE bulk actions (the prune) — include is default-on (#9); these are visible bulk excludes, never
              a silent filter. "Clear un-accepted" excludes still-drafted names (the fast path to just-my-vouched
              names) without touching authorship. */}
          {d.draft.basket.length > 0 && (
            <div className="wb-triage-bulk">
              <span className="note">Only included names are saved.</span>
              <button type="button" className="wb-mini ghost" onClick={d.includeAll}>
                include all
              </button>
              <button type="button" className="wb-mini ghost" onClick={d.excludeAll}>
                exclude all
              </button>
              <button
                type="button"
                className="wb-mini ghost"
                title="exclude every still-drafted name — keep only the ones you've accepted or edited"
                onClick={d.excludeUnaccepted}
              >
                clear un-accepted
              </button>
              <button
                type="button"
                className="wb-mini ghost"
                disabled={d.includedBasket.length === 0}
                aria-label={`export ${d.includedBasket.length} included names`}
                onClick={() =>
                  exportKeptNames({
                    thesisName: thesis.name,
                    stage: "triage",
                    asof,
                    rows: d.includedBasket.map((m) =>
                      toExportedName({
                        ticker: m.ticker,
                        name:
                          (m.security_id ? names[m.security_id] : undefined) ??
                          (m.security_id ? scoredById?.[m.security_id]?.name : undefined),
                      }),
                    ),
                  })
                }
              >
                Export ({d.includedBasket.length})
              </button>
            </div>
          )}
          {/* Item 1: the clean pre-surfacing state — one quiet hint instead of "needs SURFACE" on every row. */}
          {d.draft.basket.length > 0 && !anyFundamentals && (
            <div className="note">
              Surface your shortlist — hit <b>⇣ get data</b> on a name in the scored view, then ratify the
              candidates in its rail — confirmed fundamentals show here.
            </div>
          )}
          {/* TRIAGE PR-2 (the find) — sort + filter the placed list. VIEW-ONLY: it never changes what Save
              persists (that's basket − excluded, over the whole draft). Clear-filters is always one click away
              so a hidden-but-included name is never lost (#9). */}
          {d.draft.basket.length > 1 && (
            <div className="wb-triage-find">
              <label className="wb-find-ctl">
                sort
                <select
                  aria-label="sort placed names"
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                >
                  <option value="draft">draft order</option>
                  <option value="name">name</option>
                  <option value="archetype">archetype</option>
                  <option value="segment">segment</option>
                  <option value="sector">sector</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                archetype
                <select
                  aria-label="filter by archetype"
                  value={fArch}
                  onChange={(e) => setFArch(e.target.value)}
                >
                  <option value="">all</option>
                  {archsPresent.map((a) => (
                    <option key={a} value={a}>
                      {archLabel(a)}
                    </option>
                  ))}
                  <option value="__unset__">— unset —</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                segment
                <select
                  aria-label="filter by segment"
                  value={fSeg}
                  onChange={(e) => setFSeg(e.target.value)}
                >
                  <option value="">all</option>
                  {segLabels.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                  <option value="__unplaced__">— unplaced —</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                fundamentals
                <select
                  aria-label="filter by fundamentals"
                  value={fFund}
                  onChange={(e) => setFFund(e.target.value as typeof fFund)}
                >
                  <option value="">all</option>
                  <option value="loaded">loaded</option>
                  <option value="needs">not loaded</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                authorship
                <select
                  aria-label="filter by authorship"
                  value={fAuth}
                  onChange={(e) => setFAuth(e.target.value as typeof fAuth)}
                >
                  <option value="">all</option>
                  <option value="accepted">accepted</option>
                  <option value="drafted">drafted</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                include
                <select
                  aria-label="filter by include"
                  value={fInc}
                  onChange={(e) => setFInc(e.target.value as typeof fInc)}
                >
                  <option value="">all</option>
                  <option value="included">included</option>
                  <option value="excluded">excluded</option>
                </select>
              </label>
              <button
                type="button"
                className={`wb-mini ghost${fOffUniv ? " on" : ""}`}
                aria-pressed={fOffUniv}
                onClick={() => setFOffUniv((v) => !v)}
              >
                off-universe
              </button>
              <button
                type="button"
                className={`wb-mini ghost${compact ? " on" : ""}`}
                aria-pressed={compact}
                title="collapse the thesis-fit prose for a scannable read"
                onClick={() => setCompact((v) => !v)}
              >
                compact
              </button>
              {filtersActive && (
                <button type="button" className="wb-mini" onClick={clearFilters}>
                  clear filters
                </button>
              )}
              <span className="note">
                showing {triaged.length} of {d.draft.basket.length} placed
                {fInc && verify.length > 0
                  ? ` · ${verifyVisible.length} of ${verify.length} to review`
                  : ""}
              </span>
            </div>
          )}
          {(() => {
            // ONE row renderer shared by the flat list and the C-B/G display groups (it closes over the
            // editor's run-state — matched/identity/names/offThesisSet — so it stays a local, not a component).
            const placedRow = (m: BasketMember) => {
            const k = memberKey(m);
            const drafted = m.authored_by === "system_drafted";
            const mt = m.security_id ? matched[m.security_id] : undefined;
            // the narrator's off-thesis OPINION (bridged by security_id). RECOMMENDS only (#10): the name stays
            // placed (#9); the reason is its prose in the fit note below. Absent → not flagged (fail-open).
            const offThesis = m.security_id ? offThesisSet.has(m.security_id) : false;
            const included = d.isIncluded(k);
            const loaded = hasFundamentals(m.security_id, scoredById);
            return (
              <div
                className={`nmrow${offThesis ? " flagged" : ""}${included ? "" : " excluded"}`}
                key={k}
              >
                <div className="top">
                  {/* TRIAGE include toggle (default-on, #9): unchecking EXCLUDES the name from Save; the row stays
                      visible (greyed), one click from re-including. Orthogonal to accept — never touches authorship. */}
                  <input
                    type="checkbox"
                    className="wb-inc"
                    aria-label={`include ${m.ticker}`}
                    checked={included}
                    onChange={() => d.toggleInclude(k)}
                  />
                  {/* the archetype color (incl. red high-beta) only shows on an operator-owned name whose
                      archetype IS set (a finalize-rail decision) — unset renders neutral, not a wall of red. */}
                  <span
                    className="tk"
                    style={
                      !drafted && m.archetype ? { color: ARCH_COLOR[m.archetype] } : undefined
                    }
                  >
                    {m.ticker}
                  </span>
                  {/* the company name (bridged by security_id — BasketMember carries no name), like To Review */}
                  {m.security_id && names[m.security_id] ? (
                    <span className="co">{names[m.security_id]}</span>
                  ) : null}
                  {m.role && m.role !== "—" ? <span className="co role">{m.role}</span> : null}
                  {/* R3: an EXCLUDED (set-aside) row collapses to a quiet stub — checkbox + ticker + name + an
                      "excluded" tag stay visible (#9, re-check to restore); its chips, controls, and prose are
                      hidden so the noise recedes (inverse loudness). Exclude is VIEW-only here — it never touches
                      authorship (orthogonal A: an edited note stays operator_edited, safe from the next re-roll). */}
                  {!included ? (
                    <>
                      <span
                        className="wb-exc-tag"
                        title="excluded from Save — re-check to restore its detail"
                      >
                        excluded
                      </span>
                      {/* #7: the optional "rejected because X" — persisted with the exclusion on
                          Save; quiet, skippable, editable (never a modal on a 300-name prune) */}
                      <input
                        className="wb-exc-why"
                        aria-label={`why excluded ${m.ticker}`}
                        placeholder="why? (optional)"
                        value={d.reasons.get(k) ?? ""}
                        onChange={(e) => d.editReason(k, e.target.value)}
                      />
                    </>
                  ) : (
                    <>
                      {m.security_id && offUniverse.has(m.security_id) && <OffUniversePill />}
                      {m.security_id && identity[m.security_id] && (
                        <IdentityChips {...identity[m.security_id]} />
                      )}
                      {/* TRIAGE: fundamentals loaded vs not. Item 1 — shown ONLY once it DISCRIMINATES (≥1 name in
                          the basket has confirmed fundamentals); before any surfacing every row is "needs SURFACE",
                          which is pure noise, so the per-row badge is suppressed (a single header hint carries it). */}
                      {anyFundamentals &&
                        (loaded ? (
                          <span className="fund-badge on" title="confirmed fundamentals on file (purity / runway / market cap)">
                            ✓ fundamentals
                          </span>
                        ) : (
                          <span className="fund-badge" title="no confirmed fundamentals yet — extract → ratify in the facts panel">
                            needs SURFACE
                          </span>
                        ))}
                      {/* R1: the SEG / CONV controls sit on their own line; the row actions (accept +
                          send-back) right-align at the END of this row. NO archetype control here (item F):
                          the archetype is decided ONCE, on the finalize rail — a set value shows read-only
                          (a re-opened finalized basket), an unset one shows nothing. */}
                      <span className="ctls">
                        {m.archetype && (
                          <span className="ctl">
                            <span className="lab">arch</span>
                            <span
                              className={`arch ${m.archetype}`}
                              title="set on the scored view's rail (the finalize step) — not editable at placement"
                            >
                              {archLabel(m.archetype)}
                            </span>
                          </span>
                        )}
                        <span className="ctl">
                          <span className="lab">seg</span>
                          {/* Item 7: WIRED — selecting a link re-segments the name (`placeMember`). No "— remove —"
                              here: pruning is the include-uncheck + the off-thesis remove; this control does ONE
                              thing (move a name into a value-chain link — the way to sort keepers out of "Discovered"). */}
                          <select
                            value={m.segment ?? ""}
                            aria-label={`segment for ${m.ticker}`}
                            onChange={(e) => e.target.value && d.placeMember(k, e.target.value)}
                          >
                            {!m.segment && <option value="">— segment —</option>}
                            {segLabels.map((l) => (
                              <option key={l} value={l}>
                                {l === DISCOVERED ? "Discovered (unsorted)" : l}
                              </option>
                            ))}
                          </select>
                        </span>
                        {/* TRIAGE: the operator's per-name conviction/size (1–5; blank = unset, never 0). A crafting
                            input, orthogonal to accept — it never touches authorship, and it never feeds the score. */}
                        <span className="ctl">
                          <span className="lab" title="your conviction / intended size — 1 starter … 5 full">
                            conv
                          </span>
                          <select
                            className="wb-conv"
                            value={m.conviction ?? ""}
                            aria-label={`conviction for ${m.ticker}`}
                            onChange={(e) =>
                              d.editConviction(k, e.target.value ? Number(e.target.value) : null)
                            }
                          >
                            <option value="">—</option>
                            {[1, 2, 3, 4, 5].map((n) => (
                              <option key={n} value={n}>
                                {n}
                              </option>
                            ))}
                          </select>
                        </span>
                        {/* the row actions right-align at the END of the controls row (accept ⇄ un-accept · the
                            To-Review send-back) — chosen over the top-right slot to de-orphan them and group the
                            knobs. Reversibility (#1): accept is a TOGGLE (the state carries authorship, no badge);
                            un-accept flips back to system_drafted keeping every field value (a re-draft re-rolls it). */}
                        <span className="rowactions">
                          <button
                            type="button"
                            className="wb-mini"
                            aria-label={`${drafted ? "accept" : "un-accept"} ${m.ticker}`}
                            title={
                              drafted
                                ? "ratify this drafted placement — you own it"
                                : "un-accept — hand it back to the drafter (values kept; a re-draft re-rolls it)"
                            }
                            onClick={() => d.toggleAccept(k)}
                          >
                            {drafted ? "✓ accept" : "✕ un-accept"}
                          </button>
                          {/* the inverse of "add" for a name pulled from To-Review — send it back (reversibility #1) */}
                          {m.security_id && verifyOrigin[m.security_id] && (
                            <button
                              type="button"
                              className="wb-mini ghost"
                              aria-label={`send ${m.ticker} back to review`}
                              title="send this name back to To-Review (the inverse of add)"
                              onClick={() => sendBackToVerify(m.security_id as string)}
                            >
                              ↩ to review
                            </button>
                          )}
                        </span>
                      </span>
                    </>
                  )}
                </div>
                {/* the row's detail (prose · provenance · off-thesis flag) is hidden while EXCLUDED (R3 collapse)
                    and while COMPACT (the scannable read). The prose auto-sizes to its content, capped at 3 rows
                    then scrolling (R2). */}
                {included && !compact && (
                  <AutoTextarea
                    className="wb-prose"
                    ariaLabel={`thesis-fit for ${m.ticker}`}
                    placeholder="why this name sits in its link — thesis-fit reasoning (drafted, or yours)…"
                    value={m.thesis_fit ?? ""}
                    onChange={(v) => d.editProse(k, v)}
                  />
                )}
                {included && mt && mt.length > 0 && (
                  <div className="prov" title={`discovery match: ${mt.join(", ")}`}>
                    ← {mt.join(" · ")}
                  </div>
                )}
                {included && offThesis && (
                  <div className="flag">⚑ model thinks off-thesis — stays placed; uncheck to exclude</div>
                )}
              </div>
            );
            };
            // A display group over the ONE placed membership (C-B/G): a quiet drawer with its own collapse
            // + per-group preview. Renders nothing when empty — an empty partition is noise.
            const group = (
              gkey: string,
              title: string,
              meta: string,
              rows: BasketMember[],
              open: boolean,
              toggle: () => void,
              extra?: ReactNode,
            ) =>
              rows.length === 0 ? null : (
                <div className="resolve wb-placed-group">
                  <button
                    type="button"
                    className="resolve-h"
                    aria-expanded={open}
                    aria-label={`toggle ${title}`}
                    onClick={toggle}
                  >
                    <span className="chev">{open ? "▾" : "▸"}</span>
                    <span className="rt">{title}</span>
                    <span className="rm-meta">
                      {meta ? `${meta} · ` : ""}
                      {rows.length}
                    </span>
                  </button>
                  {open && (
                    <div className="resolve-body">
                      {extra}
                      {shownRows(gkey, rows).map(placedRow)}
                      {showMoreBtn(gkey, rows)}
                    </div>
                  )}
                </div>
              );
            // Flat when the partition doesn't discriminate (no flags, no low-quality) — today's single list.
            if (!groupingActive) {
              return (
                <>
                  {shownRows("placed", triaged).map(placedRow)}
                  {showMoreBtn("placed", triaged)}
                </>
              );
            }
            return (
              <div className="wb-placed-groups">
                {group("placed", "Placed", "", gClean, cleanOpen, () => setCleanOpen((o) => !o))}
                {group(
                  "flagged",
                  "Placed, flagged",
                  "model-flagged off-thesis — still saved unless excluded",
                  gFlagged,
                  flaggedOpen,
                  () => setFlaggedOpen((o) => !o),
                )}
                {group(
                  "low_quality",
                  "Placed, low quality",
                  "model-flagged off-thesis + junk tell matched",
                  gLowQuality,
                  lowQualityOpen,
                  () => setLowQualityOpen((o) => !o),
                  <div className="wb-triage-bulk">
                    <span className="note">
                      Each name here was flagged off-thesis by the model AND matched a junk tell (acronym
                      collision, fund-name pattern, …). Scan for real names, then clear the rest.
                    </span>
                    <button
                      type="button"
                      className="wb-mini ghost"
                      title="exclude every name in this group from Save — each stays visible (greyed) and re-includable in one click"
                      onClick={() => d.excludeKeys(gLowQuality.map(memberKey))}
                    >
                      exclude all {gLowQuality.length}
                    </button>
                  </div>,
                )}
              </div>
            );
          })()}
          {d.draft.basket.length === 0 && (
            <div className="note">No names yet — draft from the narrative, or add one below.</div>
          )}
          {d.draft.basket.length > 0 && triaged.length === 0 && (
            <div className="note">
              No names match the filter — <button type="button" className="wb-linkbtn" onClick={clearFilters}>clear filters</button> to see all {d.draft.basket.length}.
            </div>
          )}
            </>
          )}
        </div>

        {/* TO REVIEW — resolved, lower confidence. ONE master collapsible holding three nested sub-drawers
            (Keepers · Low signal · No listed ticker), mirroring the Placed section (.wb-placed-groups). Inverse
            loudness (#7): Keepers are the surfaced signal (open); the two noise buckets stay quiet + collapsed.
            Nothing dropped (#9) — every bucket stays promotable via the same check-to-add. The master header
            count stays keepers-only (the headline is the signal; each sub-drawer carries its own count). */}
        {verify.length > 0 && (
          <div className="sect">
            <button
              type="button"
              className="sect-h wb-sect-toggle"
              aria-expanded={reviewOpen}
              onClick={() => setReviewOpen((o) => !o)}
            >
              <span className="chev">{reviewOpen ? "▾" : "▸"}</span>
              To review <em>· in your universe, lower confidence — confirm or dismiss</em>
              <span className="ct">· {vKeepers.length}</span>
            </button>
            {reviewOpen && (
              <div className="wb-placed-groups">
                {/* the keepers — the signal, surfaced (its own sub-drawer, open by default) */}
                {vKeepers.length > 0 ? (
                  <div className="resolve wb-placed-group">
                    <button
                      type="button"
                      className="resolve-h"
                      aria-expanded={keepersOpen}
                      aria-label="toggle Keepers"
                      onClick={() => setKeepersOpen((o) => !o)}
                    >
                      <span className="chev">{keepersOpen ? "▾" : "▸"}</span>
                      <span className="rt">Keepers</span>
                      <span className="rm-meta">on-thesis, has a ticker · {vKeepers.length}</span>
                    </button>
                    {keepersOpen && (
                      <div className="resolve-body">
                        {vKeepers.map((p, i) => verifyRow(p, `keep-${i}`))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="note">
                    No clear keepers — the model didn't flag any of these as a strong fit. The Low signal /
                    No listed ticker drawers below hold the rest.
                  </div>
                )}
                {/* off-thesis noise — quiet, NO yellow (the majority; highlight keepers, not this) */}
                {vOffThesis.length > 0 && (
                  <div className="resolve wb-placed-group">
                    <button
                      type="button"
                      className="resolve-h"
                      aria-expanded={lowSignalOpen}
                      aria-label="toggle Low signal"
                      onClick={() => setLowSignalOpen((o) => !o)}
                    >
                      <span className="chev">{lowSignalOpen ? "▾" : "▸"}</span>
                      <span className="rt">Low signal</span>
                      <span className="rm-meta">
                        model sees no clear thesis fit · {vOffThesis.length} hidden
                      </span>
                    </button>
                    {lowSignalOpen && (
                      <div className="resolve-body">
                        {vOffThesis.map((p, i) => verifyRow(p, `off-${i}`))}
                      </div>
                    )}
                  </div>
                )}
                {/* ticker-less — quiet (likely subs/holdcos/debt; probably not directly investable) */}
                {vNoTicker.length > 0 && (
                  <div className="resolve wb-placed-group">
                    <button
                      type="button"
                      className="resolve-h"
                      aria-expanded={noTickerOpen}
                      aria-label="toggle No listed ticker"
                      onClick={() => setNoTickerOpen((o) => !o)}
                    >
                      <span className="chev">{noTickerOpen ? "▾" : "▸"}</span>
                      <span className="rt">No listed ticker</span>
                      <span className="rm-meta">
                        likely a sub / holdco / debt issuer — probably not directly investable ·{" "}
                        {vNoTicker.length}
                      </span>
                    </button>
                    {noTickerOpen && (
                      <div className="resolve-body">
                        {vNoTicker.map((p, i) => verifyRow(p, `nt-${i}`))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* COULDN'T RESOLVE — identity-resolution failures, ORTHOGONAL to thesis-fit. A quiet drawer; never
            confused with to-review (which is all resolved names). Ambiguous gets a CIK picker; absent is
            display-only. */}
        {(ambiguous.length > 0 || absent.length > 0) && (
          <div className="sect">
            <div className="resolve">
              <button
                type="button"
                className="resolve-h"
                aria-expanded={couldntOpen}
                onClick={() => setCouldntOpen((o) => !o)}
              >
                <span className="chev">{couldntOpen ? "▾" : "▸"}</span>
                <span className="rt">Couldn't resolve</span>
                <span className="rm-meta">
                  identity, not thesis-fit · {ambiguous.length} ambiguous · {absent.length} absent
                </span>
              </button>
              {couldntOpen && (
                <div className="resolve-body">
                  {ambiguous.map((p, i) => {
                    // A name gated for NO CURRENT LISTING (Slice 2) lands here too — but it's not a redomicile
                    // collision, so it reads with a hedged "not listed" pill + note + a "place anyway" action
                    // (the frictionless rescue; the candidate is its own row). A guess, never a verdict (#9).
                    const unlisted = p.listing_status === "inactive";
                    return (
                    <div key={`amb-${i}`}>
                      <div className="rrow">
                        <span className="tk">{p.ticker || "—"}</span>
                        <span className="co">{p.name}</span>
                        {p.discovery_source === "off_universe" && <OffUniversePill />}
                        <IdentityChips
                          sector={p.sector}
                          exchange={p.exchange}
                          category={p.category}
                        />
                        {unlisted ? (
                          <span className="rpill unlisted">not listed</span>
                        ) : (
                          <span className="rpill amb">ambiguous</span>
                        )}
                        <button
                          type="button"
                          className="rbtn"
                          aria-label={`${unlisted ? "place" : "pick CIK for"} ${p.name}`}
                          onClick={() => togglePick(p.name)}
                        >
                          {unlisted ? "place anyway…" : "pick CIK…"}
                        </button>
                      </div>
                      <div className="rnote">
                        {unlisted
                          ? "no current listing found in EDGAR — a guess (listing-presence heuristic), not a delisting; place it anyway if it's real"
                          : "matched several CIKs (e.g. a redomicile) — choose which entity is the real one before it can place"}
                      </div>
                      {pickOpen.has(p.name) && (
                        <ul className="wb-matches">
                          {p.candidates.map((c) => {
                            const inBasket = keys.has(c.security_id);
                            return (
                              <li key={c.security_id}>
                                <button
                                  type="button"
                                  disabled={inBasket}
                                  onClick={() => pickAmbiguous(p, c)}
                                >
                                  <b>{c.ticker}</b>
                                  {c.cik ? <span className="cik">CIK {c.cik}</span> : null}
                                  {c.name ? <span className="co">{c.name}</span> : null}
                                  {inBasket ? <span className="muted"> · in basket</span> : null}
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                    );
                  })}
                  {absent.map((p, i) => (
                    <div key={`abs-${i}`}>
                      <div className="rrow">
                        <span className="tk">{p.ticker || "—"}</span>
                        <span className="co">{p.name}</span>
                        {p.discovery_source === "off_universe" && <OffUniversePill />}
                        <span className="rpill abs">absent</span>
                        <span className="rtag">no SEC filer</span>
                      </div>
                      <div className="rnote">
                        named in filings but has no master row — private, not yet an SEC registrant
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <AddName
        existingKeys={keys}
        onAdd={(m, name) => {
          d.addMember(m);
          if (m.security_id && name) setNames((prev) => ({ ...prev, [m.security_id as string]: name }));
        }}
      />
    </div>
  );
}
