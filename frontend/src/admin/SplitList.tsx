import { type ReactNode, useState } from "react";

import type { AdminSplitJumpOut, AdminSplitListEntryOut, AdminSplitListOut } from "../api/hooks";
import {
  useConfirmSplit,
  useConfirmSplitValues,
  useDismissJump,
  useRejectSplit,
  useRestoreSplit,
  useRevokeSplit,
  useSplitList,
  useSplitTapeStep,
  useUndoJumpDismissal,
} from "../api/hooks";
import { errText } from "../workbench/format";
import {
  NOTE_MAX,
  confirmLine,
  confirmValuesLine,
  isDate,
  overrideLabel,
  polygonSays,
  ratioProblem,
  rejectLine,
  secSays,
  splitSentence,
  tapeFactorSentence,
  tapeStepSentence,
  valuesProblem,
} from "./splitWords";
import { ExternalLink } from "../components/ExternalLink";
import { LIMITS } from "../api/limits";

type Split = AdminSplitListEntryOut;
type ConfirmValuesMutation = ReturnType<typeof useConfirmSplitValues>;

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

/** The SEC filing's located passage and Massive.com's row, side by side — the operator's evidence. */
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
                <ExternalLink className="adm-link" href={sec.url}>
                  {sec.accession}
                </ExternalLink>
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
        <div className="adm-split-src">Massive.com</div>
        {poly ? (
          <>
            <div>
              {poly.ticker ?? "—"} · {poly.ratio_label ?? `${poly.split_from} → ${poly.split_to}`}
            </div>
            <div>first session {poly.execution_date ?? "—"}</div>
            <div className="adm-note adm-mono">{poly.id}</div>
          </>
        ) : (
          <div className="adm-sub">no Massive.com row</div>
        )}
      </div>
    </div>
  );
}

const md = (d: string) => d.slice(5); // "2026-08-24" -> "08-24"
const px = (x: number | null | undefined) => (x == null ? "—" : String(Number(x.toPrecision(4))));
const vol = (x: number | null | undefined) => (x == null ? "—" : Math.round(x).toLocaleString("en-US"));

/** S5 — a split-shaped price jump the split-night hold released (three sessions on) that no source listed: the
 *  step itself (the two bars, close and volume) and what the price did next. A question for the operator, never
 *  a page. Its one action (2026-10-24): Dismiss ("this was a real move") — display only, its inverse is Undo. */
function Jump({ j, action }: { j: AdminSplitJumpOut; action?: ReactNode }) {
  const who = j.ticker ?? j.security_id;
  const what = j.label.startsWith(`${who} `) ? j.label.slice(who.length + 1) : j.label;
  const after = j.after ?? [];
  return (
    <li data-testid={j.dismissal_id ? "adm-split-jump-dismissed-row" : "adm-split-jump-row"}>
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
      {j.dismissal_id ? (
        <div className="adm-sub">
          dismissed by you as a real move{j.dismissed_at ? `, ${md(j.dismissed_at.slice(0, 10))}` : ""}
          {j.dismissed_reason ? ` — “${j.dismissed_reason}”` : ""}
        </div>
      ) : null}
      {action}
    </li>
  );
}

/** A steward's cited document on a confirmed or announced split (D3): the link, and whether the steward confirmed
 *  past the tape's step. Nothing for a split the sources or the tape settled (both fields are null there). */
function Cited({ s }: { s: Split }) {
  if (!s.cited_url) return null;
  return (
    <>
      {" "}
      <ExternalLink className="adm-link" href={s.cited_url}>
        cited document
      </ExternalLink>
      {s.tape_override ? <span className="adm-sub"> · confirmed past the tape</span> : null}
    </>
  );
}

/** D3 — "Confirm with other values": for a needs-review split neither source states right (an ADS ratio, a date the
 *  extractor missed), the steward types the ratio (old -> new shares), the first session and the https document
 *  that states them. Once a first session is entered the form reads the stored tape's step there and shows it beside
 *  the entered factor; the override box appears only when the tape does not support the values. The server checks
 *  the same thing again and answers 409 with its own sentence (shown here); Revoke undoes the confirmation. */
