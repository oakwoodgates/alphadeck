import type { CallCardResponse } from "../api/hooks";
import { fmtDate, gradeClass, verdictLabel } from "../util/format";

type Member = CallCardResponse["armed_members"][number];

// M5 Part A — the per-member ranked menu for a THEME. Calm by design (inverse loudness): loudness stays on
// the Decision Queue's single headline; this is a quiet ranked list. A single-name thesis IS its headline
// CallCard above, so the menu only earns its place when there's more than one moving name.
export function MemberMenu({
  card,
  selectedKey,
  onSelectMember,
}: {
  card: CallCardResponse;
  /** Currently inspected member key (ticker or security_id) for subtle highlight when wired in Cockpit. */
  selectedKey?: string | null;
  /** When provided, clicking any armed or watch row opens that name's CompanyDrawer in place. */
  onSelectMember?: (key: string) => void;
}) {
  const armed = card.armed_members;
  const watch = card.watch_members;
  if (armed.length <= 1 && watch.length === 0) return null;

  const isSelected = (m: Member) =>
    Boolean(
      selectedKey &&
        (selectedKey === m.security_id ||
          (m.ticker && selectedKey.toLowerCase() === m.ticker.toLowerCase())),
    );

  return (
    <section className="member-menu">
      <div className="mm-h">Basket · ranked</div>
      {armed.map((m, i) => (
        <ArmedRow
          key={m.security_id}
          m={m}
          headline={i === 0}
          selected={isSelected(m)}
          onSelect={onSelectMember ? () => onSelectMember(m.ticker ?? m.security_id) : undefined}
        />
      ))}
      {watch.length > 0 && (
        <div className="mm-watch">
          <div className="mm-sub">Moving · no conviction yet — watch</div>
          {watch.map((m) =>
            onSelectMember ? (
              <button
                type="button"
                className={`mm-row mm-btn watch${isSelected(m) ? " sel" : ""}`}
                key={m.security_id}
                onClick={() => onSelectMember(m.ticker ?? m.security_id)}
              >
                <span className="mm-tk">{m.ticker ?? "◇"}</span>
                <span className="mm-note">moving, no conviction yet</span>
              </button>
            ) : (
              <div className="mm-row watch" key={m.security_id}>
                <span className="mm-tk">{m.ticker ?? "◇"}</span>
                <span className="mm-note">moving, no conviction yet</span>
              </div>
            ),
          )}
        </div>
      )}
    </section>
  );
}

function ArmedRow({
  m,
  headline,
  selected,
  onSelect,
}: {
  m: Member;
  headline: boolean;
  selected: boolean;
  onSelect?: () => void;
}) {
  const cls = `mm-row armed${headline ? " headline" : ""}${m.lapsing ? " lapsing" : ""}${
    m.theme_armed ? " theme-armed" : ""
  }${onSelect ? " mm-btn" : ""}${selected ? " sel" : ""}`;
  const content = (
    <>
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
        m.conviction_grade && <span className="mm-conv">{m.conviction_grade} setup</span>
      )}
      {/* Q14: the ENTRY window's own flag (few trading sessions left through arm_until) — distinct
          from `lapsing` below, which is the HOLD clock. */}
      {m.entry_window_closing && (
        <span
          className="mm-ewc"
          title="entry window closing — the last trading sessions in which this entry signal is valid"
        >
          entry window closing
        </span>
      )}
      <span className="mm-runway">
        {m.lapsing ? "hold clock closing · " : "runway to "}
        {fmtDate(m.exit_by)}
      </span>
    </>
  );
  if (onSelect) {
    return (
      <button type="button" className={cls} onClick={onSelect}>
        {content}
      </button>
    );
  }
  return <div className={cls}>{content}</div>;
}
