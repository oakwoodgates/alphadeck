import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { CONTEXT_ROLE_DESCRIPTION, CONTEXT_ROLE_LABEL, contextRoleTag } from "../../workbench/format";
import type { DashboardProps } from "../registry";
import "./filing-stream.css";
import {
  DAYS_OPTIONS,
  DEFAULT_DAYS,
  DEFAULT_FILTERS,
  KIND_LABEL,
  KIND_ORDER,
  LIMITS,
  ROSTER_PIT_FROM,
  SILENT_LABELS,
  countRows,
  filterCards,
  filterDays,
  filtersActive,
  fmtDay,
  fmtDayHeader,
  foldEntries,
  foldEntryWords,
  foldGroupOf,
  foldedByGroup,
  plural,
  unresolvedWords,
  usdHint,
  workLines,
  type Filters,
  type StreamKind,
} from "./streamView";
import {
  useFilingStream,
  useFilingStreamCards,
  type FilingStreamDayOut,
  type FilingStreamNameOut,
  type FilingStreamOut,
  type FilingStreamRowOut,
  type StreamDays,
} from "./useFilingStream";
import { ExternalLink, isSecLink } from "../../components/ExternalLink";

// Filing Stream (docs/dashboards/filing-stream.md): ONE card's names' filing-derived facts as a dated stream,
// newest day first, as they were readable at the as-of. Display only. Every row and count is computed server-side
// (the shared filing reader); this page picks the card and the window, filters client side (kind, link, name,
// role, "Counting names only"), folds the routine rows into quiet lines (a day's Form 4s; per day and company its
// prospectus supplements and its insider ownership filings), and words the work. No
// badges and no color by kind: loudness marks the exception only (#7 / WB#3).

const SLOW = "Reading this card's filings — the first load after a run or a basket edit can take a moment.";

/** One stream row: a ticker chip (opens the Cockpit on that name), the form, the headline (opens the detail), its
 *  quiet tags, the dollar hint and the link to the filing. */
