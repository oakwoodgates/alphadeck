import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Peer Table's two reads (docs/dashboards/peer-table.md). Its own hooks, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type PeerScopesOut = components["schemas"]["PeerScopesOut"];
export type PeerKeyOptionOut = components["schemas"]["PeerKeyOptionOut"];
export type PeerCardOptionOut = components["schemas"]["PeerCardOptionOut"];
export type PeerTableOut = components["schemas"]["PeerTableOut"];
export type PeerCompanyOut = components["schemas"]["PeerCompanyOut"];
export type PeerCardChipOut = components["schemas"]["PeerCardChipOut"];
export type PeerCellOut = components["schemas"]["PeerCellOut"];
export type PeerCapOut = components["schemas"]["PeerCapOut"];
export type PeerRevenueOut = components["schemas"]["PeerRevenueOut"];
export type PeerYoyOut = components["schemas"]["PeerYoyOut"];
export type PeerRunwayOut = components["schemas"]["PeerRunwayOut"];
export type PeerPurityOut = components["schemas"]["PeerPurityOut"];
export type PeerSpyOut = components["schemas"]["PeerSpyOut"];
export type PeerInsiderOut = components["schemas"]["PeerInsiderOut"];
export type PeerGroupOut = components["schemas"]["PeerGroupOut"];
export type PeerGroupStatsOut = components["schemas"]["PeerGroupStatsOut"];
export type PeerMedianOut = components["schemas"]["PeerMedianOut"];
export type PeerCoverageOut = components["schemas"]["PeerCoverageOut"];
export type PeerBlankOut = components["schemas"]["PeerBlankOut"];
export type PeerFlaggedOut = components["schemas"]["PeerFlaggedOut"];
export type PeerUnresolvedOut = components["schemas"]["PeerUnresolvedOut"];

export type ScopeKind = PeerTableOut["scope"];
export type ColumnKey = PeerTableOut["columns"][number];
export type Gap = NonNullable<PeerCellOut["gap"]>;
export type CapTier = NonNullable<PeerCapOut["tier"]>;

/** The scope lists at the as-of: the business-type leaves, the supersectors and the counted cards, with their
 *  companies, and the leaf to open on. */
export function usePeerScopes(asof: string) {
  return useQuery({
    queryKey: ["dashboard-peer-table-scopes", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/peer-table/scopes", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One scope's whole table: the as-of and the scope are server-side, so they are the key. The sort and the filters
 *  are client side, so they are not. Disabled until a scope is resolved; the previous view stays on screen while the
 *  next one loads. */
export function usePeerTable(asof: string, scope: ScopeKind | null, key: string | null) {
  return useQuery({
    queryKey: ["dashboard-peer-table", asof, scope, scope === "all" ? null : key] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/peer-table", {
        params: {
          query: scope === "all" ? { asof, scope } : { asof, scope: scope as ScopeKind, key },
        },
      });
      if (error) throw error;
      return data;
    },
    enabled: scope != null && (scope === "all" || key != null),
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
