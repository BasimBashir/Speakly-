"""Integration tests for DocCard extraction.

Uses the real test DB (api/.env.test) but mocks the LLM service.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, UserModel
from api.schemas.doc_card import DocCard
from api.services.knowledge_base.doc_card_extraction import (
    extract_doc_card_for_document,
)

VALID_CARD = {
    "title": "Test Doc",
    "summary_150_words": "A test document for the extraction pipeline.",
    "key_facts": ["fact 1", "fact 2"],
    "entities": {
        "organizations": ["Acme"],
        "products": [],
        "people": [],
        "locations": [],
        "dates": [],
    },
    "numbers_and_pricing": [],
    "faqs": [],
    "suggested_agent_uses": ["test use"],
    "topics": ["test", "extraction"],
}


def _mock_llm_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


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


@pytest.mark.asyncio
async def test_extraction_happy_path(db_session):
    """Doc with user_description + full_text -> DocCard persisted."""
    org_id, user_id = await _make_org_and_user(db_session)

    document = await db_session.create_document(
        organization_id=org_id,
        created_by=user_id,
        filename="t.pdf",
        file_size_bytes=100,
        file_hash="abc",
        mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Test doc for extraction. Agent should know its key facts.",
        doc_type="other",
        intended_use=["inbound"],
    )
    await db_session.update_document_full_text(document.id, "Lorem ipsum dolor sit amet.")
    await db_session.update_document_status(document.id, "completed", total_chunks=0)

    fake_llm = AsyncMock()
    fake_llm.create_chat_completion = AsyncMock(
        return_value=_mock_llm_response(json.dumps(VALID_CARD))
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(document.id)

    assert isinstance(card, DocCard)
    refreshed = await db_session.get_document_by_id(document.id)
    assert refreshed.doc_card is not None
    assert refreshed.doc_card["title"] == "Test Doc"
    assert refreshed.topics == ["test", "extraction"]
    assert refreshed.doc_card_extracted_at is not None


@pytest.mark.asyncio
async def test_extraction_skipped_for_legacy_doc_without_description(db_session):
    """Doc with NULL user_description -> extraction skipped, no doc_card written."""
    org_id, user_id = await _make_org_and_user(db_session)

    document = await db_session.create_document(
        organization_id=org_id,
        created_by=user_id,
        filename="legacy.pdf",
        file_size_bytes=100,
        file_hash="def",
        mime_type="application/pdf",
        retrieval_mode="full_document",
    )

    card = await extract_doc_card_for_document(document.id)
    assert card is None
    refreshed = await db_session.get_document_by_id(document.id)
    assert refreshed.doc_card is None


@pytest.mark.asyncio
async def test_extraction_org_isolation(db_session):
    """Extracting doc A doesn't affect doc B in another org."""
    org_a, user_a = await _make_org_and_user(db_session)
    org_b, user_b = await _make_org_and_user(db_session)

    doc_a = await db_session.create_document(
        organization_id=org_a,
        created_by=user_a,
        filename="a.pdf",
        file_size_bytes=10,
        file_hash="aaa",
        mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Doc A in org A. Agent should know its key facts about A.",
        doc_type="other",
        intended_use=["inbound"],
    )
    doc_b = await db_session.create_document(
        organization_id=org_b,
        created_by=user_b,
        filename="b.pdf",
        file_size_bytes=10,
        file_hash="bbb",
        mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Doc B in org B. Agent should know its key facts about B.",
        doc_type="other",
        intended_use=["inbound"],
    )
    await db_session.update_document_full_text(doc_a.id, "Content A.")
    await db_session.update_document_full_text(doc_b.id, "Content B.")

    fake_llm = AsyncMock()
    card_a = {**VALID_CARD, "title": "A"}
    fake_llm.create_chat_completion = AsyncMock(
        return_value=_mock_llm_response(json.dumps(card_a))
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        await extract_doc_card_for_document(doc_a.id)

    refreshed_a = await db_session.get_document_by_id(doc_a.id)
    assert refreshed_a.doc_card is not None
    assert refreshed_a.doc_card["title"] == "A"
    refreshed_b = await db_session.get_document_by_id(doc_b.id)
    assert refreshed_b.doc_card is None  # untouched
