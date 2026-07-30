# DCAT-AP.de 3.0 SHACL shapes

Vendored verbatim so `tests/test_dcat.py` runs offline, and so the same profile
gates this repository and the aggregating catalogue (`CATALOGUE.md`), which vendors
the same set. `test_dcat.SHAPE_FILES` lists the five that are used.

The profile is **two** upstreams, per
[DCAT-AP.de-SHACL-Validation](https://github.com/GovDataOfficial/DCAT-AP.de-SHACL-Validation)'s
README: SEMIC's DCAT-AP 3.0 shapes, plus the German files that translate, restrict
and extend them. Loading only the German ones checks almost nothing --
`dcat-ap-SHACL-DE.ttl` deactivates and adds shapes rather than restating the base,
so on its own even an empty `dcat:Dataset` passes.

| File | From | Role |
| --- | --- | --- |
| `dcat-ap-SHACL.ttl` | [SEMICeu/DCAT-AP](https://github.com/SEMICeu/DCAT-AP), `releases/3.0.0/shacl/` | the base profile: classes, cardinalities, ranges |
| `dcat-ap-SHACL-DE.ttl` | DCAT-AP.de-SHACL-Validation, `validator/resources/v3.0/shapes/` | German messages; deactivates and adds shapes on top of the base |
| `dcat-ap-de-controlledvocabularies.ttl` | same | which controlled vocabulary each property must draw from |
| `dcat-ap-spec-german-additions.ttl` | same | the `dcatde:` properties -- `politicalGeocodingLevelURI`, `licenseAttributionByText`, `contributorID` |
| `dcat-ap-de-deprecated.ttl` | same | warnings for the properties and codelists 3.0 retired |

One file from that directory is deliberately not vendored:

- `dcat-ap-de-imports.ttl` -- no shapes of its own, only `owl:imports` of remote
  vocabularies (EU authority tables, the DCAT-AP.de licence and politicalGeocoding
  lists, SPDX, ADMS). Fetching those would make the test need the network; leaving
  them out means the `skos:inScheme` membership shapes cannot be decided, which
  `test_dcat.py` excludes explicitly.

`dcatde:contributorID` needs no separate profile in 3.0: the German additions only
require it to be an IRI *if present*, so the GovData-delivery convention costs
nothing here. Under 2.0 that was a separate `dcat-ap-konventionen.ttl`.

For the complete check, including the vocabulary membership this cannot do, put
`dcat/id/dataset/foerderdatenbank-programme.ttl` through
<https://www.itb.ec.europa.eu/shacl/dcat-ap.de/upload> before publishing, against
the 3.0 profile.

Refresh by re-downloading from the two paths above; there is no generator script,
these are upstream files.
