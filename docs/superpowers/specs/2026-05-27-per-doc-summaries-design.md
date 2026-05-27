# Per-Document Summaries + Org Knowledge Index — Design

**Status:** Draft
**Date:** 2026-05-27
**Scope:** First subsystem of the broader "company brain" feature. External-source connectors (Gmail, Slack, Discord, WhatsApp, Mattermost) are out of scope and will get their own specs later.

## Problem

The voice AI platform already has a working knowledge-base RAG pipeline (Docling parsing, chunking, OpenAI embeddings, pgvector IVFFlat, contextual retrieval, an agent retrieval tool). Two gaps remain:

1. At upload time, the user can't tell the system **what the document is and how the call agent should use it**. The pipeline parses bytes; it doesn't know intent.
2. The call agent has **no top-down view** of what's in the knowledge base. It can search chunks but can't answer "which document covers X" without making the search itself.

The user originally framed this as needing a single megasummary "central brain" markdown file. That approach is rejected (see "Approach considered and rejected" below). Instead, we build per-document structured summaries plus a small, auto-built org-level index.

## Goals

- A user uploading a document can attach: `doc_type`, `intended_use` (inbound / outbound / both), and a required free-text description.
- After every successful ingest, an LLM extracts a structured `DocCard` (summary, key facts, entities, FAQs, suggested uses, topics) steered by the user's description.
- An auto-rebuilt org-scoped "knowledge index" markdown is injected into the call agent's system prompt at call start, giving the agent a table of contents over the org's documents.
- The system stays **model-agnostic** — extraction routes through the existing `create_llm_service_from_provider` factory, defaulting to Dograh's hosted MPS tier.
- Zero breaking changes to the existing knowledge-base API or call pipeline.

## Non-Goals

- External-source connectors (email, Slack, Discord, WhatsApp, Mattermost). Separate subsystem.
- A single combined markdown summary across all documents. Rejected — see below.
- Multi-tier hierarchical topic clustering (Approach C from brainstorming). Deferred until per-org doc counts exceed ~1000.
- Auto re-extraction on description edits. Re-extraction is explicit only.
- New tools for the call agent beyond the existing `retrieve_from_knowledge_base`.

## Approach considered and rejected

> "LLM reads everything and writes a single megasummary markdown."

Rejected because:

- **Compression loses signal.** Forces the LLM to pick what to keep before knowing what the caller will ask. RAG defers that choice to query time.
- **Re-summarize on every change.** Adding one doc re-processes everything.
- **No source attribution.** The call agent can't cite which doc backs a claim.
- **Recursive context overflow.** "Read all data, write a summary" still has to fit all data into context.
- **Latency at call time.** A large markdown blob in every LLM turn burns tokens and adds delay.

The chosen approach (per-doc cards + small auto-built index) achieves the same intent — the agent gets top-down knowledge of what's available — without these costs.

## Architecture

```
┌──────────────────┐    ┌─────────────────────────────────────────────┐
│   Upload UI      │───▶│  POST /knowledge-base/process-document      │
│ (DocumentUpload) │    │  (now requires doc_type, intended_use,      │
└──────────────────┘    │   user_description)                          │
                        └────────────────────┬────────────────────────┘
                                             │ enqueue
                                             ▼
                        ┌────────────────────────────────────────────┐
                        │  process_knowledge_base_document (ARQ)     │
                        │                                            │
                        │  existing:                                 │
                        │    download → Docling (MPS) → chunks       │
                        │    → OpenAI embeddings → DB write          │
                        │                                            │
                        │  NEW final step:                           │
                        │    extract_doc_card(document_id)           │
                        │      → builds extraction input             │
                        │      → calls LLM via factory               │
                        │      → validates Pydantic                  │
                        │      → writes doc_card + topics + ts       │
                        │      → enqueues rebuild_org_knowledge_index│
                        └────────────────────┬───────────────────────┘
                                             │ enqueue (deduped)
                                             ▼
                        ┌────────────────────────────────────────────┐
                        │  rebuild_org_knowledge_index (ARQ)         │
                        │                                            │
                        │  Pure projection over DocCards (no LLM):   │
                        │    SELECT docs → group_by(doc_type)        │
                        │    → render markdown                       │
                        │    → write organization_configurations     │
                        │      .knowledge_index                      │
                        │    → WorkerSyncManager.publish             │
                        │      kb_index_updated:{org_id}             │
                        └────────────────────┬───────────────────────┘
                                             │ pub/sub
                                             ▼
                        ┌────────────────────────────────────────────┐
                        │  pipecat_engine_context_composer           │
                        │                                            │
                        │  At call start:                            │
                        │    read org's knowledge_index.md           │
                        │      (worker cache, invalidated by pub/sub)│
                        │    filter by call direction                │
                        │    inject <organization_knowledge> section │
                        │      into system prompt                    │
                        │                                            │
                        │  Existing retrieve_from_knowledge_base     │
                        │  tool unchanged.                           │
                        └────────────────────────────────────────────┘
```

