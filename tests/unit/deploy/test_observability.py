"""
tests/unit/deploy/test_observability.py

The Grafana files under deploy/observability/ hold together: every dashboard and alert rule file parses, uids are
unique, every query names a provisioned data source, and every SQL query reads only tables that
deploy/observability/postgres/readonly.sql grants to the read-only role (which never covers token or session tables).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
OBS = ROOT / "deploy" / "observability"
DASHBOARDS = sorted((OBS / "grafana" / "dashboards").glob("*.json"))
ALERT_FILES = sorted((OBS / "grafana" / "provisioning" / "alerting").glob("*.yaml"))
DATASOURCES = OBS / "grafana" / "provisioning" / "datasources" / "datasources.yaml"
READONLY_SQL = OBS / "postgres" / "readonly.sql"

# Grafana's server-side expressions (math, threshold) in alert rules.
EXPRESSION_UID = "__expr__"
SECRET_TABLES = {"wcl_tokens", "sessions", "oauth_states"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _datasource_uids() -> dict[str, str]:
    """Provisioned data source UID -> type."""
    doc = yaml.safe_load(DATASOURCES.read_text(encoding="utf-8"))
    return {ds["uid"]: ds["type"] for ds in doc["datasources"]}


def _granted_tables() -> set[str]:
    """The tables named in readonly.sql's GRANT SELECT statements."""
    text = READONLY_SQL.read_text(encoding="utf-8")
    tables: set[str] = set()
    for body in re.findall(r"GRANT\s+SELECT\s+ON\s+(.*?)\s+TO\s+khadbot_readonly", text, flags=re.S | re.I):
        tables.update(name.strip() for name in body.split(",") if name.strip())
    return tables


def _tables_read(sql: str) -> set[str]:
    """Table names after FROM or JOIN; a name followed by "(" is a function (``extract(epoch FROM now())``)."""
    return {m.lower() for m in re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b(?!\s*\()", sql, flags=re.I)}


def _dashboard(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _panels(dashboard: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])  # panels inside a collapsed row


def _rules() -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in ALERT_FILES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in doc["groups"]:
            for rule in group["rules"]:
                yield path, rule


def _queries() -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(where, data source uid, query model) for every dashboard target, variable and alert rule query."""
    for path in DASHBOARDS:
        dashboard = _dashboard(path)
        for var in dashboard.get("templating", {}).get("list", []):
            if "datasource" in var:
                yield f"{path.name} variable {var['name']}", var["datasource"]["uid"], {}
        for panel in _panels(dashboard):
            if panel["type"] == "row":
                continue
            for target in panel.get("targets", []):
                uid = target.get("datasource", panel.get("datasource", {})).get("uid")
                yield f"{path.name} panel {panel['title']!r} {target['refId']}", uid, target
    for path, rule in _rules():
        for query in rule["data"]:
            yield f"{path.name} rule {rule['uid']} {query['refId']}", query["datasourceUid"], query["model"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_files_exist() -> None:
    assert len(DASHBOARDS) >= 4
    assert ALERT_FILES


def test_dashboard_uids_unique() -> None:
    uids = [_dashboard(path)["uid"] for path in DASHBOARDS]
    assert all(uids)
    assert len(uids) == len(set(uids))


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_panel_ids_unique(path: Path) -> None:
    ids = [panel["id"] for panel in _panels(_dashboard(path))]
    assert len(ids) == len(set(ids))


def test_alert_rule_uids_unique() -> None:
    uids = [rule["uid"] for _, rule in _rules()]
    assert all(uids)
    assert len(uids) == len(set(uids))


def test_alert_conditions_name_a_query() -> None:
    for _, rule in _rules():
        assert rule["condition"] in {query["refId"] for query in rule["data"]}, rule["uid"]


def test_every_query_names_a_provisioned_datasource() -> None:
    provisioned = set(_datasource_uids()) | {EXPRESSION_UID}
    bad = [(where, uid) for where, uid, _ in _queries() if uid not in provisioned]
    assert not bad


def test_sql_reads_only_granted_tables() -> None:
    granted = _granted_tables()
    postgres = {uid for uid, kind in _datasource_uids().items() if "postgres" in kind}
    seen = 0
    for where, uid, model in _queries():
        if uid not in postgres:
            continue
        sql = model.get("rawSql", "")
        assert sql, f"{where}: a Postgres query without rawSql"
        tables = _tables_read(sql)
        assert tables, f"{where}: no table found"
        assert tables <= granted, f"{where}: reads {sorted(tables - granted)}, not granted in readonly.sql"
        seen += 1
    assert seen


def test_readonly_role_never_reads_secret_tables() -> None:
    assert _granted_tables()
    assert not _granted_tables() & SECRET_TABLES


def test_table_parser_skips_functions() -> None:
    sql = "SELECT extract(epoch FROM now() - min(updated_at)) FROM jobs AS j LEFT JOIN usage u ON true"
    assert _tables_read(sql) == {"jobs", "usage"}
