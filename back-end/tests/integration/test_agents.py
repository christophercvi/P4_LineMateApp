import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import of, read_sse

pytestmark = pytest.mark.integration

ATTACHMENTS = Path(__file__).resolve().parents[2] / "seed_attachments"


def conv() -> str:
    return f"conv-{uuid.uuid4().hex[:8]}"


def ask(client, headers, question: str, *, model="llama3.2:3b", reasoning=False, conversation_id=None, **extra):
    body = {"question": question, "model": model, "reasoning": reasoning, "conversationId": conversation_id or conv(), **extra}
    return read_sse(client, "/api/ask/stream", body, headers)


def answer_text(events) -> str:
    return "".join(of(events, "content"))


def wait_for(fn, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


# --------------------------------------------------------------------------- Ask LineMate


def test_ask_streams_steps_sources_and_cited_answer(app_client, as_cook):
    cid = conv()
    events = ask(app_client, as_cook, "How often do we change the fryer oil and how is it logged?", conversation_id=cid)
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert "step" in names and "sources" in names and "content" in names
    meta = of(events, "meta")[0]
    assert meta["model"] == "llama3.2:3b" and meta["reasoning"] is False
    sources = of(events, "sources")[0]
    assert sources and any(s["docId"] == "DOC-SOP-001" for s in sources)
    assert "[1]" in answer_text(events)
    assert not of(events, "reasoning")
    done = of(events, "done")[0]
    assert done["run_id"].startswith("RUN-")
    detail = app_client.get(f"/api/conversations/{cid}", headers=as_cook).json()
    assert [m["role"] for m in detail["history"]] == ["user", "assistant"]
    assert detail["history"][1]["extra"]["runId"] == done["run_id"]


def test_line_cook_retrieval_excludes_incident_reports(app_client, as_cook, as_manager):
    q = "What happened with the mislabelled allergen container incident and the sesame reaction?"
    cook = of(ask(app_client, as_cook, q), "sources")
    assert all(s["category"] != "incident" for s in (cook[0] if cook else []))
    mgr = of(ask(app_client, as_manager, q), "sources")[0]
    assert any(s["category"] == "incident" for s in mgr)


def test_reasoning_toggle_streams_thoughts_for_thinking_models(app_client, as_cook, chat_recorder):
    events = ask(app_client, as_cook, "How long can cooked rice sit at room temperature?", model="qwen3:4b-q4_K_M", reasoning=True)
    assert of(events, "meta")[0]["reasoning"] is True
    assert "".join(of(events, "reasoning")).strip().startswith("The context covers this")
    assert chat_recorder.calls[-1]["reasoning"] is True
    off = ask(app_client, as_cook, "How long can cooked rice sit at room temperature?", model="qwen3:4b-q4_K_M")
    assert not of(off, "reasoning") and chat_recorder.calls[-1]["reasoning"] is False


def test_reasoning_switch_is_ignored_for_models_without_thinking(app_client, as_cook, chat_recorder):
    events = ask(app_client, as_cook, "Where do the sheet trays live?", model="llama3.2:3b", reasoning=True)
    assert of(events, "meta")[0]["reasoning"] is False and not of(events, "reasoning")
    assert chat_recorder.calls[-1]["reasoning"] is None


def test_unknown_or_blocked_model_is_rejected(app_client, as_cook):
    r = app_client.post("/api/ask/stream", headers=as_cook, json={"question": "hi", "model": "made-up:1b", "conversationId": conv()})
    assert r.status_code == 403


def test_follow_up_uses_conversation_memory(app_client, as_cook):
    cid = conv()
    ask(app_client, as_cook, "What temperature should the fryer oil be at?", conversation_id=cid)
    ask(app_client, as_cook, "And how often do we change it?", conversation_id=cid)
    detail = app_client.get(f"/api/conversations/{cid}", headers=as_cook).json()
    assert detail["messages"] == 4
    convs = app_client.get("/api/conversations", headers=as_cook).json()
    assert any(c["id"] == cid and c["messages"] == 4 for c in convs)


def test_conversations_are_private_and_deletable(app_client, as_cook, as_sous):
    cid = conv()
    ask(app_client, as_cook, "Where is the allergen binder kept?", conversation_id=cid)
    assert app_client.get(f"/api/conversations/{cid}", headers=as_sous).status_code == 404
    renamed = app_client.patch(f"/api/conversations/{cid}", headers=as_cook, json={"title": "Allergen binder"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Allergen binder"
    assert renamed.json()["messages"] == 2
    assert app_client.patch(f"/api/conversations/{cid}", headers=as_sous, json={"title": "x"}).status_code == 404
    assert app_client.patch(f"/api/conversations/{cid}", headers=as_cook, json={"title": ""}).status_code == 422
    assert app_client.delete(f"/api/conversations/{cid}", headers=as_cook).status_code == 204
    assert app_client.get(f"/api/conversations/{cid}", headers=as_cook).status_code == 404


def test_model_failure_reports_error_event(app_client, as_cook, chat_recorder):
    chat_recorder.fail = True
    events = ask(app_client, as_cook, "How do I season the grill grates?")
    errors = of(events, "error")
    assert errors and "Ollama" in str(errors[0])


def test_chat_photo_upload_and_vision_question(app_client, as_cook):
    cid = conv()
    photo = (ATTACHMENTS / "walk-in-shelf-6am.jpg").read_bytes()
    up = app_client.post(
        "/api/chat/uploads", headers=as_cook, data={"conversationId": cid}, files={"file": ("shelf.jpg", photo, "image/jpeg")}
    )
    assert up.status_code == 201, up.text
    asset = up.json()
    assert asset["kind"] == "jpg" and app_client.get(asset["url"]).content == photo
    events = ask(
        app_client,
        as_cook,
        "Is this shelf stored correctly?",
        model="gemma4:e2b",
        conversation_id=cid,
        attachments=[{"name": "shelf.jpg", "assetId": asset["assetId"], "bytes": asset["bytes"]}],
    )
    steps = " ".join(str(s.get("title")) for s in of(events, "step"))
    assert "photo" in steps.lower()
    assert of(events, "done")


def test_cook_supply_request_is_redirected_to_sous_chef(app_client, as_cook):
    events = ask(app_client, as_cook, "We're running low on fryer oil, down to 1 case. Can you reorder?")
    assert not of(events, "interrupt")
    assert "Tell your Sous Chef" in answer_text(events)


def test_sous_chef_supply_request_interrupts_for_manager(app_client, as_sous):
    events = ask(app_client, as_sous, "We're running low on canola fryer oil, down to 1 case. Please reorder.")
    interrupt = of(events, "interrupt")
    assert interrupt, [e for e, _ in events]
    payload = interrupt[0]
    assert payload["kind"] == "draft_supply_order" and payload["canApprove"] is False
    assert payload["approvalId"].startswith("APR-")


def test_escalation_approval_runs_action_and_resumes_run(app_client, as_prep_sous, as_manager):
    events = ask(app_client, as_prep_sous, "Walk-in is still warm, please escalate this and hold the product.", ticketId="TKT-020")
    interrupt = of(events, "interrupt")[0]
    assert interrupt["kind"] == "escalate_incident"
    run_id = of(events, "done")[0]["run_id"]
    assert app_client.get(f"/api/agent/runs/{run_id}", headers=as_prep_sous).json()["status"] == "interrupted"

    aid = interrupt["approvalId"]
    assert app_client.post(f"/api/approvals/{aid}/decision", headers=as_prep_sous, json={"decision": "approved"}).status_code == 403
    r = app_client.post(
        f"/api/approvals/{aid}/decision", headers=as_manager, json={"decision": "approved", "reason": "Product on hold, engineer called"}
    )
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decidedBy"] == "CM-11"

    ticket = app_client.get("/api/tickets/TKT-020", headers=as_manager).json()
    assert ticket["ticket"]["priority"] == "critical" and "escalated" in ticket["ticket"]["tags"]
    assert ticket["ticket"]["status"] == "in_progress"
    run = wait_for(lambda: (x := app_client.get(f"/api/agent/runs/{run_id}", headers=as_prep_sous).json())["status"] == "success" and x)
    assert any(n["node"] == "execute_action" for n in run["nodes"])
    again = app_client.post(f"/api/approvals/{aid}/decision", headers=as_manager, json={"decision": "rejected"})
    assert again.status_code == 409


def test_reject_and_separation_of_duties(app_client, as_manager, as_admin, as_cook):
    pending = [a for a in app_client.get("/api/approvals", headers=as_manager).json() if a["status"] == "pending"]
    target = next(a for a in pending if a["kind"] == "reassign_ticket" or a["kind"] == "draft_supply_order")
    denied = app_client.post(f"/api/approvals/{target['id']}/decision", headers=as_admin, json={"decision": "approved"})
    assert denied.status_code == 403 and "Separation of duties" in denied.json()["detail"]
    assert app_client.get("/api/approvals", headers=as_cook).status_code == 403
    r = app_client.post(
        f"/api/approvals/{target['id']}/decision", headers=as_manager, json={"decision": "rejected", "reason": "Not needed this week"}
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["reason"] == "Not needed this week"
    assert app_client.get(f"/api/approvals/{target['id']}", headers=as_manager).json()["status"] == "rejected"


# --------------------------------------------------------------------------- Triage


def test_triage_for_sous_chef_is_station_scoped(app_client, as_sous):
    events = read_sse(app_client, "/api/agent/triage/stream", {"model": "llama3.2:3b", "station": "prep"}, as_sous)
    nodes = [n["node"] for n in of(events, "node") if n["status"] == "success"]
    assert nodes[:2] == ["load_tickets", "rank"] and "summarize" in nodes
    result = of(events, "result")[0]
    assert result["ranked"] and result["summary"]
    station_of = {t["id"]: t["station"] for t in app_client.get("/api/tickets", headers=as_sous).json()}
    assert all(station_of[r["ticketId"]] == "grill" for r in result["ranked"])
    assert of(events, "done")


def test_triage_for_manager_covers_kitchen(app_client, as_manager):
    events = read_sse(app_client, "/api/agent/triage/stream", {"model": "qwen3:4b-q4_K_M"}, as_manager)
    result = of(events, "result")[0]
    station_of = {t["id"]: t["station"] for t in app_client.get("/api/tickets", headers=as_manager).json()}
    assert len({station_of[r["ticketId"]] for r in result["ranked"]}) > 1
    assert all(r["score"] >= 0 and r["reasons"] for r in result["ranked"])


def test_triage_forbidden_for_line_cooks(app_client, as_cook):
    r = app_client.post("/api/agent/triage/stream", headers=as_cook, json={})
    assert r.status_code == 403


# --------------------------------------------------------------------------- runs and graphs


def test_agent_runs_visibility(app_client, as_sous, as_admin, as_cook):
    mine = app_client.get("/api/agent/runs", headers=as_sous).json()
    assert mine and all(r["userId"] == "u-priya" for r in mine)
    every = app_client.get("/api/agent/runs", headers=as_admin).json()
    assert len({r["userId"] for r in every}) > 1
    other = next(r for r in every if r["userId"] != "u-priya")
    assert app_client.get(f"/api/agent/runs/{other['id']}", headers=as_sous).status_code == 403
    assert app_client.get("/api/agent/runs", headers=as_cook).status_code == 403


def test_graph_diagrams(app_client, as_manager):
    graphs = app_client.get("/api/agent/graphs", headers=as_manager).json()
    assert set(graphs) == {"ask", "triage"}
    assert "classify_intent" in graphs["ask"] and "human_approval" in graphs["ask"]
    assert "rank" in graphs["triage"] and "summarize" in graphs["triage"]
