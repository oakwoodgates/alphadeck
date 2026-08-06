import { lazy, Suspense, useMemo, useState } from "react";

import type { PriceBar, ScoreboardEpisodeOut } from "../api/hooks";
import { useDisplaySignals, useEpisodePriceWindow, useWorkbenchScored } from "../api/hooks";
import { fmtDate, gradeClass } from "../util/format";
import { EventLedger } from "./EventLedger";
import { identityCells, signalHeadlines } from "./ledger";
import { buildOverlayEvents } from "./overlay";
import { episodeBadges, fmtReturn, returnLabel } from "./rows";
import {
  edgeLens,
  fmtPrice,
  horizonLens,
  moveNote,
  noForwardBar,
  setupStrengthPct,
} from "./scorecard";

// A shared empty-bars const so a still-loading window keeps a STABLE reference (never a fresh `[]` each
// render), which keeps the chart effect from re-running on an unrelated re-render.
const NO_BARS: PriceBar[] = [];

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
}: {
  ep: ScoreboardEpisodeOut;
  thesisName?: string;
  /** The page-level scoreboard as-of. Present → the price sparkline mounts (on-demand, drawer-open
   *  only) and its series is capped here. Absent → no chart (the pure-render tests pass no asof). */
  asof?: string;
}) {
  // A just-armed episode (no forward bar yet) carries degenerate 0.0% forward/peak/arm_until
  // returns; mirror the ledger row and never render those as a flat move (fix: false-flat 0.0%).
  const noBar = noForwardBar(ep);
  const fwd = fmtReturn(ep.forward_return);
  const note = moveNote(ep);
  const horizon = noBar ? null : horizonLens(ep); // no real peak to judge until a bar lands
  const edge = edgeLens(ep);
  const armUntil = fmtReturn(ep.arm_until_return);
  const strength = setupStrengthPct(ep);
  const badges = episodeBadges(ep);
  // the arm_until_return row is a forward-return too — degenerate before the first bar; the grades
  // and setup strength are arm-date facts, so they stay.
  const showArmUntil = !noBar && (ep.arm_until != null || ep.arm_until_return != null);
  const showGrades = ep.entry_grade != null || ep.conviction_grade != null || strength != null;
  const hasSetup = showArmUntil || showGrades;

  // --- Slice B: lift the price window + the unified numbered events HERE so the chart and the ledger share
  // the ONE array (row #N ↔ chip #N, built once). The window request mirrors the old in-PriceSparkline gate
  // (asof present AND a forward bar exists); the server owns the effective floor and asof cap (invariant #1).
  const end = ep.exit_by ?? asof ?? "";
  const windowQ = useEpisodePriceWindow(
    { thesisId: ep.thesis_id, securityId: ep.security_id, start: ep.arm_date, end, asof: asof ?? "" },
    Boolean(asof) && !noBar,
  );
  const bars = windowQ.data?.bars ?? NO_BARS;
  const events = useMemo(
    () => buildOverlayEvents(ep, windowQ.data?.insider_buys ?? [], windowQ.data?.bars ?? []),
    [ep, windowQ.data],
  );
  // The cross-highlight bridge: a chip hover (PriceSparkline) or a row hover (EventLedger) sets `activeN`;
  // the other child rings/tints the match. Held here so both children read one source of truth.
  const [activeN, setActiveN] = useState<number | null>(null);

  // The Cockpit strip's two reads (identity + signal headlines) — same asof, drawer-open gated (the hooks
  // disable themselves without an asof), joined to THIS episode by security_id. A missing field → "—".
  const scoredMember =
    useWorkbenchScored(ep.thesis_id, asof ?? "").data?.members.find(
      (m) => m.security_id === ep.security_id,
    ) ?? null;
  const memberSignals =
    useDisplaySignals(ep.thesis_id, asof ?? "").data?.members.find(
      (m) => m.security_id === ep.security_id,
    ) ?? null;
  const identity = identityCells(scoredMember);
  const signals = signalHeadlines(memberSignals);
  // A closed/matured episode's display signals are trailing windows re-derived at the drawer's asof, NOT the
  // episode's period — caption them so a name-current 90d figure isn't misread as the episode's own.
  const tapeAsof = asof && (ep.status === "closed" || ep.matured) ? asof : null;

  return (
    <div className="sc">
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
        <div className="sc-reason">closed · {ep.close_reason}</div>
      )}

      {/* Lens 1 — The move (provenance: closes the "show the prices" gap). */}
      <section className="sc-lens">
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
        {/* Slice-3: the intra-window [arm → last real bar] price path. On-demand (drawer-open only),
            capped at asof server-side; the drawer's expanded mode gives it room. Rendered only when
            the page-level asof is threaded in (the pure-render scorecard tests pass none → no chart). */}
        {asof && (
          <Suspense fallback={<div className="sc-spark sc-spark-empty">reading the price path…</div>}>
            <PriceSparkline
              ep={ep}
              bars={bars}
              events={events}
              activeN={activeN}
              onActivate={setActiveN}
              loading={windowQ.isLoading}
              error={windowQ.isError}
            />
          </Suspense>
        )}
      </section>

      {/* Lens 2 — Horizon calibration: is exit_by well-timed? Hidden entirely without a peak. */}
      {horizon && (
        <section className="sc-lens">
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
      <section className="sc-lens">
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
                armed <span className={`ret ${edge.forward.cls}`}>{edge.forward.text}</span> (from{" "}
                {fmtDate(ep.arm_date)})
              </span>
            </div>
          </>
        )}
      </section>

      {/* Lens 4 — Entry window + setup. */}
      {hasSetup && (
        <section className="sc-lens">
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
                  entry <span className={`grade ${gradeClass(ep.entry_grade)}`}>
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
                <span title="an experimental relative indicator — not a probability">
                  setup strength {strength}%
                </span>
              )}
            </div>
          )}
        </section>
      )}

      {/* Slice B — the event ledger + Cockpit strip: the SAME numbered events the chart drew (row #N ↔ chip
          #N, one shared array), cross-highlighting with it. Gated exactly as the chart is (asof + a forward
          bar); a no-forward-bar episode gets neither. Empty events (still loading) → the ledger renders
          nothing yet. */}
      {asof && !noBar && (
        <EventLedger
          events={events}
          activeN={activeN}
          onActivate={setActiveN}
          identity={identity}
          signals={signals}
          tapeAsof={tapeAsof}
        />
      )}
    </div>
  );
}
