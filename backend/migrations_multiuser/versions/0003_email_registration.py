"""Verified email registration; existing username accounts remain valid."""
from alembic import op

revision = 'mu0003'
down_revision = 'mu0002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE users ADD COLUMN email VARCHAR(254)')
    op.execute('ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)')
    op.execute('''CREATE TABLE email_challenges (
        email VARCHAR(254) PRIMARY KEY,
        id VARCHAR(32) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        sent_at TIMESTAMP WITH TIME ZONE NOT NULL,
        attempts INTEGER NOT NULL,
        delivered BOOLEAN NOT NULL
    )''')


def downgrade():
    op.execute('DROP TABLE email_challenges')
    op.execute('ALTER TABLE users DROP CONSTRAINT users_email_key')
    op.execute('ALTER TABLE users DROP COLUMN email')
