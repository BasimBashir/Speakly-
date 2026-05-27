"""change embedding dim to 1024

Migration: switch pgvector column from 1536 dims (OpenAI text-embedding-3-small)
to 1024 dims (open-source community standard: BGE-M3, mxbai-embed-large,
BGE-large, OpenAI 3-small/large via Matryoshka dimensions param).

pgvector cannot ALTER a dim-typed column with existing rows, so this drops
and recreates the column. Any existing embeddings are deleted; re-process
documents after running this migration.

Revision ID: a1b2c3d4e5f7
Revises: 4d74436261d7
Create Date: 2026-05-27 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "4d74436261d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safety: refuse to migrate if any chunks exist (would be silently deleted).
    conn = op.get_bind()
    chunk_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM knowledge_base_chunks")
    ).scalar()
    if chunk_count and chunk_count > 0:
        raise RuntimeError(
            f"Refusing to migrate: knowledge_base_chunks has {chunk_count} rows. "
            "Dropping the embedding column would delete them. Delete chunks "
            "manually (or soft-delete the parent documents) before retrying."
        )

    op.drop_index(
        "ix_kb_chunks_embedding_ivfflat",
        table_name="knowledge_base_chunks",
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_column("knowledge_base_chunks", "embedding")
    op.add_column(
        "knowledge_base_chunks",
        sa.Column("embedding", Vector(1024), nullable=True),
    )
    op.create_index(
        "ix_kb_chunks_embedding_ivfflat",
        "knowledge_base_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kb_chunks_embedding_ivfflat",
        table_name="knowledge_base_chunks",
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_column("knowledge_base_chunks", "embedding")
    op.add_column(
        "knowledge_base_chunks",
        sa.Column("embedding", Vector(1536), nullable=True),
    )
    op.create_index(
        "ix_kb_chunks_embedding_ivfflat",
        "knowledge_base_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
