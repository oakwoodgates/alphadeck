import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { apiErrorText } from "../api/errorText";
import {
  useRaiseFlag,
  useUserCompanyValues,
  useWithdrawFlag,
  type MyFlagOut,
  type UserValueOut,
} from "../api/hooks";
import { LIMITS } from "../api/limits";
import { useIsSteward } from "../auth/me";
import { useAppClock } from "../clock";
import { ExternalLink } from "../components/ExternalLink";
import { LocatedPassages } from "../components/LocatedPassages";
import {
  FACT_HEADING,
  figureWords,
  filingPhrase,
  fmtDay,
  lowerFirst,
  outcomeIsLoud,
  outcomeWords,
  paceLine,
  sentence,
  shortDay,
} from "../components/valueWords";
import { companyFactsPath } from "../nav";
import { fmtDate, isPastAsof } from "../util/format";

const FLAG_PLACEHOLDER = "What looks wrong? (optional)";
const FLAG_PRIVACY = "Only you and the steward see this. It does not change the value.";

/** The Workbench rail's SHARED VALUES block (V2 W5e): a company's share count and cash/burn as the platform holds
 *  them, READ-ONLY, one small quiet block per value. They are shared by every user, set by the platform or approved
 *  by the steward; a user never writes one. A value that looks wrong is FLAGGED (it tells the steward and changes
 *  nothing; withdrawing is the visible inverse). The steward gets no Flag and no form here: a link to the steward's
 *  page instead.
 *
 *  A user NEVER sees an unverified number: a value awaiting the steward shows only its filing (link, date) and the
 *  filing's own passage; the API sends no figure and this adds none. The block is CURRENT state: on a dated view it
 *  says so, and never explains an old blank meter with today's state. Every note (the user's own, the steward's
 *  outcome) is plain text (React text nodes). */
export function SharedValues({ securityId, asof }: { securityId: string; asof?: string }) {
  const q = useUserCompanyValues(securityId);
  const steward = useIsSteward();
  const { currentNight } = useAppClock();
  const dated = asof != null && isPastAsof(asof, currentNight);
  const data = q.data;

  return (
    <div className="sv" data-testid="shared-values">
      <div className="dd-sub">
        Shared values <em>— current</em>
      </div>
      {dated && asof && (
        <div className="note sv-dated">
          This is the current state, not the state on {fmtDate(asof)}. Today&apos;s values do not explain that
          date&apos;s meters.
        </div>
      )}
      {q.isLoading && <div className="note">Reading the shared values…</div>}
      {q.error != null && !data && (
        <div className="note">{apiErrorText(q.error) ?? "Couldn't read the shared values."}</div>
      )}
      {data && (
        <>
          <ValueBlock value={data.shares} securityId={securityId} canFlag={!steward} />
          <ValueBlock value={data.cash_burn} securityId={securityId} canFlag={!steward} />
        </>
      )}
      {steward && data && (
        <div className="sv-steward">
          <Link to={companyFactsPath(securityId, null)}>Open on the steward&apos;s page ↗</Link>
        </div>
      )}
    </div>
  );
}

const Sep = () => <span className="sv-sep"> · </span>;

function Filing({ url, children }: { url: string | null | undefined; children: ReactNode }) {
  // the link text carries the arrow only when there is a link to follow (ExternalLink renders bare text otherwise)
  return url ? (
    <ExternalLink className="sv-link" href={url}>
      {children} ↗
    </ExternalLink>
  ) : (
    <>{children}</>
  );
}

