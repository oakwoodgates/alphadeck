import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Theme Leaderboard's one read (docs/dashboards/theme-leaderboard.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type LeaderboardOut = components["schemas"]["LeaderboardOut"];
export type LeaderboardCardOut = components["schemas"]["LeaderboardCardOut"];
export type LeaderboardTapeOut = components["schemas"]["LeaderboardTapeOut"];
export type LeaderboardMedianOut = components["schemas"]["LeaderboardMedianOut"];
export type LeaderboardBreadthOut = components["schemas"]["LeaderboardBreadthOut"];
export type LeaderboardKeysOut = components["schemas"]["LeaderboardKeysOut"];
export type LeaderboardAdoptionOut = components["schemas"]["LeaderboardAdoptionOut"];
export type LeaderboardInsidersOut = components["schemas"]["LeaderboardInsidersOut"];
export type LeaderboardCapMixOut = components["schemas"]["LeaderboardCapMixOut"];
export type LeaderboardFlaggedOut = components["schemas"]["LeaderboardFlaggedOut"];

export type CardState = NonNullable<LeaderboardCardOut["state"]>;

/** Every counted card with its five parts: the filters, sorts, percentiles and score are the page's (client side),
 *  so the as-of is the only request parameter. The previous view stays on screen while the next as-of loads. */
export function useThemeLeaderboard(asof: string) {
  return useQuery({
    queryKey: ["dashboard-theme-leaderboard", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/theme-leaderboard", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
