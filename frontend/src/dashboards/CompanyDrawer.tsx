import { Fragment, lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type {
  DisplaySignal,
  PriceBar,
  TriggerRefOut,
  UserCompanyValuesOut,
} from "../api/hooks";
import { useEpisodePriceWindow, useMemberPanel } from "../api/hooks";
import { useAppClock, useCountdownOrigin } from "../clock";
import { groupBasket, type BucketKey, type BucketRow } from "../cockpit/buckets";
import { DataFlagsSection, FlagWrap } from "../cockpit/DataFlagMark";
import { cellFlag, stoppedFeedReason } from "../cockpit/dataFlags";
import {
  AdvCell,
  DisplayHeadlineRow,
  DisplaySignalsSection,
  fmtMetricValue,
  LastCell,
  Off52wCell,
  RETURN_WINDOW_KEYS,
  RvolCell,
} from "../cockpit/DisplaySignalsSection";
import { TriggerRow } from "../components/CallCard";
import { Drawer } from "../components/Drawer";
import { ExternalLink, isSecLink } from "../components/ExternalLink";
import { ProvenanceSources } from "../components/ProvenanceSources";
import { lowerFirst, reasonClause } from "../components/valueWords";
import { ASOF, validAsof } from "../nav";
import { EventLedger } from "../scoreboard/EventLedger";
import {
  buildMemberOverlayEvents,
  type ChartSpan,
  flagBands,
  tapeSignals,
} from "../scoreboard/overlay";
import {
  businessTypeLabel,
  byEventDateDesc,
  byWhenDateAsc,
  dayCount,
  daysFrom,
  fmtDate,
  gradeClass,
  isPastAsof,
  SETUP_STRENGTH_TOOLTIP,
  supersectorLabel,
  verdictLabel,
} from "../util/format";
import {
  formatMarketCap,
  meterValueLabel,
  originWithFiler,
  runwayCashWords,
  runwayNotApplicable,
  runwayPaceWords,
} from "../workbench/format";
import { Meter } from "../workbench/Meter";
import "./company-drawer.css";

const NO_BARS: PriceBar[] = [];
const CHART_SPANS: readonly ChartSpan[] = ["3M", "6M", "1Y", "ALL"];
const RETURN_LABELS: Record<(typeof RETURN_WINDOW_KEYS)[number], string> = {
  ret_1d: "1D",
  ret_7d: "7D",
  ret_30d: "30D",
  ret_90d: "90D",
  ret_1y: "1Y",
};

// lightweight-charts (~160kB) stays drawer-only + on-demand in its own async chunk.
const PriceSparkline = lazy(() =>
  import("../scoreboard/PriceSparkline").then((m) => ({ default: m.PriceSparkline })),
);

const STATE_LINE: Partial<Record<BucketKey, string>> = {
  warming: "Conviction fired — awaiting market confirmation.",
  watch: "Moving, no conviction yet — confirmation only.",
  quiet: "No live signals at this as-of.",
  context: "Watched for context — never counts toward the call.",
  bench: "On the bench — speculative, never counts toward the call.",
  etfs: "ETF sleeve — never counts toward the call.",
};

const AUTHOR_TAG: Record<string, string> = {
  system_drafted: "model draft",
  operator_set: "model draft",
  operator_edited: "your words",
};

export interface CompanyDrawerSelection {
  thesisId: string;
  nameKey: string;
}

interface Props {
  selection: CompanyDrawerSelection;
  asof: string;
  onClose: () => void;
  onSwitchCard: (thesisId: string, nameKey: string) => void;
  onOpenCockpit: (thesisId: string, nameKey?: string | null) => void;
}

/** Redesigned dashboard company overlay: combines the Cockpit's per-name decision context (call,
 *  clocks, triggers, risk signals, thesis fit, scoring meters, identity, data flags, decisions)
 *  with the Scoreboard's interactive price/events chart and cross-highlighted event ledger.
 *  Adapts seamlessly from a tight 600px slide-over to a full-width two-column expanded dossier. */
export function CompanyDrawer({
  selection,
  asof,
  onClose,
  onSwitchCard,
  onOpenCockpit,
}: Props) {
  const q = useMemberPanel(selection.thesisId, selection.nameKey, asof);
  const data = q.data ?? null;
  const origin = useCountdownOrigin(asof);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const asofParam = validAsof(searchParams.get(ASOF));

  const [span, setSpan] = useState<ChartSpan>("6M");
  const [activeN, setActiveN] = useState<number | null>(null);

  const resolved = useMemo(() => {
    if (!data) return null;
    const groups = groupBasket([data.member], data.card, data.scored ? [data.scored] : undefined);
    const firstGroup = groups[0];
    const firstRow = firstGroup?.rows[0];
    if (!firstGroup || !firstRow) return null;
    return { row: firstRow, def: firstGroup.def };
  }, [data]);

  const member = resolved?.row.member ?? null;
  const call = resolved?.row.call ?? null;
  const scored = resolved?.row.scored ?? null;
  const def = resolved?.def ?? null;
  const dataFlags = data?.data_flags ?? null;
  const display = data?.display ?? null;
  const signals = display?.signals ?? [];

  const sigByKind = useMemo(() => {
    const map = new Map<string, DisplaySignal>();
    for (const s of signals) map.set(s.kind, s);
    return map;
  }, [signals]);

  const ownTriggers: TriggerRefOut[] = useMemo(() => {
    if (!member) return [];
    const list =
      call && call.triggers.length > 0
        ? call.triggers
        : (data?.card.triggers_fired ?? []).filter(
            (t) => t.ticker != null && t.ticker === member.ticker,
          );
    return [...list].sort(byEventDateDesc);
  }, [call, data, member]);

  const risks: TriggerRefOut[] = useMemo(() => {
    if (!member) return [];
    return (data?.card.risk_signals ?? [])
      .filter((t) => t.ticker != null && t.ticker === member.ticker)
      .sort(byEventDateDesc);
  }, [data, member]);

  // Price path + recorded SEC event families (buys, sells, 8-Ks, 13D/G) on demand via the shared
  // as-of-capped window read (edition: 0 = live record clock on today, public clock on past asof).
  const sid = member?.security_id ?? null;
  const windowQ = useEpisodePriceWindow(
    {
      thesisId: data?.thesis_id ?? "",
      securityId: sid ?? "",
      start: asof,
      end: asof,
      asof,
      edition: 0,
    },
    Boolean(data && sid),
  );
  const bars = windowQ.data?.bars ?? NO_BARS;

  const bands = useMemo(
    () => flagBands(dataFlags?.flags ?? [], windowQ.data?.bars ?? NO_BARS),
    [dataFlags, windowQ.data],
  );

  const events = useMemo(
    () =>
      buildMemberOverlayEvents({
        bars: windowQ.data?.bars ?? NO_BARS,
        triggers: ownTriggers,
        risks,
        decisions: data?.decisions ?? [],
        insiderBuys: windowQ.data?.insider_buys ?? [],
        sells: windowQ.data?.insider_sells ?? [],
        filings: windowQ.data?.corporate_events ?? [],
        stakes: windowQ.data?.activist_stakes ?? [],
        splits: dataFlags?.splits ?? [],
        tape: { signals: tapeSignals(display), asof },
      }),
    [windowQ.data, ownTriggers, risks, data?.decisions, dataFlags, display, asof],
  );

  const smaSig = sigByKind.get("sma_position") ?? null;
  const trailSig = sigByKind.get("trailing_returns") ?? null;
  const rangeSig = sigByKind.get("range_52w") ?? null;
  const rvolSig = sigByKind.get("rvol") ?? null;
  const volSig = sigByKind.get("volume_regime") ?? null;
  const headlines = signals.filter((s) => s.headline);

  const stopped = stoppedFeedReason(dataFlags);
  const subParts: ReactNode[] = [];
  if (scored?.name) subParts.push(scored.name);
  else if (member && !member.security_id) subParts.push("unresolved — no security-master link");
  if (scored?.exchange) subParts.push(scored.exchange);
  const originText = originWithFiler(scored?.origin, scored?.foreign_filer_form);
  if (originText) subParts.push(originText);
  if (scored?.market_cap.value != null) {
    subParts.push(
      <FlagWrap key="hd-mcap" flag={cellFlag(dataFlags, "mktcap")}>
        {formatMarketCap(scored.market_cap.value)}
      </FlagWrap>,
    );
  }

  const title = (
    <div className={`cd-head-stack ${def?.cls ?? ""}`}>
      <div className="cd-title-row">
        <span className="np-tk">{member?.ticker ?? selection.nameKey}</span>
        {def && (
          <span className="np-bucket">
            <span className="rowdot" />
            {def.label}
          </span>
        )}
        {smaSig && (
          <span className="cd-head-price">
            <FlagWrap flag={cellFlag(dataFlags, "last")}>
              <LastCell sig={smaSig} stopped={stopped} />
            </FlagWrap>
          </span>
        )}
      </div>
      {subParts.length > 0 && (
        <div className="cd-head-sub">
          {subParts.map((part, i) => (
            <Fragment key={i}>
              {i > 0 && <span className="sep">·</span>}
              <span>{part}</span>
            </Fragment>
          ))}
        </div>
      )}
    </div>
  );

  const lookupTicker = member?.ticker ?? null;
  const openTickerLookup = () => {
    if (!lookupTicker) return;
    const sp = new URLSearchParams();
    sp.set("t", lookupTicker);
    if (asofParam) sp.set(ASOF, asofParam);
    onClose();
    navigate(`/dashboards/ticker-lookup?${sp.toString()}`);
  };

  const actions = (
    <>
      {lookupTicker && (
        <button
          type="button"
          className="cd-cockpit cd-action-quiet"
          title={`Open ${lookupTicker} in Ticker Lookup (full filings and cross-card dossier)`}
          onClick={openTickerLookup}
        >
          Ticker Lookup ↗
        </button>
      )}
      <button
        type="button"
        className="cd-cockpit"
        title="Open this company in the Cockpit"
        onClick={() => onOpenCockpit(selection.thesisId, member?.ticker ?? selection.nameKey)}
      >
        Open in Cockpit ↗
      </button>
    </>
  );

  const switching = Boolean(data && q.isPlaceholderData);

  // Derived call details
  const armDays = daysFrom(origin, call?.arm_until);
  const exitDays = daysFrom(origin, call?.exit_by);
  const conf = call?.confidence == null ? null : Math.round(call.confidence * 100);
  const grades: [string, string][] = [];
  if (call?.conviction_grade) grades.push(["conviction", call.conviction_grade]);
  if (call?.confirmation_grade) grades.push(["confirmation", call.confirmation_grade]);
  if (call?.entry_grade) grades.push(["entry", call.entry_grade]);

  const held = Boolean(
    data?.position?.security_id && member?.security_id && data.position.security_id === member.security_id,
  );

  const weight =
    member?.conviction == null
      ? "—"
      : member.conviction >= 1 && member.conviction <= 5
        ? `${"●".repeat(member.conviction)}${"○".repeat(5 - member.conviction)} ${member.conviction}`
        : String(member.conviction);

  const btype =
    scored?.instrument_kind === "etf"
      ? "ETF sleeve"
      : scored?.business_type
        ? `${businessTypeLabel(scored.business_type)} · ${supersectorLabel(scored.business_supersector)}` +
          (scored.royalty ? " · royalty/streaming" : "") +
          (scored.business_type_override ? " · your tag" : "")
        : "—";

  const cik = data?.cik ?? null;
  const cikCell: ReactNode =
    cik && data?.edgar_url ? (
      <ExternalLink
        href={data.edgar_url}
        title={isSecLink(data.edgar_url) ? "Open the company on EDGAR" : "Open the stored source"}
      >
        {cik} ↗
      </ExternalLink>
    ) : (
      (cik ?? "—")
    );

  const identityCells: [string, ReactNode][] = member
    ? [
        ["Type", btype],
        ["Segment", member.segment ?? "—"],
        ["Sector", scored?.sector ?? "—"],
        ["Exchange", scored?.exchange ?? "—"],
        ["CIK", cikCell],
        ["Category", scored?.category ?? "—"],
        ["Origin", originText ?? "—"],
        [
          "Mkt cap",
          <FlagWrap key="mktcap" flag={cellFlag(dataFlags, "mktcap")}>
            {formatMarketCap(scored?.market_cap.value)}
          </FlagWrap>,
        ],
        ["Size weight (yours)", weight],
        ["Role", member.role || "—"],
      ]
    : [];
  if (scored?.price_symbol) identityCells.push(["Priced under", scored.price_symbol]);

  const catalystFacts = data?.catalyst_facts ?? [];
  const cardCatalysts = useMemo(
    () => [...(data?.card_catalysts ?? [])].sort(byWhenDateAsc),
    [data?.card_catalysts],
  );
  const hasCatalysts = catalystFacts.length > 0 || cardCatalysts.length > 0;

  const trailByKey = new Map((trailSig?.metrics ?? []).map((m) => [m.key, m]));
  const hasTapeStrip = Boolean(smaSig || trailSig || rangeSig || rvolSig || volSig || headlines.length > 0);
  const myDecisions = data?.decisions ?? [];

  return (
    <Drawer open onClose={onClose} title={title} actions={actions}>
      {q.isLoading && !data ? (
        <div className="dash-note">Loading company view…</div>
      ) : q.isError && !data ? (
        <div className="cd-empty">
          <div className="dash-note">Could not load this company view.</div>
          <button
            type="button"
            className="cd-cockpit"
            onClick={() => onOpenCockpit(selection.thesisId, selection.nameKey)}
          >
            Open card in Cockpit ↗
          </button>
        </div>
      ) : !q.isPending && q.data === null ? (
        <div className="cd-empty">
          <div className="dash-note">
            {selection.nameKey} is not on this card&apos;s current basket.
          </div>
          <button
            type="button"
            className="cd-cockpit"
            onClick={() => onOpenCockpit(selection.thesisId, null)}
          >
            Open card in Cockpit ↗
          </button>
        </div>
      ) : data && resolved && member && def ? (
        <div className={`cd-body ${def.cls}${switching ? " recomputing" : ""}`}>
          {/* Card context / multi-card switcher */}
          <div className="cd-cards">
            {data.cards.length > 1 ? (
              <>
                <span className="cd-cards-label">Cards ({data.cards.length})</span>
                <div role="group" aria-label="Switch thesis card" style={{ display: "contents" }}>
                  {data.cards.map((c) => {
                    const active = c.thesis_id === data.thesis_id;
                    return (
                      <button
                        key={c.thesis_id}
                        type="button"
                        className={`cd-card-chip${active ? " active" : ""}`}
                        aria-pressed={active}
                        onClick={() => {
                          if (!active) {
                            setActiveN(null);
                            onSwitchCard(c.thesis_id, member.security_id ?? c.ticker);
                          }
                        }}
                      >
                        {c.thesis_name}
                        {c.archived && <span className="cd-card-arch">archived</span>}
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              <>
                <span className="cd-cards-label">Card</span>
                <span className="cd-card-name">
                  {data.thesis_name}
                  {data.cards[0]?.archived && <span className="cd-card-arch">archived</span>}
                </span>
              </>
            )}
          </div>
          {switching && <div className="dash-note">Switching card…</div>}

          <div className="cd-grid">
            {/* Full-width top band in expanded mode; slots 2 & 3 in default 600px mode */}
            <div className="cd-top">
              {/* Interactive price & events chart */}
              <section className="cd-chart-panel cd-slot-chart" aria-label="Price and events">
                <div className="cd-chart-head">
                  <div className="cd-sec-h">
                    Price &amp; events
                    {events.length > 0 && <span className="cd-sec-count">{events.length}</span>}
                  </div>
                  {bars.length >= 2 && (
                    <div className="cd-spans" role="group" aria-label="Chart range">
                      {CHART_SPANS.map((s) => (
                        <button
                          key={s}
                          type="button"
                          className={`cd-span-btn${span === s ? " active" : ""}`}
                          aria-pressed={span === s}
                          onClick={() => setSpan(s)}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {!sid ? (
                  <div className="sc-spark sc-spark-empty">
                    Unresolved security — no price tape linked.
                  </div>
                ) : (
                  <Suspense
                    fallback={<div className="sc-spark sc-spark-empty">reading the price path…</div>}
                  >
                    <PriceSparkline
                      span={span}
                      bars={bars}
                      events={events}
                      bands={bands}
                      activeN={activeN}
                      onActivate={setActiveN}
                      loading={windowQ.isLoading}
                      error={windowQ.isError}
                      hideIngest
                    />
                  </Suspense>
                )}
                {windowQ.data && (
                  <div className="sc-tape">
                    tape: {windowQ.data.source} · loaded from {fmtDate(windowQ.data.start)} · as-of{" "}
                    {fmtDate(windowQ.data.asof)}
                  </div>
                )}
              </section>

              {/* Tape Snapshot Strip: returns + range + volume + posture headlines */}
              {hasTapeStrip && (
                <section className="cd-tape cd-slot-tape" aria-label="Tape snapshot">
                  <div className="cd-tape-cells">
                    {smaSig && (
                      <div className="cd-tcell">
                        <span className="k">Last</span>
                        <span className="v">
                          <FlagWrap flag={cellFlag(dataFlags, "last")}>
                            <LastCell sig={smaSig} stopped={stopped} />
                          </FlagWrap>
                        </span>
                      </div>
                    )}
                    {RETURN_WINDOW_KEYS.map((key) => {
                      const m = trailByKey.get(key);
                      return (
                        <div className="cd-tcell" key={key}>
                          <span className="k">{RETURN_LABELS[key]}</span>
                          <span className="v">
                            <FlagWrap flag={cellFlag(dataFlags, key)}>
                              {!m || m.value == null ? (
                                <span className="muted" title={m?.note ?? undefined}>
                                  —
                                </span>
                              ) : (
                                <span className={`ret ${m.tone ?? ""}`}>{fmtMetricValue(m)}</span>
                              )}
                            </FlagWrap>
                          </span>
                        </div>
                      );
                    })}
                    {rangeSig && (
                      <div className="cd-tcell">
                        <span className="k">Off 52w</span>
                        <span className="v">
                          <FlagWrap flag={cellFlag(dataFlags, "range_52w")}>
                            <Off52wCell sig={rangeSig} />
                          </FlagWrap>
                        </span>
                      </div>
                    )}
                    {rvolSig && (
                      <div className="cd-tcell">
                        <span className="k">RVOL 8d</span>
                        <span className="v">
                          <FlagWrap flag={cellFlag(dataFlags, "rvol")}>
                            <RvolCell sig={rvolSig} />
                          </FlagWrap>
                        </span>
                      </div>
                    )}
                    {volSig && (
                      <div className="cd-tcell">
                        <span className="k">ADV 20d</span>
                        <span className="v">
                          <FlagWrap flag={cellFlag(dataFlags, "adv_20d")}>
                            <AdvCell sig={volSig} />
                          </FlagWrap>
                        </span>
                      </div>
                    )}
                  </div>
                  {headlines.length > 0 && (
                    <div className="cd-tape-headlines">
                      {headlines.map((s) => (
                        <DisplayHeadlineRow key={s.kind} headline={s.headline!} />
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>

            <div className="cd-cols">
              {/* Left column in expanded mode: Call + Signals + Event Ledger + Decisions */}
              <div className="cd-main">
                {/* Call & Clocks Hero */}
                <section className="cd-hero cd-slot-call" aria-label="The call for this name">
                  <div className="cd-hero-top">
                    <div className="cd-hero-verdict">
                      {call?.verdict ? (
                        <div className="np-verdict">
                          {verdictLabel(call.verdict)}
                          {call.theme_armed && (
                            <span
                              className="np-theme"
                              title="Armed on the theme conviction (a fallback) — capped at a starter, not its own signal"
                            >
                              theme
                            </span>
                          )}
                        </div>
                      ) : (
                        <div className="np-stateline">{STATE_LINE[def.key] ?? "—"}</div>
                      )}
                    </div>
                    {conf !== null && (
                      <div className="cd-hero-conf" title={SETUP_STRENGTH_TOOLTIP}>
                        <span>Setup</span>
                        <span className="bar">
                          <span className="fill" style={{ display: "block", width: `${conf}%` }} />
                        </span>
                        <span className="pct">{conf}%</span>
                      </div>
                    )}
                  </div>

                  {grades.length > 0 && (
                    <div className="cd-hero-grades">
                      {grades.map(([k, g]) => (
                        <span key={k}>
                          {k} <span className={`grade ${gradeClass(g)}`}>{g.toUpperCase()}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {(call?.arm_until || call?.exit_by) && (
                    <div className="cd-clocks">
                      {call.arm_until && (
                        <div className={`clock-row entry${call.entry_window_closing ? " closing" : ""}`}>
                          <span className="cd">{fmtDate(call.arm_until)}</span>
                          <span className="x">
                            {call.verdict ? "Entry window · confirmation clock" : "Confirmation clock"}
                            {armDays !== null &&
                              (armDays < 0
                                ? " · lapsed"
                                : call.verdict
                                  ? armDays === 0
                                    ? " · act today"
                                    : ` · act within ${armDays}d`
                                  : armDays === 0
                                    ? " · decays today"
                                    : ` · decays in ${armDays}d`)}
                            {call.entry_window_closing && " · entry window closing"}
                          </span>
                        </div>
                      )}
                      {call.exit_by && (
                        <div className={`clock-row hold${call.lapsing ? " lapse" : ""}`}>
                          <span className="cd">{fmtDate(call.exit_by)}</span>
                          <span className="x">
                            Hold exit-by · conviction clock
                            {exitDays !== null &&
                              exitDays >= 0 &&
                              (call.lapsing
                                ? ` · hold clock closing · ${dayCount(exitDays)}`
                                : ` · ${dayCount(exitDays)}`)}
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  {held && data.position && (
                    <div className="cd-pos">
                      Position open — entered {fmtDate(data.position.opened_on)}
                      {data.position.entry_price != null && <> @ ${data.position.entry_price}</>}
                      {data.position.current_price != null && <> · now ${data.position.current_price}</>}
                    </div>
                  )}
                </section>

                {/* Triggers & Risk Signals */}
                <section className="cd-signals-grid cd-slot-signals" aria-label="Triggers and risk signals">
                  <div className="cd-signal-pane">
                    <div className="cd-sec-h">
                      Triggers · this name&apos;s own
                      {ownTriggers.length > 0 && (
                        <span className="cd-sec-count">{ownTriggers.length}</span>
                      )}
                    </div>
                    {ownTriggers.length > 0 ? (
                      ownTriggers.map((t, i) => (
                        <TriggerRow
                          key={i}
                          item={{ ...t, ticker: null }}
                          icon="◉"
                          variant="hit"
                          showGrade
                        />
                      ))
                    ) : (
                      <div className="np-stateline">None live.</div>
                    )}
                  </div>
                  <div className="cd-signal-pane">
                    <div className="cd-sec-h">
                      Risk signals · this name
                      {risks.length > 0 && <span className="cd-sec-count">{risks.length}</span>}
                    </div>
                    {risks.length > 0 ? (
                      risks.map((t, i) => (
                        <TriggerRow
                          key={i}
                          item={{ ...t, ticker: null }}
                          icon="▲"
                          variant="warn"
                          showGrade={false}
                        />
                      ))
                    ) : (
                      <div className="np-stateline">None active.</div>
                    )}
                  </div>
                </section>

                {/* Upcoming & recent Catalysts (company catalyst facts + card calendar) */}
                {hasCatalysts && (
                  <section className="cd-slot-catalysts" aria-label="Catalysts">
                    <div className="cd-sec-h">
                      Catalysts
                      <span className="cd-sec-count">
                        {catalystFacts.length + cardCatalysts.length}
                      </span>
                    </div>
                    <div className="cd-cat-list">
                      {catalystFacts.slice(0, 6).map((f, i) => (
                        <div className={`cd-cat-row${f.live ? " live" : ""}`} key={`cf-${i}`}>
                          <span className="cd-cat-when">{fmtDate(f.date)}</span>
                          <span className="cd-cat-body">
                            <span className="cd-cat-lbl">{f.label}</span>
                            {f.live && f.grade && (
                              <span className={`grade ${gradeClass(f.grade)}`}>
                                {f.grade.toUpperCase()}
                              </span>
                            )}
                            {f.catalyst_type && (
                              <span className="cd-cat-kind">
                                {f.catalyst_type.replace(/_/g, " ")}
                              </span>
                            )}
                            {f.url && (
                              <ExternalLink className="evled-link" href={f.url}>
                                {isSecLink(f.url) ? "filing" : "source"} ↗
                              </ExternalLink>
                            )}
                          </span>
                        </div>
                      ))}
                      {catalystFacts.length > 6 && (
                        <div className="cd-cat-more">
                          +{catalystFacts.length - 6} more company catalyst
                          {catalystFacts.length - 6 === 1 ? "" : "s"}
                        </div>
                      )}
                      {cardCatalysts.map((c) => {
                        const d = daysFrom(origin, c.when_date);
                        const soon = d !== null && d >= 0 && d <= 21;
                        const passed = d !== null && d < 0;
                        const when = c.when_date
                          ? `${fmtDate(c.when_date)}${d === null ? "" : d >= 0 ? ` · ${dayCount(d)}` : ` · ${-d}d ago`}`
                          : (c.when_label ?? "—");
                        return (
                          <div
                            className={`cd-cat-row card${soon ? " soon" : ""}${passed ? " passed" : ""}`}
                            key={c.id}
                          >
                            <span className="cd-cat-when">{when}</span>
                            <span className="cd-cat-body">
                              <span className="cd-cat-lbl">{c.label}</span>
                              <span className="cd-cat-kind">{c.kind || "card"}</span>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                )}

                {/* Cross-highlighted Event Ledger (row #N <-> chip #N) */}
                {events.length > 0 && (
                  <div className="cd-slot-ledger">
                    <EventLedger
                      events={events}
                      activeN={activeN}
                      onActivate={setActiveN}
                      showCockpitStrip={false}
                      heading="Event ledger"
                      hideIngest
                      filterable
                    />
                  </div>
                )}

                {/* Decision Log */}
                {myDecisions.length > 0 && (
                  <section className="cd-slot-decisions" aria-label="Decision log">
                    <div className="cd-sec-h">Decision log · this name</div>
                    {myDecisions.slice(0, 6).map((d) => (
                      <div className={`np-dlog${d.voided ? " voided" : ""}`} key={d.id}>
                        <b className="act">{d.action}</b>
                        <span className="dt">{fmtDate(d.decision_date)}</span>
                        <span className="dd">
                          {[
                            d.shares != null ? `${d.shares} sh` : null,
                            d.price != null ? `@ $${d.price}` : null,
                            d.reason || null,
                            d.call_verdict ? `platform: ${d.call_verdict.replace(/_/g, "-")}` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          {d.voided && " · voided"}
                        </span>
                      </div>
                    ))}
                  </section>
                )}
              </div>

              {/* Right column in expanded mode: Thesis Fit + Scoring + Identity + Full Indicators */}
              <div className="cd-side">
                {(member.thesis_fit || scored) && (
                  <section className="cd-thesis-stack cd-slot-thesis" aria-label="Thesis fit and scoring">
                    {member.thesis_fit && (
                      <div>
                        <div className="cd-sec-h">Thesis fit</div>
                        <div className="np-fit">
                          {member.thesis_fit}
                          <span className="tag">
                            {AUTHOR_TAG[member.authored_by] ?? member.authored_by}
                          </span>
                        </div>
                      </div>
                    )}

                    {scored && (
                      <div>
                        <div className="cd-sec-h">Scoring snapshot</div>
                        <div className="np-meters">
                          {(
                            [
                              ["Purity", "purity", false],
                              ["Runway", "runway", false],
                              ["Catalysts", "catalysts", false],
                              ["Dilution", "dilution", true],
                            ] as const
                          ).map(([label, key, risk]) => {
                            const pace = key === "runway" ? runwayPaceWords(scored[key]) : null;
                            const cash = key === "runway" ? runwayCashWords(scored[key]) : null;
                            const na = key === "runway" ? runwayNotApplicable(scored[key]) : null;
                            return (
                              <Fragment key={key}>
                                <div className="np-meter-row">
                                  <Meter
                                    label={label}
                                    figure={scored[key]}
                                    risk={risk}
                                    title={na ?? undefined}
                                    notApplicable={na != null ? "n/a" : undefined}
                                  />
                                  <span className="val">{meterValueLabel(key, scored[key])}</span>
                                </div>
                                {na && (
                                  <div className="np-pace" data-testid="np-runway-na">
                                    {na}
                                  </div>
                                )}
                                {cash && (
                                  <div className="np-pace" data-testid="np-runway-cash">
                                    {cash}
                                  </div>
                                )}
                                {pace && (
                                  <div className="np-pace" data-testid="np-runway-pace">
                                    {pace.ytd}
                                  </div>
                                )}
                                {pace?.caveat && (
                                  <div className="np-pace" data-testid="np-runway-caveat">
                                    {pace.caveat}
                                  </div>
                                )}
                                <ProvenanceSources
                                  sources={scored[key].provenance ?? []}
                                  label={`${label} provenance`}
                                />
                              </Fragment>
                            );
                          })}
                        </div>
                        <SharedLines scored={scored} values={data.shared_values ?? null} asof={asof} />
                      </div>
                    )}
                  </section>
                )}

                {/* Identity & Data Health */}
                <section className="cd-slot-identity" aria-label="Identity and data health">
                  <div className="cd-sec-h">Identity</div>
                  <div className="np-idgrid">
                    {identityCells.map(([k, v]) => (
                      <div className="cell" key={k}>
                        <div className="k">{k}</div>
                        <div className="v">{v}</div>
                      </div>
                    ))}
                    {member.detail && (
                      <div className="cell wide">
                        <div className="k">Detail</div>
                        <div className="v">{member.detail}</div>
                      </div>
                    )}
                  </div>
                  {scored && (
                    <ProvenanceSources
                      sources={scored.market_cap.provenance ?? []}
                      label="Market cap provenance"
                    />
                  )}
                  {scored?.thin_price_history && (
                    <div className="np-thinflag">
                      ⚠ thin price history — history-window signals may be starved
                    </div>
                  )}
                  {scored?.price_feed_limitation && (
                    <div className="np-feednote" title={scored.price_feed_limitation}>
                      price history limited: vendor serves no history
                    </div>
                  )}
                  <DataFlagsSection member={dataFlags} />
                </section>

                {/* Full Indicators breakdown (expanded by default; still collapsible in one click) */}
                {signals.length > 0 && (
                  <details className="cd-details cd-slot-indicators" open>
                    <summary>
                      Indicators · full breakdown
                      <span className="cd-sec-count">{signals.length}</span>
                    </summary>
                    <div className="cd-details-body">
                      <DisplaySignalsSection
                        display={display}
                        foreignFilerForm={scored?.foreign_filer_form}
                      />
                    </div>
                  </details>
                )}
              </div>
            </div>
          </div>

          <div className="cd-footer">
            Read-only — sizing, facts, and business-type re-tags live in the Workbench; acting,
            passing, and undo live in the Cockpit. Closing returns the dashboard exactly as you left
            it.
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

function SharedLines({
  scored,
  values,
  asof,
}: {
  scored: NonNullable<BucketRow["scored"]>;
  values: UserCompanyValuesOut | null;
  asof: string;
}) {
  const { currentNight } = useAppClock();
  if (isPastAsof(asof, currentNight)) return null;
  const lines: string[] = [];
  if (values) {
    for (const [title, v] of [
      ["Share count", values.shares],
      ["Cash", values.cash_burn],
    ] as const) {
      if (v.state === "awaiting_steward") lines.push(`${title}: awaiting the steward`);
      else if (v.state === "no_value" && v.reason !== "cash-generative")
        lines.push(`${title}: no value yet (${reasonClause(v)})`);
      else if (v.pending) lines.push(`${title}: ${lowerFirst(v.pending.line)}`);
    }
  }
  if (scored.instrument_kind !== "etf" && scored.purity.value == null)
    lines.push("Purity: not set yet (yours to set in the Workbench)");
  if (lines.length === 0) return null;
  return (
    <div className="np-shared">
      {lines.map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  );
}
