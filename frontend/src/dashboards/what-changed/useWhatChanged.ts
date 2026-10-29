import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The What Changed dashboard's one read (docs/dashboards/what-changed.md). Its own hook, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type WhatChangedOut = components["schemas"]["WhatChangedOut"];
export type WhatChangedItemOut = components["schemas"]["WhatChangedItemOut"];
export type WhatChangedCardRefOut = components["schemas"]["WhatChangedCardRefOut"];

/** `since` = "last_night" | "7d" | a YYYY-MM-DD date: the baseline night, resolved server-side (where every
 *  diff rule lives), so it is the whole key. The previous view stays on screen while the next one loads; a
 *  picked date outside the record comes back as a 422 whose `detail` says why. */
export function useWhatChanged(since: string) {
  return useQuery({
    queryKey: ["dashboard-what-changed", since] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/what-changed", {
        params: { query: { since } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
