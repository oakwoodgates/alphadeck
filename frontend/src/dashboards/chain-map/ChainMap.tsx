import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { contextRoleTag } from "../../workbench/format";
import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./chain-map.css";
import {
  NODE_H,
  NODE_W,
  clip,
  layoutMap,
  moveFocus,
  sideGlyph,
  type Dir,
  type MapLayout,
  type PlacedNode,
  type Rect,
} from "./mapLayout";
import {
  AUTHOR_TAG,
  ageGlyph,
  columnTag,
  filterCards,
  filingWords,
  fmtDay,
  linkWords,
  nodeWords,
  plural,
  roleWords,
  sideWords,
  typeLabel,
  workLine,
} from "./mapView";
import { useChainMap, useChainMapCards, type ChainMapNodeOut, type ChainMapOut } from "./useChainMap";

// Chain Map (docs/dashboards/chain-map.md): ONE card as a picture. Its links in chain order are columns, the
// companies on the roster at the as-of are the nodes inside them, and the role-tagged companies stand on two
// rails beside the chain, each with one edge to the chain's bracket (a role names a part, not a target). Plain
// inline SVG laid out by the pure mapLayout.ts; no graph library. Display only. Every number (the placement, the
// 50-day side, the filing age, the counts) is computed server-side; this page picks the card and the toggle,
// lays the picture out and words it. The picture is one tab stop and the arrow keys move between names.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";
const SLOW = "Reading this card's names — the first load after a run or a basket edit can take a moment.";
const PROMPT =
  "Hover or focus a name (the arrow keys move between names) for its link, role, description and last filing. Click opens it in the Cockpit.";
const NARROW = "(max-width: 640px)";

/** A phone-width screen stacks the rails above and below the chain. jsdom has no matchMedia: the wide layout. */
function useNarrow(): boolean {
  const has = typeof window !== "undefined" && typeof window.matchMedia === "function";
  const [narrow, setNarrow] = useState(() => (has ? window.matchMedia(NARROW).matches : false));
  useEffect(() => {
    if (!has) return;
    const mq = window.matchMedia(NARROW);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [has]);
  return narrow;
}

const ARROWS: Record<string, Dir> = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };

// --- the readout -----------------------------------------------------------------------------------------------

function Readout({ node, filingDays }: { node: ChainMapNodeOut | null; filingDays: number }) {
  if (!node) {
    return (
      <div className="cm-readout" data-testid="cm-readout" aria-live="polite">
        <span className="cm-prompt">{PROMPT}</span>
      </div>
    );
  }
  const type = typeLabel(node);
  return (
    <div className="cm-readout" data-testid="cm-readout" aria-live="polite">
      <div className="cm-ro-head">
        <span className="cm-ro-tk">{node.ticker}</span>
        {node.name && <span className="cm-ro-name">{node.name}</span>}
        <span className="cm-n">{linkWords(node.links)}</span>
        {node.role && <span className="cm-ro-role">{contextRoleTag(node.role)}</span>}
      </div>
      {node.role && <div className="cm-ro-line">{roleWords(node)}</div>}
      <div className="cm-ro-line">
        {[type || null, sideWords(node), filingWords(node, filingDays)].filter(Boolean).join(" · ")}
      </div>
      {node.thesis_fit && (
        <p className="cm-ro-fit">
          {node.thesis_fit}{" "}
          <span className="cm-n">({AUTHOR_TAG[node.authored_by ?? "system_drafted"]})</span>
        </p>
      )}
      {node.matched_terms.length > 0 && (
        <div className="cm-ro-line">Matched terms: {node.matched_terms.join(", ")}</div>
      )}
    </div>
  );
}

// --- the picture -----------------------------------------------------------------------------------------------

const railLabel = (d: ChainMapOut, side: "left" | "right") => d.rails.find((r) => r.side === side)?.label ?? "";

