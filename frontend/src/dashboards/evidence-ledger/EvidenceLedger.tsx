import { useEffect, useMemo, useState } from "react";

import { CONTEXT_ROLE_DESCRIPTION } from "../../workbench/format";
import type { DashboardProps } from "../registry";
import "./evidence-ledger.css";
import {
  AUTHORITY_LABEL,
  AUTHORITY_MEANS,
  AUTHORITY_ORDER,
  AUTHORITY_TAG,
  DEFAULT_FILTERS,
  KIND_LABEL,
  KIND_ORDER,
  LIMITS,
  NOT_EVIDENCE,
  ROSTER_PIT_FROM,
  WINDOW_OPTIONS,
  byWords,
  countOf,
  countRows,
  filterCards,
  filterDays,
  filterStanding,
  filtersActive,
  fmtDay,
  fmtDayHeader,
  nightWords,
  plural,
  traceWords,
  unresolvedWords,
  workLines,
  type Authority,
  type Filters,
  type LedgerKind,
} from "./ledgerView";
import {
  useEvidenceLedger,
  useEvidenceLedgerCards,
  type EvidenceLedgerOut,
  type EvidenceLedgerRowOut,
} from "./useEvidenceLedger";

// Evidence Ledger (docs/dashboards/evidence-ledger.md): ONE card's provenance as a dated list. Everything the
// recorded call and the stored facts rest on, each row marked by WHO put it there. Display only. Every row and
// count is computed server-side; this page picks the card, filters client side (class, kind, company, EDGAR
// link, date window, "Counting names only"), opens a row to its inputs and sources, and words the work. The
// class is a quiet word, never a color or a badge, and never a judgment of the row (#7 / WB#3).

const SLOW = "Reading this card's record — the first load after a run or a basket edit can take a moment.";

const isEdgarUrl = (url: string) => url.includes("sec.gov");

/** One ledger row: a ticker chip (opens the Cockpit on that name), the row's form, its label (opens the detail),
 *  its quiet tags (the class, Context / Bench) and the link to its first source. */
