"""Enforce private model binding at the database boundary."""
from alembic import op

revision='mu0002'
down_revision='mu0001'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE FUNCTION validate_record_model_owner() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.model_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM model_configs WHERE id=NEW.model_id
          AND (scope='platform' OR owner_id=NEW.owner_id)) THEN
        RAISE EXCEPTION 'Model ownership mismatch' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$''')
    op.execute('''CREATE TRIGGER record_model_owner BEFORE INSERT OR UPDATE ON records
                  FOR EACH ROW EXECUTE FUNCTION validate_record_model_owner()''')


def downgrade():
    op.execute('DROP TRIGGER record_model_owner ON records')
    op.execute('DROP FUNCTION validate_record_model_owner()')
