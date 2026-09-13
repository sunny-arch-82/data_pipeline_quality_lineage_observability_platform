# Upstream attribution

This repository is a modified portfolio version of the MIT-licensed
`isinghabhishek/data-observability-platform` project by Abhishek Singh.

Upstream project:
https://github.com/isinghabhishek/data-observability-platform

The original MIT copyright and permission notice are preserved in `LICENSE`.

## Portfolio modifications

This version adds an incident-management layer focused on production data
operations:

- incident records with dataset, failure type, severity, affected rows,
  detection time, resolution time, and downstream impact count;
- deterministic LOW / HIGH / CRITICAL severity classification;
- Marquez lineage lookups for downstream blast-radius analysis;
- persisted impacted assets for affected models, datasets, and dashboards when
  those assets are represented in the lineage graph;
- incident creation from dbt/Elementary quality failures, contract violations,
  and SLA breaches;
- incident API endpoints for listing, inspecting, and resolving incidents.

Other upstream components and third-party project names (dbt, Elementary,
OpenLineage, Marquez, FastAPI, PostgreSQL, etc.) remain their respective
projects and trademarks.
