# Security

How the API and the UI treat what comes in and what goes out: one output rule per context, the error contract,
the request limits, the caller's address, TLS and the Content-Security-Policy. Read it before you add a link
field, a Slack message, a nginx location, an error path, a request field or a throttle. Sign-in, roles and
allowances are in [AUTH](AUTH.md).

## Code

| Piece | Module |
|---|---|
| Response links | `backend/domain/links.py` (`safe_http_url`, `HttpUrlOut`) |
| UI links | `frontend/src/components/ExternalLink.tsx` (`safeHttpUrl`, `linkDestination`, `isSecLink`) |
| Slack text | `backend/notify/escape.py` |
| Browser headers, body sizes, `X-Real-IP` | `frontend/nginx.conf` (server block and each proxied location) |
| Error contract | `backend/app/errors.py` (`install`, `fail`, `note_caller`, `SENTENCES`) |
| Request limits | `backend/domain/text_types.py`; used by the request models in `backend/app/schemas_api.py`; mirrored in `frontend/src/api/limits.ts` |
| The caller's address | `backend/app/client_address.py` |

## Output rules

Text from a user, a feed (SEC, a vendor) or a model reaches each output only through that output's rule.

| Context | Rule | Guard |
|---|---|---|
| A URL in an API response | A link field is typed `HttpUrlOut`: it goes out only as an `http`/`https` URL with a host, no `user:password@`, no whitespace or control character, within `MAX_URL_LENGTH`; anything else is `null` on the wire. Stored data is never rejected or rewritten. `domain.links.source_url` (re-exported by `dashboards/filings.py`) uses the same function. | `backend/tests/app/test_url_fields_guard.py` fails on a response field named `url`, `*_url`, `href` or `source_ref` typed plain `str` without `HttpUrlOut` or a listed exemption |
| A data-fed link in the UI | Rendered by `ExternalLink` only: an anchor only for a URL that passes the same rule (else its label as text), always `rel="noopener noreferrer"`; a link off `sec.gov` shows its host; an "EDGAR" label only where `isSecLink` accepts. | `frontend/src/components/__tests__/externalLinkGuard.test.ts` fails on any `<a href={…}>` or `.href =` outside it, except a short list of static internal links and blob downloads |
| Text sent to Slack | `&`, `<`, `>` are escaped (so `<!channel>`, `<@U…>` and `<url\|label>` stay inert), other control characters become a space, and the message is capped, once, at the chokepoint every POST passes (`notify.post_text` and `notify/page.py`'s fallback). Each template flattens every value it interpolates (`one_line`); the log notifier does the same (log injection). | `backend/tests/notify/test_slack_hostile.py` drives every template with hostile values |
| Browser headers | Every nginx response carries `nosniff`, a `Referrer-Policy`, an enforced `frame-ancestors 'none'` and the full policy as report-only (below), all with `always`. No HSTS here: nginx serves plain port 80, and HSTS belongs to the TLS terminator. | `backend/tests/ops/test_nginx_headers.py`: the headers sit on the server block and no `location` drops them |
| HTML | React escapes text and attributes. App source uses no `dangerouslySetInnerHTML`, `innerHTML` or `eval`. | none (review) |

Known exemptions from the URL guard:

- `ExtractedFact.source_ref` and `LocatedPassage.source_ref` (`backend/domain/extraction.py`) stay text on the
  wire: the same models are the request body of the flag explainer and feed the ratify body, so nulling them would
  break the round trip. The UI renders the passage link through `ExternalLink`.
- `AgentsOut.connector_url` is server-built (the public origin plus `/mcp`), shown as text, never an `href`.

## Errors

What a failed request answers. An error the code did not word itself never reaches a plain user's screen.

- **A fixed sentence and a reference.** The body is `{"detail": "<the sentence for that class> Reference
  1a2b3c4d."}`: a fixed sentence (`errors.SENTENCES` and the routes' own constants) plus 8 random hex characters.
  The exception and its traceback are logged under that reference (logger `alphadeck.errors`: ERROR for a 5xx,
  WARNING otherwise), so a quoted reference finds the log line. No SQL, column value, server path or upstream (SEC,
  vendor, OpenFIGI, model) text is in the body.
- **A steward also sees the cause.** For a `super_admin` caller the body continues `Detail: <ExceptionClass>:
  <message>`. Why: the steward fixes the data layer and needs the cause; a plain user gets nothing they could probe
  with. Every gated router carries `errors.note_caller`; an unknown caller (the open `POST /auth/login`, or a failure
  before the gate ran) is treated as a plain user.
- **Unhandled database errors** go through the global handlers in `errors.install`, which map each psycopg class to
  a status (`errors.classify`): bad data and a check, foreign-key or not-null break are 422, a uniqueness or other
  integrity conflict 409, an oversized index row 413, a timeout or a lost connection 503 with `Retry-After`
  (request timeouts: [DEPLOY](DEPLOY.md) "Connections"), anything else 500.
- **Failures a route catches** (an upstream fetch, a file read, the company write lock held elsewhere) go through
  `errors.fail(status, sentence, exc, user=...)` with the route's own status. Background jobs (the chain draft, the
  on-promote data fetch) follow the same contract on their poll; a partial fetch names each failed ticker's parts in
  plain words and logs the raw messages under the reference.
- **An explicit mapping wins.** A route's own `HTTPException` (the duplicate-email 409, the own-cards 404, the
  allowance 429, every domain 422) is answered as worded.
