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

// Auto-extract candidate scoring facts from a security's latest 10-Q/10-K (hybrid-1). An EXPLICIT operator
// action: `enabled: false` so it NEVER fires on a render — the facts panel triggers it via `refetch()`.
export function useExtract(securityId: string) {
  return useQuery({
    queryKey: ["workbench-extract", securityId] as const,
    enabled: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/workbench/securities/{security_id}/extract", {
        params: { path: { security_id: securityId } },
      });
      if (error) throw error;
      return data;
    },
  });
}

// The narrative -> chain drafter (S5, the SECOND LLM seam): draft a value chain from the thesis's narrative.
// Same explicit pattern as useExtract (`enabled: false` — fired by the "Draft from narrative" button via
// refetch(), NEVER on a render). Response-only: it returns a draft (segments + placed/ambiguous/absent names)
// and persists nothing; the operator ratifies + promotes. Fail-open — no key returns an empty draft, never a
// 5xx, so hand-authoring is untouched.
export function useDraftChain(thesisId: string) {
  return useQuery({
    queryKey: ["workbench-draft-chain", thesisId] as const,
    enabled: false,
    queryFn: async () => {
      const { data, error } = await api.POST("/workbench/theses/{thesis_id}/draft-chain", {
        params: { path: { thesis_id: thesisId } },
      });
      if (error) throw error;
      return data;
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
