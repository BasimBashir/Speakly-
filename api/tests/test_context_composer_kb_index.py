"""Tests for the org knowledge index injection in pipecat context composer."""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine_context_composer import (
    compose_kb_index_section,
    _direction_filter_text,
)


SAMPLE_MD = """# Organization Knowledge Index (3 docs)

## Contract (2 docs)
- **A** (a.pdf) — short summary… _uses: inbound_ _topics: x_
- **B** (b.pdf) — short summary… _uses: outbound_ _topics: y_

## Policy (1 docs)
- **C** (c.pdf) — short summary… _uses: inbound, outbound_ _topics: z_
"""


@pytest.mark.asyncio
async def test_section_omitted_when_no_index():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value=None),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction=None, enabled=True
        )
    assert section == ""


@pytest.mark.asyncio
async def test_section_omitted_when_disabled_for_node():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value={"md": SAMPLE_MD}),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction=None, enabled=False
        )
    assert section == ""


@pytest.mark.asyncio
async def test_section_present_with_index():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value={"md": SAMPLE_MD}),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction=None, enabled=True
        )
    assert "<organization_knowledge>" in section
    assert "a.pdf" in section
    assert "b.pdf" in section


@pytest.mark.asyncio
async def test_inbound_call_filters_outbound_only():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value={"md": SAMPLE_MD}),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction="inbound", enabled=True
        )
    assert "a.pdf" in section
    assert "c.pdf" in section
    assert "b.pdf" not in section


@pytest.mark.asyncio
async def test_outbound_call_filters_inbound_only():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value={"md": SAMPLE_MD}),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction="outbound", enabled=True
        )
    assert "b.pdf" in section
    assert "c.pdf" in section
    assert "a.pdf" not in section


def test_direction_filter_keeps_headers():
    filtered = _direction_filter_text(SAMPLE_MD, "inbound")
    assert "# Organization Knowledge Index" in filtered
    assert "## Contract" in filtered
    assert "## Policy" in filtered
