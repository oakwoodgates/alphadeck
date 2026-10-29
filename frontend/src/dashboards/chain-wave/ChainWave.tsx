import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";

import { CONTEXT_ROLE_DESCRIPTION } from "../../workbench/format";
import { FlaggedNote, leftOutWords } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./chain-wave.css";
import {
  DEFAULT_WEEKS,
  WINDOW_OPTIONS,
  cellTone,
  filterCards,
  fmtDay,
  fmtPct,
  fmtPp,
  gapWords,
  plural,
  readoutWords,
  rowLabel,
  spyBar,
  weekHeaders,
  weekWords,
} from "./waveView";
import {
  useChainWave,
  useChainWaveCards,
  type ChainWaveCellOut,
  type ChainWaveMemberOut,
  type ChainWaveOut,
  type ChainWaveRowOut,
  type WaveWeeks,
} from "./useChainWave";

// Chain Wave (docs/dashboards/chain-wave.md): ONE card's links in chain order down the side, weeks across, each
// cell the link's weekly return minus SPY's, so a move traveling along the chain shows as a diagonal; below the
// chain, its counting names and one row per context role (payers first). Display only. Every number (the cells,
// who counts, priced, thin, the readout) is computed server-side; this page picks the card, the window and the
// toggle, shades the cells (a muted diverging scale, nothing loud) and words the numbers.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";
const SLOW = "Computing this card's weeks — the first load after a run or a basket edit can take a moment.";

