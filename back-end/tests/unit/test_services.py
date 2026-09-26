from datetime import timedelta
from pathlib import Path

import httpx2
import numpy as np
import pytest

from back_end.agents.common import resolve_think, same_target
from back_end.core.errors import PayloadTooLarge, Unprocessable
from back_end.core.timeutil import utcnow
from back_end.db.models import CrewMember, Document, Station, Ticket
from back_end.services import analytics as an
from back_end.services import ingestion as ing
from back_end.services.embeddings import HashEmbeddings, NomicTextEmbeddings, _seconds
from back_end.services.ollama import OllamaService, _remaining, _same, thinking_mode
from back_end.services.storage import FileStore, kind_for, safe_name
from tests.conftest import FakeOllama

pytestmark = pytest.mark.unit

NOW = utcnow()
STATIONS = [Station(id="grill", name="Grill", short="GRL", color="#c00"), Station(id="prep", name="Prep", short="PRP", color="#0c0")]
CREW = [
    CrewMember(id="CM-01", name="Marco", station_id="grill", title="Line Cook", initials="MR"),
    CrewMember(id="CM-02", name="Priya", station_id="grill", title="Sous Chef - Grill", initials="PS"),
    CrewMember(id="CM-06", name="Samuel", station_id="prep", title="Sous Chef - Prep", initials="SO"),
]


def doc(i: str, station: str, days: int, category: str = "sop") -> Document:
    return Document(
        id=i,
        title=f"Doc {i}",
        category=category,
        station_id=station,
        owner_id="CM-02",
        last_reviewed=NOW - timedelta(days=days),
        source="seed",
        file_name="x.md",
        file_kind="md",
        status="indexed",
    )


def ticket(
    i: str,
    priority: str,
    station: str,
    assignee: str | None,
    related: str | None = None,
    status: str = "open",
    tags: list[str] | None = None,
    age: int = 1,
) -> Ticket:
    return Ticket(
        id=i,
        title=f"Ticket {i}",
        priority=priority,
        status=status,
        station_id=station,
        assignee_id=assignee,
        reporter_id="CM-01",
        related_doc_id=related,
        created_at=NOW - timedelta(days=age),
        updated_at=NOW - timedelta(days=age),
        tags=tags or [],
    )


DOCS = [doc("D-FRESH", "grill", 10), doc("D-STALE", "prep", 300), doc("D-INC", "prep", 400, "incident")]
LK = an.Lookup.build(CREW, DOCS, STATIONS)


# --------------------------------------------------------------------------- analytics


def test_stale_rules_skip_incidents_and_archived():
    assert not an.is_stale(DOCS[0]) and an.is_stale(DOCS[1])
    assert not an.is_stale(DOCS[2]), "incident reports are historical and never stale"
    archived = doc("D-ARCH", "prep", 999)
    archived.status = "archived"
    assert not an.is_stale(archived)
    assert an.is_stale(DOCS[0], threshold=5)


def test_stale_rows_counts_citations_and_tickets():
    tickets = [ticket("T1", "high", "prep", "CM-06", "D-STALE")]
    rows = an.stale_rows(DOCS, tickets, {"D-STALE": 7}, threshold=180)
    assert [r["id"] if "id" in r else r["doc_id"] for r in rows] == ["D-STALE"]
    row = rows[0]
    assert row["days_since_review"] >= 300


def test_ownership_mismatch_and_suggested_owner():
    tickets = [
        ticket("T1", "high", "grill", "CM-01", "D-STALE"),  # prep SOP assigned to grill cook
        ticket("T2", "low", "grill", "CM-01", "D-FRESH"),  # same station: fine
        ticket("T3", "critical", "grill", "CM-01", "D-STALE", status="closed"),  # closed: ignored
    ]
    rows = an.mismatches(tickets, LK)
    assert [r["ticket_id"] for r in rows] == ["T1"]
    assert rows[0]["doc_station"] == "prep" and rows[0]["assignee_station"] == "grill"
    assert rows[0]["suggested_assignee_id"] == "CM-06" and rows[0]["doc_stale"] is True
    flows = an.mismatch_flows(rows, LK)
    assert flows == [{"source": "Prep (owner)", "target": "Grill (assigned)", "value": 1}]


