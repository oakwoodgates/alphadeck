import { useState } from "react";

import type {
  AdminCikChangeOut,
  AdminPriceRepairsOut,
  AdminPriceSymbolChangeOut,
  AdminProbeCandidateOut,
  AdminRenamedRowOut,
  AdminSuccessorCandidateOut,
  AdminSuccessorRowOut,
  AdminUnpriceableRowOut,
} from "../api/hooks";
import {
  useAcknowledgeUnpriceable,
  useAdoptPriceSymbol,
  useAdoptSuccessorCik,
  useRevokeAcknowledgment,
  useSetPriceSymbol,
  useUndoCikChange,
  useUndoPriceSymbolChange,
} from "../api/hooks";
import { workbenchPath } from "../nav";
import { errText } from "../workbench/format";

/** The security ids this panel renders a row for — the freshness card leaves THESE out of its own stale
 *  and closed blocks, so a name is shown once (in the place it can be acted on) and never dropped: a row
 *  the panel does not hold stays in the freshness card (#9). */
export function repairHandledIds(r: AdminPriceRepairsOut | null | undefined): Set<string> {
  if (!r) return new Set();
  return new Set([
    ...r.renamed.map((x) => x.security_id),
    ...r.unpriceable.map((x) => x.security_id),
    ...r.acknowledged.map((x) => x.security_id),
    ...r.closed.map((x) => x.security_id),
  ]);
}

/** "Client error '404 Not Found' for url …" -> "404 Not Found"; anything else clipped. */
const vendorShort = (err: string | null | undefined) => {
  if (!err) return null;
  const m = /'(\d{3} [^']+)'/.exec(err);
  return m ? m[1] : err.length > 80 ? `${err.slice(0, 79)}…` : err;
};

const ratio = (c: AdminProbeCandidateOut) => (c.ratio == null ? "—" : `×${c.ratio.toFixed(3)}`);

/** One candidate's splice verdict, in the operator's words. */
function CandidateVerdict({ c }: { c: AdminProbeCandidateOut }) {
  if (c.error) return <span className="adm-sub">fetch failed ({vendorShort(c.error)})</span>;
  const tail = c.last_bar ? `, bars to ${c.last_bar}` : "";
  return (
    <span className={c.verified ? undefined : "adm-sub"} data-testid={c.verified ? "adm-verified" : undefined}>
      {c.verified ? "splice-verified" : "not verified"} ({c.overlap} shared bars, {ratio(c)}
      {tail})
    </span>
  );
}

/** A standing operator change on a row: what was applied, and its Undo (WB#1 — the inverse is on the row). */
function AppliedLine({
  change,
  verb,
  onUndo,
  busy,
}: {
  change: AdminPriceSymbolChangeOut;
  verb: string;
  onUndo: () => void;
  busy: boolean;
}) {
  return (
    <div className="adm-repair-act" data-testid="adm-applied-line">
      <span>
        {verb} <b>{change.new_symbol ?? "the canonical ticker"}</b>
        {change.name_changed && change.new_name ? <> (name → {change.new_name})</> : null} — the next
        nightly pass re-pulls the tape under it
      </span>
      <button
        type="button"
        className="adm-minibtn"
        disabled={busy || !change.undoable}
        onClick={onUndo}
      >
        Undo {change.action === "adopt" ? "adopt" : "set"}
      </button>
      {!change.undoable && (
        <span className="adm-sub">
          the master changed since (now {change.current_symbol ?? "unset"}) — undo unavailable
        </span>
      )}
    </div>
  );
}

const thesesText = (row: { theses?: { name: string }[] }) =>
  (row.theses ?? []).map((t) => t.name).join(", ");

/** A standing successor-CIK re-point on its row: what was adopted, and its Undo (WB#1). */
function AppliedCikLine({
  change,
  onUndo,
  busy,
}: {
  change: AdminCikChangeOut;
  onUndo: () => void;
  busy: boolean;
}) {
  return (
    <div className="adm-repair-act" data-testid="adm-applied-cik-line">
      <span>
        adopted CIK <b>{change.new_cik}</b>
        {change.new_name && change.new_name !== change.prior_name ? <> (name → {change.new_name})</> : null}{" "}
        — the next nightly pass reads its filings
      </span>
      <button type="button" className="adm-minibtn" disabled={busy || !change.undoable} onClick={onUndo}>
        Undo adopt
      </button>
      {!change.undoable && (
        <span className="adm-sub">
          the master changed since (now CIK {change.current_cik ?? "unset"}) — undo unavailable
        </span>
      )}
    </div>
  );
}

