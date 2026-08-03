"""Client for the tagger endpoint, with a content-addressed cache.

The pipeline host is too small to run the model, so segmentation is a network call.
That makes a cache load-bearing rather than a nicety:

* **Repeated runs cost nothing.** A rerun after a processing fix re-segments only
  the strings that changed -- typically none.
* **``publish()`` stays deterministic and offline.** Model inference is not
  bit-reproducible across container generations or batch composition, so a
  published column should be read from a materialised result rather than recomputed.
  Same reason the raw fields are stored rather than refetched.
* **A new export is cheap.** 2440 rows collapse to 2341 unique strings; on the
  second export only genuinely new keyword values are sent.

The key is ``md5(revision + keywords)``: the exact raw string, so any upstream edit
is a miss, *and* the segmenter revision, so a code change or a retrained checkpoint is
a miss too. The second half was learned the hard way -- keyed on the string alone,
adding the function-word repair left 2340 stale rows that a rerun had no reason to
touch, and the fix reached nothing until someone deleted the cache by hand. See
:mod:`segment.revision`.

    export FDB_TAGGER_URL=https://...modal.run
    export FDB_TAGGER_TOKEN=...
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CACHE = Path("data/keyword_cache.sqlite")
# The endpoint caps a request at 512 values; stay under it.
CHUNK = 256


def fingerprint(keywords: str) -> str:
    """Content hash of one raw value. The pipeline's primary key, revision-free.

    A new revision must *replace* a stored segmentation rather than sit beside it,
    so the row identity stays the string alone and the revision travels as a column.
    The local cache is the opposite case -- see :func:`cache_key`.
    """
    return hashlib.md5(keywords.encode()).hexdigest()


def cache_key(keywords: str, revision: str) -> str:
    """Cache identity: the value *and* what would segment it.

    Keeping both revisions addressable is what makes a rollback cheap -- reverting
    the code restores its cache entries instead of paying for the column again.
    """
    return hashlib.md5(f"{revision}\n{keywords}".encode()).hexdigest()


class Cache:
    """sqlite, because it ships with Python and this is one table."""

    def __init__(self, path: str | Path = DEFAULT_CACHE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS segments (
                   md5 TEXT PRIMARY KEY,
                   keywords TEXT NOT NULL,
                   result TEXT NOT NULL,
                   model TEXT,
                   created_at REAL NOT NULL,
                   revision TEXT
               )"""
        )
        # Pre-revision caches exist in deployments. Their rows are keyed on
        # md5(keywords) and can never collide with a revision-keyed lookup, so they
        # are simply unreachable rather than wrong -- adding the column is enough.
        if "revision" not in {row[1] for row in self.db.execute("PRAGMA table_info(segments)")}:
            self.db.execute("ALTER TABLE segments ADD COLUMN revision TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # Context-manager support so callers are not obliged to remember close(); the
    # test suite runs with warnings as errors, which an unclosed handle trips.
    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_many(self, values: list[str], revision: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        keys = [cache_key(v, revision) for v in values]
        for start in range(0, len(keys), 500):  # sqlite parameter limit
            chunk = keys[start : start + 500]
            rows = self.db.execute(
                f"SELECT keywords, result FROM segments WHERE md5 IN ({','.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
            out.update({kw: json.loads(res) for kw, res in rows})
        return out

    def put_many(self, pairs: list[tuple[str, dict]], model: str, revision: str) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO segments VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    cache_key(kw, revision),
                    kw,
                    json.dumps(res, ensure_ascii=False),
                    model,
                    time.time(),
                    revision,
                )
                for kw, res in pairs
            ],
        )
        self.db.commit()


class TaggerClient:
    """Segment keyword strings via the endpoint, reading through a local cache."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        cache: Cache | None = None,
        timeout: int = 600,
        retries: int = 3,
    ):
        self.url = url or os.environ.get("FDB_TAGGER_URL", "")
        self.token = token or os.environ.get("FDB_TAGGER_TOKEN", "")
        if not self.url or not self.token:
            raise ValueError("set FDB_TAGGER_URL and FDB_TAGGER_TOKEN (or pass them)")
        self.cache = cache if cache is not None else Cache()
        self.timeout = timeout
        self.retries = retries
        self._revision: str | None = None

    def close(self) -> None:
        self.cache.close()

    def __enter__(self) -> "TaggerClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _post(self, values: list[str]) -> dict:
        payload = json.dumps({"keywords": values}).encode()
        for attempt in range(self.retries):
            request = urllib.request.Request(
                self.url,
                data=payload,
                headers={
                    "content-type": "application/json",
                    # Not `Authorization`: Modal's web-endpoint proxy reserves it
                    # and the value never reaches the handler.
                    "x-tagger-token": self.token,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                # 401/413/422 are our fault and will not fix themselves; only a
                # cold start or a transient 5xx is worth waiting for.
                if exc.code < 500:
                    raise
                if attempt == self.retries - 1:
                    raise
            except urllib.error.URLError:
                if attempt == self.retries - 1:
                    raise
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def revision(self) -> str:
        """What the endpoint is currently serving, asked once per client.

        A request with no values: the endpoint answers with its revision and does no
        work. It costs one round trip, which also warms the container that the first
        real chunk would otherwise have waited on -- and the client cannot derive
        this locally, because the checkpoint lives on a Volume it never reads.

        An endpoint too old to report one leaves the cache keyed as it was before,
        which is the pre-revision behaviour and no worse than it.
        """
        if self._revision is None:
            self._revision = self._post([]).get("revision", "unknown")
        return self._revision

    def segment(self, values: list[str], use_cache: bool = True) -> list[dict]:
        """Segment many values, in input order. Only cache misses hit the network."""
        if not values:
            return []
        revision = self.revision()
        cached = self.cache.get_many(values, revision) if use_cache else {}
        # Deduplicate as well as cache: the column repeats, and a duplicate is a
        # wasted call even on a cold cache.
        missing = list(dict.fromkeys(v for v in values if v not in cached))

        for start in range(0, len(missing), CHUNK):
            chunk = missing[start : start + CHUNK]
            body = self._post(chunk)
            results = body["results"]
            if len(results) != len(chunk):
                raise RuntimeError(f"asked for {len(chunk)} segments, got {len(results)}")
            pairs = list(zip(chunk, results))
            cached.update(dict(pairs))
            if use_cache:
                # The revision from this very response, not the probed one: if the
                # service were redeployed mid-run, storing these under the old key
                # would cache new answers as old ones.
                self.cache.put_many(
                    pairs, body.get("model", "unknown"), body.get("revision", revision)
                )

        return [cached[v] for v in values]
