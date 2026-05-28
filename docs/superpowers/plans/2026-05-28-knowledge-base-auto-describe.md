# Knowledge-Base Auto-Describe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Auto-write button next to the "Describe this document" field in the KB upload modal that calls an LLM to draft the description. The parse is cached in Redis so the eventual Upload & Process reuses it instead of double-parsing.

**Architecture:** New `POST /api/v1/knowledge-base/describe-preview` endpoint. Server hashes the file, looks up `kb:parse:{hash}` in Redis, parses via MPS on miss, calls LLM via the same plumbing as `doc_card_extraction`, returns a short description string. The existing background worker reads the same cache key before its own MPS call and deletes the key after reuse.

**Tech Stack:** FastAPI (multipart file upload), `redis.asyncio`, Pydantic v2, existing `mps_service_key_client.process_document`, existing `create_llm_service_from_provider`, Next.js 15 + React 19 + shadcn/ui (lucide `Sparkles`/`Loader2`).

**Spec reference:** [`docs/superpowers/specs/2026-05-28-knowledge-base-auto-describe-design.md`](../specs/2026-05-28-knowledge-base-auto-describe-design.md)

---

## File Structure

**Created:**

- `api/services/knowledge_base/parse_cache.py` — Redis wrapper for the MPS-parse cache. Single responsibility: set/get/delete by file hash.
- `api/services/knowledge_base/llm_resolution.py` — Shared helper extracted from `doc_card_extraction.py`. Resolves `(provider, model, api_key, kwargs)` for KB-side LLM calls.
- `api/services/knowledge_base/describe_prompts.py` — Houses the three candidate prompt strategies + the final `build_describe_prompt(...)` selector.
- `api/services/knowledge_base/describe_preview.py` — Service orchestrator: file hash → cache lookup → MPS parse → LLM call → description string.
- `api/services/knowledge_base/describe_prompt_eval.py` — Standalone script that runs all three strategies against the fixture corpus and writes a markdown report.
- `api/tests/fixtures/describe_eval/faq_small.txt`
- `api/tests/fixtures/describe_eval/policy_mid.txt`
- `api/tests/fixtures/describe_eval/legal_long.txt`
- `api/tests/test_knowledge_base_parse_cache.py`
- `api/tests/test_describe_preview_route.py`
- `api/tests/test_describe_preview_service.py`
- `docs/superpowers/specs/2026-05-28-describe-eval-results.md` — Generated during Task 6.

**Modified:**

- `api/schemas/knowledge_base.py` — Add `DescribePreviewResponseSchema`.
- `api/routes/knowledge_base.py` — Add `POST /describe-preview` route.
- `api/tasks/knowledge_base_processing.py` — Insert cache-reuse step before the MPS call.
- `api/services/knowledge_base/doc_card_extraction.py` — Replace the local `_resolve_extraction_llm` with the shared helper.
- `ui/src/app/files/DocumentUpload.tsx` — Add Auto-write button + handler + state.
- `ui/src/client/sdk.gen.ts`, `ui/src/client/types.gen.ts` — Auto-regenerated.

---

## Task 1: Redis-backed parse cache

**Files:**
- Create: `api/services/knowledge_base/parse_cache.py`
- Create: `api/tests/test_knowledge_base_parse_cache.py`

- [ ] **Step 1: Write failing tests for set/get/delete round-trip**

`api/tests/test_knowledge_base_parse_cache.py`:

```python
"""Tests for the KB parse cache (Redis-backed)."""

import json
from unittest.mock import AsyncMock

import pytest

from api.services.knowledge_base.parse_cache import (
    delete_cached_parse,
    get_cached_parse,
    set_cached_parse,
)


class _FakeRedis:
    """Minimal async Redis double for the cache contract."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        value = self.store.get(key)
        return value.encode() if isinstance(value, str) else value

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, key: str):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", _get_redis
    )
    return fake


async def test_set_then_get_returns_payload(fake_redis):
    payload = {"full_text": "hello", "chunks": [], "docling_metadata": {}}
    await set_cached_parse("abc123", payload)
    result = await get_cached_parse("abc123")
    assert result == payload


async def test_get_returns_none_for_missing_key(fake_redis):
    assert await get_cached_parse("does-not-exist") is None


async def test_set_uses_30_minute_ttl(fake_redis):
    await set_cached_parse("abc123", {"full_text": "hi", "chunks": [], "docling_metadata": {}})
    assert fake_redis.ttls["kb:parse:abc123"] == 30 * 60


async def test_delete_removes_key(fake_redis):
    await set_cached_parse("abc123", {"full_text": "hi", "chunks": [], "docling_metadata": {}})
    await delete_cached_parse("abc123")
    assert await get_cached_parse("abc123") is None


async def test_get_swallows_redis_errors_returns_none(monkeypatch):
    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", boom
    )
    assert await get_cached_parse("abc123") is None


async def test_set_swallows_redis_errors(monkeypatch):
    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "api.services.knowledge_base.parse_cache._get_redis", boom
    )
    # Should not raise.
    await set_cached_parse("abc123", {"full_text": "x", "chunks": [], "docling_metadata": {}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
  python -m pytest api/tests/test_knowledge_base_parse_cache.py -v
```
Expected: collection error / ImportError on `api.services.knowledge_base.parse_cache`.

