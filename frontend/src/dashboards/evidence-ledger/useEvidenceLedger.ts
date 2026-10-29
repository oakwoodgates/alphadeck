import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { MONITOR_STALE_MS } from "../../api/queryClient";
import type { components } from "../../api/types.gen";

// The Evidence Ledger's two reads (docs/dashboards/evidence-ledger.md). Its own hooks, not api/hooks.ts, so cutting
// the dashboard deletes this folder cleanly (docs/DASHBOARDS.md rule 8).

export type EvidenceLedgerOut = components["schemas"]["EvidenceLedgerOut"];
export type EvidenceLedgerCardsOut = components["schemas"]["EvidenceLedgerCardsOut"];
export type EvidenceLedgerCardOut = components["schemas"]["EvidenceLedgerCardOut"];
export type EvidenceLedgerDayOut = components["schemas"]["EvidenceLedgerDayOut"];
export type EvidenceLedgerRowOut = components["schemas"]["EvidenceLedgerRowOut"];
export type EvidenceLedgerSourceOut = components["schemas"]["EvidenceLedgerSourceOut"];
export type EvidenceLedgerNameOut = components["schemas"]["EvidenceLedgerNameOut"];
export type EvidenceLedgerTalliesOut = components["schemas"]["EvidenceLedgerTalliesOut"];

/** The picker: the cards counted at the as-of, A→Z (the server's order). */
export function useEvidenceLedgerCards(asof: string) {
  return useQuery({
    queryKey: ["dashboard-evidence-ledger-cards", asof] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/evidence-ledger/cards", {
        params: { query: { asof } },
      });
      if (error) throw error;
      return data;
    },
    staleTime: MONITOR_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

/** One card's ledger: the as-of and the card are server-side, so both are in the key. Every filter (class, kind,
 *  name, EDGAR link, date window) is client-side and not in the key. Disabled until a card is picked; the
 *  previous view stays on screen while the next one loads. */
export function useEvidenceLedger(asof: string, thesisId: string | null) {
  return useQuery({
    queryKey: ["dashboard-evidence-ledger", asof, thesisId] as const,
    queryFn: async () => {
      const { data, error } = await api.GET("/dashboards/evidence-ledger/{thesis_id}", {
        params: {
          path: { thesis_id: thesisId as string },
          query: { asof },
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
