import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import {
  usePlatformQueue,
  type PlatformQueueItemOut,
  type PlatformQueueValueOut,
  type PlatformQueuePen,
} from "../api/hooks";
import { labelCurrency } from "../components/currency";
import { ASOF, companyFactsPath, validAsof } from "../nav";
import { errText } from "../workbench/format";
import { FactSearch } from "./FactSearch";
import { FACT_TITLE, PINNED_FLAG, day, valueWords } from "./factWords";

// The Shared facts card on Admin (V2 W4c): which companies' share count or cash/burn awaits the steward, and a box to
// open any company. A READ only: it writes nothing (the writes live on the company page, and they refresh this queue).
// Honest loudness: nothing waiting is one calm line and no list; only the count of companies that need a decision is
// loud; the companies that sit only in a Discovered pen are folded behind a gray count, reversible in one click, and
// are not even asked for until opened. The rows keep the server's order (no client re-sort).

export interface OtherWork {
  /** The noun for one, and for any other number ("split to review" / "splits to review"). */
  one: string;
  many: string;
  /** The `data-testid` of the Admin panel the link scrolls to. */
  target: string;
  count: number;
}

const PAGE = 50; // the page size usePlatformQueue asks for
const DAY_MS = 86_400_000;

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** How long a company has waited, from its oldest waiting candidate: "waiting 3 days". */
export function waitedWords(sinceIso: string, now: number = Date.now()): string {
  const since = Date.parse(sinceIso);
  if (Number.isNaN(since)) return "waiting";
  const days = Math.floor(Math.max(0, now - since) / DAY_MS);
  if (days < 1) return "waiting less than a day";
  return `waiting ${days} ${plural(days, "day", "days")}`;
}

const flagWords = (f: string) => (f === PINNED_FLAG ? "held by your pin" : f);

/** One waiting value in words. A flagged candidate with nothing shown: its raw value, date and flags. A newer flag
 *  beside a value that is shown: said so, with the filing's date. */
export function waitingWords(factType: "shares_outstanding" | "cash_burn", w: PlatformQueueValueOut): string {
  const title = FACT_TITLE[factType].toLowerCase();
  const c = w.candidate;
  if (w.value != null) {
    return `${title}: a newer filing${c.event_date ? ` (${day(c.event_date)})` : ""} waits beside the value shown`;
  }
  let out = `${title}: ${valueWords(factType, c, labelCurrency(c.statement_currency, c.flags))}`;
  if (c.event_date) out += `, dated ${day(c.event_date)}`;
  const flags = (c.flags ?? []).map(flagWords);
  if (flags.length) out += `, flagged ${flags.join(", ")}`;
  return out;
}

function Row({ item, asof, now }: { item: PlatformQueueItemOut; asof: string | null; now: number }) {
  const waits: string[] = [];
  if (item.shares) waits.push(waitingWords("shares_outstanding", item.shares));
  if (item.cash_burn) waits.push(waitingWords("cash_burn", item.cash_burn));
  return (
    <li
      className={`sf-row${item.pen_only ? " sf-pen" : ""}`}
      data-testid="sf-row"
      data-pen={item.pen_only ? "true" : undefined}
    >
      <Link to={companyFactsPath(item.security_id, asof)} className="sf-link">
        <span className="sf-who">
          <b>{item.ticker ?? "—"}</b> {item.name ?? ""}
        </span>
        <span className="sf-cards">
          on {item.cards} {plural(item.cards, "card", "cards")}
        </span>
        <span className="sf-what">{waits.join(" · ")}</span>
        <span className="sf-wait" title={`since ${day(item.waiting_since)}`}>
          {waitedWords(item.waiting_since, now)}
        </span>
      </Link>
    </li>
  );
}

