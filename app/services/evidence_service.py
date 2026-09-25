"""Evidence Retrieval Service — REUSES the existing Gemini embedding endpoint
and the existing Qdrant Cloud instance (same credentials via env vars).
Dedicated collections `edunation_assessment_{dim}` on the same cluster —
no duplicate vector infrastructure. Honest keyword fallback if Qdrant or
Gemini are unavailable."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import get_settings
from ..core_log import get_logger

log = get_logger("evidence")

DIM = 384  # local fallback dimension
_TOKEN = re.compile(r"[a-z0-9\u0600-\u06FF_]+")

# circuit breaker: after a connection failure, skip Qdrant for 60s and use the
# keyword fallback immediately (avoids paying a timeout on every retrieval).
import time as _time
_QDRANT_DEAD_UNTIL = 0.0


@dataclass
class RetrievedEvidence:
    material_id: str
    material_title: str
    page: Optional[int]
    slide: Optional[int]
    section: Optional[str]
    text: str
    relevance: float


# ------------------------------------------------------------- embeddings
def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    tokens = _TOKEN.findall(text.lower())
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIM
        vec[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


async def _gemini_embed(texts: list[str]) -> Optional[list[list[float]]]:
    settings = get_settings()
    if not settings.embeddings_available:
        return None
    vectors = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for text in texts:
                resp = await client.post(
                    f"{settings.gemini_base}/models/{settings.embed_model}:embedContent",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json={"content": {"parts": [{"text": text[:8000]}]},
                          "outputDimensionality": settings.embed_dim})
                if resp.status_code >= 400:
                    log.warning("gemini_embed_failed status=%s", resp.status_code)
                    return None
                vectors.append([float(x) for x in resp.json()["embedding"]["values"]])
        return vectors
    except httpx.HTTPError as exc:
        log.warning("gemini_embed_error %s", str(exc)[:120])
        return None


def _run(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ------------------------------------------------------------ qdrant (reuse)
def _collection(dim: int) -> str:
    return f"edunation_assessment_{dim}"


def _qdrant_headers() -> dict:
    settings = get_settings()
    return {"api-key": settings.qdrant_api_key}


def _qdrant_alive() -> bool:
    return _time.time() >= _QDRANT_DEAD_UNTIL


def _qdrant_mark_dead() -> None:
    global _QDRANT_DEAD_UNTIL
    _QDRANT_DEAD_UNTIL = _time.time() + 60.0


async def _qdrant_ensure(dim: int) -> bool:
    settings = get_settings()
    if not (settings.qdrant_url and settings.qdrant_api_key):
        return False
    if not _qdrant_alive():
        return False
    name = _collection(dim)
    try:
        async with httpx.AsyncClient(base_url=settings.qdrant_url,
                                     headers=_qdrant_headers(), timeout=30) as client:
            exists = await client.get(f"/collections/{name}")
            if exists.status_code != 200:
                created = await client.put(f"/collections/{name}", json={
                    "vectors": {"size": dim, "distance": "Cosine"}})
                if created.status_code >= 400:
                    return False
            for field in ("course_id", "material_id"):
                await client.put(f"/collections/{name}/index",
                                 json={"field_name": field, "field_schema": "keyword"},
                                 params={"wait": "true"})
        return True
    except httpx.HTTPError as exc:
        log.warning("qdrant_ensure_failed %s", str(exc)[:120])
        _qdrant_mark_dead()
        return False


async def _qdrant_upsert(dim: int, ids: list[str], vectors: list[list[float]],
                         payloads: list[dict]) -> bool:
    settings = get_settings()
    if not await _qdrant_ensure(dim):
        return False
    try:
        async with httpx.AsyncClient(base_url=settings.qdrant_url,
                                     headers=_qdrant_headers(), timeout=60) as client:
            resp = await client.put(
                f"/collections/{_collection(dim)}/points",
                json={"points": [{"id": i, "vector": v, "payload": p}
                                 for i, v, p in zip(ids, vectors, payloads)]})
            return resp.status_code < 400
    except httpx.HTTPError as exc:
        log.warning("qdrant_upsert_failed %s", str(exc)[:120])
        _qdrant_mark_dead()
        return False


async def _qdrant_search(dim: int, vector: list[float], course_id: str,
                         k: int) -> list[tuple[float, dict]]:
    settings = get_settings()
    if not (settings.qdrant_url and settings.qdrant_api_key):
        return []
    if not _qdrant_alive():
        return []
    try:
        async with httpx.AsyncClient(base_url=settings.qdrant_url,
                                     headers=_qdrant_headers(), timeout=30) as client:
            resp = await client.post(
                f"/collections/{_collection(dim)}/points/query",
                json={"query": vector, "limit": k, "with_payload": True,
                      "filter": {"must": [{"key": "course_id",
                                           "match": {"value": course_id}}]}})
            if resp.status_code >= 400:
                log.warning("qdrant_search_failed status=%s", resp.status_code)
                return []
            points = resp.json().get("result", {}).get("points", [])
            return [(p.get("score", 0.0), p.get("payload") or {}) for p in points]
    except httpx.HTTPError as exc:
        log.warning("qdrant_search_error %s", str(exc)[:120])
        _qdrant_mark_dead()
        return []


# ------------------------------------------------------------------ public
def index_chunks(course_id: str, material_id: str, material_title: str,
                 chunks) -> int:
    """Embed + upsert material chunks into the shared Qdrant cluster."""
    texts = [c.text for c in chunks]
    vectors = _run(_gemini_embed(texts))
    provider_dim = get_settings().embed_dim if vectors else DIM
    if vectors is None:
        vectors = [_hash_embed(t) for t in texts]
    payloads = [{"course_id": course_id, "material_id": material_id,
                 "material_title": material_title, "text": c.text[:4000],
                 "page": c.page, "slide": c.slide, "section": c.section}
                for c in chunks]
    ok = _run(_qdrant_upsert(provider_dim, [c.id for c in chunks], vectors, payloads))
    if not ok:
        log.warning("evidence_index_local_only material=%s", material_title[:40])
    return len(chunks)


def retrieve(course_id: str, query: str, k: Optional[int] = None,
             boost_text: str = "") -> list[RetrievedEvidence]:
    """Evidence retrieval for evaluation — vector search primary (both indexed
    dimension namespaces tried, so mixed Gemini/hash indexing still hits),
    score-ranked keyword fallback over stored chunks as the guaranteed
    backbone. ``boost_text`` (question topic etc.) weights the ranking."""
    settings = get_settings()
    k = k or settings.evidence_top_k
    vectors = _run(_gemini_embed([query[:1500]]))
    hits: list = []
    if vectors:
        # primary: the Gemini-indexed collection
        hits = _run(_qdrant_search(settings.embed_dim, vectors[0], course_id, k))
        if not hits and settings.embed_dim != DIM:
            # material may have been indexed while Gemini was unavailable
            # (hash-embed 384-dim collection) — retry that namespace
            hits = _run(_qdrant_search(DIM, _hash_embed(query), course_id, k))
    else:
        hits = _run(_qdrant_search(DIM, _hash_embed(query), course_id, k))
    if hits:
        return [RetrievedEvidence(
            material_id=h["payload"].get("material_id", ""),
            material_title=h["payload"].get("material_title", ""),
            page=h["payload"].get("page"), slide=h["payload"].get("slide"),
            section=h["payload"].get("section"),
            text=h["payload"].get("text", ""), relevance=round(score, 4))
            for score, h in sorted(hits, key=lambda x: -x[0])]
    # keyword fallback (logged, honest) — score-ranked, never a hard threshold
    return _keyword_search(course_id, query, k, boost_text=boost_text)


def _norm_token(token: str) -> str:
    """Light normalization so 'keys' matches 'key', 'joins' matches 'join',
    'comparing' matches 'compare' — enough for evidence lookup without an
    NLP dependency."""
    for suffix in ("ies", "es", "s", "ing", "ed", "e"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokens_of(text: str) -> set[str]:
    return {_norm_token(t) for t in _TOKEN.findall((text or "").lower()) if len(t) > 3}


def _keyword_search(course_id: str, query: str, k: int,
                    boost_text: str = "") -> list[RetrievedEvidence]:
    """Robust evidence lookup over the stored material chunks.

    Scoring: 2 points per match on the QUESTION/TOPIC tokens (boost) + 1 per
    match on the general query tokens, ranked; any chunk sharing at least one
    meaningful token is a candidate — no hard overlap threshold that silently
    discarded evidence on longer answers."""
    from ..db import db
    from ..models import CourseMaterial, MaterialChunk
    query_tokens = _tokens_of(query)
    boost_tokens = _tokens_of(boost_text)
    if not query_tokens and not boost_tokens:
        return []
    with db().session_scope() as session:
        rows = (session.query(MaterialChunk, CourseMaterial.filename)
                .join(CourseMaterial, MaterialChunk.material_id == CourseMaterial.id)
                .filter(MaterialChunk.course_id == course_id)
                .limit(2000).all())
    scored = []
    for chunk, filename in rows:
        chunk_tokens = _tokens_of(chunk.text)
        if not chunk_tokens:
            continue
        overlap_q = len(query_tokens & chunk_tokens)
        overlap_b = len(boost_tokens & chunk_tokens)
        score = 2 * overlap_b + overlap_q
        if score <= 0:
            continue
        denom = max(1, 2 * len(boost_tokens) + len(query_tokens))
        scored.append(RetrievedEvidence(
            material_id=chunk.material_id, material_title=filename,
            page=chunk.page, slide=chunk.slide, section=chunk.section,
            text=chunk.text, relevance=round(score / denom, 4)))
    scored.sort(key=lambda e: -e.relevance)
    if scored:
        log.info("evidence_keyword_fallback hits=%d", len(scored[:k]))
    return scored[:k]
