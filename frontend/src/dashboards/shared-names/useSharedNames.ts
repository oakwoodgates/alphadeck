import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Shared Names dashboard's one read (docs/dashboards/shared-names.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type SharedNamesOut = components["schemas"]["SharedNamesOut"];
export type SharedNameRowOut = components["schemas"]["SharedNameRowOut"];
export type SharedNameChipOut = components["schemas"]["SharedNameChipOut"];
export type SharedNamePairOut = components["schemas"]["SharedNamePairOut"];
export type SharedNameCardRefOut = components["schemas"]["SharedNameCardRefOut"];

/** Today's baskets on the live cards. `countingOnly` is the server-side rule (Context and Bench memberships
 *  dropped before both sections are computed), so it is part of the key. The previous view stays on screen
 *  while the other one loads; the page reads its wording off the payload's own `counting_only`. */
export function useSharedNames(countingOnly: boolean) {
  return useQuery({
    queryKey: ["dashboard-shared-names", countingOnly] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/shared-names", {
        params: { query: { counting_only: countingOnly } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
