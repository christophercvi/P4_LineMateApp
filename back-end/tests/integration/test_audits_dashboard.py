import pytest

pytestmark = pytest.mark.integration


def test_lookups(app_client, as_cook):
    body = app_client.get("/api/lookups", headers=as_cook).json()
    assert {s["id"] for s in body["stations"]} == {"grill", "prep", "pastry", "foh"}
    assert len(body["crew"]) == 12


def test_stale_audit_scope_and_threshold(app_client, as_sous, as_manager, as_cook):
    assert app_client.get("/api/audits/stale", headers=as_cook).status_code == 403
    grill = app_client.get("/api/audits/stale", headers=as_sous).json()
    assert grill["scope"] == "Grill" and grill["threshold"] == 180
    assert all(r["station"] == "grill" and r["daysSinceReview"] > 180 for r in grill["rows"])
    strict = app_client.get("/api/audits/stale", headers=as_manager, params={"threshold": 30}).json()
    loose = app_client.get("/api/audits/stale", headers=as_manager, params={"threshold": 365}).json()
    assert len(strict["rows"]) >= len(loose["rows"])
    assert {r["station"] for r in strict["rows"]} > {"grill"}
    assert all(r["category"] != "incident" for r in strict["rows"])
    assert strict["excludedIncidents"]


def test_ownership_audit(app_client, as_manager, as_admin):
    body = app_client.get("/api/audits/ownership", headers=as_manager).json()
    row = next(r for r in body["rows"] if r["ticketId"] == "TKT-009")
    assert row["docId"] == "DOC-SOP-001" and row["assigneeStation"] == "foh" and row["docStation"] == "grill"
    assert row["suggestedAssigneeId"]
    assert body["flows"] and all(f["value"] > 0 for f in body["flows"])
    assert app_client.get("/api/audits/ownership", headers=as_admin).status_code == 403


def test_workload_scope_by_role(app_client, as_sous, as_manager, as_cook):
    assert app_client.get("/api/analytics/workload", headers=as_cook).status_code == 403
    mine = app_client.get("/api/analytics/workload", headers=as_sous).json()
    assert mine["station"] == "grill"
    every = app_client.get("/api/analytics/workload", headers=as_manager).json()
    assert {r["station"] for r in every["rows"]} == {"grill", "prep", "pastry", "foh"}
    for r in every["rows"]:
        assert r["open"] == r["critical"] + r["high"] + r["medium"] + r["low"]
    assert any(r["flagged"] for r in every["rows"])
    assert abs(sum(r["share"] for r in every["rows"]) - 100) < 0.5


def test_workload_status_filter(app_client, as_manager):
    blocked = app_client.get("/api/analytics/workload", headers=as_manager, params={"statuses": "blocked"}).json()
    assert sum(r["open"] for r in blocked["rows"]) >= 1
    every = app_client.get("/api/analytics/workload", headers=as_manager, params={"statuses": "open,in_progress,blocked"}).json()
    assert sum(r["open"] for r in every["rows"]) > sum(r["open"] for r in blocked["rows"])


def test_dashboard_by_role(app_client, as_cook, as_manager, as_admin):
    cook = app_client.get("/api/dashboard", headers=as_cook).json()
    assert cook["scope"] == "Grill" and cook["admin"] is None
    assert all(t["assigneeId"] == "CM-01" for t in cook["myTickets"])
    assert len(cook["openTrend"]) == 14 and len(cook["closedTrend"]) == 12
    mgr = app_client.get("/api/dashboard", headers=as_manager).json()
    assert mgr["scope"] == "All stations" and mgr["kpis"]["open"] >= cook["kpis"]["open"]
    assert 0 <= mgr["kpis"]["resolutionRate"] <= 1
    admin = app_client.get("/api/dashboard", headers=as_admin).json()
    assert admin["admin"] is not None


def test_seeded_approval_payloads_have_resolved_dates(app_client, as_manager):
    """Relative seed tokens ('@agod:-2') inside JSON payloads become real ISO dates."""
    approvals = app_client.get("/api/approvals", headers=as_manager).json()
    supply = [a for a in approvals if a["kind"] == "draft_supply_order"]
    assert supply, "seed has supply-order approvals"
    for a in approvals:
        assert "@ago" not in str(a["payload"])
    for a in supply:
        needed = a["payload"]["neededBy"]
        assert len(needed) == 10 and needed[4] == "-" and needed[7] == "-"
