import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The ETFs page's one read (docs/dashboards/etfs.md). Its own hook, not api/hooks.ts, so cutting the dashboard
// deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

type Schemas = components["schemas"];

export type EtfsOut = Schemas["EtfsOut"];
export type EtfCompareRowOut = Schemas["EtfCompareRowOut"];
export type EtfFlowRowOut = Schemas["EtfFlowRowOut"];
export type EtfSizeRowOut = Schemas["EtfSizeRowOut"];
export type EtfTrendRowOut = Schemas["EtfTrendRowOut"];
export type EtfReadingOut = Schemas["EtfReadingOut"];
export type EtfCardRefOut = Schemas["EtfCardRefOut"];
export type EtfCoverageOut = Schemas["EtfCoverageOut"];
export type EtfFlaggedOut = Schemas["EtfFlaggedOut"];
export type EtfWindow = EtfsOut["window"];

/** The whole page at the as-of. Only panel 1 depends on the window, and the server slices every window off ONE
 *  cached read per as-of, so a window switch is quick once the first load is done. The previous view stays on
 *  screen while the next one loads. */
export function useEtfs(asof: string, window: EtfWindow) {
  return useQuery({
    queryKey: ["dashboard-etfs", asof, window] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/etfs", {
        params: { query: { asof, window } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
