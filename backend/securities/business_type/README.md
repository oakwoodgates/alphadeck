# Business-type taxonomy

What a company does, at two levels: a leaf (`miner`, `utilities`, `biotech_pharma`, ...) and its super-sector
(`materials`, `energy_utilities`, `healthcare`, ...), plus the royalty/streaming overlay (a company-name tell that
SIC codes cannot see). It is derived on read from the master's stored `sector` (EDGAR `sicDescription`) by the
loader and resolver in `__init__.py`; nothing computed is stored. Display identity only: never a fact, never a call
input, and deterministic, never model-sourced (invariant 3). The classification lives in the data files below; edit
them by pull request.

| File | Columns | What it decides |
| --- | --- | --- |
| `sic_map.csv` | `sic_description`, `business_type`, `royalty` | SIC description to leaf, the main map |
| `supersectors.csv` | `business_type`, `supersector` | leaf to super-sector; must cover every leaf |
| `royalty_patterns.txt` | one regex per line | the overlay's company-name patterns |
| `overrides.csv` | `kind`, `key`, `business_type`, `note` | exceptions: one ticker (for example ERII) or a whole SIC string |
| `runway_not_meaningful.csv` | `kind`, `key`, `note` | where a runway (cash divided by burn) means nothing; `kind` is `sic` (every company with that SIC description) or `ticker` (one company); `note` is for people. Every ETF is covered by its kind without a row. Read only by `runway_applies` ([PLATFORM_VALUES](../../../docs/PLATFORM_VALUES.md), "Runway does not apply") |

Precedence: the stored per-security re-tag > an `overrides.csv` ticker row > an `overrides.csv` sic row >
`sic_map.csv` > `other` (a sector present but unmapped, kept visible) > unclassified (no sector on file). The runway
list stands apart from this order: it keys on the SIC string or ticker, never the leaf.

## The re-tag and the screens

The maps recommend; the steward decides (invariant 10). The derived leaf shows at once, marked "from SIC". The
steward can re-tag one security on the Workbench rail (`POST /workbench/securities/{id}/business-type`,
`require_super_admin`; `tests/app/test_auth_gate.py`). The re-tag is master-level and stored in
`security_master.business_type` (migration 0033) only when it differs from the derived leaf; `null` clears it. A
standing re-tag reads "your tag · revert", its visible inverse (WB#1). The read rides `ScoredMemberOut`
(`business_type`, `business_supersector`, `royalty`, `business_type_override`, `instrument_kind`). A fund is
`instrument_kind='etf'`, not a leaf.

## Editing notes

- SIC strings match whitespace-collapsed and case-insensitive. The strings in `sic_map.csv` are EDGAR's own,
  including the SEC's typos ("Transmisison", "Opeators", "of fices"); leave them as said.
- Leaf and super-sector values must be members of `BusinessType` and `BusinessSupersector` in
  `backend/domain/enums.py`. A new leaf needs the enum member and a `supersectors.csv` row. No migration: nothing
  here is stored.
- Every file is validated at import: a changed header, an unknown value, a bad `kind`, an empty or duplicate key,
  or a leaf missing from `supersectors.csv` fails when the backend starts, never as a quiet misclassification.
- `runway_not_meaningful.csv` leaves out "Finance Services" (crypto miners and treasury companies) and "Insurance
  Agents, Brokers & Service" on purpose: their runway is real
  (`test_the_operator_decisions_stay_out_of_the_runway_list`).
- `test_every_runway_sic_entry_is_a_live_sic_string` checks every `sic` row is a live string and pins the count of
  `sic` rows at 21: adding or removing one means updating that assertion.

## Checking an edit

From `backend/` with this checkout's venv:

```bash
python -m pytest tests/securities/test_business_type.py
```

The suite includes the exhaustiveness check: every live SIC string in
`tests/securities/fixtures/live_sic_strings.json` must still map. A new SIC description from EDGAR lands in `other`
(visible, never dropped) until it is mapped.

## Refreshing the live corpus

The operator runs this read-only against prod's database (agents never connect to a database outside pytest):

```sql
SELECT DISTINCT sector FROM security_master WHERE sector IS NOT NULL ORDER BY sector;
```

Then update `tests/securities/fixtures/live_sic_strings.json` and map any new strings here.
