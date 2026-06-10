import { Fragment, useState } from "react";

import { usePromoteThesis, useTheses, useThesis, useWorkbenchScored } from "../api/hooks";
import { DDRail } from "./DDRail";
import { ScoredRow } from "./ScoredRow";
import { archLabel } from "./format";

interface Props {
  asof: string;
  onAsofChange: (asof: string) => void;
  onBack: () => void;
}

function errText(e: unknown): string {
  const d = (e as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : "the request was rejected";
}

/** The Workbench (Phase-2 front half): a narrative → a scored, structured basket → promote to the Board.
 *  Slice 4 DISPLAYS + SCORES + PROMOTES the seeded chain (wired to the live scored endpoint, re-derived
 *  on read). AUTHORING — building/editing the chain — is Slice 4b; the gap is made honest in-product
 *  (the disabled affordance on the value-chain hero) so polish never masquerades as a finished product. */
export function Workbench({ asof, onAsofChange, onBack }: Props) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];

  // entry point: a minimal selector over the theses, defaulting to the first (no wire add).
  const [pickedId, setPickedId] = useState("");
  const thesisId = pickedId || theses[0]?.id || "";

  const [seg, setSeg] = useState<string | null>(null);
  const [pickedMemberId, setPickedMemberId] = useState<string | null>(null);

  const thesisQ = useThesis(thesisId);
  const scoredQ = useWorkbenchScored(thesisId, asof);
  const promote = usePromoteThesis();

  const thesis = thesisQ.data;
  const scored = scoredQ.data;
  const segments = scored?.segments ?? [];
  const members = scored?.members ?? [];

  // The seeded basket is FLAT — the value-chain decomposition is authored (Slice 4b), so a seeded
  // thesis has no segments yet. When it does (post-authoring), the names group under the selected
  // link; until then they render as one flat scored list so the meters always show.
  const grouped = segments.length > 0;
  const countFor = (label: string) => members.filter((m) => m.segment === label).length;
  const activeSeg = grouped ? (seg ?? segments[0]?.label ?? null) : null;
  const shownMembers = activeSeg ? members.filter((m) => m.segment === activeSeg) : members;
  const selectedMember =
    shownMembers.find((m) => m.security_id === pickedMemberId) ?? shownMembers[0] ?? null;
  const linkCount = new Set(members.map((m) => m.segment).filter(Boolean)).size;

  const activeName = thesis?.name ?? theses.find((t) => t.id === thesisId)?.name ?? "…";

  const switchThesis = (id: string) => {
    setPickedId(id);
    setSeg(null);
    setPickedMemberId(null);
    promote.reset();
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
        <div className="wb-flow">
          <b>NARRATIVE</b> › <b>DECOMPOSE</b> › <b>SCORE</b> › <b>PROMOTE</b>
        </div>
      </div>

      <div className="wb-body">
        <main className="wb-main">
          {scoredQ.error && <p style={{ color: "var(--neg)" }}>Failed to score the basket.</p>}

          <section className="sect">
            <div className="sect-h">
              The narrative <em>— your words, preserved</em>
            </div>
            <div className="narrative">
              {thesis?.narrative ?? "…"}
              <span className="by">Operator · the edge is yours, the chain and the names are the Workbench's job</span>
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
                  : "No value chain yet — the seeded basket isn't decomposed into links. The decomposition (the Workbench's hero) is authored in Slice 4b; this slice scores & promotes the flat basket below."}
              </div>
            )}
            {/* The honest authoring gap: this view scores & promotes a SEEDED basket — it cannot yet
                BUILD or decompose one. The disabled affordance + note say so plainly (authoring = 4b). */}
            <div className="wb-authoring-gap">
              <span className="wb-stub" aria-disabled="true">
                ＋ add / edit names
              </span>
              <span className="note">
                Authoring — build &amp; edit the value chain, place names, decompose the basket — ships in
                Slice 4b (with a ticker→security resolver). This view scores &amp; promotes the seeded
                basket.
              </span>
            </div>
            {grouped && (
              <div className="note">
                Click a link to see its names. The whole chain is visible so you pick from a map — not the
                two names that came to mind first.
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
                Scores are data-derived — purity from revenue mix, runway from cash &amp; burn, catalysts
                from the feeds, dilution from convert overhang, market cap from price × shares.{" "}
                <b>Dilution is the ember risk axis</b> (more = more pressure); a bare “—” means no data,
                not zero. Click a name for the evidence.
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
                {members.map((m) => (
                  <span className="bchip" key={m.security_id}>
                    <b>{m.ticker ?? "◇"}</b>
                    <span className={`arch ${m.archetype}`}>{archLabel(m.archetype)}</span>
                    {m.segment ? <small>{m.segment}</small> : null}
                  </span>
                ))}
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
              <div className="toast show err">
                Couldn't promote — {errText(promote.error)}. Nothing was sent.
              </div>
            )}
            <div className="seam">
              <b>On promote</b>, the chain structure persists with the thesis — the segment each name
              sits in (a label on basket_member). The scores aren't stored; they re-derive on read, so a
              chain reopened months later shows current numbers.
            </div>
          </section>
        </main>

        <aside className="wb-rail">
          <DDRail member={selectedMember} />
        </aside>
      </div>
    </div>
  );
}
