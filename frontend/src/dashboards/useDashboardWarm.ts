import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { components } from "../api/types.gen";

export type WarmStatusOut = components["schemas"]["WarmStatusOut"];

function warmStatusKey(asofParam: string | null) {
  return ["dashboard-warm-status", asofParam ?? "current"] as const;
}

/** Read current dashboard cache warmth for `asofParam`. Polls every 2s only while a warm pass is running. */
export function useDashboardWarmStatus(asofParam: string | null) {
  return useQuery({
    queryKey: warmStatusKey(asofParam),
    staleTime: 15_000,
    retry: false,
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/warm/status", {
        params: { query: asofParam ? { asof: asofParam } : {} },
      });
      if (error || !data) throw error ?? new Error("Failed to read dashboard warm status");
      return data;
    },
  });
}

/** Trigger an asynchronous dashboard warm pass and seed the status cache immediately so polling starts. */
export function useTriggerDashboardWarm(asofParam: string | null) {
  const qc = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: async () => {
      const { data, error } = await api.POST("/dashboards/warm", {
        params: { query: asofParam ? { asof: asofParam } : {} },
      });
      if (error || !data) throw error ?? new Error("Failed to start dashboard warm");
      return data;
    },
    onSuccess: (status) => {
      qc.setQueryData(warmStatusKey(asofParam), status);
    },
  });
}
