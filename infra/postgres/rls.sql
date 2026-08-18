-- Optional Postgres RLS (G30). Not applied by the app.
-- Application predicate in tenancy.py still runs. Enable these policies
-- only after SET LOCAL app.current_tenant is issued on every connection.

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminder_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_outbox ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_users ON users;
CREATE POLICY tenant_users ON users
  USING (tenant_id = current_setting('app.current_tenant', true));

-- tickets / inbox may not have tenant_id; scope via user join in a later migration.
-- This file is a template, not a live cluster change.
