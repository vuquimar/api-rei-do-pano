"""add_codes_to_search_vector

Revision ID: d48a6b2c7f9e
Revises: 9e8e49c4f51e
Create Date: 2024-09-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd48a6b2c7f9e'
down_revision: Union[str, None] = '9e8e49c4f51e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Atualiza a função do gatilho para adicionar CODPRD e CODBARRAS ao vetor de busca com peso A
    op.execute("""
        CREATE OR REPLACE FUNCTION public.update_search_vector() RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW."NOMEFANTASIA", ''))), 'A') ||
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW."CODPRD", ''))), 'A') ||
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW."CODBARRAS", ''))), 'A') ||
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW.group_description, ''))), 'C');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Dispara a atualização para todas as linhas existentes para recalcular o vetor de busca
    op.execute("""
        UPDATE products SET "NOMEFANTASIA" = "NOMEFANTASIA";
    """)


def downgrade() -> None:
    # Reverte a função do gatilho para a versão anterior (sem CODPRD e CODBARRAS)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.update_search_vector() RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW."NOMEFANTASIA", ''))), 'A') ||
                setweight(to_tsvector('portuguese', public.immutable_unaccent(coalesce(NEW.group_description, ''))), 'C');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    # Dispara a atualização para todas as linhas existentes para recalcular o vetor de busca
    op.execute("""
        UPDATE products SET "NOMEFANTASIA" = "NOMEFANTASIA";
    """)
