# What Changed

> Slug `what-changed` · status `experimental` · a **"now" view** (`asofAware: false`) with its own "since"
> selector. The operator signed off this plan before the build. The rules every dashboard follows:
> `docs/DASHBOARDS.md`.

**Label:** What Changed. **Description:** "What moved on your cards since last night: states, keys, names,
triggers and clocks."

## What it shows

"What should I look at now": a night-over-night diff of every live card's **recorded call** (the `calls`
log, which the nightly appends to only when a card changes). It reads only that small log, the live cards
(`thesis_repo.list_all`; archived cards are left out) and the master's tickers. Display only: it never feeds
a call, never writes to the database and never calls the LLM.

### Which two cards are compared, per live card

- **The now card:** the recorded card in effect at the **newest night** in the log. T = the max `asof` in
  `calls` across the live cards.
- **The baseline card:** the card in effect at the baseline date.
  - "In effect at D" = the row with the greatest `(asof, seq)` where `asof <= D`. The log only appends on
    change, so the last row on or before D is what stood that night.
  - A night with several rows uses the highest `seq`.
- **The "since" selector:**
  - **Last night** (default): the baseline is the previous recorded night, the greatest `asof < T` across
    the log.
  - **Last 7 days:** the baseline is T − 7 calendar days.
  - **Since a date:** a date picker.
- **Special cases:**
  - A card with no baseline row (created inside the window) shows as one "New card" line.
  - A card whose now row is reconstructed (`reconstructed = true`) carries a quiet "reconstructed" tag.
  - Archived cards are left out (start from `repositories.thesis_repo.list_all`).

### What counts as a change

Comparing the two `CallCard`s (`calls.card`, parsed with `domain.call.CallCard.model_validate`):

1. **Column moves:** `state` changed, both directions, e.g. "Warming → Armed", "Armed → Warming".
2. **Keys turned:** `key_conviction.turned` or `key_confirmation.turned` flipped. Shows the key's own
   `label`, both on and off.
3. **Names:**
   - On `armed_members`: a member newly armed or no longer armed.
   - On `watch_members`: a new member on watch.
   - Members are matched by `security_id`.
4. **Triggers:** in `triggers_fired`, newly fired or no longer firing. In `risk_signals`, new ones.
   - Identity = `(kind, security_id, event_date, label)`.
   - Shows the label, the kind and the ticker.
   - Wording is neutral: "no longer firing", never "expired" or "failed".
5. **Clocks (on the now card only, as of T):**
   - `arm_until` within the next 3 calendar days ("entry window closes in N days")
   - `exit_by` on or before T and inside the window ("exit-by reached")

   Calendar days, matching the Decision Queue's "act within Nd".

- Tickers come from `securities.master.tickers_for(conn, ids)`.
- Every item is ONE line. Clicking it calls `onSelectThesis(thesisId, ticker)`, or `onSelectThesis(thesisId)`
  for a card-level item.

### Layout

- **Group by kind (default):** column moves, keys turned, names, triggers, clocks, new cards, each a section
  with its count. A toggle switches to **group by card**: each card's changes together, cards sorted by name.
- **A quiet night:** "No changes since <baseline date>." A calm page means nothing moved.
- **The quiet "show the work" line:** the two nights compared ("Oct 27 → Oct 28"), the live cards, the cards
  with a change, and the rule "the log records only on change, so a card with no new row is unchanged".

## The plan's choices (rule 4)

- **Loudness:** quiet throughout. Dense one-line items in the muted grays, like the nightly's
  `TRANSITIONS:` block; the only color is a state word, in its Board column's own color. Nothing notifies.
- **Which names count:** every name the recorded card itself lists (armed, watch, triggers, risk signals).
  The dashboard re-judges nothing: Context and Bench names never vote, so they never reach those lists.
- **How groups combine:** none; each line is one change on one card.
- **Thin data:** a name with no master ticker keeps its line (ticker "—", opened by its `security_id`); a
  live card with no recorded call yet is counted on the quiet line, never dropped.
- **Cache:** none. The `calls` log is small (994 rows on prod); a request is four SELECTs (the span, the
  previous night, and one `DISTINCT ON` read per night) plus one ticker read.

## Where it lives

- Compute (pure): `backend/dashboards/what_changed.py`: `what_changed(...)` holds every diff rule and
  `baseline_date(...)` the "since" rule.
- Router: `backend/app/routers/dashboards/what_changed.py`, `GET /dashboards/what-changed?since=`
  (`last_night` | `7d` | `YYYY-MM-DD`), its reads and wire models in the same module.
- Tests: `backend/tests/dashboards/test_what_changed.py`; `frontend/src/dashboards/what-changed/__tests__/`.
- Frontend: `frontend/src/dashboards/what-changed/` (`WhatChanged.tsx`, `useWhatChanged.ts`,
  `what-changed.css`).

## Known limits

- **"Since you last looked" isn't possible:** there is no login, no app state in the browser, and dashboards
  can't write. So "since last night" is the default.
- **A night the nightly didn't run looks the same as a quiet night.**
- **It compares two nights, not the path between them** (an addition at build time). Over "Last 7 days" a
  card that went Warming → Armed → Warming shows no column move.
- **"Now" only.** Because it is a "now" view, the checklist's past-as-of test does not apply: the endpoint
  takes no as-of. The baseline reads are cut at the baseline night (`asof <= D`), so a later row never
  reaches the baseline card.

## Later (out of scope now)

Price events (new crosses, breadth surges, cross clusters), which come after the Rotation Map's price read
lands.
