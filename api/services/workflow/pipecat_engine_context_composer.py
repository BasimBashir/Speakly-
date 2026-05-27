"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

import os
from typing import TYPE_CHECKING, Callable, Optional
from typing import Optional as _Optional

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.services.knowledge_base.org_index_cache import get_index_for_org
from api.services.knowledge_base.org_index_renderer import enforce_size_budget
from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"  # Play pre-recorded audio
TTS_MARKER = "▸"  # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode system prompt instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with excatcly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""

KB_INDEX_PROMPT_BUDGET_CHARS = int(
    os.environ.get("KB_INDEX_PROMPT_BUDGET_CHARS", "32000")
)


async def compose_kb_index_section(
    *,
    organization_id: int,
    call_direction: _Optional[str],
    enabled: bool,
) -> str:
    if not enabled:
        return ""

    payload = await get_index_for_org(organization_id)
    if not payload:
        return ""

    base_md = payload.get("md") or ""
    md = base_md if not call_direction else _direction_filter_text(base_md, call_direction)
    md = enforce_size_budget(md, max_bytes=KB_INDEX_PROMPT_BUDGET_CHARS)

    if not md.strip() or "0 docs" in md.split("\n", 1)[0]:
        return ""

    return (
        "<organization_knowledge>\n"
        "The following is your organization's knowledge index — a table of "
        "contents of documents available to you. Use it to decide WHICH document "
        "to look in. To get actual content, call the `retrieve_from_knowledge_base` "
        "tool with a specific question.\n\n"
        f"{md}\n\n"
        "Important rules:\n"
        "- The index is a guide, not a source of truth. Quote facts only after "
        "retrieving them with the tool.\n"
        "- If a caller asks about something not in the index, say so honestly.\n"
        "- Prefer documents whose intended_use matches this call's direction.\n"
        "</organization_knowledge>"
    )


def _direction_filter_text(md: str, direction: str) -> str:
    out = []
    for line in md.split("\n"):
        if line.startswith("- "):
            uses_segment = line.split("_uses:", 1)
            if len(uses_segment) == 2:
                uses = uses_segment[1].split("_", 1)[0]
                if direction not in uses:
                    continue
        out.append(line)
    return "\n".join(out)


async def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
    organization_id: _Optional[int] = None,
    call_direction: _Optional[str] = None,
) -> str:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, and (when recordings
    are enabled anywhere in the workflow) the recording response mode
    instructions into a single string.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether any node in the workflow uses recordings.

    Returns:
        The composed system prompt text.
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    parts = [p for p in (global_prompt, formatted_node_prompt) if p]

    if has_recordings and "RECORDING_ID:" in formatted_node_prompt:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    if organization_id is not None:
        include_index = getattr(node, "include_kb_index", True)
        kb_section = await compose_kb_index_section(
            organization_id=organization_id,
            call_direction=call_direction,
            enabled=include_index,
        )
        if kb_section:
            parts.append(kb_section)

    return "\n\n".join(parts)


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, custom tools (including built-in
    categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []

    # Knowledge base retrieval tool
    if node.document_uuids:
        kb_tool_def = get_knowledge_base_tool(node.document_uuids)
        kb_schema = get_function_schema(
            kb_tool_def["function"]["name"],
            kb_tool_def["function"]["description"],
            properties=kb_tool_def["function"]["parameters"].get("properties", {}),
            required=kb_tool_def["function"]["parameters"].get("required", []),
        )
        functions.append(kb_schema)

    # Custom tools
    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids,
            mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
        )
        functions.extend(custom_tool_schemas)

    # Transition function schemas
    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    return functions
