import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "./client";
import type { components } from "./types.gen";

// Wire types — generated from the backend's OpenAPI (never hand-written; run `npm run gen:api`).
export type ThesisSummary = components["schemas"]["ThesisSummary"];
export type ThesisDetail = components["schemas"]["ThesisDetail"];
export type CallCardResponse = components["schemas"]["CallCardResponse"];
// the per-member call rows (M5 armed/watch tiers) — the Cockpit's per-name buckets + panel read these
export type MemberCallOut = components["schemas"]["MemberCallOut"];
export type TriggerRefOut = components["schemas"]["TriggerRefOut"];
export type WorkbenchScored = components["schemas"]["WorkbenchScored"];
export type ScoredMemberOut = components["schemas"]["ScoredMemberOut"];
export type ScoredFigureOut = components["schemas"]["ScoredFigureOut"];
export type Segment = components["schemas"]["Segment"];
export type ProvenanceOut = components["schemas"]["ProvenanceOut"];
export type PromoteThesisRequest = components["schemas"]["PromoteThesisRequest"];
export type SecurityMatchOut = components["schemas"]["SecurityMatchOut"];
export type BasketMember = components["schemas"]["BasketMember"];
export type ExtractedFact = components["schemas"]["ExtractedFact"];
export type LocatedPassage = components["schemas"]["LocatedPassage"];
export type FlagExplanationOut = components["schemas"]["FlagExplanationOut"];
// the ratify body is a discriminated union (one variant per fact type)
export type RatifyFactBody =
  | components["schemas"]["RatifyRevenueMix"]
  | components["schemas"]["RatifyShares"]
  | components["schemas"]["RatifyCashBurn"]
  | components["schemas"]["RatifyCatalyst"];
export type CatalystIn = components["schemas"]["CatalystIn"];
export type KillCriterionIn = components["schemas"]["KillCriterionIn"];
export type CatalystOut = components["schemas"]["Catalyst"];
export type KillCriterionOut = components["schemas"]["KillCriterion"];
export type ExclusionIn = components["schemas"]["ExclusionIn"];
export type ExcludedName = components["schemas"]["ExcludedName"];
// the narrative -> chain draft (S5): segments + each proposed name resolved to placed/ambiguous/absent
export type ChainDraftOut = components["schemas"]["ChainDraftOut"];
// the draft run's honesty report (the honest-discovery slice): EFTS coverage + capped terms + the tail-sweep
// tri-state + the narration fill — display-only RUN state the status strip renders, never persisted
export type DraftReportOut = components["schemas"]["DraftReportOut"];
export type ResolvedPlacement = components["schemas"]["ResolvedPlacement"];
export type ResolvedSegment = components["schemas"]["ResolvedSegment"];
export type SecurityCandidate = components["schemas"]["SecurityCandidate"];
export type TermSetEntry = components["schemas"]["TermSetEntry"];
export type TermEdit = components["schemas"]["TermEdit"];
export type TierRecommendation = components["schemas"]["TierRecommendation"];
// the run-loader picker: one saved draft-run's summary (the detail endpoint returns a full ChainDraftOut)
export type SavedRunSummary = components["schemas"]["SavedRunSummary"];
export type TriageSessionPut = components["schemas"]["TriageSessionPut"];
export type TriageSessionEnvelope = components["schemas"]["TriageSessionEnvelope"];
// the per-security price pull's receipt (the finalize screen's decoupled price leg)
export type PriceIngestOut = components["schemas"]["PriceIngestOut"];

export function useTheses(includeArchived = false) {
  return useQuery({
    // the partial key ["theses"] still invalidates BOTH variants (every mutation that touches the
    // list keeps working); archived stay excluded by default — only the Board asks for them
    queryKey: ["theses", includeArchived] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/theses", {
        params: { query: { include_archived: includeArchived } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// Archive (never delete) / restore — the Board's hygiene control. The spine, calls log, and
// decision log all stay; an archived thesis just leaves the default list + the cron's walk.
export function useSetArchived() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ thesisId, archived }: { thesisId: string; archived: boolean }) => {
      const path = archived ? "/theses/{thesis_id}/archive" : "/theses/{thesis_id}/unarchive";
      const { data, error } = await api.POST(path, {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["theses"] }),
  });
}

