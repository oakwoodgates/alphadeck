import type { AdminSplitListEntryOut, AdminSplitListOut } from "../api/hooks";
import {
  useConfirmSplit,
  useRejectSplit,
  useRestoreSplit,
  useRevokeSplit,
  useSplitList,
} from "../api/hooks";
import { errText } from "../workbench/format";

type Split = AdminSplitListEntryOut;

const bucket = (d: AdminSplitListOut, k: "needs_review" | "announced" | "confirmed" | "set_aside"): Split[] =>
  d[k] ?? [];

const STATUS_WORD: Record<string, string> = {
  needs_review: "needs review",
  confirmed: "confirmed",
  announced: "announced",
  rejected: "rejected",
  revoked: "revoked",
};

/** "BTOC — 1-for-16 reverse split · first session 2026-10-15" */
function Header({ s }: { s: Split }) {
  return (
    <div>
      <b>{s.ticker ?? s.security_id}</b> — {s.label} · first session {s.effective_date}
      {s.name ? <span className="adm-sub"> ({s.name})</span> : null}
    </div>
  );
}

/** The SEC filing's located passage and Polygon's row, side by side — the operator's evidence. */
function Sides({ s }: { s: Split }) {
  const sec = s.sec;
  const poly = s.polygon;
  return (
    <div className="adm-split-sides" data-testid="adm-split-sides">
      <div className="adm-split-side" data-testid="adm-split-sec">
        <div className="adm-split-src">SEC</div>
        {sec ? (
          <>
            <div>
              {sec.form ?? "filing"}{" "}
              {sec.url ? (
                <a className="adm-link" href={sec.url} target="_blank" rel="noreferrer">
                  {sec.accession}
                </a>
              ) : (
                sec.accession
              )}{" "}
              filed {sec.filed ?? "—"}
            </div>
            <div>
              {sec.ratio_label ?? (
                <span>the extractor abstained — {sec.abstain_reason ?? "no ratio read"}</span>
              )}
            </div>
            <div>
              {sec.effective_date
                ? `${sec.date_kind === "trading" ? "first session" : "legal effective date"} ${sec.effective_date}`
                : "no date stated"}
            </div>
            {sec.passage ? (
              <blockquote className="adm-passage" data-testid="adm-split-passage">
                “{sec.passage}”
                {sec.document ? <span className="adm-note"> — {sec.document}</span> : null}
              </blockquote>
            ) : null}
          </>
        ) : (
          <div className="adm-sub">no SEC filing in the window states this split</div>
        )}
      </div>
      <div className="adm-split-side" data-testid="adm-split-polygon">
        <div className="adm-split-src">Polygon</div>
        {poly ? (
          <>
            <div>
              {poly.ticker ?? "—"} · {poly.ratio_label ?? `${poly.split_from} → ${poly.split_to}`}
            </div>
            <div>first session {poly.execution_date ?? "—"}</div>
            <div className="adm-note adm-mono">{poly.id}</div>
          </>
        ) : (
          <div className="adm-sub">no Polygon row</div>
        )}
      </div>
    </div>
  );
}

/** The "Splits" panel (split plan S1): the split list the backfill filled from Polygon's split reference and
 *  each company's own SEC filing. LOUD only where one source cannot settle a split (Polygon alone, a
 *  disagreement, or an extractor abstain): there the SEC passage and Polygon's row sit side by side with
 *  Confirm (SEC values / Polygon values when they differ) and Reject. Auto-confirmed splits (two sources
 *  agreed), announced ones, and the rejected / revoked ones are QUIET collapsed lists — the set-aside ones
 *  grayed and still listed (WB#2). Every action has its inverse beside it: Confirm ⇄ Revoke, Reject /
 *  Revoke ⇄ Restore (WB#1). Nothing reads the list yet. Renders nothing when the list is empty. */
