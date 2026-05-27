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