export function useThesis(thesisId: string) {
  return useQuery({
    queryKey: ["thesis", thesisId],
    enabled: Boolean(thesisId),
    queryFn: async () => {
      const { data, error } = await api.GET("/theses/{thesis_id}", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// One thesis's call, recomputed at `asof` (the read path; never reads the calls log).
function callQuery(thesisId: string, asof: string) {
  return {
    queryKey: ["call", thesisId, asof] as const,
    enabled: Boolean(thesisId) && Boolean(asof),
    queryFn: async () => {
      const { data, error } = await api.GET("/theses/{thesis_id}/call", {
        params: { path: { thesis_id: thesisId }, query: { asof } },
      });
      if (error) throw error;
      return data;
    },
  };
}

export function useCall(thesisId: string, asof: string) {
  return useQuery(callQuery(thesisId, asof));
}

// The Board computes a call per thesis to place each card in its lifecycle column.
export function useCalls(thesisIds: string[], asof: string) {
  return useQueries({ queries: thesisIds.map((id) => callQuery(id, asof)) });
}

// --- the Scoreboard (SCORE): the forward record scored — the calls log + the decision log ---
export type ScoreboardResponse = components["schemas"]["ScoreboardResponse"];
export type ScoreboardThesisOut = components["schemas"]["ScoreboardThesisOut"];
export type ScoreboardEpisodeOut = components["schemas"]["ScoreboardEpisodeOut"];
export type ScoreboardMetricOut = components["schemas"]["ScoreboardMetricOut"];
export type EpisodeOperatorOut = components["schemas"]["EpisodeOperatorOut"];
export type OperatorSpanOut = components["schemas"]["OperatorSpanOut"];

export type ScoreboardReplayResponse = components["schemas"]["ScoreboardReplayResponse"];
export type ScoreboardReplayThesisOut = components["schemas"]["ScoreboardReplayThesisOut"];

// The HISTORICAL (replayed) panel — served from the operator-kicked artifact, so it is
// asof-INDEPENDENT (the artifact is what it is; no asof in the key). available:false = no
// artifact yet; the panel renders nothing at all (absence, not an empty shell).
export function useScoreboardReplay() {
  return useQuery({
    queryKey: ["scoreboard-replay"] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/scoreboard/replay");
      if (error) throw error;
      return data;
    },
  });
}

// ONE aggregate GET (cross-thesis is the Scoreboard's nature — deliberately not a useCalls-style
// fan-out). Read-only on the server; archived theses ride by default (the record is not erased).
export function useScoreboard(asof: string, includeArchived = true) {
  return useQuery({
    queryKey: ["scoreboard", asof, includeArchived] as const,
    enabled: Boolean(asof),
    queryFn: async () => {
      const { data, error } = await api.GET("/scoreboard", {
        params: { query: { asof, include_archived: includeArchived } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// --- decision capture: the operator-decisions log (take / pass / close / void) ---
export type DecisionIn = components["schemas"]["DecisionIn"];
export type DecisionOut = components["schemas"]["DecisionOut"];

// The thesis's decision log, newest first (voided rows ride along flagged — greyed, never hidden).
export function useDecisions(thesisId: string) {
  return useQuery({
    queryKey: ["decisions", thesisId] as const,
    enabled: Boolean(thesisId),
    queryFn: async () => {
      const { data, error } = await api.GET("/theses/{thesis_id}/decisions", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// APPEND one decision (advisory only, #5 — this LOGS a fill/pass made elsewhere; nothing routes).
// A landed decision changes the derived position, so the CALL invalidates too (every asof, via the
// partial key match) and the card visibly flips — take → Managing; close → back to signals-driven.
export function usePostDecision(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: DecisionIn) => {
      const { data, error } = await api.POST("/theses/{thesis_id}/decisions", {
        params: { path: { thesis_id: thesisId } },
        body,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["decisions", thesisId] });
      qc.invalidateQueries({ queryKey: ["call", thesisId] });
    },
  });
}

// The Workbench scored read: the value-chain segments + the four-meter scores per basket member,
// re-derived at `asof` (Option B — nothing persists; the UI always shows current numbers).
export function useWorkbenchScored(thesisId: string, asof: string) {
  return useQuery({
    queryKey: ["workbench-scored", thesisId, asof] as const,
    enabled: Boolean(thesisId) && Boolean(asof),
    queryFn: async () => {
      const { data, error } = await api.GET("/workbench/theses/{thesis_id}/scored", {
        params: { path: { thesis_id: thesisId }, query: { asof } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// The authoring add-a-name typeahead (Slice 4b): a read-only discovery net over the current tenant's
// security master. Disabled until the operator types; the operator picks an exact row to place.
export function useResolveSecurities(query: string) {
  return useQuery({
    queryKey: ["workbench-securities", query] as const,
    enabled: query.trim().length > 0,
    queryFn: async () => {
      const { data, error } = await api.GET("/workbench/securities", {
        params: { query: { q: query, limit: 10 } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// Promote/save a structured thesis (the app's only mutation): create/update via full-replace upsert,
// then invalidate the Board list AND the scored read so the meters re-derive on the new structure.
// Scores are never sent — they re-derive on read. `identity_overrides` is optional at the call sites
// (defaulted here): only the explicit bind-anyway flow ever sends member ids — an override is a
// deliberate, per-promote choice, never ambient state a payload builder could accidentally carry.
export function usePromoteThesis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      body: Omit<PromoteThesisRequest, "identity_overrides"> & { identity_overrides?: string[] },
    ) => {
      const { data, error } = await api.POST("/workbench/theses", {
        body: { ...body, identity_overrides: body.identity_overrides ?? [] },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (thesis) => {
      qc.invalidateQueries({ queryKey: ["theses"] });
      if (thesis?.id) {
        qc.invalidateQueries({ queryKey: ["thesis", thesis.id] });
        qc.invalidateQueries({ queryKey: ["workbench-scored", thesis.id] }); // re-score on edit
      }
    },
  });
}

// Produce + persist the thesis's tiered discovery term set (the WRITER seam that discovery later READS): the
// keyword-gen LLM PROPOSES candidates, a deterministic guard tiers them, and the operator's SEEDS are the only
// SIGNAL. An EXPLICIT operator action (a mutation, fired by the "Produce term set" button — never on render).
// A re-run REGENERATES (preserves operator seeds, re-rolls the LLM broad terms) — not an append. On success
// invalidate the thesis so the stored `term_set` re-reads. This slice is PRODUCE + DISPLAY only; the edit UI
// (re-tier / add / remove a term by hand) is a later slice on the same object.
export function useProduceTerms(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/workbench/theses/{thesis_id}/terms", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thesis", thesisId] });
    },
  });
}

// SAVE the operator's manually-edited term set — NO LLM (the writer seam with the LLM is `useProduceTerms`).
// The caller sends the FULL edited set ({term, tier}); the server re-stamps authorship by diffing the stored
// set (an untouched system_drafted term stays re-rollable). Returns the saved ThesisDetail — the caller adopts
// its RE-STAMPED term_set as the next working set (never an optimistic copy: the next edit must diff against
// the server's authorship, not a guessed one).
export function useEditTerms(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (terms: TermEdit[]) => {
      const { data, error } = await api.PUT("/workbench/theses/{thesis_id}/terms/edit", {
        params: { path: { thesis_id: thesisId } },
        body: { terms },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thesis", thesisId] });
    },
  });
}

// The tier RECOMMENDER (INVARIANT #10): the LLM recommends signal/broad + a one-line reason per term; the
// operator confirms via the EXISTING tier toggle. An explicit operator action (the "Recommend tiers" button) —
// DISPLAY-ONLY: the result is stashed in component state, never persisted, never invalidates the thesis
// (a recommendation changes nothing). retry:false — a Haiku one-shot, re-click on failure.
export function useRecommendTiers(thesisId: string) {
  return useMutation({
    retry: false,
    mutationFn: async () => {
      const { data, error } = await api.POST("/workbench/theses/{thesis_id}/recommend-tiers", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data; // TierRecommendation[]
    },
  });
}

// The extract query's OPTIONS, shared by the hook and the section runner — one key, one fetcher, one
// cache entry, however many observers (the row control, the FactsPanel, the section fan-out).
export function extractQueryOptions(securityId: string, thesisId?: string) {
  return {
    queryKey: ["workbench-extract", securityId, thesisId ?? null] as const,
    retry: false as const, // an explicit one-shot operator action — never auto-retry
    queryFn: async () => {
      // thesis_id is OPTIONAL + purity-only (SURFACE 1b): with it, the revenue_mix candidate carries a
      // GROUNDED purity ESTIMATE; without it (or when the seam declines) purity is today's located-only HUMAN.
      const { data, error } = await api.GET("/workbench/securities/{security_id}/extract", {
        params: {
          path: { security_id: securityId },
          query: thesisId ? { thesis_id: thesisId } : {},
        },
      });
      if (error) throw error;
      return data;
    },
  };
}

// Auto-extract candidate scoring facts from a security's latest 10-Q/10-K (hybrid-1). An EXPLICIT operator
// action: `enabled: false` so it NEVER fires on a render — the facts panel triggers it via `refetch()`.
export function useExtract(securityId: string, thesisId?: string) {
  return useQuery({ ...extractQueryOptions(securityId, thesisId), enabled: false });
}

async function postIngestPrices(securityId: string): Promise<PriceIngestOut> {
  const { data, error } = await api.POST("/workbench/securities/{security_id}/ingest-prices", {
    params: { path: { security_id: securityId } },
  });
  if (error) throw error;
  return data;
}

// Pull EOD bars for ONE security — the DECOUPLED price leg (writes fact_price_eod; incremental +
// cache-first server-side). An explicit per-name action (retry:false); success re-derives the scored
// view so the cap / "needs shares" label updates.
export function useIngestPrices() {
  const qc = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: postIngestPrices,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workbench-scored"] }),
  });
}

export interface SectionDataReport {
  total: number;
  pricesOk: number;
  extractsOk: number;
  failures: { ticker: string; what: string }[];
}

// The SECTION runner — gate 2 at value-chain-section granularity: for every member the caller passes
// (the ACTIVE section — a slice of the saved shortlist, never the draft), pull prices (incremental,
// cache-first server-side) and prefetch the extract into the SAME query the rows + rail observe. It
// EXTRACTS-AND-PROPOSES only: nothing here confirms a fact — candidates stage for the operator's per-fact
// ratify, purity stays HUMAN. An already-fetched extract is not re-spent (client cache checked).
export function useSectionData(thesisId: string) {
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<SectionDataReport | null>(null);

  const run = async (members: { security_id: string; ticker?: string | null }[]) => {
    setRunning(true);
    setReport(null);
    const outcomes = await Promise.all(
      members.map(async (m) => {
        const [px, ex] = await Promise.allSettled([
          postIngestPrices(m.security_id),
          qc.getQueryData(["workbench-extract", m.security_id, thesisId ?? null]) !== undefined
            ? Promise.resolve("cached" as const) // cache-first client-side too — never re-spend a fetch
            : qc.fetchQuery(extractQueryOptions(m.security_id, thesisId)),
        ]);
        return { ticker: m.ticker ?? m.security_id.slice(0, 8), px, ex };
      }),
    );
    const failures: SectionDataReport["failures"] = [];
    for (const o of outcomes) {
      if (o.px.status === "rejected") failures.push({ ticker: o.ticker, what: "price" });
      if (o.ex.status === "rejected") failures.push({ ticker: o.ticker, what: "extract" });
    }
    setReport({
      total: members.length,
      pricesOk: outcomes.filter((o) => o.px.status === "fulfilled").length,
      extractsOk: outcomes.filter((o) => o.ex.status === "fulfilled").length,
      failures,
    });
    setRunning(false);
    // ONE re-derive after the whole section lands (caps compute where shares are already ratified)
    qc.invalidateQueries({ queryKey: ["workbench-scored"] });
  };

  return { run, running, report, reset: () => setReport(null) };
}

// The narrative -> chain drafter (S5, the SECOND LLM seam) — now a KICK-OFF + POLL job (the draft takes minutes;
// held open as one request it 504'd at the proxy while the backend kept billing). `useStartDraft` POSTs and gets
// a job_id back (202); `useDraftJobStatus` polls for the result. Response-only: the job returns a draft and
// persists nothing; the operator ratifies + promotes.

// Kick off the draft job. retry:false — an expensive Opus pass must NEVER auto-retry (the server 409s a parallel
// run); the operator re-clicks on failure.
export function useStartDraft(thesisId: string) {
  return useMutation({
    retry: false,
    mutationFn: async () => {
      const { data, error } = await api.POST("/workbench/theses/{thesis_id}/draft-chain", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data; // DraftJobRef { job_id, status: "running" }
    },
  });
}

// Poll a kicked-off draft job. Enabled only once a job_id exists; polls every 2.5s WHILE the job is "running"
// and STOPS on a terminal status (done|failed). retry:false — a 404 (unknown/expired/restart-wiped job) is a
// terminal, visible failure (never an infinite spinner), not something to retry.
export function useDraftJobStatus(thesisId: string, jobId: string | null) {
  return useQuery({
    queryKey: ["workbench-draft-job", thesisId, jobId] as const,
    enabled: !!jobId,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2500 : false),
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/workbench/theses/{thesis_id}/draft-chain/jobs/{job_id}",
        { params: { path: { thesis_id: thesisId, job_id: jobId as string } } },
      );
      if (error) throw error;
      return data; // DraftJobStatus { job_id, status, result, error }
    },
  });
}

// --- Run loader (the saved-draft-run picker, a dev/test cost-saver) ---
// List a thesis's saved draft runs, newest-first. GATED backend-side by ALPHADECK_RUN_LOADER_ENABLED: when the
// loader is disabled the endpoint 404s -> this query `isError`, and the picker self-hides (the single flag drives
// both). retry:false so a disabled endpoint isn't hammered; enabled only once a thesis id exists.
export function useThesisRuns(thesisId: string) {
  return useQuery({
    queryKey: ["workbench-runs", thesisId] as const,
    enabled: Boolean(thesisId),
    retry: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/workbench/theses/{thesis_id}/runs", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data; // SavedRunSummary[]
    },
  });
}

// Load ONE saved run on demand (the operator picked it): a GET-by-id as a MUTATION so the picker can
// `mutateAsync(runId)` and hand the returned ChainDraftOut straight to the editor's applyDraft — no refetch
// dance, no fire-on-render. retry:false — a stale/unknown run surfaces as a visible failure the operator retries.
export function useLoadThesisRun(thesisId: string) {
  return useMutation({
    retry: false,
    mutationFn: async (runId: string) => {
      const { data, error } = await api.GET("/workbench/theses/{thesis_id}/runs/{run_id}", {
        params: { path: { thesis_id: thesisId, run_id: runId } },
      });
      if (error) throw error;
      return data; // ChainDraftOut — the same shape the draft endpoint returns
    },
  });
}

// --- Triage session (the resumable prune) — GET restore / PUT autosave / DELETE start-over ---
// The editor's whole working state, one opaque blob per thesis. The FE owns the `state` shape (triageSession.ts);
// the backend is a dumb store. See workbench/triage_store.py.

// Restore this thesis's saved prune session. Enabled only in edit mode (the caller passes `enabled`), retry:false
// so a load fault surfaces as `isError` (the editor shows a retry, NEVER seeds fresh — a transient error must not
// look like "no session"). `session` is the envelope, or null for genuinely-absent (→ seed fresh).
export function useTriageSession(thesisId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["triage-session", thesisId] as const,
    enabled: enabled && Boolean(thesisId),
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/workbench/theses/{thesis_id}/triage-session",
        { params: { path: { thesis_id: thesisId } } },
      );
      // A 404 can NEVER mask a real saved session — an existing session is 200 {session:…} and "none yet" is
      // 200 {session:null}. A 404 means route-missing (a backend/frontend deploy skew) or thesis-not-in-tenant
      // (then you couldn't be editing it). So treat 404 as "no session" → mount fresh, NOT a hard block. Only
      // 5xx/network (which CAN hide an existing prune) stay `isError` → the retry gate (fix #1).
      if (response.status === 404) return { session: null };
      if (error) throw error;
      return data; // TriageSessionGet { session: TriageSessionEnvelope | null }
    },
  });
}

// Autosave (debounced by the caller). retry:2 — a transient blip retries quietly; a sustained failure surfaces
// as `isError` (the loud "Not saved" indicator + manual retry).
export function usePutTriageSession(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    retry: 2,
    mutationFn: async (body: TriageSessionPut) => {
      const { data, error } = await api.PUT("/workbench/theses/{thesis_id}/triage-session", {
        params: { path: { thesis_id: thesisId } },
        body,
      });
      if (error) throw error;
      return data; // TriageSessionEnvelope
    },
    // Keep the RESTORE cache in sync with every autosave. Without this, the ["triage-session", id] query holds
    // whatever the FIRST GET returned (often {session:null}, before any prune existed) and re-opening the editor
    // restores THAT stale value — the prune appears gone even though the server has it. Writing the fresh envelope
    // into the cache makes a re-open (SPA nav or edit-toggle, no full reload) restore the latest saved state.
    onSuccess: (envelope) =>
      qc.setQueryData(["triage-session", thesisId], { session: envelope }),
  });
}

// Discard the saved session (the operator's explicit "start over"). Invalidate so a subsequent restore reads
// the now-absent session.
export function useDeleteTriageSession(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: async () => {
      const { error } = await api.DELETE("/workbench/theses/{thesis_id}/triage-session", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["triage-session", thesisId] }),
  });
}

// The FLAG-explanation drafter (M4b — the ONE LLM seam): a plain-English read of a FLAG candidate, grounded
// in its located passage, shown ALONGSIDE the raw text. Same explicit pattern as useExtract (`enabled: false`
// — fired by the "Explain" button via refetch(), never on a render; cached in-session per candidate). The
// explanation is a DISPLAY aid only: it carries no value and never rides the ratify body. Fail-open — the
// endpoint never 5xxs, returning {grounded:false} when the LLM is unavailable, so the panel works as today.
export function useExplainFlag(candidate: ExtractedFact) {
  return useQuery({
    queryKey: ["flag-explain", candidate.source_ref, candidate.fact_type] as const,
    enabled: false,
    queryFn: async () => {
      const { data, error } = await api.POST("/workbench/facts/explain", { body: candidate });
      if (error) throw error;
      return data;
    },
  });
}

// Ratify a confirmed scoring fact (hybrid-2a) — the operator's FINAL values written via ingest_*. On success
// invalidate the scored read so the meter re-derives (Option B; nothing persists in the UI).
export function useRatifyFact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: RatifyFactBody) => {
      const { data, error } = await api.POST("/workbench/facts", { body });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workbench-scored"] }); // the meter re-derives
      // a ratified fact can move the CALL too (a catalyst conviction turns Key-1; a shares fact
      // completes a cap) — refresh every observed call read (partial key: all theses, all asofs)
      qc.invalidateQueries({ queryKey: ["call"] });
    },
  });
}

// --- spine-list authoring: the catalyst SURFACE + kill criteria (sole-writer endpoints) ---

export function usePutCatalysts(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: CatalystIn[]) => {
      const { data, error } = await api.PUT("/theses/{thesis_id}/catalysts", {
        params: { path: { thesis_id: thesisId } },
        body,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thesis", thesisId] }); // the calendar re-reads
      qc.invalidateQueries({ queryKey: ["call", thesisId] }); // the catalyst surface rides the card
    },
  });
}

// #7: the durable exclusion set — Save persists the editor's pruning (session decisions ∪ the
// carried-forward prior NOs) so a re-draft never re-surfaces a rejected name as fresh work.
export function usePutExclusions(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ExclusionIn[]) => {
      const { data, error } = await api.PUT("/theses/{thesis_id}/exclusions", {
        params: { path: { thesis_id: thesisId } },
        body,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["thesis", thesisId] }),
  });
}

export function usePutKillCriteria(thesisId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: KillCriterionIn[]) => {
      const { data, error } = await api.PUT("/theses/{thesis_id}/kill-criteria", {
        params: { path: { thesis_id: thesisId } },
        body,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thesis", thesisId] });
      qc.invalidateQueries({ queryKey: ["call", thesisId] }); // the counter-case re-derives
    },
  });
}
