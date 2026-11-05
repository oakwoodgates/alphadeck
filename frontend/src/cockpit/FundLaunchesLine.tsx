import { useState } from "react";

import { useFundAttach, useFundLaunches, useIngestPrices, type FundLaunchesOut } from "../api/hooks";
import { MatchLine, StageChips } from "../radar/fundParts";
import { errText } from "../workbench/format";

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
        title="fund filings whose name fits this card — a recommendation to look, never a signal"
      >
        {open ? "▾" : "▸"} fund launches · {c.filings} filings from {c.sponsors} sponsors in{" "}
        {d.window_days} days (signal {c.signal_filings} · broad {c.broad_filings} · name{" "}
        {c.name_filings})
      </button>
      {attach.error != null && (
        <div className="note err">add/remove failed: {errText(attach.error)}</div>
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
