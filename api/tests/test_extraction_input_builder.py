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
