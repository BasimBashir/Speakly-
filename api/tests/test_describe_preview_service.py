"""Tests for the describe-preview service orchestrator (S2 two-call path)."""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.knowledge_base import describe_preview


@pytest.fixture
def patch_collaborators(monkeypatch, tmp_path):
    """Stub the cache, MPS, LLM, and resolver so we can assert call patterns."""
    cache_state: dict[str, dict] = {}

    async def fake_get(file_hash: str):
        return cache_state.get(file_hash)

    async def fake_set(file_hash: str, payload: dict):
        cache_state[file_hash] = payload

    monkeypatch.setattr(describe_preview, "get_cached_parse", fake_get)
    monkeypatch.setattr(describe_preview, "set_cached_parse", fake_set)

    mps_mock = AsyncMock(return_value={
        "mode": "full_document",
        "full_text": "Document body text.",
        "chunks": [],
        "docling_metadata": {},
    })
    monkeypatch.setattr(
        describe_preview.mps_service_key_client, "process_document", mps_mock
    )

    async def fake_resolve(_user_id):
        return "speaches", "llama3", None, {"base_url": "http://localhost:8000/v1"}

    monkeypatch.setattr(describe_preview, "resolve_kb_llm", fake_resolve)

    # S2 = two sequential LLM calls. First returns a JSON profile, second the prose.
    llm_service = MagicMock()
    llm_service.run_inference = AsyncMock(side_effect=[
        '{"topic":"FAQ about coffee","audience":"customers","agent_use_hint":"refunds","key_entities":["coffee"]}',
        "An FAQ document about coffee subscriptions. The agent should consult it whenever a caller asks how the subscription works, about delivery options, or about returns.",
    ])
    factory_mock = MagicMock(return_value=llm_service)
    monkeypatch.setattr(
        describe_preview, "create_llm_service_from_provider", factory_mock
    )

    return {
        "cache_state": cache_state,
        "mps_mock": mps_mock,
        "factory_mock": factory_mock,
        "llm_service": llm_service,
    }


async def test_cache_miss_calls_mps_caches_and_returns_description(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"hello body")
    file_hash = hashlib.sha256(b"hello body").hexdigest()

    result = await describe_preview.generate_description_preview(
        file_path=str(file_path),
        filename="doc.txt",
        mime_type="text/plain",
        doc_type="faq",
        intended_use=["inbound"],
        user_id=42,
    )

    assert "agent" in result.description.lower()
    assert result.from_cache is False
    patch_collaborators["mps_mock"].assert_awaited_once()
    assert file_hash in patch_collaborators["cache_state"]
    # Two LLM calls (profile + narrative).
    assert patch_collaborators["llm_service"].run_inference.await_count == 2


async def test_cache_hit_skips_mps(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"hello body")
    file_hash = hashlib.sha256(b"hello body").hexdigest()

    patch_collaborators["cache_state"][file_hash] = {
        "mode": "full_document",
        "full_text": "Pre-cached body.",
        "chunks": [],
        "docling_metadata": {},
    }

    result = await describe_preview.generate_description_preview(
        file_path=str(file_path),
        filename="doc.txt",
        mime_type="text/plain",
        doc_type="faq",
        intended_use=["inbound"],
        user_id=42,
    )

    assert result.from_cache is True
    patch_collaborators["mps_mock"].assert_not_called()
    assert patch_collaborators["llm_service"].run_inference.await_count == 2


async def test_llm_failure_raises_describe_preview_error(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"x")
    patch_collaborators["llm_service"].run_inference.side_effect = RuntimeError("llm down")

    with pytest.raises(describe_preview.DescribePreviewError) as exc:
        await describe_preview.generate_description_preview(
            file_path=str(file_path),
            filename="doc.txt",
            mime_type="text/plain",
            doc_type=None,
            intended_use=None,
            user_id=42,
        )
    assert exc.value.code == "llm_failed"


async def test_mps_failure_raises_describe_preview_error(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"x")
    patch_collaborators["mps_mock"].side_effect = RuntimeError("mps down")

    with pytest.raises(describe_preview.DescribePreviewError) as exc:
        await describe_preview.generate_description_preview(
            file_path=str(file_path),
            filename="doc.txt",
            mime_type="text/plain",
            doc_type=None,
            intended_use=None,
            user_id=42,
        )
    assert exc.value.code == "parse_failed"


async def test_empty_document_text_raises_parse_failed(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"x")
    # MPS returns no usable text.
    patch_collaborators["mps_mock"].return_value = {
        "mode": "full_document",
        "full_text": "",
        "chunks": [],
        "docling_metadata": {},
    }

    with pytest.raises(describe_preview.DescribePreviewError) as exc:
        await describe_preview.generate_description_preview(
            file_path=str(file_path),
            filename="doc.txt",
            mime_type="text/plain",
            doc_type=None,
            intended_use=None,
            user_id=42,
        )
    assert exc.value.code == "parse_failed"
