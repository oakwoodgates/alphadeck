"""Every proxied location in ``frontend/nginx.conf`` tells the backend who called (security PR 5 step 1).

``proxy_set_header X-Real-IP $remote_addr`` overwrites whatever the caller sent with the address nginx saw;
``app/client_address.py`` believes it only from the nginx container. nginx inherits ``proxy_set_header`` into a
location only when that location sets none of its own, and every proxied location sets ``Host``, so each one must
set ``X-Real-IP`` itself. A ``proxy_set_header`` is not an ``add_header``: the security-header inheritance rule
(``test_nginx_headers.py``) is untouched.
"""

from __future__ import annotations

from tests.ops.test_nginx_headers import PROXIED, _locations, _parse, _server, _tokens


def _proxied():
    return [
        loc for loc in _locations(_server()) if any(d[0] == "proxy_pass" for d in loc.directives)
    ]


def _sets_real_ip(loc) -> bool:
    return any(
        d[:3] == ["proxy_set_header", "X-Real-IP", "$remote_addr"] and len(d) == 3
        for d in loc.directives
    )


def test_every_proxied_location_sets_x_real_ip_from_remote_addr():
    locs = _proxied()
    names = {b.name.removeprefix("location ").strip() for b in locs}
    assert PROXIED <= names  # the walk covers the API, /mcp and the OAuth routes
    assert len(locs) >= 10
    missing = [loc.name for loc in locs if not _sets_real_ip(loc)]
    assert missing == []


def test_no_location_forwards_a_caller_supplied_address():
    for loc in _proxied():
        for d in loc.directives:
            if d[0] == "proxy_set_header" and d[1].lower() in ("x-real-ip", "x-forwarded-for"):
                assert d[2] == "$remote_addr", f"{loc.name}: {d}"


def test_the_check_sees_a_location_without_it():
    server = _parse(
        _tokens("server { location /x { proxy_pass http://b; proxy_set_header Host $host; } }")
    ).children[0]
    (loc,) = _locations(server)
    assert not _sets_real_ip(loc)
