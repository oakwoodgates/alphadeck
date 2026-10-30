import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Market Map's one read (docs/dashboards/market-map.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type MarketMapOut = components["schemas"]["MarketMapOut"];
export type MapCompany = components["schemas"]["MapCompanyOut"];
export type MapMembership = components["schemas"]["MapMembershipOut"];
export type MapCard = components["schemas"]["MapCardOut"];
export type MapFlagged = components["schemas"]["MapFlaggedOut"];

/** The whole map's readings at an as-of. The payload is readings, not a layout, and every control (group-by,
 *  window, benchmark, size, tiles, Context/Bench) is applied on the page, so the as-of is the only thing in the
 *  key: a control flip never refetches. The previous as-of stays on screen while the next one loads. */
export function useMarketMap(asof: string) {
  return useQuery({
    queryKey: ["dashboard-market-map", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/market-map", { params: { query: { asof } } });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
