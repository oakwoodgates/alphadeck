# Docs standard

How to write and edit the Markdown in this repo: the rules, the banned patterns, the doc types with their
budgets and templates, how to rename or delete a doc, and how to run the checker. Read it before you write
or edit a doc, a skill, an agent file or a `CLAUDE.md`; it binds people and agents alike.
[`scripts/check_docs.py`](../scripts/check_docs.py) enforces what it can; the reviewer checks the rest.

## Rules

| # | Rule | Why, where not obvious |
|---|---|---|
| 1 | Reference, not narrative: present tense, what is true now. No PR numbers, status tags, ship or decision dates, or workstream codenames (`F1`, `R8-B`, `W7a`). Stable ids stay: invariant numbers, WB#1-3, DASHBOARDS rule N, CALL_LOGIC section numbers, rule ids in `backend/domain/rule_log.py`. Dates that describe the data stay. | History lives in git and the rule log. |
| 2 | One home per concern. Each fact has one owning doc; others link to it. | Copies drift. |
| 3 | Point at code; do not transcribe it. Name the module, function or constant; never copy code tables, constants, defaults, wire shapes, UI strings or registry fields. Contract wording (recorded call-card text) is the exception: say so and name the constant. | A copy goes stale. |
| 4 | Lead with the answer: what it is, who uses it, when to read the doc. | |
| 5 | Plain words; see the banned patterns. | |
| 6 | Tables for enumerations; prose for reasoning. | |
| 7 | Status in one place. Open work lives in [ROADMAP](ROADMAP.md) only; a dashboard's status in its `entry.ts`; a switch's default in `domain/config.py` or `domain/settings.py` (a doc says "see the setting"). | |
| 8 | Size budgets per doc type (below). A subsystem doc over 3,500 words splits. | Long docs go unread. |
| 9 | Say who can do it: a plain user, the steward, the machine (cron or a background job) or anyone signed in, for every route, control and CLI. Name the enforcing dependency (`require_super_admin`) and the test that pins it. | The most common drift. |
| 10 | A measured number stays only if it sets a dial or a threshold. Case studies, timings and row counts go to the PR description or the rule log. | |
| 11 | Cite headings or symbols, never line numbers. A heading that code cites is a contract: keep it verbatim or repoint every citer in the same PR. | Line numbers move. |
| 12 | Procedures live in skills: steps, checks and stop conditions. A doc owns the model, the contract and the reasons and names the skill once. If a section says what to type or check in order, it is a skill. | |
| 13 | A one-time runbook becomes an archive record in `docs/archive/` after it runs and is never updated. Docs never link to the archive; code may cite a record as evidence for a constant. | It describes a past state. |
| 14 | Commands run as written, with auth header, working directory and environment. A doc lists a CLI once, in one line; flags live in `--help`. | |
| 15 | Edit generated files at their source: `backend/openapi.json` and `frontend/src/api/types.gen.ts` come from docstrings, the `alphadeck-agent` recipe blocks from `backend/agent_tools/prompts/`. Do not cite docs in API-visible docstrings. | They ship in the API contract. |
| 16 | Vocabulary: "user" is a plain user and their workspace, "steward" the super-admin who curates platform data, "operator" only the person who runs the deployment. Never `v1` or `v2` for a feature; V2 is the SaaS release. One name per concept. | |
| 17 | Name the guards: a doc that tells someone to edit a data file or a guarded subsystem names the tests that constrain the edit. | |
| 18 | Plans are not reference; a plan lives in the PR description. No `Choices to confirm` sections. | |
| 19 | One "Known limits" list per doc, current residuals only. | |
| 20 | Re-check every kept claim against the code. A call-logic disagreement between doc and code is listed for the operator in the PR, never silently fixed either way. | The call logic is the product. |

## Banned patterns

| Pattern | Where | Checker |
|---|---|---|
| Status tags (`[BUILT]`, `[PROPOSED]`, `[PINNED]`) and any bracketed status in a heading | docs, not the archive | fail |
| PR references: `PR #123`, `#123` (three or more digits), `(#61)` | docs, not the archive | fail |
| Any path into the gitignored scratch folder for plans | all tracked Markdown | fail |
| A doc file name with a line number after a colon | all tracked Markdown | fail |
| A code file name with a line number after a colon | docs, not the archive | warn |
| Machine-local paths and database URLs that carry credentials | all tracked Markdown | fail |
| `MEASURED`, `Choices to confirm`, `Not yet rehearsed` | docs, not the archive | fail |
| `load-bearing`, `honest by construction`, `the spine` | docs, not the archive | fail |
| More than 3 em dashes per 100 words; lines stacking three or more bold or CAPS marks | docs, not the archive | warn |
| Letter-number codenames outside code spans (SEC form names such as 8-K, S-1, 13D/G, 10-K and DEF 14A are not codenames) | docs, not the archive | warn |
| `curl` to an app route other than `/health` without a credential | docs and skills, not the archive | fail |