function ConfirmValues({ s, busy, send }: { s: Split; busy: boolean; send: ConfirmValuesMutation }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="adm-act">
        <button type="button" className="adm-minibtn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          Confirm with other values
        </button>
        <span className="adm-act-line" data-testid="adm-split-values-effect">
          {confirmValuesLine()}
        </span>
      </div>
      {open && <ValuesForm s={s} busy={busy} send={send} onCancel={() => setOpen(false)} />}
    </>
  );
}

function ValuesForm({
  s,
  busy,
  send,
  onCancel,
}: {
  s: Split;
  busy: boolean;
  send: ConfirmValuesMutation;
  onCancel: () => void;
}) {
  const [ratioFrom, setRatioFrom] = useState("");
  const [ratioTo, setRatioTo] = useState("");
  const [firstSession, setFirstSession] = useState("");
  const [citedUrl, setCitedUrl] = useState("");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState(false);
  const rf = ratioFrom.trim();
  const rt = ratioTo.trim();
  const url = citedUrl.trim();

  // the tape is read once a first session is entered; the ratio joins the read once it is a ratio
  const ratioReady = ratioProblem(rf, rt) === null;
  const args = isDate(firstSession) ? { firstSession, ...(ratioReady ? { ratioFrom: rf, ratioTo: rt } : {}) } : null;
  const tape = useSplitTapeStep(s.version_id, args);
  const t = args ? tape.data : undefined;
  const reading = args !== null && !t && Boolean(tape.isFetching);
  const outside = t?.within_tolerance === false; // no bar to read counts: the server refuses it too

  const problem = valuesProblem({ ratioFrom: rf, ratioTo: rt, firstSession, citedUrl: url });
  const hint = problem ?? (outside && !override ? "Tick the box above to confirm past the tape." : null);
  const failure = send.isError && send.variables?.versionId === s.version_id ? errText(send.error) : null;

  const submit = () =>
    send.mutate({
      versionId: s.version_id,
      body: {
        ratio_from: rf,
        ratio_to: rt,
        first_session: firstSession,
        cited_url: url,
        ...(note.trim() ? { note: note.trim() } : {}),
        // a ticked box counts only while the tape still does not support the values
        override_tape: outside && override,
      },
    });

  return (
    <div className="adm-hold-edit" data-testid="adm-split-values-form">
      <div className="adm-hold-full">
        Now listed: {s.label}, first session {s.effective_date}. Enter the values the document you cite states.
      </div>
      <label>
        Every{" "}
        <input
          className="adm-input"
          style={{ width: "9ch", minWidth: 0 }}
          type="text"
          inputMode="decimal"
          value={ratioFrom}
          maxLength={17}
          onChange={(e) => setRatioFrom(e.target.value)}
          aria-label="Old shares"
        />{" "}
        shares became{" "}
        <input
          className="adm-input"
          style={{ width: "9ch", minWidth: 0 }}
          type="text"
          inputMode="decimal"
          value={ratioTo}
          maxLength={17}
          onChange={(e) => setRatioTo(e.target.value)}
          aria-label="New shares"
        />
      </label>
      <label>
        First session at the new price{" "}
        <input
          className="adm-input adm-input-sm"
          type="date"
          value={firstSession}
          onChange={(e) => setFirstSession(e.target.value)}
          aria-label="First session"
        />
      </label>
      <label className="adm-hold-why adm-hold-full">
        Cited document{" "}
        <input
          className="adm-input adm-input-sm"
          type="url"
          placeholder="https://"
          value={citedUrl}
          maxLength={LIMITS.url}
          onChange={(e) => setCitedUrl(e.target.value)}
          aria-label="Cited document"
        />
      </label>
      <label className="adm-hold-why adm-hold-full">
        Note (optional){" "}
        <input
          className="adm-input adm-input-sm"
          type="text"
          value={note}
          maxLength={NOTE_MAX}
          onChange={(e) => setNote(e.target.value)}
          aria-label="Note"
        />
      </label>

      {args && (
        <div className="adm-plain adm-hold-full" data-testid="adm-split-tape-step">
          {t ? (
            <>
              <div>{tapeStepSentence(t)}</div>
              {tapeFactorSentence(t, rf, rt) ? <div>{tapeFactorSentence(t, rf, rt)}</div> : null}
            </>
          ) : tape.isError ? (
            <div className="adm-err">The stored tape could not be read: {errText(tape.error)}</div>
          ) : (
            <div className="adm-sub">Reading the stored tape…</div>
          )}
        </div>
      )}
      {outside && t && (
        <label className="adm-hold-full">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />{" "}
          {overrideLabel(t)}
        </label>
      )}

      <button
        type="button"
        className="adm-minibtn primary"
        disabled={busy || reading || hint !== null}
        onClick={submit}
      >
        Confirm these values
      </button>
      <button type="button" className="adm-minibtn" onClick={onCancel}>
        Cancel
      </button>
      {hint && (
        <div className="adm-sub adm-hold-full" data-testid="adm-split-values-hint">
          {hint}
        </div>
      )}
      {failure && (
        <div className="adm-err adm-hold-full" data-testid="adm-split-values-error">
          {failure}
        </div>
      )}
    </div>
  );
}

