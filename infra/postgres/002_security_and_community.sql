-- Additions made after the first build: session security, device binding, and the community features
-- (caller reports, personal block list). Every statement is idempotent, so this file is applied on top
-- of 001_schema.sql by the simulator and again at every API start (app/db.py, ensure_additions).

-- ---------------------------------------------------------------- sessions --
-- Every token carries the generation it was issued under. Raising it (when a PIN or password changes, a role
-- changes, or on "sign out everywhere") refuses every token issued before.
ALTER TABLE users       ADD COLUMN IF NOT EXISTS token_gen           integer NOT NULL DEFAULT 0;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS token_gen           integer NOT NULL DEFAULT 0;
-- How many times in a row the account has been locked; each lock lasts twice as long as the last.
ALTER TABLE users       ADD COLUMN IF NOT EXISTS lockouts            integer NOT NULL DEFAULT 0;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS lockouts            integer NOT NULL DEFAULT 0;
ALTER TABLE users       ADD COLUMN IF NOT EXISTS pin_changed_at      timestamptz;
ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS password_changed_at timestamptz;

-- A signed-out session. Kept until the session could no longer have been refreshed.
CREATE TABLE IF NOT EXISTS revoked_sessions (
  sid         text PRIMARY KEY,
  kind        text NOT NULL,
  sub         text NOT NULL,
  reason      text NOT NULL DEFAULT 'logout',
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS revoked_sessions_expires ON revoked_sessions (expires_at);

-- A secret held by one installed copy of the app, enrolled after a PIN check. A Step-Up passed with
-- "biometric" must carry a proof made with it, so a stolen token alone cannot pass (docs/11, section 1.3).
CREATE TABLE IF NOT EXISTS device_keys (
  user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  key_id      text NOT NULL,
  secret      text NOT NULL,
  label       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_used   timestamptz,
  PRIMARY KEY (user_id, key_id)
);

-- ---------------------------------------------------------- caller reports --
-- "This caller was a scammer." verified = the reporter received a call from that number, or the number
-- was in a message they checked that was flagged. Only verified reports can raise a number above risk 1.
CREATE TABLE IF NOT EXISTS caller_reports (
  report_id    uuid PRIMARY KEY,
  tenant_id    text NOT NULL,
  caller_hash  text NOT NULL,
  reporter_id  uuid NOT NULL,
  call_id      uuid,
  decision_id  uuid,
  category     text NOT NULL DEFAULT 'scam' CHECK (category IN ('scam','spam','safe')),
  verified     boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (caller_hash, reporter_id)
);
CREATE INDEX IF NOT EXISTS caller_reports_reporter ON caller_reports (reporter_id, created_at);

-- ------------------------------------------------------ personal block list --
-- "Never pay this UPI ID." unblock_at is set when the payer asks to unblock; the block lifts then, not before.
CREATE TABLE IF NOT EXISTS user_blocks (
  user_id      uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  payee_vpa    text NOT NULL,
  payee_name   text,
  reason       text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  unblock_at   timestamptz,
  PRIMARY KEY (user_id, payee_vpa)
);

CREATE INDEX IF NOT EXISTS audit_log_action    ON audit_log (action, created_at DESC);
