import { Fragment, useState, type ReactNode } from "react";

import {
  type BasketMember,
  type BusinessTypeLeaf,
  type Segment,
  type ThesisDetail,
  useDeleteTriageSession,
  usePromoteThesis,
  useSectionData,
  useSetBusinessType,
  useTheses,
  useThesis,
  useTriageSession,
  useWorkbenchScored,
} from "../api/hooks";
import { ErrorToast } from "../components/ErrorToast";
import { exportKeptNames, toExportedName } from "../util/exportNames";
import { ChainEditor } from "./ChainEditor";
import {
  addLink,
  effectiveSegment,
  reconcileMemberSegments,
  removeLink,
  renameLink,
  reorderLink,
  sanitizeBasketForPromote,
} from "./chainOps";
import { clearedRestore, deserialize } from "./triageSession";
import { DDRail } from "./DDRail";
import { ScoredRow } from "./ScoredRow";
import { ThesisFields } from "./ThesisFields";
import { DISCOVERED } from "./useChainDraft";
import { errText, memberHasFundamentals } from "./format";

interface Props {
  header?: ReactNode;
  asof: string;
}

// S3 (re-scope) — the stale-session age-gate threshold: an autosaved working session OLDER than this stops
// silently driving the editor and surfaces the resume-vs-start-from-the-saved-Basket choice instead (it is
// NEVER auto-deleted — expiry ends the silent restore, not the prune; principle #2). Named + trivially
// tunable; the always-on "resumed autosave" badge (ChainEditor) carries awareness below the threshold.
const STALE_SESSION_DAYS = 3;
const DAY_MS = 24 * 60 * 60 * 1000;
const STALE_SESSION_MS = STALE_SESSION_DAYS * DAY_MS;

/** The Workbench (Phase-2 front half): a narrative → a scored, structured basket → promote to the Board.
 *  DISPLAY · SCORE · PROMOTE (S4) + AUTHORING (S4b): the operator builds/edits the value chain in an edit
 *  mode (ChainEditor), saving through the full-replace promote; the meters re-derive on the new structure. */
