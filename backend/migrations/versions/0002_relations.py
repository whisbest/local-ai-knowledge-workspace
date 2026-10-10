from alembic import op
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    # Triggers validate logical types in addition to deferred foreign key edges.
    for operation in ('INSERT','UPDATE'):
        op.execute(f"""CREATE TRIGGER record_parent_{operation.lower()} BEFORE {operation} ON records
        WHEN NEW.kind IN ('document','version','chunk','index','message','generation','source')
        BEGIN
        SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM records p WHERE p.id=NEW.parent_id AND p.kind=
        CASE NEW.kind WHEN 'document' THEN 'kb' WHEN 'version' THEN 'document' WHEN 'chunk' THEN 'version'
        WHEN 'index' THEN 'document' WHEN 'message' THEN 'conversation' WHEN 'generation' THEN 'conversation'
        WHEN 'source' THEN 'generation' END) THEN RAISE(ABORT,'Invalid record parent') END;
        END""")
    op.execute("""CREATE TABLE record_edges (
        child_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        parent_id TEXT NOT NULL REFERENCES records(id) DEFERRABLE INITIALLY DEFERRED)""")
    op.execute('INSERT INTO record_edges SELECT id,parent_id FROM records WHERE parent_id IS NOT NULL')
    op.execute("""CREATE TRIGGER record_edge_insert AFTER INSERT ON records WHEN NEW.parent_id IS NOT NULL
        BEGIN INSERT INTO record_edges VALUES (NEW.id,NEW.parent_id); END""")
    op.execute("""CREATE TRIGGER record_edge_update AFTER UPDATE OF parent_id ON records
        BEGIN DELETE FROM record_edges WHERE child_id=NEW.id;
        INSERT INTO record_edges SELECT NEW.id,NEW.parent_id WHERE NEW.parent_id IS NOT NULL; END""")
    for operation in ('INSERT','UPDATE'):
        op.execute(f"""CREATE TRIGGER vector_relation_{operation.lower()} BEFORE {operation} ON vectors BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM records WHERE id=NEW.document_id AND kind='document')
        OR NOT EXISTS(SELECT 1 FROM records WHERE id=NEW.version_id AND kind='version' AND parent_id=NEW.document_id)
        OR NOT EXISTS(SELECT 1 FROM records WHERE id=NEW.generation_id AND kind='index' AND parent_id=NEW.document_id)
        OR NOT EXISTS(SELECT 1 FROM records WHERE id=NEW.chunk_id AND kind='chunk' AND parent_id=NEW.version_id)
        THEN RAISE(ABORT,'Invalid vector relationship') END; END""")

def downgrade():raise RuntimeError('Automatic data deletion is not supported.')
