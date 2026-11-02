"""Process-local snapshots. Expiry is monotonic; nothing is persisted to disk."""

import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .errors import AccessError


@dataclass
class Snapshot:
    id: str
    envelope: dict
    settings: dict
    view: dict
    png: bytes
    image_metadata: dict
    expires_at: str
    deadline: float
    size: int


class SnapshotStore:
    def __init__(
        self,
        *,
        ttl: float = 600,
        capacity: int = 8,
        max_bytes: int = 64 * 1024 * 1024,
        clock=time.monotonic,
    ):
        if ttl <= 0 or capacity < 1 or max_bytes < 1:
            raise ValueError("Snapshot limits must be positive")
        self.ttl, self.capacity, self.max_bytes, self.clock = ttl, capacity, max_bytes, clock
        self._items: OrderedDict[str, Snapshot] = OrderedDict()

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def _purge(self):
        for key in list(self._items):
            if self._items[key].deadline <= self.clock():
                del self._items[key]

    def add(
        self,
        snapshot_id: str,
        envelope: dict,
        settings: dict,
        view: dict,
        png: bytes,
        image_metadata: dict,
    ) -> Snapshot:
        self._purge()
        size = len(
            json.dumps([envelope, settings, view, image_metadata], ensure_ascii=False).encode()
        ) + len(png)
        if size > self.max_bytes:
            raise AccessError(
                "snapshot_too_large", "Snapshot exceeds this process's memory budget."
            )
        while self._items and (
            len(self._items) >= self.capacity
            or sum(item.size for item in self._items.values()) + size > self.max_bytes
        ):
            self._items.popitem(last=False)
        item = Snapshot(
            snapshot_id,
            envelope,
            settings,
            view,
            png,
            image_metadata,
            (datetime.now(timezone.utc) + timedelta(seconds=self.ttl)).isoformat(),
            self.clock() + self.ttl,
            size,
        )
        self._items[snapshot_id] = item
        return item

    def get(self, snapshot_id: str) -> Snapshot:
        self._purge()
        if snapshot_id not in self._items:
            raise AccessError(
                "snapshot_expired",
                "Snapshot is expired, evicted, or unknown; call read_dashboard again.",
            )
        return self._items[snapshot_id]
