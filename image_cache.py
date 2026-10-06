"""Bounded, optional disk cache for Commons thumbnails; never selects questions."""

import hashlib
import logging
from pathlib import Path
from queue import Full, Queue
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
ALLOWED_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class ThumbnailCache:
    def __init__(self, directory, *, max_bytes=256 * 1024 * 1024,
                 max_file_bytes=8 * 1024 * 1024, ttl_seconds=7 * 24 * 3600):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self.ttl_seconds = ttl_seconds
        self.queue = Queue(maxsize=64)
        self.lock = Lock()
        self.pending = set()
        self.retry_at = {}
        self.worker = None

    @staticmethod
    def key(url):
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    @staticmethod
    def allowed(url):
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS
                and parts.port in {None, 443} and not parts.username and not parts.password)

    def find(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            return None
        for suffix in MIME_SUFFIXES.values():
            path = self.directory / (key + suffix)
            try:
                if path.is_file() and time.time() - path.stat().st_mtime < self.ttl_seconds:
                    return path
            except OSError:
                pass
        return None

    def enqueue(self, question):
        url = question.get("image_url", "")
        try:
            if not question.get("is_dynamic") or not self.allowed(url):
                return
        except (ValueError, TypeError):
            return
        key = self.key(url)
        if self.find(key):
            return
        with self.lock:
            now = time.monotonic()
            self.retry_at = {k: until for k, until in self.retry_at.items() if until > now}
            if key in self.pending or key in self.retry_at:
                return
            try:
                self.queue.put_nowait((key, url))
            except Full:
                return
            self.pending.add(key)
            if self.worker is None or not self.worker.is_alive():
                self.worker = Thread(target=self._work, name="city-thumbnail", daemon=True)
                self.worker.start()

    def _work(self):
        while True:
            key, url = self.queue.get()
            try:
                self._download(key, url)
            except Exception:
                logger.debug("Thumbnail cache download failed: %s", key, exc_info=True)
                with self.lock:
                    self.retry_at[key] = time.monotonic() + 5 * 60
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.queue.task_done()

    def _download(self, key, url):
        if self.find(key):
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            request = Request(url, headers={"User-Agent": "CityGuesser/1.0 (educational city photo game)"})
            with urlopen(request, timeout=4) as response:
                if not self.allowed(response.geturl()):
                    raise ValueError("Unexpected thumbnail redirect")
                mime = response.headers.get_content_type()
                if mime not in MIME_SUFFIXES:
                    raise ValueError("Unsupported thumbnail type")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > self.max_file_bytes:
                    raise ValueError("Thumbnail exceeds size limit")
                started = time.monotonic()
                total = 0
                with NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_file_bytes or time.monotonic() - started > 15:
                            raise ValueError("Thumbnail download exceeded budget")
                        output.write(chunk)
                if total == 0 or (declared and total != int(declared)):
                    raise ValueError("Incomplete thumbnail")
                # Check signatures so an HTML error cannot become a cached image.
                with temporary.open("rb") as saved:
                    header = saved.read(12)
                valid = ((mime == "image/jpeg" and header.startswith(b"\xff\xd8\xff"))
                         or (mime == "image/png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
                         or (mime == "image/webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP"))
                if not valid or total > self.max_bytes:
                    raise ValueError("Invalid thumbnail")
                if not self._prune(total):
                    raise OSError("Thumbnail cache is at capacity")
                temporary.replace(self.directory / (key + MIME_SUFFIXES[mime]))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prune(self, incoming):
        files = []
        now = time.time()
        for suffix in MIME_SUFFIXES.values():
            for path in self.directory.glob("*" + suffix):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                if now - stat.st_mtime >= self.ttl_seconds:
                    try:
                        path.unlink(missing_ok=True)
                        continue
                    except OSError:
                        pass  # Windows can retain a file currently being served.
                files.append((stat.st_mtime, stat.st_size, path))
        size = sum(item[1] for item in files)
        for _, length, path in sorted(files):
            if size + incoming <= self.max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
                size -= length
            except OSError:
                continue
        return size + incoming <= self.max_bytes
