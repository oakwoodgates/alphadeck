import { useEffect } from "react";

import type { CallCardResponse, ThesisDetail, TriggerRefOut } from "../api/hooks";
import { useDecisions } from "../api/hooks";
import { TriggerRow } from "../components/CallCard";
import { Meter } from "../workbench/Meter";
import { formatMarketCap, meterValueLabel } from "../workbench/format";
import { archLabel, byEventDateDesc, daysFrom, fmtDate, gradeClass, verdictLabel } from "../util/format";
import type { BucketDef, BucketKey, BucketRow } from "./buckets";

interface Props {
  row: BucketRow;
  def: BucketDef;
  /** The thesis card — the wire's only home for a Warming name's triggers and any member's risk
   *  signals (both joins are by ticker: TriggerRefOut carries no security_id). */
  card: CallCardResponse | undefined;
  /** For the per-name slice of the decision log (same query the rail already fetched). */
  thesisId: string;
  /** The thesis's open position — shown here only when attributed to THIS name (its security_id). */
  position: ThesisDetail["position"];
  asof: string;
  onClose: () => void;
}

/** The verdict-less buckets tell their state in words instead (the honest degrade — a Warming name
 *  has a live conviction the member lists don't carry; a Quiet one truly has nothing at this as-of). */
const STATE_LINE: Partial<Record<BucketKey, string>> = {
  warming: "Conviction fired — awaiting market confirmation.",
  watch: "Moving, no conviction yet — confirmation only.",
  quiet: "No live signals at this as-of.",
};

const AUTHOR_TAG: Record<string, string> = {
  system_drafted: "drafted",
  operator_set: "yours",
  operator_edited: "edited",
};

/** The per-name panel — a fixed, NON-MODAL slide-over (no scrim: the table stays clickable, so
 *  switching names is one click on the next row; the table itself never unmounts). READ-ONLY by
 *  design: every value is a wire field this page already fetched — sizing, facts, and archetype
 *  decisions live in the Workbench. Esc / ✕ / re-clicking the row closes it. */
