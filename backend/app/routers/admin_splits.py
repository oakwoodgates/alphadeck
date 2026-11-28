"""The Admin "Splits" surface (split plan S1) — the operator's ratification of the split list the backfill
filled, every action with a visible inverse (WB#1).

What the system found (``pipeline.backfill_splits``; read, never auto-decided beyond the operator's own rule —
two independent sources agreeing): each split with Massive.com's row and the SEC filing's located passage side by
side. Auto-confirmed and announced splits are QUIET lists; a split one source cannot settle (one source only, a
disagreement, an extractor abstain) is LOUD — the only loud thing here (WB#3).

What the operator can do, and its inverse:

- **Confirm SEC values** / **Confirm Massive.com values** (when they differ), or **Confirm** (when only one source
  states both a ratio and a date) a needs-review split -> its values become the list's. Inverse: **Revoke**.
- **Confirm with cited values** (decision D3, 2026-11-25) a needs-review split neither source states right (POM: an
  ADR termination, 10 ADSs -> 3 shares): the steward's ratio and first session, a required https URL of the
  document that states them, an optional note. The server reads the stored tape's step at that session as first
  served (``pipeline.detect_splits.tape_at``) and refuses (409) a factor outside ``domain.splits.TAPE_TOLERANCE``
  of it, or a session with no bar, unless the steward overrides — the version records the override. Inverse:
  **Revoke**. ``GET /admin/splits/{version_id}/tape-step`` is the same reading for the form, and ``GET
  /admin/splits/{version_id}/tape`` the stored closes and volumes around a split's first session (W5-1).
- **Reject** a needs-review or announced split. Inverse: **Restore** (back to the state before).
- **Revoke** any confirmed split, auto-confirmed or not. Inverse: **Restore** (back to confirmed).
- A forward split under x1.5 that only Massive.com lists, with no SEC filing about it, arrives SET ASIDE as a
  distribution (D2, ``domain.splits.distribution_reason``; ``decided_by`` "set aside automatically: a
  distribution"); so does one Massive.com labels a stock dividend whose filing mentions a split without a ratio
  (W6-2). **Restore** returns it to review, and neither the backfill nor the nightly touches it again.

Rejected and revoked splits stay listed, grayed (WB#2). Every action appends one version naming the operator
(``repositories/split_repo.py``) and names the version it acts on, so a stale card is refused (409), never
applied over a newer decision. No push notification is ever sent from here. What reads the list: the nightly
price leg and the repair tool keep the stored tape on its scale (S2), the insider screen and the market cap read
its factors (S2), and the split-night hold (S5) holds a company's price triggers across a split-shaped step no
confirmed split covers — so a confirm here lifts that hold the next night. Auth stays deferred.

**Split-shaped jumps with no split listed (S5)** — the panel also lists the price jumps the split-night hold
has released (three sessions on) that no source ever listed (``pipeline.split_jumps``): the operator's "is this
real?". Never a page (#7). Since 2026-10-24 the operator can **Dismiss** one ("this was a real move" —
``repositories.split_jump_repo``, append-only): it leaves the list and is listed under ``dismissed_jumps`` (WB#2);
inverse: **Undo**. A dismissal is display only — the split-night hold never reads it.

**What a confirm would touch (W2-8)** — each needs-review or announced split on the list read carries the
security's first stored bar (``first_bar``: a split whose first session is on or before it has no stored bar to
adjust) and the platform share count a confirm would put on the new scale (``shares_rescaled``: the Workbench count
the market cap reads, dated before the first session). Read for the whole list at once, in a fixed number of
queries, never one per split; display only.

**Split filings the list could not place (framework completion F1-3)** — the panel also lists the SEC filings
about a split the detector could not place (no ratio, no date, an extractor abstain), as the nightly's newest run
and the long-window check's newest record list them (``pipeline.split_unplaced``), each with the split on the list
it most likely talks about (``listed_near``: the one citing it, else the nearest of its exact ratio, else the
nearest within ``UNPLACED_NEAR_DAYS``), read at once from the list already read and one read of the master. The
steward can **Dismiss** one ("not a split to list" — ``repositories.split_filing_dismissal_repo``, append-only):
it is listed under ``unplaced_dismissed`` (WB#2); inverse: **Undo**. Display only: the split list, the detector
and the split-night hold never read the unplaced list or a dismissal.

**A split no source lists (framework completion F2-1)** — the steward can **Add** one, from exactly one signal: an
unplaced filing (``from_accession``) or an "is this real?" jump (``from_jump``), with cited values and the same tape
check as a cited-values confirm (``POST /admin/splits``; the form's tape read is ``GET
/admin/splits/securities/{security_id}/tape-step``). The server copies the filing's evidence from the unplaced list,
never from the client, and refuses (409) a filing or jump no longer listed or dismissed, and a split the list already
holds (``split_repo.add_steward``: a live one of the same exact ratio within the nightly's merge window included).
Inverse: **Revoke** (**Reject** while announced). The price leg rescales a stored tape only on a split's first
night: a past added split waits for the operator's ``pipeline.repair_split_basis``.

**A listed split of the same ratio nearby (framework completion F8-3)** — each needs-review or announced split names
the other splits on the list of its exact ratio whose first session is within ``SAME_RATIO_NEAR_DAYS`` of its own
(``same_ratio_listed``, from the versions already read): APUS's needs-review 1-for-10 in March beside its confirmed
1-for-10 in July. Display only: serial reverse splits of one ratio exist, so no rule merges two splits; the steward
settles them.

**A filing that calls a listed split off (framework completion F10)** — each split on the list, any status, names
the filings that call it off (``called_off``: "would not be effecting the reverse stock split", a failed vote),
matched when the list is read: the call-off rows of the nightly's newest run and the long-window check's newest
record (``pipeline.split_unplaced``, the same read as the unplaced list), each against its company's splits by the
one matcher (``domain.split_calloffs``) under the rule the settings set (``pipeline.detect_splits.calloff_rule``;
``split_calloff_warn`` false: none named). AIRI's confirmed 1-for-5 names the 8-K filed the day before its first
session. Display only: nothing decides on it; the steward settles it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.deps import get_conn
from domain.links import HttpUrlOut, filing_index_url, safe_http_url
from domain.market_time import market_today, market_tz
from domain.scopes import DATA_TENANT_ID
from domain.split_calloffs import CalloffRule, called_off
from domain.splits import (
    AUTO_VIA_LABELS,
    SET_ASIDE,
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    TAPE_TOLERANCE,
    ListedSplit,
    SecEvidence,
    TapeEvidence,
    direction,
    price_factor,
    ratio_label,
    same_ratio,
    scale_after,
)
from domain.text_types import Prose, RequestDate, UrlText
from pipeline.backfill_splits import MATCH_WINDOW_DAYS
from pipeline.detect_splits import (
    TAPE_PRIOR_MAX_DAYS,
    calloff_rule,
    calloff_warn,
    tape_at,
    tape_window,
)
from pipeline.split_jumps import UnlistedJump, scan_unlisted_jumps, split_dismissed
from pipeline.split_unplaced import UnplacedRead, calloff_evidence, read_unplaced
from platform_values.state import read_states
from repositories import split_filing_dismissal_repo, split_jump_repo, split_repo
from repositories.split_filing_dismissal_repo import FilingDismissalConflict
from repositories.split_jump_repo import JumpDismissalConflict
from repositories.split_repo import SplitConflict, SplitVersion

router = APIRouter(prefix="/admin/splits", tags=["admin"])


def _num(v: object) -> str | None:
    """An exact ratio number as text ("16", "6.25") — never a float's rounding."""
    if v is None:
        return None
    return format(v.normalize(), "f")  # type: ignore[attr-defined]


class AdminSplitListSecOut(BaseModel):
    """The SEC filing behind a split: the extractor's reading and the LOCATED passage, verbatim.
    ``date_kind`` = ``trading`` (the filing states the first session) or ``legal`` (a legal effective date);
    ``abstain_reason`` = why no single ratio was read."""

    accession: str
    form: str | None = None
    filed: date | None = None
    url: HttpUrlOut = None
    passage: str | None = None
    document: str | None = None
    effective_date: date | None = None
    date_kind: str | None = None
    ratio_from: str | None = None
    ratio_to: str | None = None
    ratio_label: str | None = None
    abstain_reason: str | None = None


class AdminSplitListPolygonOut(BaseModel):
    """Massive.com's split-reference row: ``split_from`` old shares -> ``split_to`` new shares. ``adjustment_type`` is
    Massive.com's own label (``forward_split``, ``reverse_split`` or ``stock_dividend``), evidence only; null on a row
    stored before the label was read or from the v3 endpoint."""

    id: str
    ticker: str | None = None
    execution_date: date | None = None
    split_from: str | None = None
    split_to: str | None = None
    ratio_label: str | None = None
    adjustment_type: str | None = None


class AdminSplitListOptionOut(BaseModel):
    """One value the operator can confirm a needs-review split with."""

    source: Literal["sec", "polygon"]
    ratio_from: str
    ratio_to: str
    effective_date: date
    label: str


class AdminSplitSharesOut(BaseModel):
    """The platform share count a confirm would RESCALE (W2-8): the count the market cap reads (the Workbench's,
    ``dashboards.market_cap.cap_cell``), dated before the split's first session, so the split puts it on the new
    scale (divided by the price factor). ``count`` = as stored; ``date`` = the day it was true (its
    ``valid_from``)."""

    count: float
    date: date


