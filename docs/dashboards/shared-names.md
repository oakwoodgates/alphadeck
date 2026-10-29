# Shared Names

> Slug `shared-names` · status `experimental` · a **"now" view** (`asofAware: false`). The operator signed off
> this plan before the build. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Shared Names. **Description:** "Companies that sit on more than one card, and which cards share them."

## What it shows

It reads **today's baskets on the live cards**; archived cards are excluded (`thesis_repo.list_all`'s
default). Display only: it never feeds a call, never writes to the database and never calls the LLM.

### 1. Companies on 2+ cards (the main table)

- One row per company, matched by `security_id`.
- Columns: ticker · company name (the security master's name; "—" when the master has none) · the number of
  cards · one chip per card it sits on.
- A chip shows the card name, the company's link(s) on that card (`segment`; a row in no link reads
  "Unsegmented", the Cockpit's word) and its context role if set. Clicking a chip opens that card's Cockpit
  with the company selected (`onSelectThesis(thesisId, ticker)`, the ticker that card carries).
- A Context or Bench membership (`domain.thesis.is_silent(member)`) gets a quiet lowercase "context" or
  "bench" tag on its chip, and the chip is drawn quieter (dashed), like its Cockpit row.
- A company with several rows on one card (several links) counts **once** for that card, its links listed
  together on the one chip.
- Default sort: most cards first, then ticker. The count column's header reads "On the most cards". All
  wording is neutral, with no verdicts.

### 2. "Opposite sides" marker

- Applies to a company that **counts** on one card (any non-silent membership there) and is tagged
  `context_role == "counter_case"` on a **different** card.
- Shows as a quiet "opposite sides" mark on the row; its hover names the cards on each side ("Counts on: … ·
  Counter-case on: …").
- A filter control ("Opposite sides only · N") shows only these rows. It renders only when at least one row
  qualifies (honest loudness, WB#3), and a second click turns it off.

### 3. Card pairs (a second section)

- Every pair of cards sharing at least one company: card A · card B · shared count · the shared tickers.
- Default sort: most shared first, then card names.
- Clicking a pair narrows the main table to those companies, with a visible "Shared by A and B · clear" bar;
  a second click on the active pair also clears it. The pair filter and the opposite-sides filter combine.

### 4. "Counting names only" toggle

- Off by default. When on, Context/Bench memberships are dropped **before both sections are computed**.
- It is a query param on the endpoint (`GET /dashboards/shared-names?counting_only=true`), so the rule lives
  in one place, server-side.
- A counter-case tag on a Context/Bench row drops with its row, so with the toggle on, "opposite sides" fires
  only for a counter-case tag on a membership that counts.

### 5. The quiet line that shows the work

For example: "77 live cards · 214 companies on 2+ cards · matched by security · Context and Bench included ·
6 basket rows without a resolved security not matched." Every number is a real count from the payload. With
the toggle on, the Context/Bench part reads "Context and Bench left out (N basket rows)". Basket rows with
`security_id is None` are counted there, never silently dropped (rule 5 / invariant #9).

## The plan's choices (rule 4)

- **Loudness:** quiet throughout. The only marks are lowercase tags in the muted grays; no row is
  highlighted and nothing notifies.
- **Which names count:** Context and Bench are included by default and tagged; the toggle leaves them out.
- **How groups combine:** a company is one row; a card is one chip per company, however many links it holds
  there; a pair counts each shared company once.
- **Thin data:** a company with no master name keeps its row (name "—"); a basket row with no resolved
  security is counted on the quiet line, never matched by ticker.
- **Cache:** none. One read of the live cards and the master names per request (the Board's own scale).

## Where it lives

- Compute (pure): `backend/dashboards/shared_names.py`, `shared_names(theses, names, counting_only=...)`.
- Router: `backend/app/routers/dashboards/shared_names.py`, `GET /dashboards/shared-names`, its wire models
  in the same module.
- Tests: `backend/tests/dashboards/test_shared_names.py`;
  `frontend/src/dashboards/shared-names/__tests__/`.
- Frontend: `frontend/src/dashboards/shared-names/` (`SharedNames.tsx`, `useSharedNames.ts`,
  `shared-names.css`).

## Known limits

- **Matching is by security**, so two share classes of one company count as two companies.
- **"Now" only.** A past-date view would need the basket roster snapshots (point-in-time only from
  2026-09-15), a later step. Because it is a "now" view, the checklist's past-as-of test does not apply: the
  endpoint takes no as-of.

## Out of scope

Correlation between cards, a graph view, card family trees.
