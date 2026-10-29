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

Numbered by the build (2026-10-29). Items 1 to 5 are plan details that did not match the code, fixed sensibly; the
rest are choices the plan left open.

1. **Business type comes from `master.identity_for`,** not `resolve_business_type` + `get_many` by hand. It is the same
   `resolve_business_type` call over the stored sector, name and ticker with the operator's re-tag winning, in one query
   (the Leaders read), and it returns the company name the readout shows. The node shows the leaf's short label
   (`businessTypeLabel`); the royalty overlay is not drawn.
2. **"Matched terms" reads `BasketMember.surfaced_terms`** (the frozen provenance of how the name entered the card).
   The `matched_terms` the Workbench shows is ephemeral run state, not on the roster. The readout keeps the plan's
   words, "Matched terms".
3. **`memberships_of` does not carry the description, its author or the terms,** so the card's basket is paired with
   its memberships row for row (`roster_rows`, a strict zip: a length mismatch raises). `baskets.py` is untouched.
4. **The 50-day side is the sign of `sma_position`'s `pct_vs_fast`** (two decimals). A third state, "at", is drawn as a
   flat bar when it rounds to zero. No reading (fewer than 50 bars, no price) draws no mark.
5. **A company in two links has two nodes, each with ITS row's description and author,** because the draft writes one
   `basket_member` row per link. The company-level facts (role, glyphs, every link it sits in) are the same on both.
6. **A role-tagged company with no row in a real link is on a rail, whatever its other rows are.** That covers a silent
   (Context/Bench) name and, rarely, a name whose only rows are an old link or No link; the old-link and No-link columns
   then omit it (it is drawn once). A role-tagged company that sits in a real link stays in its column, never on a rail,
   and never in the Context or Bench column (those hold the names with no role).
7. **A real link is always a column, even with nobody in it.** An old-link, No-link, Context or Bench column exists only
   when somebody sits in it, and the toggle renders only when a Context or Bench name has no role (a control that cannot
   discriminate does not render).
8. **Rail order:** left rail payer, then financier; right rail direct read, counter-case, participant, benchmark (the
   plan's order); inside a role, roster order. A rail with no names has no zone, no bracket and no edge.
9. **The last-filing glyph is the newest `FilingRow` of ANY kind** in the year, as the plan says: that includes a split,
   a dilution fact, a ratified catalyst (dated by its event) and an operator-ratified fact (dated by the ratification).
   If you want it to mean SEC forms only, restrict it to the 8-K, 13D/G, Form 4, 4/A and revenue kinds. Two facts on the
   newest day: the first by kind order, form, headline, accession.
10. **A flagged price history leaves out the 50-day side only.** The name stays on the map, drawn gray (45% opacity) with
    its reason in the tooltip, the readout and the shared note; its bars are never read; its business type and its filing
    age still show (they are not price readings).
11. **An unresolved row** is a node with a dashed outline, its ticker and "no resolved security", no glyph at all (not
    even a dash). It is keyed by its upper-cased ticker, so two rows of one ticker are one company.
12. **One edge per rail node,** landing at evenly spaced points down the bracket in the node order (so the curves never
    cross), cubic curves, a small dot at the landing, the counter-case dashed. The role label sits once per role per rail
    at the middle of its middle edge, with a halo in the page color. The edge draws no weight and no arrowhead.
13. **Wide layout:** three SVG slices of one frame (left rail, chain, right rail) side by side; the chain slice scrolls
    sideways and the rails stay put. The two brackets belong to the pinned rail slices, at the chain's edges, as tall as
    the chain's tallest column. Node 136 x 44, column 148 wide, 8 apart (all fixed, nothing sized by data).
14. **Narrow layout (a phone, `max-width: 640px`):** the same three slices stacked: the left rail above the chain, the
    right rail below it, each rail a vertical list with its bracket as a horizontal line (under the upper rail, over the
    lower). Edges are curves from the node's right side to that line, nested so they never cross. Edge labels are left
    out there (each rail node carries its role tag), and the chain still scrolls sideways.
15. **Node text:** ticker (clipped to 9 characters), the role tag (only when set), the business type (clipped to 15),
    a triangle up or down or a bar for the 50-day side, and "Nd" for the filing age ("—" when none stored). Column
    header: the label (clipped to 22), a tag line (the descriptor; "not a link today"; "rows with no link"; "no role · not
    in the call"), and the name count. Full text rides in a tooltip.
16. **Keyboard:** one tab stop (the first name; it follows the focus). Up and down walk one column or rail; left and right
    step to the previous or next group (left rail, the columns, right rail), landing on the same row or the group's last
    name (the row is not remembered across several steps). Enter or Space opens the Cockpit on the name; hover and focus
    drive the same readout.
17. **Click opens the Cockpit on the name's ticker on this card** (`onSelectThesis(thesisId, ticker)`), rail names
    included; the card's name opens the Cockpit on the card.
18. **The readout** shows the ticker, name, every link the company sits in, its role with the approved description, the
    business type, the 50-day side with its percent, the last filing line (form, date, age, headline), the description
    with "model draft" or "your words" (`operator_set`, retired for basket members, reads "model draft") and the matched
    terms. It is a steady-height box above the picture so hovering never moves the picture.
19. **The whole page is cached as one read** per (card, clock, as-of); the Context/Bench toggle is client side, so the wire
    has no parameter for it. The 404 reads the cached picker list first, as the Chain Wave and the Filing Stream do.
20. **The wire adds `filing_days`** (365) and `clock`; `authored_by` uses the existing `Authorship` enum and `role` the
    existing `ContextRole`. The counter-case edge's dashing is the page's (its role), not a wire field.
21. **The folder is self-contained:** `fmtDay`, `plural` and the picker filter are small copies, not imports from another
    dashboard, so cutting any dashboard stays a folder delete. The only shared pieces are the ones the rules name
    (`baskets`, `filings`, `cache`, `FlaggedNote`, the Workbench's role words, `businessTypeLabel`).
22. **The registry test** gained this slug and order 140 only. The Read-Throughs table (order 130) is the sibling build's
    line; after the two merge the pinned list reads both.
23. **Not measured:** the compute time and payload of a big card on real data (this build ran on hand-built rosters and
    the test database). The year of filing facts for a 150-name card is the heaviest read (the Filing Stream's 365-day
    case); the cache holds the finished page once per card and as-of. Check it on dev with a prod copy before relying on it.
24. **Not verified in the live app:** the picture was checked by rendering the component's markup (from the test
    renderer) with the real stylesheet in the Browser pane at a desktop and a phone width, on hand-built payloads. It has
    not been seen on a live card in the running app.