- [ ] **Step 3: Implement the cache module**

`api/services/knowledge_base/parse_cache.py`:

```python
"""Redis-backed cache for MPS document-parse output.

The cache is keyed by SHA-256 file hash so identical files share a single
parse across the preview and upload paths. TTL is 30 minutes — long enough
for a user to write/edit their description before clicking Upload & Process.

Cache failures are best-effort: any Redis error is logged and treated as a
miss; the caller falls back to a fresh MPS parse.
"""

import json
from typing import Optional

from loguru import logger
from redis import asyncio as aioredis

from api.constants import REDIS_URL

PARSE_CACHE_TTL_SECONDS = 30 * 60
KEY_PREFIX = "kb:parse:"

_client: Optional[aioredis.Redis] = None


async def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = await aioredis.from_url(REDIS_URL)
    return _client


def _key(file_hash: str) -> str:
    return f"{KEY_PREFIX}{file_hash}"


async def get_cached_parse(file_hash: str) -> Optional[dict]:
    """Return the cached MPS-parse payload for this hash, or None."""
    try:
        client = await _get_redis()
        raw = await client.get(_key(file_hash))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning(f"Parse-cache get failed for {file_hash}: {exc}")
        return None


async def set_cached_parse(file_hash: str, payload: dict) -> None:
    """Store the MPS-parse payload under this hash with a 30-minute TTL."""
    try:
        client = await _get_redis()
        await client.set(
            _key(file_hash),
            json.dumps(payload),
            ex=PARSE_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning(f"Parse-cache set failed for {file_hash}: {exc}")


async def delete_cached_parse(file_hash: str) -> None:
    """Remove the cached parse for this hash (called after worker reuse)."""
    try:
        client = await _get_redis()
        await client.delete(_key(file_hash))
    except Exception as exc:
        logger.warning(f"Parse-cache delete failed for {file_hash}: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_knowledge_base_parse_cache.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/knowledge_base/parse_cache.py api/tests/test_knowledge_base_parse_cache.py
git commit -m "feat(kb): add Redis parse cache keyed by file hash"
```

---

## Task 2: Worker reuses cached parse on upload

**Files:**
- Modify: `api/tasks/knowledge_base_processing.py:108-160`
- Modify: `api/tests/test_knowledge_base_parse_cache.py` (add reuse-path test)

- [ ] **Step 1: Write failing test for worker reuse**

Append to `api/tests/test_knowledge_base_parse_cache.py`:

```python
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from api.tasks import knowledge_base_processing


async def test_worker_reuses_cached_parse_and_deletes_key(
    fake_redis, tmp_path, monkeypatch
):
    """When kb:parse:{hash} exists, worker must NOT call MPS, then delete the key."""
    # Build a tiny temp file the worker will treat as the downloaded S3 object.
    file_bytes = b"hello world"
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    cached_payload = {
        "mode": "full_document",
        "docling_metadata": {"pages": 1},
        "full_text": "Hello world cached.",
        "chunks": [],
    }
    await knowledge_base_processing.set_cached_parse(file_hash, cached_payload)

    # Patch the worker's collaborators.
    async def fake_download(s3_key, target_path):
        with open(target_path, "wb") as fh:
            fh.write(file_bytes)
        return True

    fake_doc = MagicMock(id=42, created_by=None, organization_id=7, retrieval_mode="full_document")
    monkeypatch.setattr(knowledge_base_processing.storage_fs, "adownload_file", fake_download)
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_status",
        AsyncMock(),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_metadata",
        AsyncMock(),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "get_document_by_hash",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "get_document_by_id",
        AsyncMock(return_value=fake_doc),
    )
    monkeypatch.setattr(
        knowledge_base_processing.db_client,
        "update_document_full_text",
        AsyncMock(),
    )
    mps_mock = AsyncMock()
    monkeypatch.setattr(
        knowledge_base_processing.mps_service_key_client,
        "process_document",
        mps_mock,
    )
    monkeypatch.setattr(
        knowledge_base_processing,
        "_enqueue_doc_card_extraction",
        AsyncMock(),
    )

    await knowledge_base_processing.process_knowledge_base_document(
        ctx={},
        document_id=42,
        s3_key="knowledge_base/7/abc/file.txt",
        organization_id=7,
        created_by_provider_id="user-1",
        retrieval_mode="full_document",
    )

    mps_mock.assert_not_called()
    # Cache key should be gone after reuse.
    assert await knowledge_base_processing.get_cached_parse(file_hash) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
python -m pytest api/tests/test_knowledge_base_parse_cache.py::test_worker_reuses_cached_parse_and_deletes_key -v
```
Expected: FAIL — worker currently calls MPS unconditionally.

- [ ] **Step 3: Wire cache reuse into the worker**

Edit `api/tasks/knowledge_base_processing.py`. Add imports at the top (after the existing `from api.services.storage import storage_fs`):

```python
from api.services.knowledge_base.parse_cache import (
    delete_cached_parse,
    get_cached_parse,
    set_cached_parse,
)
```

Then replace the block starting at `logger.info(f"Delegating document processing to MPS (mode={retrieval_mode})")` (currently lines ~151-160) with:

