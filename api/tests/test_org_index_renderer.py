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
