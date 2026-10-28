from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import DomainModel
from domain.enums import Authorship, ContextRole, TermTier


class TermSetEntry(DomainModel):
    """One discovery keyword in the thesis's persisted, tiered term set — the SIGNAL/BROAD input the EDGAR
    precision filter reads (it decides which EFTS hits PLACE a company).

    ``term`` is the EFTS phrase; ``tier`` is SIGNAL or BROAD (see ``TermTier``). ``authored_by`` + ``source``
    carry provenance so the future operator-edit UI (confirm / override a term's tier) is a PURE addition on
    the same object: the deterministic ``/terms`` guard writes ``system_drafted``; an operator override becomes
    ``operator_set`` / ``operator_edited``. It is STRUCTURE / config — never a fact or a number (#3).
    """

    term: str
    tier: TermTier
    authored_by: Authorship = (
        Authorship.SYSTEM_DRAFTED
    )  # the guard's default; the operator overrides later
    source: str | None = None  # candidate provenance, e.g. "keyword_gen" / "operator" / "seed"


class Segment(DomainModel):
    """A link in the thesis's value chain (the Workbench decomposition) — STRUCTURE, not a score.

    The per-segment candidate count is DERIVED on read (``len`` of members in the link), never stored.
    """

    label: str  # the link, e.g. "Reactor developers"
    descriptor: str | None = None  # the operator's per-segment tag, e.g. "catalyst-rich"


# THE TWO FIXED SILENT LINKS (Context and Bench). Every thesis carries both, always the LAST two links (after
# every real link and after Discovered, wherever Discovered sits — Discovered is NOT pinned and still counts
# toward the call). A company the OPERATOR places in either one stays on the card and visible but NEVER
# counts toward the call: ``voting_thesis`` removes those rows at the one assembly seam
# (``pipeline.core.assemble_from_pit``), so their signals can never warm, arm, veto or grade the card. That is
# the same stance as the chain's other quiet context — presence on the page is not a vote (#4: opinionated on
# timing, deferential on thesis; the operator decides which names the call listens to, never the model).
# Defined ONCE here; the promote route, the hydrate mapper, the draft reconciler and the validator below all
# read these, so the spelling and the descriptors cannot drift. NAMING GUARD: never call either one "watch" —
# ``CallCard.watch_members`` means the confirmation-only members of a call, an unrelated thing.
CONTEXT_LABEL = "Context"
BENCH_LABEL = "Bench"
SILENT_LABELS: tuple[str, str] = (CONTEXT_LABEL, BENCH_LABEL)  # display order
CONTEXT_DESCRIPTOR = (
    "Watched for context: buyers, counter-case names, giants where the theme is a slice. "
    "Never counts toward the call."
)
BENCH_DESCRIPTOR = (
    "Speculative names: pivots, distressed or thinly traded. Never counts toward the call."
)
_SILENT_DESCRIPTORS = {CONTEXT_LABEL: CONTEXT_DESCRIPTOR, BENCH_LABEL: BENCH_DESCRIPTOR}


def reserved_label(label: str | None) -> str | None:
    """The canonical spelling ("Context" / "Bench") when ``label`` names a silent link in ANY case or with
    stray whitespace, else ``None``. The write side canonicalizes through this (a "context" typed by hand is
    the link, never a 422 for case and never a lookalike second link); ``is_silent`` then compares exactly.
    """
    if label is None:
        return None
    folded = label.strip().casefold()
    for canonical in SILENT_LABELS:
        if folded == canonical.casefold():
            return canonical
    return None


def is_silent(member: BasketMember) -> bool:
    """Is this member in Context or Bench (it never counts toward the call)? An EXACT match on the stored
    label — canonicalization happens at the write (promote), so a stored row is already spelled right.
    """
    return member.segment in SILENT_LABELS


