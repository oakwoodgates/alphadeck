import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Ticker Lookup's reads (docs/dashboards/ticker-lookup.md). Its own hooks, not api/hooks.ts, so cutting the
// dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8). The suggestions as you type are NOT here: they
// come from the Workbench's existing company search (`useResolveSecurities` in api/hooks.ts), already served.

export type TickerLookupOut = components["schemas"]["TickerLookupOut"];
export type TickerLookupResolveOut = components["schemas"]["TickerLookupResolveOut"];
export type LookupIdentity = components["schemas"]["TickerLookupIdentityOut"];
export type LookupCap = components["schemas"]["TickerLookupCapOut"];
export type LookupCards = components["schemas"]["TickerLookupCardsOut"];
export type LookupCard = components["schemas"]["TickerLookupCardOut"];
export type LookupLink = components["schemas"]["TickerLookupLinkOut"];
export type LookupPlace = components["schemas"]["TickerLookupPlaceOut"];
export type LookupTriggers = components["schemas"]["TickerLookupTriggersOut"];
export type LookupTrigger = components["schemas"]["TickerLookupTriggerOut"];
export type LookupSource = components["schemas"]["TickerLookupSourceOut"];
export type LookupTape = components["schemas"]["TickerLookupTapeOut"];
export type LookupFigure = components["schemas"]["TickerLookupFigureOut"];
export type LookupSparkline = components["schemas"]["TickerLookupSparklineOut"];
export type LookupFilings = components["schemas"]["TickerLookupFilingsOut"];
export type LookupFilingDay = components["schemas"]["TickerLookupFilingDayOut"];
export type LookupFiling = components["schemas"]["TickerLookupFilingOut"];
export type LookupCardCatalyst = components["schemas"]["TickerLookupCardCatalystOut"];
export type LookupDecision = components["schemas"]["TickerLookupDecisionOut"];

/** One typed ticker resolved to its company: the exact ticker (the server upper-cases it), else, when asked for, a
 *  former ticker. `null` = no such company (the server's 404). Any other failure throws. */
export async function fetchResolve(t: string, former = false): Promise<TickerLookupResolveOut | null> {
  const { data, error, response } = await api.GET("/dashboards/ticker-lookup/resolve", {
    params: { query: { t, former } },
  });
  if (response?.status === 404) return null;
  if (error) throw error;
  return data ?? null;
}

export const resolveKey = (t: string, former = false) =>
  ["dashboard-ticker-lookup-resolve", t.toUpperCase(), former] as const;

/** The company behind `?t=`: disabled until there is a ticker. The result is the company, or `null` when the
 *  master has no such ticker. */
export function useResolveTicker(t: string | null) {
  return useQuery({
    queryKey: resolveKey(t ?? ""),
    queryFn: () => fetchResolve(t as string),
    enabled: t != null && t !== "",
    staleTime: MONITOR_STALE_MS,
  });
}

/** One company's whole page at the as-of. Disabled until a company is resolved. The server holds the page in the
 *  shared dashboard cache per company and clock, and the filings window toggle is client side, so only the company
 *  and the as-of are in the key. The previous page stays on screen while the next one loads. */
export function useTickerLookup(asof: string, securityId: string | null) {
  return useQuery({
    queryKey: ["dashboard-ticker-lookup", asof, securityId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/ticker-lookup/{security_id}", {
        params: { path: { security_id: securityId as string }, query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    enabled: securityId != null,
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