# F8-3: how many calendar days either side of an open split's first session another split on the list of its exact
# ratio is named beside it (``same_ratio_listed``), and at most how many are named
SAME_RATIO_NEAR_DAYS = 180
SAME_RATIO_SHOWN = 2


class AdminSplitListedNearOut(BaseModel):
    """The split on the list nearest a filing or a split (display only; no decision reads it): its NEWEST version,
    any status. ``label`` = its ratio in words; ``same_ratio`` = it is the exact ratio the filing (or the split)
    states (``null`` when the filing states none); ``cites_filing`` = its SEC filing IS this filing (beside a split:
    that split's own SEC filing); ``days_from`` = its first session minus the reference date (an unplaced filing's
    filing date, or a split's first session), signed.
    """

    version_id: UUID
    key_date: date
    effective_date: date
    label: str
    status: str
    same_ratio: bool | None
    cites_filing: bool
    days_from: int


class AdminSplitCalledOffOut(BaseModel):
    """A filing that CALLS OFF a listed split (framework completion F10): it says the split will not go ahead ("would
    not be effecting the reverse stock split") or that its vote failed, as the nightly's newest run (``source``
    ``nightly``) or the long-window check's newest record (``check``) read it; ``seen_on`` = the day that run read.
    Matched when the list is read, by the one matcher (``domain.split_calloffs``) under the ``split_calloff_*``
    settings: filed on one of their forms, from the split's announcement to its first session plus their days, any
    ratio or only the same one. ``kind`` = ``decision`` (the company says it will not effect it) or ``failed_vote``;
    ``ratio_label`` = the ratio the call-off states (``null``: none); ``same_ratio`` = it is the split's exact ratio
    (``null`` when none is stated); ``days_from`` = its filing date minus the split's first session, signed; ``url``
    = the filing index; ``document`` / ``passage`` = the located call-off passage, verbatim. Display only.
    """

    accession: str
    form: str
    filed: date
    url: HttpUrlOut = None
    kind: str
    ratio_label: str | None
    same_ratio: bool | None
    days_from: int
    document: str | None
    passage: str | None
    source: Literal["nightly", "check"]
    seen_on: date


class AdminSplitListEntryOut(BaseModel):
    """One split on the list (its NEWEST version). ``version_id`` names that version — every action posts it,
    and a stale one is refused. ``ratio_from`` -> ``ratio_to`` = old shares -> new shares; ``price_factor`` =
    old / new (prices before the split x it). ``decided_by`` is the source label the panel shows ("auto: SEC +
    Massive.com", "you, 10-20"). ``actions`` = what the status allows; ``restore_to`` = where Restore returns it.
    ``review_reason`` (a needs-review split only) = why it waits: ``sources_differ`` (SEC and Massive.com state
    different values), ``extractor_abstained`` (the SEC reading abstained) or ``one_source`` (only one source states
    both a ratio and a date). ``value_source`` ``steward`` = the steward's own cited values (D3; ``action`` ``add``: a
    split the steward added that no source lists): ``cited_url`` is the document that states them, ``tape_override``
    whether the steward confirmed or added past the stored tape's step (both
    ``null`` on every other split). ``first_bar`` / ``shares_rescaled`` (W2-8; a needs-review or announced split on
    the list read, ``null`` elsewhere) = the security's first stored bar (a split whose first session is on or before
    it adjusts no stored bar) and the share count a confirm would put on the new scale (``null``: none on file, or
    none dated before the first session). ``same_ratio_listed`` (F8-3; a needs-review or announced split on the list
    read, ``null`` elsewhere and when none) = the other splits on the list of its exact ratio whose first session is
    within 180 days of its own, nearest first, at most two; display only, nothing merges them. ``called_off`` (F10;
    any status, ``null`` when none, or with the warning off) = the filings that call it off, those stating its ratio
    first; display only.
    """

    version_id: UUID
    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    key_date: date
    effective_date: date
    ratio_from: str
    ratio_to: str
    price_factor: str
    direction: str
    label: str
    status: str
    confirmed_via: str | None = None
    value_source: str
    author: str
    action: str
    recorded_at: datetime
    decided_by: str
    valid_from: date
    note: str | None = None
    sec: AdminSplitListSecOut | None = None
    polygon: AdminSplitListPolygonOut | None = None
    confirm_options: list[AdminSplitListOptionOut] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    restore_to: str | None = None
    review_reason: Literal["sources_differ", "extractor_abstained", "one_source"] | None = None
    cited_url: HttpUrlOut = None
    tape_override: bool | None = None
    first_bar: date | None = None
    shares_rescaled: AdminSplitSharesOut | None = None
    same_ratio_listed: list[AdminSplitListedNearOut] | None = None
    called_off: list[AdminSplitCalledOffOut] | None = None


class AdminSplitJumpBarOut(BaseModel):
    """One stored bar after a split-shaped jump (what the price did next)."""

    d: date
    close: float | None = None
    volume: float | None = None


class AdminSplitJumpOut(BaseModel):
    """A split-shaped price jump with NO split listed (split plan S5): the close moved x3 or more (or to a third
    or less) between two stored bars with the volume moving the other way, the split-night hold has released it
    (three sessions on), and no SEC filing or Massive.com row on the list sits near it. A real move or a split no
    source has listed yet — the operator's question ("is this real?"). ``step`` = close / prior close.
    """

    security_id: UUID
    ticker: str | None = None
    prior_date: date
    step_date: date
    prior_close: float
    close: float
    prior_volume: float
    volume: float
    step: float
    label: str
    sessions_since: int
    after: list[AdminSplitJumpBarOut] = Field(default_factory=list)
    # the operator's standing dismissal ("this was a real move"), when one stands — undo it with
    # POST /admin/splits/jumps/dismissals/{dismissal_id}/undo
    dismissal_id: UUID | None = None
    dismissed_at: datetime | None = None
    dismissed_reason: str | None = None


# F1-3: how many calendar days either side of an unplaced filing a listed split of another ratio still counts as
# near it (``listed_near``)
UNPLACED_NEAR_DAYS = 120
# an SEC accession number, ``0001628280-26-066700`` (the dismissal log's CHECK)
ACCESSION_PATTERN = r"^\d{10}-\d{2}-\d{6}$"
UNPLACED_DISMISS_REASON = "not a split to list, said the steward"
UNPLACED_UNDO_REASON = "undone by the steward"


class AdminSplitUnplacedOut(BaseModel):
    """An SEC filing about a split the list could not place (no ratio, no date, an extractor abstain), as the
    nightly's newest run (``source`` ``nightly``) or the long-window check's newest record (``check``) lists it;
    ``seen_on`` = the day that run read. ``url`` = the filing index (built from the company's CIK when the run's row
    carries none); ``document`` / ``passage`` = the located document and passage, verbatim; ``ratio_from`` ->
    ``ratio_to`` = the ratio it states, when one; ``reason`` = why it was not placed. ``listed_near`` = the split on
    the list it most likely talks about: the one citing it, else the nearest of its exact ratio, else the nearest
    within 120 days of its filing (``null``: none). ``dismissal_id`` / ``dismissed_at`` / ``dismissed_reason`` = the
    steward's standing dismissal ("not a split to list"), when one stands: undo it with ``POST
    /admin/splits/unplaced/dismissals/{dismissal_id}/undo``. Display only.
    """

    security_id: UUID
    ticker: str | None
    name: str | None
    accession: str
    form: str
    filed: date
    url: HttpUrlOut = None
    reason: str
    document: str | None
    passage: str | None
    ratio_from: str | None
    ratio_to: str | None
    ratio_label: str | None
    source: Literal["nightly", "check"]
    seen_on: date
    listed_near: AdminSplitListedNearOut | None = None
    dismissal_id: UUID | None
    dismissed_at: datetime | None
    dismissed_reason: str | None


class AdminSplitUnplacedRunOut(BaseModel):
    """A run the unplaced filings were read from: the nightly's newest (``source`` ``nightly``; ``sec_days``
    ``null``: the nightly's own window) or the long-window check's newest record (``check``). ``asof`` = the night
    or day it read; ``rows`` = how many filings it listed, dismissed ones included; ``calloffs`` = how many filings it
    read with call-off wording (F10).
    """

    source: Literal["nightly", "check"]
    asof: date
    started_at: datetime
    sec_days: int | None
    rows: int
    calloffs: int = 0


