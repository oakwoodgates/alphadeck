import { useState } from "react";

import type { BasketMember } from "../api/hooks";
import { useIngestPrices, useResolveEtf } from "../api/hooks";
import { errText } from "./format";

interface Props {
  existingKeys: Set<string>; // security_ids already in the basket (block a double-add)
  onAdd: (m: BasketMember, name?: string | null) => void; // name → the display-only security_id→name bridge
}

/** Surface an ETF as a low-torque `fund` sleeve (ETF Sleeve, Slice 1) — sits directly below `AddName`. The
 *  operator types a ticker (e.g. LIT); the server resolves it (lookup-or-create) and marks it
 *  instrument_kind='etf' — the sleeve's ONE marker (every sleeve surface keys on it; a fund has no SIC) —
 *  and we add it to the basket as a plain member, then pull its EOD price so the sleeve shows its price.
 *  Unlike AddName this needs NO prior master match — surfacing an ETF absent from the master is the whole
 *  point (hold the theme, skip picking names). The member + price machinery is reused, not rebuilt.
 *  Reversible like any member (interaction #1): the row's own remove is the inverse. */
export function SurfaceEtf({ existingKeys, onAdd }: Props) {
  const [ticker, setTicker] = useState("");
  const resolveEtf = useResolveEtf();
  const ingestPx = useIngestPrices();

  const surface = () => {
    const t = ticker.trim().toUpperCase();
    if (!t || resolveEtf.isPending) return;
    resolveEtf.mutate(t, {
      onSuccess: (match) => {
        setTicker("");
        // already in the basket — don't double-add; the reversible inverse is the existing row's remove
        if (existingKeys.has(match.security_id)) return;
        onAdd(
          {
            ticker: match.ticker,
            role: "ETF sleeve",
            security_id: match.security_id,
            segment: null,
            conviction: null,
            authored_by: "system_drafted", // no description typed — honestly a model draft (S1)
            signed_off: true, // surfacing the sleeve IS the endorsement (a hand-add — auto sign-off)
          },
          match.name, // feed the name bridge so the placed sleeve shows it (BasketMember carries no name)
        );
        ingestPx.mutate(match.security_id); // the sleeve shows its price (the existing decoupled price leg)
      },
    });
  };

  return (
    <div className="wb-addname wb-surface-etf">
      <input
        className="wb-input"
        placeholder="⌾ surface an ETF — type a ticker (LIT, URA, SMH…)"
        aria-label="surface ETF ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") surface();
        }}
      />
      <button
        type="button"
        className="wb-mini"
        onClick={surface}
        disabled={!ticker.trim() || resolveEtf.isPending}
      >
        {resolveEtf.isPending ? "surfacing…" : "surface ETF"}
      </button>
      {resolveEtf.isError && (
        <div className="note err">couldn't surface — {errText(resolveEtf.error)}; try again</div>
      )}
    </div>
  );
}
