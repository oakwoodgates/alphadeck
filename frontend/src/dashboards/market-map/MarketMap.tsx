import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";

import { businessTypeLabel, supersectorLabel } from "../../util/format";
import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./market-map.css";
import {
  COMPARES,
  DEFAULT_OPTS,
  DEFAULT_SORT,
  GROUP_BYS,
  MIN_PRICED,
  SIZE_BYS,
  WINDOWS,
  bandLabel,
  benchReturn,
  buildTree,
  capSourceWords,
  cardLinksBySecurity,
  clickAction,
  colorStep,
  companyCards,
  compareAvailability,
  fmtDay,
  fmtPct,
  flaggedInView,
  fmtUsd,
  headerTitle,
  inView,
  layoutTree,
  legendSteps,
  nextSort,
  resolveZoom,
  shown,
  sortRows,
  stepClass,
  tableRows,
  tileText,
  windowLabel,
  type CardLink,
  type Compare,
  type GroupBy,
  type Placed,
  type SizeBy,
  type SortCol,
  type SortState,
  type TableRow,
  type TileMode,
  type Tree,
  type TreeNode,
  type ViewOpts,
  type WindowKey,
} from "./mapView";
import { useMarketMap, type MapCompany, type MarketMapOut } from "./useMarketMap";

// Market Map (docs/dashboards/market-map.md): a nested treemap heat map of every company on the cards. Tiles are
// sized by market cap (or dollar volume, or equal) and colored by the return over a chosen window on a fixed
// scale; boxes group them by card and link, sector, business type or cap band. The server ships readings; this
// page builds every grouping, the medians, the colors and the squarified layout, so no control refetches.
// Display only: it never feeds a call and writes nothing.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";
const MIN_MAP_H = 480;
// room kept under the map for the legend and the work lines
const BELOW_MAP = 96;
const PHONE_QUERY = "(max-width: 767px)";

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

const GAP_WORDS: Record<string, string> = {
  no_price: "awaiting price",
  no_shares: "awaiting a share count",
  ads_unread: "the ADS ratio is not read",
  no_cap: "the share count or the close is not positive",
  flagged: "a flagged price history",
};

const SCALE_TITLE =
  "Fixed per window, not relative to the day: a quiet day is a gray map. Beyond the ends stays the end color.";

function cacheWords(d: MarketMapOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work, every number off the payload and the tree in view. */
export function workLine(d: MarketMapOut, tree: Tree, opts: ViewOpts): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "card", "cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "card", "cards")} counted`;
  const parts = [
    `as of ${fmtDay(d.asof)}`,
    cards,
    plural(tree.companies, "company", "companies"),
    `${plural(tree.tiles, opts.tiles === "groups" ? "group tile" : "tile", opts.tiles === "groups" ? "group tiles" : "tiles")} drawn`,
    `${tree.priced.toLocaleString("en-US")} priced for ${windowLabel(opts.window)}`,
  ];
  if (opts.sizeBy === "cap") {
    parts.push(
      `caps: ${tree.caps.workbench.toLocaleString("en-US")} Workbench / ${tree.caps.cover.toLocaleString("en-US")} filing cover / ${tree.caps.none.toLocaleString("en-US")} none`,
    );
  }
  if (opts.sizeBy !== "equal" && tree.noWeight > 0) {
    parts.push(`${plural(tree.noWeight, "company", "companies")} drawn dashed at the smallest size`);
  }
  parts.push(
    opts.includeSilent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows, "basket row", "basket rows")})`,
  );
  const unresolved = d.unresolved_rows + (opts.includeSilent ? d.unresolved_silent_rows : 0);
  parts.push(`${plural(unresolved, "basket row", "basket rows")} without a resolved security not drawn`);
  // the same names the note below lists: the flagged ones in view under the Context/Bench toggle
  parts.push(
    `${plural(flaggedInView(d, opts.includeSilent).length, "name", "names")} left out for a flagged price history`,
  );
  parts.push(`scale ±${legendSteps(opts.window)[8].label.slice(1)} for ${windowLabel(opts.window)}`);
  parts.push(
    opts.compare === "none" ? "returns raw" : `returns minus ${opts.compare === "spy" ? "SPY" : "IWM"}'s own`,
  );
  parts.push("business type and sector are today's");
  parts.push(cacheWords(d));
  return parts.join(" · ");
}

function isPhone(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(PHONE_QUERY).matches
    : false;
}

// --- the tiles ---------------------------------------------------------------------------------------------