def with_silent_links(segments: list[Segment]) -> list[Segment]:
    """The chain with its two protected links in place: any segment whose label is a reserved name (any
    case — a lookalike or a wrong descriptor is replaced, not kept) is dropped, every other link keeps its
    given order untouched (Discovered included, wherever it sits), and Context then Bench are appended LAST
    with their fixed descriptors. Idempotent. The promote route applies it to what the operator sent and the
    hydrate mapper to what is stored, so both links read back last on every load with no data migration.
    """
    kept = [s for s in segments if reserved_label(s.label) is None]
    return [*kept, *(Segment(label=k, descriptor=d) for k, d in _SILENT_DESCRIPTORS.items())]


def silent_conflicts(basket: list[BasketMember]) -> list[str]:
    """One plain sentence per company that breaks "silent or voting, never both": it holds a Context / Bench row
    AND another row (a real link, Discovered, no link, or the other silent link). A company is keyed by its
    ``security_id`` — by its upper-cased ticker only when it has none — so run this AFTER the write's
    canonicalize and identity-coherence passes (sibling listings of one company merge into one id there). The
    caller refuses the write with these sentences; a row is never dropped to make the rule hold. Empty list =
    no conflict. In first-seen order."""
    rows: dict[tuple[str, object], list[BasketMember]] = {}
    for m in basket:
        key: tuple[str, object] = (
            ("id", m.security_id)
            if m.security_id is not None
            else ("ticker", m.ticker.strip().upper())
        )
        rows.setdefault(key, []).append(m)
    out: list[str] = []
    for group in rows.values():
        silent = list(dict.fromkeys(m.segment for m in group if is_silent(m)))
        if not silent or len(group) < 2:
            continue
        ticker = group[0].ticker.strip()
        if len(silent) == 2:
            out.append(f"{ticker} is in both {silent[0]} and {silent[1]}")
        elif any(not is_silent(m) for m in group):
            out.append(f"{ticker} is in {silent[0]} and also in another link")
        else:
            out.append(f"{ticker} is listed more than once in {silent[0]}")
    return out


def context_role_conflicts(basket: list[BasketMember]) -> list[str]:
    """One plain sentence per company whose rows carry two or more DIFFERENT non-null context roles — the
    role is per company per card, so a company has one. A company is keyed by its ``security_id`` (run this
    AFTER the write's canonicalize and identity-coherence passes, where sibling listings of one company merge
    into one id); a row with no ``security_id`` is never grouped, so it can neither conflict nor be conflicted
    with. A NULL row is no conflict (it inherits, ``with_inherited_context_roles``). The caller refuses the
    write with these sentences; a row is never dropped or rewritten to make the rule hold. Empty list = no
    conflict. In first-seen order."""
    sent: dict[UUID, dict[ContextRole, None]] = {}  # per company: the distinct roles, first-seen
    ticker: dict[UUID, str] = {}
    for m in basket:
        if m.security_id is None or m.context_role is None:
            continue
        ticker.setdefault(m.security_id, m.ticker.strip())
        sent.setdefault(m.security_id, {})[m.context_role] = None
    return [
        f"{ticker[sid]} was sent with {len(roles)} different context roles ({', '.join(roles)})"
        for sid, roles in sent.items()
        if len(roles) > 1
    ]


def with_inherited_context_roles(basket: list[BasketMember]) -> list[BasketMember]:
    """The basket with every NULL-role row of a company that carries a role on another row filled with that
    role — the write-side half of "the role is stored on every row of a company". It is what lets a hand-add
    or a re-draft that puts an existing company into a second link (the new row arrives with no role) succeed
    instead of silently splitting the company's role. Keyed by ``security_id``; a row with none is never
    grouped, and a company with no role on any row stays all-NULL (unset is never defaulted, and clearing =
    NULL on every row). A company holding two different roles is left untouched — that is the conflict the
    caller already refused (``context_role_conflicts``), not a case to guess at. A new list; a row that
    inherits nothing is returned as the same object, and the argument is untouched."""
    roles: dict[UUID, set[ContextRole]] = {}
    for m in basket:
        if m.security_id is not None and m.context_role is not None:
            roles.setdefault(m.security_id, set()).add(m.context_role)
    out: list[BasketMember] = []
    for m in basket:
        held = roles.get(m.security_id) if m.security_id is not None else None
        if m.context_role is None and held is not None and len(held) == 1:
            out.append(m.model_copy(update={"context_role": next(iter(held))}))
        else:
            out.append(m)
    return out


