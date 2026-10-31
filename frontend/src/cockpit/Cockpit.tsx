import { Fragment, useState, type KeyboardEvent } from "react";
import { flushSync } from "react-dom";

import type { DisplaySignal, MemberDataFlagsOut } from "../api/hooks";
import {
  useCall,
  useDisplaySignals,
  useThesis,
  useThesisDataFlags,
  useWorkbenchScored,
} from "../api/hooks";
import { useCountdownOrigin } from "../clock";
import { CallCard } from "../components/CallCard";
import { DataFlagMark, FlagWrap } from "./DataFlagMark";
import { cellFlag, derivedCellFlag, factorWords, flagsBySid, stoppedFeedReason } from "./dataFlags";
import {
  AdvCell,
  GroupMovingLine,
  InsiderCell,
  LastCell,
  Off52wCell,
  PostureCell,
  ReturnCells,
  RvolCell,
  SparklineCell,
  TapeCell,
  type SparkTick,
} from "./DisplaySignalsSection";
import { groupMoving } from "./groupAggregate";
import { tapeMarks } from "./tapeMarks";
import { CatalystEditor, KillCriteriaEditor } from "./SpineListEditors";
import { MemberMenu } from "../components/MemberMenu";
import {
  ENTRY_WINDOW_BUCKETS,
  dedupeBySecurityId,
  groupBasket,
  groupByBusinessType,
  groupBySegment,
  nameKeyFor,
  resolveNameKey,
  type BucketDef,
  type BucketRow,
} from "./buckets";
import { nextSort, sortRenderRows, type SortColId, type SortState } from "./sortBasket";
import { NamePanel } from "./NamePanel";
import { RotationStrip } from "./RotationStrip";
import { exportKeptNames, exportWatchlist, toExportedName } from "../util/exportNames";
import {
  accentVar,
  businessTypeLabel,
  byWhenDateAsc,
  dayCount,
  daysFrom,
  fmtDate,
  STATE_CLASS,
  STATE_LABEL,
  supersectorLabel,
  tickerLabel,
} from "../util/format";
import { CONTEXT_ROLE_DESCRIPTION, contextRoleTag, formatMarketCap } from "../workbench/format";
import { SILENT_DESCRIPTORS, isSilentLink } from "../workbench/useChainDraft";

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
  /** The call rail's open state, URL-owned via ?rail= (App's CockpitRoute) alongside the as-of
   *  dial and the name selection — so a collapse survives a reload without touching storage. */
  railOpen: boolean;
  onRailChange: (open: boolean) => void;
}

/** The Entry-by cell: the entry-window (confirmation) clock. This is the clock that actually
 *  governs how long a member STAYS armed — it de-arms on `arm_until`, which can be a month before
 *  the exit_by date beside it (the live CRVO/MPLT confusion: "Armed · Dec 8" yet de-armed Jul 19),
 *  which is why the two clocks are separate, separately sortable columns rather than one stacked
 *  cell. Loud (`.closing`) only when the call says the entry window is closing
 *  (`entry_window_closing` — the last few TRADING sessions, decided by the assembler, Q14); muted
 *  otherwise. The FE no longer derives its own "inside a week" threshold: one definition of
 *  "closing", the brain's. Mirrors the NamePanel two-clock idiom, whose labels carry the full
 *  wording. */
function EntryWindow({
  asof,
  armUntil,
  closing,
}: {
  asof: string;
  armUntil: string;
  closing: boolean;
}) {
  // F2 — the entry window is a COUNTDOWN: it counts from the action day on a live view, the as-of on a past one
  const armDays = daysFrom(useCountdownOrigin(asof), armUntil);
  return (
    <span
      className={`entry-window${closing ? " closing" : ""}`}
      title={
        closing
          ? "entry window closing — the last trading sessions in which this entry signal is valid; it lapses after this date"
          : "the entry window — the entry signal is valid through this date, often well before the exit-by"
      }
    >
      {fmtDate(armUntil)}
      {armDays !== null && (armDays < 0 ? " · lapsed" : ` · ${dayCount(armDays)}`)}
      {closing && " · entry window closing"}
    </span>
  );
}

