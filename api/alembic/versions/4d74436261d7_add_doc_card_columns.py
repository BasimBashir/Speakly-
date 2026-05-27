"""add doc card columns

Revision ID: 4d74436261d7
Revises: 6bd9f67ec994
Create Date: 2026-05-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4d74436261d7"
down_revision: Union[str, None] = "6bd9f67ec994"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base_documents",
        sa.Column("doc_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "intended_use",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column("user_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column("doc_card", sa.JSON(), nullable=True),
    )
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "doc_card_extracted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # topics is JSONB so we can attach a GIN index for fast containment
    # queries (e.g. topics @> '["billing"]'). PostgreSQL's GIN access method
    # has no default operator class for the json type, only jsonb.
    op.add_column(
        "knowledge_base_documents",
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_kb_documents_topics_gin",
        "knowledge_base_documents",
        ["topics"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kb_documents_topics_gin", table_name="knowledge_base_documents"
    )
    op.drop_column("knowledge_base_documents", "topics")
    op.drop_column("knowledge_base_documents", "doc_card_extracted_at")
    op.drop_column("knowledge_base_documents", "doc_card")
    op.drop_column("knowledge_base_documents", "user_description")
    op.drop_column("knowledge_base_documents", "intended_use")
    op.drop_column("knowledge_base_documents", "doc_type")
