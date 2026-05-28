"""Standalone eval harness for the KB auto-describe prompt.

Runs each of the three candidate strategies (S1, S2, S3) over the three
fixture documents three times each, writes a markdown report.

Usage:
    source venv/bin/activate
    set -a && source api/.env && set +a
    python -m api.services.knowledge_base.describe_prompt_eval
"""

import asyncio
import os
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from api.services.knowledge_base.describe_prompts import (
    SYSTEM_PROMPT,
    build_prompt_s1_direct,
    build_prompt_s2_step1_profile,
    build_prompt_s2_step2_narrative,
    build_prompt_s3_fewshot,
)
from api.services.knowledge_base.llm_resolution import resolve_kb_llm
from api.services.pipecat.service_factory import create_llm_service_from_provider
from pipecat.processors.aggregators.llm_context import LLMContext

FIXTURE_DIR = Path("api/tests/fixtures/describe_eval")
REPORT_PATH = Path("docs/superpowers/specs/2026-05-28-describe-eval-results.md")
EVAL_USER_ID = os.environ.get("KB_DESCRIBE_EVAL_USER_ID")  # set to a real user id in api/.env
RUNS_PER_COMBO = 3

# Each fixture file → (display_name, doc_type, intended_use)
FIXTURES = [
    ("faq_small.txt", "Small FAQ", "faq", ["inbound", "outbound"]),
    ("policy_mid.txt", "Mid-length policy", "policy", ["inbound"]),
    ("legal_long.txt", "Long legal doc", "contract", ["outbound"]),
]


async def _run_llm(llm, system_prompt: str, user_prompt: str) -> str:
    context = LLMContext()
    context.set_messages([{"role": "user", "content": user_prompt}])
    raw = await llm.run_inference(context, system_instruction=system_prompt) or ""
    return raw.strip()


async def _eval_one(
    label: str,
    builder: Callable,
    document_text: str,
    doc_type: str,
    intended_use: list[str],
    llm,
) -> str:
    user_prompt = builder(document_text, doc_type, intended_use)
    return await _run_llm(llm, SYSTEM_PROMPT, user_prompt)


async def _eval_s2(
    document_text: str,
    doc_type: str,
    intended_use: list[str],
    llm,
) -> str:
    profile = await _run_llm(
        llm,
        SYSTEM_PROMPT,
        build_prompt_s2_step1_profile(document_text, doc_type, intended_use),
    )
    return await _run_llm(
        llm,
        SYSTEM_PROMPT,
        build_prompt_s2_step2_narrative(profile),
    )


async def main() -> None:
    user_id = int(EVAL_USER_ID) if EVAL_USER_ID else None
    provider, model, api_key, kwargs = await resolve_kb_llm(user_id)
    logger.info(f"Eval LLM: provider={provider}, model={model}")
    llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)

    lines: list[str] = []
    lines.append("# Auto-Describe Prompt Eval Results")
    lines.append("")
    lines.append(f"LLM: `provider={provider}, model={model}`")
    lines.append("")

    for filename, display_name, doc_type, intended_use in FIXTURES:
        path = FIXTURE_DIR / filename
        if not path.exists():
            logger.warning(f"Skipping missing fixture: {path}")
            continue
        document_text = path.read_text(encoding="utf-8")

        lines.append(f"## {display_name} (`{filename}`)")
        lines.append(f"- doc_type: `{doc_type}`")
        lines.append(f"- intended_use: `{intended_use}`")
        lines.append(f"- length: {len(document_text)} chars")
        lines.append("")

        for label, runner in [
            ("S1 (direct)", lambda dt, t, u: _eval_one("S1", build_prompt_s1_direct, dt, t, u, llm)),
            ("S2 (structured→narrative)", lambda dt, t, u: _eval_s2(dt, t, u, llm)),
            ("S3 (few-shot)", lambda dt, t, u: _eval_one("S3", build_prompt_s3_fewshot, dt, t, u, llm)),
        ]:
            lines.append(f"### Strategy: {label}")
            lines.append("")
            for run_index in range(1, RUNS_PER_COMBO + 1):
                logger.info(f"{filename} | {label} | run {run_index}")
                output = await runner(document_text, doc_type, intended_use)
                word_count = len(output.split())
                lines.append(f"**Run {run_index}** ({word_count} words):")
                lines.append("")
                lines.append("> " + output.replace("\n", "\n> "))
                lines.append("")

        lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Wrote report to {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
