"""Pydantic schemas for the DocCard structured per-document summary."""

from pydantic import BaseModel, Field


class FaqPair(BaseModel):
    """A single Q/A pair extracted from a document."""

    q: str
    a: str


class DocCard(BaseModel):
    """Structured per-document summary produced by the extraction LLM call.

    Steered by the user's description and intended_use at upload time.
    Stored as JSON in knowledge_base_documents.doc_card.
    """

    title: str = Field(..., description="Short, human-readable title")
    summary_150_words: str = Field(..., description="~150-word summary of the doc")
    key_facts: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Free-form category -> list of mentioned entities",
    )
    numbers_and_pricing: list[str] = Field(default_factory=list)
    faqs: list[FaqPair] = Field(default_factory=list)
    suggested_agent_uses: list[str] = Field(default_factory=list)
    topics: list[str] = Field(
        default_factory=list,
        description="3-10 normalized lowercase-English keywords for the org index",
    )
