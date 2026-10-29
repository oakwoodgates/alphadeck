import type {
  ChainMapCardsOut,
  ChainMapColumnOut,
  ChainMapCountsOut,
  ChainMapEdgeOut,
  ChainMapNodeOut,
  ChainMapOut,
  ChainMapRailOut,
} from "../useChainMap";

// Hand-built Chain Map payloads for the layout, view and component tests (no server).

export const node = (ticker: string, over: Partial<ChainMapNodeOut> = {}): ChainMapNodeOut => ({
  key: `k-${ticker}`,
  company_key: `s-${ticker}`,
  ticker,
  name: `${ticker} Corp`,
  security_id: `s-${ticker}`,
  links: ["Reactors"],
  role: null,
  business_type: "utilities",
  side_50d: "above",
  pct_vs_50d: 4.2,
  last_filing: { date: "2026-10-25", age_days: 3, form: "8-K", headline: "8-K · 9.01" },
  flag: null,
  thesis_fit: null,
  authored_by: null,
  matched_terms: [],
  ...over,
});

export const column = (
  key: string,
  kind: ChainMapColumnOut["kind"],
  label: string,
  nodes: ChainMapNodeOut[],
  descriptor: string | null = null,
): ChainMapColumnOut => ({ key, kind, label, descriptor, nodes });

export const rail = (side: "left" | "right", nodes: ChainMapNodeOut[]): ChainMapRailOut => ({
  side,
  label: side === "left" ? "what pays and funds" : "what reads across",
  nodes,
});

export const edgesOf = (rails: ChainMapRailOut[]): ChainMapEdgeOut[] =>
  rails.flatMap((r) =>
    r.nodes.map((n) => ({ node_key: n.key, role: n.role as NonNullable<ChainMapNodeOut["role"]>, side: r.side })),
  );

export const counts = (over: Partial<ChainMapCountsOut> = {}): ChainMapCountsOut => ({
  links_today: 2,
  old_links: 1,
  roster_rows: 11,
  resolved_rows: 10,
  unresolved_rows: 1,
  silent_rows: 3,
  companies: 11,
  on_rails: 4,
  untagged_silent: 1,
  roles: [
    { role: "payer", names: 1 },
    { role: "financier", names: 1 },
    { role: "direct_read", names: 1 },
    { role: "counter_case", names: 2 },
  ],
  with_side: 6,
  without_side: 5,
  with_filing: 5,
  without_filing: 6,
  ...over,
});

// the card: two real links, an old link, the quiet Context column; a payer and a financier on the left rail, a
// direct read and a counter-case on the right rail; SMR is a counter-case that also sits in a real link (a tag,
// no rail copy); RBOT has a flagged price history; UNR has no resolved security
const OKLO = node("OKLO", {
  thesis_fit: "builds small modular reactors",
  authored_by: "operator_edited",
  matched_terms: ["SMR", "reactor"],
});
const SMR = node("SMR", {
  role: "counter_case",
  side_50d: "below",
  pct_vs_50d: -1.5,
  last_filing: null,
  thesis_fit: "a rival design",
  authored_by: "system_drafted",
});
const RBOT = node("RBOT", { side_50d: null, pct_vs_50d: null, flag: "mixed-scale price history" });
const CCJ = node("CCJ", { links: ["Fuel"], side_50d: "at", pct_vs_50d: 0, business_type: "miner" });
const UNR = node("UNR", {
  links: ["Fuel"],
  company_key: "ticker:UNR",
  security_id: null,
  name: null,
  business_type: null,
  side_50d: null,
  pct_vs_50d: null,
  last_filing: null,
});
const OLDW = node("OLDW", { links: ["Old wires"], business_type: null, side_50d: null, pct_vs_50d: null });
const CTXN = node("CTXN", { links: ["Context"], business_type: "bank" });
const MSFT = node("MSFT", { role: "payer", links: ["Context"], name: "Microsoft" });
const BX = node("BX", { role: "financier", links: ["Context"] });
const DIRX = node("DIRX", { role: "direct_read", links: ["Bench"] });
const CNTR = node("CNTR", { role: "counter_case", links: ["Context"] });

export const COLUMNS: ChainMapColumnOut[] = [
  column("link:Reactors", "link", "Reactors", [OKLO, SMR, RBOT], "make the power"),
  column("link:Fuel", "link", "Fuel", [CCJ, UNR]),
  column("old:Old wires", "old_link", "Old wires", [OLDW]),
  column("silent:Context", "silent", "Context", [CTXN]),
];
export const RAILS: ChainMapRailOut[] = [rail("left", [MSFT, BX]), rail("right", [DIRX, CNTR])];
export const EDGES: ChainMapEdgeOut[] = edgesOf(RAILS);

export const payload = (over: Partial<ChainMapOut> = {}): ChainMapOut => ({
  asof: "2026-10-28",
  thesis_id: "t-nuke",
  thesis_name: "Nuclear",
  clock: "public",
  roster_fallback: false,
  columns: COLUMNS,
  rails: RAILS,
  edges: EDGES,
  flagged: [{ security_id: "s-RBOT", ticker: "RBOT", reason: "mixed-scale price history" }],
  counts: counts(),
  filing_days: 365,
  cache: "hit",
  compute_ms: 800,
  ...over,
});

export const CARDS: ChainMapCardsOut = {
  asof: "2026-10-28",
  live_cards: 3,
  not_yet_created: 0,
  cards: [
    { thesis_id: "t-copper", name: "Copper", links: 4 },
    { thesis_id: "t-grid", name: "Grid", links: 5 },
    { thesis_id: "t-nuke", name: "Nuclear", links: 2 },
  ],
  cache: "hit",
  compute_ms: 10,
};
