import { Fragment, useState } from "react";

import {
  type ScoredMemberOut,
  usePromoteThesis,
  useTheses,
  useThesis,
  useWorkbenchScored,
} from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { ChainEditor } from "./ChainEditor";
import { DDRail } from "./DDRail";
import { ScoredRow } from "./ScoredRow";
import { ThesisFields } from "./ThesisFields";
import { archLabel, errText } from "./format";

interface Props {
  asof: string;
  onAsofChange: (asof: string) => void;
  onBack: () => void;
}

/** The Workbench (Phase-2 front half): a narrative → a scored, structured basket → promote to the Board.
 *  DISPLAY · SCORE · PROMOTE (S4) + AUTHORING (S4b): the operator builds/edits the value chain in an edit
 *  mode (ChainEditor), saving through the full-replace promote; the meters re-derive on the new structure. */
export function Workbench({ asof, onAsofChange, onBack }: Props) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];

  // entry point: a minimal selector over the theses, defaulting to the first (no wire add).
  const [pickedId, setPickedId] = useState("");
  const thesisId = pickedId || theses[0]?.id || "";

  const [seg, setSeg] = useState<string | null>(null);
  const [pickedMemberId, setPickedMemberId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

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

  const thesis = thesisQ.data;
  const scored = scoredQ.data;
  const segments = scored?.segments ?? [];
  const members = scored?.members ?? [];

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
    promote.reset();
  };

  const startCreate = () => {
    setFormMode("create");
    setEditing(false);
    setFormName("");
    setFormNarrative("");
    promote.reset();
  };

  const startEditNarrative = () => {
    if (!thesis) return;
    setFormMode("edit");
    setEditing(false);
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

  const onPromote = () => {
    if (!thesis) return;
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: thesis.basket,
      segments: thesis.segments,
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
          <a className="stub">Scoreboard</a>
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
              <ChainEditor key={thesis.id} thesis={thesis} onDone={() => setEditing(false)} />
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
                  <div className="chain">
                    {segments.map((s, i) => (
                      <Fragment key={s.label}>
                        <button
                          type="button"
                          className={`seg${s.label === activeSeg ? " on" : ""}`}
                          onClick={() => {
                            setSeg(s.label);
                            setPickedMemberId(null);
                          }}
                        >
                          <div className="sn">{s.label}</div>
                          <div className="smeta">
                            <span className="ct">
                              {countFor(s.label)} {countFor(s.label) === 1 ? "name" : "names"}
                            </span>
                            {s.descriptor ? <> · {s.descriptor}</> : null}
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
                    onClick={() => setEditing(true)}
                    disabled={!thesis}
                  >
                    ✎ Edit the chain
                  </button>
                  <span className="note">
                    Build &amp; edit the value chain by hand — or, in the editor, <b>Draft from narrative</b>{" "}
                    to have the drafter pre-fill the links + names for you to accept, edit, or drop.
                  </span>
                </div>
                {grouped && (
                  <div className="note">
                    Click a link to see its names. The whole chain is visible so you pick from a map —
                    not the two names that came to mind first.
                  </div>
                )}
              </section>

              {shownMembers.length > 0 && (
                <section className="sect">
                  <div className="sect-h">
                    <span>{activeSeg ?? "The basket, scored"}</span>{" "}
                    <em>
                      — {shownMembers.length} {shownMembers.length === 1 ? "name" : "names"}, scored
                    </em>
                  </div>
                  {shownMembers.map((m) => (
                    <ScoredRow
                      key={m.security_id}
                      member={m}
                      selected={m.security_id === selectedMember?.security_id}
                      onSelect={() => setPickedMemberId(m.security_id)}
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
                      return (
                        <span className="bchip" key={m.security_id}>
                          <b>{m.ticker ?? "◇"}</b>
                          <span className={`arch ${m.archetype}`}>{archLabel(m.archetype)}</span>
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
                    onClick={onPromote}
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
                onApplyArchetype={applyArchetype}
                applying={promote.isPending}
              />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