class AdminSplitListOut(BaseModel):
    """The "Splits" panel. ``needs_review`` is the one LOUD list (one source could not settle it);
    ``announced`` (both sources agree, the first session is still ahead), ``confirmed`` and ``set_aside``
    (rejected / revoked — kept, grayed) are QUIET lists. ``total`` = every split on the list. Read live.
    ``unlisted_jumps`` (S5) — split-shaped jumps on basket names that the split-night hold has released and no
    source ever listed, the last 30 days; ``unlisted_jumps_error`` when that read failed (shown, never hidden).
    ``dismissed_jumps`` — the ones the operator dismissed as a real move (still listed, behind the panel's
    "dismissed" filter; each undoable). ``unplaced`` — SEC filings about a split the list could not place, newest
    filing first; ``unplaced_dismissed`` — the ones the steward dismissed (each undoable); ``unplaced_runs`` — the
    runs they were read from; ``unplaced_error`` when that read failed or passed over an unreadable file (shown,
    never hidden; a failed read names no call-off).
    """

    needs_review: list[AdminSplitListEntryOut] = Field(default_factory=list)
    announced: list[AdminSplitListEntryOut] = Field(default_factory=list)
    confirmed: list[AdminSplitListEntryOut] = Field(default_factory=list)
    set_aside: list[AdminSplitListEntryOut] = Field(default_factory=list)
    total: int = 0
    unlisted_jumps: list[AdminSplitJumpOut] = Field(default_factory=list)
    unlisted_jumps_error: str | None = None
    dismissed_jumps: list[AdminSplitJumpOut] = Field(default_factory=list)
    unplaced: list[AdminSplitUnplacedOut] = Field(default_factory=list)
    unplaced_dismissed: list[AdminSplitUnplacedOut] = Field(default_factory=list)
    unplaced_runs: list[AdminSplitUnplacedRunOut] = Field(default_factory=list)
    unplaced_error: str | None = None


class AdminSplitListConfirmIn(BaseModel):
    """Whose values to confirm: the SEC filing's or Massive.com's."""

    source: Literal["sec", "polygon"]


class AdminSplitJumpReasonIn(BaseModel):
    """Why (optional — a default reason is stored)."""

    reason: str | None = Field(default=None, max_length=500)


# D3 — the steward's cited values. A ratio is decimal TEXT, the way the panel's entries carry one ("10", "6.25"):
# digits with an optional decimal part, above 0 and at most RATIO_MAX.
RATIO_PATTERN = r"^\d{1,6}(\.\d{1,10})?$"
RATIO_MAX = Decimal(100_000)
CITED_NOTE_MAX = 500

RatioText = Annotated[str, StringConstraints(strip_whitespace=True, pattern=RATIO_PATTERN)]


def _https_url(value: str) -> str:
    """A cited document is a plain https URL (``domain.links``' wire rule, https only)."""
    if safe_http_url(value) is None or not value.lower().startswith("https://"):
        raise ValueError("must be an https URL with a host and no user:password")
    return value


CitedUrl = Annotated[UrlText, AfterValidator(_https_url)]


def _ratio(text: str, name: str) -> Decimal:
    value = Decimal(text)
    if not 0 < value <= RATIO_MAX:
        raise ValueError(f"{name} must be above 0 and at most {RATIO_MAX}")
    return value


class AdminSplitConfirmValuesIn(BaseModel):
    """The steward's OWN values for a needs-review split (decision D3): ``ratio_from`` old shares -> ``ratio_to`` new
    shares (decimal text, above 0 and at most 100,000, never equal), ``first_session`` = the first session at the
    new price, ``cited_url`` = the https URL of the document that states them (required), ``note`` (optional).
    ``override_tape`` = confirm although the stored tape's step at that session sits outside the tolerance of the
    entered factor, or no bar can be read (the server checks it either way: ``GET .../tape-step``).
    """

    ratio_from: RatioText
    ratio_to: RatioText
    first_session: RequestDate
    cited_url: CitedUrl
    note: Annotated[Prose, Field(max_length=CITED_NOTE_MAX)] | None = None
    override_tape: bool = False

    @model_validator(mode="after")
    def _a_split(self) -> AdminSplitConfirmValuesIn:
        if _ratio(self.ratio_from, "ratio_from") == _ratio(self.ratio_to, "ratio_to"):
            raise ValueError("ratio_from equals ratio_to: that is not a split")
        return self


AccessionText = Annotated[str, StringConstraints(strip_whitespace=True, pattern=ACCESSION_PATTERN)]


class AdminSplitAddIn(BaseModel):
    """A split NO source lists, added by the steward (framework completion F2-1) with cited values: ``security_id``,
    ``ratio_from`` old shares -> ``ratio_to`` new shares (decimal text, above 0 and at most 100,000, never equal),
    ``first_session`` = the first session at the new price, ``cited_url`` = the https URL of the document that
    states them (required), ``note`` (optional). It starts from exactly one signal: ``from_accession`` = an SEC
    filing on the current unplaced list (its evidence is read by the server, never sent), or ``from_jump`` = the
    step date of a split-shaped jump on the current "is this real?" list. ``override_tape`` = add although the stored
    tape's step at that session sits outside the tolerance of the entered factor, or no bar can be read (the server
    checks it either way: ``GET /admin/splits/securities/{security_id}/tape-step``).
    """

    security_id: UUID
    ratio_from: RatioText
    ratio_to: RatioText
    first_session: RequestDate
    cited_url: CitedUrl
    note: Annotated[Prose, Field(max_length=CITED_NOTE_MAX)] | None = None
    override_tape: bool | None = None
    from_accession: AccessionText | None = None
    from_jump: RequestDate | None = None

    @model_validator(mode="after")
    def _one_signal_and_a_split(self) -> AdminSplitAddIn:
        if (self.from_accession is None) == (self.from_jump is None):
            raise ValueError(
                "name exactly one of from_accession (an unplaced filing) or from_jump (a jump's step date)"
            )
        if _ratio(self.ratio_from, "ratio_from") == _ratio(self.ratio_to, "ratio_to"):
            raise ValueError("ratio_from equals ratio_to: that is not a split")
        return self


class AdminSplitTapeStepOut(BaseModel):
    """The stored tape at a first session, beside the factor the steward entered (D3) — what the confirm-values
    route checks. Each bar is read as the vendor FIRST served it (``pipeline.detect_splits.tape_at``: the bar on
    ``first_session`` and the stored bar before it, at most 14 days back). ``step`` = close / prior close;
    ``volume_ratio`` = volume / prior volume. ``price_factor`` = old / new shares of the entered ratio (``null``
    when none was entered); ``step_low`` .. ``step_high`` = the steps ``tolerance`` (``domain.splits.
    TAPE_TOLERANCE``, in ln, either direction) accepts around it. ``within_tolerance`` = the step sits inside
    (``null`` with no ratio, ``false`` when no bar can be read); ``reason`` says why not. A read.
    """

    first_session: date
    prior_date: date | None = None
    prior_close: float | None = None
    close: float | None = None
    prior_volume: float | None = None
    volume: float | None = None
    step: float | None = None
    volume_ratio: float | None = None
    prior_served_before: bool | None = None
    price_factor: float | None = None
    tolerance: float = TAPE_TOLERANCE
    step_low: float | None = None
    step_high: float | None = None
    within_tolerance: bool | None = None
    reason: str | None = None


# the tape window (W5-1): stored sessions each side of a split's first session
TAPE_SESSIONS_DEFAULT = 5
TAPE_SESSIONS_MAX = 10


class AdminSplitTapeBarOut(BaseModel):
    """One stored session in a split's tape window. ``close`` / ``volume`` = the bar as the vendor FIRST served it
    (the version ``tape_at`` reads: the as-traded print); ``newest_close`` = its newest stored version (the store's
    scale today); ``restated`` = the two closes differ (re-based later: by the vendor, or by the split list's own
    rescale of the stored tape); ``filled`` = the vendor never served this session: the bar is Massive.com's raw bar
    the price leg filled in (W6-3b), never read as split evidence."""

    d: date
    close: float | None = None
    volume: float | None = None
    newest_close: float | None = None
    restated: bool = False
    filled: bool = False


class AdminSplitTapeOut(BaseModel):
    """The stored tape around a split's first session (W5-1): what was served each session either side of it.
    ``bars`` = up to ``sessions`` stored sessions before ``around`` and up to ``sessions`` from ``around`` on (it
    first, when stored), oldest first, fewer where the tape ends; ``around`` defaults to the split's ``key_date``.
    ``prior_date`` / ``step`` / ``volume_ratio`` / ``prior_served_before`` = the step at ``around`` as first served,
    the reading ``GET .../tape-step`` shows (the bar on ``around`` and the stored bar before it, at most 14 days
    back; ``null`` when either is missing). A read.
    """

    version_id: UUID
    around: date
    sessions: int
    bars: list[AdminSplitTapeBarOut] = Field(default_factory=list)
    prior_date: date | None = None
    step: float | None = None
    volume_ratio: float | None = None
    prior_served_before: bool | None = None


def tape_why(
    tape: TapeEvidence | None, first_session: date, ratio_from: Decimal, ratio_to: Decimal
) -> str | None:
    """Why the stored tape does not support ``ratio_from`` -> ``ratio_to`` first trading on ``first_session``
    (``None`` = it does): no bar to read, or a step more than ``TAPE_TOLERANCE`` (in ln) from the price factor,
    EITHER way. Unlike the machine's auto-confirm (``domain.splits.tape_agreement``) it reads no volume and no
    minimum factor: the cited document is the evidence, the tape the check the steward must override.
    """
    if tape is None or tape.session != first_session:
        return (
            f"no stored bar on {first_session.isoformat()} (with one in the {TAPE_PRIOR_MAX_DAYS} days before "
            "it) to read"
        )
    k = float(price_factor(ratio_from, ratio_to))
    step, band = tape.step, math.exp(TAPE_TOLERANCE)
    if step <= 0 or abs(math.log(step / k)) > TAPE_TOLERANCE:
        return (
            f"the tape steps x{step:.3g} on {first_session.isoformat()}, not within x{band:.3g} of the "
            f"entered x{k:.3g} ({ratio_label(ratio_from, ratio_to)})"
        )
    return None


