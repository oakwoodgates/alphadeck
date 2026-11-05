import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Overlap Map's one read (docs/dashboards/overlap-map.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type OverlapMapOut = components["schemas"]["OverlapMapOut"];
export type OverlapCardOut = components["schemas"]["OverlapCardOut"];
export type OverlapPairOut = components["schemas"]["OverlapPairOut"];
export type OverlapGroupsOut = components["schemas"]["OverlapGroupsOut"];
export type OverlapCompanyOut = components["schemas"]["OverlapCompanyOut"];
export type OverlapCompanyCardOut = components["schemas"]["OverlapCompanyCardOut"];

/** One view of the page: the as-of and the Context / ETFs / Bench toggle are server-side rules, so both are in the
 *  key. The threshold, the tabs and the Venn picks all derive from this one payload on the client. The previous
 *  view stays on screen while the next one loads. */
export function useOverlapMap(asof: string, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-overlap-map", asof, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/overlap-map", {
        params: { query: { asof, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
