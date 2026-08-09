# Business-type taxonomy — edit these files to change the classification

WHAT a company DOES, two levels: a **leaf** (`miner`, `utilities`, `biotech_pharma`, …) and its
**super-sector** (`materials`, `energy_utilities`, `healthcare`, …), plus the **royalty/streaming
overlay**. Derived on read from the master's stored `sector` (EDGAR `sicDescription`) — MONITOR
display identity, never a call input. The Python in `__init__.py` is only the loader + resolver;
**the classification itself lives in these data files** and is yours to edit:

| File | What it decides |
| --- | --- |
| `sic_map.csv` | SIC description → leaf (the main map; grouped by leaf, royalty-by-SIC flag) |
| `supersectors.csv` | leaf → super-sector (must cover EVERY leaf) |
| `royalty_patterns.txt` | the overlay's company-name regexes |
| `overrides.csv` | your exceptions: one ticker (e.g. ERII) or a whole SIC string, without touching the main map |

Precedence: per-security DB re-tag (the Workbench UI) > `overrides.csv` ticker row > `overrides.csv`
sic row > `sic_map.csv` > `other` (sector present but unmapped — stays visible) > unclassified (no
sector on file).

Editing notes:

- SIC strings match **whitespace-collapsed + case-insensitive**, so reflowing spacing/case is safe.
  The strings in `sic_map.csv` are EDGAR's own, verbatim — including the SEC's typos
  ("Transmisison", "Opeators", "of fices"); leave those as-said.
- Leaf/super VALUES must be members of `BusinessType` / `BusinessSupersector` in
  `backend/domain/enums.py` (the type contract). Adding a brand-new leaf = add the enum member,
  map it in `supersectors.csv`, then use it here. No migration — nothing here is stored.
- Every file is validated at import: a typo'd value, duplicate key, or a leaf missing from
  `supersectors.csv` fails loudly the moment the backend starts (never a quiet misclassification).

Checking an edit:

```powershell
# from backend\ with the venv active — the taxonomy suite includes the exhaustiveness check
# (every live SIC string in tests/securities/fixtures/live_sic_strings.json must still map):
pytest tests/securities/test_business_type.py
```

If EDGAR grows a NEW SIC description it lands in `other` (visible, never dropped) until you map it.
To refresh the pinned live corpus behind the exhaustiveness test:

```sql
SELECT DISTINCT sector FROM security_master WHERE sector IS NOT NULL ORDER BY sector;
```

→ update `backend/tests/securities/fixtures/live_sic_strings.json`, then map any new strings here.
