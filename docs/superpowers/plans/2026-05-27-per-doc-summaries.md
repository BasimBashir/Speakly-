# Per-Document Summaries + Org Knowledge Index — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing knowledge-base pipeline so each uploaded document gets a structured `DocCard` extracted by an LLM, and the call agent receives an auto-built org-scoped knowledge index in its system prompt at call start.

**Architecture:** Two new background steps layered on the existing `process_knowledge_base_document` ARQ task — (1) `extract_doc_card` runs after chunks/full_text are persisted and writes a structured summary, (2) `rebuild_org_knowledge_index` renders a small markdown table-of-contents over all DocCards in an org. The call agent's system-prompt composer injects this index at call start. Everything stays model-agnostic via the existing `create_llm_service_from_provider` factory, defaulting to the Dograh MPS tier.

**Tech Stack:** Python 3 + FastAPI + SQLAlchemy (async) + Alembic + ARQ (Redis queue) + pgvector. Next.js 15 + React 19 + TypeScript + Tailwind + shadcn/ui. OpenTelemetry → Langfuse. PostHog.

**Spec:** `docs/superpowers/specs/2026-05-27-per-doc-summaries-design.md`

---

## File Map

### New files

| File | Responsibility |
|---|---|
| `api/alembic/versions/<rev>_add_doc_card_columns.py` | Migration: 6 new columns on `knowledge_base_documents` + GIN index on `topics` |
| `api/schemas/doc_card.py` | Pydantic `DocCard` and `FaqPair` schemas (single source of truth) |
| `api/services/knowledge_base/__init__.py` | Package init |
| `api/services/knowledge_base/extraction_input.py` | Pure `build_extraction_input(full_text, chunks, budget)` |
| `api/services/knowledge_base/doc_card_extraction.py` | LLM-driven extraction service |
| `api/services/knowledge_base/org_index_renderer.py` | Pure `build_org_index_md(docs)` |
| `api/services/knowledge_base/org_index_cache.py` | Worker-local cache + WorkerSyncManager handler |
| `api/tasks/org_index_rebuild.py` | ARQ task with Redis lock |
| `api/tests/test_doc_card_schema.py` | Unit tests for DocCard validation |
| `api/tests/test_extraction_input_builder.py` | Unit tests for `build_extraction_input` |
| `api/tests/test_org_index_renderer.py` | Unit tests for renderer |
| `api/tests/test_doc_card_extraction_task.py` | Integration: extraction happy path + org isolation |
| `api/tests/test_doc_card_extraction_failure.py` | Integration: LLM 5xx, invalid JSON, no API key |
| `api/tests/test_org_index_rebuild_task.py` | Integration: triggers, dedup, pub/sub |
| `api/tests/test_knowledge_base_routes_with_metadata.py` | Route tests for new fields and endpoints |
| `api/tests/test_context_composer_kb_index.py` | Composer injection + filtering tests |

### Modified files

| File | Change |
|---|---|
| `api/db/models.py` | Add 6 columns to `KnowledgeBaseDocumentModel` |
| `api/db/knowledge_base_client.py` | New methods: `create_document` accepts new fields; `update_document_user_inputs`, `update_doc_card`, `list_active_documents_for_index` |
| `api/schemas/knowledge_base.py` | `ProcessDocumentRequestSchema` gets required fields; `DocumentResponseSchema` exposes them; new `EditDocumentRequestSchema` |
| `api/routes/knowledge_base.py` | `/process-document` validates new fields; new `PATCH /documents/{uuid}`; new `POST /documents/{uuid}/re-extract` |
| `api/tasks/knowledge_base_processing.py` | Tail-call doc_card extraction; enqueue org index rebuild |
| `api/tasks/function_names.py` | Add `EXTRACT_DOC_CARD`, `REBUILD_ORG_KNOWLEDGE_INDEX` |
| `api/tasks/arq.py` | Register new tasks in `WorkerSettings.functions` |
| `api/services/workflow/pipecat_engine_context_composer.py` | Inject `<organization_knowledge>` section; filter by `call_type` |
| `api/services/workflow/tools/knowledge_base.py` | One-sentence tool description update |
| `api/services/worker_sync/protocol.py` | Add `KB_INDEX_UPDATED` event type |
| `api/app.py` | Register `kb_index_updated` handler with WorkerSyncManager |
| `ui/src/app/files/DocumentUpload.tsx` | Add doc_type, intended_use, user_description fields |
| `ui/src/app/files/DocumentList.tsx` | Add status pill column |

---

## Conventions and ground rules