function NodeShape({
  p,
  tabbable,
  onHover,
  onFocusNode,
  onBlurNode,
  onOpen,
  onKey,
}: {
  p: PlacedNode;
  tabbable: boolean;
  onHover: (key: string | null) => void;
  onFocusNode: (key: string) => void;
  onBlurNode: (key: string) => void;
  onOpen: (n: ChainMapNodeOut) => void;
  onKey: (e: KeyboardEvent<SVGGElement>, key: string, n: ChainMapNodeOut) => void;
}) {
  const n = p.node;
  const label = nodeWords(n);
  const type = typeLabel(n);
  const resolved = n.security_id != null;
  const cls = `cm-node${n.flag != null ? " flagged" : ""}${resolved ? "" : " unresolved"}${n.role ? " tagged" : ""}`;
  return (
    <g
      className={cls}
      role="button"
      tabIndex={tabbable ? 0 : -1}
      aria-label={label}
      data-key={p.key}
      data-testid="cm-node"
      transform={`translate(${p.x} ${p.y})`}
      onMouseEnter={() => onHover(p.key)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onFocusNode(p.key)}
      onBlur={() => onBlurNode(p.key)}
      onClick={() => onOpen(n)}
      onKeyDown={(e) => onKey(e, p.key, n)}
    >
      <title>{label}</title>
      <rect className="cm-node-box" width={NODE_W} height={NODE_H} rx={5} />
      <text className="cm-tk" x={8} y={17}>
        {clip(n.ticker, 9)}
      </text>
      {n.role && (
        <text className="cm-role" x={NODE_W - 8} y={16} textAnchor="end">
          {contextRoleTag(n.role)}
        </text>
      )}
      {resolved ? (
        <>
          {type && (
            <text className="cm-type" x={8} y={35}>
              {clip(type, 15)}
            </text>
          )}
          {n.side_50d && <path className="cm-side" d={sideGlyph(n.side_50d, NODE_W - 46, 32)} />}
          <text className="cm-age" x={NODE_W - 8} y={35} textAnchor="end">
            {ageGlyph(n)}
          </text>
        </>
      ) : (
        <text className="cm-type" x={8} y={35}>
          no resolved security
        </text>
      )}
    </g>
  );
}

function ZoneSvg({
  rect,
  label,
  className,
  children,
}: {
  rect: Rect;
  label: string;
  className: string;
  children: ReactNode;
}) {
  return (
    <div className={`cm-zone ${className}`}>
      <svg
        role="group"
        aria-label={label}
        width={rect.w}
        height={rect.h}
        viewBox={`${rect.x} ${rect.y} ${rect.w} ${rect.h}`}
      >
        {children}
      </svg>
    </div>
  );
}