def _tape_now(
    conn: psycopg.Connection, security_id: UUID, first_session: date
) -> TapeEvidence | None:
    return tape_at(conn, tenant_id=DATA_TENANT_ID, security_id=security_id, session=first_session)


def _data_version(conn: psycopg.Connection, version_id: UUID) -> SplitVersion:
    """The split version a tape read names, on the data tenant; **404** otherwise."""
    v = split_repo.get_version(conn, version_id)
    if v is None or v.tenant_id != DATA_TENANT_ID:
        raise HTTPException(status_code=404, detail=f"no split version {version_id}")
    return v


def _md(ts: datetime) -> str:
    return ts.astimezone(market_tz()).strftime("%m-%d")


# D2: the label of a split the system set aside as a distribution (``split_repo.is_auto_set_aside``)
DECIDED_DISTRIBUTION = "set aside automatically: a distribution"


def _decided_by(v: SplitVersion) -> str:
    """The source label: "auto: SEC + Massive.com" (or "auto: SEC + tape" / "auto: Massive.com + tape") for the
    two-source rule, "you, 10-20" for the operator, ``DECIDED_DISTRIBUTION`` for a distribution the system set
    aside (D2), else who listed it (the backfill / the nightly).
    """
    auto = AUTO_VIA_LABELS.get(v.confirmed_via or "")
    if v.author != split_repo.AUTHOR_OPERATOR:
        if auto:
            return auto
        if split_repo.is_auto_set_aside(v):
            return DECIDED_DISTRIBUTION
        return "the nightly" if v.author == split_repo.AUTHOR_DETECT else "the backfill"
    verb = {
        "confirm": "you",
        "reject": "rejected by you",
        "revoke": "revoked by you",
        "restore": "restored by you",
    }.get(v.action, "you")
    return f"{auto + ' · ' if auto else ''}{verb}, {_md(v.recorded_at)}"


def _decided_label(v: SplitVersion) -> str:
    """``_decided_by``, with the steward's cited values named as such: "you (cited values), 11-25" for a confirm,
    "added by you (cited values), 11-27" for a split the steward added (F2-1)."""
    if v.value_source == split_repo.SOURCE_STEWARD and v.action == split_repo.ACTION_CONFIRM:
        return f"you (cited values), {_md(v.recorded_at)}"
    if v.action == split_repo.ACTION_ADD:
        return f"added by you (cited values), {_md(v.recorded_at)}"
    return _decided_by(v)


def _review_reason(
    v: SplitVersion, n_options: int
) -> Literal["sources_differ", "extractor_abstained", "one_source"] | None:
    """Why a needs-review split waits (``None`` on any other status). Two confirmable values = the sources differ;
    else the SEC reading abstained; else only one source states both a ratio and a date."""
    if v.status != STATUS_NEEDS_REVIEW:
        return None
    if n_options >= 2:
        return "sources_differ"
    if v.sec_abstain_reason:
        return "extractor_abstained"
    return "one_source"


def split_out(v: SplitVersion, *, restore_to: str | None = None) -> AdminSplitListEntryOut:
    options = []
    for src in split_repo.confirm_options(v):
        vals = split_repo.confirm_values(v, src)
        assert vals is not None
        options.append(
            AdminSplitListOptionOut(
                source=src,  # type: ignore[arg-type]
                ratio_from=_num(vals[0]) or "",
                ratio_to=_num(vals[1]) or "",
                effective_date=vals[2],
                label=f"{ratio_label(vals[0], vals[1])}, first session {vals[2].isoformat()}",
            )
        )
    sec = (
        AdminSplitListSecOut(
            accession=v.sec_accession,
            form=v.sec_form,
            filed=v.sec_filed,
            url=v.sec_url,
            passage=v.sec_passage,
            document=v.sec_document,
            effective_date=v.sec_effective_date,
            date_kind=v.sec_date_kind,
            ratio_from=_num(v.sec_ratio_from),
            ratio_to=_num(v.sec_ratio_to),
            ratio_label=(
                ratio_label(v.sec_ratio_from, v.sec_ratio_to)
                if v.sec_ratio_from is not None and v.sec_ratio_to is not None
                else None
            ),
            abstain_reason=v.sec_abstain_reason,
        )
        if v.sec_accession
        else None
    )
    poly = (
        AdminSplitListPolygonOut(
            id=v.polygon_id,
            ticker=v.polygon_ticker,
            execution_date=v.polygon_execution_date,
            split_from=_num(v.polygon_split_from),
            split_to=_num(v.polygon_split_to),
            ratio_label=(
                ratio_label(v.polygon_split_from, v.polygon_split_to)
                if v.polygon_split_from is not None and v.polygon_split_to is not None
                else None
            ),
            adjustment_type=v.polygon_adjustment_type,
        )
        if v.polygon_id
        else None
    )
    return AdminSplitListEntryOut(
        version_id=v.id,
        security_id=v.security_id,
        ticker=v.ticker,
        name=v.name,
        key_date=v.key_date,
        effective_date=v.effective_date,
        ratio_from=_num(v.ratio_from) or "",
        ratio_to=_num(v.ratio_to) or "",
        price_factor=_num(v.price_factor) or "",
        direction=direction(v.ratio_from, v.ratio_to),
        label=ratio_label(v.ratio_from, v.ratio_to),
        status=v.status,
        confirmed_via=v.confirmed_via,
        value_source=v.value_source,
        author=v.author,
        action=v.action,
        recorded_at=v.recorded_at,
        decided_by=_decided_label(v),
        valid_from=v.valid_from,
        note=v.note,
        sec=sec,
        polygon=poly,
        confirm_options=options,
        actions=split_repo.allowed_actions(v),
        restore_to=restore_to,
        review_reason=_review_reason(v, len(options)),
        cited_url=v.cited_url,
        tape_override=v.tape_override,
    )


def jump_out(j: UnlistedJump, dismissal: Mapping[str, Any] | None = None) -> AdminSplitJumpOut:
    s = j.step
    return AdminSplitJumpOut(
        security_id=j.security_id,
        ticker=j.ticker,
        prior_date=s.prior_d,
        step_date=s.d,
        prior_close=s.prior_close,
        close=s.close,
        prior_volume=s.prior_volume,
        volume=s.volume,
        step=s.step,
        label=j.label,
        sessions_since=j.sessions_since,
        after=[AdminSplitJumpBarOut(d=d, close=c, volume=v) for d, c, v in j.after],
        dismissal_id=dismissal["id"] if dismissal is not None else None,
        dismissed_at=dismissal["recorded_at"] if dismissal is not None else None,
        dismissed_reason=dismissal["reason"] if dismissal is not None else None,
    )


def _jumps(
    conn: psycopg.Connection, *, tenant_id: UUID, asof: date
) -> tuple[list[AdminSplitJumpOut], list[AdminSplitJumpOut]]:
    """``(the jumps still asking "is this real?", the ones the operator dismissed)`` as of ``asof``."""
    jumps = scan_unlisted_jumps(conn, asof=asof, tenant_id=tenant_id)
    dismissed = split_jump_repo.standing_dismissals(conn, tenant_id=tenant_id)
    keep, gone = split_dismissed(jumps, dismissed)
    return (
        [jump_out(j) for j in keep],
        [jump_out(j, dismissed[(j.security_id, j.step.d)]) for j in gone],
    )


# --- split filings the list could not place (F1-3): display only; no decision reads them --------------------


