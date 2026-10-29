import { useMemo, useState, type ReactNode } from "react";

import { fmtDate } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./leaders.css";
import {
  GROUP_BY_OPTIONS,
  GROUP_SORT,
  LISTS,
  LIST_SORT,
  SEVERAL_SORT,
  VALUE_COL,
  fmtContraction,
  fmtDay,
  fmtPct,
  fmtRatio,
  fmtYesNo,
  groupFirstDir,
  groupLabel,
  listMeta,
  nameFirstDir,
  nextSort,
  pct,
  plural,
  sortGroups,
  sortNames,
  type GroupCol,
  type NameCol,
  type Sort,
  type SortDir,
} from "./leadersView";
import {
  useLeaders,
  type GroupBy,
  type LeadersCardOut,
  type LeadersNameOut,
  type LeadersOut,
  type ListKey,
} from "./useLeaders";

// Leaders (docs/dashboards/leaders.md): names across every live card near their 52-week high, leading the
// market, coiling, with insider buying or loud volume, at the as-of; the names on several of those lists; and
// the groups where insiders cluster. Display only. Every rule (the thresholds, who is on which list, the
// no-reading counts, the insider groups) is computed server-side; this page picks the view and orders each
// table. Loudness marks the exception only (#7 / WB#3): the one accent is the leader-blue insider cluster.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

