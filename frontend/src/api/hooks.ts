import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { components } from "./types.gen";

// Wire types — generated from the backend's OpenAPI (never hand-written; run `npm run gen:api`).
export type ThesisSummary = components["schemas"]["ThesisSummary"];
export type ThesisDetail = components["schemas"]["ThesisDetail"];
export type CallCardResponse = components["schemas"]["CallCardResponse"];
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
  | components["schemas"]["RatifyCashBurn"];
// the narrative -> chain draft (S5): segments + each proposed name resolved to placed/ambiguous/absent
export type ChainDraftOut = components["schemas"]["ChainDraftOut"];
export type ResolvedPlacement = components["schemas"]["ResolvedPlacement"];
export type ResolvedSegment = components["schemas"]["ResolvedSegment"];
export type SecurityCandidate = components["schemas"]["SecurityCandidate"];
export type TermSetEntry = components["schemas"]["TermSetEntry"];
export type TermEdit = components["schemas"]["TermEdit"];

export function useTheses() {
  return useQuery({
    queryKey: ["theses"],
    queryFn: async () => {
      const { data, error } = await api.GET("/theses");
      if (error) throw error;
      return data;
    },
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
// Scores are never sent — they re-derive on read.
export function usePromoteThesis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: PromoteThesisRequest) => {
      const { data, error } = await api.POST("/workbench/theses", { body });
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

// Auto-extract candidate scoring facts from a security's latest 10-Q/10-K (hybrid-1). An EXPLICIT operator
// action: `enabled: false` so it NEVER fires on a render — the facts panel triggers it via `refetch()`.
export function useExtract(securityId: string) {
  return useQuery({
    queryKey: ["workbench-extract", securityId] as const,
    enabled: false,
    retry: false, // an explicit one-shot operator action — never auto-retry (same pattern as the draft query)
    queryFn: async () => {
      const { data, error } = await api.GET("/workbench/securities/{security_id}/extract", {
        params: { path: { security_id: securityId } },
      });
      if (error) throw error;
      return data;
    },
  });
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
    },
  });
}
