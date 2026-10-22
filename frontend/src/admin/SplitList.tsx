import type { AdminSplitJumpOut, AdminSplitListEntryOut, AdminSplitListOut } from "../api/hooks";
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

const md = (d: string) => d.slice(5); // "2026-08-24" -> "08-24"
const px = (x: number | null | undefined) => (x == null ? "—" : String(Number(x.toPrecision(4))));
const vol = (x: number | null | undefined) => (x == null ? "—" : Math.round(x).toLocaleString("en-US"));

/** S5 — a split-shaped price jump the split-night hold released (three sessions on) that no source listed: the
 *  step itself (the two bars, close and volume) and what the price did next. A question for the operator, no
 *  action of its own (nothing to undo) and never a page. */
function Jump({ j }: { j: AdminSplitJumpOut }) {
  const who = j.ticker ?? j.security_id;
  const what = j.label.startsWith(`${who} `) ? j.label.slice(who.length + 1) : j.label;
  const after = j.after ?? [];
  return (
    <li data-testid="adm-split-jump-row">
      <div>
        <b>{who}</b> — {what} · {j.sessions_since} sessions ago
      </div>
      <div className="adm-sub adm-mono">
        {md(j.prior_date)} close {px(j.prior_close)} · vol {vol(j.prior_volume)} → {md(j.step_date)} close{" "}
        {px(j.close)} · vol {vol(j.volume)}
      </div>
      {after.length > 0 ? (
        <div className="adm-sub adm-mono">
          then {after.map((b) => `${md(b.d)} ${px(b.close)}`).join(" · ")}
        </div>
      ) : null}
    </li>
  );
}

/** The "Splits" panel (split plan S1): the split list the backfill filled from Polygon's split reference and
 *  each company's own SEC filing. LOUD only where one source cannot settle a split (Polygon alone, a
 *  disagreement, or an extractor abstain): there the SEC passage and Polygon's row sit side by side with
 *  Confirm (SEC values / Polygon values when they differ) and Reject. Auto-confirmed splits (two sources
 *  agreed), announced ones, and the rejected / revoked ones are QUIET collapsed lists — the set-aside ones
 *  grayed and still listed (WB#2). Every action has its inverse beside it: Confirm ⇄ Revoke, Reject /
 *  Revoke ⇄ Restore (WB#1). S5 adds the operator's other question: a split-shaped price jump the split-night
 *  hold released with no split listed ("is this real?") — loud while it stands, an item to look at, never an
 *  action. Renders nothing when the list and that question are both empty. */
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
  const jumps = q.data?.unlisted_jumps ?? [];
  const jumpsError = q.data?.unlisted_jumps_error ?? null;
  if (!q.data || (!q.data.total && !jumps.length && !jumpsError)) return null;
  const review = bucket(q.data, "needs_review");
  const announced = bucket(q.data, "announced");
  const confirmed = bucket(q.data, "confirmed");
  const setAside = bucket(q.data, "set_aside");
  const id = (s: Split) => ({ versionId: s.version_id });

  return (
    <section className="adm-card" data-testid="adm-splits">
      <div className="adm-h">Splits</div>

      {jumps.length > 0 && (
        <div className="adm-stale" data-testid="adm-splits-jumps">
          <div className="adm-line adm-loud">
            <b>{jumps.length}</b> split-shaped jump(s) with no split listed — is this real?
          </div>
          <ul className="adm-repairs">
            {jumps.map((j) => (
              <Jump key={`${j.security_id}:${j.step_date}`} j={j} />
            ))}
          </ul>
          <div className="adm-sub">
            The price moved ×3 or more (or to a third or less) between two sessions with the volume moving the other
            way, the shape of an unadjusted split. The split-night hold kept this name's price triggers on the bars
            before the jump for three sessions and has released them. No SEC filing or Polygon row on the list sits
            near the date: it is a real move (a crash on heavy volume looks like an unadjusted forward split) or a
            split no source has listed yet. Polygon's adjusted bars are not shown (no Polygon price client exists).
            Nothing here pages or changes a call.
          </div>
        </div>
      )}
      {jumpsError ? (
        <div className="adm-err" data-testid="adm-splits-jumps-error">
          The split-shaped jump check failed: {jumpsError}
        </div>
      ) : null}

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
            A split is confirmed on its own only when two independent sources agree on it (the SEC filing and
            Polygon exactly, or either one with the price tape). Confirmed splits are read by the price loader, the
            insider price screen and the market cap, and a confirmed split lifts the split-night hold on that
            company's price triggers. A confirmed split can be revoked and a rejected one restored.
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
