import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Read-Throughs table's one read (docs/dashboards/read-through.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type ReadThroughOut = components["schemas"]["ReadThroughOut"];
export type ReadThroughRowOut = components["schemas"]["ReadThroughRowOut"];
export type ReadThroughCardOut = components["schemas"]["ReadThroughCardOut"];
export type ReadThroughFilingOut = components["schemas"]["ReadThroughFilingOut"];
export type ReadThroughCatalystOut = components["schemas"]["ReadThroughCatalystOut"];
export type ReadThroughTapeOut = components["schemas"]["ReadThroughTapeOut"];
export type ReadThroughSpyOut = components["schemas"]["ReadThroughSpyOut"];
export type ReadThroughRoleCountOut = components["schemas"]["ReadThroughRoleCountOut"];
export type ReadThroughCardRefOut = components["schemas"]["ReadThroughCardRefOut"];
export type ReadThroughFlaggedOut = components["schemas"]["ReadThroughFlaggedOut"];
export type RoleKey = ReadThroughRowOut["role"];

/** The whole table at the as-of. Everything else (tab, sort, filters) is client side, so the as-of is the only
 *  parameter and the only key: the server computes the table once per as-of and serves every view off it. The
 *  previous view stays on screen while the next one loads. */
export function useReadThrough(asof: string) {
  return useQuery({
    queryKey: ["dashboard-read-through", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/read-through", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
