# Data Pipeline Quality, Lineage & Observability Platform

A production-style data observability platform for dbt pipelines using OpenLineage + Marquez, dbt + Elementary, Python/FastAPI, PostgreSQL, SLA monitoring, data contracts, and alerting.

This portfolio version adds a focused **incident-management and lineage blast-radius layer** on top of the upstream observability stack. Quality, contract, and SLA failures can now become durable incidents with severity, timestamps, affected-row counts, and downstream impact analysis.

## Architecture

```text
                 dbt models / warehouse
                         |
            +------------+-------------+
            |                          |
      dbt + Elementary           OpenLineage events
      quality/anomalies                |
            |                       Marquez
            |                          |
     contracts + SLA -----------------+
            |
            v
      Incident Processor
            |
            +--> severity: LOW / HIGH / CRITICAL
            +--> affected dataset + failure type
            +--> affected rows
            +--> detected_at / resolved_at
            +--> Marquez lineage lookup
                         |
                         v
               downstream blast radius
              models / datasets / dashboards*
                         |
                         v
             PostgreSQL observability schema
                         |
                         v
                 FastAPI incident API
```

`*` Dashboards are included when they are represented as lineage assets in Marquez.

## What this portfolio version adds

- Durable `observability.incidents` records.
- `LOW`, `HIGH`, and `CRITICAL` incident severity classification.
- Lineage-based downstream impact lookup through the Marquez API.
- Persisted impacted assets in `observability.incident_impacts`.
- Automatic incident creation for:
  - dbt / Elementary quality-test failures;
  - data-contract violations;
  - SLA freshness breaches;
  - pipeline-run failures.
- Automatic resolution of SLA incidents when the dataset recovers.
- FastAPI endpoints to list, inspect, and resolve incidents.

The goal is deliberately narrow: **turn observability signals into actionable incidents and show the blast radius of a data failure.**

## Severity rules

The rules are intentionally explainable rather than ML-based:

| Severity | Rule |
|---|---|
| `CRITICAL` | 5+ downstream impacted assets or 1,000+ affected rows |
| `HIGH` | SLA/contract/pipeline failure, 2+ downstream assets, or 100+ affected rows |
| `LOW` | Isolated quality/anomaly failure below those thresholds |

See [`docs/incident_management.md`](docs/incident_management.md) for the full incident workflow.

## Core stack

| Concern | Technology |
|---|---|
| Lineage emission & storage | OpenLineage (`dbt-ol`) + Marquez |
| Quality checks & anomaly detection | dbt + Elementary |
| Data contracts | YAML + Pydantic |
| SLA / freshness monitoring | Python + APScheduler |
| Incident processing | Python + SQLAlchemy + Marquez REST API |
| Incident / freshness API | FastAPI |
| Metadata store | PostgreSQL |
| Alerting | Slack / email |
| Local deployment | Docker Compose + Makefile |

## Quick start

```bash
cp .env.example .env
# Fill in alerting values if you want Slack/email delivery.

docker compose up -d
make setup
make run
```

`make setup` validates the Marquez API, so start the Docker services before running it.

Local interfaces:

- Marquez API: `http://localhost:5000`
- Marquez UI: `http://localhost:3000`
- Observability / Incident API: `http://localhost:8080`
- Elementary report: `http://localhost:8081`
- FastAPI docs: `http://localhost:8080/docs`

## Incident API examples

List open incidents:

```bash
curl 'http://localhost:8080/incidents?open_only=true'
```

Inspect one incident and its stored blast radius:

```bash
curl 'http://localhost:8080/incidents/<incident_id>'
```

Resolve an incident:

```bash
curl -X POST 'http://localhost:8080/incidents/<incident_id>/resolve'
```

## Example warehouse queries

Open incidents by severity:

```sql
SELECT
    severity,
    COUNT(*) AS open_incidents
FROM observability.incidents
WHERE resolved_at IS NULL
GROUP BY severity
ORDER BY
    CASE severity
        WHEN 'CRITICAL' THEN 1
        WHEN 'HIGH' THEN 2
        ELSE 3
    END;
```

Most disruptive incidents:

```sql
SELECT
    incident_id,
    dataset,
    failure_type,
    severity,
    affected_rows,
    downstream_impact_count,
    detected_at
FROM observability.incidents
ORDER BY downstream_impact_count DESC, detected_at DESC
LIMIT 20;
```

Blast radius for a specific incident:

```sql
SELECT
    i.dataset AS failed_dataset,
    i.severity,
    impact.asset_type,
    impact.asset_namespace,
    impact.asset_name,
    impact.depth
FROM observability.incidents i
JOIN observability.incident_impacts impact
  ON i.incident_id = impact.incident_id
WHERE i.incident_id = '<incident_id>'
ORDER BY impact.depth, impact.asset_type, impact.asset_name;
```

## Testing

```bash
make test
```

The added tests cover severity classification and downstream lineage traversal in addition to the upstream contract, freshness, alert, anomaly, and lineage tests.

## Repository hygiene

`.env`, Python caches, dbt targets/packages/logs, Elementary generated output, and runtime reports are ignored. Commit `.env.example`, never a real `.env` containing credentials or Slack webhooks.


