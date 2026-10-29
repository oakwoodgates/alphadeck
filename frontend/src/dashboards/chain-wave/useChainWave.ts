import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Chain Wave's two reads (docs/dashboards/chain-wave.md). Its own hooks, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type ChainWaveOut = components["schemas"]["ChainWaveOut"];
export type ChainWaveCardsOut = components["schemas"]["ChainWaveCardsOut"];
export type ChainWaveCardOut = components["schemas"]["ChainWaveCardOut"];
export type ChainWaveRowOut = components["schemas"]["ChainWaveRowOut"];
export type ChainWaveMemberOut = components["schemas"]["ChainWaveMemberOut"];
export type ChainWaveCellOut = components["schemas"]["ChainWaveCellOut"];
export type ChainWaveWeekOut = components["schemas"]["ChainWaveWeekOut"];
export type ChainWaveReadoutOut = components["schemas"]["ChainWaveReadoutOut"];
export type WaveWeeks = components["schemas"]["ChainWaveWeeks"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useChainWaveCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-chain-wave-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/chain-wave/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's grid: the as-of, the card, the window and the Context/Bench toggle are all server-side, so all
 *  four are in the key (the server slices every window and the toggle off ONE cached read per card and as-of,
 *  so a window or toggle flip is quick once the first load is done). Disabled until a card is picked; the
 *  previous view stays on screen while the next one loads. */
export function useChainWave(asof: string, thesisId: string | null, weeks: WaveWeeks, includeSilent: boolean) {
  return useQuery({
    queryKey: ["dashboard-chain-wave", asof, thesisId, weeks, includeSilent] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/chain-wave/{thesis_id}", {
        params: {
          path: { thesis_id: thesisId as string },
          query: { asof, weeks, include_silent: includeSilent },
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
