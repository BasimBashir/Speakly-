"""ARQ task: rebuild the org knowledge index markdown.

Coalesces concurrent triggers via a 30-second Redis lock keyed by org.
Persists to organization_configurations under key `knowledge_index`
and broadcasts a WorkerSyncManager event so all workers invalidate
their per-worker cache.
"""

import hashlib
from datetime import UTC, datetime

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.db import db_client
from api.enums import OrganizationConfigurationKey, RedisChannel
from api.services.knowledge_base.org_index_renderer import (
    build_org_index_md,
    enforce_size_budget,
)
from api.services.worker_sync.protocol import WorkerSyncEvent, WorkerSyncEventType

LOCK_KEY_TEMPLATE = "kb_index_rebuild_lock:{org_id}"
LOCK_TTL_SECONDS = 30


async def rebuild_org_knowledge_index(ctx, organization_id: int) -> None:
    """Rebuild the knowledge_index for an organization.

    Idempotent. Coalesced by Redis lock. If another rebuild is in flight
    within LOCK_TTL_SECONDS, this invocation is a no-op.
    """
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    lock_key = LOCK_KEY_TEMPLATE.format(org_id=organization_id)
    try:
        acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
        if not acquired:
            logger.debug(
                f"KB index rebuild already in flight for org {organization_id}; skipping"
            )
            return

        documents = await db_client.list_active_documents_for_index(organization_id)
        md = build_org_index_md(documents, call_direction=None)
        md = enforce_size_budget(md)

        payload = {
            "md": md,
            "doc_count": len(documents),
            "char_count": len(md),
            "generated_at": datetime.now(UTC).isoformat(),
            "hash": hashlib.sha256(md.encode("utf-8")).hexdigest(),
        }

        await db_client.upsert_configuration(
            organization_id=organization_id,
            key=OrganizationConfigurationKey.KNOWLEDGE_INDEX.value,
            value=payload,
        )

        # ARQ workers don't run the FastAPI lifespan, so there is no
        # WorkerSyncManager instance. Publish directly to the Redis
        # pub/sub channel so FastAPI workers invalidate their caches.
        event = WorkerSyncEvent(
            event_type=WorkerSyncEventType.KB_INDEX_UPDATED.value,
            action="update",
            org_id=str(organization_id),
        )
        await redis.publish(RedisChannel.WORKER_SYNC.value, event.to_json())

        logger.info(
            f"Rebuilt KB index for org {organization_id}: "
            f"{len(documents)} docs, {len(md)} chars"
        )
    finally:
        await redis.close()
