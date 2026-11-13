# SECURITY.md — output rules

One rule per output context, and where it lives. Text that came from a user, a feed (SEC, a vendor) or a model is
written into each context only through that context's rule. Security hardening PR 4a (2026-11-12) added the rules
below; LLM prompts, agent tool output and error bodies have their own (separate) changes.

What a request may carry (the input side, security hardening PR 3) is in `docs/AUTH.md` § Request limits.

## The rules

| Context | The rule | Where it lives | The guard |
|---|---|---|---|
| A URL in an API response | A field that carries a link is typed `HttpUrlOut`: the value goes out only when it is an `http`/`https` URL with a host, no `user:password@`, no whitespace or control character, at most 2,000 characters; anything else is `null` on the wire. Stored data is never rejected or rewritten; only what goes out is checked. `dashboards/filings.source_url` uses the same function. | `backend/domain/links.py` (`safe_http_url`, `HttpUrlOut`) | `backend/tests/app/test_url_fields_guard.py` fails on a response field named `url`, `*_url`, `href` or `source_ref` typed plain `str` without `HttpUrlOut` or a listed exemption |
| A link in the UI whose URL comes from data | Rendered by `ExternalLink` only: an anchor only for a URL that passes the same rule (else its label as plain text), always `target="_blank" rel="noopener noreferrer"`. A link to `sec.gov` (or a host ending `.sec.gov`) looks as it always did; a link anywhere else shows its host and an external mark, with the full URL as its title. An "EDGAR" label is chosen with `isSecLink`, so it only ever sits on a real SEC link. | `frontend/src/components/ExternalLink.tsx` (`safeHttpUrl`, `linkDestination`, `isSecLink`) | `frontend/src/components/__tests__/externalLinkGuard.test.ts` fails on any `<a href={…}>` (or `.href =`) outside it, except the three static internal links and the two blob downloads |
| Text sent to Slack | `&`, `<`, `>` become `&amp;`, `&lt;`, `&gt;` (Slack's documented escaping: `<!channel>`, `<@U…>` and `<url\|label>` stay inert text), other control characters a space, the message capped. Applied once at the chokepoint every POST passes (`post_text` and `notify/page.py`'s fallback POST). A push's line breaks are deliberate, so each template also flattens every value it interpolates (`one_line`), and the log notifier flattens its lines the same way (log injection). | `backend/notify/escape.py` | `backend/tests/notify/test_slack_hostile.py` drives every template with hostile values |
| Browser headers | Every nginx response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` and an enforced `Content-Security-Policy: frame-ancestors 'none'`, plus the full policy as `Content-Security-Policy-Report-Only` (see below). No HSTS at this layer: nginx serves plain port 80; HSTS belongs to the TLS terminator in front of it. | `frontend/nginx.conf` (server block, `always`) | `backend/tests/ops/test_nginx_headers.py` parses the file: the headers are on the server block, and no `location` drops them |
| HTML text and attributes | React escapes text and attribute values. No `dangerouslySetInnerHTML`, `innerHTML` or `eval` anywhere. | React | — |
| The caller's network address (an input, but one rule) | Only `app/client_address.py` reads the caller's address. It believes nginx's `X-Real-IP` only from the nginx container itself; any other peer is keyed on its own address; an IPv6 address counts by its /64. Every throttle (sign-in, `/register`, `/authorize`) uses it. | `backend/app/client_address.py`; `frontend/nginx.conf` sets `X-Real-IP $remote_addr` on every proxied location | `backend/tests/app/test_client_address_guard.py` fails on any other module under `backend/app` that reads `request.client` or `scope["client"]`; `backend/tests/ops/test_nginx_real_ip.py` parses nginx.conf |

## The Content-Security-Policy is report-only for now

The full policy (`default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src
https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'self'; object-src 'none'; form-action
'self'`) ships as `Content-Security-Policy-Report-Only`: the browser logs what it would block and blocks nothing. A
test run cannot load the built app under nginx, and a wrong policy blanks the app. The operator loads the stack once,
checks the browser console for CSP violations, and a one-line follow-up renames the header to
`Content-Security-Policy` (merging `frame-ancestors 'none'` into it). See `docs/DEPLOY.md` § Safety.

The policy assumes the built `index.html` has no inline `<script>`; keep it that way.

## Known exemptions

- `ExtractedFact.source_ref` and `LocatedPassage.source_ref` (`backend/domain/extraction.py`) stay text on the wire:
  the same models are the request body of `POST /workbench/facts/explain` and feed the ratify body, so nulling them
  would refuse the round trip. The Facts panel renders the passage link through `ExternalLink`.
- `AgentsOut.connector_url` is server-built (the configured public origin + the MCP path), shown as text and copied,
  never an `href`.
- The backend's port 8000 is published on the host in the stock compose file, so a direct request there bypasses
  the nginx headers (a Phase 0 item, with TLS).
