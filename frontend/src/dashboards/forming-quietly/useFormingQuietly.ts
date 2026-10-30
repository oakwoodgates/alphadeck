import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// Forming Quietly's one read (docs/dashboards/forming-quietly.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type FormingQuietlyOut = components["schemas"]["FormingQuietlyOut"];
export type FormingRowOut = components["schemas"]["FormingRowOut"];
export type FormingKeyOut = components["schemas"]["FormingKeyOut"];
export type FormingNameOut = components["schemas"]["FormingNameOut"];
export type FormingTriggerOut = components["schemas"]["FormingTriggerOut"];
export type FormingSourceOut = components["schemas"]["FormingSourceOut"];
export type FormingWatchOut = components["schemas"]["FormingWatchOut"];
export type FormingRiskOut = components["schemas"]["FormingRiskOut"];
export type FormingQuietOut = components["schemas"]["FormingQuietOut"];
export type FormingInsiderOut = components["schemas"]["FormingInsiderOut"];
export type FormingCoilingOut = components["schemas"]["FormingCoilingOut"];
export type FormingLeadingOut = components["schemas"]["FormingLeadingOut"];
export type FormingBreadthOut = components["schemas"]["FormingBreadthOut"];
export type FormingAdoptionOut = components["schemas"]["FormingAdoptionOut"];
export type FormingFlaggedOut = components["schemas"]["FormingFlaggedOut"];

export type RowState = FormingRowOut["state"];

/** Every Warming and Incubating card of the recorded calls in effect at the as-of, in the default order: the
 *  Incubating toggle, the filters and the sorts are the page's (client side), so the as-of is the only request
 *  parameter. The previous view stays on screen while the next as-of loads. */
export function useFormingQuietly(asof: string) {
  return useQuery({
    queryKey: ["dashboard-forming-quietly", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/forming-quietly", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