function cacheWords(d: ChainWaveOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work. */
function workLine(d: ChainWaveOut): string {
  const counting = d.rows.find((r) => r.kind === "counting");
  const first = d.week_columns[0];
  const last = d.week_columns[d.week_columns.length - 1];
  return [
    "each cell = the row's weekly return minus SPY's, in percentage points (equal weight, rebalanced daily); full color at ±10 pp",
    first && last
      ? `${d.weeks} weeks, ${fmtDay(first.first_session ?? first.monday)} → ${fmtDay(last.close ?? d.last_session)}`
      : `${d.weeks} weeks`,
    counting ? `${counting.priced} of ${plural(counting.names, "counting name", "counting names")} priced` : null,
    d.flagged.length > 0
      ? `names with a flagged price history are left out (${plural(d.flagged.length, "company", "companies")})`
      : "names with a flagged price history are left out",
    d.include_silent
      ? "Context and Bench shown as the last two chain rows"
      : `Context and Bench left out of the chain (${plural(d.silent_rows, "basket row", "basket rows")}; the role rows still include them)`,
    d.unresolved_rows > 0
      ? `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security counted as names, never priced`
      : null,
    `rows with fewer than ${d.thin_below} priced names drawn quieter`,
    d.spy_available ? "against SPY" : "no SPY tape at this as-of: no cell can be measured",
    d.session_source === "companies" ? "sessions from the companies' bars" : null,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

// --- the grid --------------------------------------------------------------------------------------------------

/** One drawn line of the grid: SPY, a row, or a row's company (when the row is open). */
type Line =
  | { key: string; kind: "spy" }
  | { key: string; kind: "row"; row: ChainWaveRowOut }
  | { key: string; kind: "member"; row: ChainWaveRowOut; member: ChainWaveMemberOut };

type Spot = { line: number; col: number };

const cellId = (line: number, col: number) => `cw-cell-${line}-${col}`;

function lineName(l: Line): string {
  if (l.kind === "spy") return "SPY";
  if (l.kind === "row") return rowLabel(l.row);
  return `${l.member.ticker} (${rowLabel(l.row)})`;
}

/** The hover / keyboard-focus readout's words for one cell. */
function focusWords(d: ChainWaveOut, l: Line, col: number): string {
  const week = d.week_columns[col];
  const spy = d.spy[col];
  if (l.kind === "spy") {
    return [`SPY · ${weekWords(week)}`, `SPY ${fmtPct(spy?.ret)}`, gapWords(spy?.gap)].filter(Boolean).join(" · ");
  }
  const c: ChainWaveCellOut | undefined = l.kind === "row" ? l.row.cells[col] : l.member.cells[col];
  const names =
    l.kind === "row"
      ? `${plural(c?.names ?? 0, "name", "names")} contributing`
      : c?.names
        ? "contributing"
        : "no daily return that week";
  return [
    `${lineName(l)} · ${weekWords(week)}`,
    `row ${fmtPct(c?.ret)}`,
    `SPY ${fmtPct(c?.spy ?? spy?.ret)}`,
    `difference ${fmtPp(c?.diff)}`,
    names,
    gapWords(c?.gap),
  ]
    .filter(Boolean)
    .join(" · ");
}

function Cell({
  cell,
  thin,
  spy,
  id,
  active,
  label,
  onHover,
  onFocusCell,
}: {
  cell: ChainWaveCellOut | undefined;
  thin: boolean;
  spy: boolean;
  id: string;
  active: boolean;
  label: string;
  onHover: (on: boolean) => void;
  onFocusCell: () => void;
}) {
  const common = {
    id,
    role: "gridcell",
    tabIndex: active ? 0 : -1,
    "aria-label": label,
    "data-testid": "cw-cell",
    onMouseEnter: () => onHover(true),
    onMouseLeave: () => onHover(false),
    onFocus: onFocusCell,
  };
  if (spy) {
    const bar = spyBar(cell?.ret);
    return (
      <div {...common} className={`cw-cell cw-spy${bar ? "" : " gap"}`}>
        {bar ? (
          <span className={`cw-spybar ${bar.dir}`} style={{ height: `${Math.max(6, bar.share * 50)}%` }} />
        ) : (
          "—"
        )}
      </div>
    );
  }
  const t = cellTone(cell?.diff, thin);
  const style = { "--cw-a": `${Math.round(t.strength * 80)}%` } as CSSProperties;
  return (
    <div {...common} className={`cw-cell ${t.tone}`} style={style}>
      {t.tone === "gap" ? "—" : null}
    </div>
  );
}

function RowHeader({
  l,
  open,
  onToggle,
  onSelectThesis,
  thesisId,
  thesisName,
}: {
  l: Line;
  open: boolean;
  onToggle: () => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
  thesisId: string;
  thesisName: string;
}) {
  if (l.kind === "spy") {
    return (
      <div role="rowheader" className="cw-label cw-label-spy" title="SPY's own weekly return: up or down from the middle">
        <span className="cw-label-text">SPY</span>
        <span className="cw-n">own weekly return</span>
      </div>
    );
  }
  if (l.kind === "member") {
    const m = l.member;
    return (
      <div role="rowheader" className="cw-label cw-label-member">
        <button
          type="button"
          className="cw-chip"
          title={`Open ${thesisName} on ${m.ticker}`}
          onClick={() => onSelectThesis(thesisId, m.ticker)}
        >
          {m.ticker}
        </button>
        {!m.priced && m.flag == null && m.security_id != null && <span className="cw-n">not priced</span>}
      </div>
    );
  }
  const r = l.row;
  return (
    <div role="rowheader" className="cw-label">
      <button
        type="button"
        className="cw-row-btn"
        aria-expanded={open}
        title={
          r.kind === "role" && r.role
            ? `${CONTEXT_ROLE_DESCRIPTION[r.role]} Click for its companies.`
            : "Click for its companies"
        }
        onClick={onToggle}
      >
        <span className="cw-chev" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className="cw-label-text">{rowLabel(r)}</span>
        <span className="cw-n" title="names priced / names">
          {r.priced}/{r.names}
        </span>
        {r.kind === "old_link" && <span className="cw-tag">not a link today</span>}
      </button>
    </div>
  );
}

function Grid({
  data,
  open,
  onToggle,
  onSelectThesis,
}: {
  data: ChainWaveOut;
  open: ReadonlySet<string>;
  onToggle: (key: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const lines = useMemo<Line[]>(() => {
    const out: Line[] = [{ key: "spy", kind: "spy" }];
    for (const row of data.rows) {
      out.push({ key: row.key, kind: "row", row });
      if (open.has(row.key)) {
        for (const member of row.members) out.push({ key: `${row.key}|${member.key}`, kind: "member", row, member });
      }
    }
    return out;
  }, [data, open]);
  const cols = data.week_columns.length;
  const headers = weekHeaders(data.week_columns);
  const [hover, setHover] = useState<Spot | null>(null);
  const [active, setActive] = useState<Spot>({ line: 0, col: Math.max(0, cols - 1) });
  const at = { line: Math.min(active.line, lines.length - 1), col: Math.min(active.col, cols - 1) };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, [number, number]> = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    };
    const mv = moves[e.key];
    if (!mv || !(e.target as HTMLElement).classList.contains("cw-cell")) return;
    e.preventDefault();
    const next = {
      line: Math.max(0, Math.min(lines.length - 1, at.line + mv[0])),
      col: Math.max(0, Math.min(cols - 1, at.col + mv[1])),
    };
    setActive(next);
    document.getElementById(cellId(next.line, next.col))?.focus();
  };

  const focusLine = hover ? lines[hover.line] : null;
  return (
    <>
      <div className="cw-focus" data-testid="cw-focus" aria-live="polite">
        {hover && focusLine
          ? focusWords(data, focusLine, hover.col)
          : "Hover a cell (or focus one and use the arrow keys) for its numbers."}
      </div>
      <div className="cw-scroll">
        <div
          className="cw-grid"
          role="grid"
          aria-label={`${data.thesis_name}: weekly return minus SPY's, by link`}
          style={{ "--cw-cols": `repeat(${cols}, minmax(9px, 1fr))`, "--cw-n": cols } as CSSProperties}
          onKeyDown={onKeyDown}
        >
          <div role="row" className="cw-row cw-head">
            <div role="columnheader" className="cw-label cw-corner">
              Link · priced/names
            </div>
            {data.week_columns.map((w, i) => (
              <div
                key={w.monday}
                role="columnheader"
                className={`cw-week${i === cols - 1 && w.to_date ? " to-date" : ""}`}
                title={weekWords(w)}
              >
                {headers[i]}
              </div>
            ))}
          </div>
          {lines.map((l, li) => {
            const thin = l.kind === "row" ? l.row.thin : false;
            const rowClass =
              l.kind === "spy"
                ? "cw-line-spy"
                : l.kind === "member"
                  ? "cw-line-member"
                  : `cw-line-row kind-${l.row.kind}${l.row.thin ? " thin" : ""}${open.has(l.key) ? " open" : ""}`;
            const member = l.kind === "member" ? l.member : null;
            const leftOut = member && (member.flag != null || member.security_id == null);
            return (
              <div key={l.key} role="row" className={`cw-row ${rowClass}`} data-key={l.key}>
                <RowHeader
                  l={l}
                  open={open.has(l.key)}
                  onToggle={() => onToggle(l.key)}
                  onSelectThesis={onSelectThesis}
                  thesisId={data.thesis_id}
                  thesisName={data.thesis_name}
                />
                {leftOut ? (
                  <div role="gridcell" className="cw-off" style={{ gridColumn: "2 / -1" }}>
                    {member.flag != null ? leftOutWords(member.flag) : "no resolved security: never priced"}
                  </div>
                ) : (
                  data.week_columns.map((_, ci) => {
                    const cell =
                      l.kind === "spy" ? data.spy[ci] : l.kind === "row" ? l.row.cells[ci] : l.member.cells[ci];
                    return (
                      <Cell
                        key={ci}
                        cell={cell}
                        thin={thin}
                        spy={l.kind === "spy"}
                        id={cellId(li, ci)}
                        active={at.line === li && at.col === ci}
                        label={focusWords(data, l, ci)}
                        onHover={(on) => setHover(on ? { line: li, col: ci } : null)}
                        onFocusCell={() => {
                          setActive({ line: li, col: ci });
                          setHover({ line: li, col: ci });
                        }}
                      />
                    );
                  })
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

// --- the page ----------------------------------------------------------------------------------------------------

export default function ChainWave({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [weeks, setWeeks] = useState<WaveWeeks>(DEFAULT_WEEKS);
  const [includeSilent, setIncludeSilent] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  const cardsQ = useChainWaveCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId =
    picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useChainWave(asof, thesisId, weeks, includeSilent);
  const data = q.data;

  // a new card starts with every row closed
  useEffect(() => setOpen(new Set()), [thesisId]);

  const options = useMemo(() => {
    const hits = filterCards(cards, query);
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, query, thesisId]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Chain Wave's cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);

  return (
    <div className="cw">
      <div className="cw-controls">
        <div className="cw-pick">
          <span className="cw-seg-label">Card</span>
          <input
            type="search"
            className="cw-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="cw-select"
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
            <span className="cw-n">
              {plural(options.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        <div className="cw-seg" role="group" aria-label="Window">
          <span className="cw-seg-label">Window</span>
          {WINDOW_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`cw-seg-btn${weeks === o.value ? " on" : ""}`}
              aria-pressed={weeks === o.value}
              onClick={() => setWeeks(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="cw-check" title="Add Context and Bench as the last two chain rows (the role rows include them either way)">
          <input type="checkbox" checked={includeSilent} onChange={(e) => setIncludeSilent(e.target.checked)} />
          Show Context and Bench
        </label>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Chain Wave.</div>
      ) : (
        <>
          <div className="cw-title">
            <button
              type="button"
              className="cw-card"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="cw-n">{plural(data.links_today, "link", "links")} today</span>
          </div>
          {data.readout && (
            <div
              className="cw-readout"
              data-testid="cw-readout"
              title={`From the close of ${fmtDay(data.readout.start)} to ${fmtDay(data.readout.end)}, each line's return minus SPY's`}
            >
              {readoutWords(data.readout)}
            </div>
          )}
          <Grid data={data} open={open} onToggle={toggle} onSelectThesis={onSelectThesis} />
          {data.roles_set === 0 && (
            <div className="dash-note cw-work">No context roles are set on this card, so there are no role rows.</div>
          )}
          <div className="dash-note cw-work">{workLine(data)}</div>
          {data.roster_fallback && (
            <div className="dash-note cw-work">
              Today's roster stands in for this card: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <FlaggedNote flagged={data.flagged} />
          <div className="dash-note cw-legend">
            Known limits: a look back on the card's roster as of {fmtDay(data.asof)}, not the roster each past week
            (hindsight selection). Chain order is today's: links load live, only the roster is snapshotted. Equal
            weight. Small links are one or two stocks; the n beside each row says so. Our own simple measure, not a
            published method. Price flags are read once per as-of.
          </div>
        </>
      )}
    </div>
  );
}