/** What the successor CIK's own filings say it is — the identity an Adopt applies, in the operator's words. */
function SuccessorIdentity({ c }: { c: AdminSuccessorCandidateOut }) {
  if (!c.identity_read) {
    return <span className="adm-sub">its submissions could not be read on that pass ({c.error ?? "unknown"})</span>;
  }
  const forms = c.files_domestic_forms
    ? "files 10-K / 10-Q (insider Form 4s apply)"
    : c.recent_foreign_form
      ? `files ${c.recent_foreign_form}`
      : "no annual / quarterly report yet";
  return (
    <span className="adm-sub">
      {c.incorporation ? `incorporated ${c.incorporation} · ` : ""}
      {forms}
    </span>
  );
}

/** The "Price feed repairs" panel (T2 + Q8 + T3): every price-feed decision the operator owes or has made,
 *  in one place. LOUD only where action is owed — a RENAMED name (its splice-verified successor to Adopt) and
 *  an UNPRICEABLE one (Set a symbol / Acknowledge / Remove); the CLOSED names, the ACKNOWLEDGED ones and the
 *  standing repairs are QUIET counts that open to their lists (still visible, never dropped — #9/WB#2).
 *  T7 adds a LOUD block for a member whose company now files under a NEW CIK (the filing legs read the dead
 *  one) with its recommended successor to Adopt, and a quiet list of the re-points in force.
 *  Every action is the operator's click, and every one has its inverse beside it (WB#1): Adopt/Set ⇄ Undo,
 *  Acknowledge ⇄ Revoke. Nothing here is applied by the system (#3). Renders nothing when there is nothing
 *  to show (honest loudness). */
