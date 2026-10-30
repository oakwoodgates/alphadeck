import { useMemo, useState } from "react";

import { fmtDate } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./insider-tracker.css";
import {
  DAYS_OPTIONS,
  DEFAULT_DAYS,
  DEFAULT_FILTERS,
  GROUP_BYS,
  LIMITS,
  NOT_A_TRIGGER_TITLE,
  NO_AGGREGATE,
  ROLE_LABEL,
  SIZE_LABEL,
  TIER_LABEL,
  cacheWords,
  capTitle,
  capWords,
  clusterTitle,
  clusterWords,
  dimValues,
  emptyWords,
  facetCounts,
  filterRows,
  filtersActive,
  groupFacts,
  groupRows,
  pct,
  plural,
  priceWords,
  sharesWords,
  toggleCount,
  toggleDiscriminates,
  tone,
  tradeNote,
  triggerTitle,
  usdFromTitle,
  usdWords,
  workLines,
  type Dim,
  type Filters,
  type GroupBy,
  type RoleClass,
  type Row,
  type SizeBand,
  type TierKey,
  type Toggle,
} from "./trackerView";
import {
  useInsiderTracker,
  type InsiderTrackerDays,
  type InsiderTrackerNameOut,
  type InsiderTrackerOut,
} from "./useInsiderTracker";

// Insider Buying (docs/dashboards/insider-tracker.md): every open-market insider purchase (Form 4 code P) across the
// counted cards' names in a window, one row per Form 4, dated by the day it was FILED, newest first: who bought, how
// much, whether others bought too, whether a recorded insider trigger carries it, and what the tape did since.
// Display only, and no aggregate: the Track Record page slices outcomes by market-cap tier. Every row and count is
// computed server-side; this page groups (filing week, name, card), filters and words them. The trading set's tape
// look (docs/dashboards/hot-board.md §Look): dense rows, mono figures, ONE accent (the cluster word, the Leaders
// page's cluster tone), the sign tone only on the signed since-values, nothing bold but the ticker.

const SLOW = "Reading every counted name's filings — the first load after a run or a basket edit can take a while.";

const COLUMNS: readonly { key: string; label: string }[] = [
  { key: "filed", label: "filed" },
  { key: "trade", label: "trade date" },
  { key: "tk", label: "ticker" },
  { key: "card", label: "card" },
  { key: "filer", label: "filer" },
  { key: "role", label: "role" },
  { key: "sh", label: "shares" },
  { key: "px", label: "price" },
  { key: "usd", label: "$ size" },
  { key: "cluster", label: "cluster" },
  { key: "tags", label: "tags" },
  { key: "trig", label: "trigger" },
  { key: "since", label: "since" },
  { key: "iwm", label: "IWM" },
  { key: "cap", label: "cap ≈" },
];

type Open = DashboardProps["onSelectThesis"];

function SinceCells({ row }: { row: Row }) {
  const s = row.since;
  // a flagged or unpriced name has no return to compare: one cell spans the since and IWM columns and says why
  if (s.status === "flagged") {
    return (
      <div role="cell" className="ins-c ins-c-since ins-span" title={s.reason ?? undefined}>
        <span className="ins-quiet">flagged: {s.reason}</span>
      </div>
    );
  }
  if (s.status === "unpriced") {
    return (
      <div role="cell" className="ins-c ins-c-since ins-span" title="no close on or after the filing date">
        <span className="ins-quiet">unpriced</span>
      </div>
    );
  }
  return (
    <>
      <div
        role="cell"
        className="ins-c ins-c-since"
        title={`as-traded close ${fmtDate(s.entry_date)} → ${fmtDate(s.end_date)}`}
      >
        <span className={`ins-ret ${tone(s.ret)}`}>{pct(s.ret)}</span>
      </div>
      <div
        role="cell"
        className="ins-c ins-c-iwm"
        data-label="IWM"
        title={`IWM over the same two dates, ${fmtDate(s.entry_date)} → ${fmtDate(s.end_date)}`}
      >
        {s.bench_ret != null ? <span className="ins-bench">{pct(s.bench_ret)}</span> : <span className="ins-quiet">—</span>}
      </div>
    </>
  );
}

