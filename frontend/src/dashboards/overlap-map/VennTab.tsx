import { useMemo, useState } from "react";

import type { DashboardProps } from "../registry";
import { SET_KINDS, SET_KIND_LABEL, setDefs, validPicks, type SetDef } from "./mapView";
import { maskAt, overlapsOf, vennLayout, vennRegions } from "./vennLayout";
import type { OverlapMapOut } from "./useOverlapMap";

// The Venn tab (docs/dashboards/overlap-map.md): any two or three sets from one searchable picker (a card, a link on
// a card, a supersector or a business type), circle area proportional to the set, every region's count EXACT and
// clickable to list its companies.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const TINTS = ["var(--txt-4)", "var(--txt-3)", "var(--txt-2)"] as const;
const PER_KIND = 30;

const linkWords = (links: readonly (string | null)[]) => links.map((l) => l ?? "no link").join(", ");

function Picker({
  defs,
  picked,
  onAdd,
}: {
  defs: readonly SetDef[];
  picked: readonly string[];
  onAdd: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const full = picked.length >= 3;
  const needle = q.trim().toLowerCase();
  const left = defs.filter((s) => !picked.includes(s.key) && (!needle || s.label.toLowerCase().includes(needle)));
  return (
    <div className="om-picker">
      <button
        type="button"
        className="om-btn"
        disabled={full}
        aria-expanded={open}
        title={full ? "Three sets are the most a Venn draws: remove one first" : "Add a third set, or swap one"}
        onClick={() => setOpen((o) => !o)}
      >
        Add a set
      </button>
      {open && !full && (
        <div className="om-picker-list" data-testid="om-picker-list">
          <input
            type="search"
            className="om-search"
            placeholder="Search cards, links, sectors, business types"
            aria-label="Search the sets"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {SET_KINDS.map((kind) => {
            const rows = left.filter((s) => s.kind === kind);
            if (rows.length === 0) return null;
            return (
              <div key={kind} className="om-picker-group" role="group" aria-label={SET_KIND_LABEL[kind]}>
                <div className="om-picker-kind">{SET_KIND_LABEL[kind]}</div>
                {rows.slice(0, PER_KIND).map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    className="om-picker-item"
                    onClick={() => {
                      onAdd(s.key);
                      setOpen(false);
                      setQ("");
                    }}
                  >
                    {s.label}
                    <span className="om-dim"> · {plural(s.members.length, "company", "companies")}</span>
                  </button>
                ))}
                {rows.length > PER_KIND && (
                  <div className="om-dim">{rows.length - PER_KIND} more: type to narrow</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function VennTab({
  d,
  picks,
  onPicks,
  onSelectThesis,
}: {
  d: OverlapMapOut;
  picks: readonly string[];
  onPicks: (keys: string[]) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const defs = useMemo(() => setDefs(d), [d]);
  const keys = useMemo(() => validPicks(d, defs, picks), [d, defs, picks]);
  const chosen = useMemo(
    () => keys.map((k) => defs.find((s) => s.key === k)).filter((s): s is SetDef => s != null),
    [defs, keys],
  );
  const layout = useMemo(
    () => (chosen.length >= 2 ? vennLayout(overlapsOf(chosen.map((s) => ({ key: s.key, label: s.label, members: s.members })))) : null),
    [chosen],
  );
  const regions = useMemo(
    () => (chosen.length >= 2 ? vennRegions(chosen.map((s) => ({ key: s.key, label: s.label, members: s.members }))) : []),
    [chosen],
  );
  const byId = useMemo(() => new Map(d.companies.map((c) => [c.security_id, c])), [d]);
  const [mask, setMask] = useState<number | null>(null);
  const region = regions.find((r) => r.mask === mask) ?? null;

  if (!layout || chosen.length < 2) {
    return <div className="dash-note">A Venn needs at least two cards in view.</div>;
  }

  const setPicks = (next: string[]) => {
    setMask(null);
    onPicks(next);
  };

  return (
    <div className="om-venn">
      <div className="om-legend" role="list" aria-label="The sets">
        {chosen.map((s, i) => (
          <span key={s.key} className="om-legend-item" role="listitem">
            <span className="om-swatch" style={{ background: TINTS[i] }} aria-hidden="true" />
            {s.label}
            <span className="om-dim"> · {plural(s.members.length, "company", "companies")}</span>
            <button
              type="button"
              className="om-x"
              aria-label={`Remove ${s.label}`}
              disabled={chosen.length <= 2}
              onClick={() => setPicks(keys.filter((k) => k !== s.key))}
            >
              ×
            </button>
          </span>
        ))}
        <Picker defs={defs} picked={keys} onAdd={(k) => setPicks([...keys, k])} />
      </div>
      <div className="om-quiet">
        {chosen.length === 3
          ? "Areas are approximate for three sets; the counts are exact."
          : "Circle area is proportional to the set; the counts are exact."}
        {layout.minRadiusApplied &&
          " One set is more than 50 times another: the small circle is drawn at a minimum size, so its area is not to scale."}
      </div>
      <div className="om-body">
        <svg
          className="om-venn-svg"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="group"
          aria-label={`Venn diagram of ${chosen.map((s) => s.label).join(", ")}`}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            if (!(r.width > 0)) return;
            const x = ((e.clientX - r.left) / r.width) * layout.width;
            const y = ((e.clientY - r.top) / r.height) * layout.height;
            const m = maskAt(layout.circles, x, y);
            setMask(m === 0 ? null : m);
          }}
        >
          {layout.circles.map((c, i) => (
            <circle key={chosen[i].key} className="om-venn-circle" cx={c.cx} cy={c.cy} r={c.r} fill={TINTS[i]} />
          ))}
          {regions.map((r) => {
            const a = layout.anchors[r.mask];
            return (
              <text
                key={r.mask}
                className={`om-venn-count${mask === r.mask ? " on" : ""}`}
                data-testid={`om-region-${r.mask}`}
                x={a.x}
                y={a.y}
                textAnchor="middle"
                dominantBaseline="central"
                role="button"
                tabIndex={0}
                aria-label={`${r.words}: ${plural(r.count, "company", "companies")}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setMask(r.mask);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setMask(r.mask);
                  }
                }}
              >
                {r.count}
              </text>
            );
          })}
        </svg>
        <aside className="om-panel" data-testid="om-region-list" aria-live="polite">
          {region ? (
            <>
              <div className="om-panel-head">
                <span className="om-panel-name">{region.words}</span>
                <span className="om-dim">{plural(region.count, "company", "companies")}</span>
              </div>
              {region.count === 0 && <div className="om-dim">No company is only here.</div>}
              <ul className="om-co-list">
                {region.ids.map((id) => {
                  const co = byId.get(id);
                  if (!co) return null;
                  return (
                    <li key={id} className="om-co">
                      <span className="om-co-id">
                        <span className="om-co-ticker">{co.ticker}</span>
                        {co.name && <span className="om-dim"> {co.name}</span>}
                      </span>
                      <span className="om-co-chips">
                        {co.rows.map((r) => (
                          <button
                            key={r.card}
                            type="button"
                            className="om-chip"
                            onClick={() => onSelectThesis(d.cards[r.card].thesis_id, r.ticker)}
                          >
                            {d.cards[r.card].name} · {linkWords(r.links)}
                          </button>
                        ))}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <div className="om-dim">Click a region, or its count, to list its companies.</div>
          )}
        </aside>
      </div>
    </div>
  );
}
