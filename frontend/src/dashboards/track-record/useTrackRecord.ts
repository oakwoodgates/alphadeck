import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Track Record's one read (docs/dashboards/track-record.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type TrackRecordOut = components["schemas"]["TrackRecordOut"];
export type TrackRowOut = components["schemas"]["TrackRowOut"];
export type TrackEntryOut = components["schemas"]["TrackEntryOut"];
export type TrackSpanOut = components["schemas"]["TrackSpanOut"];
export type TrackCapOut = components["schemas"]["TrackCapOut"];
export type TrackCountsOut = components["schemas"]["TrackCountsOut"];
export type TrackHorizonCountOut = components["schemas"]["TrackHorizonCountOut"];
export type TrackCellOut = components["schemas"]["TrackCellOut"];
export type TrackTableRowOut = components["schemas"]["TrackTableRowOut"];
export type TrackFlaggedOut = components["schemas"]["TrackFlaggedOut"];

/** The whole page's read at the as-of: every arm episode's row (both entry rules, every horizon, the cap and the
 *  liquidity at the event), the counts and the opening table. The tiering, slicing, filtering and the entry toggle
 *  are client side (recordView.ts), so none of them is a request parameter or part of the key. The Scoreboard's
 *  own edition and honesty readings are the server's defaults. The previous view stays on screen while the next
 *  as-of loads. */
export function useTrackRecord(asof: string) {
  return useQuery({
    queryKey: ["dashboard-track-record", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/track-record", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
