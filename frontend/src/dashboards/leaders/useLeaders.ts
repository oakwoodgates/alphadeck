import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Leaders board's one read (docs/dashboards/leaders.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type LeadersOut = components["schemas"]["LeadersOut"];
export type LeadersNameOut = components["schemas"]["LeadersNameOut"];
export type LeadersCardOut = components["schemas"]["LeadersCardOut"];
export type LeadersListOut = components["schemas"]["LeadersListOut"];
export type LeadersInsiderGroupOut = components["schemas"]["LeadersInsiderGroupOut"];
export type LeadersClusterNameOut = components["schemas"]["LeadersClusterNameOut"];
export type GroupBy = LeadersOut["group_by"];
export type ListKey = LeadersListOut["key"];

/** One view of the board: the as-of, the Context/Bench toggle and the insider-cluster grouping are
 *  server-side rules, so all three are in the key (the server serves every view off ONE cached read per
 *  as-of, so a flip is quick once the first load is done). The previous view stays on screen while the next
 *  one loads. */
export function useLeaders(asof: string, groupBy: GroupBy, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-leaders", asof, groupBy, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/leaders", {
        params: { query: { asof, group_by: groupBy, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
