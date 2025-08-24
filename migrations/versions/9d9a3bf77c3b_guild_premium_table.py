"""guild premium table

Revision ID: 9d9a3bf77c3b
Revises: 64cf63a0a835
Create Date: 2025-02-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d9a3bf77c3b'
down_revision: Union[str, Sequence[str], None] = '64cf63a0a835'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'guild_premium',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('guild_id', sa.Integer(), nullable=False),
        sa.Column('tier', sa.String(length=32), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', name='uq_guild_premium')
    )
    op.create_index(op.f('ix_guild_premium_guild_id'), 'guild_premium', ['guild_id'], unique=True)

    # migrate existing premium memberships to guild_premium
    op.execute(
        """
        INSERT INTO guild_premium (guild_id, tier, expires_at, granted_by)
        SELECT guild_id,
               MAX(tier) AS tier,
               MAX(expires_at) AS expires_at,
               MAX(user_id) AS granted_by
        FROM premium_memberships
        GROUP BY guild_id
        """
    )

    op.drop_index(op.f('ix_premium_memberships_user_id'), table_name='premium_memberships')
    op.drop_index(op.f('ix_premium_memberships_guild_id'), table_name='premium_memberships')
    op.drop_table('premium_memberships')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'premium_memberships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('guild_id', sa.Integer(), nullable=False),
        sa.Column('tier', sa.String(length=32), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'guild_id', name='uq_premium_user_guild')
    )
    op.create_index(op.f('ix_premium_memberships_guild_id'), 'premium_memberships', ['guild_id'], unique=False)
    op.create_index(op.f('ix_premium_memberships_user_id'), 'premium_memberships', ['user_id'], unique=False)

    op.execute(
        """
        INSERT INTO premium_memberships (user_id, guild_id, tier, expires_at)
        SELECT COALESCE(granted_by, 0) AS user_id, guild_id, tier, expires_at
        FROM guild_premium
        """
    )

    op.drop_index(op.f('ix_guild_premium_guild_id'), table_name='guild_premium')
    op.drop_table('guild_premium')