function StreamLine({
  row,
  open,
  onToggle,
  onSelectThesis,
  thesisId,
  thesisName,
}: {
  row: FilingStreamRowOut;
  open: boolean;
  onToggle: () => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
  thesisId: string;
  thesisName: string;
}) {
  const hint = usdHint(row);
  // a quiet tag only for what is true of THIS row: the row's own predicates, the company's role when set, and
  // Context / Bench when the company is silent
  const silent = row.silent ? row.links.find((l): l is string => l != null && SILENT_LABELS.includes(l)) : null;
  const tags = [...row.tags, ...(row.role ? [contextRoleTag(row.role)] : []), ...(silent ? [silent] : [])];
  const expandable = row.detail.length > 0 || row.kind === "form4a";
  const source = isSecLink(row.url) ? "EDGAR" : "source";
  const material = !row.routine && row.kind !== "filing";
  const rowLinks = row.links.filter((l): l is string => l != null);
  return (
    <li
      className={`fs-row${open ? " open" : ""}${material ? " fs-material" : row.kind === "filing" ? " fs-index" : ""}`}
      data-testid="fs-row"
      data-kind={row.kind}
    >
      <div className="fs-line">
        <button
          type="button"
          className="fs-chip"
          title={`Open ${thesisName} on ${row.ticker}`}
          onClick={() => onSelectThesis(thesisId, row.ticker)}
        >
          {row.ticker}
        </button>
        <span className="fs-form">{row.form}</span>
        <span className="fs-main">
          {expandable ? (
            <button type="button" className="fs-head" aria-expanded={open} onClick={onToggle}>
              <span className="fs-chev" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
              {row.headline}
            </button>
          ) : (
            <span className="fs-head plain">{row.headline}</span>
          )}
          {tags.map((t) => (
            <span key={t} className="fs-tag" title={row.role && t === contextRoleTag(row.role) ? CONTEXT_ROLE_DESCRIPTION[row.role] : undefined}>
              {t}
            </span>
          ))}
        </span>
        <span className="fs-usd">{hint}</span>
        {row.url ? (
          <ExternalLink
            className="fs-src"
            href={row.url}
            title={source === "EDGAR" ? "Open the filing on EDGAR" : "Open the stored source"}
            ariaLabel={`Open ${row.ticker} ${row.form} on ${source === "EDGAR" ? "EDGAR" : "its source"}`}
          >
            {source} ↗
          </ExternalLink>
        ) : (
          <span className="fs-src none" />
        )}
      </div>
      {open && (
        <div className="fs-detail" data-testid="fs-detail">
          <ul>
            {row.detail.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
            {row.kind === "form4a" && (
              <li>
                Its standing (applied, kept, pending) is on <Link to="/admin">Admin › Insider amendments</Link>.
              </li>
            )}
          </ul>
          {(row.accession || rowLinks.length > 0) && (
            <div className="fs-detail-meta">
              {row.accession && <span>Accession {row.accession}</span>}
              {rowLinks.length > 0 && <span>Links: {rowLinks.join(", ")}</span>}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

const CAD_W = 1000;
const CAD_H = 42;
const CAD_BASE = 28;
const CAD_MAX_BAR = 24;

/** Monochrome daily filing cadence strip across the selected window: brighter lower bar = material rows, muted upper
 *  bar = routine rows. Hover updates a fixed-height readout (zero vertical shift); click jumps to that day. */
function CadenceStrip({
  days,
  windowStart,
  asof,
}: {
  days: readonly FilingStreamDayOut[];
  windowStart: string;
  asof: string;
}) {
  const [hoverDate, setHoverDate] = useState<string | null>(null);
  if (days.length === 0) return null;

  const startMs = Date.parse(`${windowStart}T00:00:00Z`);
  const endMs = Date.parse(`${asof}T00:00:00Z`);
  const spanMs = Math.max(86_400_000, endMs - startMs);
  const padL = 4;
  const padR = 4;
  const plotW = CAD_W - padL - padR;

  const points = days.map((d) => {
    const ms = Date.parse(`${d.date}T00:00:00Z`);
    const t = Number.isNaN(ms) ? 1 : Math.max(0, Math.min(1, (ms - startMs) / spanMs));
    const routine = d.routine;
    const material = Math.max(0, d.rows.length - routine);
    const tickers = [...new Set(d.rows.map((r) => r.ticker))].slice(0, 5);
    return { date: d.date, x: padL + t * plotW, total: d.rows.length, material, routine, tickers };
  });

  const maxTotal = Math.max(1, ...points.map((p) => p.total));
  const totalMat = points.reduce((s, p) => s + p.material, 0);
  const totalRt = points.reduce((s, p) => s + p.routine, 0);
  const barW = Math.max(3, Math.min(10, plotW / Math.max(20, Math.round(spanMs / 86_400_000) + 1) * 0.72));
  const hovered = hoverDate ? points.find((p) => p.date === hoverDate) ?? null : null;
  const readout = hovered
    ? `${fmtDayHeader(hovered.date)}: ${hovered.material} material${hovered.routine > 0 ? `, ${hovered.routine} routine` : ""} · ${hovered.tickers.join(", ")}${hovered.total > hovered.tickers.length ? "…" : ""}`
    : "Hover a day to inspect, click to jump";

  const jumpTo = (date: string) => {
    const el = document.getElementById(`fs-day-${date}`);
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="fs-cadence" data-testid="fs-cadence">
      <div className="fs-cad-head">
        <span className="fs-cad-title">Daily cadence</span>
        <span className="fs-cad-meta">
          {totalMat} material · {totalRt} routine · {plural(days.length, "active day", "active days")}
        </span>
      </div>
      <div className={`fs-cad-read${hovered ? " active" : ""}`} aria-live="polite" title={hovered ? readout : undefined}>
        {readout}
      </div>
      <svg
        className="fs-cad-svg"
        viewBox={`0 0 ${CAD_W} ${CAD_H}`}
        preserveAspectRatio="xMinYMid meet"
        role="img"
        aria-label="Daily filing cadence across the window"
        onMouseLeave={() => setHoverDate(null)}
      >
        <line className="fs-cad-axis" x1={padL} x2={CAD_W - padR} y1={CAD_BASE} y2={CAD_BASE} />
        <text className="fs-cad-tick" x={padL} y={39} textAnchor="start">
          {fmtDay(windowStart)}
        </text>
        <text className="fs-cad-tick" x={CAD_W - padR} y={39} textAnchor="end">
          {fmtDay(asof)}
        </text>
        {points.map((p) => {
          const totalH = Math.max(2, (Math.sqrt(p.total) / Math.sqrt(maxTotal)) * CAD_MAX_BAR);
          const matH = p.material > 0 ? Math.max(2, (p.material / p.total) * totalH) : 0;
          const rtH = Math.max(0, totalH - matH);
          const isHi = hoverDate === p.date;
          return (
            <g
              key={p.date}
              className={`fs-cad-bar${isHi ? " hi" : ""}`}
              onMouseEnter={() => setHoverDate(p.date)}
              onClick={() => jumpTo(p.date)}
            >
              <rect className="fs-cad-hit" x={p.x - Math.max(5, barW)} y={2} width={Math.max(10, barW * 2)} height={CAD_BASE} />
              {rtH > 0 && (
                <rect className="fs-cad-rt" x={p.x - barW / 2} y={CAD_BASE - matH - rtH} width={barW} height={rtH} rx={0.8} />
              )}
              {matH > 0 && (
                <rect className="fs-cad-mat" x={p.x - barW / 2} y={CAD_BASE - matH} width={barW} height={matH} rx={0.8} />
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Roster activity strip from `data.names`: shows active companies with counts and quiet (0-row) names; clicking
 *  toggles the ticker filter. */
function RosterStrip({
  names,
  activeName,
  onPick,
}: {
  names: readonly FilingStreamNameOut[];
  activeName: string;
  onPick: (ticker: string) => void;
}) {
  const [showQuiet, setShowQuiet] = useState(false);
  if (names.length < 2) return null;

  const sorted = [...names].sort((a, b) => b.rows - a.rows || a.ticker.localeCompare(b.ticker));
  const active = sorted.filter((n) => n.rows > 0);
  const quiet = sorted.filter((n) => n.rows === 0);
  const visible = showQuiet || quiet.length <= 6 ? sorted : active;
  const picked = activeName.trim().toLocaleLowerCase();

  return (
    <div className="fs-roster" data-testid="fs-roster" aria-label="Roster activity">
      <span className="fs-seg-label">Roster</span>
      {visible.map((n) => {
        const isOn = picked !== "" && picked === n.ticker.toLocaleLowerCase();
        return (
          <button
            key={n.key}
            type="button"
            className={`fs-roster-chip${n.rows === 0 ? " quiet" : ""}${isOn ? " on" : ""}`}
            aria-pressed={isOn}
            title={`${n.ticker}: ${plural(n.rows, "row", "rows")} in window${n.silent ? " (Context/Bench)" : ""} — click to filter`}
            onClick={() => onPick(isOn ? "" : n.ticker)}
          >
            <span>{n.ticker}</span>
            <span className="fs-roster-ct">{n.rows}</span>
          </button>
        );
      })}
      {quiet.length > 6 && (
        <button type="button" className="fs-roster-more" onClick={() => setShowQuiet((v) => !v)}>
          {showQuiet ? "hide quiet" : `+${quiet.length} quiet (0)`}
        </button>
      )}
    </div>
  );
}

// --- the page ----------------------------------------------------------------------------------------------------

export default function FilingStream({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [days, setDays] = useState<StreamDays>(DEFAULT_DAYS);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [foldOpen, setFoldOpen] = useState<ReadonlySet<string>>(new Set());

  const cardsQ = useFilingStreamCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId =
    picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useFilingStream(asof, thesisId, days);
  const data: FilingStreamOut | undefined = q.data;

  // a new card starts with its rows closed and the card-specific filters cleared (its links and roles differ)
  useEffect(() => {
    setOpen(new Set());
    setFoldOpen(new Set());
    setFilters((f) => ({ ...f, link: "", name: "", text: "", role: "" }));
  }, [thesisId]);

  const options = useMemo(() => {
    const hits = filterCards(cards, query);
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, query, thesisId]);

  const filtered = useMemo(() => (data ? filterDays(data.groups, filters, data.links) : []), [data, filters]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Filing Stream's cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const toggle = (set: ReadonlySet<string>, key: string, put: (s: ReadonlySet<string>) => void) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    put(next);
  };
  const toggleKind = (k: StreamKind) =>
    setFilters((f) => {
      const kinds = new Set(f.kinds);
      if (kinds.has(k)) kinds.delete(k);
      else kinds.add(k);
      return { ...f, kinds };
    });
  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);

  // a control that cannot discriminate does not render (WB#3): kinds with no row in the window, a link / role
  // filter on a card with one link or no roles, "Counting names only" with no silent name, the fold toggle with
  // nothing to fold
  const kindsHere = data ? KIND_ORDER.filter((k) => (data.counts.by_kind.find((c) => c.kind === k)?.rows ?? 0) > 0) : [];
  const hasSilent = data?.names.some((n) => n.silent) ?? false;
  const shown = countRows(filtered);
  const folded = foldedByGroup(filtered);
  // the toggle says what it unfolds: the Form 4s alone, or every routine row when another group is on the page
  const onlyForm4 = (data?.groups ?? []).every((g) =>
    g.rows.every((r) => !r.routine || foldGroupOf(r) === "form4"),
  );
  const filtering = filtersActive(filters);

  return (
    <div className="fs">
      <div className="fs-controls">
        <div className="fs-pick">
          <span className="fs-seg-label">Card</span>
          <input
            type="search"
            className="fs-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="fs-select"
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
            <span className="fs-n">
              {plural(options.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        <div className="fs-seg" role="group" aria-label="Window">
          <span className="fs-seg-label">Window</span>
          {DAYS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`fs-seg-btn${days === o.value ? " on" : ""}`}
              aria-pressed={days === o.value}
              onClick={() => setDays(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Filing Stream.</div>
      ) : (
        <>
          <div className="fs-title">
            <button
              type="button"
              className="fs-card"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="fs-n">{plural(data.links_today, "link", "links")} today</span>
          </div>

          <div className="fs-filters">
            {kindsHere.length > 1 && (
              <div className="fs-seg" role="group" aria-label="Kinds">
                <span className="fs-seg-label">Kinds</span>
                {kindsHere.map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={`fs-seg-btn${filters.kinds.has(k) ? " on" : ""}`}
                    aria-pressed={filters.kinds.has(k)}
                    onClick={() => toggleKind(k)}
                  >
                    {KIND_LABEL[k]}{" "}
                    <span className="fs-n">{data.counts.by_kind.find((c) => c.kind === k)?.rows ?? 0}</span>
                  </button>
                ))}
              </div>
            )}
            {data.links.length > 1 && (
              <label className="fs-field">
                <span className="fs-seg-label">Link</span>
                <select
                  className="fs-select"
                  aria-label="Link"
                  value={filters.link}
                  onChange={(e) => setFilters((f) => ({ ...f, link: e.target.value }))}
                >
                  <option value="">All links</option>
                  {data.links.map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.label}
                      {l.kind === "old_link" ? " (not a link today)" : ""} · {plural(l.names, "name", "names")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {data.names.length > 1 && (
              <label className="fs-field">
                <span className="fs-seg-label">Name</span>
                <input
                  type="search"
                  className="fs-search"
                  aria-label="Find a name"
                  placeholder="Find a name"
                  list="fs-names"
                  value={filters.name}
                  onChange={(e) => setFilters((f) => ({ ...f, name: e.target.value }))}
                />
                <datalist id="fs-names">
                  {data.names.map((n) => (
                    <option key={n.key} value={n.ticker} />
                  ))}
                </datalist>
              </label>
            )}
            {data.roles.length > 0 && (
              <label className="fs-field">
                <span className="fs-seg-label">Role</span>
                <select
                  className="fs-select"
                  aria-label="Role"
                  value={filters.role}
                  onChange={(e) => setFilters((f) => ({ ...f, role: e.target.value }))}
                >
                  <option value="">All roles</option>
                  {data.roles.map((r) => (
                    <option key={r} value={r}>
                      {CONTEXT_ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {hasSilent && (
              <label className="fs-check" title="Leave out the names in Context and Bench, which never count toward the call">
                <input
                  type="checkbox"
                  checked={filters.countingOnly}
                  onChange={(e) => setFilters((f) => ({ ...f, countingOnly: e.target.checked }))}
                />
                Counting names only
              </label>
            )}
            {data.counts.rows > 1 && (
              <label className="fs-field">
                <span className="fs-seg-label">Text</span>
                <input
                  type="search"
                  className="fs-search"
                  aria-label="Filter text"
                  placeholder="Headline or form"
                  value={filters.text}
                  onChange={(e) => setFilters((f) => ({ ...f, text: e.target.value }))}
                />
              </label>
            )}
            {data.counts.routine > 0 && (
              <label
                className="fs-check"
                title={
                  onlyForm4
                    ? "Show each day's routine Form 4 filings (none holds a purchase) in place instead of one folded line"
                    : "Show every routine row in place instead of its folded line: the routine Form 4s (none holds a purchase), the prospectus supplements and the insider ownership filings with no transaction rows stored"
                }
              >
                <input
                  type="checkbox"
                  checked={filters.showRoutine}
                  onChange={(e) => setFilters((f) => ({ ...f, showRoutine: e.target.checked }))}
                />
                {onlyForm4 ? "Show routine Form 4s" : "Show routine rows"}
              </label>
            )}
            {filtering && (
              <button
                type="button"
                className="fs-clear"
                onClick={() =>
                  setFilters((f) => ({
                    ...DEFAULT_FILTERS,
                    kinds: new Set(KIND_ORDER),
                    showRoutine: f.showRoutine,
                  }))
                }
              >
                Clear filters
              </button>
            )}
          </div>

          <CadenceStrip days={filtered} windowStart={data.window_start} asof={data.asof} />
          <RosterStrip
            names={data.names}
            activeName={filters.name}
            onPick={(ticker) => setFilters((f) => ({ ...f, name: ticker }))}
          />

          {data.counts.rows === 0 ? (
            <div className="dash-note fs-empty">
              No stored filing-derived fact for this card's names in the last {data.days} days (see the limits below).
            </div>
          ) : shown === 0 ? (
            <div className="dash-note fs-empty">No row passes the filters.</div>
          ) : (
            <div className="fs-stream">
              {filtered.map((day) => (
                <section
                  key={day.date}
                  id={`fs-day-${day.date}`}
                  className="fs-day"
                  aria-label={fmtDayHeader(day.date)}
                >
                  <h2 className="fs-day-head">
                    {fmtDayHeader(day.date)} <span className="fs-n">{plural(day.rows.length, "row", "rows")}</span>
                  </h2>
                  <ul className="fs-list">
                    {foldEntries(day.rows, filters.showRoutine).map((entry) => {
                      const line = (row: FilingStreamRowOut) => (
                        <StreamLine
                          key={row.key}
                          row={row}
                          open={open.has(row.key)}
                          onToggle={() => toggle(open, row.key, setOpen)}
                          onSelectThesis={onSelectThesis}
                          thesisId={data.thesis_id}
                          thesisName={data.thesis_name}
                        />
                      );
                      if (entry.type === "row") return line(entry.row);
                      const foldKey = `${day.date}|${entry.key}`;
                      const isOpen = foldOpen.has(foldKey);
                      return (
                        <li key={`fold-${foldKey}`} className="fs-fold-item" data-fold={entry.group}>
                          <button
                            type="button"
                            className="fs-fold"
                            aria-expanded={isOpen}
                            onClick={() => toggle(foldOpen, foldKey, setFoldOpen)}
                          >
                            <span className="fs-chev" aria-hidden="true">
                              {isOpen ? "▾" : "▸"}
                            </span>
                            {foldEntryWords(entry)}
                          </button>
                          {isOpen && <ul className="fs-list fs-folded">{entry.rows.map(line)}</ul>}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}

          {workLines(data, shown, folded, filtering).map((line, i) => (
            <div key={i} className="dash-note fs-work">
              {line}
            </div>
          ))}
          {unresolvedWords(data.unresolved) && (
            <div className="dash-note fs-work">{unresolvedWords(data.unresolved)}</div>
          )}
          {data.roster_fallback && (
            <div className="dash-note fs-work">
              Today's roster stands in for this card: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <ul className="dash-note fs-limits">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
