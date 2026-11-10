"""ChromaDB vector store integration for enterprise refund policies.

Provides local, embedded semantic vector search using ChromaDB. Supports
both persistent on-disk storage (for server runtimes) and isolated / ephemeral
stores (for sandbox testing).
"""
import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from app import config

logger = logging.getLogger(__name__)


class DeterministicFallbackEmbeddingFunction(EmbeddingFunction):
    """Offline, deterministic embedding function for fast isolated testing.
    
    Generates normalized 128-dimensional dense vector embeddings based on
    token hashing, ensuring fast, repeatable offline similarity scoring.
    """
    def __init__(self, dim: int = 128):
        self.dim = dim

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: List[List[float]] = []
        for text in input:
            vec = [0.0] * self.dim
            words = re.findall(r"[A-Za-z0-9$]+", text.lower())
            if not words:
                embeddings.append(vec)
                continue
            for w in words:
                idx = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [round(x / norm, 6) for x in vec]
            embeddings.append(vec)
        return embeddings


def _get_default_embedding_function() -> EmbeddingFunction:
    """Attempt to load Chroma's local ONNX MiniLM model, falling back to deterministic offline embeddings."""
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        fn = DefaultEmbeddingFunction()
        # Verify it can execute without network error
        fn(["test"])
        return fn
    except Exception as exc:
        logger.info("Chroma ONNX model not cached or offline, using fallback embedding function: %s", exc)
        return DeterministicFallbackEmbeddingFunction()


class PolicyVectorStore:
    """Interface to ChromaDB collection for refund policies."""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        in_memory: bool = False,
        collection_name: Optional[str] = None,
        embedding_function: Optional[EmbeddingFunction] = None,
    ):
        self.collection_name = collection_name or config.CHROMA_COLLECTION_NAME
        self.embedding_function = embedding_function or _get_default_embedding_function()

        if in_memory:
            self.client: ClientAPI = chromadb.EphemeralClient()
        else:
            path = Path(persist_dir or config.CHROMA_PERSIST_DIR)
            path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(path))

        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )

    def index_policy(
        self,
        policy_id: str,
        title: str,
        content: str,
        tags: str = "",
        uploaded_by: str = "system",
    ) -> Dict[str, Any]:
        """Upsert a policy document into ChromaDB."""
        document_text = f"{title}\n{content}\nTags: {tags}".strip()
        metadata = {
            "id": policy_id,
            "title": title,
            "tags": tags,
            "uploaded_by": uploaded_by,
        }
        self.collection.upsert(
            ids=[policy_id],
            documents=[document_text],
            metadatas=[metadata],
        )
        return {"id": policy_id, "title": title, "status": "INDEXED"}

    def search_policies(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """Search policies by semantic similarity."""
        if self.collection.count() == 0:
            return []

        n_results = min(top_k, self.collection.count())
        res = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

        results: List[Dict[str, Any]] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metadatas = res.get("metadatas", [[]])[0]
        distances = res.get("distances", [[]])[0]

        for pid, doc, meta, dist in zip(ids, docs, metadatas, distances):
            # In cosine distance: similarity = 1 - distance
            score = round(max(0.0, 1.0 - dist), 4)
            # Recover content by splitting title header
            lines = doc.split("\n", 1)
            raw_content = lines[1].rsplit("\nTags:", 1)[0].strip() if len(lines) > 1 else doc
            results.append({
                "id": pid,
                "title": meta.get("title", ""),
                "content": raw_content,
                "tags": meta.get("tags", ""),
                "uploaded_by": meta.get("uploaded_by", ""),
                "score": score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def delete_policy(self, policy_id: str) -> None:
        """Delete a policy by id from the collection."""
        self.collection.delete(ids=[policy_id])

    def count(self) -> int:
        """Count indexed policies."""
        return self.collection.count()

    def reset(self) -> None:
        """Clear all policies from the collection."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )


_global_store: Optional[PolicyVectorStore] = None


def get_vector_store(
    persist_dir: Optional[Path] = None,
    in_memory: bool = False,
    force_new: bool = False,
) -> PolicyVectorStore:
    """Factory helper to obtain a vector store instance."""
    global _global_store
    if force_new or in_memory or persist_dir is not None:
        return PolicyVectorStore(persist_dir=persist_dir, in_memory=in_memory)
    if _global_store is None:
        _global_store = PolicyVectorStore()
    return _global_store