```python
mps_response = await get_cached_parse(file_hash)
if mps_response is not None:
    logger.info(
        f"Reusing cached MPS parse for document {document_id} (hash={file_hash[:12]}...)"
    )
    await delete_cached_parse(file_hash)
else:
    logger.info(
        f"Delegating document processing to MPS (mode={retrieval_mode})"
    )
    mps_response = await mps_service_key_client.process_document(
        file_path=temp_file_path,
        filename=filename,
        content_type=mime_type or "application/octet-stream",
        retrieval_mode=retrieval_mode,
        max_tokens=max_tokens,
        organization_id=organization_id,
        created_by=created_by_provider_id,
    )
```

- [ ] **Step 4: Run the new test plus the existing KB E2E suite to confirm no regression**

Run:
```bash
python -m pytest api/tests/test_knowledge_base_parse_cache.py -v
python -m pytest api/tests/test_kb_e2e_doc_card.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add api/tasks/knowledge_base_processing.py api/tests/test_knowledge_base_parse_cache.py
git commit -m "feat(kb): worker reuses cached MPS parse + deletes key after"
```

---

## Task 3: Extract shared LLM-resolution helper

**Files:**
- Create: `api/services/knowledge_base/llm_resolution.py`
- Modify: `api/services/knowledge_base/doc_card_extraction.py:205-238`

Pure refactor — existing behavior unchanged.

- [ ] **Step 1: Run existing doc-card tests as a baseline**

Run:
```bash
python -m pytest api/tests/test_kb_e2e_doc_card.py api/tests/test_doc_card_extraction_failure.py -v
```
Expected: all green. Note the count for the next comparison.

- [ ] **Step 2: Create the shared helper module**

`api/services/knowledge_base/llm_resolution.py`:

```python
"""Shared LLM resolution for KB-side LLM calls.

Resolves (provider, model, api_key, kwargs) from the document creator's
UserConfiguration, falling back to the Dograh MPS default tier when no
LLM is configured.
"""

import os
import random
from typing import Optional

from api.db import db_client


async def resolve_kb_llm(
    user_id: Optional[int],
) -> tuple[str, str, Optional[str], dict]:
    """Return (provider, model, api_key, kwargs) for KB-side LLM calls."""
    if not user_id:
        return _dograh_default()

    user_configuration = await db_client.get_user_configurations(user_id)
    llm_config = user_configuration.model_dump(exclude_none=True).get("llm")
    if not llm_config:
        return _dograh_default()

    provider = llm_config.get("provider", "dograh")
    model = llm_config.get("model", "default")
    api_key = llm_config.get("api_key")
    if isinstance(api_key, list):
        api_key = random.choice(api_key)

    kwargs: dict = {}
    if provider == "azure":
        kwargs["endpoint"] = llm_config.get("endpoint", "")
    elif provider in ("openrouter", "speaches") and llm_config.get("base_url"):
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


def _dograh_default() -> tuple[str, str, Optional[str], dict]:
    tier = os.environ.get("KB_DOC_CARD_MODEL_TIER", "default")
    return "dograh", tier, None, {}
```

- [ ] **Step 3: Replace the local copy in `doc_card_extraction.py`**

In `api/services/knowledge_base/doc_card_extraction.py`:

1. Remove the `_resolve_extraction_llm` and `_dograh_default` function bodies (lines ~205-238).
2. Remove the unused `import os` and `import random` lines if no other code in the file uses them (grep first).
3. Add this import near the other `api.services...` imports:

```python
from api.services.knowledge_base.llm_resolution import resolve_kb_llm
```

4. Change the single callsite from:

```python
provider, model, api_key, kwargs = await _resolve_extraction_llm(document.created_by)
```

to:

```python
provider, model, api_key, kwargs = await resolve_kb_llm(document.created_by)
```

- [ ] **Step 4: Run doc-card tests to confirm the refactor preserves behavior**

Run:
```bash
python -m pytest api/tests/test_kb_e2e_doc_card.py api/tests/test_doc_card_extraction_failure.py -v
```
Expected: same number of tests pass as in Step 1.

- [ ] **Step 5: Commit**

```bash
git add api/services/knowledge_base/llm_resolution.py api/services/knowledge_base/doc_card_extraction.py
git commit -m "refactor(kb): extract resolve_kb_llm into shared module"
```

---

## Task 4: Eval fixtures (text corpus)

**Files:**
- Create: `api/tests/fixtures/describe_eval/faq_small.txt`
- Create: `api/tests/fixtures/describe_eval/policy_mid.txt`
- Create: `api/tests/fixtures/describe_eval/legal_long.txt`

These are committed pre-parsed text so the eval script doesn't burn MPS calls on every run.

- [ ] **Step 1: Create `faq_small.txt`**

Write `api/tests/fixtures/describe_eval/faq_small.txt`:

