import { useState } from "react";

import type { DashboardProps } from "../registry";
import MapTab from "./MapTab";
import SectorsTab from "./SectorsTab";
import VennTab from "./VennTab";
import { DEFAULT_THRESHOLD, LIMITS_LINE, defaultVenn, pairVenn, workLine } from "./mapView";
import "./overlap-map.css";
import { useOverlapMap } from "./useOverlapMap";

// Overlap Map (docs/dashboards/overlap-map.md): where the cards overlap, in three views of one read: the Map (cards
// as bubbles joined by the companies they share), the Venn (any two or three sets) and the Sectors (each card's
// sector mix). Structure only, display only. Every count, group and position is computed server-side; this page
// picks the view and draws it (inline SVG, no chart library).

type Tab = "map" | "venn" | "sectors";
const TABS: readonly { key: Tab; label: string }[] = [
  { key: "map", label: "Map" },
  { key: "venn", label: "Venn" },
  { key: "sectors", label: "Sectors" },
];

export default function OverlapMap({ asof, onSelectThesis }: DashboardProps) {
  const [tab, setTab] = useState<Tab>("map");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLD);
  const [showShared, setShowShared] = useState(false);
  const [picks, setPicks] = useState<string[] | null>(null); // null = the default pair
  const q = useOverlapMap(asof, includeSilent);
  const data = q.data;

  if (q.isLoading) {
    return <div className="dash-note">Reading the cards and laying out the map — the first load after a run or a basket edit can take a few seconds.</div>;
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Overlap Map.</div>;

  const venn = picks ?? defaultVenn(data);
  const openVenn = (a: number, b: number) => {
    setPicks(pairVenn(data, a, b));
    setTab("venn");
  };

  return (
    <div className="om">
      <div className="om-top">
        <div className="om-tabs" role="tablist" aria-label="Views">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`om-tab-${t.key}`}
              aria-selected={tab === t.key}
              aria-controls="om-view"
              className={`om-tab${tab === t.key ? " on" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="om-check" title="Add the Context, ETFs and Bench memberships, which never count toward a call">
          <input type="checkbox" checked={includeSilent} onChange={(e) => setIncludeSilent(e.target.checked)} />
          Include Context, ETFs and Bench
        </label>
      </div>
      <div id="om-view" role="tabpanel" aria-labelledby={`om-tab-${tab}`}>
        {tab === "map" && (
          <MapTab
            d={data}
            threshold={threshold}
            onThreshold={setThreshold}
            showShared={showShared}
            onShowShared={setShowShared}
            onSelectThesis={onSelectThesis}
            onOpenVenn={openVenn}
          />
        )}
        {tab === "venn" && <VennTab d={data} picks={venn} onPicks={setPicks} onSelectThesis={onSelectThesis} />}
        {tab === "sectors" && <SectorsTab d={data} onSelectThesis={onSelectThesis} />}
      </div>
      <div className="om-quiet om-work" data-testid="om-work">
        {workLine(data)}
      </div>
      <div className="om-quiet">{LIMITS_LINE}</div>
    </div>
  );
}