- **The validation 422** keeps FastAPI's list shape (`loc`, `msg`, `type`, `ctx`) but never echoes the caller's
  `input` (it can be megabytes) or a documentation `url`.

Tests: `backend/tests/app/test_errors.py` (the handlers), `test_error_text.py` (the route sites, both callers),
`test_hostile_input.py` (hostile values on every route the database sweep ranked: a status below 500, no row written).

## Request limits

What a request may carry. **The one rule: limits apply to what comes in on a request, never to stored data being
read.** The domain models (`Thesis`, `BasketMember`, ...) are built from stored rows on every load (the nightly,
replay, the backtest, every route), so none carries a limit: a stored value over a limit can never make a card
unloadable. Every limit is a named constant in `backend/domain/text_types.py`, used only by the request models in
`app/schemas_api.py` (request-side subclasses such as `BasketMemberIn`) and by query parameters.

| Field class | Constant |
|---|---|
| One-line label (names, labels, `role`, `kind`, a displayed ticker, token and display names) | `LINE_MAX` |
| Short prose (descriptor, `thesis_fit`, kill criterion, reasons, notes) | `PROSE_MAX` |
| Narrative | `NARRATIVE_MAX` |
| Search term, seed, surfaced term | `TERM_MAX` |
| A ticker that can reach the shared master | `TickerIn` (the `securities/symbols.py` pattern) |
| URL or `source_ref` | `URL_MAX` |
| Email, password | `EMAIL_MAX`, `PASSWORD_MIN` to `PASSWORD_MAX` (never trimmed) |
| Collections per card (basket rows, links, terms, catalysts, kill criteria, exclusions) | `BASKET_MAX`, `SEGMENTS_MAX`, `TERMS_MAX`, `CATALYSTS_MAX`, `KILL_CRITERIA_MAX`, `EXCLUSIONS_MAX` |
| Numbers | finite only; ranged types `Shares`, `Money`, `MixPct`, `AdsRatio`, `DecisionAmount` |
| Dates | `RequestDate`, `CatalystDate`, `AsofDate` (windows around today's market date) |
| The triage-session autosave | opaque state, `STATE_MAX_BYTES` (413) and `STATE_MAX_DEPTH` |

Every text class trims leading and trailing whitespace and alters nothing else; a NUL or other control character is
refused, except tab, newline and carriage return in the prose classes. A value over a limit is a 422 in FastAPI's
list shape naming the field; the forms carry the same limits as `maxLength`. Nothing is cut short silently.

- Sign-in checks the email and password limits in the request model, before any hash is computed.
- A promote is a full replace. A stored card over a limit refuses its next save with a 422 naming the field and is
  left as it was; there is no bypass.
- nginx caps request bodies at 1 MB, raised to 25 MB on two exact locations (the promote and the triage session);
  neither sets its own `add_header`, so both keep the server's headers.
- Plain `BaseModel` request models ignore an unknown field (an older autosave may carry one); the nested domain
  copies refuse one.
- Steward-only bodies keep their own limits and are listed as exemptions in the guard.

Tests: `backend/tests/app/test_request_bounds_guard.py` fails on an unbounded request field, on a domain model that
carries a request limit, and on drift between `limits.ts` and the backend; `test_request_limits.py` walks every class
at and past its limit; `test_stored_over_limit.py` loads a card stored over every limit; `tests/domain/test_text_types.py`
pins the types; `tests/ops/test_nginx_body_limits.py` reads nginx.

## The caller's address

Every throttle (sign-in, `/register`, `/authorize`) counts per client address, so the backend must see the real
caller behind nginx.

- nginx sets `proxy_set_header X-Real-IP $remote_addr` on every location that proxies to the backend;
  `$remote_addr` overwrites whatever the caller sent.
- `app/client_address.py` is the only module that reads the address. It believes `X-Real-IP` only when the direct
  peer is the nginx container (found by resolving the compose service `frontend`, cached a minute). Any other peer,
  such as a direct hit on the published port 8000, is keyed on its own address. If the lookup fails, every request
  through nginx shares nginx's address: one budget for everyone, too strict but never spoofable. An IPv6 caller
  counts by its /64.
- Tests: `backend/tests/app/test_client_address_guard.py` fails on any other module under `app/` that reads
  `request.client` or `scope["client"]`; `backend/tests/app/test_client_address.py`;
  `backend/tests/ops/test_nginx_real_ip.py` parses nginx.

## TLS

The repo ships no TLS terminator: nginx listens on plain port 80 and the backend's port 8000 is published on the
host. Prod runs as the local compose stack. Anything beyond localhost needs TLS first: the session cookie is
`Secure` by default (`ALPHADECK_COOKIE_SECURE`), a bearer token must never cross a network in clear, and
`ALPHADECK_PUBLIC_ORIGIN` accepts `http` only on a loopback host ([AGENT_TOOLS](AGENT_TOOLS.md) "Hosted endpoint").
When a TLS proxy goes in front of nginx, nginx must read the real address from it (`set_real_ip_from <proxy
address>;` and `real_ip_header X-Forwarded-For;` in `frontend/nginx.conf`), or every caller shares the proxy's
address and a few failed sign-ins anywhere pause everyone.

## The Content-Security-Policy

nginx enforces only `frame-ancestors 'none'`; the full policy (in `frontend/nginx.conf`) ships as
`Content-Security-Policy-Report-Only`, so the browser logs what it would block and blocks nothing. A test run cannot
load the built app under nginx, and a wrong policy blanks the app. Enforcing it is one rename of the header (folding
`frame-ancestors` in) after a browser-console check of the main pages finds no violation. The policy assumes the
built `index.html` has no inline `<script>`; keep it that way.

## Who can do what

| Surface | Who | Enforced by |
|---|---|---|
| `Detail:` in an error body | the steward | `errors.note_caller`, `errors.message` |
| Steward-only request bodies, with their own limits | the steward | `require_super_admin` |

## Known limits

- The Content-Security-Policy is report-only.
- A direct request to the published port 8000 bypasses nginx's headers and body caps.
- Two GET routes write an allowance count; see [AUTH](AUTH.md) "The cookie and CSRF".

## Related

[AUTH](AUTH.md), [CONNECTORS](CONNECTORS.md) (OAuth limits), [AGENT_TOOLS](AGENT_TOOLS.md) (the tool error shape).