```
NorthStar Coffee Subscription — FAQ

Q: How does the subscription work?
A: You pick a roast (light, medium, dark) and a frequency (weekly, biweekly, monthly). We grind fresh and ship the next business day.

Q: Can I pause or skip a shipment?
A: Yes. Log in, go to "My Subscription," and choose Pause or Skip. You can also do this from the order email.

Q: What's your return policy?
A: If the bag is unopened, mail it back within 14 days for a full refund. If the bag is opened, we'll issue store credit if the coffee was defective.

Q: Where do you ship?
A: Continental US only. We do not ship to Alaska, Hawaii, or international addresses.

Q: How is shipping priced?
A: Free for orders over $40, otherwise a flat $5.

Q: Do you offer gift subscriptions?
A: Yes — a 3-month or 6-month prepaid plan. The recipient gets a welcome email with their first ship date.

Q: How do I change my shipping address?
A: Update it in account settings at least 48 hours before your next ship date.

Q: Is the coffee fair-trade certified?
A: All single-origin lots are Fair Trade Certified. Our blends are sourced direct-trade.

Q: How do I cancel?
A: Cancel anytime from "My Subscription." There are no cancellation fees.

Q: Who do I email for help?
A: support@northstarcoffee.example with your order number.
```

- [ ] **Step 2: Create `policy_mid.txt`**

Write `api/tests/fixtures/describe_eval/policy_mid.txt`:

```
Bluepeak Outdoor — Returns & Refunds Policy (Effective 2025-01-01)

1. Scope
This policy applies to retail purchases made directly from Bluepeak Outdoor (online and physical retail). Wholesale, custom orders, and gift cards are excluded — see sections 7 and 8.

2. Eligibility window
Most items may be returned within 60 days of the original ship date. Apparel and footwear must be unworn, with original tags attached, in resellable condition. Hard goods (tents, packs, stoves, cookware) may be returned within 60 days regardless of whether they have been used in the field, provided they are not damaged beyond normal use during a sincere try-out.

3. Refund method
Refunds are issued to the original payment method within 5–10 business days of our receipt of the returned item. Gift returns are issued as store credit. Items purchased with store credit are refunded only as store credit.

4. Return shipping
Customers cover return shipping by default. We provide a prepaid return label at no cost if the return is due to our error (wrong item shipped, manufacturing defect, damage in transit).

5. Defective products
If a product fails due to a defect within the manufacturer's warranty period (typically 1 year, longer for premium lines), Bluepeak will repair, replace, or refund at our discretion. Contact warranty@bluepeak.example with photos and order number.

6. Final sale items
Items marked "Final Sale" at the time of purchase cannot be returned or exchanged for any reason other than a verified defect.

7. Wholesale orders
Wholesale customers must contact their account rep. The retail return window does not apply.

8. Gift cards
Gift cards are non-refundable.

9. International orders
International returns are accepted within 90 days but the customer is responsible for return shipping, customs duties, and any restocking fees. Refunds exclude original shipping.

10. How to initiate a return
Log into your account, find the order, click "Start a Return," and follow the prompts. You'll receive an RMA number and shipping instructions. Returns without an RMA number may be delayed or refused.

11. Questions
Email returns@bluepeak.example or call 1-800-555-0117 Monday–Friday, 9am–6pm Pacific.
```

- [ ] **Step 3: Create `legal_long.txt`**

Generate the text from the user-supplied PDF via MPS in full_document mode and commit the resulting text. Run this one-off:

```bash
source venv/bin/activate && set -a && source api/.env && set +a && \
  python -c "
import asyncio
from api.services.mps_service_key_client import mps_service_key_client

async def main():
    res = await mps_service_key_client.process_document(
        file_path='ATTACH_THE_USER_PDF_HERE',
        filename='Peramune-v-Savage-Garage-NZ-Ltd-2024-NZCA-512.pdf',
        content_type='application/pdf',
        retrieval_mode='full_document',
    )
    with open('api/tests/fixtures/describe_eval/legal_long.txt', 'w', encoding='utf-8') as fh:
        fh.write(res.get('full_text') or '')

asyncio.run(main())
"
```

(Engineer note: if the PDF isn't on disk, ask the user to drop it at the path above. Don't fabricate text.)

- [ ] **Step 4: Commit the fixtures**

```bash
git add api/tests/fixtures/describe_eval/
git commit -m "test(kb): add describe-prompt eval fixtures"
```

---

## Task 5: Candidate prompt strategies + eval harness

**Files:**
- Create: `api/services/knowledge_base/describe_prompts.py`
- Create: `api/services/knowledge_base/describe_prompt_eval.py`

The eval script imports the three candidate strategies from `describe_prompts.py`. After Task 6, the winning strategy stays; the losers can be removed in a follow-up commit.

- [ ] **Step 1: Create `describe_prompts.py` with all three candidates**

`api/services/knowledge_base/describe_prompts.py`:

```python
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
# Public selector — bound to the winner after Task 6
# ---------------------------------------------------------------------------

# NOTE: Updated in Task 6 to point at the winning strategy.
# Until then, the route is not wired up, so this default is unused at runtime.
def build_describe_prompt(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    """Build the user prompt for the auto-describe LLM call.

    Wired to the winning strategy in Task 6.
    """
    return build_prompt_s1_direct(document_text, doc_type, intended_use)
```

- [ ] **Step 2: Create the eval script**

`api/services/knowledge_base/describe_prompt_eval.py`:

```python
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
```

- [ ] **Step 3: Commit the candidates + harness**

```bash
git add api/services/knowledge_base/describe_prompts.py api/services/knowledge_base/describe_prompt_eval.py
git commit -m "feat(kb): three candidate prompts + eval harness for auto-describe"
```

---

## Task 6: Run the eval, pick the winner, pin it

