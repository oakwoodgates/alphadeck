import { Fragment, useState } from "react";

import {
  type ScoredMemberOut,
  type ThesisDetail,
  useDeleteTriageSession,
  usePromoteThesis,
  useSectionData,
  useTheses,
  useThesis,
  useTriageSession,
  useWorkbenchScored,
} from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { exportKeptNames, toExportedName } from "../util/exportNames";
import { ChainEditor } from "./ChainEditor";
import { deserialize } from "./triageSession";
import { DDRail } from "./DDRail";
import { ScoredRow } from "./ScoredRow";
import { ThesisFields } from "./ThesisFields";
import { archLabel, errText, memberHasFundamentals } from "./format";

interface Props {
  asof: string;
  onAsofChange: (asof: string) => void;
  onBack: () => void;
  onOpenScoreboard: () => void;
}

/** The Workbench (Phase-2 front half): a narrative → a scored, structured basket → promote to the Board.
 *  DISPLAY · SCORE · PROMOTE (S4) + AUTHORING (S4b): the operator builds/edits the value chain in an edit
 *  mode (ChainEditor), saving through the full-replace promote; the meters re-derive on the new structure. */
export function Workbench({ asof, onAsofChange, onBack, onOpenScoreboard }: Props) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];

  // entry point: a minimal selector over the theses, defaulting to the first (no wire add).
  const [pickedId, setPickedId] = useState("");
  const thesisId = pickedId || theses[0]?.id || "";

  const [seg, setSeg] = useState<string | null>(null);
  const [pickedMemberId, setPickedMemberId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // D — Save-Chain re-entry legibility: set when the editor exits via a successful Save, so the scored view
  // SAYS the thesis is re-openable. Honest copy: re-entry restores the saved BASKET — not the draft-time
  // discovery context (matched terms / flags are run state; re-discovering is a re-draft). Cleared on any
  // navigation that changes what the note refers to.
  const [chainSaved, setChainSaved] = useState(false);

  // M1a/M1b — the thesis form, ONE panel with two modes: "create" (a new narrative) or "edit" (an
  // existing thesis's name/narrative). Both go through the single existing promote writer — create =
  // a null id + empty chain (drafted next); edit = the SAME id RESENDING the existing chain, so a
  // narrative tweak never wipes the operator's authored names.
  const [formMode, setFormMode] = useState<"" | "create" | "edit">("");
  const [formName, setFormName] = useState("");
  const [formNarrative, setFormNarrative] = useState("");

  const thesisQ = useThesis(thesisId);
  const scoredQ = useWorkbenchScored(thesisId, asof);
  const promote = usePromoteThesis();
  // the SECTION data runner (gate 2 at section granularity): prices + staged extraction for every name
  // in the active section — bounded by the section, extract-and-propose only (the operator still
  // ratifies per fact). The per-name row button stays the surgical option.
  const sectionData = useSectionData(thesisId);
  // The resumable prune session (triageSession.ts + the blob store). Fetched only in edit mode; the editor
  // mount is GATED on it settling (below) so a restore seeds at mount, and a load ERROR never looks like
  // "no session" (which would silently discard a real prune). `dismissedIncompatible` is the operator's
  // choice on an incompatible (schema-bumped) session — mount fresh; reset per thesis in switchThesis.
  const thesis = thesisQ.data;
  const sessionQ = useTriageSession(thesisId, editing && Boolean(thesis));
  const deleteSession = useDeleteTriageSession(thesisId);
  const [dismissedIncompatible, setDismissedIncompatible] = useState(false);
  const scored = scoredQ.data;
  const segments = scored?.segments ?? [];
  const members = scored?.members ?? [];
  // TRIAGE: the scored members keyed by security_id — passed to the editor for the "fundamentals loaded" badge
  // (a cheap read-time join, no fetch; reflects the last saved state).
  const scoredById = Object.fromEntries(members.map((m) => [m.security_id, m]));

  // The seeded basket is FLAT until authored — when it has segments, names group under the selected
  // link; until then they render as one flat scored list so the meters always show.
  const grouped = segments.length > 0;
  const countFor = (label: string) => members.filter((m) => m.segment === label).length;
  const activeSeg = grouped ? (seg ?? segments[0]?.label ?? null) : null;
  const shownMembers = activeSeg ? members.filter((m) => m.segment === activeSeg) : members;
  const selectedMember =
    shownMembers.find((m) => m.security_id === pickedMemberId) ?? shownMembers[0] ?? null;
  const linkCount = new Set(members.map((m) => m.segment).filter(Boolean)).size;
  // the authorship seam: who placed each name (operator now; S5's drafter will add "drafted")
  const authoredByFor = (sid: string) =>
    thesis?.basket.find((b) => b.security_id === sid)?.authored_by;

  const activeName = thesis?.name ?? theses.find((t) => t.id === thesisId)?.name ?? "…";

  const switchThesis = (id: string) => {
    setPickedId(id);
    setSeg(null);
    setPickedMemberId(null);
    setEditing(false);
    setChainSaved(false);
    setDismissedIncompatible(false); // the incompatible-session choice is per thesis
    sectionData.reset();
    promote.reset();
  };

  const startCreate = () => {
    setFormMode("create");
    setEditing(false);
    setChainSaved(false);
    setFormName("");
    setFormNarrative("");
    promote.reset();
  };

  const startEditNarrative = () => {
    if (!thesis) return;
    setFormMode("edit");
    setEditing(false);
    setChainSaved(false);
    setFormName(thesis.name);
    setFormNarrative(thesis.narrative);
    promote.reset();
  };

  const cancelForm = () => {
    setFormMode("");
    promote.reset();
  };

  const onSubmitForm = async () => {
    const name = formName.trim();
    const narrative = formNarrative.trim();
    if (!name || !narrative) return;
    try {
      if (formMode === "edit") {
        if (!thesis) return;
        // edit = the promote upsert with the SAME id, RESENDING the existing chain (basket + segments)
        // so a name/narrative tweak never wipes the operator's authored names. Scores re-derive on read.
        await promote.mutateAsync({
          id: thesis.id,
          name,
          narrative,
          ticker: thesis.ticker ?? null,
          basket: thesis.basket,
          segments: thesis.segments,
        });
      } else {
        // create = the promote upsert with a null id (no new write path); empty chain, drafted next.
        const created = await promote.mutateAsync({
          id: null,
          name,
          narrative,
          ticker: null,
          basket: [],
          segments: [],
        });
        if (created?.id) switchThesis(created.id); // land on the new (Incubating) thesis
      }
      setFormMode("");
    } catch {
      // promote.error holds the FastAPI detail — surfaced inline below; the form stays open (nothing lost)
    }
  };

  // IDENTITY MISMATCHES, computed client-side from data both queries already carry: the basket stores the
  // member's LABEL (bm.ticker); the scored read joins the BOUND master row's ticker by security_id. A
  // disagreement is the misbind class (a crossed label riding another company's id, or a drifted label) —
  // post-fix it fires only on pre-guard damage or a deliberately overridden pair, so the chip stays rare
  // (honest loudness). The same list feeds the promote bind-anyway override below.
  const idMismatches = (thesis?.basket ?? []).flatMap((b) => {
    const bound = b.security_id ? scoredById[b.security_id]?.ticker : null;
    return b.security_id && b.ticker && bound && b.ticker.toUpperCase() !== bound.toUpperCase()
      ? [{ securityId: b.security_id, stored: b.ticker, bound }]
      : [];
  });

  const onPromote = (identityOverrides?: string[]) => {
    if (!thesis) return;
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: thesis.basket,
      segments: thesis.segments,
      ...(identityOverrides?.length ? { identity_overrides: identityOverrides } : {}),
    });
  };

  // #10 apply: the operator confirms the derived archetype recommendation for ONE name. Persists via the
  // existing promote writer (one member's archetype changed -> operator_edited; the rest of the chain is
  // resent untouched, the wipe-trap guard); the scored read re-derives, clearing the chip. Never auto-applied.
  const applyArchetype = (
    securityId: string,
    archetype: NonNullable<ScoredMemberOut["archetype_hint"]>,
  ) => {
    if (!thesis) return;
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: thesis.basket.map((b) =>
        b.security_id === securityId
          ? { ...b, archetype, authored_by: "operator_edited" as const }
          : b,
      ),
      segments: thesis.segments,
    });
  };

  // Gate the editor mount on the prune-session GET settling — the three (really four) restore cases. A restore
  // must seed at MOUNT (the editor snapshots its state in useState initializers, no in-hook re-sync), so we don't
  // mount ChainEditor until we know what to seed it with.
  const mountEditor = (t: ThesisDetail, restored?: Parameters<typeof ChainEditor>[0]["restored"]) => (
    <ChainEditor
      key={t.id}
      thesis={t}
      asof={asof}
      restored={restored}
      onDone={(saved) => {
        setEditing(false);
        setChainSaved(saved); // a saved exit surfaces the re-entry note; a discard clears it
      }}
      scoredById={scoredById}
    />
  );
  const renderEditor = (t: ThesisDetail) => {
    // 1) ERROR — do NOT mount fresh: that makes a saved prune APPEAR GONE. Surface + retry.
    if (sessionQ.isError) {
      return (
        <div className="wb-session-note">
          <ErrorToast>
            Couldn't load your saved prune — {errText(sessionQ.error)}. Your work is safe; retry
            rather than starting over.
          </ErrorToast>
          <button type="button" className="wb-mini" onClick={() => sessionQ.refetch()}>
            Retry
          </button>
        </div>
      );
    }
    // 2) NOT SETTLED YET — wait before mounting. ChainEditor seeds its state ONCE at mount (useState
    // initializers); if we mounted before the GET resolved, it would seed EMPTY and the restore data arriving a
    // beat later would be ignored (same key → no remount). `isLoading` alone misses the window where the query
    // is enabled but its data is still absent, so gate strictly on success.
    if (!sessionQ.isSuccess) {
      return <p className="muted wb-session-note">Loading your saved prune…</p>;
    }
    const env = sessionQ.data?.session ?? null;
    // 3) no session (or the operator chose to start fresh over an incompatible one) → seed from the thesis.
    if (!env || dismissedIncompatible) return mountEditor(t);
    const result = deserialize(env);
    // 4) session present but INCOMPATIBLE (a breaking schema bump) — surface a choice, NEVER a silent seed-fresh.
    if (result.status === "incompatible") {
      return (
        <div className="wb-session-note">
          <p className="muted">
            Your saved prune for this thesis was written by an older version and can't be restored
            here. Keep editing fresh, or discard the saved session.
          </p>
          <button
            type="button"
            className="wb-mini"
            onClick={() => setDismissedIncompatible(true)}
          >
            Keep editing fresh
          </button>
          <button
            type="button"
            className="wb-mini ghost"
            onClick={() => {
              deleteSession.mutate();
              setDismissedIncompatible(true);
            }}
          >
            Discard saved session
          </button>
        </div>
      );
    }
    // session present + restorable → seed from the blob.
    return mountEditor(t, result);
  };

  return (
    <div className="wb-shell">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          ALPHA&nbsp;DECK <small>// research cockpit</small>
        </div>
        <nav className="nav">
          <a onClick={onBack}>Board</a>
          <a className="on">Workbench</a>
          <a onClick={onOpenScoreboard}>Scoreboard</a>
        </nav>
        <div className="spacer" />
        <label className="asof">
          as-of
          <input type="date" value={asof} onChange={(e) => onAsofChange(e.target.value)} />
        </label>
      </header>

      <div className="wb-top">
        <h1>{activeName}</h1>
        <span className="wb-badge">Workbench</span>
        {theses.length > 1 && (
          <select
            className="wb-thesis"
            value={thesisId}
            onChange={(e) => switchThesis(e.target.value)}
            aria-label="switch thesis"
          >
            {theses.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        )}
        {/* the front door: always available, even with zero theses (the empty-universe entry point) */}
        <button type="button" className="wb-new-btn" onClick={startCreate}>
          + New thesis
        </button>
        <div className="wb-flow">
          <b>NARRATIVE</b> › <b>DECOMPOSE</b> › <b>SCORE</b> › <b>PROMOTE</b>
        </div>
      </div>

      <div className="wb-body">
        {formMode ? (
          <>
            <main className="wb-main">
              <section className="sect">
                <div className="sect-h">
                  {formMode === "edit" ? "Edit the thesis" : "New thesis"}{" "}
                  <em>
                    {formMode === "edit"
                      ? "— refine the name or narrative; your chain is preserved"
                      : "— start from your own narrative"}
                  </em>
                </div>
                <ThesisFields
                  name={formName}
                  narrative={formNarrative}
                  onName={setFormName}
                  onNarrative={setFormNarrative}
                />
                <div className="wb-create-actions">
                  <button
                    type="button"
                    className="promote"
                    onClick={onSubmitForm}
                    disabled={promote.isPending || !formName.trim() || !formNarrative.trim()}
                  >
                    {promote.isPending
                      ? formMode === "edit"
                        ? "Saving…"
                        : "Creating…"
                      : formMode === "edit"
                        ? "Save changes"
                        : "Create thesis"}
                  </button>
                  <button type="button" className="wb-edit-btn" onClick={cancelForm}>
                    Cancel
                  </button>
                </div>
                {formMode === "edit" && thesis && thesis.basket.length > 0 && (
                  <div className="note">
                    Editing the narrative won't touch your {thesis.basket.length}-name chain. If the
                    story shifted, <b>re-draft</b> from the editor to refresh the names.
                  </div>
                )}
                {promote.isError && (
                  <ErrorToast>
                    Couldn't {formMode === "edit" ? "save" : "create"} — {errText(promote.error)}.{" "}
                    {formMode === "edit" ? "No changes were saved." : "Nothing was saved."}
                  </ErrorToast>
                )}
              </section>
            </main>
            <aside className="wb-rail">
              <div className="ddcard">
                <div className="dd-body">
                  <p className="muted">
                    {formMode === "edit" ? (
                      <>
                        The narrative is your words, preserved. Editing it here leaves the value
                        chain untouched — the names you placed stay placed.
                      </>
                    ) : (
                      <>
                        Name the thesis and capture the narrative in your words. You'll land on it
                        ready to <b>Draft from narrative</b> — the drafter proposes the value chain +
                        the names for you to ratify.
                      </>
                    )}
                  </p>
                </div>
              </div>
            </aside>
          </>
        ) : editing && thesis ? (
          <>
            <main className="wb-main">
              {renderEditor(thesis)}
            </main>
            <aside className="wb-rail">
              <div className="ddcard">
                <div className="dd-body">
                  <p className="muted">
                    Editing the chain — place names into links, add names from the master, then save.
                    The scores re-derive on the new structure (nothing is stored).
                  </p>
                </div>
              </div>
            </aside>
          </>
        ) : (
          <>
            <main className="wb-main">
              {scoredQ.error && <p style={{ color: "var(--neg)" }}>Failed to score the basket.</p>}

              <section className="sect">
                <div className="sect-h">
                  The narrative <em>— your words, preserved</em>
                  {thesis && (
                    <button
                      type="button"
                      className="wb-edit-narrative"
                      onClick={startEditNarrative}
                      aria-label="edit narrative"
                    >
                      ✎ Edit
                    </button>
                  )}
                </div>
                <div className="narrative">
                  {thesis?.narrative ?? "…"}
                  <span className="by">
                    Operator · the edge is yours, the chain and the names are the Workbench's job
                  </span>
                </div>
              </section>

              <section className="sect">
                <div className="sect-h">
                  The value chain <em>— where the money flows, decomposed from your narrative</em>
                </div>
                {grouped ? (
                  <>
                    {/* compact tabs (label + count) that WRAP — the per-tab descriptor blew the row out
                        into a horizontal scroll strip; the ACTIVE segment's descriptor is the line below */}
                    <div className="chain">
                      {segments.map((s, i) => (
                        <Fragment key={s.label}>
                          <button
                            type="button"
                            className={`seg${s.label === activeSeg ? " on" : ""}`}
                            onClick={() => {
                              setSeg(s.label);
                              setPickedMemberId(null);
                              sectionData.reset(); // the report describes the LAST run's section
                            }}
                          >
                            <div className="sn">{s.label}</div>
                            <div className="smeta">
                              <span className="ct">
                                {countFor(s.label)} {countFor(s.label) === 1 ? "name" : "names"}
                              </span>
                            </div>
                          </button>
                          {i < segments.length - 1 ? (
                            <span className="chain-arrow" aria-hidden="true">
                              ›
                            </span>
                          ) : null}
                        </Fragment>
                      ))}
                    </div>
                    {activeSeg && segments.find((s) => s.label === activeSeg)?.descriptor && (
                      <div className="chain-desc">
                        <b>{activeSeg}</b> —{" "}
                        {segments.find((s) => s.label === activeSeg)?.descriptor}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="wb-empty">
                    {scoredQ.isLoading
                      ? "Scoring…"
                      : "No value chain yet — the seeded basket isn't decomposed into links. Use “Edit the chain” to build it."}
                  </div>
                )}
                <div className="wb-authoring-gap">
                  <button
                    type="button"
                    className="wb-edit-btn"
                    onClick={() => {
                      setChainSaved(false);
                      setEditing(true);
                    }}
                    disabled={!thesis}
                  >
                    ✎ Edit the chain
                  </button>
                  <span className="note">
                    Build &amp; edit the value chain by hand — or, in the editor, <b>Draft from narrative</b>{" "}
                    to have the drafter pre-fill the links + names for you to accept, edit, or drop.
                  </span>
                </div>
                {/* D — the visible inverse of Save (reversibility #1): say OUT LOUD that Save isn't a door
                    closing. Honest scope: the saved BASKET is editable on return; the draft-time discovery
                    context (matched terms, flags, To-Review queues) is run state — a re-draft re-runs it. */}
                {chainSaved && (
                  <div className="toast show">
                    ✓ Chain saved. Reopen it anytime with <b>✎ Edit the chain</b> — you'll be editing your
                    saved basket (a re-draft is how you re-run discovery).
                  </div>
                )}
              </section>

              {shownMembers.length > 0 && (
                <section className="sect">
                  <div className="sect-h">
                    <span>{activeSeg ?? "The basket, scored"}</span>{" "}
                    <em>
                      — {shownMembers.length} {shownMembers.length === 1 ? "name" : "names"}, scored
                      {/* the FUNNEL, visible (gate 2→3 progress): confirmed-data coverage over the WHOLE
                          basket (not the segment view) — the same memberHasFundamentals rule everywhere */}
                      {" · "}data confirmed on {members.filter(memberHasFundamentals).length} of{" "}
                      {members.length} basket-wide
                    </em>
                    <button
                      type="button"
                      className="wb-mini ghost"
                      disabled={members.length === 0}
                      aria-label={`export ${members.length} shortlist names`}
                      onClick={() =>
                        exportKeptNames({
                          thesisName: activeName,
                          stage: "shortlist",
                          asof,
                          rows: members.map((m) =>
                            toExportedName({ ticker: m.ticker, name: m.name }),
                          ),
                        })
                      }
                    >
                      Export ({members.length})
                    </button>
                  </div>
                  {/* the SECTION get-data (gate 2 at section granularity): prices + staged extraction
                      for EVERY name in the active section, one deliberate click — bounded by the
                      section, cache-first both sides, proposes only (per-fact ratify stays yours).
                      The per-row button below remains the surgical option. */}
                  <div className="wb-section-data">
                    <button
                      type="button"
                      className="wb-mini"
                      disabled={sectionData.running || shownMembers.length === 0}
                      title="pull EOD prices (incremental, cache-first) + stage extraction candidates for every name in this section — proposes only; you still ratify per fact, purity stays yours"
                      onClick={() =>
                        sectionData.run(
                          shownMembers.map((m) => ({
                            security_id: m.security_id,
                            ticker: m.ticker,
                          })),
                        )
                      }
                    >
                      {sectionData.running
                        ? `getting data for ${shownMembers.length} names…`
                        : `⇣ get data — ${activeSeg ?? "all names"} (${shownMembers.length})`}
                    </button>
                    {sectionData.report && (
                      <span className="note">
                        prices on {sectionData.report.pricesOk} · candidates staged on{" "}
                        {sectionData.report.extractsOk} of {sectionData.report.total} — ratify per
                        name below
                      </span>
                    )}
                    {sectionData.report && sectionData.report.failures.length > 0 && (
                      <span className="flag">
                        ⚑ failed:{" "}
                        {sectionData.report.failures
                          .map((f) => `${f.ticker} (${f.what})`)
                          .join(", ")}
                      </span>
                    )}
                  </div>
                  {shownMembers.map((m) => (
                    <ScoredRow
                      key={m.security_id}
                      member={m}
                      selected={m.security_id === selectedMember?.security_id}
                      onSelect={() => setPickedMemberId(m.security_id)}
                      thesisId={thesisId}
                    />
                  ))}
                  <div className="note">
                    Scores are data-derived — purity from revenue mix, runway from cash &amp; burn,
                    catalysts from the feeds, dilution from convert overhang, market cap from price ×
                    shares. <b>Dilution is the ember risk axis</b> (more = more pressure); a bare “—”
                    means no data, not zero. Click a name for the evidence.
                  </div>
                </section>
              )}

              <section className="sect">
                <div className="sect-h">
                  Basket{" "}
                  <em>
                    — {members.length} {members.length === 1 ? "name" : "names"}
                    {linkCount > 0 ? ` across ${linkCount} ${linkCount === 1 ? "link" : "links"}` : ""}
                  </em>
                </div>
                <div className="basket-bot">
                  <div className="bmems">
                    {members.map((m) => {
                      const auth = authoredByFor(m.security_id);
                      const mm = idMismatches.find((x) => x.securityId === m.security_id);
                      return (
                        <span className="bchip" key={m.security_id}>
                          <b>{m.ticker ?? "◇"}</b>
                          {/* the identity-mismatch flag (the misbind class): the stored member LABEL
                              disagrees with the BOUND master row this id points at. Rare by design —
                              pre-guard damage or a deliberate override — so it's loud when it fires. */}
                          {mm && (
                            <span
                              className="flag"
                              title={`identity mismatch: this member is labeled ${mm.stored} but its security_id is bound to ${mm.bound} (${m.name ?? "see row"}). Facts, prices and filings follow the BOUND id. Re-pick the name (remove + re-add via search), or promote with the explicit bind-anyway override (logged).`}
                            >
                              ⚠ label {mm.stored} ≠ bound {mm.bound}
                            </span>
                          )}
                          {/* only a DECIDED archetype renders (item F) — unset is quiet, not "null" */}
                          {m.archetype && (
                            <span className={`arch ${m.archetype}`}>{archLabel(m.archetype)}</span>
                          )}
                          {m.segment ? <small>{m.segment}</small> : null}
                          {auth ? (
                            <span className="wb-author">
                              {auth === "operator_set" ? "operator" : auth.replace("_", " ")}
                            </span>
                          ) : null}
                        </span>
                      );
                    })}
                    {members.length === 0 && <span className="muted">No scored names yet.</span>}
                  </div>
                  <button
                    type="button"
                    className="promote"
                    onClick={() => onPromote()}
                    disabled={promote.isPending || !thesis}
                  >
                    {promote.isPending ? "Promoting…" : "Promote to thesis → Board (Incubating)"}
                  </button>
                </div>
                {promote.isSuccess && (
                  <div className="toast show">
                    ✓ Sent to the Board as Incubating — the back half takes over timing.
                  </div>
                )}
                {promote.isError && (
                  <ErrorToast>
                    Couldn't promote — {errText(promote.error)}. Nothing was sent.
                  </ErrorToast>
                )}
                {/* The bind-anyway override (the gate idiom — friction + a record, never a wall): promote
                    fail-closed on an identity mismatch; the override re-sends the SAME chain listing the
                    flagged members' ids, per-promote, logged server-side. Rendered only when the 422 was
                    an identity mismatch AND the flagged rows are visible above (the ⚠ chips). */}
                {promote.isError &&
                  errText(promote.error).startsWith("identity mismatch") &&
                  idMismatches.length > 0 && (
                    <button
                      type="button"
                      className="wb-mini"
                      title={idMismatches
                        .map((x) => `${x.stored} stays bound to ${x.bound}`)
                        .join("; ")}
                      onClick={() => onPromote(idMismatches.map((x) => x.securityId))}
                    >
                      Bind anyway — accept {idMismatches.length} identity{" "}
                      {idMismatches.length === 1 ? "mismatch" : "mismatches"} (logged)
                    </button>
                  )}
                <div className="seam">
                  <b>On promote</b>, the chain structure persists with the thesis — the segment each name
                  sits in (a label on basket_member). The scores aren't stored; they re-derive on read,
                  so a chain reopened months later shows current numbers.
                </div>
              </section>
            </main>

            <aside className="wb-rail">
              <DDRail
                member={selectedMember}
                // the persisted thesis-fit prose, bridged from the thesis basket by security_id
                thesisFit={
                  selectedMember
                    ? (thesis?.basket.find((b) => b.security_id === selectedMember.security_id)
                        ?.thesis_fit ?? null)
                    : null
                }
                onApplyArchetype={applyArchetype}
                applying={promote.isPending}
                thesisId={thesisId}
              />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
