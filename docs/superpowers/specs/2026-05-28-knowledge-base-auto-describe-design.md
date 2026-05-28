# Knowledge-Base Auto-Describe — Design

**Status:** Drafted 2026-05-28. Awaiting user review.
**Owner:** Basim Bashir
**Related:** [2026-05-27 per-doc summaries design](./2026-05-27-per-doc-summaries-design.md)

## 1. Problem

When a user uploads a document to the knowledge base (`ui/src/app/files/DocumentUpload.tsx`), the **"Describe this document"** textarea is a hard requirement (≥20 chars) and is the input that downstream code uses to ground the doc-card extractor (`api/services/knowledge_base/doc_card_extraction.py`). Users find this slow and skip-prone — they don't know what to write.

Add a button next to the description label that asks the LLM to draft this description from the document content. The user reviews/edits the draft before clicking **Upload & Process**.

## 2. Goals & non-goals

**Goals**

- One-click description generation after file selection.
- Draft is editable; the user remains the author of record.
- Avoid double-parsing the file: parse for preview, reuse on actual upload.

**Non-goals**

- No change to retrieval-mode selection (Full Document / Chunked Search).
- No new analytics / quota / billing surfaces.
- No streaming UI — single short LLM call.
- No edit history / undo for the textarea content (re-rolling overwrites by design).

## 3. UX

In `DocumentUpload.tsx`, the "Describe this document" block becomes:

```
[Describe this document *]              [✨ Auto-write]
[textarea ........................................]
[                                         18/20 min characters]
```

Button behavior:

- Disabled until a file is selected. Not gated on `doc_type` / `intended_use` (those are sent if filled; the prompt tolerates absence).
- While in-flight: spinner icon + "Generating…" label, button disabled.
- On success: textarea is replaced with the draft; toast "Description generated"; button label becomes "Regenerate" so the second-click intent is explicit.
- On failure: toast the human-readable error, textarea untouched.

Existing validations (file type, ≤5 MB, ≥20 chars to enable Upload & Process) are unchanged.

## 4. Architecture

### 4.1 New backend endpoint

`POST /api/v1/knowledge-base/describe-preview`

- **Auth:** authenticated user; resolves `user_id` for LLM config lookup. No org row is created.
- **Request:** multipart/form-data
  - `file` — bytes (same accepted types as `/upload-url`: `.pdf .docx .doc .txt .json`, ≤5 MB)
  - `doc_type` — optional string (one of `contract|policy|pricing|faq|script|other`)
  - `intended_use` — optional list `["inbound"]` / `["outbound"]` / both
- **Response (200):**
  ```json
  { "description": "string", "from_cache": false }
  ```
- **Errors:**
  - `400` — invalid file type or size
  - `502` — MPS parse failed OR LLM call failed; body includes `detail` for the toast

### 4.2 Server-side flow

```
file upload
  │
  ├─ validate (extension, size)               # reject 400
  ├─ compute sha256(bytes)  → file_hash
  │
  ├─ Redis GET kb:parse:{file_hash}
  │     hit  → use cached `parsed_text`       # no MPS call
  │     miss → call mps_service_key_client.process_document(...)
  │            → store result in Redis        # TTL 30min
  │            → set `parsed_text`
  │
  ├─ build prompt(parsed_text, doc_type, intended_use)
  ├─ resolve LLM via _resolve_extraction_llm(user_id)   # same fallback as doc-card
  ├─ run LLM (single call) → raw_text
  ├─ post-process: strip + truncate to 600 chars
  └─ return { description, from_cache }
```

### 4.3 Parse cache

- **Backend:** Redis (already deployed for ARQ).
- **Key:** `kb:parse:{file_sha256}` — global, not org-scoped. Document content equality is what we care about; access control lives at the upload endpoint, not at the cache.
- **Value:** JSON blob with the MPS response we'd otherwise re-fetch — `{ "full_text": "...", "chunks": [...], "docling_metadata": {...} }`.
- **TTL:** 30 minutes from last write. Long enough for the user to type their description; short enough to bound Redis footprint.
- **Worker reuse:** `process_knowledge_base_document` (`api/tasks/knowledge_base_processing.py:50`) gets a new step: after computing `file_hash`, check the cache. On hit, skip the `mps_service_key_client.process_document` call and use the cached payload directly. On successful reuse, `DEL` the key so a second upload of the same content is parsed fresh.
- **Failure:** Redis errors are logged and swallowed; the request behaves as a cache miss. Cache is best-effort.

### 4.4 LLM config resolution

Reuse `_resolve_extraction_llm(user_id)` from `api/services/knowledge_base/doc_card_extraction.py:205` (or extract it to a shared helper if cleaner — implementation-plan decision). Same fallback chain:

1. User's `user_configuration.llm` → `provider/model/api_key/kwargs`.
2. If absent → Dograh MPS default tier (`KB_DOC_CARD_MODEL_TIER` env, defaults to `"default"`).

This means the local-models (`speaches`) provider is supported out of the box because the resolver already handles `base_url` for speaches/openrouter.

## 5. Prompt strategy & evaluation

The prompt that ships is **TBD pending the experiment below**. The endpoint will hard-code the winning prompt; no per-request prompt selection.

### 5.1 Evaluation harness

Script: `api/services/knowledge_base/describe_prompt_eval.py`. Run from project root:

```bash
source venv/bin/activate
set -a && source api/.env && set +a
python -m api.services.knowledge_base.describe_prompt_eval
```