This is the *experiment* task. The output of this task is **a single edit** to `build_describe_prompt` in `describe_prompts.py` that points at the winning strategy, plus a committed report.

- [ ] **Step 1: Run the eval against the user's local model stack**

Run:
```bash
source venv/bin/activate && set -a && source api/.env && set +a && \
  python -m api.services.knowledge_base.describe_prompt_eval
```

Expected: `docs/superpowers/specs/2026-05-28-describe-eval-results.md` is written. 27 entries: 3 docs × 3 strategies × 3 runs.

If the script errors with "no LLM configured": set `KB_DESCRIBE_EVAL_USER_ID` in `api/.env` to a user id whose `user_configuration.llm` has the local Speaches provider configured, then re-run.

- [ ] **Step 2: Score the report**

Open `docs/superpowers/specs/2026-05-28-describe-eval-results.md`. Score each of the 27 samples 1–5 on:

- **Faithfulness** — every claim traceable to the doc.
- **Usefulness** — does it tell the agent *when* to consult this doc?
- **Length discipline** — 40–80 words.
- **Cross-run consistency** — three runs of the same strategy on the same doc should read similarly.

Compute the mean score per strategy. Pick the winner. Append a final section to the report:

```markdown
## Recommendation

**Winner:** S<N> (<strategy name>)

**Mean scores (faithfulness / usefulness / length / consistency):**
- S1: a / b / c / d → overall <mean>
- S2: a / b / c / d → overall <mean>
- S3: a / b / c / d → overall <mean>

**One-sentence reasoning per strategy:**
- S1: …
- S2: …
- S3: …
```

- [ ] **Step 3: Confirm with the user**

Surface the recommendation in chat:

> "Eval results in `docs/superpowers/specs/2026-05-28-describe-eval-results.md`. My recommendation is **S<N>** because <reason>. Confirm or override?"

Block on the user's response. **Do not proceed without explicit confirmation or override.**

- [ ] **Step 4: Pin the winner**

Edit `api/services/knowledge_base/describe_prompts.py`. Replace the body of `build_describe_prompt` to call the winner directly:

```python
def build_describe_prompt(
    document_text: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
) -> str:
    """Build the user prompt for the auto-describe LLM call.

    Winner of the 2026-05-28 eval: <S1|S2|S3>.
    See docs/superpowers/specs/2026-05-28-describe-eval-results.md.
    """
    return build_prompt_s<N>_<name>(document_text, doc_type, intended_use)
```

For S2 (two-step), the service module needs both prompts. If S2 wins, also expose a second helper `build_describe_prompt_step2(profile_json)` from `describe_prompts.py` and document in the docstring that S2 is two-call. The service module (Task 7) must branch on that.

- [ ] **Step 5: Commit report + pinned prompt**

```bash
git add docs/superpowers/specs/2026-05-28-describe-eval-results.md api/services/knowledge_base/describe_prompts.py
git commit -m "feat(kb): pin auto-describe prompt to S<N> after eval"
```

---

## Task 7: Describe-preview service module

**Files:**
- Create: `api/services/knowledge_base/describe_preview.py`
- Create: `api/tests/test_describe_preview_service.py`

- [ ] **Step 1: Write the failing service-level tests**

`api/tests/test_describe_preview_service.py`:

```python
"""Tests for the describe-preview service orchestrator."""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.knowledge_base import describe_preview


@pytest.fixture
def patch_collaborators(monkeypatch, tmp_path):
    """Stub the cache, MPS, LLM, and resolver so we can assert call patterns."""
    # Cache fns
    cache_state: dict[str, dict] = {}

    async def fake_get(file_hash: str):
        return cache_state.get(file_hash)

    async def fake_set(file_hash: str, payload: dict):
        cache_state[file_hash] = payload

    monkeypatch.setattr(describe_preview, "get_cached_parse", fake_get)
    monkeypatch.setattr(describe_preview, "set_cached_parse", fake_set)

    # MPS
    mps_mock = AsyncMock(return_value={
        "mode": "full_document",
        "full_text": "Document body text.",
        "chunks": [],
        "docling_metadata": {},
    })
    monkeypatch.setattr(
        describe_preview.mps_service_key_client, "process_document", mps_mock
    )

    # LLM resolver
    async def fake_resolve(_user_id):
        return "speaches", "llama3", None, {"base_url": "http://localhost:8000/v1"}

    monkeypatch.setattr(describe_preview, "resolve_kb_llm", fake_resolve)

    # LLM service: factory returns an object whose .run_inference returns a string.
    llm_service = MagicMock()
    llm_service.run_inference = AsyncMock(return_value="An FAQ document about coffee subscriptions. The agent should consult it whenever a caller asks how the subscription works, about delivery options, or about returns.")
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


async def test_cache_miss_calls_mps_and_caches(patch_collaborators, tmp_path):
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


async def test_cache_hit_skips_mps(patch_collaborators, tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_bytes(b"hello body")
    file_hash = hashlib.sha256(b"hello body").hexdigest()

    # Pre-populate the cache.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_describe_preview_service.py -v
```
Expected: ImportError on `api.services.knowledge_base.describe_preview`.

- [ ] **Step 3: Implement the service module**

`api/services/knowledge_base/describe_preview.py`:

