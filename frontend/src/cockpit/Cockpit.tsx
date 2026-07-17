import { Fragment, useState } from "react";
import { flushSync } from "react-dom";

import { useCall, useDisplaySignals, useThesis, useWorkbenchScored } from "../api/hooks";
import { CallCard } from "../components/CallCard";
import { CatalystEditor, KillCriteriaEditor } from "./SpineListEditors";
import { MemberMenu } from "../components/MemberMenu";
import {
  groupBasket,
  nameKeyFor,
  resolveNameKey,
  type BucketKey,
  type BucketRow,
} from "./buckets";
import { NamePanel } from "./NamePanel";
import { exportKeptNames, toExportedName } from "../util/exportNames";
import {
  accentVar,
  archLabel,
  daysFrom,
  fmtDate,
  STATE_CLASS,
  STATE_LABEL,
  tickerLabel,
} from "../util/format";
import { formatMarketCap } from "../workbench/format";

interface Props {
  thesisId: string;
  asof: string;
  onAsofChange: (asof: string) => void;
  onBack?: () => void;
  /** The per-name panel's selection key (?name= — a ticker, or a security_id for duplicate
   *  tickers), owned by the URL via App's CockpitRoute so a scoreboard row / a shared link can
   *  land with the panel already open. */
  selectedName: string | null;
  onSelectName: (key: string | null) => void;
}

