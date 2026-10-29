import { memo, useCallback, useMemo, useState } from "react";

import { businessTypeLabel } from "../../util/format";
import { contextRoleTag } from "../../workbench/format";
import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./peer-table.css";
import {
  COLUMNS,
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  LIMITS,
  SCOPE_LABEL,
  SCOPE_ORDER,
  capCell,
  chipLinks,
  chipWords,
  companyMatches,
  controlsFor,
  coverageLines,
  coverageOf,
  coverageTitle,
  coverageWords,
  filtersActive,
  firstKey,
  groupFigures,
  groupLabel,
  insiderCell,
  nextSort,
  numberCell,
  optionsFor,
  plural,
  resolveScope,
  revenueCell,
  rosterWords,
  runwayCell,
  sideCell,
  sortCompanies,
  spyCell,
  tagWords,
  tierLabel,
  unresolvedSummary,
  workLines,
  yoyCell,
  fmtPct,
  fmtUsd,
  type Cell,
  type ColumnDef,
  type Filters,
  type Scope,
  type Sort,
  type SortKey,
  type TierPick,
} from "./tableView";
import { usePeerScopes, usePeerTable, type PeerCompanyOut, type PeerTableOut, type ScopeKind } from "./usePeerTable";

// Peer Table (docs/dashboards/peer-table.md): the names across the cards counted at the as-of side by side, one
// scope at a time (a business type, a supersector, one card, or every card), each company ONCE, grouped by what it
// does, with every blank counted. Display only. Every cell, group figure and coverage count is computed
// server-side; this page picks the scope, keeps the sort and the filters (component state only) and words the work.
// No badges and no color by tier or role: loudness marks the exception only (#7 / WB#3). A cap tier is a slicing
// dimension, never a call input.

const SLOW =
  "Reading the names in this scope — the first load after a run or a basket edit can take a moment.";
const SLOW_ALL =
  "All live cards reads every company on every counted card — the first load after a run or a basket edit can take a minute.";