def _text(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def stated_ratio(ratio_from: Any, ratio_to: Any) -> tuple[Decimal, Decimal] | None:
    """The exact ratio a filing states (decimal text, both sides above 0), or ``None``."""
    if not isinstance(ratio_from, str) or not isinstance(ratio_to, str):
        return None
    try:
        rf, rt = Decimal(ratio_from), Decimal(ratio_to)
    except ArithmeticError:
        return None
    if not (rf.is_finite() and rt.is_finite() and rf > 0 and rt > 0):
        return None
    return rf, rt


def near_out(
    v: SplitVersion, *, ref: date, ratio: tuple[Decimal, Decimal] | None, cites: bool
) -> AdminSplitListedNearOut:
    """One listed split as a hint beside ``ref`` (a filing date): ``same_ratio`` against ``ratio`` (``None``
    when none is stated), ``days_from`` = its first session minus ``ref``."""
    return AdminSplitListedNearOut(
        version_id=v.id,
        key_date=v.key_date,
        effective_date=v.effective_date,
        label=ratio_label(v.ratio_from, v.ratio_to),
        status=v.status,
        same_ratio=None if ratio is None else same_ratio(v.ratio_from, v.ratio_to, *ratio),
        cites_filing=cites,
        days_from=(v.effective_date - ref).days,
    )


def listed_near(
    versions: Iterable[SplitVersion],
    *,
    security_id: UUID,
    ref: date,
    accession: str | None = None,
    ratio: tuple[Decimal, Decimal] | None = None,
    near_days: int = UNPLACED_NEAR_DAYS,
) -> AdminSplitListedNearOut | None:
    """The split on the list an unplaced filing most likely talks about, from the newest versions already read
    (any status), of the filing's own security: (1) the one whose SEC filing is ``accession``; else (2) when the
    filing states a ``ratio``, the nearest of that exact ratio, at any distance; else (3) the nearest within
    ``near_days`` of ``ref`` (the filing's date). Nearest = the fewest calendar days between its first session and
    ``ref``, the earlier first session on a tie. ``None`` when none qualifies. PURE; display only.
    """
    own = [v for v in versions if v.security_id == security_id]

    def gap(v: SplitVersion) -> int:
        return abs((v.effective_date - ref).days)

    def nearest(vs: Iterable[SplitVersion]) -> SplitVersion | None:
        return min(vs, key=lambda v: (gap(v), v.effective_date, str(v.id)), default=None)

    pick = nearest(v for v in own if accession is not None and v.sec_accession == accession)
    if pick is None and ratio is not None:
        pick = nearest(v for v in own if same_ratio(v.ratio_from, v.ratio_to, *ratio))
    if pick is None:
        pick = nearest(v for v in own if gap(v) <= near_days)
    if pick is None:
        return None
    return near_out(
        pick,
        ref=ref,
        ratio=ratio,
        cites=accession is not None and pick.sec_accession == accession,
    )


def same_ratio_listed(
    versions: Iterable[SplitVersion], v: SplitVersion, *, near_days: int = SAME_RATIO_NEAR_DAYS
) -> list[AdminSplitListedNearOut] | None:
    """The OTHER splits on the list (F8-3), from the newest versions already read (any status), of ``v``'s security
    and ``v``'s exact ratio whose first session lies within ``near_days`` of ``v``'s: nearest first (the earlier
    first session on a tie), at most ``SAME_RATIO_SHOWN``. ``days_from`` = the other's first session minus ``v``'s;
    ``cites_filing`` = both cite the same SEC filing. ``None`` when none qualifies. APUS's needs-review 1-for-10 of
    03-26 names its confirmed 1-for-10 of 07-24 (+120). PURE; display only: serial reverse splits of one ratio
    exist, so nothing merges two splits (the steward settles them).
    """
    ratio = (v.ratio_from, v.ratio_to)

    def gap(o: SplitVersion) -> int:
        return abs((o.effective_date - v.effective_date).days)

    near = sorted(
        (
            o
            for o in versions
            if o.security_id == v.security_id
            and o.id != v.id
            and same_ratio(o.ratio_from, o.ratio_to, *ratio)
            and gap(o) <= near_days
        ),
        key=lambda o: (gap(o), o.effective_date, str(o.id)),
    )[:SAME_RATIO_SHOWN]
    if not near:
        return None
    return [
        near_out(
            o,
            ref=v.effective_date,
            ratio=ratio,
            cites=v.sec_accession is not None and o.sec_accession == v.sec_accession,
        )
        for o in near
    ]


def _masters(
    conn: psycopg.Connection, *, tenant_id: UUID, security_ids: Collection[UUID]
) -> dict[UUID, Mapping[str, Any]]:
    """The master's CIK and name for ``security_ids``, in one read."""
    if not security_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, cik, name FROM security_master WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, list(security_ids)),
        )
        return {r["id"]: r for r in cur.fetchall()}


def unplaced_out(
    row: Mapping[str, Any],
    *,
    master: Mapping[str, Any] | None,
    versions: Iterable[SplitVersion],
    dismissal: Mapping[str, Any] | None = None,
) -> AdminSplitUnplacedOut:
    """One row of ``pipeline.split_unplaced.read_unplaced`` as the panel lists it: the link the row carries, else
    the filing index built from the master's CIK (never a guessed one); the master's name; ``listed_near``; the
    standing ``dismissal``, if any."""
    sid = UUID(str(row["security_id"]))
    filed = date.fromisoformat(row["filed"])
    accession = row["accession"]
    ratio = stated_ratio(row.get("ratio_from"), row.get("ratio_to"))
    cik = master.get("cik") if master is not None else None
    return AdminSplitUnplacedOut(
        security_id=sid,
        ticker=_text(row.get("ticker")),
        name=master.get("name") if master is not None else None,
        accession=accession,
        form=_text(row.get("form")) or "",
        filed=filed,
        url=safe_http_url(row.get("url")) or filing_index_url(cik, accession),
        reason=_text(row.get("reason")) or "",
        document=_text(row.get("document")),
        passage=_text(row.get("passage")),
        ratio_from=_text(row.get("ratio_from")),
        ratio_to=_text(row.get("ratio_to")),
        ratio_label=ratio_label(*ratio) if ratio is not None else None,
        source=row["source"],
        seen_on=date.fromisoformat(row["seen_on"]),
        listed_near=listed_near(
            versions, security_id=sid, ref=filed, accession=accession, ratio=ratio
        ),
        dismissal_id=dismissal["id"] if dismissal is not None else None,
        dismissed_at=dismissal["recorded_at"] if dismissal is not None else None,
        dismissed_reason=dismissal["reason"] if dismissal is not None else None,
    )


def _unplaced(
    conn: psycopg.Connection,
    versions: Iterable[SplitVersion],
    *,
    tenant_id: UUID,
    only: tuple[UUID, str] | None = None,
    read: UnplacedRead | None = None,
) -> tuple[
    list[AdminSplitUnplacedOut],
    list[AdminSplitUnplacedOut],
    list[AdminSplitUnplacedRunOut],
    str | None,
]:
    """``(the unplaced filings still listed, the ones the steward dismissed, the runs read, the read's error)``:
    one read of the unplaced list (files; ``read`` = one already made), one of the master, one of the standing
    dismissals; ``listed_near`` from ``versions`` (the list's newest versions). ``only`` = one filing
    ``(security_id, accession)``.
    """
    read = read if read is not None else read_unplaced()
    rows = [
        r
        for r in read.rows
        if only is None or (r["security_id"], r["accession"]) == (str(only[0]), only[1])
    ]
    runs = [AdminSplitUnplacedRunOut(**run) for run in read.runs]
    if not rows:
        return [], [], runs, read.error
    ids = {UUID(r["security_id"]) for r in rows}
    masters = _masters(conn, tenant_id=tenant_id, security_ids=ids)
    dismissed = split_filing_dismissal_repo.standing_dismissals(conn, tenant_id=tenant_id)
    by_security: dict[UUID, list[SplitVersion]] = defaultdict(list)
    for v in versions:
        if v.security_id in ids:
            by_security[v.security_id].append(v)
    keep: list[AdminSplitUnplacedOut] = []
    gone: list[AdminSplitUnplacedOut] = []
    for r in rows:  # newest filing first, as read
        sid = UUID(r["security_id"])
        dismissal = dismissed.get((sid, r["accession"]))
        o = unplaced_out(
            r, master=masters.get(sid), versions=by_security.get(sid, ()), dismissal=dismissal
        )
        (keep if dismissal is None else gone).append(o)
    return keep, gone, runs, read.error


# --- a filing that calls a listed split off (F10): matched on read by the one matcher; display only --------------


def calloff_out(row: Mapping[str, Any], s: SecEvidence, v: SplitVersion) -> AdminSplitCalledOffOut:
    """One call-off row of ``pipeline.split_unplaced.read_unplaced`` (``s``: the filing the matcher read from it,
    ``calloff_evidence``) beside the listed split ``v`` it calls off."""
    c = s.calloff
    assert c is not None
    rf, rt = c.ratio_from, c.ratio_to
    stated = rf is not None and rt is not None  # ``c.has_ratio``
    return AdminSplitCalledOffOut(
        accession=s.accession,
        form=s.form,
        filed=s.filed,
        url=safe_http_url(row.get("url")),
        kind=c.kind,
        ratio_label=ratio_label(rf, rt) if stated else None,
        same_ratio=same_ratio(v.ratio_from, v.ratio_to, rf, rt) if stated else None,
        days_from=(s.filed - v.effective_date).days,
        document=_text(c.document),
        passage=_text(c.passage),
        source=row["source"],
        seen_on=date.fromisoformat(row["seen_on"]),
    )


def called_off_for(
    v: SplitVersion, rows: Iterable[Mapping[str, Any]], *, rule: CalloffRule
) -> list[AdminSplitCalledOffOut] | None:
    """The filings that call the listed split ``v`` (any status) off, from the call-off rows ``rows``
    (``read_unplaced().calloffs``) of ``v``'s own security: THE one matcher (``domain.split_calloffs.called_off``)
    under ``rule`` (``pipeline.detect_splits.calloff_rule``), against ``v``'s ratio and first session, announced on
    its SEC filing's date, else the day it was listed (a Massive.com-only split). Those stating its ratio first,
    then by filing date. ``None`` when none does. PURE; display only.
    """
    own = {r["accession"]: r for r in rows if r["security_id"] == str(v.security_id)}
    if not own:
        return None
    hits = called_off(
        [calloff_evidence(r) for r in own.values()],
        ratio_from=v.ratio_from,
        ratio_to=v.ratio_to,
        first_session=v.effective_date,
        announced_on=v.sec_filed or v.valid_from,
        rule=rule,
    )
    return [calloff_out(own[s.accession], s, v) for s in hits] or None


def _called_off(
    entries: Iterable[AdminSplitListEntryOut],
    versions: Iterable[SplitVersion],
    rows: Iterable[Mapping[str, Any]],
    *,
    rule: CalloffRule,
) -> None:
    """``called_off`` on every entry (any status) whose company has a call-off row, from the versions already read
    and the one read of the rows: no query."""
    by_security: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_security[r["security_id"]].append(r)
    if not by_security:
        return
    by_version = {e.version_id: e for e in entries}
    for v in versions:
        own = by_security.get(str(v.security_id))
        if own and v.id in by_version:
            by_version[v.id].called_off = called_off_for(v, own, rule=rule)


