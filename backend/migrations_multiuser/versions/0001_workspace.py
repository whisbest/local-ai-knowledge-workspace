"""Frozen mu0001 DDL; never imports live application model metadata."""
from alembic import op

revision='mu0001'
down_revision=None
branch_labels=None
depends_on=None

DDL = """
CREATE TABLE login_limits (
	key VARCHAR(64) NOT NULL,
	failures INTEGER NOT NULL,
	window_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (key)
)

;

CREATE TABLE platform_state (
	key VARCHAR(40) NOT NULL,
	payload JSONB NOT NULL,
	PRIMARY KEY (key)
)

;

CREATE TABLE users (
	id VARCHAR(32) NOT NULL,
	username VARCHAR(80) NOT NULL,
	password_hash VARCHAR(256) NOT NULL,
	role VARCHAR(10) NOT NULL,
	enabled BOOLEAN NOT NULL,
	must_change BOOLEAN NOT NULL,
	quota JSONB NOT NULL,
	last_claim TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (role IN ('admin','user')),
	UNIQUE (username)
)

;

CREATE TABLE audit_events (
	id BIGSERIAL NOT NULL,
	actor_id VARCHAR(32),
	event VARCHAR(40) NOT NULL,
	object_id VARCHAR(80),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id)
)

;

CREATE TABLE idempotency (
	owner_id VARCHAR(32) NOT NULL,
	operation VARCHAR(80) NOT NULL,
	key VARCHAR(150) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	result JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (owner_id, operation, key),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE model_configs (
	id VARCHAR(32) NOT NULL,
	owner_id VARCHAR(32),
	scope VARCHAR(10) NOT NULL,
	purpose VARCHAR(16) NOT NULL,
	name VARCHAR(100) NOT NULL,
	mode VARCHAR(10) NOT NULL,
	endpoint VARCHAR(2048) NOT NULL,
	model VARCHAR(180) NOT NULL,
	revision VARCHAR(32) NOT NULL,
	enabled BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	dimension INTEGER,
	fingerprint VARCHAR(64),
	secret VARCHAR(4096),
	key_version INTEGER NOT NULL,
	PRIMARY KEY (id),
	CHECK ((scope='platform' AND owner_id IS NULL) OR (scope='personal' AND owner_id IS NOT NULL)),
	CHECK (purpose IN ('chat','embedding')),
	CHECK (mode IN ('api','local')),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE quota_reservations (
	id VARCHAR(32) NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	resource VARCHAR(30) NOT NULL,
	request_id VARCHAR(100) NOT NULL,
	amount BIGINT NOT NULL,
	state VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, resource, request_id),
	CHECK (state IN ('reserved','committed','released','unknown') AND amount >= 0),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE sessions (
	token_hash VARCHAR(64) NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	csrf VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked BOOLEAN NOT NULL,
	PRIMARY KEY (token_hash),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE user_preferences (
	owner_id VARCHAR(32) NOT NULL,
	payload JSONB NOT NULL,
	PRIMARY KEY (owner_id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE records (
	id VARCHAR(32) NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	parent_id VARCHAR(32),
	model_id VARCHAR(32),
	state VARCHAR(24) NOT NULL,
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, parent_id) REFERENCES records (owner_id, id),
	FOREIGN KEY(owner_id) REFERENCES users (id),
	FOREIGN KEY(model_id) REFERENCES model_configs (id)
)

;

CREATE TABLE jobs (
	id VARCHAR(32) NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	target VARCHAR(32) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	state VARCHAR(24) NOT NULL,
	stage VARCHAR(24) NOT NULL,
	done INTEGER NOT NULL,
	total INTEGER NOT NULL,
	attempt INTEGER NOT NULL,
	lease_token VARCHAR(32),
	lease_until TIMESTAMP WITH TIME ZONE,
	heartbeat_at TIMESTAMP WITH TIME ZONE,
	payload JSONB NOT NULL,
	error VARCHAR(300),
	external_started BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, id),
	FOREIGN KEY(owner_id, target) REFERENCES records (owner_id, id),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE stream_events (
	id BIGSERIAL NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	generation_id VARCHAR(32) NOT NULL,
	seq INTEGER NOT NULL,
	kind VARCHAR(20) NOT NULL,
	payload JSONB NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id, generation_id) REFERENCES records (owner_id, id),
	UNIQUE (owner_id, generation_id, seq),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE vectors (
	id VARCHAR(32) NOT NULL,
	owner_id VARCHAR(32) NOT NULL,
	document_id VARCHAR(32) NOT NULL,
	version_id VARCHAR(32) NOT NULL,
	index_id VARCHAR(32) NOT NULL,
	chunk_id VARCHAR(32) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	dimension INTEGER NOT NULL,
	values REAL[] NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id, document_id) REFERENCES records (owner_id, id),
	FOREIGN KEY(owner_id, version_id) REFERENCES records (owner_id, id),
	FOREIGN KEY(owner_id, index_id) REFERENCES records (owner_id, id),
	FOREIGN KEY(owner_id, chunk_id) REFERENCES records (owner_id, id),
	UNIQUE (owner_id, index_id, chunk_id),
	CHECK (dimension > 0 AND cardinality(values) = dimension),
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;
CREATE INDEX ix_model_configs_owner_id ON model_configs (owner_id);
CREATE INDEX ix_quota_reservations_owner_id ON quota_reservations (owner_id);
CREATE INDEX ix_sessions_owner_id ON sessions (owner_id);
CREATE INDEX ix_records_kind ON records (kind);
CREATE INDEX ix_records_owner_id ON records (owner_id);
CREATE INDEX ix_records_parent_id ON records (parent_id);
CREATE INDEX ix_records_state ON records (state);
CREATE INDEX ix_jobs_owner_id ON jobs (owner_id);
CREATE INDEX ix_jobs_state ON jobs (state);
CREATE INDEX ix_jobs_target ON jobs (target);
CREATE INDEX ix_stream_events_generation_id ON stream_events (generation_id);
CREATE INDEX ix_vectors_index_id ON vectors (index_id);
CREATE INDEX ix_vectors_owner_id ON vectors (owner_id);"""

TABLES = ['vectors', 'stream_events', 'jobs', 'records', 'user_preferences', 'sessions', 'quota_reservations', 'model_configs', 'idempotency', 'audit_events', 'users', 'platform_state', 'login_limits']

def upgrade():
    op.execute(DDL)

def downgrade():
    for table in TABLES:op.execute('DROP TABLE '+table)