function PurchaseRow({
  row,
  open,
  onToggle,
  onSelectThesis,
  cardId,
}: {
  row: Row;
  open: boolean;
  onToggle: () => void;
  onSelectThesis: Open;
  /** the card whose group the row sits in (a card grouping): the ticker opens that card */
  cardId: string | null;
}) {
  const note = tradeNote(row);
  const first = row.cards.find((c) => c.thesis_id === cardId) ?? row.cards[0];
  const filerMore = row.co_filers > 0 ? ` and ${row.co_filers} more` : "";
  return (
    <>
      <div role="row" className="ins-row" data-testid="ins-row" data-cluster={row.cluster ? "yes" : "no"}>
        <div role="cell" className="ins-c ins-c-filed">
          <button
            type="button"
            className="ins-chev"
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} the filing's words for ${row.ticker} filed ${row.filed}`}
            onClick={onToggle}
          >
            {open ? "▾" : "▸"}
          </button>
          {row.url ? (
            <a
              className="ins-link"
              href={row.url}
              target="_blank"
              rel="noopener noreferrer"
              title={`Open the filing on EDGAR (${row.accession ?? "no accession"})`}
            >
              {fmtDate(row.filed)}
            </a>
          ) : (
            <span title={row.filed}>{fmtDate(row.filed)}</span>
          )}
          {row.form !== "4" && <span className="ins-form">{row.form}</span>}
        </div>
        <div role="cell" className="ins-c ins-c-trade" data-label="trade" title={row.trade_date ?? "no trade date stated"}>
          {row.trade_date ? fmtDate(row.trade_date) : <span className="ins-quiet">—</span>}
          {note && <span className="ins-note">{note}</span>}
        </div>
        <div role="cell" className="ins-c ins-c-tk">
          <button
            type="button"
            className="ins-tk"
            title={first ? `Open ${first.thesis_name} on ${first.ticker}` : undefined}
            disabled={!first}
            onClick={() => first && onSelectThesis(first.thesis_id, first.ticker)}
          >
            {row.ticker}
          </button>
        </div>
        <div role="cell" className="ins-c ins-c-card">
          {row.cards.map((c) => (
            <button
              key={c.thesis_id}
              type="button"
              className="ins-chip"
              title={`Open ${c.thesis_name} on ${c.ticker}${c.silent ? ` (${c.silent}: never counts toward the call)` : ""}`}
              onClick={() => onSelectThesis(c.thesis_id, c.ticker)}
            >
              {c.thesis_name}
              {c.silent && <span className="ins-silent"> · {c.silent}</span>}
            </button>
          ))}
        </div>
        <div role="cell" className="ins-c ins-c-filer" title={row.headline}>
          {row.filer ?? <span className="ins-quiet">filer not named</span>}
          {filerMore && <span className="ins-quiet">{filerMore}</span>}
        </div>
        <div role="cell" className="ins-c ins-c-role" data-label="role" title={row.role_classes.map((c) => ROLE_LABEL[c]).join(" · ")}>
          {row.role ?? <span className="ins-quiet">not stated</span>}
        </div>
        <div role="cell" className="ins-c ins-c-sh" data-label="shares">
          {sharesWords(row.shares)}
        </div>
        <div role="cell" className="ins-c ins-c-px" data-label="price">
          {priceWords(row.price)}
        </div>
        <div role="cell" className="ins-c ins-c-usd" title="the purchases' dollars, not net of any sale in the same filing">
          {row.usd !== null ? usdWords(row.usd) : <span className="ins-quiet">—</span>}
        </div>
        <div role="cell" className="ins-c ins-c-cluster">
          {row.cluster && (
            <span className="ins-cluster" title={clusterTitle(row.cluster)}>
              {clusterWords(row.cluster)}
            </span>
          )}
        </div>
        <div role="cell" className="ins-c ins-c-tags">
          {row.tags.map((t) => (
            <span key={t} className="ins-tag">
              {t}
            </span>
          ))}
        </div>
        <div role="cell" className="ins-c ins-c-trig">
          {row.triggers.length === 0 ? (
            <span className="ins-quiet" title={NOT_A_TRIGGER_TITLE}>
              not a trigger
            </span>
          ) : (
            row.triggers.map((h) => (
              <span key={h.thesis_id} className="ins-trig" title={triggerTitle(h)}>
                trigger on {h.thesis_name}
                {h.live === false && <span className="ins-quiet"> · off the card</span>}
              </span>
            ))
          )}
        </div>
        <SinceCells row={row} />
        <div role="cell" className="ins-c ins-c-cap" data-label="cap" title={capTitle(row.cap)}>
          {row.cap.value === null ? <span className="ins-quiet">—</span> : capWords(row.cap)}
        </div>
      </div>
      {open && (
        <div role="row" className="ins-more" data-testid="ins-more">
          <div role="cell" className="ins-more-cell">
            <div className="ins-more-head">{row.headline}</div>
            {row.detail.map((line, i) => (
              <div key={i} className="ins-more-line">
                {line}
              </div>
            ))}
            {row.accession && <div className="ins-more-line ins-quiet">accession {row.accession}</div>}
          </div>
        </div>
      )}
    </>
  );
}

