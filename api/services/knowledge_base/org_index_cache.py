"""Per-worker in-memory cache of the org knowledge index markdown.

The cache is invalidated by a WorkerSyncManager pub/sub event broadcast
after each rebuild. Workers re-read authoritative state from the DB
on next access.
"""

from typing import Optional

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.worker_sync.protocol import WorkerSyncEvent

_CACHE: dict[int, dict] = {}


async def get_index_for_org(organization_id: int) -> Optional[dict]:
    """Return the cached or freshly-loaded knowledge_index value for an org.

    Shape: {"md": str, "doc_count": int, "char_count": int,
            "generated_at": str, "hash": str} or None if not yet built.
    """
    cached = _CACHE.get(organization_id)
    if cached is not None:
        return cached
    value = await db_client.get_configuration_value(
        organization_id=organization_id,
        key=OrganizationConfigurationKey.KNOWLEDGE_INDEX.value,
        default=None,
    )
    if value:
        _CACHE[organization_id] = value
    return value


async def invalidate(organization_id: int) -> None:
    _CACHE.pop(organization_id, None)


async def handle_kb_index_updated(event: WorkerSyncEvent) -> None:
    """WorkerSyncManager handler — invalidate local cache on broadcast."""
    try:
        org_id = int(event.org_id) if event.org_id else 0
    except ValueError:
        logger.warning(f"kb_index_updated: invalid org_id {event.org_id!r}")
        return
    if org_id:
        await invalidate(org_id)
        logger.debug(f"KB index cache invalidated for org {org_id}")
