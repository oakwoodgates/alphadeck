"""Alpha Deck's data-first MCP server: typed, read-only tools over the API's own JSON (docs/AGENT_TOOLS.md).

``views/`` hold the PURE shaping functions (payload in, bounded reply out; no I/O, no clock); ``client`` reads the
loopback API; ``resolve`` turns a thesis name or a ticker into an id; ``budget`` is the one size rule; ``server``
wires them into an MCP server over stdio. The legacy capture server (``agent_access``) stays beside it until it is
retired.
"""
