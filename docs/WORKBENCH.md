# Workbench

The Workbench is where a user builds a thesis card: create it, draft its companies from the narrative, cut the
draft down, save the basket (which starts loading the new companies' data), arrange the value chain, set purity and
sign off the names they endorse. A plain user works on the cards in their own workspace; a steward has the same
screen plus a few controls that write shared platform data (listed under "Who can do what"). How the draft finds
its names is [DISCOVERY](DISCOVERY.md); how a member is scored is [WORKBENCH_SCORING](WORKBENCH_SCORING.md). Read
this before you change the editor, the scored view, the save route or authorship.

## Code

| Piece | Module |
|---|---|
| The page and the scored view | `frontend/src/workbench/Workbench.tsx`, `ScoredRow.tsx`, `DDRail.tsx` |
| The editor: draft, buckets, prune, save | `frontend/src/workbench/ChainEditor.tsx`, `useChainDraft.ts` |
| Value-chain edits on the scored view | `frontend/src/workbench/chainOps.ts` |
| Junk tells | `frontend/src/workbench/junkTells.ts` |
| Autosaved session | `frontend/src/workbench/triageSession.ts`, `backend/workbench/triage_store.py` |
| Purity panel; shared values | `frontend/src/workbench/FactsPanel.tsx`, `SharedValues.tsx` |
| Save and the member load | `backend/app/routers/workbench.py` (`promote`, `start_member_load`) |
| Fixed links and roles | `backend/domain/thesis.py` (`with_silent_links`, `silent_conflicts`, `fund_conflicts`, `context_role_conflicts`) |
| Exclusions | `backend/app/routers/theses.py` (`put_exclusions`) |

## How it works

### Two screens

The editor (opened with "Edit the basket") owns membership: draft, add, remove and prune, then Save. The scored
view owns the value chain (the links and where each name sits), the silent links, context roles and purity; each
edit there saves at once. Both write through the same full-replace save, `POST /workbench/theses`, and re-read
after the other writes.

### Create and edit

- "+ New thesis" takes a name and a narrative and saves with `id: null`, the save's create branch. The card starts
  Incubating with an empty basket (`Workbench.create.test.tsx`).
- Editing the narrative resends the stored basket and segments: the save is a full replace, so an edit that sent
  empty lists would wipe the chain (`Workbench.edit.test.tsx`). The editor suggests a re-draft and never re-drafts on
  its own.
- The save never carries the term set, catalysts, kill criteria, exclusions or the meta-index switch. Each has its
  own writer, so a save cannot blank them.

### Draft

- The term-set drawer produces, recommends and edits terms ([DISCOVERY](DISCOVERY.md) "The term set").
- "Draft from narrative" (full) and "Quick draft (seeds only)" start the draft job on a click, never on render. Quick
  is disabled, with the reason, while the card has no SIGNAL seeds.
- A draft merges into the editor (`useChainDraft.loadDraft`): new segments append and placements are added, deduped
  by `security_id`. Existing members are never replaced.
- The load mode is a three-way select. Left alone, a quick draft loads in pick mode and a full draft auto-loads; an
  explicit choice wins for both. In pick mode, new PLACED names go to a Recommended pile instead of the basket. The
  mode redirects only that append-new branch, so existing members take the normal merge untouched
  (`ChainEditor.cherrypick.test.tsx`).
- A model never places a name in Context, ETFs or Bench.

| Bucket | Holds | How a name enters the basket |
|---|---|---|
| Placed | PLACED names | already in it (pick mode: from the pile) |
| Recommended (pick mode) | new PLACED names held back | check to add |
| To Review | VERIFY and tail-sweep names | check to add; the row moves up to Placed |
| Couldn't resolve | AMBIGUOUS and ABSENT names | an explicit CIK pick for AMBIGUOUS; ABSENT is never placeable |

There is no "skip": a candidate is added or stays in the queue. A skip that removed the row was a silent loss.

Matched terms, off-thesis flags and the To-Review queues are run state. Reopening a saved card restores the saved
basket, not that context; a re-draft, or a saved run when the run loader is on, brings it back.

### The cheap cut

The first cut costs nothing: the user judges on what each row already shows (name, ticker, identity chips, matched
terms, the off-thesis flag).

- The confidence ladder is Excluded, Included, Signed off; excluded wins. A drafted name starts included
  ([invariant 9](INVARIANTS.md#9-recall-is-sacred)); unchecking excludes it, and an excluded row stays visible,
  grayed, one click from coming back. Save persists the included names only.
- The placed list has two gestures:

| Mode | Start state | Checking a name | What Save writes |
|---|---|---|---|
| Research | every name included | unchecking excludes it (a durable no) | the included names and the exclusion set |
| Pick | every name open and un-picked | picks it into the basket | the picked names; earlier exclusions carried forward, minus names picked now |

  In Pick an un-picked name is available, not rejected, and nothing new is excluded. Switching modes resets working
  state, never to an all-checked list: entering Research returns to the fresh state with stored exclusions grayed,
  because checking everything would withdraw every stored exclusion on the next Save
  (`ChainEditor.placedMode.test.tsx`).
- Bulk actions (Research only): include all, exclude all, clear not signed-off.
- The find bar sorts and filters the list and can compact it. The view never changes what Save persists: Save is the
  whole draft minus excluded names, whatever is filtered out (`ChainEditor.test.tsx`, "the VIEW never changes what
  Save persists"). A name in several links is one row with one chip per link.
- Placed partitions show only when they discriminate: Placed, Placed flagged (off-thesis), and Placed low quality
  (off-thesis and a junk tell from `JUNK_TELLS`), which starts collapsed with an "exclude all" action. The model's
  flag is the recall guard: a junk tell alone never demotes a name. A new tell is one registry entry
  (`junkTells.test.ts`).
- To Review highlights the rare keeper rather than flagging the common junk
  ([invariant 7](INVARIANTS.md#7-inverse-loudness)): keepers (on-thesis, with a ticker) at the top; off-thesis names
  split into "Low signal" (two or more matched terms) and "Lowest signal" (one or none, with zero-term off-universe
  names first); names with no listed ticker in their own drawer, add disabled. Precedence: off-thesis, then no
  ticker, then keeper. An empty drawer does not render.
- Exclusions are durable. Save writes the exclusion set, each with an optional reason, through
  `PUT /theses/{id}/exclusions`, its own writer. The next session or re-draft shows those names grayed. Discovery
  never filters on exclusions: a no is an editor default, never a recall cut. Exclusions key on `security_id`; an
  unresolved name's set-aside stays in the session.

### Save

- Save writes the exclusion set first, then sends the included basket through the save route (see "The save
  contract"); if the exclusion write fails, the basket is not sent.
- The companies a save adds load through `start_member_load`, the same lane a fund or SPAC attach from the Radar
  uses. Cold names (no other live card holds them) queue for the staged onboarding job
  ([INGEST](INGEST.md) "Cold names (onboarding)"); a few, outside the nightly window, take the fast lane as a
  background ingest (`onboard_fast_lane_max`). Past the global ingest cap, or when a plain user's daily allowance of
  new names loaded at once (`price_pull`, one unit per new name) cannot cover them all, they wait for onboarding and
  the nightly. A save that adds nothing starts nothing, and the load never fails the save
  (`tests/app/test_workbench_ingest_kick.py`; `tests/app/test_user_fetches.py`).
- The response's `ingest` or `onboarding` field drives the note under the card (`IngestNote.tsx`,
  `OnboardingNote.tsx`), which polls `GET .../ingest/jobs/{job_id}` or `GET .../onboarding`.
- There is no per-name or per-section "get data" control (`Workbench.noGetData.test.tsx`). Share counts and cash/burn
  are platform values filled by background extraction and the steward ([PLATFORM_VALUES](PLATFORM_VALUES.md));
  prices arrive through the member load, onboarding and the nightly.
- The editor autosaves its whole working state to the server (`PUT /workbench/theses/{id}/triage-session`): one
  mutable file per card, failing loudly so "Not saved" is honest, and writing no basket or fact row
  (`test_session_put_writes_no_spine_rows`). A session older than `STALE_SESSION_DAYS` (`Workbench.tsx`) asks before
  it drives the editor. A save keeps the session; the user's "start over" deletes it. On restore, the saved card's
  context roles win over the session's, even where the card says unset.

### Arranging the value chain

- On the scored view's rail, a multi-select of links moves a company between links; a company in N links is N
  `basket_member` rows sharing a `security_id`. The link editor renames (the new label cascades to members),
  reorders, removes and adds links. Each action saves at once through the pure transforms in `chainOps.ts`, which
  mirror `useChainDraft` and never edit it.
- A name with no real link (cleared, its last link removed, or an orphan label) shows under Discovered, never
  nowhere; the save payload heals orphans to Discovered. A real link is structure only and never changes the call.
- ETF sleeves show funds with their price. `GET /workbench/securities/{id}/etf-holdings` lists a fund's holdings, and
  including one adds it to the basket.

### Context, ETFs and Bench

- Every card ends with three fixed links, Context, ETFs and Bench, in that order. They cannot be renamed, deleted or
  reordered, and no other link may take their names. A name there is on the card but never counts toward the call
  ([CALL_LOGIC](CALL_LOGIC.md) section 1). The labels and descriptors live in `useChainDraft.ts` and `domain/thesis.py`.
- A company is in a silent link or in the chain, never both, and holds one row there; moving it into a silent link
  collapses its rows.
- ETFs holds funds (`instrument_kind = 'etf'`) and only funds, and every fund goes there. Why: a fund's own breakout
  once voted from the Discovered floor and armed a card. Adding a fund files it in ETFs; a company is never offered
  ETFs.
- Only a person places a name in a silent link. The checkbox is the way back (WB#1): unchecking Context sends the name
  to Discovered, not to its earlier links.
- Never call any of them "watch": `CallCard.watch_members` means something else.
- They hide when empty except in the editors and stay quiet. The Board side is [BOARD](BOARD.md).

### Context roles

- A context role says what part a company plays on this card. The six values are `ContextRole` in
  `domain/enums.py` (`payer`, `counter_case`, `direct_read`, `participant`, `benchmark`, `financier`); labels and
  hovers are in `format.ts`. It is not `BasketMember.role` (free prose) and not `SignalEvent.role`.
- It is per company per card: it rides every row of the company, NULL means unset, and it is allowed in any link.
  The save refuses two different roles on one company with a 422 and writes nothing; a NULL row beside a set role
  inherits it; clearing is NULL on every row.
- The user sets it on the rail ("none" clears it); it shows as a quiet lowercase tag only when set.
- It is never a call input and never written by a model: a golden test assembles one card with and without roles
  and gets identical cards (`tests/pipeline/test_context_roles_seam.py`). A roster snapshot carries the role only
  when set, so a role-less roster's hash is unchanged.

### Purity and shared values

- Purity is the user's own judgment. `FactsPanel.tsx` runs the extract on a click
  (`GET /workbench/securities/{id}/extract`, with the card's id): the located filing passage and, when the model can
  ground it, an estimate. The user confirms or overrides it, and `POST /workbench/facts` writes `revenue_mix` in the
  user's own workspace with `ratified_by` set to their email and `vouched` recording confirm or override.
- A plain user's extract is purity only (`extract_purity_for_security`); an annual-report filer answers
  "purity not covered". How the extractor reads a filing is [EXTRACTOR](EXTRACTOR.md).
- An estimate or candidate is never stored as a `fact_*` row
  ([invariant 3](INVARIANTS.md#3-the-model-augments-it-never-sources-a-number-or-a-firing)). Why: the as-of read has
  no ratifier filter and feeds the call detectors, so a stored estimate would reach calls without an error.
- The share count and cash/burn show read-only in the shared values block ([PLATFORM_VALUES](PLATFORM_VALUES.md)
  "The user's read"), with a Flag ([VALUE_FLAGS](VALUE_FLAGS.md)). A plain user's `POST /workbench/facts` for either is
  a 403; a `catalyst` fact is a 409 (the form is retired).
- A meter shows "—" until its facts exist, which is not zero.

### Sign-off and authorship

- Sign-off (`BasketMember.signed_off`) endorses the company. It never sets authorship, never gates Save (include
  does), and never feeds the call or a score ([invariant 4](INVARIANTS.md#4-opinionated-on-timing-deferential-on-the-thesis)).
  It carries across a re-draft, and a signed-off name is never dropped.
- A hand-add (the master search, a holdings include, a surfaced fund) enters signed off: the pick is the endorsement.
  A To-Review add or a pile pick enters included but not signed off; "Sign off all picked" endorses that picked set
  in one act and shows only when some picked name is unendorsed.
- Authorship (`authored_by`) records who wrote the member's description, `thesis_fit`: `system_drafted` ("model
  draft") until the user edits the text, then `operator_edited` ("your words"). Nothing else changes it, and editing
  does not sign off. Only an edited description pins a name against a re-draft's re-roll.
- `operator_set` is retired for members. The save translates a stray one to `signed_off = true` and `system_drafted`
  (`test_promote_translates_the_legacy_operator_set`). In the term set it still marks a seed.
- `thesis_fit` is distinct from `detail` (the Board's met cell) and from a segment's descriptor.
- `BasketMember.conviction` (1 to 5; NULL is unset, never zero) is stored and carried by every save, but no control
  sets it, and it never reaches the call. It is not the signal conviction computed in `calls/`.

### The save contract

`promote` runs these checks in order; a refusal writes nothing.

1. Normalize the fixed links: drop any payload segment named Context, ETFs or Bench and append the three; respell a
   member's label to the exact one.
2. Validate the thesis (segment consistency): 422.
3. Translate a legacy `operator_set` member.
4. Membership: every `security_id` must be an exact row of the shared master, else 404
   ([invariant 11](INVARIANTS.md#11-entity-resolution-is-an-exact-allowlist);
   `test_promote_rejects_a_security_not_in_this_tenants_master`).
5. Move a non-primary sibling to its CIK's primary instrument (`master.canonicalize_ids`), logged.
6. Identity coherence (`securities/coherence.classify_members`): a sibling or renamed label aligns to the bound row; a
   cross-company or drifted label is a 422 naming both identities, unless the member's id is listed in
   `identity_overrides` (logged).
7. Silent or voting, funds in ETFs only, one role per company: 422 naming each offender; then NULL roles inherit.
8. Keep the stored `surfaced_terms` for existing members ([DISCOVERY](DISCOVERY.md) "Re-scope and `surfaced_terms`").
9. Workspace caps on live cards and companies: 409 under the workspace's caps lock.
10. Upsert and commit (another workspace's card id is a 404), then `start_member_load` for the new companies.

Scores are never sent and never stored; they derive on read.

## Contracts

- `POST /workbench/theses` is the only writer of `basket_member` and replaces the whole basket.
- The draft, the autosaved session and the run loader write no `basket_member` or `fact_*` row.
- `PromoteOut.ingest` or `PromoteOut.onboarding` names the member load, never both.
- The session blob's `SCHEMA_VERSION` (`triageSession.ts`) stays put: new fields are additive, and old blobs restore.

## Who can do what

Every route sits behind `require_user` on its router; a card route uses `get_thesis_or_404`, so another workspace's
card is a 404. The steward-only routes below are swept by
`tests/app/test_auth_gate.py::test_a_plain_user_gets_403_on_every_steward_route`.

| Route or control | Who | Enforced by |
|---|---|---|
| Create, edit, Save, scored-view edits (`POST /workbench/theses`) | any signed-in user, own workspace; workspace caps | `get_workspace`, `workspace_caps.check`; `tests/app/test_workspace_caps.py` |
| Exclusions, session autosave, member-load polls | any signed-in user, own card | `get_thesis_or_404` |
| Term set, drafts, saved runs | see [DISCOVERY](DISCOVERY.md) "Who can do what" | |
| Master search (`GET /workbench/securities`) | anyone signed in; read only | `require_user` |
| Purity extract | any signed-in user, a company on their own card; `extract` allowance; a steward gets the full extract | `require_own_security`, `allowance.charge("extract")`; `test_a_plain_users_extract_is_purity_only_and_never_reads_the_values_filing` |
| Ratify purity (`POST /workbench/facts`) | any signed-in user, own workspace | `get_workspace` |
| Set a share count or cash/burn by hand | steward | in-route check; `test_a_plain_users_share_count_or_cash_confirm_is_refused` |
| ETF holdings | any signed-in user, a fund on their own card; `holdings` allowance | `require_own_security`, `allowance.charge("holdings")` |
| Price pull (`POST .../ingest-prices`) | steward | `require_super_admin`; `test_the_price_pull_is_steward_only` |
| Flag explainer (`POST /workbench/facts/explain`) | steward, from the Admin company page, not the Workbench | `require_super_admin`; `test_the_flag_explainer_is_steward_only` |
| Business-type re-tag (`POST .../business-type`) | steward; the rail shows no control to a user | `require_super_admin`; `Workbench.tsx` passes `onRetag` only to a steward |
| Surface an ETF (`POST /workbench/securities/resolve-etf`) | steward | `require_super_admin`; `test_a_plain_users_resolve_etf_is_403_and_writes_nothing`, `SurfaceEtf.test.tsx` |

## Cost and limits

Platform data (prices, filings, share counts, cash/burn) loads in the background for every company on a live card. A
user's own spend is bounded twice, and [AUTH](AUTH.md) owns both: the daily allowances on user-triggered fetches
(`term_run`, `chain_draft`, `extract`, `holdings`, `price_pull`; 429 naming the reset; a steward is never counted;
AUTH "User-triggered fetches") and the workspace caps on live cards, companies and terms per card (409, nothing
written; a save that keeps or shrinks a count always passes; AUTH "Workspace caps"). Global caps bound the drafts and
ingests running at once.

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `STALE_SESSION_DAYS` | `frontend/src/workbench/Workbench.tsx` | n/a (code) |
| `DRAFT_POLL_TIMEOUT_MS` | `frontend/src/workbench/ChainEditor.tsx` | n/a (code) |
| `onboard_fast_lane_max` | `domain/settings.py` | no |
| `ingest_jobs_max_running` | `domain/settings.py` | yes |
| `allowance_*_per_day`, `workspace_cap_*_default` | `domain/settings.py` ([AUTH](AUTH.md)) | yes |

## Known limits

- No control sets `BasketMember.conviction`; it is stored and shown, and stays unset on new members.
- Unchecking a silent link sends a multi-link company to Discovered, not back to its earlier links.
- Draft run state (matched terms, flags, the To-Review queues) is not restored when a saved card reopens.
- An unresolved name's exclusion stays in the session; only resolved names persist one.
- `GET .../extract` and `GET .../etf-holdings` spend an allowance on a GET request; see [AUTH](AUTH.md).

## Related

- [DISCOVERY](DISCOVERY.md): term sets, the draft pipeline, identity.
- [WORKBENCH_SCORING](WORKBENCH_SCORING.md): the meters.
- [PLATFORM_VALUES](PLATFORM_VALUES.md), [EXTRACTOR](EXTRACTOR.md), [VALUE_FLAGS](VALUE_FLAGS.md).
- [CALL_LOGIC](CALL_LOGIC.md) section 1 and [BOARD](BOARD.md): what the silent links and roles mean downstream.
- [AUTH](AUTH.md): allowances and caps. The interaction principles WB#1-3: `frontend/CLAUDE.md`.
