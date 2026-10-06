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
    candidate_cache_generation, candidate_photo_snapshot, restore_candidate_photos,
)
import photo_statistics
from photo_rules import photo_id

logger = logging.getLogger(__name__)


class DynamicQuestionPool:
    def __init__(self, path, *, cities=None, minimum=12, target=24, batch_size=24,
                 retry_seconds=30, request_interval=3, prepare_image=None):
        self.path = Path(path)
        self.candidate_path = self.path.with_name("photo-candidates.json")
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
        self.stop_event = Event()
        self.pause = self.stop_event.wait
        self.worker = None
        self.saved_candidate_generation = -1
        self.prepare_image = prepare_image
        self.candidate_turns = 0
        self.last_city = None

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
                try:
                    # Keep a corrupt or unexpectedly large file from delaying play.
                    if self.candidate_path.stat().st_size > 20 * 1024 * 1024:
                        raise ValueError("Candidate cache exceeds size limit")
                    restore_candidate_photos(json.loads(self.candidate_path.read_text(encoding="utf-8")))
                except FileNotFoundError:
                    pass
                except (OSError, ValueError):
                    logger.warning("Could not load photo candidate cache", exc_info=True)
                self.loaded = True
            now = time.monotonic()
            if now >= self.statistics_at:
                self._save_statistics()
                self.statistics_at = now + 60
            if self.worker is not None or self.running or now < self.retry_at or self.stop_event.is_set():
                return
            entries = prepared_question_snapshot()
            if not self._deficits(entries):
                return
            self.running = True
            self.worker = Thread(target=self._run, name="city-pool", daemon=True)
            self.worker.start()

    def stop(self, timeout=5):
        """Interrupt idle/between-attempt waits; an active HTTP call finishes first."""
        self.stop_event.set()
        with self.lock:
            worker = self.worker
        if worker is not None:
            worker.join(timeout=timeout)

    def _run(self):
        """Continue bounded batches without needing another browser request."""
        try:
            while not self.stop_event.is_set():
                delay = max(0, self.retry_at - time.monotonic(), commons_retry_delay())
                if delay:
                    self.pause(delay)
                    continue
                if not self._deficits(prepared_question_snapshot()):
                    self.pause(60)
                    continue
                with self.lock:
                    self.running = True
                self._refill()
        finally:
            with self.lock:
                self.running = False
                self.worker = None

    def _refill(self):
        active_city = None
        city_attempts = 0
        city_ceiling = self.target
        try:
            for attempt in range(self.batch_size):
                if self.stop_event.is_set() or commons_retry_delay() > 0:
                    break
                entries = prepared_question_snapshot()
                cities = self._deficits(entries)
                if not cities:
                    break
                if attempt:
                    if self.pause(self.request_interval) or commons_retry_delay() > 0:
                        break
                counts = Counter(entry["question"]["answer"] for entry in entries)
                cached = self._candidate_cities(entries)
                # Keep a short run on freshly discovered or restored candidates.
                # Recheck stock and freshness before each attempt, including after
                # another request has changed the shared inventory.
                if (active_city not in cities or city_attempts >= 3
                        or active_city[0] not in cached
                        or counts[active_city[0]] >= city_ceiling):
                    active_city = self._select_city(cities, counts, cached)
                    city_attempts = 0
                    city_ceiling = self.minimum if counts[active_city[0]] < self.minimum else self.target
                city = active_city
                city_attempts += 1
                images = [entry["question"]["image_id"] for entry in entries]
                question = get_city_question(city, images)
                if question is None:
                    if commons_retry_delay() > 0:
                        # Global backoff is not a failure of this particular city.
                        break
                    # A thin or failing city must not stop replenishment elsewhere.
                    self.city_retry_at[city[0]] = time.monotonic() + 5 * 60
                    active_city = None
                else:
                    if self.prepare_image:
                        self.prepare_image(question)
                    self._save()
        except Exception:
            logger.warning("Could not replenish dynamic question pool", exc_info=True)
        finally:
            self._save_candidates()
            self._save_statistics()
            with self.lock:
                self.running = False
                self.retry_at = time.monotonic() + max(self.retry_seconds, commons_retry_delay())

    def _candidate_cities(self, entries):
        """Cities with fresh candidates that are not already in ready stock."""
        stocked = {entry["question"]["image_id"] for entry in entries}
        now = time.time()
        return {name for name, entry in candidate_photo_snapshot()["cities"].items()
                if entry["expires_at"] > now and any(
                    photo_id(photo["title"]) not in stocked for photo in entry["photos"])}

    def _select_city(self, cities, counts, cached):
        # Empty cities get immediate coverage. Every fourth city turn uses the
        # normal lowest-stock order even when other cities have cached candidates.
        empty = [city for city in cities if counts[city[0]] == 0]
        reusable = [city for city in cities if city[0] in cached]
        if empty:
            choices = empty
        elif reusable and self.candidate_turns < 3:
            choices = reusable
            self.candidate_turns += 1
        else:
            choices = cities
            self.candidate_turns = 0
        alternatives = [city for city in choices if city[0] != self.last_city]
        city = (alternatives or choices)[0]
        self.last_city = city[0]
        return city

    def _deficits(self, entries):
        counts = Counter(entry["question"]["answer"] for entry in entries)
        now = time.monotonic()
        available = [city for city in self.cities if counts[city[0]] < self.target
                     and self.city_retry_at.get(city[0], 0) <= now]
        random.shuffle(available)
        return sorted(available, key=lambda city: (counts[city[0]] >= self.minimum, counts[city[0]]))

    def _save(self):
        self._write(self.path, prepared_question_snapshot())
        self._save_candidates()

    def _save_candidates(self):
        if candidate_cache_generation() == self.saved_candidate_generation:
            return
        try:
            data = candidate_photo_snapshot()
            self._write(self.candidate_path, data)
            self.saved_candidate_generation = data["generation"]
        except (OSError, ValueError):
            logger.warning("Could not save photo candidate cache", exc_info=True)

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