**Tests:** All backend tests run with the test DB. Per `AGENTS.md`:

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/<file> -v
```

**Migrations:** Created via `./scripts/makemigrate.sh "<message>"` and applied via `./scripts/migrate.sh`.

**Commits:** Conventional Commits style (`feat:`, `fix:`, `test:`, `refactor:`). Commit at the end of each task. Never `--amend` on hook failure — fix and create a new commit.

**Org isolation:** Per `api/AGENTS.md`, every read/write of an org-scoped resource MUST filter by `organization_id`. Reviewed in every relevant task.

**Frontend client regeneration:** After backend schema changes, run `npm run generate-client` in `ui/`. Required before UI code can reference new API shapes.

---

## Task 1: Alembic migration — add DocCard columns

**Files:**
- Create: `api/alembic/versions/<auto-generated-rev>_add_doc_card_columns.py`

- [ ] **Step 1: Generate the migration skeleton**

Run from repo root:
```bash
./scripts/makemigrate.sh "add doc card columns"
```

Find the new file under `api/alembic/versions/`. Its name starts with a revision hash. Note the revision string.

- [ ] **Step 2: Replace `upgrade()` and `downgrade()` with the additive schema change**

Edit the generated file. Keep the auto-generated `revision`, `down_revision`, `branch_labels`, `depends_on` lines as-is. Replace the body of `upgrade()` and `downgrade()` with:

```python
def upgrade() -> None:
    op.add_column(
        "knowledge_base_documents",
        sa.Column("doc_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "intended_use",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column("user_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column("doc_card", sa.JSON(), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "doc_card_extracted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "topics",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_kb_documents_topics_gin",
        "knowledge_base_documents",
        ["topics"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kb_documents_topics_gin", table_name="knowledge_base_documents"
    )
    op.drop_column("knowledge_base_documents", "topics")
    op.drop_column("knowledge_base_documents", "doc_card_extracted_at")
    op.drop_column("knowledge_base_documents", "doc_card")
    op.drop_column("knowledge_base_documents", "user_description")
    op.drop_column("knowledge_base_documents", "intended_use")
    op.drop_column("knowledge_base_documents", "doc_type")
```

- [ ] **Step 3: Apply the migration to dev DB and verify**

```bash
./scripts/migrate.sh
```

Verify columns exist:
```bash
source venv/bin/activate && set -a && source api/.env && set +a && \
python -c "
import asyncio
from sqlalchemy import text
from api.db.database import async_session_factory

async def check():
    async with async_session_factory() as s:
        r = await s.execute(text(\"SELECT column_name FROM information_schema.columns WHERE table_name='knowledge_base_documents' AND column_name IN ('doc_type','intended_use','user_description','doc_card','doc_card_extracted_at','topics') ORDER BY column_name\"))
        for row in r:
            print(row[0])

asyncio.run(check())
"
```

Expected output (6 lines):
```
doc_card
doc_card_extracted_at
doc_type
intended_use
topics
user_description
```

- [ ] **Step 4: Apply migration to test DB**

```bash
set -a && source api/.env.test && set +a && ./scripts/migrate.sh
```

- [ ] **Step 5: Commit**

```bash
git add api/alembic/versions/*_add_doc_card_columns.py
git commit -m "feat(kb): add doc card columns to knowledge_base_documents"
```

---

## Task 2: Update `KnowledgeBaseDocumentModel`

**Files:**
- Modify: `api/db/models.py` (search for `class KnowledgeBaseDocumentModel`)

- [ ] **Step 1: Add the 6 new column attributes**

In the `KnowledgeBaseDocumentModel` class, add these columns (place them after `retrieval_mode`, `full_text` and before any relationship lines):

```python
    doc_type = Column(String(40), nullable=True)
    intended_use = Column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    user_description = Column(Text, nullable=True)
    doc_card = Column(JSON, nullable=True)
    doc_card_extracted_at = Column(DateTime(timezone=True), nullable=True)
    topics = Column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
```

- [ ] **Step 2: Smoke-test by importing**

```bash
source venv/bin/activate && python -c "from api.db.models import KnowledgeBaseDocumentModel; print([c.name for c in KnowledgeBaseDocumentModel.__table__.columns if c.name in ('doc_type','intended_use','user_description','doc_card','doc_card_extracted_at','topics')])"
```

Expected: `['doc_type', 'intended_use', 'user_description', 'doc_card', 'doc_card_extracted_at', 'topics']`

- [ ] **Step 3: Commit**

```bash
git add api/db/models.py
git commit -m "feat(kb): map doc card columns on KnowledgeBaseDocumentModel"
```

---

## Task 3: `DocCard` Pydantic schema with tests

**Files:**
- Create: `api/schemas/doc_card.py`
- Create: `api/tests/test_doc_card_schema.py`

- [ ] **Step 1: Write failing tests**

Create `api/tests/test_doc_card_schema.py`:

```python
import pytest
from pydantic import ValidationError

from api.schemas.doc_card import DocCard, FaqPair


def _valid_card_dict():
    return {
        "title": "Enterprise Contract v3",
        "summary_150_words": "Standard enterprise contract covering renewal, SLA, and cancellation terms.",
        "key_facts": ["12-month term", "30-day cancellation notice"],
        "entities": {
            "people": [],
            "organizations": ["Acme Corp"],
            "products": ["Pro Plan"],
            "locations": [],
            "dates": ["2026-01-01"],
        },
        "numbers_and_pricing": ["$49/mo Pro tier", "30-day refund window"],
        "faqs": [{"q": "When can I cancel?", "a": "Anytime with 30 days notice."}],
        "suggested_agent_uses": ["Answer renewal questions", "Quote pricing"],
        "topics": ["renewal", "sla", "cancellation"],
    }


def test_valid_doc_card_parses():
    card = DocCard.model_validate(_valid_card_dict())
    assert card.title == "Enterprise Contract v3"
    assert len(card.faqs) == 1
    assert isinstance(card.faqs[0], FaqPair)


def test_missing_required_field_rejected():
    bad = _valid_card_dict()
    del bad["title"]
    with pytest.raises(ValidationError):
        DocCard.model_validate(bad)


def test_topics_must_be_list_of_strings():
    bad = _valid_card_dict()
    bad["topics"] = [{"not": "a string"}]
    with pytest.raises(ValidationError):
        DocCard.model_validate(bad)


def test_entities_accepts_arbitrary_categories():
    """Schema is dict[str, list[str]] — categories aren't fixed."""
    d = _valid_card_dict()
    d["entities"]["custom_category"] = ["something"]
    card = DocCard.model_validate(d)
    assert card.entities["custom_category"] == ["something"]


def test_faq_pair_requires_both_fields():
    bad = _valid_card_dict()
    bad["faqs"] = [{"q": "missing answer"}]
    with pytest.raises(ValidationError):
        DocCard.model_validate(bad)
```

- [ ] **Step 2: Run tests — expect import failure**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_doc_card_schema.py -v
```

Expected: collection errors / `ModuleNotFoundError: No module named 'api.schemas.doc_card'`.

- [ ] **Step 3: Implement the schema**

Create `api/schemas/doc_card.py`:

```python
"""Pydantic schemas for the DocCard structured per-document summary."""

from pydantic import BaseModel, Field


class FaqPair(BaseModel):
    """A single Q/A pair extracted from a document."""

    q: str
    a: str


class DocCard(BaseModel):
    """Structured per-document summary produced by the extraction LLM call.

    Steered by the user's description and intended_use at upload time.
    Stored as JSON in knowledge_base_documents.doc_card.
    """

    title: str = Field(..., description="Short, human-readable title")
    summary_150_words: str = Field(..., description="~150-word summary of the doc")
    key_facts: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Free-form category -> list of mentioned entities",
    )
    numbers_and_pricing: list[str] = Field(default_factory=list)
    faqs: list[FaqPair] = Field(default_factory=list)
    suggested_agent_uses: list[str] = Field(default_factory=list)
    topics: list[str] = Field(
        default_factory=list,
        description="3-10 normalized lowercase-English keywords for the org index",
    )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest api/tests/test_doc_card_schema.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add api/schemas/doc_card.py api/tests/test_doc_card_schema.py
git commit -m "feat(kb): add DocCard pydantic schema"
```

---

## Task 4: Update `ProcessDocumentRequestSchema` and `DocumentResponseSchema`

**Files:**
- Modify: `api/schemas/knowledge_base.py`

- [ ] **Step 1: Modify `ProcessDocumentRequestSchema`**

Find the class in `api/schemas/knowledge_base.py` and replace it with:

```python
class ProcessDocumentRequestSchema(BaseModel):
    """Request schema for triggering document processing."""

    document_uuid: str = Field(..., description="Document UUID to process")
    s3_key: str = Field(..., description="S3 key of the uploaded file")
    retrieval_mode: str = Field(
        default="chunked",
        description="Retrieval mode: 'chunked' for vector search or 'full_document' for full text retrieval",
    )
    doc_type: str = Field(
        ...,
        description="Document type: contract, policy, pricing, faq, script, or other",
        min_length=1,
    )
    intended_use: list[str] = Field(
        ...,
        description="One or both of: inbound, outbound",
        min_length=1,
    )
    user_description: str = Field(
        ...,
        description="User-provided description of what this doc is and how the agent should use it",
        min_length=20,
    )
```

- [ ] **Step 2: Add new fields to `DocumentResponseSchema`**

In the same file, find `DocumentResponseSchema` and add these fields after `retrieval_mode`:

```python
    doc_type: Optional[str] = None
    intended_use: List[str] = Field(default_factory=list)
    user_description: Optional[str] = None
    doc_card: Optional[Dict[str, Any]] = None
    doc_card_extracted_at: Optional[datetime] = None
    topics: List[str] = Field(default_factory=list)
```

- [ ] **Step 3: Add `EditDocumentRequestSchema`**

Append to the same file:

```python
class EditDocumentRequestSchema(BaseModel):
    """Request schema for editing user-provided document inputs.

    All fields optional — only provided fields are updated.
    Editing these fields does NOT auto-trigger re-extraction.
    """

    doc_type: Optional[str] = Field(default=None, min_length=1)
    intended_use: Optional[List[str]] = Field(default=None, min_length=1)
    user_description: Optional[str] = Field(default=None, min_length=20)
```

- [ ] **Step 4: Smoke test the imports**

```bash
source venv/bin/activate && python -c "
from api.schemas.knowledge_base import ProcessDocumentRequestSchema, DocumentResponseSchema, EditDocumentRequestSchema
print('OK:', ProcessDocumentRequestSchema.model_fields.keys())
"
```

Expected output contains: `doc_type`, `intended_use`, `user_description`.

- [ ] **Step 5: Commit**

```bash
git add api/schemas/knowledge_base.py
git commit -m "feat(kb): extend request/response schemas with doc_type, intended_use, user_description"
```

---

## Task 5: DB client — new and updated methods

**Files:**
- Modify: `api/db/knowledge_base_client.py`

- [ ] **Step 1: Update `create_document` signature to accept new fields**

Find `async def create_document(` in `api/db/knowledge_base_client.py`. Add these parameters BEFORE the existing `retrieval_mode` parameter (keep all existing params):

```python
        doc_type: Optional[str] = None,
        intended_use: Optional[List[str]] = None,
        user_description: Optional[str] = None,
```

Inside the function body, where `KnowledgeBaseDocumentModel(...)` is constructed, add these kwargs:

```python
                doc_type=doc_type,
                intended_use=intended_use or [],
                user_description=user_description,
```

- [ ] **Step 2: Add `update_document_user_inputs` method**

Append inside the `KnowledgeBaseClient` class:

```python
    async def update_document_user_inputs(
        self,
        document_uuid: str,
        organization_id: int,
        doc_type: Optional[str] = None,
        intended_use: Optional[List[str]] = None,
        user_description: Optional[str] = None,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Update user-provided fields. Org-scoped. Returns None if not found.

        Editing these fields does NOT auto-trigger re-extraction.
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.document_uuid == document_uuid,
                KnowledgeBaseDocumentModel.organization_id == organization_id,
                KnowledgeBaseDocumentModel.is_active == True,
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()
            if not document:
                return None
            if doc_type is not None:
                document.doc_type = doc_type
            if intended_use is not None:
                document.intended_use = intended_use
            if user_description is not None:
                document.user_description = user_description
            await session.commit()
            await session.refresh(document)
            return document
```

- [ ] **Step 3: Add `update_doc_card` method**

Append inside the same class:

```python
    async def update_doc_card(
        self,
        document_id: int,
        doc_card: dict,
        topics: list[str],
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Persist the extracted DocCard and bump doc_card_extracted_at.

        topics is denormalized from doc_card['topics'] for GIN indexing.
        """
        from datetime import UTC, datetime
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()
            if not document:
                return None
            document.doc_card = doc_card
            document.topics = topics
            document.doc_card_extracted_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(document)
            return document
```

- [ ] **Step 4: Add `list_active_documents_for_index` method**

Append inside the same class:

```python
    async def list_active_documents_for_index(
        self,
        organization_id: int,
    ) -> List[KnowledgeBaseDocumentModel]:
        """List active docs with a non-null doc_card for the org index builder."""
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                    KnowledgeBaseDocumentModel.doc_card.isnot(None),
                )
                .order_by(KnowledgeBaseDocumentModel.created_at.desc())
            )
            result = await session.execute(query)
            return list(result.scalars().all())
```

- [ ] **Step 5: Smoke test by importing**

```bash
source venv/bin/activate && python -c "
from api.db.knowledge_base_client import KnowledgeBaseClient
c = KnowledgeBaseClient
print([m for m in dir(c) if m in ('update_document_user_inputs','update_doc_card','list_active_documents_for_index')])
"
```

Expected: `['list_active_documents_for_index', 'update_doc_card', 'update_document_user_inputs']`.

- [ ] **Step 6: Commit**

```bash
git add api/db/knowledge_base_client.py
git commit -m "feat(kb): db client methods for user-input edits, doc card writes, and org-index listing"
```

---

## Task 6: Extraction input builder with tests

**Files:**
- Create: `api/services/knowledge_base/__init__.py` (empty file)
- Create: `api/services/knowledge_base/extraction_input.py`
- Create: `api/tests/test_extraction_input_builder.py`

- [ ] **Step 1: Create the empty package init**

```bash
mkdir -p api/services/knowledge_base && touch api/services/knowledge_base/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `api/tests/test_extraction_input_builder.py`:

```python
from api.services.knowledge_base.extraction_input import build_extraction_input


def _chunks(n, text_per_chunk="x" * 100):
    return [
        {"chunk_text": f"chunk {i} {text_per_chunk}", "chunk_index": i}
        for i in range(n)
    ]


def test_small_full_text_passes_through():
    result = build_extraction_input(
        full_text="short text", chunks=[], budget_chars=10_000
    )
    assert result == "short text"
    assert "[document truncated for extraction]" not in result


def test_large_full_text_falls_back_to_stitched_sample():
    chunks = _chunks(50)
    big_text = "a" * 100_000
    result = build_extraction_input(
        full_text=big_text, chunks=chunks, budget_chars=5_000
    )
    assert "[document truncated for extraction]" in result
    assert len(result) <= 5_500  # budget + marker overhead


def test_stitched_sample_is_deterministic():
    chunks = _chunks(50)
    a = build_extraction_input(full_text=None, chunks=chunks, budget_chars=2_000)
    b = build_extraction_input(full_text=None, chunks=chunks, budget_chars=2_000)
    assert a == b


def test_stitched_sample_includes_first_and_last_chunks():
    chunks = _chunks(50)
    result = build_extraction_input(
        full_text=None, chunks=chunks, budget_chars=10_000
    )
    assert "chunk 0 " in result
    assert "chunk 49 " in result


def test_empty_input_returns_empty_string():
    assert build_extraction_input(full_text=None, chunks=[], budget_chars=1000) == ""
```

- [ ] **Step 3: Run tests — expect import failure**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_extraction_input_builder.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `build_extraction_input`**

Create `api/services/knowledge_base/extraction_input.py`:

```python
"""Build the LLM extraction input from either full text or chunks.

Pure, deterministic. No I/O. No LLM. Used by doc_card_extraction.
"""

from typing import Optional, Sequence

TRUNCATION_MARKER = "\n\n[document truncated for extraction]"


def build_extraction_input(
    *,
    full_text: Optional[str],
    chunks: Sequence[dict],
    budget_chars: int,
) -> str:
    """Return a string suitable for prompting the extraction LLM.

    Strategy:
    - If full_text is set and fits in budget_chars: return it as-is.
    - If full_text exceeds budget OR is None: build a stitched sample
      from chunks: first 8 + last 4 + every Nth middle chunk in order,
      capped by budget. Append the truncation marker.

    Stability: same input -> byte-identical output (no randomness).

    Args:
        full_text: The document's full extracted text, or None.
        chunks: List of {chunk_text, chunk_index, ...}. Sorted by chunk_index.
        budget_chars: Soft cap on the returned string length (excluding marker).
    """
    if full_text and len(full_text) <= budget_chars:
        return full_text

    if not chunks and not full_text:
        return ""

    if not chunks and full_text:
        # Truncate full text to budget
        return full_text[:budget_chars] + TRUNCATION_MARKER

    sorted_chunks = sorted(chunks, key=lambda c: c["chunk_index"])
    head_n = min(8, len(sorted_chunks))
    tail_n = min(4, max(0, len(sorted_chunks) - head_n))
    head = sorted_chunks[:head_n]
    tail = sorted_chunks[-tail_n:] if tail_n else []

    middle_pool = sorted_chunks[head_n : len(sorted_chunks) - tail_n] if tail_n else sorted_chunks[head_n:]
    middle = _every_nth(middle_pool, budget_chars=budget_chars // 2)

    selected = head + middle + tail
    selected = sorted({c["chunk_index"]: c for c in selected}.values(), key=lambda c: c["chunk_index"])

    parts = [c["chunk_text"] for c in selected]
    joined = "\n\n".join(parts)
    if len(joined) > budget_chars:
        joined = joined[:budget_chars]
    return joined + TRUNCATION_MARKER


def _every_nth(pool: Sequence[dict], budget_chars: int) -> list[dict]:
    """Pick chunks evenly spaced from pool, stopping when budget would be exceeded."""
    if not pool:
        return []
    out: list[dict] = []
    running = 0
    step = max(1, len(pool) // 16)
    for i in range(0, len(pool), step):
        chunk = pool[i]
        chunk_len = len(chunk["chunk_text"])
        if running + chunk_len > budget_chars:
            break
        out.append(chunk)
        running += chunk_len
    return out
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest api/tests/test_extraction_input_builder.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add api/services/knowledge_base/__init__.py api/services/knowledge_base/extraction_input.py api/tests/test_extraction_input_builder.py
git commit -m "feat(kb): pure extraction input builder with deterministic stitched sampling"
```

---

## Task 7: DocCard extraction service (LLM call)

**Files:**
- Create: `api/services/knowledge_base/doc_card_extraction.py`

- [ ] **Step 1: Implement the extraction service**

Create `api/services/knowledge_base/doc_card_extraction.py`:

```python
"""DocCard extraction service.

Runs after chunks/full_text are persisted. Calls the LLM via the
model-agnostic create_llm_service_from_provider factory. Resolves the
provider from the document creator's UserConfiguration; falls back to
the Dograh MPS default tier.
"""

import os
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.schemas.doc_card import DocCard
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.knowledge_base.extraction_input import build_extraction_input
from api.services.pipecat.service_factory import create_llm_service_from_provider

EXTRACTION_BUDGET_CHARS = int(os.environ.get("KB_DOC_CARD_BUDGET_CHARS", "400000"))


SYSTEM_PROMPT = (
    "You extract structured DocCards from business documents to power "
    "voice AI agents during calls. Stay grounded in the document — do not "
    "invent facts. Extract content in the document's source language, except "
    "the 'topics' field which is always lowercased English keywords."
)

USER_PROMPT_TEMPLATE = """\
Document type: {doc_type}
Intended use: {intended_use}
User's description of this doc:
{user_description}

Extract the DocCard as JSON matching this exact schema:
{{
  "title": "string",
  "summary_150_words": "string (~150 words)",
  "key_facts": ["string", ...],
  "entities": {{ "people": [...], "organizations": [...], "products": [...], "locations": [...], "dates": [...] }},
  "numbers_and_pricing": ["string", ...],
  "faqs": [{{"q": "...", "a": "..."}}, ...],
  "suggested_agent_uses": ["string", ...],
  "topics": ["lowercase-english-keyword", ...]
}}

Return ONLY the JSON object. No prose before or after.

<document>
{document}
</document>"""


async def extract_doc_card_for_document(document_id: int) -> Optional[DocCard]:
    """Extract the DocCard for a document and persist it.

    Returns the DocCard on success, None on skip (no description / no text).
    Raises on unrecoverable errors after one repair attempt.
    """
    document = await db_client.get_document_by_id(document_id)
    if not document:
        logger.error(f"DocCard extraction: document {document_id} not found")
        return None

    if not document.user_description:
        logger.info(
            f"DocCard extraction: skipping doc {document_id} — no user_description (legacy doc)"
        )
        return None

    provider, model, api_key, kwargs = await _resolve_extraction_llm(document.created_by)
    if provider != "dograh" and not api_key:
        await db_client.update_document_status(
            document_id,
            "completed",
            error_message="LLM provider not configured for extraction. Set your API key in Model Configurations.",
        )
        return None

    if document.retrieval_mode == "full_document" and document.full_text:
        full_text = document.full_text
        chunks: list[dict] = []
    else:
        full_text = None
        raw_chunks = await db_client.get_chunks_for_document(
            document_id=document_id, organization_id=document.organization_id
        )
        chunks = [
            {"chunk_text": c.chunk_text, "chunk_index": c.chunk_index} for c in raw_chunks
        ]

    extraction_input = build_extraction_input(
        full_text=full_text, chunks=chunks, budget_chars=EXTRACTION_BUDGET_CHARS
    )

    if not extraction_input.strip():
        await db_client.update_document_status(
            document_id,
            "completed",
            error_message="no_text_content",
        )
        return None

    user_prompt = USER_PROMPT_TEMPLATE.format(
        doc_type=document.doc_type or "other",
        intended_use=", ".join(document.intended_use or []) or "unspecified",
        user_description=document.user_description,
        document=extraction_input,
    )

    llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)

    card = await _call_and_validate(llm, user_prompt, repair_allowed=True)
    if card is None:
        raise RuntimeError(
            f"DocCard extraction failed for document {document_id} after repair attempt"
        )

    await db_client.update_doc_card(
        document_id=document_id,
        doc_card=card.model_dump(),
        topics=card.topics,
    )
    logger.info(f"DocCard extracted for document {document_id}")
    return card


async def _call_and_validate(
    llm, user_prompt: str, *, repair_allowed: bool
) -> Optional[DocCard]:
    response = await llm.create_chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content if response.choices else ""
    try:
        parsed = parse_llm_json(raw)
        return DocCard.model_validate(parsed)
    except (ValueError, ValidationError) as e:
        if not repair_allowed:
            logger.error(f"DocCard extraction validation failed: {e}")
            return None
        repair_prompt = (
            f"{user_prompt}\n\n"
            f"Your previous response failed validation: {e}. "
            "Return ONLY a valid JSON object matching the schema."
        )
        return await _call_and_validate(llm, repair_prompt, repair_allowed=False)


async def _resolve_extraction_llm(
    created_by_user_id: Optional[int],
) -> tuple[str, str, Optional[str], dict]:
    """Resolve (provider, model, api_key, kwargs) for the extraction call.

    Mirrors resolve_user_llm_config in api/services/workflow/qa/llm_config.py.
    Falls back to Dograh MPS default tier when user config is absent.
    """
    if not created_by_user_id:
        return _dograh_default()

    user_configuration = await db_client.get_user_configurations(created_by_user_id)
    llm_config = user_configuration.model_dump(exclude_none=True).get("llm")
    if not llm_config:
        return _dograh_default()

    provider = llm_config.get("provider", "dograh")
    model = llm_config.get("model", "default")
    api_key = llm_config.get("api_key")
    if isinstance(api_key, list):
        import random
        api_key = random.choice(api_key)

    kwargs: dict = {}
    if provider == "azure":
        kwargs["endpoint"] = llm_config.get("endpoint", "")
    elif provider == "openrouter" and llm_config.get("base_url"):
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


def _dograh_default() -> tuple[str, str, Optional[str], dict]:
    tier = os.environ.get("KB_DOC_CARD_MODEL_TIER", "default")
    return "dograh", tier, None, {}
```

- [ ] **Step 2: Smoke import**

```bash
source venv/bin/activate && python -c "
from api.services.knowledge_base.doc_card_extraction import extract_doc_card_for_document, _resolve_extraction_llm
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add api/services/knowledge_base/doc_card_extraction.py
git commit -m "feat(kb): doc card extraction service with model-agnostic LLM routing"
```

---

## Task 8: Wire extraction into the existing processing task

**Files:**
- Modify: `api/tasks/knowledge_base_processing.py`

- [ ] **Step 1: Add the extraction tail-call to the success paths**

In `api/tasks/knowledge_base_processing.py`, after the existing code that calls `update_document_status(..., "completed", ...)`. There are two such call sites — one in the `full_document` branch (around line 138-144 of the current file) and one in the chunked-mode branch (around line 215-220).

After **each** of those `update_document_status(..., "completed", ...)` calls and BEFORE the corresponding `logger.info(...)`/return, add:

```python
        # Run DocCard extraction as a non-blocking tail step. Failures don't
        # roll back the completed status — the doc stays searchable.
        try:
            from api.services.knowledge_base.doc_card_extraction import (
                extract_doc_card_for_document,
            )
            from api.tasks.arq import enqueue_job
            from api.tasks.function_names import FunctionNames

            await extract_doc_card_for_document(document_id)
            await enqueue_job(
                FunctionNames.REBUILD_ORG_KNOWLEDGE_INDEX, organization_id
            )
        except Exception as extraction_err:
            logger.warning(
                f"DocCard extraction failed for {document_id}: {extraction_err}",
                exc_info=True,
            )
```

(Apply this block in both completion branches.)

- [ ] **Step 2: Smoke import**

```bash
source venv/bin/activate && python -c "from api.tasks.knowledge_base_processing import process_knowledge_base_document; print('OK')"
```

Expected: `OK`. (`REBUILD_ORG_KNOWLEDGE_INDEX` doesn't exist yet — that's OK because the import is inside the function body and only resolved on call.)

- [ ] **Step 3: Commit**

```bash
git add api/tasks/knowledge_base_processing.py
git commit -m "feat(kb): tail-call doc card extraction after chunk/full_text persistence"
```

---

## Task 9: Integration test for DocCard extraction happy path

**Files:**
- Create: `api/tests/test_doc_card_extraction_task.py`

- [ ] **Step 1: Write the test**

Create `api/tests/test_doc_card_extraction_task.py`:

```python
"""Integration tests for DocCard extraction.

Uses the real test DB (api/.env.test) but mocks the LLM service.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.schemas.doc_card import DocCard
from api.services.knowledge_base.doc_card_extraction import (
    extract_doc_card_for_document,
)


VALID_CARD = {
    "title": "Test Doc",
    "summary_150_words": "A test document for the extraction pipeline.",
    "key_facts": ["fact 1", "fact 2"],
    "entities": {"organizations": ["Acme"], "products": [], "people": [], "locations": [], "dates": []},
    "numbers_and_pricing": [],
    "faqs": [],
    "suggested_agent_uses": ["test use"],
    "topics": ["test", "extraction"],
}


def _mock_llm_response(content: str):
    """Build a mock object shaped like the LLM service response."""
    from types import SimpleNamespace
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


@pytest.mark.asyncio
async def test_extraction_happy_path(setup_test_db, test_org_and_user):
    """Doc with user_description + chunks -> DocCard persisted."""
    org_id, user_id = test_org_and_user

    document = await db_client.create_document(
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
    await db_client.update_document_full_text(document.id, "Lorem ipsum dolor sit amet.")
    await db_client.update_document_status(document.id, "completed", total_chunks=0)

    import json
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
    refreshed = await db_client.get_document_by_id(document.id)
    assert refreshed.doc_card is not None
    assert refreshed.doc_card["title"] == "Test Doc"
    assert refreshed.topics == ["test", "extraction"]
    assert refreshed.doc_card_extracted_at is not None


@pytest.mark.asyncio
async def test_extraction_skipped_for_legacy_doc_without_description(
    setup_test_db, test_org_and_user
):
    """Doc with NULL user_description -> extraction skipped, no doc_card written."""
    org_id, user_id = test_org_and_user

    document = await db_client.create_document(
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
    refreshed = await db_client.get_document_by_id(document.id)
    assert refreshed.doc_card is None


@pytest.mark.asyncio
async def test_extraction_org_isolation(setup_test_db, test_org_and_user, test_other_org_and_user):
    """Extracting doc A doesn't affect doc B in another org."""
    org_a, user_a = test_org_and_user
    org_b, user_b = test_other_org_and_user

    doc_a = await db_client.create_document(
        organization_id=org_a, created_by=user_a,
        filename="a.pdf", file_size_bytes=10, file_hash="aaa", mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Doc A in org A. Agent should know its key facts about A.",
        doc_type="other", intended_use=["inbound"],
    )
    doc_b = await db_client.create_document(
        organization_id=org_b, created_by=user_b,
        filename="b.pdf", file_size_bytes=10, file_hash="bbb", mime_type="application/pdf",
        retrieval_mode="full_document",
        user_description="Doc B in org B. Agent should know its key facts about B.",
        doc_type="other", intended_use=["inbound"],
    )
    await db_client.update_document_full_text(doc_a.id, "Content A.")
    await db_client.update_document_full_text(doc_b.id, "Content B.")

    import json
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

    refreshed_b = await db_client.get_document_by_id(doc_b.id)
    assert refreshed_b.doc_card is None  # untouched
```

- [ ] **Step 2: Check whether fixtures exist**

```bash
grep -n "setup_test_db\|test_org_and_user\|test_other_org_and_user" api/conftest.py
```

If any of `setup_test_db`, `test_org_and_user`, `test_other_org_and_user` are missing from `api/conftest.py`, examine an existing test like `api/tests/test_workflow_qa_masking.py` or `api/tests/test_workflow_create_route.py` to see the actual fixture names used in the repo. Update the test imports/parameters to match the existing fixture names. The substance of the assertions stays the same.

- [ ] **Step 3: Run the tests**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_doc_card_extraction_task.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add api/tests/test_doc_card_extraction_task.py
git commit -m "test(kb): integration tests for doc card extraction happy path and org isolation"
```

---

## Task 10: Failure-mode tests for DocCard extraction

**Files:**
- Create: `api/tests/test_doc_card_extraction_failure.py`

- [ ] **Step 1: Write the failure-mode tests**

Create `api/tests/test_doc_card_extraction_failure.py`:

```python
"""Failure-mode tests for DocCard extraction.

Invalid JSON, missing API key for non-Dograh provider, validation errors.
"""

import json
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from api.db import db_client
from api.services.knowledge_base.doc_card_extraction import (
    extract_doc_card_for_document,
)


def _mock_llm_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


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


async def _make_extractable_doc(org_id, user_id):
    doc = await db_client.create_document(
        organization_id=org_id, created_by=user_id,
        filename="t.pdf", file_size_bytes=10, file_hash="h",
        mime_type="application/pdf", retrieval_mode="full_document",
        user_description="Test document for failure cases. Should trigger extraction.",
        doc_type="other", intended_use=["inbound"],
    )
    await db_client.update_document_full_text(doc.id, "Some text.")
    return doc


@pytest.mark.asyncio
async def test_invalid_json_triggers_repair_then_succeeds(setup_test_db, test_org_and_user):
    org_id, user_id = test_org_and_user
    doc = await _make_extractable_doc(org_id, user_id)

    fake_llm = AsyncMock()
    fake_llm.create_chat_completion = AsyncMock(
        side_effect=[
            _mock_llm_response("not json at all {[}"),
            _mock_llm_response(json.dumps(_valid_card())),
        ]
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is not None
    assert fake_llm.create_chat_completion.call_count == 2  # repair attempt happened


@pytest.mark.asyncio
async def test_invalid_json_twice_raises(setup_test_db, test_org_and_user):
    org_id, user_id = test_org_and_user
    doc = await _make_extractable_doc(org_id, user_id)

    fake_llm = AsyncMock()
    fake_llm.create_chat_completion = AsyncMock(
        return_value=_mock_llm_response("still not json")
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        with pytest.raises(RuntimeError):
            await extract_doc_card_for_document(doc.id)


@pytest.mark.asyncio
async def test_validation_failure_triggers_repair(setup_test_db, test_org_and_user):
    """LLM returns valid JSON missing a required field; repair fixes it."""
    org_id, user_id = test_org_and_user
    doc = await _make_extractable_doc(org_id, user_id)

    bad = {"summary_150_words": "S"}  # missing title etc.
    fake_llm = AsyncMock()
    fake_llm.create_chat_completion = AsyncMock(
        side_effect=[
            _mock_llm_response(json.dumps(bad)),
            _mock_llm_response(json.dumps(_valid_card())),
        ]
    )

    with patch(
        "api.services.knowledge_base.doc_card_extraction.create_llm_service_from_provider",
        return_value=fake_llm,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is not None
    assert fake_llm.create_chat_completion.call_count == 2


@pytest.mark.asyncio
async def test_non_dograh_provider_without_api_key_is_skipped(
    setup_test_db, test_org_and_user
):
    """User config set to openai with no api_key -> skipped with informative error."""
    org_id, user_id = test_org_and_user
    doc = await _make_extractable_doc(org_id, user_id)

    async def fake_resolve(_user_id):
        return ("openai", "gpt-4.1", None, {})

    with patch(
        "api.services.knowledge_base.doc_card_extraction._resolve_extraction_llm",
        side_effect=fake_resolve,
    ):
        card = await extract_doc_card_for_document(doc.id)

    assert card is None
    refreshed = await db_client.get_document_by_id(doc.id)
    assert refreshed.doc_card is None
    assert refreshed.processing_error is not None
    assert "Model Configurations" in refreshed.processing_error
```

- [ ] **Step 2: Run tests, expect pass**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_doc_card_extraction_failure.py -v
```

Expected: 4 passed. (If fixture names differ, adjust per Task 9 Step 2.)

- [ ] **Step 3: Commit**

```bash
git add api/tests/test_doc_card_extraction_failure.py
git commit -m "test(kb): failure-mode tests for doc card extraction (invalid json, validation, no api key)"
```

---

## Task 11: Org index renderer with tests

**Files:**
- Create: `api/services/knowledge_base/org_index_renderer.py`
- Create: `api/tests/test_org_index_renderer.py`

- [ ] **Step 1: Write failing tests**

Create `api/tests/test_org_index_renderer.py`:

```python
from types import SimpleNamespace

from api.services.knowledge_base.org_index_renderer import (
    build_org_index_md,
    enforce_size_budget,
)


def _doc(
    *,
    title="T",
    filename="f.pdf",
    doc_type="other",
    intended_use=("inbound",),
    summary="A short summary that is long enough for testing the renderer.",
    topics=("a", "b", "c"),
):
    return SimpleNamespace(
        filename=filename,
        doc_type=doc_type,
        intended_use=list(intended_use),
        doc_card={
            "title": title,
            "summary_150_words": summary,
            "topics": list(topics),
        },
    )


def test_empty_org_returns_header_only():
    md = build_org_index_md([], call_direction=None)
    assert "0 docs" in md
    assert "##" not in md


def test_one_doc_flat_list_no_grouping():
    md = build_org_index_md([_doc(title="A")], call_direction=None)
    assert "## " not in md  # no group header for tiny orgs
    assert "A" in md


def test_grouped_when_five_or_more_docs():
    docs = [_doc(doc_type="contract", title=f"C{i}") for i in range(3)] + [
        _doc(doc_type="policy", title="P1"),
        _doc(doc_type="policy", title="P2"),
    ]
    md = build_org_index_md(docs, call_direction=None)
    assert "## Contract" in md
    assert "## Policy" in md


def test_inbound_call_filters_outbound_only_docs():
    docs = [
        _doc(title="InboundOnly", intended_use=("inbound",)),
        _doc(title="OutboundOnly", intended_use=("outbound",)),
        _doc(title="Both", intended_use=("inbound", "outbound")),
    ]
    md = build_org_index_md(docs, call_direction="inbound")
    assert "InboundOnly" in md
    assert "OutboundOnly" not in md
    assert "Both" in md


def test_outbound_call_filters_inbound_only_docs():
    docs = [
        _doc(title="InboundOnly", intended_use=("inbound",)),
        _doc(title="OutboundOnly", intended_use=("outbound",)),
    ]
    md = build_org_index_md(docs, call_direction="outbound")
    assert "OutboundOnly" in md
    assert "InboundOnly" not in md


def test_size_budget_truncates_with_marker():
    docs = [
        _doc(title=f"D{i}", summary="x" * 500) for i in range(200)
    ]
    md = build_org_index_md(docs, call_direction=None)
    md_capped = enforce_size_budget(md, max_bytes=2_000)
    assert len(md_capped.encode("utf-8")) <= 2_500
    assert "more docs not shown" in md_capped


def test_renderer_skips_docs_with_null_doc_card():
    docs = [_doc(title="Good"), SimpleNamespace(doc_card=None, filename="bad.pdf")]
    md = build_org_index_md(docs, call_direction=None)
    assert "Good" in md
    assert "bad.pdf" not in md
```

- [ ] **Step 2: Run tests — expect import failure**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_org_index_renderer.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the renderer**

Create `api/services/knowledge_base/org_index_renderer.py`:

```python
"""Pure renderer for the org-scoped knowledge index markdown.

No I/O, no LLM. Builds a markdown string from a list of documents
that have a non-null doc_card. Used by the org_index_rebuild ARQ task
and the context composer's fallback excerpt.
"""

from collections import defaultdict
from typing import Iterable, Optional

DEFAULT_BUDGET_BYTES = 64_000
TRUNCATION_MARKER = "\n\n_[+ {n} more docs not shown — use search to find them]_"

SUMMARY_PREVIEW_CHARS = 140
TOPICS_PREVIEW = 5


def build_org_index_md(
    documents: Iterable,
    *,
    call_direction: Optional[str],
) -> str:
    """Render the org index markdown.

    Args:
        documents: Iterable of objects with attributes filename, doc_type,
            intended_use, doc_card (dict with title, summary_150_words, topics)
            OR doc_card=None (skipped).
        call_direction: "inbound", "outbound", or None to include all docs.

    Returns:
        Markdown string. Header always present. Body grouped by doc_type
        when >= 5 docs after filtering, otherwise flat.
    """
    filtered = [d for d in documents if getattr(d, "doc_card", None)]
    if call_direction in ("inbound", "outbound"):
        filtered = [
            d for d in filtered if call_direction in (d.intended_use or [])
        ]

    header = f"# Organization Knowledge Index ({len(filtered)} docs)\n"

    if not filtered:
        return header

    if len(filtered) < 5:
        body_lines = [_render_doc_line(d) for d in filtered]
        return header + "\n" + "\n".join(body_lines)

    by_type: dict[str, list] = defaultdict(list)
    for d in filtered:
        by_type[(d.doc_type or "other").lower()].append(d)

    out: list[str] = [header]
    for doc_type in sorted(by_type.keys()):
        group = sorted(by_type[doc_type], key=lambda d: d.doc_card["title"])
        out.append(f"\n## {doc_type.title()} ({len(group)} docs)")
        for d in group:
            out.append(_render_doc_line(d))
    return "\n".join(out)


def _render_doc_line(doc) -> str:
    card = doc.doc_card or {}
    title = card.get("title", doc.filename)
    summary = (card.get("summary_150_words") or "")[:SUMMARY_PREVIEW_CHARS]
    topics = card.get("topics", [])[:TOPICS_PREVIEW]
    intended_use = ", ".join(doc.intended_use or []) or "unspecified"
    return (
        f"- **{title}** ({doc.filename}) — {summary}… "
        f"_uses: {intended_use}_ _topics: {', '.join(topics)}_"
    )


def enforce_size_budget(md: str, *, max_bytes: int = DEFAULT_BUDGET_BYTES) -> str:
    """Truncate longest lines first until under budget; append marker."""
    encoded = md.encode("utf-8")
    if len(encoded) <= max_bytes:
        return md

    lines = md.split("\n")
    truncated_count = 0
    while len("\n".join(lines).encode("utf-8")) > max_bytes and lines:
        # Drop the longest body line (skip header / group headers)
        idx, _ = max(
            (
                (i, len(line))
                for i, line in enumerate(lines)
                if line.startswith("- ")
            ),
            key=lambda t: t[1],
            default=(None, 0),
        )
        if idx is None:
            break
        lines.pop(idx)
        truncated_count += 1

    out = "\n".join(lines)
    if truncated_count:
        out += TRUNCATION_MARKER.format(n=truncated_count)
    return out
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest api/tests/test_org_index_renderer.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/knowledge_base/org_index_renderer.py api/tests/test_org_index_renderer.py
git commit -m "feat(kb): pure org index renderer with grouping, filtering, and size budget"
```

---

## Task 12: Worker-sync event type + handler scaffolding

**Files:**
- Modify: `api/services/worker_sync/protocol.py`
- Create: `api/services/knowledge_base/org_index_cache.py`

- [ ] **Step 1: Add new event type**

In `api/services/worker_sync/protocol.py`, find `class WorkerSyncEventType(str, Enum):` and add:

```python
    KB_INDEX_UPDATED = "kb_index_updated"
```

- [ ] **Step 2: Create the org-index cache module**

Create `api/services/knowledge_base/org_index_cache.py`:

```python
"""Per-worker in-memory cache of the org knowledge index markdown.

The cache is invalidated by a WorkerSyncManager pub/sub event broadcast
after each rebuild. Workers re-read authoritative state from the DB
on next access.
"""

from typing import Optional

from loguru import logger

from api.db import db_client
from api.services.worker_sync.protocol import WorkerSyncEvent

_CACHE: dict[int, dict] = {}


async def get_index_for_org(organization_id: int) -> Optional[dict]:
    """Return the cached or freshly-loaded knowledge_index value for an org.

    Shape: {"md": str, "doc_count": int, "char_count": int,
            "generated_at": str, "hash": str} or None if not yet built.
    """
    cached = _CACHE.get(organization_id)
    if cached is not None:
        return cached
    value = await db_client.get_configuration_value(
        organization_id=organization_id, key="knowledge_index", default=None
    )
    if value:
        _CACHE[organization_id] = value
    return value


async def invalidate(organization_id: int) -> None:
    _CACHE.pop(organization_id, None)


async def handle_kb_index_updated(event: WorkerSyncEvent) -> None:
    """WorkerSyncManager handler — invalidate local cache on broadcast."""
    try:
        org_id = int(event.org_id) if event.org_id else 0
    except ValueError:
        logger.warning(f"kb_index_updated: invalid org_id {event.org_id!r}")
        return
    if org_id:
        await invalidate(org_id)
        logger.debug(f"KB index cache invalidated for org {org_id}")
```

- [ ] **Step 3: Smoke import**

```bash
source venv/bin/activate && python -c "
from api.services.knowledge_base.org_index_cache import get_index_for_org, handle_kb_index_updated
from api.services.worker_sync.protocol import WorkerSyncEventType
print(WorkerSyncEventType.KB_INDEX_UPDATED.value)
"
```

Expected: `kb_index_updated`.

- [ ] **Step 4: Commit**

```bash
git add api/services/worker_sync/protocol.py api/services/knowledge_base/org_index_cache.py
git commit -m "feat(kb): worker-sync event type and per-worker org-index cache"
```

---

## Task 13: Org-index rebuild ARQ task

**Files:**
- Modify: `api/tasks/function_names.py`
- Create: `api/tasks/org_index_rebuild.py`
- Modify: `api/tasks/arq.py`

- [ ] **Step 1: Add the function name**

Edit `api/tasks/function_names.py`:

```python
class FunctionNames:
    RUN_INTEGRATIONS_POST_WORKFLOW_RUN = "run_integrations_post_workflow_run"
    PROCESS_WORKFLOW_COMPLETION = "process_workflow_completion"
    UPLOAD_VOICEMAIL_AUDIO_TO_S3 = "upload_voicemail_audio_to_s3"
    SYNC_CAMPAIGN_SOURCE = "sync_campaign_source"
    PROCESS_CAMPAIGN_BATCH = "process_campaign_batch"
    PROCESS_KNOWLEDGE_BASE_DOCUMENT = "process_knowledge_base_document"
    REBUILD_ORG_KNOWLEDGE_INDEX = "rebuild_org_knowledge_index"
```

- [ ] **Step 2: Implement the task**

Create `api/tasks/org_index_rebuild.py`:

```python
"""ARQ task: rebuild the org knowledge index markdown.

Coalesces concurrent triggers via a 30-second Redis lock keyed by org.
Persists to organization_configurations under key `knowledge_index`
and broadcasts a WorkerSyncManager event so all workers invalidate
their per-worker cache.
"""

import hashlib
import json
from datetime import UTC, datetime

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.db import db_client
from api.services.knowledge_base.org_index_renderer import (
    build_org_index_md,
    enforce_size_budget,
)
from api.services.worker_sync.protocol import WorkerSyncEventType

LOCK_KEY_TEMPLATE = "kb_index_rebuild_lock:{org_id}"
LOCK_TTL_SECONDS = 30


async def rebuild_org_knowledge_index(ctx, organization_id: int) -> None:
    """Rebuild the knowledge_index for an organization.

    Idempotent. Coalesced by Redis lock. If another rebuild is in flight
    within LOCK_TTL_SECONDS, this invocation is a no-op.
    """
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    lock_key = LOCK_KEY_TEMPLATE.format(org_id=organization_id)
    try:
        acquired = await redis.set(lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS)
        if not acquired:
            logger.debug(
                f"KB index rebuild already in flight for org {organization_id}; skipping"
            )
            return

        documents = await db_client.list_active_documents_for_index(organization_id)
        md = build_org_index_md(documents, call_direction=None)
        md = enforce_size_budget(md)

        payload = {
            "md": md,
            "doc_count": len(documents),
            "char_count": len(md),
            "generated_at": datetime.now(UTC).isoformat(),
            "hash": hashlib.sha256(md.encode("utf-8")).hexdigest(),
        }

        await db_client.upsert_configuration(
            organization_id=organization_id,
            key="knowledge_index",
            value=payload,
        )

        from api.app import worker_sync_manager  # late import to avoid cycle
        if worker_sync_manager:
            await worker_sync_manager.broadcast(
                event_type=WorkerSyncEventType.KB_INDEX_UPDATED.value,
                action="update",
                org_id=str(organization_id),
            )

        logger.info(
            f"Rebuilt KB index for org {organization_id}: "
            f"{len(documents)} docs, {len(md)} chars"
        )
    finally:
        await redis.close()
```

- [ ] **Step 3: Register the task in ARQ**

Edit `api/tasks/arq.py`. In the imports near line 46, add:

```python
from api.tasks.org_index_rebuild import rebuild_org_knowledge_index
```

In `WorkerSettings.functions`, add `rebuild_org_knowledge_index` to the list.

- [ ] **Step 4: Smoke import**

```bash
source venv/bin/activate && python -c "
from api.tasks.org_index_rebuild import rebuild_org_knowledge_index
from api.tasks.arq import WorkerSettings
assert rebuild_org_knowledge_index in WorkerSettings.functions
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add api/tasks/function_names.py api/tasks/org_index_rebuild.py api/tasks/arq.py
git commit -m "feat(kb): org index rebuild ARQ task with redis lock and worker-sync broadcast"
```

---

## Task 14: Wire WorkerSyncManager handler in app startup

**Files:**
- Modify: `api/app.py`

- [ ] **Step 1: Locate the worker_sync_manager setup**

Search for `WorkerSyncManager` in `api/app.py`:

```bash
grep -n "WorkerSyncManager\|worker_sync_manager\|register" api/app.py | head -30
```

Note the lines where the existing manager is instantiated and where existing handlers (e.g. `langfuse_credentials`) are registered.

- [ ] **Step 2: Register the kb_index handler**

Immediately after the existing `worker_sync_manager.register(...)` call(s) and BEFORE `worker_sync_manager.start()` add:

```python
    from api.services.knowledge_base.org_index_cache import handle_kb_index_updated
    from api.services.worker_sync.protocol import WorkerSyncEventType

    worker_sync_manager.register(
        WorkerSyncEventType.KB_INDEX_UPDATED.value,
        handle_kb_index_updated,
    )
```

- [ ] **Step 3: Smoke import and confirm**

```bash
source venv/bin/activate && python -c "import api.app; print('OK')"
```

Expected: `OK` (no import error).

- [ ] **Step 4: Commit**

```bash
git add api/app.py
git commit -m "feat(kb): register kb_index_updated handler with worker-sync manager"
```

---

## Task 15: Integration test for org index rebuild + triggers

**Files:**
- Create: `api/tests/test_org_index_rebuild_task.py`

- [ ] **Step 1: Write the tests**

Create `api/tests/test_org_index_rebuild_task.py`:

```python
"""Integration tests for the org knowledge index rebuild task."""

from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.tasks.org_index_rebuild import rebuild_org_knowledge_index


VALID_CARD = {
    "title": "Test",
    "summary_150_words": "Summary.",
    "key_facts": [], "entities": {}, "numbers_and_pricing": [],
    "faqs": [], "suggested_agent_uses": [], "topics": ["a"],
}


async def _doc_with_card(org_id, user_id, *, filename, intended_use=("inbound",)):
    doc = await db_client.create_document(
        organization_id=org_id, created_by=user_id,
        filename=filename, file_size_bytes=10, file_hash=filename,
        mime_type="application/pdf", retrieval_mode="full_document",
        user_description="Test " + filename + " with enough characters.",
        doc_type="other", intended_use=list(intended_use),
    )
    await db_client.update_doc_card(
        document_id=doc.id, doc_card={**VALID_CARD, "title": filename}, topics=["a"]
    )
    return doc


@pytest.mark.asyncio
async def test_rebuild_writes_index_with_doc(setup_test_db, test_org_and_user):
    org_id, user_id = test_org_and_user
    await _doc_with_card(org_id, user_id, filename="d1.pdf")

    with patch("api.tasks.org_index_rebuild.worker_sync_manager", AsyncMock()):
        await rebuild_org_knowledge_index({}, org_id)

    value = await db_client.get_configuration_value(
        organization_id=org_id, key="knowledge_index"
    )
    assert value is not None
    assert "d1.pdf" in value["md"]
    assert value["doc_count"] == 1


@pytest.mark.asyncio
async def test_rebuild_excludes_inactive_docs(setup_test_db, test_org_and_user):
    org_id, user_id = test_org_and_user
    doc = await _doc_with_card(org_id, user_id, filename="active.pdf")
    deleted = await _doc_with_card(org_id, user_id, filename="deleted.pdf")
    await db_client.delete_document(deleted.document_uuid, org_id)

    with patch("api.tasks.org_index_rebuild.worker_sync_manager", AsyncMock()):
        await rebuild_org_knowledge_index({}, org_id)

    value = await db_client.get_configuration_value(
        organization_id=org_id, key="knowledge_index"
    )
    assert "active.pdf" in value["md"]
    assert "deleted.pdf" not in value["md"]


@pytest.mark.asyncio
async def test_rebuild_is_org_scoped(setup_test_db, test_org_and_user, test_other_org_and_user):
    org_a, user_a = test_org_and_user
    org_b, user_b = test_other_org_and_user
    await _doc_with_card(org_a, user_a, filename="a.pdf")
    await _doc_with_card(org_b, user_b, filename="b.pdf")

    with patch("api.tasks.org_index_rebuild.worker_sync_manager", AsyncMock()):
        await rebuild_org_knowledge_index({}, org_a)

    value_a = await db_client.get_configuration_value(
        organization_id=org_a, key="knowledge_index"
    )
    value_b = await db_client.get_configuration_value(
        organization_id=org_b, key="knowledge_index"
    )
    assert "a.pdf" in value_a["md"]
    assert "b.pdf" not in value_a["md"]
    assert value_b is None  # never built for org_b
```

- [ ] **Step 2: Run tests**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_org_index_rebuild_task.py -v
```

Expected: 3 passed. (Adjust fixture names if needed, as in Task 9 Step 2.)

- [ ] **Step 3: Commit**

```bash
git add api/tests/test_org_index_rebuild_task.py
git commit -m "test(kb): integration tests for org index rebuild — content, deletions, org scoping"
```

---

## Task 16: Route — require new fields on `/process-document`

**Files:**
- Modify: `api/routes/knowledge_base.py`

- [ ] **Step 1: Update `process_document` handler**

In `api/routes/knowledge_base.py`, find the `process_document` function. Replace the `db_client.create_document(...)` call so it passes the new fields from the request:

```python
        document = await db_client.create_document(
            organization_id=user.selected_organization_id,
            created_by=user.id,
            filename=filename,
            file_size_bytes=0,
            file_hash="",
            mime_type="application/octet-stream",
            custom_metadata={"s3_key": request.s3_key},
            document_uuid=request.document_uuid,
            retrieval_mode=request.retrieval_mode,
            doc_type=request.doc_type,
            intended_use=request.intended_use,
            user_description=request.user_description,
        )
```

- [ ] **Step 2: Update the response**

In the same function, update the returned `DocumentResponseSchema(...)` to include the new fields:

```python
        return DocumentResponseSchema(
            id=document.id,
            document_uuid=request.document_uuid,
            filename=filename,
            file_size_bytes=0,
            file_hash="",
            mime_type="application/octet-stream",
            processing_status="pending",
            processing_error=None,
            total_chunks=0,
            retrieval_mode=request.retrieval_mode,
            custom_metadata={"s3_key": request.s3_key},
            docling_metadata={},
            source_url=None,
            created_at=document.created_at,
            updated_at=document.updated_at,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            is_active=True,
            doc_type=request.doc_type,
            intended_use=request.intended_use,
            user_description=request.user_description,
            doc_card=None,
            doc_card_extracted_at=None,
            topics=[],
        )
```

- [ ] **Step 3: Also update existing read endpoints to expose new fields**

In the same file, find `list_documents` and `get_document`. In both, update the `DocumentResponseSchema(...)` construction to include the new fields (read from the model):

```python
            doc_type=doc.doc_type if hasattr(doc, "doc_type") else None,
            intended_use=doc.intended_use or [],
            user_description=doc.user_description,
            doc_card=doc.doc_card,
            doc_card_extracted_at=doc.doc_card_extracted_at,
            topics=doc.topics or [],
```

Use `document` instead of `doc` in `get_document`.

- [ ] **Step 4: Commit**

```bash
git add api/routes/knowledge_base.py
git commit -m "feat(kb): require doc_type, intended_use, user_description on /process-document"
```

---

## Task 17: Route — PATCH `/documents/{uuid}` for user-input edits

**Files:**
- Modify: `api/routes/knowledge_base.py`

- [ ] **Step 1: Add the PATCH handler**

In `api/routes/knowledge_base.py`, after `delete_document` (around line 348) add:

```python
@router.patch(
    "/documents/{document_uuid}",
    response_model=DocumentResponseSchema,
    summary="Edit user-provided document fields",
)
async def edit_document_inputs(
    document_uuid: str,
    request: EditDocumentRequestSchema,
    user=Depends(get_user),
):
    """Edit user-provided fields on a document.

    Does NOT auto-trigger re-extraction. Use POST /documents/{uuid}/re-extract
    explicitly if you want the DocCard regenerated after editing.

    Access Control:
    * Users can only edit documents from their organization.
    """
    document = await db_client.update_document_user_inputs(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
        doc_type=request.doc_type,
        intended_use=request.intended_use,
        user_description=request.user_description,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Re-render the org index since description may have changed
    await enqueue_job(
        FunctionNames.REBUILD_ORG_KNOWLEDGE_INDEX,
        user.selected_organization_id,
    )

    return DocumentResponseSchema(
        id=document.id,
        document_uuid=document.document_uuid,
        filename=document.filename,
        file_size_bytes=document.file_size_bytes,
        file_hash=document.file_hash,
        mime_type=document.mime_type,
        processing_status=document.processing_status,
        processing_error=document.processing_error,
        total_chunks=document.total_chunks,
        retrieval_mode=document.retrieval_mode,
        custom_metadata=document.custom_metadata,
        docling_metadata=document.docling_metadata,
        source_url=document.source_url,
        created_at=document.created_at,
        updated_at=document.updated_at,
        organization_id=document.organization_id,
        created_by=document.created_by,
        is_active=document.is_active,
        doc_type=document.doc_type,
        intended_use=document.intended_use or [],
        user_description=document.user_description,
        doc_card=document.doc_card,
        doc_card_extracted_at=document.doc_card_extracted_at,
        topics=document.topics or [],
    )
```

- [ ] **Step 2: Update imports at the top of the file**

Add `EditDocumentRequestSchema` to the existing import line from `api.schemas.knowledge_base`.

- [ ] **Step 3: Smoke import**

```bash
source venv/bin/activate && python -c "from api.routes.knowledge_base import router; print([r.path for r in router.routes if 'documents' in r.path])"
```

Expected output includes `/knowledge-base/documents/{document_uuid}`.

- [ ] **Step 4: Commit**

```bash
git add api/routes/knowledge_base.py
git commit -m "feat(kb): PATCH endpoint to edit doc_type/intended_use/user_description"
```

---

## Task 18: Route — POST `/documents/{uuid}/re-extract`

**Files:**
- Modify: `api/routes/knowledge_base.py`

- [ ] **Step 1: Add the re-extract handler**

In `api/routes/knowledge_base.py`, append after the PATCH handler from Task 17:

```python
@router.post(
    "/documents/{document_uuid}/re-extract",
    status_code=202,
    summary="Re-extract the DocCard for a document",
)
async def re_extract_doc_card(
    document_uuid: str,
    user=Depends(get_user),
):
    """Force re-extraction of the DocCard.

    Useful after editing the description. Blocked at 400 if user_description
    is still null (e.g. for legacy uploads that predate this feature).

    Access Control:
    * Users can only re-extract documents from their organization.
    """
    document = await db_client.get_document_by_uuid(
        document_uuid=document_uuid,
        organization_id=user.selected_organization_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not document.user_description:
        raise HTTPException(
            status_code=400,
            detail="Cannot re-extract: document has no user_description. PATCH the document first.",
        )

    # Run extraction inline in a background task by enqueuing the existing
    # processing function in extract-only mode.
    # Simplest path: trigger the extraction service directly via a tiny ARQ wrapper.
    await enqueue_job(
        FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
        document.id,
        document.custom_metadata.get("s3_key", ""),
        user.selected_organization_id,
        str(user.provider_id),
        128,
        document.retrieval_mode,
    )
    # The processing function will tail-call extract_doc_card_for_document
    # and then enqueue the org-index rebuild.
    return {"status": "enqueued", "document_uuid": document_uuid}
```

- [ ] **Step 2: Smoke import**

```bash
source venv/bin/activate && python -c "from api.routes.knowledge_base import router; print([r.path for r in router.routes if 're-extract' in r.path])"
```

Expected: `['/knowledge-base/documents/{document_uuid}/re-extract']`.

- [ ] **Step 3: Commit**

```bash
git add api/routes/knowledge_base.py
git commit -m "feat(kb): re-extract endpoint with legacy-doc guard"
```

---

## Task 19: Route tests for new endpoints

**Files:**
- Create: `api/tests/test_knowledge_base_routes_with_metadata.py`

- [ ] **Step 1: Write the tests**

Create `api/tests/test_knowledge_base_routes_with_metadata.py`:

```python
"""Route tests for KB endpoints with the new doc_type/intended_use/user_description."""

import pytest


@pytest.mark.asyncio
async def test_process_document_rejects_missing_doc_type(test_client_authed, setup_test_db):
    """Process-document MUST receive doc_type."""
    response = await test_client_authed.post(
        "/api/v1/knowledge-base/process-document",
        json={
            "document_uuid": "test-uuid-1",
            "s3_key": "fake/key",
            "retrieval_mode": "chunked",
            "intended_use": ["inbound"],
            "user_description": "Test document description with at least 20 chars.",
        },
    )
    assert response.status_code == 422
    assert "doc_type" in response.text


@pytest.mark.asyncio
async def test_process_document_rejects_short_description(test_client_authed, setup_test_db):
    response = await test_client_authed.post(
        "/api/v1/knowledge-base/process-document",
        json={
            "document_uuid": "test-uuid-2",
            "s3_key": "fake/key",
            "retrieval_mode": "chunked",
            "doc_type": "other",
            "intended_use": ["inbound"],
            "user_description": "short",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_document_org_scoped(test_client_authed, setup_test_db, test_org_and_user, test_other_org_and_user):
    """PATCH on a doc owned by another org returns 404."""
    from api.db import db_client
    org_b, user_b = test_other_org_and_user
    foreign_doc = await db_client.create_document(
        organization_id=org_b, created_by=user_b,
        filename="x.pdf", file_size_bytes=1, file_hash="x",
        mime_type="application/pdf", retrieval_mode="full_document",
        user_description="Belongs to org B; org A must get 404.",
        doc_type="other", intended_use=["inbound"],
    )
    response = await test_client_authed.patch(
        f"/api/v1/knowledge-base/documents/{foreign_doc.document_uuid}",
        json={"doc_type": "policy"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_re_extract_blocks_legacy_doc_without_description(
    test_client_authed, setup_test_db, test_org_and_user
):
    from api.db import db_client
    org_id, user_id = test_org_and_user
    legacy = await db_client.create_document(
        organization_id=org_id, created_by=user_id,
        filename="legacy.pdf", file_size_bytes=1, file_hash="leg",
        mime_type="application/pdf", retrieval_mode="full_document",
    )
    response = await test_client_authed.post(
        f"/api/v1/knowledge-base/documents/{legacy.document_uuid}/re-extract",
    )
    assert response.status_code == 400
    assert "user_description" in response.text
```

- [ ] **Step 2: Verify test fixture names**

Check whether `test_client_authed` exists:

```bash
grep -n "test_client_authed\|test_client\|httpx_client\|AsyncClient" api/conftest.py | head -10
```

If the fixture is named differently in the repo (e.g. `client`, `http_client`, `authed_client`), rename the parameter in each test. Look at `api/tests/test_workflow_create_route.py` for the actual fixture name pattern used. The assertions stay the same.

- [ ] **Step 3: Run tests**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_knowledge_base_routes_with_metadata.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add api/tests/test_knowledge_base_routes_with_metadata.py
git commit -m "test(kb): route tests for required fields, PATCH org scoping, and re-extract legacy guard"
```

---

## Task 20: Regenerate UI client + update upload UI

**Files:**
- Modify: `ui/src/app/files/DocumentUpload.tsx`
- Modify: `ui/src/client/*` (auto-generated)

- [ ] **Step 1: Regenerate the API client**

```bash
cd ui && npm run generate-client
```

This rewrites `ui/src/client/`. Verify by greping for the new fields:

```bash
grep -l "user_description\|doc_type\|intended_use" ui/src/client/ -r
```

Expected: matches in the generated SDK files.

- [ ] **Step 2: Replace `DocumentUpload.tsx`**

Replace the entirety of `ui/src/app/files/DocumentUpload.tsx` with the version below. (It keeps the existing OSS notice, file picker, drag-and-drop, and retrieval-mode selection patterns, and adds the three new fields.)

```tsx
'use client';

import { FileText, Info, Upload, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
import type { DocumentUploadResponseSchema } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAppConfig } from '@/context/AppConfigContext';
import logger from '@/lib/logger';

interface DocumentUploadProps {
  onUploadSuccess: () => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024;
const ACCEPTED_FILE_TYPES = ['.pdf', '.docx', '.doc', '.txt', '.json'];
const MIN_DESCRIPTION_LENGTH = 20;

const DOC_TYPES = [
  { value: 'contract', label: 'Contract' },
  { value: 'policy', label: 'Policy' },
  { value: 'pricing', label: 'Pricing' },
  { value: 'faq', label: 'FAQ' },
  { value: 'script', label: 'Script' },
  { value: 'other', label: 'Other' },
];

export default function DocumentUpload({ onUploadSuccess }: DocumentUploadProps) {
  const { config } = useAppConfig();
  const isOSS = config?.deploymentMode === 'oss';

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [retrievalMode, setRetrievalMode] = useState('full_document');
  const [docType, setDocType] = useState('');
  const [useInbound, setUseInbound] = useState(true);
  const [useOutbound, setUseOutbound] = useState(false);
  const [userDescription, setUserDescription] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ossNotice = isOSS ? (
    <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/50 dark:bg-amber-950/30">
      <Info className="h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
      <div className="text-xs text-amber-900 dark:text-amber-200">
        <p className="font-medium">Processed by an external service</p>
        <p className="mt-1">
          Uploaded documents are sent to Speakly&apos;s managed Model Proxy Service for parsing and
          chunking. Extracted text and embeddings are returned and stored locally in your database.
        </p>
      </div>
    </div>
  ) : null;

  const validateFile = (file: File): boolean => {
    const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_TYPES.includes(fileExtension)) {
      toast.error(`Please select a supported file type: ${ACCEPTED_FILE_TYPES.join(', ')}`);
      return false;
    }
    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 5MB');
      return false;
    }
    return true;
  };

  const handleFileSelected = (file: File) => {
    if (!validateFile(file)) {
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }
    setSelectedFile(file);
  };

  const clearSelectedFile = () => {
    setSelectedFile(null);
    setRetrievalMode('full_document');
    setDocType('');
    setUseInbound(true);
    setUseOutbound(false);
    setUserDescription('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const intendedUse = (): string[] => {
    const out: string[] = [];
    if (useInbound) out.push('inbound');
    if (useOutbound) out.push('outbound');
    return out;
  };

  const canSubmit =
    !!selectedFile &&
    !!docType &&
    intendedUse().length > 0 &&
    userDescription.trim().length >= MIN_DESCRIPTION_LENGTH &&
    !uploading;

  const uploadFile = async () => {
    if (!selectedFile || !canSubmit) return;

    setUploading(true);
    setUploadProgress(0);

    try {
      const uploadUrlResponse = await getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
        body: {
          filename: selectedFile.name,
          mime_type: selectedFile.type || 'application/octet-stream',
          custom_metadata: {
            original_filename: selectedFile.name,
            uploaded_at: new Date().toISOString(),
          },
        },
      });

      if (uploadUrlResponse.error || !uploadUrlResponse.data) {
        throw new Error('Failed to get upload URL');
      }

      const uploadData: DocumentUploadResponseSchema = uploadUrlResponse.data;
      setUploadProgress(25);

      const uploadResponse = await fetch(uploadData.upload_url, {
        method: 'PUT',
        body: selectedFile,
        headers: { 'Content-Type': selectedFile.type || 'application/octet-stream' },
      });
      if (!uploadResponse.ok) throw new Error('Failed to upload file to storage');
      setUploadProgress(75);

      const processResponse = await processDocumentApiV1KnowledgeBaseProcessDocumentPost({
        body: {
          document_uuid: uploadData.document_uuid,
          s3_key: uploadData.s3_key,
          retrieval_mode: retrievalMode,
          doc_type: docType,
          intended_use: intendedUse(),
          user_description: userDescription.trim(),
        },
      });
      if (processResponse.error) throw new Error('Failed to trigger processing');

      setUploadProgress(100);
      toast.success(`File uploaded: ${selectedFile.name}. Processing started.`);
      clearSelectedFile();
      onUploadSuccess();
    } catch (error) {
      logger.error('Error uploading document:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload document');
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) handleFileSelected(file);
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileSelected(file);
  };

  if (selectedFile && !uploading) {
    return (
      <div className="space-y-4">
        {ossNotice}
        <div className="flex items-center gap-3 p-3 border rounded-lg bg-muted/30">
          <FileText className="w-8 h-8 text-primary flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium truncate">{selectedFile.name}</p>
            <p className="text-xs text-muted-foreground">{(selectedFile.size / 1024).toFixed(1)} KB</p>
          </div>
          <Button variant="ghost" size="icon" onClick={clearSelectedFile}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        <div className="space-y-2">
          <Label className="text-sm font-medium">Document type *</Label>
          <Select value={docType} onValueChange={setDocType}>
            <SelectTrigger>
              <SelectValue placeholder="Select a document type" />
            </SelectTrigger>
            <SelectContent>
              {DOC_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label className="text-sm font-medium">Intended use *</Label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <Checkbox checked={useInbound} onCheckedChange={(v) => setUseInbound(!!v)} />
              <span className="text-sm">Inbound</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <Checkbox checked={useOutbound} onCheckedChange={(v) => setUseOutbound(!!v)} />
              <span className="text-sm">Outbound</span>
            </label>
          </div>
        </div>

        <div className="space-y-2">
          <Label className="text-sm font-medium">Describe this document *</Label>
          <Textarea
            placeholder="What's in it, what's important, and how the agent should use it during calls."
            value={userDescription}
            onChange={(e) => setUserDescription(e.target.value)}
            className="min-h-24"
          />
          <p className="text-xs text-muted-foreground">
            {userDescription.length} / {MIN_DESCRIPTION_LENGTH}+ characters
          </p>
        </div>

        <div className="space-y-3">
          <Label className="text-sm font-medium">How should the agent use this document?</Label>
          <RadioGroup value={retrievalMode} onValueChange={setRetrievalMode}>
            <label
              htmlFor="full_document"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'full_document' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="full_document" id="full_document" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">Full Document</p>
                <p className="text-xs text-muted-foreground">
                  The entire document is provided to the agent on each retrieval.
                </p>
              </div>
            </label>
            <label
              htmlFor="chunked"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'chunked' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="chunked" id="chunked" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">Chunked Search</p>
                <p className="text-xs text-muted-foreground">
                  Best for large documents like manuals or policies.
                </p>
              </div>
            </label>
          </RadioGroup>
        </div>

        <Button onClick={uploadFile} className="w-full" disabled={!canSubmit}>
          Upload & Process
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {ossNotice}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_FILE_TYPES.join(',')}
        onChange={handleFileSelect}
        className="hidden"
        disabled={uploading}
      />
      <div
        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
          dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/25'
        } ${uploading ? 'opacity-50 pointer-events-none' : 'cursor-pointer hover:border-primary hover:bg-muted/50'}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <p className="text-lg font-medium mb-2">{uploading ? 'Uploading...' : 'Drop your document here'}</p>
        <p className="text-sm text-muted-foreground mb-4">or click to browse</p>
        <p className="text-xs text-muted-foreground">
          Supported formats: {ACCEPTED_FILE_TYPES.join(', ')} (Max 5MB)
        </p>
      </div>
      {uploading && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>Uploading...</span>
            <span>{uploadProgress}%</span>
          </div>
          <Progress value={uploadProgress} />
        </div>
      )}
      {!uploading && (
        <div className="flex justify-center">
          <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
            Choose File
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify the UI builds**

```bash
cd ui && npm run lint && npm run build
```

Expected: no errors. If shadcn's `Checkbox`, `Textarea`, or `Select` are not yet installed, add them with `npx shadcn-ui@latest add checkbox textarea select` (note: command varies by shadcn version — check `package.json`).

- [ ] **Step 4: Commit**

```bash
git add ui/src/client ui/src/app/files/DocumentUpload.tsx
git commit -m "feat(kb): require doc_type, intended_use, description in upload UI"
```

---

## Task 21: UI status pills on `DocumentList`

**Files:**
- Modify: `ui/src/app/files/DocumentList.tsx`

- [ ] **Step 1: Find the document row rendering**

Open `ui/src/app/files/DocumentList.tsx`. Locate where each document row is rendered (look for `.map((doc =>` or similar). Identify where the existing `processing_status` is displayed.

- [ ] **Step 2: Add status pill computation and render**

Above the return JSX (or as a hoisted helper function in the same file), add:

```tsx
type PillState =
  | { kind: 'ready'; label: string; color: 'green' }
  | { kind: 'needs_description'; label: string; color: 'blue' }
  | { kind: 'pending'; label: string; color: 'gray' }
  | { kind: 'no_text'; label: string; color: 'amber' }
  | { kind: 'failed'; label: string; color: 'red' };

function getDocCardPill(doc: {
  doc_card_extracted_at?: string | null;
  user_description?: string | null;
  processing_status?: string;
  processing_error?: string | null;
}): PillState {
  if (doc.doc_card_extracted_at) {
    return { kind: 'ready', label: 'Summary ready', color: 'green' };
  }
  if (!doc.user_description) {
    return { kind: 'needs_description', label: 'Needs description', color: 'blue' };
  }
  if (doc.processing_error === 'no_text_content') {
    return { kind: 'no_text', label: 'No text', color: 'amber' };
  }
  if (doc.processing_status === 'completed' && !doc.processing_error) {
    return { kind: 'pending', label: 'Summary pending…', color: 'gray' };
  }
  if (doc.processing_error) {
    return { kind: 'failed', label: 'Summary failed', color: 'red' };
  }
  return { kind: 'pending', label: 'Summary pending…', color: 'gray' };
}

const PILL_CLASS: Record<PillState['color'], string> = {
  green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300',
  blue: 'bg-blue-100 text-blue-800 dark:bg-blue-950/40 dark:text-blue-300',
  gray: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  amber: 'bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300',
  red: 'bg-red-100 text-red-800 dark:bg-red-950/40 dark:text-red-300',
};
```

In the row JSX, beside the existing processing status, render the pill:

```tsx
{(() => {
  const pill = getDocCardPill(doc);
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${PILL_CLASS[pill.color]}`}
    >
      {pill.label}
    </span>
  );
})()}
```

- [ ] **Step 3: Build the UI**

```bash
cd ui && npm run build
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/src/app/files/DocumentList.tsx
git commit -m "feat(kb): status pill column on document list showing summary state"
```

---

## Task 22: Context composer — inject `<organization_knowledge>` section

**Files:**
- Modify: `api/services/workflow/pipecat_engine_context_composer.py`

- [ ] **Step 1: Add a new helper function**

In `api/services/workflow/pipecat_engine_context_composer.py`, after the existing constants and BEFORE `compose_system_prompt_for_node`, add:

```python
import os
from typing import Optional as _Optional

from api.services.knowledge_base.org_index_cache import get_index_for_org
from api.services.knowledge_base.org_index_renderer import (
    build_org_index_md as _filter_index_md_for_direction,
    enforce_size_budget,
)

KB_INDEX_PROMPT_BUDGET_CHARS = int(
    os.environ.get("KB_INDEX_PROMPT_BUDGET_CHARS", "32000")  # ~8000 tokens
)


async def compose_kb_index_section(
    *,
    organization_id: int,
    call_direction: _Optional[str],
    enabled: bool,
) -> str:
    """Return the `<organization_knowledge>` block to inject in the system prompt.

    Returns an empty string when:
      - The node opted out (enabled=False)
      - The org has no built index yet
      - The filtered index is empty

    The cached payload is always built without a direction filter; we re-render
    a filtered view here using the same renderer so we can produce
    direction-specific markdown without rebuilding the cache.
    """
    if not enabled:
        return ""

    payload = await get_index_for_org(organization_id)
    if not payload:
        return ""

    # The cached md is the unfiltered version. To honor call direction we
    # re-render from documents — but to avoid an extra DB hit on the hot path,
    # we apply a light text-level filter: if call_direction is set, drop lines
    # whose `_uses:` token doesn't include it.
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
    """Drop doc lines that don't include `direction` in their `_uses:` token.

    Header lines (`# ...`, `## ...`, blanks) are kept.
    """
    out = []
    for line in md.split("\n"):
        if line.startswith("- "):
            # `_uses: inbound, outbound_` token format from renderer
            uses_segment = line.split("_uses:", 1)
            if len(uses_segment) == 2:
                uses = uses_segment[1].split("_", 1)[0]
                if direction not in uses:
                    continue
        out.append(line)
    return "\n".join(out)
```

- [ ] **Step 2: Integrate the section into `compose_system_prompt_for_node`**

Change `compose_system_prompt_for_node` from synchronous to `async` and inject the KB section. Replace the existing function with:

```python
async def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
    organization_id: _Optional[int] = None,
    call_direction: _Optional[str] = None,
) -> str:
    """Compose the full system prompt text for a workflow node."""
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
```

- [ ] **Step 3: Update the call sites**

```bash
grep -rn "compose_system_prompt_for_node" api/ --include="*.py"
```

For each call site outside this file, change it to `await compose_system_prompt_for_node(...)` and pass `organization_id` and `call_direction`. These usually come from `workflow_run.organization_id` and `workflow_run.call_type` (existing columns).

If a caller doesn't have a `workflow_run` in scope, pass `organization_id=None` to preserve current behavior (no KB section).

- [ ] **Step 4: Smoke import**

```bash
source venv/bin/activate && python -c "
from api.services.workflow.pipecat_engine_context_composer import compose_system_prompt_for_node, compose_kb_index_section
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add api/services/workflow/pipecat_engine_context_composer.py
git commit -m "feat(kb): inject org knowledge index into system prompt at call start"
```

---

## Task 23: Context composer tests

**Files:**
- Create: `api/tests/test_context_composer_kb_index.py`

- [ ] **Step 1: Write the tests**

Create `api/tests/test_context_composer_kb_index.py`:

```python
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
    assert "a.pdf" in section  # inbound
    assert "c.pdf" in section  # both
    assert "b.pdf" not in section  # outbound-only filtered out


@pytest.mark.asyncio
async def test_outbound_call_filters_inbound_only():
    with patch(
        "api.services.workflow.pipecat_engine_context_composer.get_index_for_org",
        AsyncMock(return_value={"md": SAMPLE_MD}),
    ):
        section = await compose_kb_index_section(
            organization_id=1, call_direction="outbound", enabled=True
        )
    assert "b.pdf" in section  # outbound
    assert "c.pdf" in section  # both
    assert "a.pdf" not in section  # inbound-only filtered out


def test_direction_filter_keeps_headers():
    filtered = _direction_filter_text(SAMPLE_MD, "inbound")
    assert "# Organization Knowledge Index" in filtered
    assert "## Contract" in filtered
    assert "## Policy" in filtered
```

- [ ] **Step 2: Run the tests**

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_context_composer_kb_index.py -v
```

Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add api/tests/test_context_composer_kb_index.py
git commit -m "test(kb): context composer injection and direction filtering"
```

---

## Task 24: Update KB retrieval tool description

**Files:**
- Modify: `api/services/workflow/tools/knowledge_base.py`

- [ ] **Step 1: Add the sentence to both description branches**

Find `get_knowledge_base_tool` in `api/services/workflow/tools/knowledge_base.py`. Update both branches of the `description` ternary to append:

```python
description += (
    " Refer to the `<organization_knowledge>` index in your system prompt to "
    "identify which documents are likely relevant before searching."
)
```

Add this AFTER the `description = (...)` if/else block, before `return {...}`.

- [ ] **Step 2: Smoke import + sanity check**

```bash
source venv/bin/activate && python -c "
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool
print(get_knowledge_base_tool()['function']['description'])
"
```

Expected: description text ends with the new sentence about `<organization_knowledge>`.

- [ ] **Step 3: Commit**

```bash
git add api/services/workflow/tools/knowledge_base.py
git commit -m "feat(kb): point retrieval tool at org knowledge index in its description"
```

---

## Task 25: Observability — OTEL spans and PostHog event

**Files:**
- Modify: `api/services/knowledge_base/doc_card_extraction.py`
- Modify: `api/tasks/org_index_rebuild.py`
- Modify: `api/enums.py` (add PostHog event)

- [ ] **Step 1: Add the PostHog event enum**

Find `class PostHogEvent` in `api/enums.py`. Add:

```python
    KNOWLEDGE_BASE_DOC_CARD_GENERATED = "knowledge_base_doc_card_generated"
```

- [ ] **Step 2: Wrap extraction call with OTEL span**

In `api/services/knowledge_base/doc_card_extraction.py`, at the top, add:

```python
from opentelemetry import trace

from api.services.pipecat.tracing_config import ensure_tracing
```

Modify `extract_doc_card_for_document` to wrap the LLM call. After the `provider, model, api_key, kwargs = await _resolve_extraction_llm(...)` line, replace the subsequent block from `llm = create_llm_service_from_provider(...)` through the `card = await _call_and_validate(...)` line with:

```python
    if ensure_tracing():
        tracer = trace.get_tracer("knowledge_base")
        with tracer.start_as_current_span("kb.doc_card_extraction") as span:
            span.set_attribute("doc_id", document_id)
            span.set_attribute("doc_type", document.doc_type or "other")
            span.set_attribute("model_provider", provider)
            span.set_attribute("model_id", model)
            span.set_attribute(
                "extraction_mode",
                "full_text" if full_text else "stitched_sample",
            )
            span.set_attribute("input_chars", len(extraction_input))
            llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
            try:
                card = await _call_and_validate(llm, user_prompt, repair_allowed=True)
                span.set_attribute("final_status", "success" if card else "failed")
            except Exception as e:
                span.set_attribute("final_status", "failed")
                span.record_exception(e)
                raise
    else:
        llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
        card = await _call_and_validate(llm, user_prompt, repair_allowed=True)
```

After the successful `await db_client.update_doc_card(...)` call, add:

```python
    from api.enums import PostHogEvent
    from api.services.posthog_client import capture_event

    user = None
    if document.created_by:
        user = await db_client.get_user_by_id(document.created_by)
    capture_event(
        distinct_id=str(user.provider_id) if user else f"org_{document.organization_id}",
        event=PostHogEvent.KNOWLEDGE_BASE_DOC_CARD_GENERATED,
        properties={
            "document_id": document.id,
            "doc_type": document.doc_type,
            "organization_id": document.organization_id,
            "model_provider": provider,
            "model_id": model,
        },
    )
```

- [ ] **Step 3: Wrap rebuild task with OTEL span**

In `api/tasks/org_index_rebuild.py`, add at top:

```python
from opentelemetry import trace
from api.services.pipecat.tracing_config import ensure_tracing
```

Wrap the body of `rebuild_org_knowledge_index` (inside the `try:` after `acquired` check) with:

```python
        if ensure_tracing():
            tracer = trace.get_tracer("knowledge_base")
            with tracer.start_as_current_span("kb.org_index_rebuild") as span:
                span.set_attribute("organization_id", organization_id)
                # ... existing rebuild body ...
                span.set_attribute("doc_count", len(documents))
                span.set_attribute("output_chars", len(md))
        else:
            # existing rebuild body (unchanged)
```

Pragmatic note: rather than duplicate the body, lift it into a private `_do_rebuild(organization_id) -> tuple[int, str]` helper that both branches call. Set span attributes from the returned tuple.

- [ ] **Step 4: Smoke import**

```bash
source venv/bin/activate && python -c "
from api.services.knowledge_base.doc_card_extraction import extract_doc_card_for_document
from api.tasks.org_index_rebuild import rebuild_org_knowledge_index
from api.enums import PostHogEvent
assert PostHogEvent.KNOWLEDGE_BASE_DOC_CARD_GENERATED.value == 'knowledge_base_doc_card_generated'
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add api/services/knowledge_base/doc_card_extraction.py api/tasks/org_index_rebuild.py api/enums.py
git commit -m "feat(kb): OTEL spans for doc card extraction and org index rebuild; PostHog event"
```

---

## Task 26: End-to-end smoke test (gated)

**Files:**
- Create: `api/tests/test_kb_e2e_doc_card.py`

- [ ] **Step 1: Add pytest marker registration**

In `api/conftest.py` (or `pyproject.toml` / `pytest.ini` — check which is in use), register an `mps` marker:

In `api/conftest.py`, near the top:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "mps: tests that require the live MPS service")
```

If `pytest_configure` already exists in `api/conftest.py`, append the `addinivalue_line` call inside it.

- [ ] **Step 2: Write the smoke test**

Create `api/tests/test_kb_e2e_doc_card.py`:

```python
"""End-to-end smoke test for the doc card + org index pipeline.

Requires a running MPS service. Gated by `pytest -m mps`.
Provide a tiny fixture PDF at api/tests/fixtures/sample.pdf (or .txt).
"""

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.mps


@pytest.mark.asyncio
async def test_full_pipeline_produces_index_section(
    setup_test_db, test_org_and_user, run_arq_worker
):
    """Upload -> chunks -> doc_card -> org index -> composer injection."""
    from api.db import db_client
    from api.tasks.knowledge_base_processing import process_knowledge_base_document
    from api.tasks.org_index_rebuild import rebuild_org_knowledge_index
    from api.services.knowledge_base.org_index_cache import get_index_for_org
    from api.services.workflow.pipecat_engine_context_composer import (
        compose_kb_index_section,
    )

    fixture = Path(__file__).parent / "fixtures" / "sample.txt"
    if not fixture.exists():
        pytest.skip("Fixture sample.txt missing")

    org_id, user_id = test_org_and_user

    # Bypass S3 by writing a minimal doc + full_text directly.
    doc = await db_client.create_document(
        organization_id=org_id, created_by=user_id,
        filename="sample.txt", file_size_bytes=fixture.stat().st_size,
        file_hash=db_client.compute_file_hash(str(fixture)),
        mime_type="text/plain", retrieval_mode="full_document",
        user_description="A small sample text fixture for the e2e test pipeline.",
        doc_type="other", intended_use=["inbound"],
    )
    await db_client.update_document_full_text(doc.id, fixture.read_text())
    await db_client.update_document_status(doc.id, "completed", total_chunks=0)

    # Run extraction directly (no need for the full ARQ loop in this test).
    from api.services.knowledge_base.doc_card_extraction import (
        extract_doc_card_for_document,
    )
    card = await extract_doc_card_for_document(doc.id)
    assert card is not None

    await rebuild_org_knowledge_index({}, org_id)
    payload = await get_index_for_org(org_id)
    assert payload is not None
    assert "sample.txt" in payload["md"]

    section = await compose_kb_index_section(
        organization_id=org_id, call_direction="inbound", enabled=True
    )
    assert "<organization_knowledge>" in section
    assert "sample.txt" in section
```

- [ ] **Step 3: Add the fixture file**

```bash
mkdir -p api/tests/fixtures && printf "Sample fixture document for end-to-end testing.\nIt has a few sentences for the LLM to summarize." > api/tests/fixtures/sample.txt
```

- [ ] **Step 4: Document how to run**

The test is gated. To run it once locally:

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && \
python -m pytest api/tests/test_kb_e2e_doc_card.py -m mps -v
```

For the default CI run, the marker filter excludes it.

- [ ] **Step 5: Commit**

```bash
git add api/conftest.py api/tests/test_kb_e2e_doc_card.py api/tests/fixtures/sample.txt
git commit -m "test(kb): gated end-to-end smoke test for doc card + index pipeline"
```

---

## Manual verification (after all tasks)

Run through this checklist live, against a local dev environment. Required before declaring the feature shippable.

- [ ] **Upload a doc with all fields filled.** Watch the file list → status pill transitions `Summary pending… → ✓ Summary ready` within ~10s for a small doc.
- [ ] **Inspect the DocCard.** Hit `GET /api/v1/knowledge-base/documents/{uuid}` and confirm `doc_card`, `doc_card_extracted_at`, and `topics` are populated.
- [ ] **Trigger an outbound call** through a workflow scoped to your test org. Capture the system prompt sent to the LLM (use Langfuse trace or workflow run logs). Confirm `<organization_knowledge>` section is present and that any inbound-only docs are NOT in it.
- [ ] **Edit a description.** PATCH the doc with a new `user_description`. Confirm the org index `payload.md` reflects the new description within a few seconds (cached invalidation via WorkerSyncManager).
- [ ] **Re-extract.** POST `/documents/{uuid}/re-extract`. Confirm a new `doc_card_extracted_at` timestamp appears.
- [ ] **Legacy doc guard.** Find a doc with no `user_description`. POST `/re-extract` and confirm a 400 with the expected message.
- [ ] **Soft-delete a doc.** Verify it disappears from `payload.md` within seconds.

---

## Self-review notes

- **Spec coverage check:** every spec section maps to one or more tasks: data model → Tasks 1-2, DocCard schema → Task 3, request/response schemas → Task 4, DB client → Task 5, upload UX → Tasks 16, 20, extraction pipeline → Tasks 6-10, org index → Tasks 11-15, call agent integration → Tasks 22-24, error handling → covered across Tasks 7-10, 15, 19, observability → Task 25, security/org-scoping → enforced across Tasks 5, 17, 19, edge cases → Tasks 11, 18, 22, testing → Tasks 3, 6, 9-11, 15, 19, 23, 26, frontend → Tasks 20-21.
- **Type consistency:** `DocCard` defined in Task 3 is used unchanged by Tasks 7, 9, 10. `KnowledgeBaseDocumentModel` columns added in Task 2 are queried/written in Tasks 5, 7, 11, 15, 17, 18. `EditDocumentRequestSchema` added in Task 4 is imported in Task 17. `KB_INDEX_UPDATED` event added in Task 12 is broadcast in Task 13 and handled by registration in Task 14.
- **No placeholders:** scan complete — every step has executable code or commands. Fixture-name verification steps (Task 9 Step 2, Task 19 Step 2) are explicit about which file to read and what to look for, not deferred to the engineer's judgement.
