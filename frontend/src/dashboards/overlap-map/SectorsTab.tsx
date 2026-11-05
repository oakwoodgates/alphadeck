import { useMemo, useState } from "react";

import type { DashboardProps } from "../registry";
import { buildSectorGrid, cellRadius, sectorLabel, type SectorMode } from "./sectorGrid";
import type { OverlapMapOut } from "./useOverlapMap";

// The Sectors tab (docs/dashboards/overlap-map.md): a grid of the counted cards by supersector (or business type),
// a gray bubble per cell sized by the card's companies there. Rows sit by the stated rule in sectorGrid.ts.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const MAX_R = 16;

export default function SectorsTab({
  d,
  onSelectThesis,
}: {
  d: OverlapMapOut;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const [mode, setMode] = useState<SectorMode>("supersector");
  const [cell, setCell] = useState<{ card: number; key: string } | null>(null);
  const grid = useMemo(
    () =>
      buildSectorGrid(
        d.cards.map((c) => c.name),
        d.companies,
        mode,
      ),
    [d, mode],
  );
  const biggest = useMemo(
    () => grid.rows.reduce((m, r) => Math.max(m, ...Object.values(r.cells).map((v) => v.length), 0), 0),
    [grid],
  );
  const open = cell && cell.card < d.cards.length ? cell : null;
  const openIds = open ? (grid.rows.find((r) => r.card === open.card)?.cells[open.key] ?? []) : [];

  return (
    <div className="om-sectors">
      <div className="om-controls">
        <div className="om-seg" role="group" aria-label="Columns">
          <span className="om-seg-label">Columns</span>
          {(
            [
              ["supersector", "Supersectors"],
              ["business_type", "Business types"],
            ] as const
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              className={`om-seg-btn${mode === m ? " on" : ""}`}
              aria-pressed={mode === m}
              onClick={() => {
                setMode(m);
                setCell(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="om-quiet">
          Rows: cards with a similar mix sit together (by each card's largest column, then its share of the card).
        </span>
      </div>
      <div className="om-grid-scroll">
        <table className="om-grid" data-testid="om-grid">
          <thead>
            <tr>
              <th className="om-grid-card" scope="col">
                Card
              </th>
              {grid.columns.map((c) => (
                <th key={c.key} scope="col" className="om-grid-col" data-testid={`om-col-${c.key || "unclassified"}`}>
                  <div className="om-grid-col-label">{c.label}</div>
                  <div className="om-dim">
                    {plural(c.cards, "card", "cards")} · {plural(c.companies, "company", "companies")}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((r) => (
              <tr key={d.cards[r.card].thesis_id} data-testid={`om-row-${r.card}`}>
                <th scope="row" className="om-grid-card">
                  <button type="button" className="om-link" onClick={() => onSelectThesis(d.cards[r.card].thesis_id)}>
                    {d.cards[r.card].name}
                  </button>
                  <div className="om-dim">{plural(r.sectors, "sector", "sectors")}</div>
                </th>
                {grid.columns.map((c) => {
                  const n = r.cells[c.key]?.length ?? 0;
                  const on = open?.card === r.card && open.key === c.key;
                  return (
                    <td key={c.key} className="om-grid-cell">
                      {n > 0 && (
                        <button
                          type="button"
                          className={`om-cell${on ? " on" : ""}`}
                          data-testid={`om-cell-${r.card}-${c.key || "unclassified"}`}
                          title={`${d.cards[r.card].name} · ${c.label}: ${plural(n, "company", "companies")}`}
                          aria-label={`${d.cards[r.card].name}, ${c.label}: ${plural(n, "company", "companies")}`}
                          onClick={() => setCell(on ? null : { card: r.card, key: c.key })}
                        >
                          <svg width={2 * MAX_R + 2} height={2 * MAX_R + 2} aria-hidden="true">
                            <circle cx={MAX_R + 1} cy={MAX_R + 1} r={cellRadius(n, biggest, MAX_R)} />
                          </svg>
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {open && (
        <div className="om-panel om-cell-list" data-testid="om-cell-list">
          <div className="om-panel-head">
            <span className="om-panel-name">
              {d.cards[open.card].name} · {sectorLabel(open.key, mode)}
            </span>
            <span className="om-dim">{plural(openIds.length, "company", "companies")}</span>
          </div>
          <div className="om-tickers">
            {openIds.map((idx) => {
              const co = d.companies[idx];
              const row = co.rows.find((x) => x.card === open.card);
              return (
                <button
                  key={co.security_id}
                  type="button"
                  className="om-ticker"
                  title={co.name ?? co.ticker}
                  onClick={() => onSelectThesis(d.cards[open.card].thesis_id, row?.ticker ?? co.ticker)}
                >
                  {co.ticker}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
