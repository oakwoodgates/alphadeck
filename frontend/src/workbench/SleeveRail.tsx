import type { ReactNode } from "react";

import type { EtfHoldingOut, ScoredMemberOut } from "../api/hooks";
import { useEtfHoldings } from "../api/hooks";
import { archLabel, errText, sleevePriceLabel } from "./format";

/** One holding line: weight · ticker/name · the filing's identifier (some filers list equity holdings
 *  with no ticker at all — then name+CUSIP/ISIN IS the identity; shown, never dropped, #9). `action` is
 *  an optional trailing control (the include button on an available row), right-aligned. */
function HoldingLine({ h, held, action }: { h: EtfHoldingOut; held?: boolean; action?: ReactNode }) {
  return (
    <li className={held ? "held" : undefined}>
      <span className="pct">{h.pct_val != null ? `${h.pct_val.toFixed(1)}%` : "—"}</span>
      {h.ticker && <b className="tk">{h.ticker}</b>}
      <span className="co">{h.name ?? "—"}</span>
      {!h.ticker && (h.cusip || h.isin) && (
        <span className="hid">{h.cusip ? `CUSIP ${h.cusip}` : `ISIN ${h.isin}`}</span>
      )}
      {held && <span aria-hidden="true">✓</span>}
      {action}
    </li>
  );
}

/** The DD rail for a `fund` sleeve (ETF Sleeve) — the sleeve's dossier in the SAME right-rail real estate
 *  a scored name uses, so "click a row → its evidence in the panel" holds for the sleeve too (it used to
 *  expand a drawer INLINE in the scored list, shoving the basket down). A fund is an EXPRESSION, not a
 *  scored equity (#4/#6): no meters, no extract/ratify — its dossier is its identity + price (context,
 *  never a signal) + its N-PORT holdings mapped against the basket (the three-bucket partition:
 *  held / available / unresolved — nothing dropped, #9).
 *
 *  The holdings pull is a LIVE SEC read (N-PORT + OpenFIGI), so it sits behind an explicit button — the
 *  cost thread, the same deliberate-spend gate as a name's "get data" — never auto-fired on selection.
 *  Once pulled it's cached per sleeve (the query key carries security_id), so re-selecting shows it
 *  instantly without re-spending.
 *
 *  Slice 2b — the INCLUDE button: an "available" holding (in your master, not this basket) gets a one-click
 *  "+ include" that adds it to the thesis basket (the ETF surfaces names you're missing; you add them
 *  without leaving the sleeve). Reversible (#1): once in the basket the button flips to "✓ included" and
 *  clicking it REMOVES the member — returning to the prior state, never destroying data. The state is read
 *  off the LIVE basket (`basketSids`), so an include never forces an expensive re-pull of the holdings. */