```python
"""Service that drafts a description for an uploaded document.

Flow: file hash → Redis cache lookup → MPS parse on miss → LLM call →
short description string. The MPS-parse payload is cached so the background
worker can reuse it instead of re-parsing during Upload & Process.
"""

import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional

from loguru import logger

from api.services.knowledge_base.describe_prompts import (
    SYSTEM_PROMPT,
    build_describe_prompt,
)
from api.services.knowledge_base.llm_resolution import resolve_kb_llm
from api.services.knowledge_base.parse_cache import (
    get_cached_parse,
    set_cached_parse,
)
from api.services.mps_service_key_client import mps_service_key_client
from api.services.pipecat.service_factory import create_llm_service_from_provider
from pipecat.processors.aggregators.llm_context import LLMContext

MAX_DESCRIPTION_CHARS = 600
PREVIEW_RETRIEVAL_MODE = "full_document"  # Cheaper than chunked; we only need text.


@dataclass
class DescribePreviewResult:
    description: str
    from_cache: bool


class DescribePreviewError(Exception):
    """Raised when the preview flow fails. `code` is one of:
        'parse_failed'  -- MPS could not extract text
        'llm_failed'    -- LLM call or post-processing failed
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256_of_file(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def _document_text_from_payload(payload: dict) -> str:
    full_text = payload.get("full_text")
    if full_text:
        return full_text
    chunks = payload.get("chunks") or []
    return "\n\n".join(c.get("chunk_text", "") for c in chunks)


def _post_process(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip()
    return text


async def generate_description_preview(
    *,
    file_path: str,
    filename: str,
    mime_type: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
    user_id: Optional[int],
) -> DescribePreviewResult:
    file_hash = _sha256_of_file(file_path)

    cached = await get_cached_parse(file_hash)
    if cached is not None:
        logger.info(f"describe-preview cache HIT for {file_hash[:12]}...")
        payload = cached
        from_cache = True
    else:
        logger.info(f"describe-preview cache MISS for {file_hash[:12]}...")
        try:
            payload = await mps_service_key_client.process_document(
                file_path=file_path,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
                retrieval_mode=PREVIEW_RETRIEVAL_MODE,
            )
        except Exception as exc:
            logger.warning(f"describe-preview MPS parse failed: {exc}")
            raise DescribePreviewError("parse_failed", str(exc)) from exc
        await set_cached_parse(file_hash, payload)
        from_cache = False

    document_text = _document_text_from_payload(payload)
    if not document_text.strip():
        raise DescribePreviewError("parse_failed", "MPS returned no text")

    provider, model, api_key, kwargs = await resolve_kb_llm(user_id)
    intended_list = list(intended_use) if intended_use else None
    user_prompt = build_describe_prompt(document_text, doc_type, intended_list)

    try:
        llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
        context = LLMContext()
        context.set_messages([{"role": "user", "content": user_prompt}])
        raw = await llm.run_inference(context, system_instruction=SYSTEM_PROMPT) or ""
    except Exception as exc:
        logger.warning(f"describe-preview LLM call failed: {exc}")
        raise DescribePreviewError("llm_failed", str(exc)) from exc

    description = _post_process(raw)
    if not description:
        raise DescribePreviewError("llm_failed", "LLM returned empty description")

    return DescribePreviewResult(description=description, from_cache=from_cache)
```

> **Note for engineer:** If Task 6 selected S2 (two-step), edit `generate_description_preview` to call the LLM twice — first with `build_prompt_s2_step1_profile`, then with `build_prompt_s2_step2_narrative(profile_json)`. The `describe_prompts` module exposes both helpers; pull whichever you need. Keep the cache/MPS/error wrapping unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_describe_preview_service.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/knowledge_base/describe_preview.py api/tests/test_describe_preview_service.py
git commit -m "feat(kb): describe-preview service (cache + MPS + LLM)"
```

---

## Task 8: HTTP route + Pydantic schema

**Files:**
- Modify: `api/schemas/knowledge_base.py`
- Modify: `api/routes/knowledge_base.py`
- Create: `api/tests/test_describe_preview_route.py`

- [ ] **Step 1: Write the failing route tests**

`api/tests/test_describe_preview_route.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest api/tests/test_describe_preview_route.py -v
```
Expected: failures because the endpoint doesn't exist yet.

- [ ] **Step 3: Add the response schema**

Append to `api/schemas/knowledge_base.py`:

```python
class DescribePreviewResponseSchema(BaseModel):
    """Response schema for the auto-describe preview endpoint."""

    description: str = Field(..., description="LLM-drafted description of the document")
    from_cache: bool = Field(
        ...,
        description="True if the underlying parse came from the Redis cache",
    )
```

- [ ] **Step 4: Add the route**

At the top of `api/routes/knowledge_base.py`, extend the imports:

```python
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from api.schemas.knowledge_base import (
    ChunkSearchRequestSchema,
    ChunkSearchResponseSchema,
    DescribePreviewResponseSchema,
    DocumentListResponseSchema,
    DocumentResponseSchema,
    DocumentUploadRequestSchema,
    DocumentUploadResponseSchema,
    EditDocumentRequestSchema,
    ProcessDocumentRequestSchema,
)
from api.services.knowledge_base.describe_preview import (
    DescribePreviewError,
    generate_description_preview,
)
```

Then add this route handler at the bottom of the file:

```python
DESCRIBE_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".json"}