def test_workload_uses_pandas_and_balances():
    tickets = [
        ticket("T1", "high", "grill", "CM-01"),
        ticket("T2", "low", "grill", "CM-01"),
        ticket("T3", "medium", "prep", "CM-06"),
        ticket("T4", "high", "prep", "CM-06", status="closed"),
    ]
    rows, imbalance = an.workload(tickets, STATIONS)
    by = {r["station"]: r for r in rows}
    assert by["grill"]["open"] == 2 and by["prep"]["open"] == 1
    assert by["grill"]["high"] == 1 and by["grill"]["low"] == 1
    assert by["grill"]["share"] == pytest.approx(66.7) and by["grill"]["load_index"] == pytest.approx(1.33)
    assert imbalance == pytest.approx(1.5)
    rows_closed, _ = an.workload(tickets, STATIONS, statuses=["closed"])
    assert sum(r["open"] for r in rows_closed) == 1


def test_rank_tickets_is_deterministic_and_explained():
    tickets = [
        ticket("T-LOW", "low", "grill", "CM-01"),
        ticket("T-CRIT", "critical", "grill", "CM-01", "D-STALE", tags=["food-safety"], age=20),
        ticket("T-HIGH", "high", "grill", "CM-02", status="blocked"),
    ]
    ranked = an.rank_tickets(tickets, LK, station="grill")
    assert [r["ticket_id"] for r in ranked] == ["T-CRIT", "T-HIGH", "T-LOW"]
    crit = ranked[0]
    assert "food safety" in crit["reasons"] and "assigned outside the owning station" in crit["reasons"]
    assert crit["score"] == pytest.approx(100 + 8 + 6 + 5 + 7, abs=0.01)
    assert an.rank_tickets([], LK) == []


def test_weekly_trend_and_opened_per_day():
    tickets = [ticket("T1", "low", "grill", None, age=1), ticket("T2", "low", "prep", None, age=8)]
    assert an.weekly_trend(tickets, weeks=3) == [0, 1, 1]
    per_day = an.opened_per_day(tickets, STATIONS, days=10)
    assert len(per_day) >= 10


# --------------------------------------------------------------------------- ingestion


