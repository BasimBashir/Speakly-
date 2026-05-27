"""Integration tests for the org knowledge index rebuild task."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, UserModel
from api.enums import OrganizationConfigurationKey


VALID_CARD = {
    "title": "Test",
    "summary_150_words": "Summary.",
    "key_facts": [],
    "entities": {},
    "numbers_and_pricing": [],
    "faqs": [],
    "suggested_agent_uses": [],
    "topics": ["a"],
}


async def _make_org_and_user(db_session):
    """Helper: create a fresh org + user via the DBClient's session."""
    suffix = uuid.uuid4().hex[:8]
    async with db_session.async_session() as session:
        org = OrganizationModel(provider_id=f"test-org-{suffix}")
        session.add(org)
        await session.flush()
        user = UserModel(
            provider_id=f"test-user-{suffix}",
            selected_organization_id=org.id,
        )
        session.add(user)
        await session.flush()
        return org.id, user.id


async def _doc_with_card(db_session, org_id, user_id, *, filename, intended_use=("inbound",)):
    """Helper: create a document and attach a doc card."""
    doc = await db_session.create_document(
        organization_id=org_id,
        created_by=user_id,
        filename=filename,
        file_size_bytes=10,
        file_hash=f"h-{uuid.uuid4().hex[:8]}",
        mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description=f"Test {filename} with enough characters for validation.",
        doc_type="other",
        intended_use=list(intended_use),
    )
    await db_session.update_doc_card(
        document_id=doc.id,
        doc_card={**VALID_CARD, "title": filename},
        topics=["a"],
    )
    return doc


def _mock_redis():
    """Return a mock Redis client that always acquires the lock.

    The real ``redis.asyncio.Redis`` object is awaitable (has ``__await__``),
    so the production code does ``redis = await aioredis.from_url(...)``.
    We make ``from_url`` an ``AsyncMock`` so the ``await`` resolves to this
    mock instance.
    """
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)  # lock acquired
    r.publish = AsyncMock(return_value=0)  # broadcast — no subscribers
    r.close = AsyncMock()
    return r


def _patch_redis():
    """Patch ``aioredis.from_url`` to return a mock Redis.

    ``from_url`` is a sync function that returns an awaitable Redis object.
    We replace it with an ``AsyncMock`` so ``await from_url(...)`` yields
    the mock Redis directly.
    """
    mock_redis = _mock_redis()
    return patch(
        "api.tasks.org_index_rebuild.aioredis.from_url",
        new=AsyncMock(return_value=mock_redis),
    )


@pytest.mark.asyncio
async def test_rebuild_writes_index_with_doc(db_session):
    """Rebuild persists a knowledge_index config containing the doc's title."""
    org_id, user_id = await _make_org_and_user(db_session)
    await _doc_with_card(db_session, org_id, user_id, filename="pricing.pdf")

    from api.tasks.org_index_rebuild import rebuild_org_knowledge_index

    with _patch_redis():
        await rebuild_org_knowledge_index({}, organization_id=org_id)

    config = await db_session.get_configuration(
        org_id, OrganizationConfigurationKey.KNOWLEDGE_INDEX.value
    )
    assert config is not None
    payload = config.value
    assert "pricing.pdf" in payload["md"]
    assert payload["doc_count"] == 1


@pytest.mark.asyncio
async def test_rebuild_excludes_inactive_docs(db_session):
    """Soft-deleted docs are excluded from the rebuilt index."""
    org_id, user_id = await _make_org_and_user(db_session)
    active_doc = await _doc_with_card(db_session, org_id, user_id, filename="active.pdf")
    deleted_doc = await _doc_with_card(db_session, org_id, user_id, filename="deleted.pdf")

    # Soft-delete one document
    await db_session.delete_document(
        document_uuid=deleted_doc.document_uuid,
        organization_id=org_id,
    )

    from api.tasks.org_index_rebuild import rebuild_org_knowledge_index

    with _patch_redis():
        await rebuild_org_knowledge_index({}, organization_id=org_id)

    config = await db_session.get_configuration(
        org_id, OrganizationConfigurationKey.KNOWLEDGE_INDEX.value
    )
    assert config is not None
    payload = config.value
    assert "active.pdf" in payload["md"]
    assert "deleted.pdf" not in payload["md"]
    assert payload["doc_count"] == 1


@pytest.mark.asyncio
async def test_rebuild_is_org_scoped(db_session):
    """Rebuilding org A's index does not create an index for org B."""
    org_a, user_a = await _make_org_and_user(db_session)
    org_b, user_b = await _make_org_and_user(db_session)

    await _doc_with_card(db_session, org_a, user_a, filename="doc_a.pdf")
    await _doc_with_card(db_session, org_b, user_b, filename="doc_b.pdf")

    from api.tasks.org_index_rebuild import rebuild_org_knowledge_index

    with _patch_redis():
        await rebuild_org_knowledge_index({}, organization_id=org_a)

    config_a = await db_session.get_configuration(
        org_a, OrganizationConfigurationKey.KNOWLEDGE_INDEX.value
    )
    assert config_a is not None
    assert "doc_a.pdf" in config_a.value["md"]
    assert "doc_b.pdf" not in config_a.value["md"]

    config_b = await db_session.get_configuration(
        org_b, OrganizationConfigurationKey.KNOWLEDGE_INDEX.value
    )
    assert config_b is None
