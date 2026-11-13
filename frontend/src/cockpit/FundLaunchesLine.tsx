import { useState } from "react";

import { useFundAttach, useFundLaunches, useIngestPrices, type FundLaunchesOut } from "../api/hooks";
import { refusalText } from "../api/refusals";
import { MatchLine, StageChips } from "../radar/fundParts";
import { errText } from "../workbench/format";

// The API's fixed 404 when a plain user's attach names a fund the shared company list does not hold yet (only a
// steward adds to the list; security hardening PR 1a). It is an answer, not a breakage, so it reads on its own.
const NOT_IN_LIST = "This fund is not in the company list yet. A steward can add it.";

/** The card's quiet fund-launch line (docs/FUND_RADAR.md), near the basket's silent ETFs link: how many fund
 *  filings fit this card in the window, counted by FILING and by SPONSOR (not by fund, so one batch cannot look
 *  like a wave), split by tier. Collapsed by default; its own query, so it never slows the card; renders NOTHING
 *  when there are none (honest loudness). Display only: a recommendation, never a call input (#4). */
export function FundLaunchesLine({
  thesisId,
  asof,
  readOnly = false,
}: {
  thesisId: string;
  asof: string;
  readOnly?: boolean;
}) {
  const q = useFundLaunches(thesisId, asof);
  const d = q.data;
  if (!d || d.counts.filings === 0) return null;
  return <FundLaunchesBody thesisId={thesisId} d={d} readOnly={readOnly} />;
}

function FundLaunchesBody({
  thesisId,
  d,
  readOnly,
}: {
  thesisId: string;
  d: FundLaunchesOut;
  readOnly: boolean;
}) {
  const [open, setOpen] = useState(false);
  const attach = useFundAttach();
  const ingestPx = useIngestPrices();
  const c = d.counts;

  const add = (symbol: string) =>
    attach.mutate(
      { thesis_id: thesisId, symbol },
      {
        onSuccess: (r) => {
          // the sleeve shows its price (the existing decoupled price leg), as "Surface an ETF" does
          if (r.added && r.security_id) ingestPx.mutate(r.security_id);
        },
      },
    );
  const remove = (symbol: string) => attach.mutate({ thesis_id: thesisId, symbol, detach: true });

  return (
    <div className="fl-line">
      <button
        type="button"
        className="fl-line-h"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title={
          "fund filings whose name fits this card — a recommendation to look, never a signal" +
          (d.symbol_history_since
            ? ` · tickers and listings tracked since ${d.symbol_history_since}`
            : "")
        }
      >
        {open ? "▾" : "▸"} fund launches · {c.filings} filings from {c.sponsors} sponsors in{" "}
        {d.window_days} days (signal {c.signal_filings} · broad {c.broad_filings} · name{" "}
        {c.name_filings})
      </button>
      {attach.error != null &&
        (errText(attach.error) === NOT_IN_LIST ? (
          <div className="note err">{NOT_IN_LIST}</div>
        ) : (
          <div className="note err">add/remove failed: {errText(attach.error)}</div>
        ))}
      {refusalText(ingestPx.error) && (
        // the fund's price pull was refused (security hardening PR 1b: today's price-pull allowance) — as worded
        <div className="note err">{refusalText(ingestPx.error)}</div>
      )}
      {open && (
        <div className="fl-line-list">
          {d.filings.map((f) => (
            <div className="fl-line-filing" key={f.accession}>
              <div className="fl-line-filing-h">
                <span className="radar-date">{f.public_date}</span>
                <span className="fl-trust">{f.trust}</span>
                <span className="idchip">{f.form}</span>
                <a className="radar-link" href={f.url} target="_blank" rel="noreferrer">
                  filing ↗
                </a>
              </div>
              {f.funds.map((fund) => {
                const symbol = fund.stage.ticker ? (fund.symbols ?? [])[0] : undefined;
                return (
                  <div className="fl-fund" key={fund.key}>
                    <div className="fl-fund-top">
                      <span className="radar-co">{fund.name}</span>
                      <StageChips stage={fund.stage} />
                      {symbol &&
                        (fund.in_etfs ? (
                          <button
                            type="button"
                            className="wb-mini"
                            disabled={readOnly || attach.isPending}
                            title="remove it from the card's ETFs link — returns to the prior state (reversible)"
                            onClick={() => remove(symbol)}
                          >
                            ✓ in ETFs — remove
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="wb-mini"
                            disabled={readOnly || attach.isPending}
                            title={`add ${symbol} to the card's ETFs link (a silent link: it never counts toward the call)`}
                            onClick={() => add(symbol)}
                          >
                            + add to ETFs
                          </button>
                        ))}
                    </div>
                    {(fund.matches ?? []).map((m) => (
                      <MatchLine key={m.thesis_id} m={m} showCard={false} />
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
