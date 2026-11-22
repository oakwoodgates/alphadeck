# Value flags

A value flag is a plain user's report that a shared share count or cash/burn looks wrong or is missing. It tells
the steward and never changes the value; only the flagging user and the steward ever see it. A user raises,
lists and withdraws their own flags; the steward closes them as fixed or kept and can reopen a closed one. Read
this before you change the flag table, its routes, the Slack message or anything that reads `value_flag`. The
values themselves are [PLATFORM_VALUES](PLATFORM_VALUES.md). Naming guard: a value flag is not the extractor's
FLAG tier and not a candidate's `outcome='flag'`.

## Code

| Piece | Module |
|---|---|
| Raise, withdraw, list; the steward's close, reopen and reads | `platform_values/flags.py` |
| Every query of the table | `repositories/value_flag_repo.py` (and the user's read in `platform_values/user_view.py`) |
| The user's routes | `app/routers/value_flags.py` |
| The steward's routes | `app/routers/admin_platform_values.py` |
| The Slack message | `notify/flags.py` |
| Refusal sentences | `app/errors.py` (`VALUE_FLAG_SENTENCES`, `VALUE_FLAG_STEWARD`, `VALUE_FLAG_NOT_FOUND`) |
| Screens | user: `frontend/src/workbench/SharedValues.tsx`; steward: `frontend/src/admin/CompanyFacts.tsx`, `SharedFacts.tsx` |

## How it works

**What may be flagged:** a company's share count or cash/burn in any state: a value shown, a value awaiting the
steward, or no value yet (a request to look at it). The request carries only the company, the value type and an
optional note. The server records the user and what was current when the flag was raised, from
`platform_values.state.read_state`: the shown fact (`seen_fact_id`), the candidate awaiting the steward
(`seen_candidate_id`), or neither. When a value is shown and a newer candidate is pending, the flag targets the
value shown. Screens say "current when flagged".

**The table** (`value_flag`; migrations 0202, 0210, 0211; user-keyed, in `UNSCOPED_TABLES`):

- one open flag per user and value (a partial unique index);
- closed once, as `fixed` or `kept` by the steward or `withdrawn` by the user; a trigger refuses any edit of a
  closed row, so a reopen appends a new row;
- a flag sees a value or a candidate, never both (a check);
- `fixed_decision_id` is allowed only on a `fixed` row; the check uses `IS NOT DISTINCT FROM`, because a plain
  `outcome = 'fixed'` is unknown on an open row and would pass;
- `reopens` links a reopen row to the flag it reopens: at most one per flag (a unique index), and a trigger refuses
  a reopen of an open or withdrawn flag or one that changes the user, company or value;
- `reopened_by` holds the steward's email on a reopen row only (a check).

**Raising** (`flags.raise_flag`) runs in the route's one transaction: a per-user-and-value advisory transaction lock
(`lock_value`, namespace `FLAG_LOCK_NAMESPACE`), the duplicate check, the allowance charge (`app.allowance.take`),
the state read, the insert. The route commits the row and its allowance unit together; a refusal rolls both back.
Two simultaneous identical requests give one row, one unit and one Slack message, and the loser answers 409
having spent nothing (`tests/app/test_value_flag_api.py`, two real threads). The partial unique index still guards
the row.

**Withdrawing** closes the user's own open flag as `withdrawn` and never refunds the unit; flagging the value again
spends one.

## Contracts

**The user's routes** (`/value-flags`, no as-of: a flag is about the value now):

| Route | Answers |
|---|---|
| `POST /value-flags` | 201 and the new flag |
| `GET /value-flags` | the caller's own flags, open first (newest opened), then closed (newest closed), paged |
| `POST /value-flags/{flag_id}/withdraw` | the withdrawn flag |

Refusals on a raise, in order: 401 not signed in; 403 the steward; 422 a request that does not parse (another value
type, an over-long note); 404 a company unknown or not on any of the caller's own cards, one body for both, read in
the caller's workspace before any flag or allowance read, so it never says whether the company is on anyone else's
card; 409 an open flag by this user on this value; 429 the day's flag allowance spent. A withdraw refuses the
steward (403), an id that is not the caller's or does not exist with the same 404, and a closed flag (409). Each
refusal's sentence is fixed in `app/errors.py`; screens show it as sent. A flag's answer never names who closed it
or another user. Notes are plain text as stored; screens render them as text.

**The allowance:** the action `flag`, `ALPHADECK_ALLOWANCE_FLAG_PER_DAY` per user per market day
([AUTH](AUTH.md)). It is not a fetch; it bounds the Slack messages one user can cause.

**The Slack message** (`notify/flags.py`): one per raised flag, none on a withdrawal, close or reopen. It is built
from what the raise transaction read and sent by a FastAPI background task after the commit and after the answer,
so Slack never holds or decides a click. Destination: `ALPHADECK_STEWARD_SLACK_WEBHOOK_URL`, else
`SLACK_WEBHOOK_URL`, else one log line, with a short timeout (`FLAG_TIMEOUT_S`); a failure is logged without the URL
and never changes the answer or the row. It names the company and the value, what was current when flagged (a
share count's figure and who set it; no figure for cash, nor for a value awaiting the steward), the note if any, and
a link to the steward's company page (`/admin/facts/<security_id>` under `ALPHADECK_PUBLIC_ORIGIN`, else a path in
words). It says "a user": never an email, a user id, a workspace, a card count or a queue rank. Each interpolated
value is flattened with `notify.escape.one_line` and escaped at `notify.post_text`, so a note cannot ping a
channel, link or forge a line. The channel carries users' notes, so every reader of it must be a steward.

## The steward's side

Steward-only routes in `app/routers/admin_platform_values.py`, under the steward gate; each answers the company's
new state (`CompanyValuesOut`), and a plain user gets 403 with nothing written:

| Route | Does |
|---|---|
| `POST /admin/platform-values/flags/{flag_id}/close` | close as `fixed` (naming the decision that fixed it) or `kept` (with a note for the user) |
| `POST /admin/platform-values/flags/{flag_id}/reopen` | the inverse of a close: a new open row |
| `GET /admin/platform-values/{security_id}` | the company read, with its users' flags |

**Closing.** `fixed` needs a standing approve or correct of the same company and value type; `kept` needs a note
that is not blank after trimming and names no decision. Refusal sentences: `app.errors.VALUE_FLAG_SENTENCES`. Nothing
closes or reopens a flag by itself: an approve or correct never closes one.

**Close as fixed is ordered by the company write lock.** `flags.steward_close` takes the company write lock (the one
every steward decision takes), then in one transaction locks the flag row, checks the decision still stands, closes
the flag and commits. An undo of that decision therefore runs wholly before the close (refused, nothing written) or
wholly after it (the close stands). `tests/app/test_admin_platform_values_flags_api.py` proves it with two real
threads; without the lock, a close could commit a fix linked to a decision undone a moment earlier.

**A linked decision undone later.** The closed row is never rewritten, so a `fixed` flag keeps its
`fixed_decision_id`. The company read marks the decision undone (`fixed_decision_undone`) and the flag can be
reopened. The user still reads `fixed` until the steward reopens it.

**Reopen** (`flags.reopen`) appends a new open row linked by `reopens`, copying the user, company, value, note and
what was current from the closed row, and stamps `reopened_by`. The closed row stays in the history, so a reopen
and a second close keep both closes. It sends no Slack message and spends none of the user's allowance. It refuses
an open flag, one already reopened, one the user withdrew, and a value the user has flagged again since.

**The company read's flags** (`PlatformUserFlagsOut`): every open flag, oldest first, and one page of closed ones,
newest closed first, so an old closed flag stays reachable. Each carries the user's email, what it saw, the
outcome, the linked decision and whether it can be reopened (`can_reopen`, `reopen_refusal`). The queue lists a
company with an open flag even when no candidate waits, with the count of open flags only
([PLATFORM_VALUES](PLATFORM_VALUES.md)).

## Privacy

A plain user's answers (`GET /value-flags`, `GET /platform-values/...`'s `my_flag`) carry only the caller's own
flags, with the outcome and the steward's note, never `closed_by`, `reopened_by`, another user's flag or any count.
Only the steward's routes read `user_email`, `closed_by` and `reopened_by`; the user-side reads select a column list
without them and name the caller's id in their SQL. `tests/app/test_value_flag_guard.py` fails when a product file
other than the repository and the user's read queries `value_flag`. Proven with real sign-ins (two plain users in
two workspaces and the steward) in `tests/app/test_admin_platform_values_flags_api.py`.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `POST /value-flags`, `POST /value-flags/{flag_id}/withdraw` | plain user | `require_user` (`_USER`); the routes refuse the steward (`VALUE_FLAG_STEWARD`) |
| `GET /value-flags` | anyone signed in (their own flags) | `require_user` |
| The Flag control on the Shared values block | plain user | the steward sees a link to the company page instead |
| Close, reopen, the company read's flags | steward | `require_super_admin` (`_STEWARD`) |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_ALLOWANCE_FLAG_PER_DAY` | `domain/settings.py` | yes, backend |
| `ALPHADECK_STEWARD_SLACK_WEBHOOK_URL` | `domain/settings.py` | yes, backend |
| `SLACK_WEBHOOK_URL` (the fallback) | `domain/settings.py` | yes |
| `ALPHADECK_PUBLIC_ORIGIN` (the message's link) | `domain/settings.py` | see [AUTH](AUTH.md) |

## Known limits

- Flags cover the share count and cash/burn only; the table's `value_type` also admits `catalyst`, which no route
  accepts.
- A `fixed` flag whose decision is undone later still reads `fixed` to its user until the steward reopens it.

## Related

[PLATFORM_VALUES](PLATFORM_VALUES.md), [AUTH](AUTH.md) (allowances), [ADMIN](ADMIN.md) (the steward's pages),
[TENANT_SCOPES](TENANT_SCOPES.md).
