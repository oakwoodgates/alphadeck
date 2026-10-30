import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Hot Board's one read (docs/dashboards/hot-board.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type HotBoardOut = components["schemas"]["HotBoardOut"];
export type HotRowOut = components["schemas"]["HotRowOut"];
export type HotCardRefOut = components["schemas"]["HotCardRefOut"];
export type HotTriggerOut = components["schemas"]["HotTriggerOut"];
export type HotSourceOut = components["schemas"]["HotSourceOut"];
export type HotCellOut = components["schemas"]["HotCellOut"];
export type HotTapeOut = components["schemas"]["HotTapeOut"];
export type HotCapOut = components["schemas"]["HotCapOut"];
export type HotClocksOut = components["schemas"]["HotClocksOut"];
export type HotSinceArmOut = components["schemas"]["HotSinceArmOut"];
export type HotFlaggedOut = components["schemas"]["HotFlaggedOut"];

export type RowKind = HotRowOut["kind"];
export type CapTier = NonNullable<HotCapOut["tier"]>;

/** Every candidate row of the cards' recorded calls in effect at the as-of: the toggles, filters, sorts and the
 *  heat are the page's (client side), so the as-of is the only request parameter. The previous view stays on
 *  screen while the next as-of loads. */
export function useHotBoard(asof: string) {
  return useQuery({
    queryKey: ["dashboard-hot-board", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/hot-board", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