## Data model

Migration `add_doc_card_columns.py`. All additive, all safe for backfill.

On `knowledge_base_documents`:

| Column | Type | Default | Purpose |
|---|---|---|---|
| `doc_type` | `VARCHAR(40)` nullable | — | User-selected at upload. One of: `contract`, `policy`, `pricing`, `faq`, `script`, `other`. Old rows null. |
| `intended_use` | `JSON` not-null | `'[]'` | Array of strings: `["inbound"]`, `["outbound"]`, or both. |
| `user_description` | `TEXT` nullable | — | Required free-text description from upload modal. Old rows null. |
| `doc_card` | `JSON` nullable | — | Full extracted DocCard. Filled by extraction task. |
| `doc_card_extracted_at` | `TIMESTAMPTZ` nullable | — | When extraction last succeeded. |
| `topics` | `JSON` not-null | `'[]'` | Flat list of normalized topic strings (denormalized from `doc_card` for indexing). |

GIN index on `topics`.

Org index lives in `organization_configurations` (existing table) under key `knowledge_index`:

```json
{
  "md": "<rendered markdown>",
  "doc_count": 312,
  "char_count": 14823,
  "generated_at": "2026-05-27T14:22:01Z",
  "hash": "sha256:..."
}
```

## DocCard schema

`api/schemas/doc_card.py`:

```python
class FaqPair(BaseModel):
    q: str
    a: str

class DocCard(BaseModel):
    title: str                            # short, human-readable
    summary_150_words: str                # what this doc is
    key_facts: list[str]                  # 5-15 bullet facts
    entities: dict[str, list[str]]        # {people, organizations, products, locations, dates}
    numbers_and_pricing: list[str]        # "$49/mo Pro tier", "30-day refund window"
    faqs: list[FaqPair]                   # {q, a} pairs found in doc
    suggested_agent_uses: list[str]       # 3-7 bullets, drawn from intended_use
    topics: list[str]                     # 3-10 normalized lowercase-english keywords
```

## Upload UX

**UI (`ui/src/app/files/DocumentUpload.tsx`):**

Three new required fields *before* the file picker:
- `Document type` — shadcn `Select`, dropdown.
- `Intended use` — checkboxes (inbound / outbound), at least one required.
- `Describe this document` — `Textarea`, min 20 chars.

File picker stays a hidden `<input type="file">` triggered by a visible button (per `ui/AGENTS.md` convention). Submit disabled until all valid.

**API:**

- `ProcessDocumentRequestSchema` gains `doc_type: str`, `intended_use: list[str]`, `user_description: str` (all required, Pydantic-validated).
- `POST /process-document` writes these to the new columns before enqueuing the job and passes them in the job context.
- New `PATCH /documents/{document_uuid}` — edit `user_description` / `doc_type` / `intended_use`. Org-scoped (404 cross-org). Does **not** auto re-extract.
- New `POST /documents/{document_uuid}/re-extract` — enqueues `process_knowledge_base_document` in extract-only mode. Returns 202.

Run `npm run generate-client` in `ui/` after schema change.

## Extraction pipeline

New module `api/services/knowledge_base/doc_card_extraction.py`. Called as the final step of `process_knowledge_base_document` after chunks (or `full_text`) are persisted.