export function Workbench({ header, asof }: Props) {
  const thesesQ = useTheses();
  const theses = thesesQ.data ?? [];

  // entry point: a minimal selector over the theses, defaulting to the first (no wire add).
  const [pickedId, setPickedId] = useState("");
  const thesisId = pickedId || theses[0]?.id || "";

  const [seg, setSeg] = useState<string | null>(null);
  const [pickedMemberId, setPickedMemberId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // #1–3 (chain-editing Phase 2) — the value-chain topology editor on the SCORE view. `editLinks` is the
  // reveal-toggle: off = the tab strip is pure navigation (inverse-loudness #7 — the edit affordances
  // don't render until asked for); on = per-link rename / reorder / remove + the `+ add link` bootstrap.
  // `newLink` is the add-a-link input. Both reset per thesis (switchThesis) and on entering the editor.
  const [editLinks, setEditLinks] = useState(false);
  const [newLink, setNewLink] = useState("");
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
  // Bumped by "Clear" to force a fresh ChainEditor remount (it seeds ONCE at mount) after the session is wiped.
  const [editorNonce, setEditorNonce] = useState(0);
  // "Clear" seeds the editor from an EMPTY chain (no companies) keeping the term-set seeds — NOT from the
  // thesis's persisted basket. One-shot: reset on exit/switch so a later re-open shows the real saved state.
  const [cleared, setCleared] = useState(false);
  // S3 (re-scope) — one-shot: the editor was remounted BY the Re-scope action, so it seeds fresh from the
  // THESIS (the whole saved Basket freezes; the candidate pile starts empty) and fires ONE draft at mount
  // (`autoDraft`). Reset on exit/switch like `cleared`; the two one-shots are mutually exclusive (each
  // action's onSuccess clears the other, so a Clear after a Re-scope never auto-drafts a blank canvas).
  const [rescoping, setRescoping] = useState(false);
  // S3 (stale session) — the operator's answer to the stale-autosave choice panel: "" = unanswered (the
  // panel gates a stale restore), "resume" = mount the restored session anyway, "fresh" = start from the
  // saved Basket (that click's delete is the ONLY discard — the gate itself never destroys the prune).
  // Reset per thesis in switchThesis, like `dismissedIncompatible` (the sibling choice it mirrors).
  const [staleSessionChoice, setStaleSessionChoice] = useState<"" | "resume" | "fresh">("");
  const scored = scoredQ.data;
  const segments = scored?.segments ?? [];
  const members = scored?.members ?? [];
  // TRIAGE: the scored members keyed by security_id — passed to the editor for the "fundamentals loaded" badge
  // (a cheap read-time join, no fetch; reflects the last saved state).
  const scoredById = Object.fromEntries(members.map((m) => [m.security_id, m]));

  // The `fund` sleeves (ETF Sleeve, Slice 1) are the theme's low-torque EXPRESSION — semantically NOT a
  // value-chain link (they carry segment: null), so they never belong to a segment tab and would be dropped
  // by the segment filter below. Split them out: the value-chain view scores the equity `chainMembers`, and
  // the sleeves render in their OWN always-visible section (with price). Display-only; a sleeve never touches
  // the call path (#4/#6).
  const sleeveMembers = members.filter((m) => m.instrument_kind === "etf");
  const chainMembers = members.filter((m) => m.instrument_kind !== "etf");
  // The seeded basket is FLAT until authored — when it has segments, names group under the selected
  // link; until then they render as one flat scored list so the meters always show.
  const grouped = segments.length > 0;
  // Group by the EFFECTIVE segment (chain-editing Phase 1): a NULL or ORPHAN (label not in the chain)
  // name normalizes to Discovered so it surfaces under a synthesized Discovered tab instead of failing
  // the raw `m.segment === activeSeg` filter and vanishing (#9 / WB#2). Only when there IS a real chain
  // (`grouped`) — a flat pre-decompose basket stays one ungrouped list.
  const anyDiscovered =
    grouped && chainMembers.some((m) => effectiveSegment(m, segments) === DISCOVERED);
  const renderSegments =
    anyDiscovered && !segments.some((s) => s.label === DISCOVERED)
      ? [...segments, { label: DISCOVERED, descriptor: null }]
      : segments;
  const countFor = (label: string) =>
    chainMembers.filter((m) => effectiveSegment(m, segments) === label).length;
  const activeSeg = grouped ? (seg ?? renderSegments[0]?.label ?? null) : null;
  const shownMembers = activeSeg
    ? chainMembers.filter((m) => effectiveSegment(m, segments) === activeSeg)
    : chainMembers;
  // Selection resolves across the scored chain AND the sleeves — a `fund` row drives the DD rail (SleeveRail)
  // just like a scored name. The default (nothing picked) stays the first chain member, never a sleeve.
  const selectedMember =
    [...shownMembers, ...sleeveMembers].find((m) => m.security_id === pickedMemberId) ??
    shownMembers[0] ??
    null;
  const linkCount = new Set(members.map((m) => m.segment).filter(Boolean)).size;
  // the authorship seam: who placed each name (operator now; S5's drafter will add "drafted")
  const authoredByFor = (sid: string) =>
    thesis?.basket.find((b) => b.security_id === sid)?.authored_by;

  // #4 (chain-editing Phase 1) — the DD-rail segment control for the selected name. `options` = the live
  // chain's real links (Discovered is the automatic floor, never a checkbox); `current` = the name's live
  // memberships read from the SPINE (`thesis.basket`) and normalized through effectiveSegment (a NULL /
  // orphan label → Discovered → dropped), so an unsorted name reads as no checked links → the rail's
  // "Unsorted (Discovered)" line. Deduped: a name never shows the same link twice.
  const segmentOptions = segments.map((s) => s.label).filter((l) => l !== DISCOVERED);
  const selectedSegments = selectedMember
    ? [
        ...new Set(
          (thesis?.basket ?? [])
            .filter((b) => b.security_id === selectedMember.security_id)
            .map((b) => effectiveSegment(b, segments))
            .filter((l) => l !== DISCOVERED),
        ),
      ]
    : [];

  const activeName = thesis?.name ?? theses.find((t) => t.id === thesisId)?.name ?? "…";

  const switchThesis = (id: string) => {
    setPickedId(id);
    setSeg(null);
    setPickedMemberId(null);
    setEditing(false);
    setEditLinks(false); // the link-editor reveal is per thesis / per scored session
    setNewLink("");
    setChainSaved(false);
    setDismissedIncompatible(false); // the incompatible-session choice is per thesis
    setCleared(false); // the cleared state is per thesis / per edit session
    setRescoping(false); // the re-scope one-shot is per thesis / per edit session
    setStaleSessionChoice(""); // the stale-session choice is per thesis
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

  // Business-Type M1 (#10): the operator RE-TAGS one security's business type — overruling the SIC
  // maps for one name, or clearing back to derived (null — the visible revert, WB #1). A MASTER-level
  // identity write (durable across theses), NOT a promote: the spine carries no type field. The hook
  // invalidates the scored read, so the chip/leaf re-derive everywhere they join.
  const setBType = useSetBusinessType();
  const retagBusinessType = (securityId: string, businessType: BusinessTypeLeaf | null) =>
    setBType.mutate({ securityId, businessType });

  // Slice 2b — INCLUDE an available N-PORT holding into the basket (from the SleeveRail). Persists via the
  // promote writer (a scored-view change persists immediately), APPENDING a member in AddName's shape — an
  // include IS a placement: role "—", uncharacterized (item F), a hand-add (auto-signed-off, model-draft
  // description until the operator types one — S1 honest authorship). Idempotent: a
  // name already in the basket is a no-op. The scored read re-derives (the new name joins its master
  // identity + scores); the holdings overlap is a PULL SNAPSHOT and deliberately does NOT re-fetch (cost
  // thread) — the button reflects the live basket.
  const includeHolding = (securityId: string, ticker: string) => {
    if (!thesis) return;
    if (thesis.basket.some((b) => b.security_id === securityId)) return;
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: [
        ...thesis.basket,
        {
          ticker,
          role: "—",
          security_id: securityId,
          segment: null,
          conviction: null,
          authored_by: "system_drafted" as const, // no description typed — a model draft (S1)
          signed_off: true, // the include click IS the endorsement (a hand-add — auto sign-off)
        },
      ],
      segments: thesis.segments,
    });
  };

  // The include's visible inverse (#1): drop a member from the basket (returns to the prior state, destroys
  // nothing — facts/prices stay in the bitemporal store; re-including re-binds the same id). Same promote
  // writer, resending the chain minus that member.
  const removeMember = (securityId: string) => {
    if (!thesis) return;
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: thesis.basket.filter((b) => b.security_id !== securityId),
      segments: thesis.segments,
    });
  };

  // The shared SCORE-view chain writer (chain-editing Phase 1–2): sanitize (self-heal any pre-existing
  // orphan so a stale label can't 422 the validator) → the SAME full-replace promote the sibling handlers
  // use. Every mover + topology handler routes through it. Read from the SPINE (`thesis.basket`/
  // `thesis.segments`) — it carries the full BasketMembers (authored_by / signed_off / conviction /
  // surfaced_terms) the scored read does NOT. `segment` is display/structure, never a call input (#4): these
  // edits touch labels + rows only, never verdict/grade/exit-by. Reversible by construction (WB#1).
  const promoteChain = (next: { basket: BasketMember[]; segments: Segment[] }) => {
    if (!thesis) return;
    const clean = sanitizeBasketForPromote(next.basket, next.segments);
    promote.mutate({
      id: thesis.id,
      name: thesis.name,
      narrative: thesis.narrative,
      ticker: thesis.ticker ?? null,
      basket: clean.basket,
      segments: clean.segments,
    });
  };

  // #4 (Phase 1) — the DD-rail mover: rebuild ONE name's rows into the checked links (or the Discovered
  // floor when cleared), copying every per-name field. Clearing floors to the visible Discovered pen (never
  // null → never unreachable to re-select).
  const setMemberSegments = (securityId: string, labels: string[]) => {
    if (!thesis) return;
    promoteChain(reconcileMemberSegments(thesis.basket, thesis.segments, securityId, labels));
  };

  // #1–3 (Phase 2) — value-chain topology on the SCORE view: rename / reorder / add / remove links, each
  // MIRRORING useChainDraft's transform (rename cascades onto members; reorder swaps; add rejects blank/dup;
  // remove is multi-safe and routes a LAST placement to the Discovered floor) and immediate-promoting.
  const renameLinkOnScore = (oldLabel: string, newLabel: string) => {
    if (!thesis) return;
    promoteChain(renameLink(thesis.basket, thesis.segments, oldLabel, newLabel));
  };
  const reorderLinkOnScore = (label: string, dir: -1 | 1) => {
    if (!thesis) return;
    promoteChain(reorderLink(thesis.basket, thesis.segments, label, dir));
  };
  const addLinkOnScore = (label: string) => {
    if (!thesis) return;
    promoteChain(addLink(thesis.basket, thesis.segments, label));
  };
  const removeLinkOnScore = (label: string) => {
    if (!thesis) return;
    promoteChain(removeLink(thesis.basket, thesis.segments, label));
  };
  // the `+ add link` commit (works flat — the bootstrap): append + clear the input. `addLink` rejects a
  // blank/dup, so a stray submit is a harmless no-op.
  const commitAddLink = () => {
    const l = newLink.trim();
    if (!l) return;
    addLinkOnScore(l);
    setNewLink("");
  };

  // Gate the editor mount on the prune-session GET settling — the three (really four) restore cases. A restore
  // must seed at MOUNT (the editor snapshots its state in useState initializers, no in-hook re-sync), so we don't
  // mount ChainEditor until we know what to seed it with.
  // Clear: wipe the saved prune session and re-seed the editor with an EMPTY value chain + companies, KEEPING
  // the term-set seeds (a blank canvas to re-draft). `deleteSession` nulls the restore cache; we flip `cleared`
  // and bump the nonce — the nonce in the key force-remounts ChainEditor (cancelling the old instance's pending
  // autosave so it can't re-create the session), and `cleared` makes the remount seed from `clearedRestore`.
  const startOver = () => {
    if (
      !window.confirm(
        "Clear the value chain and all companies from the editor? Your term-set seeds are kept, and your saved prune is discarded.",
      )
    )
      return;
    deleteSession.mutate(undefined, {
      onSuccess: () => {
        setCleared(true);
        setRescoping(false); // mutually exclusive with the re-scope one-shot (a cleared mount never auto-drafts)
        setEditorNonce((n) => n + 1);
      },
    });
  };
  // S3 — Re-scope (the maintenance loop, one button): clear the TRANSIENT candidate pile and re-run
  // discovery on the CURRENT (refined) term set, keeping the WHOLE saved Basket frozen. The only thing
  // cleared is the session blob (old To-Review/ambiguous/absent buckets + unsaved drafted names) — nothing
  // on the spine is touched (#9: the delete is a blob unlink; a dropped candidate that still matches
  // re-surfaces by re-derivation). The remount seeds FROM THE THESIS (`mountEditor(t)` — deliberately NOT
  // `clearedRestore`, which empties the chain): by the hook's own math establishedKeys = base ∩ base =
  // every saved member, so the entire Basket (accepted AND system_drafted) freezes into the Basket panel
  // and the working pile starts empty. `rescoping` makes that remount fire ONE draft (`autoDraft`) — the
  // fresh candidates under the current terms.
  const startRescope = () => {
    const count = thesis?.basket.length ?? 0;
    if (
      !window.confirm(
        `Re-scope: clear the candidate pile and re-run discovery on the current term set? Your saved Basket (${count} ${count === 1 ? "name" : "names"}) is kept frozen. Unsaved candidate work and the autosaved prune are discarded.`,
      )
    )
      return;
    deleteSession.mutate(undefined, {
      onSuccess: () => {
        setCleared(false); // mutually exclusive with the Clear one-shot (fresh-from-thesis, never an empty chain)
        setRescoping(true);
        setEditorNonce((n) => n + 1);
      },
    });
  };
  const mountEditor = (
    t: ThesisDetail,
    restored?: Parameters<typeof ChainEditor>[0]["restored"],
    restoredUpdatedAt?: string,
  ) => (
    <ChainEditor
      key={`${t.id}:${editorNonce}`}
      thesis={t}
      asof={asof}
      restored={restored}
      // S3: the restored session's server timestamp — drives the quiet "resumed autosave" badge, so a
      // session-driven editor is never indistinguishable from a spine-driven one. Passed ONLY on the
      // real-session restore path (never for Clear's synthetic restore or a fresh mount).
      restoredUpdatedAt={restoredUpdatedAt}
      // S3: true only on the re-scope remount — that mount fires ONE draft (ref-guarded in the editor).
      autoDraft={rescoping}
      onStartOver={startOver}
      onRescope={startRescope}
      onDone={(saved) => {
        setEditing(false);
        setCleared(false); // the cleared state is one-shot — re-entry shows the real saved state
        setRescoping(false); // the re-scope one-shot too — re-entry never re-fires the draft
        setChainSaved(saved); // a saved exit surfaces the re-entry note; a discard clears it
      }}
      scoredById={scoredById}
    />
  );
  const renderEditor = (t: ThesisDetail) => {
    // 0) CLEARED — the operator hit Clear: seed an EMPTY chain keeping the term seeds (overrides the session).
    if (cleared) return mountEditor(t, clearedRestore(t.term_set));
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
    // 5) S3 — the stale-session age-gate: a restorable session OLDER than the threshold must not silently
    // drive the editor (the 159-vs-160 trap: an old prune resumes invisibly and clobbers the Basket on the
    // next Save). Surface a CHOICE — resume it, or start from the saved Basket — and NEVER auto-delete:
    // expiry ends the silent restore, only the operator's click discards the prune (principle #2). A
    // malformed timestamp parses NaN → not stale (never lose a prune to a parse bug).
    if (staleSessionChoice === "fresh") return mountEditor(t); // chose the saved Basket (delete in flight)
    const ageMs = Date.now() - Date.parse(env.updated_at);
    if (ageMs > STALE_SESSION_MS && staleSessionChoice !== "resume") {
      const days = Math.floor(ageMs / DAY_MS);
      return (
        <div className="wb-session-note">
          <p className="muted">
            Autosaved working session from {new Date(env.updated_at).toLocaleDateString()} ({days}{" "}
            days old) — resume it, or start from the saved Basket?
          </p>
          <button
            type="button"
            className="wb-mini"
            onClick={() => setStaleSessionChoice("resume")}
          >
            Resume
          </button>
          <button
            type="button"
            className="wb-mini ghost"
            onClick={() => {
              deleteSession.mutate();
              setStaleSessionChoice("fresh");
            }}
          >
            Start from the saved Basket
          </button>
        </div>
      );
    }
    // session present + restorable (fresh, or explicitly resumed) → seed from the blob. The timestamp
    // rides along so the editor can badge the restored mount.
    return mountEditor(t, result, env.updated_at);
  };

  return (
    <div className="wb-shell">
      {header}

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
                  {thesis && (
                    <button
                      type="button"
                      className="wb-mini ghost wb-edit-links"
                      aria-pressed={editLinks}
                      onClick={() => setEditLinks((v) => !v)}
                    >
                      {editLinks ? "done" : "✎ edit links"}
                    </button>
                  )}
                </div>
                {editLinks ? (
                  // #1–3 — the inline link editor (rename / reorder / remove per link) + the `+ add link`
                  // bootstrap, which works FLAT (no links yet) so the first link is buildable from SCORE.
                  // The synthesized Discovered floor shows read-only (names leave it via a name's links).
                  <div className="chain chain-edit">
                    {segments.length === 0 && (
                      <span className="seg-edit-hint">No links yet — name your first one.</span>
                    )}
                    {renderSegments.map((s) =>
                      s.label === DISCOVERED ? (
                        <span
                          key={s.label}
                          className="seg-floor"
                          title="the unsorted floor — names land here when a link is cleared or removed; move them out via a name's links"
                        >
                          {s.label} · {countFor(s.label)}
                        </span>
                      ) : (
                        <div key={s.label} className="seg-edit">
                          <input
                            className="seg-rename"
                            defaultValue={s.label}
                            aria-label={`rename ${s.label}`}
                            disabled={promote.isPending}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                const v = (e.target as HTMLInputElement).value.trim();
                                if (v && v !== s.label) renameLinkOnScore(s.label, v);
                              }
                            }}
                            onBlur={(e) => {
                              const v = e.target.value.trim();
                              if (v && v !== s.label) renameLinkOnScore(s.label, v);
                              else e.target.value = s.label; // revert a blank / unchanged edit
                            }}
                          />
                          <button
                            type="button"
                            className="seg-move"
                            disabled={
                              promote.isPending ||
                              segments.findIndex((x) => x.label === s.label) <= 0
                            }
                            aria-label={`move ${s.label} left`}
                            onClick={() => reorderLinkOnScore(s.label, -1)}
                          >
                            ←
                          </button>
                          <button
                            type="button"
                            className="seg-move"
                            disabled={
                              promote.isPending ||
                              segments.findIndex((x) => x.label === s.label) >=
                                segments.length - 1
                            }
                            aria-label={`move ${s.label} right`}
                            onClick={() => reorderLinkOnScore(s.label, 1)}
                          >
                            →
                          </button>
                          <button
                            type="button"
                            className="seg-remove"
                            disabled={promote.isPending}
                            aria-label={`remove ${s.label}`}
                            onClick={() => removeLinkOnScore(s.label)}
                          >
                            ✕
                          </button>
                        </div>
                      ),
                    )}
                    <div className="seg-add">
                      <input
                        className="seg-add-input"
                        value={newLink}
                        placeholder="new link name"
                        aria-label="new link name"
                        disabled={promote.isPending}
                        onChange={(e) => setNewLink(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            commitAddLink();
                          }
                        }}
                      />
                      <button
                        type="button"
                        className="seg-add-btn"
                        disabled={promote.isPending || !newLink.trim()}
                        onClick={commitAddLink}
                      >
                        + add link
                      </button>
                    </div>
                  </div>
                ) : grouped ? (
                  <>
                    {/* compact tabs (label + count) that WRAP — the per-tab descriptor blew the row out
                        into a horizontal scroll strip; the ACTIVE segment's descriptor is the line below */}
                    <div className="chain">
                      {renderSegments.map((s, i) => (
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
                          {i < renderSegments.length - 1 ? (
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
                      : "No value chain yet — the seeded basket isn't decomposed into links. Use “✎ edit links” to add one, or “Edit the basket” to draft it from your narrative."}
                  </div>
                )}
                <div className="wb-authoring-gap">
                  <button
                    type="button"
                    className="wb-edit-btn"
                    onClick={() => {
                      setChainSaved(false);
                      setEditLinks(false); // leave the link editor when opening the basket builder
                      setEditing(true);
                    }}
                    disabled={!thesis}
                  >
                    ✎ Edit the basket
                  </button>
                  <span className="note">
                    Arrange the value chain right here — <b>✎ edit links</b> above renames, reorders, adds
                    &amp; removes links, and a name&apos;s links live on its card. Open <b>Edit the basket</b>{" "}
                    to add names or <b>Draft from narrative</b>.
                  </span>
                </div>
                {/* D — the visible inverse of Save (reversibility #1): say OUT LOUD that Save isn't a door
                    closing. Honest scope: the saved BASKET is editable on return; the draft-time discovery
                    context (matched terms, flags, To-Review queues) is run state — a re-draft re-runs it. */}
                {chainSaved && (
                  <div className="toast show">
                    ✓ Chain saved. Reopen it anytime with <b>✎ Edit the basket</b> — you'll be editing your
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
                      {/* the FUNNEL, visible (gate 2→3 progress): confirmed-data coverage over the scored
                          basket (not the segment view) — the same memberHasFundamentals rule everywhere.
                          Over `chainMembers` (the scored equities): a `fund` sleeve carries no fundamentals
                          by nature, so counting it would peg the meter below 100% forever. */}
                      {" · "}data confirmed on {chainMembers.filter(memberHasFundamentals).length} of{" "}
                      {chainMembers.length} basket-wide
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
                      asof={asof}
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

              {/* ETF sleeves (ETF Sleeve) — the theme's low-torque expression, in their OWN group (not a
                  value-chain segment) so a surfaced `fund` sleeve always renders with its price, whatever
                  segment tab is active. Display-only: price is context, never a signal (#4/#6). Selecting a
                  sleeve drives the DD rail (SleeveRail) — its N-PORT holdings + basket overlap show there. */}
              {sleeveMembers.length > 0 && (
                <section className="sect wb-sleeves">
                  <div className="sect-h">
                    <span>ETF sleeve{sleeveMembers.length === 1 ? "" : "s"}</span>{" "}
                    <em>— a low-torque expression of the theme; price is context, not a signal</em>
                  </div>
                  {sleeveMembers.map((m) => (
                    <ScoredRow
                      key={m.security_id}
                      member={m}
                      selected={m.security_id === selectedMember?.security_id}
                      onSelect={() => setPickedMemberId(m.security_id)}
                      thesisId={thesisId}
                      asof={asof}
                    />
                  ))}
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
                          {m.segment ? <small>{m.segment}</small> : null}
                          {/* HONEST authorship (S1): the tag reads who wrote the DESCRIPTION — "your
                              words" only when the operator actually edited it (operator_edited);
                              everything else (incl. the retired legacy operator_set) is a model draft. */}
                          {auth ? (
                            <span className="wb-author">
                              {auth === "operator_edited" ? "your words" : "model draft"}
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
                onRetag={retagBusinessType}
                retagPending={setBType.isPending}
                thesisId={thesisId}
                asof={asof}
                // Slice 2b — the sleeve's include/remove write path (used only by the SleeveRail branch).
                // basketSids is the LIVE basket, so an included holding's button flips to "✓ included"
                // as soon as the promote's thesis refetch lands — no holdings re-pull (cost thread).
                sleeve={{
                  basketSids: new Set(
                    (thesis?.basket ?? [])
                      .map((b) => b.security_id)
                      .filter((id): id is string => Boolean(id)),
                  ),
                  onInclude: includeHolding,
                  onRemove: removeMember,
                  includePending: promote.isPending,
                }}
                // #4 (chain-editing Phase 1) — the value-chain mover for the selected equity. The ETF
                // branch returns early (SleeveRail), so a sleeve never renders it; a null selection omits it.
                segmentControl={
                  selectedMember
                    ? {
                        options: segmentOptions,
                        current: selectedSegments,
                        onChange: (labels) => setMemberSegments(selectedMember.security_id, labels),
                        pending: promote.isPending,
                      }
                    : undefined
                }
              />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
