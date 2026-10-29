import { useMemo, useState } from "react";

import { CONTEXT_ROLE_DESCRIPTION, contextRoleTag } from "../../workbench/format";
import { businessTypeLabel } from "../../util/format";
import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./read-through.css";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  FILING_WINDOWS,
  LIMITS,
  ROLE_ORDER,
  ROLE_TAB_LABEL,
  businessTypeOptions,
  cardNames,
  catalystWords,
  chipLinks,
  chipWords,
  filingWords,
  filterRows,
  filtersActive,
  inTab,
  nextSort,
  plural,
  rosterWords,
  sortRows,
  tabCounts,
  tapeCell,
  unresolvedWords,
  workLines,
  type Filters,
  type Sort,
  type SortKey,
  type TabKey,
} from "./tableView";
import { useReadThrough, type ReadThroughRowOut, type ReadThroughSpyOut } from "./useReadThrough";

// Read-Throughs (docs/dashboards/read-through.md): every company the operator tagged with a context role on a
// card counted at the as-of, one row per company and role, with its cards, business type, newest filing,
// next dated catalyst and tape against SPY. Display only. Every row and number is computed server-side;
// this page keeps the tab, the sort and the filters (component state only) and words the work. No badges and no
// color by role: loudness marks the exception only (#7 / WB#3).

const SLOW = "Reading every role company's year of filings and tape — the first load after a run or a basket edit can take a moment.";

const SORT_TITLE: Record<SortKey, string> = {
  company: "Sort by ticker, A to Z",
  role: "Sort by role, payers first",
  cards: "Sort by the first card's name",
  business_type: "Sort by business type",
  last_filing: "Sort by the date of the newest filing",
  next_catalyst: "Sort by the date of the next dated catalyst",
  vs_spy_7d: "Sort by the 7 trading-day return minus SPY's, in percentage points",
  vs_spy_30d: "Sort by the 30 trading-day return minus SPY's, in percentage points",
};

