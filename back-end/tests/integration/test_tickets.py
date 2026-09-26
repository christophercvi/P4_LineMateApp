import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ATTACHMENTS = Path(__file__).resolve().parents[2] / "seed_attachments"


def new_ticket(client, headers, **body) -> dict:
    payload = {
        "title": "Grill station hand sink drains slowly",
        "description": "Water pools after 30 seconds.",
        "priority": "medium",
        **body,
    }
    r = client.post("/api/tickets", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_list_filters(app_client, as_manager):
    rows = app_client.get("/api/tickets", headers=as_manager, params={"status": "open,blocked", "station": "grill"}).json()
    assert rows and all(t["status"] in ("open", "blocked") and t["station"] == "grill" for t in rows)
    crit = app_client.get("/api/tickets", headers=as_manager, params={"priority": "critical"}).json()
    assert {"TKT-001", "TKT-017"} <= {t["id"] for t in crit}
    hits = app_client.get("/api/tickets", headers=as_manager, params={"q": "blast chiller"}).json()
    assert [t["id"] for t in hits][:1] == ["TKT-017"]


def test_mine_and_mismatch_flags(app_client, as_cook, as_manager):
    mine = app_client.get("/api/tickets", headers=as_cook, params={"mine": True}).json()
    assert mine and all(t["assigneeId"] == "CM-01" for t in mine)
    t9 = next(t for t in app_client.get("/api/tickets", headers=as_manager).json() if t["id"] == "TKT-009")
    assert t9["mismatch"] is True


def test_detail_permissions_reflect_role(app_client, as_cook, as_sous, as_prep_sous):
    cook = app_client.get("/api/tickets/TKT-010", headers=as_cook).json()
    assert cook["permissions"]["changeStatus"] is True  # Marco is the assignee
    assert cook["permissions"]["lowerOrClose"] is False and cook["permissions"]["raise"] is True
    sous = app_client.get("/api/tickets/TKT-007", headers=as_sous).json()["permissions"]
    assert sous["assign"] and sous["lowerOrClose"]
    other = app_client.get("/api/tickets/TKT-007", headers=as_prep_sous).json()["permissions"]
    assert not other["assign"] and not other["lowerOrClose"]


def test_cook_ticket_defaults_to_own_station(app_client, as_cook):
    t = new_ticket(app_client, as_cook)
    assert t["id"].startswith("TKT-") and t["station"] == "grill" and t["status"] == "open"
    assert t["reporterId"] == "CM-01"


def test_admin_cannot_create_tickets(app_client, as_admin):
    r = app_client.post("/api/tickets", headers=as_admin, json={"title": "Not a kitchen user"})
    assert r.status_code == 403


def test_validation_errors_are_reported(app_client, as_cook):
    r = app_client.post("/api/tickets", headers=as_cook, json={"title": "no"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def test_priority_and_status_rules(app_client, as_cook, as_sous, as_prep_sous, as_manager):
    t = new_ticket(app_client, as_cook, priority="medium")
    url = f"/api/tickets/{t['id']}"
    assert app_client.patch(url, headers=as_cook, json={"priority": "high"}).status_code == 200
    assert app_client.patch(url, headers=as_cook, json={"priority": "low"}).status_code == 403
    assert app_client.patch(url, headers=as_cook, json={"status": "in_progress"}).status_code == 403
    assert app_client.patch(url, headers=as_prep_sous, json={"status": "closed"}).status_code == 403
    r = app_client.patch(url, headers=as_sous, json={"assigneeId": "CM-03", "assigneeSet": True, "status": "in_progress"})
    assert r.status_code == 200 and r.json()["assigneeId"] == "CM-03" and r.json()["status"] == "in_progress"
    assert app_client.patch(url, headers=as_sous, json={"assigneeId": "CM-99", "assigneeSet": True}).status_code == 400
    # A status-only patch leaves the assignee alone; an explicit assigneeId (even null) changes it.
    assert app_client.patch(url, headers=as_sous, json={"status": "blocked"}).json()["assigneeId"] == "CM-03"
    assert app_client.patch(url, headers=as_sous, json={"assigneeId": "CM-01"}).json()["assigneeId"] == "CM-01"
    assert app_client.patch(url, headers=as_sous, json={"assigneeId": None}).json()["assigneeId"] is None
    assert app_client.patch(url, headers=as_sous, json={"assigneeId": "CM-03", "status": "in_progress"}).status_code == 200
    r = app_client.patch(url, headers=as_manager, json={"priority": "low", "status": "closed"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    activity = app_client.get(url, headers=as_manager).json()["activity"]
    assert any("closed" in (e["detail"] or "") for e in activity)


def test_threaded_comments(app_client, as_cook, as_sous, as_admin):
    t = new_ticket(app_client, as_cook)
    url = f"/api/tickets/{t['id']}/comments"
    first = app_client.post(url, headers=as_cook, json={"body": "Happens every rush."})
    assert first.status_code == 201
    reply = app_client.post(url, headers=as_sous, json={"body": "Plumber booked for 2 PM.", "parentId": first.json()["id"]})
    assert reply.status_code == 201 and reply.json()["parentId"] == first.json()["id"]
    assert app_client.post(url, headers=as_admin, json={"body": "hello"}).status_code == 403
    detail = app_client.get(f"/api/tickets/{t['id']}", headers=as_cook).json()
    assert [c["authorId"] for c in detail["comments"]] == ["CM-01", "CM-02"]
    assert detail["ticket"]["commentCount"] == 2


def test_attachments_upload_and_photo_index(app_client, as_cook, services):
    t = new_ticket(app_client, as_cook, title="Fryer oil looks dark again")
    photo = (ATTACHMENTS / "fryer-oil-tuesday.jpg").read_bytes()
    files = [("files", ("fryer.jpg", photo, "image/jpeg")), ("files", ("notes.txt", b"Oil changed Monday night.", "text/plain"))]
    r = app_client.post(f"/api/tickets/{t['id']}/attachments", headers=as_cook, files=files)
    assert r.status_code == 201, r.text
    atts = r.json()
    assert [a["kind"] for a in atts] == ["jpg", "txt"]
    got = app_client.get(atts[0]["url"])
    assert got.status_code == 200 and got.content == photo
    vs = services.vectors

    def owners() -> set:
        return vs.stats()[vs.photos_name]["doc_ids"]

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and t["id"] not in owners():
        time.sleep(0.2)
    assert t["id"] in owners()


def test_attachment_type_is_enforced(app_client, as_cook):
    r = app_client.post(
        "/api/tickets/TKT-010/attachments", headers=as_cook, files=[("files", ("run.sh", b"#!/bin/sh\necho hi", "text/x-sh"))]
    )
    assert r.status_code == 422


def test_unknown_ticket(app_client, as_cook):
    r = app_client.get("/api/tickets/TKT-999", headers=as_cook)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
