import { useMemo, useRef, useState } from "react";

import { CONTEXT_ROLE_DESCRIPTION, contextRoleTag } from "../../workbench/format";
import { SILENT_DESCRIPTORS, isSilentLink } from "../../workbench/useChainDraft";
import type { DashboardProps } from "../registry";
import "./shared-names.css";
import {
  useSharedNames,
  type SharedNameCardRefOut,
  type SharedNameChipOut,
  type SharedNameRowOut,
  type SharedNamesOut,
} from "./useSharedNames";

// Shared Names (docs/dashboards/shared-names.md): the companies on more than one live card, and which cards
// share them. A "now" view, display only. Every rule (matching, silent links, "opposite sides", the counting
// toggle, the sorts) is computed server-side; this page renders the payload and filters what is on screen.

type Pair = { a: SharedNameCardRefOut; b: SharedNameCardRefOut };

const isPair = (p: Pair | null, a: string, b: string) =>
  p !== null && p.a.thesis_id === a && p.b.thesis_id === b;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The hover on an "opposite sides" row: the cards on each side, named from the chips. */
function sidesTitle(row: SharedNameRowOut): string {
  const counts = row.cards.filter((c) => c.counts).map((c) => c.thesis_name);
  const counter = row.cards
    .filter((c) => c.context_role === "counter_case")
    .map((c) => c.thesis_name);
  return `Counts on: ${counts.join(", ")} · Counter-case on: ${counter.join(", ")}`;
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: SharedNamesOut): string {
  const parts = [
    plural(d.live_cards, "live card", "live cards"),
    `${plural(d.names.length, "company", "companies")} on 2+ cards`,
    "matched by security",
    d.counting_only
      ? `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`
      : "Context and Bench included",
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not matched`,
  ];
  return parts.join(" · ");
}

function Chip({
  chip,
  onSelect,
}: {
  chip: SharedNameChipOut;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const links = chip.links.map((l) => l ?? "Unsegmented").join(", ");
  const counter = chip.context_role === "counter_case";
  return (
    <button
      type="button"
      className={`sn-chip${chip.counts ? "" : " quiet"}${counter ? " is-counter" : ""}`}
      title={`Open ${chip.thesis_name} on ${chip.ticker}`}
      onClick={() => onSelect(chip.thesis_id, chip.ticker)}
    >
      <span className="sn-card">{chip.thesis_name}</span>
      {links && <span className="sn-links">{links}</span>}
      {chip.silent_link && isSilentLink(chip.silent_link) && (
        <span className="silent-tag" title={SILENT_DESCRIPTORS[chip.silent_link]}>
          {chip.silent_link.toLowerCase()}
        </span>
      )}
      {chip.context_role && (
        <span className="role-tag" title={CONTEXT_ROLE_DESCRIPTION[chip.context_role]}>
          {contextRoleTag(chip.context_role)}
        </span>
      )}
    </button>
  );
}

/** Zero-text neutral proportional bar for card count / shared count. */
function CountBar({ value, max }: { value: number; max: number }) {
  if (max <= 0) return null;
  const pct = Math.max(8, Math.min(100, (value / max) * 100));
  return (
    <span className="sn-bar" aria-hidden="true">
      <span className="sn-bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

type MainSortCol = "ticker" | "name" | "card_count";
type PairSortCol = "card_a" | "card_b" | "shared_count";
type SortDir = "asc" | "desc";

export default function SharedNames({ onSelectThesis }: DashboardProps) {
  const [countingOnly, setCountingOnly] = useState(false);
  const [oppositeOnly, setOppositeOnly] = useState(false);
  const [pair, setPair] = useState<Pair | null>(null);
  const [query, setQuery] = useState("");
  const [cardFilter, setCardFilter] = useState<string>("");
  const [mainSort, setMainSort] = useState<{ col: MainSortCol; dir: SortDir }>({ col: "card_count", dir: "desc" });
  const [pairSort, setPairSort] = useState<{ col: PairSortCol; dir: SortDir }>({ col: "shared_count", dir: "desc" });
  const mainRef = useRef<HTMLElement>(null);
  const q = useSharedNames(countingOnly);
  const data = q.data;

  // the pair filter keeps the two cards; its companies come from the CURRENT payload, so the counting
  // toggle can never leave a stale set on screen (a pair that shares nothing in this view shows 0)
  const pairIds = useMemo(() => {
    if (!pair || !data) return null;
    const hit = data.pairs.find((p) =>
      isPair(pair, p.card_a.thesis_id, p.card_b.thesis_id),
    );
    return new Set(hit?.security_ids ?? []);
  }, [pair, data]);

  const cardOptions = useMemo(() => {
    if (!data) return [];
    const seen = new Map<string, string>();
    for (const r of data.names) {
      for (const c of r.cards) seen.set(c.thesis_id, c.thesis_name);
    }
    return [...seen.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name, "en-US", { sensitivity: "base" }));
  }, [data]);

  if (q.isLoading) return <div className="dash-note">Loading…</div>;
  if (q.error || !data) {
    return <div className="dash-note">Could not load Shared Names.</div>;
  }

  const qNorm = query.trim().toLowerCase();
  const oppositeCount = data.names.filter((r) => r.opposite_sides).length;
  const filteredRows = data.names.filter((r) => {
    if (oppositeOnly && !r.opposite_sides) return false;
    if (pairIds && !pairIds.has(r.security_id)) return false;
    if (cardFilter && !r.cards.some((c) => c.thesis_id === cardFilter)) return false;
    if (qNorm) {
      const hit =
        r.ticker.toLowerCase().includes(qNorm) ||
        (r.name ?? "").toLowerCase().includes(qNorm) ||
        r.cards.some((c) => c.thesis_name.toLowerCase().includes(qNorm));
      if (!hit) return false;
    }
    return true;
  });

  const rows = [...filteredRows].sort((a, b) => {
    const sign = mainSort.dir === "asc" ? 1 : -1;
    if (mainSort.col === "card_count") {
      return (a.card_count - b.card_count) * sign || a.ticker.localeCompare(b.ticker);
    }
    if (mainSort.col === "ticker") {
      return a.ticker.localeCompare(b.ticker) * sign;
    }
    const na = a.name ?? "";
    const nb = b.name ?? "";
    if (!na && nb) return 1;
    if (!nb && na) return -1;
    return na.localeCompare(nb, "en-US", { sensitivity: "base" }) * sign || a.ticker.localeCompare(b.ticker);
  });

  const filteredPairs = data.pairs.filter((p) => {
    if (cardFilter && p.card_a.thesis_id !== cardFilter && p.card_b.thesis_id !== cardFilter) return false;
    if (qNorm) {
      const hit =
        p.card_a.thesis_name.toLowerCase().includes(qNorm) ||
        p.card_b.thesis_name.toLowerCase().includes(qNorm) ||
        p.tickers.some((t) => t.toLowerCase().includes(qNorm));
      if (!hit) return false;
    }
    return true;
  });

  const sortedPairs = [...filteredPairs].sort((a, b) => {
    const sign = pairSort.dir === "asc" ? 1 : -1;
    if (pairSort.col === "shared_count") {
      return (
        (a.shared_count - b.shared_count) * sign ||
        a.card_a.thesis_name.localeCompare(b.card_a.thesis_name) ||
        a.card_b.thesis_name.localeCompare(b.card_b.thesis_name)
      );
    }
    const va = pairSort.col === "card_a" ? a.card_a.thesis_name : a.card_b.thesis_name;
    const vb = pairSort.col === "card_a" ? b.card_a.thesis_name : b.card_b.thesis_name;
    return va.localeCompare(vb, "en-US", { sensitivity: "base" }) * sign || b.shared_count - a.shared_count;
  });

  const maxCards = data.names.reduce((m, r) => Math.max(m, r.card_count), 0);
  const maxShared = data.pairs.reduce((m, p) => Math.max(m, p.shared_count), 0);
  const sharedShare = data.companies > 0 ? Math.round((data.names.length / data.companies) * 100) : 0;

  const flipMain = (col: MainSortCol) =>
    setMainSort((s) =>
      s.col === col ? { col, dir: s.dir === "desc" ? "asc" : "desc" } : { col, dir: col === "card_count" ? "desc" : "asc" },
    );
  const flipPair = (col: PairSortCol) =>
    setPairSort((s) =>
      s.col === col ? { col, dir: s.dir === "desc" ? "asc" : "desc" } : { col, dir: col === "shared_count" ? "desc" : "asc" },
    );
  const mainArrow = (col: MainSortCol) => (mainSort.col === col ? (mainSort.dir === "asc" ? " ↑" : " ↓") : "");
  const pairArrow = (col: PairSortCol) => (pairSort.col === col ? (pairSort.dir === "asc" ? " ↑" : " ↓") : "");

  const choosePair = (p: Pair) => {
    if (isPair(pair, p.a.thesis_id, p.b.thesis_id)) {
      setPair(null); // a second click on the active pair clears it (its inverse, WB#1)
      return;
    }
    setPair(p);
    mainRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  };

  return (
    <div className="sn">
      <div className="sn-controls">
        <label
          className="sn-check"
          title="Drop Context and Bench memberships before both sections are computed"
        >
          <input
            type="checkbox"
            checked={countingOnly}
            onChange={(e) => setCountingOnly(e.target.checked)}
          />
          Counting names only
        </label>
        {(oppositeCount > 0 || oppositeOnly) && (
          <button
            type="button"
            className={`sn-toggle${oppositeOnly ? " on" : ""}`}
            aria-pressed={oppositeOnly}
            title="Companies that count on one card and are tagged counter-case on a different card"
            onClick={() => setOppositeOnly((v) => !v)}
          >
            Opposite sides only · {oppositeCount}
          </button>
        )}
        <input
          type="search"
          className="sn-search"
          aria-label="Filter ticker, company, or card"
          placeholder="Find ticker, company, or card…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {cardOptions.length > 1 && (
          <select
            className="sn-select"
            aria-label="Filter by card"
            value={cardFilter}
            onChange={(e) => setCardFilter(e.target.value)}
          >
            <option value="">all cards</option>
            {cardOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        {(query.trim() || cardFilter) && (
          <button
            type="button"
            className="sn-clear"
            onClick={() => {
              setQuery("");
              setCardFilter("");
            }}
          >
            clear search
          </button>
        )}
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>
      <div className="dash-note sn-work">{workLine(data)}</div>
      {data.companies > 0 && (
        <div className="dash-note sn-denom" data-testid="sn-denom">
          {data.names.length.toLocaleString("en-US")} of {data.companies.toLocaleString("en-US")} distinct companies sit on 2+ cards ({sharedShare}%).
        </div>
      )}

      <section className="sn-sec" ref={mainRef}>
        <h2 className="sn-h">
          Companies on 2+ cards
          <span className="sn-ct">
            {rows.length === data.names.length
              ? ` · ${data.names.length}`
              : ` · showing ${rows.length} of ${data.names.length}`}
          </span>
        </h2>
        {pair && (
          <div className="sn-filter">
            <span>
              Shared by {pair.a.thesis_name} and {pair.b.thesis_name} ·{" "}
              {plural(pairIds?.size ?? 0, "company", "companies")}
            </span>
            <button type="button" className="sn-clear" onClick={() => setPair(null)}>
              clear
            </button>
          </div>
        )}
        {data.names.length === 0 ? (
          <div className="dash-note">No company sits on more than one live card.</div>
        ) : rows.length === 0 ? (
          <div className="dash-note">No company matches these filters.</div>
        ) : (
          <div className="sn-main-scroll">
            <table className="sn-table">
              <thead>
                <tr>
                  <th aria-sort={mainSort.col === "ticker" ? (mainSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className={`sn-sort${mainSort.col === "ticker" ? " on" : ""}`} onClick={() => flipMain("ticker")}>
                      Ticker{mainArrow("ticker")}
                    </button>
                  </th>
                  <th aria-sort={mainSort.col === "name" ? (mainSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className={`sn-sort${mainSort.col === "name" ? " on" : ""}`} onClick={() => flipMain("name")}>
                      Company{mainArrow("name")}
                    </button>
                  </th>
                  <th
                    className="sn-num"
                    aria-sort={mainSort.col === "card_count" ? (mainSort.dir === "asc" ? "ascending" : "descending") : "none"}
                    title="Sorted by the number of live cards each company sits on, then by ticker"
                  >
                    <button type="button" className={`sn-sort${mainSort.col === "card_count" ? " on" : ""}`} onClick={() => flipMain("card_count")}>
                      On the most cards{mainArrow("card_count")}
                    </button>
                  </th>
                  <th>Cards</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.security_id} className={r.opposite_sides ? "is-opp" : undefined}>
                    <td className="tk">
                      {r.ticker}
                      {r.opposite_sides && (
                        <span className="sn-opp" title={sidesTitle(r)}>
                          opposite sides
                        </span>
                      )}
                    </td>
                    <td className="co">
                      {r.name ? (
                        <span className="sn-co-name" title={r.name}>
                          {r.name}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="sn-num">
                      <span>{r.card_count}</span>
                      <CountBar value={r.card_count} max={maxCards} />
                    </td>
                    <td>
                      <div className="sn-chips">
                        {r.cards.map((c) => (
                          <Chip key={c.thesis_id} chip={c} onSelect={onSelectThesis} />
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="sn-sec">
        <h2 className="sn-h">
          Card pairs
          <span className="sn-ct"> · {plural(sortedPairs.length, "pair", "pairs")}</span>
        </h2>
        {data.pairs.length === 0 ? (
          <div className="dash-note">No two live cards share a company.</div>
        ) : sortedPairs.length === 0 ? (
          <div className="dash-note">No card pair matches these filters.</div>
        ) : (
          <div className="sn-pairs-scroll">
            <table className="sn-table sn-pairs">
              <thead>
                <tr>
                  <th aria-sort={pairSort.col === "card_a" ? (pairSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className={`sn-sort${pairSort.col === "card_a" ? " on" : ""}`} onClick={() => flipPair("card_a")}>
                      Card A{pairArrow("card_a")}
                    </button>
                  </th>
                  <th aria-sort={pairSort.col === "card_b" ? (pairSort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className={`sn-sort${pairSort.col === "card_b" ? " on" : ""}`} onClick={() => flipPair("card_b")}>
                      Card B{pairArrow("card_b")}
                    </button>
                  </th>
                  <th
                    className="sn-num"
                    aria-sort={pairSort.col === "shared_count" ? (pairSort.dir === "asc" ? "ascending" : "descending") : "none"}
                    title="Sorted by shared companies, then by card names"
                  >
                    <button type="button" className={`sn-sort${pairSort.col === "shared_count" ? " on" : ""}`} onClick={() => flipPair("shared_count")}>
                      Shared{pairArrow("shared_count")}
                    </button>
                  </th>
                  <th>Companies</th>
                </tr>
              </thead>
              <tbody>
                {sortedPairs.map((p) => {
                  const on = isPair(pair, p.card_a.thesis_id, p.card_b.thesis_id);
                  const select = () => choosePair({ a: p.card_a, b: p.card_b });
                  return (
                    <tr
                      key={`${p.card_a.thesis_id}:${p.card_b.thesis_id}`}
                      className={on ? "on" : undefined}
                      tabIndex={0}
                      aria-selected={on}
                      title="Show only these companies in the table above"
                      onClick={select}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          select();
                        }
                      }}
                    >
                      <td>{p.card_a.thesis_name}</td>
                      <td>{p.card_b.thesis_name}</td>
                      <td className="sn-num">
                        <span>{p.shared_count}</span>
                        <CountBar value={p.shared_count} max={maxShared} />
                      </td>
                      <td className="sn-tickers">{p.tickers.join(" · ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
