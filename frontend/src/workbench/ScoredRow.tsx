import { type MouseEvent, useState } from "react";

import type { EtfHoldingOut, ScoredMemberOut } from "../api/hooks";
import { useAutoConfirmShares, useEtfHoldings, useExtract, useIngestPrices } from "../api/hooks";
import { Meter } from "./Meter";
import {
  archLabel,
  errText,
  formatMarketCap,
  memberHasFundamentals,
  onFileValues,
  sharesAsof,
  sleevePriceLabel,
  staleSharesMonths,
} from "./format";

interface Props {
  member: ScoredMemberOut;
  selected: boolean;
  onSelect: () => void;
  // the active thesis — threaded to the extract so the row's "get data" and the rail's FactsPanel share ONE
  // query (same key, same grounded-purity behavior). Optional: an un-wired/test render omits it.
  thesisId?: string;
  // the scored as-of — used only to age the ratified share count behind the market cap (the ENDV stale-shares
  // flag). Optional: an un-wired/test render omits it (then no age check runs).
  asof?: string;
}

/** One scored basket member: ticker + archetype + market-cap figure, then the four meters. purity /
 *  runway / catalysts cluster as the "goodness" group; dilution sits after a faint separator as the
 *  ember RISK axis (the pressure meter), so it can't be misread as a fourth goodness meter.
 *
 *  GATE 2 of the three-gate TRIAGE flow — "mark for data": the ⇣ get-data control fires THIS ONE name's
 *  extraction (the existing per-name endpoint; 2–4 EDGAR requests, cache-first). The mark and the spend
 *  collapse into one deliberate click — cost is the operator's to spend, never ambient. It shares the
 *  FactsPanel's query (same key), so a fetched row's candidates render in the rail instantly. The control
 *  then tracks what is LEFT to ratify and disappears only when nothing remains. Failures are per-name +
 *  retryable.
 *
 *  Get-data also AUTO-APPLIES an AUTO (unflagged) shares count — removing a ceremonial confirm, not a real
 *  one: nobody knows a share count by heart, so confirming the extractor's cover figure only rubber-stamped
 *  it. The server owns the number and the AUTO gate (see `useAutoConfirmShares`); a FLAGged count still goes
 *  to the operator, and the market cap is the real check. */
/** One holding line in the sleeve's drawer: weight · ticker/name · the filing's identifier (some
 *  filers list equity holdings with no ticker at all — then name+CUSIP/ISIN IS the identity). */
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

