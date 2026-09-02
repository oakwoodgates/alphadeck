# Recall answer-key — INVARIANT #9 ground truth
#
# PROVENANCE: this is the ~29-name psychedelic-thesis ground truth used to
# score discovery recall for INVARIANT #9 ("recall is sacred"). It was the
# hand-verified set of companies that SHOULD be discovered for the psychedelic
# thesis, established during the discovery bake-off — the reference the EDGAR-
# first discovery re-score is measured against.
#
# CURRENT RESULT (2026-09-01 live re-score — the draft-scope gate, PR #303):
# RECALL HOLDS 31/31 placeable (all of ANSWER below), on a clean 201/201-page
# full-scope run. ATAI — the historical miss (the dual-CIK redomicile) — has
# been RECALLED since 2026-07-06 (both CIKs surface, off-universe; the
# identity bridge was therefore DROPPED by operator decision: identity-MERGE
# logic is subtle-bug-prone and the remaining value — collapsing a duplicate
# row — is cosmetic; see DISCOVERY.md).
#
# THE PRTG RULING v2 (operator, 2026-09-01 — SUPERSEDES 2026-07-06): PRTG
# (Portage Biotech) DELISTED and dropped out of SEC company_tickers entirely —
# no master row, structurally unplaceable, and NOT reliably surfaced: its only
# path onto a draft is the stochastic organizer/tail-sweep corner, and it has
# surfaced in ZERO recorded dev runs since 2026-07-31 (the 2026-07-06
# surfacing that ruling v1 counted on was one lucky Opus pass; verified
# against the data/draft_runs artifacts during the #303 gate). Ruling: the
# HARD BAR is 31/31 on ANSWER (the placeable set); PRTG moves to
# SURFACED_ONLY_CANARY below — a re-score still CHECKS and REPORTS whether it
# surfaced shown-not-placed (the canary's value: it marks the corner EFTS
# structurally can't see), but its absence no longer fails the gate. Ruling
# v1's principle stands unchanged for any FUTURE delisted-but-surfaceable
# name: #9 tests SILENT DROPS, not placeability — a name the operator can see
# and judge was never dropped.
#
# WHY IT'S COMMITTED: #9 is a standing invariant. Its guarantee ("no name
# silently dropped") is only testable if this ground truth is committed — it
# previously lived only in gitignored temp scripts, meaning the recall gate
# couldn't actually be re-run after a discovery change. This fixture makes the
# #9 recall re-score reproducible.
#
# HOW TO USE: run the discovery pipeline against the psychedelic thesis and
# score placed+verify names against ANSWER — AT CIK LEVEL (a CIK carries
# several master ticker rows — common + warrants, e.g. KTTAW/PBMWW — and
# ids_for_ciks resolves to one of them arbitrarily; compare a company's
# ticker GROUP against ALL tickers of each surfaced CIK, or ticker-string
# scoring miscounts). The bar is 31/31 on ANSWER. Separately, CHECK and
# REPORT (never assume, never count) whether each SURFACED_ONLY_CANARY name
# appears anywhere on the draft (any status — the Couldn't-resolve drawer
# included). A name in ANSWER that does NOT appear in discovery output is a
# #9 failure (a silent drop) and must be investigated, never waved through.
#
# MAINTENANCE: this is a reference fixture, not a live-data snapshot. It changes
# only if the ground-truth membership genuinely changes (a company delists,
# renames, or a real omission is found) — NOT to make a failing test pass.
# Editing this to match broken output defeats the invariant.

# The operator's canonical seed compounds — the recall GUARANTOR (a hit on one
# of these PLACES a name; the deterministic guard keeps 3-letter collision
# tokens like MDMA/DMT/LSD and broad "ketamine" as BROAD). esketamine covers the
# ketamine-adjacent bracket (J&J Spravato).
SEEDS: list[str] = [
    "psilocybin",
    "psilocin",
    "ibogaine",
    "noribogaine",
    "esketamine",
    "arketamine",
    "mescaline",
    "5-MeO-DMT",
    "mebufotenin",
    "lysergic acid",
    "neuroplastogen",
]

# The ground truth — one set of ACCEPTABLE tickers per company (a set handles
# rebrands / dual tickers on one CIK, e.g. Cybin CYBN/HELP, MindMed→Definium
# MNMD/DFTX). 31 placeable companies: the CORE psychedelic developers + the
# ADJACENT CNS bracket (esketamine/ketamine names — NBIX/ALKS/AXSM/ANRO/…). A
# company is "recalled" if ANY ticker in its set appears in discovery's
# PLACED+VERIFY. (PRTG moved to SURFACED_ONLY_CANARY — ruling v2 above.)
ANSWER: list[set[str]] = [
    {"CMPS"},
    {"CYBN", "HELP"},
    {"MNMD", "DFTX"},
    {"ATAI"},
    {"GHRS"},
    {"DRUG"},
    {"ENVB"},
    {"CMND"},
    {"SILO"},
    {"PBM"},
    {"OPTH"},
    {"MIRA"},
    {"NRXP"},
    {"RLMD"},
    {"IXHL"},
    {"SPRC"},
    {"JUNS"},
    {"KTTA"},
    {"TNXP"},
    {"VTGN"},
    {"PLRZ"},
    {"XTLB"},
    {"ANRO"},
    {"AXSM"},
    {"ALKS"},
    {"JNJ"},
    {"BHVN"},
    {"NMRA"},
    {"NERV"},
    {"SUPN"},
    {"XENE"},
]

# SURFACED-ONLY canaries (ruling v2, 2026-09-01) — real on-thesis names that are
# STRUCTURALLY UNPLACEABLE (delisted out of company_tickers → no master row) and
# reach a draft only through the stochastic organizer/tail-sweep corner. NOT
# counted toward the recall bar; a re-score CHECKS and REPORTS each (surfaced
# shown-not-placed, or absent) so the corner's behavior stays on the record. A
# future mechanism that surfaces these RELIABLY would be measured against this
# list — and a canary that RELISTS moves back into ANSWER.
SURFACED_ONLY_CANARY: list[set[str]] = [
    {"PRTG"},  # Portage Biotech — delisted; last recorded surfacing 2026-07-06
]

# The named collision "junk" the OLD (LLM-tiered) draft wrongly PLACED — miners /
# utilities / retail that hit only a 3-letter collision token. It must be GONE
# from PLACED (a nuisance in VERIFY is deletable; junk auto-placed is the failure
# the SIGNAL/BROAD guard exists to prevent).
JUNK: set[str] = {
    "VRSN",
    "ED",
    "PCG",
    "EIX",
    "META",
    "NOC",
    "UNH",
    "CI",
    "GT",
    "LEVI",
    "SO",
    "DUK",
    "D",
    "AEP",
}

# The demo tenant's persisted "psychedelic therapy" thesis this key scores against.
PSYCHEDELIC_THESIS_ID = "e7aa7aa2-b61f-46e3-b745-7b66ac95b2ed"