@router.post(
    "/describe-preview",
    response_model=DescribePreviewResponseSchema,
    summary="Auto-draft a description for an uploaded document",
)
async def describe_preview(
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(default=None),
    intended_use: Optional[List[str]] = Form(default=None),
    user=Depends(get_user),
):
    """Generate a draft 'Describe this document' string via the LLM.

    The file is parsed once via MPS; the parse is cached for 30 minutes
    keyed by sha256 so the eventual Upload & Process reuses it. No DB
    record is created here.

    Access Control:
    * Authenticated users only. No org scoping needed — nothing is persisted.
    """
    filename = file.filename or "upload"
    extension = os.path.splitext(filename)[1].lower()
    if extension not in DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Allowed: "
                + ", ".join(sorted(DESCRIBE_PREVIEW_ALLOWED_EXTENSIONS))
            ),
        )

    content = await file.read()
    if len(content) > DESCRIBE_PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 5 MB limit")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
    try:
        tmp.write(content)
        tmp.close()

        try:
            result = await generate_description_preview(
                file_path=tmp.name,
                filename=filename,
                mime_type=file.content_type or "application/octet-stream",
                doc_type=doc_type,
                intended_use=intended_use,
                user_id=user.id,
            )
        except DescribePreviewError as exc:
            raise HTTPException(status_code=502, detail=exc.code) from exc

        return DescribePreviewResponseSchema(
            description=result.description,
            from_cache=result.from_cache,
        )
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
```

Also confirm `Optional`, `List` are imported at the top (they are — line 4).

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
python -m pytest api/tests/test_describe_preview_route.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/knowledge_base.py api/schemas/knowledge_base.py api/tests/test_describe_preview_route.py
git commit -m "feat(kb): POST /describe-preview endpoint"
```

---

## Task 9: Regenerate the UI SDK client

**Files:**
- Modify: `ui/src/client/sdk.gen.ts`, `ui/src/client/types.gen.ts` (auto-regenerated)

- [ ] **Step 1: Regenerate**

Run from project root:
```bash
cd ui && npm run generate-client
```

Expected: `sdk.gen.ts` now contains a new `describePreviewApiV1KnowledgeBaseDescribePreviewPost` function, and `types.gen.ts` contains `DescribePreviewResponseSchema`.

- [ ] **Step 2: Confirm the generated client compiles**

Run:
```bash
cd ui && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/client/
git commit -m "chore(ui): regenerate client for describe-preview"
```

---

## Task 10: UI Auto-write button

**Files:**
- Modify: `ui/src/app/files/DocumentUpload.tsx`

- [ ] **Step 1: Add imports + state**

At the top of `ui/src/app/files/DocumentUpload.tsx`, expand the lucide import and add the new SDK call:

```tsx
import { FileText, Info, Loader2, Sparkles, Upload, X } from 'lucide-react';
```

Add to the existing block of SDK imports (after `processDocumentApiV1KnowledgeBaseProcessDocumentPost`):

```tsx
import {
  describePreviewApiV1KnowledgeBaseDescribePreviewPost,
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
```

Inside the `DocumentUpload` component, add state next to the existing `useState` calls:

```tsx
const [isGeneratingDescription, setIsGeneratingDescription] = useState(false);
const [descriptionGenerated, setDescriptionGenerated] = useState(false);
```

In `clearSelectedFile`, reset the new state:

```tsx
setDescriptionGenerated(false);
```

- [ ] **Step 2: Add the handler**

Add this handler inside the component, near the other handlers (e.g. just above `uploadFile`):

```tsx
const handleAutoDescribe = async () => {
  if (!selectedFile) return;
  setIsGeneratingDescription(true);
  try {
    const response = await describePreviewApiV1KnowledgeBaseDescribePreviewPost({
      body: {
        file: selectedFile,
        doc_type: docType || undefined,
        intended_use:
          intendedUse.inbound || intendedUse.outbound
            ? [
                ...(intendedUse.inbound ? ['inbound'] : []),
                ...(intendedUse.outbound ? ['outbound'] : []),
              ]
            : undefined,
      },
    });
    if (response.error || !response.data) {
      const detail =
        (response.error as { detail?: string } | undefined)?.detail ?? 'unknown_error';
      const message =
        detail === 'parse_failed'
          ? "Couldn't read the document. Try writing a description manually."
          : detail === 'llm_failed'
            ? 'Auto-describe failed — please write one yourself.'
            : 'Could not generate description.';
      toast.error(message);
      return;
    }
    setUserDescription(response.data.description);
    setDescriptionGenerated(true);
    toast.success('Description generated');
  } catch (error) {
    logger.error('Auto-describe failed:', error);
    toast.error('Could not generate description.');
  } finally {
    setIsGeneratingDescription(false);
  }
};
```

- [ ] **Step 3: Render the button in the description block**

Replace the existing Description block (`{/* Description */}` through the closing `</div>` after the char counter). Locate it currently at lines ~289-311 of `DocumentUpload.tsx`. Replace with:

