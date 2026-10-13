from alembic import context
from app.multiuser.db import Base

with context.config.attributes['engine'].connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
