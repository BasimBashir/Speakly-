"""Tests for the local (in-process) document parser."""

import pytest

from api.services.knowledge_base import local_document_parser as parser


def _make_pdf(text: str) -> bytes:
    """Build a minimal valid single-page PDF with extractable text."""
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += b"trailer\n<< /Root 1 0 R /Size " + str(len(objects) + 1).encode() + b" >>\n"
    pdf += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return bytes(pdf)


def _write(tmp_path, name: str, data) -> str:
    path = tmp_path / name
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    return str(path)


# --------------------------------------------------------------------------- #
# Extraction by type
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pdf_full_document(tmp_path):
    path = _write(tmp_path, "doc.pdf", _make_pdf("Local parser test document"))
    result = await parser.process_document(
        file_path=path,
        filename="doc.pdf",
        content_type="application/pdf",
        retrieval_mode="full_document",
    )
    assert result["mode"] == "full_document"
    assert result["chunks"] == []
    assert "Local parser test document" in result["full_text"]
    assert result["docling_metadata"]["pages"] == 1
    assert result["docling_metadata"]["parser"] == "local"


@pytest.mark.asyncio
async def test_docx_full_document(tmp_path):
    from docx import Document

    document = Document()
    document.add_paragraph("Hello from docx.")
    document.add_paragraph("Second paragraph of text.")
    path = str(tmp_path / "doc.docx")
    document.save(path)

    result = await parser.process_document(
        file_path=path,
        filename="doc.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        retrieval_mode="full_document",
    )
    assert "Hello from docx." in result["full_text"]
    assert "Second paragraph" in result["full_text"]


@pytest.mark.asyncio
async def test_txt_and_md_extraction(tmp_path):
    txt = _write(tmp_path, "a.txt", "Plain text body.")
    md = _write(tmp_path, "a.md", "# Heading\n\nMarkdown body.")
    for path, name in ((txt, "a.txt"), (md, "a.md")):
        result = await parser.process_document(
            file_path=path,
            filename=name,
            content_type="text/plain",
            retrieval_mode="full_document",
        )
        assert result["full_text"].strip()


@pytest.mark.asyncio
async def test_unsupported_extension_raises(tmp_path):
    path = _write(tmp_path, "a.xyz", b"binary junk")
    with pytest.raises(ValueError):
        await parser.process_document(
            file_path=path,
            filename="a.xyz",
            content_type="application/octet-stream",
            retrieval_mode="full_document",
        )


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_chunked_mode_produces_sequential_chunks(tmp_path):
    body = "This is sentence one. Here is sentence two about cats. " * 20
    path = _write(tmp_path, "a.txt", body)
    result = await parser.process_document(
        file_path=path,
        filename="a.txt",
        content_type="text/plain",
        retrieval_mode="chunked",
        max_tokens=40,
    )
    assert result["mode"] == "chunked"
    assert result["full_text"] is None
    chunks = result["chunks"]
    assert len(chunks) > 1
    # chunk_index sequential from 0
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c["chunk_text"].strip()
        assert c["token_count"] > 0
        # We skip contextual augmentation — they must be equal.
        assert c["contextualized_text"] == c["chunk_text"]
        assert c["chunk_metadata"] == {}


@pytest.mark.asyncio
async def test_long_sentence_is_hard_split(tmp_path):
    # A single "sentence" (no terminators) much larger than max_tokens.
    body = "word " * 500
    path = _write(tmp_path, "a.txt", body)
    result = await parser.process_document(
        file_path=path,
        filename="a.txt",
        content_type="text/plain",
        retrieval_mode="chunked",
        max_tokens=20,
    )
    # Must not collapse into one oversized chunk.
    assert len(result["chunks"]) > 1


@pytest.mark.asyncio
async def test_empty_document_returns_empty_text(tmp_path):
    path = _write(tmp_path, "a.txt", "   \n  ")
    result = await parser.process_document(
        file_path=path,
        filename="a.txt",
        content_type="text/plain",
        retrieval_mode="chunked",
    )
    # Whitespace-only -> no chunks; the caller's "no text" guard handles UX.
    assert result["chunks"] == []
