import type { CallCardResponse } from "../api/hooks";
import {
  accentVar,
  CALL_HEAD,
  daysFrom,
  fmtDate,
  gradeClass,
  STATE_CLASS,
  verdictLabel,
} from "../util/format";

type TriggerLike = NonNullable<CallCardResponse["triggers_fired"]>[number];

// The rail: the opinionated, auditable call. Recomputed live at `card.asof` (the read path).
export function CallCard({ card }: { card: CallCardResponse }) {
  const sc = STATE_CLASS[card.state] ?? "incub";
  const accent = accentVar(sc);
  const armed = card.state === "armed";
  const managing = card.state === "managing";
  // Confidence is an Armed-state metric (§7): the backend nulls it for a not-yet card, so the bar
  // only renders when armed — a Warming card never wears the Armed card's confidence bar.
  const conf = card.confidence == null ? null : Math.round(card.confidence * 100);
  const triggers = card.triggers_fired ?? [];
  const missing = card.missing ?? [];
  const armDays = daysFrom(card.asof, card.arm_until);
  const exitDays = daysFrom(card.asof, card.exit_by);

  return (
    <div className={`callcard cc-${sc}`}>
      <div className="cc-head">
        <span>{CALL_HEAD[card.state] ?? "The Call"}</span>
        {card.conviction_grade && (
          <span className={`grade ${gradeClass(card.conviction_grade)}`}>
            {card.conviction_grade.toUpperCase()} THESIS
          </span>
        )}
      </div>

      <div className="cc-body">
        <div className={`verdict ${sc}`}>{verdictLabel(card.verdict)}</div>
        <div className="vsub">{card.expression}</div>

        {/* the two keys — the arming model, made explicit */}
        <div className="keys">
          <Key label="Conviction" turned={card.key_conviction.turned} detail={card.key_conviction.detail} />
          <Key
            label="Confirmation"
            turned={card.key_confirmation.turned}
            detail={card.key_confirmation.detail}
            weak={card.key_confirmation.turned && card.confirmation_grade === "flip"}
          />
        </div>

        {conf !== null && (
          <div className="conf">
            <div className="conf-row">
              <span>Confidence</span>
              <span style={{ color: `var(${accent})` }}>{conf}%</span>
            </div>
            <div className="conf-bar">
              <div
                className="conf-fill"
                style={{ width: `${conf}%`, background: `var(${accent})` }}
              />
            </div>
          </div>
        )}

        {triggers.length > 0 && (
          <div className="trg">
            <div className="trg-h">Triggers fired</div>
            {triggers.map((t, i) => (
              <TriggerRow key={i} item={t} icon="◉" variant="hit" showGrade />
            ))}
          </div>
        )}

        {missing.length > 0 && (
          <div className="trg">
            <div className="trg-h">Still missing</div>
            {missing.map((m, i) => (
              <div className="trg-item miss" key={i}>
                <span className="ic">○</span>
                <span>{m}</span>
              </div>
            ))}
          </div>
        )}

        {(card.risk_signals ?? []).length > 0 && (
          <div className="trg">
            <div className="trg-h">Risk signals</div>
            {(card.risk_signals ?? []).map((r, i) => (
              <TriggerRow key={i} item={r} icon="▲" variant="warn" showGrade={false} />
            ))}
          </div>
        )}

        {card.counter_case && (
          <div className="counter">
            <b>Counter-case</b>
            {card.counter_case}
          </div>
        )}

        {(card.arm_until || card.exit_by) && (
          <div className="clocks">
            {card.arm_until && (
              // Armed: the entry window is an act-by deadline. Not-yet: there's no entry to act on,
              // so it's informational — the confirmation (market move) is just aging, not a go-cue.
              <div className={`clock-row entry${armed ? "" : " quiet"}`}>
                <span className="cd">{fmtDate(card.arm_until)}</span>
                <span className="x">
                  {armed ? "Entry window · confirmation clock" : "Confirmation clock"}
                  {armDays !== null &&
                    (armDays < 0
                      ? " · lapsed"
                      : armed
                        ? ` · act within ${armDays}d`
                        : ` · decays in ${armDays}d`)}
                </span>
              </div>
            )}
            {card.exit_by && (
              <div className="clock-row hold">
                <span className="cd">{fmtDate(card.exit_by)}</span>
                <span className="x">
                  Hold exit-by · conviction clock
                  {exitDays !== null && exitDays >= 0 && ` · ${exitDays}d`}
                </span>
              </div>
            )}
          </div>
        )}

        {/* the rail is state-appropriate (inverse loudness): only Armed shows the loud primary Act.
            A not-yet card shows the gate — the platform withholding its go-signal — and a logged
            early-entry override, not an act button. */}
        {armed ? (
          <div className="actions">
            <button className="btn primary" type="button">
              Act
            </button>
            <button className="btn" type="button">
              Override
            </button>
            <button className="btn" type="button">
              Snooze
            </button>
          </div>
        ) : managing ? (
          <div className="actions two">
            <button className="btn" type="button">
              Log exit
            </button>
            <button className="btn" type="button">
              Trail stop
            </button>
          </div>
        ) : (
          <div className="gate">
            <div className="gate-note">
              The gate is withholding the go-signal — friction, not a block. Enter early if you
              disagree; the override is logged.
            </div>
            <button className="btn wide" type="button">
              Override — enter early (logged)
            </button>
          </div>
        )}
        <div className="advisory">
          Advisory only — order routing never; the override log lands with the Scoreboard.
        </div>
      </div>
    </div>
  );
}

function Key({
  label,
  turned,
  detail,
  weak = false,
}: {
  label: string;
  turned: boolean;
  detail?: string | null;
  weak?: boolean;
}) {
  // `weak` = turned but only momentum-only (flip confirmation): amber, not green, so the loudest
  // element on the card doesn't overstate a starter.
  return (
    <div className={`key ${turned ? "on" : ""} ${weak ? "weak" : ""}`}>
      <div className="kh">
        <span className="dot" />
        {label}
      </div>
      <div className="kd">{detail}</div>
    </div>
  );
}

// One trigger / risk-signal row: ticker chip · label · (optional grade) · (optional source link). Triggers
// pass showGrade + the hit variant (◉); risk signals don't (warn variant, ▲). The single source for the row.
function TriggerRow({
  item,
  icon,
  variant,
  showGrade,
}: {
  item: TriggerLike;
  icon: string;
  variant: "hit" | "warn";
  showGrade: boolean;
}) {
  const url = (item.sources ?? []).find((s) => s.url)?.url;
  return (
    <div className={`trg-item ${variant}`}>
      <span className="ic">{icon}</span>
      <span>
        {item.ticker && <span className="trg-tk">{item.ticker}</span>}
        {item.label}
        {showGrade && item.grade && (
          <>
            {" · "}
            <span className={`grade ${gradeClass(item.grade)}`}>{item.grade.toUpperCase()}</span>
          </>
        )}
        {url && (
          <>
            {" "}
            <a href={url} target="_blank" rel="noreferrer">
              ↗ source
            </a>
          </>
        )}
      </span>
    </div>
  );
}
