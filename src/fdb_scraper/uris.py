"""Every URL this dataset publishes, in one place.

Two kinds of string live here and they are not interchangeable:

*identifiers*
    :data:`BASE`, :data:`VOCAB` and :data:`DATASET`. What a dataset, a
    distribution, an agent or a minted property *is called*.

*locations*
    :data:`DATASET_DOC_URL`, :data:`SCHEMA_URL`, :data:`DOWNLOAD_BASE`. Where the
    bytes are fetched from.

Both kinds resolve against :data:`HOST`, deliberately: the identifiers have to be
dereferenceable to be worth minting, and the only server that answers for them is
the one this repo deploys. ``Caddyfile`` serves ``dcat/`` at the server root and
negotiates ``/def/`` and ``/id/`` there, which is what makes ``/def/fdb`` return
the vocabulary rather than a 404.

These are hardcoded rather than read from the environment on purpose. ``dcat/`` is
generated *and committed* -- an environment variable would make the committed
artefact depend on whoever last ran the generator, and the tests assert the exact
URIs because a wrong one is not a runtime error, it is a published mistake. A
change here shows up as a reviewable diff across ``dcat/`` and the tests, which is
the intended amount of friction for changing an identifier.
"""

from __future__ import annotations

# The deployed host. 
HOST = "https://fdb.cdl.correlaid.org"

# --- Identifiers (permanent) -------------------------------------------------

# Namespace for everything this dataset names: datasets, distributions, agents,
# individual programmes.
BASE = f"{HOST}/id/"

# The one dataset this repository publishes. Stated here rather than in the
# generator because it is an identifier, and because it is also the last path
# segment of the document an aggregating catalogue harvests -- the two must not be
# spelled separately.
DATASET_ID = "foerderdatenbank-programme"
DATASET = f"{BASE}dataset/{DATASET_ID}"

# Namespace for terms no established vocabulary describes -- see
# :mod:`fdb_scraper.semantics`. A hash namespace, so ``dcat/def/fdb.ttl`` served
# at ``/def/fdb`` makes every term in it dereferenceable.
VOCAB = f"{HOST}/def/fdb#"

# --- Locations ---------------------------------------------------------------

# The whole harvesting interface: a self-contained description of the one dataset,
# with its distributions, publisher and contact point in the same graph. This
# repository publishes no ``dcat:Catalog`` -- the catalogue that lists this dataset
# alongside the rest of the Civic Data Lab's is built and served elsewhere, and it
# fetches this document. Named with the extension rather than relying on the
# content-negotiated ``/id/dataset/<id>``, so a harvester that sends no Accept
# header still gets Turtle.
DATASET_DOC_URL = f"{DATASET}.ttl"
# The CSVW column contract, referenced from the distribution with dct:conformsTo.
SCHEMA_URL = f"{HOST}/table-schema.json"
# Where scripts/build_dist.py writes the published files.
DOWNLOAD_BASE = f"{HOST}/data/"
