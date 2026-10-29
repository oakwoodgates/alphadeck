import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Rotation Map's one read (docs/dashboards/rotation-map.md). Its own hook, not api/hooks.ts, so cutting
// the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type RotationMapOut = components["schemas"]["RotationMapOut"];
export type RotationGroupOut = components["schemas"]["RotationGroupOut"];
export type RotationNameOut = components["schemas"]["RotationNameOut"];
export type RotationNameCardOut = components["schemas"]["RotationNameCardOut"];
export type RotationWindowOut = components["schemas"]["RotationWindowOut"];
export type GroupBy = RotationMapOut["group_by"];

/** One view of the map: the as-of, the grouping and the Context/Bench toggle are server-side rules, so all
 *  three are in the key (the server serves every view off ONE cached read per as-of, so a flip is quick once
 *  the first load is done). The vs-SPY toggle is client-side: the payload carries SPY's own returns. The
 *  previous view stays on screen while the next one loads. */
export function useRotationMap(asof: string, groupBy: GroupBy, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-rotation-map", asof, groupBy, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/rotation-map", {
        params: { query: { asof, group_by: groupBy, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
