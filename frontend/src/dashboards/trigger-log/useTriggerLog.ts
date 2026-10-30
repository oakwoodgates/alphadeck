import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Trigger Log's one read (docs/dashboards/trigger-log.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type TriggerLogOut = components["schemas"]["TriggerLogOut"];
export type TriggerLogRowOut = components["schemas"]["TriggerLogRowOut"];
export type TriggerLogSourceOut = components["schemas"]["TriggerLogSourceOut"];
export type TriggerLogSinceOut = components["schemas"]["TriggerLogSinceOut"];
export type TriggerLogNameOut = components["schemas"]["TriggerLogNameOut"];
export type TriggerLogDays = components["schemas"]["TriggerLogDays"];
/** Which role(s) one read loads: the entry triggers (the default), the risk signals, or both. */
export type TriggerLogRole = TriggerLogOut["role"];

/** The window's rows of one role at the as-of. The as-of, the window and the role are server-side (risk signals
 *  are the bulk of the record, so each role is its own read), so all three are in the key (the server holds one
 *  cached read per window, role and as-of). Grouping, every filter and every sort are client-side and not in the
 *  key. The previous view stays on screen while the next one loads. */
export function useTriggerLog(asof: string, days: TriggerLogDays, role: TriggerLogRole) {
  return useQuery({
    queryKey: ["dashboard-trigger-log", asof, days, role] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/trigger-log", {
        params: { query: { asof, days, role } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
