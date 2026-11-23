import { memo, useCallback, useMemo, useState } from "react";

import { FlaggedNote, flaggedRule } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./forming-quietly.css";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  FRAMING,
  LIMITS,
  RECONSTRUCTED_TITLE,
  adoptionCell,
  breadthCell,
  breadth200Cell,
  coilingCell,
  coilingNameTitle,
  daysWords,
  filterControls,
  filtersActive,
  hiddenIncubating,
  insiderCell,
  insiderNameTitle,
  keyLine,
  kindWord,
  leadingCell,
  leadingNameTitle,
  nextSort,
  plural,
  riskCell,
  rowMatches,
  rowsOnShow,
  sinceWords,
  sortRows,
  stateTitle,
  stateWord,
  triggerLine,
  turnedKeys,
  workLines,
  type Cell,
  type Filters,
  type Sort,
  type SortKey,
} from "./quietView";
import { useFormingQuietly, type FormingNameOut, type FormingRowOut, type RowState } from "./useFormingQuietly";
import { ExternalLink } from "../../components/ExternalLink";
import { RecordStartNote } from "../RecordStartNote";

// Forming Quietly (docs/dashboards/forming-quietly.md): the cards where a key has turned or signals are gathering but
// nothing is Armed yet. The quietest page in the app (invariant #7): the tape look at its lowest energy, every figure
// in the gray scale, NO accent anywhere, the state chip gray, the sign tone only on the adoption percent. A card's row
// carries the recorded call's own words (the key that turned, what is still missing), the quiet signals on its
// roster as short gray phrases and its EDGAR adoption slope. Display only. Every figure is computed server-side; this
// page picks the rows (the Incubating toggle and the filters) and sorts and words them.

type Column = { label: string; title: string; sort?: SortKey; cls?: string };

const COLUMNS: readonly Column[] = [
  { label: "Card", title: "Open the card in the Cockpit. Sort by the card's name", sort: "card" },
  { label: "State", title: "The recorded state, and the current run's first night. Sort by days in state", sort: "days" },
  { label: "Key that turned", title: "The keys the recorded call shows turned, in its own words, and the names behind them" },
  { label: "Still missing", title: "The recorded call's missing lines, verbatim, and its confirmation-only names" },
  { label: "Risk", title: "Risk signals on the recorded call. Sort by count", sort: "risk", cls: "fq-num" },
  { label: "Insider buying", title: "Names with an open-market insider buy in the last 30 days. Sort by distinct buyers", sort: "insider" },
  { label: "Coiling", title: "Names whose base the volatility-contraction member calls coiling. Sort by count", sort: "coiling" },
  { label: "Leading", title: "Names whose relative strength is at a 13-week high against SPY or IWM. Sort by count", sort: "leading" },
  { label: "Breadth", title: "Names at or above their 50-day average, among the names with enough history. Sort by share above 50d", sort: "breadth" },
  { label: "Adoption", title: "EDGAR documents that match the card's SIGNAL terms: the last three closed months against the three before. Sort by the percent", sort: "adoption", cls: "fq-num" },
];

const SLOW =
  "Reading every counted card's recorded call, the tape of each name on a row and each card's term mentions — the first load after a run or a basket edit can take a moment.";

const MAX_NAMES = 3;

/** A cell that is only a gray blank: on a phone it is left out of the stack (a label over "—" says nothing). */
const blankCls = (c: Cell, extra = "") => [extra, c.text === "—" && !c.sub ? "blank" : ""].filter(Boolean).join(" ") || undefined;

