import type { FundMatchOut, FundOut } from "../api/hooks";

// The fund launch radar's shared bits (docs/FUND_RADAR.md): the stage chips and one card's match line. Quiet by
// design (inverse loudness): no red, no badge on every row. The tier is a small lowercase tag, never a color.

const STAGES = ["filed", "effective", "ticker", "listed"] as const;

const STAGE_TITLE: Record<(typeof STAGES)[number], string> = {
  filed: "a filing listed this fund as new",
  effective: "a later 485BPOS listed the fund (it went effective)",
  ticker: "SEC's fund ticker file showed a symbol (the night we first saw it)",
  listed: "Nasdaq Trader's directory lists the symbol as an ETF (the night we first saw it)",
};

/** The stages a fund has reached, as quiet chips (an unreached stage renders nothing); the current one is a
 *  touch stronger. Each chip's hover carries its date. */
export function StageChips({ stage }: { stage: FundOut["stage"] }) {
  return (
    <span className="fl-stages">
      {STAGES.map((s) => {
        const d = stage[s];
        if (!d) return null;
        return (
          <span
            key={s}
            className={`fl-stage${stage.current === s ? " cur" : ""}`}
            title={`${STAGE_TITLE[s]} — ${d}`}
          >
            {s}
          </span>
        );
      })}
    </span>
  );
}

/** One card's reads of one fund: the terms that hit (with the tier as a small tag) and the name hits (the
 *  company ticker, its link, and the token that matched). A recommendation (#10): it changes nothing. */
export function MatchLine({ m, showCard = true }: { m: FundMatchOut; showCard?: boolean }) {
  const terms = [...(m.signal_terms ?? []), ...(m.broad_terms ?? [])];
  return (
    <div className="radar-match fl-match">
      {showCard && <span className="rm-th">{m.thesis_name}</span>}
      {m.tier && (
        <>
          <span className="rm-terms">{terms.join(" · ")}</span>
          <span className="fl-tier" title={`theme read: a ${m.tier}-tier term hit the fund's name`}>
            {m.tier}
          </span>
        </>
      )}
      {(m.name_hits ?? []).map((h) => (
        <span
          className="fl-namehit"
          key={`${h.ticker}-${h.token}`}
          title={`the fund's name holds "${h.token}" (${h.via === "ticker" ? "the ticker" : "the company name"})`}
        >
          {h.ticker}
          {h.link ? ` · ${h.link}` : ""}
        </span>
      ))}
    </div>
  );
}
