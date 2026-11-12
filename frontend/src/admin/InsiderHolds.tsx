import { useState } from "react";
import type { ReactNode } from "react";

import type { AdminHoldDecisionOut, AdminInsiderHoldOut, AdminInsiderHoldsOut } from "../api/hooks";
import { useApproveHold, useDismissHold, useInsiderHolds, useRevokeHoldDecision } from "../api/hooks";
import { errText } from "../workbench/format";

/** One wire row with the server's list default filled in (the generated type marks `history` optional). */
type Hold = Omit<AdminInsiderHoldOut, "history"> & { history: AdminHoldDecisionOut[] };

const norm = (h: AdminInsiderHoldOut): Hold => ({ ...h, history: h.history ?? [] });

type BucketKey = "owed" | "placed_by_rule" | "filer_corrected" | "decided" | "dismissed" | "amendment_row";

const rows = (d: AdminInsiderHoldsOut, k: BucketKey) => (d[k] ?? []).map(norm);
/** The API's count for a bucket (its `counts`), the list length only when an older server omits it. */
const count = (d: AdminInsiderHoldsOut, k: BucketKey) => d.counts?.[k] ?? (d[k] ?? []).length;

/** A Form 4 transaction code in plain words (SEC's Form 4 code table). */
const TXN: Record<string, string> = {
  P: "buy",
  S: "sale",
  A: "award",
  D: "return to the issuer",
  F: "tax withholding",
  I: "discretionary trade",
  M: "option exercise",
  C: "conversion",
  E: "expiration",
  H: "expiration",
  O: "exercise",
  X: "exercise",
  G: "gift",
  L: "small acquisition",
  W: "inheritance",
  Z: "trust deposit or withdrawal",
  J: "other acquisition or disposition",
  K: "swap",
  U: "tender of shares",
  V: "voluntary report",
};

const num = (v: number | null | undefined) =>
  v == null ? "?" : v.toLocaleString("en-US", { maximumFractionDigits: 4 });

function trade(h: Hold): string {
  const code = h.row.txn_code;
  const what = code ? (TXN[code] ?? `code ${code}`) : "a transaction";
  return `${what} ${num(h.row.shares)} @ ${num(h.row.price)}`;
}