function cacheWords(d: LeadersOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: LeadersOut): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  return [
    cards,
    plural(d.companies, "company", "companies"),
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not counted`,
    `near high: within ${Math.abs(d.near_high_pct)}% of the 52w high`,
    `volume: RVOL 20D ≥ ${d.rvol_loud}`,
    `insider cluster: ${d.cluster_min_buyers}+ buyers in 30d`,
    `insider group: ${d.group_min_names}+ names with buys`,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ].join(" · ");
}

/** The second quiet line: how many companies each member gave no reading (not on that list for want of
 *  data, never silently gone). */
function noReadingLine(d: LeadersOut): string {
  return `no reading: ${d.lists.map((l) => `${l.member} ${l.no_reading.toLocaleString("en-US")}`).join(" · ")}`;
}

function SortTh<C extends string>({
  col,
  label,
  title,
  sort,
  onSort,
  num = true,
}: {
  col: C;
  label: string;
  title: string;
  sort: Sort<C>;
  onSort: (c: C) => void;
  num?: boolean;
}) {
  const dir: SortDir | null = sort.col === col ? sort.dir : null;
  return (
    <th
      className={num ? "ld-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button type="button" className={`ld-sort${dir ? " active" : ""}`} title={title} onClick={() => onSort(col)}>
        {label}
        {dir && (
          <span className="ld-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

/** A name: its ticker opens its first card's Cockpit on it; its company name; a chip per card (with its links
 *  there) opens that card's Cockpit on the name. */
function NameCell({
  ticker,
  name,
  cards,
  onSelect,
}: {
  ticker: string;
  name: string | null;
  cards: readonly LeadersCardOut[];
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const first = cards[0];
  return (
    <td className="ld-id">
      <button
        type="button"
        className="ld-tk"
        title={first ? `Open ${first.thesis_name} on ${first.ticker}` : undefined}
        onClick={() => first && onSelect(first.thesis_id, first.ticker)}
      >
        {ticker}
      </button>
      {name && <span className="ld-co">{name}</span>}
      <span className="ld-chips">
        {cards.map((c) => {
          const links = c.links.map((l) => l ?? "Unsegmented").join(", ");
          return (
            <button
              key={c.thesis_id}
              type="button"
              className="ld-chip"
              title={`Open ${c.thesis_name} on ${c.ticker}`}
              onClick={() => onSelect(c.thesis_id, c.ticker)}
            >
              {c.thesis_name}
              {links && <span className="ld-chip-links"> · {links}</span>}
            </button>
          );
        })}
      </span>
    </td>
  );
}

function Insider({ n }: { n: { buys_30d: number | null; buyers_30d: number | null; cluster: boolean } }) {
  if (n.buys_30d == null || n.buys_30d === 0) return <span className="ld-v">—</span>;
  const buyers = n.buyers_30d ?? 0;
  return (
    <span
      className={`ld-v ld-ins${n.cluster ? " cluster" : ""}`}
      title={`${n.buys_30d} open-market ${n.buys_30d === 1 ? "buy" : "buys"} by ${buyers} ${
        buyers === 1 ? "insider" : "insiders"
      }, last 30 days`}
    >
      {n.buys_30d}/{buyers}
    </span>
  );
}

/** A list's own value cell. */
function ValueCell({ list, n, data }: { list: ListKey; n: LeadersNameOut; data: LeadersOut }) {
  switch (list) {
    case "near_high": {
      const short = n.range_bars != null && n.range_bars < data.full_year_bars;
      return (
        <td className="ld-num">
          <span className="ld-v">{fmtPct(n.pct_off_high)}</span>
          {n.high_date && <span className="ld-sub">high {fmtDate(n.high_date)}</span>}
          {short && (
            <span className="ld-sub" title="the range covers fewer bars than a full year">
              {n.range_bars} bars
            </span>
          )}
        </td>
      );
    }
    case "rs_leader":
      return <td className="ld-num ld-v">{n.rs_vs.length ? n.rs_vs.join(" · ") : "—"}</td>;
    case "coiling":
      return <td className="ld-num ld-v">{fmtContraction(n.contraction)}</td>;
    case "insider":
      return (
        <td className="ld-num">
          <Insider n={n} />
        </td>
      );
    case "volume":
      return <td className="ld-num ld-v">{fmtRatio(n.rvol20)}</td>;
  }
}

/** The columns every list shows after its own value. */
function TapeHeads({ sort, onSort, rvol = true }: { sort: Sort<NameCol>; onSort: (c: NameCol) => void; rvol?: boolean }) {
  return (
    <>
      <SortTh col="ret_7d" label="7D return" title="Sort by the 7-day return" sort={sort} onSort={onSort} />
      <SortTh col="ret_30d" label="30D return" title="Sort by the 30-day return" sort={sort} onSort={onSort} />
      <SortTh
        col="above_50d"
        label="Above 50d"
        title="Sort by whether the close is above the 50-day SMA"
        sort={sort}
        onSort={onSort}
      />
      {rvol && <SortTh col="rvol20" label="RVOL 20D" title="Sort by the 20-day RVOL" sort={sort} onSort={onSort} />}
    </>
  );
}

function TapeCells({ n, rvol = true }: { n: LeadersNameOut; rvol?: boolean }) {
  return (
    <>
      <td className="ld-num ld-v">{fmtPct(n.ret_7d)}</td>
      <td className="ld-num ld-v">{fmtPct(n.ret_30d)}</td>
      <td className="ld-num ld-v">{fmtYesNo(n.above_50d)}</td>
      {rvol && <td className="ld-num ld-v">{fmtRatio(n.rvol20)}</td>}
    </>
  );
}

function Section({ title, rule, children }: { title: string; rule: string; children: ReactNode }) {
  return (
    <section className="ld-sec" aria-label={title}>
      <h2 className="ld-h">{title}</h2>
      <div className="dash-note ld-rule">{rule}</div>
      {children}
    </section>
  );
}

// --- 1. on several lists ------------------------------------------------------------------------------

function Several({ data, onSelect }: { data: LeadersOut; onSelect: DashboardProps["onSelectThesis"] }) {
  const [sort, setSort] = useState<Sort<NameCol>>(SEVERAL_SORT);
  const names = useMemo(() => {
    const on = new Set(data.several);
    return sortNames(
      data.names.filter((n) => on.has(n.security_id)),
      sort,
    );
  }, [data, sort]);
  const onSort = (c: NameCol) => setSort((s) => nextSort(s, c, nameFirstDir));
  return (
    <Section
      title="On several lists"
      rule={`Names on ${data.multi_min_lists} or more of the lists below, with the lists they are on.`}
    >
      {names.length === 0 ? (
        <div className="dash-note ld-empty">No name is on {data.multi_min_lists} or more lists at this as-of.</div>
      ) : (
        <div className="ld-scroll">
          <table className="ld-table" aria-label="On several lists">
            <thead>
              <tr>
                <SortTh col="ticker" label="Name" title="Sort by ticker" sort={sort} onSort={onSort} num={false} />
                <SortTh col="lists" label="Lists" title="Sort by the number of lists, then the 30-day return" sort={sort} onSort={onSort} num={false} />
                <TapeHeads sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {names.map((n) => (
                <tr key={n.security_id}>
                  <NameCell ticker={n.ticker} name={n.name} cards={n.cards} onSelect={onSelect} />
                  <td>
                    <span className="ld-lists">
                      {n.lists.map((k) => (
                        <span key={k} className="ld-list-chip" title={listMeta(k).label}>
                          {listMeta(k).chip}
                        </span>
                      ))}
                    </span>
                  </td>
                  <TapeCells n={n} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// --- 2. one tab per list ------------------------------------------------------------------------------

function listRule(key: ListKey, d: LeadersOut): string {
  switch (key) {
    case "near_high":
      return `A close within ${Math.abs(d.near_high_pct)}% of its 52-week closing high (range_52w). A range over fewer than ${d.full_year_bars} bars says so.`;
    case "rs_leader":
      return "Relative strength at a fresh 13-week high against SPY or IWM (relative_strength's leading).";
    case "coiling":
      return "Three tightening pullback ranges above a rising 50-day SMA (vcp's coiling); contraction = the newest range as a share of the oldest.";
    case "insider":
      return `At least ${d.insider_min_buys} open-market insider buy in the last 30 days (insider_flow_90d, the Cockpit's open-market screen). Blue = ${d.cluster_min_buyers}+ distinct buyers.`;
    case "volume":
      return `The as-of bar's volume at least ${d.rvol_loud}× the mean of the 20 bars before it (rvol's RVOL 20D).`;
  }
}

