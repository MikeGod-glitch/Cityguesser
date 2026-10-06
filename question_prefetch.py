"""Thread-safe background question tasks, independent of Flask sessions."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Lock
import time


class QuestionPrefetch:
    def __init__(self, *, workers=4, ttl_seconds=30 * 60):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="city-question")
        self.tasks = {}
        self.lock = Lock()
        self.ttl_seconds = ttl_seconds

    def start(self, player_id, fetch, recent_images, recent_cities, conflicts):
        """Retain pending or usable tasks; replace failed, expired, or conflicting ones."""
        now = time.monotonic()
        with self.lock:
            for stale_id, (future, created_at) in list(self.tasks.items()):
                if now - created_at > self.ttl_seconds:
                    future.cancel()
                    self.tasks.pop(stale_id, None)

            existing = self.tasks.get(player_id)
            if existing:
                future = existing[0]
                if not future.done():
                    return
                try:
                    question = future.result()
                    if question is not None and not conflicts(question):
                        return
                except Exception:
                    # A failed background task must not interrupt foreground play.
                    pass
                self.tasks.pop(player_id, None)

            future = self.executor.submit(fetch, recent_images, recent_cities)
            self.tasks[player_id] = (future, now)

    def get(self, player_id, conflicts, *, wait_seconds=0, consume=False):
        """Check against current history even for peeks; keep timed-out tasks alive."""
        with self.lock:
            entry = self.tasks.get(player_id)
        if not entry:
            return None

        try:
            question = entry[0].result(timeout=wait_seconds)
        except FutureTimeout:
            return None
        except Exception:
            question = None

        if question is not None and conflicts(question):
            question = None
        if consume or question is None:
            with self.lock:
                # Do not remove a task another request installed in the meantime.
                if self.tasks.get(player_id) == entry:
                    self.tasks.pop(player_id, None)
        return question

    def cancel(self, player_id):
        with self.lock:
            entry = self.tasks.pop(player_id, None)
        if entry:
            entry[0].cancel()
