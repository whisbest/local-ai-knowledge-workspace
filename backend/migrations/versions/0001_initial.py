from alembic import op
from app.core import Base
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():Base.metadata.create_all(op.get_bind())
def downgrade():raise RuntimeError('Automatic data deletion is not supported.')
