# Idea Timeline

> Slug `idea-timeline` · status `experimental` · a **"now" view** (`asofAware: false`): the time axis IS the
> history. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Idea Timeline. **Description:** "Each card's recorded state over time, with its catalysts and your
logged decisions."

It answers "where are my ideas in playing out": each live card's **recorded** state (the `calls` log, the
original record only) drawn over time on one shared date axis. Display only: it never feeds a call, never
writes to the database and never calls the LLM.

## What it shows

**One row per live card.** Archived cards are left out (`thesis_repo.list_all`'s default). A card with no
recorded call yet keeps its row (no segments, "no call yet"), never dropped.

### Left side (columns)

- **Card:** the card name; clicking it opens that card's Cockpit (`onSelectThesis(thesisId)`).
- **State:** the current state chip (the latest recorded night's state), in the Board's state colors.
- **Days in state:** calendar days from the current run's first night to today.
- **Days armed:** total calendar days in armed runs (the current run counted to today).
- **Times armed:** entries into armed = the number of armed runs (a card whose first recorded night is armed
  counts that as an entry).
- **First recorded:** the card's first recorded night.

### Right side (the timeline)

- One horizontal bar per card on a shared date axis with month ticks and a visible today line.
- **The axis** runs from the earliest recorded night (across the live cards) to today, then on to the latest
  future marker it shows, capped at **today + 90 days**. A marker past the cap never stretches the axis; it is
  counted on its row ("+N later"). The future part is lightly shaded.
- **Colored segments**, one per run of the same recorded state, in the Board's state colors (the
  `--incub` / `--warm` / `--armed` / `--manage` tokens the Board columns use).
- **Segment hover:** the state, the verdict on the run's first night, the from → to dates, the days, the
  nights recorded, and the notes below (several rows on a night, reconstructed, set aside).

### How a night's state is read (stated on the page in the quiet line)

- **One row per night, plus re-runs on change.** Each completed nightly pass writes a card's first row at a
  new night; a re-run on the same night appends only when the card changed
  (`calls_repo.record_if_changed`, which compares against the row at the SAME night).
- **A state holds** from its night until the card's next recorded night; the current run holds until today.
- **A night with several rows uses the LAST one** (highest `seq`). The segment hover lists those nights with
  their row counts.
- **Reconstructed rows** (`calls.reconstructed = true`, a missed night rebuilt later by `pipeline.backfill`)
  draw **hatched**, hover "reconstructed: reported, never scored".
- **Set aside rows**: a row carrying an ACTIVE `exclude` annotation (`call_annotation`: `action = 'exclude'`
  and no `revoke` row whose `revokes` names it, the rule `call_annotations_repo.ACTIVE_SQL` states) draws
  with a **distinct (opposite-angle) hatch**, hover "set aside on the Scoreboard: <reason>". Under the
  Scoreboard's default policy (`scoreboard_exclude_annotated = true`) those rows are what it sets aside.
- Both flags are read off each night's winning (last) row.

### Markers on each row

- **Catalysts:** every `Thesis.catalysts` entry with a `when_date` is a small tick, muted when past (before
  today), its label on hover. Undated catalysts are counted in a quiet "+N undated" note on the row.
- **Decisions** (`operator_decision`, by `decision_date`): take ▲, close ▼, pass ◇. A row a later `void`
  names stays visible, grayed, its hover saying "voided"; a void row draws nothing of its own. The name acted
  on is read from the card's basket by `security_id`. This table is empty on prod today; the empty case
  renders nothing.
- **The current card's clocks**, from the LATEST calls row per card (`card->>'exit_by'`,
  `card->>'arm_until'`), shown only when set: exit-by as a bracket at its date (the signal-validity horizon,
  not a trade exit), and the entry window as a thin underline from the current run's first night to
  `arm_until`.
- Markers past the visible end of the axis are counted on the row ("+N later"), never silently dropped.

### Controls

- **Zoom:** 30 days / 90 days / all (default all). A preset of N days shows today − N to today + N (the future
  tail also capped by the axis end above); "all" is the full axis. Zoom is a viewport: segments and markers
  before the visible start are clipped.
- **State filter chips** (show only armed, etc.). A chip renders only for a state at least one card holds
  (honest loudness, WB#3); several can be on; none on = every card. Turning a chip off is its inverse.
- **Sort:**
  - current state (armed → warming → incubating → managing → no call yet), then days in that state, longest
    first. **The default.**
  - days in current state (longest first)
  - total days armed (most first)
  - first recorded (earliest first)
  - name (A → Z)

  Ties break on the card name. Header wording is neutral ("Days in state", "Days armed").

### The quiet "show the work" line

Live cards (and how many have no recorded call yet), recorded calls counted, the nights they cover, the date
range, and the reading rules above, in one line, with the counts of nights that hold several rows, are
reconstructed, or are set aside.

## The plan's choices (rule 4)

- **Loudness:** quiet. The only color is the state colors the Board already uses; nothing notifies, nothing is
  highlighted beyond the state fill. Armed is the loudest fill because it is the loudest state (invariant #7).
- **Which names count:** this view reads cards, not names; Context and Bench do not apply.
- **How groups combine:** one row per card; a segment is one run of the same recorded state; days are
  calendar days (`end - start`, with `end` the next run's first night, or today for the current run).
- **Thin data:** a card with no recorded call keeps its row and is counted on the quiet line.
- **Cache:** none. It reads the small `calls` log (about 1,000 rows on prod) without the card blob, plus one
  `DISTINCT ON` read of the latest row's two clocks.

## Where it lives

- Compute (pure): `backend/dashboards/idea_timeline.py`,
  `idea_timeline(theses, calls, annotations, decisions, clocks, today=...)`.
- Router + reads + wire models: `backend/app/routers/dashboards/idea_timeline.py`,
  `GET /dashboards/idea-timeline`. `today` is `domain.market_time.market_today()`, never an ambient clock.
- Tests: `backend/tests/dashboards/test_idea_timeline.py`; `frontend/src/dashboards/idea-timeline/__tests__/`.
- Frontend: `frontend/src/dashboards/idea-timeline/` (`IdeaTimeline.tsx`, `useIdeaTimeline.ts`,
  `timeline.ts`, `idea-timeline.css`).

## Known limits

- **A night with no row** reads as the previous state carried through. Under today's recording rule that
  means no pass recorded the card that night (the nightly did not run, or its recording gate withheld the
  call); this view cannot tell those apart, nor from a night an older rule may have skipped as "no change".
- **"Managing" comes from logged positions;** none are recorded on prod yet, so no managing segment shows.
- **Card lineage** (a split card carrying its original's date) is not built; each card starts at its own
  first recorded call.
- **"Now" only.** The time axis is the history; the endpoint takes no as-of, so the checklist's past-as-of
  test does not apply.

## Out of scope

Clicking a segment to open the Cockpit at that date, edition re-runs (this is the original record only), a
"since you last looked" diff.