export function ScoredRow({ member, selected, onSelect, thesisId, asof }: Props) {
  const extract = useExtract(member.security_id, thesisId);
  // the fund sleeve's holdings drawer (ETF Sleeve, Slice 2a) — `enabled:false`: the N-PORT pull fires
  // ONLY on the drawer toggle below (a deliberate operator click, the cost thread), never on render
  const [holdingsOpen, setHoldingsOpen] = useState(false);
  const etfHoldings = useEtfHoldings(member.security_id, thesisId, asof);
  // ENDV finding (display-only): a ratified share count from a stale cover (an old/delinquent filer) yields a
  // plausible-but-wrong market cap with no age signal. Light a WARM flag ONLY when the count is > ~6 months
  // old (honest loudness — the common current count shows nothing). No signal touched; just the eye's catch.
  const staleMonths = asof ? staleSharesMonths(sharesAsof(member), asof) : null;
  // the surgical get-data pulls the FULL per-name set: extraction candidates + EOD price bars (the
  // decoupled price leg) — same completeness as the section button, one name at a time
  const ingestPx = useIngestPrices();
  const autoShares = useAutoConfirmShares();
  const loaded = memberHasFundamentals(member);
  // "data ready" means there are CANDIDATES to ratify. An empty extract now says WHICH nothing
  // (Retrieval Slice 1 — the three empty states): `no-annual-filing` = genuinely nothing on EDGAR
  // (the only case where the old "no filings" read was true); `cover-not-located` = an annual filing
  // exists but its cover couldn't be read — the name is UNREAD, not empty, and stays a visible
  // candidate for the next pass (interaction #2). A foreign 20-F/40-F filer with a readable cover now
  // yields a FLAG shares candidate like any other name.
  const facts = extract.data?.facts;
  const hasCandidates = (facts?.length ?? 0) > 0;
  const emptyReason = extract.data !== undefined && !hasCandidates ? extract.data.empty_reason : null;
  // The candidates STILL needing the operator, i.e. fetched but with no ratified fact of that type yet. This
  // is the control's real subject. It used to key off `loaded` (ANY confirmed fact), which was already a bug —
  // ratifying one fact hid the control while purity/cash were still outstanding — and auto-applying shares
  // would have INDUSTRIALIZED it: every clean name would self-confirm its shares and instantly go quiet with
  // two facts unratified. Counting what's left keeps the name honestly surfaced until it's actually done.
  const onFile = onFileValues(member);
  const remaining = (facts ?? []).filter((f) => !onFile[f.fact_type]);
  const getData = async () => {
    ingestPx.mutate(member.security_id);
    const res = await extract.refetch();
    // Auto-apply the AUTO shares count — bound to THIS explicit get-data, never a render (the query cache can
    // already hold candidates, and a fact must never be written just by looking at a name). We gate on the
    // candidate's tier to skip a pointless call; the SERVER re-verifies AUTO and owns the number. `mutate`
    // (not mutateAsync) is non-throwing: a failed auto-apply leaves today's manual confirm exactly as it is.
    // An annual-cover shares candidate is ALWAYS "flag", so a dark name can never take this branch.
    const shares = res?.data?.facts?.find((f) => f.fact_type === "shares_outstanding");
    if (shares?.tier === "auto") autoShares.mutate(member.security_id);
  };

  // A `fund` sleeve is an EXPRESSION, not a scored equity (ETF Sleeve, Slice 1; #4/#6). It has no insider /
  // dilution / purity / runway data, so the four equity meters would be all-blank noise and "get data" would
  // 404 on extraction (a fund's cik is None). Render the sleeve honestly: ticker + "ETF sleeve" label + its
  // PRICE (context, never a signal) — no meters, no get-data, no stale-shares flag. (All hooks above run
  // regardless — they're inert here: useExtract is disabled, the mutations never fire — so rules-of-hooks
  // hold.)
  if (member.archetype === "fund") {
    const hd = etfHoldings.data;
    const toggleHoldings = (e: MouseEvent) => {
      e.stopPropagation();
      const opening = !holdingsOpen;
      setHoldingsOpen(opening);
      // fire the pull on the FIRST open only (the deliberate click IS the spend); a re-open re-shows
      // the cached answer, the retry button below re-spends explicitly
      if (opening && hd === undefined && !etfHoldings.isFetching) void etfHoldings.refetch();
    };
    return (
      <div className={`nmrow fund${selected ? " sel" : ""}`} onClick={onSelect}>
        <div className="top">
          <button type="button" className="nmrow-sel" onClick={onSelect}>
            <span className="tk">{member.ticker ?? "◇"}</span>
            {member.name && <span className="co">{member.name}</span>}
            <span className="arch fund">{archLabel("fund")}</span>
          </button>
          <button
            type="button"
            className="wb-getdata"
            aria-expanded={holdingsOpen}
            aria-label={`${holdingsOpen ? "hide" : "show"} holdings for ${member.ticker ?? "this sleeve"}`}
            title="pull this ETF's latest N-PORT holdings (SEC, quarter-end, ~60 days lagged) and see which are already in your basket — one deliberate pull, cached after"
            onClick={toggleHoldings}
          >
            {holdingsOpen ? "⌄ holdings" : "› holdings"}
          </button>
          <span className="cap">
            <small>sleeve price</small>
            {sleevePriceLabel(member)}
          </span>
        </div>
        <div className="wb-meters fund-note">
          <span className="fit">
            ETF sleeve — a low-torque expression of the thesis; the equity meters don’t apply
          </span>
        </div>
        {holdingsOpen && (
          <div className="wb-holdings" onClick={(e) => e.stopPropagation()}>
            {etfHoldings.isFetching ? (
              <span className="wb-h-note">pulling N-PORT holdings…</span>
            ) : etfHoldings.error ? (
              <button
                type="button"
                className="wb-getdata err"
                title={`couldn't pull holdings — ${errText(etfHoldings.error)}; click to retry`}
                onClick={(e) => {
                  e.stopPropagation();
                  void etfHoldings.refetch();
                }}
              >
                ⚠ retry holdings
              </button>
            ) : hd ? (
              <>
                <div className="wb-h-sum">
                  <span>
                    {hd.holdings_count} holdings · {hd.held.length} in basket ·{" "}
                    {hd.available.length} in master · {hd.unresolved.length} unresolved
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
              </>
            ) : null}
          </div>
        )}
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
          {/* archetype chip only when DECIDED (item F: unset renders nothing here — quiet; the ✦ dot +
              the rail carry the pending decision, so an all-unset fresh basket isn't a wall of chips) */}
          {member.archetype && (
            <span className={`arch ${member.archetype}`}>{archLabel(member.archetype)}</span>
          )}
          {member.archetype_hint && member.archetype_hint !== member.archetype && (
            <span
              className="arch-rec-dot"
              title={`figures suggest ${archLabel(member.archetype_hint)} — open the name to apply`}
            >
              ✦
            </span>
          )}
        </button>
        <span className="cap">
          <small>mkt cap</small>
          {formatMarketCap(member.market_cap.value)}
          {staleMonths != null && (
            <span
              className="wb-stale-shares"
              title={`the share count behind this cap is from a filing cover ~${staleMonths} months old — an old or delinquent filer. The cap could be materially wrong; verify the current count before trusting it.`}
            >
              ⚠ shares ~{staleMonths}mo old
            </span>
          )}
        </span>
        {/* gate 2 — per-name, opt-in, visible cost. The control now tracks what's LEFT to ratify (not merely
            "any fact confirmed"), so an auto-applied shares count can't silence a name whose purity/cash are
            still outstanding. Hidden only when nothing remains: fully ratified, or never fetched + loaded. */}
        {extract.isFetching ? (
          <span className="wb-getdata busy">extracting…</span>
        ) : remaining.length > 0 ? (
          <button
            type="button"
            className="wb-getdata ready"
            aria-label={`data ready for ${member.ticker ?? "name"} — open to ratify`}
            title={`${remaining.length} candidate${remaining.length > 1 ? "s" : ""} still to ratify (${remaining
              .map((f) => f.fact_type)
              .join(" · ")}) — open the name and ratify them in the rail`}
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
          >
            ✓ data ready — ratify {remaining.length}
          </button>
        ) : loaded ? null : (
          <>
            {emptyReason === "no-annual-filing" ? (
            // honest-loudness: fetched and GENUINELY empty — no 10-K/10-Q and no 20-F/40-F either
            // (a brand-new or non-reporting listing); retrying won't help — say why, quietly
            <span
              className="wb-getdata none"
              title="nothing on EDGAR the extractor can read — no 10-K/10-Q and no annual foreign filing (20-F/40-F) on file for this issuer"
            >
              — nothing on EDGAR
            </span>
          ) : emptyReason === "cover-not-located" ? (
            // DISTINCT from genuinely-empty (interaction #2): the annual filing EXISTS but its cover
            // couldn't be read this pass — the name is UNREAD, not empty; it stays a visible candidate
            <span
              className="wb-getdata unread"
              title="this issuer's annual filing (20-F/40-F) is on file, but its cover share count couldn't be read this pass — unread, not empty; it stays a candidate for a future extractor pass"
            >
              ◌ annual filing unread
            </span>
          ) : extract.error ? (
            <button
              type="button"
              className="wb-getdata err"
              aria-label={`retry get data for ${member.ticker ?? "name"}`}
              title={`couldn't extract — ${errText(extract.error)}; click to retry`}
              onClick={(e) => {
                e.stopPropagation();
                getData();
              }}
            >
              ⚠ retry get data
            </button>
          ) : (
            <button
              type="button"
              className="wb-getdata"
              aria-label={`get data for ${member.ticker ?? "name"}`}
              title="pull this name's latest SEC filings (10-Q/10-K; a foreign filer's 20-F/40-F cover for shares + statements for cash/runway) + its EOD price bars — cache-first, 2–4 EDGAR requests; the candidates land in the rail for you to ratify"
              onClick={(e) => {
                e.stopPropagation();
                getData();
              }}
            >
              ⇣ get data
            </button>
            )}
          </>
        )}
        {/* the price leg's own failure — visible per name, non-blocking (extraction may still be fine) */}
        {ingestPx.isError && (
          <span
            className="wb-getdata err"
            title={`price pull failed — ${errText(ingestPx.error)}; get data again to retry`}
          >
            ⚠ price
          </span>
        )}
      </div>
      <div className="wb-meters">
        <Meter label="purity" figure={member.purity} />
        <Meter label="runway" figure={member.runway} />
        <Meter label="catalysts" figure={member.catalysts} />
        <span className="wb-meter-sep" aria-hidden="true" />
        <Meter label="dilution" figure={member.dilution} risk />
        <span className="fit">{member.fit}</span>
      </div>
    </div>
  );
}
