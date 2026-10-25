// The Splits cards in PLAIN ENGLISH (operator ask, 2026-10-24): what the split is, what each source says, and what
// each button does. Pure words over the GET /admin/splits payload — never a different decision, only how it is
// explained (backend: app/routers/admin_splits.py; the verbs: repositories/split_repo.py).
import type { AdminSplitListEntryOut } from "../api/hooks";

type Split = AdminSplitListEntryOut;
type Option = NonNullable<Split["confirm_options"]>[number];

const md = (d: string) => d.slice(5); // "2026-07-06" -> "07-06"
const n = (x: string | number) => Number(x).toLocaleString("en-US", { maximumFractionDigits: 6 });

/** "every 15 shares became 1" / "every share became 2" — old shares -> new shares. */
export function sharesPhrase(ratioFrom: string, ratioTo: string): string {
  const from = Number(ratioFrom);
  const to = Number(ratioTo);
  const before = from === 1 ? "every share" : `every ${n(from)} shares`;
  return `${before} became ${n(to)}`;
}

/** "1-for-15 reverse split: every 15 shares became 1; first traded 2026-07-06." */
export function splitSentence(s: Split): string {
  return `${s.label}: ${sharesPhrase(s.ratio_from, s.ratio_to)}; first traded on the new basis ${s.effective_date}.`;
}

/** What the SEC filing says, in a sentence. */
export function secSays(s: Split): string {
  const sec = s.sec;
  if (!sec) return "No SEC filing in the window states this split.";
  const form = sec.form ?? "filing";
  const filed = sec.filed ? ` filed ${sec.filed}` : "";
  if (!sec.ratio_label) {
    return `The SEC ${form}${filed} was found, but no single ratio could be read from it (${
      sec.abstain_reason ?? "no ratio read"
    }).`;
  }
  const date = sec.effective_date
    ? sec.date_kind === "trading"
      ? `, first trading on the new basis ${sec.effective_date}`
      : `, legally effective ${sec.effective_date} (the first trading day can differ)`
    : ", with no date stated";
  return `The SEC ${form}${filed} says ${sec.ratio_label}${date}.`;
}

/** What Polygon says, in a sentence. */
export function polygonSays(s: Split): string {
  const p = s.polygon;
  if (!p) return "Polygon lists no split here.";
  const what = p.ratio_label ?? `${p.split_from} → ${p.split_to}`;
  return `Polygon lists ${what}${p.execution_date ? `, first trading ${p.execution_date}` : ""}.`;
}

/** What Confirm does with one source's values. */
export function confirmLine(s: Split, o: Option, many: boolean): string {
  const whose = many ? `${o.source === "sec" ? "the SEC filing's" : "Polygon's"} values (${o.label})` : o.label;
  const moved =
    o.effective_date !== s.effective_date ? ` The split moves to ${o.effective_date}.` : "";
  return `Confirm ${whose}: prices before ${md(o.effective_date)} are put on the new scale, and the split-night hold on this company's price triggers lifts.${moved}`;
}

/** What Reject does. */
export function rejectLine(): string {
  return "Reject: no split; prices stay as stored. The split stays listed, grayed, and Restore brings it back.";
}