function LedgerLine({
  row,
  open,
  onToggle,
  onSelectThesis,
  thesisId,
  thesisName,
}: {
  row: EvidenceLedgerRowOut;
  open: boolean;
  onToggle: () => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
  thesisId: string;
  thesisName: string;
}) {
  const byline = byWords(row);
  const expandable = row.detail.length > 0 || row.sources.length > 0 || byline != null;
  const link = row.sources.find((s) => s.url != null && isEdgarUrl(s.url)) ?? row.sources.find((s) => s.url != null);
  const edgar = link?.url != null && isEdgarUrl(link.url);
  const more = row.sources.filter((s) => s.url != null).length - 1;
  const title = row.role ? CONTEXT_ROLE_DESCRIPTION[row.role] : undefined;
  return (
    <li className={`el-row${open ? " open" : ""}`} data-testid="el-row" data-kind={row.kind} data-authority={row.authority}>
      <div className="el-line">
        {row.ticker ? (
          <button
            type="button"
            className="el-chip"
            title={`Open ${thesisName} on ${row.ticker}`}
            onClick={() => onSelectThesis(thesisId, row.ticker)}
          >
            {row.ticker}
          </button>
        ) : (
          <span className="el-chip none" />
        )}
        <span className="el-form">{row.form ?? ""}</span>
        <span className="el-main">
          {expandable ? (
            <button type="button" className="el-head" aria-expanded={open} onClick={onToggle} title={title}>
              <span className="el-chev" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
              {row.label}
            </button>
          ) : (
            <span className="el-head plain" title={title}>
              {row.label}
            </span>
          )}
          <span className="el-tag" title={`${AUTHORITY_LABEL[row.authority]}: ${AUTHORITY_MEANS[row.authority]}`}>
            {AUTHORITY_TAG[row.authority]}
          </span>
          {row.silent && (
            <span className="el-tag" title="Never counts toward the call">
              {row.silent}
            </span>
          )}
        </span>
        {link?.url ? (
          <a
            className="el-src"
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            title={`${edgar ? "Open the filing on EDGAR" : "Open the stored source"}${more > 0 ? ` (${more} more in the row)` : ""}`}
            aria-label={`Open ${row.ticker ?? "card"} ${row.form ?? row.kind} on ${edgar ? "EDGAR" : "its source"}`}
          >
            {edgar ? "EDGAR" : "source"} ↗
          </a>
        ) : (
          <span className="el-src none" />
        )}
      </div>
      {open && (
        <div className="el-detail" data-testid="el-detail">
          <ul>
            <li>
              {AUTHORITY_LABEL[row.authority]}: {AUTHORITY_MEANS[row.authority]}
            </li>
            {byline && <li>{byline}</li>}
            {row.role && <li>{CONTEXT_ROLE_DESCRIPTION[row.role]}</li>}
            {row.detail.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
            {row.sources.map((s, i) => (
              <li key={`src-${i}`} className="el-source">
                <span>
                  Source: {s.source}
                  {s.ref ? ` · ${s.ref}` : ""}
                </span>
                {s.url && (
                  <>
                    {" "}
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {isEdgarUrl(s.url) ? "EDGAR" : "source"} ↗
                    </a>
                  </>
                )}
                {s.detail.length > 0 && (
                  <ul className="el-inputs">
                    {s.detail.map((line, j) => (
                      <li key={j}>{line}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

// --- the page ----------------------------------------------------------------------------------------------------

export default function EvidenceLedger({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  const cardsQ = useEvidenceLedgerCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId =
    picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useEvidenceLedger(asof, thesisId);
  const data: EvidenceLedgerOut | undefined = q.data;

  // a new card starts with its rows closed and the company filter cleared (its roster differs)
  useEffect(() => {
    setOpen(new Set());
    setFilters((f) => ({ ...f, name: "" }));
  }, [thesisId]);

  const options = useMemo(() => {
    const hits = filterCards(cards, query);
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, query, thesisId]);

  const standing = useMemo(() => (data ? filterStanding(data.standing, filters, data.asof) : []), [data, filters]);
  const days = useMemo(() => (data ? filterDays(data.groups, filters, data.asof) : []), [data, filters]);
  const allRows = useMemo(() => (data ? [...data.standing, ...data.groups.flatMap((g) => g.rows)] : []), [data]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Evidence Ledger's cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const toggleOpen = (key: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const toggleIn = <T,>(set: ReadonlySet<T>, v: T): Set<T> => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    return next;
  };
  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);

  // a control that cannot discriminate does not render (WB#3): a class or a kind with one value in the ledger, the
  // company filter on a one-company roster, the EDGAR and Context / Bench checks with no row they would cut, the
  // window with no dated row
  const classesHere = data ? AUTHORITY_ORDER.filter((a) => countOf(data.tallies.by_authority, a) > 0) : [];
  const kindsHere = data ? KIND_ORDER.filter((k) => countOf(data.tallies.by_kind, k) > 0) : [];
  const hasEdgar = allRows.some((r) => r.edgar);
  const hasSilent = allRows.some((r) => r.silent);
  const shown = standing.length + countRows(days);
  const filtering = filtersActive(filters);
  const trace = data ? traceWords(data.tallies) : null;

  const lines = (rows: readonly EvidenceLedgerRowOut[], d: EvidenceLedgerOut) =>
    rows.map((row) => (
      <LedgerLine
        key={row.key}
        row={row}
        open={open.has(row.key)}
        onToggle={() => toggleOpen(row.key)}
        onSelectThesis={onSelectThesis}
        thesisId={d.thesis_id}
        thesisName={d.thesis_name}
      />
    ));

  return (
    <div className="el">
      <div className="el-controls">
        <div className="el-pick">
          <span className="el-seg-label">Card</span>
          <input
            type="search"
            className="el-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="el-select"
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
            <span className="el-n">
              {plural(options.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Evidence Ledger.</div>
      ) : (
        <>
          <div className="el-title">
            <button
              type="button"
              className="el-card"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="el-n">{plural(data.tallies.rows, "row", "rows")}</span>
          </div>
          <div className="dash-note el-night">{nightWords(data)}</div>
          {trace && <div className="dash-note el-trace">{trace}</div>}

          <div className="el-filters">
            {classesHere.length > 1 && (
              <div className="el-seg" role="group" aria-label="Classes">
                <span className="el-seg-label">Put there by</span>
                {classesHere.map((a: Authority) => (
                  <button
                    key={a}
                    type="button"
                    className={`el-seg-btn${filters.authorities.has(a) ? " on" : ""}`}
                    aria-pressed={filters.authorities.has(a)}
                    title={`${AUTHORITY_LABEL[a]}: ${AUTHORITY_MEANS[a]}`}
                    onClick={() => setFilters((f) => ({ ...f, authorities: toggleIn(f.authorities, a) }))}
                  >
                    {AUTHORITY_LABEL[a]} <span className="el-n">{countOf(data.tallies.by_authority, a)}</span>
                  </button>
                ))}
              </div>
            )}
            {kindsHere.length > 1 && (
              <div className="el-seg" role="group" aria-label="Kinds">
                <span className="el-seg-label">Kinds</span>
                {kindsHere.map((k: LedgerKind) => (
                  <button
                    key={k}
                    type="button"
                    className={`el-seg-btn${filters.kinds.has(k) ? " on" : ""}`}
                    aria-pressed={filters.kinds.has(k)}
                    onClick={() => setFilters((f) => ({ ...f, kinds: toggleIn(f.kinds, k) }))}
                  >
                    {KIND_LABEL[k]} <span className="el-n">{countOf(data.tallies.by_kind, k)}</span>
                  </button>
                ))}
              </div>
            )}
            {data.names.length > 1 && (
              <label className="el-field">
                <span className="el-seg-label">Company</span>
                <input
                  type="search"
                  className="el-search"
                  aria-label="Find a company"
                  placeholder="Find a company"
                  list="el-names"
                  value={filters.name}
                  onChange={(e) => setFilters((f) => ({ ...f, name: e.target.value }))}
                />
                <datalist id="el-names">
                  {data.names.map((n) => (
                    <option key={n.key} value={n.ticker} />
                  ))}
                </datalist>
              </label>
            )}
            {data.groups.length > 0 && (
              <div className="el-seg" role="group" aria-label="Window">
                <span className="el-seg-label">Window</span>
                {WINDOW_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className={`el-seg-btn${filters.window === o.value ? " on" : ""}`}
                    aria-pressed={filters.window === o.value}
                    onClick={() => setFilters((f) => ({ ...f, window: o.value }))}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            )}
            {hasEdgar && (
              <label className="el-check" title="Only the rows with a source that links to EDGAR">
                <input
                  type="checkbox"
                  checked={filters.edgarOnly}
                  onChange={(e) => setFilters((f) => ({ ...f, edgarOnly: e.target.checked }))}
                />
                Only rows with an EDGAR link
              </label>
            )}
            {hasSilent && (
              <label className="el-check" title="Leave out the rows of companies in Context and Bench, which never count toward the call">
                <input
                  type="checkbox"
                  checked={filters.countingOnly}
                  onChange={(e) => setFilters((f) => ({ ...f, countingOnly: e.target.checked }))}
                />
                Counting names only
              </label>
            )}
          </div>

          {data.tallies.rows === 0 ? (
            <div className="dash-note el-empty">
              Nothing is recorded for this card at this as-of: no trigger, stored fact, catalyst, kill criterion, evidence link or
              role (see the limits below).
            </div>
          ) : shown === 0 ? (
            <div className="dash-note el-empty">No row passes the filters.</div>
          ) : (
            <div className="el-ledger">
              {standing.length > 0 && (
                <section className="el-day el-standing" aria-label="Standing">
                  <h2 className="el-day-head">
                    Standing <span className="el-n">{plural(standing.length, "row", "rows")}</span>
                    <span className="el-sub">no date: the card's own entries and roles no roster snapshot dates</span>
                  </h2>
                  <ul className="el-list">{lines(standing, data)}</ul>
                </section>
              )}
              {days.map((day) => (
                <section key={day.date} className="el-day" aria-label={fmtDayHeader(day.date)}>
                  <h2 className="el-day-head">
                    {fmtDayHeader(day.date)} <span className="el-n">{plural(day.rows.length, "row", "rows")}</span>
                  </h2>
                  <ul className="el-list">{lines(day.rows, data)}</ul>
                </section>
              ))}
            </div>
          )}

          {workLines(data, shown, filtering).map((line, i) => (
            <div key={i} className="dash-note el-work">
              {line}
            </div>
          ))}
          {unresolvedWords(data.unresolved) && (
            <div className="dash-note el-work">{unresolvedWords(data.unresolved)}</div>
          )}
          {data.roster_fallback && (
            <div className="dash-note el-work">
              Today's roster stands in for this card: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <div className="dash-note el-work">{NOT_EVIDENCE}</div>
          <ul className="dash-note el-limits">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
