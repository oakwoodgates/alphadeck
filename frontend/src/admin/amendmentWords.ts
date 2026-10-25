// The Insider amendments cards in PLAIN ENGLISH (operator ask, 2026-10-24): what the filer did, and what each
// button does to the rows a call reads. Pure functions over the GET /admin/insider-amendments payload — the
// backend computes every fact (the trade groups, each button's removes / adds from the resolver itself, whether a
// call can read a row); this module only words them. Nothing here decides anything.
import type {
  AdminInsiderActionOut,
  AdminInsiderAmendmentOut,
  AdminInsiderTradeGroupOut,
  AdminInsiderTxnOut,
} from "../api/hooks";

/** What a Form 4 transaction code means, as a verb phrase around "N shares" (SEC Form 4, Table I codes). */
const VERB: Record<string, (s: string) => string> = {
  P: (s) => `bought ${s}`,
  S: (s) => `sold ${s}`,
  A: (s) => `received ${s} as a grant or award`,
  F: (s) => `had ${s} withheld to pay taxes or an exercise price`,
  M: (s) => `acquired ${s} by exercising or converting`,
  X: (s) => `acquired ${s} by exercising`,
  C: (s) => `converted ${s}`,
  G: (s) => `gave ${s} as a gift`,
  D: (s) => `returned ${s} to the company`,
  J: (s) => `reported ${s} in another kind of transaction (code J)`,
  W: (s) => `acquired or disposed of ${s} by will`,
  I: (s) => `traded ${s} in a discretionary transaction`,
};

const NOUN: Record<string, [string, string]> = { P: ["buy", "buys"], S: ["sale", "sales"] };