export function Cockpit({
  thesisId,
  asof,
  onAsofChange,
  onBack,
  selectedName,
  onSelectName,
}: Props) {
  const thesisQ = useThesis(thesisId);
  const callQ = useCall(thesisId, asof);
  const scoredQ = useWorkbenchScored(thesisId, asof);
  // read-only per-name indicators, fetched ONCE at page level (a future table cell shares this query)
  const displayQ = useDisplaySignals(thesisId, asof);
  const thesis = thesisQ.data;
  const card = callQ.data;

  const state = card?.state ?? "incubating";
  const sc = STATE_CLASS[state] ?? "incub";

  const basket = thesis?.basket ?? [];
  // The per-name buckets (Managing … Quiet): the basket partitioned by each member's OWN call —
  // display-only joins over data this page already fetches (no call is re-derived here). While the
  // call is still computing (card undefined) everything reads Quiet, honestly.
  const groups = groupBasket(basket, card, scoredQ.data?.members);
  const exportRows = groups
    .flatMap((g) => g.rows)
    .map((r) => toExportedName({ ticker: r.member.ticker, name: r.scored?.name }));

  // The per-name panel's selection — lifted to the URL (the selectedName prop, ?name= via App's
  // CockpitRoute) and RESOLVED to a row on every render, so a deep link opens the panel the moment
  // data loads. Ordinal stays the render identity (duplicate tickers remain distinct rows); the
  // panel is a SIBLING overlay: opening/closing/switching never unmounts the table, so grouping,
  // dots, and scroll survive exactly as left. If the key matches nothing (a stale link, a basket
  // edit), the panel simply doesn't render — no strand.
  const selected = resolveNameKey(groups, selectedName);
  const selOrdinal = selected?.row.ordinal ?? null;
  const toggleRow = (r: BucketRow) =>
    onSelectName(r.ordinal === selOrdinal ? null : nameKeyFor(r, basket));

  // Collapsible buckets — open by default; a collapse is an explicit, reversible view filter (the
  // header keeps its count while closed, so nothing reads as dropped). Local view state only.
  const [closedGroups, setClosedGroups] = useState<Set<BucketKey>>(new Set());
  const toggleGroup = (key: BucketKey) => {
    const apply = () =>
      setClosedGroups((s) => {
        const next = new Set(s);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
    // The fold rides a View Transition so the rows below SLIDE up/down instead of snapping —
    // table rows can't height-animate, so we animate the layout change itself. flushSync makes
    // React commit inside the snapshot callback; jsdom/older browsers take the instant path.
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
    if (doc.startViewTransition) doc.startViewTransition(() => flushSync(apply));
    else apply();
  };

  const evidence = thesis?.evidence ?? [];
  const catalysts = thesis?.catalysts ?? [];
  const killCriteria = thesis?.kill_criteria ?? [];

  return (
    <div className="cp-shell">
      <header className="cp-top">
        {onBack && (
          <button type="button" className="back" onClick={onBack}>
            ← Board
          </button>
        )}
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <div className="cp-title">
          <span className="tk" style={{ color: `var(${accentVar(sc)})` }}>
            {tickerLabel(thesis?.ticker, basket.length)}
          </span>
          <h1>{thesis?.name ?? "…"}</h1>
          {card && (
            <span
              className="state-badge"
              style={{
                color: `var(--${sc})`,
                background: `color-mix(in srgb, var(--${sc}) 14%, transparent)`,
                border: `1px solid color-mix(in srgb, var(--${sc}) 40%, transparent)`,
              }}
            >
              {STATE_LABEL[state]}
            </span>
          )}
        </div>
        <div className="spacer" />
        <label className="asof">
          as-of
          <input type="date" value={asof} onChange={(e) => onAsofChange(e.target.value)} />
        </label>
      </header>

      <div className="cp-body">
        <main className="cp-main">
          {thesisQ.isLoading && <p className="muted">Loading thesis…</p>}
          {thesisQ.error && <p style={{ color: "var(--neg)" }}>Failed to load the thesis.</p>}

          {thesis && (
            <>
              <section className="sect">
                <div className="sect-h">Narrative &amp; conviction</div>
                <div className="narrative">
                  {thesis.narrative}
                  <span className="by">— your notes, preserved</span>
                </div>
              </section>

              <section className="sect">
                <div className="sect-h">
                  Basket · the expression
                  <button
                    type="button"
                    className="wb-mini ghost"
                    disabled={exportRows.length === 0}
                    aria-label={`export ${exportRows.length} board names`}
                    onClick={() =>
                      exportKeptNames({
                        thesisName: thesis.name,
                        stage: "board",
                        asof,
                        rows: exportRows,
                      })
                    }
                  >
                    Export ({exportRows.length})
                  </button>
                </div>
                {/* Grouped by each member's own call-state bucket (strongest → weakest, the Board's
                    column idiom in-table). The dead Role/Detail columns are gone from the table —
                    the authored text survives on the per-name panel, not as an all-"—" column.
                    Empty buckets render no header (loudness marks the exception). */}
                <table className="basket">
                  <thead>
                    <tr>
                      <th className="dotc" aria-label="status" />
                      <th>Ticker</th>
                      <th>Name</th>
                      <th>Archetype</th>
                      <th style={{ textAlign: "right" }}>Mkt cap</th>
                      <th style={{ textAlign: "right" }}>Exit-by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map(({ def, rows }) => (
                      <Fragment key={def.key}>
                        <tr className={`grp ${def.cls}`}>
                          <td colSpan={6}>
                            {/* the To Review heading idiom (chev · label · hint · count · hairline),
                                bucket-colored; click-to-collapse, open by default — the count stays
                                visible while closed, so a collapsed bucket never reads as dropped */}
                            <button
                              type="button"
                              className="grp-h"
                              aria-expanded={!closedGroups.has(def.key)}
                              onClick={() => toggleGroup(def.key)}
                            >
                              {/* one glyph, rotated closed — the swap read as a flicker */}
                              <span className="chev">▾</span>
                              <span className="lbl">{def.label}</span>
                              <em className="hint">· {def.hint}</em>
                              <span className="ct">· {rows.length}</span>
                            </button>
                          </td>
                        </tr>
                        {/* folded rows stay MOUNTED and visibility-COLLAPSE (never unmount):
                            a collapsed row still feeds the column-width algorithm, so folding
                            the bucket with the widest cells can't re-flow the columns */}
                        {rows.map((r) => (
                          <tr
                            key={r.ordinal}
                            className={`bkt ${def.cls}${closedGroups.has(def.key) ? " folded" : ""}${r.ordinal === selOrdinal ? " sel" : ""}`}
                            tabIndex={0}
                            aria-selected={r.ordinal === selOrdinal}
                            onClick={() => toggleRow(r)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                toggleRow(r);
                              }
                            }}
                          >
                            <td className="dotc">
                              <span className="rowdot" title={def.label} />
                            </td>
                            <td className="tk">{r.member.ticker}</td>
                            <td className="co">
                              {r.scored?.name ?? <span className="muted">—</span>}
                            </td>
                            <td>
                              {/* a DECIDED archetype only (item F): an unset one renders a quiet "—",
                                  never the string "null" (the decision lives on the Workbench rail) */}
                              {r.member.archetype ? (
                                <span className={`arch ${r.member.archetype}`}>
                                  {archLabel(r.member.archetype)}
                                </span>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                            <td className="met">
                              {/* computed market cap (the scoring engine, re-derived on read),
                                  bridged by security_id — "—" when un-scored / no price+shares facts */}
                              {formatMarketCap(r.scored?.market_cap.value)}
                            </td>
                            <td className={`met exitby${r.call?.lapsing ? " lapse" : ""}`}>
                              {r.call?.exit_by
                                ? `${r.call.lapsing ? "lapses " : ""}${fmtDate(r.call.exit_by)}`
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </section>

              {evidence.length > 0 && (
                <section className="sect vt-evidence">
                  <div className="sect-h">Evidence</div>
                  {evidence.map((e) => (
                    <div className="ev" key={e.id}>
                      <span className="typ">{e.kind}</span>
                      <span className="lbl">{e.label}</span>
                      <span className="dt">{e.date_label ?? ""}</span>
                    </div>
                  ))}
                </section>
              )}

              {/* both sections render EVEN AT ZERO now — an unauthored thesis needs the authoring
                  entry point (the sections used to vanish when empty, which made "no way to add
                  one" invisible). The editors write through the sole-writer endpoints; a promote
                  can never wipe the lists (the structural guard, server-side). */}
              <section className="sect vt-cats">
                <div className="sect-h">Catalyst calendar</div>
                {catalysts.map((c) => {
                  const d = daysFrom(asof, c.when_date);
                  const soon = d !== null && d >= 0 && d <= 21;
                  const when = c.when_date
                    ? `${fmtDate(c.when_date)}${d !== null && d >= 0 ? ` · ${d}d` : ""}`
                    : (c.when_label ?? "—");
                  return (
                    <div className={`cat ${soon ? "soon" : ""}`} key={c.id}>
                      <span className="when">{when}</span>
                      <span className="lbl">{c.label}</span>
                      <span className="kind">{c.kind ?? ""}</span>
                    </div>
                  );
                })}
                <CatalystEditor thesisId={thesisId} catalysts={catalysts} />
              </section>

              <section className="sect vt-kills">
                <div className="sect-h">Kill criteria</div>
                {killCriteria.map((k) => (
                  <div className="kill" key={k.id}>
                    {k.text}
                  </div>
                ))}
                <KillCriteriaEditor thesisId={thesisId} kills={killCriteria} />
              </section>
            </>
          )}
        </main>

        {/* the thesis-level rail stays (no longer the ONLY per-name view); it dims — not hides —
            under the panel overlay, and comes right back on close */}
        <aside className={`cp-rail${selected ? " dimmed" : ""}`}>
          {callQ.isLoading && <p className="muted">Computing the call…</p>}
          {callQ.error && <p style={{ color: "var(--neg)" }}>Failed to compute the call.</p>}
          {card && <CallCard card={card} thesisId={thesisId} />}
          {card && <MemberMenu card={card} />}
        </aside>
      </div>

      {selected && (
        <NamePanel
          row={selected.row}
          def={selected.def}
          card={card}
          thesisId={thesisId}
          position={thesis?.position}
          display={
            displayQ.data?.members.find(
              (m) => m.security_id === selected.row.member.security_id,
            ) ?? null
          }
          asof={asof}
          onClose={() => onSelectName(null)}
        />
      )}
    </div>
  );
}