/** An instant as its New York wall-clock time: "2021-03-18 16:05 ET" (display only). */
export function etStamp(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(t))
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} ET`;
}

/** The day of an instant in New York ("2026-11-12"), for "on <day>" lines. */
const etDay = (iso: string | null | undefined) => etStamp(iso)?.slice(0, 10) ?? "—";

const who = (d: AdminHoldDecisionOut) => d.decided_by?.display_name || "an unnamed steward";

/** The words for one decision in a row's history (oldest first, as the API returns it). */
function historyLine(d: AdminHoldDecisionOut): string {
  const what =
    d.action === "approve"
      ? `set the trade date ${d.ratified_date ?? "—"}`
      : d.action === "dismiss"
        ? "dismissed it as not a trade"
        : d.action === "revoke"
          ? "undid a decision"
          : d.action;
  return `${etStamp(d.recorded_at) ?? d.recorded_at} · ${who(d)} ${what}${d.reason ? ` · “${d.reason}”` : ""}`;
}

/** Ticker, company, person and the transaction in plain words. */
function Head({ h }: { h: Hold }) {
  return (
    <div className="adm-hold-head">
      <b className="adm-mono">{h.ticker ?? h.security_id}</b>
      {h.company_name ? <span className="adm-sub">{h.company_name}</span> : null}
      <span className="adm-quiet">{h.row.insider_name ?? "—"}</span>
      <span className="adm-mono adm-hold-trade" data-testid="adm-hold-trade">
        {trade(h)}
      </span>
    </div>
  );
}

/** The filing: the form and accession (linked to EDGAR), the row, SEC's acceptance and the filing's own dates. */
function FilingRef({ h }: { h: Hold }) {
  const accepted = etStamp(h.accepted);
  return (
    <div className="adm-filing-ref" data-testid="adm-hold-ref">
      {h.form} ·{" "}
      {h.filing_url ? (
        <a className="adm-link" href={h.filing_url} target="_blank" rel="noreferrer">
          {h.accession} ↗
        </a>
      ) : (
        h.accession
      )}{" "}
      · row {h.txn_seq} · accepted by SEC {accepted ?? "—"}
      {h.filed ? ` · filing date ${h.filed}` : ""} · period of report as filed{" "}
      {h.period_of_report_as_filed ?? "—"} · signed as filed {h.signature_date_as_filed ?? "—"}
    </div>
  );
}

/** "Filed as <date> — <reason>": the quiet rows' first line. */
const filedAs = (h: Hold) => (
  <>
    Filed as <b>{h.date_as_filed ?? "—"}</b> ({h.reason_words})
  </>
);

type Mutations = {
  approve: ReturnType<typeof useApproveHold>;
  dismiss: ReturnType<typeof useDismissHold>;
  revoke: ReturnType<typeof useRevokeHoldDecision>;
};

/** The server's refusal for THIS row (404 / 409 / 422), from whichever write last failed on it. */
function rowError(m: Mutations, holdId: string): string | null {
  for (const x of [m.approve, m.dismiss, m.revoke]) {
    const v = x.variables as { holdId?: string } | undefined;
    if (x.isError && v?.holdId === holdId) return errText(x.error);
  }
  return null;
}

/** Every decision on the row, Undos included, oldest first — opened on request; no toggle when there is none. */
function History({ h }: { h: Hold }) {
  const [open, setOpen] = useState(false);
  if (!h.history.length) return null;
  return (
    <div>
      <button type="button" className="adm-linkbtn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Show"} decision history ({h.history.length})
      </button>
      {open && (
        <ol className="adm-problems adm-quietrows" data-testid="adm-hold-history">
          {h.history.map((d) => (
            <li key={d.id}>{historyLine(d)}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

type Mode = null | "date" | "dismiss";

/** The decide controls of an owed or rule-placed row: Use the suggestion, set a date, or dismiss. */
function Decide({ h, m, busy, owed }: { h: Hold; m: Mutations; busy: boolean; owed: boolean }) {
  const [mode, setMode] = useState<Mode>(null);
  // only a suggestion that agrees with the filing prefills the date; a weak one is shown as text, never offered
  const [date, setDate] = useState(h.suggested_date && h.agrees ? h.suggested_date : "");
  const marketDate = h.market_date ?? undefined;
  const [why, setWhy] = useState("");
  const [dismissWhy, setDismissWhy] = useState("");

  if (mode === "date") {
    return (
      <div className="adm-hold-edit" data-testid="adm-hold-date-edit">
        <label>
          Trade date{" "}
          <input
            className="adm-input adm-input-sm adm-hold-date"
            type="date"
            value={date}
            max={marketDate}
            onChange={(e) => setDate(e.target.value)}
            aria-label="Trade date"
          />
        </label>
        <label className="adm-hold-why">
          Why{" "}
          <input
            className="adm-input adm-input-sm"
            type="text"
            value={why}
            maxLength={500}
            onChange={(e) => setWhy(e.target.value)}
            aria-label="Why this date"
          />
        </label>
        <button
          type="button"
          className="adm-minibtn primary"
          disabled={busy || !date}
          onClick={() => m.approve.mutate({ holdId: h.hold_id, ratifiedDate: date, reason: why })}
        >
          Save date
        </button>
        <button type="button" className="adm-minibtn" onClick={() => setMode(null)}>
          Cancel
        </button>
        <div className="adm-sub adm-hold-full" data-testid="adm-hold-date-hint">
          {marketDate
            ? `A date must be on or before ${marketDate}, the day the filing was public.`
            : "A date must be on or before the day the filing was public."}
        </div>
      </div>
    );
  }
  if (mode === "dismiss") {
    return (
      <div className="adm-hold-edit" data-testid="adm-hold-dismiss-edit">
        <label className="adm-hold-why">
          Why it is not a trade{" "}
          <input
            className="adm-input adm-input-sm"
            type="text"
            value={dismissWhy}
            maxLength={500}
            onChange={(e) => setDismissWhy(e.target.value)}
            aria-label="Why it is not a trade"
          />
        </label>
        <button
          type="button"
          className="adm-minibtn primary"
          disabled={busy || !dismissWhy.trim()}
          onClick={() => m.dismiss.mutate({ holdId: h.hold_id, reason: dismissWhy.trim() })}
        >
          Dismiss
        </button>
        <button type="button" className="adm-minibtn" onClick={() => setMode(null)}>
          Cancel
        </button>
        {!dismissWhy.trim() && <div className="adm-sub adm-hold-full">A reason is required to dismiss.</div>}
      </div>
    );
  }
  return (
    <div className="adm-hold-acts">
      {h.suggested_date && h.agrees && (
        <button
          type="button"
          className={`adm-minibtn${owed ? " primary" : ""}`}
          disabled={busy}
          onClick={() => m.approve.mutate({ holdId: h.hold_id })}
        >
          Use {h.suggested_date}
        </button>
      )}
      <button type="button" className="adm-minibtn" disabled={busy} onClick={() => setMode("date")}>
        {owed ? "Set another date" : "Set the trade date"}
      </button>
      <button type="button" className="adm-minibtn adm-ghost" disabled={busy} onClick={() => setMode("dismiss")}>
        Dismiss: not a trade
      </button>
    </div>
  );
}

function Undo({ h, m, busy }: { h: Hold; m: Mutations; busy: boolean }) {
  if (!h.decision) return null;
  const id = h.decision.id;
  return (
    <div className="adm-hold-acts">
      <button
        type="button"
        className="adm-minibtn"
        disabled={busy}
        onClick={() => m.revoke.mutate({ decisionId: id, holdId: h.hold_id })}
      >
        Undo
      </button>
    </div>
  );
}

function Err({ m, h }: { m: Mutations; h: Hold }) {
  const e = rowError(m, h.hold_id);
  return e ? (
    <div className="adm-err" data-testid="adm-hold-err" role="alert">
      {e}
    </div>
  ) : null;
}

/** The suggestion line: an agreeing one in green (its "Use" button beside it), a weak one as text only. */
function Suggestion({ h, showAgreeing = true }: { h: Hold; showAgreeing?: boolean }) {
  if (!h.suggested_date) {
    return showAgreeing ? (
      <div className="adm-hold-sugg" data-testid="adm-hold-sugg">
        No date can be suggested from the filing.
      </div>
    ) : null;
  }
  if (h.agrees && !showAgreeing) return null;
  return (
    <div className="adm-hold-sugg" data-testid="adm-hold-sugg">
      Suggested date <b className={h.agrees ? "adm-hold-ok" : "adm-hold-weak"}>{h.suggested_date}</b> ·{" "}
      {h.agrees ? (
        "on or after the filing's period of report"
      ) : (
        <>
          <span className="adm-hold-weak">weak</span>: not on or after a readable period of report, so it is not
          offered
        </>
      )}
    </div>
  );
}

/** An owed row: what the filing wrote and why it cannot be true, the facts, the suggestion, the controls. */
function OwedRow({ h, m, busy }: { h: Hold; m: Mutations; busy: boolean }) {
  return (
    <li data-testid="adm-hold-row">
      <Head h={h} />
      <div className="adm-plain">
        The filing says the trade date is <b>{h.date_as_filed ?? "—"}</b>. It cannot be true: {h.reason_words}.
      </div>
      <div className="adm-sub">
        Not counted in any call until you decide.
        {h.owed_why ? (
          <span data-testid="adm-hold-owed-why"> Why the rule did not place it: {h.owed_why}.</span>
        ) : null}
      </div>
      <Suggestion h={h} />
      <Decide h={h} m={m} busy={busy} owed />
      <Err m={m} h={h} />
      <FilingRef h={h} />
      <History h={h} />
    </li>
  );
}

/** A quiet section: collapsed, its count from the API, opened on request (never dropped — WB#2). */
function Quiet({
  testid,
  title,
  n,
  gray = false,
  children,
  note,
}: {
  testid: string;
  title: string;
  n: number;
  gray?: boolean;
  children: ReactNode;
  note?: string;
}) {
  return (
    <details className={`adm-quietlist${gray ? " adm-grayed" : ""}`} data-testid={testid}>
      <summary className="adm-line adm-quiet">
        {title} <span className="adm-mono adm-sub">· {n}</span>
      </summary>
      <ul className="adm-repairs adm-quietrows">{children}</ul>
      {note ? <div className="adm-sub">{note}</div> : null}
    </details>
  );
}

/** The "Held insider rows" panel (bad dates step 3; the mockup signed off 2026-11-12). A held row is a Form 4 row
 *  whose transaction date cannot be true. LOUD only for the rows owed a steward decision; the rule-placed, the
 *  filer-corrected, the decided, the dismissed and the held Form 4/A rows sit in quiet collapsed lists, still
 *  visible. Every bucket, suggestion and reason is the API's (never re-derived here); every decision has its Undo
 *  (WB#1), a dismissed row stays listed, grayed (WB#2), and the server's refusal shows on its row. Renders
 *  nothing when no row is held. */
export function InsiderHolds() {
  const q = useInsiderHolds();
  const approve = useApproveHold();
  const dismiss = useDismissHold();
  const revoke = useRevokeHoldDecision();
  const m: Mutations = { approve, dismiss, revoke };
  const busy = approve.isPending || dismiss.isPending || revoke.isPending;

  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-insider-holds">
        <div className="adm-h">Held insider rows</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  if (!q.data || !q.data.total) return null;
  const d = q.data;
  const owed = rows(d, "owed");
  const placed = rows(d, "placed_by_rule");
  const filer = rows(d, "filer_corrected");
  const decided = rows(d, "decided");
  const dismissed = rows(d, "dismissed");
  const amend = rows(d, "amendment_row");
  const nOwed = count(d, "owed");

  // each bucket in the backend's own words (`bucket_words`); the key itself only if an older server omits them
  const words = (k: BucketKey) => d.bucket_words?.[k] ?? k.replace(/_/g, " ");
  const quietParts = (["placed_by_rule", "filer_corrected", "decided", "dismissed", "amendment_row"] as const)
    .filter((k) => count(d, k) > 0)
    .map((k) => `${count(d, k)} ${words(k)}`);

  return (
    <section className="adm-card" data-testid="adm-insider-holds">
      <div className="adm-h">
        Held insider rows
        {nOwed > 0 ? (
          <span className="adm-loud" data-testid="adm-holds-owed-count">
            {nOwed} owed
          </span>
        ) : null}
      </div>

      {owed.length > 0 ? (
        <div className="adm-stale adm-hold-owed" data-testid="adm-holds-owed">
          <div className="adm-line adm-loud">
            <b>{nOwed}</b> insider row(s) held and owed your decision
          </div>
          <ul className="adm-repairs">
            {owed.map((h) => (
              <OwedRow key={h.hold_id} h={h} m={m} busy={busy} />
            ))}
          </ul>
          <div className="adm-sub">
            Every decision has an Undo. A decision counts from the moment you make it; views of earlier nights keep
            reading what the rule read.
          </div>
        </div>
      ) : (
        <div className="adm-sub" data-testid="adm-holds-none-owed">
          No held row needs a decision.{quietParts.length ? ` ${quietParts.join(" · ")}.` : ""}
        </div>
      )}

      {placed.length > 0 && (
        <Quiet
          testid="adm-holds-placed"
          title="Counted from the filing night by rule"
          n={count(d, "placed_by_rule")}
          note="No action needed. The date is an approximation and every card that shows one of these says so."
        >
          {placed.map((h) => (
            <li key={h.hold_id} data-testid="adm-hold-row">
              <Head h={h} />
              <div className="adm-sub">
                {filedAs(h)} · counted from <b>{h.filing_night ?? "—"}</b>, the night the filing was public
              </div>
              <Suggestion h={h} showAgreeing={false} />
              <Decide h={h} m={m} busy={busy} owed={false} />
              <Err m={m} h={h} />
              <FilingRef h={h} />
              <History h={h} />
            </li>
          ))}
        </Quiet>
      )}

      {filer.length > 0 && (
        <Quiet testid="adm-holds-filer" title="Corrected by the filer" n={count(d, "filer_corrected")}>
          {filer.map((h) => (
            <li key={h.hold_id} data-testid="adm-hold-row">
              <Head h={h} />
              <div className="adm-sub">
                {filedAs(h)} · {h.why ?? "the filer's own Form 4/A decides it"} · nothing to do
              </div>
              <FilingRef h={h} />
              <History h={h} />
            </li>
          ))}
        </Quiet>
      )}

      {decided.length > 0 && (
        <Quiet testid="adm-holds-decided" title="Decided" n={count(d, "decided")}>
          {decided.map((h) => (
            <li key={h.hold_id} data-testid="adm-hold-row">
              <Head h={h} />
              <div className="adm-sub">
                {filedAs(h)}
                {h.decision ? (
                  <>
                    {" "}
                    · {who(h.decision)} set <b>{h.decision.ratified_date ?? "—"}</b> on {etDay(h.decision.recorded_at)}
                    {h.decision.reason ? ` · “${h.decision.reason}”` : ""}
                  </>
                ) : null}
              </div>
              <Undo h={h} m={m} busy={busy} />
              <Err m={m} h={h} />
              <FilingRef h={h} />
              <History h={h} />
            </li>
          ))}
        </Quiet>
      )}

      {dismissed.length > 0 && (
        <Quiet testid="adm-holds-dismissed" title="Dismissed" n={count(d, "dismissed")} gray>
          {dismissed.map((h) => (
            <li key={h.hold_id} data-testid="adm-hold-row">
              <Head h={h} />
              <div className="adm-sub">
                {filedAs(h)}
                {h.decision ? (
                  <>
                    {" "}
                    · dismissed by {who(h.decision)} on {etDay(h.decision.recorded_at)}
                    {h.decision.reason ? ` · “${h.decision.reason}”` : ""}
                  </>
                ) : null}{" "}
                · still listed, never counted
              </div>
              <Undo h={h} m={m} busy={busy} />
              <Err m={m} h={h} />
              <FilingRef h={h} />
              <History h={h} />
            </li>
          ))}
        </Quiet>
      )}

      {amend.length > 0 && (
        <Quiet
          testid="adm-holds-amendment"
          title="Held in a Form 4/A"
          n={count(d, "amendment_row")}
          note={`${words("amendment_row").replace(/^./, (c) => c.toUpperCase())}.`}
        >
          {amend.map((h) => (
            <li key={h.hold_id} data-testid="adm-hold-row">
              <Head h={h} />
              <div className="adm-sub">{filedAs(h)}</div>
              <FilingRef h={h} />
              <History h={h} />
            </li>
          ))}
        </Quiet>
      )}
    </section>
  );
}
