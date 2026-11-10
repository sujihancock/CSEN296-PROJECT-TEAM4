"""Unit tests for ChromaDB vector store integration and RAG policy retrieval."""
import sqlite3
import uuid
import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.retriever import Retriever, get_store_for_conn
from app.vector_store import PolicyVectorStore


@pytest.fixture
def mem_store():
    name = f"test_{uuid.uuid4().hex[:8]}"
    return PolicyVectorStore(in_memory=True, collection_name=name)


def test_vector_store_index_and_semantic_search(mem_store):
    mem_store.index_policy(
        "POL-001",
        "Physical Goods Refund Policy",
        "Physical items can be returned within 30 days for a full refund.",
        "physical refund 30 days",
    )
    mem_store.index_policy(
        "POL-003",
        "Digital Download License Policy",
        "E-books and digital software downloads cannot be refunded once downloaded.",
        "digital ebook license non-refundable",
    )

    hits = mem_store.search_policies("I bought an electronic book PDF and want my money back", top_k=1)
    assert len(hits) == 1
    assert hits[0]["id"] == "POL-003"
    assert "Digital" in hits[0]["title"]


def test_vector_store_upsert(mem_store):
    mem_store.index_policy("POL-010", "Initial Title", "Old policy text", "tag1")
    assert mem_store.count() == 1

    mem_store.index_policy("POL-010", "Updated Title", "New updated policy text", "tag2")
    assert mem_store.count() == 1
    hits = mem_store.search_policies("New updated policy text", top_k=1)
    assert hits[0]["title"] == "Updated Title"


def test_vector_store_delete(mem_store):
    mem_store.index_policy("POL-020", "Temporary Policy", "Temporary content", "")
    assert mem_store.count() == 1
    mem_store.delete_policy("POL-020")
    assert mem_store.count() == 0
    assert mem_store.search_policies("Temporary", top_k=1) == []


def test_vector_store_reset(mem_store):
    mem_store.index_policy("POL-001", "P1", "C1", "")
    mem_store.index_policy("POL-002", "P2", "C2", "")
    assert mem_store.count() == 2
    mem_store.reset()
    assert mem_store.count() == 0


def test_vector_store_isolation():
    store_a = PolicyVectorStore(in_memory=True, collection_name=f"iso_{uuid.uuid4().hex[:8]}")
    store_b = PolicyVectorStore(in_memory=True, collection_name=f"iso_{uuid.uuid4().hex[:8]}")

    store_a.index_policy("POL-A", "Policy A", "Exclusively in store A", "")
    assert store_a.count() == 1
    assert store_b.count() == 0
    assert store_b.search_policies("Policy A", top_k=1) == []


def test_retriever_dual_write_and_sync(tmp_path):
    db_file = tmp_path / "test_dual_write.db"
    init_db(seed=False, path=db_file)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    retriever = Retriever()
    res = retriever.add_policy(
        conn,
        "POL-TEST",
        "Test Policy",
        "Customers may claim refunds if defective.",
        "defective return",
        "admin",
    )
    assert res["status"] == "INDEXED"

    # Verify SQLite row
    row = conn.execute("SELECT * FROM policies WHERE id = 'POL-TEST'").fetchone()
    assert row is not None
    assert row["title"] == "Test Policy"

    # Verify semantic search through retriever
    results = retriever.search(conn, "broken and defective product claim")
    assert len(results) >= 1
    assert results[0]["id"] == "POL-TEST"
    conn.close()


def test_vector_stats_endpoint():
    with TestClient(app) as client:
        r = client.get("/policies/vector-stats", headers={"X-API-Key": "admin-key"})
        assert r.status_code == 200
        data = r.json()
        assert data["engine"] == "ChromaDB"
        assert "indexed_vectors" in data
