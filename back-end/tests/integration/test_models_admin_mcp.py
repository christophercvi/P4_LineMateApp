import pytest

pytestmark = pytest.mark.integration

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, token: str, method: str, params: dict | None = None, rid: int = 1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"})


# --------------------------------------------------------------------------- models


def test_my_models_come_from_ollama(app_client, as_cook, fake_ollama):
    body = app_client.get("/api/models", headers=as_cook).json()
    names = [x["name"] for x in body["models"]]
    assert {"llama3.2:3b", "qwen3:4b-q4_K_M", "gemma4:e2b"} <= set(names)
    assert "nomic-embed-text:latest" not in names
    assert body["ollamaUp"] is True and body["default"] in names and body["vision"] is True
    thinking = {x["name"]: x["thinking"] for x in body["models"]}
    assert thinking["llama3.2:3b"] == "none"
    assert thinking["qwen3:4b-q4_K_M"] != "none" and thinking["gemma4:e2b"] != "none"


def test_newly_pulled_model_appears_without_code_change(app_client, as_cook, fake_ollama, services):
    fake_ollama.models["mistral-small:24b"] = {
        "family": "mistral",
        "size": 14_000_000_000,
        "params": "24B",
        "quant": "Q4_K_M",
        "caps": ["completion", "tools"],
        "ctx": 32768,
    }
    services.ollama._cache = None
    try:
        names = [x["name"] for x in app_client.get("/api/models", headers=as_cook).json()["models"]]
        assert "mistral-small:24b" in names
    finally:
        del fake_ollama.models["mistral-small:24b"]
        services.ollama._cache = None


def test_allowlist_hides_models_per_role(app_client, as_admin, as_cook, as_manager, services):
    admin = app_client.get("/api/admin/models", headers=as_admin).json()
    chat = admin["allowlist"]["line_cook"]
    assert "phi4-mini:latest" in chat
    allow = {role: list(models) for role, models in admin["allowlist"].items()}
    allow["line_cook"] = [n for n in chat if n != "phi4-mini:latest"]
    r = app_client.put("/api/admin/models/allowlist", headers=as_admin, json={"allowlist": allow})
    assert r.status_code == 200 and "phi4-mini:latest" not in r.json()["allowlist"]["line_cook"]
    cook = [x["name"] for x in app_client.get("/api/models", headers=as_cook).json()["models"]]
    mgr = [x["name"] for x in app_client.get("/api/models", headers=as_manager).json()["models"]]
    assert "phi4-mini:latest" not in cook and "phi4-mini:latest" in mgr
    allow["line_cook"] = []
    bad = app_client.put("/api/admin/models/allowlist", headers=as_admin, json={"allowlist": allow})
    assert bad.status_code == 400
    allow["line_cook"] = chat
    assert app_client.put("/api/admin/models/allowlist", headers=as_admin, json={"allowlist": allow}).status_code == 200


def test_model_admin_is_admin_only(app_client, as_manager):
    assert app_client.get("/api/admin/models", headers=as_manager).status_code == 403
    assert app_client.post("/api/admin/models/load", headers=as_manager, json={"name": "llama3.2:3b"}).status_code == 403


def test_load_and_unload(app_client, as_admin, fake_ollama, services):
    r = app_client.post("/api/admin/models/load", headers=as_admin, json={"name": "gemma4:e2b"})
    assert r.status_code == 200 and r.json()["loaded"] is True
    assert "gemma4:e2b" in fake_ollama.loaded
    r = app_client.post("/api/admin/models/unload", headers=as_admin, json={"name": "gemma4:e2b"})
    assert r.status_code == 200 and r.json()["loaded"] is False
    assert app_client.post("/api/admin/models/load", headers=as_admin, json={"name": "nope:1b"}).status_code == 404


# --------------------------------------------------------------------------- admin users / vector store