def voting_thesis(thesis: Thesis) -> Thesis:
    """The same thesis with every silent ROW (Context / Bench) removed from the basket — the only roster the
    call reads. Per-row is per-company: the write refuses a company that holds a silent row AND another row
    (a company is silent or voting, never both), so removing silent rows never removes a voting company's
    vote. A copy; the argument is untouched (history views hydrate the roster from a snapshot and swap it
    onto the thesis after construction, so the filter must read ``segment`` off the basket as given).
    """
    return thesis.model_copy(update={"basket": [m for m in thesis.basket if not is_silent(m)]})


class BasketMember(DomainModel):
    ticker: str
    role: str  # the name's role in the thesis (operator/decomposition prose)
    # NOTE the archetype column is GONE (Business-Type M1): the durable "what does this name DO"
    # characterization is master-level identity now (security_master.business_type + the derive-on-read
    # maps in securities/business_type/), never a per-thesis spine field.
    security_id: UUID | None = None
    detail: str | None = None  # the board/cockpit "met" cell (e.g. "mkt $1.2B")
    segment: str | None = None  # the value-chain link this name sits in (a Thesis.segments label)
    thesis_fit: str | None = (
        None  # WHY it sits in that link — the drafted/edited thesis-fit reasoning (S5); never a fact/number
    )
    conviction: int | None = Field(
        default=None, ge=1, le=5
    )  # the operator's per-name weight (1=starter … 5=full); NULL = unset (never 0). Stored METADATA — it
    # never feeds the meters/verdict/grade (#4: the system sizes from signals, it doesn't judge the idea);
    # carried to the Board / SCORE later. Operator-authored by definition (no LLM recommendation).
    # NAMING GUARD: this OPERATOR conviction (a size weight, stored metadata) is DISTINCT from SIGNAL
    # conviction in `calls/` (conviction_kinds / conviction_grade / key_conviction — deterministic call
    # triggers). They must NEVER cross: wiring operator conviction into the call is a #4 violation.
    surfaced_terms: list[str] = Field(
        default_factory=list
    )  # the discovery terms that SURFACED this name — factual provenance of the membership event, FROZEN
    # at Basket entry (the promote freeze pass: stored wins for an existing member; the backfill CLI's
    # --overwrite is the only correction path). Keyword STRINGS only — never a number, never a call input
    # (#3). [] = honest empty: a hand-added name (ETF sleeve / manual ticker) was surfaced by no term.
    authored_by: Authorship = (
        Authorship.SYSTEM_DRAFTED
    )  # WHO WROTE the description (`thesis_fit`) — honest by construction (Discovery cleanup S1): a
    # member reads "model draft" until the operator EDITS the prose → OPERATOR_EDITED ("your words").
    # NOTHING else flips it — sign-off, include, conviction all leave it alone. OPERATOR_SET is RETIRED
    # for basket members (it stays in the enum as the TERM SET's load-bearing "seed" authorship); a
    # legacy payload still carrying it is translated at promote, never stored.
    signed_off: bool = False  # the operator's per-NAME endorsement — the top rung of the confidence
    # ladder (Excluded → Included (system-recommended) → Signed off). A MARKER only: never authorship
    # (the text stays a model draft until edited), never a promote gate (include gates; sign-off marks),
    # never a call/score input (#4). A hand-add enters signed_off=True (the operator picked the name
    # deliberately); the flag is CARRIED across a re-draft's re-roll, and a signed-off name is never
    # dropped (#9).
    context_role: ContextRole | None = None  # this COMPANY's part on this card; NULL = unset
    # The operator's own word for what a company is to THIS card (payer / counter_case / direct_read /
    # participant / benchmark / financier). PER COMPANY PER CARD: stored on EVERY row of a company (a company
    # in N links is N rows, all carrying the same role) — the promote refuses two different ones and fills a
    # NULL row beside a set role (``context_role_conflicts`` / ``with_inherited_context_roles`` above).
    # Allowed on any row, silent links and Discovered included. DISPLAY metadata only: it never feeds the
    # call, a recorded field or a score (#4), and no LLM path writes it (#3) — the chain drafter, discovery
    # and the reconciler leave it NULL.
    # NAMING GUARD: this is NOT ``role`` above (the free-text prose field) and NOT ``SignalEvent.role``
    # (entry_trigger / risk_signal) — three unrelated "roles"; never wire one into another.


