import { useRadarSpac, useSpacAttach, useSpacCatalyst, type SpacEventOut } from "../api/hooks";
import { errText } from "../workbench/format";

interface Props {
  onBack: () => void;
  onOpenWorkbench: () => void;
  onOpenScoreboard: () => void;
  onOpenAdmin: () => void;
}

// The deal-state chip copy — the honest frame (Rev 2): a DA is a LEAD to watch, not a live name.
// "searching" is the quiet default and renders plain; the three transitions get labeled chips.
const STATE_LABEL: Record<SpacEventOut["deal_state"], string> = {
  searching: "searching",
  announced: "deal announced",
  terminated: "deal terminated",
  completed: "combination completed",
};

/** The SPAC Radar (docs/temp/spac-radar-options.md slices 1+2): the blank-check transition tape —
 *  quiet, pull-only (#7), every row linked to its filing (#6). Matches are recommendations (#10):
 *  nothing changes until the operator clicks; add ⇄ remove is a reversible pair (#1). */
export function Radar({ onBack, onOpenWorkbench, onOpenScoreboard, onOpenAdmin }: Props) {
  const q = useRadarSpac(90);
  const attach = useSpacAttach();
  const catalyst = useSpacCatalyst();
  const events = q.data?.events ?? [];

  return (
    <div className="board-shell adm-shell">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <nav className="nav">
          <a onClick={onBack}>Board</a>
          <a onClick={onOpenWorkbench}>Workbench</a>
          <a onClick={onOpenScoreboard}>Scoreboard</a>
          <a className="on">Radar</a>
          <a onClick={onOpenAdmin}>Admin</a>
        </nav>
        <div className="spacer" />
        {/* no as-of dial: a "now" watch surface, like Admin */}
      </header>

      <main className="radar-main">
        <div className="sect">
          <div className="sect-h">
            SPAC Radar{" "}
            <em>
              · blank-check transition filings — a deal announcement is a lead to watch, not a live
              name (pre-liquidity target; deals can die before close)
            </em>
          </div>
          {q.data && (
            <div className="note">
              {q.data.shells_known} shells known · filings from the last {q.data.window_days} days ·
              fed nightly by the cron (backfill: <code>python -m pipeline.spac_radar --days 30</code>
              )
            </div>
          )}
          {q.isLoading && <div className="note">Loading…</div>}
          {q.error != null && <div className="note err">radar unreachable: {errText(q.error)}</div>}
          {!q.isLoading && !q.error && events.length === 0 && (
            <div className="note">
              No transition filings in the window yet — the tape fills as the nightly scan runs.
            </div>
          )}
          {attach.error != null && (
            <div className="note err">add/remove failed: {errText(attach.error)}</div>
          )}
          {catalyst.error != null && (
            <div className="note err">catalyst prefill failed: {errText(catalyst.error)}</div>
          )}
          <div className="radar-tape">
            {events.map((e) => (
              <RadarRow key={e.accession} e={e} attach={attach} catalyst={catalyst} />
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}

function RadarRow({
  e,
  attach,
  catalyst,
}: {
  e: SpacEventOut;
  attach: ReturnType<typeof useSpacAttach>;
  catalyst: ReturnType<typeof useSpacCatalyst>;
}) {
  const matches = e.matches ?? [];
  const holders = e.in_basket_of ?? [];
  return (
    <div className="radar-row">
      <div className="radar-top">
        <span className="radar-date">{e.filed}</span>
        <span className="radar-ticker">{e.ticker ?? "—"}</span>
        <span className="radar-co">{e.company_name}</span>
        <span className="idchip" title="the filing's form type (+ 8-K item codes when resolvable)">
          {e.form}
          {e.items && e.items.length > 0 ? ` · ${e.items.join(" ")}` : ""}
        </span>
        <span className={`radar-state ${e.deal_state}`}>{STATE_LABEL[e.deal_state]}</span>
        <a className="radar-link" href={e.url} target="_blank" rel="noreferrer">
          filing ↗
        </a>
      </div>
      {/* per-thesis matches — recommendations (#10): visible, act only on a click. Honest loudness:
          rows with no match carry no controls at all. */}
      {matches.length > 0 && (
        <div className="radar-matches">
          {matches.map((m) => {
            const inBasket = holders.includes(m.thesis_id);
            return (
              <div className="radar-match" key={m.thesis_id}>
                <span className="rm-th">matches {m.thesis_name}</span>
                <span className="rm-terms">
                  {[...m.signal_terms, ...m.broad_terms].join(" · ")}
                  {m.truncated ? " · (doc capped before matching)" : ""}
                </span>
                {e.security_id != null && (
                  <button
                    type="button"
                    className="wb-mini"
                    disabled={attach.isPending}
                    title={
                      inBasket
                        ? "remove from the basket — returns to the prior state (reversible)"
                        : "add to the thesis basket as an uncharacterized member — the finalize rail characterizes it"
                    }
                    onClick={() =>
                      attach.mutate({ thesis_id: m.thesis_id, cik: e.cik, detach: inBasket })
                    }
                  >
                    {inBasket ? "✓ in basket — remove" : `+ add to ${m.thesis_name}`}
                  </button>
                )}
                <button
                  type="button"
                  className="wb-mini ghost"
                  disabled={catalyst.isPending}
                  title="prefill a display-calendar catalyst (the events you're watching) — never an auto conviction fact"
                  onClick={() =>
                    catalyst.mutate({
                      thesisId: m.thesis_id,
                      label: `${e.ticker ?? e.company_name} combination vote`,
                    })
                  }
                >
                  + vote catalyst
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
