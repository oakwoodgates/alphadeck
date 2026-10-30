import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Daily Digest's one read (docs/dashboards/daily-digest.md). Its own hook, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

type Schemas = components["schemas"];

export type DailyDigestOut = Schemas["DailyDigestOut"];
export type DigestNightsOut = Schemas["DigestNightsOut"];
export type DigestLastNightOut = Schemas["DigestLastNightOut"];
export type DigestCardOut = Schemas["DigestCardOut"];
export type DigestLineOut = Schemas["DigestLineOut"];
export type DigestSourceOut = Schemas["DigestSourceOut"];
export type DigestPositionOut = Schemas["DigestPositionOut"];
export type DigestSinceFillOut = Schemas["DigestSinceFillOut"];
export type DigestLastSessionOut = Schemas["DigestLastSessionOut"];
export type DigestHoldOut = Schemas["DigestHoldOut"];
export type DigestDueOut = Schemas["DigestDueOut"];
export type DigestDueItemOut = Schemas["DigestDueItemOut"];
export type DigestInsiderOut = Schemas["DigestInsiderOut"];
export type DigestBuyRowOut = Schemas["DigestBuyRowOut"];
export type DigestTriggerOut = Schemas["DigestTriggerOut"];
export type DigestTapeOut = Schemas["DigestTapeOut"];
export type DigestTapeRowOut = Schemas["DigestTapeRowOut"];
export type DigestFormingOut = Schemas["DigestFormingOut"];
export type DigestCountsOut = Schemas["DigestCountsOut"];
export type DigestFlaggedOut = Schemas["DigestFlaggedOut"];

/** The whole morning page at the as-of, in one read: every section's rows and the counts behind them. The tape's
 *  "show every armed name" toggle is client side, so the as-of is the only request parameter. The previous view
 *  stays on screen while the next as-of loads. */
export function useDailyDigest(asof: string) {
  return useQuery({
    queryKey: ["dashboard-daily-digest", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/daily-digest", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
