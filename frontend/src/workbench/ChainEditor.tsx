import { useEffect, useRef, useState } from "react";

import type {
  BasketMember,
  ChainDraftOut,
  ResolvedPlacement,
  ScoredMemberOut,
  SecurityCandidate,
  TermEdit,
  TermSetEntry,
  ThesisDetail,
} from "../api/hooks";
import {
  useDraftJobStatus,
  useEditTerms,
  useProduceTerms,
  usePromoteThesis,
  useRecommendTiers,
  useStartDraft,
} from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { AddName } from "./AddName";
import { ARCHETYPES, archLabel, errText } from "./format";
import { memberKey, useChainDraft } from "./useChainDraft";

// Stop polling a draft after this long and show "timed out, try again". A real draft floor is the ~300s Opus
// tail-sweep + EDGAR discovery over the universe + decompose + narrate, so this is generous; it sits BELOW the
// 900s server-side running-job reaper, so the operator sees the timeout before the job is reaped (the backend
// job is left to the reaper — the FE only stops polling, never orphans it).
const DRAFT_POLL_TIMEOUT_MS = 600_000;

interface Props {
  thesis: ThesisDetail;
  onDone: () => void; // exit edit mode (the parent unmounts this, re-snapshotting on the next edit)
  // TRIAGE: the parent's scored members, keyed by security_id — a cheap read-time join (no fetch) that drives
  // the per-row "fundamentals loaded vs not" badge. Reflects the LAST SAVED state, so a freshly-drafted (unsaved)
  // name reads "needs SURFACE" — exactly the shortlist signal. Optional (an un-scored / test render omits it).
  scoredById?: Record<string, ScoredMemberOut>;
}

// "Fundamentals loaded" = the name carries a confirmed SURFACE-extractable scoring fact (purity / runway /
// market-cap). Catalysts + dilution come from the feeds/converts, not a SURFACE extract, so they don't count —
// this badge answers "does this survivor still need an extract → ratify?", nothing more.
const hasFundamentals = (
  sid: string | null | undefined,
  scoredById?: Record<string, ScoredMemberOut>,
): boolean => {
  if (!sid) return false;
  const sm = scoredById?.[sid];
  if (!sm) return false;
  return sm.purity?.pips != null || sm.runway?.pips != null || sm.market_cap?.value != null;
};

// A term's provenance: an operator seed vs an LLM-proposed (guard-tiered) term. The data already carries it.
const termAuthor = (a: string): string =>
  a === "operator_set" ? "seed" : a === "operator_edited" ? "edited" : "auto";

