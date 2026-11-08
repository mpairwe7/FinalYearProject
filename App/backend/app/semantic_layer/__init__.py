"""Staff-only data semantic layer over URA's analytical data (ADR-0033, phase 3).

The kernel holds what the law says. This layer answers what the data shows,
for URA staff only: certified, aggregate metrics over certified views, never
taxpayer-level rows and never free SQL.

* :mod:`.catalogue`: the certified metrics, each with an owner, a fixed SQL
  template, typed parameters and a minimum group size.
* :mod:`.guard`: every template must be one SELECT over its declared sources.
* :mod:`.adapter`: runs a metric read-only, with a row cap and a time cap, and
  suppresses groups below the minimum size.
* :mod:`.config`: the production gate. Off by default (``FLAG_SEMANTIC_LAYER``);
  enabling it in production needs Vault for credentials, a data-sharing
  agreement and a DPIA on record, and at least one certified metric.

This is the integration point an ontology-driven semantic layer (NVIDIA Auto
Ontology or an equivalent) would sit behind: its certified metrics become
catalogue entries. It stays closed until those conditions are met.
"""
