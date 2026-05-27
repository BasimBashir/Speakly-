"""End-to-end smoke test for the doc card + org index pipeline.

Requires a running MPS service. Gated by ``pytest -m mps``.
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, UserModel

pytestmark = pytest.mark.mps


async def _make_org_and_user(db_session):
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


@pytest.mark.asyncio
async def test_full_pipeline_produces_index_section(db_session):
    """Upload -> full_text -> doc_card -> org index -> composer injection."""
    from api.services.knowledge_base.doc_card_extraction import (
        extract_doc_card_for_document,
    )
    from api.tasks.org_index_rebuild import rebuild_org_knowledge_index
    from api.services.knowledge_base.org_index_cache import get_index_for_org, invalidate
    from api.services.workflow.pipecat_engine_context_composer import (
        compose_kb_index_section,
    )

    fixture = Path(__file__).parent / "fixtures" / "sample.txt"
    if not fixture.exists():
        pytest.skip("Fixture sample.txt missing")

    org_id, user_id = await _make_org_and_user(db_session)

    doc = await db_session.create_document(
        organization_id=org_id,
        created_by=user_id,
        filename="sample.txt",
        file_size_bytes=fixture.stat().st_size,
        file_hash=db_session.compute_file_hash(str(fixture)),
        mime_type="text/plain",
        retrieval_mode="full_document",
        user_description="A small sample text fixture for the e2e test pipeline.",
        doc_type="other",
        intended_use=["inbound"],
    )
    await db_session.update_document_full_text(doc.id, fixture.read_text())
    await db_session.update_document_status(doc.id, "completed", total_chunks=0)

    # Mock the LLM for extraction
    valid_card = {
        "title": "Sample Fixture",
        "summary_150_words": "A sample text fixture for testing.",
        "key_facts": ["test fact"],
        "entities": {},
        "numbers_and_pricing": [],
        "faqs": [],
        "suggested_agent_uses": ["testing"],
        "topics": ["test", "fixture"],
    }
    fake_llm = AsyncMock()
    fake_llm.run_inference = AsyncMock(return_value=json.dumps(valid_card))

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(doc.id)
    assert card is not None

    # Mock Redis for the rebuild task
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.publish = AsyncMock(return_value=0)
    mock_redis.close = AsyncMock()

    with patch(
        "api.tasks.org_index_rebuild.aioredis.from_url",
        new=AsyncMock(return_value=mock_redis),
    ):
        await rebuild_org_knowledge_index({}, org_id)

    # Invalidate cache so get_index_for_org reads fresh from DB
    await invalidate(org_id)
    payload = await get_index_for_org(org_id)
    assert payload is not None
    assert "sample.txt" in payload["md"]

    section = await compose_kb_index_section(
        organization_id=org_id, call_direction="inbound", enabled=True
    )
    assert "<organization_knowledge>" in section
    assert "sample.txt" in section
