"""Redis-backed cache for MPS document-parse output.

The cache is keyed by SHA-256 file hash so identical files share a single
parse across the preview and upload paths. TTL is 30 minutes — long enough
for a user to write/edit their description before clicking Upload & Process.

Cache failures are best-effort: any Redis error is logged and treated as a
miss; the caller falls back to a fresh MPS parse.
"""

import json
from typing import Optional

from loguru import logger
from redis import asyncio as aioredis

from api.constants import REDIS_URL

PARSE_CACHE_TTL_SECONDS = 30 * 60
KEY_PREFIX = "kb:parse:"

_client: Optional[aioredis.Redis] = None


async def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = await aioredis.from_url(REDIS_URL)
    return _client


def _key(file_hash: str) -> str:
    return f"{KEY_PREFIX}{file_hash}"


async def get_cached_parse(file_hash: str) -> Optional[dict]:
    """Return the cached MPS-parse payload for this hash, or None."""
    try:
        client = await _get_redis()
        raw = await client.get(_key(file_hash))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning(f"Parse-cache get failed for {file_hash}: {exc}")
        return None


async def set_cached_parse(file_hash: str, payload: dict) -> None:
    """Store the MPS-parse payload under this hash with a 30-minute TTL."""
    try:
        client = await _get_redis()
        await client.set(
            _key(file_hash),
            json.dumps(payload),
            ex=PARSE_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning(f"Parse-cache set failed for {file_hash}: {exc}")


async def delete_cached_parse(file_hash: str) -> None:
    """Remove the cached parse for this hash (called after worker reuse)."""
    try:
        client = await _get_redis()
        await client.delete(_key(file_hash))
    except Exception as exc:
        logger.warning(f"Parse-cache delete failed for {file_hash}: {exc}")
