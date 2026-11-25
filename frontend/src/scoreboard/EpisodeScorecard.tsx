import { lazy, Suspense, useMemo, useState } from "react";

import type { DisplaySignal, PriceBar, ScoreboardEpisodeOut } from "../api/hooks";
import { useEpisodePriceWindow, useMemberPanel } from "../api/hooks";
import { DataFlagsSection, FlagWrap } from "../cockpit/DataFlagMark";
import { cellFlag, stoppedFeedReason } from "../cockpit/dataFlags";
import {
  AdvCell,
  DisplayHeadlineRow,
  fmtMetricValue,
  LastCell,
  Off52wCell,
  RETURN_WINDOW_KEYS,
  RvolCell,
} from "../cockpit/DisplaySignalsSection";
import { ExternalLink, isSecLink } from "../components/ExternalLink";
import "../dashboards/company-drawer.css";
import { fmtDate, gradeClass, SETUP_STRENGTH_TOOLTIP } from "../util/format";
import { isRerun } from "./editions";
import { EventLedger } from "./EventLedger";
import { identityCells, transitionLines } from "./ledger";
import { buildOverlayEvents, type ChartSpan, flagBands, tapeSignals } from "./overlay";
import {
  closeReasonLine,
  episodeBadges,
  fmtReturn,
  ingestProvenanceLine,
  returnLabel,
  runIdentityTitle,
} from "./rows";
import {
  edgeLens,
  fmtPrice,
  horizonLens,
  moveNote,
  noForwardBar,
  operatorLensLine,
  setupStrengthPct,
} from "./scorecard";

// A shared empty-bars const so a still-loading window keeps a STABLE reference (never a fresh `[]` each
// render), which keeps the chart effect from re-running on an unrelated re-render.
const NO_BARS: PriceBar[] = [];
const CHART_SPANS: readonly ChartSpan[] = ["3M", "6M", "1Y", "ALL"];
const RETURN_LABELS: Record<(typeof RETURN_WINDOW_KEYS)[number], string> = {
  ret_1d: "1D",
  ret_7d: "7D",
  ret_30d: "30D",
  ret_90d: "90D",
  ret_1y: "1Y",
};

// lightweight-charts (~150kB) is drawer-only + on-demand — code-split it into its own async chunk so it
// never bloats the initial bundle (loaded the first time a drawer's scorecard renders with an asof).
const PriceSparkline = lazy(() =>
  import("./PriceSparkline").then((m) => ({ default: m.PriceSparkline })),
);

// The Slice-1 scorecard mounted inside the Drawer: the richer per-episode outcome the backend
// already ships but the ledger row never showed — four timing lenses that let the operator judge
// whether the platform's TIMING was any good. Read-only, provenance-first, honest loudness: a lens
// with no data renders nothing or one quiet muted line (never "null"/"NaN"). It surfaces more of
// what's computed — it changes no computation. Every field read here is on ScoreboardEpisodeOut.

