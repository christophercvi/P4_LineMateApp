import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration


def test_health_reports_seed_and_vectors(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["seed"]["state"] == "ready"
    counts = body["seed"]["counts"]
    assert counts["documents"] == 24 and counts["tickets"] == 40 and counts["stations"] == 4
    vectors = body["seed"]["vectors"]
    assert vectors["documents"] == 24 and vectors["chunks"] > 100 and vectors["photos"] >= 3


def test_login_me_and_lookups(app_client, as_cook):
    me = app_client.get("/api/auth/me", headers=as_cook).json()
    assert me["username"] == "marco" and me["role"] == "line_cook" and me["station"] == "grill"
    lookups = app_client.get("/api/lookups", headers=as_cook).json()
    assert {s["id"] for s in lookups["stations"]} == {"grill", "prep", "pastry", "foh"}
    assert len(lookups["crew"]) >= 12


def test_bad_credentials_and_missing_token(app_client):
    r = app_client.post("/api/auth/login", json={"username": "marco", "password": "nope"})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert app_client.get("/api/dashboard").status_code == 401
    r = app_client.get("/api/dashboard", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_validation_errors_are_readable(app_client):
    r = app_client.post("/api/auth/login", json={"username": "marco"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error" and "password" in r.json()["detail"]


def test_create_account_as_line_cook(app_client):
    body = {
        "displayName": "Jamie Tran",
        "username": "jamie.tran",
        "email": "jamie@hearthline-kitchen.com",
        "password": "Grill-Shift-2026",
        "station": "grill",
    }
    r = app_client.post("/api/auth/register", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["user"]["role"] == "line_cook" and data["user"]["station"] == "grill"
    assert data["user"]["crewMemberId"]
    headers = {"Authorization": f"Bearer {data['token']}"}
    assert app_client.get("/api/auth/me", headers=headers).json()["username"] == "jamie.tran"
    # the new cook shows up in the crew list and can sign in again
    crew = app_client.get("/api/lookups", headers=headers).json()["crew"]
    assert any(c["name"] == "Jamie Tran" for c in crew)
    again = app_client.post("/api/auth/login", json={"username": "jamie.tran", "password": "Grill-Shift-2026"})
    assert again.status_code == 200

    dup = app_client.post("/api/auth/register", json=body)
    assert dup.status_code == 409


def test_create_account_rejects_weak_password_and_unknown_station(app_client):
    base = {"displayName": "Weak", "username": "weak.user", "email": "weak@hearthline-kitchen.com", "station": "grill"}
    assert app_client.post("/api/auth/register", json={**base, "password": "short"}).status_code == 422
    r = app_client.post("/api/auth/register", json={**base, "password": "LongEnough-2026", "station": "moon"})
    assert r.status_code in (400, 422)


def test_all_seeded_users_can_sign_in(app_client):
    for username in ("marco", "priya", "samuel", "elena", "alex.admin"):
        r = app_client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, username


def test_public_station_list_for_account_creation(app_client):
    r = app_client.get("/api/auth/stations")
    assert r.status_code == 200
    assert {s["id"] for s in r.json()} == {"grill", "pastry", "prep", "foh"}
    assert app_client.get("/api/lookups").status_code == 401
