import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// Insider Buying's one read (docs/dashboards/insider-tracker.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type InsiderTrackerOut = components["schemas"]["InsiderTrackerOut"];
export type InsiderTrackerRowOut = components["schemas"]["InsiderTrackerRowOut"];
export type InsiderTrackerCardOut = components["schemas"]["InsiderTrackerCardOut"];
export type InsiderTrackerClusterOut = components["schemas"]["InsiderTrackerClusterOut"];
export type InsiderTrackerTriggerOut = components["schemas"]["InsiderTrackerTriggerOut"];
export type InsiderTrackerSinceOut = components["schemas"]["InsiderTrackerSinceOut"];
export type InsiderTrackerCapOut = components["schemas"]["InsiderTrackerCapOut"];
export type InsiderTrackerNameOut = components["schemas"]["InsiderTrackerNameOut"];
export type InsiderTrackerDays = components["schemas"]["InsiderTrackerDays"];

/** The window's rows at the as-of. The as-of, the window and the Context-and-Bench toggle are server-side (the
 *  server holds one cached read per window, toggle and as-of), so all three are in the key. Grouping, every filter
 *  and every sort are client-side and not in the key. The previous view stays on screen while the next one loads. */
export function useInsiderTracker(asof: string, days: InsiderTrackerDays, silent: boolean) {
  return useQuery({
    queryKey: ["dashboard-insider-tracker", asof, days, silent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/insider-tracker", {
        params: { query: { asof, days, silent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
