# Local Document Parsing — Design

**Date:** 2026-06-19
**Status:** Approved (pending spec review)
**Fork:** Speakly (BasimBashir/Speakly-)

## Problem

Knowledge-base document ingestion (PDF/DOCX upload) and the "Auto-write"
description feature both delegate document **parsing + chunking** to a remote
managed service:

```
mps_service_key_client.process_document()  →  POST https://services.dograh.com/api/v1/document/process
```

For a fully self-hosted, local-models deployment this is the only remaining
hard dependency on Dograh's cloud for the KB feature. It is currently broken:
the managed service returns `500 - "Processing failed: libGL.so.1: cannot open
shared object file"` (a server-side regression on `services.dograh.com`,
confirmed working on 2026-06-05 and failing on 2026-06-19). The user's local
container is healthy and has `libGL.so.1`, so the fault is entirely remote.

Goal: parse documents **locally, in-process**, removing the cloud dependency
for KB ingestion. Replace the remote parse entirely (no fallback, no toggle).

## Scope

- **In scope:** text-based PDFs, DOCX, TXT, MD. Both `full_document` and
  `chunked` retrieval modes. Replace the two parse call sites.
- **Out of scope (YAGNI):** OCR / scanned-image PDFs; contextual-retrieval
  augmentation (`contextualized_text` will equal `chunk_text`); removing
  `mps_service_key_client` (it is retained for service-key CRUD and
  billing/usage); exact embedding-model tokenization.

## The payload contract (must be preserved exactly)

`process_document(...)` returns, and downstream consumers expect:

```python
{
  "mode": "chunked" | "full_document",
  "docling_metadata": { ... },          # arbitrary dict, stored on the document
  "full_text": str | None,              # populated in full_document mode
  "chunks": [                           # populated in chunked mode
    {
      "chunk_text": str,                # required
      "contextualized_text": str | None,# optional; consumer falls back to chunk_text
      "chunk_index": int,               # required, sequential from 0
      "chunk_metadata": dict | None,    # optional; consumer defaults to {}
      "token_count": int,               # optional; consumer defaults to 0
    },
    ...
  ],
}
```

Consumers:
- `api/services/knowledge_base/describe_preview.py` — uses `full_text` or joins
  `chunks[].chunk_text` for the LLM description.
- `api/tasks/knowledge_base_processing.py` — `full_document`: stores `full_text`.
  `chunked`: builds `KnowledgeBaseChunkModel` per chunk, embeds
  `contextualized_text` via the local embeddings service, stores in pgvector.

Because the local parser returns this identical shape, **no consumer code
changes** beyond swapping which function produces the payload.

## Architecture

New module: `api/services/knowledge_base/local_document_parser.py`

```python
async def process_document(
    *,
    file_path: str,
    filename: str,
    content_type: str,
    retrieval_mode: str = "chunked",
    max_tokens: int = 128,
    chunk_overlap_tokens: int = 0,
    merge_peers: bool = True,
    tokenizer_model: str | None = None,
    organization_id: int | None = None,   # accepted for parity, unused
    created_by: str | None = None,        # accepted for parity, unused
    correlation_id: str | None = None,    # accepted for parity, unused
) -> dict
```

- Signature mirrors `mps_service_key_client.process_document` field-for-field
  so the swap at call sites is a one-line reference change.
- Declared `async`; the CPU-bound extraction/chunking runs inside
  `asyncio.to_thread(...)` so it never blocks the event loop (matters in the
  request path for Auto-write and in the arq worker).
- No network, no auth, no service key.

## Components

### Extraction (sync; dispatched by extension / content-type)

| Type | Library | Notes |
|------|---------|-------|
| `.pdf` | **pypdf** (BSD) | concatenate per-page `extract_text()`; record page count |
| `.docx` | **python-docx** | join paragraph text |
| `.txt`, `.md` | stdlib | read + decode utf-8, `errors="replace"` |
| other | — | raise `ValueError` (→ existing "couldn't read" UX) |