export function EpisodeScorecard({
  ep,
  thesisName,
  asof,
  edition = null,
}: {
  ep: ScoreboardEpisodeOut;
  thesisName?: string;
  /** The page-level scoreboard as-of. Present → the price sparkline mounts (on-demand, drawer-open
   *  only) and its series is capped here. Absent → no chart (the pure-render tests pass no asof). */
  asof?: string;
  /** The EDITION this episode belongs to (S4) — the number the Scoreboard served. Rides the drawer's price
   *  window read; an edition >= 1 has no ingest history, so the INGEST badge/line never render for it. */
  edition?: number | null;
}) {
  const rerun = isRerun(edition);
  // A just-armed episode (no forward bar yet) carries degenerate 0.0% forward/peak/arm_until
  // returns; mirror the ledger row and never render those as a flat move (fix: false-flat 0.0%).
  const noBar = noForwardBar(ep);
  const fwd = fmtReturn(ep.forward_return);
  const note = moveNote(ep);
  const horizon = noBar ? null : horizonLens(ep); // no real peak to judge until a bar lands
  const edge = edgeLens(ep);
  const armUntil = fmtReturn(ep.arm_until_return);
  const strength = setupStrengthPct(ep);
  const badges = episodeBadges(ep, rerun);
  // null on a healthy arm → nothing renders; a re-run edition has no ingest history at all
  const ingestLine = rerun ? null : ingestProvenanceLine(ep);
  // the arm_until_return row is a forward-return too — degenerate before the first bar; the grades
  // and setup strength are arm-date facts, so they stay.
  const showArmUntil = !noBar && (ep.arm_until != null || ep.arm_until_return != null);
  const showGrades = ep.entry_grade != null || ep.conviction_grade != null || strength != null;
  // A2: the quiet operator line rides the Lens-4 section — which must render for it even when the
  // grades/arm_until are absent (a logged decision on a grade-less episode still shows).
  const opLine = operatorLensLine(ep);
  const hasSetup = showArmUntil || showGrades || opLine != null;

  // Chart range preset: undefined = default episode zoom (arm -> exit); 3M / 6M / 1Y / ALL zoom the
  // loaded universe in place without rebuilding the canvas.
  const [span, setSpan] = useState<ChartSpan | undefined>(undefined);

  // --- Slice B: lift the price window + the unified numbered events HERE so the chart and the ledger share
  // the ONE array (row #N ↔ chip #N, built once). Fetched whenever the drawer is open (asof present): the
  // window is the name's full relevance-floor history, NOT arm-relative — so a just-armed episode with no
  // forward bar YET still has months of pre-arm path to draw. Drawing the path and scoring the forward
  // OUTCOME are separate concerns: the outcome lenses below stay gated on noForwardBar; the chart does not.
  // The server owns the effective floor + asof cap (invariant #1).
  const end = ep.exit_by ?? asof ?? "";
  const windowQ = useEpisodePriceWindow(
    {
      thesisId: ep.thesis_id,
      securityId: ep.security_id,
      start: ep.arm_date,
      end,
      asof: asof ?? "",
      edition,
    },
    Boolean(asof),
  );
  const bars = windowQ.data?.bars ?? NO_BARS;

  // Single-member panel fetch (replaces 3 whole-basket queries: scored + display-signals + data-flags)
  // and shares the exact TanStack Query cache with CompanyDrawer. Gracefully returns null if a historical
  // episode's company was later removed from the basket.
  const memberQ = useMemberPanel(
    asof ? ep.thesis_id : null,
    asof ? ep.security_id : null,
    asof ?? "",
  );
  const memberData =
    memberQ.data &&
    !memberQ.isPlaceholderData &&
    (memberQ.data.thesis_id == null || memberQ.data.thesis_id === ep.thesis_id) &&
    (memberQ.data.member?.security_id == null ||
      memberQ.data.member.security_id === ep.security_id)
      ? memberQ.data
      : null;

  const scoredMember = memberData?.scored ?? null;
  const memberSignals = memberData?.display ?? null;
  const memberFlags = memberData?.data_flags ?? null;
  const cik = memberData?.cik ?? null;
  const edgarUrl = memberData?.edgar_url ?? null;

  const bands = useMemo(
    () => flagBands(memberFlags?.flags ?? [], windowQ.data?.bars ?? NO_BARS),
    [memberFlags, windowQ.data],
  );
  // A3: the display-signal events join the numbered universe as the grayest family. Only the allow-listed
  // kinds contribute (`tapeSignals` — sma_position + relative_strength; `insider_flow_90d` is excluded by
  // construction, its buys/sells already ARE the insider family). The `asof` rides in because these are
  // compute-on-read, not recorded history: the tooltip/ledger date the read rather than implying the call
  // knew it. No asof → no tape argument at all (never a fabricated as-of on a chip that claims one, #6).
  const events = useMemo(
    () =>
      buildOverlayEvents(
        ep,
        windowQ.data?.insider_buys ?? [],
        windowQ.data?.bars ?? NO_BARS,
        asof ? { signals: tapeSignals(memberSignals), asof } : undefined,
        // Slice B: the widened recorded families off the SAME price-window response — sells, 8-Ks,
        // 13D/G stakes — through the same numbering (screened/passive rows gray via eventSetAside).
        {
          sells: windowQ.data?.insider_sells ?? [],
          filings: windowQ.data?.corporate_events ?? [],
          stakes: windowQ.data?.activist_stakes ?? [],
          // S3: the company's splits, each on its first day at the new price
          splits: memberFlags?.splits ?? [],
        },
      ),
    [ep, windowQ.data, memberSignals, memberFlags, asof],
  );
  // The cross-highlight bridge: a chip hover (PriceSparkline) or a row hover (EventLedger) sets `activeN`;
  // the other child rings/tints the match. Held here so both children read one source of truth.
  const [activeN, setActiveN] = useState<number | null>(null);

  const identity = identityCells(scoredMember);
  const allSignals = memberSignals?.signals ?? [];
  const sigByKind = useMemo(() => {
    const map = new Map<string, DisplaySignal>();
    for (const s of allSignals) map.set(s.kind, s);
    return map;
  }, [allSignals]);
  const smaSig = sigByKind.get("sma_position") ?? null;
  const trailSig = sigByKind.get("trailing_returns") ?? null;
  const rangeSig = sigByKind.get("range_52w") ?? null;
  const rvolSig = sigByKind.get("rvol") ?? null;
  const volSig = sigByKind.get("volume_regime") ?? null;
  const headlines = allSignals.filter((s) => s.headline);
  const trailByKey = useMemo(
    () => new Map((trailSig?.metrics ?? []).map((m) => [m.key, m])),
    [trailSig],
  );
  const stopped = stoppedFeedReason(memberFlags);
  const hasTapeStrip = Boolean(
    asof && (smaSig || trailSig || rangeSig || rvolSig || volSig || headlines.length > 0),
  );
  const hasIdentityPanel = Boolean(
    asof && (scoredMember != null || cik != null || (memberFlags?.flags?.length ?? 0) > 0),
  );

  // C3: the un-numbered record trail — intra-run verdict/grade changes off the wire. Not gated on
  // asof: it reads the episode alone (no price window), so the pure-render path shows it too.
  const trail = transitionLines(ep.transitions);
  // A closed/matured episode's display signals are trailing windows re-derived at the drawer's asof, NOT the
  // episode's period — caption them so a name-current 90d figure isn't misread as the episode's own.
  const tapeAsof = asof && (ep.status === "closed" || ep.matured) ? asof : null;

  return (
    <div className="sc">
      <div className="sc-top">
        <div className="sc-head-block">
          <div className="sc-head">
            <span className="sc-tk">{ep.ticker ?? "—"}</span>
            {ep.company_name && <span className="sc-co">{ep.company_name}</span>}
            {thesisName && <span>· {thesisName}</span>}
            <span className="sc-span">
              {fmtDate(ep.arm_date)} → {ep.dearm_date ? fmtDate(ep.dearm_date) : "still armed"}
            </span>
          </div>
          {badges.length > 0 && (
            <div className="sc-badges">
              {badges.map((b) => (
                <span key={b.label} className={`sb-badge ${b.cls}`} title={b.title}>
                  {b.label}
                </span>
              ))}
            </div>
          )}
          {ep.status === "closed" && ep.close_reason && (
            // the reason in English; `title` keeps the raw wire token one hover away (translated, not
            // hidden). Slice C: a composed dearm_detail turns the "(see de-arm day)" deferral into the
            // actual answer — backend-authored copy, this line only renders it.
            <div className="sc-reason" title={ep.close_reason}>
              closed · {closeReasonLine(ep.close_reason, ep.dearm_detail)}
            </div>
          )}
          {/* The ingest-provenance line — rendered ONLY when the arm's ingest is actually flagged, so a
              healthy episode carries nothing at all (#7: loudness marks the exception). The INGEST badge
              above says THAT it's flagged; this says WHY (the server's composed note + the measured lag). */}
          {ingestLine && <div className="sc-ingest">{ingestLine}</div>}

          {/* The run-identity line (F1) — which policy and which code wrote this arm's recorded row. Unlike
              the ingest line above it is NOT gated on a flag: it is provenance on a drill-down, where a
              constant line is the point. It renders nothing at all on a legacy row (recorded before the stamp
              existed) or a replayed episode (a recompute has no recorded run) — the server sends null, and an
              absent line is the honest reading of "we do not know", never a row of em-dashes. The copy is
              composed server-side (the `ingest_note` precedent), so nothing here slices a hash. */}
          {ep.run_identity_note && (
            <div className="sc-runid" title={runIdentityTitle(ep)}>
              {ep.run_identity_note}
            </div>
          )}

          {/* The join-date note (editions S4) — QUIET, server-composed, and present only on a re-run edition's
              episode whose company's join date is an ESTIMATE (the record has no join dates; an exact join is
              not an exception). When the arm fell on that estimated night it also says why the episode is
              censored. Rendered verbatim, the run-identity precedent. */}
          {ep.edition_join?.note && (
            <div className="sc-annot sc-join" title={`join rule: ${ep.edition_join.method}`}>
              {ep.edition_join.note}
            </div>
          )}
        </div>

        {/* Full-width chart band (in expanded 100vw mode spans top; in default 600px sits below Lens 1). */}
        {asof && (
          <section className="cd-chart-panel sc-slot-chart" aria-label="Price and events">
            <div className="cd-chart-head">
              <div className="cd-sec-h">
                Price &amp; events
                {events.length > 0 && <span className="cd-sec-count">{events.length}</span>}
              </div>
              {bars.length >= 2 && (
                <div className="cd-spans" role="group" aria-label="Chart range">
                  <button
                    type="button"
                    className={`cd-span-btn${span === undefined ? " active" : ""}`}
                    aria-pressed={span === undefined}
                    onClick={() => setSpan(undefined)}
                  >
                    Episode
                  </button>
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
            <Suspense fallback={<div className="sc-spark sc-spark-empty">reading the price path…</div>}>
              <PriceSparkline
                ep={ep}
                span={span}
                bars={bars}
                events={events}
                bands={bands}
                activeN={activeN}
                onActivate={setActiveN}
                loading={windowQ.isLoading}
                error={windowQ.isError}
                hideIngest={rerun}
              />
            </Suspense>
            {/* The tape's own provenance (#6): WHICH fact table drew this path, the EFFECTIVE floor the
                server computed (not the start we asked for), and the as-of it was capped at (#1). Renders
                only once the window has landed — a caption over a loading chart would describe nothing. */}
            {windowQ.data && (
              <div className="sc-tape">
                tape: {windowQ.data.source} · loaded from {fmtDate(windowQ.data.start)} · as-of{" "}
                {fmtDate(windowQ.data.asof)}
              </div>
            )}
          </section>
        )}

        {/* Current Tape Snapshot Strip (reused from CompanyDrawer, with honest closed-episode disclaimer). */}
        {hasTapeStrip && (
          <section className="cd-tape sc-slot-tape" aria-label="Current tape snapshot">
            {tapeAsof && (
              <div className="sc-tape-cap">current tape · as-of {fmtDate(tapeAsof)}</div>
            )}
            <div className="cd-tape-cells">
              {smaSig && (
                <div className="cd-tcell">
                  <span className="k">Last</span>
                  <span className="v">
                    <FlagWrap flag={cellFlag(memberFlags, "last")}>
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
                      <FlagWrap flag={cellFlag(memberFlags, key)}>
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
                    <FlagWrap flag={cellFlag(memberFlags, "range_52w")}>
                      <Off52wCell sig={rangeSig} />
                    </FlagWrap>
                  </span>
                </div>
              )}
              {rvolSig && (
                <div className="cd-tcell">
                  <span className="k">RVOL 8d</span>
                  <span className="v">
                    <FlagWrap flag={cellFlag(memberFlags, "rvol")}>
                      <RvolCell sig={rvolSig} />
                    </FlagWrap>
                  </span>
                </div>
              )}
              {volSig && (
                <div className="cd-tcell">
                  <span className="k">ADV 20d</span>
                  <span className="v">
                    <FlagWrap flag={cellFlag(memberFlags, "adv_20d")}>
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

      <div className="sc-cols">
        <div className="sc-main">
          {/* Lens 1 — The move (provenance: closes the "show the prices" gap). */}
          <section className="sc-lens sc-slot-move">
            <div className="sc-h">The move</div>
            {noBar
              ? // no forward bar yet: the ENTRY only — an exit "@ arm_date" would be the same bar (a
                // false round-trip). No arrow, no exit.
                ep.entry_close != null && (
                  <div className="sc-flow">
                    <span>
                      {fmtPrice(ep.entry_close)} @ {fmtDate(ep.arm_date)}
                    </span>
                  </div>
                )
              : (ep.entry_close != null || ep.exit_close != null) && (
                  <div className="sc-flow">
                    <span>
                      {fmtPrice(ep.entry_close)} @ {fmtDate(ep.arm_date)}
                    </span>
                    <span className="sc-arrow">→</span>
                    <span>
                      {fmtPrice(ep.exit_close)} @ {fmtDate(ep.exit_date)}
                    </span>
                  </div>
                )}
            <div className="sc-ret">
              {/* never a false-flat 0.0% before a forward bar — "—", exactly as the ledger row does */}
              <span className={`ret ${fwd.cls}`}>{noBar ? "—" : fwd.text}</span>
              <span className="sc-retlabel">{returnLabel(ep)}</span>
            </div>
            {note && <div className="sc-muted">{note}</div>}
          </section>

          {/* Lens 2 — Horizon calibration: is exit_by well-timed? Hidden entirely without a peak. */}
          {horizon && (
            <section className="sc-lens sc-slot-horizon">
              <div className="sc-h">Horizon calibration</div>
              <div className="sc-ret">
                <span className={`ret ${horizon.peak.cls}`}>{horizon.peak.text}</span>
                <span className="sc-retlabel">peak @ {fmtDate(ep.peak_date)}</span>
              </div>
              {horizon.timing && <div className="sc-line">{horizon.timing}</div>}
              {horizon.giveback && <div className="sc-muted">{horizon.giveback}</div>}
            </section>
          )}

          {/* Lens 3 — Edge preservation: did we arm in time, or miss the early move? */}
          <section className="sc-lens sc-slot-edge">
            <div className="sc-h">Edge preservation</div>
            {edge.kind === "no_warm" ? (
              <div className="sc-muted">{edge.line}</div>
            ) : noBar ? (
              // a warm-vs-arm comparison here would be 0.0% vs 0.0% — say so, don't fake it
              <div className="sc-muted">not scorable until a forward bar lands</div>
            ) : (
              <>
                <div className="sc-line">{edge.lead}</div>
                <div className="sc-cmp">
                  <span>
                    warm-up <span className={`ret ${edge.warm.cls}`}>{edge.warm.text}</span> (from{" "}
                    {fmtDate(ep.warm_date)})
                  </span>
                  <span>
                    armed <span className={`ret ${edge.forward.cls}`}>{edge.forward.text}</span>{" "}
                    (from {fmtDate(ep.arm_date)})
                  </span>
                </div>
              </>
            )}
          </section>

          {/* Lens 4 — Entry window + setup. */}
          {hasSetup && (
            <section className="sc-lens sc-slot-setup">
              <div className="sc-h">Entry window + setup</div>
              {showArmUntil && (
                <div className="sc-ret">
                  <span className={`ret ${armUntil.cls}`}>{armUntil.text}</span>
                  <span className="sc-retlabel">
                    to entry-window close{ep.arm_until ? ` ${fmtDate(ep.arm_until)}` : ""}
                  </span>
                </div>
              )}
              {showGrades && (
                <div className="sc-setup">
                  {ep.entry_grade && (
                    <span>
                      entry{" "}
                      <span className={`grade ${gradeClass(ep.entry_grade)}`}>
                        {ep.entry_grade.toUpperCase()}
                      </span>
                    </span>
                  )}
                  {ep.conviction_grade && (
                    <span>
                      conviction{" "}
                      <span className={`grade ${gradeClass(ep.conviction_grade)}`}>
                        {ep.conviction_grade.toUpperCase()}
                      </span>
                    </span>
                  )}
                  {strength != null && (
                    <span title={SETUP_STRENGTH_TOOLTIP}>setup strength {strength}%</span>
                  )}
                </div>
              )}
              {/* A2: the operator's decision, one quiet line — the chip/ledger row carry the full detail */}
              {opLine && <div className="sc-muted">{opLine}</div>}
            </section>
          )}

          {/* Slice B + Tier 3: filterable event ledger sharing the chart's numbered events (row #N ↔ chip #N). */}
          {asof && events.length > 0 && (
            <div className="sc-slot-ledger">
              <EventLedger
                events={events}
                activeN={activeN}
                onActivate={setActiveN}
                showCockpitStrip={false}
                heading="Event ledger"
                hideIngest={rerun}
                filterable
              />
            </div>
          )}
        </div>

        {(hasIdentityPanel || trail.length > 0) && (
          <div className="sc-side">
            {/* Company identity & data health (reusing CompanyDrawer's grid + DataFlagsSection). */}
            {hasIdentityPanel && (
              <section
                className="sc-lens sc-slot-identity"
                aria-label="Company identity and data health"
              >
                <div className="sc-h">
                  Company identity
                  {tapeAsof && <span className="sc-h-sub">as-of {fmtDate(tapeAsof)}</span>}
                </div>
                <div className="np-idgrid">
                  {identity.map((cell) => (
                    <div className="cell" key={cell.label}>
                      <div className="k">{cell.label}</div>
                      <div className="v">
                        {cell.label === "Mkt cap" ? (
                          <FlagWrap flag={cellFlag(memberFlags, "mktcap")}>{cell.value}</FlagWrap>
                        ) : (
                          cell.value
                        )}
                      </div>
                    </div>
                  ))}
                  {cik && (
                    <div className="cell">
                      <div className="k">CIK</div>
                      <div className="v">
                        {edgarUrl ? (
                          <ExternalLink
                            href={edgarUrl}
                            title={
                              isSecLink(edgarUrl)
                                ? "Open the company on EDGAR"
                                : "Open the stored source"
                            }
                          >
                            {cik} ↗
                          </ExternalLink>
                        ) : (
                          cik
                        )}
                      </div>
                    </div>
                  )}
                </div>
                <DataFlagsSection member={memberFlags} />
              </section>
            )}

            {/* Slice C3 — the record trail: the finer intra-run calls-log transitions (verdict/grade)
                below the event ledger. A quiet UN-NUMBERED list — deliberately NOT chips and NOT in the
                1..N array (the numbered ledger stays chip-events-only, docs/SCOREBOARD.md). Every line
                traces to a recorded card diff (#6); no changes → nothing renders at all (#7). */}
            {trail.length > 0 && (
              <section className="sc-lens sc-trail sc-slot-trail">
                <div className="sc-h">Record trail</div>
                {trail.map((l, i) => (
                  <div key={i} className="sc-muted">
                    {fmtDate(l.date)} · {l.text}
                  </div>
                ))}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
