import { useState } from "react";

import type { BasketMember, SecurityMatchOut } from "../api/hooks";
import { useResolveSecurities } from "../api/hooks";
import { ARCHETYPES, archLabel } from "./format";

interface Props {
  existingKeys: Set<string>; // security_ids already in the basket (disable re-adding)
  onAdd: (m: BasketMember) => void;
}

/** Add a name to the basket via the resolver typeahead (Slice 4b): search the master (a discovery net),
 *  pick an EXACT row, classify it (archetype + role), and add it as an `operator_set` placement (the
 *  server re-stamps `operator_set` on save). No match → the honest "ingest first" note; never a guess. */
export function AddName({ existingKeys, onAdd }: Props) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<SecurityMatchOut | null>(null);
  const [archetype, setArchetype] = useState<string>("high_beta");
  const [role, setRole] = useState("");
  const results = useResolveSecurities(picked ? "" : q);
  const matches = results.data ?? [];

  const reset = () => {
    setQ("");
    setPicked(null);
    setRole("");
    setArchetype("high_beta");
  };

  const add = () => {
    if (!picked) return;
    onAdd({
      ticker: picked.ticker,
      role: role.trim() || "—",
      archetype: archetype as BasketMember["archetype"],
      security_id: picked.security_id,
      segment: null, // starts unplaced; the operator places it via the row's segment select
      conviction: null, // unset until the operator weights it in the row
      authored_by: "operator_set",
    });
    reset();
  };

  if (picked) {
    return (
      <div className="wb-addname picked">
        <span className="tk">{picked.ticker}</span>
        <select
          className="wb-input"
          value={archetype}
          aria-label="archetype"
          onChange={(e) => setArchetype(e.target.value)}
        >
          {ARCHETYPES.map((a) => (
            <option key={a} value={a}>
              {archLabel(a)}
            </option>
          ))}
        </select>
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
