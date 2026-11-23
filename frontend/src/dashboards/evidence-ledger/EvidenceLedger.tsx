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
import { ExternalLink, isSecLink } from "../../components/ExternalLink";
import { RecordStartNote } from "../RecordStartNote";

// Evidence Ledger (docs/dashboards/evidence-ledger.md): ONE card's provenance as a dated list. Everything the
// recorded call and the stored facts rest on, each row marked by WHO put it there. Display only. Every row and
// count is computed server-side; this page picks the card, filters client side (class, kind, company, EDGAR
// link, date window, "Counting names only"), opens a row to its inputs and sources, and words the work. The
// class is a quiet word, never a color or a badge, and never a judgment of the row (#7 / WB#3).

const SLOW = "Reading this card's record — the first load after a run or a basket edit can take a moment.";


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
  const link = row.sources.find((s) => isSecLink(s.url)) ?? row.sources.find((s) => s.url != null);
  const edgar = isSecLink(link?.url);
  const more = row.sources.filter((s) => s.url != null).length - 1;
  const hasPrice = row.sources.some((s) => s.source === "price");
  const isSignal = row.kind === "trigger" || row.kind === "risk_signal";
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
          {row.reconstructed && (
            <span className="el-recon" title="From a reconstructed night: reported, never scored">
              reconstructed
            </span>
          )}
        </span>
        <span className="el-auth-col">
          <span className="el-tag" title={`${AUTHORITY_LABEL[row.authority]}: ${AUTHORITY_MEANS[row.authority]}`}>
            {AUTHORITY_TAG[row.authority]}
          </span>
          {row.silent && (
            <span className="el-tag" title="Never counts toward the call">
              {row.silent}
            </span>
          )}
        </span>
        <span className="el-src-col">
          {link?.url ? (
            <>
              <ExternalLink
                className="el-src"
                href={link.url}
                title={`${edgar ? "Open the filing on EDGAR" : "Open the stored source"}${more > 0 ? ` (${more} more in the row)` : ""}`}
                ariaLabel={`Open ${row.ticker ?? "card"} ${row.form ?? row.kind} on ${edgar ? "EDGAR" : "its source"}`}
              >
                {edgar ? "EDGAR" : "source"} ↗
              </ExternalLink>
              {more > 0 && <span className="el-src-more">+{more}</span>}
            </>
          ) : hasPrice ? (
            <span className="el-src-kind" title="Traces to a price reference only">
              price
            </span>
          ) : isSignal && row.sources.length === 0 ? (
            <span className="el-src-kind" title="States no source">
              no source
            </span>
          ) : (
            <span className="el-src none" />
          )}
        </span>
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
                    <ExternalLink href={s.url}>{isSecLink(s.url) ? "EDGAR" : "source"} ↗</ExternalLink>
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

// --- provenance composition + roster strips ----------------------------------------------------------------------