export function PriceFeedRepairs({ repairs }: { repairs: AdminPriceRepairsOut }) {
  const adopt = useAdoptPriceSymbol();
  const setSym = useSetPriceSymbol();
  const undo = useUndoPriceSymbolChange();
  const ack = useAcknowledgeUnpriceable();
  const revoke = useRevokeAcknowledgment();
  const adoptCik = useAdoptSuccessorCik();
  const undoCik = useUndoCikChange();
  const [symbols, setSymbols] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});

  const { renamed, unpriceable, acknowledged, closed } = repairs;
  const successor = repairs.successor ?? [];
  // a standing repair whose name still has its own row shows on that row; the rest (healed names that
  // left the inventory) keep their Undo in the quiet "applied" list — the inverse never vanishes (WB#1)
  const onRow = new Set([...renamed, ...unpriceable].map((r) => r.security_id));
  const applied = repairs.applied.filter((c) => !onRow.has(c.security_id));
  const onSuccessorRow = new Set(successor.map((r) => r.security_id));
  const cikApplied = (repairs.cik_applied ?? []).filter((c) => !onSuccessorRow.has(c.security_id));
  const busy =
    adopt.isPending ||
    setSym.isPending ||
    undo.isPending ||
    ack.isPending ||
    revoke.isPending ||
    adoptCik.isPending ||
    undoCik.isPending;
  const anything =
    renamed.length ||
    unpriceable.length ||
    acknowledged.length ||
    closed.length ||
    applied.length ||
    successor.length ||
    cikApplied.length ||
    repairs.successor_error ||
    repairs.probe_error;
  if (!anything) return null;

  const failed = [adopt, setSym, undo, ack, revoke, adoptCik, undoCik].find((m) => m.isError);

  return (
    <section className="adm-card" data-testid="adm-price-repairs">
      <div className="adm-h">Price feed repairs</div>

      {successor.length > 0 && (
        <div className="adm-stale" data-testid="adm-successor-cik">
          <div className="adm-line adm-loud">
            <b>{successor.length}</b> name(s) now file under a NEW CIK — the filing legs (Form 4, 8-K,
            13D/G) still read the old one
          </div>
          <ul className="adm-repairs">
            {successor.map((r: AdminSuccessorRowOut) => {
              const dereg = r.deregistrations?.[0];
              const rec = r.candidates.find((c) => c.cik === r.recommended) ?? null;
              return (
                <li key={r.security_id} data-testid="adm-successor-row">
                  <div>
                    <b>{r.ticker ?? r.security_id}</b> — stored CIK {r.stored_cik}
                    {r.old_entity_name ? <> ({r.old_entity_name})</> : null} lists no symbol
                    {dereg ? <> · {dereg.form} filed {dereg.filed}</> : null} · {thesesText(r)}
                  </div>
                  {r.candidates.map((c) => {
                    const ev = c.succession_filings?.[0];
                    return (
                      <div key={c.cik} className="adm-sub" data-testid="adm-successor-candidate">
                        SEC now assigns {c.matched_on.join(", ")} to CIK <b>{c.cik}</b>
                        {c.entity_name ? <> ({c.entity_name})</> : null}
                        {ev ? <> · {ev.form} filed {ev.filed}</> : null} · <SuccessorIdentity c={c} />
                      </div>
                    );
                  })}
                  {r.change ? (
                    <AppliedCikLine
                      change={r.change}
                      busy={busy}
                      onUndo={() => undoCik.mutate({ changeId: r.change!.id })}
                    />
                  ) : (
                    <div className="adm-repair-act">
                      {rec ? (
                        <button
                          type="button"
                          className="adm-minibtn primary"
                          disabled={busy || !rec.identity_read}
                          onClick={() => adoptCik.mutate({ securityId: r.security_id, cik: rec.cik })}
                        >
                          Adopt CIK {rec.cik}
                        </button>
                      ) : (
                        <span className="adm-sub">
                          SEC&apos;s map names more than one CIK — no single recommendation; repair it by hand
                        </span>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            The CIK we store lists no symbol on EDGAR, and SEC&apos;s ticker map assigns ours to the
            successor (checked on the {repairs.successor_asof} pass). Adopt re-points the CIK in place —
            the tape, the facts and the basket stay — and refreshes the identity from the successor&apos;s
            filings; Undo restores the prior values exactly. Nothing changes until you click.
          </div>
        </div>
      )}

      {repairs.successor_error && (
        <div className="adm-line adm-loud" data-testid="adm-successor-error">
          The successor-CIK check could not run on the last pass ({repairs.successor_error}).
        </div>
      )}

      {renamed.length > 0 && (
        <div className="adm-stale" data-testid="adm-renamed-repairs">
          <div className="adm-line adm-loud">
            <b>{renamed.length}</b> renamed name(s) — EDGAR lists the company under a symbol the price
            feed does not follow
          </div>
          <ul className="adm-repairs">
            {renamed.map((r: AdminRenamedRowOut) => {
              const former = r.former_names?.[0]?.name;
              const filing = r.name_change_filings?.[0];
              // not probed (the probe failed that night): the EDGAR symbols are still adoptable, unverified
              const cands: AdminProbeCandidateOut[] = r.candidates.length
                ? r.candidates
                : r.edgar_tickers.map((s) => ({ symbol: s, verified: false, overlap: 0, bars: 0 }));
              return (
                <li key={r.security_id} data-testid="adm-renamed-row">
                  <div>
                    <b>{r.ticker ?? r.security_id}</b> — last bar {r.edge ?? "never"} · {thesesText(r)} ·
                    EDGAR now lists {r.edgar_tickers.join(", ")}
                    {r.edgar_name && <> ({r.edgar_name}{former ? <>; formerly {former}</> : null})</>}
                    {filing && <> · 8-K item 5.03 filed {filing.filed}</>}
                  </div>
                  {r.change ? (
                    <AppliedLine
                      change={r.change}
                      verb="adopted"
                      busy={busy}
                      onUndo={() => undo.mutate({ changeId: r.change!.id })}
                    />
                  ) : (
                    <div className="adm-repair-act">
                      {!r.probed && <span className="adm-sub">not probed on this pass ·</span>}
                      {cands.map((c) => (
                        <span key={c.symbol} className="adm-cand">
                          <b>{c.symbol}</b> {r.probed && <CandidateVerdict c={c} />}
                          <button
                            type="button"
                            className={`adm-minibtn${c.verified ? " primary" : ""}`}
                            disabled={busy}
                            onClick={() => adopt.mutate({ securityId: r.security_id, symbol: c.symbol })}
                          >
                            Adopt {c.symbol}
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="adm-sub">
            Each symbol EDGAR lists was spliced against our stored tape on the {repairs.asof} pass —
            at least 5 shared dates with the median close within 1% is verified (the same company&apos;s
            tape under its new symbol). Nothing changes until you Adopt; the next nightly pass then
            re-pulls the tape, and Undo restores the prior symbol exactly.
          </div>
        </div>
      )}

      {unpriceable.length > 0 && (
        <div className="adm-stale" data-testid="adm-unpriceable">
          <div className="adm-line adm-loud">
            <b>{unpriceable.length}</b> name(s) have never been priced — no stored bar, neither closed
            nor renamed
          </div>
          <ul className="adm-repairs">
            {unpriceable.map((u: AdminUnpriceableRowOut) => (
              <li key={u.security_id} data-testid="adm-unpriceable-row">
                <div>
                  <b>{u.ticker ?? u.security_id}</b> — no stored bar ·{" "}
                  {vendorShort(u.vendor_error) ? `vendor ${vendorShort(u.vendor_error)}` : "no vendor bars"}{" "}
                  · EDGAR lists {u.edgar_tickers?.length ? u.edgar_tickers.join(", ") : "no symbol"} ·{" "}
                  {thesesText(u)}
                </div>
                {u.change ? (
                  <AppliedLine
                    change={u.change}
                    verb="set to"
                    busy={busy}
                    onUndo={() => undo.mutate({ changeId: u.change!.id })}
                  />
                ) : (
                  <div className="adm-repair-act">
                    <input
                      className="adm-input adm-input-sm"
                      type="text"
                      aria-label={`vendor symbol for ${u.ticker ?? u.security_id}`}
                      placeholder="vendor symbol (e.g. a TSX listing)"
                      value={symbols[u.security_id] ?? ""}
                      onChange={(e) => setSymbols({ ...symbols, [u.security_id]: e.target.value })}
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className="adm-minibtn"
                      disabled={busy || !(symbols[u.security_id] ?? "").trim()}
                      onClick={() =>
                        setSym.mutate({
                          securityId: u.security_id,
                          symbol: (symbols[u.security_id] ?? "").trim(),
                        })
                      }
                    >
                      Set price symbol
                    </button>
                    <input
                      className="adm-input adm-input-sm"
                      type="text"
                      aria-label={`reason to keep ${u.ticker ?? u.security_id} unpriced`}
                      placeholder="why keep it unpriced (optional)"
                      value={reasons[u.security_id] ?? ""}
                      onChange={(e) => setReasons({ ...reasons, [u.security_id]: e.target.value })}
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className="adm-minibtn"
                      disabled={busy}
                      onClick={() =>
                        ack.mutate({ securityId: u.security_id, reason: reasons[u.security_id] })
                      }
                    >
                      Acknowledge — keep unpriced
                    </button>
                    <a className="adm-link" href={workbenchPath(null)}>
                      Remove from basket →
                    </a>
                    <span className="adm-sub">(Workbench: open {thesesText(u)} → ✎ Edit the basket)</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <div className="adm-sub">
            A set symbol is validated with one vendor fetch before it is written; an acknowledged name
            goes quiet (no page) and stays listed below, revocable.
          </div>
        </div>
      )}

      {repairs.probe_error && (
        <div className="adm-line adm-loud" data-testid="adm-probe-error">
          The rename probe could not run on the last pass ({repairs.probe_error}) — renamed names carry
          no splice verdict tonight.
        </div>
      )}

      {acknowledged.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-acknowledged">
          <summary className="adm-line adm-quiet">
            {acknowledged.length} acknowledged — kept unpriced by the operator (quiet; never paged)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {acknowledged.map((a) => (
              <li key={a.id}>
                <b>{a.ticker ?? a.security_id}</b> — “{a.reason}” · {a.created_at.slice(0, 10)}
                {a.still_unpriceable === false && <> · it has bars now (the acknowledgment is moot)</>}{" "}
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy}
                  onClick={() => revoke.mutate({ ackId: a.id })}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {closed.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-repairs-closed">
          <summary className="adm-line adm-quiet">
            {closed.length} closed — stopped trading (a delisting form near the last bar); no repair,
            and their vendor 404 is expected
          </summary>
          <ul className="adm-problems adm-quietrows">
            {closed.map((t) => (
              <li key={t.security_id}>
                <b>{t.ticker ?? t.security_id}</b> — stopped trading {t.closed_at ?? "unknown"} · last bar{" "}
                {t.edge ?? "never"} · {t.thesis}
              </li>
            ))}
          </ul>
        </details>
      )}

      {applied.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-applied">
          <summary className="adm-line adm-quiet">
            {applied.length} price-symbol repair(s) in force (operator-applied)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {applied.map((c) => (
              <li key={c.id}>
                <b>{c.ticker ?? c.security_id}</b> — {c.action === "adopt" ? "adopted" : "set to"}{" "}
                {c.new_symbol ?? "the canonical ticker"} · {c.created_at.slice(0, 10)} ({c.basis}){" "}
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy || !c.undoable}
                  onClick={() => undo.mutate({ changeId: c.id })}
                >
                  Undo
                </button>
                {!c.undoable && <span className="adm-sub"> the master changed since — undo unavailable</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {cikApplied.length > 0 && (
        <details className="adm-quietlist" data-testid="adm-cik-applied">
          <summary className="adm-line adm-quiet">
            {cikApplied.length} successor-CIK re-point(s) in force (operator-applied)
          </summary>
          <ul className="adm-problems adm-quietrows">
            {cikApplied.map((c) => (
              <li key={c.id}>
                <b>{c.ticker ?? c.security_id}</b> — CIK {c.prior_cik} → {c.new_cik} ·{" "}
                {c.created_at.slice(0, 10)} ({c.basis}){" "}
                <button
                  type="button"
                  className="adm-minibtn"
                  disabled={busy || !c.undoable}
                  onClick={() => undoCik.mutate({ changeId: c.id })}
                >
                  Undo
                </button>
                {!c.undoable && <span className="adm-sub"> the master changed since — undo unavailable</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {failed && <div className="adm-err">{errText(failed.error)}</div>}
    </section>
  );
}