function Lists({ data, onSelect }: { data: LeadersOut; onSelect: DashboardProps["onSelectThesis"] }) {
  const [tab, setTab] = useState<ListKey>("near_high");
  const [sorts, setSorts] = useState<Record<ListKey, Sort<NameCol>>>(LIST_SORT);
  const sort = sorts[tab];
  const list = data.lists.find((l) => l.key === tab);
  const names = useMemo(() => {
    const byId = new Map(data.names.map((n) => [n.security_id, n]));
    const rows = (list?.security_ids ?? []).flatMap((id) => byId.get(id) ?? []);
    return sortNames(rows, sort);
  }, [data, list, sort]);
  const onSort = (c: NameCol) => setSorts((s) => ({ ...s, [tab]: nextSort(s[tab], c, nameFirstDir) }));
  const meta = listMeta(tab);
  const isVolume = tab === "volume";
  return (
    <Section title="Lists" rule="One tab per list; a name can be on several.">
      <div className="ld-tabs" role="tablist" aria-label="Lists">
        {LISTS.map((l) => {
          const n = data.lists.find((x) => x.key === l.key)?.security_ids.length ?? 0;
          return (
            <button
              key={l.key}
              type="button"
              role="tab"
              id={`ld-tab-${l.key}`}
              aria-selected={tab === l.key}
              aria-controls="ld-tabpanel"
              className={`ld-tab${tab === l.key ? " on" : ""}`}
              onClick={() => setTab(l.key)}
            >
              {l.label}
              <span className="ld-tab-n">{n}</span>
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id="ld-tabpanel" aria-labelledby={`ld-tab-${tab}`}>
        <div className="dash-note ld-rule">
          {listRule(tab, data)} {list ? `${plural(list.no_reading, "company", "companies")} had no reading.` : ""}
        </div>
        {names.length === 0 ? (
          <div className="dash-note ld-empty">No names on this list at this as-of.</div>
        ) : (
          <div className="ld-scroll">
            <table className="ld-table" aria-label={meta.label}>
              <thead>
                <tr>
                  <SortTh col="ticker" label="Name" title="Sort by ticker" sort={sort} onSort={onSort} num={false} />
                  <SortTh col={VALUE_COL[tab]} label={meta.value} title={`Sort by ${meta.title}`} sort={sort} onSort={onSort} />
                  <TapeHeads sort={sort} onSort={onSort} rvol={!isVolume} />
                </tr>
              </thead>
              <tbody>
                {names.map((n) => (
                  <tr key={n.security_id}>
                    <NameCell ticker={n.ticker} name={n.name} cards={n.cards} onSelect={onSelect} />
                    <ValueCell list={tab} n={n} data={data} />
                    <TapeCells n={n} rvol={!isVolume} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Section>
  );
}

// --- 3. insider clusters by group ---------------------------------------------------------------------

function InsiderGroups({
  data,
  groupBy,
  onGroupBy,
  onSelect,
}: {
  data: LeadersOut;
  groupBy: GroupBy;
  onGroupBy: (g: GroupBy) => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<GroupCol>>(GROUP_SORT);
  const view = data.group_by;
  const groups = useMemo(() => sortGroups(data.insider_groups, sort, view), [data, sort, view]);
  const onSort = (c: GroupCol) => setSort((s) => nextSort(s, c, groupFirstDir));
  return (
    <Section
      title="Insider clusters by group"
      rule={`Groups where at least ${data.group_min_names} names had open-market insider buys in the last 30 days, with the share of the group's names that did. Each name shows buys/buyers; blue = ${data.cluster_min_buyers}+ buyers.`}
    >
      <div className="ld-controls">
        <div className="ld-seg-group" role="group" aria-label="Group by">
          <span className="ld-seg-label">Group by</span>
          {GROUP_BY_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`ld-seg-btn${groupBy === o.value ? " on" : ""}`}
              aria-pressed={groupBy === o.value}
              onClick={() => onGroupBy(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {groups.length === 0 ? (
        <div className="dash-note ld-empty">
          No group has {data.group_min_names} or more names with insider buys at this as-of.
        </div>
      ) : (
        <div className="ld-scroll">
          <table className="ld-table" aria-label="Insider clusters by group">
            <thead>
              <tr>
                <SortTh
                  col="label"
                  label={GROUP_BY_OPTIONS.find((o) => o.value === view)?.label ?? "Group"}
                  title="Sort by name"
                  sort={sort}
                  onSort={onSort}
                  num={false}
                />
                <SortTh col="with_buys" label="Names with buys" title="Sort by names with buys, then the share" sort={sort} onSort={onSort} />
                <SortTh col="share" label="Share" title="Sort by the share of the group's names with buys" sort={sort} onSort={onSort} />
                <SortTh col="buys" label="Buys 30d" title="Sort by the group's open-market buys" sort={sort} onSort={onSort} />
                <th>Names · buys/buyers</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.key}>
                  <td className="ld-label">{groupLabel(g, view)}</td>
                  <td className="ld-num">
                    <span className="ld-v">
                      {g.with_buys} of {g.names}
                    </span>
                    {g.no_insider_data > 0 && (
                      <span className="ld-sub">{g.no_insider_data} without insider data</span>
                    )}
                  </td>
                  <td className="ld-num ld-v">{pct(g.share)}</td>
                  <td className="ld-num ld-v">{g.buys}</td>
                  <td>
                    <span className="ld-members">
                      {g.members.map((m) => {
                        const first = m.cards[0];
                        return (
                          <button
                            key={m.security_id}
                            type="button"
                            className="ld-member"
                            title={first ? `Open ${first.thesis_name} on ${first.ticker}${m.name ? ` (${m.name})` : ""}` : undefined}
                            onClick={() => first && onSelect(first.thesis_id, first.ticker)}
                          >
                            <span className="ld-member-tk">{m.ticker}</span> <Insider n={m} />
                          </button>
                        );
                      })}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

export default function Leaders({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const q = useLeaders(asof, groupBy, includeSilent);
  const data = q.data;

  if (q.isLoading) {
    return (
      <div className="dash-note">Computing across all cards — the first load after a run can take a minute.</div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Leaders board.</div>;

  return (
    <div className="ld">
      <div className="ld-controls">
        <label className="ld-check" title="Add Context and Bench memberships">
          <input type="checkbox" checked={includeSilent} onChange={(e) => setIncludeSilent(e.target.checked)} />
          Show Context and Bench
        </label>
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>
      <div className="dash-note ld-work">{workLine(data)}</div>
      <div className="dash-note ld-work">{noReadingLine(data)}</div>
      {data.roster_fallback_cards > 0 && (
        <div className="dash-note ld-work">
          Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
          point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
        </div>
      )}
      <div className="dash-note ld-legend">
        End-of-day readings from the Cockpit's own display signals, one row per company with a chip per card; none
        feeds a call. A company a member gave no reading is not on that list and is counted above. Click a name or a
        card to open it in the Cockpit.
      </div>

      <Several data={data} onSelect={onSelectThesis} />
      <Lists data={data} onSelect={onSelectThesis} />
      <InsiderGroups data={data} groupBy={groupBy} onGroupBy={setGroupBy} onSelect={onSelectThesis} />
    </div>
  );
}
