-- UPI Inspector schema. See docs/05-data-model.md.

-- 1. Tenancy and people ------------------------------------------------------
CREATE TABLE tenants (
  tenant_id      text PRIMARY KEY,
  name           text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  user_id         uuid PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants,
  display_name    text NOT NULL,
  phone_hash      text UNIQUE NOT NULL,
  phone_last4     text NOT NULL DEFAULT '',
  pin_hash        text,
  primary_vpa     text NOT NULL,
  segment         text NOT NULL,
  age_band        text NOT NULL,
  locale          text NOT NULL DEFAULT 'en',
  voice_warnings  boolean NOT NULL DEFAULT true,
  large_text      boolean NOT NULL DEFAULT false,
  balance         numeric(14,2) NOT NULL DEFAULT 0,
  seed_balance    numeric(14,2) NOT NULL DEFAULT 0,
  is_demo         boolean NOT NULL DEFAULT false,
  is_synthetic    boolean NOT NULL DEFAULT false,
  active          boolean NOT NULL DEFAULT true,
  failed_attempts integer NOT NULL DEFAULT 0,
  locked_until    timestamptz,
  first_seen      timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON users (tenant_id);

CREATE TABLE staff_users (
  staff_id        uuid PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants,
  display_name    text NOT NULL,
  email           text UNIQUE NOT NULL,
  password_hash   text NOT NULL,
  role            text NOT NULL CHECK (role IN ('admin','analyst','auditor')),
  active          boolean NOT NULL DEFAULT true,
  failed_attempts integer NOT NULL DEFAULT 0,
  locked_until    timestamptz,
  last_login_at   timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  device_id      uuid PRIMARY KEY,
  user_id        uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  fingerprint    text NOT NULL,
  first_seen     timestamptz NOT NULL,
  last_seen      timestamptz NOT NULL
);
CREATE INDEX ON devices (user_id);

-- 2. Payees ------------------------------------------------------------------
CREATE TABLE vpas (
  vpa_id          text PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants,
  registered_name text NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('person','merchant')),
  verified        boolean NOT NULL DEFAULT false,
  owner_user_id   uuid REFERENCES users ON DELETE SET NULL,
  on_denylist     boolean NOT NULL DEFAULT false,
  is_synthetic    boolean NOT NULL DEFAULT false,
  first_seen      timestamptz NOT NULL
);

CREATE TABLE vpa_devices (
  vpa_id         text NOT NULL REFERENCES vpas ON DELETE CASCADE,
  fingerprint    text NOT NULL,
  PRIMARY KEY (vpa_id, fingerprint)
);

CREATE TABLE vpa_reputation (
  tenant_id      text NOT NULL REFERENCES tenants,
  vpa_id         text NOT NULL REFERENCES vpas ON DELETE CASCADE,
  recv_decayed   double precision NOT NULL DEFAULT 0,
  bad_decayed    double precision NOT NULL DEFAULT 0,
  lifetime_recv  integer NOT NULL DEFAULT 0,
  reports_30d    integer NOT NULL DEFAULT 0,
  confirmed_90d  integer NOT NULL DEFAULT 0,
  near_miss_14d  integer NOT NULL DEFAULT 0,
  fan_in_1h      integer NOT NULL DEFAULT 0,
  fan_in_24h     integer NOT NULL DEFAULT 0,
  first_time_payer_ratio_24h double precision NOT NULL DEFAULT 0,
  p_bad          double precision NOT NULL DEFAULT 0.01,
  maturity       double precision NOT NULL DEFAULT 0,
  trust_score    smallint NOT NULL DEFAULT 45,
  band           text NOT NULL DEFAULT 'NO_HISTORY',
  band_reason    text,
  counters_at    timestamptz NOT NULL,
  updated_at     timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, vpa_id)
);

-- event log behind the windowed counts (reports, confirmed fraud, near-misses)
CREATE TABLE vpa_events (
  id             bigserial PRIMARY KEY,
  tenant_id      text NOT NULL,
  vpa_id         text NOT NULL,
  kind           text NOT NULL,            -- report | confirmed | nearmiss | flagged_message
  weight         double precision NOT NULL,
  ref            text,
  at             timestamptz NOT NULL
);
CREATE INDEX ON vpa_events (tenant_id, vpa_id, at);

