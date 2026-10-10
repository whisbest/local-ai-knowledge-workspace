from alembic import context
from app.core import Base

engine=context.config.attributes['engine']
with engine.connect() as conn:
    context.configure(connection=conn,target_metadata=Base.metadata)
    with context.begin_transaction():context.run_migrations()
