from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    """Thesis lifecycle (a loop, not a ratchet)."""

    INCUBATING = "incubating"
    WARMING = "warming"
    ARMED = "armed"
    MANAGING = "managing"


class Verdict(StrEnum):
    WATCHING = "watching"
    NOT_YET = "not_yet"
    FLIP_ONLY = "flip_only"  # a flip thesis: small, short-dated, do-not-hold
    STARTER_ENTRY = (
        "starter_entry"  # core thesis, starter-sized entry (confirmation not volume-backed)
    )
    CORE_ENTRY = "core_entry"  # core thesis, volume-confirmed: build to core size
    MANAGING = "managing"


class Grade(StrEnum):
    FLIP = "flip"  # fast, sentiment-driven; trade small/short-dated; do not hold
    CORE = "core"  # structural; build the position


class Role(StrEnum):
    """Only entry triggers can turn the two keys; risk signals feed counter-case/confidence."""

    ENTRY_TRIGGER = "entry_trigger"
    RISK_SIGNAL = "risk_signal"


class Kind(StrEnum):
    """What produced the signal (extensible)."""

    INSIDER = "insider"
    CATALYST = (
        "catalyst"  # Key-1 conviction for theme theses (deterministic/ratified commitment, #10)
    )
    # Key-1 FALLBACK: an operator-ratified, thesis-level theme conviction that arms an otherwise-
    # confirmed basket member as a disciplined STARTER (M5b). The weaker key — capped at flip.
    THEME_CONVICTION = "theme_conviction"
    TECHNICAL_BREAKOUT = "technical_breakout"
    LAGGARD = "laggard"
    SQUEEZE = "squeeze"
    ETF_LAUNCH = "etf_launch"
    ETF_FLOW = "etf_flow"
    DILUTION_RISK = "dilution_risk"


class CatalystType(StrEnum):
    """The catalyst nature, where one applies (optional on a signal; extensible)."""

    REGULATORY = "regulatory"  # e.g. an NRC license action
    PROMOTER_ATTENTION = "promoter_attention"
    CLINICAL_READOUT = "clinical_readout"
    PERSONNEL = "personnel"
    EARNINGS = "earnings"
    CONTRACT = "contract"  # e.g. a power-purchase / offtake agreement
    GOV_FUNDING = "gov_funding"  # e.g. a DOE loan guarantee / grant award


class InstrumentKind(StrEnum):
    """What an instrument IS on the security master — the modular foundation brick (ETF Sleeve, Slice 1).

    ``EQUITY`` = a common-stock operating company (the default; every existing master row). ``ETF`` = a fund
    the operator SURFACES as the low-torque ``fund`` sleeve expression of a thesis. Operator-DECLARED, never
    auto-detected. Descriptive IDENTITY (like ``sector`` / ``category``): it never feeds a number on a call
    card and never gates the call path — the sleeve is an EXPRESSION, never a call input (#4/#6). Future
    callable kinds (``spac``, …) extend this enum WITHOUT another migration+backfill.
    """

    EQUITY = "equity"
    ETF = "etf"


class Archetype(StrEnum):
    """A basket member's role in expressing the thesis."""

    LEADER = "leader"
    HIGH_BETA = "high_beta"
    LOTTO = "lotto"
    SHOVEL = "shovel"
    ADJACENT = "adjacent"  # off-thesis / impure exposure — surfaced and FLAGGED, never hidden (Workbench hero)
    FUND = "fund"  # an ETF: the low-torque safe-exposure sleeve expression of the thesis


class BusinessType(StrEnum):
    """WHAT a company DOES — the business-type LEAF (MONITOR/organization identity, Business-Type M1).

    Derived on read from the master's stored ``sector`` (EDGAR ``sicDescription``) via the editable
    data files in ``securities/business_type/`` — descriptive IDENTITY like ``sector``/``origin``
    (#1/#3 govern numbers, not identity strings): never a fact, never a call input, never fires/arms/
    vetoes/grades. ``OTHER`` is the visible tail (an unmapped-but-present sector — never a silent
    drop, #9); an un-enriched row derives ``None`` (unclassified — the honest abstain). This enum is
    the TYPE CONTRACT; the SIC→leaf DATA lives in ``sic_map.csv`` (operator-editable)."""

    MINER = "miner"
    BANK = "bank"
    UTILITIES = "utilities"
    OIL_GAS = "oil_gas"
    SEMICONDUCTORS = "semiconductors"
    BIOTECH_PHARMA = "biotech_pharma"
    MEDICAL_DEVICES = "medical_devices"
    HEALTHCARE_SERVICES = "healthcare_services"
    SOFTWARE_IT = "software_it"
    FINANCE_BROKERS = "finance_brokers"
    REAL_ESTATE = "real_estate"
    INDUSTRIALS_MACHINERY = "industrials_machinery"
    CHEMICALS_MATERIALS = "chemicals_materials"
    COMMS_MEDIA = "comms_media"
    TRANSPORTATION = "transportation"
    CONSUMER_RETAIL = "consumer_retail"
    BUSINESS_SERVICES = "business_services"
    # a blank-check shell (SIC "Blank Checks") — a LEAF for now by operator ruling; flagged as a
    # candidate to move to the instrument-kind axis later (a SPAC is arguably what the INSTRUMENT is)
    SPAC = "spac"
    OTHER = "other"


class BusinessSupersector(StrEnum):
    """The business-type SUPER-SECTOR — the coarse watch grouping above the leaf ("are the utilities
    moving?"). Pure code map from the leaf (``securities/business_type/supersectors.csv``), zero
    storage, re-drawable without a migration. Identity like ``BusinessType``; never a call input."""

    HEALTHCARE = "healthcare"
    FINANCIALS = "financials"
    TECHNOLOGY = "technology"
    INDUSTRIALS = "industrials"
    CONSUMER_COMMS = "consumer_comms"
    MATERIALS = "materials"
    ENERGY_UTILITIES = "energy_utilities"
    REAL_ESTATE = "real_estate"
    OTHER = "other"


class TermTier(StrEnum):
    """A discovery keyword's tier in the thesis's persisted term set — the precision filter's INPUT.

    SIGNAL = a discriminating compound / drug / mechanism token; a single EFTS hit PLACES a company.
    BROAD = a collision-prone abbreviation or disease/indication term; counts only toward the >=2-distinct
    rule, never places alone. The thesis OWNS this tiering (a deterministic guard sets the default; the
    operator overrides later) and discovery READS it — the "is this discriminating?" decision is OFF the LLM.
    """

    SIGNAL = "signal"
    BROAD = "broad"


class Authorship(StrEnum):
    """Who placed a basket member in its value-chain segment (the Workbench authorship seam).

    The MVP writes only the operator values; ``SYSTEM_DRAFTED`` is present now so the seam already
    supports the bounded LLM drafter (the next slice) without a later schema change.
    """

    SYSTEM_DRAFTED = "system_drafted"  # LLM-drafted placement (post-MVP); the operator ratifies it
    OPERATOR_SET = "operator_set"  # the operator placed it
    OPERATOR_EDITED = "operator_edited"  # the operator edited a drafted placement