def test_admin_user_management(app_client, as_admin, as_manager):
    users = app_client.get("/api/admin/users", headers=as_admin).json()
    assert len(users) >= 13 and all("passwordHash" not in u for u in users)
    assert app_client.get("/api/admin/users", headers=as_manager).status_code == 403
    r = app_client.patch("/api/admin/users/u-jordan", headers=as_admin, json={"station": "pastry"})
    assert r.status_code == 200 and r.json()["station"] == "pastry"
    r = app_client.patch("/api/admin/users/u-jordan", headers=as_admin, json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    login = app_client.post("/api/auth/login", json={"username": "jordan", "password": "Hearthline#2026"})
    assert login.status_code in (401, 403)
    app_client.patch("/api/admin/users/u-jordan", headers=as_admin, json={"active": True, "station": "grill"})
    assert app_client.patch("/api/admin/users/u-admin", headers=as_admin, json={"active": False}).status_code == 409


def test_vector_store_status(app_client, as_admin):
    body = app_client.get("/api/admin/vector-store", headers=as_admin).json()
    by_name = {c["name"]: c for c in body["collections"]}
    docs = next(c for n, c in by_name.items() if n.startswith("documents"))
    photos = next(c for n, c in by_name.items() if n.startswith("photos"))
    assert docs["vectors"] > 300 and docs["documents"] >= 24 and docs["distance"] == "cosine"
    assert photos["embeddingModel"].startswith("nomic-embed-vision") and photos["vectors"] >= 3
    assert {s["name"] for s in body["services"]} >= {"Ollama", "Chroma"}


def test_reconcile_and_reindex(app_client, as_admin):
    r = app_client.post("/api/admin/vector-store/reconcile", headers=as_admin)
    assert r.status_code == 200 and set(r.json()) >= {"removed"}
    r = app_client.post("/api/admin/vector-store/reindex", headers=as_admin, json={"collection": "photos"})
    assert r.status_code == 202 and r.json()["collection"]


# --------------------------------------------------------------------------- MCP console and endpoint


def test_mcp_console_role_views(app_client, as_sous, as_admin, as_cook):
    assert app_client.get("/api/mcp", headers=as_cook).status_code == 403
    sous = app_client.get("/api/mcp", headers=as_sous).json()
    assert {t["name"] for t in sous["tools"]} == {
        "search_documents",
        "list_open_tickets",
        "workload_analytics",
        "create_ticket",
        "draft_supply_order",
        "escalate_incident",
    }
    assert sous["tokens"] is None
    admin = app_client.get("/api/mcp", headers=as_admin).json()
    assert admin["tokens"] and admin["endpoint"].endswith("/mcp")
    assert {s["id"] for s in admin["servers"]} == {"supplier-inventory", "shift-scheduler"}
    assert all(s["status"] == "connected" for s in admin["servers"])


def test_mcp_try_read_tool(app_client, as_sous):
    r = app_client.post("/api/mcp/tools/search_documents/try", headers=as_sous, json={"input": {"query": "fryer oil change", "k": 2}})
    out = r.json()
    assert r.status_code == 200 and out["status"] == "ok", out
    assert out["output"] and out["latencyMs"] >= 0


def test_mcp_try_write_tool_interrupts_for_approval(app_client, as_sous, as_manager):
    r = app_client.post("/api/mcp/tools/draft_supply_order/try", headers=as_sous, json={"input": None})
    out = r.json()
    assert out["status"] == "interrupted" and out["requiresApproval"] is True
    approval_id = out["output"]["approval_id"]
    pending = {a["id"] for a in app_client.get("/api/approvals", headers=as_manager).json() if a["status"] == "pending"}
    assert approval_id in pending


def test_mcp_try_respects_roles(app_client, as_admin):
    r = app_client.post("/api/mcp/tools/create_ticket/try", headers=as_admin, json={"input": None})
    assert r.status_code == 403
    assert app_client.post("/api/mcp/tools/nope/try", headers=as_admin, json={}).status_code == 404


def test_service_token_lifecycle_and_protected_endpoint(app_client, as_admin, as_manager):
    assert app_client.post("/mcp", json={}, headers=MCP_HEADERS).status_code == 401
    assert rpc(app_client, "lm_svc_not-a-real-token", "tools/list").status_code == 401
    bad = app_client.post("/api/mcp/tokens", headers=as_manager, json={"name": "km-script", "client": "Script", "scopes": ["mcp:read"]})
    assert bad.status_code == 403
    r = app_client.post(
        "/api/mcp/tokens", headers=as_admin, json={"name": "pytest-reader", "client": "Test suite", "scopes": ["mcp:read"], "days": 7}
    )
    assert r.status_code == 201
    created = r.json()
    token = created["token"]
    assert token.startswith("lm_svc_") and created["prefix"] in token
    listed = rpc(app_client, token, "tools/list")
    assert listed.status_code == 200, listed.text
    assert {t["name"] for t in listed.json()["result"]["tools"]} >= {"search_documents", "create_ticket"}
    called = rpc(app_client, token, "tools/call", {"name": "list_open_tickets", "arguments": {"min_priority": "critical"}}, 2)
    assert called.status_code == 200 and not called.json()["result"].get("isError"), called.text
    denied = rpc(
        app_client, token, "tools/call", {"name": "create_ticket", "arguments": {"title": "Read-only token write", "priority": "low"}}, 3
    )
    assert denied.json()["result"]["isError"] is True
    assert app_client.post(f"/api/mcp/tokens/{created['id']}/revoke", headers=as_admin).json()["revoked"] is True
    assert rpc(app_client, token, "tools/list").status_code == 401


def test_user_jwt_works_on_mcp_endpoint(app_client, as_cook):
    token = as_cook["Authorization"].split()[1]
    r = rpc(app_client, token, "tools/call", {"name": "list_open_tickets", "arguments": {}})
    assert r.status_code == 200 and r.json()["result"]["isError"] is True
