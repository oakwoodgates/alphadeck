import { type ReactNode, useEffect, useState } from "react";

import type {
  AdminSplitAddIn,
  AdminSplitJumpOut,
  AdminSplitListEntryOut,
  AdminSplitListOut,
  AdminSplitUnplacedOut,
  SplitTapeTarget,
} from "../api/hooks";
import {
  useAddSplit,
  useConfirmSplit,
  useConfirmSplitValues,
  useDismissJump,
  useDismissUnplaced,
  useRejectSplit,
  useRestoreSplit,
  useRevokeSplit,
  useSplitList,
  useSplitTapeStep,
  useUndoJumpDismissal,
  useUndoUnplacedDismissal,
} from "../api/hooks";
import { errText } from "../workbench/format";
import {
  type FormAct,
  NOTE_MAX,
  REASON_MAX,
  STATUS_WORD,
  addFilingIntro,
  addJumpIntro,
  addedLine,
  confirmLine,
  confirmValuesLine,
  hintLine,
  isDate,
  jumpScaleLine,
  listedNearLine,
  orderUnplaced,
  overrideHint,
  overrideLabel,
  polygonSays,
  ratioProblem,
  rejectLine,
  secSays,
  setAsideLine,
  splitSentence,
  tapeFactorSentence,
  tapeStepSentence,
  unplacedCovered,
  unplacedDismissedLine,
  unplacedHelp,
  unplacedRunsLine,
  unplacedSource,
  unplacedWhy,
  valuesProblem,
} from "./splitWords";
import { ExternalLink } from "../components/ExternalLink";
import { LIMITS } from "../api/limits";

type Split = AdminSplitListEntryOut;
type Unplaced = AdminSplitUnplacedOut;
type ConfirmValuesMutation = ReturnType<typeof useConfirmSplitValues>;
type AddSplitMutation = ReturnType<typeof useAddSplit>;
type DismissUnplacedMutation = ReturnType<typeof useDismissUnplaced>;
type DismissJumpMutation = ReturnType<typeof useDismissJump>;
type UndoUnplacedMutation = ReturnType<typeof useUndoUnplacedDismissal>;

const bucket = (d: AdminSplitListOut, k: "needs_review" | "announced" | "confirmed" | "set_aside"): Split[] =>
  d[k] ?? [];

/** "BTOC — 1-for-16 reverse split · first session 2026-10-15", and on an open split what a confirm would touch
 *  (W2-8: a first session on its first stored bar, the share count it rescales). */
