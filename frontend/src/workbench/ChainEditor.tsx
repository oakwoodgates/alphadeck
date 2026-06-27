import { useEffect, useRef, useState } from "react";

import type {
  BasketMember,
  ChainDraftOut,
  ResolvedPlacement,
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
}

// A term's provenance: an operator seed vs an LLM-proposed (guard-tiered) term. The data already carries it.
const termAuthor = (a: string): string =>
  a === "operator_set" ? "seed" : a === "operator_edited" ? "edited" : "auto";

// A placed name's authorship — a QUIET tell (inverse loudness): who owns this placement. "drafted" is the LLM's
// (still has an accept button); "operator" is yours; "edited" is a draft you tweaked.
const authorLabel = (a: string): string =>
  a === "operator_set" ? "operator" : a === "system_drafted" ? "drafted" : "edited";

/** The authoring surface (Slice 4b + the S5 draft/ratify, 5c): build & edit the value chain by hand — or
 *  DRAFT it from the narrative (the narrative→chain drafter) and ratify per name. A drafted placement loads
 *  as `system_drafted` (badged, prunable); accepting it → `operator_set`, editing any field → `operator_edited`.
 *  A name the drafter couldn't resolve uniquely (AMBIGUOUS) enters the basket ONLY by an explicit operator
 *  pick (ticker + CIK disambiguate); one with no master row (ABSENT) is shown, never placed. A drafted name
 *  is UNSCORED until the operator extract→ratifies it. Nothing persists until SAVE (the full-replace promote,
 *  which honors each member's authorship and stores the thesis-fit prose). */
export function ChainEditor({ thesis, onDone }: Props) {
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

  const segLabels = d.draft.segments.map((s) => s.label);
  const keys = new Set(d.draft.basket.map(memberKey));

  // --- post-draft results buckets (the IA reorg) ---
  const PLACED_PREVIEW = 12; // a large draft (hundreds of names) collapses to a preview + "show more"
  const [showAllPlaced, setShowAllPlaced] = useState(false);
  const [couldntOpen, setCouldntOpen] = useState(true); // the couldn't-resolve drawer (open by default)
  const [pickOpen, setPickOpen] = useState<Set<string>>(new Set()); // which ambiguous rows show the CIK picker
  const placedShown = showAllPlaced ? d.draft.basket : d.draft.basket.slice(0, PLACED_PREVIEW);
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
      authored_by: "system_drafted",
    });
    setVerify((prev) => prev.filter((x) => x !== p));
  };

  const onSave = () =>
    save.mutate(
      {
        id: thesis.id,
        name: thesis.name,
        narrative: thesis.narrative,
        ticker: thesis.ticker ?? null,
        basket: d.draft.basket,
        segments: d.draft.segments,
      },
      { onSuccess: () => onDone() },
    );

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
            {d.draft.basket.length > 0 && <span className="ct">· {d.draft.basket.length}</span>}
          </div>
          {placedShown.map((m) => {
            const k = memberKey(m);
            const drafted = m.authored_by === "system_drafted";
            const mt = m.security_id ? matched[m.security_id] : undefined;
            const offThesis = false; // DORMANT — no off_thesis signal exists yet; never flag a name on invented data
            return (
              <div className={`nmrow${offThesis ? " flagged" : ""}`} key={k}>
                <div className="top">
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
                <textarea
                  className="wb-prose"
                  rows={3}
                  aria-label={`thesis-fit for ${m.ticker}`}
                  placeholder="why this name sits in its link — thesis-fit reasoning (drafted, or yours)…"
                  value={m.thesis_fit ?? ""}
                  onChange={(e) => d.editProse(k, e.target.value)}
                />
                {mt && mt.length > 0 && (
                  <div className="prov" title={`discovery match: ${mt.join(", ")}`}>
                    ← {mt.join(" · ")}
                  </div>
                )}
                {offThesis && (
                  <div className="flag">⚑ model thinks off-thesis — surfaced, never silently dropped</div>
                )}
              </div>
            );
          })}
          {d.draft.basket.length === 0 && (
            <div className="note">No names yet — draft from the narrative, or add one below.</div>
          )}
          {d.draft.basket.length > PLACED_PREVIEW && !showAllPlaced && (
            <div className="showmore">
              <button type="button" className="wb-mini" onClick={() => setShowAllPlaced(true)}>
                show {d.draft.basket.length - PLACED_PREVIEW} more placed
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
                    <span className="ctls">
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
                  {ambiguous.map((p, i) => (
                    <div key={`amb-${i}`}>
                      <div className="rrow">
                        <span className="tk">{p.ticker || "—"}</span>
                        <span className="co">{p.name}</span>
                        <span className="rpill amb">ambiguous</span>
                        <button
                          type="button"
                          className="rbtn"
                          aria-label={`pick CIK for ${p.name}`}
                          onClick={() => togglePick(p.name)}
                        >
                          pick CIK…
                        </button>
                      </div>
                      <div className="rnote">
                        matched several CIKs (e.g. a redomicile) — choose which entity is the real one before
                        it can place
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
                  ))}
                  {absent.map((p, i) => (
                    <div key={`abs-${i}`}>
                      <div className="rrow">
                        <span className="tk">{p.ticker || "—"}</span>
                        <span className="co">{p.name}</span>
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
