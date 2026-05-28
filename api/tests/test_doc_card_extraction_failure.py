"""Failure-mode tests for DocCard extraction.

Invalid JSON, missing API key for non-Dograh provider, validation errors.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.knowledge_base.doc_card_extraction import (
    extract_doc_card_for_document,
)


def _mock_llm_text(content: str):
    """Pipecat's run_inference returns raw text."""
    return content


def _valid_card():
    return {
        "title": "T",
        "summary_150_words": "S",
        "key_facts": [],
        "entities": {},
        "numbers_and_pricing": [],
        "faqs": [],
        "suggested_agent_uses": [],
        "topics": [],
    }


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


async def _make_extractable_doc(db_session, org_id, user_id):
    doc = await db_session.create_document(
        organization_id=org_id,
        created_by=user_id,
        filename="t.pdf",
        file_size_bytes=10,
        file_hash=f"h-{uuid.uuid4().hex[:8]}",
        mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Test document for failure cases. Should trigger extraction.",
        doc_type="other",
        intended_use=["inbound"],
    )
    await db_session.update_document_full_text(doc.id, "Some text.")
    return doc


@pytest.mark.asyncio
async def test_invalid_json_triggers_repair_then_succeeds(db_session):
    """First LLM response is invalid JSON; repair attempt returns valid JSON."""
    org_id, user_id = await _make_org_and_user(db_session)
    doc = await _make_extractable_doc(db_session, org_id, user_id)

    fake_llm = AsyncMock()
    fake_llm.run_inference = AsyncMock(
        side_effect=[
            _mock_llm_text("not json at all {[}"),
            _mock_llm_text(json.dumps(_valid_card())),
        ]
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is not None
    assert fake_llm.run_inference.call_count == 2  # repair attempt happened


@pytest.mark.asyncio
async def test_invalid_json_twice_raises(db_session):
    """Both attempts return invalid JSON -> RuntimeError raised."""
    org_id, user_id = await _make_org_and_user(db_session)
    doc = await _make_extractable_doc(db_session, org_id, user_id)

    fake_llm = AsyncMock()
    fake_llm.run_inference = AsyncMock(
        return_value=_mock_llm_text("still not json")
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        with pytest.raises(RuntimeError):
            await extract_doc_card_for_document(doc.id)


@pytest.mark.asyncio
async def test_validation_failure_triggers_repair(db_session):
    """LLM returns valid JSON missing a required field; repair fixes it."""
    org_id, user_id = await _make_org_and_user(db_session)
    doc = await _make_extractable_doc(db_session, org_id, user_id)

    bad = {"summary_150_words": "S"}  # missing required `title` and other required-by-schema fields
    fake_llm = AsyncMock()
    fake_llm.run_inference = AsyncMock(
        side_effect=[
            _mock_llm_text(json.dumps(bad)),
            _mock_llm_text(json.dumps(_valid_card())),
        ]
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is not None
    assert fake_llm.run_inference.call_count == 2


@pytest.mark.asyncio
async def test_non_dograh_provider_without_api_key_is_skipped(db_session):
    """User config set to openai with no api_key -> skipped with informative error."""
    org_id, user_id = await _make_org_and_user(db_session)
    doc = await _make_extractable_doc(db_session, org_id, user_id)

    async def fake_resolve(_user_id):
        return ("openai", "gpt-4.1", None, {})

    with patch(
        "api.services.knowledge_base.doc_card_extraction.resolve_kb_llm",
        side_effect=fake_resolve,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is None
    refreshed = await db_session.get_document_by_id(doc.id)
    assert refreshed.doc_card is None
    assert refreshed.processing_error is not None
    assert "Model Configurations" in refreshed.processing_error