**Per-document flow:**

1. Load `DocumentRow` (has `user_description`, `doc_type`, `intended_use`).
2. Build extraction input:
   - If `full_text` length < `EXTRACTION_TOKEN_BUDGET` (default 150K tokens), input = `full_text`.
   - Else, input = `stitched_sample(chunks)`: first 8 + last 4 + every Nth by section-header rank, target ~120K tokens, deterministic order. Append marker `[document truncated for extraction]`.
3. Build prompt:
   - System: "You extract structured DocCards from business documents to power voice AI agents during calls."
   - User: includes `doc_type`, `intended_use`, `user_description`, then `<document>…</document>`, then the JSON schema and a one-shot example.
   - Instructs the LLM to extract the card in the document's language, except `topics` always lowercased English.
4. Resolve model via existing `resolve_user_llm_config` pattern (same as QA):
   - If user's `UserConfiguration.llm` is set → use that `(provider, model, api_key, kwargs)`.
   - Else fall back to `("dograh", "default", None, {})` — routes through MPS using the org's default Dograh service key.
5. Call LLM via `create_llm_service_from_provider(...)`. JSON output parsed by `api/services/gen_ai/json_parser.py` (handles provider quirks).
6. Validate against `DocCard` Pydantic schema. On validation failure, one repair attempt with the parse error in the prompt.
7. Write `doc_card`, `topics` (extracted from `doc_card.topics`), `doc_card_extracted_at = now()`.
8. Enqueue `rebuild_org_knowledge_index(organization_id)`.

**Model agnosticism is structural:**
- Extraction never imports a vendor SDK.
- Prompt uses portable techniques (JSON schema + one-shot), no vendor-specific tool-use blocks.
- New env override `KB_DOC_CARD_MODEL_TIER` (default `"default"`) is consulted **only** when the resolved provider is `"dograh"`. It overrides the model tier (e.g. lets Dograh users move to `"accurate"` without code changes). When a user has their own provider configured, their model is used verbatim and the env var is ignored.

**Status semantics:** `processing_status` reaches `completed` once chunks are stored, regardless of extraction state. Doc is searchable immediately. Extraction state is surfaced via `doc_card_extracted_at IS NULL` and the UI status pill (see Edge cases).

## Org index builder

`api/services/knowledge_base/org_index_builder.py` + new ARQ task `rebuild_org_knowledge_index(organization_id)`.

**Triggers** (all enqueue the same task; deduped by 30s Redis lock keyed by `org_id`):

- DocCard extraction completes.
- Doc soft-deleted.
- User edits description / doc_type / intended_use.
- "Re-extract summary" completes.

**Build logic (no LLM, pure projection):**

```python
async def build_org_index_md(organization_id: int) -> str:
    docs = await db_client.list_active_documents_for_index(organization_id)
    # docs filtered to: is_active=True AND doc_card IS NOT NULL

    if len(docs) < 5:
        # Flat list for tiny orgs
        return render_flat(docs)

    by_type = group_by(docs, key=lambda d: d.doc_type or "other")
    lines = [f"# Organization Knowledge Index ({len(docs)} docs)\n"]
    for doc_type in sorted(by_type.keys()):
        group = sorted(by_type[doc_type], key=lambda d: d.doc_card["title"])
        lines.append(f"\n## {doc_type.title()} ({len(group)} docs)")
        for d in group:
            card = d.doc_card
            lines.append(
                f"- **{card['title']}** ({d.filename}) — "
                f"{card['summary_150_words'][:140]}… "
                f"_uses: {', '.join(d.intended_use)}_ "
                f"_topics: {', '.join(card['topics'][:5])}_"
            )
    return enforce_size_budget("\n".join(lines), max_bytes=64_000)
```

`enforce_size_budget` truncates longest summaries first and appends `[+ N more docs not shown — use search to find them]`.

**Cache invalidation:** after writing `organization_configurations.knowledge_index`, publish `kb_index_updated:{org_id}` via `WorkerSyncManager` (per `api/AGENTS.md`). Each worker invalidates its in-memory cache on receipt.

