from __future__ import annotations


class CacheMiss(Exception):
    """A requested resource isn't cached and live pulls are disabled.

    The ingestion etiquette guard: the test transport never hits the network — a cache miss with
    live pulls off raises this instead.
    """