CREATE TABLE vpa_reputation_history (
  tenant_id      text NOT NULL,
  vpa_id         text NOT NULL,
  at             timestamptz NOT NULL,
  trust_score    smallint NOT NULL,
  band           text NOT NULL,
  cause          text,
  PRIMARY KEY (tenant_id, vpa_id, at)
);

-- seed copy used by demo-reset
CREATE TABLE vpa_reputation_seed (LIKE vpa_reputation INCLUDING ALL);

CREATE TABLE graph_features (
  vpa_id                      text PRIMARY KEY,
  downstream_hop              smallint NOT NULL DEFAULT 9,
  shares_device_with_reported boolean NOT NULL DEFAULT false,
  passthrough_ratio           double precision NOT NULL DEFAULT 0,
  community_id                integer,
  community_risk_share        double precision NOT NULL DEFAULT 0,
  computed_at                 timestamptz NOT NULL
);

CREATE TABLE consortium_signals (
  vpa_token      text NOT NULL,
  source_tenant  text NOT NULL REFERENCES tenants,
  bad_decayed    double precision NOT NULL,
  recv_decayed   double precision NOT NULL,
  p_bad          double precision NOT NULL DEFAULT 0,
  band           text NOT NULL,
  confirmed      boolean NOT NULL DEFAULT false,
  updated_at     timestamptz NOT NULL,
  PRIMARY KEY (vpa_token, source_tenant)
);

CREATE TABLE flagged_indicators (
  tenant_id      text NOT NULL REFERENCES tenants,
  indicator_type text NOT NULL,
  value          text NOT NULL,
  reporter_count integer NOT NULL DEFAULT 0,
  first_seen     timestamptz NOT NULL,
  last_seen      timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, indicator_type, value)
);

CREATE TABLE flagged_indicator_reporters (
  tenant_id      text NOT NULL,
  indicator_type text NOT NULL,
  value          text NOT NULL,
  user_id        uuid NOT NULL,
  PRIMARY KEY (tenant_id, indicator_type, value, user_id)
);

-- 3. Payments, decisions, outcomes ---------------------------------------------
CREATE TABLE transactions (
  txn_id             text PRIMARY KEY,
  tenant_id          text NOT NULL,
  user_id            uuid NOT NULL,
  payer_vpa          text NOT NULL,
  payee_vpa          text NOT NULL,
  claimed_name       text,
  amount             numeric(14,2) NOT NULL,
  note               text,
  device_id          uuid,
  session            jsonb NOT NULL DEFAULT '{}',
  parent_decision_id uuid,
  status             text NOT NULL DEFAULT 'PENDING',   -- PENDING | SUCCESS | FAILED | NOT_ATTEMPTED
  truth_is_fraud     boolean,
  truth_family       text,
  campaign_id        text,
  is_synthetic       boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL
);
CREATE INDEX ON transactions (payee_vpa, created_at);
CREATE INDEX ON transactions (user_id, created_at);
CREATE INDEX ON transactions (created_at);

