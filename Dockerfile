# Image for the weekly pipeline. Not a service -- it runs, writes, and exits.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Dependencies before source, so an edit to src/ does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
COPY scripts/ ./scripts/
# Only the endpoint client, not the service: fdb_scraper.history imports it by path
# to segment new keywords values. The rest of services/keyword_segmenter is torch,
# the Modal app and the training data, none of which belongs in this image.
COPY services/keyword_segmenter/segment/ ./services/keyword_segmenter/segment/
# pyproject declares readme = "README.md", so the build backend needs it present.
COPY README.md ./
# build_dist.py copies this beside the CSV it writes, so a consumer who downloads
# the data from the served tree can reach the licence from there. Absent from the
# image, every publish run dies after the pipeline has already done its work.
COPY LICENSE_DATA ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# Where fdb_scraper.history looks for the segmenter client. Stated rather than left
# to the path derivation, which assumes the package sits beside services/.
ENV FDB_SEGMENTER_DIR=/app/services/keyword_segmenter

# Not root. Everything this writes lands on a bind mount shared with the file
# server, and root-owned files there are awkward to back up, prune or hand to
# rsync. Fixed uid so the host directory can be chowned to match once:
#   sudo install -d -o 1000 -g 1000 /data/fdb/public /data/fdb/state
RUN useradd --uid 1000 --create-home --shell /bin/bash fdb
USER fdb

# The default target. The Coolify scheduled task overrides it with the same
# command plus --out, which is the only argument that differs per host.
CMD ["python", "scripts/build_dist.py", "--out", "/srv/public"]
