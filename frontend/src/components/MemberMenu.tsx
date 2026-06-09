import type { CallCardResponse } from "../api/hooks";
import { fmtDate, gradeClass, verdictLabel } from "../util/format";

type Member = CallCardResponse["armed_members"][number];

// M5 Part A — the per-member ranked menu for a THEME. Calm by design (inverse loudness): loudness stays on
// the Decision Queue's single headline; this is a quiet ranked list. A single-name thesis IS its headline
// CallCard above, so the menu only earns its place when there's more than one moving name.
export function MemberMenu({ card }: { card: CallCardResponse }) {
  const armed = card.armed_members;
  const watch = card.watch_members;
  if (armed.length <= 1 && watch.length === 0) return null;

  return (
    <section className="member-menu">
      <div className="mm-h">Basket · ranked</div>
      {armed.map((m, i) => (
        <ArmedRow key={m.security_id} m={m} headline={i === 0} />
      ))}
      {watch.length > 0 && (
        <div className="mm-watch">
          <div className="mm-sub">Moving · no conviction yet — watch</div>
          {watch.map((m) => (
            <div className="mm-row watch" key={m.security_id}>
              <span className="mm-tk">{m.ticker ?? "◇"}</span>
              <span className="mm-note">moving, no conviction yet</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ArmedRow({ m, headline }: { m: Member; headline: boolean }) {
  return (
    <div
      className={`mm-row armed${headline ? " headline" : ""}${m.lapsing ? " lapsing" : ""}${
        m.theme_armed ? " theme-armed" : ""
      }`}
    >
      <span className="mm-tk">{m.ticker ?? "◇"}</span>
      <span className={`grade ${gradeClass(m.entry_grade)}`}>{verdictLabel(m.verdict ?? "")}</span>
      {/* M5b: armed on the THEME conviction (a fallback, capped at a starter) vs on its own signal */}
      {m.theme_armed ? (
        <span
          className="mm-theme"
          title="Armed on the theme conviction (a fallback) — capped at a starter, not its own signal"
        >
          theme
        </span>
      ) : (
        m.conviction_grade && <span className="mm-conv">{m.conviction_grade} thesis</span>
      )}
      <span className="mm-runway">
        {m.lapsing ? "lapses " : "runway to "}
        {fmtDate(m.exit_by)}
      </span>
    </div>
  );
}