## Call agent integration

**Where:** `api/services/workflow/pipecat_engine_context_composer.py`. The org index is injected once at call start, inside a dedicated `<organization_knowledge>` section.

**System prompt insertion:**

```
<organization_knowledge>
The following is your organization's knowledge index — a table of
contents of documents available to you. Use it to decide WHICH document
to look in. To get actual content, call the `retrieve_from_knowledge_base`
tool with a specific question.

{org_index_md}

Important rules:
- The index is a guide, not a source of truth. Quote facts only after
  retrieving them with the tool.
- If a caller asks about something not in the index, say so honestly.
- Prefer documents whose intended_use matches this call's direction.
</organization_knowledge>
```

**Read path:**
- Composer reads `organization_configurations.knowledge_index.md` once per call.
- Cached in worker memory keyed by `(org_id, hash)`. Invalidated by `WorkerSyncManager` pub/sub.
- If missing / empty (new org) → section omitted entirely.

**Token budget:**
- Configurable `KB_INDEX_PROMPT_BUDGET_TOKENS` (default 8000).
- If exceeded → fallback excerpt: doc count + first 40 doc lines + `[index too large, use search]`.

**Inbound/outbound filtering:**
- Composer reads `workflow_run.call_type` (existing column from migration `b79f19f68157`).
- Doc lines whose `intended_use` excludes the current direction are dropped before rendering.

**Per-node opt-out:**
- Workflow nodes get a setting `include_kb_index: bool` (default `true`). Short IVR-style flows can disable the injection.

**Tool description update only — no new tool.** One sentence added to `retrieve_from_knowledge_base`'s description:
> "Refer to the `<organization_knowledge>` index in your system prompt to identify which documents are likely relevant before searching."

## Error handling

**Extraction failure modes:**

| Failure | Behavior |
|---|---|
| LLM 5xx / timeout | ARQ retries 2x w/ backoff. After 3 total failures: `doc_card = NULL`, `processing_error` set, `processing_status` stays `completed`. Doc still searchable. |
| Invalid JSON | One auto-repair attempt (re-prompt with parse error). Then mark failed. |
| Pydantic validation failure | Same as invalid JSON. |
| User LLM provider has no API key (non-Dograh) | Skip extraction. UI shows "Summary pending — configure LLM in Model Configurations". Dograh default never hits this. |
| Zero extractable text (image-only PDF) | Skip. `processing_error = "no_text_content"`. Doc excluded from org index. |
| Doc soft-deleted mid-extraction | Extraction completes. Index rebuild filters out inactive docs. Orphaned `doc_card` is harmless. |

**Org index rebuild failure modes:**

| Failure | Behavior |
|---|---|
| Rebuild task crashes | Existing `knowledge_index` untouched. ARQ retries 3x. After exhaustion, previous index stays live. Log + Sentry alert. |
| Concurrent triggers | Coalesced by 30s Redis lock. Last-writer-wins is correct (builder reads current state). |
| Stale worker cache | `WorkerSyncManager` invalidates within ~50ms. One call in that window may use stale index — acceptable. |

**UI status pills (DocumentList.tsx):**

| State | Pill |
|---|---|
| `doc_card_extracted_at IS NOT NULL` | `✓ Summary ready` (green) |
| `user_description IS NULL` (legacy doc) | `Needs description` (blue, with edit button) |
| `processing_status = completed` AND `doc_card IS NULL` AND no error | `Summary pending…` (gray + spinner) |
| `processing_error = "no_text_content"` | `No text — add OCR'd version` (amber) |
| Other extraction failure | `Summary failed — retry` (red + retry button) |

## Observability

OTEL → Langfuse spans:

- `kb.doc_card_extraction` per doc — attrs: `doc_id`, `doc_type`, `model_provider`, `model_id`, `input_tokens`, `output_tokens`, `extraction_mode` (full_text|stitched_sample), `truncated`, `validation_retries`, `final_status`.
- `kb.org_index_rebuild` per rebuild — attrs: `organization_id`, `doc_count`, `output_chars`, `truncated_to_budget`, `duration_ms`.
- Existing `knowledge_base_retrieval` span unchanged.
- New call-root attrs: `kb.index.included` (bool), `kb.index.doc_count` (int).

