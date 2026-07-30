"""Smoke test for the deployment: the real stack, the real command, real HTTP.

Everything else in the suite runs the pipeline in-process. That leaves the parts
only the containers have: whether the image can run what the Coolify scheduled task
invokes, whether Postgres is reachable by service name, whether Caddy serves what
the pipeline wrote with the right content types, and whether anything that should
not be public is.

Those are the failures that would appear only after deploying, so they are worth a
test that costs a Docker build. Marked ``docker`` and deselected by default::

    uv run pytest -m docker

The fixture export is mounted instead of downloading, so this needs no network after
the images are pulled. ``compose.smoke.yaml`` adds exactly two things -- that mount
and a published port -- so what runs is otherwise the deployment's own configuration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = pytest.mark.docker

ROOT = Path(__file__).parent.parent
PROJECT = "fdbsmoke"
PORT = 18099
BASE = f"http://127.0.0.1:{PORT}"

# The Coolify Scheduled Task, verbatim except for --export-dir. If this drifts from
# the command in docker-compose.yaml, the test stops being a smoke test.
SCHEDULED_TASK = [
    "python",
    "scripts/build_dist.py",
    "--out",
    "/srv/public",
    "--export-dir",
    "/export",
]


def compose(*args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "docker-compose.yaml"),
            "-f",
            str(ROOT / "compose.smoke.yaml"),
            "-p",
            PROJECT,
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """The deployment, brought up against a temporary data directory."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")

    import os

    data = tmp_path_factory.mktemp("fdb-data")
    # Created by hand here because the deployment expects them to exist, chowned to
    # the image's uid. The README says the same for the real host.
    (data / "public").mkdir()
    (data / "state").mkdir()

    env = {
        **os.environ,
        "FDB_DATA": str(data),
        "POSTGRES_PASSWORD": "smoketest",
        "SMOKE_PORT": str(PORT),
        "SERVICE_FQDN_FILES_80": "files.smoketest",
    }

    compose("down", "-v", env=env, check=False)
    try:
        compose("up", "-d", "--build", "--wait", env=env)
        yield {"env": env, "data": data}
    finally:
        compose("down", "-v", env=env, check=False)


@pytest.fixture(scope="module")
def published(stack) -> dict:
    """Run the scheduled task once and return the stack, so assertions can share it."""
    result = compose(
        "exec", "-T", "pipeline", *SCHEDULED_TASK, env=stack["env"], check=False
    )
    assert result.returncode == 0, (
        f"the scheduled-task command failed:\n{result.stdout}\n{result.stderr}"
    )
    stack["output"] = result.stdout
    return stack


class Response(NamedTuple):
    status: int
    content_type: str | None
    body: bytes


def get(path: str, accept: str | None = None) -> Response:
    """One request, with the connection closed before returning.

    The body is read eagerly rather than handing back the response object: the
    suite runs with ``filterwarnings = ["error"]``, and an unclosed socket surfaces
    as a ResourceWarning at interpreter teardown, which fails the run with every
    test still reported as passing.
    """
    request = urllib.request.Request(f"{BASE}{path}")
    if accept:
        request.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return Response(
                response.status, response.headers["Content-Type"], response.read()
            )
    except urllib.error.HTTPError as error:
        # An HTTPError is itself a response and holds the connection open. Closed
        # here and returned as data, so a 404 assertion does not leak a socket into
        # interpreter teardown -- which fails the run while reporting every test as
        # passing.
        with error:
            return Response(error.code, error.headers["Content-Type"], error.read())


def test_the_scheduled_task_command_runs_in_the_image(published) -> None:
    """The exact command Coolify is configured to invoke, in the built image."""
    assert "is ready to publish" in published["output"]
    assert "validated" in published["output"]


def test_the_pipeline_reaches_postgres_by_service_name(published) -> None:
    """The connection string in docker-compose.yaml resolves and authenticates."""
    result = compose(
        "exec",
        "-T",
        "history",
        "psql",
        "-U",
        "fdb",
        "-d",
        "fdb",
        "-tAc",
        "SELECT count(*) FROM foerderdatenbank.programmes WHERE on_website_to IS NULL",
        env=published["env"],
    )
    assert result.stdout.strip() == "3", f"unexpected history: {result.stdout!r}"


def test_caddy_serves_the_csv_with_its_own_media_type(published) -> None:
    """Nothing else in the suite proves the served type; Go's mime map lacks it."""
    response = get("/data/programme.csv")
    assert response.status == 200
    assert response.content_type == "text/csv; charset=utf-8"


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("text/turtle", "text/turtle; charset=utf-8"),
        ("application/ld+json", None),  # no .jsonld is published yet
        ("text/html,*/*", "text/turtle; charset=utf-8"),  # falls back, no .html either
    ],
)
def test_the_vocabulary_uri_negotiates(published, accept: str, expected: str) -> None:
    """``/def/fdb`` has no extension, so only the server can resolve it.

    The published tree carries ``fdb.ttl`` alone, so an Accept that asks for
    something else must still resolve rather than 404 -- which is what the try_files
    fallback in the Caddyfile is for.
    """
    if expected is None:
        pytest.skip("only Turtle is generated today; the branch exists for later")
    response = get("/def/fdb", accept=accept)
    assert response.status == 200
    assert response.content_type == expected
    assert b"fdb:Foerderprogramm" in response.body


def test_the_dataset_uri_resolves(published) -> None:
    response = get("/id/dataset/foerderdatenbank-programme", accept="text/turtle")
    assert response.status == 200
    assert response.content_type == "text/turtle; charset=utf-8"


def test_the_harvestable_document_and_schema_are_served(published) -> None:
    # The extension-qualified path, which is what an aggregating catalogue is
    # configured with: a harvester that sends no Accept header must still get
    # Turtle rather than whatever the negotiation falls back to.
    turtle = get("/id/dataset/foerderdatenbank-programme.ttl")
    assert turtle.status == 200
    assert turtle.content_type == "text/turtle; charset=utf-8"
    schema = json.loads(get("/table-schema.json").body)
    assert schema["tableSchema"]["columns"], "the served schema has no columns"


def test_the_history_is_not_served(published) -> None:
    """The reason only `public` is mounted into Caddy.

    ``file_server browse`` lists whatever it is given, so mounting the data root
    would publish dlt's working directory -- which holds extracted copies of the
    export -- alongside the distributions.
    """
    listing = get("/").body.decode()
    assert "state" not in listing, "the state directory is listed by the file server"

    for leaked in ("/state/", "/state/dlt/", "/state/dlt/pipelines/"):
        assert get(leaked).status == 404, f"{leaked} is reachable"
