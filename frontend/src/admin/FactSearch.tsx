import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { useResolveSecurities } from "../api/hooks";
import { companyFactsPath } from "../nav";

// The company search box (V2 W4b): type a ticker or name, pick a match, land on that company's page. It reads the
// whole company list the Workbench's add-a-name box reads (a former ticker matches too) and writes nothing. An empty
// box asks for nothing; a typing pause of 180 ms is waited out before the server is asked.

const SETTLE_MS = 180;

function useSettled(value: string, ms: number): string {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function FactSearch({ asof }: { asof: string | null }) {
  const [text, setText] = useState("");
  const navigate = useNavigate();
  const typed = text.trim();
  const settled = useSettled(typed, SETTLE_MS);
  const q = useResolveSecurities(settled);
  const current = typed !== "" && settled === typed;
  const matches = current ? (q.data ?? []) : [];
  return (
    <div className="cf-search">
      <label className="cf-field wide">
        Find a company (ticker or name)
        <input
          type="text"
          autoComplete="off"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      {matches.length > 0 && (
        <ul className="cf-matches">
          {matches.map((m) => (
            <li key={m.security_id}>
              <button
                type="button"
                className="adm-minibtn"
                onClick={() => navigate(companyFactsPath(m.security_id, asof))}
              >
                <b>{m.ticker}</b> {m.name ?? ""}
                {m.instrument_kind === "etf" && <span className="cf-fund"> fund</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {current && !q.isFetching && q.data && matches.length === 0 && (
        <div className="cf-quiet">No company matches.</div>
      )}
    </div>
  );
}
