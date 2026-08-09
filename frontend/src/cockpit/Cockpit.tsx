import { Fragment, useState } from "react";
import { flushSync } from "react-dom";

import type { DisplaySignal } from "../api/hooks";
import { useCall, useDisplaySignals, useThesis, useWorkbenchScored } from "../api/hooks";
import { CallCard } from "../components/CallCard";
import { InsiderCell, PostureCell, ReturnCells, RvolCell } from "./DisplaySignalsSection";
import { CatalystEditor, KillCriteriaEditor } from "./SpineListEditors";
import { MemberMenu } from "../components/MemberMenu";
import {
  dedupeBySecurityId,
  groupBasket,
  groupByBusinessType,
  groupBySegment,
  nameKeyFor,
  resolveNameKey,
  type BucketDef,
  type BucketRow,
} from "./buckets";
import { NamePanel } from "./NamePanel";
import { exportKeptNames, exportWatchlist, toExportedName } from "../util/exportNames";
import {
  accentVar,
  businessTypeLabel,
  daysFrom,
  fmtDate,
  STATE_CLASS,
  STATE_LABEL,
  supersectorLabel,
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

/** The entry-window (confirmation) clock, rendered inside an armed-family member's exit-by cell.
 *  This is the clock that actually governs how long the member STAYS armed — a member de-arms on
 *  `arm_until`, which can be a month before the exit_by "lapses" date the cell leads with (the live
 *  CRVO/MPLT confusion: "Armed · Dec 8" yet de-armed Jul 19). Loud (`.closing`) inside a week or
 *  once lapsed; muted otherwise. Mirrors the NamePanel two-clock idiom. */
function EntryWindow({ asof, armUntil }: { asof: string; armUntil: string }) {
  const armDays = daysFrom(asof, armUntil);
  return (
    <span className={`entry-window${armDays !== null && armDays <= 7 ? " closing" : ""}`}>
      entry closes {fmtDate(armUntil)}
      {armDays !== null && (armDays < 0 ? " · lapsed" : ` · ${armDays}d`)}
    </span>
  );
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
  // read-only per-name indicators, fetched ONCE at page level — the panel's top strip and the
  // basket table's SMA cell read the same query, bridged by security_id
  const displayQ = useDisplaySignals(thesisId, asof);
  const smaBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const sig = (m.signals ?? []).find((s) => s.kind === "sma_position");
    if (sig) smaBySid.set(m.security_id, sig);
  }
  // trailing price returns (1d/7d/30d/90d) — the SAME display-signals query, one more member on the
  // generic payload, bridged by security_id exactly like the SMA cell above
  const trailBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const sig = (m.signals ?? []).find((s) => s.kind === "trailing_returns");
    if (sig) trailBySid.set(m.security_id, sig);
  }
  // relative volume (RVOL) — the SAME display-signals query, bridged by security_id like the SMA and
  // trailing-return cells; a volume-backed move reads a warm accent off the wire's loud threshold
  const rvolBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const sig = (m.signals ?? []).find((s) => s.kind === "rvol");
    if (sig) rvolBySid.set(m.security_id, sig);
  }
  // insider open-market buys (30d + 90d) — the SAME display-signals query, bridged by security_id
  // like the SMA / trailing-return / RVOL cells. The `insider_flow_90d` member now emits a 30d
  // sub-window alongside its 90d metrics off ONE fetch; two basket columns read `{buys}/{buyers}`
  // per window off it (a cluster of ≥2 distinct buyers accents — breadth is the conviction tell).
  const insiderBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const sig = (m.signals ?? []).find((s) => s.kind === "insider_flow_90d");
    if (sig) insiderBySid.set(m.security_id, sig);
  }
  const thesis = thesisQ.data;
  const card = callQ.data;

  const state = card?.state ?? "incubating";
  const sc = STATE_CLASS[state] ?? "incub";

  const basket = thesis?.basket ?? [];
  // The per-name buckets (Managing … Quiet): the basket partitioned by each member's OWN call —
  // display-only joins over data this page already fetches (no call is re-derived here). While the
  // call is still computing (card undefined) everything reads Quiet, honestly.
  const groups = groupBasket(basket, card, scoredQ.data?.members);
  // The NON-segment lenses show ONE row per name: a multi-segment name has N identical rows (one per
  // value-chain link) that would otherwise render as confusing duplicates. The value-chain lens keeps
  // the FULL `groups` (each link-row intended); `groups` also stays full for selection + exports.
  const dedupedGroups = dedupeBySecurityId(groups);
  // Foreign-filer annotation on the thesis CallCard — ONLY for a single-name thesis whose sole member is a
  // §16-exempt 20-F/40-F filer (a multi-name basket annotates per-name on the NamePanel instead, #7). The
  // form scopes the card's conviction sub-note; absent (domestic, unknown, multi-name, or scored not yet
  // loaded) → the card renders unannotated (byte-identical to before).
  const soleMember = basket.length === 1 ? basket[0] : undefined;
  const soleForeignForm = soleMember
    ? scoredQ.data?.members.find((m) => m.security_id === soleMember.security_id)?.foreign_filer_form
    : undefined;
  const foreignFiler = soleForeignForm ? { form: soleForeignForm } : null;
  const exportRows = groups
    .flatMap((g) => g.rows)
    .map((r) => toExportedName({ ticker: r.member.ticker, name: r.scored?.name }));
  // The TradingView-watchlist export needs the exchange (mapped to a TV prefix, bare when unmappable) —
  // and only a tickered name can go in a watchlist, so drop the ticker-less rows (the count then reflects
  // exactly what exports). Exchange rides on the scored member, bridged by security_id like the name.
  // Emit the RESOLVED vendor symbol when set (price_symbol ?? ticker) so an OTC name exports as OTC:FDCTD —
  // the symbol TradingView actually indexes the full history under — not the starved SEC ticker (OTC:FDCT).
  // buildWatchlistTxt dedupes by emitted symbol, so two members resolving to one vendor symbol collapse.
  const watchlistRows = groups
    .flatMap((g) => g.rows)
    .map((r) => ({
      ticker: r.scored?.price_symbol ?? r.member.ticker ?? "",
      exchange: r.scored?.exchange ?? null,
    }))
    .filter((r) => r.ticker.trim() !== "");

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

  // The basket LENS: group by each member's call-state bucket (the default — the call is the
  // product), by business-type super-sector ("are the utilities moving?"), or by value-chain link
  // ("which part of the chain is moving?"). Local view state; every lens renders the SAME names — a
  // lens re-orders, never drops. Call-state + business-type dedupe to one row per name; the
  // value-chain lens shows every link-row (a multi-segment name appears under each of its links).
  const [groupMode, setGroupMode] = useState<"state" | "type" | "segment">("state");
  const segments = thesis?.segments ?? [];
  // Gate the value-chain lens on the thesis actually having a decomposed chain (≥1 placed segment):
  // an undecomposed basket has nothing to group by (interaction principle #3 — a control that can't
  // discriminate shouldn't render). If it can't be shown, coerce a stale "segment" mode back to state.
  const hasValueChain = basket.some((m) => !!m.segment);
  const lensMode: "state" | "type" | "segment" =
    groupMode === "segment" && !hasValueChain ? "state" : groupMode;
  // One render shape for both lenses: each group carries its header identity + the rows WITH their
  // call-state def, so the state dot / exit-by survive the type lens (the call never disappears).
  const renderGroups: {
    key: string;
    cls: string;
    label: string;
    hint: string | null;
    rows: { row: BucketRow; def: BucketDef }[];
  }[] =
    lensMode === "state"
      ? dedupedGroups.map((g) => ({
          key: g.def.key,
          cls: g.def.cls,
          label: g.def.label,
          hint: g.def.hint,
          rows: g.rows.map((row) => ({ row, def: g.def })),
        }))
      : lensMode === "type"
        ? groupByBusinessType(dedupedGroups).map((g) => ({
            key: `type:${g.key}`,
            cls: "bkt-type",
            label: supersectorLabel(g.key === "unclassified" ? null : g.key),
            hint: null,
            rows: g.rows,
          }))
        : // the value-chain lens uses the FULL groups (never deduped) — a multi-segment name is
          // meant to appear under each of its links; the descriptor rides the header hint
          groupBySegment(groups, segments).map((g) => ({
            key: `seg:${g.key}`,
            cls: "bkt-segment",
            label: g.label,
            hint: g.descriptor,
            rows: g.rows,
          }));

  // Collapsible buckets — open by default; a collapse is an explicit, reversible view filter (the
  // header keeps its count while closed, so nothing reads as dropped). Local view state only,
  // keyed per lens (a fold in one lens doesn't leak into the other).
  const [closedGroups, setClosedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = (key: string) => {
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
                    title="Download the basket as JSON (ticker + name, sorted for a stable diff)"
                    onClick={() =>
                      exportKeptNames({
                        thesisName: thesis.name,
                        stage: "board",
                        asof,
                        rows: exportRows,
                      })
                    }
                  >
                    Export JSON ({exportRows.length})
                  </button>
                  <button
                    type="button"
                    className="wb-mini ghost"
                    disabled={watchlistRows.length === 0}
                    aria-label={`export ${watchlistRows.length} names as a TradingView watchlist`}
                    title="Download a .txt that imports directly into a TradingView watchlist (EXCHANGE:TICKER)"
                    onClick={() =>
                      exportWatchlist({ thesisName: thesis.name, asof, rows: watchlistRows })
                    }
                  >
                    Export Watchlist ({watchlistRows.length})
                  </button>
                  {/* the lens toggle: call-state stays the DEFAULT (the call is the product); the
                      other lenses re-group the SAME names by super-sector or value-chain link. A view
                      choice, not a filter — nothing hides, nothing drops. */}
                  <span className="lens" role="group" aria-label="group basket by">
                    <button
                      type="button"
                      className={`wb-mini ghost${lensMode === "state" ? " on" : ""}`}
                      aria-pressed={lensMode === "state"}
                      onClick={() => setGroupMode("state")}
                    >
                      call state
                    </button>
                    <button
                      type="button"
                      className={`wb-mini ghost${lensMode === "type" ? " on" : ""}`}
                      aria-pressed={lensMode === "type"}
                      title="group by business-type super-sector — are the utilities moving?"
                      onClick={() => setGroupMode("type")}
                    >
                      business type
                    </button>
                    {/* the value-chain lens renders ONLY when the basket is decomposed into links
                        (nothing to group by otherwise) — honest loudness (#7) */}
                    {hasValueChain && (
                      <button
                        type="button"
                        className={`wb-mini ghost${lensMode === "segment" ? " on" : ""}`}
                        aria-pressed={lensMode === "segment"}
                        title="group by value-chain link — which part of the chain is moving?"
                        onClick={() => setGroupMode("segment")}
                      >
                        value chain
                      </button>
                    )}
                  </span>
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
                      <th>Type</th>
                      <th style={{ textAlign: "right" }}>SMA</th>
                      {/* trailing EOD price returns — 1d is the last close vs the PRIOR close (not a
                          24h/intraday move; this platform is end-of-day) */}
                      <th style={{ textAlign: "right" }}>1d</th>
                      <th style={{ textAlign: "right" }}>7d</th>
                      <th style={{ textAlign: "right" }}>30d</th>
                      <th style={{ textAlign: "right" }}>90d</th>
                      {/* 1Y = 252 trading bars (the same bar convention as the shorter windows) */}
                      <th style={{ textAlign: "right" }}>1Y</th>
                      {/* relative volume, two windows off ONE member: RVOL|8 is the as-of bar's volume
                          vs the prior 8-bar average (mirrors the breakout detector — the call-matched
                          read); RVOL|20 is the same idea over 20 bars (the trader "unusually active vs
                          its month?" convention, deliberately call-decoupled). A warm accent marks the
                          volume-backed exception, #7 — each column off its OWN threshold. */}
                      <th style={{ textAlign: "right" }}>RVOL|8</th>
                      <th style={{ textAlign: "right" }}>RVOL|20</th>
                      {/* insider open-market buys: {buys}/{distinct buyers} per trailing window,
                          short before long (matching the return ladder). A ≥2-buyer cluster accents
                          — breadth is the conviction tell; a lone buyer shows un-accented, 0 is "—" */}
                      <th style={{ textAlign: "right" }}>Ins 30d</th>
                      <th style={{ textAlign: "right" }}>Ins 90d</th>
                      <th style={{ textAlign: "right" }}>Mkt cap</th>
                      <th style={{ textAlign: "right" }}>Exit-by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {renderGroups.map((g) => (
                      <Fragment key={g.key}>
                        <tr className={`grp ${g.cls}`}>
                          <td colSpan={16}>
                            {/* the To Review heading idiom (chev · label · hint · count · hairline),
                                bucket-colored; click-to-collapse, open by default — the count stays
                                visible while closed, so a collapsed bucket never reads as dropped */}
                            <button
                              type="button"
                              className="grp-h"
                              aria-expanded={!closedGroups.has(g.key)}
                              onClick={() => toggleGroup(g.key)}
                            >
                              {/* one glyph, rotated closed — the swap read as a flicker */}
                              <span className="chev">▾</span>
                              <span className="lbl">{g.label}</span>
                              {g.hint && <em className="hint">· {g.hint}</em>}
                              <span className="ct">· {g.rows.length}</span>
                            </button>
                          </td>
                        </tr>
                        {/* folded rows stay MOUNTED and visibility-COLLAPSE (never unmount):
                            a collapsed row still feeds the column-width algorithm, so folding
                            the bucket with the widest cells can't re-flow the columns */}
                        {g.rows.map(({ row: r, def }) => (
                          <tr
                            key={r.ordinal}
                            className={`bkt ${def.cls}${closedGroups.has(g.key) ? " folded" : ""}${r.ordinal === selOrdinal ? " sel" : ""}`}
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
                              {/* the CALL-STATE dot rides the row's own def in BOTH lenses — the
                                  type lens re-groups, it never hides the call */}
                              <span className="rowdot" title={def.label} />
                            </td>
                            <td className="tk">
                              {r.member.ticker}
                              {/* #1 thin-history flag — a quiet amber caret ONLY on a starved row (honest
                                  loudness). A data-health mark, never a call input. */}
                              {r.scored?.thin_price_history && (
                                <span
                                  className="thin-mark"
                                  title="thin price history — history-window signals may be starved"
                                >
                                  ⚠
                                </span>
                              )}
                            </td>
                            <td className="co">
                              {r.scored?.name ?? <span className="muted">—</span>}
                            </td>
                            <td>
                              {/* the business-type LEAF (Business-Type M1) — derived from the SIC
                                  maps server-side, joined off the scored read; the super rides the
                                  hover, ◈ marks the royalty overlay (honest loudness — 32 names
                                  live). An ETF keys on instrument_kind (a fund has no SIC); an
                                  un-enriched name reads a quiet "—", never a guess. */}
                              {r.scored?.instrument_kind === "etf" ? (
                                <span className="btype bt-etf">ETF sleeve</span>
                              ) : r.scored?.business_type ? (
                                <span
                                  className={`btype bt-${r.scored.business_supersector ?? "other"}`}
                                  title={`${supersectorLabel(r.scored.business_supersector)}${
                                    r.scored.business_type_override ? " · your tag" : " · from SIC"
                                  }${r.scored.royalty ? " · royalty/streaming" : ""}`}
                                >
                                  {businessTypeLabel(r.scored.business_type)}
                                  {r.scored.royalty && <span className="bt-royalty">◈</span>}
                                </span>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                            <td className="met smac">
                              {/* the tape posture at table grain: the panel headline's glyph + the
                                  distance vs the slow line; the literal statement rides the hover */}
                              <PostureCell
                                sig={
                                  r.member.security_id
                                    ? (smaBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                              />
                            </td>
                            {/* trailing returns (1d/7d/30d/90d/1Y) — five cells from the trailing_returns
                                display member, bridged by security_id; green up / red down, "—" on a
                                thin-history gap. On the per-name row, so it renders in BOTH lenses. */}
                            <ReturnCells
                              sig={
                                r.member.security_id
                                  ? (trailBySid.get(r.member.security_id) ?? null)
                                  : null
                              }
                            />
                            <td className="met rvolc">
                              {/* RVOL|8 — the call-matched 8-bar read: a warm 'hot' accent on a
                                  volume-backed move (>= the wire's loud_mult), "—" on a
                                  volumeless/thin as-of bar. Renders in BOTH lenses (per-name row). */}
                              <RvolCell
                                sig={
                                  r.member.security_id
                                    ? (rvolBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                              />
                            </td>
                            <td className="met rvolc">
                              {/* RVOL|20 — the 20-bar trader-convention read (call-decoupled), off
                                  the SAME member's second metric, accenting from its OWN threshold
                                  (loud_mult_20); a name short of 20 base bars reads an honest "—". */}
                              <RvolCell
                                sig={
                                  r.member.security_id
                                    ? (rvolBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                                metricKey="rvol20"
                                loudKey="loud_mult_20"
                              />
                            </td>
                            {/* insider open-market buys — {buys}/{distinct buyers} off the
                                insider_flow_90d member's 30d / 90d metrics, bridged by security_id.
                                Renders in BOTH lenses (per-name row); a ≥2-buyer cluster accents. */}
                            <td className="met insc">
                              <InsiderCell
                                sig={
                                  r.member.security_id
                                    ? (insiderBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                                countKey="buy_count_30d"
                                buyersKey="distinct_buyers_30d"
                                window="30d"
                              />
                            </td>
                            <td className="met insc">
                              <InsiderCell
                                sig={
                                  r.member.security_id
                                    ? (insiderBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                                countKey="buy_count"
                                buyersKey="distinct_buyers"
                                window="90d"
                              />
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
                              {/* the entry-window (confirmation) clock — the clock that governs how
                                  long an armed-family member STAYS armed (it de-arms on arm_until,
                                  often well before exit_by). Armed / Lapsing / Theme-armed only; a
                                  Watch row also carries arm_until on the wire but must NOT light up
                                  (honest loudness). */}
                              {(def.key === "armed" ||
                                def.key === "lapsing" ||
                                def.key === "theme_armed") &&
                                r.call?.arm_until && (
                                  <EntryWindow asof={asof} armUntil={r.call.arm_until} />
                                )}
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
          {card && <CallCard card={card} thesisId={thesisId} foreignFiler={foreignFiler} />}
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