PostHog event: `KNOWLEDGE_BASE_DOC_CARD_GENERATED` (org_id, doc_id, doc_type, success).

## Security / org-scoping

Per `api/AGENTS.md` Organization Scoping rules:

- Every read of `knowledge_index` filters by `organization_id`. Composer takes `org_id` from the workflow run, never from request payload.
- `PATCH /documents/{uuid}` and `POST /documents/{uuid}/re-extract` validate that the doc belongs to `user.selected_organization_id` before mutating. Same pattern as existing `delete_document`.
- Doc card extraction reads `created_by` from the document row (existing column) to resolve the LLM config — no caller-provided IDs trusted.

## Edge cases

- **Tiny orgs (< 5 docs):** flat list, no doc_type grouping.
- **Non-English documents:** DocCard extracted in source language; `topics` always lowercased English for routing consistency.
- **Per-node opt-out:** workflow nodes can set `include_kb_index: false`.
- **Index size cap:** 64KB total via summary truncation; 8000 token prompt-injection cap with excerpt fallback.
- **Legacy docs (uploaded before this feature):** `user_description`, `doc_type`, `intended_use` are NULL / empty. They are listed in the file UI with a "Needs description" pill and excluded from the org index until the user fills the fields via the new `PATCH` endpoint. Clicking "Re-extract summary" on a legacy doc without filling the fields first is blocked at the route layer (400 "description required"). No bulk-backfill script — this is a user-facing data-quality task, not a migration.

## Testing

**Unit (no LLM, no DB):**
- `test_doc_card_schema.py` — Pydantic validation across required fields, type coercion, malformed entities.
- `test_org_index_renderer.py` — empty org, 1 doc (flat), grouped+sorted, > 64KB truncation, inbound-only filter, skipped null-card rows.
- `test_extraction_input_builder.py` — full passthrough for small docs, stitched-sample determinism, truncation marker.

**Integration (real DB per `api/.env.test`, mocked LLM):**
- `test_doc_card_extraction_task.py` — happy path, org-scoped isolation.
- `test_doc_card_extraction_failure.py` — LLM 5xx retry behavior, invalid JSON repair, Pydantic validation repair, missing api_key skip, Dograh default works without api_key.
- `test_org_index_rebuild_task.py` — trigger on extraction / delete / edit, 30s lock coalescing, WorkerSyncManager publish.
- `test_org_index_org_scoping.py` — security: orgA upload doesn't affect orgB index.

**Routes:**
- `test_knowledge_base_routes_with_metadata.py` — `/process-document` requires new fields; new `PATCH` and `re-extract` endpoints; cross-org 404s.

**Composer:**
- `test_context_composer_kb_index.py` — section present/absent, inbound/outbound filtering, budget fallback, per-node opt-out.

**E2E:**
- `test_kb_e2e_doc_card.py` — small fixture PDF → full pipeline → index reflects doc → composer injects section. Gated by `--mps` pytest marker.

**Frontend:**
- One vitest test for `DocumentUpload.tsx` form validation.

**Manual verification checklist:**
- Upload a PDF with all fields → status pill transitions `pending → processing → ✓ Summary ready`.
- Start an outbound call; capture system prompt; verify `<organization_knowledge>` present and inbound-only docs excluded.
- Soft-delete a doc; verify removed from index within seconds.
- Edit description; verify index reflects new description, card unchanged.
- Click "Re-extract summary"; verify new `doc_card_extracted_at` and updated card.

## Future work (out of scope)

- External-source connectors (Gmail, Slack, Discord, WhatsApp, Mattermost). Each is its own subsystem with its own spec.
- Hierarchical topic clustering (Approach C) — graduate when per-org doc counts exceed ~1000.
- Auto re-extraction on description edits — currently explicit only for cost discipline.
- A separate `get_org_knowledge_index` tool — currently system-prompt-injected; switch to tool-based if prompt-size pressure grows.