export function SleeveRail({
  member,
  thesisId,
  asof,
  basketSids,
  onInclude,
  onRemove,
  includePending,
}: {
  member: ScoredMemberOut;
  thesisId?: string;
  asof?: string;
  // the LIVE thesis basket's security_ids — an available holding whose id is here has been included; its
  // button becomes the reversible "✓ included" (remove). Read off the basket, not a re-pull (cost thread).
  basketSids?: Set<string>;
  onInclude?: (securityId: string, ticker: string) => void;
  onRemove?: (securityId: string) => void;
  includePending?: boolean;
}) {
  // `enabled:false` — the pull fires ONLY on the button's refetch() below (a deliberate operator click),
  // never on mount/selection. `asof` threads the Workbench's as-of so the filing picked is the one
  // KNOWABLE then (#1); the response labels the holdings' report-period vintage.
  const etfHoldings = useEtfHoldings(member.security_id, thesisId, asof);
  const hd = etfHoldings.data;
  const pull = () => {
    if (!etfHoldings.isFetching) void etfHoldings.refetch();
  };
  // The include/remove control for ONE available holding — rendered only when the caller wired the write
  // path (`onInclude`). An available holding always carries a security_id (that IS what matched it to the
  // master); the guard keeps TS happy and degrades to no-button for a read-only render.
  const includeAction = (h: EtfHoldingOut): ReactNode => {
    const sid = h.security_id;
    if (!sid || !onInclude) return null;
    if (basketSids?.has(sid)) {
      return (
        <button
          type="button"
          className="wb-h-inc in"
          disabled={includePending}
          title="in your basket — click to remove (undo the include)"
          onClick={() => onRemove?.(sid)}
        >
          ✓ included
        </button>
      );
    }
    // an available holding always carries a ticker (that IS what matched it to the master); the guard keeps
    // the promote member's ticker non-null and no-ops the impossible tickerless case.
    const tk = h.ticker;
    if (!tk) return null;
    return (
      <button
        type="button"
        className="wb-h-inc"
        disabled={includePending}
        title="add this name to the thesis basket"
        onClick={() => onInclude(sid, tk)}
      >
        + include
      </button>
    );
  };
  return (
    <div className="ddcard sleeve">
      <div className="dd-head">
        <span className="tk">{member.ticker ?? "◇"}</span>
        <span className="arch fund">{archLabel("fund")}</span>
      </div>
      <div className="dd-body">
        {member.name && (
          <div className="dd-ident">
            <span className="dd-co">{member.name}</span>
          </div>
        )}
        <div className="dd-facts">
          <span>
            <b>sleeve price</b>
            {sleevePriceLabel(member)}
          </span>
        </div>
        <p className="dd-thesis-fit muted">
          A low-torque expression of the thesis — price is context, never a signal, and the equity
          meters don’t apply.
        </p>

        <div className="dd-sub">Fund holdings · basket overlap</div>
        {hd ? (
          <div className="wb-holdings">
            <div className="wb-h-sum">
              <span>
                {hd.holdings_count} holdings · {hd.held.length} in basket · {hd.available.length} in
                master · {hd.unresolved.length} unresolved
              </span>
              <a
                href={hd.source_ref}
                target="_blank"
                rel="noreferrer"
                title="the N-PORT filing this list traces to"
              >
                N-PORT{hd.report_date ? ` as-of ${hd.report_date}` : ""} ↗
              </a>
            </div>
            {hd.held.length > 0 && (
              <>
                <div className="wb-h-group">✓ already in your basket</div>
                <ul>
                  {hd.held.map((h, i) => (
                    <HoldingLine key={h.security_id ?? i} h={h} held />
                  ))}
                </ul>
              </>
            )}
            {hd.available.length > 0 && (
              <>
                <div className="wb-h-group">in the master — not in this basket</div>
                <ul>
                  {hd.available.map((h, i) => (
                    <HoldingLine key={h.security_id ?? i} h={h} action={includeAction(h)} />
                  ))}
                </ul>
              </>
            )}
            {hd.unresolved.length > 0 && (
              <>
                <div
                  className="wb-h-group"
                  title="no master match — this filer lists these without a ticker identifier (or they're foreign lines); shown, never dropped"
                >
                  unresolved — no master match
                </div>
                <ul>
                  {hd.unresolved.map((h, i) => (
                    <HoldingLine key={`${h.name ?? "u"}-${i}`} h={h} />
                  ))}
                </ul>
              </>
            )}
          </div>
        ) : etfHoldings.isFetching ? (
          <span className="wb-h-note">pulling N-PORT holdings…</span>
        ) : (
          <button
            type="button"
            className={`wb-getdata${etfHoldings.error ? " err" : ""}`}
            title={
              etfHoldings.error
                ? `couldn't pull holdings — ${errText(etfHoldings.error)}; click to retry`
                : "pull this ETF's latest N-PORT holdings (SEC, quarter-end, ~60 days lagged) and see which are already in your basket — one deliberate pull, cached after"
            }
            onClick={pull}
          >
            {etfHoldings.error ? "⚠ retry holdings" : "⌾ pull holdings"}
          </button>
        )}
      </div>
    </div>
  );
}