const money = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`;
const shares = (v: number | null | undefined) => (v == null ? "an unstated number of" : v.toLocaleString("en-US"));
const price = (v: number) => `$${Number(v.toFixed(4))}`;

/** "sold 5,000 shares at $29.64 on 2025-03-18 ($148,200)" — one transaction code's rows, summed. */
export function groupPhrase(g: AdminInsiderTradeGroupOut): string {
  const code = g.txn_code ?? "?";
  const n = g.trades ?? 0;
  const what = `${shares(g.shares)} shares${n > 1 ? ` in ${n} trades` : ""}`;
  let out = (VERB[code] ?? ((s: string) => `reported ${s} (code ${code})`))(what);
  if (g.price_high != null && g.price_high > 0) {
    out +=
      g.price_low != null && g.price_low !== g.price_high
        ? ` at ${price(g.price_low)}–${price(g.price_high)}`
        : ` at ${price(g.price_high)}`;
  }
  if (g.first_date) {
    out += g.last_date && g.last_date !== g.first_date ? ` between ${g.first_date} and ${g.last_date}` : ` on ${g.first_date}`;
  }
  if ((code === "P" || code === "S") && g.usd != null) out += ` (${money(g.usd)})`;
  return out;
}

const who = (a: AdminInsiderAmendmentOut) => {
  const name = a.owner_name ?? (a.owners ?? []).join(" and ") ?? "";
  const owner = name || "The reporting owner";
  return a.owner_role ? `${owner} (${a.owner_role})` : owner;
};

/** The ONE sentence on what the filer did: who, bought / sold, how many shares, at what price, on what date, the
 *  dollar total — and, for a 4/A that reports no trades, what the Form 4 it amends reported. */
export function filerSentence(a: AdminInsiderAmendmentOut): string {
  const groups = a.trades ?? [];
  const filed = a.filed ? ` filed ${a.filed}` : "";
  if (groups.length) {
    // each buy / sale group states its own dollars; the total only adds something when there are several
    const signal = groups.filter((g) => g.txn_code === "P" || g.txn_code === "S").length;
    const total = a.usd_total != null && signal > 1 ? `, ${money(a.usd_total)} in buys and sales in all` : "";
    return `${who(a)} ${groups.map(groupPhrase).join("; ")}${total}, per this correction${filed}.`;
  }
  const original = (a.originals ?? []).find((o) => o.role === "original") ?? (a.originals ?? [])[0];
  const was = original?.trades?.length
    ? `; the Form 4 it amends${original.filed ? ` (filed ${original.filed})` : ""} said they ${original.trades
        .map(groupPhrase)
        .join("; ")}`
    : "";
  return `${who(a)} filed a correction${filed ? ` on ${a.filed}` : ""} that reports no trades${was}.`;
}

/** "a sale of 1,094 shares at $3.47 on 2026-10-02" / "3 sales (1,500 shares, 2026-10-02 to 2026-10-05)" */
export function rowsPhrase(rows: AdminInsiderTxnOut[]): string {
  const byCode = new Map<string, AdminInsiderTxnOut[]>();
  for (const r of rows) {
    const k = r.txn_code ?? "?";
    byCode.set(k, [...(byCode.get(k) ?? []), r]);
  }
  const parts: string[] = [];
  for (const [code, rs] of byCode) {
    const [one, many] = NOUN[code] ?? [`code-${code} trade`, `code-${code} trades`];
    if (rs.length === 1) {
      const r = rs[0];
      const at = r.price != null && r.price > 0 ? ` at ${price(r.price)}` : "";
      parts.push(`a ${one} of ${shares(r.shares)} shares${at}${r.txn_date ? ` on ${r.txn_date}` : ""}`);
    } else {
      const total = rs.reduce((s, r) => s + (r.shares ?? 0), 0);
      const dates = rs.map((r) => r.txn_date).filter((d): d is string => !!d).sort();
      const span = dates.length ? (dates[0] === dates[dates.length - 1] ? `, ${dates[0]}` : `, ${dates[0]} to ${dates[dates.length - 1]}`) : "";
      parts.push(`${rs.length} ${many} (${total.toLocaleString("en-US")} shares${span})`);
    }
  }
  return parts.join(" and ");
}

/** The calls' side of one button: what the read loses / gains, and whether a call can read it. */
export function effectPhrase(e: AdminInsiderActionOut | undefined): string {
  if (!e) return "";
  const removes = e.removes ?? [];
  const adds = e.adds ?? [];
  if (!removes.length && !adds.length) return "No buy or sale the calls read changes.";
  const parts: string[] = [];
  if (removes.length) parts.push(`the calls stop reading ${rowsPhrase(removes)}`);
  if (adds.length) parts.push(`${removes.length ? "and read" : "the calls read"} ${rowsPhrase(adds)}${removes.length ? " instead" : ""}`);
  const said = parts.join(" ");
  const tail = e.changes_call ? " This can change a call." : " No live call or edition page reads these, so no call changes.";
  return `${said.charAt(0).toUpperCase()}${said.slice(1)}.${tail}`;
}

/** What each button IS, before its effect (the verb in the operator's words). */
const INTRO: Record<string, string> = {
  apply: "Use the 4/A's trades in place of the whole original.",
  keep: "Ignore the 4/A; the original stands as filed.",
  add: "Keep every original trade and add the 4/A's trades beside them.",
  undo: "Back to how it was before your decision.",
  undo_pick: "Back to undecided: no filing is picked, and every candidate's trades stand as filed.",
};

/** One short line per button: what it is, then what it does to the calls. */
export function actionLine(a: AdminInsiderAmendmentOut, action: string, target?: string): string {
  const e = (a.actions ?? []).find((x) => x.action === action && (target == null || x.target === target));
  if (action === "link") {
    const o = (a.originals ?? []).find((x) => x.accession === target);
    const when = o?.filed ? `filed ${o.filed}` : `${target}`;
    const what = o?.trades?.length ? ` (${o.trades.map(groupPhrase).join("; ")})` : "";
    const rows = (a.trades ?? []).reduce((s, g) => s + (g.trades ?? 0), 0);
    const match =
      o?.matched_rows != null && rows ? ` ${o.matched_rows} of the 4/A's ${rows} trade(s) match it by shares and price.` : "";
    const then =
      e && ((e.removes ?? []).length || (e.adds ?? []).length)
        ? ` ${effectPhrase(e)}`
        : " Picking it changes nothing until you then choose what to do with it.";
    return `The Form 4 ${when}${what}.${match}${then}`;
  }
  return `${INTRO[action] ?? ""} ${effectPhrase(e)}`.trim();
}

/** The system's pick (rule 2b), in words. */
export function pickSentence(a: AdminInsiderAmendmentOut): string | null {
  const p = a.pick;
  if (!p) return null;
  const when = p.filed ? `the Form 4 filed ${p.filed}` : `Form 4 ${p.target}`;
  const why = `every one of this 4/A's ${p.rows} trade(s) matches it by shares and price, and no other filing's do`;
  return p.standing
    ? `The system picked ${when}: ${why}.`
    : `The next nightly pass picks ${when}: ${why}.`;
}