/** A chip row for one filter dimension: each chip restates how many rows it shows with every other filter
 *  applied. Renders nothing when the payload holds fewer than two values (it could not discriminate). */
function Chips({
  dim,
  label,
  rows,
  filters,
  selected,
  text,
  onToggle,
}: {
  dim: Dim;
  label: string;
  rows: readonly Row[];
  filters: Filters;
  selected: ReadonlySet<string>;
  text: (value: string) => string;
  onToggle: (value: string) => void;
}) {
  const values = dimValues(rows, dim);
  if (values.length < 2) return null;
  const counts = facetCounts(rows, filters, dim);
  return (
    <div className="ins-seg" role="group" aria-label={label}>
      <span className="ins-seg-label">{label}</span>
      {values.map((v) => (
        <button
          key={v}
          type="button"
          className={`ins-chipbtn${selected.has(v) ? " on" : ""}`}
          aria-pressed={selected.has(v)}
          onClick={() => onToggle(v)}
        >
          {text(v)} <span className="ins-n">{counts.get(v) ?? 0}</span>
        </button>
      ))}
    </div>
  );
}

function FlaggedNote({ flagged }: { flagged: InsiderTrackerNameOut[] }) {
  const [open, setOpen] = useState(false);
  if (flagged.length === 0) return null;
  return (
    <div className="dash-note ins-work">
      <button type="button" className="ins-fold" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="ins-chev" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        {plural(flagged.length, "name", "names")} left unpriced for a flagged price history
      </button>
      {open && (
        <ul className="ins-names">
          {flagged.map((n) => (
            <li key={n.security_id}>
              {n.ticker}: flagged, {n.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function toggled<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export default function InsiderTracker({ asof, onSelectThesis }: DashboardProps) {
  const [days, setDays] = useState<InsiderTrackerDays>(DEFAULT_DAYS);
  const [silent, setSilent] = useState(false);
  const [groupBy, setGroupBy] = useState<GroupBy>("week");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const q = useInsiderTracker(asof, days, silent);
  const data: InsiderTrackerOut | undefined = q.data;
  const rows = useMemo(() => data?.rows ?? [], [data]);

  const filtered = useMemo(() => filterRows(rows, filters), [rows, filters]);
  const groups = useMemo(() => groupRows(filtered, groupBy), [filtered, groupBy]);
  const filtering = filtersActive(filters);
  const stale = data != null && (data.asof !== asof || data.window_days !== days || data.include_silent !== silent);

  const cards = useMemo(
    () => dimValues(rows, "card").map((id) => ({ id, name: rows.flatMap((r) => r.cards).find((c) => c.thesis_id === id)?.thesis_name ?? id })),
    [rows],
  );
  const cardCounts = useMemo(() => facetCounts(rows, filters, "card"), [rows, filters]);

  const toggles: { key: Toggle; label: string; title: string }[] = [
    { key: "clustersOnly", label: "Clusters only", title: "Only purchases with another buyer's filing on the same name within 30 days" },
    { key: "triggersOnly", label: "Triggers only", title: "Only purchases a recorded insider trigger carries" },
  ];

  return (
    <div className="ins">
      <div className="ins-controls">
        <div className="ins-seg" role="group" aria-label="Window">
          <span className="ins-seg-label">Window</span>
          {DAYS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`ins-chipbtn${days === o.value ? " on" : ""}`}
              aria-pressed={days === o.value}
              onClick={() => setDays(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="ins-seg" role="group" aria-label="Group by">
          <span className="ins-seg-label">Group</span>
          {GROUP_BYS.map((g) => (
            <button
              key={g.key}
              type="button"
              className={`ins-chipbtn${groupBy === g.key ? " on" : ""}`}
              aria-pressed={groupBy === g.key}
              onClick={() => setGroupBy(g.key)}
            >
              {g.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={`ins-chipbtn${silent ? " on" : ""}`}
          aria-pressed={silent}
          title="Also read the names the cards hold in Context and Bench: they never count toward a call, so they are tagged"
          onClick={() => setSilent((s) => !s)}
        >
          Include Context and Bench
        </button>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load Insider Buying.</div>
      ) : (
        <>
          <div className="ins-filters">
            <Chips
              dim="size"
              label="Size"
              rows={rows}
              filters={filters}
              selected={filters.sizes}
              text={(v) => SIZE_LABEL[v as SizeBand]}
              onToggle={(v) => setFilters((f) => ({ ...f, sizes: toggled(f.sizes, v as SizeBand) }))}
            />
            <Chips
              dim="role"
              label="Role"
              rows={rows}
              filters={filters}
              selected={filters.roles}
              text={(v) => ROLE_LABEL[v as RoleClass]}
              onToggle={(v) => setFilters((f) => ({ ...f, roles: toggled(f.roles, v as RoleClass) }))}
            />
            <Chips
              dim="tier"
              label="Cap"
              rows={rows}
              filters={filters}
              selected={filters.tiers}
              text={(v) => TIER_LABEL[v as TierKey]}
              onToggle={(v) => setFilters((f) => ({ ...f, tiers: toggled(f.tiers, v as TierKey) }))}
            />
            {cards.length > 1 && (
              <label className="ins-field">
                <span className="ins-seg-label">Card</span>
                <select
                  className="ins-select"
                  aria-label="Card"
                  value={filters.card}
                  onChange={(e) => setFilters((f) => ({ ...f, card: e.target.value }))}
                >
                  <option value="">All cards</option>
                  {cards.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · {cardCounts.get(c.id) ?? 0}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {toggles.map(
              (t) =>
                toggleDiscriminates(rows, t.key) && (
                  <button
                    key={t.key}
                    type="button"
                    className={`ins-chipbtn${filters[t.key] ? " on" : ""}`}
                    aria-pressed={filters[t.key]}
                    title={t.title}
                    onClick={() => setFilters((f) => ({ ...f, [t.key]: !f[t.key] }))}
                  >
                    {t.label} <span className="ins-n">{toggleCount(rows, filters, t.key)}</span>
                  </button>
                ),
            )}
            {filtering && (
              <>
                <span className="ins-n ins-showing">
                  {filtered.length.toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")}
                </span>
                <button type="button" className="ins-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
                  clear filters
                </button>
              </>
            )}
          </div>

          {data.counts.cards_counted === 0 ? (
            <div className="dash-note ins-empty">No card existed by this as-of.</div>
          ) : rows.length === 0 ? (
            <div className="dash-note ins-empty">{emptyWords(data)}</div>
          ) : filtered.length === 0 ? (
            <div className="dash-note ins-empty">No purchase passes the filters.</div>
          ) : (
            <div className="ins-scroll">
              <div className="ins-table" role="table" aria-label="Insider purchases">
                <div className="ins-head" role="row">
                  {COLUMNS.map((c) => (
                    <div key={c.key} role="columnheader" className={`ins-h ins-c-${c.key}`}>
                      {c.label}
                    </div>
                  ))}
                </div>
                {groups.map((g) => (
                  <div key={g.key} role="rowgroup" aria-label={g.label}>
                    <div className="ins-gh" role="row">
                      <div role="columnheader" aria-colspan={COLUMNS.length} className="ins-gh-cell">
                        {g.thesisId ? (
                          <button
                            type="button"
                            className="ins-cardname"
                            title={`Open ${g.label} in the Cockpit`}
                            onClick={() => onSelectThesis(g.thesisId as string)}
                          >
                            {g.label}
                          </button>
                        ) : (
                          <span className="ins-gname" title={groupBy === "week" ? g.key : undefined}>
                            {g.label}
                          </span>
                        )}
                        {groupFacts(g, groupBy).map((f, i) => (
                          <span key={i} className="ins-n" title={groupBy === "name" && i === 1 ? usdFromTitle(g) : undefined}>
                            {f}
                          </span>
                        ))}
                        {groupBy === "name" && g.clusterBuyers !== null && (
                          <span className="ins-cluster">cluster · {plural(g.clusterBuyers, "buyer", "buyers")}</span>
                        )}
                      </div>
                    </div>
                    {g.rows.map((r) => (
                      <PurchaseRow
                        key={`${g.key}:${r.key}`}
                        row={r}
                        open={open.has(r.key)}
                        onToggle={() => setOpen((o) => toggled(o, r.key))}
                        onSelectThesis={onSelectThesis}
                        cardId={g.thesisId}
                      />
                    ))}
                  </div>
                ))}
              </div>
            </div>
          )}

          {workLines(data, filtered.length, filtering).map((line, i) => (
            <div key={i} className="dash-note ins-work">
              {line}
            </div>
          ))}
          <FlaggedNote flagged={data.flagged} />
          <div className="dash-note ins-work">{cacheWords(data)}</div>
          <div className="dash-note ins-work ins-fixed">{NO_AGGREGATE}</div>
          <ul className="dash-note ins-limits">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
