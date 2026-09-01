import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  BasketMember,
  ChainDraftOut,
  DraftReportOut,
  DraftScope,
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
import { businessTypeLabel } from "../util/format";
import { useDebouncedCallback } from "../util/useDebouncedCallback";
import { AddName } from "./AddName";
import { SurfaceEtf } from "./SurfaceEtf";
import { AutoTextarea } from "./AutoTextarea";
import { DraftStatusStrip, type DraftCounts } from "./DraftStatusStrip";
import {
  countryClass,
  type CountryClass,
  errText,
  exchangeClass,
  type ExchangeClass,
  filerRegime,
  memberHasFundamentals,
  originWithFiler,
  spacClass,
  type SpacClass,
} from "./format";
import {
  matchesAnyJunkTell,
  signalAcronymTermsFrom,
  type JunkTellContext,
} from "./junkTells";
import { RunPicker } from "./RunPicker";
import { groupHasTerm, hasTermUniverse, termsInclude, termUniverse } from "./termFilter";
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
  // Wipe the saved prune session and re-seed the editor fresh from the thesis (the explicit "start over"). The
  // parent owns it (it deletes the session + force-remounts this editor); omitted in test/un-sessioned renders.
  onStartOver?: () => void;
  // S3 (re-scope, parent-owned like onStartOver): clear the transient candidate pile and re-run discovery on
  // the CURRENT term set, keeping the whole saved Basket frozen — the parent confirms, deletes the session,
  // and remounts this editor fresh-from-thesis with `autoDraft`. Omitted in test/un-sessioned renders.
  onRescope?: () => void;
  // S3: fire ONE draft at mount — set by the parent ONLY on the re-scope remount. The Re-scope click is the
  // explicit operator action (the sanctioned exception to "never on render"); a ref guard makes the kick-off
  // exactly once per mount, `drafting` disables the buttons, and the server's one-running-draft 409 backstops.
  autoDraft?: boolean;
  // S3: the restored session's server `updated_at`. Present ⇒ this mount SEEDED from an autosaved session
  // (not the saved spine) → the quiet "resumed autosave · age" badge renders beside the autosave indicator,
  // so a session-driven editor is never indistinguishable from a spine-driven one (the 159-vs-160 tell).
  restoredUpdatedAt?: string;
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

// S3 — compact relative age for the resumed-autosave badge ("just now" / "5m ago" / "3h ago" / "2d ago").
// A malformed timestamp reads "just now" (harmless — the badge's presence is the signal, the age is color).
const relAge = (iso: string): string => {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 60_000) return "just now";
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
};

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

// The per-member display identity shape — the draft/session `identity` map's value AND the scored-join
// baseline (`idFor` merges the two; see the D4 comment at its definition).
type MemberIdentity = {
  sector?: string | null;
  // the normalized two-level business type (Business-Type M1) + its royalty overlay — derived server-side
  // from the SIC; the cockpit's Type read, shown here MUTED. Both carried from the scored join (`sm.*`).
  businessType?: string | null;
  royalty?: boolean | null;
  exchange?: string | null;
  category?: string | null;
  origin?: string | null;
  foreignFilerForm?: string | null;
};