A banned phrase inside a code span is a quotation, not a use: the phrase, PR-reference and codename checks
skip code spans.

## Doc types and budgets

A word is a whitespace-separated token with a letter or a digit in it. The checker warns over the soft
budget and fails over the hard one.

| Doc type | Soft / hard | Template |
|---|---|---|
| `README.md` | 1,300 / 1,600 | What Alpha Deck is; core concepts; architecture; repo map; run and test; the docs table; one line of trust state |
| Root `CLAUDE.md` | 2,500 / 3,000 | Safety rules; invariants one line each; interaction principles; how to work; conventions lint misses; naming guards; commands; where to look |
| `backend/CLAUDE.md`, `frontend/CLAUDE.md` | 1,000 / 1,200 | Rules for work under that folder |
| Subsystem reference | 1,500 to 3,000 / 3,500 | below |
| Catalog (DETECTORS, [DASHBOARDS](DASHBOARDS.md)) | 4,500 / 5,000 | Table-led; each entry 300 words or fewer |
| Dashboard doc | 400 / 500 | below |
| Skill | 300 to 700 / 900 | below |
| Skill or agent description | 40 / 60 | Starts "Use when <trigger>." |
| Archive record | 600 / 800 | below |
| GLOSSARY | 1,200 / 1,500 | One or two lines per term plus the owner link |
| [ROADMAP](ROADMAP.md) | 1,500 / 2,000 | One line per item plus the owner link |
| Always loaded: root `CLAUDE.md` plus every description | 3,200 / 3,300 | Every session pays for these words |

**Subsystem reference:**

```markdown
# <Name>
One paragraph: what it is, who uses it (user, steward, machine), when to read this doc.
## Code
| Piece | Module |
## How it works
Rules, each with one line of why when the reason is not in the code.
## Contracts
What other code relies on: tables, locks, exit codes, wire behavior.
## Who can do what
| Route, control or CLI | Who | Enforced by |
## Settings
| Knob | Owner | Passed by compose? |
## Known limits
## Related
```

A settings table never copies the default.

**Dashboard doc:** a title, then one line with the slug, the as-of behavior and a link to
[DASHBOARDS](DASHBOARDS.md), then six headings the checker requires: `What it shows`, `Who counts`,
`Data and code` (a table: compute module, route, shared readers, cache key, frontend folder, tests),
`As-of behavior`, `Rules not visible in code` (a rule and its why, one line each) and `Known limits`. No
status banner, sign-off line or timings. The Board's doc is `docs/BOARD.md`.

**Skill:** front matter `name` equals the folder. Body headings: `Guardrails`, `Steps`, `Checks`,
`Stop and ask when`, `Model` (one link to the owning doc).

**Archive record:** `What ran` (what, when, by whom), `Artifacts` (where they are), `Decisions`,
`Follow-ups` (links to ROADMAP), `Full runbook` (a git ref such as `git show <commit>:docs/<NAME>.md`).

## Rename, split or delete a doc

1. Find every citer (code comments, docstrings, scripts, tests, skills, docs): `git grep -n -F '<NAME>.md'`.
2. Repoint every citer in the same PR. A heading that code cites keeps its exact text, or all its citers
   move in the same PR; in a split, a moved heading keeps its name.
3. Never edit an applied migration, even a comment; keep the headings it cites.
4. If a citer is an API docstring, regenerate the contract pair: `python -m app.openapi_export`, then
   `npm run gen:api`.
5. Move content to its owner before deleting its old home.
6. Run the checker and `git diff --check`.

## Run the checker

From the repo root, with Python 3.11 or later:

```bash
python scripts/check_docs.py                   # exit 1 on a fail finding not in the baseline
python scripts/check_docs.py --all             # every finding, baselined or not
python scripts/check_docs.py --write-baseline  # record the current findings
```

Standard library only: no database, no network. It reads the files git would track; CI runs it in the
`docs` job. Its checks are `links`, `pointers` (every `docs/` path named in code or doc text exists, and a
cited heading or DASHBOARDS rule number matches; report only under migrations and the archive), `banned`,
`budgets`, `parity` (dashboard `entry.ts` files and their docs), `frontmatter` and `archive`.

The baseline, `scripts/check_docs_baseline.json`, holds the findings the tree had when the checker arrived,
keyed by file, check and a normalized key, never a line number. A link to a doc that a later PR adds may go
in the baseline: regenerate it with `--write-baseline`, never by hand, and list the forward links in the PR
description. When the baseline is deleted, every finding fails.