# --- what a confirm would touch (W2-8): read for the whole list at once; no decision reads it -------------------


def first_bars(
    conn: psycopg.Connection, *, tenant_id: UUID, security_ids: Collection[UUID]
) -> dict[UUID, date]:
    """Each security's FIRST stored bar (its oldest ``fact_price_eod`` date, any version), in one read for the set;
    a security with no bar is absent. A split whose first session is on or before it has no stored bar to adjust.
    """
    if not security_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT security_id, min(d) AS first_bar FROM fact_price_eod "
            "WHERE tenant_id = %s AND security_id = ANY(%s) GROUP BY security_id",
            (tenant_id, list(security_ids)),
        )
        return {r["security_id"]: r["first_bar"] for r in cur.fetchall()}


def rescaled_count(
    e: AdminSplitListEntryOut, shares: Mapping[str, Any] | None
) -> AdminSplitSharesOut | None:
    """The share count a confirm of ``e`` would put on the new scale, or ``None``: the market cap's own rule
    (``dashboards.market_cap.cap_cell``, ``domain.splits.scale_after`` from the count's date) with the split in
    force, so a count dated before its first session is rescaled and one dated on or after it is not.
    """
    day = shares.get("valid_from") if shares is not None else None
    if shares is None or shares.get("shares") is None or not isinstance(day, date):
        return None
    in_force = ListedSplit(
        key_date=e.key_date,
        effective_date=e.effective_date,
        ratio_from=Decimal(e.ratio_from),
        ratio_to=Decimal(e.ratio_to),
        status=STATUS_CONFIRMED,
    )
    if scale_after([in_force], day, through=e.effective_date) == 1:
        return None
    return AdminSplitSharesOut(count=float(shares["shares"]), date=day)


def _hints(
    conn: psycopg.Connection, out: AdminSplitListOut, *, tenant_id: UUID, asof: date
) -> None:
    """``first_bar`` and ``shares_rescaled`` on every open entry (needs review, announced), read for the whole list
    at once: the first bars, and the platform share counts as the meters read and pick them
    (``platform_values.state.read_states``, a fixed number of queries whatever the list's size)."""
    entries = [*out.needs_review, *out.announced]
    ids = list(dict.fromkeys(e.security_id for e in entries))
    if not ids:
        return
    bars = first_bars(conn, tenant_id=tenant_id, security_ids=ids)
    states = read_states(conn, ids, "shares_outstanding", asof=asof)
    for e in entries:
        e.first_bar = bars.get(e.security_id)
        e.shares_rescaled = rescaled_count(e, states[e.security_id].meter_value)