Returns `(full_text: str, metadata: dict)` where metadata includes
`{"parser": "local", "filename": ..., "pages": int, "char_count": int}`.

### Chunking (chunked mode only)

- Sentence-split with **nltk punkt** (already downloaded in the devcontainer
  image build).
- Greedily group sentences up to ~`max_tokens`; carry over trailing sentences
  for `chunk_overlap_tokens` overlap when > 0. `merge_peers` keeps small
  adjacent sentences together (greedy grouping satisfies this).
- **Token counting heuristic:** `~ceil(len(text) / 4)` (≈4 chars/token).
  Deliberate tradeoff — no model download, fully offline; chunk sizes are
  approximate, which is acceptable for retrieval. A real BGE-M3 tokenizer can
  be plugged in later if exact counts are ever needed.
- Each chunk: `{chunk_text, contextualized_text=chunk_text, chunk_index,
  chunk_metadata={}, token_count}`.

`full_document` mode: `full_text` populated, `chunks=[]`.

## Data flow

```
Auto-write / Upload & Process
  → describe_preview.generate_description_preview()      (Auto-write)
  → tasks/knowledge_base_processing.process_document()   (Upload & Process)
       (before) → mps_service_key_client.process_document() → services.dograh.com   [removed]
       (after)  → local_document_parser.process_document() → in-process
       ▼
  payload {mode, full_text, chunks[], docling_metadata}
       ├─ describe_preview: text → resolve_kb_llm (v2) → LLM → description
       └─ kb_processing: full_text stored, OR chunks → embeddings(embeddings:80) → pgvector
```

Redis parse-cache (`get_cached_parse`/`set_cached_parse`, keyed by file hash)
is unchanged: Auto-write parses once and caches; Upload & Process reuses it.

## Error handling

- Unsupported extension / unreadable / encrypted PDF → raise. Existing wrappers
  handle it: `describe_preview` → `DescribePreviewError("parse_failed")`
  ("Couldn't read the document…"); `knowledge_base_processing` → document marked
  `failed` with the message. UX unchanged; now reflects local failures.
- Empty extracted text → existing "no text" guard fires; its wording is
  generalized away from "MPS returned no text".

## Testing

- **New** `api/tests/test_local_document_parser.py`:
  - PDF, DOCX, TXT, MD extraction → non-empty `full_text`, correct page count.
  - chunked mode → sequential `chunk_index` from 0, `token_count > 0`,
    `contextualized_text == chunk_text`.
  - full_document mode → `chunks == []`, `full_text` populated.
  - unsupported extension → raises.
  - empty/whitespace document → empty `full_text` handled.
- **Update** existing tests that mock `mps_service_key_client.process_document`
  (`test_describe_preview_service.py`, `test_describe_preview_route.py`) to mock
  `local_document_parser.process_document`.
- **Live check:** Auto-write + Upload & Process a real PDF end-to-end in the
  running stack; confirm a description is produced and chunks are embedded.

## Call-site & build changes

- `api/services/knowledge_base/describe_preview.py`: import + call
  `local_document_parser.process_document` instead of the MPS client.
- `api/tasks/knowledge_base_processing.py`: same swap.
- `api/requirements.txt`: add `pypdf`, `python-docx`.
- Rebuild the devcontainer image; `uv pip install pypdf python-docx` into the
  running venv for immediate testing.
- `ui/src/app/files/DocumentUpload.tsx`: replace the "sent to Speakly's managed
  Model Proxy Service for parsing" notice with copy stating documents are parsed
  locally on the self-hosted server and never leave the user's infrastructure.

## Notes / dependencies

- Builds on the in-flight `merge/1.35.0` branch (1.35.0 merge + the v2
  `resolve_kb_llm` / warmup fixes). KB LLM resolution already routes through the
  v2 effective config; this design only changes the **parse** step.
- pypdf and python-docx are pure-Python, BSD/MIT licensed, no system libraries.
