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

// The rail: the opinionated, auditable call. Recomputed live at `card.asof` (the read path).
export function CallCard({ card }: { card: CallCardResponse }) {
  const sc = STATE_CLASS[card.state] ?? "incub";
  const accent = accentVar(sc);
  const conf = Math.round(card.confidence * 100);
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
          />
        </div>

        <div className="conf">
          <div className="conf-row">
            <span>Confidence</span>
            <span style={{ color: `var(${accent})` }}>{conf}%</span>
          </div>
          <div className="conf-bar">
            <div className="conf-fill" style={{ width: `${conf}%`, background: `var(${accent})` }} />
          </div>
        </div>

        {triggers.length > 0 && (
          <div className="trg">
            <div className="trg-h">Triggers fired</div>
            {triggers.map((t, i) => {
              const url = (t.sources ?? []).find((s) => s.url)?.url;
              return (
                <div className="trg-item hit" key={i}>
                  <span className="ic">◉</span>
                  <span>
                    {t.label}
                    {t.grade && (
                      <>
                        {" · "}
                        <span className={`grade ${gradeClass(t.grade)}`}>{t.grade.toUpperCase()}</span>
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
            })}
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
            {(card.risk_signals ?? []).map((r, i) => {
              const url = (r.sources ?? []).find((s) => s.url)?.url;
              return (
                <div className="trg-item warn" key={i}>
                  <span className="ic">▲</span>
                  <span>
                    {r.label}
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
            })}
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
              <div className="clock-row entry">
                <span className="cd">{fmtDate(card.arm_until)}</span>
                <span className="x">
                  Entry window · confirmation clock
                  {armDays !== null && (armDays >= 0 ? ` · act within ${armDays}d` : " · lapsed")}
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
        <div className="advisory">Advisory only — order routing never; the override log lands with the Scoreboard.</div>
      </div>
    </div>
  );
}

function Key({ label, turned, detail }: { label: string; turned: boolean; detail?: string | null }) {
  return (
    <div className={`key ${turned ? "on" : ""}`}>
      <div className="kh">
        <span className="dot" />
        {label}
      </div>
      <div className="kd">{detail}</div>
    </div>
  );
}