def splits_out(
    conn: psycopg.Connection, *, tenant_id: UUID, asof: date | None = None
) -> AdminSplitListOut:
    """The panel, read live from the list's newest versions — plus the released, unlisted split-shaped jumps
    as of ``asof`` (default: today's market day; ``pipeline.split_jumps``), each open split's hints (W2-8) and the
    listed splits of its exact ratio near it (F8-3, from the same versions), and the split filings the list could
    not place (F1-3; ``pipeline.split_unplaced``, files) and, from the same read, the filings that call each split
    off (F10, when ``split_calloff_warn`` is on). SELECTs only.
    """
    versions = split_repo.latest_versions(conn, tenant_id=tenant_id)
    out = AdminSplitListOut(total=len(versions))
    try:
        out.unlisted_jumps, out.dismissed_jumps = _jumps(
            conn, tenant_id=tenant_id, asof=asof or market_today()
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 — a failed jump read is shown on the panel, never a failed panel
        conn.rollback()
        out.unlisted_jumps_error = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
    by_security: dict[UUID, list[SplitVersion]] = defaultdict(list)
    for v in versions:
        by_security[v.security_id].append(v)
    for v in versions:
        o = split_out(
            v,
            restore_to=split_repo.prior_status(conn, v) if v.status in SET_ASIDE else None,
        )
        if v.status in (STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED):
            o.same_ratio_listed = same_ratio_listed(by_security[v.security_id], v)
        if v.status == STATUS_NEEDS_REVIEW:
            out.needs_review.append(o)
        elif v.status == STATUS_ANNOUNCED:
            out.announced.append(o)
        elif v.status == STATUS_CONFIRMED:
            out.confirmed.append(o)
        else:
            out.set_aside.append(o)
    newest_first = lambda o: (o.effective_date, o.ticker or "")  # noqa: E731
    out.needs_review.sort(key=newest_first, reverse=True)
    out.confirmed.sort(key=newest_first, reverse=True)
    out.set_aside.sort(key=newest_first, reverse=True)
    out.announced.sort(key=newest_first)
    _hints(conn, out, tenant_id=tenant_id, asof=asof or market_today())
    try:
        read = read_unplaced()
        out.unplaced, out.unplaced_dismissed, out.unplaced_runs, out.unplaced_error = _unplaced(
            conn, versions, tenant_id=tenant_id, read=read
        )
        if calloff_warn():
            entries = (*out.needs_review, *out.announced, *out.confirmed, *out.set_aside)
            _called_off(entries, versions, read.calloffs, rule=calloff_rule())
    except (
        Exception
    ) as e:  # noqa: BLE001 — a failed unplaced read is shown on the panel, never a failed panel
        conn.rollback()
        out.unplaced_error = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
    return out


def count_needs_review(conn: psycopg.Connection, *, tenant_id: UUID = DATA_TENANT_ID) -> int:
    """The Admin status count ("N splits need review"). The split list is DATA (``domain.scopes``): a steward
    route acts on the data tenant, never on the caller's workspace. SELECTs only."""
    return split_repo.count_needs_review(conn, tenant_id=tenant_id)


def _commit(conn: psycopg.Connection, fn: Callable[[], SplitVersion]) -> SplitVersion:
    """One decision in its own transaction: commit on success; on ANY failure roll back and map the
    repository's errors to the operator-facing status (404 unknown, 409 conflict, 422 invalid)."""
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SplitConflict as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


def _out(conn: psycopg.Connection, v: SplitVersion) -> AdminSplitListEntryOut:
    fresh = split_repo.get_version(conn, v.id) or v
    try:
        return split_out(
            fresh,
            restore_to=split_repo.prior_status(conn, fresh) if fresh.status in SET_ASIDE else None,
        )
    finally:
        conn.rollback()  # a read: hold no snapshot


@router.get("", response_model=AdminSplitListOut)
def list_splits(
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListOut:
    """Every split on the list (its newest version) with both sources' evidence, bucketed for the panel:
    needs review (loud), announced / confirmed / rejected-or-revoked (quiet) — the split-shaped price jumps
    on basket names that no source listed, after the split-night hold released them (the operator's "is this
    real?"), and the SEC filings about a split the list could not place, each with the listed split nearest it.
    Each split awaiting review or announced names the listed splits of its exact ratio near it; any split names the
    filings that call it off. Read-only.
    """
    try:
        return splits_out(conn, tenant_id=DATA_TENANT_ID)
    finally:
        conn.rollback()


@router.post("/{version_id}/confirm", response_model=AdminSplitListEntryOut)
def confirm_split(
    version_id: UUID,
    body: AdminSplitListConfirmIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """CONFIRM a split that needs review with one source's values (``source`` = ``sec`` | ``polygon``): its
    ratio and its first session become the list's (a first session still ahead lands as ``announced``). Undo
    with ``POST /admin/splits/{version_id}/revoke``. **404** unknown version, **409** not the split's newest
    version / not awaiting review / that source states no complete value / another split is listed on that
    date."""
    v = _commit(
        conn,
        lambda: split_repo.confirm(
            conn,
            version_id,
            source=SOURCE_SEC if body.source == "sec" else SOURCE_POLYGON,
            tenant_id=DATA_TENANT_ID,
            today=market_today(),
        ),
    )
    return _out(conn, v)


@router.get("/{version_id}/tape-step", response_model=AdminSplitTapeStepOut)
def split_tape_step(
    version_id: UUID,
    first_session: Annotated[RequestDate, Query(description="the first session at the new price")],
    ratio_from: Annotated[RatioText | None, Query(description="old shares (decimal text)")] = None,
    ratio_to: Annotated[RatioText | None, Query(description="new shares (decimal text)")] = None,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitTapeStepOut:
    """The stored tape's STEP at ``first_session`` for the split ``version_id`` names, each bar as first served,
    beside the factor ``ratio_from`` -> ``ratio_to`` the steward entered (both or neither): what ``POST
    .../confirm-values`` checks. **404** unknown version, **422** one side of the ratio only. Read-only.
    """
    if (ratio_from is None) != (ratio_to is None):
        raise HTTPException(status_code=422, detail="give both ratio_from and ratio_to, or neither")
    try:
        v = _data_version(conn, version_id)
        tape = _tape_now(conn, v.security_id, first_session)
    finally:
        conn.rollback()  # a read: hold no snapshot
    return tape_step_out(tape, first_session, ratio_from, ratio_to)


def tape_step_out(
    tape: TapeEvidence | None, first_session: date, ratio_from: str | None, ratio_to: str | None
) -> AdminSplitTapeStepOut:
    """The tape-step reading both routes answer (by split version, by security): the step at ``first_session`` as
    first served, beside the entered ratio's factor and ``tape_why``'s verdict. **422** a ratio out of range.
    """
    out = AdminSplitTapeStepOut(first_session=first_session)
    if tape is not None and tape.session == first_session:
        out.prior_date, out.prior_close, out.close = tape.prior, tape.prior_close, tape.close
        out.prior_volume, out.volume = tape.prior_volume, tape.volume
        out.step, out.volume_ratio = tape.step, tape.volume_ratio
        out.prior_served_before = tape.prior_served_before
    if ratio_from is not None and ratio_to is not None:
        try:
            rf, rt = _ratio(ratio_from, "ratio_from"), _ratio(ratio_to, "ratio_to")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        k = float(price_factor(rf, rt))
        out.price_factor = k
        out.step_low, out.step_high = (
            k * math.exp(-TAPE_TOLERANCE),
            k * math.exp(TAPE_TOLERANCE),
        )
        out.reason = tape_why(tape, first_session, rf, rt)
        out.within_tolerance = out.reason is None
    return out


def _data_security(conn: psycopg.Connection, security_id: UUID) -> Mapping[str, Any]:
    """The data tenant's master row for ``security_id`` (id, ticker); ``LookupError`` when there is none."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker FROM security_master WHERE id = %s AND tenant_id = %s",
            (security_id, DATA_TENANT_ID),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no security {security_id}")
    return row


@router.get("/securities/{security_id}/tape-step", response_model=AdminSplitTapeStepOut)
def security_tape_step(
    security_id: UUID,
    first_session: Annotated[RequestDate, Query(description="the first session at the new price")],
    ratio_from: Annotated[RatioText | None, Query(description="old shares (decimal text)")] = None,
    ratio_to: Annotated[RatioText | None, Query(description="new shares (decimal text)")] = None,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitTapeStepOut:
    """The stored tape's STEP at ``first_session`` for a security with no split listed there yet, each bar as first
    served, beside the factor ``ratio_from`` -> ``ratio_to`` the steward entered (both or neither): what ``POST
    /admin/splits`` checks, the same reading as ``GET /admin/splits/{version_id}/tape-step``. **404** unknown
    security, **422** one side of the ratio only. Read-only.
    """
    if (ratio_from is None) != (ratio_to is None):
        raise HTTPException(status_code=422, detail="give both ratio_from and ratio_to, or neither")
    try:
        _data_security(conn, security_id)
        tape = _tape_now(conn, security_id, first_session)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.rollback()  # a read: hold no snapshot
    return tape_step_out(tape, first_session, ratio_from, ratio_to)


@router.get("/{version_id}/tape", response_model=AdminSplitTapeOut)
def split_tape_window(
    version_id: UUID,
    around: Annotated[
        RequestDate | None,
        Query(description="the session to center on (default: the split's key_date)"),
    ] = None,
    sessions: Annotated[
        int, Query(ge=1, le=TAPE_SESSIONS_MAX, description="stored sessions each side (1-10)")
    ] = TAPE_SESSIONS_DEFAULT,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitTapeOut:
    """The stored closes and volumes around the split ``version_id`` names (W5-1): ``sessions`` stored sessions
    each side of ``around`` (default the split's ``key_date``), each bar as first served beside its newest close,
    and the step at ``around`` as first served. **404** unknown version, **422** ``sessions`` outside 1-10 or a
    bad date. Read-only.
    """
    try:
        v = _data_version(conn, version_id)
        at = around or v.key_date
        bars = tape_window(
            conn, tenant_id=DATA_TENANT_ID, security_id=v.security_id, around=at, sessions=sessions
        )
        tape = _tape_now(conn, v.security_id, at)
    finally:
        conn.rollback()  # a read: hold no snapshot
    out = AdminSplitTapeOut(
        version_id=v.id,
        around=at,
        sessions=sessions,
        bars=[
            AdminSplitTapeBarOut(
                d=b.d,
                close=b.close,
                volume=b.volume,
                newest_close=b.newest_close,
                restated=b.restated,
                filled=b.filled,
            )
            for b in bars
        ],
    )
    if tape is not None and tape.session == at:
        out.prior_date, out.step = tape.prior, tape.step
        out.volume_ratio, out.prior_served_before = tape.volume_ratio, tape.prior_served_before
    return out


@router.post("/{version_id}/confirm-values", response_model=AdminSplitListEntryOut)
def confirm_split_values(
    version_id: UUID,
    body: AdminSplitConfirmValuesIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """CONFIRM a split that needs review with the steward's OWN cited values (decision D3): ``ratio_from`` ->
    ``ratio_to``, ``first_session`` and the ``cited_url`` of the document that states them become the list's
    (``value_source`` ``steward``; a first session still ahead lands as ``announced``). The server reads the stored
    tape's step at ``first_session`` as first served and refuses a factor outside the tape tolerance, or a session
    with no bar, unless ``override_tape`` (stored as ``tape_override``). Undo with ``POST
    /admin/splits/{version_id}/revoke``. **404** unknown version, **409** not the split's newest version / not
    awaiting review / another split is listed on that date / the tape does not support the values, **422** a bad
    ratio, date or URL."""
    rf, rt = _ratio(body.ratio_from, "ratio_from"), _ratio(body.ratio_to, "ratio_to")

    def act() -> SplitVersion:
        seen = split_repo.get_version(conn, version_id)
        if seen is None or seen.tenant_id != DATA_TENANT_ID:
            raise LookupError(f"no split version {version_id}")
        return split_repo.confirm_steward(
            conn,
            version_id,
            ratio_from=rf,
            ratio_to=rt,
            first_session=body.first_session,
            cited_url=body.cited_url,
            note=body.note,
            tape_why=tape_why(
                _tape_now(conn, seen.security_id, body.first_session), body.first_session, rf, rt
            ),
            override_tape=body.override_tape,
            tenant_id=DATA_TENANT_ID,
            today=market_today(),
        )

    v = _commit(conn, act)
    return _out(conn, v)


def _filing_to_add(conn: psycopg.Connection, security_id: UUID, accession: str) -> dict[str, Any]:
    """The unplaced filing an add starts from, as the unplaced list reads it NOW (its evidence is the server's,
    never the client's); ``SplitConflict`` (409) when it is not listed, or the steward dismissed it.
    """
    versions = split_repo.latest_versions(
        conn, tenant_id=DATA_TENANT_ID, security_ids=[security_id]
    )
    keep, gone, _, error = _unplaced(
        conn, versions, tenant_id=DATA_TENANT_ID, only=(security_id, accession)
    )
    if gone:
        raise SplitConflict(
            f"filing {accession} was dismissed as not a split to list — undo the dismissal first"
        )
    if not keep:
        raise SplitConflict(
            f"filing {accession} is not on the unplaced list for security {security_id} — reload the panel"
            + (f" ({error})" if error else "")
        )
    u = keep[0]
    stated = stated_ratio(u.ratio_from, u.ratio_to)
    return {
        "accession": u.accession,
        "form": u.form or None,
        "filed": u.filed,
        "url": u.url,
        "passage": u.passage,
        "document": u.document,
        "ratio_from": stated[0] if stated else None,
        "ratio_to": stated[1] if stated else None,
        "reason": u.reason or None,
    }


def _jump_to_add(conn: psycopg.Connection, security_id: UUID, step_date: date) -> dict[str, Any]:
    """The split-shaped jump an add starts from, as the "is this real?" list reads it NOW; ``SplitConflict`` (409)
    when it is not listed, or the steward dismissed it as a real move."""
    keep, gone = _jumps(conn, tenant_id=DATA_TENANT_ID, asof=market_today())
    key = (security_id, step_date)
    if any((j.security_id, j.step_date) == key for j in gone):
        raise SplitConflict(
            f"the split-shaped jump on {step_date.isoformat()} was dismissed as a real move — undo the dismissal "
            "first"
        )
    for j in keep:
        if (j.security_id, j.step_date) == key:
            return {"step_date": j.step_date, "step": j.step}
    raise SplitConflict(
        f"no split-shaped jump on {step_date.isoformat()} is listed for security {security_id} — reload the panel"
    )


@router.post("", response_model=AdminSplitListEntryOut)
def add_split(
    body: AdminSplitAddIn,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """ADD a split NO source lists, with the steward's cited values (framework completion F2-1): ``ratio_from`` ->
    ``ratio_to``, ``first_session`` and the ``cited_url`` of the document that states them become a new split on the
    list (``action`` ``add``, ``value_source`` ``steward``; ``confirmed``, or ``announced`` while the first session is
    ahead). It starts from exactly one signal: ``from_accession`` (a filing on the current unplaced list, its
    evidence copied by the server) or ``from_jump`` (a jump on the current "is this real?" list). The server reads the
    stored tape's step at ``first_session`` as first served and refuses a factor outside the tape tolerance, or a
    session with no bar, unless ``override_tape`` (stored as ``tape_override``). Undo with ``POST
    /admin/splits/{version_id}/revoke`` (``/reject`` while announced). **404** unknown security, **409** the filing
    or jump is not listed or was dismissed / a split is already keyed or listed on that session / a live split of the
    same ratio is listed within 45 days / the tape does not support the values, **422** not exactly one signal, a bad
    ratio, date, URL or accession."""
    rf, rt = _ratio(body.ratio_from, "ratio_from"), _ratio(body.ratio_to, "ratio_to")

    def act() -> SplitVersion:
        _data_security(conn, body.security_id)
        source_filing = source_jump = None
        if body.from_accession is not None:
            source_filing = _filing_to_add(conn, body.security_id, body.from_accession)
        else:
            assert body.from_jump is not None
            source_jump = _jump_to_add(conn, body.security_id, body.from_jump)
        return split_repo.add_steward(
            conn,
            tenant_id=DATA_TENANT_ID,
            security_id=body.security_id,
            ratio_from=rf,
            ratio_to=rt,
            first_session=body.first_session,
            cited_url=body.cited_url,
            note=body.note,
            tape_why=tape_why(
                _tape_now(conn, body.security_id, body.first_session), body.first_session, rf, rt
            ),
            override_tape=bool(body.override_tape),
            today=market_today(),
            near_days=MATCH_WINDOW_DAYS,
            source_filing=source_filing,
            source_jump=source_jump,
        )

    v = _commit(conn, act)
    return _out(conn, v)


@router.post("/{version_id}/reject", response_model=AdminSplitListEntryOut)
def reject_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """REJECT a split that needs review (or an announced one): it stays listed, grayed. Undo with
    ``POST /admin/splits/{version_id}/restore``. **404** unknown version, **409** not the newest version / a
    confirmed split (revoke it instead)."""
    v = _commit(conn, lambda: split_repo.reject(conn, version_id, tenant_id=DATA_TENANT_ID))
    return _out(conn, v)


@router.post("/{version_id}/revoke", response_model=AdminSplitListEntryOut)
def revoke_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """REVOKE a confirmed split, auto-confirmed or not: it stays listed, grayed. Undo with
    ``POST /admin/splits/{version_id}/restore``. **404** unknown version, **409** not the newest version /
    not confirmed."""
    v = _commit(conn, lambda: split_repo.revoke(conn, version_id, tenant_id=DATA_TENANT_ID))
    return _out(conn, v)


_T = TypeVar("_T")


def _commit_jump(conn: psycopg.Connection, fn: Callable[[], _T]) -> _T:
    """One dismissal (or its Undo) of a jump or of an unplaced filing in its own transaction — the errors mapped
    like every split action."""
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (JumpDismissalConflict, FilingDismissalConflict) as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


def _jump_now(
    conn: psycopg.Connection, *, tenant_id: UUID, security_id: UUID, step_date: date
) -> AdminSplitJumpOut:
    """The jump as the panel lists it now (with its standing dismissal, if any); 404 when it is not listed."""
    try:
        keep, gone = _jumps(conn, tenant_id=tenant_id, asof=market_today())
    finally:
        conn.rollback()  # a read: hold no snapshot
    for j in (*keep, *gone):
        if j.security_id == security_id and j.step_date == step_date:
            return j
    raise HTTPException(
        status_code=404,
        detail=f"no split-shaped jump on {step_date.isoformat()} is listed for security {security_id}",
    )


@router.post("/jumps/{security_id}/{step_date}/dismiss", response_model=AdminSplitJumpOut)
def dismiss_jump(
    security_id: UUID,
    step_date: date,
    body: AdminSplitJumpReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitJumpOut:
    """DISMISS an "is this real?" card — the operator says the split-shaped jump was a REAL MOVE (author
    ``operator:dismiss``, append-only). It leaves the list and is listed under ``dismissed_jumps``; the
    split-night hold never reads it (display only — no call changes). Undo with ``POST
    /admin/splits/jumps/dismissals/{dismissal_id}/undo``. **404** no such jump on the list, **409** already
    dismissed."""
    jump = _jump_now(conn, tenant_id=DATA_TENANT_ID, security_id=security_id, step_date=step_date)
    reason = (body.reason if body and body.reason else None) or "a real move, said the operator"
    _commit_jump(
        conn,
        lambda: split_jump_repo.dismiss(
            conn,
            tenant_id=DATA_TENANT_ID,
            security_id=security_id,
            step_date=step_date,
            reason=reason,
            detail={
                "prior_date": jump.prior_date.isoformat(),
                "prior_close": jump.prior_close,
                "close": jump.close,
                "step": jump.step,
                "label": jump.label,
            },
        ),
    )
    return _jump_now(conn, tenant_id=DATA_TENANT_ID, security_id=security_id, step_date=step_date)


@router.post("/jumps/dismissals/{dismissal_id}/undo", response_model=AdminSplitJumpOut)
def undo_jump_dismissal(
    dismissal_id: UUID,
    body: AdminSplitJumpReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitJumpOut:
    """UNDO a dismissal (author ``operator:undo``, append-only): the card is back on the "is this real?" list.
    **404** unknown dismissal (or its jump no longer listed), **409** already undone."""
    reason = (body.reason if body and body.reason else None) or "undone by the operator"
    row = _commit_jump(
        conn,
        lambda: split_jump_repo.undo(conn, dismissal_id, tenant_id=DATA_TENANT_ID, reason=reason),
    )
    return _jump_now(
        conn, tenant_id=DATA_TENANT_ID, security_id=row["security_id"], step_date=row["step_date"]
    )


def _unplaced_now(
    conn: psycopg.Connection, *, security_id: UUID, accession: str
) -> AdminSplitUnplacedOut:
    """The filing as the panel lists it now (with its standing dismissal, if any); 404 when it is not listed."""
    try:
        versions = split_repo.latest_versions(
            conn, tenant_id=DATA_TENANT_ID, security_ids=[security_id]
        )
        keep, gone, _, _ = _unplaced(
            conn, versions, tenant_id=DATA_TENANT_ID, only=(security_id, accession)
        )
    finally:
        conn.rollback()  # a read: hold no snapshot
    found = [*keep, *gone]
    if found:
        return found[0]
    raise HTTPException(
        status_code=404,
        detail=f"filing {accession} is not on the unplaced list for security {security_id}",
    )


def _still_unplaced(security_id: UUID, accession: str) -> bool:
    """Is the filing on the unplaced list now (a file read; no DB)?"""
    key = (str(security_id), accession)
    return any((r["security_id"], r["accession"]) == key for r in read_unplaced().rows)


@router.post("/unplaced/{security_id}/{accession}/dismiss", response_model=AdminSplitUnplacedOut)
def dismiss_unplaced(
    security_id: UUID,
    accession: Annotated[
        str, Path(pattern=ACCESSION_PATTERN, description="the filing's SEC accession number")
    ],
    body: AdminSplitJumpReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitUnplacedOut:
    """DISMISS an unplaced split filing — the steward says it is NOT A SPLIT TO LIST (author ``operator:dismiss``,
    append-only). It leaves ``unplaced`` and is listed under ``unplaced_dismissed``; display only (no split,
    hold or call changes). Undo with ``POST /admin/splits/unplaced/dismissals/{dismissal_id}/undo``. **404** the
    filing is not on the unplaced list, **409** already dismissed, **422** a malformed accession."""
    u = _unplaced_now(conn, security_id=security_id, accession=accession)
    reason = (body.reason if body and body.reason else None) or UNPLACED_DISMISS_REASON
    _commit_jump(
        conn,
        lambda: split_filing_dismissal_repo.dismiss(
            conn,
            tenant_id=DATA_TENANT_ID,
            security_id=security_id,
            accession=accession,
            reason=reason,
            detail={
                "ticker": u.ticker,
                "form": u.form,
                "filed": u.filed.isoformat(),
                "reason": u.reason,
                "source": u.source,
                "seen_on": u.seen_on.isoformat(),
            },
        ),
    )
    return _unplaced_now(conn, security_id=security_id, accession=accession)


@router.post("/unplaced/dismissals/{dismissal_id}/undo", response_model=AdminSplitUnplacedOut)
def undo_unplaced_dismissal(
    dismissal_id: UUID,
    body: AdminSplitJumpReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitUnplacedOut:
    """UNDO a filing's dismissal (author ``operator:undo``, append-only): the filing is back on ``unplaced``.
    **404** unknown dismissal, or its filing is no longer on the unplaced list (nothing written), **409** already
    undone."""
    reason = (body.reason if body and body.reason else None) or UNPLACED_UNDO_REASON

    def act() -> Mapping[str, Any]:
        row = split_filing_dismissal_repo.undo(
            conn, dismissal_id, tenant_id=DATA_TENANT_ID, reason=reason
        )
        if not _still_unplaced(row["security_id"], row["accession"]):
            raise LookupError(f"filing {row['accession']} is no longer on the unplaced list")
        return row

    row = _commit_jump(conn, act)
    return _unplaced_now(conn, security_id=row["security_id"], accession=row["accession"])


@router.post("/{version_id}/restore", response_model=AdminSplitListEntryOut)
def restore_split(
    version_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminSplitListEntryOut:
    """RESTORE a rejected or revoked split to exactly the state before (a rejected split back to review, a
    revoked one back to confirmed; one the system set aside as a distribution back to review). **404** unknown
    version, **409** not the newest version / not set aside."""
    v = _commit(conn, lambda: split_repo.restore(conn, version_id, tenant_id=DATA_TENANT_ID))
    return _out(conn, v)
