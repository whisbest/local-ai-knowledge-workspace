from alembic import op
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.execute('CREATE TABLE records (\n\tid VARCHAR NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tparent_id VARCHAR, \n\tstate VARCHAR NOT NULL, \n\tpayload TEXT NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tupdated_at VARCHAR NOT NULL, \n\tPRIMARY KEY (id)\n)')
    op.execute('CREATE TABLE jobs (\n\tid VARCHAR NOT NULL, \n\tkind VARCHAR NOT NULL, \n\ttarget VARCHAR NOT NULL, \n\tstate VARCHAR NOT NULL, \n\tstage VARCHAR NOT NULL, \n\tdone INTEGER NOT NULL, \n\ttotal INTEGER NOT NULL, \n\tattempt INTEGER NOT NULL, \n\terror TEXT, \n\tlease VARCHAR, \n\tpayload TEXT NOT NULL, \n\tcreated_at VARCHAR NOT NULL, \n\tupdated_at VARCHAR NOT NULL, \n\tPRIMARY KEY (id)\n)')
    op.execute('CREATE TABLE idempotency (\n\t"key" VARCHAR NOT NULL, \n\tresult TEXT NOT NULL, \n\tPRIMARY KEY ("key")\n)')
    op.execute('CREATE TABLE vectors (\n\tid VARCHAR NOT NULL, \n\tdocument_id VARCHAR NOT NULL, \n\tversion_id VARCHAR NOT NULL, \n\tgeneration_id VARCHAR NOT NULL, \n\tfingerprint VARCHAR NOT NULL, \n\tdimension INTEGER NOT NULL, \n\tchunk_id VARCHAR NOT NULL, \n\t"values" TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (chunk_id)\n)')
    op.execute('CREATE TABLE stream_events (\n\tid INTEGER NOT NULL, \n\tgeneration_id VARCHAR NOT NULL, \n\tseq INTEGER NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tpayload TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (generation_id, seq)\n)')
    op.execute('CREATE INDEX ix_records_parent_id ON records (parent_id)')
    op.execute('CREATE INDEX ix_records_kind ON records (kind)')
    op.execute('CREATE INDEX ix_records_state ON records (state)')
    op.execute('CREATE INDEX ix_jobs_target ON jobs (target)')
    op.execute('CREATE INDEX ix_jobs_state ON jobs (state)')
    op.execute('CREATE INDEX ix_vectors_document_id ON vectors (document_id)')
    op.execute('CREATE INDEX ix_vectors_version_id ON vectors (version_id)')
    op.execute('CREATE INDEX ix_vectors_generation_id ON vectors (generation_id)')
    op.execute('CREATE INDEX ix_vectors_fingerprint ON vectors (fingerprint)')
    op.execute('CREATE INDEX ix_stream_events_generation_id ON stream_events (generation_id)')

def downgrade():raise RuntimeError('Automatic data deletion is not supported.')