function Header({ s }: { s: Split }) {
  return (
    <div>
      <b>{s.ticker ?? s.security_id}</b> — {s.label} · first session {s.effective_date}
      {s.name ? <span className="adm-sub"> ({s.name})</span> : null}
      {hintLine(s) ? <div className="adm-sub" data-testid="adm-split-hint">{hintLine(s)}</div> : null}
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

/** F1-5 — one SEC filing the list could not place, each thing said once: who and which filing (the link), where it was
 *  read, why it was not placed (the ratio it states is in that reason), the listed split nearest it, and the passage
 *  the extractor located. Shared by the open row and the dismissed one. */
function UnplacedInfo({ u }: { u: Unplaced }) {
  const who = u.ticker ?? u.name ?? u.security_id;
  const near = listedNearLine(u);
  return (
    <>
      <div>
        <b>{who}</b>
        {u.ticker && u.name ? <span className="adm-sub"> ({u.name})</span> : null} —{" "}
        <ExternalLink className="adm-link" href={u.url} title={u.accession}>
          {u.form || "filing"} filed {u.filed}
        </ExternalLink>{" "}
        <span className="adm-sub" data-testid="adm-split-unplaced-source">
          · {unplacedSource(u)}
        </span>
      </div>
      <div className="adm-plain" data-testid="adm-split-unplaced-why">
        {unplacedWhy(u)}
      </div>
      {near ? (
        <div className="adm-sub" data-testid="adm-split-unplaced-near">
          {near}
        </div>
      ) : null}
      {u.passage ? (
        <blockquote className="adm-passage" data-testid="adm-split-unplaced-passage">
          “{u.passage}”{u.document ? <span className="adm-note"> — {u.document}</span> : null}
        </blockquote>
      ) : null}
    </>
  );
}

/** A refused write's sentence, shown in the row it was about (not at the foot of a long list). */
function RowError({ text }: { text: string | null }) {
  return text ? (
    <div className="adm-err" data-testid="adm-split-unplaced-row-error">
      {text}
    </div>
  ) : null;
}

/** An unplaced filing on the list: the evidence, "Add this split..." (F2-2: a real split no source lists) and Dismiss
 *  (“not a split to list”) with an optional reason. */
function UnplacedRow({
  u,
  busy,
  send,
  add,
}: {
  u: Unplaced;
  busy: boolean;
  send: DismissUnplacedMutation;
  add: AddSplitMutation;
}) {
  const [reason, setReason] = useState("");
  const adding = useAddSplitForm({ kind: "filing", u }, busy, add);
  const who = u.ticker ?? u.name ?? u.security_id;
  const why = reason.trim();
  const refused =
    send.isError && send.variables?.securityId === u.security_id && send.variables?.accession === u.accession;
  return (
    <li data-testid="adm-split-unplaced-row">
      <UnplacedInfo u={u} />
      <div className="adm-act">
        {adding.button}
        <input
          className="adm-input adm-input-sm"
          type="text"
          placeholder="reason (optional)"
          value={reason}
          maxLength={REASON_MAX}
          onChange={(e) => setReason(e.target.value)}
          aria-label={`Reason for dismissing ${who} (optional)`}
        />
        <button
          type="button"
          className="adm-minibtn"
          disabled={busy}
          onClick={() =>
            send.mutate({ securityId: u.security_id, accession: u.accession, ...(why ? { reason: why } : {}) })
          }
        >
          Dismiss
        </button>
      </div>
      {adding.form}
      <RowError text={refused ? errText(send.error) : null} />
    </li>
  );
}

/** A dismissed filing: grayed and still listed (WB#2), what the steward said, and its Undo (WB#1). Only the words are
 *  grayed: the Undo must read as enabled. */
function DismissedUnplacedRow({ u, busy, send }: { u: Unplaced; busy: boolean; send: UndoUnplacedMutation }) {
  const id = u.dismissal_id;
  const refused = send.isError && id != null && send.variables?.dismissalId === id;
  return (
    <li data-testid="adm-split-unplaced-dismissed-row">
      <div className="adm-grayed">
        <UnplacedInfo u={u} />
        <div className="adm-sub">{unplacedDismissedLine(u)}</div>
      </div>
      <div className="adm-act">
        <button
          type="button"
          className="adm-minibtn"
          disabled={busy || id == null}
          onClick={() => id != null && send.mutate({ dismissalId: id })}
        >
          Undo
        </button>
      </div>
      <RowError text={refused ? errText(send.error) : null} />
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
      {s.tape_override ? (
        <span className="adm-sub"> · {s.status === "announced" ? "announced" : "confirmed"} past the tape</span>
      ) : null}
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

type FormValues = {
  ratioFrom: string;
  ratioTo: string;
  firstSession: string;
  citedUrl: string;
  note: string;
  /** the override box, counted only while the stored tape still does not support the values */
  override: boolean;
};

/** The form "Confirm with other values" and "Add this split..." share: the ratio (old -> new shares), the first
 *  session, the https document that states them, an optional note; once a first session is entered, the stored tape's
 *  step there beside the entered factor (the override box appears only when the tape does not support the values);
 *  then the button, Cancel, what is still missing and the server's refusal. The two differ in what they start from
 *  (`initial`), what they say first (`intro`), where the tape is read (`tapeTarget`) and what the button sends. */
function SplitValuesForm({
  act,
  tapeTarget,
  initial,
  intro,
  scale,
  submitLabel,
  busy,
  failure,
  onSubmit,
  onCancel,
}: {
  act: FormAct;
  tapeTarget: SplitTapeTarget;
  initial?: Partial<Pick<FormValues, "ratioFrom" | "ratioTo" | "firstSession" | "citedUrl">>;
  intro: string;
  /** a jump's nearest whole ratio, said while no ratio is typed */
  scale?: string | null;
  submitLabel: string;
  busy: boolean;
  failure: string | null;
  onSubmit: (v: FormValues) => void;
  onCancel: () => void;
}) {
  const [ratioFrom, setRatioFrom] = useState(initial?.ratioFrom ?? "");
  const [ratioTo, setRatioTo] = useState(initial?.ratioTo ?? "");
  const [firstSession, setFirstSession] = useState(initial?.firstSession ?? "");
  const [citedUrl, setCitedUrl] = useState(initial?.citedUrl ?? "");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState(false);
  const rf = ratioFrom.trim();
  const rt = ratioTo.trim();
  const url = citedUrl.trim();
  const id = act === "add" ? "add" : "values";

  // the tape is read once a first session is entered; the ratio joins the read once it is a ratio
  const ratioReady = ratioProblem(rf, rt) === null;
  const args = isDate(firstSession) ? { firstSession, ...(ratioReady ? { ratioFrom: rf, ratioTo: rt } : {}) } : null;
  const tape = useSplitTapeStep(tapeTarget, args);
  const t = args ? tape.data : undefined;
  const reading = args !== null && !t && Boolean(tape.isFetching);
  const outside = t?.within_tolerance === false; // no bar to read counts: the server refuses it too

  const problem = valuesProblem({ ratioFrom: rf, ratioTo: rt, firstSession, citedUrl: url });
  const hint = problem ?? (outside && !override ? overrideHint(act) : null);

  return (
    <div className="adm-hold-edit" data-testid={`adm-split-${id}-form`}>
      <div className="adm-hold-full" data-testid={`adm-split-${id}-intro`}>
        {intro}
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
      {scale && !rf && !rt ? (
        <div className="adm-sub adm-hold-full" data-testid="adm-split-add-scale">
          {scale}
        </div>
      ) : null}
      {outside && t && (
        <label className="adm-hold-full">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />{" "}
          {overrideLabel(t, act)}
        </label>
      )}

      <button
        type="button"
        className="adm-minibtn primary"
        disabled={busy || reading || hint !== null}
        onClick={() =>
          onSubmit({
            ratioFrom: rf,
            ratioTo: rt,
            firstSession,
            citedUrl: url,
            note: note.trim(),
            // a ticked box counts only while the tape still does not support the values
            override: outside && override,
          })
        }
      >
        {submitLabel}
      </button>
      <button type="button" className="adm-minibtn" onClick={onCancel}>
        Cancel
      </button>
      {hint && (
        <div className="adm-sub adm-hold-full" data-testid={`adm-split-${id}-hint`}>
          {hint}
        </div>
      )}
      {failure && (
        <div className="adm-err adm-hold-full" data-testid={`adm-split-${id}-error`}>
          {failure}
        </div>
      )}
    </div>
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
  const failure = send.isError && send.variables?.versionId === s.version_id ? errText(send.error) : null;
  return (
    <SplitValuesForm
      act="confirm"
      tapeTarget={{ versionId: s.version_id }}
      intro={`Now listed: ${s.label}, first session ${s.effective_date}. Enter the values the document you cite states.`}
      submitLabel="Confirm these values"
      busy={busy}
      failure={failure}
      onCancel={onCancel}
      onSubmit={(v) =>
        send.mutate({
          versionId: s.version_id,
          body: {
            ratio_from: v.ratioFrom,
            ratio_to: v.ratioTo,
            first_session: v.firstSession,
            cited_url: v.citedUrl,
            ...(v.note ? { note: v.note } : {}),
            override_tape: v.override,
          },
        })
      }
    />
  );
}

/** F2-2 — what an add starts from: exactly one signal (D-F2a), an unplaced filing or an undismissed jump. */
type AddOrigin = { kind: "filing"; u: Unplaced } | { kind: "jump"; j: AdminSplitJumpOut };

/** Is this the add that `origin`'s form sent (so its refusal is shown in that form and no other)? */
function sentBy(v: AdminSplitAddIn | undefined, o: AddOrigin): boolean {
  if (!v) return false;
  return o.kind === "filing"
    ? v.security_id === o.u.security_id && v.from_accession === o.u.accession
    : v.security_id === o.j.security_id && v.from_jump === o.j.step_date;
}

/** What the form opens filled in with: a filing's ratio (when it states one) and its address as the cited document, no
 *  date (none is known); a jump's step date, and no ratio (none is guessed) and no document. */
function addInitial(o: AddOrigin): { ratioFrom?: string; ratioTo?: string; firstSession?: string; citedUrl?: string } {
  if (o.kind === "jump") return { firstSession: o.j.step_date };
  const { ratio_from: from, ratio_to: to, url } = o.u;
  return { ...(from != null && to != null ? { ratioFrom: from, ratioTo: to } : {}), ...(url ? { citedUrl: url } : {}) };
}

/** F2-2 — "Add this split...": the steward lists a split no source lists, from a split filing not placed or a
 *  split-shaped jump. The values and the cited https document are theirs (a filing's ratio and address come filled in,
 *  to check); the stored tape's step at that session, read for the security, sits beside the entered factor, and the
 *  override box appears only outside the tolerance. The server checks the same again and answers 409 with its own
 *  sentence (a duplicate, a tape that does not fit), shown here; Revoke undoes the add. */
function AddForm({
  origin,
  busy,
  send,
  onCancel,
}: {
  origin: AddOrigin;
  busy: boolean;
  send: AddSplitMutation;
  onCancel: () => void;
}) {
  const failure = send.isError && sentBy(send.variables, origin) ? errText(send.error) : null;
  const filing = origin.kind === "filing";
  const securityId = filing ? origin.u.security_id : origin.j.security_id;
  const provenance = filing ? { from_accession: origin.u.accession } : { from_jump: origin.j.step_date };
  return (
    <SplitValuesForm
      act="add"
      tapeTarget={{ securityId }}
      initial={addInitial(origin)}
      intro={filing ? addFilingIntro(origin.u) : addJumpIntro()}
      scale={filing ? null : jumpScaleLine(origin.j.step)}
      submitLabel="Add these values"
      busy={busy}
      failure={failure}
      onCancel={onCancel}
      onSubmit={(v) =>
        send.mutate({
          security_id: securityId,
          ratio_from: v.ratioFrom,
          ratio_to: v.ratioTo,
          first_session: v.firstSession,
          cited_url: v.citedUrl,
          ...(v.note ? { note: v.note } : {}),
          // sent only when ticked, and only while the tape still does not support the values
          ...(v.override ? { override_tape: true } : {}),
          ...provenance,
        })
      }
    />
  );
}

/** The opener and the form of one "Add this split..." (the button sits in the row's own action line, the form below).
 *  The form closes once the add it sent is listed: the row then says so itself (a listed split cites the filing; the
 *  jump leaves the list) and a second click would only be refused as a duplicate. */
function useAddSplitForm(origin: AddOrigin, busy: boolean, send: AddSplitMutation) {
  const [open, setOpen] = useState(false);
  const listed = send.isSuccess && sentBy(send.variables, origin);
  useEffect(() => {
    if (listed) setOpen(false);
  }, [listed]);
  return {
    button: (
      <button type="button" className="adm-minibtn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Add this split...
      </button>
    ),
    form: open ? <AddForm origin={origin} busy={busy} send={send} onCancel={() => setOpen(false)} /> : null,
  };
}

/** A jump's two actions: Dismiss (“this was a real move”, display only) and Add this split... (it was a split). */
function JumpActions({
  j,
  busy,
  dismiss,
  add,
}: {
  j: AdminSplitJumpOut;
  busy: boolean;
  dismiss: DismissJumpMutation;
  add: AddSplitMutation;
}) {
  const adding = useAddSplitForm({ kind: "jump", j }, busy, add);
  return (
    <>
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
          This was a real move: the card leaves this list (it stays under “dismissed”, with an Undo). No call changes —
          the split-night hold already released it.
        </span>
      </div>
      <div className="adm-act">{adding.button}</div>
      {adding.form}
    </>
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
 *  ratio, first session and cited https document, checked against the stored tape; Revoke undoes it). F1-5 adds a
 *  quiet section, "Split filings not placed": the SEC filings about a split the detector could not place, each with
 *  the evidence, the listed split nearest it and a Dismiss ("not a split to list", optional reason; display only)
 *  whose inverse is Undo under a "dismissed" filter; a failed read of them is shown beside the panel, never hidden.
 *  F2-2 adds "Add this split..." to each of those filings and to each undismissed jump: the steward lists a real split no
 *  source lists, with the ratio, the first session and the https document that states them, beside the stored tape's
 *  step at that session (Revoke undoes it). Renders nothing when the list and those questions are all empty. */
export function SplitList() {
  const q = useSplitList();
  const confirm = useConfirmSplit();
  const confirmValues = useConfirmSplitValues();
  const reject = useRejectSplit();
  const revoke = useRevokeSplit();
  const restore = useRestoreSplit();
  const dismiss = useDismissJump();
  const undoDismiss = useUndoJumpDismissal();
  const dismissUnplaced = useDismissUnplaced();
  const undoUnplaced = useUndoUnplacedDismissal();
  const addSplit = useAddSplit();
  const [showDismissed, setShowDismissed] = useState(false);
  const [showUnplacedDismissed, setShowUnplacedDismissed] = useState(false);
  const busy =
    confirm.isPending ||
    confirmValues.isPending ||
    reject.isPending ||
    revoke.isPending ||
    restore.isPending ||
    dismiss.isPending ||
    undoDismiss.isPending ||
    dismissUnplaced.isPending ||
    undoUnplaced.isPending ||
    addSplit.isPending;
  // a refused Confirm with other values or Add this split... is shown in its own form (the server's sentence), and a
  // refused Dismiss or Undo of an unplaced filing in its own row, not here
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
  // an older server's payload carries none of these keys: every one reads empty, and the panel is today's
  const unplaced = q.data?.unplaced ?? [];
  const unplacedDismissed = q.data?.unplaced_dismissed ?? [];
  const unplacedRuns = q.data?.unplaced_runs ?? [];
  const unplacedError = q.data?.unplaced_error ?? null;
  if (
    !q.data ||
    (!q.data.total &&
      !jumps.length &&
      !dismissed.length &&
      !jumpsError &&
      !unplaced.length &&
      !unplacedDismissed.length &&
      !unplacedError)
  ) {
    return null;
  }
  const review = bucket(q.data, "needs_review");
  const announced = bucket(q.data, "announced");
  const confirmed = bucket(q.data, "confirmed");
  const setAside = bucket(q.data, "set_aside");
  const id = (s: Split) => ({ versionId: s.version_id });
  const covered = unplaced.filter(unplacedCovered).length;
  const runsLine = unplacedRunsLine(unplacedRuns);
  // after a confirmed add (its first session is past): the repair the nightly does not do (an announced add: nothing)
  const added = addSplit.isSuccess && addSplit.data ? addedLine(addSplit.data) : null;

  return (
    <section className="adm-card" data-testid="adm-splits">
      <div className="adm-h">Splits</div>

      {added && (
        <div className="adm-plain" data-testid="adm-split-added">
          {added.before}
          <code className="adm-mono">{added.command}</code>
          {added.after}{" "}
          <button type="button" className="adm-minibtn" onClick={() => addSplit.reset()}>
            Close
          </button>
        </div>
      )}

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
                action={<JumpActions j={j} busy={busy} dismiss={dismiss} add={addSplit} />}
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
                <span className="adm-sub">{setAsideLine(s, STATUS_WORD[s.status] ?? s.status)}</span>{" "}
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

      {unplacedError ? (
        <div className="adm-err" data-testid="adm-splits-unplaced-error">
          The split filings not placed could not be fully read: {unplacedError}
        </div>
      ) : null}
      {(unplaced.length > 0 || unplacedDismissed.length > 0) && (
        <details className="adm-quietlist" data-testid="adm-splits-unplaced">
          <summary className="adm-line adm-quiet">
            Split filings not placed ({unplaced.length})
            {covered > 0 ? <span className="adm-sub"> · {covered} covered by a listed split</span> : null}
          </summary>
          {runsLine ? (
            <div className="adm-sub" data-testid="adm-split-unplaced-runs">
              {runsLine}
            </div>
          ) : null}
          {unplaced.length > 0 ? (
            <ul className="adm-repairs adm-unplaced">
              {orderUnplaced(unplaced).map((u) => (
                <UnplacedRow
                  key={`${u.security_id}:${u.accession}`}
                  u={u}
                  busy={busy}
                  send={dismissUnplaced}
                  add={addSplit}
                />
              ))}
            </ul>
          ) : (
            <div className="adm-sub">No split filing is left unplaced.</div>
          )}
          {unplacedDismissed.length > 0 && (
            <div className="adm-unplaced-filter" data-testid="adm-splits-unplaced-dismissed">
              <button
                type="button"
                className="adm-minibtn"
                aria-pressed={showUnplacedDismissed}
                onClick={() => setShowUnplacedDismissed((v) => !v)}
              >
                {showUnplacedDismissed ? "Hide" : "Show"} dismissed ({unplacedDismissed.length})
              </button>{" "}
              <span className="adm-sub">split filing(s) you dismissed as not a split to list</span>
              {showUnplacedDismissed && (
                <ul className="adm-repairs adm-unplaced">
                  {unplacedDismissed.map((u) => (
                    <DismissedUnplacedRow
                      key={`${u.security_id}:${u.accession}`}
                      u={u}
                      busy={busy}
                      send={undoUnplaced}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="adm-sub" data-testid="adm-split-unplaced-help">
            {unplacedHelp()}
          </div>
        </details>
      )}

      {failed && <div className="adm-err">{errText(failed.error)}</div>}
    </section>
  );
}