CREATE TABLE risk_decisions (
  decision_id    uuid PRIMARY KEY,
  txn_id         text UNIQUE NOT NULL,
  tenant_id      text NOT NULL,
  user_id        uuid NOT NULL,
  payee_vpa      text NOT NULL,
  amount         numeric(14,2) NOT NULL,
  score          double precision NOT NULL,
  tier           text NOT NULL,
  action         text NOT NULL,
  enforced       boolean NOT NULL DEFAULT true,
  reason_codes   text[] NOT NULL DEFAULT '{}',
  reasons        jsonb NOT NULL DEFAULT '[]',
  rules_fired    text[] NOT NULL DEFAULT '{}',
  contributions  jsonb NOT NULL DEFAULT '{}',
  scam_family    text,
  family_sim     double precision,
  features       jsonb NOT NULL,
  detail         jsonb NOT NULL DEFAULT '{}',   -- payee snapshot, score parts, guardian, hold, step-up ids
  model_version  text NOT NULL,
  rules_version  text NOT NULL,
  degraded       boolean NOT NULL DEFAULT false,
  latency_ms     integer NOT NULL,
  stage_ms       jsonb NOT NULL DEFAULT '{}',
  is_synthetic   boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON risk_decisions (tenant_id, created_at DESC);
CREATE INDEX ON risk_decisions (tier, created_at DESC);
CREATE INDEX ON risk_decisions (payee_vpa, created_at DESC);
CREATE INDEX ON risk_decisions (user_id, created_at DESC);

CREATE TABLE outcomes (
  decision_id      uuid PRIMARY KEY,
  user_action      text NOT NULL,
  final_status     text,
  is_near_miss     boolean NOT NULL DEFAULT false,
  near_miss_weight double precision NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE decision_labels (
  decision_id    uuid PRIMARY KEY,
  label          text NOT NULL CHECK (label IN ('FRAUD','LEGITIMATE')),
  family         text,
  note           text,
  labelled_by    uuid,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- 4. Signals -------------------------------------------------------------------
CREATE TABLE call_events (
  call_id        uuid PRIMARY KEY,
  callee_hash    text NOT NULL,
  caller_hash    text NOT NULL,
  caller_risk    smallint NOT NULL,
  caller_known   boolean NOT NULL,
  started_at     timestamptz NOT NULL,
  ended_at       timestamptz,
  source         text NOT NULL
);
CREATE INDEX ON call_events (callee_hash, started_at DESC);

CREATE TABLE caller_risk_list (
  caller_hash    text PRIMARY KEY,
  risk           smallint NOT NULL,
  source         text NOT NULL,
  updated_at     timestamptz NOT NULL
);

CREATE TABLE message_checks (
  check_id       uuid PRIMARY KEY,
  user_id        uuid NOT NULL,
  tenant_id      text NOT NULL,
  text           text NOT NULL,
  text_hash      text NOT NULL,
  label          text NOT NULL,
  scam_prob      double precision NOT NULL,
  verdict        text NOT NULL,
  family         text,
  entities       jsonb NOT NULL DEFAULT '{}',
  red_flags      jsonb NOT NULL DEFAULT '[]',
  class_probs    jsonb NOT NULL DEFAULT '{}',
  flag_set       boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON message_checks (user_id, created_at DESC);

CREATE TABLE signal_links (
  decision_id    uuid NOT NULL,
  signal_type    text NOT NULL,
  signal_id      uuid NOT NULL,
  PRIMARY KEY (decision_id, signal_type, signal_id)
);

CREATE TABLE fraud_reports (
  report_id      uuid PRIMARY KEY,
  tenant_id      text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('FRAUD','FALSE_POSITIVE')),
  decision_id    uuid,
  txn_id         text,
  payee_vpa      text,
  reporter_id    uuid,
  comment        text,
  status         text NOT NULL DEFAULT 'OPEN',
  family         text,
  resolved_by    uuid,
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON fraud_reports (tenant_id, status, created_at);

-- 5. Protective features -------------------------------------------------------
CREATE TABLE stepup_challenges (
  challenge_id   uuid PRIMARY KEY,
  decision_id    uuid NOT NULL,
  question_code  text NOT NULL,
  risky_answer   text NOT NULL,
  cooloff_until  timestamptz NOT NULL,
  auth_method    text,
  status         text NOT NULL DEFAULT 'OPEN',
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE guardians (
  guardian_link_id     uuid PRIMARY KEY,
  user_id              uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  guardian_user_id     uuid NOT NULL REFERENCES users ON DELETE CASCADE,
  mode                 text NOT NULL CHECK (mode IN ('notify','co_approve')),
  amount_threshold     numeric(14,2),
  status               text NOT NULL,
  pending_change       jsonb,
  change_effective_at  timestamptz,
  is_seed              boolean NOT NULL DEFAULT false,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_guardian ON guardians (user_id) WHERE status IN ('ACTIVE','REMOVAL_PENDING');

CREATE TABLE guardian_requests (
  request_id        uuid PRIMARY KEY,
  guardian_link_id  uuid NOT NULL,
  guardian_user_id  uuid NOT NULL,
  user_id           uuid NOT NULL,
  decision_id       uuid NOT NULL,
  kind              text NOT NULL,
  status            text NOT NULL,
  summary           jsonb NOT NULL,
  expires_at        timestamptz,
  responded_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON guardian_requests (guardian_user_id, created_at DESC);

CREATE TABLE held_payments (
  hold_id        uuid PRIMARY KEY,
  decision_id    uuid NOT NULL,
  txn_id         text NOT NULL,
  user_id        uuid NOT NULL,
  payee_vpa      text NOT NULL,
  payee_name     text,
  claimed_name   text,
  amount         numeric(14,2) NOT NULL,
  note           text,
  release_at     timestamptz NOT NULL,
  confirm_by     timestamptz NOT NULL,
  status         text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON held_payments (user_id, created_at DESC);

CREATE TABLE pair_blocks (
  user_id        uuid NOT NULL,
  payee_vpa      text NOT NULL,
  until          timestamptz NOT NULL,
  decision_id    uuid,
  PRIMARY KEY (user_id, payee_vpa)
);

CREATE TABLE complaint_packs (
  pack_id        uuid PRIMARY KEY,
  decision_id    uuid,
  txn_id         text,
  check_id       uuid,
  requested_by   text NOT NULL,
  payload        jsonb NOT NULL,
  pdf_path       text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rehearsal_scripts (
  code           text NOT NULL,
  locale         text NOT NULL,
  family         text NOT NULL,
  title          text NOT NULL,
  minutes        numeric(3,1) NOT NULL DEFAULT 1.5,
  script         jsonb NOT NULL,
  PRIMARY KEY (code, locale)
);

CREATE TABLE rehearsal_runs (
  run_id         uuid PRIMARY KEY,
  user_id        uuid NOT NULL,
  script_code    text NOT NULL,
  choices        jsonb NOT NULL,
  safe_choices   smallint NOT NULL,
  total_choices  smallint NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- 6. Configuration and catalogues ---------------------------------------------
CREATE TABLE policy_config (
  tenant_id          text PRIMARY KEY REFERENCES tenants,
  mode               text NOT NULL DEFAULT 'enforce' CHECK (mode IN ('enforce','shadow')),
  t_warning          double precision NOT NULL DEFAULT 0.30,
  t_stepup           double precision NOT NULL DEFAULT 0.60,
  t_block            double precision NOT NULL DEFAULT 0.85,
  t_decline          double precision NOT NULL DEFAULT 0.95,
  block_mode         text NOT NULL DEFAULT 'hold' CHECK (block_mode IN ('hold','decline')),
  hold_seconds       integer NOT NULL DEFAULT 1800,
  cooloff_seconds    integer NOT NULL DEFAULT 30,
  guardian_timeout_s integer NOT NULL DEFAULT 600,
  pair_block_hours   integer NOT NULL DEFAULT 24,
  updated_by         uuid,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scam_families (
  code           text PRIMARY KEY,
  name           text NOT NULL,
  prototype      double precision[] NOT NULL,
  messages       jsonb NOT NULL,
  audio_key      text NOT NULL,
  active         boolean NOT NULL DEFAULT true
);

CREATE TABLE reason_codes (
  code           text PRIMARY KEY,
  signal_group   text NOT NULL,
  priority       smallint NOT NULL,
  templates      jsonb NOT NULL
);

CREATE TABLE model_registry (
  version        text PRIMARY KEY,
  kind           text NOT NULL,
  artifact_path  text NOT NULL,
  metrics        jsonb NOT NULL,
  gates          jsonb NOT NULL DEFAULT '{}',
  stage          text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- 7. Monitoring and summaries --------------------------------------------------
CREATE TABLE drift_reports (
  report_id      uuid NOT NULL,
  model_version  text NOT NULL,
  window_start   timestamptz NOT NULL,
  window_end     timestamptz NOT NULL,
  feature        text NOT NULL,
  psi            double precision NOT NULL,
  status         text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (report_id, feature)
);

CREATE TABLE fairness_reports (
  report_id          uuid NOT NULL,
  model_version      text NOT NULL,
  window_start       timestamptz NOT NULL,
  window_end         timestamptz NOT NULL,
  dimension          text NOT NULL,
  segment            text NOT NULL,
  n_legit            integer NOT NULL,
  share_warning      double precision NOT NULL,
  share_stepup       double precision NOT NULL,
  share_block        double precision NOT NULL,
  fpr_stepup_plus    double precision NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (report_id, dimension, segment)
);

CREATE TABLE case_summaries (
  decision_id      uuid PRIMARY KEY,
  summary          text NOT NULL,
  key_evidence     jsonb NOT NULL DEFAULT '[]',
  suggested_action text,
  provider         text NOT NULL,
  model            text,
  generated_at     timestamptz NOT NULL DEFAULT now()
);

-- 8. Plumbing ------------------------------------------------------------------
CREATE TABLE audit_log (
  id             bigserial PRIMARY KEY,
  tenant_id      text,
  actor          text NOT NULL,
  actor_name     text,
  action         text NOT NULL,
  subject        text NOT NULL,
  detail         jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
  id             bigserial PRIMARY KEY,
  topic          text NOT NULL,
  payload        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  published_at   timestamptz
);

CREATE TABLE meta (
  key            text PRIMARY KEY,
  value          jsonb NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now()
);