def test_markdown_parsing_and_token_chunking(tmp_path: Path):
    md = tmp_path / "sop.md"
    md.write_text(
        "# Cooling SOP\n\nIntro line about cooling.\n\n## Stage 1\n\n"
        + "Cool from 135F to 70F within 2 hours. " * 80
        + "\n\n## Stage 2\n\nCool from 70F to 41F within 4 more hours.\n"
    )
    parsed = ing.parse_file(md, "md", "Cooling SOP")
    assert "Stage 1" in parsed.markdown and len(parsed.sections) >= 2
    chunks = ing.chunk(parsed.sections, "Cooling SOP", chunk_tokens=120, overlap=20)
    assert len(chunks) >= 4
    assert all(c.tokens <= 160 for c in chunks)
    assert all(c.text.startswith("Cooling SOP") for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert ing.summary_from(parsed.markdown)
    assert ing.count_tokens("hello world") == 2


def test_parse_cache_round_trip(tmp_path: Path):
    md = tmp_path / "a.md"
    md.write_text("# Title\n\nBody text that is long enough to keep.\n")
    first = ing.parse_cached(md, "md", "Title", tmp_path / "cache")
    assert list((tmp_path / "cache").glob("*.json"))
    second = ing.parse_cached(md, "md", "Title", tmp_path / "cache")
    assert first.markdown == second.markdown


# --------------------------------------------------------------------------- embeddings


def test_keep_alive_to_seconds():
    assert _seconds("15m") == 900 and _seconds("2h") == 7200 and _seconds("45s") == 45
    assert _seconds(30) == 30 and _seconds("120") == 120


def test_hash_embeddings_are_normalised_and_similar_for_overlap():
    e = HashEmbeddings(128)
    a, b, c = (np.array(v) for v in e.embed_documents(["fryer oil change", "change the fryer oil", "pastry dough"]))
    assert np.linalg.norm(a) == pytest.approx(1.0, abs=1e-5)
    assert a @ b > a @ c


def test_nomic_task_prefixes(monkeypatch):
    emb = NomicTextEmbeddings("http://ollama.test", "nomic-embed-text")
    seen: dict[str, list[str]] = {}
    inner = type(emb._inner)
    monkeypatch.setattr(inner, "embed_documents", lambda self, texts: seen.setdefault("docs", texts) and [[0.0]] * len(texts))
    monkeypatch.setattr(inner, "embed_query", lambda self, text: seen.setdefault("query", [text]) and [0.0])
    emb.embed_documents(["fryer oil"])
    emb.embed_query("how often")
    assert seen["docs"] == ["search_document: fryer oil"]
    assert seen["query"] == ["search_query: how often"]


# --------------------------------------------------------------------------- storage


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_storage_validation(tmp_path: Path):
    store = FileStore(tmp_path, max_bytes=1024)
    assert safe_name("../../etc/pass wd.pdf") == "pass-wd.pdf"
    assert kind_for("photo.JPG") == "jpg" and kind_for("notes.exe") is None
    with pytest.raises(Unprocessable):
        store.validate("virus.exe", b"MZ", {"pdf"})
    with pytest.raises(Unprocessable):
        store.validate("empty.md", b"", {"md"})
    with pytest.raises(PayloadTooLarge):
        store.validate("big.md", b"x" * 2048, {"md"})
    with pytest.raises(Unprocessable, match="does not look like"):
        store.validate("fake.pdf", _png_bytes(), {"pdf"})
    assert store.validate("photo.png", _png_bytes(), {"png", "jpg"}) == "png"
    assert store.validate("notes.md", b"# hello\n", {"md"}) == "md"


# --------------------------------------------------------------------------- Ollama registry


def test_thinking_modes_and_name_matching():
    assert thinking_mode("llama3.2:3b", ["completion", "tools"]) == "none"
    assert thinking_mode("qwen3:4b-q4_K_M", ["completion", "thinking"]) == "toggle"
    assert thinking_mode("qwen3:4b", ["completion", "thinking"]) == "always"
    assert thinking_mode("deepseek-r1:1.5b", ["completion", "thinking"]) == "always"
    assert _same("phi4-mini", "phi4-mini:latest") and not _same("qwen3:4b", "qwen3:4b-q4_K_M")
    assert _remaining("2099-01-01T00:00:00Z") == "forever" and _remaining("2000-01-01T00:00:00Z") == "unloading"
    assert resolve_think("always", False) is True and resolve_think("toggle", False) is False
    assert resolve_think("none", True) is None
    assert same_target({"sku": "A"}, {"sku": "A"}) and not same_target({"ticketId": "T1"}, {"ticketId": "T2"})


async def test_model_registry_is_dynamic_and_loads_on_demand():
    fake = FakeOllama()
    svc = OllamaService(
        "http://ollama.test",
        supported=["llama3.2:3b", "qwen3:4b-q4_K_M", "gemma4:e2b"],
        keep_alive="15m",
        timeout=10,
        transport=httpx2.MockTransport(fake),
    )
    try:
        models = await svc.list_models(force=True)
        names = [m.name for m in models]
        assert names[:3] == ["llama3.2:3b", "qwen3:4b-q4_K_M", "gemma4:e2b"], "supported models first"
        assert names[-1] == "nomic-embed-text:latest", "embedding models last"
        extra = next(m for m in models if m.name == "phi4-mini:latest")
        assert extra.supported is False and extra.thinking == "none"
        gemma = next(m for m in models if m.name == "gemma4:e2b")
        assert gemma.thinking == "toggle" and "vision" in gemma.capabilities and gemma.context_length == 131072
        chat = await svc.chat_models()
        assert all(not m.embedding for m in chat)

        fake.models["mistral:7b"] = {**fake.models["llama3.2:3b"], "family": "mistral"}
        assert "mistral:7b" in [m.name for m in await svc.list_models(force=True)]

        info = await svc.ensure_loaded("qwen3:4b-q4_K_M")
        assert info["was_loaded"] is False and "qwen3:4b-q4_K_M" in fake.loaded
        assert (await svc.ensure_loaded("qwen3:4b-q4_K_M"))["was_loaded"] is True
        assert (await svc.get("qwen3:4b-q4_K_M")).loaded is True
        await svc.unload("qwen3:4b-q4_K_M")
        assert "qwen3:4b-q4_K_M" not in fake.loaded
        assert await svc.version() == "0.35.0"
    finally:
        await svc.aclose()


SAMPLES = Path(__file__).resolve().parents[2] / "sample_documents"


@pytest.mark.parametrize(
    ("name", "kind", "needle"),
    [
        ("lemon-olive-oil-cake.docx", "docx", "olive oil"),
        ("quick-pickles-and-brines.xlsx", "xlsx", "|"),
        ("fry-station-quick-reference.pptx", "pptx", "fry"),
        ("receiving-deliveries-and-cold-chain-checks.pdf", "pdf", "deliver"),
        ("ice-machine-cleaning-and-sanitizing.md", "md", "sanitiz"),
    ],
)
def test_parse_real_files_to_markdown_sections(tmp_path, name, kind, needle):
    parsed = ing.parse_cached(SAMPLES / name, kind, name.rsplit(".", 1)[0], tmp_path)
    assert needle in parsed.markdown.lower()
    assert parsed.sections and all(s.text.strip() for s in parsed.sections)
    chunks = ing.chunk(parsed.sections, name)
    assert chunks and all(0 < c.tokens <= 460 for c in chunks)
    again = ing.parse_cached(SAMPLES / name, kind, name, tmp_path)
    assert again.markdown == parsed.markdown and any(tmp_path.iterdir())
