# What Changed

`what-changed` · a "now" view with its own `since` selector · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What moved on each live card between two recorded nights, one line per change, grouped by kind or by card. The
change kinds: a state moved (either direction), a key's `turned` flipped, a member newly armed, no longer armed or
new on watch, a trigger newly fired, no longer firing or re-dated, a new risk signal, a clock coming due
(`arm_until` within `ENTRY_WINDOW_DAYS` of the countdown origin, `exit_by` reached in the window), and a new card.
A quiet night reads "No changes since" the baseline date.

## Who counts

- Live cards of the caller's workspace (`thesis_repo.list_all(workspace=...)`); archived cards are out.
- Only what the recorded card lists: armed and watch members, triggers, risk signals. Context and Bench never vote,
  so they never reach those lists, and nothing is re-judged.
- A live card with no recorded call is counted on the work line, never dropped. A name with no master ticker keeps
  its line. A card whose newest row is reconstructed carries a tag.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/what_changed.py` (`what_changed`, `baseline_date`; pure) |
| Route | `GET /dashboards/what-changed?since=` (`last_night`, `7d` or a date; a date outside the record is 422) |
| Shared readers | none (the `calls` log, `master.tickers_for`) |
| Cache key | none: a few SELECTs on the log |
| Frontend | `frontend/src/dashboards/what-changed/` |
| Tests | `test_what_changed.py`, frontend `__tests__/` |

## As-of behavior

No as-of. The newest night is the latest `asof` in `calls` across the live cards; a card's call in effect at a night
is its row with the greatest `(asof, seq)` on or before it. Clock lines count from `countdown_origin` (the action
day); an entry window already ended is not listed.

## Rules not visible in code

- A trigger's identity is kind, security and event date, never the label. Why: labels carry numbers that move every
  night, so a continuing signal would read as new.
- A fired trigger that only moved its date (exactly one gone and one new for a kind and security) is one
  "re-dated" line. Why: a breakout re-dates on every new high. Risk signals are never re-dated.

## Known limits

- No "since you last looked": dashboards write nothing (rule 1), so no per-user last-visit mark exists.
- A night the nightly did not run reads like a quiet night.
- It compares two nights, not the path between: Warming, Armed, Warming over a week shows no column move.