class Evidence(DomainModel):
    """Immutable reference to a filing / data point."""

    id: UUID
    kind: str  # display label e.g. "8-K", "FORM 4", "DATA", "EO"
    label: str
    ref: str  # URL / EDGAR accession
    date_label: str | None = None


class Catalyst(DomainModel):
    id: UUID
    label: str
    kind: str | None = None  # display kind e.g. "earnings", "regulatory"
    when_date: date | None = None  # drives the catalyst_surface filter; None = fuzzy/undated
    when_label: str | None = None  # display string e.g. "~3wk", "Q3"


class KillCriterion(DomainModel):
    id: UUID
    text: str


class ExcludedName(DomainModel):
    """One durably-excluded name (#7): the operator's NO, with the optional why. Applied by the
    EDITOR as pre-seeded grayed state — discovery never filters on it (#9, recall sacred)."""

    security_id: UUID
    ticker: str | None = None  # denormalized display convenience
    reason: str | None = None  # "rejected because X" — optional, always


class Position(DomainModel):
    """Populated once the operator logs a fill — its presence drives the Managing state."""

    entry_price: float | None = None
    current_price: float | None = None
    opened_on: date | None = None
    # the held NAME (the position-deriving take row's security_id) — drives the per-member Managing
    # attribution (CALL_LOGIC §4). None = unattributed: a thesis-level take, or the seed-era stored
    # thesis.position_* columns (which carry no name) — those attribute nothing, never a guess.
    security_id: UUID | None = None


class Thesis(DomainModel):
    """First-class object (invariant #2): narrative, basket, evidence, catalysts, kill criteria, expression."""

    id: UUID
    name: str
    narrative: str  # the operator's words, preserved
    tenant_id: UUID | None = None
    parent_id: UUID | None = None  # nullable now; the umbrella/segment hierarchy lands in M5
    ticker: str | None = None
    basket: list[BasketMember] = Field(default_factory=list)
    segments: list[Segment] = Field(
        default_factory=list
    )  # the value-chain links (Workbench structure)
    term_set: list[TermSetEntry] = Field(
        default_factory=list
    )  # the persisted SIGNAL/BROAD discovery terms (written by /terms, READ by discovery)
    evidence: list[Evidence] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    position: Position | None = None
    # archive, never delete (board hygiene): set ONLY by thesis_repo.set_archived — upsert never
    # names the column, so a promote can neither archive nor resurrect (the term_set guard).
    archived_at: datetime | None = None
    # the durable exclusion set (#7): loaded with the thesis, written ONLY by set_exclusions (the
    # same structural guard — a promote can't wipe the operator's pruning).
    exclusions: list[ExcludedName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _segments_consistent(self) -> "Thesis":
        """Every placed ``member.segment`` must name one of the thesis's segments — the chain stays
        internally consistent (a name can't sit in a link that isn't in the chain). Context and Bench always
        count as present: they are fixed links every hydrated thesis carries, so a roster that names one is
        consistent even when a hand-built ``segments`` list leaves them out."""
        labels = {s.label for s in self.segments} | set(SILENT_LABELS)
        for m in self.basket:
            if m.segment is not None and m.segment not in labels:
                raise ValueError(
                    f"basket member {m.ticker!r} placed in unknown segment {m.segment!r}"
                )
        return self
