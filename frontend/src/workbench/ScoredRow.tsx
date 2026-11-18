import type { ContextRole, ScoredMemberOut, UserCompanyValuesOut } from "../api/hooks";
import { Meter } from "./Meter";
import {
  CONTEXT_ROLE_DESCRIPTION,
  annualSharesTitle,
  contextRoleTag,
  formatMarketCap,
  runwayNotApplicable,
  runwayPaceWords,
  runwayTitle,
  sharesAsof,
  sharesSource,
  sleevePriceLabel,
  staleSharesMonths,
} from "./format";

/** The hover on a market cap or runway left blank because the value awaits the steward (nothing applied). */
const AWAITING_HOVER = "awaiting the steward";

interface Props {
  member: ScoredMemberOut;
  selected: boolean;
  onSelect: () => void;
  // the scored as-of — used only to age the share count behind the market cap (the ENDV stale-shares flag).
  // Optional: an un-wired/test render omits it (then no age check runs).
  asof?: string;
  // this company's shared values as the user reads them (V2 W5e), joined by the parent off the card's one read:
  // used ONLY for the quiet "awaiting the steward" hover on a blank cap / runway. Optional: absent = no hover.
  values?: UserCompanyValuesOut | null;
  // the operator's context role for this company, passed by the parent off the thesis spine by security_id
  // (`ScoredMemberOut` itself carries none). Shown as a quiet lowercase tag ONLY when set — never a "—"
  // placeholder (WB#3). Display metadata only, never a call input (#4).
  contextRole?: ContextRole | null;
}

/** One scored basket member: ticker + name + market-cap figure, then the four meters. purity /
 *  runway / catalysts cluster as the "goodness" group; dilution sits after a faint separator as the
 *  ember RISK axis (the pressure meter), so it can't be misread as a fourth goodness meter.
 *
 *  V2 W5e: there is NO get-data chore here (and no "data ready" / "retry get data"): a share count and cash are the
 *  platform's, shared by every user, and read (with their state) in the rail's Shared values block; purity is the
 *  user's own, set in the rail. The row stays quiet: a cap or runway left blank because its value awaits the steward
 *  carries that in its hover, and nothing more. */
export function ScoredRow({ member, selected, onSelect, asof, contextRole, values }: Props) {
  // ENDV finding (display-only): a share count from a stale cover (an old/delinquent filer) yields a
  // plausible-but-wrong market cap with no age signal. Light a WARM flag ONLY when the count is > ~6 months
  // old (honest loudness — the common current count shows nothing). An ANNUAL figure (a 20-F/40-F cover) is always
  // months old and is quiet inside its 400-day cap; its date rides the hover instead. No signal touched.
  const staleMonths = asof ? staleSharesMonths(sharesAsof(member), asof, sharesSource(member)) : null;
  const annualTitle = asof ? annualSharesTitle(member, asof) : null;
  // a blank cap / runway because the value waits on the steward (nothing applied): "—" with the hover. An applied
  // value with a newer filing waiting keeps its figure (the rail's block carries the pending line).
  const capAwaiting = member.market_cap.value == null && values?.shares.state === "awaiting_steward";
  // a company a runway means nothing for (V2 C1): a plain "n/a", the sentence on hover, ahead of any other state
  const runwayNa = runwayNotApplicable(member.runway);
  const runwayAwaiting =
    runwayNa == null && member.runway.pips == null && values?.cash_burn.state === "awaiting_steward";
  const runwayPace = runwayPaceWords(member.runway);

  // the quiet context-role tag (set only; the hover is the role's description) — beside the name in both rows
  const roleTag = contextRole ? (
    <span className="role-tag" title={CONTEXT_ROLE_DESCRIPTION[contextRole]}>
      {contextRoleTag(contextRole)}
    </span>
  ) : null;

  // A `fund` sleeve is an EXPRESSION, not a scored equity (ETF Sleeve; #4/#6). It has no insider / dilution
  // / purity / runway data, so the four equity meters would be all-blank noise. Render the row honestly: ticker
  // + "ETF sleeve" label + its PRICE (context, never a signal) — no meters, no stale-shares flag. Selecting it
  // drives the DD rail (SleeveRail) exactly like a scored name: its N-PORT holdings + basket overlap live THERE
  // now, not in an inline drawer that shoved the basket down.
  if (member.instrument_kind === "etf") {
    return (
      <div className={`nmrow fund${selected ? " sel" : ""}`} onClick={onSelect}>
        <div className="top">
          <button type="button" className="nmrow-sel" onClick={onSelect}>
            <span className="tk">{member.ticker ?? "◇"}</span>
            {member.name && <span className="co">{member.name}</span>}
            <span className="btype bt-etf">ETF sleeve</span>
            {roleTag}
          </button>
          <span className="cap">
            <small>sleeve price</small>
            {sleevePriceLabel(member)}
          </span>
        </div>
        <div className="wb-meters fund-note">
          <span className="fit">
            ETF sleeve — a low-torque expression of the thesis; open it for holdings &amp; basket overlap →
          </span>
        </div>
      </div>
    );
  }

  return (
    // the row DIV owns the whole-surface click; the ticker block below is the real <button> (the
    // accessible select target) — a nested-button structure would be invalid HTML, hence the split.
    <div className={`nmrow${selected ? " sel" : ""}`} onClick={onSelect}>
      <div className="top">
        <button type="button" className="nmrow-sel" onClick={onSelect}>
          <span className="tk">{member.ticker ?? "◇"}</span>
          {/* the company NAME rides the row (joined from the master on read) — a ticker-only list made
              the finalize pass a memory quiz over 68 four-letter codes */}
          {member.name && <span className="co">{member.name}</span>}
          {roleTag}
          {/* no type chip on the list row — the identity chips (sector) already say what it is, and
              the rail's business-type head is the one decision surface (quiet list, loud exception) */}
        </button>
        <span className="cap">
          <small>mkt cap</small>
          <span title={capAwaiting ? AWAITING_HOVER : (annualTitle ?? undefined)}>
            {formatMarketCap(member.market_cap.value)}
          </span>
          {staleMonths != null && (
            <span
              className="wb-stale-shares"
              title={`the share count behind this cap is from a filing cover ~${staleMonths} months old — an old or delinquent filer. The cap could be materially wrong; verify the current count before trusting it.`}
            >
              ⚠ shares ~{staleMonths}mo old
            </span>
          )}
        </span>
      </div>
      <div className="wb-meters">
        <Meter label="purity" figure={member.purity} />
        <Meter
          label="runway"
          figure={member.runway}
          title={runwayAwaiting ? AWAITING_HOVER : runwayTitle(member.runway)}
          notApplicable={runwayNa != null ? "n/a" : undefined}
        />
        {runwayPace?.caveat && (
          // a quiet mark ONLY where last quarter is far off the year-to-date pace (honest loudness), the caveat on hover
          <span className="wb-pace" data-testid="wb-runway-pace" title={runwayPace.caveat}>
            uneven quarter
          </span>
        )}
        <Meter label="catalysts" figure={member.catalysts} />
        <span className="wb-meter-sep" aria-hidden="true" />
        <Meter label="dilution" figure={member.dilution} risk />
        <span className="fit">{member.fit}</span>
      </div>
    </div>
  );
}
