import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Price & Volume Landing's one read (docs/dashboards/landing.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type LandingOut = components["schemas"]["LandingOut"];
export type LandingGroupOut = components["schemas"]["LandingGroupOut"];
export type LandingGroupWindowOut = components["schemas"]["LandingGroupWindowOut"];
export type LandingSplitOut = components["schemas"]["LandingSplitOut"];
export type LandingNameOut = components["schemas"]["LandingNameOut"];
export type LandingNameWindowOut = components["schemas"]["LandingNameWindowOut"];
export type GroupBy = LandingOut["group_by"];
export type Quadrant = NonNullable<LandingNameWindowOut["quadrant"]>;

/** One view of the page: the as-of, the grouping and the Context/Bench toggle are server-side rules, so all
 *  three are in the key (the server serves every view off ONE cached read per as-of, so a flip is quick once
 *  the first load is done). The previous view stays on screen while the next one loads. */
export function useLanding(asof: string, groupBy: GroupBy, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-landing", asof, groupBy, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/landing", {
        params: { query: { asof, group_by: groupBy, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
