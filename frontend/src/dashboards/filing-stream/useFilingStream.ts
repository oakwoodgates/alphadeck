import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Filing Stream's two reads (docs/dashboards/filing-stream.md). Its own hooks, not api/hooks.ts, so cutting
// the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type FilingStreamOut = components["schemas"]["FilingStreamOut"];
export type FilingStreamCardsOut = components["schemas"]["FilingStreamCardsOut"];
export type FilingStreamCardOut = components["schemas"]["FilingStreamCardOut"];
export type FilingStreamDayOut = components["schemas"]["FilingStreamDayOut"];
export type FilingStreamRowOut = components["schemas"]["FilingStreamRowOut"];
export type FilingStreamLinkOut = components["schemas"]["FilingStreamLinkOut"];
export type FilingStreamNameOut = components["schemas"]["FilingStreamNameOut"];
export type FilingStreamCountsOut = components["schemas"]["FilingStreamCountsOut"];
export type StreamDays = components["schemas"]["FilingStreamDays"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useFilingStreamCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-filing-stream-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/filing-stream/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's stream: the as-of, the card and the window are server-side, so all three are in the key (the server
 *  slices every window off ONE cached 365-day read per card and as-of, so a window flip is quick once the first
 *  load is done). Every filter and the fold are client-side and not in the key. Disabled until a card is picked;
 *  the previous view stays on screen while the next one loads. */
export function useFilingStream(asof: string, thesisId: string | null, days: StreamDays) {
  return useQuery({
    queryKey: ["dashboard-filing-stream", asof, thesisId, days] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/filing-stream/{thesis_id}", {
        params: {
          path: { thesis_id: thesisId as string },
          query: { asof, days },
        },
      });
      if (error) throw error;
      return data;
    },
    enabled: thesisId != null,
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