function SortTh({
  col,
  label,
  title,
  sort,
  onSort,
  className,
  coverage,
  coverageHint,
}: {
  col: SortKey;
  label: string;
  title: string;
  sort: Sort;
  onSort: (key: SortKey) => void;
  className?: string;
  coverage?: string;
  coverageHint?: string;
}) {
  const on = sort?.key === col;
  return (
    <th className={className} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`pt-sort${on ? " on" : ""}`} title={title} onClick={() => onSort(col)}>
        {label}
        {on && (
          <span className="pt-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
      {coverage && (
        <span className="pt-cov" title={coverageHint}>
          {coverage}
        </span>
      )}
    </th>
  );
}

function CellTd({ cell, num = true }: { cell: Cell; num?: boolean }) {
  return (
    <td className={num ? "pt-num" : undefined}>
      <span className={`pt-cell${cell.tone ? ` ${cell.tone}` : ""}${cell.gray ? " gray" : ""}`} title={cell.title}>
        {cell.text}
      </span>
    </td>
  );
}

/** One company's cells. */
function cellsOf(c: PeerCompanyOut): Record<Exclude<SortKey, "company" | "cards" | "business_type">, Cell> {
  return {
    cap: capCell(c.cap),
    revenue: revenueCell(c.revenue),
    yoy: yoyCell(c.yoy),
    runway: runwayCell(c.runway),
    overhang: numberCell(
      c.overhang,
      (v) => `${v.toFixed(1)}%`,
      (v) => `Convertible-note overhang: ${v.toFixed(1)}% of shares outstanding, the dilution clock's own number`,
    ),
    purity: numberCell(
      { value: c.purity.value, gap: c.purity.gap, note: null },
      (v) => `${Math.round(v)}%`,
      (v) => `${v.toFixed(1)}% of revenue from the largest line${c.purity.segment ? `: ${c.purity.segment}` : ""}`,
    ),
    off_high: numberCell(
      c.off_high,
      fmtPct,
      (v) => `${fmtPct(v)} against the 52-week closing high`,
    ),
    sma50: sideCell(c.sma50, 50),
    sma200: sideCell(c.sma200, 200),
    rvol20: numberCell(
      c.rvol20,
      (v) => `${v.toFixed(1)}×`,
      (v) => `The as-of bar's volume is ${v.toFixed(2)}× the mean of the 20 bars before it`,
    ),
    adv: numberCell(
      c.adv,
      (v) => fmtUsd(v),
      (v) => `Average dollar volume over the last 20 bars: ${fmtUsd(v)}`,
    ),
    vs_spy: spyCell(c.vs_spy),
    insider: insiderCell(c.insider),
  };
}

const COLUMN_ORDER = COLUMNS.filter((d) => d.key !== "cards" && d.key !== "business_type");

type RowProps = {
  c: PeerCompanyOut;
  scope: ScopeKind;
  open: boolean;
  onToggle: (key: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
};

const CompanyRow = memo(function CompanyRow({ c, scope, open, onToggle, onSelectThesis }: RowProps) {
  const cells = cellsOf(c);
  const first = c.cards[0];
  const tag = tagWords(c, contextRoleTag);
  const where = c.cards.map(chipWords).join("; ");
  return (
    <tr data-testid="pt-row">
      <td className="pt-co">
        <button
          type="button"
          className="pt-chip"
          title={`Open ${first.thesis_name} on ${first.ticker}`}
          onClick={() => onSelectThesis(first.thesis_id, first.ticker)}
        >
          {c.ticker}
        </button>
        {c.name && (
          <span className="pt-name" title={c.name}>
            {c.name}
          </span>
        )}
      </td>
      <td className="pt-cards">
        {scope === "card" ? (
          <span className="pt-links" title={where}>
            {chipLinks(first)}
          </span>
        ) : (
          <button
            type="button"
            className="pt-cards-btn"
            aria-expanded={open}
            title={where}
            onClick={() => onToggle(c.key)}
          >
            {plural(c.cards.length, "card", "cards")}
          </button>
        )}
        {tag && <span className={`pt-tag${c.silent_only ? " silent" : ""}`}>{tag}</span>}
        {open && scope !== "card" && (
          <div className="pt-chips">
            {c.cards.map((k) => (
              <button
                key={k.thesis_id}
                type="button"
                className={`pt-card${k.silent ? " quiet" : ""}`}
                title={`Open ${chipWords(k)}`}
                onClick={() => onSelectThesis(k.thesis_id, k.ticker)}
              >
                <span className="pt-card-name">{k.thesis_name}</span>
                <span className="pt-card-links">{` · ${chipLinks(k)}`}</span>
              </button>
            ))}
          </div>
        )}
      </td>
      <td className="pt-bt">
        {c.business_type ? businessTypeLabel(c.business_type) : ""}
        {c.royalty && (
          <span className="pt-roy" title="The company's name marks it a royalty or streaming house">
            {" "}
            royalty
          </span>
        )}
      </td>
      {COLUMN_ORDER.map((d) => (
        <CellTd key={d.key} cell={cells[d.key as keyof typeof cells]} />
      ))}
    </tr>
  );
});

function GroupRow({
  g,
  stats,
  shown,
  filtering,
}: {
  g: PeerTableOut["groups"][number];
  stats: PeerTableOut["groups"][number]["stats"];
  shown: number;
  filtering: boolean;
}) {
  const figs = groupFigures(stats);
  return (
    <tr className={`pt-group depth-${g.depth}`} data-testid="pt-group">
      <th scope="rowgroup" className="pt-co pt-group-name">
        <span className="pt-group-label">{groupLabel(g)}</span>
        {g.kind === "old_link" && (
          <span className="pt-n" title="A link the roster still names that is no longer on the card">
            {" "}
            not on the card now
          </span>
        )}
        <span className="pt-n">{` · ${plural(stats.names, "name", "names")}`}</span>
        {filtering && g.members.length > 0 && <span className="pt-n">{` · ${shown.toLocaleString("en-US")} shown`}</span>}
      </th>
      {COLUMNS.map((d) => {
        const f = figs[d.key];
        return (
          <td key={d.key} className={d.num ? "pt-num" : undefined}>
            {f && (
              <span className="pt-fig" title={f.title}>
                {f.text}
                <span className="pt-sub">{f.sub}</span>
              </span>
            )}
          </td>
        );
      })}
    </tr>
  );
}

export default function PeerTable({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<Scope | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const [openCards, setOpenCards] = useState<ReadonlySet<string>>(new Set());
  const sc = usePeerScopes(asof);
  const scope = sc.data ? resolveScope(picked, sc.data) : null;
  const q = usePeerTable(asof, scope?.kind ?? null, scope?.key ?? null);
  const data = q.data;

  const byKey = useMemo(() => new Map((data?.companies ?? []).map((c) => [c.key, c])), [data]);
  const controls = useMemo(() => controlsFor(data?.companies ?? []), [data]);
  const toggle = useCallback(
    (key: string) =>
      setOpenCards((s) => {
        const next = new Set(s);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      }),
    [],
  );
  const filtering = filtersActive(filters);
  const kind = data?.scope ?? "business_type";
  const groups = useMemo(
    () =>
      (data?.groups ?? []).map((g) => {
        const members = g.members.flatMap((k) => {
          const c = byKey.get(k);
          return c && companyMatches(c, filters) ? [c] : [];
        });
        return { g, rows: sortCompanies(members, sort, kind) };
      }),
    [data, byKey, filters, sort, kind],
  );
  const shown = useMemo(() => groups.reduce((n, x) => n + x.rows.length, 0), [groups]);

  if (sc.isLoading || (!sc.data && sc.isFetching)) return <div className="dash-note">Loading the scopes…</div>;
  if (sc.error || !sc.data) return <div className="dash-note">Could not load the Peer Table.</div>;
  const scopes = sc.data;
  if (!scope) {
    return (
      <div className="dash-note pt-empty">
        No company is on a card counted at this as-of ({plural(scopes.cards_counted, "card", "cards")} counted). Add names
        to a card's basket and they appear here, grouped by what they do.
      </div>
    );
  }

  const options = optionsFor(scope.kind, scopes);
  const stale = !!data && (data.asof !== asof || data.scope !== scope.kind || (scope.kind !== "all" && data.key !== scope.key));
  const slow = scope.kind === "all" ? SLOW_ALL : SLOW;
  const counting = filters.countingOnly;

  const pickKind = (k: ScopeKind) => setPicked({ kind: k, key: firstKey(k, scopes) });
  const onSort = (key: SortKey) => setSort((s) => nextSort(s, key));
  const toggleTier = (t: TierPick) =>
    setFilters((f) => ({ ...f, tiers: f.tiers.includes(t) ? f.tiers.filter((x) => x !== t) : [...f.tiers, t] }));

  const body = () => {
    if (q.error && !data) return <div className="dash-note">Could not load this scope.</div>;
    if (!data) return <div className="dash-note">{slow}</div>;
    const coverage = counting ? data.coverage_counting : data.coverage;
    const unresolved = unresolvedSummary(data);
    const roster = rosterWords(data.roster_fallback_cards);
    return (
      <>
        {data.companies.length === 0 ? (
          <div className="dash-note pt-empty">
            No company in this scope. {data.scope === "card" ? "The card's roster holds no resolved company at this as-of." : ""}
          </div>
        ) : (
          <>
            <div className="pt-filters">
              {controls.query && (
                <label className="pt-field">
                  <span className="pt-seg-label">Find</span>
                  <input
                    type="search"
                    className="pt-search"
                    aria-label="Find a ticker or company"
                    placeholder="Ticker or name"
                    value={filters.query}
                    onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
                  />
                </label>
              )}
              {controls.tiers.length > 1 && (
                <span className="pt-field" role="group" aria-label="Cap tier">
                  <span className="pt-seg-label">Cap tier</span>
                  {controls.tiers.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`pt-tier${filters.tiers.includes(t) ? " on" : ""}`}
                      aria-pressed={filters.tiers.includes(t)}
                      title={t === "none" ? "Companies with no approximate cap" : `Companies in the ${t} band`}
                      onClick={() => toggleTier(t)}
                    >
                      {tierLabel(t)}
                    </button>
                  ))}
                </span>
              )}
              {controls.countingOnly && (
                <label className="pt-check" title="Leave out the companies whose every row is Context or Bench: they never count toward a call">
                  <input
                    type="checkbox"
                    checked={filters.countingOnly}
                    onChange={(e) => setFilters((f) => ({ ...f, countingOnly: e.target.checked }))}
                  />
                  Counting names only
                </label>
              )}
              {controls.withRevenue && (
                <label className="pt-check">
                  <input
                    type="checkbox"
                    checked={filters.withRevenue}
                    onChange={(e) => setFilters((f) => ({ ...f, withRevenue: e.target.checked }))}
                  />
                  Only names with revenue
                </label>
              )}
              {controls.withFact && (
                <label className="pt-check" title="Shares, cash and burn, revenue mix or a convertible note: the facts a name is marked for">
                  <input
                    type="checkbox"
                    checked={filters.withFact}
                    onChange={(e) => setFilters((f) => ({ ...f, withFact: e.target.checked }))}
                  />
                  Only names with a Workbench fact
                </label>
              )}
              {filtering && (
                <button type="button" className="pt-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
                  clear filters
                </button>
              )}
              {sort && (
                <button type="button" className="pt-clear" onClick={() => setSort(DEFAULT_SORT)}>
                  back to group order
                </button>
              )}
              {q.isFetching && <span className="dash-note">{stale ? slow : "updating…"}</span>}
            </div>

            {shown === 0 ? (
              <div className="dash-note pt-empty">No company passes the filters.</div>
            ) : null}
            <div className="pt-scroll">
              <table className="pt-table">
                <thead>
                  <tr>
                    <SortTh
                      col="company"
                      label="Company"
                      title="Sort by ticker, A to Z"
                      sort={sort}
                      onSort={onSort}
                      className="pt-co"
                    />
                    {COLUMNS.map((d: ColumnDef) => {
                      const cov = d.coverage ? coverageOf(coverage, d.coverage) : undefined;
                      return (
                        <SortTh
                          key={d.key}
                          col={d.key}
                          label={d.key === "cards" && data.scope === "card" ? "Links" : d.label}
                          title={d.key === "cards" && data.scope === "card" ? "Sort by the links the company sits in" : d.title}
                          sort={sort}
                          onSort={onSort}
                          className={`${d.num ? "pt-num" : ""}${d.key === "cards" ? " pt-cards" : ""}`.trim() || undefined}
                          coverage={cov ? coverageWords(cov) : undefined}
                          coverageHint={cov ? coverageTitle(cov) : undefined}
                        />
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {groups.map(({ g, rows }) => (
                    <GroupBlock
                      key={g.key}
                      g={g}
                      stats={counting ? g.stats_counting : g.stats}
                      rows={rows}
                      filtering={filtering}
                      scope={data.scope}
                      openCards={openCards}
                      onToggle={toggle}
                      onSelectThesis={onSelectThesis}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="dash-note pt-work">
          {shown === data.companies.length
            ? plural(shown, "company", "companies")
            : `showing ${shown.toLocaleString("en-US")} of ${plural(data.companies.length, "company", "companies")}`}
          {filtering ? " after the filters" : ""}
        </div>
        {workLines(data, shown, counting).map((line, i) => (
          <div key={i} className="dash-note pt-work">
            {line}
          </div>
        ))}
        {data.companies.length > 0 && (
          <details className="dash-note pt-work pt-cov-details">
            <summary>column coverage — every blank counted by reason</summary>
            <ul className="pt-cov-list">
              {coverageLines(coverage).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </details>
        )}
        <FlaggedNote flagged={data.flagged} />
        {unresolved && (
          <details className="dash-note pt-work pt-unresolved">
            <summary>{unresolved}</summary>
            <ul className="pt-cov-list">
              {data.unresolved.map((u, i) => (
                <li key={`${u.thesis_id}:${u.ticker}:${i}`}>
                  {u.ticker} · {u.thesis_name}
                </li>
              ))}
            </ul>
          </details>
        )}
        {roster && <div className="dash-note pt-work">{roster}</div>}
        <ul className="dash-note pt-limits">
          {LIMITS.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </>
    );
  };

  return (
    <div className="pt">
      <div className="pt-scope">
        <div className="pt-seg" role="tablist" aria-label="Scope">
          {SCOPE_ORDER.map((k) => {
            const n = k === "all" ? scopes.companies : optionsFor(k, scopes).length;
            return (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={scope.kind === k}
                disabled={n === 0}
                className={`pt-tab${scope.kind === k ? " on" : ""}${n === 0 ? " zero" : ""}`}
                onClick={() => pickKind(k)}
              >
                {SCOPE_LABEL[k]}
              </button>
            );
          })}
        </div>
        {scope.kind !== "all" && (
          <label className="pt-field">
            <span className="pt-seg-label">{SCOPE_LABEL[scope.kind]}</span>
            <select
              className="pt-select"
              aria-label={SCOPE_LABEL[scope.kind]}
              value={scope.key ?? ""}
              onChange={(e) => setPicked({ kind: scope.kind, key: e.target.value })}
            >
              {options.map((o) => (
                <option key={o.key} value={o.key} title={o.hint}>
                  {o.label} · {o.companies.toLocaleString("en-US")}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="dash-note pt-count">
          {plural(scopes.companies, "company", "companies")} on {plural(scopes.cards_counted, "counted card", "counted cards")}
        </span>
      </div>
      {body()}
    </div>
  );
}

function GroupBlock({
  g,
  stats,
  rows,
  filtering,
  scope,
  openCards,
  onToggle,
  onSelectThesis,
}: {
  g: PeerTableOut["groups"][number];
  stats: PeerTableOut["groups"][number]["stats"];
  rows: PeerCompanyOut[];
  filtering: boolean;
  scope: ScopeKind;
  openCards: ReadonlySet<string>;
  onToggle: (key: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  return (
    <>
      <GroupRow g={g} stats={stats} shown={rows.length} filtering={filtering} />
      {rows.map((c) => (
        <CompanyRow key={c.key} c={c} scope={scope} open={openCards.has(c.key)} onToggle={onToggle} onSelectThesis={onSelectThesis} />
      ))}
    </>
  );
}
