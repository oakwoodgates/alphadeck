import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Entry Windows' one read (docs/dashboards/entry-windows.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type EntryWindowsOut = components["schemas"]["EntryWindowsOut"];
export type EntryWindowsRowOut = components["schemas"]["EntryWindowsRowOut"];
export type EntryWindowsMemberOut = components["schemas"]["EntryWindowsMemberOut"];
export type EntryWindowsTriggerOut = components["schemas"]["EntryWindowsTriggerOut"];
export type EntryWindowsSourceOut = components["schemas"]["EntryWindowsSourceOut"];
export type EntryWindowsCatalystOut = components["schemas"]["EntryWindowsCatalystOut"];
export type EntryWindowsSinceOut = components["schemas"]["EntryWindowsSinceOut"];
export type EntryWindowsSparkOut = components["schemas"]["EntryWindowsSparkOut"];
export type EntryWindowsCountsOut = components["schemas"]["EntryWindowsCountsOut"];
export type EntryWindowsFlaggedOut = components["schemas"]["EntryWindowsFlaggedOut"];

/** The whole page at the as-of: every Armed and Managing row. The Managing toggle and the sorts are client side,
 *  so only the as-of is in the key. The previous view stays on screen while the next as-of loads. */
export function useEntryWindows(asof: string) {
  return useQuery({
    queryKey: ["dashboard-entry-windows", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/entry-windows", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