function ProvenanceStrip({
  tallies,
  filters,
  onToggleAuthority,
  onToggleEdgar,
}: {
  tallies: EvidenceLedgerOut["tallies"];
  filters: Filters;
  onToggleAuthority: (a: Authority) => void;
  onToggleEdgar: () => void;
}) {
  if (tallies.rows === 0) return null;

  const authSegs = AUTHORITY_ORDER.map((a, idx) => ({
    key: a,
    label: AUTHORITY_LABEL[a],
    count: countOf(tallies.by_authority, a),
    cls: `el-comp-seg-${idx}`,
    active: filters.authorities.has(a),
  })).filter((s) => s.count > 0);

  const traceSegs =
    tallies.signals > 0
      ? [
          { key: "edgar", label: "EDGAR", count: tallies.trace_accession, cls: "el-comp-seg-0", clickable: true },
          { key: "price", label: "price", count: tallies.trace_price, cls: "el-comp-seg-2", clickable: false },
          { key: "other", label: "other", count: tallies.trace_other, cls: "el-comp-seg-3", clickable: false },
          { key: "none", label: "none", count: tallies.trace_none, cls: "el-comp-seg-4", clickable: false },
        ].filter((s) => s.count > 0)
      : [];

  const factsTotal = tallies.facts_ratified + tallies.facts_verified + tallies.facts_stored;
  const factSegs =
    factsTotal > 0
      ? [
          { key: "ratified", label: "ratified", count: tallies.facts_ratified, cls: "el-comp-seg-0" },
          { key: "verified", label: "verified", count: tallies.facts_verified, cls: "el-comp-seg-2" },
          { key: "stored", label: "stored", count: tallies.facts_stored, cls: "el-comp-seg-4" },
        ].filter((s) => s.count > 0)
      : [];

  return (
    <div className="el-comp" data-testid="el-composition">
      <div className="el-comp-block">
        <div className="el-comp-head">
          <span className="el-comp-title">Put there by</span>
          <span className="el-comp-total">{plural(tallies.rows, "row", "rows")}</span>
        </div>
        <div className="el-comp-bar">
          {authSegs.map((s) => (
            <button
              key={s.key}
              type="button"
              className={`el-comp-seg ${s.cls}${s.active ? "" : " dim"}`}
              style={{ flex: s.count }}
              title={`${s.label}: ${s.count} — click to toggle`}
              onClick={() => onToggleAuthority(s.key)}
            />
          ))}
        </div>
        <div className="el-comp-legend">
          {authSegs.map((s) => (
            <button
              key={s.key}
              type="button"
              className={`el-comp-item${s.active ? "" : " dim"}`}
              onClick={() => onToggleAuthority(s.key)}
            >
              <span className={`el-comp-sw ${s.cls}`} />
              {s.label} {s.count}
            </button>
          ))}
        </div>
      </div>

      {traceSegs.length > 0 && (
        <div className="el-comp-block">
          <div className="el-comp-head">
            <span className="el-comp-title">Signal trace</span>
            <span className="el-comp-total">{plural(tallies.signals, "signal", "signals")}</span>
          </div>
          <div className="el-comp-bar">
            {traceSegs.map((s) =>
              s.clickable ? (
                <button
                  key={s.key}
                  type="button"
                  className={`el-comp-seg ${s.cls}`}
                  style={{ flex: s.count }}
                  title={`${s.label}: ${s.count} — click to toggle EDGAR-only filter`}
                  onClick={onToggleEdgar}
                />
              ) : (
                <span key={s.key} className={`el-comp-seg static ${s.cls}`} style={{ flex: s.count }} />
              ),
            )}
          </div>
          <div className="el-comp-legend">
            {traceSegs.map((s) =>
              s.clickable ? (
                <button
                  key={s.key}
                  type="button"
                  className="el-comp-item"
                  title="Toggle EDGAR-only filter"
                  onClick={onToggleEdgar}
                >
                  <span className={`el-comp-sw ${s.cls}`} />
                  {s.label} {s.count}
                </button>
              ) : (
                <span key={s.key} className="el-comp-item static">
                  <span className={`el-comp-sw ${s.cls}`} />
                  {s.label} {s.count}
                </span>
              ),
            )}
          </div>
        </div>
      )}

      {factSegs.length > 0 && (
        <div className="el-comp-block">
          <div className="el-comp-head">
            <span className="el-comp-title">Stored facts</span>
            <span className="el-comp-total">{plural(factsTotal, "fact", "facts")}</span>
          </div>
          <div className="el-comp-bar">
            {factSegs.map((s) => (
              <span key={s.key} className={`el-comp-seg static ${s.cls}`} style={{ flex: s.count }} />
            ))}
          </div>
          <div className="el-comp-legend">
            {factSegs.map((s) => (
              <span key={s.key} className="el-comp-item static">
                <span className={`el-comp-sw ${s.cls}`} />
                {s.label} {s.count}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RosterCoverage({
  names,
  activeName,
  onPick,
}: {
  names: EvidenceLedgerOut["names"];
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
    <div className="el-roster" data-testid="el-roster" aria-label="Roster coverage">
      <span className="el-seg-label">Roster</span>
      {visible.map((n) => {
        const isOn = picked !== "" && picked === n.ticker.toLocaleLowerCase();
        return (
          <button
            key={n.key}
            type="button"
            className={`el-roster-chip${n.rows === 0 ? " quiet" : ""}${isOn ? " on" : ""}`}
            aria-pressed={isOn}
            title={`${n.ticker}: ${plural(n.rows, "row", "rows")}${n.silent ? ` (${n.silent})` : ""} — click to filter`}
            onClick={() => onPick(isOn ? "" : n.ticker)}
          >
            <span>{n.ticker}</span>
            <span className="el-roster-ct">{n.rows}</span>
          </button>
        );
      })}
      {quiet.length > 6 && (
        <button type="button" className="el-roster-more" onClick={() => setShowQuiet((v) => !v)}>
          {showQuiet ? "hide quiet" : `+${quiet.length} quiet (0)`}
        </button>
      )}
    </div>
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
    setFilters((f) => ({ ...f, name: "", text: "" }));
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
      <RecordStartNote empty={data != null && allRows.length === 0} />
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
          <ProvenanceStrip
            tallies={data.tallies}
            filters={filters}
            onToggleAuthority={(a) => setFilters((f) => ({ ...f, authorities: toggleIn(f.authorities, a) }))}
            onToggleEdgar={() => setFilters((f) => ({ ...f, edgarOnly: !f.edgarOnly }))}
          />

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
            {data.tallies.rows > 1 && (
              <label className="el-field">
                <span className="el-seg-label">Text</span>
                <input
                  type="search"
                  className="el-search"
                  aria-label="Filter text"
                  placeholder="Label or source"
                  value={filters.text}
                  onChange={(e) => setFilters((f) => ({ ...f, text: e.target.value }))}
                />
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
            {filtering && (
              <button
                type="button"
                className="el-clear"
                onClick={() =>
                  setFilters({
                    ...DEFAULT_FILTERS,
                    authorities: new Set(AUTHORITY_ORDER),
                    kinds: new Set(KIND_ORDER),
                  })
                }
              >
                Clear filters
              </button>
            )}
          </div>

          <RosterCoverage
            names={data.names}
            activeName={filters.name}
            onPick={(ticker) => setFilters((f) => ({ ...f, name: ticker }))}
          />

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
              {(() => {
                const firstHistoricalIdx = days.findIndex((d) => d.date <= data.asof);
                const hasFutureAndPast = firstHistoricalIdx > 0;
                return days.map((day, idx) => (
                  <div key={day.date} className="el-day-wrap">
                    {hasFutureAndPast && idx === firstHistoricalIdx && (
                      <div className="el-asof-divider" data-testid="el-asof-divider">
                        <span>as of {fmtDay(data.asof)} · dated ahead above · historical below</span>
                      </div>
                    )}
                    <section className="el-day" aria-label={fmtDayHeader(day.date)}>
                      <h2 className="el-day-head">
                        {fmtDayHeader(day.date)} <span className="el-n">{plural(day.rows.length, "row", "rows")}</span>
                      </h2>
                      <ul className="el-list">{lines(day.rows, data)}</ul>
                    </section>
                  </div>
                ));
              })()}
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
