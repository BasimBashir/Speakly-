"""
Route tests for knowledge-base endpoints with doc-card metadata fields.

Covers:
  - Pydantic validation for required fields (doc_type, user_description length)
  - Organization scoping on PATCH
  - Legacy-doc guard on re-extract
"""

import pytest

from api.db.models import OrganizationModel, UserModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PROCESS_PAYLOAD = {
    "document_uuid": "00000000-0000-0000-0000-000000000001",
    "s3_key": "knowledge_base/1/doc/test.pdf",
    "retrieval_mode": "chunked",
    "doc_type": "faq",
    "intended_use": ["inbound"],
    "user_description": "This document contains frequently asked questions about our product.",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def org_a_user(async_session):
    """Create org A and a user belonging to it."""
    org = OrganizationModel(provider_id="test-org-kb-route-a")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id="test-user-kb-route-a",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


@pytest.fixture
async def org_b_user(async_session):
    """Create org B and a user belonging to it."""
    org = OrganizationModel(provider_id="test-org-kb-route-b")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id="test-user-kb-route-b",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcessDocumentValidation:
    """POST /api/v1/knowledge-base/process-document — Pydantic rejection."""

    async def test_process_document_rejects_missing_doc_type(
        self, test_client_factory, org_a_user
    ):
        """Omitting the required ``doc_type`` field should produce a 422."""
        _org, user = org_a_user
        payload = {
            "document_uuid": "00000000-0000-0000-0000-000000000002",
            "s3_key": "knowledge_base/1/doc/test.pdf",
            "retrieval_mode": "chunked",
            # doc_type intentionally omitted
            "intended_use": ["inbound"],
            "user_description": "A detailed description that is long enough to pass validation.",
        }

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/process-document", json=payload
            )

        assert response.status_code == 422
        body = response.json()
        # Pydantic v2 error location
        field_errors = [
            e for e in body["detail"] if "doc_type" in str(e.get("loc", []))
        ]
        assert len(field_errors) > 0, "Expected a validation error for doc_type"

    async def test_process_document_rejects_short_description(
        self, test_client_factory, org_a_user
    ):
        """``user_description`` shorter than 20 characters should produce a 422."""
        _org, user = org_a_user
        payload = {
            **VALID_PROCESS_PAYLOAD,
            "user_description": "Too short",  # <20 chars
        }

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/process-document", json=payload
            )

        assert response.status_code == 422
        body = response.json()
        field_errors = [
            e
            for e in body["detail"]
            if "user_description" in str(e.get("loc", []))
        ]
        assert len(field_errors) > 0, (
            "Expected a validation error for user_description"
        )


class TestPatchDocumentOrgScoping:
    """PATCH /api/v1/knowledge-base/documents/{uuid} — org isolation."""

    async def test_patch_document_org_scoped(
        self, db_session, test_client_factory, org_a_user, org_b_user
    ):
        """PATCHing a document owned by org B while authed as org A returns 404."""
        org_b, _user_b = org_b_user
        _org_a, user_a = org_a_user

        # Create a document owned by org B
        doc = await db_session.create_document(
            organization_id=org_b.id,
            created_by=_user_b.id,
            filename="orgb_doc.pdf",
            file_size_bytes=1024,
            file_hash="abc123",
            mime_type="application/pdf",
            doc_type="policy",
            intended_use=["inbound"],
            user_description="This policy document belongs to org B exclusively.",
        )

        async with test_client_factory(user_a) as client:
            response = await client.patch(
                f"/api/v1/knowledge-base/documents/{doc.document_uuid}",
                json={"doc_type": "faq"},
            )

        assert response.status_code == 404


class TestReExtractLegacyGuard:
    """POST /api/v1/knowledge-base/documents/{uuid}/re-extract — legacy doc."""

    async def test_re_extract_blocks_legacy_doc_without_description(
        self, db_session, test_client_factory, org_a_user
    ):
        """Re-extract should return 400 when the document has no user_description."""
        org_a, user_a = org_a_user

        # Create a legacy document with no user_description
        doc = await db_session.create_document(
            organization_id=org_a.id,
            created_by=user_a.id,
            filename="legacy_doc.pdf",
            file_size_bytes=2048,
            file_hash="def456",
            mime_type="application/pdf",
            # user_description intentionally omitted (None)
        )

        async with test_client_factory(user_a) as client:
            response = await client.post(
                f"/api/v1/knowledge-base/documents/{doc.document_uuid}/re-extract",
            )

        assert response.status_code == 400
        assert "user_description" in response.json()["detail"].lower()