// A placed name's authorship — a QUIET tell (inverse loudness): who owns this placement. "drafted" is the LLM's
// (still has an accept button); "operator" is yours; "edited" is a draft you tweaked.
const authorLabel = (a: string): string =>
  a === "operator_set" ? "operator" : a === "system_drafted" ? "drafted" : "edited";

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
}: {
  sector?: string | null;
  exchange?: string | null;
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
export function ChainEditor({ thesis, onDone, scoredById }: Props) {
  const d = useChainDraft(thesis);
  const save = usePromoteThesis();
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
  const [termSet, setTermSet] = useState<TermSetEntry[]>(thesis.term_set);
  const signalTerms = termSet.filter((e) => e.tier === "signal");
  const broadTerms = termSet.filter((e) => e.tier === "broad");
  const [termsOpen, setTermsOpen] = useState(true); // the term-set drawer — open by default
  const [newSeed, setNewSeed] = useState("");
  const [newSeg, setNewSeg] = useState("");

  // The tier RECOMMENDER (INVARIANT #10): the LLM recommends signal/broad + a reason per term; the operator
  // confirms via the EXISTING toggle. Display-only — `recs` is stashed (like `matched`), never persisted.
  const recommendTiers = useRecommendTiers(thesis.id);
  const [recs, setRecs] = useState<Record<string, { tier: string; reason: string }>>({});
  // OFFENSE adoptions (a BROAD term the model recommended SIGNAL, then confirmed): keep a "✦ adopted" trace in
  // v1 so the model's best contribution doesn't dissolve into an indistinguishable agreement while we judge it.
  const [adopted, setAdopted] = useState<Set<string>>(new Set());
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
  const [ambiguous, setAmbiguous] = useState<ResolvedPlacement[]>([]);
  const [verify, setVerify] = useState<ResolvedPlacement[]>([]);
  const [absent, setAbsent] = useState<ResolvedPlacement[]>([]);
  const [draftEmpty, setDraftEmpty] = useState(false);
  // Display-only provenance: security_id -> the discovery term(s) that surfaced it. Set on a draft, NOT a
  // field on BasketMember (it's draft-time discovery provenance, not a thesis fact — never promoted).
  const [matched, setMatched] = useState<Record<string, string[]>>({});
  // Display-only provenance: the security_ids of PLACED names whose discovery_source is "off_universe" (resolved
  // outside the EDGAR-discovered universe, via the sweep-augmented context). The PLACED bucket renders
  // BasketMembers, not placements, so it bridges by security_id — same shape as `matched`. NEVER promoted.
  const [offUniverse, setOffUniverse] = useState<Set<string>>(new Set());
  // Display-only OPINION: the security_ids of PLACED names the NARRATOR judged off-thesis (a boilerplate
  // term-collision). Same bridge-by-security_id shape as `offUniverse`. A RECOMMENDATION only (#10) — the name
  // STAYS placed (#9); the reason is its prose, shown in the thesis-fit note below. NEVER promoted.
  const [offThesisSet, setOffThesisSet] = useState<Set<string>>(new Set());
  // Display-only IDENTITY (Slice 2 enrichment): security_id -> sector / exchange (machine-parsed from EDGAR
  // submissions onto the master). Same bridge-by-security_id shape as `matched` for the PLACED bucket (which
  // renders BasketMembers); the other buckets read it off the placement directly. NEVER promoted.
  const [identity, setIdentity] = useState<
    Record<string, { sector?: string | null; exchange?: string | null }>
  >({});

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));

  // --- post-draft results buckets (the IA reorg) ---
  const PLACED_PREVIEW = 12; // a large draft (hundreds of names) collapses to a preview + "show more"
  const [showAllPlaced, setShowAllPlaced] = useState(false);
  const [couldntOpen, setCouldntOpen] = useState(true); // the couldn't-resolve drawer (open by default)
  const [pickOpen, setPickOpen] = useState<Set<string>>(new Set()); // which ambiguous rows show the CIK picker

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
  const archsPresent = Array.from(new Set(d.draft.basket.map((m) => m.archetype)));
  const matchesFilters = (m: BasketMember): boolean => {
    const k = memberKey(m);
    const loaded = hasFundamentals(m.security_id, scoredById);
    if (fArch && m.archetype !== fArch) return false;
    if (fSeg && (fSeg === "__unplaced__" ? !!m.segment : m.segment !== fSeg)) return false;
    if (fFund && (fFund === "loaded" ? !loaded : loaded)) return false;
    if (fAuth === "accepted" && m.authored_by === "system_drafted") return false;
    if (fAuth === "drafted" && m.authored_by !== "system_drafted") return false;
    if (fInc === "included" && !d.isIncluded(k)) return false;
    if (fInc === "excluded" && d.isIncluded(k)) return false;
    if (fOffUniv && !(m.security_id && offUniverse.has(m.security_id))) return false;
    return true;
  };
  const sorted = (list: BasketMember[]): BasketMember[] => {
    if (sortBy === "draft") return list;
    const cmp = (a: BasketMember, b: BasketMember): number => {
      if (sortBy === "name") return (a.ticker || "").localeCompare(b.ticker || "");
      if (sortBy === "archetype") return a.archetype.localeCompare(b.archetype);
      if (sortBy === "segment") return (a.segment || "￿").localeCompare(b.segment || "￿");
      return (sec(a) || "￿").localeCompare(sec(b) || "￿"); // sector; blanks sort last
    };
    return [...list].sort(cmp);
  };
  // filter → sort → preview-collapse (the collapse counts the FILTERED set, not the whole basket)
  const triaged = sorted(d.draft.basket.filter(matchesFilters));
  const placedShown = showAllPlaced ? triaged : triaged.slice(0, PLACED_PREVIEW);
  const skipVerify = (p: ResolvedPlacement) => setVerify((prev) => prev.filter((x) => x !== p));
  const togglePick = (name: string) =>
    setPickOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  // The archetype palette (matches .arch.* / the lifecycle tokens) applied to the ticker + the arch select.
  const ARCH_COLOR: Record<string, string> = {
    leader: "var(--leader)",
    high_beta: "var(--armed)",
    lotto: "var(--warm)",
    shovel: "var(--manage)",
  };
  const archSelClass = (a: string): string =>
    a === "leader" ? "lead" : a === "shovel" ? "mng" : a === "high_beta" ? "hb" : a === "lotto" ? "lot" : "";

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
          .map((p) => [p.security_id as string, { sector: p.sector, exchange: p.exchange }]),
      ),
    );
    setDraftEmpty(data.placements.length === 0 && data.segments.length === 0);
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
  // drafted) for the operator to accept / edit, like any drafted placement.
  const pickAmbiguous = (p: ResolvedPlacement, c: SecurityCandidate) => {
    d.addMember({
      ticker: c.ticker,
      role: "—",
      archetype: "high_beta",
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
      archetype: "high_beta",
      security_id: p.security_id,
      segment: p.segment,
      thesis_fit: p.prose || null,
      conviction: null,
      authored_by: "system_drafted",
    });
    setVerify((prev) => prev.filter((x) => x !== p));
  };

  // Save persists ONLY the INCLUDED subset (the prune) — the promote full-replaces, so excluded names simply
  // aren't sent. The current sort/filter VIEW never affects this: it's the whole basket minus `excluded`,
  // regardless of what's visible (#9 — the view hides, only include decides what persists).
  const onSave = () => {
    const basket = d.includedBasket;
    if (basket.length === 0 && d.draft.basket.length > 0) {
      const ok = window.confirm(
        "Save an empty basket? Every name is excluded — the thesis will have no basket to score. Include at least one, or confirm the wipe.",
      );
      if (!ok) return;
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
      { onSuccess: () => onDone() },
    );
  };

  return (
    <div className="wb-editor">
      <div className="wb-editor-head">
        <div className="sect-h">
          Build the value chain <em>— decompose the basket into links</em>
        </div>
        <div className="wb-editor-actions">
          {d.dirty && <span className="wb-dirty">unsaved</span>}
          <button type="button" className="promote" disabled={save.isPending} onClick={onSave}>
            {save.isPending ? "Saving…" : "Save chain"}
          </button>
          <button type="button" className="wb-mini ghost" onClick={onDone}>
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
      {draftError && <ErrorToast>Couldn't draft — {draftError}.</ErrorToast>}
      {draftEmpty && (
        <div className="note">
          The drafter returned nothing — no <code>ANTHROPIC_API_KEY</code> in the stack, or the model
          declined. Hand-authoring below is unaffected.
        </div>
      )}

      <div className="wb-seg-edit">
        {d.draft.segments.map((s, i) => (
          <div className="wb-seg-chip" key={i}>
            <input
              className="wb-input"
              value={s.label}
              aria-label={`link ${i + 1} label`}
              onChange={(e) => d.renameSegment(s.label, e.target.value)}
            />
            <button
              type="button"
              className="wb-mini"
              disabled={i === 0}
              aria-label={`move ${s.label} earlier`}
              onClick={() => d.moveSegment(s.label, -1)}
            >
              ←
            </button>
            <button
              type="button"
              className="wb-mini"
              disabled={i === d.draft.segments.length - 1}
              aria-label={`move ${s.label} later`}
              onClick={() => d.moveSegment(s.label, 1)}
            >
              →
            </button>
            <button
              type="button"
              className="wb-mini ghost"
              aria-label={`remove ${s.label}`}
              onClick={() => d.removeSegment(s.label)}
            >
              ×
            </button>
          </div>
        ))}
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
        {/* PLACED — flat list; arch (wired) + seg (UI-only; only "— remove —" acts). The off-thesis FLAG slot
            is built (the .flagged tint + .flag line + promoted remove) but DORMANT — there's no off_thesis
            signal in the data yet, so it never renders (kept honest; a later backend piece drives it). */}
        <div className="sect">
          <div className="sect-h">
            Placed <em>· archetype derived · segment drafted · both overridable</em>
            {d.draft.basket.length > 0 && (
              <span className="ct">
                · {d.includedBasket.length} of {d.draft.basket.length} included
              </span>
            )}
          </div>
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
                showing {triaged.length} of {d.draft.basket.length}
              </span>
            </div>
          )}
          {placedShown.map((m) => {
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
                  {/* the archetype color (incl. red high-beta) only shows once the name is operator-owned or
                      enrichment-derived; an UNCONFIRMED draft default renders neutral, not a wall of red. */}
                  <span
                    className="tk"
                    style={drafted ? undefined : { color: ARCH_COLOR[m.archetype] }}
                  >
                    {m.ticker}
                  </span>
                  {m.role && m.role !== "—" ? <span className="co">{m.role}</span> : null}
                  <span className={`wb-pauthor ${m.authored_by}`} title="who owns this placement">
                    {authorLabel(m.authored_by)}
                  </span>
                  {m.security_id && offUniverse.has(m.security_id) && <OffUniversePill />}
                  {m.security_id && identity[m.security_id] && (
                    <IdentityChips {...identity[m.security_id]} />
                  )}
                  {/* TRIAGE: fundamentals loaded vs not — which survivors still need a SURFACE extract → ratify.
                      "loaded" = a confirmed purity/runway/market-cap fact exists (a cheap read-time join). */}
                  {loaded ? (
                    <span className="fund-badge on" title="confirmed fundamentals on file (purity / runway / market cap)">
                      ✓ fundamentals
                    </span>
                  ) : (
                    <span className="fund-badge" title="no confirmed fundamentals yet — extract → ratify in the facts panel">
                      needs SURFACE
                    </span>
                  )}
                  <span className="ctls">
                    {offThesis && (
                      <button type="button" className="rm" onClick={() => d.removeMember(k)}>
                        remove
                      </button>
                    )}
                    <span className="ctl">
                      <span className="lab">arch</span>
                      <select
                        className={drafted ? "" : archSelClass(m.archetype)}
                        value={m.archetype}
                        aria-label={`archetype for ${m.ticker}`}
                        onChange={(e) =>
                          d.editArchetype(k, e.target.value as BasketMember["archetype"])
                        }
                      >
                        {ARCHETYPES.map((a) => (
                          <option key={a} value={a}>
                            {archLabel(a)}
                          </option>
                        ))}
                      </select>
                    </span>
                    <span className="ctl">
                      <span className="lab">seg</span>
                      {/* UI-only: segment-label options are inert (the real recommendation pre-fill lands when
                          the chain-draft emits segments); only "— remove —" is wired (the prune path). */}
                      <select
                        value={m.segment ?? ""}
                        aria-label={`segment for ${m.ticker}`}
                        onChange={(e) => {
                          if (e.target.value === "__remove__") d.removeMember(k);
                        }}
                      >
                        {!m.segment && <option value="">— segment —</option>}
                        {segLabels.map((l) => (
                          <option key={l} value={l}>
                            {l}
                          </option>
                        ))}
                        <option value="__remove__">— remove —</option>
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
                    {drafted && (
                      <button
                        type="button"
                        className="wb-mini"
                        aria-label={`accept ${m.ticker}`}
                        onClick={() => d.acceptMember(k)}
                      >
                        ✓ accept
                      </button>
                    )}
                  </span>
                </div>
                {/* compact mode collapses the prose editor for a scannable, table-like read (the inline editor
                    returns the moment you toggle compact off — nothing is lost). */}
                {!compact && (
                  <textarea
                    className="wb-prose"
                    rows={3}
                    aria-label={`thesis-fit for ${m.ticker}`}
                    placeholder="why this name sits in its link — thesis-fit reasoning (drafted, or yours)…"
                    value={m.thesis_fit ?? ""}
                    onChange={(e) => d.editProse(k, e.target.value)}
                  />
                )}
                {mt && mt.length > 0 && (
                  <div className="prov" title={`discovery match: ${mt.join(", ")}`}>
                    ← {mt.join(" · ")}
                  </div>
                )}
                {offThesis && (
                  <div className="flag">
                    ⚑ model thinks off-thesis — see the fit note; stays placed, remove if you disagree
                  </div>
                )}
              </div>
            );
          })}
          {d.draft.basket.length === 0 && (
            <div className="note">No names yet — draft from the narrative, or add one below.</div>
          )}
          {d.draft.basket.length > 0 && triaged.length === 0 && (
            <div className="note">
              No names match the filter — <button type="button" className="wb-linkbtn" onClick={clearFilters}>clear filters</button> to see all {d.draft.basket.length}.
            </div>
          )}
          {triaged.length > PLACED_PREVIEW && !showAllPlaced && (
            <div className="showmore">
              <button type="button" className="wb-mini" onClick={() => setShowAllPlaced(true)}>
                show {triaged.length - PLACED_PREVIEW} more
              </button>
            </div>
          )}
        </div>

        {/* TO REVIEW — resolved, lower confidence: VERIFY + tail-sweep, one bucket, one action (add / skip).
            The rec pill renders against the real status today (verify -> "recommend add"); the sweep / low
            pill classes are built but unused until the backend carries that confidence. */}
        {verify.length > 0 && (
          <div className="sect">
            <div className="sect-h">
              To review <em>· in your universe, lower confidence — confirm or dismiss</em>
              <span className="ct">· {verify.length}</span>
            </div>
            {verify.map((p, i) => {
              const inBasket = p.security_id ? keys.has(p.security_id) : false;
              return (
                <div className="nmrow" key={i}>
                  <div className="top">
                    <span className="tk">{p.ticker || "—"}</span>
                    <span className="co">{p.name}</span>
                    <IdentityChips sector={p.sector} exchange={p.exchange} />
                    <span className="ctls">
                      {p.discovery_source === "off_universe" && <OffUniversePill />}
                      <span className="pill add">recommend add</span>
                      <button
                        type="button"
                        className="act addbtn"
                        disabled={inBasket || !p.security_id}
                        aria-label={`add ${p.ticker || p.name}`}
                        onClick={() => addVerify(p)}
                      >
                        {inBasket ? "added" : "add"}
                      </button>
                      <button
                        type="button"
                        className="act skip"
                        aria-label={`skip ${p.ticker || p.name}`}
                        onClick={() => skipVerify(p)}
                      >
                        skip
                      </button>
                    </span>
                  </div>
                  {p.prose ? <div className="fit">{p.prose}</div> : null}
                  {(p.segment || p.matched_terms.length > 0) && (
                    <div className="prov lead">
                      {p.segment ? `recommend → ${p.segment}` : null}
                      {p.segment && p.matched_terms.length > 0 ? " · " : null}
                      {p.matched_terms.length > 0 ? `matched ${p.matched_terms.join(", ")}` : null}
                    </div>
                  )}
                  {p.listing_status === "inactive" && <NotListedFlag />}
                </div>
              );
            })}
          </div>
        )}

        {/* COULDN'T RESOLVE — identity-resolution failures, ORTHOGONAL to thesis-fit. A quiet drawer; never
            confused with to-review (which is all resolved names). Ambiguous gets a CIK picker; absent is
            display-only. */}
        {(ambiguous.length > 0 || absent.length > 0) && (
          <div className="sect" style={{ marginBottom: 0 }}>
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
                        <IdentityChips sector={p.sector} exchange={p.exchange} />
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

      <AddName existingKeys={keys} onAdd={d.addMember} />
    </div>
  );
}