// Machine-parsed IDENTITY (Slice 2 enrichment) — quiet sector / exchange chips. Display-only (parsed from the
// name's EDGAR submissions onto the master), never promoted onto a BasketMember. Renders nothing when absent
// (an un-enriched / off-universe name — the honest fallback).
const IdentityChips = ({
  sector,
  businessType,
  royalty,
  exchange,
  category,
  origin,
  foreignFilerForm,
}: {
  sector?: string | null;
  businessType?: string | null;
  royalty?: boolean | null;
  exchange?: string | null;
  category?: string | null;
  origin?: string | null;
  foreignFilerForm?: string | null;
}) => (
  <>
    {/* a blank-check shell (SIC "Blank Checks") gets a quiet warm tint — the one sector that means
        "nothing to act on until a deal". An identity nuance, not a warning flag (#7). */}
    {sector && (
      <span
        className={`idchip${spacClass(sector) === "spac" ? " spac" : ""}`}
        title={
          spacClass(sector) === "spac"
            ? 'blank-check shell (SEC SIC "Blank Checks") — a SPAC still hunting a target; nothing to act on until a deal'
            : "sector (SEC SIC) — machine-parsed from EDGAR submissions"
        }
      >
        {sector}
      </span>
    )}
    {/* BUSINESS TYPE — the normalized two-level bucket derived server-side from the SIC (Business-Type M1):
        the cockpit's "Type" read, shown here MUTED (matching its neighbours, NOT the cockpit's coloured
        `bt-` chip). Rides right after the raw SIC so the two read as a pair. Display identity like the rest;
        a no-sector name has no `businessType` → renders NOTHING (honest abstain). `other` (a SIC the maps
        don't cover) DOES show — the visible tail (#9), not a guess. ◈ marks the royalty/streaming overlay
        (honest loudness — a rare company-NAME tell, ~32 of 8k names). */}
    {businessType && (
      <span
        className="idchip"
        title={`business type — normalized from the SEC SIC (securities/business_type)${royalty ? " · ◈ royalty/streaming" : ""}`}
      >
        {businessTypeLabel(businessType)}
        {royalty && <span className="bt-royalty">◈</span>}
      </span>
    )}
    {exchange && (
      <span className="idchip" title="exchange — machine-parsed from EDGAR submissions">
        {exchange}
      </span>
    )}
    {/* SEC filer category — a maturity/size tell. IDENTITY (sits with sector/exchange), NOT a re-classification
        of the risk read. Machine-parsed from EDGAR submissions, display-only. */}
    {category && (
      <span className="idchip" title="SEC filer category — a maturity/size tell (EDGAR submissions)">
        {category}
      </span>
    )}
    {/* ORIGIN — where the name is from, derived server-side from the SEC's own locators (business address
        country/city, else incorporation; a US-state address reads "US"). The spot-and-skip tell for foreign
        names. Identity like the rest — it TAGS, never filters (#9); the operator's prune is the decision
        (#10). Unknown renders NOTHING (honest abstain — never a guessed origin). */}
    {origin && (
      <span
        className="idchip"
        title="origin — business address country/city (or incorporation) machine-parsed from EDGAR submissions"
      >
        {origin}
      </span>
    )}
    {/* FOREIGN FILER — a second origin chip beside origin: a §16-exempt 20-F (FPI) / 40-F (Canadian MJDS)
        filer files NO Form 4, so the insider signal is structurally unavailable. "40-F · no Form 4" (the
        origin is already its own chip). Identity like the rest; unknown renders NOTHING (honest abstain). */}
    {foreignFilerForm && (
      <span
        className="idchip"
        title={`${filerRegime(foreignFilerForm) ?? "foreign"} filer — §16-exempt, files no Form 4 (the insider signal is structurally unavailable, not quiet)`}
      >
        {originWithFiler(null, foreignFilerForm)}
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

/** The authoring surface (Slice 4b + the S5 draft/ratify, 5c — reworked by Discovery cleanup S1): build
 *  the value chain by hand — or DRAFT it from the narrative and triage per name on the CONFIDENCE LADDER:
 *  Excluded → Included (system-recommended) → Signed off (excluded wins). Sign-off ENDORSES the NAME —
 *  it never sets authorship and never gates Save. HONEST AUTHORSHIP: a description reads "model draft"
 *  until the operator EDITS it → "your words" (`operator_edited`); nothing else flips it. The draft's
 *  recommended link(s) render as READ-ONLY chips — a name recommended into N links holds N real
 *  membership rows (segment sorting + conviction live on the triage screen, not here). A name the
 *  drafter couldn't resolve uniquely (AMBIGUOUS) enters the basket ONLY by an explicit operator pick
 *  (ticker + CIK disambiguate); one with no master row (ABSENT) is shown, never placed. A drafted name
 *  is UNSCORED until the operator extract→ratifies it. Nothing persists until SAVE (the full-replace
 *  promote, which honors authorship and persists every membership row). */
export function ChainEditor({
  thesis,
  asof,
  onDone,
  scoredById,
  restored,
  onStartOver,
  onRescope,
  autoDraft,
  restoredUpdatedAt,
}: Props) {
  // The restored session seeds BOTH the hook (draft/excluded/reasons) and this component's own editor cells
  // (the draft-run buckets + term/set-aside decisions) at mount. `re` is the editor portion; `undefined` when
  // there's no session, so every initializer falls back to its thesis-derived / empty default.
  const re = restored?.editor;
  // S3 — the restored-mount marker, FROZEN at mount (useState initializer): the badge must reflect how THIS
  // editor instance seeded, and the parent's restored props DRIFT mid-session (the first autosave writes the
  // session back and the parent re-renders the same mounted key with `restored` now set) — a live read would
  // flip the badge on over a spine-seeded editor, the exact confusion the badge exists to close.
  const [restoredAt] = useState<string | null>(() => restoredUpdatedAt ?? null);
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
  // The ∅ dead-seed marker (the #9 recall gap made visible — the zero-hit counterpart to ⚠ capped): on the
  // LAST draft this term matched NO EDGAR filer, so a seed here placed no names. Loudness is tier-decided —
  // LOUD for a SIGNAL seed (it places alone, so a dead one is a real silent miss: the exceptional, high-stakes
  // case) and QUIET for a BROAD term (corroboration-only, muted). RUN state from the draft report (display-only,
  // cleared on re-draft) — never persisted, and NEVER auto-removes the term: the operator's existing remove /
  // re-tier / replace controls stay the only (reversible) way out (keep-it-visible).
  const emptyTag = (e: TermSetEntry) =>
    emptyTerms.has(norm(e.term)) ? (
      <span
        className={e.tier === "signal" ? "wb-rec wb-empty-loud" : "wb-rec wb-empty-quiet"}
        title="On the last draft this term matched no EDGAR filer — a seed here placed no names. Remove it, re-tier it, or replace it (or re-draft if you expect hits)."
      >
        ∅ no EDGAR hits
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
  // The dead ("empty") terms from the last draft — a term that matched ZERO EDGAR filers -> the ∅ chip marker.
  // Same RUN-state lifecycle as cappedTerms (set on a draft, cleared on re-draft, never persisted).
  const [emptyTerms, setEmptyTerms] = useState<Set<string>>(() => re?.emptyTerms ?? new Set());
  // Reversibility (principle #1): the origin placement of a name PULLED from To-Review into Placed, keyed by
  // security_id. It lets a Placed row that CAME from To-Review offer a "send back" — the visible inverse of add
  // (add ⇄ send-back). Only these names get the control (others were never in To-Review). Never persisted.
  const [verifyOrigin, setVerifyOrigin] = useState<Record<string, ResolvedPlacement>>(
    () => re?.verifyOrigin ?? {},
  );
  // CHERRY-PICK (pick-mode) — the Recommended pile: genuinely-NEW placed names a pick-mode draft DIVERTED
  // here for check-to-add instead of auto-loading into the basket (the starter workflow: check keepers IN,
  // the To-Review gesture with the default inverted). FLAT placements like `verify` (a multi-link name
  // carries N entries; the pile renders it as ONE row); session-persisted (additive) so unpicked
  // recommendations survive a refresh — working state, never vanished (#2).
  const [recommended, setRecommended] = useState<ResolvedPlacement[]>(() => re?.recommended ?? []);
  // Reversibility (#1) for a PICKED name: sid → the pile placements it left, so "↩ to recommended" can
  // restore the row exactly as it was — the array-valued twin of `verifyOrigin` (a multi-link name left N
  // placements). Only rows with an origin here get the send-back (others never came from the pile).
  const [recommendedOrigin, setRecommendedOrigin] = useState<Record<string, ResolvedPlacement[]>>(
    () => re?.recommendedOrigin ?? {},
  );
  // The operator's explicit LOAD-MODE choice — TRI-STATE: null = untouched → the LANE decides (⚡ quick
  // draft ⇒ pick-mode, ✦ full draft ⇒ auto-load, the operator-decided pairing); true/false = the explicit
  // choice, which wins for BOTH lanes. Surfaced in the select beside the draft buttons so the active mode
  // is visible BEFORE any kick-off. Session-persisted (additive).
  const [pickPref, setPickPref] = useState<boolean | null>(() => re?.pickPref ?? null);
  const effectivePick = (lane: "full" | "quick"): boolean => pickPref ?? lane === "quick";
  // the mode the CURRENTLY-RUNNING draft was kicked off under — FROZEN at kick-off: a toggle flipped
  // mid-run must not re-route a result the operator launched under the other mode
  const pickRun = useRef(false);
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
  const [identity, setIdentity] = useState<Record<string, MemberIdentity>>(
    () => re?.identity ?? {},
  );
  // Display-only: security_id -> the company NAME. The PLACED bucket renders BasketMembers (which carry no name),
  // so — like `matched`/`identity` — the name is bridged by security_id from the draft placements (and captured on
  // a manual add). NEVER promoted onto a BasketMember.
  const [names, setNames] = useState<Record<string, string>>(() => re?.names ?? {});
  // The identity READ for placed members (chips / filters / sector sort) — the identity-lifecycle read path.
  // The LIVE scored join is the baseline and WINS over the session/draft map (D4): the join reads the same
  // master rows the draft wrote, so it self-heals after a standalone `pipeline.enrich_identity` backfill —
  // no re-draft, no session surgery. Field-wise merge: a field the join carries beats the map's draft-time
  // snapshot; a field the join lacks (null — e.g. the scored fetch predates this session's draft-time
  // enrich) falls back to the map, so chips never regress while the join catches up. A member with no
  // scored row yet (just-drafted, unsaved) reads the map alone. READ-only: the serialize path
  // (`editorRuntime.identity` below) stays on the RAW map — the session blob keeps storing only
  // draft-time state, never the join.
  const identityFromScored = (sm: ScoredMemberOut): MemberIdentity => ({
    sector: sm.sector,
    businessType: sm.business_type,
    royalty: sm.royalty,
    exchange: sm.exchange,
    category: sm.category,
    origin: sm.origin,
    foreignFilerForm: sm.foreign_filer_form,
  });
  const idFor = (sid: string | null | undefined): MemberIdentity | undefined => {
    if (!sid) return undefined;
    const fromMap = identity[sid];
    const sm = scoredById?.[sid];
    if (!sm) return fromMap;
    const live = identityFromScored(sm);
    return {
      sector: live.sector ?? fromMap?.sector,
      businessType: live.businessType ?? fromMap?.businessType,
      royalty: live.royalty ?? fromMap?.royalty,
      exchange: live.exchange ?? fromMap?.exchange,
      category: live.category ?? fromMap?.category,
      origin: live.origin ?? fromMap?.origin,
      foreignFilerForm: live.foreignFilerForm ?? fromMap?.foreignFilerForm,
    };
  };

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));
  // Item 1 (inverse loudness): the per-row fundamentals badge only earns its place once it DISCRIMINATES — i.e.
  // once ≥1 name in the basket has confirmed fundamentals. Before any surfacing it's true of every row (pure
  // noise), so we show a single quiet header hint instead of stamping "needs SURFACE" on all of them.
  const anyFundamentals = d.draft.basket.some((m) => hasFundamentals(m.security_id, scoredById));
  const hasRealLink = segLabels.some((l) => l !== DISCOVERED);

  // --- the per-NAME display grouping (S1 multi-membership) --------------------------------------------
  // The basket holds N ROWS per name (one per LLM-recommended link — real memberships Save persists);
  // the surface renders ONE row per NAME with a read-only chip per link. Every per-name action (include /
  // sign-off / description-edit) keys on memberKey and co-mutates all of a name's rows, so the group's
  // FIRST row is representative for every per-name field. DISPLAY-only: Save stays basket − excluded,
  // computed over the ROWS (#9, test-guarded) — grouping changes what renders, never what persists.
  type NameGroup = { key: string; first: BasketMember; rows: BasketMember[]; segments: string[] };
  const groupByName = (list: BasketMember[]): NameGroup[] => {
    const order: string[] = [];
    const byKey = new Map<string, BasketMember[]>();
    for (const m of list) {
      const k = memberKey(m);
      if (!byKey.has(k)) {
        byKey.set(k, []);
        order.push(k);
      }
      byKey.get(k)!.push(m);
    }
    return order.map((k) => {
      const rows = byKey.get(k)!;
      return {
        key: k,
        first: rows[0],
        rows,
        segments: [...new Set(rows.map((r) => r.segment).filter((s): s is string => s != null))],
      };
    });
  };
  const nameGroups = groupByName(d.draft.basket);
  const nameCount = nameGroups.length;

  // --- CHERRY-PICK: the Recommended pile (pick-mode), grouped per NAME ---------------------------------
  // The pile groups per NAME for display + pick (the S1 multi-membership shape: a name the draft recommends
  // into N links carries N placements — ONE row, N link chips, one pick). Rows already in the basket are
  // dropped from the view (like To-Review's `verifyCandidates` dedup): the name stays fully visible as a
  // member — only the redundant suggestion hides, never a name from the universe (#9).
  type RecGroup = { sid: string; placements: ResolvedPlacement[] };
  const recommendedPending = recommended.filter((p) => !(p.security_id && keys.has(p.security_id)));
  const recGroups: RecGroup[] = (() => {
    const order: string[] = [];
    const bySid = new Map<string, ResolvedPlacement[]>();
    for (const p of recommendedPending) {
      const sid = p.security_id as string; // the divert filter admits only resolved placed rows
      if (!bySid.has(sid)) {
        bySid.set(sid, []);
        order.push(sid);
      }
      bySid.get(sid)!.push(p);
    }
    return order.map((sid) => ({ sid, placements: bySid.get(sid)! }));
  })();

  // The bulk "✓ sign off all picked" TARGET: the ORIGIN-TRACKED adds of this session (the pile's picks +
  // To-Review's adds — the deliberate one-by-one gestures; a hand-add enters signed off already) that are
  // still un-endorsed AND included. Excluded members are never touched (the ladder: excluded wins — an
  // excluded name can't be endorsed); established and non-picked draft members sit structurally outside
  // the two origin maps. Computed per render so the control renders ONLY when it discriminates (#3):
  // ≥1 picked member the stamp would actually change.
  const pickedUnsignedKeys = nameGroups
    .filter(
      (g) =>
        g.first.security_id &&
        (verifyOrigin[g.first.security_id] || recommendedOrigin[g.first.security_id]) &&
        !g.first.signed_off &&
        d.isIncluded(g.key),
    )
    .map((g) => g.key);
  const includedNameCount = nameGroups.filter((g) => d.isIncluded(g.key)).length;
  // Item 6(c): how many placed NAMES still sit in the "Discovered" holding pen (unsorted into a real link).
  const discoveredCount = nameGroups.filter((g) => g.segments.includes(DISCOVERED)).length;
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
  // The frozen BASKET panel (the established names, top of the editor) — collapsible, open by default.
  const [basketOpen, setBasketOpen] = useState(true);
  // C-B + G — the placed board's DISPLAY partitions (up to three groups of the ONE membership), each
  // independently collapsible. The two Placed groups start OPEN (nothing hidden by default); the acronym-
  // low-quality group starts COLLAPSED (a junk cluster to visit for a scan-and-clear pass, not a wall to
  // scroll past). Grouping only renders when it discriminates — see `groupingActive` below.
  const [cleanOpen, setCleanOpen] = useState(true);
  const [flaggedOpen, setFlaggedOpen] = useState(true);
  const [lowQualityOpen, setLowQualityOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(true); // the master To-Review section (open by default)
  const [recOpen, setRecOpen] = useState(true); // the Recommended pile (pick-mode) — the signal, open
  const [keepersOpen, setKeepersOpen] = useState(true); // the keepers sub-drawer (the signal — open)
  const [couldntOpen, setCouldntOpen] = useState(true); // the couldn't-resolve drawer (open by default)
  const [lowSignalOpen, setLowSignalOpen] = useState(false); // the low-signal noise sub-drawer (2+ terms; collapsed)
  const [lowestSignalOpen, setLowestSignalOpen] = useState(false); // the lowest-signal sub-drawer (≤1 term; collapsed)
  const [noTickerOpen, setNoTickerOpen] = useState(false); // the ticker-less names sub-drawer (collapsed)
  const [spacOpen, setSpacOpen] = useState(false); // the blank-check shells sub-drawer (collapsed — #7)
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
    emptyTerms,
    draftEmpty,
    termSet,
    recs,
    adopted,
    setAside,
    recommended,
    recommendedOrigin,
    pickPref,
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

  // "Export all" (the top-of-editor button): EVERY name this narrative surfaced, grouped by STATUS bucket for a
  // diff-friendly dump — NOT by value-chain link (the link labels are verbose and not what the operator diffs).
  // The whole placed basket (incl. excluded/set-aside — this is NOT the prune) as one "Placed" group, then the
  // To-Review pile. Each group is sorted alphabetically by ticker in exportSegmentedNames; empties are dropped.
  const nameOf = (m: { security_id?: string | null; ticker: string }): string | null =>
    (m.security_id ? names[m.security_id] : undefined) ??
    (m.security_id ? scoredById?.[m.security_id]?.name : undefined) ??
    null;
  const buildExportAllGroups = (): ExportGroup[] => {
    const bucket = (arr: ResolvedPlacement[]): ExportGroup["rows"] =>
      arr.map((p) => toExportedName({ ticker: p.ticker, name: p.name }));
    return [
      // the whole placed basket as ONE group — every NAME once (a multi-membership name's N rows
      // export as one line; the export is a name dump, not a placement dump)
      {
        label: "Placed",
        rows: nameGroups.map((g) => toExportedName({ ticker: g.first.ticker, name: nameOf(g.first) })),
      },
      // the Recommended pile (pick-mode) — surfaced + placed by the draft, awaiting a pick; "EVERY name
      // this narrative surfaced" must include the still-pending recommendations (empty groups are dropped)
      {
        label: "Recommended",
        rows: recGroups.map((g) =>
          toExportedName({ ticker: g.placements[0].ticker, name: g.placements[0].name }),
        ),
      },
      // the To-Review pile — surfaced by the draft but never placed into the basket
      { label: "To Review", rows: bucket(verify) },
      { label: "Ambiguous", rows: bucket(ambiguous) },
      { label: "Couldn't resolve", rows: bucket(absent) },
    ];
  };
  const exportAllCount =
    nameCount + recGroups.length + verify.length + ambiguous.length + absent.length;

  // TRIAGE PR-2 (the find) — sort + filter the placed list so pruning ~90 names is fast. The VIEW only: it
  // reorders/hides rows, it NEVER changes what Save persists (Save is basket − excluded, computed over the whole
  // draft, not this view — the #9 spine, test-guarded). `compact` collapses the prose for a scannable, table-like
  // read without losing the inline editors.
  const [sortBy, setSortBy] = useState<"draft" | "name" | "segment" | "sector">("draft");
  const [fSeg, setFSeg] = useState("");
  const [fFund, setFFund] = useState<"" | "loaded" | "needs">("");
  // the sign-off find-filter (S1 — replaced the authorship filter: authorship is the DESCRIPTION's
  // label now, not a triage state; the ladder rung the operator prunes by is the sign-off flag)
  const [fSign, setFSign] = useState<"" | "signed" | "unsigned">("");
  const [fInc, setFInc] = useState<"" | "included" | "excluded">("");
  const [fCountry, setFCountry] = useState<"" | CountryClass>("");
  const [fExch, setFExch] = useState<"" | ExchangeClass>("");
  const [fSpac, setFSpac] = useState<"" | SpacClass>("");
  // the discovery-term find-filter: narrow to names whose `surfaced_terms` include the pick. The value is a
  // NORMALIZED term key (the dropdown's option value), so "" is the cleared state (mirrors the others).
  const [fTerm, setFTerm] = useState("");
  const [fOffUniv, setFOffUniv] = useState(false);
  const [compact, setCompact] = useState(false);
  const filtersActive =
    sortBy !== "draft" ||
    !!fSeg ||
    !!fFund ||
    !!fSign ||
    !!fInc ||
    !!fCountry ||
    !!fExch ||
    !!fSpac ||
    !!fTerm ||
    fOffUniv;
  const clearFilters = () => {
    setSortBy("draft");
    setFSeg("");
    setFFund("");
    setFSign("");
    setFInc("");
    setFCountry("");
    setFExch("");
    setFSpac("");
    setFTerm("");
    setFOffUniv(false);
  };
  // The term-filter dropdown universe — every discovery term that surfaced ≥1 PLACED name, tier-grouped
  // (SEED / BROAD) off the working term set. Read from the persisted `surfaced_terms` on the whole placed
  // basket (not the ephemeral draft-run matches), so it's the same whenever the thesis is opened. The
  // control renders ONLY when this can discriminate (a fully off-universe basket → no terms → no control, #3).
  const termOptions = termUniverse(d.draft.basket, termSet);
  const termFilterActive = hasTermUniverse(termOptions);
  const sec = (m: BasketMember) => idFor(m.security_id)?.sector ?? "";
  // Country + Exchange + Type filters classify a name's stored IDENTITY (origin / exchange / sector) and
  // span the Basket panel, the working Placed list, AND the To-Review candidates (like INCLUDE) — the
  // fields ride the placement and, for a placed member, the read-time `identity` join. View-only (#9):
  // they narrow what RENDERS, never what Save persists. A name with no loaded identity classifies
  // unknown/other — kept under "all", filtered only by a specific pick, never dropped from the basket.
  // Type = the blank-check bucket (spacClass on the stored SIC description) — the explicit, reversible
  // hide for SPAC shells the operator used to delete one by one.
  const matchesIdentity = (
    origin: string | null | undefined,
    exchange: string | null | undefined,
    sector: string | null | undefined,
  ): boolean => {
    if (fCountry && countryClass(origin) !== fCountry) return false;
    if (fExch && exchangeClass(exchange) !== fExch) return false;
    if (fSpac && spacClass(sector) !== fSpac) return false;
    return true;
  };
  const matchesFilters = (g: NameGroup): boolean => {
    const m = g.first; // per-name fields are uniform across a name's rows (co-mutated)
    const loaded = hasFundamentals(m.security_id, scoredById);
    // segment: a multi-membership name matches when ANY of its links matches; unplaced = no link at all
    if (fSeg && (fSeg === "__unplaced__" ? g.segments.length > 0 : !g.segments.includes(fSeg))) {
      return false;
    }
    if (fFund && (fFund === "loaded" ? !loaded : loaded)) return false;
    if (fSign === "signed" && !m.signed_off) return false;
    if (fSign === "unsigned" && m.signed_off) return false;
    if (fInc === "included" && !d.isIncluded(g.key)) return false;
    if (fInc === "excluded" && d.isIncluded(g.key)) return false;
    // term: a name matches when ANY of its rows' surfaced (discovery) terms include the pick. Union across
    // the group's rows (recall-safe #9 — provenance is per-CIK so the rows are uniform, but a union never
    // drops a match). An off-universe name (empty surfaced_terms) never matches a term pick — correct.
    if (fTerm && !groupHasTerm(g.rows, fTerm)) return false;
    if (fOffUniv && !(m.security_id && offUniverse.has(m.security_id))) return false;
    // the scored-join-baseline read (idFor) — so the filters work on a saved thesis opened with NO
    // draft/session (the #241-blocked scenario): the join alone classifies the placed members
    const idn = idFor(m.security_id);
    if (!matchesIdentity(idn?.origin, idn?.exchange, idn?.sector)) return false;
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
  const sorted = (list: NameGroup[]): NameGroup[] => {
    if (sortBy === "draft") return list;
    const cmp = (a: NameGroup, b: NameGroup): number => {
      if (sortBy === "name") return (a.first.ticker || "").localeCompare(b.first.ticker || "");
      if (sortBy === "segment") {
        return (a.segments[0] || "￿").localeCompare(b.segments[0] || "￿"); // by first link; unplaced last
      }
      return (sec(a.first) || "￿").localeCompare(sec(b.first) || "￿"); // sector; blanks sort last
    };
    return [...list].sort(cmp);
  };
  // --- the Basket / working split (the additive editor) ---
  // ESTABLISHED (in the saved spine at mount — hook-computed, empty after a Clear or on a new thesis) +
  // still-INCLUDED names freeze into the Basket panel up top; everything else — new drafted names AND
  // demoted (unchecked) established names ("sent down") — is the WORKING set the partitions below triage.
  // Disjoint by construction: a NAME renders in exactly one of the two lists. The find bar filters BOTH,
  // each list filtered/sorted independently.
  const basketGroups = nameGroups.filter((g) => d.isEstablished(g.key));
  const basketIncludedGroups = basketGroups.filter((g) => d.isIncluded(g.key));
  const basketRows = sorted(basketIncludedGroups.filter(matchesFilters));
  const workingGroups = nameGroups.filter((g) => !(d.isEstablished(g.key) && d.isIncluded(g.key)));
  const workingKeys = workingGroups.map((g) => g.key);
  // filter → sort → partition → per-group preview-collapse (counts are of the FILTERED set)
  const triaged = sorted(workingGroups.filter(matchesFilters));

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
  const gClean: NameGroup[] = [];
  const gFlagged: NameGroup[] = [];
  const gLowQuality: NameGroup[] = [];
  for (const g of triaged) {
    if (isLowQuality(g.first)) gLowQuality.push(g);
    else if (g.first.security_id && offThesisSet.has(g.first.security_id)) gFlagged.push(g);
    else gClean.push(g);
  }
  // "Placed, flagged" is a noise-review group (off-thesis, but saved) — in the DEFAULT (draft) sort, order it by
  // keyword provenance, the strongest evidence FIRST (mirrors the To-Review Low/Lowest split), so the most-likely-
  // real names surface for a keep pass and the weak single/zero-term hits fall to the bottom for a scan-and-exclude.
  // An explicit dropdown sort (ticker/segment/sector) OVERRIDES this — `triaged` is already in that order,
  // so we leave it. View-only: reads the already-present `matched` counts (free client-side sort), writes nothing.
  if (sortBy === "draft") {
    const mtCount = (g: NameGroup): number =>
      g.first.security_id ? (matched[g.first.security_id]?.length ?? 0) : 0;
    gFlagged.sort(
      (a, b) =>
        mtCount(b) - mtCount(a) || (a.first.ticker || "").localeCompare(b.first.ticker || ""),
    );
  }
  const groupingActive = gFlagged.length > 0 || gLowQuality.length > 0;
  const shownRows = (gkey: string, rows: NameGroup[]) =>
    showAllGroups.has(gkey) ? rows : rows.slice(0, PLACED_PREVIEW);
  const showMoreBtn = (gkey: string, rows: NameGroup[]) =>
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
  // Load a completed draft into the editor (MERGE, not replace). Fail-open: an empty draft (no key / the model
  // declined) loads nothing and shows the quiet "returned nothing" note.
  // `pick` (the run's load mode, frozen at kick-off) routes ONLY the genuinely-NEW placed names: pick-mode
  // diverts them to the Recommended pile (check keepers IN) instead of letting loadDraft append them.
  // THE WIPE-TRAP COUSIN, guaranteed by construction: the ONLY placements removed from what loadDraft sees
  // are placed rows of sids NOT already in the basket — every existing member (established / re-rolled /
  // parked) takes loadDraft's existing path byte-identically, and the segments ride through untouched.
  // "Start empty" is about NEW names only; a saved basket is never wiped or shrunk by a load (#9/WB#2).
  const applyDraft = (data: ChainDraftOut, pick: boolean) => {
    if (pick) {
      const have = new Set(d.draft.basket.map(memberKey));
      const diverted = data.placements.filter(
        (p) => p.status === "placed" && !!p.security_id && !have.has(p.security_id),
      );
      const divertedSids = new Set(diverted.map((p) => p.security_id as string));
      d.loadDraft({
        ...data,
        placements: data.placements.filter(
          (p) => !(p.status === "placed" && p.security_id && divertedSids.has(p.security_id)),
        ),
      });
      // MERGE the pile (a re-draft must not duplicate or reset judgments): a still-pending name the new
      // draft re-places UPDATES (its old entries swap for the latest recommendation set); a pending name
      // the new draft no longer places STAYS visible (#2 — never vanished); a PICKED name is in the basket
      // (`have`), so it is never diverted back into the pile.
      setRecommended((prev) => [
        ...prev.filter((p) => !(p.security_id && divertedSids.has(p.security_id))),
        ...diverted,
      ]);
    } else {
      d.loadDraft(data);
    }
    // the run-state maps below (matched / identity / names / off-*) key by security_id and deliberately
    // cover the WHOLE result — diverted names included, so a later pick reads them like any placed member
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
            { sector: p.sector, exchange: p.exchange, category: p.category, origin: p.origin },
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
    setEmptyTerms(new Set((data.report?.empty_terms ?? []).map(norm)));
  };

  const clearPollTimeout = () => {
    if (pollTimeout.current) window.clearTimeout(pollTimeout.current);
    pollTimeout.current = null;
  };

  // Draft the chain from the narrative — an EXPLICIT operator action (never on render). KICK OFF the job and
  // start polling; arm a poll-timeout so the operator always reaches a terminal state. `scope` is the fast
  // lane (draft-scope PR-2): omitted = the full draft, which posts NO kick-off body (the pre-scope wire shape,
  // preserved); "seeds_only" rides as the body and scopes discovery to the SIGNAL seeds, tail-sweep skipped.
  const onDraft = async (scope?: DraftScope) => {
    // freeze THIS run's load mode at kick-off (the operator saw it in the select before clicking):
    // ⚡ seeds_only is the quick lane (default pick), the full draft auto-loads — an explicit choice wins
    pickRun.current = effectivePick(scope === "seeds_only" ? "quick" : "full");
    setDraftError(null);
    setDraftEmpty(false);
    setDraftStatus(null); // the strip + capped/empty markers describe the LAST run — stale once a new one starts
    setCappedTerms(new Set());
    setEmptyTerms(new Set());
    try {
      const ref = await startDraft.mutateAsync(scope ? { scope } : undefined);
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

  // S3 — the re-scope auto-draft: EXACTLY ONE draft kick-off when the parent mounted this editor with
  // `autoDraft` (the re-scope remount). The sanctioned exception to "never on render": the operator's
  // Re-scope click IS the explicit action — this effect just completes it across the remount. Ref-guarded
  // so StrictMode's double-invoke / any re-render / prop drift can never re-fire it; `drafting` disables
  // the buttons and the server's one-running-draft 409 backstop a double kick-off anyway.
  const autoDraftFired = useRef(false);
  useEffect(() => {
    if (autoDraft && !autoDraftFired.current) {
      autoDraftFired.current = true;
      void onDraft();
    }
    // mount-only by design: the parent sets autoDraft via a fresh remount key; the ref pins once-per-mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The poll's terminal transition: done → load the result; failed → show the operator-facing error; a 404
  // (unknown/expired/restart-wiped job) → a visible "draft was lost". In every case stop polling + disarm the
  // timeout. Keyed on the status/error edge so it fires once per terminal arrival.
  const jobStatus = jobQ.data?.status;
  useEffect(() => {
    if (!jobId) return;
    if (jobStatus === "done") {
      clearPollTimeout();
      if (jobQ.data?.result) applyDraft(jobQ.data.result, pickRun.current);
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
  // drafted) for the operator to accept / edit, like any drafted placement.
  const pickAmbiguous = (p: ResolvedPlacement, c: SecurityCandidate) => {
    d.addMember({
      ticker: c.ticker,
      role: "—",
      security_id: c.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      conviction: null,
      surfaced_terms: p.matched_terms, // capture at entry (empty for off-universe — honest)
      authored_by: "system_drafted",
      signed_off: false, // the pick resolves IDENTITY; the endorsement stays the operator's own act
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
      security_id: p.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      conviction: null,
      surfaced_terms: p.matched_terms, // capture at entry — frozen provenance once the promote persists it
      authored_by: "system_drafted",
      signed_off: false, // add = INCLUDED (the ladder's middle rung); sign-off stays a separate act
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

  // PICK (the pile's check-to-add): the name enters the basket EXACTLY like addVerify — `system_drafted`,
  // NOT signed off (pick = INCLUDED, the ladder's middle rung; endorsement stays a separate act),
  // `surfaced_terms` captured at entry, the draft's recommended segment(s). A multi-link recommendation
  // yields one row per link (dedup by segment — the same freshRows shape loadDraft's additions branch
  // would have appended) via the hook's addMemberRows. The origin stash powers the visible inverse.
  const pickRecommended = (g: RecGroup) => {
    const first = g.placements[0];
    const prose = g.placements.find((p) => p.prose)?.prose ?? null;
    const seen = new Set<string>();
    const rows: BasketMember[] = [];
    for (const p of g.placements) {
      if (seen.has(p.segment ?? "")) continue;
      seen.add(p.segment ?? "");
      rows.push({
        ticker: first.ticker || first.name,
        role: "—",
        security_id: g.sid,
        segment: p.segment,
        thesis_fit: prose,
        conviction: null, // the drafter never weights
        surfaced_terms: first.matched_terms, // capture at entry — frozen once the promote persists it
        authored_by: "system_drafted",
        signed_off: false, // pick = INCLUDED; sign-off stays a separate act (per-row or bulk)
      });
    }
    d.addMemberRows(rows);
    setRecommendedOrigin((prev) => ({ ...prev, [g.sid]: g.placements }));
    setRecommended((prev) => prev.filter((p) => p.security_id !== g.sid));
  };

  // The inverse of pick (reversibility #1): return a picked name to the Recommended pile exactly as it
  // was, and drop its rows from the basket. Offered ONLY on rows whose sid is in `recommendedOrigin`
  // (i.e. names that came from the pile) — the send-back twin of sendBackToVerify.
  const sendBackToRecommended = (sid: string) => {
    const origin = recommendedOrigin[sid];
    if (!origin) return;
    d.removeMember(sid); // removes ALL of the name's membership rows (keyed per name)
    setRecommended((prev) => [...prev, ...origin]);
    setRecommendedOrigin((prev) => {
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

  // Items 4 + 5 — the To-Review triage partition. Precedence off-thesis > ticker-less > blank-check > keeper:
  // the model's off-thesis names are the majority NOISE (quiet, collapsed — never yellow-flagged, that's
  // inverse loudness); the ticker-less names are likely subs/holdcos (quiet, collapsed); a ticker'd
  // blank-check SHELL (deterministic identity, SIC "Blank Checks") splits into its own collapsed drawer so
  // it never takes the Keepers signal slot — a shell is nothing to act on until it announces a deal; what
  // remains are the KEEPERS — the rare signal, surfaced up top (the keepers block). Every group stays PROMOTABLE (#9 — nothing dropped). The
  // keeper vs noise distinction is carried STRUCTURALLY (keepers up top; the noise in labeled drawers), so no
  // per-row "recommend add" badge — it would be true of every visible keeper, which is noise (honest loudness #7).
  // A re-draft can re-surface a name ALREADY in the basket as a VERIFY candidate — an "unselectable keeper"
  // (canAdd=false: it's already placed). Such a row carries no action, so drop it from To-Review entirely
  // (honest loudness #3): the name stays fully visible as a basket member — this removes only the redundant
  // duplicate suggestion, never a name from the universe (#9 — it's kept, not dropped). Derived off the live
  // `keys`, so a name sent back down out of the basket re-appears here.
  const verifyCandidates = verify.filter((p) => !(p.security_id && keys.has(p.security_id)));
  // The term filter narrows To-Review too (like the identity filters): a candidate matches on its
  // `matched_terms` (its current-run discovery provenance — the verify counterpart to a placed member's
  // frozen `surfaced_terms`). A candidate that didn't match the picked term drops from the queue view.
  const verifyVisible = verifyCandidates.filter(
    (p) =>
      matchesVerifyInclude(p) &&
      matchesIdentity(p.origin, p.exchange, p.sector) &&
      termsInclude(p.matched_terms, fTerm),
  );
  // The off-thesis noise, split by keyword provenance so the flood is read at a glance (honest loudness #7):
  //   Low signal    = matched 2+ discovery terms (the stronger keyword evidence — more likely a missed keeper).
  //   Lowest signal = matched ≤1 term. Sorted 0-terms FIRST (off-universe names the model surfaced with NO
  //     keyword provenance at all — its own suggestions, worth the eyeball), then the single incidental hits.
  // Copy-then-sort (never mutate the source array). Within Low signal, more terms first (descending).
  const vOffThesis = verifyVisible.filter((p) => p.off_thesis);
  const vLowSignal = vOffThesis
    .filter((p) => p.matched_terms.length >= 2)
    .slice()
    .sort((a, b) => b.matched_terms.length - a.matched_terms.length);
  const vLowestSignal = vOffThesis
    .filter((p) => p.matched_terms.length <= 1)
    .slice()
    .sort((a, b) => a.matched_terms.length - b.matched_terms.length); // 0-term (off-universe) at the top
  const vNoTicker = verifyVisible.filter((p) => !p.off_thesis && !p.ticker);
  // the blank-check shells — would-be keepers whose stored identity says "shell, no target yet". An
  // off-thesis or ticker-less shell stays in those (already-quiet) drawers; only the keeper slot is protected.
  const vSpac = verifyVisible.filter(
    (p) => !p.off_thesis && p.ticker && spacClass(p.sector) === "spac",
  );
  const vKeepers = verifyVisible.filter(
    (p) => !p.off_thesis && p.ticker && spacClass(p.sector) !== "spac",
  );
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
              <IdentityChips
                sector={p.sector}
                businessType={p.business_type}
                royalty={p.royalty}
                exchange={p.exchange}
                category={p.category}
                origin={p.origin}
              />
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

  // The Recommended-pile row (pick-mode) — ONE row per NAME, carrying the same affordances as a To-Review
  // row (identity chips, off-universe pill, matched-term provenance, off-thesis ⚑, prose) plus the
  // recommended link(s) as chips (N links → N chips — exactly the membership rows a pick creates). Unlike
  // To-Review, EVERY row is addable: these are the draft's PLACED recommendations (seed-hit — higher
  // confidence than To-Review's broad-only), and in auto-load mode each would already be in the basket, so
  // withholding the add on any would be a silent recall cut (#9). The same one-action check-to-add.
  const recommendedRow = (g: RecGroup) => {
    const first = g.placements[0];
    const prose = g.placements.find((p) => p.prose)?.prose ?? null;
    const segs = [...new Set(g.placements.map((p) => p.segment).filter((s) => s))];
    return (
      <div className="nmrow" key={g.sid}>
        <div className="top">
          {/* check-to-add, LEFT of the name — the To-Review gesture: checking promotes the name → it moves
              up to Placed. The reverse is the Placed row's "↩ to recommended" (reversibility #1). */}
          <input
            type="checkbox"
            className="wb-inc"
            checked={false}
            aria-label={`pick ${first.ticker || first.name}`}
            title="check to add — moves it up to Placed (the basket); sign-off stays a separate act"
            onChange={() => pickRecommended(g)}
          />
          <span className="tk">{first.ticker || "—"}</span>
          <span className="co">{first.name}</span>
          <IdentityChips
            sector={first.sector}
            businessType={first.business_type}
            royalty={first.royalty}
            exchange={first.exchange}
            category={first.category}
            origin={first.origin}
          />
          {first.discovery_source === "off_universe" && <OffUniversePill />}
          {/* the draft's recommended link(s) — the same read-only chips a placed row carries */}
          {segs.length > 0 && (
            <span className="ctl wb-reclinks" aria-label={`links for ${first.ticker || first.name}`}>
              {segs.map((s) => (
                <span
                  key={s}
                  className={`recchip${s === DISCOVERED ? " pen" : ""}`}
                  title="the draft's recommended link — picking creates a membership row per chip"
                >
                  {s === DISCOVERED ? "Discovered (unsorted)" : s}
                </span>
              ))}
            </span>
          )}
        </div>
        {prose ? <div className="fit">{prose}</div> : null}
        {first.matched_terms.length > 0 && (
          <div className="prov lead">matched {first.matched_terms.join(", ")}</div>
        )}
        {first.off_thesis && (
          <div className="flag">⚑ model thinks off-thesis — its reason is the note above; pick only if it belongs</div>
        )}
        {first.listing_status === "inactive" && <NotListedFlag />}
      </div>
    );
  };

  // ONE row renderer shared by the frozen Basket panel, the flat list, and the C-B/G display groups (it
  // closes over the editor's run-state — matched/identity/names/offThesisSet — so it stays a local, not a
  // component). Renders a NAME (the group of its N membership rows): per-name fields off the first row,
  // the recommended link(s) as read-only chips.
  const placedRow = (g: NameGroup) => {
    const m = g.first;
    const k = g.key;
    const mt = m.security_id ? matched[m.security_id] : undefined;
    // S2 (re-scope): the FROZEN seed-term provenance — `surfaced_terms`, persisted on the member when it
    // entered the Basket (S1). Distinct from `mt` (the CURRENT draft/re-scope run's matches, display-only
    // run state): a term refinement moves only the `alsoNow` diff; the frozen record can't churn. Empty
    // (a hand-added name / ETF sleeve / pre-backfill) → the row keeps today's single ← line off `mt`.
    const frozen = m.surfaced_terms ?? [];
    const alsoNow = frozen.length > 0 ? (mt ?? []).filter((t) => !frozen.includes(t)) : [];
    // display identity via the scored-join baseline (idFor) — chips render on a saved thesis opened
    // with NO draft/session, off the master join alone (the identity-lifecycle read)
    const idn = idFor(m.security_id);
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
          {/* the LADDER's gate (default-on, #9): unchecking EXCLUDES the name from Save (excluded wins);
              the row stays visible (greyed), one click from re-including. Include never touches
              authorship or the sign-off flag. */}
          <input
            type="checkbox"
            className="wb-inc"
            aria-label={`include ${m.ticker}`}
            checked={included}
            onChange={() => d.toggleInclude(k)}
          />
          <span className="tk">{m.ticker}</span>
          {/* the company name (bridged by security_id — BasketMember carries no name), like To Review */}
          {m.security_id && names[m.security_id] ? (
            <span className="co">{names[m.security_id]}</span>
          ) : null}
          {m.role && m.role !== "—" ? <span className="co role">{m.role}</span> : null}
          {/* R3: an EXCLUDED (set-aside) row collapses to a quiet stub — checkbox + ticker + name + an
              "excluded" tag stay visible (#9, re-check to restore); its chips, controls (incl. the
              sign-off toggle — structurally unreachable while excluded, the ladder's "excluded wins"),
              and prose hide so the noise recedes (inverse loudness). Exclude never touches authorship
              or the flag (an edited note stays operator_edited, safe from the next re-roll). */}
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
              {idn && <IdentityChips {...idn} />}
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
              {/* R1: the recommended-links chips sit on their own line; the row actions (sign-off +
                  send-back) right-align at the END of this row. No seg/conviction controls here (S1):
                  segment sorting + weighting move to the triage screen — this surface shows the DRAFT'S
                  recommendation, read-only. No type control either: the business type derives from the
                  master identity (re-taggable on the scored view's rail). */}
              <span className="ctls">
                {/* the LLM's recommended value-chain link(s) — READ-ONLY chips, one per membership row
                    (multiple links → multiple chips; each is a REAL basket_member row Save persists).
                    Styled LLM-rec (blue), deliberately DISTINCT from the machine-fact IdentityChips
                    (muted). An unplaced name renders no chips (the honest abstain). */}
                {g.segments.length > 0 && (
                  <span className="ctl wb-reclinks" aria-label={`links for ${m.ticker}`}>
                    <span
                      className="lab"
                      title="the draft's recommended link(s) — read-only here (segment sorting lives on the triage screen); every chip is a real membership Save persists"
                    >
                      links
                    </span>
                    {g.segments.map((s) => (
                      <span
                        key={s}
                        className={`recchip${s === DISCOVERED ? " pen" : ""}`}
                        title={
                          s === DISCOVERED
                            ? "the unsorted pen — the draft didn't arrange this name into a link"
                            : "the draft recommends this link — a real membership row; Save persists each chip"
                        }
                      >
                        {s === DISCOVERED ? "Discovered (unsorted)" : s}
                      </span>
                    ))}
                  </span>
                )}
                {/* the row actions right-align at the END of the controls row (sign off ⇄ withdraw · the
                    To-Review send-back). Reversibility (#1): sign-off is a TOGGLE on the confidence
                    ladder's top rung — it endorses the NAME, never sets authorship, never gates Save. */}
                <span className="rowactions">
                  <button
                    type="button"
                    className={`wb-mini${m.signed_off ? " on" : ""}`}
                    aria-pressed={m.signed_off}
                    aria-label={`${m.signed_off ? "withdraw sign-off" : "sign off"} ${m.ticker}`}
                    title={
                      m.signed_off
                        ? "withdraw your sign-off — the name stays included; nothing else changes"
                        : "sign off — endorse this NAME for the thesis (a marker: never authorship, never a gate)"
                    }
                    onClick={() => d.toggleSignOff(k)}
                  >
                    {m.signed_off ? "✓ signed off" : "sign off"}
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
                  {/* the inverse of "pick" for a name checked in from the Recommended pile — send it back
                      exactly as it was (reversibility #1); only pile-origin rows carry it */}
                  {m.security_id && recommendedOrigin[m.security_id] && (
                    <button
                      type="button"
                      className="wb-mini ghost"
                      aria-label={`send ${m.ticker} back to recommended`}
                      title="send this name back to the Recommended pile (the inverse of pick)"
                      onClick={() => sendBackToRecommended(m.security_id as string)}
                    >
                      ↩ to recommended
                    </button>
                  )}
                </span>
              </span>
            </>
          )}
        </div>
        {/* the row's detail (prose · provenance · off-thesis flag) is hidden while EXCLUDED (R3 collapse)
            and while COMPACT (the scannable read). The prose auto-sizes to its content, capped at 3 rows
            then scrolling (R2). HONEST AUTHORSHIP (S1): the label reads "model draft" until the operator
            EDITS the text → "your words" — nothing else flips it (sign-off endorses the NAME, not the
            words). No label on an empty description (nothing written by anyone — the honest abstain). */}
        {included && !compact && (
          <>
            {(m.thesis_fit ?? "").trim() !== "" && (
              <div className="wb-prose-head">
                <span
                  className={`wb-author wb-prose-author${m.authored_by === "operator_edited" ? " yours" : ""}`}
                  title={
                    m.authored_by === "operator_edited"
                      ? "you edited this description — these are your words"
                      : "the model drafted this description — it becomes yours only when you edit it"
                  }
                >
                  {m.authored_by === "operator_edited" ? "your words" : "model draft"}
                </span>
              </div>
            )}
            <AutoTextarea
              className="wb-prose"
              ariaLabel={`thesis-fit for ${m.ticker}`}
              placeholder="why this name sits in its link — your research note (editing makes it yours)…"
              value={m.thesis_fit ?? ""}
              onChange={(v) => d.editProse(k, v)}
            />
          </>
        )}
        {/* S2 (re-scope) — provenance as two DISTINGUISHED lines (keep-visible): ⚓ the frozen seed terms
            (why the name ENTERED the Basket — persisted at entry; a term-set edit / re-draft can never
            change it), then "+ also matches now" ONLY when the current run matched terms beyond the frozen
            set (honest loudness — the diff renders only when it says something; a just-added member's
            frozen == matched, so no duplicate line). A member with NO frozen terms (hand-added / sleeve /
            pre-backfill) keeps today's single ← current-match line — unchanged semantics. */}
        {included && frozen.length > 0 && (
          <div
            className="prov"
            title={`seeded by: ${frozen.join(", ")} — the discovery terms that surfaced this name when it entered the Basket (frozen at entry; term-set edits never change it)`}
          >
            ⚓ seeded by: {frozen.join(" · ")}
          </div>
        )}
        {included && alsoNow.length > 0 && (
          <div
            className="prov"
            title={`also matches the current term set: ${alsoNow.join(", ")} — current-run matches beyond the frozen seed terms`}
          >
            + also matches now: {alsoNow.join(" · ")}
          </div>
        )}
        {included && frozen.length === 0 && mt && mt.length > 0 && (
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
    rows: NameGroup[],
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
          {/* S3 — the resumed-session tell (quiet, on EVERY restored mount): this editor instance SEEDED from
              the autosaved working session, not the saved spine — so what's on screen can differ from what a
              fresh open would show. Closes the "which state am I looking at?" gap (159-vs-160) and flags the
              1-click-rollback risk before a Save. Frozen at mount — an autosave writing the session back
              never flips it on. */}
          {restoredAt && (
            <span
              className="wb-autosave wb-resumed"
              title={`this editor resumed your autosaved working session (saved ${new Date(restoredAt).toLocaleString()}) — it can differ from the saved Basket; Re-scope or Clear starts over from the spine`}
            >
              resumed autosave · {relAge(restoredAt)}
            </span>
          )}
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
          {/* Clear the working chain: empty the value chain + companies (keeping the term-set seeds) and discard
              the saved prune. DISTINCT from "Discard" (which just exits edit mode). Parent confirms + remounts. */}
          {onStartOver && (
            <button
              type="button"
              className="wb-mini ghost wb-startover"
              title="Clear the value chain and companies from the editor (keeps your term-set seeds; discards the saved prune)"
              onClick={onStartOver}
            >
              Clear
            </button>
          )}
        </div>
      </div>
      {save.isError && (
        <ErrorToast>Couldn't save — {errText(save.error)}. Nothing changed.</ErrorToast>
      )}

      {/* THE BASKET (the additive editor) — the ESTABLISHED names, frozen at the top: a re-draft never
          re-rolls these rows; a draft only surfaces NEW names into the working partitions below. Renders
          ONLY when established names exist (a new/cleared thesis behaves exactly as before — no panel).
          Unchecking a row here "sends it down": it leaves the panel and reappears below as an excluded
          stub (reversible — re-check to restore it here). MUST carry .wb-results — the placed-row CSS is
          scoped under it, so without the class the rows regress to the wrong card style. */}
      {d.establishedKeys.size > 0 && (
        <div className="wb-results wb-basket">
          <div className="sect">
            <button
              type="button"
              className="sect-h wb-sect-toggle"
              aria-expanded={basketOpen}
              onClick={() => setBasketOpen((o) => !o)}
            >
              <span className="chev">{basketOpen ? "▾" : "▸"}</span>
              Basket <em>· the saved basket — a re-draft only adds; uncheck to send a name down</em>
              {basketGroups.length > 0 && (
                <span className="ct">
                  · {basketIncludedGroups.length} of {basketGroups.length} kept
                </span>
              )}
            </button>
            {basketOpen &&
              (basketGroups.length > 0 && basketIncludedGroups.length === 0 ? (
                // every established name is demoted — keep the header + an honest note, never vanish (#2)
                <div className="note">
                  all {basketGroups.length} demoted — re-check below to restore
                </div>
              ) : (
                basketRows.map(placedRow)
              ))}
          </div>
        </div>
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
                    {emptyTag(e)}
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
                    {emptyTag(e)}
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
        {/* the arrow (not a bare onClick={onDraft}) keeps the click event out of the scope param */}
        <button type="button" className="wb-edit-btn" onClick={() => onDraft()} disabled={drafting}>
          {drafting ? "Drafting… (can take a few minutes)" : "✦ Draft from narrative"}
        </button>
        {/* The fast lane (draft-scope PR-2): the SAME kick-off + poll flow, scoped to the operator's own
            SIGNAL seeds — BROAD terms aren't enumerated and the Opus tail-sweep is skipped, so it's minutes
            cheaper (the cost thread: a narrower spend the operator explicitly picks). With zero SIGNAL seeds
            there's nothing to enumerate, so the button DISABLES (visible, not vanished) and the title says
            what to do about it. Same one-draft-at-a-time discipline (`drafting`; the server 409s anyway). */}
        <button
          type="button"
          className="wb-edit-btn"
          onClick={() => onDraft("seeds_only")}
          disabled={drafting || signalTerms.length === 0}
          title={
            signalTerms.length === 0
              ? "no SIGNAL seeds — seed terms in the drawer first"
              : "Fast lane: discovery on your SIGNAL seeds only — BROAD terms and the tail-sweep are not run; run a full draft to complete discovery."
          }
        >
          ⚡ Quick draft (seeds only)
        </button>
        {/* CHERRY-PICK — the LOAD-MODE choice for the next draft: how genuinely-NEW placed names land.
            "Start empty — pick keepers" diverts them to the Recommended pile (check keepers IN — the
            To-Review gesture); "auto-load all" appends them to the basket (today's prune-mode). A quiet
            TRI-STATE select (a checkbox can't honestly show "untouched ⇒ per-lane defaults"): untouched
            reads the lane pairing out loud; an explicit choice wins for BOTH lanes. Existing basket
            members are NEVER touched by the mode — it redirects only the append-new branch. */}
        <label className="wb-find-ctl wb-pickmode">
          new names
          <select
            aria-label="draft load mode"
            value={pickPref === null ? "auto" : pickPref ? "pick" : "load"}
            onChange={(e) =>
              setPickPref(e.target.value === "auto" ? null : e.target.value === "pick")
            }
            title="How the NEXT draft loads genuinely-new placed names — existing basket members are never touched. Pick keepers: new names land in a Recommended pile and you check keepers in. Auto-load all: new names append straight to the basket (prune-mode)."
          >
            <option value="auto">lane default (⚡ pick · ✦ load all)</option>
            <option value="pick">start empty — pick keepers</option>
            <option value="load">auto-load all</option>
          </select>
        </label>
        {/* S3 — Re-scope (the maintenance loop): DISTINCT from Draft-from-narrative (which layers onto the
            current pile). Parent-owned: it confirms, clears the transient candidate pile (the autosaved
            prune), keeps the WHOLE saved Basket frozen, and remounts fresh-from-thesis with one auto-draft
            on the CURRENT term set. Disabled while a draft runs (one job at a time — the cost thread). */}
        {onRescope && (
          <button
            type="button"
            className="wb-edit-btn"
            onClick={onRescope}
            disabled={drafting}
            title="Clear the stale candidate pile and re-run discovery on the CURRENT term set — your whole saved Basket stays frozen. Unsaved candidate work and the autosaved prune are discarded (you'll confirm first)."
          >
            ⟳ Re-scope
          </button>
        )}
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
        onLoad={(run) => {
          setDraftError(null);
          setDraftEmpty(false);
          // a saved run re-applies under the mode its LANE would run today (a seeds_only run is the ⚡
          // lane ⇒ pick by default, anything else the ✦ lane) — the explicit toggle overrides either way
          applyDraft(run, effectivePick(run.report?.scope === "seeds_only" ? "quick" : "full"));
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
            it. The <b>Placed</b> rows below show each name's drafted link(s) as read-only chips — per-name
            segment sorting lives on the triage screen.
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

        {/* the "Discovered" holding pen — an unsorted HOLDING PEN, not a value-chain link (Item 6).
            De-linked (muted, dashed), the label is read-only (renaming it would silently turn the pen into
            a link), and there are NO reorder arrows (order is meaningless for a pen). Sorting keepers OUT
            of it moves to the triage screen (this surface shows the draft's placement read-only);
            × dismisses an emptied pen. */}
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
                {discoveredCount} {discoveredCount === 1 ? "name is" : "names are"} still unsorted — the
                draft didn't arrange {discoveredCount === 1 ? "it" : "them"} into a link (sorting moves to
                the triage screen).
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
            Placed names <em>· links are the draft's recommendation (read-only chips) · a description is a model draft until you edit it</em>
            {nameCount > 0 && (
              <span className="ct">
                · {includedNameCount} of {nameCount} included
              </span>
            )}
          </button>
          {placedOpen && (
            <>
          {/* TRIAGE bulk actions (the prune) — include is default-on (#9); these are visible bulk excludes, never
              a silent filter. "Clear un-accepted" excludes still-drafted names (the fast path to just-my-vouched
              names) without touching authorship. */}
          {nameCount > 0 && (
            <div className="wb-triage-bulk">
              <span className="note">Only included names are saved.</span>
              {/* WORKING-SCOPED bulk include/exclude — they sweep the working set (new + demoted names),
                  never the frozen Basket: the established basket is pruned per-row, deliberately. */}
              <button
                type="button"
                className="wb-mini ghost"
                onClick={() => d.includeKeys(workingKeys)}
              >
                include all new
              </button>
              <button
                type="button"
                className="wb-mini ghost"
                onClick={() => d.excludeKeys(workingKeys)}
              >
                exclude all new
              </button>
              <button
                type="button"
                className="wb-mini ghost"
                title="exclude every name you have NOT signed off — keep only your endorsed names (each stays visible, one click back)"
                onClick={d.excludeNotSignedOff}
              >
                clear not signed-off
              </button>
              {/* Feature 2 (cherry-pick) — bulk-endorse the PICKED set: picking is deliberate, so
                  endorsing it wholesale is honest (auto-endorse-on-pick was rejected — the acts stay
                  separate). Renders ONLY when it discriminates (#3): ≥1 picked, included, un-endorsed
                  name. Stamps the FLAG per NAME (multi-membership rows co-mutate, target computed once);
                  reversible per name via each row's sign-off toggle (#1). */}
              {pickedUnsignedKeys.length > 0 && (
                <button
                  type="button"
                  className="wb-mini ghost"
                  title="sign off every name you PICKED this session (from the Recommended pile or To-Review) that isn't yet endorsed — excluded names untouched; reversible per name"
                  onClick={() => d.signOffKeys(pickedUnsignedKeys)}
                >
                  ✓ sign off all picked ({pickedUnsignedKeys.length})
                </button>
              )}
              <button
                type="button"
                className="wb-mini ghost"
                disabled={includedNameCount === 0}
                aria-label={`export ${includedNameCount} included names`}
                onClick={() =>
                  exportKeptNames({
                    thesisName: thesis.name,
                    stage: "triage",
                    asof,
                    // per NAME (a multi-membership name exports once), included only
                    rows: nameGroups
                      .filter((g) => d.isIncluded(g.key))
                      .map((g) =>
                        toExportedName({
                          ticker: g.first.ticker,
                          name:
                            (g.first.security_id ? names[g.first.security_id] : undefined) ??
                            (g.first.security_id
                              ? scoredById?.[g.first.security_id]?.name
                              : undefined),
                        }),
                      ),
                  })
                }
              >
                Export ({includedNameCount})
              </button>
            </div>
          )}
          {/* Item 1: the clean pre-surfacing state — one quiet hint instead of "needs SURFACE" on every row. */}
          {nameCount > 0 && !anyFundamentals && (
            <div className="note">
              Surface your shortlist — hit <b>⇣ get data</b> on a name in the scored view, then ratify the
              candidates in its rail — confirmed fundamentals show here.
            </div>
          )}
          {/* TRIAGE PR-2 (the find) — sort + filter the placed list. VIEW-ONLY: it never changes what Save
              persists (that's basket − excluded, over the whole draft). Clear-filters is always one click away
              so a hidden-but-included name is never lost (#9). */}
          {nameCount > 1 && (
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
                  <option value="segment">segment</option>
                  <option value="sector">sector</option>
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
                sign-off
                <select
                  aria-label="filter by sign-off"
                  value={fSign}
                  onChange={(e) => setFSign(e.target.value as typeof fSign)}
                >
                  <option value="">all</option>
                  <option value="signed">signed off</option>
                  <option value="unsigned">not signed off</option>
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
              <label className="wb-find-ctl">
                country
                <select
                  aria-label="filter by country"
                  value={fCountry}
                  onChange={(e) => setFCountry(e.target.value as typeof fCountry)}
                >
                  <option value="">all</option>
                  <option value="us">US</option>
                  <option value="foreign">foreign</option>
                  <option value="unknown">unknown</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                exchange
                <select
                  aria-label="filter by exchange"
                  value={fExch}
                  onChange={(e) => setFExch(e.target.value as typeof fExch)}
                >
                  <option value="">all</option>
                  <option value="main">mainstream</option>
                  <option value="otc">OTC</option>
                  <option value="other">other</option>
                  <option value="unknown">unknown</option>
                </select>
              </label>
              <label className="wb-find-ctl">
                type
                <select
                  aria-label="filter by type"
                  value={fSpac}
                  onChange={(e) => setFSpac(e.target.value as typeof fSpac)}
                >
                  <option value="">all</option>
                  <option value="spac">blank check</option>
                  <option value="other">other</option>
                  <option value="unknown">unknown</option>
                </select>
              </label>
              {/* TERM — the discovery-term filter: narrow to names a chosen keyword surfaced. Rendered ONLY
                  when the universe can discriminate (≥1 surfaced term, #3). The operator's own SIGNAL seeds
                  sit first under "Seed terms"; keyword-gen BROAD terms follow. Each optgroup renders only
                  when non-empty (no bare "Seed terms" header when every term is broad). */}
              {termFilterActive && (
                <label className="wb-find-ctl">
                  term
                  <select
                    aria-label="filter by discovery term"
                    value={fTerm}
                    onChange={(e) => setFTerm(e.target.value)}
                  >
                    <option value="">all</option>
                    {termOptions.signal.length > 0 && (
                      <optgroup label="Seed terms">
                        {termOptions.signal.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {termOptions.broad.length > 0 && (
                      <optgroup label="Broad terms">
                        {termOptions.broad.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </label>
              )}
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
                {/* whole-basket NAME count across BOTH lists (Basket panel + working) — the denominator
                    stays the full set of names, so a filter reads the same as before the split */}
                showing {basketRows.length + triaged.length} of {nameCount} placed
                {(fInc || fCountry || fExch || fSpac || fTerm) && verifyCandidates.length > 0
                  ? ` · ${verifyVisible.length} of ${verifyCandidates.length} to review`
                  : ""}
              </span>
            </div>
          )}
          {(() => {
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
                      onClick={() => d.excludeKeys(gLowQuality.map((g) => g.key))}
                    >
                      exclude all {gLowQuality.length}
                    </button>
                  </div>,
                )}
              </div>
            );
          })()}
          {nameCount === 0 && (
            <div className="note">No names yet — draft from the narrative, or add one below.</div>
          )}
          {/* an established thesis with nothing NEW yet — the working list is honestly empty, not filtered */}
          {workingGroups.length === 0 && nameCount > 0 && (
            <div className="note">
              no new names — draft from the narrative to surface additions
            </div>
          )}
          {workingGroups.length > 0 && triaged.length === 0 && (
            <div className="note">
              No names match the filter — <button type="button" className="wb-linkbtn" onClick={clearFilters}>clear filters</button> to see all {nameCount}.
            </div>
          )}
            </>
          )}
        </div>

        {/* RECOMMENDED (pick-mode) — the pile of genuinely-NEW placed names a pick-mode draft diverted for
            check-to-add, ABOVE To-Review (these are seed-hit / draft-placed — higher confidence than
            To-Review's broad-only corroboration) and visually distinct (the accent panel). Renders ONLY
            while it holds pending rows (#3 — an empty pile is noise); unpicked rows persist (session
            working state), and none is ever silently dropped (#9). */}
        {recGroups.length > 0 && (
          <div className="sect wb-recommended">
            <button
              type="button"
              className="sect-h wb-sect-toggle"
              aria-expanded={recOpen}
              onClick={() => setRecOpen((o) => !o)}
            >
              <span className="chev">{recOpen ? "▾" : "▸"}</span>
              Recommended{" "}
              <em>· the draft placed these — start-empty mode holds them here; check keepers in</em>
              <span className="ct">· {recGroups.length}</span>
            </button>
            {recOpen && recGroups.map(recommendedRow)}
          </div>
        )}

        {/* TO REVIEW — resolved, lower confidence. ONE master collapsible holding three nested sub-drawers
            (Keepers · Low signal · No listed ticker), mirroring the Placed section (.wb-placed-groups). Inverse
            loudness (#7): Keepers are the surfaced signal (open); the two noise buckets stay quiet + collapsed.
            Nothing dropped (#9) — every bucket stays promotable via the same check-to-add. The master header
            count stays keepers-only (the headline is the signal; each sub-drawer carries its own count). */}
        {verifyCandidates.length > 0 && (
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
                    No clear keepers — the model didn't flag any of these as a strong fit. The Blank checks /
                    Low signal / Lowest signal / No listed ticker drawers below hold the rest.
                  </div>
                )}
                {/* blank-check shells — deterministic identity (SIC "Blank Checks"), split out of Keepers so
                    a ticker'd SPAC never takes the signal slot. Quiet + collapsed (#7 — a shell is maximally
                    early); every row stays promotable via the same check-to-add (#9 — hidden, never dropped). */}
                {vSpac.length > 0 && (
                  <div className="resolve wb-placed-group">
                    <button
                      type="button"
                      className="resolve-h"
                      aria-expanded={spacOpen}
                      aria-label="toggle Blank checks"
                      onClick={() => setSpacOpen((o) => !o)}
                    >
                      <span className="chev">{spacOpen ? "▾" : "▸"}</span>
                      <span className="rt">Blank checks</span>
                      <span className="rm-meta">
                        SPAC shells, no target yet — nothing to act on until a deal · {vSpac.length}{" "}
                        hidden
                      </span>
                    </button>
                    {spacOpen && (
                      <div className="resolve-body">
                        {vSpac.map((p, i) => verifyRow(p, `spac-${i}`))}
                      </div>
                    )}
                  </div>
                )}
                {/* off-thesis noise, split by keyword provenance — quiet, NO yellow (the majority; highlight
                    keepers, not this). Each drawer renders only when non-empty (honest loudness #7: a bucket
                    true of nothing doesn't render). Low signal = 2+ terms; Lowest signal = ≤1 term. */}
                {vLowSignal.length > 0 && (
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
                        model sees no clear thesis fit · matched 2+ terms · {vLowSignal.length} hidden
                      </span>
                    </button>
                    {lowSignalOpen && (
                      <div className="resolve-body">
                        {vLowSignal.map((p, i) => verifyRow(p, `off-${i}`))}
                      </div>
                    )}
                  </div>
                )}
                {/* the weakest — one incidental keyword hit, or an off-universe name the model surfaced with
                    NO keyword provenance (those sort to the top of this drawer). */}
                {vLowestSignal.length > 0 && (
                  <div className="resolve wb-placed-group">
                    <button
                      type="button"
                      className="resolve-h"
                      aria-expanded={lowestSignalOpen}
                      aria-label="toggle Lowest signal"
                      onClick={() => setLowestSignalOpen((o) => !o)}
                    >
                      <span className="chev">{lowestSignalOpen ? "▾" : "▸"}</span>
                      <span className="rt">Lowest signal</span>
                      <span className="rm-meta">
                        weakest — a single incidental keyword hit · {vLowestSignal.length} hidden
                      </span>
                    </button>
                    {lowestSignalOpen && (
                      <div className="resolve-body">
                        {vLowestSignal.map((p, i) => verifyRow(p, `lowest-${i}`))}
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
                          businessType={p.business_type}
                          royalty={p.royalty}
                          exchange={p.exchange}
                          category={p.category}
                          origin={p.origin}
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
      {/* surface an ETF as a `fund` sleeve — directly below add-a-name (ETF Sleeve, Slice 1). Same onAdd
          bridge; unlike AddName it needs no prior master match (it resolves + marks the ticker itself). */}
      <SurfaceEtf
        existingKeys={keys}
        onAdd={(m, name) => {
          d.addMember(m);
          if (m.security_id && name) setNames((prev) => ({ ...prev, [m.security_id as string]: name }));
        }}
      />
    </div>
  );
}
