import { useMemo, useState } from "react";

import type { DashboardProps } from "../registry";
import {
  THRESHOLDS,
  groupsWords,
  lineWidth,
  moveFocus,
  nameFits,
  neighborsOf,
  pairWords,
  sharedDots,
  visiblePairs,
  type ArrowKey,
  type Dot,
} from "./mapView";
import type { OverlapMapOut, OverlapCompanyOut } from "./useOverlapMap";

// The Map tab (docs/dashboards/overlap-map.md): cards as gray bubbles (area = the card's companies) at the server's
// layout, a line between two cards sharing at least N companies, optional dots for the companies on 2+ cards, and a
// panel for the clicked card. The threshold only hides lines; no bubble ever moves.

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

const linkWords = (links: readonly (string | null)[]) => links.map((l) => l ?? "no link").join(", ");

function DotDetail({
  d,
  co,
  pinned,
  onSelect,
}: {
  d: OverlapMapOut;
  co: OverlapCompanyOut;
  pinned: boolean;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  return (
    <div className="om-readout om-dot-detail" data-testid="om-readout">
      <span className="om-readout-name">{co.ticker}</span>
      {co.name && <span>{co.name}</span>}
      {co.rows.map((r) =>
        pinned ? (
          <button
            key={r.card}
            type="button"
            className="om-chip"
            onClick={() => onSelect(d.cards[r.card].thesis_id, r.ticker)}
          >
            {d.cards[r.card].name} · {linkWords(r.links)}
          </button>
        ) : (
          <span key={r.card} className="om-chip static">
            {d.cards[r.card].name} · {linkWords(r.links)}
          </span>
        ),
      )}
    </div>
  );
}

function Panel({
  d,
  card,
  onSelect,
  onOpenVenn,
}: {
  d: OverlapMapOut;
  card: number;
  onSelect: DashboardProps["onSelectThesis"];
  onOpenVenn: (a: number, b: number) => void;
}) {
  const c = d.cards[card];
  const nbs = useMemo(() => neighborsOf(d, card), [d, card]);
  return (
    <aside className="om-panel" data-testid="om-panel" aria-label={`${c.name}: shared names`}>
      <div className="om-panel-head">
        <button type="button" className="om-link om-panel-name" onClick={() => onSelect(c.thesis_id)}>
          {c.name}
        </button>
        <span className="om-dim">{plural(c.companies, "company", "companies")}</span>
      </div>
      {nbs.length === 0 ? (
        <div className="om-dim">Shares no names with any other card in view.</div>
      ) : (
        <>
          <div className="om-panel-title">Shares names with:</div>
          <ul className="om-nbs">
            {nbs.map((nb) => {
              const other = d.cards[nb.card];
              return (
                <li key={nb.card} className="om-nb" data-testid={`om-nb-${nb.card}`}>
                  <div className="om-nb-head">
                    <button type="button" className="om-link" onClick={() => onSelect(other.thesis_id)}>
                      {other.name}
                    </button>
                    <span className="om-num">{plural(nb.shared, "name", "names")}</span>
                    <button type="button" className="om-btn" onClick={() => onOpenVenn(card, nb.card)}>
                      Open in Venn
                    </button>
                  </div>
                  <div className="om-tickers">
                    {nb.companies.map((idx) => {
                      const co = d.companies[idx];
                      const row = co.rows.find((r) => r.card === nb.card);
                      return (
                        <button
                          key={co.security_id}
                          type="button"
                          className="om-ticker"
                          title={co.name ?? co.ticker}
                          onClick={() => onSelect(other.thesis_id, row?.ticker ?? co.ticker)}
                        >
                          {co.ticker}
                        </button>
                      );
                    })}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </aside>
  );
}

export default function MapTab({
  d,
  threshold,
  onThreshold,
  showShared,
  onShowShared,
  onSelectThesis,
  onOpenVenn,
}: {
  d: OverlapMapOut;
  threshold: number;
  onThreshold: (n: number) => void;
  showShared: boolean;
  onShowShared: (v: boolean) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
  onOpenVenn: (a: number, b: number) => void;
}) {
  const [pick, setSelected] = useState<number | null>(null);
  // a pick from an earlier as-of may name a card index this payload does not have
  const selected = pick != null && pick < d.cards.length ? pick : null;
  const [focus, setFocus] = useState<number | null>(null);
  const [hoverCard, setHoverCard] = useState<number | null>(null);
  const [hoverPair, setHoverPair] = useState<string | null>(null);
  const [hoverDot, setHoverDot] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);

  const lines = useMemo(() => visiblePairs(d, threshold), [d, threshold]);
  const dots = useMemo<Dot[]>(() => (showShared ? sharedDots(d) : []), [d, showShared]);
  const placed = useMemo(() => d.cards.map((c, i) => ({ c, i })).filter(({ c }) => c.x != null), [d]);
  const strip = useMemo(() => d.cards.map((c, i) => ({ c, i })).filter(({ c }) => c.shares_nothing), [d]);

  const dotCompany = pinned ?? hoverDot;
  const dotShown = dotCompany != null ? dots.find((x) => x.company === dotCompany) : undefined;
  const linkOn = hoverPair != null ? lines.find((p) => `${p.a}-${p.b}` === hoverPair) : undefined;
  const cardOn = hoverCard ?? focus;

  const readout = dotShown ? (
    <DotDetail d={d} co={d.companies[dotShown.company]} pinned={pinned === dotShown.company} onSelect={onSelectThesis} />
  ) : (
    <div className="om-readout" data-testid="om-readout">
      {linkOn
        ? pairWords(d, linkOn)
        : cardOn != null
          ? `${d.cards[cardOn].name}: ${plural(d.cards[cardOn].companies, "company", "companies")}`
          : "Hover a bubble, line or dot for its numbers; click a bubble to see who it shares names with."}
    </div>
  );

  const firstFocus = () =>
    placed.reduce((best, x) => (x.c.companies > d.cards[best].companies ? x.i : best), placed[0]?.i ?? 0);
  const onKey = (e: React.KeyboardEvent) => {
    if (placed.length === 0) return;
    if (e.key.startsWith("Arrow")) {
      e.preventDefault();
      setFocus((f) => (f == null ? firstFocus() : moveFocus(d, f, e.key as ArrowKey)));
    } else if ((e.key === "Enter" || e.key === " ") && focus != null) {
      e.preventDefault();
      setSelected(focus);
    } else if (e.key === "Escape") {
      setSelected(null);
      setPinned(null);
    }
  };

  const connectors = dotShown ? dotShown.cards.map((c) => d.cards[c]) : [];

  return (
    <div className="om-map">
      <div className="om-controls">
        <div className="om-seg" role="group" aria-label="Lines at">
          <span className="om-seg-label">Lines at</span>
          {THRESHOLDS.map((n) => (
            <button
              key={n}
              type="button"
              className={`om-seg-btn${threshold === n ? " on" : ""}`}
              aria-pressed={threshold === n}
              onClick={() => onThreshold(n)}
            >
              {n === 5 ? "5+" : n}
            </button>
          ))}
          <span className="om-seg-label">shared names</span>
        </div>
        <label className="om-check" title="A small dot for each company on 2+ cards, at the center of its cards">
          <input type="checkbox" checked={showShared} onChange={(e) => onShowShared(e.target.checked)} />
          Show shared companies
        </label>
      </div>
      <div className="om-quiet" data-testid="om-groups">
        {groupsWords(d, threshold)}
      </div>
      <div className="om-body">
        <div className="om-plot-wrap">
          {readout}
          {placed.length === 0 ? (
            <div className="dash-note">No two cards in view share a name, so there is nothing to draw.</div>
          ) : (
            <svg
              className="om-plot"
              viewBox={`0 0 ${d.box_w} ${d.box_h}`}
              role="group"
              tabIndex={0}
              aria-label="Card overlap map. Arrow keys move between cards; Enter selects one."
              onKeyDown={onKey}
              onFocus={() => setFocus((f) => f ?? firstFocus())}
              onBlur={() => setFocus(null)}
              onClick={() => setPinned(null)}
            >
              <g className="om-lines">
                {lines.map((p) => {
                  const a = d.cards[p.a];
                  const b = d.cards[p.b];
                  const key = `${p.a}-${p.b}`;
                  return (
                    <g
                      key={key}
                      className={`om-line-g${hoverPair === key ? " hover" : ""}`}
                      data-testid={`om-line-${key}`}
                      onMouseEnter={() => setHoverPair(key)}
                      onMouseLeave={() => setHoverPair(null)}
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenVenn(p.a, p.b);
                      }}
                    >
                      <line className="om-line" x1={a.x!} y1={a.y!} x2={b.x!} y2={b.y!} strokeWidth={lineWidth(p.shared)} />
                      <line className="om-line-hit" x1={a.x!} y1={a.y!} x2={b.x!} y2={b.y!} />
                    </g>
                  );
                })}
              </g>
              <g className="om-connectors">
                {connectors.map((c, i) => (
                  <line key={i} className="om-connector" x1={dotShown!.x} y1={dotShown!.y} x2={c.x!} y2={c.y!} />
                ))}
              </g>
              <g className="om-bubbles">
                {placed.map(({ c, i }) => {
                  const on = i === selected;
                  const lit = i === hoverCard || i === focus;
                  return (
                    <g
                      key={c.thesis_id}
                      className={`om-bubble${on ? " selected" : ""}${lit ? " hover" : ""}`}
                      data-testid={`om-bubble-${i}`}
                      onMouseEnter={() => setHoverCard(i)}
                      onMouseLeave={() => setHoverCard(null)}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelected(i);
                      }}
                    >
                      <circle cx={c.x!} cy={c.y!} r={c.r} />
                      {(nameFits(c.name, c.r) || lit) && (
                        <text x={c.x!} y={c.y!} textAnchor="middle" dominantBaseline="central" className="om-bubble-label">
                          {c.name}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
              <g className="om-dots">
                {dots.map((x) => (
                  <circle
                    key={x.company}
                    className={`om-dot${x.company === dotCompany ? " on" : ""}`}
                    data-testid={`om-dot-${x.company}`}
                    cx={x.x}
                    cy={x.y}
                    r={3}
                    onMouseEnter={() => setHoverDot(x.company)}
                    onMouseLeave={() => setHoverDot(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      setPinned((p) => (p === x.company ? null : x.company));
                    }}
                  />
                ))}
              </g>
            </svg>
          )}
          {strip.length > 0 && (
            <div className="om-strip" data-testid="om-strip">
              <span className="om-strip-title">Shares no names</span>
              {strip.map(({ c, i }) => (
                <button
                  key={c.thesis_id}
                  type="button"
                  className={`om-strip-item${selected === i ? " selected" : ""}`}
                  onClick={() => setSelected(i)}
                  title={`${plural(c.companies, "company", "companies")}`}
                >
                  <span className="om-strip-dot" style={{ width: Math.min(18, Math.max(6, c.r)), height: Math.min(18, Math.max(6, c.r)) }} aria-hidden="true" />
                  {c.name}
                </button>
              ))}
            </div>
          )}
        </div>
        {selected != null ? (
          <Panel d={d} card={selected} onSelect={onSelectThesis} onOpenVenn={onOpenVenn} />
        ) : (
          <aside className="om-panel om-dim" data-testid="om-panel">
            Click a bubble to list the cards it shares names with.
          </aside>
        )}
      </div>
    </div>
  );
}
