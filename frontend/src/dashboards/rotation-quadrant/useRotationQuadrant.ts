import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Rotation Quadrant's one read (docs/dashboards/rotation-quadrant.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type RotationQuadrantOut = components["schemas"]["RotationQuadrantOut"];
export type QuadrantGroupOut = components["schemas"]["QuadrantGroupOut"];
export type QuadrantNameOut = components["schemas"]["QuadrantNameOut"];
export type QuadrantTailPointOut = components["schemas"]["QuadrantTailPointOut"];
export type QuadrantFlaggedOut = components["schemas"]["QuadrantFlaggedOut"];
export type GroupBy = RotationQuadrantOut["group_by"];
export type Quadrant = NonNullable<QuadrantGroupOut["quadrant"]>;

/** One view of the page: the as-of, the grouping and the Context/Bench toggle are server-side rules, so all
 *  three are in the key (the server serves every grouping off ONE cached read per as-of and toggle, so a
 *  grouping flip is quick once the first load is done). The previous view stays on screen while the next one
 *  loads. */
export function useRotationQuadrant(asof: string, groupBy: GroupBy, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-rotation-quadrant", asof, groupBy, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/rotation-quadrant", {
        params: { query: { asof, group_by: groupBy, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
