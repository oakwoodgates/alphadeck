"""The TWO tenant scopes over the one ``tenant_id`` column (docs/TENANT_SCOPES.md).

Every table that carries ``tenant_id`` belongs to exactly one scope, named here ONCE:

- **DATA** — the single shared data tenant (``DATA_TENANT_ID``, the ``tenant`` row with ``kind='data'``,
  migration 0076): the company master, every ingested fact, the split / ticker / CIK history, the onboarding
  queue, the price-feed repairs, the Form 4/A tables and their decisions, the held insider rows, the set-asides
  and the repair journal.
  Since V2 it also holds the platform's share counts, cash/burn and company catalysts (``fact_shares_outstanding``
  / ``fact_cash_burn`` / ``fact_catalyst``): one value per company for everyone, never a per-workspace copy.
  The feed writes it; everyone reads it.
- **WORKSPACE** — one per user (a ``tenant`` row with ``kind='workspace'``; migration 0078 moved the founding
  operator's rows out of the data tenant into ``FOUNDING_WORKSPACE_ID``): the thesis and its children, the calls
  of record, the annotations, the operator's decisions, the editions store, the thesis-scoped facts, and (since
  V2) purity (``fact_revenue_mix``): a workspace's own judgment, read and written in that workspace only.

V2 removed the third scope (MIXED, a read that unioned the data tenant and the workspace): every read names ONE
tenant, and a writer of a DATA table writes the data tenant only (``require_write_tenant``).

The map must grow with the schema: ``tests/db/test_scopes.py`` parses every migration and fails when a table
with a ``tenant_id`` column is missing here. Nothing else may hard-code which tenant a table lives in — a read
names its scope through ``tenants_for``.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any
from uuid import UUID

# The ONE shared data tenant: today's ``…0001`` (migration 0001's seeded tenant; 0076 marks it ``kind='data'``).
DATA_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# The FOUNDING WORKSPACE: a fixed id so migration 0078 (which creates it and moves the founding operator's
# workspace rows out of the data tenant) is safe to run unattended on boot. The boot seed writes its demo cards here
# and ``pipeline.users`` attaches the founding operator to it.
FOUNDING_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000002")


class Scope(Enum):
    DATA = "data"
    WORKSPACE = "workspace"


SCOPE_OF: dict[str, Scope] = {
    # --- DATA: the shared layer (the feed writes it; every workspace reads it) -----------------------------
    "security_master": Scope.DATA,
    "fact_insider_txn": Scope.DATA,
    "fact_price_eod": Scope.DATA,
    "fact_dilution": Scope.DATA,
    "fact_fundamentals": Scope.DATA,
    "fact_corporate_event": Scope.DATA,
    "fact_activist_stake": Scope.DATA,
    "fact_fund_shares": Scope.DATA,
    "fact_spac_event": Scope.DATA,
    "fact_filing_index": Scope.DATA,
    "fact_fund_filing": Scope.DATA,
    "fact_fund_series": Scope.DATA,
    "fact_fund_symbol": Scope.DATA,
    "security_split": Scope.DATA,
    "security_split_history": Scope.DATA,
    "security_ticker_history": Scope.DATA,
    "security_cik_change": Scope.DATA,
    "security_onboarding": Scope.DATA,
    "price_symbol_change": Scope.DATA,
    "price_feed_ack": Scope.DATA,
    "price_restatement_hold": Scope.DATA,
    "insider_amendment": Scope.DATA,
    "insider_amendment_link": Scope.DATA,
    "insider_amendment_decision": Scope.DATA,
    "insider_txn_hold": Scope.DATA,
    "insider_txn_hold_decision": Scope.DATA,
    "fundamentals_set_aside": Scope.DATA,
    "split_jump_dismissal": Scope.DATA,
    "data_repair": Scope.DATA,
    "security_change": Scope.DATA,
    # --- WORKSPACE: one per user (the thesis and everything hanging off it) --------------------------------
    "thesis": Scope.WORKSPACE,
    "basket_member": Scope.WORKSPACE,
    "evidence": Scope.WORKSPACE,
    "catalyst": Scope.WORKSPACE,
    "kill_criterion": Scope.WORKSPACE,
    "thesis_exclusion": Scope.WORKSPACE,
    "basket_snapshot": Scope.WORKSPACE,
    "calls": Scope.WORKSPACE,
    "call_annotation": Scope.WORKSPACE,
    "operator_decision": Scope.WORKSPACE,
    "edition": Scope.WORKSPACE,
    "edition_event": Scope.WORKSPACE,
    "edition_entry": Scope.WORKSPACE,
    "edition_call": Scope.WORKSPACE,
    "edition_outcome": Scope.WORKSPACE,
    "edition_cause": Scope.WORKSPACE,
    "fact_theme_conviction": Scope.WORKSPACE,
    "fact_term_mentions": Scope.WORKSPACE,
    "fact_spac_match": Scope.WORKSPACE,
    # --- V2 W1a: the four formerly-MIXED tables, one scope each (no read unions two tenants) ---------------
    "fact_revenue_mix": Scope.WORKSPACE,  # purity: the workspace's own judgment
    "fact_cash_burn": Scope.DATA,  # platform values: one per company, for everyone
    "fact_shares_outstanding": Scope.DATA,
    "fact_catalyst": Scope.DATA,
}


def scope_of(table: str) -> Scope:
    """The scope ``table`` lives in. An unmapped table raises: a new tenant-bearing table must be named here."""
    try:
        return SCOPE_OF[table]
    except KeyError:
        raise KeyError(
            f"table {table!r} has no tenant scope — add it to domain.scopes.SCOPE_OF"
        ) from None


def tenants_for(table: str, *, data: UUID, workspace: UUID) -> tuple[UUID, ...]:
    """The tenant ids a read of ``table`` covers — always ONE: DATA -> ``(data,)``, WORKSPACE -> ``(workspace,)``.
    A tuple (not a bare id) so the memo keys, ``as_tenants`` and the mirror export take it unchanged.
    """
    if scope_of(table) is Scope.DATA:
        return (data,)
    return (workspace,)


def require_write_tenant(table: str, tenant_id: UUID) -> None:
    """Refuse a write of a DATA table under any tenant other than ``DATA_TENANT_ID`` (``ValueError``): every read
    of a DATA table names the data tenant only, so such a row would be read by nobody. Called first by each
    writer of a platform value. A WORKSPACE table is not checked (the test suite uses the data tenant as a
    workspace). An unmapped table raises ``KeyError`` (``scope_of``)."""
    if scope_of(table) is Scope.DATA and tenant_id != DATA_TENANT_ID:
        raise ValueError(
            f"{table} is platform data: it is written under the data tenant {DATA_TENANT_ID} only, "
            f"not {tenant_id}"
        )


def as_tenants(tenants: UUID | Sequence[UUID]) -> tuple[UUID, ...]:
    """Normalize one tenant id or several to a de-duplicated, order-preserving, non-empty tuple — the shape the
    accessors bind as ``tenant_id = ANY(%(tenants)s)``. An empty list raises: a read with no scope would answer
    nothing, silently."""
    if isinstance(tenants, UUID):
        return (tenants,)
    out = tuple(dict.fromkeys(tenants))
    if not out or not all(isinstance(t, UUID) for t in out):
        raise ValueError(
            f"a tenant scope must be one UUID or a non-empty list of UUIDs: {tenants!r}"
        )
    return out


class _WorkspaceAny:
    """The type of ``WORKSPACE_ANY`` (one instance; compare with ``is``)."""

    _instance: _WorkspaceAny | None = None

    def __new__(cls) -> _WorkspaceAny:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "WORKSPACE_ANY"

    def __reduce__(self) -> str:  # pickles (the backtest's process pool) as the same singleton
        return "WORKSPACE_ANY"


# The "every workspace" sentinel for a card-selecting read (``thesis_repo.get`` / ``get_asof`` / ``list_all`` /
# ``lifespans`` / ``created_at_for``): no workspace filter. SYSTEM code only — the nightly, the editions job, the
# repair / backfill CLIs, the replay harness. A request path always names the caller's workspace; a grep test
# (``tests/app/test_workspace_isolation.py``) fails if this name appears under ``app/``, ``dashboards/`` or the
# route-facing radar modules.
WORKSPACE_ANY = _WorkspaceAny()

# What a card-selecting read takes: one workspace's id, or the system sentinel.
WorkspaceScope = UUID | _WorkspaceAny


def workspace_filter(
    workspace: WorkspaceScope, column: str = "tenant_id"
) -> tuple[str, tuple[Any, ...]]:
    """``(sql, params)`` for a card-selecting read: ``" AND <column> = %s"`` with the workspace id, or ``""`` for
    ``WORKSPACE_ANY``. Anything else (``None`` above all) raises ``TypeError``: a forgotten workspace must fail,
    never read every workspace."""
    if workspace is WORKSPACE_ANY:
        return "", ()
    if isinstance(workspace, UUID):
        return f" AND {column} = %s", (workspace,)
    raise TypeError(
        f"workspace must be a workspace id (UUID) or domain.scopes.WORKSPACE_ANY, not {workspace!r}"
    )


def resolve_scopes(*, data_tenant: UUID | None, workspace: UUID) -> tuple[UUID, UUID]:
    """``(data_tenant, workspace)`` for a point-in-time view (the live PIT and the replay mirror share it).

    ``workspace`` is REQUIRED and must be a tenant id: a view always names whose workspace rows (the thesis-scoped
    facts, purity) it reads; ``None`` raises ``TypeError`` (auth PR 2 removed the PR 1
    ``tenant_id=`` transition alias and the silent default). ``data_tenant`` unset is ``DATA_TENANT_ID``, the one
    shared data tenant."""
    if not isinstance(workspace, UUID):
        raise TypeError(f"a point-in-time view must name its workspace (a UUID), not {workspace!r}")
    return (data_tenant if data_tenant is not None else DATA_TENANT_ID, workspace)


class DataTenantMissing(RuntimeError):
    """The database has no data tenant: the ``tenant`` row ``DATA_TENANT_ID`` is missing or is not
    ``kind='data'`` (migration 0076 not applied, or a hand-edited row). Every feed write and DATA read names that
    row, so the app refuses to start rather than default silently."""


def assert_data_tenant(conn: Any) -> None:
    """Raise ``DataTenantMissing`` unless the ``DATA_TENANT_ID`` tenant row exists with ``kind='data'``. Called at
    API boot (the lifespan) and at the start of the nightly pass. Read-only; ``conn`` is a psycopg connection
    (dict rows or tuple rows)."""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT kind FROM tenant WHERE id = %s", (DATA_TENANT_ID,))
        except Exception as exc:  # noqa: BLE001 — a missing column is the same failure, named
            raise DataTenantMissing(
                f"cannot read tenant {DATA_TENANT_ID}'s kind ({exc.__class__.__name__}: {exc}) — "
                "is migration 0076_tenant_kind applied? Run `python -m db.migrate`."
            ) from exc
        row = cur.fetchone()
    if row is None:
        raise DataTenantMissing(
            f"the data tenant {DATA_TENANT_ID} does not exist — run `python -m db.migrate`"
        )
    kind = row["kind"] if isinstance(row, dict) else row[0]
    if kind != "data":
        raise DataTenantMissing(
            f"tenant {DATA_TENANT_ID} is kind={kind!r}, not 'data' — migration 0076 marks it; "
            "refusing to run with no data tenant"
        )


# --- V2 W0a: one owner per table (the ownership registry) ---------------------------------------------------
# Additive block (V2 PR 1). ``SCOPE_OF`` names every table that carries ``tenant_id``; ``UNSCOPED_TABLES`` names
# every table that does not, so together they name EVERY table the schema has (``tests/db/test_scopes.py`` parses
# the migrations and fails on a table named in neither or in both). The cutover dry run
# (``python -m pipeline.v2_cutover plan``) reads the live table list and refuses (exit 2) on any table not named
# here. ``"platform"`` = one row set for the whole platform; ``"user-keyed"`` = rows keyed by a user (``app_user``).
UNSCOPED_TABLES: dict[str, str] = {
    # --- platform ----------------------------------------------------------------------------------------
    "tenant": "platform",
    "sec_request_budget": "platform",
    "schema_migrations": "platform",  # created by db/migrate.py, not by a migration
    # --- user-keyed --------------------------------------------------------------------------------------
    "app_user": "user-keyed",
    "user_session": "user-keyed",
    "api_token": "user-keyed",
    "user_meta": "user-keyed",
    "oauth_client": "user-keyed",
    "oauth_consent": "user-keyed",
    "oauth_grant": "user-keyed",
    "oauth_code": "user-keyed",
    "oauth_access_token": "user-keyed",
    "oauth_refresh_token": "user-keyed",
    "user_action_count": "user-keyed",
}

# The owner a scope means: DATA rows belong to the platform, WORKSPACE rows to a user's workspace.
_OWNER_OF_SCOPE: dict[Scope, str] = {Scope.DATA: "platform", Scope.WORKSPACE: "user"}


def owner_of(table: str) -> str:
    """Who owns ``table``'s rows: ``"platform"`` (a DATA table, or an unscoped platform table), ``"user"`` (a
    WORKSPACE table), ``"user-keyed"`` (an unscoped table keyed by a user), or the scope's own name for any other
    scope. A table named nowhere raises ``KeyError`` naming the file to edit."""
    scope = SCOPE_OF.get(table)
    if scope is not None:
        return _OWNER_OF_SCOPE.get(scope, scope.value)
    if table in UNSCOPED_TABLES:
        return UNSCOPED_TABLES[table]
    raise KeyError(
        f"table {table!r} has no owner - name it in domain.scopes (SCOPE_OF if it carries tenant_id, "
        "else UNSCOPED_TABLES)"
    )


# --- end V2 W0a block ---------------------------------------------------------------------------------------
