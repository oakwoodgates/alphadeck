import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Narrative Adoption's two reads (docs/dashboards/narrative-adoption.md). Its own hooks, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type AdoptionOut = components["schemas"]["AdoptionOut"];
export type AdoptionCardsOut = components["schemas"]["AdoptionCardsOut"];
export type AdoptionCardOut = components["schemas"]["AdoptionCardOut"];
export type AdoptionMonthOut = components["schemas"]["AdoptionMonthOut"];
export type AdoptionTermShareOut = components["schemas"]["AdoptionTermShareOut"];
export type AdoptionTermOut = components["schemas"]["AdoptionTermOut"];
export type AdoptionOverlayOut = components["schemas"]["AdoptionOverlayOut"];
export type AdoptionFlaggedOut = components["schemas"]["AdoptionFlaggedOut"];
export type AdoptionCountsOut = components["schemas"]["AdoptionCountsOut"];
export type AdoptionRunOut = components["schemas"]["AdoptionRunOut"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useAdoptionCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-narrative-adoption-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/narrative-adoption/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's adoption curve: the as-of and the card are server-side, so both are in the key. Disabled until a
 *  card is picked; the previous view stays on screen while the next one loads. */
export function useNarrativeAdoption(asof: string, thesisId: string | null) {
  return useQuery({
    queryKey: ["dashboard-narrative-adoption", asof, thesisId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/narrative-adoption/{thesis_id}", {
        params: {
          path: { thesis_id: thesisId as string },
          query: { asof },
        },
      });
      if (error) throw error;
      return data;
    },
    enabled: thesisId != null,
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
