"""Unit tests for incident severity and lineage blast-radius traversal."""

from src.incident_manager import classify_incident_severity, extract_downstream_impacts


def test_severity_low_for_isolated_small_quality_failure():
    assert classify_incident_severity("QUALITY_TEST", 3, 0) == "LOW"


def test_severity_high_for_contract_or_moderate_impact():
    assert classify_incident_severity("CONTRACT_VIOLATION", 0, 0) == "HIGH"
    assert classify_incident_severity("QUALITY_TEST", 5, 2) == "HIGH"
    assert classify_incident_severity("QUALITY_TEST", 100, 0) == "HIGH"


def test_severity_critical_for_large_blast_radius_or_rows():
    assert classify_incident_severity("QUALITY_TEST", 1, 5) == "CRITICAL"
    assert classify_incident_severity("QUALITY_TEST", 1000, 0) == "CRITICAL"


def test_extract_downstream_impacts_walks_only_downstream():
    graph = [
        {
            "id": "job:analytics:stg_orders",
            "type": "JOB",
            "data": {
                "namespace": "analytics",
                "name": "stg_orders",
                "inputs": [{"namespace": "analytics", "name": "raw.orders"}],
                "outputs": [{"namespace": "analytics", "name": "staging.orders"}],
            },
        },
        {
            "id": "job:analytics:fct_orders",
            "type": "JOB",
            "data": {
                "namespace": "analytics",
                "name": "fct_orders",
                "inputs": [{"namespace": "analytics", "name": "staging.orders"}],
                "outputs": [{"namespace": "analytics", "name": "mart.fct_orders"}],
            },
        },
        {
            "id": "job:analytics:revenue_dashboard",
            "type": "JOB",
            "data": {
                "namespace": "analytics",
                "name": "revenue_dashboard",
                "inputs": [{"namespace": "analytics", "name": "mart.fct_orders"}],
                "outputs": [{"namespace": "analytics", "name": "bi.revenue_dashboard"}],
            },
        },
        {
            "id": "job:analytics:unrelated",
            "type": "JOB",
            "data": {
                "namespace": "analytics",
                "name": "unrelated",
                "inputs": [{"namespace": "analytics", "name": "raw.users"}],
                "outputs": [{"namespace": "analytics", "name": "mart.users"}],
            },
        },
    ]

    impacts = extract_downstream_impacts(graph, "analytics", "raw.orders")
    names = {(i.asset_name, i.asset_type, i.depth) for i in impacts}

    assert ("stg_orders", "MODEL", 1) in names
    assert ("staging.orders", "DATASET", 1) in names
    assert ("fct_orders", "MODEL", 2) in names
    assert ("mart.fct_orders", "DATASET", 2) in names
    assert ("revenue_dashboard", "DASHBOARD", 3) in names
    assert ("bi.revenue_dashboard", "DASHBOARD", 3) in names
    assert not any(name == "unrelated" for name, _, _ in names)
