"""Our own OAuth 2.1 sign-in for the hosted ``/mcp`` endpoint (docs/CONNECTORS.md).

* ``store`` — the tables (migration 0080) as plain synchronous functions over a psycopg connection. No MCP SDK
  import, so the login door (``app.auth``), the consent routes and ``/account/connections`` use it on any install.
* ``provider`` — ``AlphaDeckAuthProvider``, the MCP SDK's ``OAuthAuthorizationServerProvider`` over ``store``.
* ``server`` — ``build_oauth_routes`` composes the SDK's own authorization-server routes over the provider and
  replaces only its metadata document; ``mount_oauth`` appends them to the API when ``ALPHADECK_PUBLIC_ORIGIN``
  is set.

Nothing here names a tenant: a grant belongs to a USER, and the login door scopes every read to that user's
workspace.
"""
