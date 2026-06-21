import { useCall, useThesis } from "../api/hooks";
import { CallCard } from "../components/CallCard";
import { MemberMenu } from "../components/MemberMenu";
import {
  accentVar,
  archLabel,
  daysFrom,
  fmtDate,
  STATE_CLASS,
  STATE_LABEL,
  tickerLabel,
} from "../util/format";

interface Props {
  thesisId: string;
  asof: string;
  onAsofChange: (asof: string) => void;
  onBack?: () => void;
}

export function Cockpit({ thesisId, asof, onAsofChange, onBack }: Props) {
  const thesisQ = useThesis(thesisId);
  const callQ = useCall(thesisId, asof);
  const thesis = thesisQ.data;
  const card = callQ.data;

  const state = card?.state ?? "incubating";
  const sc = STATE_CLASS[state] ?? "incub";

  const basket = thesis?.basket ?? [];
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
                <div className="sect-h">Basket · the expression</div>
                <table className="basket">
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Role</th>
                      <th>Archetype</th>
                      <th style={{ textAlign: "right" }}>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {basket.map((b, i) => (
                      <tr key={i}>
                        <td className="tk">{b.ticker}</td>
                        <td className="role">{b.role}</td>
                        <td>
                          <span className={`arch ${b.archetype}`}>
                            {archLabel(b.archetype)}
                          </span>
                        </td>
                        <td className="met">{b.detail ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              {evidence.length > 0 && (
                <section className="sect">
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

              {catalysts.length > 0 && (
                <section className="sect">
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
                </section>
              )}

              {killCriteria.length > 0 && (
                <section className="sect">
                  <div className="sect-h">Kill criteria</div>
                  {killCriteria.map((k) => (
                    <div className="kill" key={k.id}>
                      {k.text}
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </main>

        <aside className="cp-rail">
          {callQ.isLoading && <p className="muted">Computing the call…</p>}
          {callQ.error && <p style={{ color: "var(--neg)" }}>Failed to compute the call.</p>}
          {card && <CallCard card={card} />}
          {card && <MemberMenu card={card} />}
        </aside>
      </div>
    </div>
  );
}
