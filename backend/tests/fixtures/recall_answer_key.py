# Recall answer-key — INVARIANT #9 ground truth
#
# PROVENANCE: this is the ~29-name psychedelic-thesis ground truth used to
# score discovery recall for INVARIANT #9 ("recall is sacred"). It was the
# hand-verified set of companies that SHOULD be discovered for the psychedelic
# thesis, established during the discovery bake-off — the reference the EDGAR-
# first discovery re-score is measured against (last result: 31/32, the single
# miss being ATAI's dual-CIK redomicile, which the deferred identity bridge
# addresses).
#
# WHY IT'S COMMITTED: #9 is a standing invariant. Its guarantee ("no name
# silently dropped") is only testable if this ground truth is committed — it
# previously lived only in gitignored temp scripts, meaning the recall gate
# couldn't actually be re-run after a discovery change. This fixture makes the
# #9 recall re-score reproducible.
#
# HOW TO USE: run the discovery pipeline against the psychedelic thesis and
# score placed+verify names against this set. A name in this set that does NOT
# appear in discovery output is a #9 failure (a silent drop) and must be
# investigated, never waved through.
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
# MNMD/DFTX). 32 companies: the CORE psychedelic developers + the ADJACENT CNS
# bracket (esketamine/ketamine names — NBIX/ALKS/AXSM/ANRO/…). A company is
# "recalled" if ANY ticker in its set appears in discovery's PLACED+VERIFY.
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
    {"PRTG"},
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
