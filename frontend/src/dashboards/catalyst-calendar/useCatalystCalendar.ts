import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Catalyst Calendar dashboard's one read (docs/dashboards/catalyst-calendar.md). Its own hook, not
// api/hooks.ts, so cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type CatalystCalendarOut = components["schemas"]["CatalystCalendarOut"];
export type CatalystCalendarBucketOut = components["schemas"]["CatalystCalendarBucketOut"];
export type CatalystCalendarItemOut = components["schemas"]["CatalystCalendarItemOut"];
export type CatalystCalendarUndatedGroupOut = components["schemas"]["CatalystCalendarUndatedGroupOut"];
export type CatalystCalendarUndatedOut = components["schemas"]["CatalystCalendarUndatedOut"];

/** Every live card's dated catalysts and card deadlines, the passed ones and the undated ones. A "now" view:
 *  no as-of, so no key beyond its name; the filters are on-screen choices over this one payload. */
export function useCatalystCalendar() {
  return useQuery({
    queryKey: ["dashboard-catalyst-calendar"] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/catalyst-calendar");
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
  });
}
