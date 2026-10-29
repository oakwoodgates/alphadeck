import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Narrative Index's two reads (docs/dashboards/narrative-index.md). Its own hooks, not api/hooks.ts, so
// cutting the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type NarrativeIndexOut = components["schemas"]["NarrativeIndexOut"];
export type NarrativeCardOut = components["schemas"]["NarrativeCardOut"];
export type NarrativeBenchmarkOut = components["schemas"]["NarrativeBenchmarkOut"];
export type NarrativeCardDetailOut = components["schemas"]["NarrativeCardDetailOut"];
export type NarrativeMemberOut = components["schemas"]["NarrativeMemberOut"];
export type IndexWindow = NarrativeIndexOut["window"];

/** The grid: the as-of, the window and the Context/Bench toggle are all server-side, so all three are in the
 *  key (the server slices every window off ONE cached 1Y read per as-of and toggle, so a window switch is
 *  quick once the first load is done). The previous view stays on screen while the next one loads. */
export function useNarrativeIndex(asof: string, window: IndexWindow, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-narrative-index", asof, window, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/narrative-index", {
        params: { query: { asof, window, include_silent: includeSilent } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One enlarged card: the names contributing each session and each name's window, off the same cached read.
 *  Disabled until a card is open. */
export function useNarrativeCard(
  asof: string,
  window: IndexWindow,
  includeSilent: boolean,
  thesisId: string | null,
) {
  return useQuery({
    queryKey: ["dashboard-narrative-index-card", asof, window, includeSilent, thesisId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/narrative-index/{thesis_id}", {
        params: {
          path: { thesis_id: thesisId as string },
          query: { asof, window, include_silent: includeSilent },
        },
      });
      if (error) throw error;
      return data;
    },
    enabled: thesisId != null,
    staleTime: MONITOR_STALE_MS,
  });
}
