"""Bounded per-player cycles; only displayed questions advance a cycle."""

from collections import OrderedDict
from threading import Lock
import time


class PhotoRotation:
    def __init__(self, *, ttl_seconds=6 * 3600, max_players=2048):
        self.players = OrderedDict()
        self.lock = Lock()
        self.ttl_seconds = ttl_seconds
        self.max_players = max_players

    def seen(self, player_id):
        with self.lock:
            self._expire()
            entry = self.players.get(player_id)
            if not entry:
                return ()
            self.players.move_to_end(player_id)
            entry["used_at"] = time.monotonic()
            return tuple(image for images in entry["cities"].values() for image in images)

    def record(self, player_id, city, image_id):
        if not image_id:
            return
        with self.lock:
            self._expire()
            entry = self.players.setdefault(player_id, {"cities": {}, "used_at": 0})
            images = entry["cities"].setdefault(city, set())
            if image_id in images or len(images) >= 128:
                images.clear()
            images.add(image_id)
            entry["used_at"] = time.monotonic()
            self.players.move_to_end(player_id)
            while len(self.players) > self.max_players:
                self.players.popitem(last=False)

    def _expire(self):
        now = time.monotonic()
        while self.players:
            player_id, entry = next(iter(self.players.items()))
            if now - entry["used_at"] < self.ttl_seconds:
                break
            self.players.pop(player_id)
