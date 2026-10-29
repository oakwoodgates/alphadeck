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
  foldWords,
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
  type FilingStreamOut,
  type FilingStreamRowOut,
  type StreamDays,
} from "./useFilingStream";

// Filing Stream (docs/dashboards/filing-stream.md): ONE card's names' filing-derived facts as a dated stream,
// newest day first, as they were readable at the as-of. Display only. Every row and count is computed server-side
// (the shared filing reader); this page picks the card and the window, filters client side (kind, link, name,
// role, "Counting names only"), folds each day's routine Form 4s into one quiet line, and words the work. No
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
  const source = row.url?.includes("sec.gov") ? "EDGAR" : "source";
  return (
    <li className={`fs-row${open ? " open" : ""}`} data-testid="fs-row" data-kind={row.kind}>
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
          <a
            className="fs-src"
            href={row.url}
            target="_blank"
            rel="noopener noreferrer"
            title={source === "EDGAR" ? "Open the filing on EDGAR" : "Open the stored source"}
            aria-label={`Open ${row.ticker} ${row.form} on ${source === "EDGAR" ? "EDGAR" : "its source"}`}
          >
            {source} ↗
          </a>
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
        </div>
      )}
    </li>
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
    setFilters((f) => ({ ...f, link: "", name: "", role: "" }));
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
  const foldedCount = filtered.reduce((n, d) => n + d.rows.filter((r) => r.routine).length, 0);
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
            {data.counts.routine > 0 && (
              <label className="fs-check" title="Show each day's routine Form 4 filings (none holds a purchase) in place instead of one folded line">
                <input
                  type="checkbox"
                  checked={filters.showRoutine}
                  onChange={(e) => setFilters((f) => ({ ...f, showRoutine: e.target.checked }))}
                />
                Show routine Form 4s
              </label>
            )}
          </div>

          {data.counts.rows === 0 ? (
            <div className="dash-note fs-empty">
              No stored filing-derived fact for this card's names in the last {data.days} days (see the limits below).
            </div>
          ) : shown === 0 ? (
            <div className="dash-note fs-empty">No row passes the filters.</div>
          ) : (
            <div className="fs-stream">
              {filtered.map((day) => (
                <section key={day.date} className="fs-day" aria-label={fmtDayHeader(day.date)}>
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
                      const isOpen = foldOpen.has(day.date);
                      return (
                        <li key={`fold-${day.date}`} className="fs-fold-item">
                          <button
                            type="button"
                            className="fs-fold"
                            aria-expanded={isOpen}
                            onClick={() => toggle(foldOpen, day.date, setFoldOpen)}
                          >
                            <span className="fs-chev" aria-hidden="true">
                              {isOpen ? "▾" : "▸"}
                            </span>
                            {foldWords(entry.rows.length)}
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

          {workLines(data, shown, foldedCount, filtering).map((line, i) => (
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
