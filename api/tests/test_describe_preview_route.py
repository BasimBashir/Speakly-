"""Tests for POST /api/v1/knowledge-base/describe-preview."""

from io import BytesIO
from unittest.mock import AsyncMock

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.knowledge_base.describe_preview import (
    DescribePreviewError,
    DescribePreviewResult,
)


@pytest.fixture
async def org_user(async_session):
    org = OrganizationModel(provider_id="test-org-describe-preview")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id="test-user-describe-preview",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()
    return org, user


class TestDescribePreviewRoute:
    async def test_accepts_supported_file_and_returns_description(
        self, test_client_factory, org_user, monkeypatch
    ):
        _org, user = org_user
        mock = AsyncMock(return_value=DescribePreviewResult(
            description="Sample description that mentions when the agent should use this doc.",
            from_cache=False,
        ))
        monkeypatch.setattr(
            "api.routes.knowledge_base.generate_description_preview", mock
        )

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/describe-preview",
                files={"file": ("hello.txt", BytesIO(b"hello"), "text/plain")},
                data={"doc_type": "faq", "intended_use": "inbound"},
            )

        assert response.status_code == 200
        body = response.json()
        assert "Sample description" in body["description"]
        assert body["from_cache"] is False
        mock.assert_awaited_once()
        # Confirm the service got org + provider scoping data.
        kwargs = mock.await_args.kwargs
        assert kwargs["organization_id"] == user.selected_organization_id
        assert kwargs["created_by_provider_id"] == str(user.provider_id)

    async def test_rejects_unsupported_extension(
        self, test_client_factory, org_user
    ):
        _org, user = org_user
        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/describe-preview",
                files={"file": ("evil.exe", BytesIO(b"x" * 10), "application/octet-stream")},
                data={"doc_type": "faq", "intended_use": "inbound"},
            )
        assert response.status_code == 400
        assert "supported" in response.json()["detail"].lower()

    async def test_rejects_oversized_file(self, test_client_factory, org_user):
        _org, user = org_user
        big = BytesIO(b"x" * (5 * 1024 * 1024 + 1))
        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/describe-preview",
                files={"file": ("big.txt", big, "text/plain")},
                data={"doc_type": "faq"},
            )
        assert response.status_code == 400
        assert "5" in response.json()["detail"]

    async def test_returns_502_on_parse_failure(
        self, test_client_factory, org_user, monkeypatch
    ):
        _org, user = org_user

        async def fail(**_kwargs):
            raise DescribePreviewError("parse_failed", "boom")

        monkeypatch.setattr(
            "api.routes.knowledge_base.generate_description_preview", fail
        )

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/describe-preview",
                files={"file": ("hi.txt", BytesIO(b"hi"), "text/plain")},
                data={"doc_type": "faq"},
            )

        assert response.status_code == 502
        assert response.json()["detail"] == "parse_failed"

    async def test_returns_502_on_llm_failure(
        self, test_client_factory, org_user, monkeypatch
    ):
        _org, user = org_user

        async def fail(**_kwargs):
            raise DescribePreviewError("llm_failed", "no creds")

        monkeypatch.setattr(
            "api.routes.knowledge_base.generate_description_preview", fail
        )

        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/knowledge-base/describe-preview",
                files={"file": ("hi.txt", BytesIO(b"hi"), "text/plain")},
                data={"doc_type": "faq"},
            )

        assert response.status_code == 502
        assert response.json()["detail"] == "llm_failed"