function Picture({
  data,
  layout,
  onSelectThesis,
}: {
  data: ChainMapOut;
  layout: MapLayout;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const frame = useRef<HTMLDivElement>(null);
  const keys = useMemo(() => layout.groups.flatMap((g) => g.keys), [layout]);
  const byKey = useMemo(() => new Map(layout.nodes.map((n) => [n.key, n])), [layout]);
  const [active, setActive] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const tab = active != null && keys.includes(active) ? active : (keys[0] ?? null);
  const shownKey = hover ?? focused;
  const shown = shownKey != null ? (byKey.get(shownKey)?.node ?? null) : null;

  const focusNode = (key: string) => {
    const el = Array.from(frame.current?.querySelectorAll<SVGGElement>("[data-key]") ?? []).find(
      (e) => e.getAttribute("data-key") === key,
    );
    el?.focus();
  };
  const onKey = (e: KeyboardEvent<SVGGElement>, key: string, n: ChainMapNodeOut) => {
    const dir = ARROWS[e.key];
    if (dir) {
      e.preventDefault();
      const next = moveFocus(layout.groups, key, dir);
      setActive(next);
      focusNode(next);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelectThesis(data.thesis_id, n.ticker);
    }
  };
  const node = (p: PlacedNode) => (
    <NodeShape
      key={p.key}
      p={p}
      tabbable={p.key === tab}
      onHover={setHover}
      onFocusNode={(k) => {
        setActive(k);
        setFocused(k);
      }}
      onBlurNode={(k) => setFocused((cur) => (cur === k ? null : cur))}
      onOpen={(n) => onSelectThesis(data.thesis_id, n.ticker)}
      onKey={onKey}
    />
  );
  const railPart = (s: "left" | "right") => (
    <>
      {layout.brackets
        .filter((b) => b.side === s)
        .map((b) => (
          <path key={`b-${s}`} className="cm-bracket" d={b.d} />
        ))}
      {layout.edges
        .filter((e) => e.side === s)
        .map((e) => (
          <g key={`e-${e.nodeKey}`} data-testid="cm-edge" data-role={e.role}>
            <path className={`cm-edge${e.dashed ? " dashed" : ""}`} d={e.d} />
            <circle className="cm-end" cx={e.end.x} cy={e.end.y} r={2} />
          </g>
        ))}
      {layout.edgeLabels
        .filter((l) => l.side === s)
        .map((l) => (
          <text key={`l-${l.role}`} className="cm-edge-label" x={l.x} y={l.y} textAnchor="middle">
            {contextRoleTag(l.role)}
          </text>
        ))}
      {layout.rails
        .filter((r) => r.side === s)
        .map((r) => (
          <g key={`h-${s}`} className="cm-rail-head">
            <text x={r.x} y={r.y}>
              {r.label}
            </text>
            <text className="cm-n" x={r.x} y={r.countY}>
              {plural(r.count, "name", "names")}
            </text>
          </g>
        ))}
      {layout.nodes.filter((n) => n.zone === s).map(node)}
    </>
  );

  const { left, chain, right } = layout.zones;
  return (
    <>
      <Readout node={shown} filingDays={data.filing_days} />
      <div ref={frame} className={`cm-frame ${layout.mode}`} data-testid="cm-frame">
        {left && (
          <ZoneSvg rect={left} className="cm-zone-left" label={`Left rail: ${railLabel(data, "left")}`}>
            {railPart("left")}
          </ZoneSvg>
        )}
        <ZoneSvg rect={chain} className="cm-zone-chain" label={`${data.thesis_name}: its links in chain order`}>
          {layout.columns.map((c) => (
            <g key={c.key} className={`cm-col ${c.kind}`} data-testid="cm-col" data-key={c.key}>
              <title>{[c.label, columnTag(c.kind, c.descriptor)].filter(Boolean).join(" · ")}</title>
              <rect className="cm-col-box" x={c.x} y={c.y} width={c.w} height={c.h} rx={6} />
              <text className="cm-col-label" x={c.x + 8} y={c.y + 17}>
                {clip(c.label, 22)}
              </text>
              <text className="cm-col-tag" x={c.x + 8} y={c.y + 31}>
                {clip(columnTag(c.kind, c.descriptor), 27)}
              </text>
              <text className="cm-n" x={c.x + 8} y={c.y + 45}>
                {plural(c.count, "name", "names")}
              </text>
            </g>
          ))}
          {layout.nodes.filter((n) => n.zone === "chain").map(node)}
        </ZoneSvg>
        {right && (
          <ZoneSvg rect={right} className="cm-zone-right" label={`Right rail: ${railLabel(data, "right")}`}>
            {railPart("right")}
          </ZoneSvg>
        )}
      </div>
    </>
  );
}

// --- the page --------------------------------------------------------------------------------------------------

export default function ChainMap({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showUntagged, setShowUntagged] = useState(false);
  const narrow = useNarrow();

  const cardsQ = useChainMapCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId =
    picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useChainMap(asof, thesisId);
  const data = q.data;
  const layout = useMemo(
    () => (data ? layoutMap(data, { mode: narrow ? "stacked" : "wide", showUntagged }) : null),
    [data, narrow, showUntagged],
  );

  const options = useMemo(() => {
    const hits = filterCards(cards, query);
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, query, thesisId]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Chain Map's cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);
  const hidden = data?.counts.untagged_silent ?? 0;

  return (
    <div className="cm">
      <div className="cm-controls">
        <div className="cm-pick">
          <span className="cm-seg-label">Card</span>
          <input
            type="search"
            className="cm-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="cm-select"
            aria-label="Card"
            value={thesisId ?? ""}
            onChange={(e) => setPicked(e.target.value)}
          >
            {options.map((c) => (
              <option key={c.thesis_id} value={c.thesis_id}>
                {c.name} · {plural(c.links, "link", "links")}
              </option>
            ))}
          </select>
          {query.trim() && (
            <span className="cm-n">
              {plural(options.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        {hidden > 0 && (
          <label
            className="cm-check"
            title="The Context and Bench names with no role say nothing about the chain until a role is set; they never count toward the call"
          >
            <input type="checkbox" checked={showUntagged} onChange={(e) => setShowUntagged(e.target.checked)} />
            Show Context and Bench without a role ({hidden.toLocaleString("en-US")})
          </label>
        )}
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data || !layout ? (
        <div className="dash-note">Could not load the Chain Map.</div>
      ) : (
        <>
          <div className="cm-title">
            <button
              type="button"
              className="cm-card"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="cm-n">
              {plural(data.counts.links_today, "link", "links")} today · {plural(data.counts.companies, "name", "names")}
            </span>
          </div>
          <Picture key={data.thesis_id} data={data} layout={layout} onSelectThesis={onSelectThesis} />
          <div className="dash-note cm-work">{workLine(data, showUntagged)}</div>
          {data.roster_fallback && (
            <div className="dash-note cm-work">
              Today's roster stands in for this card: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <FlaggedNote flagged={data.flagged} />
          <div className="dash-note cm-legend">
            On each name: its business type, a mark for the 50-day average (▲ above, ▼ below, – at it) and the days
            since its newest filing (— = none stored in the last year). A name drawn gray has a
            flagged price history, so its 50-day side is left out.
          </div>
          <ul className="cm-limits">
            <li>
              A role names a part, not a target: every edge lands on the chain as a whole, never on a link or a name.
              The edge is the role the operator set; the picture infers nothing and weighs nothing.
            </li>
            <li>
              The roster is the card's roster as of {fmtDay(data.asof)}. Links, their descriptors, business types and
              descriptions are today's (only the roster is snapshotted).
            </li>
            <li>
              The 50-day side and the filing age are display context and never feed a call. The filing age counts the
              filings the system stores (8-Ks, Form 4s and 4/As, 13D/G stakes, 10-Q and 10-K revenue), not every
              filing; a split, a catalyst or a ratified or stored fact is never counted as one.
            </li>
          </ul>
        </>
      )}
    </div>
  );
}
