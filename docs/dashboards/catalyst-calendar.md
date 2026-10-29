# Catalyst Calendar

> Slug `catalyst-calendar` · status `experimental` · a **"now" view** (`asofAware: false`). The operator
> signed off this plan before the build. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Catalyst Calendar. **Description:** "Every dated catalyst and card deadline across your cards,
with passed ones to prune and undated ones to date."

## What it shows

"What should I look at now": a calendar of the operator's own dates across every live card. It reads only
each card's catalyst list and each card's latest recorded call. Display only: it never feeds a call, never
writes to the database and never calls the LLM.

`today` = `domain.market_time.market_today()`, passed into the compute as a parameter, never an ambient
clock.

### Sources

- **Live cards:** `repositories.thesis_repo.list_all(conn)` (archived cards are left out by its default).
  Each `Thesis.catalysts` entry is a `domain.thesis.Catalyst`: `id`, `label`, `kind`, `when_date`,
  `when_label`.
- **Each card's state and deadlines:** from its LATEST `calls` row, one read:
  `SELECT DISTINCT ON (thesis_id) thesis_id, state, (card->>'arm_until')::date AS arm_until,
  (card->>'exit_by')::date AS exit_by FROM calls WHERE thesis_id = ANY(%s) ORDER BY thesis_id, asof DESC,
  seq DESC`. A card with no recorded call has state "—" and no deadlines; it is still listed.

### Sections, top to bottom

1. **Upcoming.**
   - Dated catalysts with `when_date >= today`, plus each card's two deadlines as their own items:
     "entry window ends" (`arm_until`) and "exit-by" (`exit_by`), when on or after today. A deadline
     carries a small "deadline" tag where a catalyst shows its kind.
   - Grouped into buckets. A week starts on Monday.
     - **This week:** today through the coming Sunday (today alone when today is a Sunday).
     - **Next week:** the Monday after through its Sunday.
     - **Rest of the next 30 days:** after next week, through today + 30.
     - **31–90 days:** today + 31 through today + 90.
     - **Later:** after today + 90.
   - Each line: date, days away, label, kind, the card name, and that card's current state chip in the
     Board's state colors (`--incub`, `--warm`, `--armed`, `--manage`).
   - Chronological within a bucket; same-day items sorted by card name.
2. **Passed: prune?** (a quiet list)
   - Dated catalysts with `when_date < today`, newest first, each with its card and how many days ago.
   - A quiet line explains: the Board keeps showing a passed catalyst until it's removed in the card's
     editor.
   - Deadlines that passed are NOT listed here; they're the call's own clocks, not authored catalysts.
3. **Undated.**
   - Per card: the count of catalysts with no `when_date`, expandable to their labels and `when_label`
     (for example "~Q3").
   - Sorted by count, most first.

### Controls

- Filters by catalyst `kind` (chips built from the kinds present) and by card state.
- A text filter on card name or label.
- A toggle "Show card deadlines" (on by default) that hides the entry-window and exit-by items.

### Clicks

Any line calls `onSelectThesis(thesisId)` and opens that card's Cockpit.

### The quiet "show the work" line

Live cards, catalysts (dated / undated / passed), deadlines shown, and today's date.

## The plan's choices (rule 4)

- **Loudness:** quiet throughout. Dense one-line rows like What Changed, in the muted grays; the only color
  is each line's state chip, in the Board's own state colors. The passed list is quieter still. Nothing
  notifies.
- **Which names count:** none; this view lists catalysts and deadlines, which belong to a card, not a name.
  Every live card counts, whatever its state.
- **How groups combine:** none; each line is one catalyst or one deadline on one card.
- **Thin data:** a card with no recorded call keeps its catalysts on the page with state "—"; a card with no
  catalysts is counted on the quiet line, never dropped silently.
- **Cache:** none. A request is the live-card read plus one `DISTINCT ON` read of the `calls` log.

## Where it lives

- Compute (pure): `backend/dashboards/catalyst_calendar.py`: `catalyst_calendar(...)` holds the bucket rule
  (the week boundary included), the passed list and the undated groups.
- Router: `backend/app/routers/dashboards/catalyst_calendar.py`, `GET /dashboards/catalyst-calendar`, its
  read and wire models in the same module.
- Tests: `backend/tests/dashboards/test_catalyst_calendar.py`;
  `frontend/src/dashboards/catalyst-calendar/__tests__/`.
- Frontend: `frontend/src/dashboards/catalyst-calendar/` (`CatalystCalendar.tsx`, `useCatalystCalendar.ts`,
  `calendar.ts` for the filters, `catalyst-calendar.css`).

## Known limits

- **"Now" only.** Catalysts are operator-authored and edited in place, with no history, so there is no past
  view. Because it is a "now" view, the checklist's past-as-of test does not apply: the endpoint takes no
  as-of.
- **A fuzzy `when_label` is never turned into a date.** An undated catalyst stays undated.

## Out of scope

- Editing or deleting a catalyst from here. Dashboards can't write; pruning happens in the card's editor.
- Turning `when_label` text into dates.
