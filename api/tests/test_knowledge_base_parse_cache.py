"""Tests for the KB parse cache (Redis-backed)."""

import json
from unittest.mock import AsyncMock

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
