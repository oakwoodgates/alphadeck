import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Chain Map's two reads (docs/dashboards/chain-map.md). Its own hooks, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type ChainMapOut = components["schemas"]["ChainMapOut"];
export type ChainMapCardsOut = components["schemas"]["ChainMapCardsOut"];
export type ChainMapCardOut = components["schemas"]["ChainMapCardOut"];
export type ChainMapColumnOut = components["schemas"]["ChainMapColumnOut"];
export type ChainMapRailOut = components["schemas"]["ChainMapRailOut"];
export type ChainMapNodeOut = components["schemas"]["ChainMapNodeOut"];
export type ChainMapEdgeOut = components["schemas"]["ChainMapEdgeOut"];
export type ChainMapFilingOut = components["schemas"]["ChainMapFilingOut"];
export type ChainMapCountsOut = components["schemas"]["ChainMapCountsOut"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useChainMapCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-chain-map-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/chain-map/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's picture: the as-of and the card are server-side, so both are in the key. The Context/Bench toggle
 *  is client side (the page always carries those columns), so it is not. Disabled until a card is picked; the
 *  previous view stays on screen while the next one loads. */
export function useChainMap(asof: string, thesisId: string | null) {
  return useQuery({
    queryKey: ["dashboard-chain-map", asof, thesisId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/chain-map/{thesis_id}", {
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
