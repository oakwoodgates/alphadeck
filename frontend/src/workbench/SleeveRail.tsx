import type { EtfHoldingOut, ScoredMemberOut } from "../api/hooks";
import { useEtfHoldings } from "../api/hooks";
import { archLabel, errText, sleevePriceLabel } from "./format";

/** One holding line: weight · ticker/name · the filing's identifier (some filers list equity holdings
 *  with no ticker at all — then name+CUSIP/ISIN IS the identity; shown, never dropped, #9). */
function HoldingLine({ h, held }: { h: EtfHoldingOut; held?: boolean }) {
  return (
    <li className={held ? "held" : undefined}>
      <span className="pct">{h.pct_val != null ? `${h.pct_val.toFixed(1)}%` : "—"}</span>
      {h.ticker && <b className="tk">{h.ticker}</b>}
      <span className="co">{h.name ?? "—"}</span>
      {!h.ticker && (h.cusip || h.isin) && (
        <span className="hid">{h.cusip ? `CUSIP ${h.cusip}` : `ISIN ${h.isin}`}</span>
      )}
      {held && <span aria-hidden="true">✓</span>}
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
 *  instantly without re-spending. */
export function SleeveRail({
  member,
  thesisId,
  asof,
}: {
  member: ScoredMemberOut;
  thesisId?: string;
  asof?: string;
}) {
  // `enabled:false` — the pull fires ONLY on the button's refetch() below (a deliberate operator click),
  // never on mount/selection. `asof` threads the Workbench's as-of so the filing picked is the one
  // KNOWABLE then (#1); the response labels the holdings' report-period vintage.
  const etfHoldings = useEtfHoldings(member.security_id, thesisId, asof);
  const hd = etfHoldings.data;
  const pull = () => {
    if (!etfHoldings.isFetching) void etfHoldings.refetch();
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
                    <HoldingLine key={h.security_id ?? i} h={h} />
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