function ValueBlock({
  value,
  securityId,
  canFlag,
}: {
  value: UserValueOut;
  securityId: string;
  canFlag: boolean;
}) {
  const [passageOpen, setPassageOpen] = useState(false);
  const heading = FACT_HEADING[value.fact_type];
  const applied = value.value;
  const pace = paceLine(value);
  const caveat = value.value?.pace?.caveat ?? null;

  let line: ReactNode;
  if (value.state === "from_filing" || value.state === "verified") {
    const figure = figureWords(value);
    const verified = value.state === "verified";
    line = (
      <>
        {figure && (
          <>
            <Sep />
            {figure}
          </>
        )}
        {verified && (
          <>
            <Sep />
            {lowerFirst(value.label)}
          </>
        )}
        <Sep />
        {value.annual_line ? (
          <Filing url={applied?.source_url}>{value.annual_line}</Filing>
        ) : (
          <>
            {!verified && "from the "}
            <Filing url={applied?.source_url}>{filingPhrase(applied?.source, applied?.valid_from)}</Filing>
          </>
        )}
      </>
    );
  } else if (value.state === "awaiting_steward") {
    line = (
      <>
        <Sep />
        {value.label}
      </>
    );
  } else {
    line = (
      <>
        <Sep />
        {sentence(value.label)}
      </>
    );
  }

  const awaiting = value.state === "awaiting_steward" ? value.awaiting : null;
  const passages = (awaiting?.located_passages ?? []).map((p) => ({
    kind: p.kind,
    anchor: p.anchor,
    excerpt: p.excerpt,
    source_ref: p.url ?? "",
  }));

  return (
    <div className="sv-block" data-testid={`sv-${value.fact_type}`}>
      <div className="sv-line">
        <b className="sv-h">{heading}</b>
        {line}
      </div>
      {awaiting && (
        <div className="sv-sub">
          {awaiting.line}{" "}
          <Filing url={awaiting.source_url}>
            {awaiting.event_date ? `the filing dated ${fmtDay(awaiting.event_date)}` : "the filing"}
          </Filing>
          {(passages.length > 0 || awaiting.passages_unreadable > 0) && (
            <div className="sv-passage">
              {passages.length > 0 && (
                <button type="button" className="wb-mini ghost" onClick={() => setPassageOpen((v) => !v)}>
                  {passageOpen ? "Hide the filing passage" : "Show the filing passage"}
                </button>
              )}
              <LocatedPassages passages={passages} show={passageOpen} />
              {awaiting.passages_unreadable > 0 && (
                <div className="sv-sub">
                  {awaiting.passages_unreadable === 1
                    ? "One stored passage could not be read."
                    : `${awaiting.passages_unreadable} stored passages could not be read.`}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {pace && (
        <div className="dd-pace" data-testid="sv-pace">
          {pace}
        </div>
      )}
      {caveat && (
        <div className="dd-pace" data-testid="sv-pace-caveat">
          {caveat}
        </div>
      )}
      {value.pending && (
        <div className="sv-sub sv-pending">
          {sentence(value.pending.line)} <Filing url={value.pending.source_url}>the newer filing</Filing>
        </div>
      )}
      <FlagControl value={value} securityId={securityId} canFlag={canFlag} />
    </div>
  );
}

/** What a closed flag leaves behind: the steward's outcome (loud for 14 days after the close, computed from
 *  `closed_at`; then quiet) or the user's own withdrawal. */
function FlagHistory({ flag, today }: { flag: MyFlagOut; today: string }) {
  const words = outcomeWords(flag);
  if (words) {
    const loud = outcomeIsLoud(flag, today);
    return (
      <div
        className={`sv-outcome${loud ? " loud" : ""}`}
        data-testid="sv-outcome"
        data-loud={loud ? "true" : "false"}
      >
        {words}
      </div>
    );
  }
  if (flag.outcome === "withdrawn") {
    return <div className="sv-sub">Withdrawn. You can flag it again.</div>;
  }
  return null;
}

function FlagControl({
  value,
  securityId,
  canFlag,
}: {
  value: UserValueOut;
  securityId: string;
  canFlag: boolean;
}) {
  const raise = useRaiseFlag();
  const withdraw = useWithdrawFlag();
  const { marketToday } = useAppClock();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const flag = value.my_flag ?? null;
  const openFlag = flag != null && flag.closed_at == null;

  // the steward corrects on the company page and gets no Flag
  if (!canFlag) return null;

  const send = () => {
    const text = note.trim();
    raise.mutate(
      { security_id: securityId, value_type: value.fact_type, ...(text ? { note: text } : {}) },
      {
        onSuccess: () => {
          setOpen(false);
          setNote("");
        },
      },
    );
  };

  return (
    <div className="sv-flag">
      {flag && !openFlag && <FlagHistory flag={flag} today={marketToday} />}
      {openFlag && flag ? (
        <>
          <div className="sv-sub">
            You flagged this on {shortDay(flag.opened_at)}. The steward will look at it.{" "}
            <button
              type="button"
              className="wb-mini ghost"
              disabled={withdraw.isPending}
              onClick={() => withdraw.mutate({ flagId: flag.id })}
            >
              Withdraw
            </button>
          </div>
          {flag.note && <div className="sv-sub">Your note: {flag.note}</div>}
          {withdraw.isError && (
            <div className="note err">{apiErrorText(withdraw.error) ?? "Couldn't withdraw the flag."}</div>
          )}
        </>
      ) : open ? (
        <div className="sv-flagform">
          <input
            type="text"
            aria-label={FLAG_PLACEHOLDER}
            placeholder={FLAG_PLACEHOLDER}
            maxLength={LIMITS.prose}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button type="button" className="wb-mini" disabled={raise.isPending} onClick={send}>
            {raise.isPending ? "Sending…" : "Send to the steward"}
          </button>
          <button
            type="button"
            className="wb-mini ghost"
            onClick={() => {
              setOpen(false);
              setNote("");
            }}
          >
            Cancel
          </button>
          <div className="sv-sub">{FLAG_PRIVACY}</div>
          {raise.isError && (
            <div className="note err">{apiErrorText(raise.error) ?? "Couldn't send the flag."}</div>
          )}
        </div>
      ) : (
        <button type="button" className="sv-flaglink" onClick={() => setOpen(true)}>
          Flag
        </button>
      )}
    </div>
  );
}
