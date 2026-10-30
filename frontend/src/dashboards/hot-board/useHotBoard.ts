import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Hot Board's reads (docs/dashboards/hot-board.md). Its own hook, not api/hooks.ts, so cutting the
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
export type HotCandidateCountsOut = components["schemas"]["HotCandidateCountsOut"];

export type RowKind = HotRowOut["kind"];
export type CapTier = NonNullable<HotCapOut["tier"]>;

/** Which candidate rows a read carries: `armed` = the armed names with their tape plus the COUNTS of the other
 *  kinds (the page's default read); `all` = every candidate row with its tape (read the first time a toggle is
 *  used). */
export type HotRows = HotBoardOut["rows_loaded"];

/** One read of the Hot Board. The toggles, filters, sorts and the heat are the page's (client side), so the request
 *  parameters are the as-of and which rows to carry. The previous view stays on screen while the next as-of loads.
 *  `enabled` holds a read back until the page needs it: the full read waits for a toggle. */
export function useHotBoard(asof: string, rows: HotRows = "armed", enabled = true) {
  return useQuery({
    queryKey: ["dashboard-hot-board", asof, rows] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/hot-board", {
        params: { query: { asof, rows } },
      });
      if (error) throw error;
      return data;
    },
    enabled,
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
