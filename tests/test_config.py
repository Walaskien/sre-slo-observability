"""Cross-checks between files that have to agree but are written in different formats."""
import json
import re
from pathlib import Path

import yaml

import server

ROOT = Path(__file__).resolve().parent.parent
RULES = (ROOT / "prometheus" / "rules" / "slo.yml").read_text()
DASHBOARD = json.loads((ROOT / "grafana" / "dashboards" / "slo-error-budget.json").read_text())


def all_rules():
    for group in yaml.safe_load(RULES)["groups"]:
        yield from group["rules"]


def test_latency_rules_read_a_bucket_the_service_exposes():
    edges = set(re.findall(r'le="([0-9.]+)"', RULES))
    assert edges == {"0.3"}
    assert float(edges.pop()) in server.BUCKETS


def test_dashboard_uses_only_provisioned_datasources():
    provisioned = yaml.safe_load(
        (ROOT / "grafana/provisioning/datasources/datasources.yml").read_text()
    )["datasources"]
    uids = {d["uid"] for d in provisioned}
    used = set()
    for panel in DASHBOARD["panels"]:
        used.add(panel["datasource"]["uid"])
        used.update(t["datasource"]["uid"] for t in panel["targets"])
    assert used <= uids


def test_dashboard_panel_ids_are_unique():
    ids = [p["id"] for p in DASHBOARD["panels"]]
    assert len(ids) == len(set(ids))


def test_dashboard_queries_only_recorded_series_that_exist():
    recorded = {r["record"] for r in all_rules() if "record" in r}
    queries = [t["expr"] for p in DASHBOARD["panels"] for t in p["targets"]]
    used = set(re.findall(r"\bservice:[a-z_0-9]+:[a-z_0-9]+", " ".join(queries)))
    assert used
    assert used <= recorded


def test_alert_thresholds_match_the_documented_budgets():
    # 0.005 = 1 - 99.5%, 0.05 = 1 - 95%. If an SLO target moves, these have to move with it.
    exprs = {r["alert"]: r["expr"] for r in all_rules() if "alert" in r}
    assert "(14.4 * 0.005)" in exprs["SLOAvailabilityFastBurn"]
    assert "(6 * 0.005)" in exprs["SLOAvailabilitySlowBurn"]
    assert "(14.4 * 0.05)" in exprs["SLOLatencyFastBurn"]
    assert "(6 * 0.05)" in exprs["SLOLatencySlowBurn"]


def test_every_burn_alert_pairs_a_long_and_a_short_window():
    for rule in all_rules():
        if rule.get("alert", "").startswith("SLO"):
            windows = re.findall(r"ratio_rate(\w+)", rule["expr"])
            assert len(windows) == 2 and windows[0] != windows[1]
