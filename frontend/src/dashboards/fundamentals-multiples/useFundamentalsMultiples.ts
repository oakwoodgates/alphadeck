import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Fundamentals Multiples' two reads (docs/dashboards/fundamentals-multiples.md). Its own hooks, not
// api/hooks.ts, so cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type MultiplesOut = components["schemas"]["MultiplesOut"];
export type MultiplesCardsOut = components["schemas"]["MultiplesCardsOut"];
export type MultiplesCardOut = components["schemas"]["MultiplesCardOut"];
export type MultiplesRowOut = components["schemas"]["MultiplesRowOut"];
export type MultiplesCompanyOut = components["schemas"]["MultiplesCompanyOut"];
export type MultiplesBarOut = components["schemas"]["MultiplesBarOut"];
export type MultiplesLastPrintOut = components["schemas"]["MultiplesLastPrintOut"];
export type MultiplesMedianOut = components["schemas"]["MultiplesMedianOut"];
export type MultiplesMedianPointOut = components["schemas"]["MultiplesMedianPointOut"];
export type MultiplesCountsOut = components["schemas"]["MultiplesCountsOut"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useMultiplesCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-fundamentals-multiples-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/fundamentals-multiples/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's revenue series: the as-of and the card are server-side, so both are in the key. The Context/Bench
 *  toggle is client side (the page always carries those rows), so it is not. Disabled until a card is picked; the
 *  previous view stays on screen while the next one loads. */
export function useFundamentalsMultiples(asof: string, thesisId: string | null) {
  return useQuery({
    queryKey: ["dashboard-fundamentals-multiples", asof, thesisId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/fundamentals-multiples/{thesis_id}", {
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
