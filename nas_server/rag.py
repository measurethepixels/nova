"""
RAG (Retrieval-Augmented Generation) engine for the SeeStar agent.

Embeds Claude assessments, processing evaluations, and step reasoning using
Ollama's nomic-embed-text model. Stores vectors as BLOBs in SQLite.
Semantic search uses cosine similarity computed in-memory with numpy.

Pull the embedding model once: ollama pull nomic-embed-text
"""
import logging

import numpy as np
import requests

from nas_server.config import settings

log = logging.getLogger(__name__)

_EMBED_MODEL = "nomic-embed-text"
_EMBED_TIMEOUT = 30  # seconds per embedding call


def _ollama_url() -> str:
    return settings.get("ollama_url", "http://localhost:11434")


def embed(text: str) -> list[float]:
    """Generate a float embedding vector via Ollama nomic-embed-text."""
    resp = requests.post(
        f"{_ollama_url()}/api/embeddings",
        json={"model": _EMBED_MODEL, "prompt": text},
        timeout=_EMBED_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _embed_bytes(text: str) -> bytes:
    vec = embed(text)
    return np.array(vec, dtype=np.float32).tobytes()


def build_index(force: bool = False) -> int:
    """
    Embed all unindexed assessment/processing records and store them.
    Skips records already in rag_embeddings (idempotent).
    Returns the number of newly indexed records.
    """
    from nas_server.database import (
        get_conn, get_indexed_source_ids, save_rag_embedding,
    )

    # Verify embedding model is available
    try:
        embed("test")
    except Exception as e:
        log.warning(f"[rag] nomic-embed-text unavailable, skipping index build: {e}")
        return 0

    sources = [
        # (doc_type, sql, text_col)
        ("assessment",
         "SELECT id, target, recommendation FROM claude_assessments WHERE recommendation IS NOT NULL AND recommendation != ''",
         "recommendation"),
        ("processing_run",
         "SELECT id, target, critical_eval FROM processing_runs WHERE critical_eval IS NOT NULL AND critical_eval != ''",
         "critical_eval"),
        ("processing_step",
         "SELECT id, target, claude_reasoning FROM processing_history WHERE claude_reasoning IS NOT NULL AND claude_reasoning != ''",
         "claude_reasoning"),
    ]

    total = 0
    with get_conn() as conn:
        for doc_type, sql, text_col in sources:
            existing = get_indexed_source_ids(doc_type)
            rows = conn.execute(sql).fetchall()
            new_rows = [(r[0], r[1], r[2]) for r in rows if r[0] not in existing]
            if not new_rows:
                continue
            log.info(f"[rag] indexing {len(new_rows)} {doc_type} records...")
            for i, (source_id, target, text) in enumerate(new_rows):
                if not text:
                    continue
                try:
                    emb_bytes = _embed_bytes(text)
                    save_rag_embedding(doc_type, source_id, target, text, emb_bytes)
                    total += 1
                    if (i + 1) % 50 == 0:
                        log.info(f"[rag]   {i+1}/{len(new_rows)} {doc_type}")
                except Exception as e:
                    log.warning(f"[rag] failed to embed {doc_type} id={source_id}: {e}")
    log.info(f"[rag] build_index complete — {total} new records indexed")
    return total


def index_record(doc_type: str, source_id: int, target: str, text: str) -> bool:
    """Embed and store a single record. Returns True if indexed, False if skipped/failed."""
    from nas_server.database import save_rag_embedding
    if not text:
        return False
    try:
        emb_bytes = _embed_bytes(text)
        save_rag_embedding(doc_type, source_id, target, text, emb_bytes)
        return True
    except Exception as e:
        log.warning(f"[rag] index_record failed for {doc_type} id={source_id}: {e}")
        return False


def semantic_search(query: str, top_k: int = 5) -> list[dict]:
    """
    Find the top_k most semantically similar records to query.
    Returns list of dicts: {doc_type, source_id, target, text_snippet, score}.
    """
    from nas_server.database import get_rag_embeddings

    rows = get_rag_embeddings()
    if not rows:
        return []

    try:
        q_vec = np.array(embed(query), dtype=np.float32)
    except Exception as e:
        log.warning(f"[rag] failed to embed query: {e}")
        return []

    # Build matrix: (N, D)
    dim = len(q_vec)
    embeddings = np.stack([
        np.frombuffer(r["embedding"], dtype=np.float32)
        for r in rows
    ])  # (N, D)

    # Cosine similarity
    q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-9
    scores = (embeddings / norms) @ q_norm  # (N,)

    top_k = min(top_k, len(rows))
    top_idx = np.argpartition(scores, -top_k)[-top_k:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]

    return [
        {
            "doc_type": rows[i]["doc_type"],
            "source_id": rows[i]["source_id"],
            "target": rows[i]["target"],
            "text_snippet": rows[i]["text_snippet"],
            "score": float(scores[i]),
        }
        for i in top_idx
    ]
