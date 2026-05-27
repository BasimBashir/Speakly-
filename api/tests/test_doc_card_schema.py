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
