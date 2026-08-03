"""What produced a segmentation, as one short string.

A cached segmentation is only reusable while the thing that produced it is
unchanged, and "the thing that produced it" is not just the checkpoint: the
published terms are a function of the weights *and* the code that decodes them. When
the function-word repair was added, every cached row silently became stale -- the cache
was keyed on ``md5(keywords)`` alone, so a rerun sent nothing and would have republished
the pre-repair splits. Nothing detected that; a person had to remember. This module is
what replaces remembering.

Two components, because they are knowable in different places:

``code_revision``
    Hashes the files that turn probabilities into terms. Computable by the client
    from its own checkout, since the pipeline and the deployed service are built
    from the same repository.

``weights_revision``
    Hashes ``training_meta.json``, which the training run writes beside the
    checkpoint and which carries the seed, the epoch count and the measured scores
    -- so a retrain changes it even when the base model does not. Only the
    container can see it, so the endpoint reports it and the client takes its word.

The endpoint returns the pair as ``revision``; the client keys its cache on it and the
pipeline stores it per row. A code change or a retrain invalidates by construction rather
than by discipline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).parent

# Everything downstream of the model's probabilities. `client.py` is deliberately
# absent: how a result is transported and cached does not change what the result
# is, and including it would invalidate every row on an unrelated edit.
DECISION_SURFACE = ("tagger.py", "tokens.py", "postprocess.py")
UNKNOWN = "unknown"
_DIGEST_CHARS = 12


def _digest(chunks: list[bytes]) -> str:
    h = hashlib.md5()
    for chunk in chunks:
        h.update(hashlib.md5(chunk).digest())
    return h.hexdigest()[:_DIGEST_CHARS]


def code_revision(directory: str | Path = HERE) -> str:
    """Fingerprint of the code that turns probabilities into terms, in a fixed order."""
    directory = Path(directory)
    chunks = []
    for name in DECISION_SURFACE:
        path = directory / name
        # A missing file is its own revision rather than an error: the caller may be
        # fingerprinting a directory that does not hold a full checkout.
        chunks.append(path.read_bytes() if path.exists() else b"")
    return _digest(chunks)


def weights_revision(model_dir: str | Path | None) -> str:
    """Fingerprint of the trained checkpoint, via the metadata the trainer writes.

    The metadata rather than the 437MB of weights: it changes on every retrain
    (seed, epochs and held-out scores are all in it) and costs no I/O worth
    measuring at container start.
    """
    if model_dir is None:
        return UNKNOWN
    meta = Path(model_dir) / "training_meta.json"
    return _digest([meta.read_bytes()]) if meta.exists() else UNKNOWN


def revision(model_dir: str | Path | None = None, directory: str | Path = HERE) -> str:
    """``<code>.<weights>``, the string a cache entry is keyed on."""
    return f"{code_revision(directory)}.{weights_revision(model_dir)}"
