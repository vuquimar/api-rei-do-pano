"""Add weight to search vector

Revision ID: 9e8e49c4f51e
Revises: 34c46b2b86cc
Create Date: 2024-07-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e8e49c4f51e'
down_revision: Union[str, None] = '34c46b2b86cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Update the trigger function to add weights to the search vector
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

    # Trigger the update for all existing rows to recalculate the search vector
    op.execute("""
        UPDATE products SET "NOMEFANTASIA" = "NOMEFANTASIA";
    """)


def downgrade() -> None:
    # Revert the trigger function to the previous version without weights
    op.execute("""
        CREATE OR REPLACE FUNCTION public.update_search_vector() RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector :=
                to_tsvector('portuguese',
                    public.immutable_unaccent(coalesce(NEW."NOMEFANTASIA", '')) || ' ' ||
                    public.immutable_unaccent(coalesce(NEW.group_description, ''))
                );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    # Trigger the update for all existing rows to recalculate the search vector
    op.execute("""
        UPDATE products SET "NOMEFANTASIA" = "NOMEFANTASIA";
    """)
