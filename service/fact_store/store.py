from __future__ import annotations

import json
import hashlib
import os

from service import config
from service.clients import openai_client
from service.models import FactBullet, ResumeSkeleton


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_facts(path: str = config.FACT_STORE_PATH) -> list[FactBullet]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [FactBullet(**item) for item in raw]


def load_skeleton(path: str = config.SKELETON_PATH) -> ResumeSkeleton:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return ResumeSkeleton(**raw)


def _load_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_cache(path: str, cache: dict) -> None:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def get_fact_embeddings(
    facts: list[FactBullet],
    cache_path: str = config.EMBEDDING_CACHE_PATH,
) -> dict[str, list[float]]:
    """
    Returns {fact_id: embedding_vector}. Only calls the embeddings API for
    facts that are new or whose text changed since the last run.
    """
    cache = _load_cache(cache_path)
    result: dict[str, list[float]] = {}
    to_embed: list[FactBullet] = []

    for fact in facts:
        h = _text_hash(fact.text)
        cached_entry = cache.get(fact.id)
        if cached_entry and cached_entry.get("hash") == h:
            result[fact.id] = cached_entry["embedding"]
        else:
            to_embed.append(fact)

    if to_embed:
        response = openai_client.embeddings.create(
            model=config.EMBEDDING_MODEL,
            input=[f.text for f in to_embed],
        )
        for fact, item in zip(to_embed, response.data):
            result[fact.id] = item.embedding
            cache[fact.id] = {"hash": _text_hash(fact.text), "embedding": item.embedding}
        _save_cache(cache_path, cache)

    return result


def embed_query(text: str) -> list[float]:
    response = openai_client.embeddings.create(model=config.EMBEDDING_MODEL, input=[text])
    return response.data[0].embedding