const TileLayer = memo(function TileLayer({
  placed,
  window,
  pinnedKey,
  onZoom,
}: {
  placed: readonly Placed[];
  window: WindowKey;
  pinnedKey: string | null;
  onZoom: (n: TreeNode) => void;
}) {
  return (
    <>
      {placed.map((p) => {
        const { node, rect } = p;
        if (node.kind === "group") {
          return (
            <div
              key={node.key}
              className={`mm-box mm-d${Math.min(p.depth, 2)}`}
              style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
            >
              {p.header && (
                <button
                  type="button"
                  className="mm-head"
                  title={headerTitle(node)}
                  onClick={() => onZoom(node)}
                >
                  <span className="mm-head-l">{node.label}</span>
                  <span className="mm-head-v">{fmtPct(node.value)}</span>
                </button>
              )}
            </div>
          );
        }
        const valueText = fmtPct(node.value);
        const t = tileText(rect.w - 1, rect.h - 1, node.label.length, valueText.length);
        return (
          <div
            key={node.key}
            data-k={node.key}
            className={`mm-tile ${stepClass(colorStep(node.value, window))}${node.dashed ? " dashed" : ""}${pinnedKey === node.key ? " pinned" : ""}`}
            style={{
              left: rect.x + 0.5,
              top: rect.y + 0.5,
              width: rect.w - 1,
              height: rect.h - 1,
              fontSize: t.fontPx || undefined,
            }}
          >
            {t.ticker && <span className="mm-tk">{node.label}</span>}
            {t.value && <span className="mm-val">{valueText}</span>}
          </div>
        );
      })}
    </>
  );
});

// --- the info box ------------------------------------------------------------------------------------------

type Spot = { key: string; x: number; y: number };

