"""On PostgreSQL, make the audit table append-only at the database level too: even someone
with direct SQL access through the app's role can't UPDATE or DELETE audit rows."""

from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION common_audit_event_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'common_audit_event is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS common_audit_event_no_change ON common_audit_event;
CREATE TRIGGER common_audit_event_no_change BEFORE UPDATE OR DELETE ON common_audit_event
    FOR EACH ROW EXECUTE FUNCTION common_audit_event_append_only();
"""
REVERSE = """
DROP TRIGGER IF EXISTS common_audit_event_no_change ON common_audit_event;
DROP FUNCTION IF EXISTS common_audit_event_append_only();
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE)


class Migration(migrations.Migration):
    dependencies = [("common", "0001_audit_event")]
    operations = [migrations.RunPython(forwards, backwards)]
