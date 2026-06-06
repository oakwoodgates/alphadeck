import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { components } from "./types.gen";

// Wire types — generated from the backend's OpenAPI (never hand-written; run `npm run gen:api`).
export type ThesisSummary = components["schemas"]["ThesisSummary"];
export type ThesisDetail = components["schemas"]["ThesisDetail"];
export type CallCardResponse = components["schemas"]["CallCardResponse"];

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

// The Cockpit's live call, recomputed at `asof` (the read path; never reads the calls log).
export function useCall(thesisId: string, asof: string) {
  return useQuery({
    queryKey: ["call", thesisId, asof],
    enabled: Boolean(thesisId) && Boolean(asof),
    queryFn: async () => {
      const { data, error } = await api.GET("/theses/{thesis_id}/call", {
        params: { path: { thesis_id: thesisId }, query: { asof } },
      });
      if (error) throw error;
      return data;
    },
  });
}