export function SplitList() {
  const q = useSplitList();
  const confirm = useConfirmSplit();
  const reject = useRejectSplit();
  const revoke = useRevokeSplit();
  const restore = useRestoreSplit();
  const busy = confirm.isPending || reject.isPending || revoke.isPending || restore.isPending;
  const failed = [confirm, reject, revoke, restore].find((m) => m.isError);

  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-splits">
        <div className="adm-h">Splits</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  if (!q.data || !q.data.total) return null;
  const review = bucket(q.data, "needs_review");
  const announced = bucket(q.data, "announced");
  const confirmed = bucket(q.data, "confirmed");
  const setAside = bucket(q.data, "set_aside");
  const id = (s: Split) => ({ versionId: s.version_id });

  return (
    <section className="adm-card" data-testid="adm-splits">
      <div className="adm-h">Splits</div>

      {review.length > 0 && (
        <div className="adm-stale" data-testid="adm-splits-review">
          <div className="adm-line adm-loud">
            <b>{review.length}</b> split(s) need your review — one source could not settle them
          </div>
          <ul className="adm-repairs">
            {review.map((s) => {
              const options = s.confirm_options ?? [];
              return (
                <li key={s.version_id} data-testid="adm-split-review-row">
                  <Header s={s} />
                  <Sides s={s} />
                  {s.note ? <div className="adm-sub">{s.note}</div> : null}
                  <div className="adm-repair-act">
                    {options.map((o) => (
                      <button
                        key={o.source}
                        type="button"
                        className="adm-minibtn primary"
                        disabled={busy}
                        title={o.label}
                        onClick={() => confirm.mutate({ ...id(s), source: o.source })}
                      >
                        {options.length > 1 ? `Confirm ${o.source === "sec" ? "SEC" : "Polygon"} values` : "Confirm"}
                      </button>
                    ))}
                    <button type="button" className="adm-minibtn" disabled={busy} onClick={() => reject.mutate(id(s))}>
                      Reject
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            A split is confirmed on its own only when the SEC filing and Polygon agree exactly on the ratio and the
            first session. Nothing reads the list yet: a decision here changes no call, no price tape and no
            Cockpit number until the price loader reads it. A confirmed split can be revoked and a rejected one
            restored.
          </div>
        </div>
      )}

      {announced.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-splits-announced">
          <summary className="adm-line adm-quiet">
            {announced.length} announced — both sources agree, the first session is still ahead
          </summary>
          <ul className="adm-problems adm-quietrows">
            {announced.map((s) => (
              <li key={s.version_id}>
                <Header s={s} /> <span className="adm-sub">{s.decided_by}</span>{" "}
                <button type="button" className="adm-minibtn" disabled={busy} onClick={() => reject.mutate(id(s))}>
                  Reject
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {confirmed.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-splits-confirmed">
          <summary className="adm-line adm-quiet">{confirmed.length} confirmed</summary>
          <ul className="adm-problems adm-quietrows">
            {confirmed.map((s) => (
              <li key={s.version_id} data-testid="adm-split-confirmed-row">
                <Header s={s} /> <span className="adm-sub">{s.decided_by}</span>
                {s.sec?.url ? (
                  <>
                    {" "}
                    <a className="adm-link" href={s.sec.url} target="_blank" rel="noreferrer">
                      {s.sec.form ?? "SEC"} {s.sec.accession}
                    </a>
                  </>
                ) : null}{" "}
                <button type="button" className="adm-minibtn" disabled={busy} onClick={() => revoke.mutate(id(s))}>
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {setAside.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-splits-set-aside">
          <summary className="adm-line adm-quiet">{setAside.length} rejected or revoked (kept, grayed)</summary>
          <ul className="adm-problems adm-quietrows">
            {setAside.map((s) => (
              <li key={s.version_id} className="adm-grayed" data-testid="adm-split-set-aside-row">
                <Header s={s} />{" "}
                <span className="adm-sub">
                  {STATUS_WORD[s.status] ?? s.status} · {s.decided_by}
                </span>{" "}
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy}
                  title={s.restore_to ? `back to ${STATUS_WORD[s.restore_to] ?? s.restore_to}` : undefined}
                  onClick={() => restore.mutate(id(s))}
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {failed && <div className="adm-err">{errText(failed.error)}</div>}
    </section>
  );
}
