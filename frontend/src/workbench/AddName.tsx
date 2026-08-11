import { useState } from "react";

import type { BasketMember, SecurityMatchOut } from "../api/hooks";
import { useResolveSecurities } from "../api/hooks";

interface Props {
  existingKeys: Set<string>; // security_ids already in the basket (disable re-adding)
  onAdd: (m: BasketMember, name?: string | null) => void; // name → the display-only security_id→name bridge
}

/** Add a name to the basket via the resolver typeahead (Slice 4b): search the master (a discovery net),
 *  pick an EXACT row, give it a role, and add it AUTO-SIGNED-OFF (the operator picked the name
 *  deliberately — the confidence ladder's top rung) but `system_drafted`: its description is honestly
 *  "model draft" until the operator TYPES one (never a false "your words"). No match → the honest
 *  "ingest first" note; never a guess. Placement never characterizes (item F): what the name IS (its
 *  business type) derives from the master's identity — it needs nothing stamped here. */
export function AddName({ existingKeys, onAdd }: Props) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<SecurityMatchOut | null>(null);
  const [role, setRole] = useState("");
  const results = useResolveSecurities(picked ? "" : q);
  const matches = results.data ?? [];

  const reset = () => {
    setQ("");
    setPicked(null);
    setRole("");
  };

  const add = () => {
    if (!picked) return;
    onAdd(
      {
        ticker: picked.ticker,
        role: role.trim() || "—",
        security_id: picked.security_id,
        segment: null, // starts unplaced (segment sorting lives on the triage screen now)
        conviction: null, // stored metadata; no control on this surface
        authored_by: "system_drafted", // no description typed yet — honestly a model draft (S1)
        signed_off: true, // a hand-add IS the endorsement (the ladder's top rung, auto)
      },
      picked.name, // feed the name bridge so the placed row shows it (BasketMember carries no name)
    );
    reset();
  };

  if (picked) {
    return (
      <div className="wb-addname picked">
        <span className="tk">{picked.ticker}</span>
        <input
          className="wb-input"
          placeholder="role in the thesis"
          aria-label="role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        />
        <button type="button" className="wb-mini" onClick={add}>
          add to basket
        </button>
        <button type="button" className="wb-mini ghost" onClick={reset}>
          cancel
        </button>
      </div>
    );
  }

  return (
    <div className="wb-addname">
      <input
        className="wb-input"
        placeholder="＋ add a name — search the security master…"
        aria-label="search securities"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {matches.length > 0 && (
        <ul className="wb-matches">
          {matches.map((s) => {
            const inBasket = existingKeys.has(s.security_id);
            return (
              <li key={s.security_id}>
                <button
                  type="button"
                  disabled={inBasket}
                  onClick={() => {
                    setPicked(s);
                  }}
                >
                  <b>{s.ticker}</b>
                  {s.cik ? <span className="cik">CIK {s.cik}</span> : null}
                  {s.name ? <span className="co">{s.name}</span> : null}
                  {inBasket ? <span className="muted"> · in basket</span> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {q.trim().length > 0 && !results.isFetching && matches.length === 0 && (
        <div className="note">
          No match — a name must be in the security master to place it (ingestion populates the master;
          that is a separate step from authoring).
        </div>
      )}
    </div>
  );
}