- Loads pre-parsed text fixtures from `api/tests/fixtures/describe_eval/`.
- For each `(strategy, doc, run)` triple, calls the LLM with `temperature=0.7`.
- Writes a markdown report to `docs/superpowers/specs/2026-05-28-describe-eval-results.md` with input doc, strategy, run number, raw output, and a final recommendation section.

### 5.2 Test corpus

Three docs (3 × 3 strategies × 3 runs = 27 samples):

1. **`faq_small.txt`** — synthetic, ~10 Q&As about a fictional product. Tests the "small reference doc" shape.
2. **`policy_mid.txt`** — synthetic refund/returns policy, ~2 pages. Tests structured prose.
3. **`legal_long.txt`** — text extracted from the user-supplied legal PDF (`Peramune-v-Savage-Garage-NZ-Ltd-2024-NZCA-512.pdf`). Tests long-doc truncation.

Pre-parsed once via MPS and committed as text so the eval script does no MPS calls.

### 5.3 Strategies under test

- **S1 — Direct single-call.** One LLM call. System prompt frames the model as a "knowledge-base annotator for voice agents". User prompt = `doc_type` + `intended_use` chips + the doc text (truncated at ~12k chars) + instruction: *"Write a 2–3 sentence description (≥20 chars) explaining what this document contains and how a voice agent should use it. Plain prose, second-person, no JSON."*

- **S2 — Two-step structured → narrative.** Call #1 emits JSON `{topic, audience, agent_use_hint, key_entities}`. Call #2 takes that JSON and writes prose. More tokens, more constrained drift across runs.

- **S3 — Few-shot in-context.** Single call. Prompt embeds 2 hand-written exemplars of "good" descriptions for an FAQ and a policy doc, plus the user's `doc_type`/`intended_use`. Biases toward consistent style.

### 5.4 Scoring

I (Claude) score each sample 1–5 on:

- **(a) Faithfulness** — does it stay grounded in the document?
- **(b) Usefulness for an agent** — does it tell the agent *when* to consult this doc?
- **(c) Length discipline** — falls in 40–80 words.
- **(d) Cross-run consistency** — three runs of the same strategy on the same doc shouldn't read like three different documents.

Mean per strategy → recommendation with one sentence of reasoning per strategy. **User confirms or overrides** before the winning prompt is hard-coded.

## 6. Frontend changes

**File:** `ui/src/app/files/DocumentUpload.tsx`

- New state: `isGeneratingDescription: boolean`, `descriptionGenerated: boolean`.
- After `npm run generate-client`, new SDK function `describePreviewApiV1KnowledgeBaseDescribePreviewPost` becomes available.
- New handler `handleAutoDescribe`:
  - Guard: requires `selectedFile`.
  - Sends `{ file, doc_type, intended_use }` via multipart.
  - On 2xx: `setUserDescription(response.description)`, `setDescriptionGenerated(true)`, toast.
  - On error: toast with `error.detail` (or fallback), state unchanged.
- New button rendered in the description block above the textarea — see Section 3 mockup. Uses `Sparkles` lucide icon for the idle state, `Loader2` (spin) while in-flight.

Existing description-length validator is unchanged. `clearSelectedFile` also resets `descriptionGenerated`.

## 7. Error handling

| Failure mode                  | Server response                  | UI behavior                                                                     |
|-------------------------------|----------------------------------|---------------------------------------------------------------------------------|
| Invalid file (type/size)      | `400` `{detail}`                 | Toast `detail`; button enabled again                                            |
| MPS parse failed              | `502` `detail: "parse_failed"`   | Toast: "Couldn't read the document. Try writing a description manually."        |
| LLM call timed out / no creds | `502` `detail: "llm_failed"`     | Toast: "Auto-describe failed — please write one yourself."                      |
| Redis unavailable             | n/a (logged, treated as miss)    | No user-visible effect; preview just re-parses + re-LLMs next time              |

LLM credentials missing for the user **and** no Dograh MPS fallback configured → 502 `llm_failed`. Same UX as any other LLM failure.

## 8. Testing

- `api/tests/test_describe_preview_route.py`
  - 400 on bad file type
  - 400 on >5 MB
  - Cache miss → calls MPS once, stores in cache, returns description (MPS + LLM mocked)
  - Cache hit → does NOT call MPS, returns description (MPS mocked to assert no call)
  - LLM failure → 502 `llm_failed`
  - MPS failure → 502 `parse_failed`
- `api/tests/test_knowledge_base_parse_cache.py`
  - set/get/delete round-trip
  - Worker (`process_knowledge_base_document`) reuses cache entry when present, then deletes it
  - Worker falls back to MPS when cache key absent
- Manual UI smoke check (documented in PR description):
  1. Pick a doc → click Auto-write → see textarea populate.
  2. Click Upload & Process → see doc reach `completed`.
  3. Verify worker logs show "reusing cached parse" line on this upload.

## 9. Open questions for the implementation plan

- Does `mps_service_key_client.process_document` expose a "text-only, skip chunking" mode? If yes, the preview path uses it to save MPS work; if not, we use the normal call and cache its full response. **Resolution:** check `api/services/mps_service_key_client.py` before writing the cache-storage code.
- Move `_resolve_extraction_llm` to a shared helper, or keep duplicated? **Resolution:** if the two callsites are the only ones, extract to `api/services/knowledge_base/llm_resolution.py` during implementation.

## 10. Out-of-scope (explicit YAGNI list)

- Streaming token output to the UI.
- A "describe history" or undo stack on the textarea.
- Per-user / per-org quotas on auto-describe calls.
- Auto-describe at any other entry point (URL ingest, paste-text, etc.).
- Re-describing existing documents from the doc-list view.
