import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Cross Radar's one read (docs/dashboards/cross-radar.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type CrossRadarOut = components["schemas"]["CrossRadarOut"];
export type CrossGroupOut = components["schemas"]["CrossGroupOut"];
export type CrossCardOut = components["schemas"]["CrossCardOut"];
export type CrossNameOut = components["schemas"]["CrossNameOut"];
export type CrossClusterOut = components["schemas"]["CrossClusterOut"];
export type CrossFirstMoverOut = components["schemas"]["CrossFirstMoverOut"];
export type CrossNearGroupOut = components["schemas"]["CrossNearGroupOut"];
export type CrossNearNameOut = components["schemas"]["CrossNearNameOut"];
export type CrossMarkOut = components["schemas"]["CrossMarkOut"];
export type CrossTimelineRowOut = components["schemas"]["CrossTimelineRowOut"];
export type CrossTimelineNameOut = components["schemas"]["CrossTimelineNameOut"];
export type CrossPostureOut = components["schemas"]["CrossPostureOut"];
export type GroupBy = CrossRadarOut["group_by"];

/** One view of the radar: the as-of, the grouping, the Context/Bench toggle and the price × 200d timeline
 *  layer are server-side, so all four are in the key (the server serves every view off ONE cached read per
 *  as-of, so a flip is quick once the first load is done). The previous view stays on screen while the next
 *  one loads. */
export function useCrossRadar(
  asof: string,
  groupBy: GroupBy,
  includeSilent: boolean,
  price200d: boolean,
) {
  return useQuery({
    queryKey: ["dashboard-cross-radar", asof, groupBy, includeSilent, price200d] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/cross-radar", {
        params: {
          query: { asof, group_by: groupBy, include_silent: includeSilent, price_200d: price200d },
        },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
