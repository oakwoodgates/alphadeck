# Chain Map

> Slug `chain-map` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 140`.
> The operator approved this dashboard and the choices below (2026-10-29); the choices the build makes that this
> plan does not spell out go under "Choices to confirm" for the operator's review. The rules every dashboard
> follows: `docs/DASHBOARDS.md`. The third page of the RESEARCH set; it reads the Filing Stream's shared reader
> (`backend/dashboards/filings.py`) for one glyph, so it is built after it.

**Label:** Chain Map. **Description:** "One card as a picture: its links in chain order, the names inside each, and
the payers, counter-cases and direct reads drawn beside the chain by role."

The Cockpit shows a card as a table and the Chain Wave as a grid of weeks. The Chain Map shows the same card as a
picture: who sits where in the chain, how big each link is, and which context names stand beside it and in what part.
It is the analyst's orientation page for a thesis. Display only: it never feeds a call, never writes to the database
and never calls the LLM. At a past as-of it draws the roster as of that night.

## The picture

- **Columns = the card's real links in chain order** (`thesis.segments` as loaded, today's chain, Discovered wherever it
  sits), left to right. Each column has the link's label, its descriptor when set, and "n names".
- **Nodes = the companies on the roster at the as-of**, each once per link (by `security_id`), stacked in the column in
  roster order. A node shows: the ticker, and three quiet glyphs:
  - its **business type** leaf as a short label (derived on read from the master, the operator's re-tag winning;
    blank when unknown);
  - its **50-day side**: above or below the 50-day average, from the `sma_position` display member (a small up or
    down mark; none when unpriced or too few bars);
  - its **last filing age** in days, from `filings.read_filing_rows(..., days=365)` (the newest `FilingRow`'s date;
    "no filing stored in the last year" when none).
  A company whose data flags reach its price readings is drawn grayed with its reason on hover and in the shared
  `FlaggedNote`; it stays on the map. A roster row whose link is not on the card today sits in an "old link" column
  after the real links, labeled "not a link today"; rows with no link in a "No link" column after those. Never
  dropped (invariant #9).
- **Rails = the role-tagged companies**, Context and Bench included (that is where most roles live). Two rails:
  - the **left rail**, "what pays and funds": `payer`, then `financier`;
  - the **right rail**, "what reads across": `direct_read`, `counter_case`, `participant`, `benchmark`.
  A rail node carries the same glyphs as a chain node plus its role tag (`contextRoleTag`) and, on hover, the role's
  operator-approved description. A company with a role that ALSO sits in a real link is drawn in its link column with
  the role tag and no rail copy (one node per company).
- **Edges = the roles.** A rail node draws one edge to the chain's bracket (the whole chain, not a link): a role names a
  part, not a target (`docs/TRIAGE.md`), so the edge lands on the chain as a whole. Payer and financier edges come
  in from the left; direct-read, participant and benchmark edges come in from the right; a counter-case edge is
  dashed. Edges are thin, gray, no arrowheads beyond a small end mark; the role is the label.
- **Context and Bench names with no role** sit in a quiet "Context" and "Bench" column at the far right, behind a
  toggle ("Show Context and Bench without a role", off by default): they are on the card but say nothing about the
  chain until a role is set.

Hover or focus on any node shows a readout above the picture: the company's name, its link, its role, its thesis-fit
prose (`BasketMember.thesis_fit`, the drafted or operator-edited description, with its authorship label), its matched
terms, its last filing line, and its 50-day side. Click opens the Cockpit name panel (`onSelectThesis(thesisId,
ticker)`). The picture is one tab stop; the arrow keys move the focus across nodes.

## Loudness

No colors by link or role; no size by anything. Every node the same size, every column the same width. The only
visual weight is the count of nodes in a column. Neutral labels throughout (rule 3).

## Which names count

Every roster row at the as-of: the real links' names on the chain, the role-tagged silent names on the rails, and the
untagged silent names behind the toggle. Unresolved basket rows (no `security_id`) are drawn in their column as a
plain ticker with no glyphs ("no resolved security") and counted in the work line.

## Picking the card

The shared picker (`baskets.picker_cards`, lifted by the Filing Stream): the cards counted at the as-of, A to Z, each
with its link count; the first by default; the card's name calls `onSelectThesis(thesisId)`.

## The quiet "show the work" line

The card and the as-of; the clock; links / roster rows / resolved / unresolved / silent; roles set, by role; names with
a 50-day side / without; names with a filing in the last year; the flagged names; the cache state; the roster note when
today's roster stands in ("rosters are point-in-time only from Sep 15, 2026").

## Cache

`dashboards.cache.cached(conn, "chain-map", (str(thesis_id), clock), asof, ...)` holds one card's nodes with their
glyphs (the 50-day read through `per_company` + `display_members(["sma_position"])`, the last-filing read, the price
flags, the business types). The toggle is client side. The picker list is cached as `("cards", clock)`.

## Backend and frontend

- `backend/dashboards/chain_map.py`, pure: the columns in chain order (real links, old links, No link), the node
  placement (each company once; a role-tagged company in a real link stays in its column), the two rails and their
  order, the edges, the glyph assembly from the display and filing reads, the counts.
- `backend/app/routers/dashboards/chain_map.py`: `GET /dashboards/chain-map/cards?asof=` (the shared picker) and
  `GET /dashboards/chain-map/{thesis_id}?asof=` (the columns, rails, edges and counts; 404 for a card not counted at
  the as-of). `known_at, clock = serve_view(asof)` threads into every read. Wire models in this module (rule 8).
- `backend/tests/dashboards/test_chain_map.py`: the placement rules (each company once; the role-tagged counting
  name stays in its link; two roles never happen on one card, but a silent row with a role is a rail node; the old
  link and No link columns; the toggle's untagged silent names), the rail order, the edge per rail node, the glyphs
  (a flagged name grayed and named; an unpriced name without a side; the last-filing age), the counts; the router off
  one cached read with its 404; and, DB-backed, **a roster row added after the as-of is not on the map** (two upserts,
  the view at a `known_at` between them: `thesis_repo.get_asof`).
- `frontend/src/dashboards/chain-map/`: `entry.ts` (slug `chain-map`, order 140, `asofAware: true`), `ChainMap.tsx`
  (an inline SVG laid out by a pure helper, `mapLayout.ts`: column x positions, node y positions, rail positions,
  edge paths as cubic curves to the chain's bracket; no graph library, no new dependency), `useChainMap.ts`,
  `chain-map.css`, tests (the layout helper on hand-built payloads, the toggle, the readout, keyboard focus).
- **Layout:** the research set's document-like look. The SVG scrolls sideways inside the page when the chain is wide;
  the rails stay in view (the left rail pinned left, the right rail pinned right, the chain scrolling between them);
  readable on a phone with the rails stacked above and below the chain.

## Known limits (stated on the page too)

- **A role names a part, not a target:** edges land on the chain, never on a link or a name. Role targets are a later
  spine change.
- **The roster is the card's roster as of the as-of; links, descriptors, business types and thesis-fit prose are
  today's** (only the roster is snapshotted).
- **The 50-day side and the last-filing age are display context, never a call input** (`docs/DISPLAY_SIGNALS.md`).
- **The picture is a layout, not a model:** no edge weights, no clustering, no inferred relationships. Every edge is an
  operator-set role.

## Out of scope

Inferred relationships (supplier, customer, competitor from filings); edge weights; any score; the read-through panel;
card lineage; any Cockpit change; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review: the two-rail split (payer and financier left; direct read, counter-case,
participant and benchmark right), the dashed counter-case edge, equal node sizes, and the untagged Context and Bench
names behind a toggle.