export function NamePanel({ row, def, card, thesisId, position, asof, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const member = row.member;
  const call = row.call;
  const scored = row.scored;

  // This name's own triggers: the member call carries them (armed/watch tiers); a Warming name has
  // no member call, so its live firing comes from the thesis list filtered by ticker. The ticker
  // chip is stripped off each row — in a single-name panel it would be true of every line (noise).
  const own: TriggerRefOut[] =
    call && call.triggers.length > 0
      ? call.triggers
      : (card?.triggers_fired ?? []).filter((t) => t.ticker != null && t.ticker === member.ticker);
  const risks = (card?.risk_signals ?? []).filter(
    (t) => t.ticker != null && t.ticker === member.ticker,
  );

  // The operator record, per-name: the open position when it's attributed to THIS name, and the
  // decision rows logged ON this name (by security_id). Thesis-level rows (no name on the row —
  // passes, seed-era takes) stay on the rail's full log, which is also where acting and undo live:
  // the panel READS the record, it never writes it (one write surface, no divergence).
  const decisions = useDecisions(thesisId).data ?? [];
  const myDecisions = member.security_id
    ? decisions.filter((d) => d.security_id === member.security_id)
    : [];
  const held = Boolean(position?.security_id && position.security_id === member.security_id);

  const armDays = daysFrom(asof, call?.arm_until);
  const exitDays = daysFrom(asof, call?.exit_by);
  const conf = call?.confidence == null ? null : Math.round(call.confidence * 100);

  const grades: [string, string][] = [];
  if (call?.conviction_grade) grades.push(["conviction", call.conviction_grade]);
  if (call?.confirmation_grade) grades.push(["confirmation", call.confirmation_grade]);
  if (call?.entry_grade) grades.push(["entry", call.entry_grade]);

  // identity: everything free on the wire, "—" where a field didn't resolve (never a guess). The
  // operator's per-name size weight is LABELED as such — it must never read as the signal
  // conviction beside it (the two meanings of "conviction" never cross, invariant #4).
  const weight =
    member.conviction == null
      ? "—"
      : member.conviction >= 1 && member.conviction <= 5
        ? `${"●".repeat(member.conviction)}${"○".repeat(5 - member.conviction)} ${member.conviction}`
        : String(member.conviction);
  const cells: [string, string][] = [
    ["Archetype", member.archetype ? archLabel(member.archetype) : "—"],
    ["Segment", member.segment ?? "—"],
    ["Sector", scored?.sector ?? "—"],
    ["Exchange", scored?.exchange ?? "—"],
    ["Category", scored?.category ?? "—"],
    ["Mkt cap", formatMarketCap(scored?.market_cap.value)],
    ["Size weight (yours)", weight],
    ["Role", member.role || "—"],
  ];

  return (
    <aside className={`npanel ${def.cls}`} aria-label={`${member.ticker} — per-name panel`}>
      <div className="np-head">
        <span className="np-tk">{member.ticker}</span>
        <span className="np-bucket">
          <span className="rowdot" />
          {def.label}
        </span>
        <button type="button" className="np-close" title="close (Esc)" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="np-co">
        {scored?.name ??
          (member.security_id ? "—" : "unresolved — no security-master link for this row")}
      </div>

      <div className="np-h">The call · this name</div>
      {call?.verdict ? (
        <>
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
          {grades.length > 0 && (
            <div className="np-grades">
              {grades.map(([k, g]) => (
                <span key={k}>
                  {k} <span className={`grade ${gradeClass(g)}`}>{g.toUpperCase()}</span>
                </span>
              ))}
            </div>
          )}
          {conf !== null && (
            <div className="np-conf">
              <div className="row">
                <span>Confidence</span>
                <span>{conf}%</span>
              </div>
              <div className="bar">
                <div className="fill" style={{ width: `${conf}%` }} />
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="np-stateline">{STATE_LINE[def.key] ?? "—"}</div>
      )}
      {(call?.arm_until || call?.exit_by) && (
        <div className="np-clocks">
          {call.arm_until && (
            <div className="clock-row entry">
              <span className="cd">{fmtDate(call.arm_until)}</span>
              <span className="x">
                {call.verdict ? "Entry window · confirmation clock" : "Confirmation clock"}
                {armDays !== null &&
                  (armDays < 0
                    ? " · lapsed"
                    : call.verdict
                      ? ` · act within ${armDays}d`
                      : ` · decays in ${armDays}d`)}
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
                  (call.lapsing ? ` · lapses in ${exitDays}d` : ` · ${exitDays}d`)}
              </span>
            </div>
          )}
        </div>
      )}

      {held && position && (
        <>
          <div className="np-h">Position · this name</div>
          <div className="np-pos">
            Position open — entered {fmtDate(position.opened_on)}
            {position.entry_price != null && <> @ ${position.entry_price}</>}
            {position.current_price != null && <> · now ${position.current_price}</>}
          </div>
        </>
      )}

      <div className="np-h">Triggers · this name&apos;s own</div>
      {own.length > 0 ? (
        // newest-first, same idiom as the rail (frontend-only display sort)
        [...own].sort(byEventDateDesc).map((t, i) => (
          // provenance rides every row (#6); the redundant same-ticker chip is stripped
          <TriggerRow key={i} item={{ ...t, ticker: null }} icon="◉" variant="hit" showGrade />
        ))
      ) : (
        <div className="np-stateline">None live.</div>
      )}

      <div className="np-h">Risk signals · this name</div>
      {risks.length > 0 ? (
        [...risks].sort(byEventDateDesc).map((t, i) => (
          <TriggerRow key={i} item={{ ...t, ticker: null }} icon="▲" variant="warn" showGrade={false} />
        ))
      ) : (
        <div className="np-stateline">None active.</div>
      )}

      <div className="np-h">Identity</div>
      <div className="np-idgrid">
        {cells.map(([k, v]) => (
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
      {/* the enrichment's archetype RECOMMENDATION, quietly (#10: display-only, decided elsewhere) */}
      {!member.archetype && scored?.archetype_hint && (
        <div className="np-hintline">
          ✦ figures suggest {archLabel(scored.archetype_hint)} — decide in the Workbench
        </div>
      )}

      {member.thesis_fit && (
        <>
          <div className="np-h">Thesis fit</div>
          <div className="np-fit">
            {member.thesis_fit}
            <span className="tag">{AUTHOR_TAG[member.authored_by] ?? member.authored_by}</span>
          </div>
        </>
      )}

      {scored && (
        <>
          <div className="np-h">Scoring snapshot</div>
          <div className="np-meters">
            {(
              [
                ["Purity", "purity", false],
                ["Runway", "runway", false],
                ["Catalysts", "catalysts", false],
                ["Dilution", "dilution", true],
              ] as const
            ).map(([label, key, risk]) => (
              <div className="np-meter-row" key={key}>
                <Meter label={label} figure={scored[key]} risk={risk} />
                <span className="val">{meterValueLabel(key, scored[key])}</span>
              </div>
            ))}
            {scored.unconfirmed_estimates > 0 && (
              <div className="np-unconf">{scored.unconfirmed_estimates} unconfirmed estimate(s)</div>
            )}
          </div>
        </>
      )}

      {myDecisions.length > 0 && (
        <>
          <div className="np-h">Decision log · this name</div>
          {/* voided rows grey with a tag — visible, never vanished (the rail's rule, kept here) */}
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
        </>
      )}

      <div className="np-note">
        Read-only — sizing, facts, and archetype decisions live in the Workbench; acting, passing,
        and undo live on the thesis rail. Closing returns the table exactly as you left it.
      </div>
    </aside>
  );
}
