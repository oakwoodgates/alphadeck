import { useState } from "react";

import { useRadarFunds, type FundFilingOut } from "../api/hooks";
import { errText } from "../workbench/format";
import { MatchLine, StageChips } from "./fundParts";

/** The fund launch tape (docs/FUND_RADAR.md): one row per FILING, newest first. Display only, pull-only (#7), every
 *  row links its filing (#6). Defaults keep it quiet: filings with a fund that fits a live card, with the rest one
 *  click away and COUNTED (keep-it-visible), and old confidential filings SEC released held back and counted. A
 *  batch filing (dozens of single-stock wrappers) is one collapsed row, opened on click. */
export function FundLaunches() {
  const [showAll, setShowAll] = useState(false);
  const [includePrivate, setIncludePrivate] = useState(false);
  const q = useRadarFunds({ days: 90, includePrivate, matchedOnly: !showAll });
  const d = q.data;
  const counts = d?.counts;

  return (
    <div className="sect">
      <div className="sect-h">
        Fund launches{" "}
        <em>
          · new fund filings on EDGAR, the night they go public — a recommendation to look, never a signal
        </em>
      </div>
      {d && counts && (
        <div className="note">
          {counts.filings} filings · {counts.funds} funds in the last {d.window_days} days ·{" "}
          {counts.matched_funds} fit a live card · stages are the nights we first saw each fact
        </div>
      )}
      {d && counts && (
        <div className="fl-toggles">
          <label>
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />{" "}
            show all ({counts.unmatched_funds} unmatched)
          </label>
          {(counts.private_held_back_filings > 0 || includePrivate) && (
            <label>
              <input
                type="checkbox"
                checked={includePrivate}
                onChange={(e) => setIncludePrivate(e.target.checked)}
              />{" "}
              include old filings SEC released ({counts.private_held_back_funds})
            </label>
          )}
        </div>
      )}
      {q.isLoading && <div className="note">Loading…</div>}
      {q.error != null && <div className="note err">fund tape unreachable: {errText(q.error)}</div>}
      {d && d.filings.length === 0 && (
        <div className="note">
          {showAll
            ? "No fund filings in the window yet — the tape fills as the nightly scan runs."
            : "No filing in the window has a fund that fits a live card."}
        </div>
      )}
      <div className="radar-tape">
        {(d?.filings ?? []).map((f) => (
          <FilingRow key={f.accession} f={f} />
        ))}
      </div>
    </div>
  );
}

function FilingRow({ f }: { f: FundFilingOut }) {
  const [open, setOpen] = useState(false);
  const batch = f.funds.length > 1;
  const single = f.funds[0];
  const cardNames = [...new Set(f.funds.flatMap((fund) => (fund.matches ?? []).map((m) => m.thesis_name)))];
  return (
    <div className="radar-row fl-row">
      <div className="radar-top">
        <span className="radar-date">{f.public_date}</span>
        {batch ? (
          <button
            type="button"
            className="fl-batch"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            title="a batch filing — open to list its funds"
          >
            {open ? "▾" : "▸"} {f.funds.length} funds
          </button>
        ) : (
          <span className="radar-co">{single.name}</span>
        )}
        <span className="fl-trust">{f.trust}</span>
        <span className="idchip" title="the filing's form type">
          {f.form}
        </span>
        {f.private_to_public && (
          <span className="fl-old" title="an old confidential filing SEC released; counted from its release day">
            released
          </span>
        )}
        {!batch && <StageChips stage={single.stage} />}
        <a className="radar-link" href={f.url} target="_blank" rel="noreferrer">
          filing ↗
        </a>
      </div>
      {!batch && (single.matches ?? []).length > 0 && (
        <div className="radar-matches">
          {(single.matches ?? []).map((m) => (
            <MatchLine key={m.thesis_id} m={m} />
          ))}
        </div>
      )}
      {batch && !open && cardNames.length > 0 && (
        <div className="fl-fits">fits {cardNames.join(" · ")}</div>
      )}
      {batch && open && (
        <div className="fl-funds">
          {f.funds.map((fund) => (
            <div className="fl-fund" key={fund.key}>
              <div className="fl-fund-top">
                <span className="radar-co">{fund.name}</span>
                <StageChips stage={fund.stage} />
              </div>
              {(fund.matches ?? []).map((m) => (
                <MatchLine key={m.thesis_id} m={m} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
