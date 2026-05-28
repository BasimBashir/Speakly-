"""Prompt strategies for the KB auto-describe feature.

After the eval in describe_prompt_eval.py picks a winner, the chosen
strategy is wired through `build_describe_prompt`, which is the only
public entry point used by the service module.
"""

from typing import Iterable, Optional

MAX_DOC_CHARS = 12_000
MIN_WORDS = 40
MAX_WORDS = 80

SYSTEM_PROMPT = (
    "You write short, faithful descriptions of business documents for "
    "voice AI agents. The description tells a developer's voice agent "
    "what this document contains and when to consult it during a call. "
    "Stay grounded in the document — do not invent facts."
)


def _truncate(text: str) -> str:
    if len(text) <= MAX_DOC_CHARS:
        return text
    return text[:MAX_DOC_CHARS] + "\n\n[...document truncated for length...]"


def _chips_line(doc_type: Optional[str], intended_use: Optional[Iterable[str]]) -> str:
    parts = []
    if doc_type:
        parts.append(f"Document type: {doc_type}")
    if intended_use:
        use = ", ".join(intended_use)
        if use:
            parts.append(f"Intended use: {use}")
    return "\n".join(parts) if parts else "(no document type or intended use selected)"


# ---------------------------------------------------------------------------
# Strategy S1 — Direct single-call
# ---------------------------------------------------------------------------

def build_prompt_s1_direct(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    return (
        f"{_chips_line(doc_type, intended_use)}\n\n"
        f"<document>\n{_truncate(document_text)}\n</document>\n\n"
        f"Write 2-3 sentences ({MIN_WORDS}-{MAX_WORDS} words) describing what "
        f"this document contains and when a voice agent should consult it. "
        f"Use plain prose, second person ('the agent'), no JSON, no headings, "
        f"no preamble. Return only the description text."
    )


# ---------------------------------------------------------------------------
# Strategy S2 — Two-step: structured profile then narrative
# ---------------------------------------------------------------------------

def build_prompt_s2_step1_profile(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    return (
        f"{_chips_line(doc_type, intended_use)}\n\n"
        f"<document>\n{_truncate(document_text)}\n</document>\n\n"
        "Extract a JSON object with exactly these keys:\n"
        "{\n"
        '  "topic": "1-line summary of what this doc is about",\n'
        '  "audience": "who reads/uses this doc",\n'
        '  "agent_use_hint": "1 sentence on when an agent should consult it",\n'
        '  "key_entities": ["...", "..."]\n'
        "}\n"
        "Return ONLY the JSON object. No prose before or after."
    )


def build_prompt_s2_step2_narrative(profile_json: str) -> str:
    return (
        "Here is a JSON profile of a business document:\n\n"
        f"{profile_json}\n\n"
        f"Write 2-3 sentences ({MIN_WORDS}-{MAX_WORDS} words) describing what "
        "this document contains and when a voice agent should consult it. "
        "Plain prose, second person ('the agent'), no JSON, no headings, "
        "no preamble. Return only the description text."
    )


# ---------------------------------------------------------------------------
# Strategy S3 — Few-shot in-context
# ---------------------------------------------------------------------------

_S3_EXEMPLARS = """\
Example 1 — Document type: faq, Intended use: inbound, outbound
Description: This is a customer FAQ covering subscription mechanics, shipping, returns, and account management. The agent should consult it whenever a caller asks how the product works, about delivery options, or about billing changes, and quote the policy verbatim when stating the return window or shipping cost.

Example 2 — Document type: policy, Intended use: outbound
Description: A returns and refunds policy spelling out eligibility windows, refund methods, and exclusions for final-sale, wholesale, and international orders. The agent should pull from this whenever a caller asks whether their item qualifies for return, what timing they can expect, or how to start an RMA.
"""


def build_prompt_s3_fewshot(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    return (
        f"{_S3_EXEMPLARS}\n\n"
        f"Now write a description for this document.\n"
        f"{_chips_line(doc_type, intended_use)}\n\n"
        f"<document>\n{_truncate(document_text)}\n</document>\n\n"
        f"Write 2-3 sentences ({MIN_WORDS}-{MAX_WORDS} words). Plain prose, "
        f"second person ('the agent'), no JSON, no headings, no preamble. "
        f"Return only the description text."
    )


# ---------------------------------------------------------------------------
# Public selector — pinned to S2 (structured → narrative) after the
# 2026-05-28 eval. See docs/superpowers/specs/2026-05-28-describe-eval-results.md
# for scores and reasoning.
# ---------------------------------------------------------------------------


def build_describe_prompt(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    """Build the FIRST-step user prompt for the auto-describe LLM call.

    Winner: S2 (structured → narrative). The strategy is two-call:
        1. LLM call with build_describe_prompt(...) → JSON profile.
        2. LLM call with build_describe_prompt_step2(profile_json) → prose.

    Callers must run both steps in sequence.
    """
    return build_prompt_s2_step1_profile(document_text, doc_type, intended_use)


def build_describe_prompt_step2(profile_json: str) -> str:
    """Build the SECOND-step user prompt; consumes JSON from step 1."""
    return build_prompt_s2_step2_narrative(profile_json)