function InfoBox({
  node,
  spot,
  size,
  pinned,
  data,
  opts,
  onOpen,
  onClose,
}: {
  node: TreeNode;
  spot: Spot;
  size: { w: number; h: number };
  pinned: boolean;
  data: MarketMapOut;
  opts: ViewOpts;
  onOpen: (thesisId: string, ticker: string | null) => void;
  onClose: () => void;
}) {
  const flipX = spot.x > size.w / 2;
  const flipY = spot.y > size.h / 2;
  const style = {
    left: flipX ? spot.x - 14 : spot.x + 14,
    top: flipY ? spot.y - 14 : spot.y + 14,
    transform: `translate(${flipX ? "-100%" : "0"}, ${flipY ? "-100%" : "0"})`,
  };
  const close = pinned ? (
    <button type="button" className="mm-info-x" aria-label="Close" onClick={onClose}>
      ×
    </button>
  ) : null;

  if (node.kind === "groupTile") {
    return (
      <div className={`mm-info${pinned ? " pinned" : ""}`} style={style} role="status">
        {close}
        <div className="mm-info-h">
          <b>{node.label}</b>
        </div>
        <div className="mm-info-row">
          {windowLabel(opts.window)} median {fmtPct(node.value)}
        </div>
        <div className="mm-info-note">{headerTitle(node)}</div>
        {node.cardId && !pinned && <div className="mm-info-note">Click to open this card.</div>}
      </div>
    );
  }

  const c = node.company as MapCompany;
  const cards = companyCards(data, c.security_id, opts.includeSilent);
  const compareName = opts.compare === "spy" ? "SPY" : "IWM";
  const gaps = WINDOWS.filter((w) => c.returns[w.key] == null);
  const capWords = capSourceWords(c);
  return (
    <div className={`mm-info${pinned ? " pinned" : ""}`} style={style} role="status">
      {close}
      <div className="mm-info-h">
        <b>{c.ticker}</b> <span>{c.name ?? ""}</span>
      </div>
      <div className="mm-info-grid">
        {WINDOWS.map((w) => {
          const raw = c.returns[w.key];
          const rel = opts.compare === "none" ? raw : shown(raw, benchReturn(data, opts.compare, w.key));
          return (
            <div key={w.key} className={`mm-info-win${w.key === opts.window ? " on" : ""}`}>
              <span>{w.label}</span>
              <span>
                {fmtPct(rel)}
                {opts.compare !== "none" && raw != null && (
                  <span className="mm-info-raw">
                    {" "}
                    vs {compareName} (raw {fmtPct(raw)})
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>
      {gaps.map((w) => (
        <div key={w.key} className="mm-info-note">
          no {w.label} reading: {c.gaps[w.key] ?? "no reading"}
        </div>
      ))}
      <div className="mm-info-row">
        Market cap{" "}
        {c.cap != null ? (
          <>
            ~{fmtUsd(c.cap)} · {bandLabel(c.cap_tier).split(" · ")[0].toLowerCase()}
            {capWords && <span className="mm-info-raw"> ({capWords})</span>}
          </>
        ) : (
          <>
            none
            {c.cap_gap && <span className="mm-info-raw"> ({GAP_WORDS[c.cap_gap] ?? c.cap_gap})</span>}
          </>
        )}
      </div>
      {c.cap == null && c.cap_note && <div className="mm-info-note">{c.cap_note}</div>}
      {opts.sizeBy === "cap" && c.cap == null && (
        <div className="mm-info-note">no market cap: drawn at the smallest size</div>
      )}
      <div className="mm-info-row">
        Dollar volume 20D {c.dollar_volume != null ? fmtUsd(c.dollar_volume) : "—"}
        {c.dollar_volume == null && c.dollar_volume_note && (
          <span className="mm-info-raw"> ({c.dollar_volume_note})</span>
        )}
      </div>
      {opts.sizeBy === "dollar_volume" && c.dollar_volume == null && (
        <div className="mm-info-note">no dollar volume: drawn at the smallest size</div>
      )}
      <div className="mm-info-row">
        {c.supersector ? supersectorLabel(c.supersector) : "Unclassified"}
        {c.business_type ? ` · ${businessTypeLabel(c.business_type)}` : ""}
      </div>
      <div className="mm-info-cards">
        {cards.map((card) =>
          pinned ? (
            <button
              key={card.thesisId}
              type="button"
              className="mm-chip"
              title={`Open ${card.card} on ${card.ticker}`}
              onClick={() => onOpen(card.thesisId, card.ticker)}
            >
              {card.card}
              <span className="mm-chip-links"> · {card.links.join(", ")}</span>
            </button>
          ) : (
            <div key={card.thesisId} className="mm-info-card">
              {card.card} <span className="mm-info-raw">· {card.links.join(", ")}</span>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

// --- the legend --------------------------------------------------------------------------------------------

function Legend({ window }: { window: WindowKey }) {
  return (
    <div className="mm-legend" title={SCALE_TITLE} aria-label={`Color scale for ${windowLabel(window)}`}>
      {legendSteps(window).map((s) => (
        <span key={s.step} className="mm-legend-step">
          <span className={`mm-swatch ${stepClass(s.step)}`} />
          <span className="mm-legend-v">{s.label}</span>
        </span>
      ))}
      <span className="mm-legend-step">
        <span className="mm-swatch mm-c-none" />
        <span className="mm-legend-v">no reading</span>
      </span>
    </div>
  );
}

// --- the table ---------------------------------------------------------------------------------------------

function SortTh({
  col,
  label,
  sort,
  onSort,
  num = true,
}: {
  col: SortCol;
  label: string;
  sort: SortState;
  onSort: (c: SortCol) => void;
  num?: boolean;
}) {
  const dir = sort.col === col ? sort.dir : null;
  return (
    <th
      className={num ? "mm-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button
        type="button"
        className={`mm-sort${dir ? " active" : ""}`}
        title={`Sort by ${label}`}
        onClick={() => onSort(col)}
      >
        {label}
        {dir && (
          <span className="mm-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

function MapTable({
  rows,
  window,
  compare,
  data,
  includeSilent,
  sort,
  onSort,
  onOpen,
}: {
  rows: readonly TableRow[];
  window: WindowKey;
  compare: Compare;
  data: MarketMapOut;
  includeSilent: boolean;
  sort: SortState;
  onSort: (c: SortCol) => void;
  onOpen: (thesisId: string, ticker: string | null) => void;
}) {
  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);
  const cardsOf = useMemo(() => cardLinksBySecurity(data, includeSilent), [data, includeSilent]);
  if (rows.length === 0) return <div className="dash-note">No companies in this view.</div>;
  return (
    <div className="mm-scroll">
      <table className="mm-table">
        <thead>
          <tr>
            <SortTh col="label" label="Name" sort={sort} onSort={onSort} num={false} />
            <SortTh col="group" label="Group" sort={sort} onSort={onSort} num={false} />
            {WINDOWS.map((w) => (
              <SortTh
                key={w.key}
                col={w.key}
                label={compare === "none" ? w.label : `${w.label} vs ${compare === "spy" ? "SPY" : "IWM"}`}
                sort={sort}
                onSort={onSort}
              />
            ))}
            <SortTh col="cap" label="Market cap (approx.)" sort={sort} onSort={onSort} />
            <SortTh col="dollarVolume" label="$ volume 20D" sort={sort} onSort={onSort} />
            <SortTh col="sector" label="Sector" sort={sort} onSort={onSort} num={false} />
            <SortTh col="businessType" label="Business type" sort={sort} onSort={onSort} num={false} />
            <SortTh col="cards" label="Cards and links" sort={sort} onSort={onSort} num={false} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.key} className={r.kind === "group" ? "mm-grp" : undefined}>
              <td className="mm-name">
                <span className="mm-tk-t">{r.label}</span> <span className="mm-sub">{r.sub}</span>
              </td>
              <td className="mm-group">{r.group}</td>
              {WINDOWS.map((w) => (
                <td key={w.key} className={`mm-num mm-v${w.key === window ? " on" : ""}`}>
                  {fmtPct(r.values[w.key])}
                </td>
              ))}
              <td className="mm-num mm-v">
                {r.cap != null ? `${fmtUsd(r.cap)} · ${bandLabel(r.capTier).split(" · ")[0].toLowerCase()}` : "—"}
              </td>
              <td className="mm-num mm-v">{fmtUsd(r.dollarVolume)}</td>
              <td className="mm-v">{r.sector || "—"}</td>
              <td className="mm-v">{r.businessType || "—"}</td>
              <td className="mm-cards">
                {r.kind === "company" ? (
                  <CardChips cards={cardsOf.get(r.key.split("/co:")[1]) ?? []} onOpen={onOpen} />
                ) : (
                  <span className="mm-sub">{r.priced} priced</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CardChips({
  cards,
  onOpen,
}: {
  cards: readonly CardLink[];
  onOpen: (thesisId: string, ticker: string | null) => void;
}) {
  return (
    <span className="mm-chips">
      {cards.map((c) => (
        <button
          key={c.thesisId}
          type="button"
          className="mm-chip"
          title={`Open ${c.card} on ${c.ticker}`}
          onClick={() => onOpen(c.thesisId, c.ticker)}
        >
          {c.card}
          <span className="mm-chip-links"> · {c.links.join(", ")}</span>
        </button>
      ))}
    </span>
  );
}

// --- the page ----------------------------------------------------------------------------------------------

function Seg<T extends string>({
  label,
  options,
  value,
  onPick,
  disabled,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onPick: (v: T) => void;
  disabled?: Partial<Record<T, string | null>>;
}) {
  return (
    <div className="mm-seg" role="group" aria-label={label}>
      <span className="mm-seg-label">{label}</span>
      {options.map((o) => {
        const why = disabled?.[o.value] ?? null;
        return (
          <button
            key={o.value}
            type="button"
            className={`mm-seg-btn${value === o.value ? " on" : ""}`}
            aria-pressed={value === o.value}
            disabled={why != null}
            title={why ?? undefined}
            onClick={() => onPick(o.value)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function MapBody({
  data,
  asof,
  fetching,
  onSelectThesis,
}: {
  data: MarketMapOut;
  asof: string;
  fetching: boolean;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const [picked, setPicked] = useState<ViewOpts>(DEFAULT_OPTS);
  const [view, setView] = useState<"map" | "table">(() => (isPhone() ? "table" : "map"));
  const [zoom, setZoom] = useState<string[]>([]);
  const [sortPick, setSortPick] = useState<SortState | null>(null);
  const [hover, setHover] = useState<Spot | null>(null);
  const [pinned, setPinned] = useState<Spot | null>(null);
  const [size, setSize] = useState({ w: 0, h: MIN_MAP_H });
  const [fullscreen, setFullscreen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<HTMLDivElement>(null);

  // A benchmark that cannot be subtracted (no tape, or no reading for this window) falls back to raw and says so.
  const avail = compareAvailability(data, picked.compare, picked.window);
  const opts: ViewOpts = avail.ok ? picked : { ...picked, compare: "none" };
  const benchNote = avail.ok ? null : avail.reason;
  const setOpt = <K extends keyof ViewOpts>(k: K, v: ViewOpts[K]) => {
    setPicked((p) => ({ ...p, [k]: v }));
    if (k === "groupBy") setZoom([]); // zoom resets when the grouping changes
    setPinned(null);
  };

  const tree = useMemo(
    () => buildTree(data, opts),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, opts.groupBy, opts.window, opts.compare, opts.sizeBy, opts.tiles, opts.includeSilent],
  );
  const zoomed = useMemo(() => resolveZoom(tree.roots, zoom), [tree, zoom]);
  const placed = useMemo(
    () => (size.w > 0 ? layoutTree(zoomed.roots, { x: 0, y: 0, w: size.w, h: size.h }) : []),
    [zoomed, size],
  );
  const byKey = useMemo(() => new Map(placed.map((p) => [p.node.key, p.node])), [placed]);
  const rows = useMemo(
    () => (view === "table" ? tableRows(data, zoomed.roots, opts) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [view, data, zoomed, opts.compare, opts.includeSilent],
  );
  const sort = sortPick ?? DEFAULT_SORT(opts.window);
  const inViewCount = useMemo(() => inView(data, opts.includeSilent).companies.length, [data, opts.includeSilent]);

  const measure = useCallback(() => {
    const el = mapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const rootTop = rootRef.current?.getBoundingClientRect().top ?? 0;
    const top = fullscreen ? rect.top - rootTop : rect.top + window.scrollY;
    const h = Math.max(MIN_MAP_H, Math.round(window.innerHeight - top - BELOW_MAP));
    const w = Math.floor(rect.width);
    setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
  }, [fullscreen]);

  useLayoutEffect(() => {
    measure();
    if (view !== "map") return;
    const watch: Element[] = [mapRef.current, rootRef.current].filter((e): e is HTMLDivElement => e != null);
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(() => measure());
      watch.forEach((e) => ro.observe(e));
      return () => ro.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, view]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === rootRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // Escape: unpin the info box first, then step out one zoom level
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (pinned) setPinned(null);
      else setZoom((z) => (z.length ? z.slice(0, -1) : z));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pinned]);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void rootRef.current?.requestFullscreen?.();
  };

  const onZoomNode = useCallback(
    (n: TreeNode) => setZoom([...zoomed.chain.map((c) => c.key), n.key].filter((k, i, a) => a.indexOf(k) === i)),
    [zoomed],
  );
  const open = useCallback(
    (thesisId: string, ticker: string | null) => onSelectThesis(thesisId, ticker),
    [onSelectThesis],
  );

  const spotOf = (e: ReactMouseEvent, key: string): Spot => {
    const rect = mapRef.current?.getBoundingClientRect();
    return { key, x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) };
  };
  const keyAt = (e: ReactMouseEvent): string | null =>
    (e.target as HTMLElement).closest<HTMLElement>("[data-k]")?.dataset.k ?? null;
  const onMove = (e: ReactMouseEvent) => {
    const key = keyAt(e);
    if (key == null) setHover((h) => (h ? null : h));
    else setHover(spotOf(e, key));
  };
  const onMapClick = (e: ReactMouseEvent) => {
    const key = keyAt(e);
    const node = key != null ? byKey.get(key) : undefined;
    if (!node) {
      if ((e.target as HTMLElement).closest(".mm-head, .mm-info") == null) setPinned(null);
      return;
    }
    const act = clickAction(node, data, opts.includeSilent);
    if (act.kind === "open") open(act.thesisId, act.ticker);
    else setPinned(spotOf(e, node.key));
  };

  const hoverNode = !pinned && hover ? byKey.get(hover.key) : undefined;
  const pinnedNode = pinned ? byKey.get(pinned.key) : undefined;
  const groups = opts.groupBy === "card_link" || opts.groupBy === "card";

  return (
    <div className={`mm${fullscreen ? " full" : ""}`} ref={rootRef}>
      <div className="mm-controls">
        <Seg<GroupBy>
          label="Group by"
          options={GROUP_BYS}
          value={picked.groupBy}
          onPick={(v) => setOpt("groupBy", v)}
        />
        <Seg<WindowKey>
          label="Window"
          options={WINDOWS.map((w) => ({ value: w.key, label: w.label }))}
          value={picked.window}
          onPick={(v) => setOpt("window", v)}
        />
        <Seg<Compare>
          label="Compare to"
          options={COMPARES}
          value={picked.compare}
          onPick={(v) => setOpt("compare", v)}
          disabled={{
            spy: compareAvailability(data, "spy", picked.window).reason,
            iwm: compareAvailability(data, "iwm", picked.window).reason,
          }}
        />
        <Seg<SizeBy> label="Size by" options={SIZE_BYS} value={picked.sizeBy} onPick={(v) => setOpt("sizeBy", v)} />
        <Seg<TileMode>
          label="Tiles"
          options={[
            { value: "companies", label: "Companies" },
            { value: "groups", label: "Groups" },
          ]}
          value={picked.tiles}
          onPick={(v) => setOpt("tiles", v)}
        />
        <label className="mm-check" title="Add Context and Bench memberships (they never count toward a call)">
          <input
            type="checkbox"
            checked={picked.includeSilent}
            onChange={(e) => setOpt("includeSilent", e.target.checked)}
          />
          Show Context and Bench
        </label>
        <Seg<"map" | "table">
          label="View"
          options={[
            { value: "map", label: "Map" },
            { value: "table", label: "Table" },
          ]}
          value={view}
          onPick={(v) => {
            setView(v);
            setHover(null);
            setPinned(null);
          }}
        />
        {typeof document !== "undefined" && typeof document.documentElement.requestFullscreen === "function" && (
          <button type="button" className="mm-seg-btn" onClick={toggleFullscreen}>
            {fullscreen ? "Exit fullscreen" : "Fullscreen"}
          </button>
        )}
        {fetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>

      {benchNote && <div className="dash-note">{benchNote}: showing raw returns.</div>}

      {zoomed.chain.length > 0 && (
        <nav className="mm-crumbs" aria-label="Zoom">
          <button type="button" className="mm-crumb" onClick={() => setZoom([])}>
            ← All
          </button>
          {zoomed.chain.map((n, i) => {
            const last = i === zoomed.chain.length - 1;
            return (
              <span key={n.key} className="mm-crumb-wrap">
                <span className="mm-crumb-sep" aria-hidden="true">
                  ›
                </span>
                {last ? (
                  <span className="mm-crumb on" aria-current="location">
                    {n.label}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="mm-crumb"
                    onClick={() => setZoom(zoomed.chain.slice(0, i + 1).map((c) => c.key))}
                  >
                    {n.label}
                  </button>
                )}
              </span>
            );
          })}
          <span className="dash-note"> Escape steps out</span>
        </nav>
      )}

      {tree.tiles === 0 ? (
        <div className="dash-note">
          {inViewCount === 0 ? "No companies in this view." : "Nothing to draw in this view."}
        </div>
      ) : view === "map" ? (
        <div
          className="mm-map"
          ref={mapRef}
          style={{ height: size.h }}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          onClick={onMapClick}
        >
          <TileLayer placed={placed} window={opts.window} pinnedKey={pinned?.key ?? null} onZoom={onZoomNode} />
          {hoverNode && hover && (
            <InfoBox
              node={hoverNode}
              spot={hover}
              size={size}
              pinned={false}
              data={data}
              opts={opts}
              onOpen={open}
              onClose={() => undefined}
            />
          )}
          {pinnedNode && pinned && (
            <InfoBox
              node={pinnedNode}
              spot={pinned}
              size={size}
              pinned
              data={data}
              opts={opts}
              onOpen={open}
              onClose={() => setPinned(null)}
            />
          )}
        </div>
      ) : (
        <MapTable
          rows={rows}
          window={opts.window}
          compare={opts.compare}
          data={data}
          includeSilent={opts.includeSilent}
          sort={sort}
          onSort={(c) => setSortPick(nextSort(sort, c))}
          onOpen={open}
        />
      )}

      <Legend window={opts.window} />
      <div className="dash-note mm-work">{workLine(data, tree, opts)}</div>
      {groups && (
        <div className="dash-note mm-work">
          {opts.groupBy === "card_link"
            ? "A company on several cards or links is drawn once for each, so the areas here show the cards' structure, not the market."
            : "A company on several cards is drawn once for each, so the areas here show the cards' structure, not the market."}
        </div>
      )}
      <FlaggedNote flagged={flaggedInView(data, opts.includeSilent)} />
      {data.roster_fallback_cards > 0 && (
        <div className="dash-note mm-work">
          Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
          point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
        </div>
      )}
      <div className="dash-note mm-legend-note">
        Tile area is the approximate market cap by default (an estimate: the latest close times the latest share
        count); color is the return over the window on the fixed scale above. A group's figure is the median of its
        priced names, shown when at least {MIN_PRICED} are priced. Click a group's header to zoom in.
      </div>
    </div>
  );
}

export default function MarketMap({ asof, onSelectThesis }: DashboardProps) {
  const q = useMarketMap(asof);
  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing across all cards — the first load after a run can take a minute.
      </div>
    );
  }
  if (q.error || !q.data) return <div className="dash-note">Could not load the Market Map.</div>;
  return <MapBody data={q.data} asof={asof} fetching={q.isFetching} onSelectThesis={onSelectThesis} />;
}