/** The "Splits" panel (split plan S1): the split list the backfill filled from Massive.com's split reference and
 *  each company's own SEC filing. LOUD only where one source cannot settle a split (Massive.com alone, a
 *  disagreement, or an extractor abstain): there the SEC passage and Massive.com's row sit side by side with
 *  Confirm (SEC values / Massive.com values when they differ) and Reject. Auto-confirmed splits (two sources
 *  agreed), announced ones, and the rejected / revoked ones are QUIET collapsed lists — the set-aside ones
 *  grayed and still listed (WB#2). Every action has its inverse beside it: Confirm ⇄ Revoke, Reject /
 *  Revoke ⇄ Restore (WB#1). S5 adds the operator's other question: a split-shaped price jump the split-night
 *  hold released with no split listed ("is this real?") — loud while it stands; its Dismiss ("this was a real
 *  move", 2026-10-24) takes it off the list into the "dismissed" filter, its Undo puts it back, and neither
 *  changes a call. Each review card says in plain words what the split is, what each source says, and what each
 *  button does. D3 adds a third way to confirm a review card, "Confirm with other values" (the steward's own
 *  ratio, first session and cited https document, checked against the stored tape; Revoke undoes it). Renders
 *  nothing when the list and that question are both empty. */
export function SplitList() {
  const q = useSplitList();
  const confirm = useConfirmSplit();
  const confirmValues = useConfirmSplitValues();
  const reject = useRejectSplit();
  const revoke = useRevokeSplit();
  const restore = useRestoreSplit();
  const dismiss = useDismissJump();
  const undoDismiss = useUndoJumpDismissal();
  const [showDismissed, setShowDismissed] = useState(false);
  const busy =
    confirm.isPending ||
    confirmValues.isPending ||
    reject.isPending ||
    revoke.isPending ||
    restore.isPending ||
    dismiss.isPending ||
    undoDismiss.isPending;
  // a refused Confirm with other values is shown in its own form (the server's sentence), not here
  const failed = [confirm, reject, revoke, restore, dismiss, undoDismiss].find((m) => m.isError);

  if (q.isError) {
    return (
      <section className="adm-card" data-testid="adm-splits">
        <div className="adm-h">Splits</div>
        <div className="adm-err">{errText(q.error)}</div>
      </section>
    );
  }
  const jumps = q.data?.unlisted_jumps ?? [];
  const dismissed = q.data?.dismissed_jumps ?? [];
  const jumpsError = q.data?.unlisted_jumps_error ?? null;
  if (!q.data || (!q.data.total && !jumps.length && !dismissed.length && !jumpsError)) return null;
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
              <Jump
                key={`${j.security_id}:${j.step_date}`}
                j={j}
                action={
                  <div className="adm-act">
                    <button
                      type="button"
                      className="adm-minibtn"
                      disabled={busy}
                      onClick={() => dismiss.mutate({ securityId: j.security_id, stepDate: j.step_date })}
                    >
                      Dismiss
                    </button>
                    <span className="adm-act-line" data-testid="adm-split-jump-effect">
                      This was a real move: the card leaves this list (it stays under “dismissed”, with an Undo). No
                      call changes — the split-night hold already released it.
                    </span>
                  </div>
                }
              />
            ))}
          </ul>
          <div className="adm-sub">
            The price moved ×3 or more (or to a third or less) between two sessions with the volume moving the other
            way, the shape of an unadjusted split. The split-night hold kept this name's price triggers on the bars
            before the jump for three sessions and has released them. No SEC filing or Massive.com row on the list sits
            near the date: it is a real move (a crash on heavy volume looks like an unadjusted forward split) or a
            split no source has listed yet. Massive.com's adjusted bars are not shown (no Massive.com price client exists).
            Nothing here pages or changes a call.
          </div>
        </div>
      )}
      {jumpsError ? (
        <div className="adm-err" data-testid="adm-splits-jumps-error">
          The split-shaped jump check failed: {jumpsError}
        </div>
      ) : null}
      {dismissed.length > 0 && (
        <div className="adm-quietlist" data-testid="adm-splits-jumps-dismissed">
          <button
            type="button"
            className="adm-minibtn"
            aria-pressed={showDismissed}
            onClick={() => setShowDismissed((v) => !v)}
          >
            {showDismissed ? "Hide" : "Show"} dismissed ({dismissed.length})
          </button>{" "}
          <span className="adm-sub">split-shaped jump(s) you dismissed as real moves</span>
          {showDismissed && (
            <ul className="adm-problems adm-quietrows adm-grayed">
              {dismissed.map((j) => (
                <Jump
                  key={`${j.security_id}:${j.step_date}`}
                  j={j}
                  action={
                    <div className="adm-act">
                      <button
                        type="button"
                        className="adm-minibtn"
                        disabled={busy}
                        onClick={() => undoDismiss.mutate({ dismissalId: j.dismissal_id! })}
                      >
                        Undo
                      </button>
                      <span className="adm-act-line">Back on the “is this real?” list. No call changes.</span>
                    </div>
                  }
                />
              ))}
            </ul>
          )}
        </div>
      )}

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
                  <div className="adm-plain" data-testid="adm-split-plain">
                    <div data-testid="adm-split-what">{splitSentence(s)}</div>
                    <div data-testid="adm-split-sec-says">{secSays(s)}</div>
                    <div data-testid="adm-split-polygon-says">{polygonSays(s)}</div>
                  </div>
                  {s.note ? <div className="adm-sub">{s.note}</div> : null}
                  <div className="adm-acts">
                    {options.map((o) => (
                      <div className="adm-act" key={o.source}>
                        <button
                          type="button"
                          className="adm-minibtn primary"
                          disabled={busy}
                          title={o.label}
                          onClick={() => confirm.mutate({ ...id(s), source: o.source })}
                        >
                          {options.length > 1 ? `Confirm ${o.source === "sec" ? "SEC" : "Massive.com"} values` : "Confirm"}
                        </button>
                        <span className="adm-act-line" data-testid="adm-split-effect">
                          {confirmLine(s, o, options.length > 1)}
                        </span>
                      </div>
                    ))}
                    <div className="adm-act">
                      <button type="button" className="adm-minibtn" disabled={busy} onClick={() => reject.mutate(id(s))}>
                        Reject
                      </button>
                      <span className="adm-act-line" data-testid="adm-split-effect">
                        {rejectLine()}
                      </span>
                    </div>
                    <ConfirmValues s={s} busy={busy} send={confirmValues} />
                  </div>
                  <Sides s={s} />
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            A split is confirmed on its own only when two independent sources agree on it (the SEC filing and
            Massive.com exactly, or either one with the price tape). Confirmed splits are read by the price loader, the
            insider price screen and the market cap, and a confirmed split lifts the split-night hold on that
            company's price triggers. A confirmed split can be revoked and a rejected one restored. When neither
            source states the split right, confirm it with the values from a document you cite; the stored tape is
            checked against them first.
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
                <Header s={s} /> <span className="adm-sub">{s.decided_by}</span>
                <Cited s={s} />{" "}
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
                    <ExternalLink className="adm-link" href={s.sec.url}>
                      {s.sec.form ?? "SEC"} {s.sec.accession}
                    </ExternalLink>
                  </>
                ) : null}
                <Cited s={s} />{" "}
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
