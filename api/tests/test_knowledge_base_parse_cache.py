"""Tests for the KB parse cache (Redis-backed)."""

import json

import pytest

from api.services.knowledge_base.parse_cache import (
    delete_cached_parse,
    get_cached_parse,
    set_cached_parse,
)


class _FakeRedis:
    """Minimal async Redis double for the cache contract."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, key: str):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", _get_redis
    )
    return fake


async def test_set_then_get_returns_payload(fake_redis):
    payload = {"full_text": "hello", "chunks": [], "docling_metadata": {}}
    await set_cached_parse("abc123", payload)
    result = await get_cached_parse("abc123")
    assert result == payload


async def test_get_returns_none_for_missing_key(fake_redis):
    assert await get_cached_parse("does-not-exist") is None


async def test_set_uses_30_minute_ttl(fake_redis):
    await set_cached_parse("abc123", {"full_text": "hi", "chunks": [], "docling_metadata": {}})
    assert fake_redis.ttls["kb:parse:abc123"] == 30 * 60


async def test_delete_removes_key(fake_redis):
    await set_cached_parse("abc123", {"full_text": "hi", "chunks": [], "docling_metadata": {}})
    await delete_cached_parse("abc123")
    assert await get_cached_parse("abc123") is None


async def test_get_swallows_redis_errors_returns_none(monkeypatch):
    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", boom
    )
    assert await get_cached_parse("abc123") is None


async def test_set_swallows_redis_errors(monkeypatch):
    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", boom
    )
    # Should not raise.
    await set_cached_parse("abc123", {"full_text": "x", "chunks": [], "docling_metadata": {}})


async def test_delete_swallows_redis_errors(monkeypatch):
    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", boom
    )
    # Should not raise.
    await delete_cached_parse("abc123")


import hashlib
from unittest.mock import AsyncMock, MagicMock

from api.tasks import knowledge_base_processing


async def test_worker_reuses_cached_parse_and_deletes_key(
    fake_redis, tmp_path, monkeypatch
):
    """When kb:parse:{hash} exists, worker must NOT call MPS, then delete the key."""
    # Build a tiny temp file the worker will treat as the downloaded S3 object.
    file_bytes = b"hello world"
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    cached_payload = {
        "mode": "full_document",
        "docling_metadata": {"pages": 1},
        "full_text": "Hello world cached.",
        "chunks": [],
    }
    await knowledge_base_processing.set_cached_parse(file_hash, cached_payload)

    # Patch the worker's collaborators.
    async def fake_download(s3_key, target_path):
        with open(target_path, "wb") as fh:
            fh.write(file_bytes)
        return True

    fake_doc = MagicMock(id=42, created_by=None, organization_id=7, retrieval_mode="full_document")
    monkeypatch.setattr(knowledge_base_processing.storage_fs, "adownload_file", fake_download)
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_status",
        AsyncMock(),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_metadata",
        AsyncMock(),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "get_document_by_hash",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "get_document_by_id",
        AsyncMock(return_value=fake_doc),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_full_text",
        AsyncMock(),
    )
    mps_mock = AsyncMock()
    monkeypatch.setattr(
        knowledge_base_processing.mps_service_key_client,
        "process_document",
        mps_mock,
    )
    monkeypatch.setattr(
        knowledge_base_processing,
        "_enqueue_doc_card_extraction",
        AsyncMock(),
    )

    await knowledge_base_processing.process_knowledge_base_document(
        ctx={},
        document_id=42,
        s3_key="knowledge_base/7/abc/file.txt",
        organization_id=7,
        created_by_provider_id="user-1",
        retrieval_mode="full_document",
    )

    mps_mock.assert_not_called()
    # Cache key should be gone after reuse.
    assert await knowledge_base_processing.get_cached_parse(file_hash) is None
