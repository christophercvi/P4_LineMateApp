import time

import pytest

pytestmark = pytest.mark.integration

RECIPE_MD = b"""# Grilled Peach Salad

## Ingredients
- 4 ripe peaches, halved and pitted
- 200 g burrata
- 30 ml aged balsamic

## Method
1. Brush the cut side of each peach with oil.
2. Grill cut side down for 3 minutes until marked.
3. Plate with burrata and finish with balsamic.

## Allergens
Contains milk (burrata).
"""


def wait_job(client, headers, job_id: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/ingest/jobs/{job_id}", headers=headers).json()
        if job["stage"] in ("ready", "failed"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"ingest job {job_id} did not finish")


def upload(client, headers, *, name="grilled-peach-salad.md", content=RECIPE_MD, mime="text/markdown", **fields):
    data = {
        "title": "Grilled Peach Salad",
        "category": "recipe",
        "station": "grill",
        "summary": "Summer salad",
        "tags": "salad,summer",
        **fields,
    }
    return client.post("/api/documents", headers=headers, data=data, files={"file": (name, content, mime)})


def test_line_cook_never_sees_incident_reports(app_client, as_cook):
    body = app_client.get("/api/documents", headers=as_cook).json()
    assert body["hiddenIncidents"] == 6
    assert {d["category"] for d in body["items"]} <= {"recipe", "sop", "onboarding"}
    r = app_client.get("/api/documents/DOC-INC-001", headers=as_cook)
    assert r.status_code in (403, 404)


def test_manager_sees_all_categories_and_filters(app_client, as_manager):
    body = app_client.get("/api/documents", headers=as_manager).json()
    assert body["hiddenIncidents"] == 0
    assert {d["category"] for d in body["items"]} == {"recipe", "sop", "incident", "onboarding"}
    sops = app_client.get("/api/documents", headers=as_manager, params={"category": "sop", "station": "prep"}).json()
    assert {d["id"] for d in sops["items"]} == {"DOC-SOP-002", "DOC-SOP-003", "DOC-SOP-004"}
    found = app_client.get("/api/documents", headers=as_manager, params={"q": "fryer"}).json()["items"]
    assert any(d["id"] == "DOC-SOP-001" for d in found)
    stale = app_client.get("/api/documents", headers=as_manager, params={"stale": True}).json()["items"]
    assert stale and all(d["stale"] for d in stale)


def test_document_detail_has_chunks_and_signed_file(app_client, as_cook):
    d = app_client.get("/api/documents/DOC-SOP-001", headers=as_cook).json()
    assert d["document"]["title"].startswith("Fryer Oil")
    assert d["document"]["body"]
    assert len(d["chunks"]) >= 3 and all(c["tokens"] > 0 for c in d["chunks"])
    assert d["embeddingModel"].startswith("nomic-embed-text")
    url = d["document"]["fileUrl"]
    f = app_client.get(url)
    assert f.status_code == 200 and f.content[:4] == b"%PDF"
    tampered = url.replace("sig=", "sig=0")
    assert app_client.get(tampered).status_code == 403


def test_sous_chef_uploads_recipe_for_own_station(app_client, as_sous):
    r = upload(app_client, as_sous)
    assert r.status_code == 201, r.text
    out = r.json()
    doc_id = out["document"]["id"]
    assert doc_id.startswith("DOC-REC-") and out["document"]["source"] == "upload"
    job = wait_job(app_client, as_sous, out["job"]["id"])
    assert job["stage"] == "ready" and job["chunks"] >= 1
    detail = app_client.get(f"/api/documents/{doc_id}", headers=as_sous).json()
    assert detail["document"]["status"] == "ready"
    assert "burrata" in detail["document"]["body"].lower()
    assert detail["chunks"]


def test_upload_permissions(app_client, as_sous, as_cook):
    assert upload(app_client, as_sous, category="sop", title="New SOP").status_code == 403
    assert upload(app_client, as_sous, station="prep").status_code == 403
    assert upload(app_client, as_cook).status_code == 403


def test_upload_rejects_unsupported_or_spoofed_files(app_client, as_manager):
    r = upload(app_client, as_manager, name="run.exe", content=b"MZ\x90\x00binary", mime="application/octet-stream")
    assert r.status_code in (415, 422)
    r = upload(app_client, as_manager, name="fake.pdf", content=b"just text, not a pdf", mime="application/pdf")
    assert r.status_code in (415, 422)


def test_manager_replaces_document_and_version_increments(app_client, as_manager):
    before = app_client.get("/api/documents/DOC-REC-006", headers=as_manager).json()["document"]
    new = b"# House Vinaigrettes\n\n## Sherry vinaigrette\n- 1 part sherry vinegar\n- 3 parts olive oil\n- Salt\n"
    r = app_client.post(
        "/api/documents/DOC-REC-006/replace",
        headers=as_manager,
        data={"note": "Switched to sherry vinegar"},
        files={"file": ("house-vinaigrettes.md", new, "text/markdown")},
    )
    assert r.status_code == 200, r.text
    after = r.json()["document"]
    assert after["version"] == before["version"] + 1
    wait_job(app_client, as_manager, r.json()["job"]["id"])
    body = app_client.get("/api/documents/DOC-REC-006", headers=as_manager).json()["document"]["body"]
    assert "sherry" in body.lower()


def test_review_rules(app_client, as_sous, as_prep_sous, as_manager):
    r = app_client.post("/api/documents/DOC-SOP-005/review", headers=as_sous, json={"note": "Checked degreaser dilution"})
    assert r.status_code == 200, r.text
    assert r.json()["daysSinceReview"] == 0 and r.json()["stale"] is False
    assert app_client.post("/api/documents/DOC-SOP-005/review", headers=as_prep_sous, json={}).status_code == 403
    bulk = app_client.post("/api/documents/review-bulk", headers=as_manager, json={"ids": ["DOC-SOP-003", "DOC-ONB-002"]})
    assert bulk.status_code == 200
    assert set(bulk.json()["updated"]) == {"DOC-SOP-003", "DOC-ONB-002"}


def test_archive_is_manager_only_and_hides_document(app_client, as_sous, as_manager):
    assert app_client.post("/api/documents/DOC-ONB-003/archive", headers=as_sous).status_code == 403
    r = app_client.post("/api/documents/DOC-ONB-003/archive", headers=as_manager)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    ids = {d["id"] for d in app_client.get("/api/documents", headers=as_manager).json()["items"]}
    assert "DOC-ONB-003" not in ids
    with_archived = app_client.get("/api/documents", headers=as_manager, params={"includeArchived": True}).json()
    assert "DOC-ONB-003" in {d["id"] for d in with_archived["items"]}


def test_ingest_jobs_list(app_client, as_manager):
    jobs = app_client.get("/api/ingest/jobs", headers=as_manager, params={"docId": "DOC-SOP-001"}).json()
    assert jobs and all(j["docId"] == "DOC-SOP-001" for j in jobs)
    assert jobs[0]["stage"] == "ready"