/** Fifty to a page: "1–50 of 120" with Previous and Next. Absent when one page holds everything. */
function Pager({ offset, total, onOffset }: { offset: number; total: number; onOffset: (o: number) => void }) {
  if (total <= PAGE) return null;
  const last = Math.min(offset + PAGE, total);
  return (
    <div className="sf-pager">
      <button
        type="button"
        className="adm-minibtn"
        disabled={offset <= 0}
        onClick={() => onOffset(Math.max(0, offset - PAGE))}
      >
        Previous
      </button>
      <span className="adm-sub" data-testid="sf-range">
        {offset + 1}–{last} of {total}
      </span>
      <button type="button" className="adm-minibtn" disabled={last >= total} onClick={() => onOffset(offset + PAGE)}>
        Next
      </button>
    </div>
  );
}

/** One page of the queue under one pen setting: its rows and its pager. A page the data moved out from under (the
 *  last row was just decided) steps back one page rather than showing nothing. */
function QueueList({
  pen,
  total,
  enabled,
  asof,
}: {
  pen: PlatformQueuePen;
  total: number;
  enabled: boolean;
  asof: string | null;
}) {
  const [offset, setOffset] = useState(0);
  const q = usePlatformQueue(offset, pen, enabled);
  const items = q.data?.items ?? [];
  useEffect(() => {
    if (q.data && items.length === 0 && offset > 0) setOffset(Math.max(0, offset - PAGE));
  }, [q.data, items.length, offset]);
  if (q.isError) {
    return (
      <div className="adm-err" data-testid="sf-error">
        {errText(q.error)}
      </div>
    );
  }
  if (!q.data) return q.isLoading ? <div className="adm-sub">Reading the queue…</div> : null;
  const now = Date.now();
  return (
    <>
      <ul className="sf-list" data-testid={pen === "only" ? "sf-pen-list" : "sf-queue"}>
        {items.map((it) => (
          <Row key={it.security_id} item={it} asof={asof} now={now} />
        ))}
      </ul>
      <Pager offset={offset} total={total} onOffset={setOffset} />
    </>
  );
}

function scrollToPanel(testId: string) {
  const el = document.querySelector(`[data-testid="${testId}"]`);
  if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function SharedFacts({ other = [] }: { other?: OtherWork[] }) {
  const [sp] = useSearchParams();
  const asof = validAsof(sp.get(ASOF));
  const [showPen, setShowPen] = useState(false);
  // The first read excludes the Discovered-pen-only companies; `total` and `pen_only_total` do not change with the
  // page or `pen`, so what is waiting beyond the pens is their difference.
  const head = usePlatformQueue(0, "exclude");
  const total = head.data?.total ?? 0;
  const penTotal = head.data?.pen_only_total ?? 0;
  const waiting = Math.max(0, total - penTotal);
  const named = other.filter((o) => o.count > 0);

  return (
    <section className="adm-card" data-testid="adm-shared-facts">
      <div className="adm-h">Shared facts</div>
      {head.isError ? (
        <div className="adm-err" data-testid="sf-error">
          {errText(head.error)}
        </div>
      ) : !head.data ? (
        <div className="adm-sub">Reading the queue…</div>
      ) : waiting > 0 ? (
        <div className="adm-line adm-loud" data-testid="sf-count">
          {waiting} {plural(waiting, "company awaits", "companies await")} you
        </div>
      ) : (
        <div className="adm-line adm-quiet">Nothing awaits you</div>
      )}

      <FactSearch asof={asof} />

      {head.data && waiting > 0 && <QueueList pen="exclude" total={waiting} enabled asof={asof} />}

      {penTotal > 0 && (
        <div className="sf-pen-block">
          <button
            type="button"
            className="adm-linkbtn"
            data-testid="sf-pen-toggle"
            aria-expanded={showPen}
            onClick={() => setShowPen((v) => !v)}
          >
            {penTotal} more {plural(penTotal, "is", "are")} only in a Discovered pen: {showPen ? "hide" : "show"}
          </button>
          {showPen && <QueueList pen="only" total={penTotal} enabled asof={asof} />}
        </div>
      )}

      {named.length > 0 && (
        <div className="adm-sub" data-testid="sf-other">
          Other work below:{" "}
          {named.map((o, i) => (
            <span key={o.target}>
              {i > 0 && " · "}
              <button type="button" className="adm-linkbtn" onClick={() => scrollToPanel(o.target)}>
                {o.count} {plural(o.count, o.one, o.many)}
              </button>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
