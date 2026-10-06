"""Keep a small, city-diverse supply of dynamic questions between requests."""

import json
import logging
import random
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
import time

from city_provider import (
    CITIES, commons_retry_delay, get_city_question,
    prepared_question_snapshot, restore_prepared_questions,
)
import photo_statistics

logger = logging.getLogger(__name__)


class DynamicQuestionPool:
    def __init__(self, path, *, cities=None, minimum=8, target=12, batch_size=24,
                 retry_seconds=30, request_interval=3):
        self.path = Path(path)
        self.cities = list(CITIES if cities is None else cities)
        self.minimum = minimum
        self.target = target
        self.batch_size = batch_size
        self.request_interval = request_interval
        self.retry_seconds = retry_seconds
        self.lock = Lock()
        self.loaded = False
        self.running = False
        self.retry_at = 0
        self.city_retry_at = {}
        self.statistics_at = 0
        self.pause = Event().wait

    def start(self):
        """Load once, then replenish in one background worker when needed."""
        with self.lock:
            if not self.loaded:
                try:
                    restore_prepared_questions(json.loads(self.path.read_text(encoding="utf-8")))
                except FileNotFoundError:
                    pass
                except (OSError, ValueError):
                    logger.warning("Could not load dynamic question cache", exc_info=True)
                self.loaded = True
            now = time.monotonic()
            if now >= self.statistics_at:
                self._save_statistics()
                self.statistics_at = now + 60
            if self.running or now < self.retry_at:
                return
            entries = prepared_question_snapshot()
            if not self._deficits(entries):
                return
            self.running = True
            Thread(target=self._refill, name="city-pool", daemon=True).start()

    def _refill(self):
        try:
            for attempt in range(self.batch_size):
                if commons_retry_delay() > 0:
                    break
                entries = prepared_question_snapshot()
                cities = self._deficits(entries)
                if not cities:
                    break
                if attempt:
                    self.pause(self.request_interval)
                    if commons_retry_delay() > 0:
                        break
                city = cities[0]
                images = [entry["question"]["image_id"] for entry in entries]
                if get_city_question(city, images) is None:
                    # A thin or failing city must not stop replenishment elsewhere.
                    self.city_retry_at[city[0]] = time.monotonic() + 5 * 60
                else:
                    self._save()
        except Exception:
            logger.warning("Could not replenish dynamic question pool", exc_info=True)
        finally:
            self._save_statistics()
            with self.lock:
                self.running = False
                self.retry_at = time.monotonic() + max(self.retry_seconds, commons_retry_delay())

    def _deficits(self, entries):
        counts = Counter(entry["question"]["answer"] for entry in entries)
        now = time.monotonic()
        available = [city for city in self.cities if counts[city[0]] < self.target
                     and self.city_retry_at.get(city[0], 0) <= now]
        random.shuffle(available)
        return sorted(available, key=lambda city: (counts[city[0]] >= self.minimum, counts[city[0]]))

    def _save(self):
        self._write(self.path, prepared_question_snapshot())

    def _save_statistics(self):
        try:
            data = photo_statistics.snapshot(prepared_question_snapshot())
            data.update(minimum_per_city=self.minimum, target_per_city=self.target)
            self._write(self.path.with_name("photo-pool-stats.json"), data)
        except OSError:
            logger.warning("Could not save photo pool statistics", exc_info=True)

    @staticmethod
    def _write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
