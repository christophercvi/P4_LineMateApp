import time

import jwt
import pytest

from back_end.auth.permissions import Actor, can, deny_reason
from back_end.core import security
from back_end.core.timeutil import days_since, iso, resolve_relative, utcnow

pytestmark = pytest.mark.unit

COOK = Actor("u-marco", "line_cook", "grill", "CM-01", "Marco")
SOUS = Actor("u-priya", "sous_chef", "grill", "CM-02", "Priya")
PREP_SOUS = Actor("u-samuel", "sous_chef", "prep", "CM-06", "Samuel")
KM = Actor("u-elena", "kitchen_manager", None, "CM-11", "Elena")
ADMIN = Actor("u-admin", "admin", None, None, "Alex")
SERVICE = Actor("u-svc-mcp", "service", None, None, "MCP client")


# --------------------------------------------------------------------------- argon2 + JWT


def test_argon2_hash_and_verify():
    h = security.hash_password("Hearthline#2026")
    assert h.startswith("$argon2id$")
    assert security.verify_password("Hearthline#2026", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "not-a-hash")
    assert security.needs_rehash(h) is False


def test_jwt_round_trip_and_claims():
    token = security.create_access_token({"sub": "u-marco", "role": "line_cook"}, minutes=5)
    claims = security.decode_access_token(token)
    assert claims["sub"] == "u-marco" and claims["role"] == "line_cook"
    assert claims["iss"] == "linemate" and claims["exp"] > claims["iat"]


def test_jwt_rejects_tampering_expiry_and_missing_subject():
    token = security.create_access_token({"sub": "u-marco"}, minutes=5)
    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))
    settings = security.get_settings()
    expired = jwt.encode({"sub": "u", "iat": 1, "exp": 2, "iss": "linemate"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(expired)
    no_sub = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 60, "iss": "linemate"}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        security.decode_access_token(no_sub)


def test_signed_file_urls():
    url = security.signed_file_url("FA-0001", ttl=60)
    assert url.startswith("/api/files/FA-0001?exp=")
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert security.verify_file_signature("FA-0001", exp, sig)
    assert not security.verify_file_signature("FA-0002", exp, sig)
    assert not security.verify_file_signature("FA-0001", exp, "0" * 32)
    assert not security.verify_file_signature("FA-0001", int(time.time()) - 1, sig)


def test_service_tokens_have_lookup_prefix():
    token, prefix = security.new_service_token()
    assert token.startswith("lm_svc_") and len(token) > 40
    assert prefix == security.token_lookup_prefix(token) and len(prefix) == len("lm_svc_") + 8


# --------------------------------------------------------------------------- permission matrix


def test_line_cook_permissions():
    assert can.ask(COOK) and can.create_ticket(COOK) and can.comment(COOK) and can.attach_to_ticket(COOK)
    assert not can.see_incident_reports(COOK)
    assert not can.upload_any(COOK) and not can.upload_category(COOK, "recipe", "grill")
    assert can.change_status(COOK, "grill", "CM-01") and not can.change_status(COOK, "grill", "CM-03")
    assert not can.assign(COOK, "grill") and not can.lower_priority_or_close(COOK, "grill")
    assert not can.view_audits(COOK) and not can.run_triage(COOK) and not can.view_approvals(COOK)


def test_sous_chef_is_limited_to_own_station_and_non_sop_uploads():
    assert can.upload_category(SOUS, "recipe", "grill")
    assert can.upload_category(SOUS, "incident", "grill")
    assert not can.upload_category(SOUS, "sop", "grill")
    assert not can.upload_category(SOUS, "recipe", "prep")
    assert can.mark_reviewed(SOUS, "grill") and not can.mark_reviewed(SOUS, "prep")
    assert can.assign(SOUS, "grill") and not can.assign(SOUS, "pastry")
    assert can.run_triage(SOUS) and can.view_ownership_audit(SOUS)
    assert not can.approve(SOUS) and can.request_approval(SOUS)
    assert not can.archive_document(SOUS) and not can.analytics_all_stations(SOUS)


def test_kitchen_manager_and_admin_separation_of_duties():
    assert can.upload_category(KM, "sop", "prep") and can.archive_document(KM) and can.approve(KM)
    assert can.analytics_all_stations(KM) and can.view_ownership_audit(KM)
    assert can.admin(ADMIN) and can.manage_tokens(ADMIN) and can.view_all_runs(ADMIN)
    assert not can.approve(ADMIN) and not can.view_ownership_audit(ADMIN) and not can.run_triage(ADMIN)
    assert "Separation of duties" in deny_reason("approve", ADMIN)


def test_service_actor_and_mcp_roles():
    assert not can.ask(SERVICE) and can.create_ticket(SERVICE) and can.request_approval(SERVICE)
    assert can.call_mcp_tool(SOUS, ("sous_chef", "kitchen_manager"))
    assert not can.call_mcp_tool(COOK, ("sous_chef", "kitchen_manager"))


def test_deny_reasons_are_role_specific():
    assert "SOPs are published by the Kitchen Manager" in deny_reason("upload", SOUS)
    assert "can't publish" in deny_reason("upload", COOK)
    assert "Kitchen Manager" in deny_reason("approve", SOUS)
    for kind in ("status", "lower", "assign", "review", "archive"):
        assert deny_reason(kind, COOK)


def test_actor_who_prefers_crew_id():
    assert COOK.who == "CM-01" and ADMIN.who == "u-admin"


# --------------------------------------------------------------------------- time helpers


def test_relative_seed_times():
    from datetime import UTC, datetime, timedelta

    anchor = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert resolve_relative("@ago:3", anchor) == anchor - timedelta(hours=3)
    assert resolve_relative("@agod:10", anchor) == (anchor - timedelta(days=10)).date()
    assert resolve_relative("plain", anchor) == "plain"
    assert days_since(anchor - timedelta(days=200), anchor) == 200
    assert iso(None) is None and iso(utcnow()).endswith("Z")


def test_relative_paths_in_settings_resolve_from_the_backend_folder(tmp_path):
    from back_end.core.config import BACKEND_ROOT, Settings

    s = Settings(storage_dir="storage", chroma_dir="var/chroma", parse_cache_dir=None, seed_documents_dir=tmp_path)
    assert s.storage_dir == (BACKEND_ROOT / "storage").resolve()
    assert s.chroma_dir == (BACKEND_ROOT / "var" / "chroma").resolve()
    assert s.parse_cache_dir is None
    assert s.seed_documents_dir == tmp_path
