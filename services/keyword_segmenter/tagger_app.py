"""Modal endpoint serving the fine-tuned keyword tagger.

The pipeline host is small, so inference cannot run beside it -- hence a service.
This one is far cheaper than the vLLM prompting service it replaces:

* **No GPU by default.** ``deepset/gbert-base`` is 110M parameters; a CPU container
  segments the whole column (2341 unique strings) in 134s, measured. The vLLM
  service needed an L40S. See the ``GPU`` constant for when to flip it.
* **Cold start in seconds, not minutes.** 437MB off a Volume and a
  ``from_pretrained``, against a 22GB checkpoint plus engine warm-up and
  ``torch.compile``.
* ``min_containers=0``, so nothing is billed between rebuilds.

Auth is a bearer token from a Modal Secret. The endpoint is public infrastructure,
so it is not left unauthenticated: without a token anyone who finds the URL can
spend the container budget.

    modal secret create fdb-tagger-token TAGGER_TOKEN=$(openssl rand -hex 32)
    modal deploy services/keyword_segmenter/tagger_app.py

The pipeline talks to it through :mod:`segment.client`, which caches by
``md5(revision + keywords)`` so a rerun re-segments only what changed -- and a
redeploy or a retrain changes the revision, so an improvement reaches the column
without anyone clearing a cache by hand.
"""

import hmac
import os

import modal
# Top level, not inside `image.imports()`: `modal deploy` imports this module
# locally to build the app, and the endpoint's `Header(...)` default is evaluated
# then. Deferring it to the container raises NameError at deploy time.
from fastapi import Header, HTTPException

MODEL_DIR = "/tagger/keyword_tagger"
MINUTES = 60

# CPU by default, measured rather than assumed. On the full column (2341 unique
# strings, 17545 words) CPU takes 134s; a T4 would cut the forward passes to a few
# seconds. GPU therefore wins the one-off backfill (~134s -> ~40s once CUDA start
# is counted) and loses every run after it: with the cache warm each export brings
# only a handful of new strings, cold start dominates, and the CPU image is ~1GB
# against ~5GB with CUDA wheels. The recurring case decides the default.
#
# Set GPU = "T4" for a backfill, or if the column ever grows an order of magnitude.
GPU: str | None = None

tagger_vol = modal.Volume.from_name("fdb-keyword-tagger", create_if_missing=True)
token = modal.Secret.from_name("fdb-tagger-token")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "torch==2.9.0",
        "transformers==4.57.1",
        "fastapi[standard]",
        # CPU wheels unless a GPU is requested: this is what keeps the image ~1GB
        # and the cold start short.
        extra_options=(
            "" if GPU else "--extra-index-url https://download.pytorch.org/whl/cpu"
        ),
    )
    .add_local_dir(
        __file__.rsplit("/", 1)[0] + "/segment", "/root/segment", copy=True
    )
)

app = modal.App("fdb-keyword-tagger", image=image)


@app.cls(
    volumes={"/tagger": tagger_vol},
    secrets=[token],
    gpu=GPU,
    cpu=2,
    memory=4096,
    # Cold start on first request, as before: the column is rebuilt rarely, and
    # paying ~20s then is better than paying for an idle container always.
    min_containers=0,
    scaledown_window=5 * MINUTES,
    timeout=30 * MINUTES,
)
@modal.concurrent(max_inputs=8)
class Tagger:
    @modal.enter()
    def load(self) -> None:
        from segment.revision import revision
        from segment.tagger import KeywordTagger

        self.tagger = KeywordTagger(MODEL_DIR, device="cuda" if GPU else "cpu")
        # Reported with every response so a caller can tell whether a segmentation
        # it stored earlier came from this code and this checkpoint.
        # See segment/revision.py for why the client cannot compute it alone.
        self.revision = revision(MODEL_DIR)
        meta_path = f"{MODEL_DIR}/training_meta.json"
        if os.path.exists(meta_path):
            print(f"loaded tagger {self.revision}: {open(meta_path).read()}")

    @staticmethod
    def _authorised(header: str | None) -> bool:
        expected = os.environ["TAGGER_TOKEN"]
        # "Bearer " tolerated but not required, so either convention works.
        supplied = (header or "").removeprefix("Bearer ").strip()
        # compare_digest: constant time, so a wrong token cannot be guessed byte
        # by byte from response timing.
        return bool(supplied) and hmac.compare_digest(supplied, expected)

    @modal.fastapi_endpoint(method="POST", docs=True)
    def segment(self, body: dict, x_tagger_token: str = Header(default=None)):
        """POST {"keywords": ["Erneuerbare Energien Zuschuss", ...]}

        -> {"results": [{"terms": [...], "group_sizes": [...]}, ...]}

        Requires `X-Tagger-Token: <TAGGER_TOKEN>`.

        Two things this signature is working around:

        * The header is a typed `Header` dependency, not a field pulled off an
          untyped `request` argument -- FastAPI treats an unannotated parameter as a
          query parameter, so the header never arrives and every call 401s.
        * The header is `X-Tagger-Token` and not `Authorization`, because Modal's
          web-endpoint proxy reserves `Authorization` for its own auth and the value
          does not reach the handler. Verified: the secret matched byte for byte
          while the header arrived empty.
        """
        if not self._authorised(x_tagger_token):
            # 401 with no detail: a verbose error tells a prober what to fix.
            raise HTTPException(status_code=401, detail="unauthorised")

        values = body.get("keywords") or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise HTTPException(status_code=422, detail="keywords must be a list of strings")
        # A cap, so one request cannot occupy the container for half an hour. The
        # client chunks; see segment/client.py.
        if len(values) > 512:
            raise HTTPException(status_code=413, detail="at most 512 values per request")

        # An empty list is a legitimate request, not a mistake: it is how the client
        # asks for the revision before it decides which values are still cached.
        return {
            "model": "deepset/gbert-base",
            "revision": self.revision,
            "results": self.tagger.segment(values),
        }

    @modal.method()
    def segment_batch(self, values: list[str]) -> list[dict]:
        """In-cluster entry point, for callers that already hold Modal credentials."""
        return self.tagger.segment(values)
