# Vendored schema

`ontology-graph-spec-1.1.0.schema.json` is the Neo4j Ontology Graph Specification JSON Schema, version
1.1.0, copied unmodified from <https://github.com/neo4j/graph-spec> (`ontology-graph-spec.schema.json`
at commit `5f955cffa922`). It is licensed under the Apache License 2.0; see the upstream `LICENSE`.

It is vendored so that `app.kernel.schema_export.validate_ogs` checks the generated kernel document
against the published schema offline and in CI. Update it by copying the upstream file again and
running `App/backend/tests/test_kernel_schema.py`.