```tsx
        {/* Description */}
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label className="text-sm font-medium">
              Describe this document <span className="text-destructive">*</span>
            </Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={!selectedFile || isGeneratingDescription}
              onClick={handleAutoDescribe}
            >
              {isGeneratingDescription ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
              {isGeneratingDescription
                ? 'Generating…'
                : descriptionGenerated
                  ? 'Regenerate'
                  : 'Auto-write'}
            </Button>
          </div>
          <Textarea
            value={userDescription}
            onChange={(e) => setUserDescription(e.target.value)}
            placeholder="Explain what this document contains and how the agent should use it (min 20 characters)"
            className="min-h-20 max-h-40 w-full resize-none break-words"
          />
          <div className="flex justify-end">
            <span
              className={`text-xs ${
                userDescription.trim().length >= MIN_DESCRIPTION_LENGTH
                  ? 'text-muted-foreground'
                  : 'text-destructive'
              }`}
            >
              {userDescription.trim().length}/{MIN_DESCRIPTION_LENGTH} min characters
            </span>
          </div>
        </div>
```

- [ ] **Step 4: Verify TypeScript compiles**

Run:
```bash
cd ui && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/files/DocumentUpload.tsx
git commit -m "feat(ui): Auto-write button for KB document description"
```

---

## Task 11: Manual smoke test + final cleanup commit

This task is a manual walkthrough, no code. Document the result in the PR description.

- [ ] **Step 1: Start the stack**

In separate terminals (or as background processes):

```bash
# Terminal 1 — API
source venv/bin/activate && set -a && source api/.env && set +a && \
  uvicorn api.app:app --reload --port 8000

# Terminal 2 — ARQ worker
source venv/bin/activate && set -a && source api/.env && set +a && \
  arq api.tasks.arq.WorkerSettings

# Terminal 3 — UI
cd ui && npm run dev
```

- [ ] **Step 2: Run the golden path**

1. Open `http://localhost:3000/files`.
2. Click **Upload Document**.
3. Drop a small PDF or .txt file.
4. (Optional) Pick a doc type / intended use.
5. Click **Auto-write**. Confirm:
   - Button shows the spinner + "Generating…" label.
   - Textarea fills with the LLM draft within a few seconds.
   - Button now says **Regenerate**.
6. Edit the draft, then click **Upload & Process**.
7. Verify the doc appears in the list and reaches `completed` status.
8. In the API logs, look for the line `Reusing cached MPS parse for document <id>` — proves the worker reused the cache.

- [ ] **Step 3: Run the failure paths**

1. Try the button without picking a file → button stays disabled (good).
2. Pick an unsupported file (e.g. rename a .py to .pdf and check it's still rejected on the *real* type). Expect a toast.
3. With no LLM configured for your user, expect the "Auto-describe failed — please write one yourself." toast.

- [ ] **Step 4: Final guard — full test sweep**

Run:
```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
  python -m pytest api/tests/test_knowledge_base_parse_cache.py \
                   api/tests/test_describe_preview_service.py \
                   api/tests/test_describe_preview_route.py \
                   api/tests/test_kb_e2e_doc_card.py \
                   api/tests/test_doc_card_extraction_failure.py \
                   api/tests/test_knowledge_base_routes_with_metadata.py -v

cd ui && npx tsc --noEmit && npm run lint
```
Expected: all green.

- [ ] **Step 5: Open the PR**

```bash
gh pr create --title "feat(kb): auto-describe button + parse-cache reuse" --body "$(cat <<'EOF'
## Summary
- Adds an Auto-write button next to "Describe this document" in the KB upload modal. Calls a new `POST /api/v1/knowledge-base/describe-preview` endpoint that parses the file via MPS and asks the LLM to draft a 40-80 word description.
- MPS parse is cached in Redis (`kb:parse:{sha256}`, 30 min TTL); the existing `process_knowledge_base_document` worker reuses the cached parse on upload and deletes the key after.
- Shared LLM resolver extracted to `api/services/knowledge_base/llm_resolution.py`.
- Prompt selected via a 3-strategy × 3-doc × 3-run eval; results in `docs/superpowers/specs/2026-05-28-describe-eval-results.md`.

## Test plan
- [x] Cache module unit tests
- [x] Worker cache-reuse test
- [x] Describe-preview service tests (cache hit/miss, MPS failure, LLM failure)
- [x] Route tests (200, 400 bad type/size, 502 parse/llm)
- [x] Existing doc-card test suite still green
- [x] Manual: pick file → Auto-write → edit → Upload & Process → reuse logged → doc appears completed

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** §3 UX → Task 10. §4.1 endpoint → Task 8. §4.2 server flow → Task 7. §4.3 cache → Task 1 + Task 2. §4.4 LLM resolver → Task 3. §5 eval → Tasks 4–6. §6 frontend → Tasks 9–10. §7 errors → Task 7 (raises) + Task 8 (502s) + Task 10 (toasts). §8 tests → Tasks 1, 2, 7, 8, 11.
- **No placeholders.** Every code block is concrete.
- **Type consistency.** `DescribePreviewResult` and `DescribePreviewError` are defined in Task 7 and used by Task 8 imports. `DescribePreviewResponseSchema` is defined in Task 8 and used by the same Task 8 handler. `generate_description_preview` signature is used by both Task 7 (definition) and Task 8 (call site) — kwargs match. `build_describe_prompt` is defined in Task 5 and called in Task 7.
- **One known branch:** Task 6 + Task 7 — if S2 wins, the service module makes two LLM calls. That's flagged in both task notes and is the only conditional path.