/** A sortable column header. The label rides a real <button> (keyboard-focusable, cycles the sort);
 *  the direction arrow is `aria-hidden` so the column header's accessible NAME stays EXACTLY the
 *  label — the shipped `getByRole("columnheader", { name })` assertions and screen readers key off
 *  that, so it must never change. `aria-sort` on the <th> marks the active column for a11y. */
function SortableTh({
  col,
  label,
  align = "left",
  className,
  sort,
  onSort,
}: {
  col: SortColId;
  label: string;
  align?: "left" | "right";
  /** Extra class on the <th> — the sticky identity column's hook. Never touches the button, so
   *  the header's accessible NAME stays exactly the label. */
  className?: string;
  sort: SortState | null;
  onSort: (col: SortColId) => void;
}) {
  const dir = sort && sort.col === col ? sort.dir : null;
  return (
    <th
      className={className}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
      style={{ textAlign: align }}
    >
      <button
        type="button"
        className={`th-sort${dir ? " active" : ""}`}
        title={`sort by ${label}`}
        onClick={() => onSort(col)}
      >
        {label}
        {dir && (
          <span className="th-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

export function Cockpit({
  thesisId,
  asof,
  onAsofChange,
  onBack,
  selectedName,
  onSelectName,
  railOpen,
  onRailChange,
}: Props) {
  const thesisQ = useThesis(thesisId);
  // F2 — the catalyst calendar below counts down from the action day on a live view (the as-of on a past one)
  const countdownFrom = useCountdownOrigin(asof);
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
  // the recent close PATH (the basket sparkline) — the SAME display-signals query, one more member
  // (`price_path`) on the generic payload, bridged by security_id. A SHAPE beside the return ladder,
  // deliberately NOT in `signalsFor` / the sort machinery: a path is not a number to rank on (#4).
  const pathBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const sig = (m.signals ?? []).find((s) => s.kind === "price_path");
    if (sig) pathBySid.set(m.security_id, sig);
  }
  // the 52-week range (the `52w` column) and the volume regime (`ADV 20d`), then the two members the Tape
  // column reads beside the SMA map above (`vcp`, `relative_strength`) — the SAME display-signals query, more
  // members on the generic payload, bridged by security_id like the cells above
  const rangeBySid = new Map<string, DisplaySignal>();
  const volumeBySid = new Map<string, DisplaySignal>();
  const vcpBySid = new Map<string, DisplaySignal>();
  const rsBySid = new Map<string, DisplaySignal>();
  for (const m of displayQ.data?.members ?? []) {
    const range = (m.signals ?? []).find((s) => s.kind === "range_52w");
    if (range) rangeBySid.set(m.security_id, range);
    const volume = (m.signals ?? []).find((s) => s.kind === "volume_regime");
    if (volume) volumeBySid.set(m.security_id, volume);
    const vcp = (m.signals ?? []).find((s) => s.kind === "vcp");
    if (vcp) vcpBySid.set(m.security_id, vcp);
    const rs = (m.signals ?? []).find((s) => s.kind === "relative_strength");
    if (rs) rsBySid.set(m.security_id, rs);
  }
  // S3 — the shared "is this real?" data flags (compute-on-read, display-only): which cells on a row rest
  // on data the platform cannot vouch for. Bridged by security_id like the display cells; a row with no
  // entry has no flag (the common case). Only the cells a flag names change (in doubt: grayed + the reason
  // on hover; known wrong: "—" + the reason), and the row's quiet mark rides after the ticker.
  const flagsQ = useThesisDataFlags(thesisId, asof);
  // A flag qualifies the NUMBERS on its row, so it is applied only to the reads it was computed beside: this
  // thesis + as-of's display and scored reads, landed (not still loading, not a previous thesis's / date's
  // placeholder). The flag read is the faster one (MEASURED on prod's data: 1.8 s vs the display read's
  // 3.5 s on a 159-name basket), so without this gate the marks arrived first and sat beside rows whose
  // cells were still "—" (loading) or another view's numbers (placeholder) — which read as "the flag blanked
  // the row". Until both land, no row is flagged; the table already dims while recomputing (C2).
  const flagsApply =
    flagsQ.data != null &&
    !flagsQ.isPlaceholderData &&
    (displayQ.data != null || displayQ.isError) &&
    !displayQ.isPlaceholderData &&
    !scoredQ.isPlaceholderData;
  const flagBySid = flagsBySid(flagsApply ? flagsQ.data : undefined);
  const flagsFor = (sid: string | null | undefined): MemberDataFlagsOut | null =>
    sid ? (flagBySid.get(sid) ?? null) : null;
  // the company's splits inside the sparkline's window: a tick at most (plan section 11)
  const splitTicks = (sid: string | null | undefined): SparkTick[] =>
    (flagsFor(sid)?.splits ?? [])
      .filter((s) => s.bars_ago != null && s.status !== "needs_review")
      .map((s) => ({
        barsAgo: s.bars_ago as number,
        label: `${s.label} ${s.effective_date} (prices before it ${factorWords(s.factor)})`,
      }));
  // the newest last-bar date across the basket: a row whose last close is OLDER than it is lagging (a halt, a
  // delisting, a thin OTC name with no print) and its Last close cell says when. Relative to the table, so a
  // weekend or a holiday — when every row's newest bar is the same Friday — marks nothing.
  const newestBar = [...smaBySid.values()].reduce(
    (max, s) => ((s.basis.window_end ?? "") > max ? (s.basis.window_end ?? "") : max),
    "",
  );
  const thesis = thesisQ.data;
  const card = callQ.data;
  // C2: any of the as-of-keyed reads may still be showing the PREVIOUS date's values
  const tableRecomputing =
    callQ.isPlaceholderData ||
    displayQ.isPlaceholderData ||
    scoredQ.isPlaceholderData ||
    flagsQ.isPlaceholderData;

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
  // The active column SORT — a local, reversible view choice (the lens-toggle precedent); null = the
  // default order (call-rank within each group). It re-orders rows WITHIN each group only (the call
  // hierarchy stays put) and PERSISTS across a lens switch (re-ranks within the new lens's groups).
  // Click cycles desc → asc → off.
  const [sort, setSort] = useState<SortState | null>(null);
  const onSort = (col: SortColId) => setSort((cur) => nextSort(cur, col));
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

  // Apply the active sort WITHIN each group — ONE seam covers all three lenses (they share this
  // render shape). Off (sort null) leaves the default order untouched. `signalsFor` bridges a row to
  // its four display signals by security_id, exactly like the cells; the sort re-orders and drops
  // nothing (`[...rows]` in sortRenderRows — a re-order, never a filter, #9/#2). The dot is not sortable.
  const signalsFor = (r: BucketRow) => {
    const sid = r.member.security_id;
    return {
      sma: sid ? (smaBySid.get(sid) ?? null) : null,
      trail: sid ? (trailBySid.get(sid) ?? null) : null,
      rvol: sid ? (rvolBySid.get(sid) ?? null) : null,
      insider: sid ? (insiderBySid.get(sid) ?? null) : null,
      range: sid ? (rangeBySid.get(sid) ?? null) : null,
      volume: sid ? (volumeBySid.get(sid) ?? null) : null,
    };
  };
  const sortedGroups = sort
    ? renderGroups.map((g) => ({ ...g, rows: sortRenderRows(g.rows, sort, signalsFor) }))
    : renderGroups;
  // The group header's moving line reads ONLY the trail signal (the 7d column's own member), over
  // the SAME sid bridge as the cells and the sort — so the header's "priced" set is exactly the
  // rows whose 7d cell shows a number. Computed per group at render, in every lens.
  const trailFor = (r: BucketRow) => signalsFor(r).trail;
  // S3: a row whose 7d cell a data flag reaches is left out of the group's median (and the header says so)
  const sevenDayFlagged = (r: BucketRow) =>
    cellFlag(flagsFor(r.member.security_id), "ret_7d") != null;

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

  // Keyboard stepping: ↑/↓ on a focused row moves to the previous / next VISIBLE row — the rendered order,
  // so the active lens and sort count, and a folded group is skipped (it stays mounted, collapsed). Focus
  // always moves; with the per-name panel OPEN the panel follows it, so a basket is scanned a row at a time
  // without the mouse. The panel only follows onto the row its key RESOLVES to: a name shown under several
  // value-chain links resolves to its first row (the click rule), so a later link-row takes focus and leaves
  // the panel where it is rather than jumping the highlight to a row far above.
  const navRows = sortedGroups
    .filter((g) => !closedGroups.has(g.key))
    .flatMap((g) => g.rows.map(({ row }) => row));
  const stepRow = (e: KeyboardEvent<HTMLTableRowElement>, from: BucketRow, dir: 1 | -1) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.target !== e.currentTarget) return;
    e.preventDefault(); // the row owns the arrow: the scroller must not also move
    const to = navRows[navRows.findIndex((r) => r.ordinal === from.ordinal) + dir];
    if (!to) return; // the first / last visible row: nothing further that way
    const el = e.currentTarget
      .closest("table")
      ?.querySelector<HTMLElement>(`tr.bkt[data-ordinal="${to.ordinal}"]`);
    el?.focus({ preventScroll: true });
    el?.scrollIntoView?.({ block: "nearest" });
    if (selOrdinal !== null) {
      const key = nameKeyFor(to, basket);
      if (resolveNameKey(groups, key)?.row.ordinal === to.ordinal) onSelectName(key);
    }
  };

  const evidence = thesis?.evidence ?? [];
  const catalysts = thesis?.catalysts ?? [];
  const killCriteria = thesis?.kill_criteria ?? [];

  return (
    <div className="cp-shell">
      <header className="cp-top">
        {onBack && (
          <button type="button" className="back" onClick={onBack}>
            ← Back
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
        {/* Collapse the call rail to hand its fixed 380px back to the basket table — a third of a
            laptop viewport, and the table's 17 columns want all of it (MEASURED at 1091px: 610px
            of horizontal scroll -> 230px). A view dial, not a navigation: it rides ?rail=0,
            reverses in one click, and never unmounts the call from the query (the state badge
            above and the per-name panel read the same card). */}
        <button
          type="button"
          className="rail-toggle"
          aria-expanded={railOpen}
          aria-controls="cp-rail"
          title={
            railOpen
              ? "Collapse the call rail — gives its width back to the basket table"
              : "Show the call rail"
          }
          onClick={() => onRailChange(!railOpen)}
        >
          {/* the chevron is aria-hidden (the .th-sort idiom): the button's accessible NAME stays
              exactly "Call" through both states — aria-expanded carries the state, not the label */}
          <span className="chev" aria-hidden="true">
            {railOpen ? "›" : "‹"}
          </span>
          Call
        </button>
      </header>

      <div className={`cp-body${railOpen ? "" : " rail-closed"}`}>
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

              {/* the THESIS-LEVEL rotation strip — theme-breadth thrust + sector-RS leadership off the
                  SAME display-signals query the basket cells read (its TOP-LEVEL `breadth` / `sector_rs`
                  readings, not the per-member list). Read-only tape context, above the basket it
                  summarizes; renders nothing when both are absent (#4/#7). */}
              <RotationStrip
                breadth={displayQ.data?.breadth ?? null}
                sectorRs={displayQ.data?.sector_rs ?? null}
              />

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
                {/* every value in this table (the call-state dots, the scored cells, the display
                    columns) is as-of-keyed and keeps its PREVIOUS values through a scrub — dimmed
                    while the new ones compute, never presented as the new date's numbers (C2) */}
                {/* The 17 columns do not fit the main column on a laptop (MEASURED: 1025px of
                    min-content into 649px of room at a 1091px viewport). Before this they simply
                    painted OVER the rail — the page never scrolled, so those columns were
                    unreachable. The table now scrolls inside its own box; see .basket-scroll for
                    why it also carries min-width: max-content. */}
                <div className="basket-scroll">
                  <table className={`basket${tableRecomputing ? " recomputing" : ""}`}>
                    <thead>
                      {/* Click a header to rank the basket by that column — WITHIN each group (the
                          call hierarchy stays; the biggest mover inside Quiet still surfaces). A "—"
                          cell is ABSENT and sorts LAST in both directions (#9/#2); the sort re-orders,
                          never drops. The status-dot column is not sortable. */}
                      <tr>
                        <th className="dotc" aria-label="status" />
                        <SortableTh col="ticker" label="Ticker" className="tk" sort={sort} onSort={onSort} />
                        <SortableTh col="name" label="Name" sort={sort} onSort={onSort} />
                        <SortableTh col="type" label="Type" sort={sort} onSort={onSort} />
                        {/* the newest END-OF-DAY close at or before the as-of (the sma_position member's
                            `close`) — "last known": it stays when the tape has stopped, with its date */}
                        <SortableTh col="last" label="Last close" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="sma" label="SMA" align="right" sort={sort} onSort={onSort} />
                        {/* trailing EOD price returns — 1d is the last close vs the PRIOR close (not a
                            24h/intraday move; this platform is end-of-day) */}
                        <SortableTh col="ret_1d" label="1d" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="ret_7d" label="7d" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="ret_30d" label="30d" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="ret_90d" label="90d" align="right" sort={sort} onSort={onSort} />
                        {/* 1Y = 252 trading bars (the same bar convention as the shorter windows) */}
                        <SortableTh col="ret_1y" label="1Y" align="right" sort={sort} onSort={onSort} />
                        {/* the close-PATH sparkline — the shape behind the ladder's endpoint numbers
                            (the price_path member's fixed-slot series). A plain, NON-sortable header:
                            a shape is not a number to rank on, so it never joins the sort machinery. */}
                        <th
                          className="sparkc"
                          style={{ textAlign: "right" }}
                          title="the recent close path, one slot per trading bar (newest at the right) — hover a cell for its exact bars; a shape, not a number, so it is not sortable"
                        >
                          Path
                        </th>
                        {/* how far the last close sits below the 52-week closing high (0.0% = at the high) —
                            the range_52w member's pct_off_52w_high; the high and its bar date ride the hover */}
                        <SortableTh col="off52w" label="Off 52w high" align="right" sort={sort} onSort={onSort} />
                        {/* the EXCEPTIONAL tape readings only — a coiling base, RS at a 13-week high, a recent
                            golden / death cross (the vcp / relative_strength / sma_position members). A muted
                            "—" on most rows; a plain, NON-sortable header like Path: a set of marks is not a
                            number to rank on. */}
                        <th
                          className="tapec"
                          style={{ textAlign: "right" }}
                          title="exceptional tape readings only — a coiling base, relative strength at a 13-week high, a golden or death cross in the last 30 days; hover a mark for the member's own statement. A set of marks, not a number, so it is not sortable"
                        >
                          Tape
                        </th>
                        {/* relative volume, two windows off ONE member: RVOL 8D is the as-of bar's volume
                            vs the prior 8-bar average (mirrors the breakout detector — the call-matched
                            read); RVOL 20D is the same idea over 20 bars (the trader "unusually active vs
                            its month?" convention, deliberately call-decoupled). A warm accent marks the
                            volume-backed exception, #7 — each column off its OWN threshold. */}
                        <SortableTh col="rvol8" label="RVOL 8D" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="rvol20" label="RVOL 20D" align="right" sort={sort} onSort={onSort} />
                        {/* insider open-market buys: {buys}/{distinct buyers} per trailing window,
                            short before long (matching the return ladder). A ≥2-buyer cluster accents
                            — breadth is the conviction tell; a lone buyer shows un-accented, 0 is "—".
                            Sort is buyers-primary, buys-secondary (breadth-first, matching the accent). */}
                        <SortableTh col="ins_30d" label="Ins 30d" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="ins_90d" label="Ins 90d" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="mktcap" label="Mkt cap" align="right" sort={sort} onSort={onSort} />
                        {/* the liquidity read beside the size: 20-bar average DOLLAR volume (close × volume),
                            off the volume_regime member — no threshold, "thin" is the operator's call */}
                        <SortableTh col="adv" label="ADV 20d" align="right" sort={sort} onSort={onSort} />
                        {/* the two clocks, each its own sortable column (they were one stacked
                            cell): Entry-by is the CONFIRMATION clock — when the member de-arms —
                            and it closes weeks before Exit-by, the conviction/hold horizon. */}
                        <SortableTh col="entry_by" label="Entry-by" align="right" sort={sort} onSort={onSort} />
                        <SortableTh col="exit_by" label="Exit-by" align="right" sort={sort} onSort={onSort} />
                      </tr>
                    </thead>
                    <tbody>
                      {sortedGroups.map((g) => (
                        <Fragment key={g.key}>
                          <tr className={`grp ${g.cls}`}>
                            <td colSpan={22}>
                              {/* the To Review heading idiom (chev · label · hint · count · moving
                                  line · hairline), bucket-colored; click-to-collapse, open by default
                                  — the count + the moving line stay visible while closed, so a
                                  collapsed bucket never reads as dropped */}
                              <button
                                type="button"
                                className="grp-h"
                                aria-expanded={!closedGroups.has(g.key)}
                                onClick={() => toggleGroup(g.key)}
                              >
                                {/* the heading TEXT is one box so it can stick to the left of the scroller: the row
                                    spans the full table, so without this "Armed · act now · 7 names" slides out of
                                    view the moment you scroll the columns. The trailing hairline stays outside it,
                                    still flexing to the right edge. */}
                                <span className="grp-lbl">
                                  {/* one glyph, rotated closed — the swap read as a flicker */}
                                  <span className="chev">▾</span>
                                  <span className="lbl">{g.label}</span>
                                  {g.hint && <em className="hint">· {g.hint}</em>}
                                  {/* the FULL group size — every row counts, priced or not (#9) */}
                                  <span className="ct">
                                    · {g.rows.length} {g.rows.length === 1 ? "name" : "names"}
                                  </span>
                                  {/* "is this group moving?" — the MEDIAN 7d return over the group's
                                      priced rows, off the SAME ret_7d the 7d cells show (client-side,
                                      zero wire); "—" below three priced. Muted, always-present context
                                      — never a badge, never a call input (#4/#7). Per group, per lens. */}
                                  <GroupMovingLine
                                    stat={groupMoving(g.rows, trailFor, sevenDayFlagged)}
                                  />
                                </span>
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
                              data-ordinal={r.ordinal}
                              onClick={() => toggleRow(r)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  toggleRow(r);
                                } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                                  stepRow(e, r, e.key === "ArrowDown" ? 1 : -1);
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
                                {/* the business-type lens regroups by what a company DOES, which hides the silent
                                    link a name sits in — so a Context / Bench name carries a small lowercase tag
                                    there (the other lenses name the link in the group header). Muted, no badge. */}
                                {lensMode === "type" && isSilentLink(r.member.segment) && (
                                  <span
                                    className="silent-tag"
                                    title={SILENT_DESCRIPTORS[r.member.segment]}
                                  >
                                    {r.member.segment.toLowerCase()}
                                  </span>
                                )}
                                {/* the operator's CONTEXT ROLE (what part this company plays on this card) — a
                                    quiet lowercase tag on ANY row whose member has one, in every lens, and
                                    ONLY then (never a "—" placeholder, WB#3). Operator-authored display
                                    metadata: never a call input (#4); the hover is the role's description. */}
                                {r.member.context_role && (
                                  <span
                                    className="role-tag"
                                    title={CONTEXT_ROLE_DESCRIPTION[r.member.context_role]}
                                  >
                                    {contextRoleTag(r.member.context_role)}
                                  </span>
                                )}
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
                                {/* S3 — the data-flag mark: only when a flag reaches a cell on this row
                                    (WB#3); the hover names each flag and the cells it touches */}
                                <DataFlagMark member={flagsFor(r.member.security_id)} />
                              </td>
                              <td className="co">
                                {/* the hungriest column in the table — the longest company name in a
                                    196-row basket wanted 293px. Capped with an ellipsis; the full
                                    name rides the hover and the per-name panel (it hides, it never
                                    vanishes). */}
                                {r.scored?.name ? (
                                  <span className="co-name" title={r.scored.name}>
                                    {r.scored.name}
                                  </span>
                                ) : (
                                  <span className="muted">—</span>
                                )}
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
                              <td className="met lastc">
                                {/* the last close, with its bar date when it lags the basket's newest bar. A
                                    stopped feed is NOT blanked here (see DERIVED_CELL_SKIPS): the last print is
                                    the point — it shows with its date and the flag's reason on hover. */}
                                <FlagWrap flag={derivedCellFlag(flagsFor(r.member.security_id), "last")}>
                                  <LastCell
                                    sig={
                                      r.member.security_id
                                        ? (smaBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                    newestBar={newestBar || null}
                                    stopped={stoppedFeedReason(flagsFor(r.member.security_id))}
                                  />
                                </FlagWrap>
                              </td>
                              <td className="met smac">
                                {/* the tape posture at table grain: the panel headline's glyph + the
                                    distance vs the slow line; the literal statement rides the hover */}
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "sma")}>
                                  <PostureCell
                                    sig={
                                      r.member.security_id
                                        ? (smaBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                  />
                                </FlagWrap>
                              </td>
                              {/* trailing returns (1d/7d/30d/90d/1Y) — five cells from the trailing_returns
                                  display member, bridged by security_id; green up / red down, "—" on a
                                  thin-history gap. On the per-name row, so it renders in BOTH lenses.
                                  S3: a window a data flag reaches grays (in doubt) or blanks (known wrong). */}
                              <ReturnCells
                                sig={
                                  r.member.security_id
                                    ? (trailBySid.get(r.member.security_id) ?? null)
                                    : null
                                }
                                flagFor={(key) => cellFlag(flagsFor(r.member.security_id), key)}
                              />
                              <td className="met sparkc">
                                {/* the close-path sparkline off the price_path member's fixed-slot
                                    series: a neutral hairline that BREAKS on a gap (a young name draws
                                    a shorter, right-aligned path), "—" below two closes. On the
                                    per-name row, so it renders in BOTH lenses. S3: a split inside the
                                    window draws a tick at most. */}
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "path")}>
                                  <SparklineCell
                                    sig={
                                      r.member.security_id
                                        ? (pathBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                    ticks={splitTicks(r.member.security_id)}
                                  />
                                </FlagWrap>
                              </td>
                              <td className="met rngc">
                                {/* % off the 52-week closing high, off the range_52w member. The server's
                                    flag vocabulary does not name this cell, so it takes its reach from the
                                    named cells whose bar window contains its own (derivedCellFlag). */}
                                <FlagWrap flag={derivedCellFlag(flagsFor(r.member.security_id), "off52w")}>
                                  <Off52wCell
                                    sig={
                                      r.member.security_id
                                        ? (rangeBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                  />
                                </FlagWrap>
                              </td>
                              <td className="met tapec">
                                {/* the exception-only tape marks, off the sid-bridged sma / vcp / relative_strength
                                    members; each chip is flagged on the named cells that read its bars */}
                                <TapeCell
                                  marks={tapeMarks(
                                    {
                                      sma: r.member.security_id
                                        ? (smaBySid.get(r.member.security_id) ?? null)
                                        : null,
                                      vcp: r.member.security_id
                                        ? (vcpBySid.get(r.member.security_id) ?? null)
                                        : null,
                                      rs: r.member.security_id
                                        ? (rsBySid.get(r.member.security_id) ?? null)
                                        : null,
                                    },
                                    asof,
                                  )}
                                  flagFor={(cell) => derivedCellFlag(flagsFor(r.member.security_id), cell)}
                                />
                              </td>
                              <td className="met rvolc">
                                {/* RVOL 8D — the call-matched 8-bar read: a warm 'hot' accent on a
                                    volume-backed move (>= the wire's loud_mult), "—" on a
                                    volumeless/thin as-of bar. Renders in BOTH lenses (per-name row). */}
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "rvol8")}>
                                  <RvolCell
                                    sig={
                                      r.member.security_id
                                        ? (rvolBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                  />
                                </FlagWrap>
                              </td>
                              <td className="met rvolc">
                                {/* RVOL 20D — the 20-bar trader-convention read (call-decoupled), off
                                    the SAME member's second metric, accenting from its OWN threshold
                                    (loud_mult_20); a name short of 20 base bars reads an honest "—". */}
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "rvol20")}>
                                  <RvolCell
                                    sig={
                                      r.member.security_id
                                        ? (rvolBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                    metricKey="rvol20"
                                    loudKey="loud_mult_20"
                                  />
                                </FlagWrap>
                              </td>
                              {/* insider open-market buys — {buys}/{distinct buyers} off the
                                  insider_flow_90d member's 30d / 90d metrics, bridged by security_id.
                                  Renders in BOTH lenses (per-name row); a ≥2-buyer cluster accents. */}
                              <td className="met insc">
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "ins_30d")}>
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
                                </FlagWrap>
                              </td>
                              <td className="met insc">
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "ins_90d")}>
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
                                </FlagWrap>
                              </td>
                              <td className="met">
                                {/* computed market cap (the scoring engine, re-derived on read),
                                    bridged by security_id — "—" when un-scored / no price+shares facts.
                                    S3: a cap on a pre-split or unreadable share count is flagged. */}
                                <FlagWrap flag={cellFlag(flagsFor(r.member.security_id), "mktcap")}>
                                  {formatMarketCap(r.scored?.market_cap.value)}
                                </FlagWrap>
                              </td>
                              <td className="met advc">
                                {/* 20-bar average dollar volume, off the volume_regime member; flagged like
                                    the 52w cell, through the price and volume cells that read its bars */}
                                <FlagWrap flag={derivedCellFlag(flagsFor(r.member.security_id), "adv")}>
                                  <AdvCell
                                    sig={
                                      r.member.security_id
                                        ? (volumeBySid.get(r.member.security_id) ?? null)
                                        : null
                                    }
                                  />
                                </FlagWrap>
                              </td>
                              {/* Armed / Lapsing / Theme-armed only (ENTRY_WINDOW_BUCKETS, shared
                                  with the sort key): a Watch row carries arm_until on the wire too
                                  but must NOT light up — its clock is a confirmation decay, not an
                                  entry the system is endorsing (honest loudness). */}
                              <td className="met entryby">
                                {ENTRY_WINDOW_BUCKETS.has(def.key) && r.call?.arm_until ? (
                                  <EntryWindow
                                    asof={asof}
                                    armUntil={r.call.arm_until}
                                    closing={r.call.entry_window_closing}
                                  />
                                ) : (
                                  "—"
                                )}
                              </td>
                              {/* `lapsing` = the HOLD clock (exit_by) closing — never the entry
                                  window (that is the Entry-by cell's own flag, Q14). */}
                              <td
                                className={`met exitby${r.call?.lapsing ? " lapse" : ""}`}
                                title={
                                  r.call?.lapsing
                                    ? "hold clock closing — the conviction's signal-validity horizon (exit-by) is near; not a sell date"
                                    : undefined
                                }
                              >
                                {r.call?.exit_by
                                  ? `${fmtDate(r.call.exit_by)}${r.call.lapsing ? " · hold clock closing" : ""}`
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
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
                {/* a calendar reads in date order: soonest first, a fuzzy "~Q3" last — a DISPLAY sort of a
                    copy; the editor below still gets the authoring order, which is what a save stores. A
                    date already past reads "Nd ago" and grays: it stays on the calendar (keep it visible). */}
                {[...catalysts].sort(byWhenDateAsc).map((c) => {
                  const d = daysFrom(countdownFrom, c.when_date);
                  const soon = d !== null && d >= 0 && d <= 21;
                  const passed = d !== null && d < 0;
                  const when = c.when_date
                    ? `${fmtDate(c.when_date)}${d === null ? "" : d >= 0 ? ` · ${dayCount(d)}` : ` · ${-d}d ago`}`
                    : (c.when_label ?? "—");
                  return (
                    <div className={`cat${soon ? " soon" : ""}${passed ? " passed" : ""}`} key={c.id}>
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
        {/* C2 honest rendering: on an as-of scrub the rail keeps the PREVIOUS date's card up
            (keepPreviousData) instead of blanking — so it is dimmed and says so, and the card's own
            "as-of" line stays the authority on which date it answers. */}
        <aside
          id="cp-rail"
          hidden={!railOpen}
          className={`cp-rail${selected ? " dimmed" : ""}${callQ.isPlaceholderData ? " recomputing" : ""}`}
        >
          {callQ.isPlaceholderData && (
            <p className="muted stale-tag">Recomputing the call for {fmtDate(asof)}…</p>
          )}
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
          dataFlags={flagsFor(selected.row.member.security_id)}
          asof={asof}
          onClose={() => onSelectName(null)}
        />
      )}
    </div>
  );
}