function SortTh({ col, sort, onSort }: { col: Column; sort: Sort; onSort: (key: SortKey) => void }) {
  if (!col.sort) {
    return (
      <th className={col.cls} title={col.title}>
        {col.label}
      </th>
    );
  }
  const key = col.sort;
  const on = sort.key === key;
  return (
    <th className={col.cls} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`fq-sort${on ? " on" : ""}`} title={col.title} onClick={() => onSort(key)}>
        {col.label}
        {on && (
          <span className="fq-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
    </th>
  );
}

function Fig({ cell }: { cell: Cell }) {
  const cls = ["fq-fig", cell.tone ?? "", cell.gray ? "gray" : ""].filter(Boolean).join(" ");
  return (
    <>
      <span className={cls} title={cell.title}>
        {cell.text}
      </span>
      {cell.sub && <span className="fq-sub">{cell.sub}</span>}
    </>
  );
}

/** A ticker that opens the Cockpit on that name, with a gray hover line. */
function TickerChip({ ticker, title, onOpen, word }: { ticker: string; title: string; onOpen: () => void; word?: string }) {
  return (
    <span className="fq-tkwrap">
      <button type="button" className="fq-tk" title={title} onClick={onOpen}>
        {ticker}
      </button>
      {word && <span className="fq-word">{word}</span>}
    </span>
  );
}

/** Quiet monochrome horizontal ratio bar (0–100%) for 50d / 200d breadth. */
function BreadthBar({ num, den }: { num: number | null | undefined; den: number }) {
  if (num == null || den <= 0) return null;
  const pct = Math.max(0, Math.min(100, (num / den) * 100));
  return (
    <span className="fq-bbar" aria-hidden="true">
      <span className="fq-bbar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

/** A name behind the key that turned: the ticker chip, with its triggers and their source links on hover. */
function NameChip({ n, flipUp, onOpen }: { n: FormingNameOut; flipUp: boolean; onOpen: () => void }) {
  return (
    <span className="fq-name">
      <button type="button" className="fq-tk" onClick={onOpen} title={`Open the card on ${n.ticker}`}>
        {n.ticker}
      </button>
      <span className={`fq-tip${flipUp ? " flip-up" : ""}`} role="tooltip">
        {n.triggers.map((t, i) => (
          <span className="fq-tip-row" key={`${t.kind}:${t.event_date}:${i}`}>
            <span>{triggerLine(t)}</span>
            {t.sources.map((s, j) =>
              s.url ? (
                <ExternalLink key={j} href={s.url}>
                  {s.source} {s.ref}
                </ExternalLink>
              ) : (
                <span key={j} className="fq-src">
                  {s.source} {s.ref}
                </span>
              ),
            )}
            {t.sources.length === 0 && <span className="fq-src">no source stated</span>}
          </span>
        ))}
      </span>
    </span>
  );
}

type RowProps = {
  r: FormingRowOut;
  flipUp: boolean;
  stateBreak: boolean;
  onSelectThesis: DashboardProps["onSelectThesis"];
};

const Row = memo(function Row({ r, flipUp, stateBreak, onSelectThesis }: RowProps) {
  const [openAll, setOpenAll] = useState<{ insider?: boolean; coiling?: boolean; leading?: boolean }>({});
  const [riskOpen, setRiskOpen] = useState(false);
  const q = r.quiet;
  const open = (ticker?: string) => () => onSelectThesis(r.thesis_id, ticker ?? null);
  const turned = turnedKeys(r);
  const risk = riskCell(r);
  const insiderFig = insiderCell(r);
  const coilingFig = coilingCell(r);
  const leadingFig = leadingCell(r);
  const breadthFig = breadthCell(r);
  const breadth200Fig = breadth200Cell(r);
  const insider = openAll.insider ? q.insider : q.insider.slice(0, MAX_NAMES);
  const coiling = openAll.coiling ? q.coiling : q.coiling.slice(0, MAX_NAMES);
  const leading = openAll.leading ? q.leading : q.leading.slice(0, MAX_NAMES);
  const more = (key: "insider" | "coiling" | "leading", list: readonly unknown[]) =>
    list.length > MAX_NAMES ? (
      <button
        type="button"
        className="fq-more"
        aria-expanded={Boolean(openAll[key])}
        onClick={() => setOpenAll((prev) => ({ ...prev, [key]: !prev[key] }))}
      >
        {openAll[key] ? "less" : `+${list.length - MAX_NAMES}`}
      </button>
    ) : null;
  const b50 = q.breadth;
  const deltaPts = b50 && b50.counted > 0 && b50.delta_pts != null ? b50.delta_pts : null;
  return (
    <tr data-testid="fq-row" className={`fq-row ${r.state}${stateBreak ? " fq-state-break" : ""}`}>
      <td data-label="Card" className="fq-card-td">
        <button type="button" className="fq-card" title={`Open ${r.thesis_name}`} onClick={open()}>
          {r.thesis_name}
        </button>
        <span
          className="fq-sub"
          title={
            [
              q.unresolved > 0 ? `${plural(q.unresolved, "roster row", "roster rows")} with no resolved company` : null,
              q.flagged.length > 0 ? `left out: ${q.flagged.map((f) => `${f.ticker} (${f.reason})`).join(", ")}` : null,
            ]
              .filter(Boolean)
              .join(" · ") || undefined
          }
        >
          {plural(q.names, "name", "names")}
          {q.flagged.length > 0 ? ` · ${q.flagged.length} left out` : ""}
        </span>
      </td>
      <td data-label="State">
        <span className="fq-state" title={stateTitle(r)}>
          {stateWord(r.state)}
        </span>
        <span className="fq-since">
          {sinceWords(r)}
          {r.run_reconstructed && (
            <span className="fq-mark" title={RECONSTRUCTED_TITLE}>
              {" R"}
            </span>
          )}
          {daysWords(r) && ` · ${daysWords(r)}`}
        </span>
      </td>
      <td data-label="Key that turned" className="fq-text-td">
        {r.state === "warming" && turned.length > 0 ? (
          <>
            {turned.map((k) => (
              <span key={k.label} className="fq-line">
                {keyLine(k)}
              </span>
            ))}
            <span className="fq-chips">
              {r.names.map((n) => (
                <NameChip key={n.security_id} n={n} flipUp={flipUp} onOpen={open(n.ticker)} />
              ))}
            </span>
          </>
        ) : (
          <span className="fq-fig gray" title="The recorded call shows no key turned">
            no key turned
          </span>
        )}
      </td>
      <td data-label="Still missing" className="fq-text-td">
        {r.missing.length === 0 && r.watch.length === 0 ? (
          <span className="fq-fig gray">—</span>
        ) : (
          <>
            {r.missing.map((m) => (
              <span key={m} className="fq-line">
                {m}
              </span>
            ))}
            {r.watch.length > 0 && (
              <span className="fq-chips">
                {r.watch.map((w) => (
                  <TickerChip key={w.security_id} ticker={w.ticker} title={`${w.ticker}: moving, no conviction yet (confirmation only)`} onOpen={open(w.ticker)} word="confirmation only" />
                ))}
              </span>
            )}
          </>
        )}
      </td>
      <td data-label="Risk" className={blankCls(risk, "fq-num")}>
        {r.risks.length > 0 ? (
          <>
            <button
              type="button"
              className="fq-risk-btn"
              aria-expanded={riskOpen}
              onClick={() => setRiskOpen((o) => !o)}
            >
              <Fig cell={risk} />
            </button>
            {riskOpen && (
              <ul className="fq-risk-list" data-testid="fq-risk-list">
                {r.risks.map((x, idx) => (
                  <li key={`${x.ticker}:${idx}`}>
                    <strong>{x.ticker}</strong> {x.label} ({kindWord(x.kind)})
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <Fig cell={risk} />
        )}
      </td>
      <td data-label="Insider buying" className={blankCls(insiderFig)}>
        <Fig cell={insiderFig} />
        {insider.length > 0 && (
          <span className="fq-chips">
            {insider.map((n) => (
              <TickerChip key={n.security_id} ticker={n.ticker} title={insiderNameTitle(n)} onOpen={open(n.ticker)} word={n.cluster ? "cluster" : undefined} />
            ))}
            {more("insider", q.insider)}
          </span>
        )}
      </td>
      <td data-label="Coiling" className={blankCls(coilingFig)}>
        <Fig cell={coilingFig} />
        {coiling.length > 0 && (
          <span className="fq-chips">
            {coiling.map((n) => (
              <TickerChip key={n.security_id} ticker={n.ticker} title={coilingNameTitle(n)} onOpen={open(n.ticker)} />
            ))}
            {more("coiling", q.coiling)}
          </span>
        )}
      </td>
      <td data-label="Leading" className={blankCls(leadingFig)}>
        <Fig cell={leadingFig} />
        {leading.length > 0 && (
          <span className="fq-chips">
            {leading.map((n) => (
              <TickerChip key={n.security_id} ticker={n.ticker} title={leadingNameTitle(n)} onOpen={open(n.ticker)} />
            ))}
            {more("leading", q.leading)}
          </span>
        )}
      </td>
      <td data-label="Breadth" className={blankCls(breadthFig)}>
        <Fig cell={breadthFig} />
        {b50 && b50.counted > 0 && <BreadthBar num={b50.above} den={b50.counted} />}
        {deltaPts !== null && (
          <span className="fq-sub fq-delta">
            {deltaPts > 0 ? "+" : ""}
            {deltaPts.toFixed(0)} pts / 20d
          </span>
        )}
        <Fig cell={breadth200Fig} />
        {q.n_200 > 0 && <BreadthBar num={q.above_200} den={q.n_200} />}
      </td>
      <td data-label="Adoption" className="fq-num">
        <Fig cell={adoptionCell(r.adoption)} />
      </td>
    </tr>
  );
});

function rowMatchesQuery(r: FormingRowOut, qNorm: string): boolean {
  if (!qNorm) return true;
  if (r.thesis_name.toLowerCase().includes(qNorm)) return true;
  const allTickers = [
    ...r.names.map((n) => n.ticker),
    ...r.watch.map((w) => w.ticker),
    ...r.quiet.insider.map((n) => n.ticker),
    ...r.quiet.coiling.map((n) => n.ticker),
    ...r.quiet.leading.map((n) => n.ticker),
  ];
  return allTickers.some((t) => t.toLowerCase().includes(qNorm));
}

export default function FormingQuietly({ asof, onSelectThesis }: DashboardProps) {
  const [everyIncubating, setEveryIncubating] = useState(false);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [hasLeading, setHasLeading] = useState(false);
  const [hasThrust, setHasThrust] = useState(false);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const q = useFormingQuietly(asof);
  const data = q.data;

  const hidden = useMemo(() => hiddenIncubating(data?.rows ?? []), [data]);
  const onShow = useMemo(() => rowsOnShow(data?.rows ?? [], everyIncubating), [data, everyIncubating]);
  const controls = useMemo(() => filterControls(onShow), [onShow]);
  const canFilterLeading = useMemo(() => onShow.some((r) => r.quiet.leading.length > 0), [onShow]);
  const canFilterThrust = useMemo(() => onShow.some((r) => r.quiet.breadth?.thrust === true), [onShow]);
  const qNorm = search.trim().toLowerCase();
  const shown = useMemo(
    () =>
      onShow.filter(
        (r) =>
          rowMatches(r, filters) &&
          (!hasLeading || r.quiet.leading.length > 0) &&
          (!hasThrust || r.quiet.breadth?.thrust === true) &&
          rowMatchesQuery(r, qNorm),
      ),
    [onShow, filters, hasLeading, hasThrust, qNorm],
  );
  const sorted = useMemo(() => sortRows(shown, sort), [shown, sort]);

  const onSort = useCallback((key: SortKey) => setSort((s) => nextSort(s, key)), []);
  const flip = <T,>(list: readonly T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  if (q.isLoading || (!data && q.isFetching)) return <div className="dash-note">{SLOW}</div>;
  if (q.error && !data) return <div className="dash-note">Could not load Forming Quietly.</div>;
  if (!data) return <div className="dash-note">{SLOW}</div>;

  const filtering = filtersActive(filters) || hasLeading || hasThrust || qNorm.length > 0;
  const clearAllFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setHasLeading(false);
    setHasThrust(false);
    setSearch("");
  };
  const stale = q.isFetching && data.asof !== asof;
  const lines = workLines(data, hidden, everyIncubating);

  return (
    <div className="fq">
      <RecordStartNote empty={data.rows.length === 0} />
      <p className="fq-framing" data-testid="fq-framing">
        {FRAMING}
      </p>

      <div className="fq-controls">
        {hidden > 0 && (
          <label className="fq-check" title="Add the Incubating cards with no quiet signal lit">
            <input type="checkbox" checked={everyIncubating} onChange={(e) => setEveryIncubating(e.target.checked)} />
            Show every Incubating card <span className="fq-n">{`+${hidden.toLocaleString("en-US")}`}</span>
          </label>
        )}
        {controls.states.length > 0 && (
          <span className="fq-field" role="group" aria-label="State">
            <span className="fq-seg-label">State</span>
            {controls.states.map((s: RowState) => (
              <button
                key={s}
                type="button"
                className={`fq-chip${filters.states.includes(s) ? " on" : ""}`}
                aria-pressed={filters.states.includes(s)}
                onClick={() => setFilters((f) => ({ ...f, states: flip(f.states, s) }))}
              >
                {s === "warming" ? "Warming" : "Incubating"}
              </button>
            ))}
          </span>
        )}
        {controls.insider && (
          <label className="fq-check" title="Only cards with a name that had an open-market insider buy in the last 30 days">
            <input type="checkbox" checked={filters.insider} onChange={(e) => setFilters((f) => ({ ...f, insider: e.target.checked }))} />
            Has insider buying
          </label>
        )}
        {controls.coiling && (
          <label className="fq-check" title="Only cards with a name whose base is coiling">
            <input type="checkbox" checked={filters.coiling} onChange={(e) => setFilters((f) => ({ ...f, coiling: e.target.checked }))} />
            Has coiling
          </label>
        )}
        {canFilterLeading && (
          <label className="fq-check" title="Only cards with a name whose relative strength is at a 13-week high">
            <input type="checkbox" checked={hasLeading} onChange={(e) => setHasLeading(e.target.checked)} />
            Has leading
          </label>
        )}
        {canFilterThrust && (
          <label className="fq-check" title="Only cards with a 50-day breadth thrust">
            <input type="checkbox" checked={hasThrust} onChange={(e) => setHasThrust(e.target.checked)} />
            Has thrust
          </label>
        )}
        {controls.cards.length > 0 && (
          <label className="fq-field">
            <span className="fq-seg-label">Card</span>
            <select
              className="fq-select"
              aria-label="Card"
              value={filters.card ?? ""}
              onChange={(e) => setFilters((f) => ({ ...f, card: e.target.value || null }))}
            >
              <option value="">all cards</option>
              {controls.cards.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {onShow.length > 1 && (
          <input
            type="search"
            className="fq-search"
            aria-label="Filter card or ticker"
            placeholder="Find card or ticker…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
        {filtering && (
          <button type="button" className="fq-clear" onClick={clearAllFilters}>
            clear filters
          </button>
        )}
        {(sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir) && (
          <button type="button" className="fq-clear" onClick={() => setSort(DEFAULT_SORT)}>
            back to the record order
          </button>
        )}
        {(q.isFetching || stale) && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      <div className="dash-note fq-work" data-testid="fq-count">
        {plural(shown.length, "card", "cards")}
        {shown.length !== onShow.length ? ` of ${onShow.length.toLocaleString("en-US")}` : ""}
        {filtering ? " after the filters" : ""}
        {sort.key === "order" ? " · in the order of the key most recently turned" : ""}
      </div>

      {data.rows.length === 0 ? (
        <div className="dash-note fq-empty">
          No counted card is recorded Warming or Incubating at this as-of ({plural(data.cards_counted, "card", "cards")} counted,{" "}
          {data.cards_no_call} with no call yet).
        </div>
      ) : onShow.length === 0 ? (
        <div className="dash-note fq-empty">
          No Warming card and no Incubating card with a quiet signal at this as-of. {plural(hidden, "Incubating card", "Incubating cards")} can be shown above.
        </div>
      ) : shown.length === 0 ? (
        <div className="dash-note fq-empty">No card passes the filters.</div>
      ) : (
        <div className="fq-scroll">
          <table className="fq-table">
            <thead>
              <tr className="fq-super" aria-hidden="true">
                <td colSpan={5}>Recorded call</td>
                <td colSpan={4} className="fq-grp-start">
                  Quiet signals (display only)
                </td>
                <td colSpan={1} className="fq-grp-start fq-num">
                  EDGAR
                </td>
              </tr>
              <tr>
                {COLUMNS.map((c) => (
                  <SortTh key={c.label} col={c} sort={sort} onSort={onSort} />
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, idx) => (
                <Row
                  key={r.key}
                  r={r}
                  flipUp={sorted.length > 3 && idx >= sorted.length - 3}
                  stateBreak={sort.key === "order" && idx > 0 && r.state === "incubating" && sorted[idx - 1].state === "warming"}
                  onSelectThesis={onSelectThesis}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {lines.map((line, i) => (
        <div key={i} className="dash-note fq-work">
          {line}
        </div>
      ))}
      <FlaggedNote flagged={data.flagged} title={flaggedRule("they are left out of every tape and insider reading on this page. They still count in their card's names and are listed here.")} />
      <ul className="dash-note fq-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
