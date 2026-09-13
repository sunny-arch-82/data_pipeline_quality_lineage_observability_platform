# Incident Management and Blast-Radius Analysis

## Purpose

The original observability stack detects quality, freshness, contract, and lineage signals. This extension turns those signals into operational incidents that can be triaged and resolved.

```text
quality / SLA / contract / pipeline failure
                  |
                  v
          IncidentManager
                  |
       +----------+-----------+
       |                      |
       v                      v
 severity rules         Marquez lineage
                               |
                               v
                    downstream blast radius
       |                      |
       +----------+-----------+
                  v
         observability.incidents
         observability.incident_impacts
```

## Incident schema

Each incident stores:

- `incident_id`
- `dataset`
- `failure_type`
- `severity`
- `affected_rows`
- `detected_at`
- `resolved_at`
- `downstream_impact_count`
- JSON `details`

Impacted assets are stored separately with namespace, name, type, and lineage depth.

## Blast-radius lookup

The processor queries:

```text
GET /api/v1/lineage?nodeId=dataset:<namespace>:<dataset>&depth=<N>
```

The returned Marquez graph is traversed only in the downstream direction. For every job that consumes the failed dataset, the processor records the job/model and its output datasets, then continues through those outputs.

The resulting assets are persisted with the incident so historical incidents keep the blast radius that was known at detection time.

Dashboards can only be included if dashboard/BI assets are represented in the lineage graph. The implementation labels common dashboard/report names as `DASHBOARD`; other output assets are recorded as `DATASET` and jobs as `MODEL`.

## Duplicate suppression

Repeated polling should not create a new incident every time. `IncidentManager.create_incident()` reuses an existing unresolved incident with the same dataset and failure type.

## Resolution

SLA incidents are automatically resolved when the dataset returns to `FRESH` or `WARNING`. Other incident types can be resolved manually through:

```text
POST /incidents/{incident_id}/resolve
```

The resolution timestamp is stored in `resolved_at`.

## Severity policy

- `CRITICAL`: 5+ downstream assets or 1,000+ affected rows.
- `HIGH`: SLA, contract, or pipeline failure; 2+ downstream assets; or 100+ affected rows.
- `LOW`: isolated quality/anomaly failures below those thresholds.

The policy is intentionally deterministic so an interviewer or operator can explain exactly why an incident was escalated.