function SortTh({
  col,
  label,
  sort,
  onSort,
  className,
}: {
  col: SortKey;
  label: string;
  sort: Sort;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const on = sort.key === col;
  return (
    <th className={className} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`rt-sort${on ? " on" : ""}`} title={SORT_TITLE[col]} onClick={() => onSort(col)}>
        {label}
        {on && (
          <span className="rt-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
    </th>
  );
}

function TapeTd({ row, which, spy }: { row: ReadThroughRowOut; which: "7d" | "30d"; spy: ReadThroughSpyOut | null }) {
  const c = tapeCell(row.tape, which, spy);
  return (
    <td className="rt-num">
      <span className={`rt-tape${c.tone ? ` ${c.tone}` : ""}${c.gray ? " gray" : ""}`} title={c.title}>
        {c.text}
      </span>
    </td>
  );
}

export default function ReadThrough({ asof, onSelectThesis }: DashboardProps) {
  const [tab, setTab] = useState<TabKey>("all");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const q = useReadThrough(asof);
  const data = q.data;

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const filtered = useMemo(() => filterRows(rows, filters), [rows, filters]);
  const counts = useMemo(() => tabCounts(filtered), [filtered]);
  const shown = useMemo(() => sortRows(inTab(filtered, tab), sort), [filtered, tab, sort]);
  const types = useMemo(() => businessTypeOptions(rows), [rows]);
  const cards = useMemo(() => cardNames(rows), [rows]);

  if (q.isLoading || (!data && q.isFetching)) return <div className="dash-note">{SLOW}</div>;
  if (q.error || !data) return <div className="dash-note">Could not load the Read-Throughs.</div>;

  const stale = data.asof !== asof;
  const filtering = filtersActive(filters);
  // a control that cannot discriminate does not render (WB#3): the card query with fewer than two cards, the
  // business-type select with one type, the filing window with no filing in the table
  const showCard = cards.length > 1;
  const showType = types.length > 1;
  const showWindow = data.rows_with_filing > 0;
  const unresolved = unresolvedWords(data);
  const roster = rosterWords(data.roster_fallback_cards);

  return (
    <div className="rt">
      {data.rows.length > 0 && (
        <>
          <div className="rt-tabs" role="tablist" aria-label="Roles">
            {(["all", ...ROLE_ORDER] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                className={`rt-tab${tab === t ? " on" : ""}${counts[t] === 0 ? " zero" : ""}`}
                title={t === "all" ? undefined : CONTEXT_ROLE_DESCRIPTION[t]}
                onClick={() => setTab(t)}
              >
                {t === "all" ? "All roles" : ROLE_TAB_LABEL[t]} <span className="rt-n">{counts[t].toLocaleString("en-US")}</span>
              </button>
            ))}
            {q.isFetching && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
          </div>

          <div className="rt-filters">
            {showCard && (
              <label className="rt-field">
                <span className="rt-seg-label">Card</span>
                <input
                  type="search"
                  className="rt-search"
                  aria-label="Find a card"
                  placeholder="Find a card"
                  list="rt-cards"
                  value={filters.card}
                  onChange={(e) => setFilters((f) => ({ ...f, card: e.target.value }))}
                />
                <datalist id="rt-cards">
                  {cards.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </label>
            )}
            {showType && (
              <label className="rt-field">
                <span className="rt-seg-label">Business type</span>
                <select
                  className="rt-select"
                  aria-label="Business type"
                  value={filters.businessType}
                  onChange={(e) => setFilters((f) => ({ ...f, businessType: e.target.value }))}
                >
                  <option value="">All types</option>
                  {types.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label} · {t.rows.toLocaleString("en-US")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {showWindow && (
              <label className="rt-field" title="Keep only the rows whose newest filing is this recent">
                <span className="rt-seg-label">Filed in the last</span>
                <select
                  className="rt-select"
                  aria-label="Filed in the last"
                  value={filters.withinDays}
                  onChange={(e) => setFilters((f) => ({ ...f, withinDays: Number(e.target.value) }))}
                >
                  {FILING_WINDOWS.map((w) => (
                    <option key={w.value} value={w.value}>
                      {w.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {filtering && (
              <button type="button" className="rt-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
                clear filters
              </button>
            )}
          </div>
        </>
      )}

      {data.rows.length === 0 ? (
        <div className="dash-note rt-empty">
          No company carries a context role on a card counted at this as-of. A role is set on a company's row in the DD
          rail of a card's basket.
        </div>
      ) : shown.length === 0 ? (
        <div className="dash-note rt-empty">
          {tab === "all" || filtered.length === 0
            ? "No row passes the filters."
            : filtering
              ? `No ${contextRoleTag(tab)} row passes the filters.`
              : `No company holds the ${contextRoleTag(tab)} role on a card counted at this as-of.`}
        </div>
      ) : (
        <div className="rt-scroll">
          <table className="rt-table">
            <thead>
              <tr>
                <SortTh col="company" label="Company" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} className="rt-co" />
                <SortTh col="role" label="Role" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} />
                <SortTh col="cards" label="Cards" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} className="rt-cards" />
                <SortTh col="business_type" label="Business type" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} />
                <SortTh col="last_filing" label="Last filing" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} />
                <SortTh col="next_catalyst" label="Next dated catalyst" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} />
                <SortTh col="vs_spy_7d" label="7D vs SPY" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} className="rt-num" />
                <SortTh col="vs_spy_30d" label="30D vs SPY" sort={sort} onSort={(k) => setSort((s) => nextSort(s, k))} className="rt-num" />
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const first = r.cards[0];
                const filing = r.last_filing ? filingWords(r.last_filing) : null;
                const next = r.next_catalyst ? catalystWords(r.next_catalyst) : null;
                const edgar = r.last_filing?.url?.includes("sec.gov");
                return (
                  <tr key={r.key} data-testid="rt-row">
                    <td className="rt-co">
                      <button
                        type="button"
                        className="rt-chip"
                        title={`Open ${first.thesis_name} on ${first.ticker}`}
                        onClick={() => onSelectThesis(first.thesis_id, first.ticker)}
                      >
                        {r.ticker}
                      </button>
                      {r.name && (
                        <span className="rt-name" title={r.name}>
                          {r.name}
                        </span>
                      )}
                    </td>
                    <td>
                      <span className="rt-role" title={CONTEXT_ROLE_DESCRIPTION[r.role]}>
                        {contextRoleTag(r.role)}
                      </span>
                    </td>
                    <td className="rt-cards">
                      <div className="rt-chips">
                        {r.cards.map((c) => {
                          const links = chipLinks(c);
                          return (
                            <button
                              key={c.thesis_id}
                              type="button"
                              className={`rt-card${c.silent ? " quiet" : ""}`}
                              title={`Open ${chipWords(c)}`}
                              onClick={() => onSelectThesis(c.thesis_id)}
                            >
                              <span className="rt-card-name">{c.thesis_name}</span>
                              {links && <span className="rt-card-links">{` · ${links}`}</span>}
                            </button>
                          );
                        })}
                      </div>
                    </td>
                    <td className="rt-bt">{r.business_type ? businessTypeLabel(r.business_type) : ""}</td>
                    <td className="rt-fil">
                      {filing && r.last_filing ? (
                        <div className="rt-fil-line">
                          <span className="rt-fil-text" title={filing.full}>
                            {filing.short}
                          </span>
                          {r.last_filing.url && (
                            <a
                              className="rt-src"
                              href={r.last_filing.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={edgar ? "Open the filing on EDGAR" : "Open the stored source"}
                              aria-label={`Open ${r.ticker} ${r.last_filing.form} on ${edgar ? "EDGAR" : "its source"}`}
                            >
                              {edgar ? "EDGAR" : "source"} ↗
                            </a>
                          )}
                        </div>
                      ) : (
                        <span className="rt-none">none stored in the last year</span>
                      )}
                    </td>
                    <td className="rt-cat">
                      {next ? (
                        <span className="rt-cat-text" title={next.full}>
                          {next.short}
                        </span>
                      ) : (
                        <span className="rt-none">—</span>
                      )}
                    </td>
                    <TapeTd row={r} which="7d" spy={data.spy} />
                    <TapeTd row={r} which="30d" spy={data.spy} />
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data.rows.length > 0 && (
        <div className="dash-note rt-work">
          {shown.length === data.rows.length
            ? `${plural(shown.length, "row", "rows")}`
            : `showing ${shown.length.toLocaleString("en-US")} of ${plural(data.rows.length, "row", "rows")}`}
          {filtering ? " after the filters" : ""}
        </div>
      )}
      {workLines(data, shown.length).map((line, i) => (
        <div key={i} className="dash-note rt-work">
          {line}
        </div>
      ))}
      {data.cards_without_roles.length > 0 && (
        <details className="dash-note rt-work rt-none-cards">
          <summary>
            {plural(data.cards_without_roles.length, "card has", "cards have")} no role set — roles are set on a card's basket
          </summary>
          <ul className="rt-none-list">
            {data.cards_without_roles.map((c) => (
              <li key={c.thesis_id}>
                <button type="button" className="rt-none-card" title={`Open ${c.thesis_name}`} onClick={() => onSelectThesis(c.thesis_id)}>
                  {c.thesis_name}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <FlaggedNote flagged={data.flagged} />
      {unresolved && <div className="dash-note rt-work">{unresolved}</div>}
      {roster && <div className="dash-note rt-work">{roster}</div>}
      <ul className="dash-note rt-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
