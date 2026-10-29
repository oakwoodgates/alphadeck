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
  return (
    <button
      type="button"
      className={`sn-chip${chip.counts ? "" : " quiet"}`}
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

export default function SharedNames({ onSelectThesis }: DashboardProps) {
  const [countingOnly, setCountingOnly] = useState(false);
  const [oppositeOnly, setOppositeOnly] = useState(false);
  const [pair, setPair] = useState<Pair | null>(null);
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

  if (q.isLoading) return <div className="dash-note">Loading…</div>;
  if (q.error || !data) {
    return <div className="dash-note">Could not load Shared Names.</div>;
  }

  const oppositeCount = data.names.filter((r) => r.opposite_sides).length;
  const rows = data.names.filter(
    (r) => (!oppositeOnly || r.opposite_sides) && (!pairIds || pairIds.has(r.security_id)),
  );

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
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>
      <div className="dash-note sn-work">{workLine(data)}</div>

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
          <table className="sn-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Company</th>
                <th
                  className="sn-num"
                  title="Sorted by the number of live cards each company sits on, then by ticker"
                >
                  On the most cards ↓
                </th>
                <th>Cards</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.security_id}>
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
                  <td className="sn-num">{r.card_count}</td>
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
        )}
      </section>

      <section className="sn-sec">
        <h2 className="sn-h">
          Card pairs
          <span className="sn-ct"> · {plural(data.pairs.length, "pair", "pairs")}</span>
        </h2>
        {data.pairs.length === 0 ? (
          <div className="dash-note">No two live cards share a company.</div>
        ) : (
          <div className="sn-pairs-scroll">
            <table className="sn-table sn-pairs">
              <thead>
                <tr>
                  <th>Card A</th>
                  <th>Card B</th>
                  <th className="sn-num" title="Sorted by shared companies, then by card names">
                    Shared ↓
                  </th>
                  <th>Companies</th>
                </tr>
              </thead>
              <tbody>
                {data.pairs.map((p) => {
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
                      <td className="sn-num">{p.shared_count}</td>
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
