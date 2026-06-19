"""Local, in-process document parsing for the knowledge base.

Drop-in replacement for ``mps_service_key_client.process_document`` that parses
PDF / DOCX / TXT / MD on the local machine instead of delegating to the remote
managed Model Proxy Service. Returns the exact same payload shape so the
describe-preview and ingestion consumers are unchanged:

    {
      "mode": "chunked" | "full_document",
      "docling_metadata": {...},
      "full_text": str | None,   # full_document mode
      "chunks": [                # chunked mode
        {"chunk_text", "contextualized_text", "chunk_index",
         "chunk_metadata", "token_count"}, ...
      ],
    }

Text-based documents only — no OCR. Extraction + chunking are CPU-bound and run
in a worker thread so the event loop is never blocked.
"""

import asyncio
import math
import os
import re
from typing import Optional

from loguru import logger

PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}

# ~4 characters per token is a standard rough estimate; deliberately avoids a
# model download so parsing stays fully offline. Chunk sizes are approximate,
# which is fine for retrieval. Swap in a real tokenizer here if exact counts
# are ever needed.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _extract_pdf(file_path: str) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            # Try the common empty-password case; bail clearly otherwise.
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    "PDF is encrypted and could not be opened"
                ) from exc
        parts = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(parts), len(reader.pages)
    except PdfReadError as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc


def _extract_docx(file_path: str) -> str:
    from docx import Document

    document = Document(file_path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _extract_text_file(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _extract(file_path: str, filename: str, content_type: str) -> tuple[str, dict]:
    """Return (full_text, metadata) for the document, dispatching by type."""
    ext = os.path.splitext(filename)[1].lower()
    ctype = (content_type or "").lower()

    pages: Optional[int] = None
    if ext in PDF_EXTENSIONS or ctype == "application/pdf":
        full_text, pages = _extract_pdf(file_path)
    elif ext in DOCX_EXTENSIONS or "wordprocessingml" in ctype:
        full_text = _extract_docx(file_path)
    elif ext in TEXT_EXTENSIONS or ctype.startswith("text/"):
        full_text = _extract_text_file(file_path)
    else:
        raise ValueError(
            f"Unsupported document type: {ext or content_type or 'unknown'}"
        )

    metadata = {
        "parser": "local",
        "filename": filename,
        "pages": pages,
        "char_count": len(full_text),
    }
    return full_text, metadata


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
_SENTENCE_FALLBACK_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Sentence-split with nltk punkt; fall back to a regex if unavailable."""
    try:
        import nltk

        return [s for s in nltk.sent_tokenize(text) if s.strip()]
    except Exception as exc:  # noqa: BLE001 — LookupError, import errors, etc.
        logger.debug(f"nltk sentence split unavailable ({exc}); using regex fallback")
        return [s for s in _SENTENCE_FALLBACK_RE.split(text) if s.strip()]


def _hard_split_long_sentence(sentence: str, max_tokens: int) -> list[str]:
    """Word-window split for a single sentence that exceeds max_tokens."""
    words = sentence.split()
    if not words:
        return []
    max_chars = max_tokens * _CHARS_PER_TOKEN
    pieces: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        add = len(word) + (1 if current else 0)
        if current and length + add > max_chars:
            pieces.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += add
    if current:
        pieces.append(" ".join(current))
    return pieces


def _chunk_text(
    text: str, max_tokens: int, overlap_tokens: int
) -> list[str]:
    """Greedily group sentences into ~max_tokens chunks with optional overlap."""
    sentences: list[str] = []
    for sentence in _split_sentences(text):
        if _estimate_tokens(sentence) > max_tokens:
            sentences.extend(_hard_split_long_sentence(sentence, max_tokens))
        else:
            sentences.append(sentence)

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = _estimate_tokens(sentence)
        if current and current_tokens + sentence_tokens > max_tokens:
            chunks.append(" ".join(current))
            if overlap_tokens > 0:
                carried: list[str] = []
                carried_tokens = 0
                for prev in reversed(current):
                    carried.insert(0, prev)
                    carried_tokens += _estimate_tokens(prev)
                    if carried_tokens >= overlap_tokens:
                        break
                current = carried
                current_tokens = carried_tokens
            else:
                current = []
                current_tokens = 0
        current.append(sentence)
        current_tokens += sentence_tokens

    if current:
        chunks.append(" ".join(current))
    return chunks


# --------------------------------------------------------------------------- #
# Public entry point (mirrors mps_service_key_client.process_document)
# --------------------------------------------------------------------------- #
def _process_document_sync(
    file_path: str,
    filename: str,
    content_type: str,
    retrieval_mode: str,
    max_tokens: int,
    chunk_overlap_tokens: int,
) -> dict:
    full_text, metadata = _extract(file_path, filename, content_type)

    if retrieval_mode == "full_document":
        return {
            "mode": "full_document",
            "docling_metadata": metadata,
            "full_text": full_text,
            "chunks": [],
        }

    chunk_texts = _chunk_text(full_text, max_tokens, chunk_overlap_tokens)
    chunks = [
        {
            "chunk_text": chunk_text,
            "contextualized_text": chunk_text,
            "chunk_index": index,
            "chunk_metadata": {},
            "token_count": _estimate_tokens(chunk_text),
        }
        for index, chunk_text in enumerate(chunk_texts)
    ]
    return {
        "mode": "chunked",
        "docling_metadata": {**metadata, "total_chunks": len(chunks)},
        "full_text": None,
        "chunks": chunks,
    }


async def process_document(
    *,
    file_path: str,
    filename: str,
    content_type: str,
    retrieval_mode: str = "chunked",
    max_tokens: int = 128,
    chunk_overlap_tokens: int = 0,
    merge_peers: bool = True,
    tokenizer_model: Optional[str] = None,
    organization_id: Optional[int] = None,
    created_by: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> dict:
    """Parse a document locally and return the MPS-compatible payload.

    ``merge_peers``/``tokenizer_model``/``organization_id``/``created_by``/
    ``correlation_id`` are accepted for signature parity with the managed MPS
    client but are not needed for local parsing.
    """
    payload = await asyncio.to_thread(
        _process_document_sync,
        file_path,
        filename,
        content_type,
        retrieval_mode,
        max_tokens,
        chunk_overlap_tokens,
    )
    logger.info(
        f"Local parse of {filename!r} (mode={retrieval_mode}): "
        f"{payload['docling_metadata'].get('char_count', 0)} chars, "
        f"{len(payload['chunks'])} chunks"
    )
    return payload
