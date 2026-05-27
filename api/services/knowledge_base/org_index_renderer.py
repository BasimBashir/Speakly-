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
